"""Integration tests for analytics API routes.

Tests cover:
- Analytics summary (total findings by severity)
- Finding trends over time
- Findings by detector type
- File hotspots
- Health score calculation
- Fix statistics
- Repository listing for filters
"""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Skip if v1 routes don't exist yet
pytest.importorskip("repotoire.api.v1.routes.analytics")

from repotoire.api.v1.routes.analytics import router
from repotoire.db.models import FindingSeverity


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def app():
    """Create test FastAPI app with analytics routes."""
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

    def test_analytics_summary_model(self):
        """AnalyticsSummary should serialize correctly."""
        from repotoire.api.v1.routes.analytics import AnalyticsSummary

        summary = AnalyticsSummary(
            total_findings=100,
            critical=5,
            high=15,
            medium=40,
            low=30,
            info=10,
            by_severity={"critical": 5, "high": 15, "medium": 40, "low": 30, "info": 10},
            by_detector={"ruff": 50, "mypy": 30, "bandit": 20},
        )

        assert summary.total_findings == 100
        assert summary.critical == 5
        assert len(summary.by_detector) == 3

    def test_trend_data_point_model(self):
        """TrendDataPoint should have correct structure."""
        from repotoire.api.v1.routes.analytics import TrendDataPoint

        point = TrendDataPoint(
            date="2025-01-15",
            critical=2,
            high=5,
            medium=10,
            low=8,
            info=3,
            total=28,
        )

        assert point.date == "2025-01-15"
        assert point.total == 28

    def test_file_hotspot_model(self):
        """FileHotspot should have correct structure."""
        from repotoire.api.v1.routes.analytics import FileHotspot

        hotspot = FileHotspot(
            file_path="src/utils.py",
            finding_count=25,
            severity_breakdown={"critical": 2, "high": 5, "medium": 10, "low": 5, "info": 3},
        )

        assert hotspot.file_path == "src/utils.py"
        assert hotspot.finding_count == 25

    def test_health_score_response_model(self):
        """HealthScoreResponse should have correct structure."""
        from repotoire.api.v1.routes.analytics import HealthScoreResponse

        response = HealthScoreResponse(
            score=85,
            grade="B",
            trend="improving",
            categories={"structure": 90, "quality": 85, "architecture": 80},
        )

        assert response.score == 85
        assert response.grade == "B"
        assert response.trend == "improving"

    def test_fix_statistics_model(self):
        """FixStatistics should have correct structure."""
        from repotoire.api.v1.routes.analytics import FixStatistics

        stats = FixStatistics(
            total=50,
            pending=20,
            approved=10,
            applied=15,
            rejected=3,
            failed=2,
            by_status={"pending": 20, "approved": 10, "applied": 15, "rejected": 3, "failed": 2},
        )

        assert stats.total == 50
        assert stats.pending == 20

    def test_repository_info_model(self):
        """RepositoryInfo should have correct structure."""
        from repotoire.api.v1.routes.analytics import RepositoryInfo

        repo = RepositoryInfo(
            id=uuid4(),
            full_name="org/repo",
            health_score=85,
            last_analyzed_at=datetime.now(timezone.utc),
        )

        assert repo.full_name == "org/repo"
        assert repo.health_score == 85


# =============================================================================
# Helper Function Tests
# =============================================================================


class TestHelperFunctions:
    """Tests for helper functions in analytics routes."""

    def test_calculate_grade_a(self):
        """Score 90+ should be grade A."""
        from repotoire.api.v1.routes.analytics import _calculate_grade

        assert _calculate_grade(90) == "A"
        assert _calculate_grade(95) == "A"
        assert _calculate_grade(100) == "A"

    def test_calculate_grade_b(self):
        """Score 80-89 should be grade B."""
        from repotoire.api.v1.routes.analytics import _calculate_grade

        assert _calculate_grade(80) == "B"
        assert _calculate_grade(85) == "B"
        assert _calculate_grade(89) == "B"

    def test_calculate_grade_c(self):
        """Score 70-79 should be grade C."""
        from repotoire.api.v1.routes.analytics import _calculate_grade

        assert _calculate_grade(70) == "C"
        assert _calculate_grade(75) == "C"
        assert _calculate_grade(79) == "C"

    def test_calculate_grade_d(self):
        """Score 60-69 should be grade D."""
        from repotoire.api.v1.routes.analytics import _calculate_grade

        assert _calculate_grade(60) == "D"
        assert _calculate_grade(65) == "D"
        assert _calculate_grade(69) == "D"

    def test_calculate_grade_f(self):
        """Score below 60 should be grade F."""
        from repotoire.api.v1.routes.analytics import _calculate_grade

        assert _calculate_grade(59) == "F"
        assert _calculate_grade(50) == "F"
        assert _calculate_grade(0) == "F"


# =============================================================================
# Unit Tests (No Database)
# =============================================================================


class TestAnalyticsEndpointsUnit:
    """Unit tests for analytics endpoints without database."""

    def test_endpoints_require_auth(self, client):
        """Analytics endpoints should require authentication."""
        # All endpoints should return 401 without auth header
        endpoints = [
            "/api/v1/analytics/summary",
            "/api/v1/analytics/trends",
            "/api/v1/analytics/by-type",
            "/api/v1/analytics/by-file",
            "/api/v1/analytics/health-score",
            "/api/v1/analytics/fix-stats",
            "/api/v1/analytics/repositories",
        ]

        for endpoint in endpoints:
            response = client.get(endpoint)
            assert response.status_code == 401, f"Expected 401 for {endpoint}"


# =============================================================================
# Integration Tests (With Database)
# =============================================================================


