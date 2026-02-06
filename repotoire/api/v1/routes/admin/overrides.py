"""Admin API routes for quota override management.

Provides endpoints for creating, viewing, and revoking quota overrides
with full audit trail. Requires admin role.
"""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.shared.auth import ClerkUser, require_org_admin
from repotoire.api.shared.schemas.quota_override import (
    ActiveOverridesResponse,
    QuotaOverrideCreate,
    QuotaOverrideListResponse,
    QuotaOverrideResponse,
    QuotaOverrideRevoke,
)
from repotoire.db.models import Organization, PlanTier, User
from repotoire.db.models.quota_override import QuotaOverrideType
from repotoire.db.repositories.quota_override import (
    OverrideAlreadyRevokedError,
    QuotaOverrideNotFoundError,
    QuotaOverrideRepository,
)
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger
from repotoire.sandbox.quotas import get_quota_for_tier

logger = get_logger(__name__)

router = APIRouter(prefix="/admin/quota-overrides", tags=["admin", "quota-overrides"])


# =============================================================================
# Helper Functions
# =============================================================================


async def _get_db_user(session: AsyncSession, clerk_user_id: str) -> User | None:
    """Get database user from Clerk user ID."""
    result = await session.execute(
        select(User).where(User.clerk_user_id == clerk_user_id)
    )
    return result.scalar_one_or_none()


async def _get_organization(session: AsyncSession, org_id: UUID) -> Organization | None:
    """Get organization by ID."""
    result = await session.execute(
        select(Organization).where(Organization.id == org_id)
    )
    return result.scalar_one_or_none()


async def _get_redis_client() -> aioredis.Redis | None:
    """Get Redis client for caching (returns None if unavailable)."""
    import os

    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        return None

    try:
        client = aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        await client.ping()
        return client
    except Exception as e:
        logger.warning(f"Redis unavailable for quota override cache: {e}")
        return None


def _require_admin_org_id(admin: ClerkUser) -> str:
    """Verify admin has an org_id and return it.

    Security: Admins without an org_id should not be able to perform
    any organization-scoped operations.

    Args:
        admin: The authenticated admin user

    Returns:
        The admin's org_id

    Raises:
        HTTPException: If admin.org_id is None
    """
    if not admin.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization context required for this operation",
        )
    return admin.org_id


def _get_original_limit(tier: PlanTier, override_type: QuotaOverrideType) -> int:
    """Get the original tier limit for an override type."""
    quota = get_quota_for_tier(tier)

    # Map override types to quota attributes
    type_to_attr = {
        QuotaOverrideType.CONCURRENT_SESSIONS: "max_concurrent_sandboxes",
        QuotaOverrideType.DAILY_SANDBOX_MINUTES: "max_daily_sandbox_minutes",
        QuotaOverrideType.MONTHLY_SANDBOX_MINUTES: "max_monthly_sandbox_minutes",
        QuotaOverrideType.SANDBOXES_PER_DAY: "max_sandboxes_per_day",
        # These don't have direct quota mappings, use sensible defaults
        QuotaOverrideType.SANDBOX_MINUTES: "max_daily_sandbox_minutes",
        QuotaOverrideType.STORAGE_GB: 10,  # Default storage
        QuotaOverrideType.ANALYSIS_PER_MONTH: 100,  # Default analyses
        QuotaOverrideType.MAX_REPO_SIZE_MB: 500,  # Default repo size
    }

    attr = type_to_attr.get(override_type)
    if isinstance(attr, int):
        return attr
    if attr:
        return getattr(quota, attr, 0)
    return 0


# =============================================================================
# Admin Endpoints
# =============================================================================


