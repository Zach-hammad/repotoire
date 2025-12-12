"""Billing routes for subscription management.

This module provides API endpoints for managing subscriptions,
creating checkout sessions, and accessing the customer portal.
"""

import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from repotoire.api.shared.auth import ClerkUser, get_clerk_client, get_current_user, require_org
from repotoire.api.shared.services.billing import (
    PLAN_LIMITS,
    calculate_monthly_price,
    get_current_tier,
    get_current_usage,
    get_org_repos_count,
    get_org_seat_count,
    get_plan_limits,
)
from repotoire.api.shared.services.stripe_service import StripeService
from repotoire.db.models import Organization, PlanTier, SubscriptionStatus
from repotoire.db.session import get_db

router = APIRouter(prefix="/billing", tags=["billing"])


# ============================================================================
# Request/Response Models
# ============================================================================


class CheckoutRequest(BaseModel):
    """Request to create a Stripe checkout session."""

    tier: PlanTier = Field(
        ...,
        description="Subscription tier to upgrade to (pro or enterprise)",
    )
    seats: int = Field(
        default=1,
        ge=1,
        le=100,
        description="Number of seats to purchase (determines usage limits)",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "tier": "pro",
                "seats": 5,
            }
        }
    }


class CheckoutResponse(BaseModel):
    """Response with Stripe checkout URL."""

    checkout_url: str = Field(
        ...,
        description="URL to redirect user to Stripe's hosted checkout page",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "checkout_url": "https://checkout.stripe.com/c/pay/cs_test_..."
            }
        }
    }


class PortalResponse(BaseModel):
    """Response with Stripe customer portal URL."""

    portal_url: str = Field(
        ...,
        description="URL to redirect user to Stripe's customer portal",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "portal_url": "https://billing.stripe.com/session/..."
            }
        }
    }


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


class PlanInfo(BaseModel):
    """Information about a subscription plan."""

    tier: PlanTier
    name: str
    base_price_cents: int
    price_per_seat_cents: int
    min_seats: int
    max_seats: int  # -1 for unlimited
    repos_per_seat: int  # -1 for unlimited
    analyses_per_seat: int  # -1 for unlimited
    features: list[str]


class PlansResponse(BaseModel):
    """Response with available plans."""

    plans: list[PlanInfo]
    current_tier: PlanTier
    current_seats: int


class PriceCalculationRequest(BaseModel):
    """Request to calculate price for a tier and seat count."""

    tier: PlanTier
    seats: int = Field(ge=1, le=100)


class PriceCalculationResponse(BaseModel):
    """Response with calculated price."""

    tier: PlanTier
    seats: int
    base_price_cents: int
    seat_price_cents: int
    total_monthly_cents: int
    repos_limit: int
    analyses_limit: int


# ============================================================================
# Helper Functions
# ============================================================================


async def get_or_create_org(db: AsyncSession, slug: str, clerk_org_id: str | None = None) -> Organization:
    """Get organization by slug, creating it if it doesn't exist.

    Args:
        db: Database session
        slug: Organization slug
        clerk_org_id: Clerk organization ID (unused, for future use)

    Returns:
        Organization instance with subscription loaded
    """
    result = await db.execute(
        select(Organization)
        .where(Organization.slug == slug)
        .options(selectinload(Organization.subscription))
    )
    org = result.scalar_one_or_none()
    if not org:
        # Auto-create organization from Clerk data
        org = Organization(
            name=slug.replace("-", " ").title(),  # Convert slug to name
            slug=slug,
            plan_tier=PlanTier.FREE,
        )
        db.add(org)
        await db.flush()
    return org


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


@router.post(
    "/checkout",
    response_model=CheckoutResponse,
    summary="Create checkout session",
    description="""
Create a Stripe Checkout session for subscription upgrade.

**Process:**
1. Creates or retrieves Stripe customer for the organization
2. Generates a Stripe Checkout session URL
3. Returns URL for redirecting user to Stripe's hosted checkout

**After Checkout:**
- Stripe sends webhook to `/webhooks/stripe`
- Subscription is activated automatically
- User is redirected to success URL

**Pricing:**
- Pro: $29/month base + $10/seat
- Enterprise: Custom pricing (contact sales)
    """,
    responses={
        200: {"description": "Checkout session created"},
        400: {
            "description": "Invalid request or user email required",
            "content": {
                "application/json": {
                    "example": {"detail": "Organization slug required"}
                }
            },
        },
        500: {"description": "Failed to create checkout session"},
    },
)
async def create_checkout(
    request: CheckoutRequest,
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> CheckoutResponse:
    """Create a Stripe Checkout session for subscription upgrade."""
    if not user.org_slug:
        raise HTTPException(status_code=400, detail="Organization slug required")

    # Get or create org in database (Clerk org may not be synced yet)
    org = await get_or_create_org(db, user.org_slug, user.org_id)

    # Fetch user from Clerk API to get email (not included in JWT by default)
    clerk = get_clerk_client()
    try:
        clerk_user = clerk.users.get(user_id=user.user_id)
        email = clerk_user.email_addresses[0].email_address if clerk_user.email_addresses else ""
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch user details from Clerk: {e}",
        )
    if not email:
        raise HTTPException(
            status_code=400,
            detail="User email required for billing",
        )

    # Get or create Stripe customer
    customer_id = StripeService.get_or_create_customer(
        org=org,
        email=email,
    )

    # Update org with customer ID if new
    if not org.stripe_customer_id:
        org.stripe_customer_id = customer_id
        await db.commit()

    # Create checkout session with seats
    base_url = os.environ.get("NEXT_PUBLIC_APP_URL", "http://localhost:3000")
    checkout_url = StripeService.create_checkout_session(
        customer_id=customer_id,
        tier=request.tier,
        seats=request.seats,
        success_url=f"{base_url}/dashboard/billing?success=true",
        cancel_url=f"{base_url}/dashboard/billing?canceled=true",
        metadata={
            "organization_id": str(org.id),
            "organization_slug": org.slug,
        },
    )

    return CheckoutResponse(checkout_url=checkout_url)


