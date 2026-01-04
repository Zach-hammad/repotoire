"""Usage enforcement middleware for plan limits.

This module provides FastAPI dependencies for enforcing subscription
plan limits on API endpoints.
"""

import asyncio

from fastapi import Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from repotoire.api.shared.auth import ClerkUser, get_current_user_or_api_key, require_org
from repotoire.api.shared.services.billing import check_usage_limit, has_feature
from repotoire.db.models import Organization, OrganizationMembership, User
from repotoire.db.session import async_session_factory, get_db
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


async def get_org_from_user_flexible(
    user: ClerkUser,
    db: AsyncSession,
) -> Organization:
    """Get the organization for an authenticated user (JWT or API key).

    Supports lookup by either org_slug (JWT) or org_id (API key).

    Args:
        user: Authenticated Clerk user
        db: Database session

    Returns:
        Organization instance

    Raises:
        HTTPException: If organization not found or user has no org context
    """
    if not user.org_id and not user.org_slug:
        raise HTTPException(
            status_code=403,
            detail="Organization context required. Use an org-scoped API key or select an organization.",
        )

    # Build query to find org by either ID or slug
    conditions = []
    if user.org_slug:
        conditions.append(Organization.slug == user.org_slug)
    if user.org_id:
        conditions.append(Organization.clerk_org_id == user.org_id)

    result = await db.execute(
        select(Organization).where(or_(*conditions))
    )
    org = result.scalar_one_or_none()

    if not org:
        raise HTTPException(
            status_code=404,
            detail="Organization not found. Please ensure your organization is registered.",
        )

    return org


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


def enforce_feature_for_api(feature: str):
    """Create a dependency that enforces feature access for API routes.

    Works with both JWT authentication and API key authentication.
    For API keys, looks up organization by clerk_org_id instead of slug.

    Uses sync database operations in a thread to avoid async context issues
    when combined with the sync Clerk SDK.

    Args:
        feature: The feature key to require (e.g., "api_access", "auto_fix")

    Returns:
        A FastAPI dependency function

    Example:
        @router.post("/search")
        async def search_code(
            org: Organization = Depends(enforce_feature_for_api("api_access")),
        ):
            ...
    """

    async def _get_org_async(org_id: str | None, org_slug: str | None) -> Organization | None:
        """Async database lookup for org."""
        async with async_session_factory() as session:
            conditions = []
            if org_slug:
                conditions.append(Organization.slug == org_slug)
            if org_id:
                conditions.append(Organization.clerk_org_id == org_id)

            if not conditions:
                return None

            # Eagerly load subscription to avoid lazy loading after session closes
            result = await session.execute(
                select(Organization)
                .options(selectinload(Organization.subscription))
                .where(or_(*conditions))
            )
            return result.scalar_one_or_none()

    async def _get_org_from_user_scoped_key(clerk_user_id: str) -> Organization | None:
        """Look up organization from a user-scoped API key.

        For user-scoped API keys (subject = "user_xxx"), we look up the user
        in our database and find their organization membership.

        Args:
            clerk_user_id: The Clerk user ID from the API key subject

        Returns:
            Organization if found, None otherwise
        """
        async with async_session_factory() as session:
            # Find user by Clerk user ID
            result = await session.execute(
                select(User).where(User.clerk_user_id == clerk_user_id)
            )
            db_user = result.scalar_one_or_none()

            if not db_user:
                logger.debug(f"User not found for clerk_user_id: {clerk_user_id}")
                return None

            # Find user's organization membership (take first if multiple)
            result = await session.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.user_id == db_user.id
                ).limit(1)
            )
            membership = result.scalar_one_or_none()

            if not membership:
                logger.debug(f"No org membership found for user: {clerk_user_id}")
                return None

            # Get the organization with subscription eagerly loaded
            result = await session.execute(
                select(Organization)
                .options(selectinload(Organization.subscription))
                .where(Organization.id == membership.organization_id)
            )
            return result.scalar_one_or_none()

    async def _enforce(
        user: ClerkUser = Depends(get_current_user_or_api_key),
    ) -> Organization:
        org = None

        # Try to get org from org context first (org-scoped key or JWT with org)
        if user.org_id or user.org_slug:
            org = await _get_org_async(user.org_id, user.org_slug)

        # If no org context but this is a user-scoped API key, look up user's org
        if not org and user.user_id and user.user_id.startswith("user_"):
            is_api_key = user.claims and user.claims.get("auth_method") == "api_key"
            if is_api_key:
                logger.debug(f"Looking up org for user-scoped API key: {user.user_id}")
                org = await _get_org_from_user_scoped_key(user.user_id)

        if not org:
            raise HTTPException(
                status_code=404,
                detail="Organization not found. Please ensure your organization is registered.",
            )

        # Check if the organization's plan includes the required feature
        if not has_feature(org, feature):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "FEATURE_NOT_AVAILABLE",
                    "message": f"Feature '{feature}' requires a Pro or Enterprise subscription.",
                    "feature": feature,
                    "upgrade_url": "/dashboard/billing/upgrade",
                },
            )

        return org

    return _enforce