@router.post(
    "",
    response_model=QuotaOverrideResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create quota override",
    description="Grant a quota override to an organization. Requires admin role.",
)
async def create_override(
    body: QuotaOverrideCreate,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> QuotaOverrideResponse:
    """Create a new quota override for an organization.

    Requires admin role. Creates full audit trail.

    Note: Admin can only create overrides for their own organization.
    """
    # Get the admin's database user
    db_user = await _get_db_user(db, admin.user_id)
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin user not found in database",
        )

    # Verify organization exists
    org = await _get_organization(db, body.organization_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization not found: {body.organization_id}",
        )

    # Security: Verify admin has org context and belongs to the target organization
    # Admin can only create overrides for their own organization
    admin_org_id = _require_admin_org_id(admin)
    if org.clerk_org_id != admin_org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot create quota override for another organization",
        )

    # Get original limit from tier
    original_limit = _get_original_limit(org.plan_tier, body.override_type)

    # Get Redis client for caching
    redis = await _get_redis_client()

    try:
        repo = QuotaOverrideRepository(db, redis)
        override = await repo.create(
            organization_id=body.organization_id,
            override_type=body.override_type,
            original_limit=original_limit,
            override_limit=body.override_limit,
            reason=body.reason,
            created_by_id=db_user.id,
            expires_at=body.expires_at,
        )

        # Reload with relationships
        override = await repo.get_by_id(override.id, include_relationships=True)
        return QuotaOverrideResponse.from_db_model(override)

    finally:
        if redis:
            await redis.close()


@router.get(
    "",
    response_model=QuotaOverrideListResponse,
    summary="List quota overrides",
    description="List quota overrides with filters for audit dashboard.",
)
async def list_overrides(
    organization_id: Optional[UUID] = Query(
        None, description="Filter by organization ID"
    ),
    created_by_id: Optional[UUID] = Query(
        None, description="Filter by admin who created"
    ),
    override_type: Optional[QuotaOverrideType] = Query(
        None, description="Filter by override type"
    ),
    include_revoked: bool = Query(False, description="Include revoked overrides"),
    include_expired: bool = Query(False, description="Include expired overrides"),
    limit: int = Query(50, ge=1, le=100, description="Max results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> QuotaOverrideListResponse:
    """List quota overrides with filters for audit dashboard.

    Note: Admin can only view overrides for their own organization.
    """
    # Security: Require admin has org context
    admin_org_id = _require_admin_org_id(admin)

    # Security: If organization_id provided, verify admin belongs to that org
    # If not provided, default to admin's own organization
    if organization_id:
        org = await _get_organization(db, organization_id)
        if org and org.clerk_org_id != admin_org_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot view quota overrides for another organization",
            )
    else:
        # Default to admin's organization - find org by clerk_org_id
        result = await db.execute(
            select(Organization).where(Organization.clerk_org_id == admin_org_id)
        )
        admin_org = result.scalar_one_or_none()
        if admin_org:
            organization_id = admin_org.id

    repo = QuotaOverrideRepository(db)

    overrides, total = await repo.search(
        organization_id=organization_id,
        created_by_id=created_by_id,
        override_type=override_type,
        include_revoked=include_revoked,
        include_expired=include_expired,
        include_relationships=True,
        limit=limit,
        offset=offset,
    )

    return QuotaOverrideListResponse(
        items=[QuotaOverrideResponse.from_db_model(o) for o in overrides],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{override_id}",
    response_model=QuotaOverrideResponse,
    summary="Get quota override",
    description="Get details of a specific quota override.",
)
async def get_override(
    override_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> QuotaOverrideResponse:
    """Get details of a specific quota override.

    Note: Admin can only view overrides for their own organization.
    """
    # Security: Require admin has org context
    admin_org_id = _require_admin_org_id(admin)

    repo = QuotaOverrideRepository(db)

    try:
        override = await repo.get_by_id_or_raise(
            override_id, include_relationships=True
        )

        # Security: Verify admin belongs to the override's organization
        if override.organization.clerk_org_id != admin_org_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot view quota override for another organization",
            )

        return QuotaOverrideResponse.from_db_model(override)
    except QuotaOverrideNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Quota override not found: {override_id}",
        )


