"""API routes for team management and invitations.

This module provides endpoints for:
- Sending team invitations
- Accepting/declining invitations
- Listing pending invitations
- Revoking invitations
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.shared.auth import ClerkUser, require_org
from repotoire.db.models import (
    InviteStatus,
    MemberRole,
    Organization,
    OrganizationInvite,
    OrganizationMembership,
    User,
)
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger
from repotoire.services.email import get_email_service

logger = get_logger(__name__)

router = APIRouter(prefix="/team", tags=["team"])

# Invite expires after 7 days
INVITE_EXPIRY_DAYS = 7


# =============================================================================
# Request/Response Models
# =============================================================================


class SendInviteRequest(BaseModel):
    """Request to send a team invitation."""

    email: EmailStr = Field(..., description="Email address to invite")
    role: MemberRole = Field(
        default=MemberRole.MEMBER,
        description="Role for the invited user",
    )


class InviteResponse(BaseModel):
    """Response containing invitation details."""

    id: UUID
    email: str
    role: str
    status: str
    expires_at: datetime
    created_at: datetime
    invited_by: str | None = None


class AcceptInviteRequest(BaseModel):
    """Request to accept an invitation."""

    token: str = Field(..., description="Invitation token")


class PendingInvitesResponse(BaseModel):
    """Response containing list of pending invitations."""

    invites: list[InviteResponse]


# =============================================================================
# Helper Functions
# =============================================================================


async def get_user_org(session: AsyncSession, user: ClerkUser) -> Organization | None:
    """Get user's current organization."""
    if not user.org_slug:
        return None
    result = await session.execute(
        select(Organization).where(Organization.slug == user.org_slug)
    )
    return result.scalar_one_or_none()


async def get_db_user(session: AsyncSession, clerk_user_id: str) -> User | None:
    """Get database user by Clerk user ID."""
    result = await session.execute(
        select(User).where(User.clerk_user_id == clerk_user_id)
    )
    return result.scalar_one_or_none()


async def check_user_is_admin(
    session: AsyncSession,
    user: ClerkUser,
    org: Organization,
) -> bool:
    """Check if user has admin or owner role in the organization."""
    db_user = await get_db_user(session, user.user_id)
    if not db_user:
        return False

    result = await session.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == db_user.id,
            OrganizationMembership.organization_id == org.id,
            OrganizationMembership.role.in_([MemberRole.OWNER.value, MemberRole.ADMIN.value]),
        )
    )
    return result.scalar_one_or_none() is not None


# =============================================================================
# Endpoints
# =============================================================================


@router.post("/invite", response_model=InviteResponse)
async def send_invite(
    request: SendInviteRequest,
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> InviteResponse:
    """Send a team invitation.

    Only admins and owners can send invitations.
    Sends an email to the invited user with a link to accept.
    """
    # Get organization
    org = await get_user_org(session, user)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Check user has permission
    if not await check_user_is_admin(session, user, org):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and owners can send invitations",
        )

    # Check if user is already a member
    existing_member = await session.execute(
        select(OrganizationMembership)
        .join(User)
        .where(
            User.email == request.email,
            OrganizationMembership.organization_id == org.id,
        )
    )
    if existing_member.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is already a member of this organization",
        )

    # Check for existing pending invite
    existing_invite = await session.execute(
        select(OrganizationInvite).where(
            OrganizationInvite.email == request.email,
            OrganizationInvite.organization_id == org.id,
            OrganizationInvite.status == InviteStatus.PENDING,
        )
    )
    if existing_invite.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An invitation has already been sent to this email",
        )

    # Get inviter
    db_user = await get_db_user(session, user.user_id)

    # Create invitation
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=INVITE_EXPIRY_DAYS)

    invite = OrganizationInvite(
        email=request.email,
        organization_id=org.id,
        invited_by_id=db_user.id if db_user else None,
        role=request.role,
        token=token,
        status=InviteStatus.PENDING,
        expires_at=expires_at,
    )
    session.add(invite)
    await session.commit()
    await session.refresh(invite)

    # Send invitation email
    try:
        base_url = os.environ.get("APP_BASE_URL", "https://app.repotoire.io")
        invite_url = f"{base_url}/invite/accept?token={token}"

        email_service = get_email_service()
        await email_service.send_team_invite(
            to=request.email,
            org_name=org.name,
            inviter_name=db_user.name if db_user else "A team member",
            invite_url=invite_url,
        )
        logger.info(f"Sent team invite to {request.email} for org {org.slug}")
    except Exception as e:
        logger.error(f"Failed to send invite email: {e}")
        # Don't fail the request, invite is still created

    return InviteResponse(
        id=invite.id,
        email=invite.email,
        role=invite.role.value,
        status=invite.status.value,
        expires_at=invite.expires_at,
        created_at=invite.created_at,
        invited_by=db_user.name if db_user else None,
    )


