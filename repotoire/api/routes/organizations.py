"""API routes for organization management.

This module provides endpoints for:
- Organization CRUD (list, create, get, update, delete)
- Member management (list, update role, remove)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.auth import ClerkUser, get_current_user
from repotoire.db.models import (
    MemberRole,
    Organization,
    OrganizationMembership,
    PlanTier,
    User,
)
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger
from repotoire.services.audit import get_audit_service

logger = get_logger(__name__)

router = APIRouter(prefix="/orgs", tags=["organizations"])


# =============================================================================
# Request/Response Models
# =============================================================================


class OrganizationResponse(BaseModel):
    """Response model for organization details."""

    id: UUID
    name: str
    slug: str
    plan_tier: str
    member_count: int
    created_at: datetime

    class Config:
        from_attributes = True


class OrganizationDetailResponse(OrganizationResponse):
    """Detailed organization response with additional fields."""

    clerk_org_id: Optional[str] = None
    stripe_customer_id: Optional[str] = None
    plan_expires_at: Optional[datetime] = None
    graph_backend: Optional[str] = None


class CreateOrganizationRequest(BaseModel):
    """Request to create a new organization."""

    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")


class UpdateOrganizationRequest(BaseModel):
    """Request to update organization settings."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)


class MemberResponse(BaseModel):
    """Response model for organization member."""

    id: UUID
    user_id: UUID
    email: str
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    role: str
    joined_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UpdateMemberRoleRequest(BaseModel):
    """Request to update a member's role."""

    role: MemberRole


class MembersListResponse(BaseModel):
    """Response containing list of members."""

    members: List[MemberResponse]
    total: int


# =============================================================================
# Helper Functions
# =============================================================================


async def get_org_by_slug(session: AsyncSession, slug: str) -> Organization | None:
    """Get organization by slug."""
    result = await session.execute(
        select(Organization).where(Organization.slug == slug)
    )
    return result.scalar_one_or_none()


async def get_user_membership(
    session: AsyncSession,
    user: ClerkUser,
    org: Organization,
) -> OrganizationMembership | None:
    """Get user's membership in an organization."""
    db_user = await session.execute(
        select(User).where(User.clerk_user_id == user.user_id)
    )
    user_record = db_user.scalar_one_or_none()
    if not user_record:
        return None

    result = await session.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == user_record.id,
            OrganizationMembership.organization_id == org.id,
        )
    )
    return result.scalar_one_or_none()


async def require_owner(
    session: AsyncSession,
    user: ClerkUser,
    org: Organization,
) -> None:
    """Verify user is the organization owner."""
    membership = await get_user_membership(session, user, org)
    if not membership or membership.role != MemberRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the organization owner can perform this action",
        )


# =============================================================================
# Organization Endpoints
# =============================================================================


@router.get("", response_model=List[OrganizationResponse])
async def list_organizations(
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> List[OrganizationResponse]:
    """List all organizations the user is a member of."""
    # Get user record
    db_user = await session.execute(
        select(User).where(User.clerk_user_id == user.user_id)
    )
    user_record = db_user.scalar_one_or_none()
    if not user_record:
        return []

    # Get organizations with member count
    result = await session.execute(
        select(
            Organization,
            func.count(OrganizationMembership.id).label("member_count"),
        )
        .join(
            OrganizationMembership,
            Organization.id == OrganizationMembership.organization_id,
        )
        .where(OrganizationMembership.user_id == user_record.id)
        .group_by(Organization.id)
    )

    orgs = []
    for org, member_count in result.all():
        orgs.append(
            OrganizationResponse(
                id=org.id,
                name=org.name,
                slug=org.slug,
                plan_tier=org.plan_tier.value,
                member_count=member_count,
                created_at=org.created_at,
            )
        )

    return orgs


@router.post("", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
async def create_organization(
    request: CreateOrganizationRequest,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> OrganizationResponse:
    """Create a new organization.

    The creating user becomes the owner.
    """
    # Check slug uniqueness
    existing = await get_org_by_slug(session, request.slug)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organization slug already exists",
        )

    # Get or create user record
    db_user = await session.execute(
        select(User).where(User.clerk_user_id == user.user_id)
    )
    user_record = db_user.scalar_one_or_none()
    if not user_record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User not found",
        )

    # Create organization
    org = Organization(
        name=request.name,
        slug=request.slug,
        plan_tier=PlanTier.FREE,
    )
    session.add(org)
    await session.flush()  # Get org.id

    # Add creator as owner
    membership = OrganizationMembership(
        user_id=user_record.id,
        organization_id=org.id,
        role=MemberRole.OWNER,
        joined_at=datetime.now(timezone.utc),
    )
    session.add(membership)

    await session.commit()
    await session.refresh(org)

    # Audit log
    audit_service = get_audit_service()
    await audit_service.log(
        db=session,
        event_type="organization.created",
        actor_id=user_record.id,
        organization_id=org.id,
        resource_type="organization",
        resource_id=str(org.id),
        action="create",
        metadata={"name": org.name, "slug": org.slug},
    )
    await session.commit()

    logger.info(f"Organization created: {org.slug} by user {user.user_id}")

    return OrganizationResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        plan_tier=org.plan_tier.value,
        member_count=1,
        created_at=org.created_at,
    )


