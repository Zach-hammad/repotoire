"""Usage tracking API for CLI tier enforcement.

Provides endpoints to check current usage against plan limits,
enabling the CLI to enforce tier-based restrictions.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.auth import ClerkUser, get_current_user
from repotoire.api.services.billing import (
    PLAN_LIMITS,
    get_current_tier,
    get_current_usage,
    get_org_repos_count,
    get_org_seat_count,
)
from repotoire.db.models import Organization, OrganizationMembership, User
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/usage", tags=["usage"])


class UsageResponse(BaseModel):
    """Current usage stats response."""

    tier: str = Field(..., description="Current plan tier (free, pro, enterprise)")
    repos_used: int = Field(..., description="Number of repositories in use")
    repos_limit: int = Field(..., description="Maximum repositories allowed (-1 = unlimited)")
    analyses_this_month: int = Field(..., description="Analyses run this billing period")
    analyses_limit: int = Field(..., description="Maximum analyses per month (-1 = unlimited)")
    seats: int = Field(..., description="Number of seats in subscription")


@router.get("", response_model=UsageResponse)
async def get_usage(
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UsageResponse:
    """Get current usage for the authenticated user's organization.

    Returns usage statistics including:
    - Current plan tier
    - Repository usage vs limits
    - Analysis usage vs monthly limits
    - Number of seats

    If the user is not in an organization, returns default free tier limits.
    """
    # Get user from database
    result = await db.execute(select(User).where(User.clerk_user_id == user.user_id))
    db_user = result.scalar_one_or_none()

    if not db_user:
        logger.warning(f"User not found in database: {user.user_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Get user's organization (prefer org from JWT if available)
    org = None

    if user.org_id:
        # User has org context in JWT, use that
        result = await db.execute(
            select(Organization).where(Organization.clerk_org_id == user.org_id)
        )
        org = result.scalar_one_or_none()

    if not org:
        # Fall back to first membership
        result = await db.execute(
            select(OrganizationMembership).where(OrganizationMembership.user_id == db_user.id)
        )
        membership = result.scalars().first()

        if membership:
            result = await db.execute(
                select(Organization).where(Organization.id == membership.organization_id)
            )
            org = result.scalar_one_or_none()

    if not org:
        # User has no organization - return default free tier limits
        from repotoire.db.models import PlanTier

        free_limits = PLAN_LIMITS[PlanTier.FREE]
        return UsageResponse(
            tier="free",
            repos_used=0,
            repos_limit=free_limits.repos_per_seat,
            analyses_this_month=0,
            analyses_limit=free_limits.analyses_per_seat,
            seats=1,
        )

    # Get current tier and limits
    tier = get_current_tier(org)
    limits = PLAN_LIMITS.get(tier, PLAN_LIMITS[tier])
    seats = get_org_seat_count(org)

    # Get current usage
    repos_count = await get_org_repos_count(db, org.id)
    usage_record = await get_current_usage(db, org.id)
    analyses_count = usage_record.analyses_count if usage_record else 0

    # Calculate total limits based on seats
    repos_limit = (
        limits.repos_per_seat if limits.repos_per_seat == -1 else limits.repos_per_seat * seats
    )
    analyses_limit = (
        limits.analyses_per_seat
        if limits.analyses_per_seat == -1
        else limits.analyses_per_seat * seats
    )

    logger.debug(
        f"Usage for org {org.slug}: repos={repos_count}/{repos_limit}, "
        f"analyses={analyses_count}/{analyses_limit}, tier={tier.value}"
    )

    return UsageResponse(
        tier=tier.value,
        repos_used=repos_count,
        repos_limit=repos_limit,
        analyses_this_month=analyses_count,
        analyses_limit=analyses_limit,
        seats=seats,
    )


class UsageIncrementRequest(BaseModel):
    """Request to increment usage counter."""

    usage_type: str = Field(..., description="Type of usage to increment (analyses)")


class UsageIncrementResponse(BaseModel):
    """Response after incrementing usage."""

    success: bool = Field(..., description="Whether increment was successful")
    new_count: int = Field(..., description="New usage count after increment")
    limit: int = Field(..., description="Current limit (-1 = unlimited)")


@router.post("/increment", response_model=UsageIncrementResponse)
async def increment_usage_counter(
    request: UsageIncrementRequest,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UsageIncrementResponse:
    """Increment a usage counter for the organization.

    Used by the CLI to record usage after completing an action.
    Currently supports incrementing 'analyses' count.
    """
    if request.usage_type not in ("analyses",):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid usage type: {request.usage_type}",
        )

    # Get user from database
    result = await db.execute(select(User).where(User.clerk_user_id == user.user_id))
    db_user = result.scalar_one_or_none()

    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Get user's organization
    org = None

    if user.org_id:
        result = await db.execute(
            select(Organization).where(Organization.clerk_org_id == user.org_id)
        )
        org = result.scalar_one_or_none()

    if not org:
        result = await db.execute(
            select(OrganizationMembership).where(OrganizationMembership.user_id == db_user.id)
        )
        membership = result.scalars().first()

        if membership:
            result = await db.execute(
                select(Organization).where(Organization.id == membership.organization_id)
            )
            org = result.scalar_one_or_none()

    if not org:
        # No org - can't increment usage
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization membership required to track usage",
        )

    # Increment the usage
    from repotoire.api.services.billing import increment_usage

    usage_record = await increment_usage(db, org.id, request.usage_type)
    await db.commit()

    # Get limits
    tier = get_current_tier(org)
    limits = PLAN_LIMITS.get(tier, PLAN_LIMITS[tier])
    seats = get_org_seat_count(org)

    analyses_limit = (
        limits.analyses_per_seat
        if limits.analyses_per_seat == -1
        else limits.analyses_per_seat * seats
    )

    return UsageIncrementResponse(
        success=True,
        new_count=usage_record.analyses_count,
        limit=analyses_limit,
    )
