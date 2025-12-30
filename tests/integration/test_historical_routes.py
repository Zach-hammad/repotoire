"""Integration tests for historical API routes.

Tests cover:
- Issue origin endpoint
- Git history status endpoint
- Commit history endpoint
- Single commit endpoint
- Backfill trigger and status endpoints
- Attribution correction endpoint
- Health check endpoint
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.v1.routes.historical import router, _backfill_jobs
from repotoire.api.shared.auth import ClerkUser, get_current_user
from repotoire.db.session import get_db
from repotoire.db.models import Repository, Finding


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_repository():
    """Create a mock repository."""
    repo = MagicMock(spec=Repository)
    repo.id = uuid4()
    repo.name = "test-repo"
    repo.slug = "test-org/test-repo"
    return repo


@pytest.fixture
def mock_finding():
    """Create a mock finding."""
    finding = MagicMock(spec=Finding)
    finding.id = uuid4()
    finding.analysis_run_id = uuid4()
    finding.file_path = "src/main.py"
    finding.start_line = 10
    finding.end_line = 20
    return finding


@pytest.fixture
def mock_clerk_user():
    """Create a mock Clerk user."""
    return ClerkUser(user_id="user_test123", session_id="sess_test123")


@pytest.fixture
def mock_db_session():
    """Create a mock async database session."""
    session = AsyncMock(spec=AsyncSession)
    return session


@pytest.fixture
def app(mock_clerk_user, mock_db_session):
    """Create test FastAPI app with historical routes and dependency overrides."""
    test_app = FastAPI()
    test_app.include_router(router)

    # Override dependencies
    test_app.dependency_overrides[get_current_user] = lambda: mock_clerk_user
    test_app.dependency_overrides[get_db] = lambda: mock_db_session

    yield test_app

    # Cleanup
    test_app.dependency_overrides.clear()


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


# =============================================================================
# Health Check Tests
# =============================================================================


class TestHistoricalHealthCheck:
    """Tests for /historical/health endpoint."""

    def test_health_check_returns_status(self):
        """Health check endpoint should return status info."""
        # Health check doesn't need auth, use fresh app
        test_app = FastAPI()
        test_app.include_router(router)
        client = TestClient(test_app)

        response = client.get("/historical/health")

        assert response.status_code == 200
        data = response.json()

        assert "status" in data
        assert "graphiti_available" in data
        assert "openai_configured" in data
        assert "falkordb_host" in data
        assert "message" in data

    def test_health_check_shows_falkordb_config(self):
        """Health check should show FalkorDB configuration."""
        test_app = FastAPI()
        test_app.include_router(router)
        client = TestClient(test_app)

        response = client.get("/historical/health")

        assert response.status_code == 200
        data = response.json()

        # Should have FalkorDB info
        assert "falkordb_host" in data
        assert "falkordb_password_set" in data


# =============================================================================
# Issue Origin Tests
# =============================================================================


class TestIssueOrigin:
    """Tests for /historical/issue-origin endpoint."""

    def test_get_issue_origin_invalid_finding_id(self, client, mock_db_session):
        """Should return 400 for invalid finding ID format."""
        response = client.get(
            "/historical/issue-origin",
            params={"finding_id": "not-a-uuid"},
        )

        assert response.status_code == 400
        assert "Invalid finding ID format" in response.json()["detail"]

    def test_get_issue_origin_finding_not_found(self, client, mock_db_session):
        """Should return 404 for non-existent finding."""
        # Mock empty result
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        finding_id = str(uuid4())
        response = client.get(
            "/historical/issue-origin",
            params={"finding_id": finding_id},
        )

        assert response.status_code == 404
        assert "Finding not found" in response.json()["detail"]

    def test_get_issue_origin_success(self, mock_db_session, mock_finding, mock_clerk_user):
        """Should return issue origin response for valid finding."""
        from unittest.mock import patch

        # Create a fresh app with graphiti mocked
        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[get_current_user] = lambda: mock_clerk_user
        test_app.dependency_overrides[get_db] = lambda: mock_db_session

        # Mock finding found
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_finding
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        finding_id = str(mock_finding.id)

        # Mock the graphiti instance to avoid FalkorDB connection
        with patch("repotoire.api.v1.routes.historical._get_graphiti_instance") as mock_graphiti:
            mock_graphiti.return_value = MagicMock()

            test_client = TestClient(test_app)
            response = test_client.get(
                "/historical/issue-origin",
                params={"finding_id": finding_id},
            )

        assert response.status_code == 200
        data = response.json()

        assert data["finding_id"] == finding_id
        assert "confidence" in data
        assert "confidence_reason" in data
        assert data["user_corrected"] is False


# =============================================================================
# Git History Status Tests
# =============================================================================


class TestGitHistoryStatus:
    """Tests for /historical/status/{repository_id} endpoint."""

    def test_get_status_invalid_repo_id(self, client, mock_db_session):
        """Should return 400 for invalid repository ID format."""
        response = client.get("/historical/status/not-a-uuid")

        assert response.status_code == 400
        assert "Invalid repository ID format" in response.json()["detail"]

    def test_get_status_repo_not_found(self, client, mock_db_session):
        """Should return 404 for non-existent repository."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        repo_id = str(uuid4())
        response = client.get(f"/historical/status/{repo_id}")

        assert response.status_code == 404
        assert "Repository not found" in response.json()["detail"]

    def test_get_status_success(self, client, mock_db_session, mock_repository):
        """Should return git history status for valid repository."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_repository
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        repo_id = str(mock_repository.id)
        response = client.get(f"/historical/status/{repo_id}")

        assert response.status_code == 200
        data = response.json()

        assert "has_git_history" in data
        assert "commits_ingested" in data
        assert "is_backfill_running" in data


# =============================================================================
# Commit History Tests
# =============================================================================


class TestCommitHistory:
    """Tests for /historical/commits endpoint."""

    def test_get_commits_invalid_repo_id(self, client, mock_db_session):
        """Should return 400 for invalid repository ID format."""
        response = client.get(
            "/historical/commits",
            params={"repository_id": "not-a-uuid"},
        )

        assert response.status_code == 400
        assert "Invalid repository ID format" in response.json()["detail"]

    def test_get_commits_repo_not_found(self, client, mock_db_session):
        """Should return 404 for non-existent repository."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        repo_id = str(uuid4())
        response = client.get(
            "/historical/commits",
            params={"repository_id": repo_id},
        )

        assert response.status_code == 404
        assert "Repository not found" in response.json()["detail"]

    def test_get_commits_success(self, client, mock_db_session, mock_repository):
        """Should return commit history for valid repository."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_repository
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        repo_id = str(mock_repository.id)
        response = client.get(
            "/historical/commits",
            params={"repository_id": repo_id, "limit": 10, "offset": 0},
        )

        assert response.status_code == 200
        data = response.json()

        assert "commits" in data
        assert "total_count" in data
        assert "has_more" in data
        assert data["commits"] == []  # No history ingested yet
        assert data["total_count"] == 0

    def test_get_commits_pagination_params(self, client, mock_db_session, mock_repository):
        """Should respect pagination parameters."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_repository
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        repo_id = str(mock_repository.id)
        response = client.get(
            "/historical/commits",
            params={"repository_id": repo_id, "limit": 50, "offset": 100},
        )

        assert response.status_code == 200


