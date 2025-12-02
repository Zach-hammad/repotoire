"""Quota enforcement for sandbox operations.

This module provides quota checking and enforcement before sandbox creation.
It integrates quota definitions, usage tracking, and admin overrides.

Example:
    ```python
    from repotoire.sandbox.enforcement import QuotaEnforcer, QuotaExceededError
    from repotoire.sandbox.usage import get_usage_tracker
    from repotoire.db.models import PlanTier

    enforcer = QuotaEnforcer(usage_tracker=get_usage_tracker())

    # Check quota before creating sandbox
    try:
        await enforcer.enforce_or_raise("cust_123", PlanTier.PRO)
        # Proceed with sandbox creation
    except QuotaExceededError as e:
        print(f"Quota exceeded: {e.quota_type}")
        print(f"Usage: {e.current}/{e.limit}")
        print(f"Upgrade at: {e.upgrade_url}")
    ```
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional

from repotoire.db.models import PlanTier
from repotoire.logging_config import get_logger
from repotoire.sandbox.exceptions import SandboxError
from repotoire.sandbox.quotas import (
    SandboxQuota,
    QuotaOverride,
    get_quota_for_tier,
    apply_override,
)
from repotoire.sandbox.usage import SandboxUsageTracker, get_usage_tracker

logger = get_logger(__name__)

# Default upgrade URL
DEFAULT_UPGRADE_URL = os.getenv(
    "REPOTOIRE_UPGRADE_URL",
    "https://repotoire.dev/pricing",
)


class QuotaType(str, Enum):
    """Types of quotas that can be exceeded."""

    CONCURRENT = "concurrent_sandboxes"
    DAILY_MINUTES = "daily_minutes"
    MONTHLY_MINUTES = "monthly_minutes"
    DAILY_SESSIONS = "daily_sessions"


class QuotaWarningLevel(str, Enum):
    """Warning levels for quota usage."""

    OK = "ok"  # < 80%
    WARNING = "warning"  # 80-89%
    CRITICAL = "critical"  # 90-99%
    EXCEEDED = "exceeded"  # >= 100%


class QuotaExceededError(SandboxError):
    """Raised when a customer exceeds their quota limits.

    This exception includes all context needed for user-friendly error messages
    and upgrade prompts.

    Attributes:
        quota_type: Which quota was exceeded
        current: Current usage value
        limit: Maximum allowed value
        upgrade_url: URL to upgrade subscription
        tier: Customer's current tier
    """

    def __init__(
        self,
        message: str,
        quota_type: QuotaType,
        current: float,
        limit: float,
        upgrade_url: str = DEFAULT_UPGRADE_URL,
        tier: Optional[PlanTier] = None,
    ):
        super().__init__(message)
        self.quota_type = quota_type
        self.current = current
        self.limit = limit
        self.upgrade_url = upgrade_url
        self.tier = tier

    def __str__(self) -> str:
        return (
            f"{self.message} | "
            f"quota_type={self.quota_type.value} | "
            f"current={self.current} | "
            f"limit={self.limit} | "
            f"upgrade_url={self.upgrade_url}"
        )


@dataclass
class QuotaCheckResult:
    """Result of a quota check.

    Attributes:
        allowed: Whether the operation is allowed
        quota_type: Which quota was checked (or exceeded)
        current: Current usage value
        limit: Maximum allowed value
        usage_percent: Percentage of quota used (0-100+)
        warning_level: Warning level based on usage
        reason: Human-readable reason if not allowed
    """

    allowed: bool
    quota_type: QuotaType
    current: float
    limit: float
    usage_percent: float
    warning_level: QuotaWarningLevel
    reason: Optional[str] = None


@dataclass
class QuotaStatus:
    """Complete quota status for a customer.

    Provides comprehensive view of all quota types and their usage.

    Attributes:
        customer_id: Customer identifier
        tier: Subscription tier
        effective_quota: Quota after any overrides applied
        concurrent: Status for concurrent sandboxes
        daily_minutes: Status for daily minutes
        monthly_minutes: Status for monthly minutes
        daily_sessions: Status for daily session count
        overall_warning_level: Highest warning level across all quotas
        has_override: Whether admin override is applied
    """

    customer_id: str
    tier: PlanTier
    effective_quota: SandboxQuota
    concurrent: QuotaCheckResult
    daily_minutes: QuotaCheckResult
    monthly_minutes: QuotaCheckResult
    daily_sessions: QuotaCheckResult
    overall_warning_level: QuotaWarningLevel
    has_override: bool = False


def _calculate_warning_level(usage_percent: float) -> QuotaWarningLevel:
    """Calculate warning level from usage percentage."""
    if usage_percent >= 100:
        return QuotaWarningLevel.EXCEEDED
    elif usage_percent >= 90:
        return QuotaWarningLevel.CRITICAL
    elif usage_percent >= 80:
        return QuotaWarningLevel.WARNING
    return QuotaWarningLevel.OK


def _get_highest_warning_level(levels: list[QuotaWarningLevel]) -> QuotaWarningLevel:
    """Get the highest (most severe) warning level from a list."""
    priority = {
        QuotaWarningLevel.EXCEEDED: 4,
        QuotaWarningLevel.CRITICAL: 3,
        QuotaWarningLevel.WARNING: 2,
        QuotaWarningLevel.OK: 1,
    }
    return max(levels, key=lambda l: priority[l])


class QuotaEnforcer:
    """Enforce sandbox quotas for customers.

    Checks quotas before sandbox creation and raises QuotaExceededError
    if limits are exceeded. Supports graceful degradation if the quota
    service is unavailable.

    Example:
        ```python
        enforcer = QuotaEnforcer()
        await enforcer.connect()

        # Check and enforce quota
        try:
            await enforcer.enforce_or_raise("cust_123", PlanTier.PRO)
        except QuotaExceededError as e:
            print(f"Quota exceeded: {e.quota_type}")

        # Get detailed status
        status = await enforcer.get_quota_status("cust_123", PlanTier.PRO)
        print(f"Overall status: {status.overall_warning_level.value}")
        ```
    """

    def __init__(
        self,
        usage_tracker: Optional[SandboxUsageTracker] = None,
        upgrade_url: str = DEFAULT_UPGRADE_URL,
        fail_open: bool = True,
    ):
        """Initialize quota enforcer.

        Args:
            usage_tracker: Usage tracker instance (creates one if not provided)
            upgrade_url: URL for upgrade prompts in error messages
            fail_open: If True, allow operations when quota service unavailable
        """
        self._usage_tracker = usage_tracker
        self._upgrade_url = upgrade_url
        self._fail_open = fail_open
        self._overrides: dict[str, QuotaOverride] = {}
        self._conn = None
        self._connected = False

    @property
    def usage_tracker(self) -> SandboxUsageTracker:
        """Get the usage tracker, creating one if needed."""
        if self._usage_tracker is None:
            self._usage_tracker = get_usage_tracker()
        return self._usage_tracker

    async def connect(self) -> None:
        """Connect to databases (usage tracker and override storage)."""
        await self.usage_tracker.connect()
        # Load overrides from database if connected
        await self._load_overrides()

    async def close(self) -> None:
        """Close database connections."""
        if self._usage_tracker:
            await self._usage_tracker.close()

    async def __aenter__(self) -> "QuotaEnforcer":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()

    async def _load_overrides(self) -> None:
        """Load admin overrides from database."""
        if not self.usage_tracker._connected:
            return

        loop = asyncio.get_event_loop()

        def _query():
            with self.usage_tracker._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        customer_id,
                        max_concurrent_sandboxes,
                        max_daily_sandbox_minutes,
                        max_monthly_sandbox_minutes,
                        override_reason,
                        created_by
                    FROM sandbox_quota_overrides
                    """
                )
                return cur.fetchall()

        try:
            rows = await loop.run_in_executor(None, _query)
            for row in rows:
                self._overrides[row[0]] = QuotaOverride(
                    customer_id=row[0],
                    max_concurrent_sandboxes=row[1],
                    max_daily_sandbox_minutes=row[2],
                    max_monthly_sandbox_minutes=row[3],
                    override_reason=row[4],
                    created_by=row[5],
                )
            logger.debug(f"Loaded {len(self._overrides)} quota overrides")
        except Exception as e:
            logger.warning(f"Failed to load quota overrides: {e}")

    def _get_effective_quota(
        self,
        customer_id: str,
        tier: PlanTier,
    ) -> tuple[SandboxQuota, bool]:
        """Get effective quota for a customer with any overrides applied.

        Returns:
            Tuple of (effective quota, has_override)
        """
        base_quota = get_quota_for_tier(tier)
        override = self._overrides.get(customer_id)
        effective = apply_override(base_quota, override)
        return effective, override is not None

    async def check_quota(
        self,
        customer_id: str,
        tier: PlanTier,
    ) -> QuotaCheckResult:
        """Check if customer is within quota limits.

        Checks all quota types and returns the first violation found,
        or a passing result if all quotas are within limits.

        Args:
            customer_id: Customer identifier
            tier: Customer's subscription tier

        Returns:
            QuotaCheckResult indicating if operation is allowed
        """
        try:
            quota, _ = self._get_effective_quota(customer_id, tier)

            # Check concurrent sandboxes
            concurrent = await self.usage_tracker.get_concurrent_count(customer_id)
            if concurrent >= quota.max_concurrent_sandboxes:
                pct = (concurrent / quota.max_concurrent_sandboxes) * 100
                return QuotaCheckResult(
                    allowed=False,
                    quota_type=QuotaType.CONCURRENT,
                    current=concurrent,
                    limit=quota.max_concurrent_sandboxes,
                    usage_percent=pct,
                    warning_level=QuotaWarningLevel.EXCEEDED,
                    reason=f"Maximum concurrent sandboxes ({quota.max_concurrent_sandboxes}) reached",
                )

            # Check daily minutes
            daily_usage = await self.usage_tracker.get_daily_usage(customer_id)
            if daily_usage.total_minutes >= quota.max_daily_sandbox_minutes:
                pct = (daily_usage.total_minutes / quota.max_daily_sandbox_minutes) * 100
                return QuotaCheckResult(
                    allowed=False,
                    quota_type=QuotaType.DAILY_MINUTES,
                    current=daily_usage.total_minutes,
                    limit=quota.max_daily_sandbox_minutes,
                    usage_percent=pct,
                    warning_level=QuotaWarningLevel.EXCEEDED,
                    reason=f"Daily sandbox minutes ({quota.max_daily_sandbox_minutes}) exceeded",
                )

            # Check daily sessions
            if daily_usage.sandbox_count >= quota.max_sandboxes_per_day:
                pct = (daily_usage.sandbox_count / quota.max_sandboxes_per_day) * 100
                return QuotaCheckResult(
                    allowed=False,
                    quota_type=QuotaType.DAILY_SESSIONS,
                    current=daily_usage.sandbox_count,
                    limit=quota.max_sandboxes_per_day,
                    usage_percent=pct,
                    warning_level=QuotaWarningLevel.EXCEEDED,
                    reason=f"Daily sandbox sessions ({quota.max_sandboxes_per_day}) exceeded",
                )

            # Check monthly minutes
            monthly_usage = await self.usage_tracker.get_monthly_usage(customer_id)
            if monthly_usage.total_minutes >= quota.max_monthly_sandbox_minutes:
                pct = (monthly_usage.total_minutes / quota.max_monthly_sandbox_minutes) * 100
                return QuotaCheckResult(
                    allowed=False,
                    quota_type=QuotaType.MONTHLY_MINUTES,
                    current=monthly_usage.total_minutes,
                    limit=quota.max_monthly_sandbox_minutes,
                    usage_percent=pct,
                    warning_level=QuotaWarningLevel.EXCEEDED,
                    reason=f"Monthly sandbox minutes ({quota.max_monthly_sandbox_minutes}) exceeded",
                )

            # All checks passed - return the highest usage percentage
            daily_pct = (daily_usage.total_minutes / quota.max_daily_sandbox_minutes) * 100
            warning_level = _calculate_warning_level(daily_pct)

            return QuotaCheckResult(
                allowed=True,
                quota_type=QuotaType.DAILY_MINUTES,  # Most relevant for general display
                current=daily_usage.total_minutes,
                limit=quota.max_daily_sandbox_minutes,
                usage_percent=daily_pct,
                warning_level=warning_level,
            )

        except Exception as e:
            logger.error(f"Error checking quota for {customer_id}: {e}")
            if self._fail_open:
                logger.warning(
                    f"Failing open - allowing operation despite quota check error"
                )
                return QuotaCheckResult(
                    allowed=True,
                    quota_type=QuotaType.DAILY_MINUTES,
                    current=0,
                    limit=0,
                    usage_percent=0,
                    warning_level=QuotaWarningLevel.OK,
                    reason="Quota service unavailable - operation allowed",
                )
            raise

    async def enforce_or_raise(
        self,
        customer_id: str,
        tier: PlanTier,
    ) -> QuotaCheckResult:
        """Check quota and raise QuotaExceededError if exceeded.

        Args:
            customer_id: Customer identifier
            tier: Customer's subscription tier

        Returns:
            QuotaCheckResult if allowed

        Raises:
            QuotaExceededError: If any quota is exceeded
        """
        result = await self.check_quota(customer_id, tier)

        if not result.allowed:
            raise QuotaExceededError(
                message=result.reason or "Quota exceeded",
                quota_type=result.quota_type,
                current=result.current,
                limit=result.limit,
                upgrade_url=self._upgrade_url,
                tier=tier,
            )

        return result

    async def get_quota_status(
        self,
        customer_id: str,
        tier: PlanTier,
    ) -> QuotaStatus:
        """Get comprehensive quota status for a customer.

        Returns detailed status for all quota types including
        current usage and warning levels.

        Args:
            customer_id: Customer identifier
            tier: Customer's subscription tier

        Returns:
            QuotaStatus with all quota details
        """
        quota, has_override = self._get_effective_quota(customer_id, tier)

        # Get current usage
        concurrent = await self.usage_tracker.get_concurrent_count(customer_id)
        daily_usage = await self.usage_tracker.get_daily_usage(customer_id)
        monthly_usage = await self.usage_tracker.get_monthly_usage(customer_id)

        # Calculate status for each quota type
        def make_result(
            quota_type: QuotaType,
            current: float,
            limit: float,
        ) -> QuotaCheckResult:
            pct = (current / limit) * 100 if limit > 0 else 0
            level = _calculate_warning_level(pct)
            return QuotaCheckResult(
                allowed=current < limit,
                quota_type=quota_type,
                current=current,
                limit=limit,
                usage_percent=pct,
                warning_level=level,
                reason=None if current < limit else f"{quota_type.value} limit reached",
            )

        concurrent_result = make_result(
            QuotaType.CONCURRENT,
            concurrent,
            quota.max_concurrent_sandboxes,
        )

        daily_minutes_result = make_result(
            QuotaType.DAILY_MINUTES,
            daily_usage.total_minutes,
            quota.max_daily_sandbox_minutes,
        )

        monthly_minutes_result = make_result(
            QuotaType.MONTHLY_MINUTES,
            monthly_usage.total_minutes,
            quota.max_monthly_sandbox_minutes,
        )

        daily_sessions_result = make_result(
            QuotaType.DAILY_SESSIONS,
            daily_usage.sandbox_count,
            quota.max_sandboxes_per_day,
        )

        overall_level = _get_highest_warning_level([
            concurrent_result.warning_level,
            daily_minutes_result.warning_level,
            monthly_minutes_result.warning_level,
            daily_sessions_result.warning_level,
        ])

        return QuotaStatus(
            customer_id=customer_id,
            tier=tier,
            effective_quota=quota,
            concurrent=concurrent_result,
            daily_minutes=daily_minutes_result,
            monthly_minutes=monthly_minutes_result,
            daily_sessions=daily_sessions_result,
            overall_warning_level=overall_level,
            has_override=has_override,
        )

    async def set_override(
        self,
        override: QuotaOverride,
    ) -> None:
        """Set an admin override for a customer's quota.

        Args:
            override: Override to apply
        """
        self._overrides[override.customer_id] = override

        # Persist to database if connected
        if self.usage_tracker._connected:
            loop = asyncio.get_event_loop()

            def _upsert():
                with self.usage_tracker._conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO sandbox_quota_overrides (
                            customer_id,
                            max_concurrent_sandboxes,
                            max_daily_sandbox_minutes,
                            max_monthly_sandbox_minutes,
                            override_reason,
                            created_by
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (customer_id) DO UPDATE SET
                            max_concurrent_sandboxes = EXCLUDED.max_concurrent_sandboxes,
                            max_daily_sandbox_minutes = EXCLUDED.max_daily_sandbox_minutes,
                            max_monthly_sandbox_minutes = EXCLUDED.max_monthly_sandbox_minutes,
                            override_reason = EXCLUDED.override_reason,
                            created_by = EXCLUDED.created_by
                        """,
                        (
                            override.customer_id,
                            override.max_concurrent_sandboxes,
                            override.max_daily_sandbox_minutes,
                            override.max_monthly_sandbox_minutes,
                            override.override_reason,
                            override.created_by,
                        ),
                    )
                self.usage_tracker._conn.commit()

            try:
                await loop.run_in_executor(None, _upsert)
                logger.info(
                    f"Set quota override for {override.customer_id}",
                    extra={"reason": override.override_reason},
                )
            except Exception as e:
                logger.error(f"Failed to persist quota override: {e}")
                raise

    async def remove_override(self, customer_id: str) -> bool:
        """Remove an admin override for a customer.

        Args:
            customer_id: Customer to remove override for

        Returns:
            True if override was removed, False if not found
        """
        if customer_id not in self._overrides:
            return False

        del self._overrides[customer_id]

        # Remove from database if connected
        if self.usage_tracker._connected:
            loop = asyncio.get_event_loop()

            def _delete():
                with self.usage_tracker._conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM sandbox_quota_overrides WHERE customer_id = %s",
                        (customer_id,),
                    )
                self.usage_tracker._conn.commit()

            try:
                await loop.run_in_executor(None, _delete)
                logger.info(f"Removed quota override for {customer_id}")
            except Exception as e:
                logger.error(f"Failed to remove quota override: {e}")
                raise

        return True

    async def get_override(self, customer_id: str) -> Optional[QuotaOverride]:
        """Get the admin override for a customer.

        Args:
            customer_id: Customer identifier

        Returns:
            QuotaOverride if one exists, None otherwise
        """
        return self._overrides.get(customer_id)


# Global enforcer instance (lazy initialization)
_global_enforcer: Optional[QuotaEnforcer] = None


def get_quota_enforcer() -> QuotaEnforcer:
    """Get or create global quota enforcer instance."""
    global _global_enforcer
    if _global_enforcer is None:
        _global_enforcer = QuotaEnforcer()
    return _global_enforcer