@router.get("/invites", response_model=PendingInvitesResponse)
async def list_pending_invites(
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> PendingInvitesResponse:
    """List all pending invitations for the organization.

    Only admins and owners can view invitations.
    """
    org = await get_user_org(session, user)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    if not await check_user_is_admin(session, user, org):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and owners can view invitations",
        )

    result = await session.execute(
        select(OrganizationInvite)
        .where(
            OrganizationInvite.organization_id == org.id,
            OrganizationInvite.status == InviteStatus.PENDING,
        )
        .order_by(OrganizationInvite.created_at.desc())
    )
    invites = result.scalars().all()

    return PendingInvitesResponse(
        invites=[
            InviteResponse(
                id=invite.id,
                email=invite.email,
                role=invite.role.value,
                status=invite.status.value,
                expires_at=invite.expires_at,
                created_at=invite.created_at,
            )
            for invite in invites
        ]
    )


@router.post("/invite/{invite_id}/revoke")
async def revoke_invite(
    invite_id: UUID,
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Revoke a pending invitation.

    Only admins and owners can revoke invitations.
    """
    org = await get_user_org(session, user)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    if not await check_user_is_admin(session, user, org):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and owners can revoke invitations",
        )

    invite = await session.get(OrganizationInvite, invite_id)
    if not invite or invite.organization_id != org.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation not found",
        )

    if invite.status != InviteStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invitation is not pending",
        )

    invite.status = InviteStatus.REVOKED
    await session.commit()

    logger.info(f"Revoked invite {invite_id} for {invite.email}")
    return {"status": "revoked"}


@router.post("/invite/accept")
async def accept_invite(
    request: AcceptInviteRequest,
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Accept a team invitation.

    The user must be logged in. Their account will be added to the organization.
    """
    # Find invitation by token
    result = await session.execute(
        select(OrganizationInvite).where(
            OrganizationInvite.token == request.token,
        )
    )
    invite = result.scalar_one_or_none()

    if not invite:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation not found",
        )

    # Check status
    if invite.status != InviteStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invitation is {invite.status.value}",
        )

    # Check expiry
    if invite.expires_at < datetime.now(timezone.utc):
        invite.status = InviteStatus.EXPIRED
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invitation has expired",
        )

    # Get the user
    db_user = await get_db_user(session, user.user_id)
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Check email matches (optional - could allow any logged-in user)
    if db_user.email.lower() != invite.email.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invitation was sent to a different email address",
        )

    # Check if already a member
    existing = await session.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == db_user.id,
            OrganizationMembership.organization_id == invite.organization_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You are already a member of this organization",
        )

    # Create membership
    membership = OrganizationMembership(
        user_id=db_user.id,
        organization_id=invite.organization_id,
        role=invite.role,
        invited_at=invite.created_at,
        joined_at=datetime.now(timezone.utc),
    )
    session.add(membership)

    # Update invite status
    invite.status = InviteStatus.ACCEPTED
    invite.accepted_at = datetime.now(timezone.utc)

    await session.commit()

    logger.info(f"User {db_user.email} accepted invite to org {invite.organization_id}")
    return {"status": "accepted"}