# =============================================================================
# Single Commit Tests
# =============================================================================


class TestSingleCommit:
    """Tests for /historical/commits/{commit_sha} endpoint."""

    def test_get_commit_invalid_repo_id(self, client, mock_db_session):
        """Should return 400 for invalid repository ID format."""
        response = client.get(
            "/historical/commits/abc123",
            params={"repository_id": "not-a-uuid"},
        )

        assert response.status_code == 400
        assert "Invalid repository ID format" in response.json()["detail"]

    def test_get_commit_repo_not_found(self, client, mock_db_session):
        """Should return 404 when repository is not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        repo_id = str(uuid4())
        response = client.get(
            "/historical/commits/abc123def456",
            params={"repository_id": repo_id},
        )

        assert response.status_code == 404
        assert "Repository not found" in response.json()["detail"]

    def test_get_commit_not_found(self, client, mock_db_session, mock_repository):
        """Should return 404 when commit is not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_repository
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        repo_id = str(mock_repository.id)
        response = client.get(
            "/historical/commits/abc123def456",
            params={"repository_id": repo_id},
        )

        # Should return 404 since no history is ingested
        assert response.status_code == 404
        assert "Commit not found" in response.json()["detail"]


# =============================================================================
# Backfill Tests
# =============================================================================


class TestBackfill:
    """Tests for /historical/backfill endpoints."""

    def test_trigger_backfill_invalid_repo_id(self, client, mock_db_session):
        """Should return 400 for invalid repository ID format."""
        response = client.post(
            "/historical/backfill/not-a-uuid",
            json={"max_commits": 500},
        )

        assert response.status_code == 400
        assert "Invalid repository ID format" in response.json()["detail"]

    def test_trigger_backfill_repo_not_found(self, client, mock_db_session):
        """Should return 404 for non-existent repository."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        repo_id = str(uuid4())
        response = client.post(
            f"/historical/backfill/{repo_id}",
            json={"max_commits": 500},
        )

        assert response.status_code == 404
        assert "Repository not found" in response.json()["detail"]

    def test_trigger_backfill_success(self, client, mock_db_session, mock_repository):
        """Should create backfill job for valid repository."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_repository
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        repo_id = str(mock_repository.id)
        response = client.post(
            f"/historical/backfill/{repo_id}",
            json={"max_commits": 100},
        )

        assert response.status_code == 200
        data = response.json()

        assert "job_id" in data
        assert data["status"] == "queued"
        assert data["commits_processed"] == 0

    def test_get_backfill_status_not_found(self, client):
        """Should return 404 for non-existent job."""
        job_id = str(uuid4())
        response = client.get(f"/historical/backfill/status/{job_id}")

        assert response.status_code == 404
        assert "Backfill job not found" in response.json()["detail"]

    def test_get_backfill_status_success(self, client, mock_db_session, mock_repository):
        """Should return status for existing job."""
        # First create a job
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_repository
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        repo_id = str(mock_repository.id)
        create_response = client.post(
            f"/historical/backfill/{repo_id}",
            json={"max_commits": 100},
        )
        job_id = create_response.json()["job_id"]

        # Then check its status
        response = client.get(f"/historical/backfill/status/{job_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["status"] == "queued"


# =============================================================================
# Attribution Correction Tests
# =============================================================================


class TestAttributionCorrection:
    """Tests for /historical/correct/{finding_id} endpoint."""

    def test_correct_attribution_invalid_finding_id(self, client, mock_db_session):
        """Should return 400 for invalid finding ID format."""
        response = client.post(
            "/historical/correct/not-a-uuid",
            json={"commit_sha": "abc123"},
        )

        assert response.status_code == 400
        assert "Invalid finding ID format" in response.json()["detail"]

    def test_correct_attribution_finding_not_found(self, client, mock_db_session):
        """Should return 404 for non-existent finding."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        finding_id = str(uuid4())
        response = client.post(
            f"/historical/correct/{finding_id}",
            json={"commit_sha": "abc123"},
        )

        assert response.status_code == 404
        assert "Finding not found" in response.json()["detail"]

    def test_correct_attribution_success(self, client, mock_db_session, mock_finding):
        """Should correct attribution for valid finding."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_finding
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        finding_id = str(mock_finding.id)
        correct_sha = "abc123def456789"

        response = client.post(
            f"/historical/correct/{finding_id}",
            json={"commit_sha": correct_sha},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["finding_id"] == finding_id
        assert data["user_corrected"] is True
        assert data["corrected_commit_sha"] == correct_sha


# =============================================================================
# Cleanup
# =============================================================================


@pytest.fixture(autouse=True)
def cleanup_backfill_jobs():
    """Clean up backfill jobs between tests."""
    yield
    _backfill_jobs.clear()
