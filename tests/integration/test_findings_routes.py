"""Integration tests for findings API routes.

Tests cover:
- Listing findings
- Filtering by severity, detector
- Pagination
- Findings summary
"""

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Skip if v1 routes don't exist yet
pytest.importorskip("repotoire.api.v1.routes.findings")

from repotoire.api.v1.routes.findings import router
from repotoire.db.models import FindingSeverity


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def app():
    """Create test FastAPI app with findings routes."""
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api/v1")
    return test_app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_finding():
    """Create a mock Finding object."""
    finding = MagicMock()
    finding.id = uuid4()
    finding.analysis_run_id = uuid4()
    finding.detector = "ruff"
    finding.severity = FindingSeverity.MEDIUM
    finding.title = "Unused import"
    finding.description = "The import 'os' is imported but never used."
    finding.affected_files = ["src/utils.py"]
    finding.affected_nodes = ["utils.py::os:1"]
    finding.line_start = 1
    finding.line_end = 1
    finding.suggested_fix = "Remove the unused import"
    finding.estimated_effort = "trivial"
    finding.graph_context = None
    finding.created_at = datetime.now(timezone.utc)
    return finding


# =============================================================================
# Response Model Tests
# =============================================================================


class TestResponseModels:
    """Tests for response model serialization."""

    def test_finding_response_serialization(self, mock_finding):
        """FindingResponse should serialize correctly."""
        from repotoire.api.v1.routes.findings import FindingResponse

        response = FindingResponse(
            id=mock_finding.id,
            analysis_run_id=mock_finding.analysis_run_id,
            detector=mock_finding.detector,
            severity=mock_finding.severity.value,
            title=mock_finding.title,
            description=mock_finding.description,
            affected_files=mock_finding.affected_files,
            affected_nodes=mock_finding.affected_nodes,
            line_start=mock_finding.line_start,
            line_end=mock_finding.line_end,
            suggested_fix=mock_finding.suggested_fix,
            estimated_effort=mock_finding.estimated_effort,
            graph_context=mock_finding.graph_context,
            created_at=mock_finding.created_at,
        )

        assert response.id == mock_finding.id
        assert response.detector == "ruff"
        assert response.severity == "medium"
        assert response.title == "Unused import"

    def test_findings_summary_response(self):
        """FindingsSummary should have correct structure."""
        from repotoire.api.v1.routes.findings import FindingsSummary

        summary = FindingsSummary(
            critical=3,
            high=5,
            medium=10,
            low=8,
            info=2,
            total=28,
        )

        assert summary.critical == 3
        assert summary.high == 5
        assert summary.total == 28

    def test_paginated_findings_response(self, mock_finding):
        """PaginatedFindingsResponse should have correct structure."""
        from repotoire.api.v1.routes.findings import (
            FindingResponse,
            PaginatedFindingsResponse,
        )

        response = PaginatedFindingsResponse(
            items=[
                FindingResponse(
                    id=mock_finding.id,
                    analysis_run_id=mock_finding.analysis_run_id,
                    detector=mock_finding.detector,
                    severity=mock_finding.severity.value,
                    title=mock_finding.title,
                    description=mock_finding.description,
                    affected_files=mock_finding.affected_files,
                    affected_nodes=mock_finding.affected_nodes,
                    line_start=mock_finding.line_start,
                    line_end=mock_finding.line_end,
                    suggested_fix=mock_finding.suggested_fix,
                    estimated_effort=mock_finding.estimated_effort,
                    graph_context=mock_finding.graph_context,
                    created_at=mock_finding.created_at,
                )
            ],
            total=100,
            page=1,
            page_size=20,
            has_more=True,
        )

        assert len(response.items) == 1
        assert response.total == 100
        assert response.has_more is True


# =============================================================================
# Unit Tests (No Database)
# =============================================================================


class TestFindingsEndpointsUnit:
    """Unit tests for findings endpoints without database."""

    def test_unauthorized_access(self, client):
        """Endpoints should return 401 without auth header."""
        response = client.get("/api/v1/findings")
        assert response.status_code == 401

    def test_invalid_finding_id_format(self, client):
        """Should return 401 for invalid UUID format (auth checked first)."""
        # FastAPI auth dependency runs before path validation in this case
        response = client.get("/api/v1/findings/not-a-uuid")
        assert response.status_code == 401

    def test_list_findings_unauthorized(self, client):
        """List findings should require authentication."""
        response = client.get("/api/v1/findings")
        assert response.status_code == 401

    def test_get_summary_unauthorized(self, client):
        """Get summary should require authentication."""
        response = client.get("/api/v1/findings/summary")
        assert response.status_code == 401