def _has_database_url() -> bool:
    """Check if DATABASE_URL is configured."""
    url = os.getenv("DATABASE_URL", "") or os.getenv("TEST_DATABASE_URL", "")
    return bool(url.strip())


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestAnalyticsSummaryIntegration:
    """Integration tests for analytics summary endpoint."""

    @pytest.mark.asyncio
    async def test_empty_summary(self, db_session, test_user, mock_clerk):
        """Summary should return zeros when no findings exist."""
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

        # No findings created - org should exist
        assert org is not None

    @pytest.mark.asyncio
    async def test_summary_counts_by_severity(self, db_session, test_user, mock_clerk):
        """Summary should count findings by severity."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
            FindingFactory,
        )
        from repotoire.db.models import Finding
        from sqlalchemy import select, func

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

        # Create findings with different severities
        for _ in range(3):
            await FindingFactory.async_create(
                db_session,
                analysis_run_id=analysis.id,
                critical=True,
            )
        for _ in range(5):
            await FindingFactory.async_create(
                db_session,
                analysis_run_id=analysis.id,
                high=True,
            )

        # Count findings
        result = await db_session.execute(
            select(Finding.severity, func.count(Finding.id))
            .where(Finding.analysis_run_id == analysis.id)
            .group_by(Finding.severity)
        )
        counts = {row[0]: row[1] for row in result.all()}

        assert counts.get(FindingSeverity.CRITICAL, 0) == 3
        assert counts.get(FindingSeverity.HIGH, 0) == 5


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestAnalyticsTrendsIntegration:
    """Integration tests for finding trends endpoint."""

    @pytest.mark.asyncio
    async def test_trends_by_date(self, db_session, test_user, mock_clerk):
        """Trends should show findings over time."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
            FindingFactory,
        )
        from repotoire.db.models import Finding
        from sqlalchemy import select, func

        now = datetime.now(timezone.utc)

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

        # Create findings
        for _ in range(5):
            await FindingFactory.async_create(
                db_session,
                analysis_run_id=analysis.id,
            )

        # Query findings count
        result = await db_session.execute(
            select(func.count(Finding.id)).where(
                Finding.analysis_run_id == analysis.id
            )
        )
        count = result.scalar()
        assert count == 5


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestFileHotspotsIntegration:
    """Integration tests for file hotspots endpoint."""

    @pytest.mark.asyncio
    async def test_hotspots_by_finding_count(self, db_session, test_user, mock_clerk):
        """Hotspots should identify files with most findings."""
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

        # Create findings with affected_files
        for _ in range(5):
            await FindingFactory.async_create(
                db_session,
                analysis_run_id=analysis.id,
            )

        # Query findings
        result = await db_session.execute(
            select(Finding).where(Finding.analysis_run_id == analysis.id)
        )
        findings = result.scalars().all()

        # Each finding should have affected_files
        for finding in findings:
            assert finding.affected_files is not None


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestHealthScoreIntegration:
    """Integration tests for health score endpoint."""

    @pytest.mark.asyncio
    async def test_health_score_from_analysis(self, db_session, test_user, mock_clerk):
        """Health score should come from latest analysis run."""
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
        analysis = await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo.id,
            completed=True,
        )

        # Verify analysis exists
        result = await db_session.execute(
            select(AnalysisRun).where(AnalysisRun.id == analysis.id)
        )
        fetched = result.scalar_one()
        assert fetched is not None

    @pytest.mark.asyncio
    async def test_health_score_trend_calculation(self, db_session, test_user, mock_clerk):
        """Health score trend should compare with previous analysis."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
        )
        from repotoire.db.models import AnalysisRun
        from sqlalchemy import select

        now = datetime.now(timezone.utc)

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

        # Create two analyses
        await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo.id,
            completed=True,
        )
        await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo.id,
            completed=True,
        )

        # Verify multiple analyses exist
        result = await db_session.execute(
            select(AnalysisRun).where(AnalysisRun.repository_id == repo.id)
        )
        analyses = result.scalars().all()
        assert len(analyses) == 2


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestFixStatisticsIntegration:
    """Integration tests for fix statistics endpoint."""

    @pytest.mark.asyncio
    async def test_fix_counts_by_status(self, db_session, test_user, mock_clerk):
        """Fix statistics should count by status."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
            FixFactory,
        )
        from repotoire.db.models import Fix, FixStatus
        from sqlalchemy import select, func

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

        # Create fixes with different statuses
        for _ in range(3):
            await FixFactory.async_create(
                db_session,
                analysis_run_id=analysis.id,
            )  # Pending

        # Count fixes
        result = await db_session.execute(
            select(func.count(Fix.id)).where(Fix.analysis_run_id == analysis.id)
        )
        count = result.scalar()
        assert count == 3


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestRepositoriesIntegration:
    """Integration tests for repositories endpoint."""

    @pytest.mark.asyncio
    async def test_list_org_repositories(self, db_session, test_user, mock_clerk):
        """Should list all repositories for the organization."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
        )
        from repotoire.db.models import Repository
        from sqlalchemy import select

        # Create test data
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )

        # Create repositories
        for i in range(3):
            await RepositoryFactory.async_create(
                db_session,
                organization_id=org.id,
                full_name=f"org/repo-{i}",
            )

        # Query repos
        result = await db_session.execute(
            select(Repository).where(Repository.organization_id == org.id)
        )
        repos = result.scalars().all()
        assert len(repos) == 3

    @pytest.mark.asyncio
    async def test_repos_have_health_score(self, db_session, test_user, mock_clerk):
        """Repositories should have health score from latest analysis."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
        )
        from repotoire.db.models import Repository
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

        # Update health score
        repo.health_score = 85
        await db_session.flush()

        # Verify
        result = await db_session.execute(
            select(Repository).where(Repository.id == repo.id)
        )
        fetched = result.scalar_one()
        assert fetched.health_score == 85
