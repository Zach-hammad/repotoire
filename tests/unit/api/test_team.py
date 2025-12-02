"""Unit tests for team invitation API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from repotoire.db.models import (
    InviteStatus,
    MemberRole,
    Organization,
    OrganizationInvite,
    OrganizationMembership,
    PlanTier,
    User,
)


@pytest.fixture
def mock_db():
    """Create a mock async database session."""
    db = AsyncMock()
    return db


@pytest.fixture
def sample_user():
    """Create a sample user."""
    return User(
        id=uuid4(),
        clerk_user_id="user_123",
        email="admin@example.com",
        name="Admin User",
    )


@pytest.fixture
def sample_organization():
    """Create a sample organization."""
    return Organization(
        id=uuid4(),
        name="Test Org",
        slug="test-org",
        plan_tier=PlanTier.PRO,
    )


@pytest.fixture
def sample_invite(sample_organization):
    """Create a sample invitation."""
    return OrganizationInvite(
        id=uuid4(),
        email="invitee@example.com",
        organization_id=sample_organization.id,
        invited_by_id=uuid4(),
        role=MemberRole.MEMBER,
        token="test-token-123",
        status=InviteStatus.PENDING,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


class TestSendInviteRequest:
    """Tests for SendInviteRequest model."""

    def test_valid_request(self):
        """Test creating a valid invite request."""
        from repotoire.api.routes.team import SendInviteRequest

        request = SendInviteRequest(
            email="test@example.com",
            role=MemberRole.MEMBER,
        )
        assert request.email == "test@example.com"
        assert request.role == MemberRole.MEMBER

    def test_default_role(self):
        """Test that role defaults to MEMBER."""
        from repotoire.api.routes.team import SendInviteRequest

        request = SendInviteRequest(email="test@example.com")
        assert request.role == MemberRole.MEMBER


class TestInviteResponse:
    """Tests for InviteResponse model."""

    def test_from_invite(self, sample_invite):
        """Test creating response from invite model."""
        from repotoire.api.routes.team import InviteResponse

        response = InviteResponse(
            id=sample_invite.id,
            email=sample_invite.email,
            role=sample_invite.role.value,
            status=sample_invite.status.value,
            expires_at=sample_invite.expires_at,
            created_at=sample_invite.created_at,
        )
        assert response.email == "invitee@example.com"
        assert response.role == "member"
        assert response.status == "pending"


class TestHelperFunctions:
    """Tests for helper functions."""

    @pytest.mark.asyncio
    async def test_get_user_org(self, mock_db, sample_organization):
        """Test getting user's organization."""
        from repotoire.api.routes.team import get_user_org

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_organization
        mock_db.execute = AsyncMock(return_value=mock_result)

        user = MagicMock()
        user.org_slug = "test-org"

        org = await get_user_org(mock_db, user)
        assert org == sample_organization

    @pytest.mark.asyncio
    async def test_get_user_org_no_slug(self, mock_db):
        """Test getting org when user has no org_slug."""
        from repotoire.api.routes.team import get_user_org

        user = MagicMock()
        user.org_slug = None

        org = await get_user_org(mock_db, user)
        assert org is None

    @pytest.mark.asyncio
    async def test_get_db_user(self, mock_db, sample_user):
        """Test getting database user by Clerk ID."""
        from repotoire.api.routes.team import get_db_user

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_user
        mock_db.execute = AsyncMock(return_value=mock_result)

        user = await get_db_user(mock_db, "user_123")
        assert user == sample_user

    @pytest.mark.asyncio
    async def test_check_user_is_admin_true(
        self, mock_db, sample_user, sample_organization
    ):
        """Test checking if user is admin returns true for admin."""
        from repotoire.api.routes.team import check_user_is_admin

        # Mock get_db_user
        with patch(
            "repotoire.api.routes.team.get_db_user",
            new_callable=AsyncMock,
        ) as mock_get_user:
            mock_get_user.return_value = sample_user

            # Mock membership query
            membership = OrganizationMembership(
                id=uuid4(),
                user_id=sample_user.id,
                organization_id=sample_organization.id,
                role=MemberRole.ADMIN,
            )
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = membership
            mock_db.execute = AsyncMock(return_value=mock_result)

            clerk_user = MagicMock()
            clerk_user.user_id = "user_123"

            result = await check_user_is_admin(mock_db, clerk_user, sample_organization)
            assert result is True

    @pytest.mark.asyncio
    async def test_check_user_is_admin_false(
        self, mock_db, sample_user, sample_organization
    ):
        """Test checking if user is admin returns false for member."""
        from repotoire.api.routes.team import check_user_is_admin

        with patch(
            "repotoire.api.routes.team.get_db_user",
            new_callable=AsyncMock,
        ) as mock_get_user:
            mock_get_user.return_value = sample_user

            # No admin/owner membership found
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_db.execute = AsyncMock(return_value=mock_result)

            clerk_user = MagicMock()
            clerk_user.user_id = "user_123"

            result = await check_user_is_admin(mock_db, clerk_user, sample_organization)
            assert result is False


class TestInviteExpiry:
    """Tests for invitation expiry handling."""

    def test_invite_not_expired(self, sample_invite):
        """Test that fresh invite is not expired."""
        assert sample_invite.expires_at > datetime.now(timezone.utc)

    def test_invite_expired(self, sample_organization):
        """Test detecting expired invite."""
        expired_invite = OrganizationInvite(
            id=uuid4(),
            email="expired@example.com",
            organization_id=sample_organization.id,
            role=MemberRole.MEMBER,
            token="expired-token",
            status=InviteStatus.PENDING,
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            created_at=datetime.now(timezone.utc) - timedelta(days=8),
            updated_at=datetime.now(timezone.utc),
        )
        assert expired_invite.expires_at < datetime.now(timezone.utc)
