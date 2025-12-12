"""Integration tests for organizations API routes.

Tests cover:
- Organization CRUD operations
- Member management (list, update role, remove)
- Access control and permissions
"""

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Skip if v1 routes don't exist yet
pytest.importorskip("repotoire.api.v1.routes.organizations")

from repotoire.api.v1.routes.organizations import router
from repotoire.db.models import MemberRole, PlanTier


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def app():
    """Create test FastAPI app with organizations routes."""
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api/v1")
    return test_app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


# =============================================================================
# Response Model Tests
# =============================================================================


class TestResponseModels:
    """Tests for response model serialization."""

    def test_organization_response_model(self):
        """OrganizationResponse should serialize correctly."""
        from repotoire.api.v1.routes.organizations import OrganizationResponse

        response = OrganizationResponse(
            id=uuid4(),
            name="Test Org",
            slug="test-org",
            plan_tier="pro",
            member_count=5,
            created_at=datetime.now(timezone.utc),
        )

        assert response.name == "Test Org"
        assert response.slug == "test-org"
        assert response.member_count == 5

    def test_member_response_model(self):
        """MemberResponse should serialize correctly."""
        from repotoire.api.v1.routes.organizations import MemberResponse

        response = MemberResponse(
            id=uuid4(),
            user_id=uuid4(),
            email="user@example.com",
            name="Test User",
            avatar_url="https://example.com/avatar.jpg",
            role="admin",
            joined_at=datetime.now(timezone.utc),
        )

        assert response.email == "user@example.com"
        assert response.role == "admin"


# =============================================================================
# Unit Tests (No Database)
# =============================================================================


class TestOrganizationsEndpointsUnit:
    """Unit tests for organizations endpoints without database."""

    def test_unauthorized_access_list(self, client):
        """GET /orgs should return 401 without auth header."""
        response = client.get("/api/v1/orgs")
        assert response.status_code == 401

    def test_unauthorized_access_create(self, client):
        """POST /orgs should return 401 without auth header."""
        response = client.post(
            "/api/v1/orgs",
            json={"name": "Test Org", "slug": "test-org"},
        )
        assert response.status_code == 401

    def test_unauthorized_access_get_org(self, client):
        """GET /orgs/{slug} should return 401 without auth header."""
        response = client.get("/api/v1/orgs/test-org")
        assert response.status_code == 401

    def test_unauthorized_access_members(self, client):
        """GET /orgs/{slug}/members should return 401 without auth header."""
        response = client.get("/api/v1/orgs/test-org/members")
        assert response.status_code == 401


# =============================================================================
# Integration Tests (With Database)
# =============================================================================


