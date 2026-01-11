"""Billing routes for subscription management.

This module provides API endpoints for querying subscription status and usage.
Subscription management (checkout, portal) is now handled by Clerk Billing.

Migration Note (2026-01):
- Checkout and portal endpoints have been removed
- Use Clerk's <PricingTable /> and <AccountPortal /> components instead
- Subscription data is synced from Clerk via webhooks
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from repotoire.api.shared.auth import ClerkUser, get_current_user
from repotoire.api.shared.services.billing import (
    calculate_monthly_price,
    get_current_tier,
    get_current_usage,
    get_org_repos_count,
    get_org_seat_count,
    get_plan_limits,
)
from repotoire.db.models import Organization, PlanTier, SubscriptionStatus
from repotoire.db.session import get_db

router = APIRouter(prefix="/billing", tags=["billing"])


# ============================================================================
# Request/Response Models
# ============================================================================


class UsageInfo(BaseModel):
    """Current usage information for the organization."""

    repos: int = Field(..., description="Number of repositories connected", ge=0)
    analyses: int = Field(..., description="Number of analyses run this billing period", ge=0)
    limits: dict[str, int] = Field(
        ...,
        description="Usage limits (-1 means unlimited)",
        json_schema_extra={"example": {"repos": 10, "analyses": 100}},
    )


class SubscriptionResponse(BaseModel):
    """Response with subscription details and usage."""

    tier: PlanTier = Field(..., description="Current subscription tier (free, pro, enterprise)")
    status: SubscriptionStatus = Field(..., description="Subscription status (active, canceled, past_due)")
    seats: int = Field(..., description="Number of purchased seats", ge=1)
    current_period_end: datetime | None = Field(
        None,
        description="When the current billing period ends",
    )
    cancel_at_period_end: bool = Field(
        ...,
        description="Whether subscription will cancel at period end",
    )
    usage: UsageInfo = Field(..., description="Current usage metrics")
    monthly_cost_cents: int = Field(..., description="Monthly cost in cents", ge=0)

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "tier": "pro",
                "status": "active",
                "seats": 5,
                "current_period_end": "2025-02-15T00:00:00Z",
                "cancel_at_period_end": False,
                "usage": {
                    "repos": 8,
                    "analyses": 45,
                    "limits": {"repos": 50, "analyses": 500},
                },
                "monthly_cost_cents": 4900,
            }
        },
    }


# ============================================================================
# Helper Functions
# ============================================================================


async def get_org_by_slug(db: AsyncSession, slug: str) -> Organization:
    """Get organization by slug with subscription eagerly loaded.

    Args:
        db: Database session
        slug: Organization slug

    Returns:
        Organization instance with subscription loaded

    Raises:
        HTTPException: If organization not found
    """
    result = await db.execute(
        select(Organization)
        .where(Organization.slug == slug)
        .options(selectinload(Organization.subscription))
    )
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


# ============================================================================
# Routes
# ============================================================================


@router.get(
    "/subscription",
    response_model=SubscriptionResponse,
    summary="Get subscription details",
    description="""
Get current subscription and usage information for the organization.

Returns:
- Current tier and subscription status
- Number of purchased seats
- Billing period end date
- Current usage (repos, analyses) vs limits
- Monthly cost

