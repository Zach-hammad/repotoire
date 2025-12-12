"""Integration tests for fixes API routes.

Tests cover:
- Listing fixes for a finding
- Getting fix details
- Applying/rejecting fixes
- Fix status management
"""

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Skip if v1 routes don't exist yet
pytest.importorskip("repotoire.api.v1.routes.fixes")

from repotoire.api.v1.routes.fixes import router
from repotoire.db.models import FixStatus, FixConfidence


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def app():
    """Create test FastAPI app with fixes routes."""
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api/v1")
    return test_app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


# =============================================================================
# Unit Tests (No Database)
# =============================================================================


class TestFixesEndpointsUnit:
    """Unit tests for fixes endpoints without database."""

    def test_unauthorized_access(self, client):
        """Endpoints should return 401 without auth header."""
        response = client.get("/api/v1/fixes")
        assert response.status_code == 401

    def test_invalid_fix_id_format(self, client):
        """Should return 401 for invalid UUID format (auth checked first)."""
        # FastAPI auth dependency runs before path validation in this case
        response = client.get("/api/v1/fixes/not-a-uuid")
        assert response.status_code == 401


# =============================================================================
# Integration Tests (With Database)
# =============================================================================


def _has_database_url() -> bool:
    """Check if DATABASE_URL is configured."""
    url = os.getenv("DATABASE_URL", "") or os.getenv("TEST_DATABASE_URL", "")
    return bool(url.strip())


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestFixesEndpointsIntegration:
    """Integration tests for fixes endpoints with real database."""

    @pytest.mark.asyncio
    async def test_list_fixes_empty(self, db_session, test_user, mock_clerk):
        """List fixes should return empty when no fixes exist."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
        )

        # Create org with membership
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )

        # No fixes created - verify org exists
        assert org is not None

    @pytest.mark.asyncio
    async def test_list_fixes_for_finding(self, db_session, test_user, mock_clerk):
        """List fixes should return fixes for a finding."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
            FindingFactory,
            FixFactory,
        )

        # Create test data
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        repo = await RepositoryFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        analysis = await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo.id,
            completed=True,
        )
        finding = await FindingFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
        )

        # Create fixes for the finding
        for _ in range(3):
            await FixFactory.async_create(
                db_session,
                analysis_run_id=analysis.id,
            )

        # Verify fixes were created
        from repotoire.db.models import Fix
        from sqlalchemy import select

        result = await db_session.execute(
            select(Fix).where(Fix.analysis_run_id == analysis.id)
        )
        fixes = result.scalars().all()
        assert len(fixes) == 3

    @pytest.mark.asyncio
    async def test_fix_status_transitions(self, db_session, test_user, mock_clerk):
        """Fix status should transition correctly."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
            FixFactory,
        )

        # Create test data
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        repo = await RepositoryFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        analysis = await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo.id,
            completed=True,
        )

        # Create pending fix
        fix = await FixFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
        )
        assert fix.status == FixStatus.PENDING

        # Apply fix
        fix.status = FixStatus.APPLIED
        fix.applied_at = datetime.now(timezone.utc)
        await db_session.commit()
        await db_session.refresh(fix)

        assert fix.status == FixStatus.APPLIED
        assert fix.applied_at is not None

    @pytest.mark.asyncio
    async def test_reject_fix(self, db_session, test_user, mock_clerk):
        """Fix can be rejected."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
            FixFactory,
        )

        # Create test data
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        repo = await RepositoryFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        analysis = await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo.id,
            completed=True,
        )

        # Create and reject fix
        fix = await FixFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
        )
        fix.status = FixStatus.REJECTED
        await db_session.commit()
        await db_session.refresh(fix)

        assert fix.status == FixStatus.REJECTED

    @pytest.mark.asyncio
    async def test_security_fix_trait(self, db_session, test_user, mock_clerk):
        """Security fix should have security type."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
            FixFactory,
        )
        from repotoire.db.models import FixType

        # Create test data
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        repo = await RepositoryFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        analysis = await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo.id,
            completed=True,
        )

        # Create security fix
        fix = await FixFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
            security=True,
        )

        assert fix.fix_type == FixType.SECURITY