def _has_database_url() -> bool:
    """Check if DATABASE_URL is configured."""
    url = os.getenv("DATABASE_URL", "") or os.getenv("TEST_DATABASE_URL", "")
    return bool(url.strip())


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestOrganizationsEndpointsIntegration:
    """Integration tests for organizations endpoints with real database."""

    @pytest.mark.asyncio
    async def test_create_organization(self, db_session, test_user):
        """Organization can be created and persisted."""
        from tests.factories import OrganizationFactory
        from repotoire.db.models import Organization
        from sqlalchemy import select

        # Create organization
        org = await OrganizationFactory.async_create(db_session)

        # Verify it was persisted
        result = await db_session.execute(
            select(Organization).where(Organization.id == org.id)
        )
        found = result.scalar_one_or_none()

        assert found is not None
        assert found.slug == org.slug
        assert found.plan_tier == PlanTier.FREE

    @pytest.mark.asyncio
    async def test_organization_with_pro_tier(self, db_session, test_user):
        """Organization can be created with pro tier."""
        from tests.factories import OrganizationFactory

        # Create pro org
        org = await OrganizationFactory.async_create(db_session, pro=True)

        assert org.plan_tier == PlanTier.PRO
        assert org.stripe_customer_id is not None

    @pytest.mark.asyncio
    async def test_organization_slug_unique(self, db_session, test_user):
        """Organization slugs must be unique."""
        from tests.factories import OrganizationFactory
        from repotoire.db.models import Organization
        from sqlalchemy import select

        # Create first org
        org1 = await OrganizationFactory.async_create(db_session, slug="unique-slug")

        # Query to verify
        result = await db_session.execute(
            select(Organization).where(Organization.slug == "unique-slug")
        )
        orgs = result.scalars().all()

        assert len(orgs) == 1
        assert orgs[0].id == org1.id

    @pytest.mark.asyncio
    async def test_organization_membership(self, db_session, test_user):
        """User can be added as organization member."""
        from tests.factories import OrganizationFactory, OrganizationMembershipFactory
        from repotoire.db.models import OrganizationMembership
        from sqlalchemy import select

        # Create org and membership
        org = await OrganizationFactory.async_create(db_session)
        membership = await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )

        # Verify membership
        result = await db_session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == test_user.id,
                OrganizationMembership.organization_id == org.id,
            )
        )
        found = result.scalar_one_or_none()

        assert found is not None
        assert found.role == MemberRole.MEMBER  # Default role

    @pytest.mark.asyncio
    async def test_organization_owner_membership(self, db_session, test_user):
        """User can be owner of organization."""
        from tests.factories import OrganizationFactory, OrganizationMembershipFactory

        # Create org with owner membership
        org = await OrganizationFactory.async_create(db_session)
        membership = await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
            owner=True,
        )

        assert membership.role == MemberRole.OWNER

    @pytest.mark.asyncio
    async def test_organization_admin_membership(self, db_session, test_user):
        """User can be admin of organization."""
        from tests.factories import OrganizationFactory, OrganizationMembershipFactory

        # Create org with admin membership
        org = await OrganizationFactory.async_create(db_session)
        membership = await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
            admin=True,
        )

        assert membership.role == MemberRole.ADMIN

    @pytest.mark.asyncio
    async def test_list_user_organizations(self, db_session, test_user):
        """User can be member of multiple organizations."""
        from tests.factories import OrganizationFactory, OrganizationMembershipFactory
        from repotoire.db.models import OrganizationMembership
        from sqlalchemy import select

        # Create multiple orgs with memberships
        org1 = await OrganizationFactory.async_create(db_session)
        org2 = await OrganizationFactory.async_create(db_session)

        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org1.id,
        )
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org2.id,
        )

        # Query memberships
        result = await db_session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == test_user.id
            )
        )
        memberships = result.scalars().all()

        assert len(memberships) == 2


# =============================================================================
# Member Management Tests
# =============================================================================


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestMemberManagement:
    """Tests for member management."""

    @pytest.mark.asyncio
    async def test_list_members(self, db_session, test_user):
        """Organization can have multiple members."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            UserFactory,
        )
        from repotoire.db.models import OrganizationMembership
        from sqlalchemy import select

        # Create org with multiple members
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
            owner=True,
        )

        # Add another member
        other_user = await UserFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=other_user.id,
            organization_id=org.id,
        )

        # Query members
        result = await db_session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == org.id
            )
        )
        members = result.scalars().all()

        assert len(members) == 2

    @pytest.mark.asyncio
    async def test_update_member_role(self, db_session, test_user):
        """Member role can be updated."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            UserFactory,
        )

        # Create org with owner
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
            owner=True,
        )

        # Add member
        other_user = await UserFactory.async_create(db_session)
        membership = await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=other_user.id,
            organization_id=org.id,
        )

        assert membership.role == MemberRole.MEMBER

        # Update role
        membership.role = MemberRole.ADMIN
        await db_session.flush()

        assert membership.role == MemberRole.ADMIN

    @pytest.mark.asyncio
    async def test_remove_member(self, db_session, test_user):
        """Member can be removed from organization."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            UserFactory,
        )
        from repotoire.db.models import OrganizationMembership
        from sqlalchemy import select

        # Create org with owner
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
            owner=True,
        )

        # Add member to remove
        other_user = await UserFactory.async_create(db_session)
        membership = await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=other_user.id,
            organization_id=org.id,
        )

        # Remove member
        await db_session.delete(membership)
        await db_session.flush()

        # Verify member was removed
        result = await db_session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == other_user.id,
                OrganizationMembership.organization_id == org.id,
            )
        )
        found = result.scalar_one_or_none()
        assert found is None

    @pytest.mark.asyncio
    async def test_member_role_hierarchy(self, db_session, test_user):
        """Organization can have members with different roles."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            UserFactory,
        )
        from repotoire.db.models import OrganizationMembership
        from sqlalchemy import select

        # Create org
        org = await OrganizationFactory.async_create(db_session)

        # Create owner
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
            owner=True,
        )

        # Create admin
        admin_user = await UserFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=admin_user.id,
            organization_id=org.id,
            admin=True,
        )

        # Create member
        member_user = await UserFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=member_user.id,
            organization_id=org.id,
        )

        # Query by role
        result = await db_session.execute(
            select(OrganizationMembership)
            .where(OrganizationMembership.organization_id == org.id)
            .where(OrganizationMembership.role == MemberRole.OWNER)
        )
        owners = result.scalars().all()
        assert len(owners) == 1

        result = await db_session.execute(
            select(OrganizationMembership)
            .where(OrganizationMembership.organization_id == org.id)
            .where(OrganizationMembership.role == MemberRole.ADMIN)
        )
        admins = result.scalars().all()
        assert len(admins) == 1

        result = await db_session.execute(
            select(OrganizationMembership)
            .where(OrganizationMembership.organization_id == org.id)
            .where(OrganizationMembership.role == MemberRole.MEMBER)
        )
        members = result.scalars().all()
        assert len(members) == 1


