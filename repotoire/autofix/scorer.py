"""Multi-factor scoring and ranking for Best-of-N fix candidates.

This module provides sophisticated scoring algorithms that evaluate fix
candidates across multiple dimensions:
- Test pass rate (primary signal)
- Validation levels (syntax, imports, types)
- Evidence strength (RAG context, similar patterns)
- Code quality metrics (complexity, maintainability)
- Confidence level

Example:
    ```python
    from repotoire.autofix.scorer import (
        FixScorer,
        ScoringConfig,
        RankedFix,
    )

    scorer = FixScorer(config=ScoringConfig())

    # Score and rank verified fixes
    ranked = scorer.score_and_rank(
        fixes=fix_proposals,
        verification_results=results,
    )

    # Best fix is first
    best = ranked[0]
    print(f"Best fix: {best.fix.title} (score: {best.total_score:.2f})")
    ```
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from repotoire.autofix.models import FixProposal, FixConfidence
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class ScoringDimension(str, Enum):
    """Dimensions for scoring fix candidates."""

    TEST_PASS_RATE = "test_pass_rate"
    VALIDATION_LEVEL = "validation_level"
    EVIDENCE_STRENGTH = "evidence_strength"
    CODE_QUALITY = "code_quality"
    CONFIDENCE = "confidence"
    CHANGE_SIZE = "change_size"


@dataclass(frozen=True)
class ScoringConfig:
    """Configuration for fix scoring.

    Weights determine relative importance of each dimension.
    All weights should sum to 1.0.

    Attributes:
        test_weight: Weight for test pass rate (0-1)
        validation_weight: Weight for validation level (0-1)
        evidence_weight: Weight for evidence strength (0-1)
        quality_weight: Weight for code quality metrics (0-1)
        confidence_weight: Weight for base confidence level (0-1)
        change_size_weight: Weight for preferring smaller changes (0-1)
    """

    test_weight: float = 0.35  # Tests are most important
    validation_weight: float = 0.25  # Syntax/import/type validation
    evidence_weight: float = 0.15  # Evidence from RAG and patterns
    quality_weight: float = 0.10  # Code complexity and maintainability
    confidence_weight: float = 0.10  # Original confidence assessment
    change_size_weight: float = 0.05  # Prefer minimal changes

    def __post_init__(self):
        total = (
            self.test_weight
            + self.validation_weight
            + self.evidence_weight
            + self.quality_weight
            + self.confidence_weight
            + self.change_size_weight
        )
        if abs(total - 1.0) > 0.01:
            logger.warning(
                f"Scoring weights sum to {total}, not 1.0. "
                "Scores will be normalized."
            )


@dataclass
class VerificationResult:
    """Result of verifying a fix candidate in sandbox.

    Attributes:
        fix_id: ID of the fix that was verified
        tests_passed: Number of tests that passed
        tests_failed: Number of tests that failed
        tests_total: Total number of tests run
        test_output: Raw test output
        syntax_valid: Whether syntax check passed
        import_valid: Whether import check passed
        type_valid: Whether type check passed (if run)
        error: Error message if verification failed
        duration_ms: Time to run verification in milliseconds
        sandbox_cost_usd: Cost of sandbox execution
    """

    fix_id: str
    tests_passed: int = 0
    tests_failed: int = 0
    tests_total: int = 0
    test_output: str = ""
    syntax_valid: bool = False
    import_valid: Optional[bool] = None
    type_valid: Optional[bool] = None
    error: Optional[str] = None
    duration_ms: int = 0
    sandbox_cost_usd: float = 0.0

    @property
    def test_pass_rate(self) -> float:
        """Calculate test pass rate (0.0-1.0)."""
        if self.tests_total == 0:
            return 0.0  # No tests = no confidence
        return self.tests_passed / self.tests_total

    @property
    def validation_score(self) -> float:
        """Calculate validation score based on checks passed (0.0-1.0)."""
        score = 0.0
        checks = 0

        # Syntax is required
        if self.syntax_valid:
            score += 0.5  # 50% for syntax alone
        checks += 1

        # Import is important
        if self.import_valid is not None:
            checks += 1
            if self.import_valid:
                score += 0.3

        # Type check is nice to have
        if self.type_valid is not None:
            checks += 1
            if self.type_valid:
                score += 0.2

        return score

    @property
    def succeeded(self) -> bool:
        """Check if verification succeeded without errors."""
        return self.error is None and self.syntax_valid


@dataclass
class DimensionScore:
    """Score for a single dimension with explanation.

    Attributes:
        dimension: Which dimension this score is for
        score: Normalized score (0.0-1.0)
        weight: Weight applied to this dimension
        weighted_score: score * weight
        explanation: Human-readable explanation
    """

    dimension: ScoringDimension
    score: float
    weight: float
    weighted_score: float
    explanation: str


@dataclass
class RankedFix:
    """A fix candidate with its scores and ranking.

    Attributes:
        fix: The fix proposal
        verification: Verification result from sandbox
        rank: Position in ranking (1 = best)
        total_score: Weighted sum of all dimension scores (0.0-1.0)
        dimension_scores: Breakdown of scores by dimension
        is_recommended: Whether this is the recommended fix
        recommendation_reason: Why this fix is/isn't recommended
    """

    fix: FixProposal
    verification: VerificationResult
    rank: int
    total_score: float
    dimension_scores: List[DimensionScore]
    is_recommended: bool = False
    recommendation_reason: str = ""

    @property
    def test_pass_rate(self) -> float:
        """Convenience accessor for test pass rate."""
        return self.verification.test_pass_rate

    @property
    def tests_summary(self) -> str:
        """Human-readable test summary."""
        v = self.verification
        if v.tests_total == 0:
            return "No tests run"
        return f"{v.tests_passed}/{v.tests_total} tests passed ({self.test_pass_rate:.0%})"

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "fix_id": self.fix.id,
            "rank": self.rank,
            "total_score": round(self.total_score, 4),
            "test_pass_rate": round(self.verification.test_pass_rate, 4),
            "tests_summary": self.tests_summary,
            "is_recommended": self.is_recommended,
            "recommendation_reason": self.recommendation_reason,
            "dimension_scores": [
                {
                    "dimension": ds.dimension.value,
                    "score": round(ds.score, 4),
                    "weight": round(ds.weight, 4),
                    "weighted_score": round(ds.weighted_score, 4),
                    "explanation": ds.explanation,
                }
                for ds in self.dimension_scores
            ],
            "verification": {
                "tests_passed": self.verification.tests_passed,
                "tests_failed": self.verification.tests_failed,
                "tests_total": self.verification.tests_total,
                "syntax_valid": self.verification.syntax_valid,
                "import_valid": self.verification.import_valid,
                "type_valid": self.verification.type_valid,
                "duration_ms": self.verification.duration_ms,
                "sandbox_cost_usd": round(self.verification.sandbox_cost_usd, 4),
                "error": self.verification.error,
            },
        }


class FixScorer:
    """Score and rank fix candidates based on multiple factors.

    Uses weighted scoring across dimensions:
    - Test pass rate (35% default)
    - Validation level (25% default)
    - Evidence strength (15% default)
    - Code quality (10% default)
    - Confidence (10% default)
    - Change size (5% default)
    """

    def __init__(self, config: Optional[ScoringConfig] = None):
        """Initialize scorer with optional custom config.

        Args:
            config: Scoring configuration (uses defaults if not provided)
        """
        self.config = config or ScoringConfig()

    def score_and_rank(
        self,
        fixes: List[FixProposal],
        verification_results: dict[str, VerificationResult],
        min_test_pass_rate: float = 0.0,
    ) -> List[RankedFix]:
        """Score and rank fix candidates.

        Args:
            fixes: List of fix proposals to rank
            verification_results: Map of fix_id -> VerificationResult
            min_test_pass_rate: Minimum test pass rate to be considered (0.0-1.0)

        Returns:
            List of RankedFix sorted by total_score descending
        """
        ranked_fixes: List[RankedFix] = []

        for fix in fixes:
            result = verification_results.get(fix.id)
            if result is None:
                logger.warning(f"No verification result for fix {fix.id}, skipping")
                continue

            # Skip fixes that failed verification completely
            if not result.succeeded:
                logger.debug(
                    f"Fix {fix.id} failed verification: {result.error}, skipping"
                )
                continue

            # Skip fixes below minimum test pass rate
            if result.test_pass_rate < min_test_pass_rate:
                logger.debug(
                    f"Fix {fix.id} below min test pass rate "
                    f"({result.test_pass_rate:.2%} < {min_test_pass_rate:.2%})"
                )
                continue

            # Calculate scores for each dimension
            dimension_scores = self._calculate_dimension_scores(fix, result)

            # Calculate total weighted score
            total_score = sum(ds.weighted_score for ds in dimension_scores)

            ranked_fixes.append(
                RankedFix(
                    fix=fix,
                    verification=result,
                    rank=0,  # Set after sorting
                    total_score=total_score,
                    dimension_scores=dimension_scores,
                )
            )

        # Sort by total score descending
        ranked_fixes.sort(key=lambda rf: rf.total_score, reverse=True)

        # Assign ranks and determine recommendation
        for i, rf in enumerate(ranked_fixes):
            rf.rank = i + 1

            # Best fix with 100% tests passing is recommended
            if i == 0 and rf.verification.test_pass_rate == 1.0:
                rf.is_recommended = True
                rf.recommendation_reason = (
                    "Highest score with all tests passing"
                )
            elif i == 0:
                rf.is_recommended = False
                rf.recommendation_reason = (
                    f"Best available but only {rf.verification.test_pass_rate:.0%} tests pass"
                )
            else:
                rf.is_recommended = False
                rf.recommendation_reason = (
                    f"Ranked #{rf.rank} of {len(ranked_fixes)}"
                )

        return ranked_fixes

    def _calculate_dimension_scores(
        self,
        fix: FixProposal,
        result: VerificationResult,
    ) -> List[DimensionScore]:
        """Calculate scores for all dimensions.

        Args:
            fix: The fix proposal
            result: Verification result

        Returns:
            List of DimensionScore for each dimension
        """
        scores = []

        # 1. Test Pass Rate
        test_score = result.test_pass_rate
        scores.append(
            DimensionScore(
                dimension=ScoringDimension.TEST_PASS_RATE,
                score=test_score,
                weight=self.config.test_weight,
                weighted_score=test_score * self.config.test_weight,
                explanation=f"{result.tests_passed}/{result.tests_total} tests passed",
            )
        )

        # 2. Validation Level
        val_score = result.validation_score
        val_parts = []
        if result.syntax_valid:
            val_parts.append("syntax")
        if result.import_valid:
            val_parts.append("imports")
        if result.type_valid:
            val_parts.append("types")
        val_explanation = f"Passed: {', '.join(val_parts) if val_parts else 'none'}"
        scores.append(
            DimensionScore(
                dimension=ScoringDimension.VALIDATION_LEVEL,
                score=val_score,
                weight=self.config.validation_weight,
                weighted_score=val_score * self.config.validation_weight,
                explanation=val_explanation,
            )
        )

        # 3. Evidence Strength
        evidence_score = self._calculate_evidence_score(fix)
        evidence_count = (
            len(fix.evidence.similar_patterns)
            + len(fix.evidence.documentation_refs)
            + len(fix.evidence.best_practices)
            + len(fix.evidence.rag_context)
        )
        scores.append(
            DimensionScore(
                dimension=ScoringDimension.EVIDENCE_STRENGTH,
                score=evidence_score,
                weight=self.config.evidence_weight,
                weighted_score=evidence_score * self.config.evidence_weight,
                explanation=f"{evidence_count} evidence items",
            )
        )

        # 4. Code Quality
        quality_score = self._calculate_quality_score(fix)
        scores.append(
            DimensionScore(
                dimension=ScoringDimension.CODE_QUALITY,
                score=quality_score,
                weight=self.config.quality_weight,
                weighted_score=quality_score * self.config.quality_weight,
                explanation=self._get_quality_explanation(fix),
            )
        )

        # 5. Confidence
        confidence_score = self._confidence_to_score(fix.confidence)
        scores.append(
            DimensionScore(
                dimension=ScoringDimension.CONFIDENCE,
                score=confidence_score,
                weight=self.config.confidence_weight,
                weighted_score=confidence_score * self.config.confidence_weight,
                explanation=f"Confidence: {fix.confidence.value}",
            )
        )

        # 6. Change Size (prefer smaller)
        change_score = self._calculate_change_size_score(fix)
        total_lines = sum(
            len(c.fixed_code.splitlines()) for c in fix.changes
        )
        scores.append(
            DimensionScore(
                dimension=ScoringDimension.CHANGE_SIZE,
                score=change_score,
                weight=self.config.change_size_weight,
                weighted_score=change_score * self.config.change_size_weight,
                explanation=f"{total_lines} lines of change",
            )
        )

        return scores

    def _calculate_evidence_score(self, fix: FixProposal) -> float:
        """Calculate evidence strength score (0.0-1.0).

        More evidence = higher score, with diminishing returns.
        """
        evidence = fix.evidence

        # Count evidence items with different weights
        score = 0.0

        # Similar patterns from codebase (most valuable)
        patterns = len(evidence.similar_patterns)
        score += min(patterns * 0.15, 0.3)  # Up to 0.3 for 2+ patterns

        # Documentation references (valuable)
        docs = len(evidence.documentation_refs)
        score += min(docs * 0.1, 0.2)  # Up to 0.2 for 2+ docs

        # Best practices (valuable)
        practices = len(evidence.best_practices)
        score += min(practices * 0.1, 0.2)  # Up to 0.2 for 2+ practices

        # RAG context (good but plentiful)
        rag = len(evidence.rag_context)
        score += min(rag * 0.05, 0.3)  # Up to 0.3 for 6+ context items

        return min(score, 1.0)

    def _calculate_quality_score(self, fix: FixProposal) -> float:
        """Calculate code quality score (0.0-1.0).

        Based on:
        - Validation status (syntax, imports, types)
        - Whether tests were generated
        """
        score = 0.5  # Base score

        # Validation bonuses
        if fix.syntax_valid:
            score += 0.2
        if fix.import_valid:
            score += 0.15
        if fix.type_valid:
            score += 0.1

        # Test generation bonus
        if fix.tests_generated and fix.test_code:
            score += 0.05

        return min(score, 1.0)

    def _get_quality_explanation(self, fix: FixProposal) -> str:
        """Get human-readable quality explanation."""
        parts = []
        if fix.syntax_valid:
            parts.append("syntax OK")
        if fix.import_valid:
            parts.append("imports OK")
        if fix.type_valid:
            parts.append("types OK")
        if fix.tests_generated:
            parts.append("tests generated")
        return ", ".join(parts) if parts else "basic quality"

    def _confidence_to_score(self, confidence: FixConfidence) -> float:
        """Convert confidence level to numeric score."""
        return {
            FixConfidence.HIGH: 1.0,
            FixConfidence.MEDIUM: 0.6,
            FixConfidence.LOW: 0.3,
        }.get(confidence, 0.5)

    def _calculate_change_size_score(self, fix: FixProposal) -> float:
        """Calculate change size score (0.0-1.0).

        Smaller changes score higher (prefer minimal fixes).
        """
        total_lines = sum(
            len(c.fixed_code.splitlines()) for c in fix.changes
        )

        # Score decreases as lines increase
        # 0-10 lines: 1.0
        # 10-50 lines: 0.8-1.0
        # 50-100 lines: 0.5-0.8
        # 100+ lines: 0.2-0.5

        if total_lines <= 10:
            return 1.0
        elif total_lines <= 50:
            return 1.0 - (total_lines - 10) * 0.005  # Linear decay
        elif total_lines <= 100:
            return 0.8 - (total_lines - 50) * 0.006
        else:
            return max(0.2, 0.5 - (total_lines - 100) * 0.003)


def select_best_fix(
    ranked_fixes: List[RankedFix],
    require_all_tests_pass: bool = False,
    min_score: float = 0.0,
) -> Optional[RankedFix]:
    """Select the best fix from ranked candidates.

    Args:
        ranked_fixes: List of ranked fixes (sorted by score)
        require_all_tests_pass: If True, only consider fixes with 100% tests passing
        min_score: Minimum total score required

    Returns:
        Best fix meeting criteria, or None if no fix qualifies
    """
    for fix in ranked_fixes:
        # Check test requirement
        if require_all_tests_pass and fix.verification.test_pass_rate < 1.0:
            continue

        # Check minimum score
        if fix.total_score < min_score:
            continue

        return fix

    return None
