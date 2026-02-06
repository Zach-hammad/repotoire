"""Billing routes for subscription management.

This module provides API endpoints for querying subscription status and usage.
Subscription management (checkout, portal) is now handled by Stripe direct.

Note: Clerk billing was deprecated in favor of direct Stripe integration.
"""

import logging
from datetime import datetime

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

logger = logging.getLogger(__name__)

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


# ============================================================================
# Stripe Checkout & Portal Endpoints
# ============================================================================


class CheckoutRequest(BaseModel):
    """Request to create a checkout session."""
    plan: str = Field(..., description="Plan: 'team' or 'enterprise'")
    seats: int = Field(default=1, ge=1, le=100, description="Number of seats")
    success_url: str | None = Field(None, description="Custom success redirect URL")
    cancel_url: str | None = Field(None, description="Custom cancel redirect URL")


class CheckoutResponse(BaseModel):
    """Checkout session response."""
    checkout_url: str = Field(..., description="Stripe Checkout URL")
    session_id: str = Field(..., description="Stripe session ID")


class PortalResponse(BaseModel):
    """Billing portal response."""
    portal_url: str = Field(..., description="Stripe Customer Portal URL")


@router.post(
    "/checkout",
    response_model=CheckoutResponse,
    summary="Create checkout session",
    description="""
Create a Stripe Checkout session for subscription signup.

Plans:
- **team**: $15/dev/month (billed annually at $180/dev/year)
- **enterprise**: Custom pricing - contact sales

Includes 7-day free trial for team plan.
    """,
)
async def create_checkout(
    request: CheckoutRequest,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CheckoutResponse:
    """Create a Stripe Checkout session for subscription."""
    import os

    from repotoire.api.shared.services import StripeService

    # Validate plan
    price_map = {
        "team": os.environ.get("STRIPE_PRICE_TEAM", ""),
        "enterprise": os.environ.get("STRIPE_PRICE_ENTERPRISE", ""),
    }

    if request.plan not in price_map:
        raise HTTPException(
            status_code=400,
            detail="Invalid plan. Choose 'team' or 'enterprise'.",
        )

    price_id = price_map[request.plan]
    if not price_id:
        raise HTTPException(
            status_code=500,
            detail=f"Price not configured for plan '{request.plan}'. Contact support.",
        )

    # Get or create organization
    org = None
    if user.org_slug:
        try:
            org = await get_org_by_slug(db, user.org_slug)
        except HTTPException:
            pass

    if not org:
        raise HTTPException(
            status_code=400,
            detail="You need to create an organization first.",
        )

    # Get or create Stripe customer
    customer = StripeService.get_or_create_customer(
        email=user.email or f"org-{org.id}@repotoire.io",
        name=org.name,
        metadata={
            "org_id": str(org.id),
            "org_slug": org.slug,
        },
    )

    # Update org with Stripe customer ID
    if not org.stripe_customer_id:
        org.stripe_customer_id = customer.id
        await db.commit()

    # Create checkout session
    trial_days = 7 if request.plan == "team" else None

    session = StripeService.create_checkout_session(
        customer_id=customer.id,
        price_id=price_id,
        quantity=request.seats,
        success_url=request.success_url,
        cancel_url=request.cancel_url,
        trial_days=trial_days,
        metadata={
            "organization_id": str(org.id),
            "org_slug": org.slug,
            "tier": request.plan,  # webhook expects 'tier' not 'plan'
            "seats": str(request.seats),
        },
    )

    logger.info(f"Created checkout session {session.id} for org {org.slug}")

    return CheckoutResponse(
        checkout_url=session.url,
        session_id=session.id,
    )


@router.get(
    "/portal",
    response_model=PortalResponse,
    summary="Get billing portal URL",
    description="""
Get a Stripe Customer Portal URL for managing subscription.

The portal allows customers to:
- Update payment method
- View and download invoices
- Change or cancel subscription
- Update billing information
    """,
)
async def get_billing_portal(
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PortalResponse:
    """Get Stripe Customer Portal URL."""
    from repotoire.api.shared.services import StripeService

    # Get organization
    if not user.org_slug:
        raise HTTPException(
            status_code=400,
            detail="You need to be in an organization to access billing.",
        )

    org = await get_org_by_slug(db, user.org_slug)

    if not org.stripe_customer_id:
        raise HTTPException(
            status_code=400,
            detail="No billing account found. Subscribe to a plan first.",
        )

    # Create portal session
    session = StripeService.create_portal_session(
        customer_id=org.stripe_customer_id,
    )

    logger.info(f"Created portal session for org {org.slug}")

    return PortalResponse(portal_url=session.url)


@router.get(
    "/plans",
    summary="Get available plans",
    description="Get information about available subscription plans.",
)
async def get_plans() -> dict:
    """Get available subscription plans."""
    return {
        "plans": [
            {
                "id": "free",
                "name": "CLI (Free)",
                "description": "For individual developers",
                "price_monthly": 0,
                "features": [
                    "Unlimited local analysis",
                    "42 code detectors",
                    "AI-powered fixes (BYOK)",
                    "Python, JS, TS, Rust, Go",
                ],
            },
            {
                "id": "team",
                "name": "Team",
                "description": "For engineering teams",
                "price_monthly": 15,
                "price_annual": 180,
                "per": "developer",
                "trial_days": 7,
                "features": [
                    "Everything in CLI",
                    "Team dashboard",
                    "Code ownership analysis",
                    "Bus factor alerts",
                    "PR quality gates",
                    "90-day history",
                    "Unlimited repos",
                ],
            },
            {
                "id": "enterprise",
                "name": "Enterprise",
                "description": "For large organizations",
                "price_monthly": "custom",
                "features": [
                    "Everything in Team",
                    "SSO/SAML authentication",
                    "Audit logs",
                    "Custom integrations",
                    "Dedicated support",
                    "SLA guarantee",
                    "Unlimited history",
                    "On-prem option",
                ],
            },
        ],
    }
