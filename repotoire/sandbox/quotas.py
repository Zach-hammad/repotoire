"""Per-customer sandbox usage quotas and limits.

This module defines quota limits per subscription tier that complement
TierSandboxConfig (which defines per-execution resource limits).

TierSandboxConfig (tiers.py) = per-execution limits (memory, CPU, timeout)
SandboxQuota (this file) = per-customer limits (daily minutes, concurrent, monthly)

Example:
    ```python
    from repotoire.sandbox.quotas import get_quota_for_tier, TIER_QUOTAS
    from repotoire.db.models import PlanTier

    # Get quota for a tier
    quota = get_quota_for_tier(PlanTier.PRO)
    print(f"Max daily minutes: {quota.max_daily_sandbox_minutes}")

    # Check if within limits
    if current_minutes < quota.max_daily_sandbox_minutes:
        # Proceed with execution
        pass
    ```
"""

from dataclasses import dataclass
from typing import Optional

from repotoire.db.models import PlanTier


@dataclass(frozen=True)
class SandboxQuota:
    """Per-customer sandbox usage limits.

    These limits are enforced per customer/organization, not per execution.
    They complement TierSandboxConfig which limits individual sandbox resources.

    Attributes:
        max_concurrent_sandboxes: Maximum sandboxes running at once
        max_daily_sandbox_minutes: Maximum minutes of sandbox time per day
        max_monthly_sandbox_minutes: Maximum minutes of sandbox time per month
        max_sandboxes_per_day: Maximum sandbox sessions started per day
        max_cost_per_day_usd: Maximum cost in USD per day (soft limit for alerts)
        max_cost_per_month_usd: Maximum cost in USD per month (soft limit)
    """

    max_concurrent_sandboxes: int
    max_daily_sandbox_minutes: int
    max_monthly_sandbox_minutes: int
    max_sandboxes_per_day: int
    max_cost_per_day_usd: Optional[float] = None
    max_cost_per_month_usd: Optional[float] = None


# Tier to quota mapping
# These quotas complement TIER_SANDBOX_CONFIGS in tiers.py
TIER_QUOTAS: dict[PlanTier, SandboxQuota] = {
    PlanTier.FREE: SandboxQuota(
        max_concurrent_sandboxes=2,
        max_daily_sandbox_minutes=30,  # 30 min/day
        max_monthly_sandbox_minutes=300,  # 5 hours/month
        max_sandboxes_per_day=10,
        max_cost_per_day_usd=0.50,
        max_cost_per_month_usd=5.00,
    ),
    PlanTier.PRO: SandboxQuota(
        max_concurrent_sandboxes=10,
        max_daily_sandbox_minutes=300,  # 5 hours/day
        max_monthly_sandbox_minutes=6000,  # 100 hours/month
        max_sandboxes_per_day=100,
        max_cost_per_day_usd=10.00,
        max_cost_per_month_usd=100.00,
    ),
    PlanTier.ENTERPRISE: SandboxQuota(
        max_concurrent_sandboxes=50,
        max_daily_sandbox_minutes=1440,  # 24 hours (unlimited practical)
        max_monthly_sandbox_minutes=43200,  # 720 hours/month
        max_sandboxes_per_day=500,
        max_cost_per_day_usd=None,  # No limit for enterprise
        max_cost_per_month_usd=None,
    ),
}


def get_quota_for_tier(tier: PlanTier) -> SandboxQuota:
    """Get sandbox quota limits for a subscription tier.

    Args:
        tier: The subscription tier

    Returns:
        SandboxQuota with limits for the tier

    Example:
        >>> quota = get_quota_for_tier(PlanTier.PRO)
        >>> quota.max_concurrent_sandboxes
        10
    """
    return TIER_QUOTAS.get(tier, TIER_QUOTAS[PlanTier.FREE])


def get_default_quota() -> SandboxQuota:
    """Get default quota for unknown/unauthenticated users.

    Returns:
        SandboxQuota with FREE tier limits
    """
    return TIER_QUOTAS[PlanTier.FREE]


@dataclass
class QuotaOverride:
    """Admin override for customer quotas.

    Allows support team to increase limits for specific customers
    without changing their tier.

    Attributes:
        customer_id: Customer to override quotas for
        max_concurrent_sandboxes: Override for concurrent limit (None = use tier default)
        max_daily_sandbox_minutes: Override for daily minutes (None = use tier default)
        max_monthly_sandbox_minutes: Override for monthly minutes (None = use tier default)
        max_sandboxes_per_day: Override for daily sessions (None = use tier default)
        override_reason: Why this override was granted
        created_by: Admin user who created the override
    """

    customer_id: str
    max_concurrent_sandboxes: Optional[int] = None
    max_daily_sandbox_minutes: Optional[int] = None
    max_monthly_sandbox_minutes: Optional[int] = None
    max_sandboxes_per_day: Optional[int] = None
    override_reason: Optional[str] = None
    created_by: Optional[str] = None


def apply_override(
    base_quota: SandboxQuota,
    override: Optional[QuotaOverride],
) -> SandboxQuota:
    """Apply admin override to a base quota.

    Creates a new SandboxQuota with override values applied.
    Only non-None override values replace base quota values.

    Args:
        base_quota: The tier-based quota
        override: Optional admin override

    Returns:
        New SandboxQuota with overrides applied

    Example:
        >>> base = get_quota_for_tier(PlanTier.FREE)
        >>> override = QuotaOverride(
        ...     customer_id="cust_123",
        ...     max_concurrent_sandboxes=5,  # Increase from 2
        ... )
        >>> effective = apply_override(base, override)
        >>> effective.max_concurrent_sandboxes
        5
        >>> effective.max_daily_sandbox_minutes  # Unchanged
        30
    """
    if override is None:
        return base_quota

    return SandboxQuota(
        max_concurrent_sandboxes=(
            override.max_concurrent_sandboxes
            if override.max_concurrent_sandboxes is not None
            else base_quota.max_concurrent_sandboxes
        ),
        max_daily_sandbox_minutes=(
            override.max_daily_sandbox_minutes
            if override.max_daily_sandbox_minutes is not None
            else base_quota.max_daily_sandbox_minutes
        ),
        max_monthly_sandbox_minutes=(
            override.max_monthly_sandbox_minutes
            if override.max_monthly_sandbox_minutes is not None
            else base_quota.max_monthly_sandbox_minutes
        ),
        max_sandboxes_per_day=(
            override.max_sandboxes_per_day
            if override.max_sandboxes_per_day is not None
            else base_quota.max_sandboxes_per_day
        ),
        max_cost_per_day_usd=base_quota.max_cost_per_day_usd,
        max_cost_per_month_usd=base_quota.max_cost_per_month_usd,
    )
