"""Best-of-N feature entitlements and access control.

This module manages feature availability based on subscription tier and add-ons.
Best-of-N sampling is:
- Unavailable on Free tier
- Available as paid add-on ($29/month) on Pro tier
- Included free on Enterprise tier

Example:
    ```python
    from repotoire.autofix.entitlements import (
        get_customer_entitlement,
        FeatureAccess,
    )
    from repotoire.db.models import PlanTier

    # Get entitlement for a customer
    entitlement = await get_customer_entitlement(
        customer_id="cust_123",
        tier=PlanTier.PRO,
        db=session,
    )

    if entitlement.is_available:
        # Run Best-of-N
        pass
    else:
        # Show upgrade prompt
        print(f"Upgrade at: {entitlement.upgrade_url}")
    ```
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

from repotoire.db.models import PlanTier
from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class FeatureAccess(str, Enum):
    """Access level for Best-of-N feature."""

    UNAVAILABLE = "unavailable"  # Free tier - cannot use
    ADDON = "addon"  # Pro tier - paid add-on required
    INCLUDED = "included"  # Enterprise - included free


@dataclass(frozen=True)
class BestOfNTierConfig:
    """Best-of-N configuration for a subscription tier.

    Attributes:
        access: Access level (unavailable, addon, included)
        max_n: Maximum number of candidates allowed (0 = disabled)
        monthly_runs_limit: Maximum Best-of-N runs per month (-1 = unlimited)
        addon_price_monthly: Price for Pro tier add-on (None if not applicable)
    """

    access: FeatureAccess
    max_n: int
    monthly_runs_limit: int
    addon_price_monthly: Optional[float] = None


# Tier to Best-of-N configuration mapping
TIER_BEST_OF_N_CONFIG: dict[PlanTier, BestOfNTierConfig] = {
    PlanTier.FREE: BestOfNTierConfig(
        access=FeatureAccess.UNAVAILABLE,
        max_n=0,
        monthly_runs_limit=0,
    ),
    PlanTier.PRO: BestOfNTierConfig(
        access=FeatureAccess.ADDON,
        max_n=5,  # Up to 5 candidates when add-on enabled
        monthly_runs_limit=100,  # 100 Best-of-N runs/month
        addon_price_monthly=29.00,  # $29/month add-on
    ),
    PlanTier.ENTERPRISE: BestOfNTierConfig(
        access=FeatureAccess.INCLUDED,
        max_n=10,  # Up to 10 candidates
        monthly_runs_limit=-1,  # Unlimited (-1)
    ),
}


@dataclass
class BestOfNEntitlement:
    """Customer's Best-of-N feature entitlement.

    This tracks what a customer can do with Best-of-N based on their
    tier, add-on purchases, and current usage.

    Attributes:
        tier: Customer's subscription tier
        access: Access level for the feature
        addon_enabled: True if Pro user purchased the add-on
        max_n: Maximum candidates allowed (0 = disabled)
        monthly_runs_limit: Maximum runs per month (-1 = unlimited)
        monthly_runs_used: Current month's usage
        addon_price: Price string for the add-on (e.g., "$29/month")
    """

    tier: PlanTier
    access: FeatureAccess
    addon_enabled: bool = False
    max_n: int = 0
    monthly_runs_limit: int = 0
    monthly_runs_used: int = 0
    addon_price: Optional[str] = None

    @property
    def is_available(self) -> bool:
        """Check if Best-of-N is available for this customer."""
        if self.access == FeatureAccess.UNAVAILABLE:
            return False
        if self.access == FeatureAccess.ADDON:
            return self.addon_enabled
        return True  # INCLUDED

    @property
    def remaining_runs(self) -> int:
        """Get remaining runs for the month (-1 = unlimited)."""
        if self.monthly_runs_limit == -1:
            return -1
        return max(0, self.monthly_runs_limit - self.monthly_runs_used)

    @property
    def is_within_limit(self) -> bool:
        """Check if customer has runs remaining."""
        if self.monthly_runs_limit == -1:
            return True  # Unlimited
        return self.monthly_runs_used < self.monthly_runs_limit

    @property
    def upgrade_url(self) -> Optional[str]:
        """Get upgrade URL based on current access level."""
        if self.access == FeatureAccess.UNAVAILABLE:
            return "https://repotoire.dev/pricing"
        return None

    @property
    def addon_url(self) -> Optional[str]:
        """Get add-on purchase URL for Pro tier."""
        if self.access == FeatureAccess.ADDON and not self.addon_enabled:
            return "https://repotoire.dev/account/addons"
        return None


def get_tier_config(tier: PlanTier) -> BestOfNTierConfig:
    """Get Best-of-N configuration for a subscription tier.

    Args:
        tier: The subscription tier

    Returns:
        BestOfNTierConfig with limits for the tier
    """
    return TIER_BEST_OF_N_CONFIG.get(tier, TIER_BEST_OF_N_CONFIG[PlanTier.FREE])


async def get_customer_entitlement(
    customer_id: str,
    tier: PlanTier,
    db: Optional["AsyncSession"] = None,
) -> BestOfNEntitlement:
    """Get customer's Best-of-N entitlement based on tier and add-ons.

    This checks:
    1. Base tier configuration (access level, max_n, limits)
    2. Add-on purchases (for Pro tier)
    3. Current month's usage

    Args:
        customer_id: Customer identifier
        tier: Customer's subscription tier
        db: Optional database session for fetching add-on and usage data

    Returns:
        BestOfNEntitlement with customer's current entitlement
    """
    config = get_tier_config(tier)

    # Build base entitlement from tier config
    addon_enabled = False
    monthly_runs_used = 0

    # If we have a database session, check add-on status and usage
    if db is not None:
        try:
            # Check if Pro user has purchased the add-on
            if tier == PlanTier.PRO:
                addon_enabled = await _check_addon_enabled(customer_id, db)

            # Get current month's usage
            monthly_runs_used = await _get_monthly_usage(customer_id, db)

        except Exception as e:
            logger.warning(
                f"Failed to fetch entitlement data for {customer_id}: {e}",
                extra={"tier": tier.value},
            )
            # Fall back to base config without add-on/usage data

    addon_price = None
    if config.addon_price_monthly is not None:
        addon_price = f"${config.addon_price_monthly:.0f}/month"

    return BestOfNEntitlement(
        tier=tier,
        access=config.access,
        addon_enabled=addon_enabled,
        max_n=config.max_n,
        monthly_runs_limit=config.monthly_runs_limit,
        monthly_runs_used=monthly_runs_used,
        addon_price=addon_price,
    )


async def _check_addon_enabled(
    customer_id: str,
    db: "AsyncSession",
) -> bool:
    """Check if customer has Best-of-N add-on enabled.

    Args:
        customer_id: Customer identifier
        db: Database session

    Returns:
        True if add-on is active
    """
    from sqlalchemy import select, and_

    try:
        # Import the model dynamically to avoid circular imports
        from repotoire.db.models.billing import CustomerAddon

        result = await db.execute(
            select(CustomerAddon).where(
                and_(
                    CustomerAddon.customer_id == customer_id,
                    CustomerAddon.addon_type == "best_of_n",
                    CustomerAddon.is_active == True,
                )
            )
        )
        return result.scalar_one_or_none() is not None

    except Exception as e:
        # If the table doesn't exist yet, return False
        logger.debug(f"Could not check addon status: {e}")
        return False


async def _get_monthly_usage(
    customer_id: str,
    db: "AsyncSession",
) -> int:
    """Get customer's Best-of-N usage for the current month.

    Args:
        customer_id: Customer identifier
        db: Database session

    Returns:
        Number of Best-of-N runs this month
    """
    from sqlalchemy import select, and_

    try:
        # Import the model dynamically to avoid circular imports
        from repotoire.db.models.billing import BestOfNUsage

        # Get first day of current month
        today = date.today()
        month_start = today.replace(day=1)

        result = await db.execute(
            select(BestOfNUsage.runs_count).where(
                and_(
                    BestOfNUsage.customer_id == customer_id,
                    BestOfNUsage.month == month_start,
                )
            )
        )
        row = result.scalar_one_or_none()
        return row if row is not None else 0

    except Exception as e:
        # If the table doesn't exist yet, return 0
        logger.debug(f"Could not fetch monthly usage: {e}")
        return 0


async def record_best_of_n_usage(
    customer_id: str,
    candidates_generated: int,
    sandbox_cost_usd: float,
    db: "AsyncSession",
) -> None:
    """Record a Best-of-N run in the usage table.

    Args:
        customer_id: Customer identifier
        candidates_generated: Number of candidates generated
        sandbox_cost_usd: Total sandbox cost for this run
        db: Database session
    """
    from sqlalchemy import text

    try:
        # Get first day of current month
        today = date.today()
        month_start = today.replace(day=1)

        # Upsert usage record
        await db.execute(
            text(
                """
                INSERT INTO best_of_n_usage (
                    customer_id, month, runs_count, candidates_generated, sandbox_cost_usd
                ) VALUES (:customer_id, :month, 1, :candidates, :cost)
                ON CONFLICT (customer_id, month) DO UPDATE SET
                    runs_count = best_of_n_usage.runs_count + 1,
                    candidates_generated = best_of_n_usage.candidates_generated + :candidates,
                    sandbox_cost_usd = best_of_n_usage.sandbox_cost_usd + :cost
                """
            ),
            {
                "customer_id": customer_id,
                "month": month_start,
                "candidates": candidates_generated,
                "cost": sandbox_cost_usd,
            },
        )
        await db.commit()

        logger.debug(
            f"Recorded Best-of-N usage for {customer_id}",
            extra={
                "candidates": candidates_generated,
                "cost_usd": sandbox_cost_usd,
            },
        )

    except Exception as e:
        logger.warning(f"Failed to record Best-of-N usage: {e}")
        await db.rollback()


def get_entitlement_sync(
    customer_id: str,
    tier: PlanTier,
) -> BestOfNEntitlement:
    """Synchronous version of get_customer_entitlement (without DB lookup).

    Use this in CLI contexts where you only need tier-based entitlement
    without add-on or usage checks.

    Args:
        customer_id: Customer identifier
        tier: Customer's subscription tier

    Returns:
        BestOfNEntitlement with tier-based defaults
    """
    config = get_tier_config(tier)

    addon_price = None
    if config.addon_price_monthly is not None:
        addon_price = f"${config.addon_price_monthly:.0f}/month"

    return BestOfNEntitlement(
        tier=tier,
        access=config.access,
        addon_enabled=False,  # Unknown without DB
        max_n=config.max_n,
        monthly_runs_limit=config.monthly_runs_limit,
        monthly_runs_used=0,  # Unknown without DB
        addon_price=addon_price,
    )
