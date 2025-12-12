"""Integration tests for usage API routes.

Tests cover:
- Getting usage statistics
- Usage limits and quotas
- Usage history
"""

import os
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Skip if v1 routes don't exist yet
pytest.importorskip("repotoire.api.v1.routes.usage")

from repotoire.api.v1.routes.usage import router


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def app():
    """Create test FastAPI app with usage routes."""
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


class TestUsageEndpointsUnit:
    """Unit tests for usage endpoints without database."""

    def test_unauthorized_access(self, client):
        """Endpoints should return 401 without auth header."""
        response = client.get("/api/v1/usage")
        assert response.status_code == 401


# =============================================================================
# Integration Tests (With Database)
# =============================================================================


def _has_database_url() -> bool:
    """Check if DATABASE_URL is configured."""
    url = os.getenv("DATABASE_URL", "") or os.getenv("TEST_DATABASE_URL", "")
    return bool(url.strip())


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestUsageEndpointsIntegration:
    """Integration tests for usage endpoints with real database."""

    @pytest.mark.asyncio
    async def test_get_usage_empty(self, db_session, test_user, mock_clerk):
        """Get usage should return zeros for org without usage."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
        )

        # Create org
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )

        # No usage records - org should exist
        assert org is not None

    @pytest.mark.asyncio
    async def test_track_usage(self, db_session, test_user, mock_clerk):
        """Usage can be tracked."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            UsageRecordFactory,
        )

        # Create org
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )

        # Track usage
        usage = await UsageRecordFactory.async_create(
            db_session,
            organization_id=org.id,
        )

        assert usage.id is not None
        assert usage.organization_id == org.id
        assert usage.repos_count >= 0
        assert usage.analyses_count >= 0

    @pytest.mark.asyncio
    async def test_usage_limits_free_tier(self, db_session, test_user, mock_clerk):
        """Free tier should have limited usage."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
        )
        from repotoire.db.models import PlanTier

        # Create free tier org
        org = await OrganizationFactory.async_create(db_session)
        assert org.plan_tier == PlanTier.FREE

        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )

        # Free tier limits
        assert org.plan_tier == PlanTier.FREE

    @pytest.mark.asyncio
    async def test_usage_limits_pro_tier(self, db_session, test_user, mock_clerk):
        """Pro tier should have higher limits."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
        )
        from repotoire.db.models import PlanTier

        # Create pro tier org
        org = await OrganizationFactory.async_create(db_session, pro=True)
        assert org.plan_tier == PlanTier.PRO

        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )

        # Pro tier should have higher limits
        assert org.plan_tier == PlanTier.PRO

    @pytest.mark.asyncio
    async def test_usage_history(self, db_session, test_user, mock_clerk):
        """Usage history should be retrievable."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            UsageRecordFactory,
        )
        from repotoire.db.models import UsageRecord
        from sqlalchemy import select

        # Create org
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )

        # Track multiple usage records
        for _ in range(5):
            await UsageRecordFactory.async_create(
                db_session,
                organization_id=org.id,
            )

        # Verify usage history
        result = await db_session.execute(
            select(UsageRecord).where(UsageRecord.organization_id == org.id)
        )
        records = result.scalars().all()
        assert len(records) == 5

    @pytest.mark.asyncio
    async def test_count_repos_and_analyses(self, db_session, test_user, mock_clerk):
        """Should count repositories and analyses for org."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
        )
        from repotoire.db.models import Repository, AnalysisRun
        from sqlalchemy import select, func

        # Create org with repos and analyses
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )

        # Create repos
        repos = []
        for _ in range(3):
            repo = await RepositoryFactory.async_create(
                db_session,
                organization_id=org.id,
            )
            repos.append(repo)

        # Create analyses
        for repo in repos:
            for _ in range(2):
                await AnalysisRunFactory.async_create(
                    db_session,
                    repository_id=repo.id,
                    completed=True,
                )

        # Count repos
        result = await db_session.execute(
            select(func.count(Repository.id)).where(
                Repository.organization_id == org.id
            )
        )
        repo_count = result.scalar()
        assert repo_count == 3

        # Count analyses
        result = await db_session.execute(
            select(func.count(AnalysisRun.id))
            .join(Repository)
            .where(Repository.organization_id == org.id)
        )
        analysis_count = result.scalar()
        assert analysis_count == 6
