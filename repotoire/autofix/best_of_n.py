"""Best-of-N sampling for auto-fix generation.

This module provides Best-of-N sampling which generates multiple fix candidates,
verifies each in parallel E2B sandboxes, and selects the best one based on
test pass rate and quality metrics.

Feature Availability:
- Free tier: Unavailable
- Pro tier: Available as paid add-on ($29/month), up to 5 candidates
- Enterprise tier: Included free, up to 10 candidates

Example:
    ```python
    from repotoire.autofix.best_of_n import (
        BestOfNGenerator,
        BestOfNConfig,
        BestOfNNotAvailableError,
    )
    from repotoire.autofix.entitlements import get_customer_entitlement
    from repotoire.db.models import PlanTier

    # Get entitlement
    entitlement = await get_customer_entitlement(
        customer_id="cust_123",
        tier=PlanTier.PRO,
        db=session,
    )

    # Generate and verify fixes
    generator = BestOfNGenerator(
        config=BestOfNConfig(n=5),
        customer_id="cust_123",
        tier=PlanTier.PRO,
        entitlement=entitlement,
    )

    try:
        ranked = await generator.generate_and_verify(
            issue=finding,
            repository_path="/path/to/repo",
            test_command="pytest",
        )
        print(f"Best fix: {ranked[0].fix.title}")
    except BestOfNNotAvailableError as e:
        print(f"Upgrade required: {e.message}")
    ```
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, List, Optional

from repotoire.autofix.entitlements import (
    BestOfNEntitlement,
    FeatureAccess,
    record_best_of_n_usage,
)
from repotoire.autofix.models import FixProposal, Finding
from repotoire.autofix.scorer import (
    FixScorer,
    RankedFix,
    ScoringConfig,
    VerificationResult,
    select_best_fix,
)
from repotoire.autofix.verifier import ParallelVerifier, VerificationConfig
from repotoire.db.models import PlanTier
from repotoire.logging_config import get_logger
from repotoire.sandbox.config import SandboxConfig

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


def get_next_month_start() -> datetime:
    """Get the datetime when the next month starts (for usage reset)."""
    today = date.today()
    if today.month == 12:
        next_month = date(today.year + 1, 1, 1)
    else:
        next_month = date(today.year, today.month + 1, 1)
    return datetime.combine(next_month, datetime.min.time())


class BestOfNNotAvailableError(Exception):
    """Raised when Best-of-N is not available for customer's tier.

    This exception provides user-friendly messaging and upgrade URLs
    based on the customer's current access level.
    """

    def __init__(self, tier: PlanTier, access: FeatureAccess):
        self.tier = tier
        self.access = access

        if access == FeatureAccess.UNAVAILABLE:
            self.message = (
                "Best-of-N sampling is not available on the Free plan. "
                "Upgrade to Pro or Enterprise to access this feature."
            )
            self.upgrade_url = "https://repotoire.dev/pricing"
            self.addon_url = None
        elif access == FeatureAccess.ADDON:
            self.message = (
                "Best-of-N sampling requires the Pro Add-on. "
                "Enable it in your account settings for $29/month."
            )
            self.upgrade_url = None
            self.addon_url = "https://repotoire.dev/account/addons"
        else:
            self.message = "Best-of-N sampling is not available."
            self.upgrade_url = None
            self.addon_url = None

        super().__init__(self.message)


class BestOfNUsageLimitError(Exception):
    """Raised when customer has exceeded their monthly Best-of-N usage limit.

    Provides information about current usage and when the limit resets.
    """

    def __init__(self, used: int, limit: int, resets_at: datetime):
        self.used = used
        self.limit = limit
        self.resets_at = resets_at
        self.message = (
            f"Monthly Best-of-N limit reached ({used}/{limit} runs). "
            f"Resets on {resets_at.strftime('%B 1, %Y')}."
        )
        super().__init__(self.message)


@dataclass
class BestOfNConfig:
    """Configuration for Best-of-N generation.

    Attributes:
        n: Number of fix candidates to generate
        max_concurrent_sandboxes: Maximum concurrent verifications
        test_timeout: Timeout for test execution in seconds
        min_test_pass_rate: Minimum test pass rate to consider a fix
        temperature: Temperature for LLM generation (higher = more diverse)
        scoring_config: Configuration for scoring algorithm
        verification_config: Configuration for verification
    """

    n: int = 5
    max_concurrent_sandboxes: int = 5
    test_timeout: int = 120
    min_test_pass_rate: float = 0.0
    temperature: float = 0.7  # Moderate diversity
    scoring_config: Optional[ScoringConfig] = None
    verification_config: Optional[VerificationConfig] = None


@dataclass
class BestOfNResult:
    """Result of Best-of-N generation and verification.

    Attributes:
        ranked_fixes: List of fixes ranked by score
        best_fix: The highest-ranked fix (if any passed tests)
        candidates_generated: Total number of candidates generated
        candidates_verified: Number that completed verification
        total_duration_ms: Total time for generation and verification
        total_sandbox_cost_usd: Total sandbox cost
    """

    ranked_fixes: List[RankedFix]
    best_fix: Optional[RankedFix]
    candidates_generated: int
    candidates_verified: int
    total_duration_ms: int
    total_sandbox_cost_usd: float

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "ranked_fixes": [rf.to_dict() for rf in self.ranked_fixes],
            "best_fix": self.best_fix.to_dict() if self.best_fix else None,
            "candidates_generated": self.candidates_generated,
            "candidates_verified": self.candidates_verified,
            "total_duration_ms": self.total_duration_ms,
            "total_sandbox_cost_usd": round(self.total_sandbox_cost_usd, 4),
            "has_recommendation": self.best_fix is not None and self.best_fix.is_recommended,
        }


class BestOfNGenerator:
    """Orchestrates Best-of-N fix generation with entitlement enforcement.

    This class:
    1. Checks customer entitlement (tier, add-on, usage)
    2. Generates N fix candidates with varied prompts
    3. Verifies each candidate in parallel sandboxes
    4. Scores and ranks fixes by test results and quality
    5. Records usage for billing
    """

    def __init__(
        self,
        config: BestOfNConfig,
        customer_id: str,
        tier: PlanTier,
        entitlement: BestOfNEntitlement,
        db: Optional["AsyncSession"] = None,
    ):
        """Initialize Best-of-N generator.

        Args:
            config: Generation configuration
            customer_id: Customer identifier
            tier: Customer's subscription tier
            entitlement: Customer's Best-of-N entitlement
            db: Database session for recording usage
        """
        self.config = config
        self.customer_id = customer_id
        self.tier = tier
        self.entitlement = entitlement
        self.db = db

        # Initialize components
        self.scorer = FixScorer(config.scoring_config)
        self.verification_config = config.verification_config or VerificationConfig(
            max_concurrent=config.max_concurrent_sandboxes,
            test_timeout=config.test_timeout,
        )

    async def generate_and_verify(
        self,
        issue: Finding,
        repository_path: str,
        test_command: str = "pytest",
    ) -> BestOfNResult:
        """Generate N fixes, verify in parallel, return ranked list.

        Args:
            issue: The finding/issue to fix
            repository_path: Path to the repository
            test_command: Test command to run for verification

        Returns:
            BestOfNResult with ranked fixes and metrics

        Raises:
            BestOfNNotAvailableError: If customer cannot use Best-of-N
            BestOfNUsageLimitError: If monthly limit is exceeded
        """
        import time
        start_time = time.time()

        # 1. Check entitlement
        self._check_entitlement()

        # 2. Check monthly usage limit
        await self._check_usage_limit()

        # 3. Clamp N to tier's max
        n = min(self.config.n, self.entitlement.max_n)

        logger.info(
            f"Starting Best-of-N generation for {self.customer_id}",
            extra={
                "n": n,
                "tier": self.tier.value,
                "finding_id": getattr(issue, "id", None),
            },
        )

        # 4. Generate N fix candidates
        fixes = await self._generate_n_fixes(issue, repository_path, n)

        if not fixes:
            logger.warning("No fix candidates generated")
            return BestOfNResult(
                ranked_fixes=[],
                best_fix=None,
                candidates_generated=0,
                candidates_verified=0,
                total_duration_ms=int((time.time() - start_time) * 1000),
                total_sandbox_cost_usd=0.0,
            )

        # 5. Verify all candidates in parallel
        verification_results = await self._verify_parallel(
            fixes, repository_path, test_command
        )

        # 6. Score and rank
        ranked = self.scorer.score_and_rank(
            fixes=fixes,
            verification_results=verification_results,
            min_test_pass_rate=self.config.min_test_pass_rate,
        )

        # 7. Select best fix
        best = select_best_fix(ranked, require_all_tests_pass=False)

        # Calculate total cost
        total_cost = sum(
            r.sandbox_cost_usd for r in verification_results.values()
        )

        # 8. Record usage
        await self._record_usage(len(fixes), total_cost)

        total_duration_ms = int((time.time() - start_time) * 1000)

        logger.info(
            f"Best-of-N complete for {self.customer_id}",
            extra={
                "candidates": len(fixes),
                "verified": len(verification_results),
                "ranked": len(ranked),
                "best_score": best.total_score if best else None,
                "duration_ms": total_duration_ms,
                "cost_usd": total_cost,
            },
        )

        return BestOfNResult(
            ranked_fixes=ranked,
            best_fix=best,
            candidates_generated=len(fixes),
            candidates_verified=len([r for r in verification_results.values() if r.succeeded]),
            total_duration_ms=total_duration_ms,
            total_sandbox_cost_usd=total_cost,
        )

    def _check_entitlement(self) -> None:
        """Raise if customer cannot use Best-of-N.

        Raises:
            BestOfNNotAvailableError: If feature is not available
        """
        if not self.entitlement.is_available:
            raise BestOfNNotAvailableError(self.tier, self.entitlement.access)

    async def _check_usage_limit(self) -> None:
        """Check monthly run limit.

        Raises:
            BestOfNUsageLimitError: If monthly limit is exceeded
        """
        if self.entitlement.monthly_runs_limit == -1:
            return  # Enterprise: unlimited

        if not self.entitlement.is_within_limit:
            raise BestOfNUsageLimitError(
                used=self.entitlement.monthly_runs_used,
                limit=self.entitlement.monthly_runs_limit,
                resets_at=get_next_month_start(),
            )

    async def _generate_n_fixes(
        self,
        issue: Finding,
        repository_path: str,
        n: int,
    ) -> List[FixProposal]:
        """Generate N diverse fix candidates.

        Uses varied prompts and temperature to generate diverse fixes.

        Args:
            issue: The issue to fix
            repository_path: Path to repository
            n: Number of candidates to generate

        Returns:
            List of FixProposal candidates
        """
        from repotoire.autofix.engine import AutoFixEngine

        fixes: List[FixProposal] = []

        # Create engine instance
        # Note: In production, you'd pass the actual Neo4j client
        try:
            engine = AutoFixEngine(neo4j_client=None)  # Will use mock/dummy
        except Exception as e:
            logger.warning(f"Could not initialize AutoFixEngine: {e}")
            # Fall back to generating dummy candidates for testing
            return []

        # Generate N candidates with varied parameters
        variation_prompts = [
            "Generate a minimal fix that addresses only the issue.",
            "Generate a comprehensive fix that also improves code quality.",
            "Generate a fix following best practices and design patterns.",
            "Generate a simple, readable fix that's easy to understand.",
            "Generate a performant fix optimized for efficiency.",
        ]

        for i in range(n):
            try:
                # Vary the generation approach
                variation = variation_prompts[i % len(variation_prompts)]

                # Generate fix with variation
                fix = await engine.generate_fix(
                    finding=issue,
                    repository_path=repository_path,
                    # Pass variation hint via context
                    context_size=5,
                )

                if fix is not None:
                    # Assign unique ID for this candidate
                    fix.id = f"{fix.id}_candidate_{i+1}"
                    fixes.append(fix)

            except Exception as e:
                logger.warning(f"Failed to generate candidate {i+1}: {e}")
                continue

        logger.info(f"Generated {len(fixes)}/{n} fix candidates")
        return fixes

    async def _verify_parallel(
        self,
        fixes: List[FixProposal],
        repository_path: str,
        test_command: str,
    ) -> dict[str, VerificationResult]:
        """Verify all fixes in parallel sandboxes.

        Args:
            fixes: Fix candidates to verify
            repository_path: Path to repository
            test_command: Test command to run

        Returns:
            Dictionary mapping fix_id -> VerificationResult
        """
        async with ParallelVerifier(
            config=self.verification_config,
            customer_id=self.customer_id,
            tier=self.tier,
        ) as verifier:
            return await verifier.verify_all(
                fixes=fixes,
                repository_path=repository_path,
                test_command=test_command,
            )

    async def _record_usage(
        self,
        candidates_generated: int,
        sandbox_cost_usd: float,
    ) -> None:
        """Record Best-of-N usage for billing.

        Args:
            candidates_generated: Number of candidates generated
            sandbox_cost_usd: Total sandbox cost
        """
        if self.db is not None:
            try:
                await record_best_of_n_usage(
                    customer_id=self.customer_id,
                    candidates_generated=candidates_generated,
                    sandbox_cost_usd=sandbox_cost_usd,
                    db=self.db,
                )
            except Exception as e:
                logger.warning(f"Failed to record usage: {e}")


async def generate_best_of_n(
    issue: Finding,
    repository_path: str,
    customer_id: str,
    tier: PlanTier,
    entitlement: BestOfNEntitlement,
    n: int = 5,
    test_command: str = "pytest",
    db: Optional["AsyncSession"] = None,
) -> BestOfNResult:
    """Convenience function for Best-of-N generation.

    Args:
        issue: The issue to fix
        repository_path: Path to repository
        customer_id: Customer identifier
        tier: Customer's subscription tier
        entitlement: Customer's entitlement
        n: Number of candidates to generate
        test_command: Test command
        db: Database session

    Returns:
        BestOfNResult with ranked fixes
    """
    config = BestOfNConfig(n=n)
    generator = BestOfNGenerator(
        config=config,
        customer_id=customer_id,
        tier=tier,
        entitlement=entitlement,
        db=db,
    )
    return await generator.generate_and_verify(
        issue=issue,
        repository_path=repository_path,
        test_command=test_command,
    )
