"""Usage enforcement middleware for plan limits.

This module provides FastAPI dependencies for enforcing subscription
plan limits on API endpoints.
"""

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.auth import ClerkUser, require_org
from repotoire.api.services.billing import check_usage_limit, has_feature
from repotoire.db.models import Organization
from repotoire.db.session import get_db


async def get_org_from_user(
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> Organization:
    """Get the organization for an authenticated user.

    Args:
        user: Authenticated Clerk user with org context
        db: Database session

    Returns:
        Organization instance

    Raises:
        HTTPException: If organization not found
    """
    if not user.org_slug:
        raise HTTPException(
            status_code=400,
            detail="Organization slug required",
        )

    result = await db.execute(
        select(Organization).where(Organization.slug == user.org_slug)
    )
    org = result.scalar_one_or_none()

    if not org:
        raise HTTPException(
            status_code=404,
            detail="Organization not found",
        )

    return org


async def enforce_repo_limit(
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> Organization:
    """Dependency to enforce repository limit before adding a new repo.

    Use this dependency on endpoints that create new repositories.

    Args:
        user: Authenticated Clerk user with org context
        db: Database session

    Returns:
        Organization instance if within limits

    Raises:
        HTTPException: 403 if limit exceeded with upgrade prompt
    """
    org = await get_org_from_user(user, db)

    result = await check_usage_limit(db, org, "repos")

    if not result.allowed:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "USAGE_LIMIT_EXCEEDED",
                "message": result.message,
                "current": result.current,
                "limit": result.limit,
                "upgrade_url": result.upgrade_url,
            },
        )

    return org


async def enforce_analysis_limit(
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> Organization:
    """Dependency to enforce analysis limit before running an analysis.

    Use this dependency on endpoints that trigger code analysis.

    Args:
        user: Authenticated Clerk user with org context
        db: Database session

    Returns:
        Organization instance if within limits

    Raises:
        HTTPException: 403 if limit exceeded with upgrade prompt
    """
    org = await get_org_from_user(user, db)

    result = await check_usage_limit(db, org, "analyses")

    if not result.allowed:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "USAGE_LIMIT_EXCEEDED",
                "message": result.message,
                "current": result.current,
                "limit": result.limit,
                "upgrade_url": result.upgrade_url,
            },
        )

    return org


def enforce_feature(feature: str):
    """Create a dependency that enforces access to a specific feature.

    Use this to gate endpoints behind specific plan features.

    Args:
        feature: The feature key to require (e.g., "auto_fix", "sso")

    Returns:
        A FastAPI dependency function

    Example:
        @router.post("/auto-fix")
        async def run_auto_fix(
            org: Organization = Depends(enforce_feature("auto_fix")),
        ):
            ...
    """

    async def _enforce(
        user: ClerkUser = Depends(require_org),
        db: AsyncSession = Depends(get_db),
    ) -> Organization:
        org = await get_org_from_user(user, db)

        if not has_feature(org, feature):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "FEATURE_NOT_AVAILABLE",
                    "message": f"Feature '{feature}' is not available on your plan.",
                    "feature": feature,
                    "upgrade_url": "/dashboard/billing/upgrade",
                },
            )

        return org

    return _enforce