@router.get("/{slug}", response_model=OrganizationDetailResponse)
async def get_organization(
    slug: str,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> OrganizationDetailResponse:
    """Get organization details by slug.

    User must be a member of the organization.
    """
    org = await get_org_by_slug(session, slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Verify membership
    membership = await get_user_membership(session, user, org)
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this organization",
        )

    # Get member count
    count_result = await session.execute(
        select(func.count(OrganizationMembership.id)).where(
            OrganizationMembership.organization_id == org.id
        )
    )
    member_count = count_result.scalar() or 0

    return OrganizationDetailResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        clerk_org_id=org.clerk_org_id,
        plan_tier=org.plan_tier.value,
        plan_expires_at=org.plan_expires_at,
        stripe_customer_id=(
            org.stripe_customer_id if membership.role == MemberRole.OWNER else None
        ),
        graph_backend=org.graph_backend,
        member_count=member_count,
        created_at=org.created_at,
    )


@router.patch("/{slug}", response_model=OrganizationResponse)
async def update_organization(
    slug: str,
    request: UpdateOrganizationRequest,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> OrganizationResponse:
    """Update organization settings.

    Requires admin or owner role.
    """
    org = await get_org_by_slug(session, slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Verify admin/owner
    membership = await get_user_membership(session, user, org)
    if not membership or membership.role not in [MemberRole.OWNER, MemberRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or owner role required",
        )

    # Update fields
    if request.name is not None:
        org.name = request.name

    await session.commit()
    await session.refresh(org)

    # Get member count
    count_result = await session.execute(
        select(func.count(OrganizationMembership.id)).where(
            OrganizationMembership.organization_id == org.id
        )
    )
    member_count = count_result.scalar() or 0

    logger.info(f"Organization updated: {org.slug}")

    return OrganizationResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        plan_tier=org.plan_tier.value,
        member_count=member_count,
        created_at=org.created_at,
    )


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organization(
    slug: str,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Delete an organization.

    Only the owner can delete the organization.
    This is a permanent action that removes all associated data.
    """
    org = await get_org_by_slug(session, slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Verify owner
    await require_owner(session, user, org)

    # Audit before delete
    db_user = await session.execute(
        select(User).where(User.clerk_user_id == user.user_id)
    )
    user_record = db_user.scalar_one_or_none()

    audit_service = get_audit_service()
    await audit_service.log(
        db=session,
        event_type="organization.deleted",
        actor_id=user_record.id if user_record else None,
        organization_id=org.id,
        resource_type="organization",
        resource_id=str(org.id),
        action="delete",
        metadata={"name": org.name, "slug": org.slug},
    )

    # Delete (cascades to memberships, repos, etc.)
    await session.delete(org)
    await session.commit()

    logger.info(f"Organization deleted: {slug}")


# =============================================================================
# Member Management Endpoints
# =============================================================================


@router.get("/{slug}/members", response_model=MembersListResponse)
async def list_members(
    slug: str,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MembersListResponse:
    """List all members of an organization.

    User must be a member to view the member list.
    """
    org = await get_org_by_slug(session, slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Verify membership
    membership = await get_user_membership(session, user, org)
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this organization",
        )

    # Get members with user details
    result = await session.execute(
        select(OrganizationMembership, User)
        .join(User, OrganizationMembership.user_id == User.id)
        .where(OrganizationMembership.organization_id == org.id)
        .order_by(OrganizationMembership.role, User.email)
    )

    members = []
    for member, user_record in result.all():
        members.append(
            MemberResponse(
                id=member.id,
                user_id=user_record.id,
                email=user_record.email,
                name=user_record.name,
                avatar_url=user_record.avatar_url,
                role=member.role.value,
                joined_at=member.joined_at,
            )
        )

    return MembersListResponse(
        members=members,
        total=len(members),
    )


@router.patch("/{slug}/members/{user_id}", response_model=MemberResponse)
async def update_member_role(
    slug: str,
    user_id: UUID,
    request: UpdateMemberRoleRequest,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MemberResponse:
    """Update a member's role.

    - Admins can change member roles (but not other admins or owners)
    - Owners can change any role except their own
    - Cannot demote the last owner
    """
    org = await get_org_by_slug(session, slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Verify caller is admin or owner
    caller_membership = await get_user_membership(session, user, org)
    if not caller_membership or caller_membership.role not in [
        MemberRole.OWNER,
        MemberRole.ADMIN,
    ]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or owner role required",
        )

    # Get target membership
    result = await session.execute(
        select(OrganizationMembership, User)
        .join(User, OrganizationMembership.user_id == User.id)
        .where(
            OrganizationMembership.organization_id == org.id,
            OrganizationMembership.user_id == user_id,
        )
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    target_membership, target_user = row

    # Permission checks
    if caller_membership.role == MemberRole.ADMIN:
        # Admins can't change owners or other admins
        if target_membership.role in [MemberRole.OWNER, MemberRole.ADMIN]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admins cannot modify owner or admin roles",
            )
        # Admins can't promote to owner
        if request.role == MemberRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only owners can promote to owner",
            )

    # Can't demote the last owner
    if target_membership.role == MemberRole.OWNER and request.role != MemberRole.OWNER:
        owner_count = await session.execute(
            select(func.count(OrganizationMembership.id)).where(
                OrganizationMembership.organization_id == org.id,
                OrganizationMembership.role == MemberRole.OWNER,
            )
        )
        if (owner_count.scalar() or 0) <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot demote the last owner",
            )

    # Update role
    target_membership.role = request.role
    await session.commit()

    # Audit log
    db_user = await session.execute(
        select(User).where(User.clerk_user_id == user.user_id)
    )
    actor = db_user.scalar_one_or_none()

    audit_service = get_audit_service()
    await audit_service.log(
        db=session,
        event_type="member.role_changed",
        actor_id=actor.id if actor else None,
        organization_id=org.id,
        resource_type="membership",
        resource_id=str(target_membership.id),
        action="update",
        metadata={
            "user_email": target_user.email,
            "new_role": request.role.value,
        },
    )
    await session.commit()

    logger.info(
        f"Member role updated: {target_user.email} -> {request.role.value} in {org.slug}"
    )

    return MemberResponse(
        id=target_membership.id,
        user_id=target_user.id,
        email=target_user.email,
        name=target_user.name,
        avatar_url=target_user.avatar_url,
        role=target_membership.role.value,
        joined_at=target_membership.joined_at,
    )


@router.delete("/{slug}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    slug: str,
    user_id: UUID,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Remove a member from the organization.

    - Members can remove themselves (leave)
    - Admins can remove members (but not other admins or owners)
    - Owners can remove anyone except themselves if they're the last owner
    """
    org = await get_org_by_slug(session, slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Get caller info
    caller_db = await session.execute(
        select(User).where(User.clerk_user_id == user.user_id)
    )
    caller_user = caller_db.scalar_one_or_none()

    caller_membership = await get_user_membership(session, user, org)
    if not caller_membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this organization",
        )

    # Get target membership
    result = await session.execute(
        select(OrganizationMembership, User)
        .join(User, OrganizationMembership.user_id == User.id)
        .where(
            OrganizationMembership.organization_id == org.id,
            OrganizationMembership.user_id == user_id,
        )
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    target_membership, target_user = row
    is_self_removal = caller_user and caller_user.id == user_id

    # Permission checks
    if not is_self_removal:
        if caller_membership.role == MemberRole.MEMBER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Members can only remove themselves",
            )
        if caller_membership.role == MemberRole.ADMIN:
            if target_membership.role in [MemberRole.OWNER, MemberRole.ADMIN]:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admins cannot remove owners or other admins",
                )

    # Can't remove the last owner
    if target_membership.role == MemberRole.OWNER:
        owner_count = await session.execute(
            select(func.count(OrganizationMembership.id)).where(
                OrganizationMembership.organization_id == org.id,
                OrganizationMembership.role == MemberRole.OWNER,
            )
        )
        if (owner_count.scalar() or 0) <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot remove the last owner",
            )

    # Remove membership
    await session.delete(target_membership)
    await session.commit()

    # Audit log
    audit_service = get_audit_service()
    await audit_service.log(
        db=session,
        event_type="member.removed" if not is_self_removal else "member.left",
        actor_id=caller_user.id if caller_user else None,
        organization_id=org.id,
        resource_type="membership",
        resource_id=str(target_membership.id),
        action="delete",
        metadata={"user_email": target_user.email},
    )
    await session.commit()

    logger.info(f"Member removed: {target_user.email} from {org.slug}")