@router.post(
    "/{override_id}/revoke",
    response_model=QuotaOverrideResponse,
    summary="Revoke quota override",
    description="Revoke an active quota override. Requires admin role.",
)
async def revoke_override(
    override_id: UUID,
    body: QuotaOverrideRevoke,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> QuotaOverrideResponse:
    """Revoke an active quota override.

    Requires admin role. Creates audit trail entry.

    Note: Admin can only revoke overrides for their own organization.
    """
    # Security: Require admin has org context
    admin_org_id = _require_admin_org_id(admin)

    # Get the admin's database user
    db_user = await _get_db_user(db, admin.user_id)
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin user not found in database",
        )

    # Security: Verify admin belongs to the override's organization
    repo_check = QuotaOverrideRepository(db)
    try:
        override_check = await repo_check.get_by_id_or_raise(
            override_id, include_relationships=True
        )
        if override_check.organization.clerk_org_id != admin_org_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot revoke quota override for another organization",
            )
    except QuotaOverrideNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Quota override not found: {override_id}",
        )

    # Get Redis client for cache invalidation
    redis = await _get_redis_client()

    try:
        repo = QuotaOverrideRepository(db, redis)
        override = await repo.revoke(
            override_id=override_id,
            revoked_by_id=db_user.id,
            reason=body.reason,
        )

        # Reload with relationships
        override = await repo.get_by_id(override.id, include_relationships=True)
        return QuotaOverrideResponse.from_db_model(override)

    except QuotaOverrideNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Quota override not found: {override_id}",
        )
    except OverrideAlreadyRevokedError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Quota override already revoked: {override_id}",
        )
    finally:
        if redis:
            await redis.close()


@router.get(
    "/organization/{organization_id}/active",
    response_model=ActiveOverridesResponse,
    summary="Get active overrides for organization",
    description="Get all active quota overrides for an organization.",
)
async def get_active_overrides(
    organization_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> ActiveOverridesResponse:
    """Get all active quota overrides for an organization.

    Note: Admin can only view overrides for their own organization.
    """
    # Security: Require admin has org context
    admin_org_id = _require_admin_org_id(admin)

    # Verify organization exists
    org = await _get_organization(db, organization_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization not found: {organization_id}",
        )

    # Security: Verify admin belongs to the target organization
    if org.clerk_org_id != admin_org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot view quota overrides for another organization",
        )

    repo = QuotaOverrideRepository(db)
    active_overrides = await repo.get_all_active_overrides(organization_id)

    return ActiveOverridesResponse(
        organization_id=organization_id,
        overrides={
            override_type: override.override_limit
            for override_type, override in active_overrides.items()
        },
    )


@router.get(
    "/organization/{organization_id}/history",
    response_model=List[QuotaOverrideResponse],
    summary="Get override history for organization",
    description="Get full audit history of overrides for an organization.",
)
async def get_override_history(
    organization_id: UUID,
    limit: int = Query(100, ge=1, le=500, description="Max results"),
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> List[QuotaOverrideResponse]:
    """Get full audit history of overrides for an organization.

    Note: Admin can only view history for their own organization.
    """
    # Security: Require admin has org context
    admin_org_id = _require_admin_org_id(admin)

    # Verify organization exists
    org = await _get_organization(db, organization_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization not found: {organization_id}",
        )

    # Security: Verify admin belongs to the target organization
    if org.clerk_org_id != admin_org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot view quota override history for another organization",
        )

    repo = QuotaOverrideRepository(db)
    history = await repo.get_audit_history(organization_id, limit=limit)

    return [QuotaOverrideResponse.from_db_model(o) for o in history]


@router.post(
    "/cleanup-expired",
    summary="Cleanup expired overrides",
    description="Mark expired overrides as revoked for admin's organization only.",
)
async def cleanup_expired_overrides(
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> dict:
    """Mark expired overrides as revoked for admin's organization.

    This is a maintenance task that marks expired overrides with
    revoke_reason = "Expired" for clarity in audit logs.

    Note: Only cleans up overrides for the admin's own organization.
    """
    # Security: Only cleanup overrides for admin's organization
    if not admin.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization context required for cleanup",
        )

    # Find admin's organization
    result = await db.execute(
        select(Organization).where(Organization.clerk_org_id == admin.org_id)
    )
    admin_org = result.scalar_one_or_none()
    if not admin_org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin organization not found",
        )

    repo = QuotaOverrideRepository(db)
    count = await repo.cleanup_expired(organization_id=admin_org.id)

    return {
        "message": f"Marked {count} expired overrides as revoked",
        "count": count,
    }
