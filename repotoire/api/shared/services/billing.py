"""Billing service for plan limits and usage tracking.

This module provides functionality for checking plan limits, tracking usage,
and enforcing subscription-based access control.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.db.models import (
    Organization,
    PlanTier,
    Subscription,
    SubscriptionStatus,
    UsageRecord,
)

if TYPE_CHECKING:
    pass


@dataclass
class PlanLimits:
    """Limits for a subscription plan tier.

    Attributes:
        repos_per_seat: Repositories allowed per seat (-1 = unlimited)
        analyses_per_seat: Analyses per month per seat (-1 = unlimited)
        min_seats: Minimum number of seats required
        max_seats: Maximum number of seats allowed (-1 = unlimited)
        base_price_cents: Base monthly price in cents (platform fee)
        price_per_seat_cents: Price per seat per month in cents
        features: List of enabled feature keys
    """

    repos_per_seat: int
    analyses_per_seat: int
    min_seats: int
    max_seats: int
    base_price_cents: int
    price_per_seat_cents: int
    features: list[str]


# Plan limits configuration with per-seat pricing
# Free: $0, 1 seat, 1 repo, 10 analyses
# Pro: $33/mo (includes 1 seat), +$10/additional seat, 5 repos/seat, unlimited analyses
# Enterprise: $199/mo (includes 3 seats), +$20/additional seat, unlimited repos, unlimited analyses
#
# Pricing model: base_price_cents includes min_seats, price_per_seat_cents is for ADDITIONAL seats only
PLAN_LIMITS: dict[PlanTier, PlanLimits] = {
    PlanTier.FREE: PlanLimits(
        repos_per_seat=1,
        analyses_per_seat=10,
        min_seats=1,
        max_seats=1,
        base_price_cents=0,
        price_per_seat_cents=0,
        features=["basic_analysis", "community_support"],
    ),
    PlanTier.PRO: PlanLimits(
        repos_per_seat=5,
        analyses_per_seat=-1,  # Unlimited
        min_seats=1,
        max_seats=50,
        base_price_cents=3300,  # $33 (includes 1 seat)
        price_per_seat_cents=1000,  # $10/additional seat
        features=[
            "basic_analysis",
            "advanced_analysis",
            "priority_support",
            "api_access",
            "auto_fix",
        ],
    ),
    PlanTier.ENTERPRISE: PlanLimits(
        repos_per_seat=-1,  # Unlimited
        analyses_per_seat=-1,  # Unlimited
        min_seats=3,
        max_seats=-1,  # Unlimited
        base_price_cents=19900,  # $199 (includes 3 seats)
        price_per_seat_cents=2000,  # $20/additional seat
        features=[
            "basic_analysis",
            "advanced_analysis",
            "sso",
            "sla",
            "dedicated_support",
            "api_access",
            "auto_fix",
            "custom_rules",
            "audit_logs",
        ],
    ),
}


def calculate_monthly_price(tier: PlanTier, seats: int) -> int:
    """Calculate monthly price in cents for a tier and seat count.

    The base price includes the minimum seats. Additional seats beyond
    the minimum are charged at price_per_seat_cents each.

    Examples:
        - Pro with 1 seat: $33 (base includes 1 seat)
        - Pro with 3 seats: $33 + $10*2 = $53
        - Enterprise with 3 seats: $199 (base includes 3 seats)
        - Enterprise with 5 seats: $199 + $20*2 = $239

    Args:
        tier: The plan tier
        seats: Number of seats

    Returns:
        Total monthly price in cents
    """
    limits = PLAN_LIMITS.get(tier, PLAN_LIMITS[PlanTier.FREE])
    effective_seats = max(seats, limits.min_seats)
    additional_seats = max(0, effective_seats - limits.min_seats)
    return limits.base_price_cents + (limits.price_per_seat_cents * additional_seats)


def get_plan_limits(tier: PlanTier) -> PlanLimits:
    """Get the limits for a plan tier.

    Args:
        tier: The plan tier to get limits for

    Returns:
        PlanLimits for the specified tier
    """
    return PLAN_LIMITS.get(tier, PLAN_LIMITS[PlanTier.FREE])


def get_current_tier(org: Organization) -> PlanTier:
    """Get the effective plan tier for an organization.

    Considers subscription status - returns FREE for inactive subscriptions.

    Args:
        org: The organization to check

    Returns:
        The effective PlanTier
    """
    if not org.subscription:
        return org.plan_tier

    if org.subscription.status in (
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.TRIALING,
    ):
        return org.plan_tier

    # Subscription is not active, fall back to free
    return PlanTier.FREE


async def get_current_usage(
    db: AsyncSession,
    org_id: UUID,
) -> UsageRecord | None:
    """Get the current period's usage record for an organization.

    Creates a new usage record if one doesn't exist for the current period.

    Args:
        db: Database session
        org_id: Organization UUID

    Returns:
        The current UsageRecord, or None if organization not found
    """
    now = datetime.now(timezone.utc)
    # Current period is the current month
    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Calculate end of month
    if now.month == 12:
        period_end = period_start.replace(year=now.year + 1, month=1)
    else:
        period_end = period_start.replace(month=now.month + 1)

    # Try to find existing usage record
    result = await db.execute(
        select(UsageRecord).where(
            and_(
                UsageRecord.organization_id == org_id,
                UsageRecord.period_start == period_start,
            )
        )
    )
    usage = result.scalar_one_or_none()

    if usage:
        return usage

    # Create new usage record for current period
    usage = UsageRecord(
        organization_id=org_id,
        period_start=period_start,
        period_end=period_end,
        repos_count=0,
        analyses_count=0,
    )
    db.add(usage)
    await db.flush()

    return usage


async def get_org_repos_count(db: AsyncSession, org_id: UUID) -> int:
    """Get the current count of active repositories for an organization.

    Args:
        db: Database session
        org_id: Organization UUID

    Returns:
        Number of active repositories
    """
    from repotoire.db.models import Repository

    result = await db.execute(
        select(Repository).where(
            and_(
                Repository.organization_id == org_id,
                Repository.is_active == True,  # noqa: E712
            )
        )
    )
    repos = result.scalars().all()
    return len(repos)


async def increment_usage(
    db: AsyncSession,
    org_id: UUID,
    usage_type: str,
    amount: int = 1,
) -> UsageRecord:
    """Increment usage counter for an organization.

    Args:
        db: Database session
        org_id: Organization UUID
        usage_type: Type of usage to increment ("repos" or "analyses")
        amount: Amount to increment by (default: 1)

    Returns:
        Updated UsageRecord
    """
    usage = await get_current_usage(db, org_id)
    if not usage:
        raise ValueError(f"Could not get usage record for org {org_id}")

    if usage_type == "repos":
        usage.repos_count += amount
    elif usage_type == "analyses":
        usage.analyses_count += amount

    await db.flush()
    return usage


@dataclass
class UsageLimitResult:
    """Result of a usage limit check.

    Attributes:
        allowed: Whether the operation is allowed
        message: Human-readable message explaining the result
        current: Current usage count
        limit: Maximum allowed (-1 for unlimited)
        upgrade_url: URL to upgrade (if limit exceeded)
    """

    allowed: bool
    message: str
    current: int
    limit: int
    upgrade_url: str | None = None


def get_org_seat_count(org: Organization) -> int:
    """Get the number of seats for an organization.

    Args:
        org: Organization to check

    Returns:
        Number of seats (minimum 1)
    """
    if org.subscription and org.subscription.seat_count:
        return org.subscription.seat_count
    return 1


async def check_usage_limit(
    db: AsyncSession,
    org: Organization,
    limit_type: str,
) -> UsageLimitResult:
    """Check if an organization is within usage limits.

    Limits are calculated based on per-seat allowances.

    Args:
        db: Database session
        org: Organization to check
        limit_type: Type of limit to check ("repos" or "analyses")

    Returns:
        UsageLimitResult with allowed status and details
    """
    tier = get_current_tier(org)
    limits = get_plan_limits(tier)
    seats = get_org_seat_count(org)

    if limit_type == "repos":
        # For repos, count actual active repos
        current = await get_org_repos_count(db, org.id)
        # Calculate total limit based on seats
        per_seat_limit = limits.repos_per_seat
        if per_seat_limit == -1:
            limit = -1
        else:
            limit = per_seat_limit * seats

        if limit == -1:
            return UsageLimitResult(
                allowed=True,
                message="Unlimited repositories",
                current=current,
                limit=-1,
            )

        if current >= limit:
            return UsageLimitResult(
                allowed=False,
                message=f"Repository limit reached ({current}/{limit}). Add more seats or upgrade.",
                current=current,
                limit=limit,
                upgrade_url="/dashboard/billing",
            )

        return UsageLimitResult(
            allowed=True,
            message=f"{current}/{limit} repositories used",
            current=current,
            limit=limit,
        )

    elif limit_type == "analyses":
        usage = await get_current_usage(db, org.id)
        current = usage.analyses_count if usage else 0
        # Calculate total limit based on seats
        per_seat_limit = limits.analyses_per_seat
        if per_seat_limit == -1:
            limit = -1
        else:
            limit = per_seat_limit * seats

        if limit == -1:
            return UsageLimitResult(
                allowed=True,
                message="Unlimited analyses",
                current=current,
                limit=-1,
            )

        if current >= limit:
            return UsageLimitResult(
                allowed=False,
                message=f"Monthly analysis limit reached ({current}/{limit}). Add more seats or upgrade.",
                current=current,
                limit=limit,
                upgrade_url="/dashboard/billing",
            )

        return UsageLimitResult(
            allowed=True,
            message=f"{current}/{limit} analyses this month",
            current=current,
            limit=limit,
        )

    # Unknown limit type, allow by default
    return UsageLimitResult(
        allowed=True,
        message="Unknown limit type",
        current=0,
        limit=-1,
    )


def has_feature(org: Organization, feature: str) -> bool:
    """Check if an organization has access to a specific feature.

    Args:
        org: Organization to check
        feature: Feature key to check

    Returns:
        True if the organization has access to the feature
    """
    tier = get_current_tier(org)
    limits = get_plan_limits(tier)
    return feature in limits.features
