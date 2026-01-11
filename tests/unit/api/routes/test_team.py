"""Unit tests for team invitation API routes.

Tests cover:
- Invite creation
- Expired invite rejection (>7 days)
- Duplicate invite prevention
- Admin permission checks
- Accept/revoke invite flows
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from repotoire.api.shared.auth import ClerkUser
from repotoire.db.models import (
    InviteStatus,
    MemberRole,
    Organization,
    OrganizationInvite,
    OrganizationMembership,
    PlanTier,
    User,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_clerk_user():
    """Create a mock Clerk user with org membership."""
    return ClerkUser(
        user_id="user_admin123",
        session_id="sess_test123",
        org_id="org_test123",
        org_slug="test-org",
        org_role="admin",
    )


@pytest.fixture
def mock_db_user():
    """Create a mock database user."""
    user = User(
        id=uuid4(),
        clerk_user_id="user_admin123",
        email="admin@example.com",
        name="Admin User",
    )
    return user


@pytest.fixture
def mock_organization():
    """Create a mock organization."""
    return Organization(
        id=uuid4(),
        name="Test Organization",
        slug="test-org",
        clerk_org_id="org_test123",
        plan_tier=PlanTier.PRO,
    )


@pytest.fixture
def mock_pending_invite(mock_organization):
    """Create a mock pending invitation."""
    return OrganizationInvite(
        id=uuid4(),
        email="invitee@example.com",
        organization_id=mock_organization.id,
        invited_by_id=uuid4(),
        role=MemberRole.MEMBER,
        token="test-token-123",
        status=InviteStatus.PENDING,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def mock_expired_invite(mock_organization):
    """Create a mock expired invitation."""
    return OrganizationInvite(
        id=uuid4(),
        email="expired@example.com",
        organization_id=mock_organization.id,
        invited_by_id=uuid4(),
        role=MemberRole.MEMBER,
        token="expired-token-123",
        status=InviteStatus.PENDING,
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        created_at=datetime.now(timezone.utc) - timedelta(days=8),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def mock_db():
    """Create a mock async database session."""
    db = AsyncMock()
    return db


# =============================================================================
# Request/Response Model Tests
# =============================================================================


class TestSendInviteRequest:
    """Tests for SendInviteRequest model."""

    def test_valid_request(self):
        """Test creating a valid invite request."""
        from repotoire.api.v1.routes.team import SendInviteRequest

        request = SendInviteRequest(
            email="test@example.com",
            role=MemberRole.MEMBER,
        )
        assert request.email == "test@example.com"
        assert request.role == MemberRole.MEMBER

    def test_default_role_is_member(self):
        """Test that role defaults to MEMBER."""
        from repotoire.api.v1.routes.team import SendInviteRequest

        request = SendInviteRequest(email="test@example.com")
        assert request.role == MemberRole.MEMBER

    def test_admin_role(self):
        """Test creating invite with admin role."""
        from repotoire.api.v1.routes.team import SendInviteRequest

        request = SendInviteRequest(
            email="admin@example.com",
            role=MemberRole.ADMIN,
        )
        assert request.role == MemberRole.ADMIN

    def test_invalid_email_rejected(self):
        """Test that invalid email is rejected."""
        from pydantic import ValidationError
        from repotoire.api.v1.routes.team import SendInviteRequest

        with pytest.raises(ValidationError):
            SendInviteRequest(email="not-an-email")


class TestInviteResponse:
    """Tests for InviteResponse model."""

    def test_from_invite(self, mock_pending_invite):
        """Test creating response from invite model."""
        from repotoire.api.v1.routes.team import InviteResponse

        response = InviteResponse(
            id=mock_pending_invite.id,
            email=mock_pending_invite.email,
            role=mock_pending_invite.role.value,
            status=mock_pending_invite.status.value,
            expires_at=mock_pending_invite.expires_at,
            created_at=mock_pending_invite.created_at,
        )
        assert response.email == "invitee@example.com"
        assert response.role == "member"
        assert response.status == "pending"


# =============================================================================
# Helper Function Tests
# =============================================================================


class TestHelperFunctions:
    """Tests for team route helper functions."""

    @pytest.mark.asyncio
    async def test_get_user_org_returns_org(self, mock_db, mock_clerk_user, mock_organization):
        """Test getting user's organization."""
        from repotoire.api.v1.routes.team import get_user_org

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_organization
        mock_db.execute = AsyncMock(return_value=mock_result)

        org = await get_user_org(mock_db, mock_clerk_user)
        assert org == mock_organization

    @pytest.mark.asyncio
    async def test_get_user_org_no_slug_returns_none(self, mock_db):
        """Test getting org when user has no org_slug."""
        from repotoire.api.v1.routes.team import get_user_org

        user = ClerkUser(user_id="user_123", org_slug=None)
        org = await get_user_org(mock_db, user)
        assert org is None

    @pytest.mark.asyncio
    async def test_get_db_user(self, mock_db, mock_db_user):
        """Test getting database user by Clerk ID."""
        from repotoire.api.v1.routes.team import get_db_user

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_db_user
        mock_db.execute = AsyncMock(return_value=mock_result)

        user = await get_db_user(mock_db, "user_admin123")
        assert user == mock_db_user

    @pytest.mark.asyncio
    async def test_check_user_is_admin_true_for_admin(
        self, mock_db, mock_clerk_user, mock_db_user, mock_organization
    ):
        """Test checking if user is admin returns true for admin."""
        from repotoire.api.v1.routes.team import check_user_is_admin

        with patch(
            "repotoire.api.v1.routes.team.get_db_user",
            new_callable=AsyncMock,
            return_value=mock_db_user,
        ):
            # Mock membership query returning admin membership
            membership = OrganizationMembership(
                id=uuid4(),
                user_id=mock_db_user.id,
                organization_id=mock_organization.id,
                role=MemberRole.ADMIN,
            )
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = membership
            mock_db.execute = AsyncMock(return_value=mock_result)

            result = await check_user_is_admin(mock_db, mock_clerk_user, mock_organization)
            assert result is True

    @pytest.mark.asyncio
    async def test_check_user_is_admin_true_for_owner(
        self, mock_db, mock_clerk_user, mock_db_user, mock_organization
    ):
        """Test checking if user is admin returns true for owner."""
        from repotoire.api.v1.routes.team import check_user_is_admin

        with patch(
            "repotoire.api.v1.routes.team.get_db_user",
            new_callable=AsyncMock,
            return_value=mock_db_user,
        ):
            membership = OrganizationMembership(
                id=uuid4(),
                user_id=mock_db_user.id,
                organization_id=mock_organization.id,
                role=MemberRole.OWNER,
            )
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = membership
            mock_db.execute = AsyncMock(return_value=mock_result)

            result = await check_user_is_admin(mock_db, mock_clerk_user, mock_organization)
            assert result is True

    @pytest.mark.asyncio
    async def test_check_user_is_admin_false_for_member(
        self, mock_db, mock_clerk_user, mock_db_user, mock_organization
    ):
        """Test checking if user is admin returns false for regular member."""
        from repotoire.api.v1.routes.team import check_user_is_admin

        with patch(
            "repotoire.api.v1.routes.team.get_db_user",
            new_callable=AsyncMock,
            return_value=mock_db_user,
        ):
            # No admin/owner membership found
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_db.execute = AsyncMock(return_value=mock_result)

            result = await check_user_is_admin(mock_db, mock_clerk_user, mock_organization)
            assert result is False

    @pytest.mark.asyncio
    async def test_check_user_is_admin_false_when_user_not_found(
        self, mock_db, mock_clerk_user, mock_organization
    ):
        """Test returns false when db user not found."""
        from repotoire.api.v1.routes.team import check_user_is_admin

        with patch(
            "repotoire.api.v1.routes.team.get_db_user",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await check_user_is_admin(mock_db, mock_clerk_user, mock_organization)
            assert result is False


# =============================================================================
# Invite Expiry Tests
# =============================================================================


class TestInviteExpiry:
    """Tests for invitation expiry handling."""

    def test_invite_not_expired(self, mock_pending_invite):
        """Test that fresh invite is not expired."""
        assert mock_pending_invite.expires_at > datetime.now(timezone.utc)

    def test_invite_expired(self, mock_expired_invite):
        """Test detecting expired invite."""
        assert mock_expired_invite.expires_at < datetime.now(timezone.utc)

    @pytest.mark.asyncio
    async def test_accept_expired_invite_rejected(
        self, mock_db, mock_clerk_user, mock_expired_invite, mock_db_user
    ):
        """Test that accepting expired invite raises 400 error."""
        from fastapi import HTTPException
        from repotoire.api.v1.routes.team import accept_invite, AcceptInviteRequest

        # Mock finding the expired invite
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_expired_invite
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        request = AcceptInviteRequest(token="expired-token-123")

        with pytest.raises(HTTPException) as exc_info:
            await accept_invite(
                request=request,
                user=mock_clerk_user,
                session=mock_db,
            )

        assert exc_info.value.status_code == 400
        assert "expired" in exc_info.value.detail.lower()
        # Should have updated status to EXPIRED
        assert mock_expired_invite.status == InviteStatus.EXPIRED


# =============================================================================
# Duplicate Invite Prevention Tests
# =============================================================================


class TestDuplicateInvitePrevention:
    """Tests for duplicate invite prevention."""

    @pytest.mark.asyncio
    async def test_duplicate_pending_invite_rejected(
        self, mock_db, mock_clerk_user, mock_db_user, mock_organization, mock_pending_invite
    ):
        """Test that duplicate pending invite raises 400 error."""
        from fastapi import HTTPException
        from repotoire.api.v1.routes.team import send_invite, SendInviteRequest

        # Mock org lookup
        org_result = MagicMock()
        org_result.scalar_one_or_none.return_value = mock_organization

        # Mock admin check
        membership = OrganizationMembership(
            id=uuid4(),
            user_id=mock_db_user.id,
            organization_id=mock_organization.id,
            role=MemberRole.ADMIN,
        )
        admin_result = MagicMock()
        admin_result.scalar_one_or_none.return_value = membership

        # Mock member check (not a member)
        member_result = MagicMock()
        member_result.scalar_one_or_none.return_value = None

        # Mock existing invite check (already has pending invite)
        existing_invite_result = MagicMock()
        existing_invite_result.scalar_one_or_none.return_value = mock_pending_invite

        # Setup execute to return different results based on call order
        mock_db.execute = AsyncMock(
            side_effect=[
                org_result,
                admin_result,
                member_result,
                existing_invite_result,
            ]
        )

        request = SendInviteRequest(email="invitee@example.com")

        with patch(
            "repotoire.api.v1.routes.team.get_user_org",
            new_callable=AsyncMock,
            return_value=mock_organization,
        ), patch(
            "repotoire.api.v1.routes.team.check_user_is_admin",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "repotoire.api.v1.routes.team.get_db_user",
            new_callable=AsyncMock,
            return_value=mock_db_user,
        ):
            # Reset mock to simulate the actual query behavior
            mock_member_result = MagicMock()
            mock_member_result.scalar_one_or_none.return_value = None

            mock_invite_result = MagicMock()
            mock_invite_result.scalar_one_or_none.return_value = mock_pending_invite

            mock_db.execute = AsyncMock(
                side_effect=[mock_member_result, mock_invite_result]
            )

            with pytest.raises(HTTPException) as exc_info:
                await send_invite(
                    request=request,
                    user=mock_clerk_user,
                    session=mock_db,
                )

            assert exc_info.value.status_code == 400
            assert "already been sent" in exc_info.value.detail.lower()


# =============================================================================
# Send Invite Tests
# =============================================================================


class TestSendInvite:
    """Tests for POST /team/invite endpoint."""

    @pytest.mark.asyncio
    async def test_send_invite_success(
        self, mock_db, mock_clerk_user, mock_db_user, mock_organization
    ):
        """Test successful invite creation."""
        from repotoire.api.v1.routes.team import send_invite, SendInviteRequest

        # Mock all checks pass
        mock_member_result = MagicMock()
        mock_member_result.scalar_one_or_none.return_value = None  # Not a member

        mock_invite_result = MagicMock()
        mock_invite_result.scalar_one_or_none.return_value = None  # No existing invite

        mock_db.execute = AsyncMock(
            side_effect=[mock_member_result, mock_invite_result]
        )
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        request = SendInviteRequest(email="newuser@example.com", role=MemberRole.MEMBER)

        with patch(
            "repotoire.api.v1.routes.team.get_user_org",
            new_callable=AsyncMock,
            return_value=mock_organization,
        ), patch(
            "repotoire.api.v1.routes.team.check_user_is_admin",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "repotoire.api.v1.routes.team.get_db_user",
            new_callable=AsyncMock,
            return_value=mock_db_user,
        ), patch(
            "repotoire.api.v1.routes.team.get_email_service",
        ) as mock_email:
            # Mock email service
            mock_email_instance = AsyncMock()
            mock_email.return_value = mock_email_instance

            # Mock the refresh to set required attributes
            async def mock_refresh_impl(invite):
                invite.id = uuid4()
                invite.created_at = datetime.now(timezone.utc)

            mock_db.refresh.side_effect = mock_refresh_impl

            response = await send_invite(
                request=request,
                user=mock_clerk_user,
                session=mock_db,
            )

            mock_db.add.assert_called_once()
            mock_db.commit.assert_called_once()
            assert response.email == "newuser@example.com"
            assert response.role == "member"
            assert response.status == "pending"

    @pytest.mark.asyncio
    async def test_send_invite_non_admin_rejected(
        self, mock_db, mock_clerk_user, mock_organization
    ):
        """Test that non-admin users cannot send invites."""
        from fastapi import HTTPException
        from repotoire.api.v1.routes.team import send_invite, SendInviteRequest

        request = SendInviteRequest(email="newuser@example.com")

        with patch(
            "repotoire.api.v1.routes.team.get_user_org",
            new_callable=AsyncMock,
            return_value=mock_organization,
        ), patch(
            "repotoire.api.v1.routes.team.check_user_is_admin",
            new_callable=AsyncMock,
            return_value=False,  # Not an admin
        ):
            with pytest.raises(HTTPException) as exc_info:
                await send_invite(
                    request=request,
                    user=mock_clerk_user,
                    session=mock_db,
                )

            assert exc_info.value.status_code == 403
            assert "admin" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_send_invite_existing_member_rejected(
        self, mock_db, mock_clerk_user, mock_db_user, mock_organization
    ):
        """Test that inviting existing member raises 400 error."""
        from fastapi import HTTPException
        from repotoire.api.v1.routes.team import send_invite, SendInviteRequest

        # Mock existing membership
        existing_membership = OrganizationMembership(
            id=uuid4(),
            user_id=uuid4(),
            organization_id=mock_organization.id,
            role=MemberRole.MEMBER,
        )
        mock_member_result = MagicMock()
        mock_member_result.scalar_one_or_none.return_value = existing_membership

        mock_db.execute = AsyncMock(return_value=mock_member_result)

        request = SendInviteRequest(email="existing@example.com")

        with patch(
            "repotoire.api.v1.routes.team.get_user_org",
            new_callable=AsyncMock,
            return_value=mock_organization,
        ), patch(
            "repotoire.api.v1.routes.team.check_user_is_admin",
            new_callable=AsyncMock,
            return_value=True,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await send_invite(
                    request=request,
                    user=mock_clerk_user,
                    session=mock_db,
                )

            assert exc_info.value.status_code == 400
            assert "already a member" in exc_info.value.detail.lower()


# =============================================================================
# Accept Invite Tests
# =============================================================================


class TestAcceptInvite:
    """Tests for POST /team/invite/accept endpoint."""

    @pytest.mark.asyncio
    async def test_accept_invite_token_not_found(self, mock_db, mock_clerk_user):
        """Test that invalid token raises 404 error."""
        from fastapi import HTTPException
        from repotoire.api.v1.routes.team import accept_invite, AcceptInviteRequest

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        request = AcceptInviteRequest(token="invalid-token")

        with pytest.raises(HTTPException) as exc_info:
            await accept_invite(
                request=request,
                user=mock_clerk_user,
                session=mock_db,
            )

        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_accept_invite_already_accepted_rejected(
        self, mock_db, mock_clerk_user, mock_organization
    ):
        """Test that accepting already accepted invite raises 400 error."""
        from fastapi import HTTPException
        from repotoire.api.v1.routes.team import accept_invite, AcceptInviteRequest

        accepted_invite = OrganizationInvite(
            id=uuid4(),
            email="test@example.com",
            organization_id=mock_organization.id,
            role=MemberRole.MEMBER,
            token="accepted-token",
            status=InviteStatus.ACCEPTED,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            created_at=datetime.now(timezone.utc),
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = accepted_invite
        mock_db.execute = AsyncMock(return_value=mock_result)

        request = AcceptInviteRequest(token="accepted-token")

        with pytest.raises(HTTPException) as exc_info:
            await accept_invite(
                request=request,
                user=mock_clerk_user,
                session=mock_db,
            )

        assert exc_info.value.status_code == 400
        assert "accepted" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_accept_invite_email_mismatch_rejected(
        self, mock_db, mock_clerk_user, mock_db_user, mock_organization
    ):
        """Test that email mismatch raises 403 error."""
        from fastapi import HTTPException
        from repotoire.api.v1.routes.team import accept_invite, AcceptInviteRequest

        # Create invite for different email
        invite = OrganizationInvite(
            id=uuid4(),
            email="other@example.com",  # Different from mock_db_user.email
            organization_id=mock_organization.id,
            role=MemberRole.MEMBER,
            token="token-123",
            status=InviteStatus.PENDING,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            created_at=datetime.now(timezone.utc),
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = invite
        mock_db.execute = AsyncMock(return_value=mock_result)

        request = AcceptInviteRequest(token="token-123")

        with patch(
            "repotoire.api.v1.routes.team.get_db_user",
            new_callable=AsyncMock,
            return_value=mock_db_user,  # Has email admin@example.com
        ):
            with pytest.raises(HTTPException) as exc_info:
                await accept_invite(
                    request=request,
                    user=mock_clerk_user,
                    session=mock_db,
                )

            assert exc_info.value.status_code == 403
            assert "different email" in exc_info.value.detail.lower()


# =============================================================================
# Revoke Invite Tests
# =============================================================================


class TestRevokeInvite:
    """Tests for POST /team/invite/{invite_id}/revoke endpoint."""

    @pytest.mark.asyncio
    async def test_revoke_invite_success(
        self, mock_db, mock_clerk_user, mock_pending_invite, mock_organization
    ):
        """Test successful invite revocation."""
        from repotoire.api.v1.routes.team import revoke_invite

        mock_db.get = AsyncMock(return_value=mock_pending_invite)
        mock_db.commit = AsyncMock()

        with patch(
            "repotoire.api.v1.routes.team.get_user_org",
            new_callable=AsyncMock,
            return_value=mock_organization,
        ), patch(
            "repotoire.api.v1.routes.team.check_user_is_admin",
            new_callable=AsyncMock,
            return_value=True,
        ):
            # Update org_id on invite to match
            mock_pending_invite.organization_id = mock_organization.id

            response = await revoke_invite(
                invite_id=mock_pending_invite.id,
                user=mock_clerk_user,
                session=mock_db,
            )

            assert response["status"] == "revoked"
            assert mock_pending_invite.status == InviteStatus.REVOKED
            mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_revoke_invite_not_found(
        self, mock_db, mock_clerk_user, mock_organization
    ):
        """Test that revoking non-existent invite raises 404."""
        from fastapi import HTTPException
        from repotoire.api.v1.routes.team import revoke_invite

        mock_db.get = AsyncMock(return_value=None)

        with patch(
            "repotoire.api.v1.routes.team.get_user_org",
            new_callable=AsyncMock,
            return_value=mock_organization,
        ), patch(
            "repotoire.api.v1.routes.team.check_user_is_admin",
            new_callable=AsyncMock,
            return_value=True,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await revoke_invite(
                    invite_id=uuid4(),
                    user=mock_clerk_user,
                    session=mock_db,
                )

            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_revoke_invite_already_accepted_rejected(
        self, mock_db, mock_clerk_user, mock_organization
    ):
        """Test that revoking accepted invite raises 400."""
        from fastapi import HTTPException
        from repotoire.api.v1.routes.team import revoke_invite

        accepted_invite = OrganizationInvite(
            id=uuid4(),
            email="test@example.com",
            organization_id=mock_organization.id,
            role=MemberRole.MEMBER,
            token="token",
            status=InviteStatus.ACCEPTED,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            created_at=datetime.now(timezone.utc),
        )

        mock_db.get = AsyncMock(return_value=accepted_invite)

        with patch(
            "repotoire.api.v1.routes.team.get_user_org",
            new_callable=AsyncMock,
            return_value=mock_organization,
        ), patch(
            "repotoire.api.v1.routes.team.check_user_is_admin",
            new_callable=AsyncMock,
            return_value=True,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await revoke_invite(
                    invite_id=accepted_invite.id,
                    user=mock_clerk_user,
                    session=mock_db,
                )

            assert exc_info.value.status_code == 400
            assert "not pending" in exc_info.value.detail.lower()