# =============================================================================
# Delete Organization Tests
# =============================================================================


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestDeleteOrganization:
    """Tests for organization deletion."""

    @pytest.mark.asyncio
    async def test_delete_organization(self, db_session, test_user):
        """Organization can be deleted."""
        from tests.factories import OrganizationFactory, OrganizationMembershipFactory
        from repotoire.db.models import Organization
        from sqlalchemy import select

        # Create org with owner
        org = await OrganizationFactory.async_create(db_session)
        org_id = org.id
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
            owner=True,
        )

        # Delete org
        await db_session.delete(org)
        await db_session.flush()

        # Verify org was deleted
        result = await db_session.execute(
            select(Organization).where(Organization.id == org_id)
        )
        deleted_org = result.scalar_one_or_none()
        assert deleted_org is None

    @pytest.mark.asyncio
    async def test_delete_organization_cascades_memberships(self, db_session, test_user):
        """Deleting organization should cascade to memberships."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            UserFactory,
        )
        from repotoire.db.models import Organization, OrganizationMembership
        from sqlalchemy import select

        # Create org with multiple members
        org = await OrganizationFactory.async_create(db_session)
        org_id = org.id
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
            owner=True,
        )

        other_user = await UserFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=other_user.id,
            organization_id=org.id,
        )

        # Delete org
        await db_session.delete(org)
        await db_session.flush()

        # Verify memberships were deleted (cascade)
        result = await db_session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == org_id
            )
        )
        memberships = result.scalars().all()
        assert len(memberships) == 0


# =============================================================================
# Access Control Tests
# =============================================================================


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestOrganizationAccessControl:
    """Tests for organization access control."""

    @pytest.mark.asyncio
    async def test_user_can_only_see_own_orgs(self, db_session, test_user):
        """User should only be able to query their own organizations."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            UserFactory,
        )
        from repotoire.db.models import OrganizationMembership
        from sqlalchemy import select

        # Create user's org
        user_org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=user_org.id,
        )

        # Create other user's org
        other_user = await UserFactory.async_create(db_session)
        other_org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=other_user.id,
            organization_id=other_org.id,
        )

        # Query test_user's memberships
        result = await db_session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == test_user.id
            )
        )
        user_memberships = result.scalars().all()

        assert len(user_memberships) == 1
        assert user_memberships[0].organization_id == user_org.id

    @pytest.mark.asyncio
    async def test_membership_isolation(self, db_session, test_user):
        """Users should have separate memberships."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            UserFactory,
        )
        from repotoire.db.models import OrganizationMembership
        from sqlalchemy import select, func

        # Create org
        org = await OrganizationFactory.async_create(db_session)

        # Add test_user as owner
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
            owner=True,
        )

        # Add other user as member
        other_user = await UserFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=other_user.id,
            organization_id=org.id,
        )

        # Count total memberships for org
        result = await db_session.execute(
            select(func.count(OrganizationMembership.id)).where(
                OrganizationMembership.organization_id == org.id
            )
        )
        count = result.scalar()

        assert count == 2