# =============================================================================
# Integration Tests (With Database)
# =============================================================================


def _has_database_url() -> bool:
    """Check if DATABASE_URL is configured."""
    url = os.getenv("DATABASE_URL", "") or os.getenv("TEST_DATABASE_URL", "")
    return bool(url.strip())


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestFindingsEndpointsIntegration:
    """Integration tests for findings with real database."""

    @pytest.mark.asyncio
    async def test_create_and_query_findings(self, db_session, test_user):
        """Findings can be created and queried from the database."""
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

        # Create findings
        for _ in range(5):
            await FindingFactory.async_create(
                db_session,
                analysis_run_id=analysis.id,
            )

        # Query findings directly
        result = await db_session.execute(
            select(Finding).where(Finding.analysis_run_id == analysis.id)
        )
        findings = result.scalars().all()

        assert len(findings) == 5

    @pytest.mark.asyncio
    async def test_findings_with_different_severities(self, db_session, test_user):
        """Findings can have different severity levels."""
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

        # Create findings with different severities
        await FindingFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
            critical=True,
        )
        await FindingFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
            high=True,
        )
        await FindingFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
        )  # Default is medium

        # Query findings by severity
        result = await db_session.execute(
            select(Finding).where(
                Finding.analysis_run_id == analysis.id,
                Finding.severity == FindingSeverity.CRITICAL,
            )
        )
        critical_findings = result.scalars().all()

        assert len(critical_findings) == 1

    @pytest.mark.asyncio
    async def test_findings_count_by_severity(self, db_session, test_user):
        """Can count findings by severity."""
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
        for _ in range(2):
            await FindingFactory.async_create(
                db_session,
                analysis_run_id=analysis.id,
                critical=True,
            )
        for _ in range(3):
            await FindingFactory.async_create(
                db_session,
                analysis_run_id=analysis.id,
                high=True,
            )

        # Count by severity
        result = await db_session.execute(
            select(Finding.severity, func.count(Finding.id))
            .where(Finding.analysis_run_id == analysis.id)
            .group_by(Finding.severity)
        )
        counts = {row[0]: row[1] for row in result.all()}

        assert counts.get(FindingSeverity.CRITICAL, 0) == 2
        assert counts.get(FindingSeverity.HIGH, 0) == 3

    @pytest.mark.asyncio
    async def test_findings_with_detector_filter(self, db_session, test_user):
        """Findings can be filtered by detector."""
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

        # Create findings with specific detectors
        for _ in range(3):
            await FindingFactory.async_create(
                db_session,
                analysis_run_id=analysis.id,
                dead_code=True,  # Uses vulture detector
            )

        await FindingFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
            circular_dependency=True,  # Uses graph:circular_dependency
        )

        # Filter by detector (vulture handles dead code)
        result = await db_session.execute(
            select(Finding).where(
                Finding.analysis_run_id == analysis.id,
                Finding.detector == "vulture",
            )
        )
        vulture_findings = result.scalars().all()

        assert len(vulture_findings) == 3

    @pytest.mark.asyncio
    async def test_finding_details(self, db_session, test_user):
        """Finding should have all expected details."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
            FindingFactory,
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

        # Verify finding details
        assert finding.id is not None
        assert finding.analysis_run_id == analysis.id
        assert finding.detector is not None
        assert finding.severity is not None
        assert finding.title is not None
        assert finding.description is not None


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestFindingsAccessControl:
    """Tests for access control on findings."""

    @pytest.mark.asyncio
    async def test_findings_belong_to_org(self, db_session, test_user):
        """Findings should be associated with an organization via repository."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
            FindingFactory,
        )
        from repotoire.db.models import Finding, Repository, AnalysisRun
        from sqlalchemy import select
        from sqlalchemy.orm import joinedload

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

        # Create analysis and findings in each repo
        analysis1 = await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo1.id,
            completed=True,
        )
        analysis2 = await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo2.id,
            completed=True,
        )

        await FindingFactory.async_create(
            db_session,
            analysis_run_id=analysis1.id,
        )
        await FindingFactory.async_create(
            db_session,
            analysis_run_id=analysis2.id,
        )

        # Query findings for org1's repo only
        result = await db_session.execute(
            select(Finding)
            .join(AnalysisRun)
            .join(Repository)
            .where(Repository.organization_id == org1.id)
        )
        org1_findings = result.scalars().all()

        assert len(org1_findings) == 1
