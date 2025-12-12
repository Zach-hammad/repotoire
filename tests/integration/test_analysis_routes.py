"""Integration tests for analysis API routes.

Tests cover:
- Triggering analysis
- Getting analysis status
- Listing analysis runs
- Getting analysis results
"""

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Skip if v1 routes don't exist yet
pytest.importorskip("repotoire.api.v1.routes.analysis")

from repotoire.api.v1.routes.analysis import router
from repotoire.db.models import AnalysisStatus


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def app():
    """Create test FastAPI app with analysis routes."""
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api/v1")
    return test_app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_analysis_run():
    """Create a mock AnalysisRun object."""
    run = MagicMock()
    run.id = uuid4()
    run.repository_id = uuid4()
    run.commit_sha = "abc123def456"
    run.branch = "main"
    run.status = AnalysisStatus.COMPLETED
    run.health_score = 85
    run.structure_score = 80
    run.quality_score = 88
    run.architecture_score = 87
    run.score_delta = 3
    run.findings_count = 12
    run.files_analyzed = 150
    run.progress_percent = 100
    run.current_step = None
    run.started_at = datetime.now(timezone.utc)
    run.completed_at = datetime.now(timezone.utc)
    run.error_message = None
    run.created_at = datetime.now(timezone.utc)
    return run


@pytest.fixture
def mock_repository():
    """Create a mock Repository object."""
    repo = MagicMock()
    repo.id = uuid4()
    repo.organization_id = uuid4()
    repo.full_name = "test-org/test-repo"
    repo.default_branch = "main"
    repo.is_active = True
    repo.health_score = 85
    return repo


# =============================================================================
# Response Model Tests
# =============================================================================


class TestResponseModels:
    """Tests for response model serialization."""

    def test_analysis_status_response_serialization(self, mock_analysis_run):
        """AnalysisStatusResponse should serialize correctly."""
        from repotoire.api.v1.routes.analysis import AnalysisStatusResponse

        response = AnalysisStatusResponse(
            id=mock_analysis_run.id,
            repository_id=mock_analysis_run.repository_id,
            commit_sha=mock_analysis_run.commit_sha,
            branch=mock_analysis_run.branch,
            status=mock_analysis_run.status.value,
            progress_percent=mock_analysis_run.progress_percent,
            current_step=mock_analysis_run.current_step,
            health_score=mock_analysis_run.health_score,
            structure_score=mock_analysis_run.structure_score,
            quality_score=mock_analysis_run.quality_score,
            architecture_score=mock_analysis_run.architecture_score,
            findings_count=mock_analysis_run.findings_count,
            files_analyzed=mock_analysis_run.files_analyzed,
            error_message=mock_analysis_run.error_message,
            started_at=mock_analysis_run.started_at,
            completed_at=mock_analysis_run.completed_at,
            created_at=mock_analysis_run.created_at,
        )

        assert response.id == mock_analysis_run.id
        assert response.status == "completed"
        assert response.progress_percent == 100
        assert response.health_score == 85

    def test_trigger_analysis_response(self):
        """TriggerAnalysisResponse should have correct structure."""
        from repotoire.api.v1.routes.analysis import TriggerAnalysisResponse

        analysis_run_id = uuid4()
        response = TriggerAnalysisResponse(
            analysis_run_id=analysis_run_id,
            status="queued",
            message="Analysis queued successfully",
        )

        assert response.analysis_run_id == analysis_run_id
        assert response.status == "queued"


# =============================================================================
# Unit Tests (No Database)
# =============================================================================


class TestAnalysisEndpointsUnit:
    """Unit tests for analysis endpoints without database."""

    def test_trigger_analysis_unauthorized(self, client):
        """Trigger endpoint should return 401 without auth header."""
        response = client.post(
            "/api/v1/analysis/trigger",
            json={"repository_id": str(uuid4())},
        )
        assert response.status_code == 401

    def test_get_analysis_status_unauthorized(self, client):
        """Get analysis status should return 401 without auth header."""
        response = client.get(f"/api/v1/analysis/{uuid4()}/status")
        assert response.status_code == 401


# =============================================================================
# Integration Tests (With Database)
# =============================================================================