@router.post(
    "/portal",
    response_model=PortalResponse,
    summary="Access customer portal",
    description="""
Create a Stripe Customer Portal session for self-service billing management.

**Portal Capabilities:**
- View and download invoices
- Update payment methods
- Change subscription plan
- Cancel subscription
- Update billing information

**Requirements:**
- Organization must have an existing Stripe customer (created during first checkout)
    """,
    responses={
        200: {"description": "Portal session created"},
        400: {
            "description": "No billing account found",
            "content": {
                "application/json": {
                    "example": {"detail": "No billing account found. Please subscribe to a plan first."}
                }
            },
        },
        404: {"description": "Organization not found"},
    },
)
async def create_portal(
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> PortalResponse:
    """Create a Stripe Customer Portal session for self-service billing."""
    if not user.org_slug:
        raise HTTPException(status_code=400, detail="Organization slug required")

    org = await get_org_by_slug(db, user.org_slug)

    if not org.stripe_customer_id:
        raise HTTPException(
            status_code=400,
            detail="No billing account found. Please subscribe to a plan first.",
        )

    base_url = os.environ.get("NEXT_PUBLIC_APP_URL", "http://localhost:3000")
    portal_url = StripeService.create_portal_session(
        customer_id=org.stripe_customer_id,
        return_url=f"{base_url}/dashboard/billing",
    )

    return PortalResponse(portal_url=portal_url)


@router.get(
    "/plans",
    response_model=PlansResponse,
    summary="Get available plans",
    description="""
Get all available subscription plans with pricing and limits.

Returns detailed information about each plan including:
- Base price and per-seat pricing
- Repository and analysis limits per seat
- Feature list
- Current tier and seats for comparison

**Available Plans:**
- **Free**: 0/month, 2 repos, 10 analyses/month
- **Pro**: $29/month base + $10/seat, 10 repos/seat, 100 analyses/seat
- **Enterprise**: Custom pricing, unlimited repos and analyses
    """,
    responses={
        200: {"description": "Plans retrieved successfully"},
    },
)
async def get_plans(
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PlansResponse:
    """Get all available subscription plans with pricing and limits."""
    current_tier = PlanTier.FREE
    current_seats = 1

    # Try to get org info if user is in an organization
    if user.org_slug:
        try:
            org = await get_org_by_slug(db, user.org_slug)
            current_tier = get_current_tier(org)
            current_seats = get_org_seat_count(org)
        except HTTPException:
            pass  # Use defaults if org not found

    # Build plan list from PLAN_LIMITS
    plans = []
    for tier, limits in PLAN_LIMITS.items():
        plan_name = tier.value.capitalize()
        plans.append(
            PlanInfo(
                tier=tier,
                name=plan_name,
                base_price_cents=limits.base_price_cents,
                price_per_seat_cents=limits.price_per_seat_cents,
                min_seats=limits.min_seats,
                max_seats=limits.max_seats,
                repos_per_seat=limits.repos_per_seat,
                analyses_per_seat=limits.analyses_per_seat,
                features=limits.features,
            )
        )

    return PlansResponse(
        plans=plans,
        current_tier=current_tier,
        current_seats=current_seats,
    )


@router.post(
    "/calculate-price",
    response_model=PriceCalculationResponse,
    summary="Calculate price",
    description="""
Calculate the total monthly price for a given tier and seat count.

Useful for displaying dynamic pricing in the UI before checkout.

**Calculation:**
- Base price (includes minimum seats)
- Additional seats at per-seat rate
- Total = base + (additional_seats * per_seat_rate)

Returns the calculated limits (repos, analyses) based on seats.
    """,
    responses={
        200: {"description": "Price calculated successfully"},
        400: {"description": "Invalid tier or seat count exceeds maximum"},
    },
)
async def calculate_price(
    request: PriceCalculationRequest,
    user: ClerkUser = Depends(get_current_user),
) -> PriceCalculationResponse:
    """Calculate the total monthly price for a tier and seat count."""
    limits = PLAN_LIMITS.get(request.tier)
    if not limits:
        raise HTTPException(status_code=400, detail=f"Unknown tier: {request.tier.value}")

    # Validate seats
    effective_seats = max(request.seats, limits.min_seats)
    if limits.max_seats != -1 and effective_seats > limits.max_seats:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {limits.max_seats} seats allowed for {request.tier.value}",
        )

    # Calculate prices - base includes min_seats, only charge for additional seats
    additional_seats = max(0, effective_seats - limits.min_seats)
    seat_price = limits.price_per_seat_cents * additional_seats
    total = limits.base_price_cents + seat_price

    # Calculate limits
    repos_limit = -1 if limits.repos_per_seat == -1 else limits.repos_per_seat * effective_seats
    analyses_limit = (
        -1 if limits.analyses_per_seat == -1 else limits.analyses_per_seat * effective_seats
    )

    return PriceCalculationResponse(
        tier=request.tier,
        seats=effective_seats,
        base_price_cents=limits.base_price_cents,
        seat_price_cents=seat_price,
        total_monthly_cents=total,
        repos_limit=repos_limit,
        analyses_limit=analyses_limit,
    )