**Note:** Works without an organization - returns free tier defaults for
users not yet in an organization.
    """,
    responses={
        200: {"description": "Subscription details retrieved successfully"},
    },
)
async def get_subscription(
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SubscriptionResponse:
    """Get current subscription and usage for the organization."""
    # Default values for users without an organization
    tier = PlanTier.FREE
    seats = 1
    repos_count = 0
    analyses_count = 0
    status = SubscriptionStatus.ACTIVE
    current_period_end = None
    cancel_at_period_end = False

    # Try to get org info if user is in an organization
    if user.org_slug:
        try:
            org = await get_org_by_slug(db, user.org_slug)

            # Get effective tier and seat count
            tier = get_current_tier(org)
            seats = get_org_seat_count(org)

            # Get usage
            usage = await get_current_usage(db, org.id)
            repos_count = await get_org_repos_count(db, org.id)
            analyses_count = usage.analyses_count if usage else 0

            # Get subscription details
            subscription = org.subscription
            if subscription:
                status = subscription.status
                current_period_end = subscription.current_period_end
                cancel_at_period_end = subscription.cancel_at_period_end
        except HTTPException:
            pass  # Use defaults if org not found

    limits = get_plan_limits(tier)

    # Calculate total limits based on seats
    repos_limit = -1 if limits.repos_per_seat == -1 else limits.repos_per_seat * seats
    analyses_limit = -1 if limits.analyses_per_seat == -1 else limits.analyses_per_seat * seats

    # Calculate monthly cost
    monthly_cost = calculate_monthly_price(tier, seats)

    return SubscriptionResponse(
        tier=tier,
        status=status,
        seats=seats,
        current_period_end=current_period_end,
        cancel_at_period_end=cancel_at_period_end,
        usage=UsageInfo(
            repos=repos_count,
            analyses=analyses_count,
            limits={
                "repos": repos_limit,
                "analyses": analyses_limit,
            },
        ),
        monthly_cost_cents=monthly_cost,
    )


# ============================================================================
# Stub Endpoints (for frontend compatibility during Clerk migration)
# ============================================================================
# These endpoints return empty/placeholder data while frontend transitions
# to using Clerk's <PricingTable /> and <AccountPortal /> components.


class InvoiceResponse(BaseModel):
    """Invoice information."""
    id: str
    amount_due: int
    currency: str
    status: str
    created: datetime
    invoice_pdf: str | None = None


class InvoicesResponse(BaseModel):
    """List of invoices."""
    invoices: list[InvoiceResponse] = Field(default_factory=list)
    hasMore: bool = False


class PaymentMethodResponse(BaseModel):
    """Payment method information."""
    brand: str | None = None
    last4: str | None = None
    exp_month: int | None = None
    exp_year: int | None = None


class PortalUrlResponse(BaseModel):
    """Billing portal URL."""
    url: str | None = None


@router.get(
    "/invoices",
    summary="Get invoices (deprecated)",
    description="This endpoint has been removed. Use Clerk's AccountPortal component.",
    responses={410: {"description": "Resource no longer available"}},
)
async def get_invoices(
    limit: int = 10,
    user: ClerkUser = Depends(get_current_user),
) -> None:
    """Deprecated endpoint - invoices are now managed via Clerk."""
    raise HTTPException(
        status_code=410,
        detail="Invoice management has moved to Clerk. Use the AccountPortal component.",
    )


@router.get(
    "/payment-method",
    summary="Get payment method (deprecated)",
    description="This endpoint has been removed. Use Clerk's AccountPortal component.",
    responses={410: {"description": "Resource no longer available"}},
)
async def get_payment_method(
    user: ClerkUser = Depends(get_current_user),
) -> None:
    """Deprecated endpoint - payment methods are now managed via Clerk."""
    raise HTTPException(
        status_code=410,
        detail="Payment method management has moved to Clerk. Use the AccountPortal component.",
    )


@router.get(
    "/portal",
    summary="Get billing portal URL (deprecated)",
    description="This endpoint has been removed. Use Clerk's AccountPortal component.",
    responses={410: {"description": "Resource no longer available"}},
)
async def get_billing_portal_url(
    user: ClerkUser = Depends(get_current_user),
) -> None:
    """Deprecated endpoint - billing portal is now via Clerk's AccountPortal component."""
    raise HTTPException(
        status_code=410,
        detail="Billing portal has moved to Clerk. Use the AccountPortal component.",
    )


# NOTE: Full checkout, portal, calculate-price, and plans endpoints have been removed.
# Use Clerk's <PricingTable /> and <AccountPortal /> components instead.
# Subscription webhooks from Clerk are handled in /webhooks/clerk
# Plan information is now managed in Clerk Dashboard.