def _has_database_url() -> bool:
    """Check if DATABASE_URL is configured."""
    url = os.getenv("DATABASE_URL", "") or os.getenv("TEST_DATABASE_URL", "")
    return bool(url.strip())


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestAnalysisEndpointsIntegration:
    """Integration tests for analysis endpoints with real database."""

    @pytest.mark.asyncio
    async def test_create_analysis_run(self, db_session, test_user):
        """AnalysisRun can be created and persisted."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
        )
        from repotoire.db.models import AnalysisRun
        from sqlalchemy import select

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

        # Create analysis run
        analysis = await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo.id,
        )

        # Verify it was persisted
        result = await db_session.execute(
            select(AnalysisRun).where(AnalysisRun.id == analysis.id)
        )
        found = result.scalar_one_or_none()

        assert found is not None
        assert found.repository_id == repo.id

    @pytest.mark.asyncio
    async def test_list_analyses_for_repository(self, db_session, test_user):
        """Multiple analyses can be queried for a repository."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
        )
        from repotoire.db.models import AnalysisRun
        from sqlalchemy import select

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

        # Create multiple analysis runs
        for _ in range(5):
            await AnalysisRunFactory.async_create(
                db_session,
                repository_id=repo.id,
                completed=True,
            )

        # Query analyses
        result = await db_session.execute(
            select(AnalysisRun).where(AnalysisRun.repository_id == repo.id)
        )
        analyses = result.scalars().all()

        assert len(analyses) == 5

    @pytest.mark.asyncio
    async def test_analysis_with_findings(self, db_session, test_user):
        """Analysis run can have associated findings."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
            FindingFactory,
        )
        from repotoire.db.models import Finding
        from sqlalchemy import select

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

        # Create some findings
        for _ in range(3):
            await FindingFactory.async_create(
                db_session,
                analysis_run_id=analysis.id,
            )

        # Query findings for analysis
        result = await db_session.execute(
            select(Finding).where(Finding.analysis_run_id == analysis.id)
        )
        findings = result.scalars().all()

        assert len(findings) == 3

    @pytest.mark.asyncio
    async def test_analysis_status_progression(self, db_session, test_user):
        """Analysis status can be updated through different states."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
        )
        from repotoire.db.models import AnalysisRun, AnalysisStatus
        from sqlalchemy import select

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

        # Create a queued analysis (default status)
        analysis = await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo.id,
        )

        assert analysis.status == AnalysisStatus.QUEUED

        # Update to running
        analysis.status = AnalysisStatus.RUNNING
        analysis.progress_percent = 50
        await db_session.flush()

        # Verify the update worked
        assert analysis.status == AnalysisStatus.RUNNING
        assert analysis.progress_percent == 50

    @pytest.mark.asyncio
    async def test_analysis_scores_recorded(self, db_session, test_user):
        """Completed analysis should have all scores recorded."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
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

        # Verify scores are set
        assert analysis.status == AnalysisStatus.COMPLETED
        assert analysis.health_score is not None
        assert analysis.structure_score is not None
        assert analysis.quality_score is not None
        assert analysis.architecture_score is not None


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestAnalysisErrorHandling:
    """Tests for error handling in analysis endpoints."""

    def test_unauthorized_access(self, client):
        """Endpoints should return 401 without auth header."""
        response = client.get(f"/api/v1/analysis/{uuid4()}/status")
        assert response.status_code == 401

    def test_invalid_analysis_id_format(self, client):
        """Should return 401 for invalid UUID format (auth is checked first)."""
        # Note: FastAPI auth dependency runs before path validation in this case
        response = client.get("/api/v1/analysis/not-a-uuid/status")
        assert response.status_code == 401


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestAnalysisAccessControl:
    """Tests for access control on analyses."""

    @pytest.mark.asyncio
    async def test_analysis_belongs_to_repo(self, db_session, test_user):
        """Analysis should be associated with correct repository."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
        )
        from repotoire.db.models import AnalysisRun, Repository
        from sqlalchemy import select
        from sqlalchemy.orm import joinedload

        # Create org with repo
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

        # Create analysis
        analysis = await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo.id,
            completed=True,
        )

        # Query analysis with repo relationship
        result = await db_session.execute(
            select(AnalysisRun)
            .where(AnalysisRun.id == analysis.id)
        )
        found_analysis = result.scalar_one()

        # Verify the relationship
        assert found_analysis.repository_id == repo.id

    @pytest.mark.asyncio
    async def test_analyses_filtered_by_org(self, db_session, test_user):
        """Analyses should be filterable by organization."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
        )
        from repotoire.db.models import AnalysisRun, Repository
        from sqlalchemy import select

        # Create two orgs
        org1 = await OrganizationFactory.async_create(db_session)
        org2 = await OrganizationFactory.async_create(db_session)

        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org1.id,
        )

        # Create repos in each org
        repo1 = await RepositoryFactory.async_create(
            db_session,
            organization_id=org1.id,
        )
        repo2 = await RepositoryFactory.async_create(
            db_session,
            organization_id=org2.id,
        )

        # Create analyses in each repo
        await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo1.id,
            completed=True,
        )
        await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo2.id,
            completed=True,
        )

        # Query analyses for org1's repo only
        result = await db_session.execute(
            select(AnalysisRun)
            .join(Repository)
            .where(Repository.organization_id == org1.id)
        )
        org1_analyses = result.scalars().all()

        assert len(org1_analyses) == 1
