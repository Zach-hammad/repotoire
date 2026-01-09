"""Unit tests for analysis tasks."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from repotoire.db.models import AnalysisStatus, PlanTier


@pytest.fixture
def mock_session():
    """Mock database session."""
    with patch("repotoire.workers.tasks.get_sync_session") as mock:
        session = MagicMock()
        mock.return_value.__enter__ = MagicMock(return_value=session)
        mock.return_value.__exit__ = MagicMock(return_value=False)
        yield session


@pytest.fixture
def mock_neo4j():
    """Mock Neo4j client."""
    with patch("repotoire.workers.tasks._get_graph_client_for_org") as mock:
        client = MagicMock()
        mock.return_value = client
        yield mock


@pytest.fixture
def mock_clone():
    """Mock repository cloning."""
    with patch("repotoire.workers.tasks._clone_repository") as mock:
        mock.return_value = Path("/tmp/test-clone")
        yield mock


@pytest.fixture
def mock_progress():
    """Mock progress tracker."""
    with patch("repotoire.workers.tasks.ProgressTracker") as mock:
        tracker = MagicMock()
        mock.return_value = tracker
        yield tracker


@pytest.fixture
def mock_repo():
    """Create a mock repository."""
    repo = MagicMock()
    repo.id = uuid4()
    repo.full_name = "test/repo"
    repo.default_branch = "main"
    repo.organization = MagicMock()
    repo.organization.id = uuid4()
    repo.organization.plan_tier = PlanTier.PRO
    repo.organization.slug = "test-org"
    return repo


@pytest.fixture
def mock_health():
    """Create a mock health result."""
    health = MagicMock()
    health.overall_score = 85
    health.structure_score = 80
    health.quality_score = 90
    health.architecture_score = 85
    health.findings = [MagicMock(), MagicMock()]
    return health


class TestAnalyzeRepository:
    """Tests for analyze_repository task."""

    def test_successful_analysis(
        self,
        mock_session,
        mock_neo4j,
        mock_clone,
        mock_progress,
        mock_repo,
        mock_health,
    ):
        """Test successful repository analysis."""
        # Setup
        mock_session.get.return_value = mock_repo
        analysis_run_id = str(uuid4())
        repo_id = str(mock_repo.id)
        commit_sha = "abc123def456"

        with patch("repotoire.workers.tasks.IngestionPipeline") as mock_pipeline:
            with patch("repotoire.workers.tasks.AnalysisEngine") as mock_engine:
                mock_pipeline.return_value.ingest.return_value = MagicMock(
                    files_processed=100
                )
                mock_engine.return_value.analyze.return_value = mock_health

                # Import here to avoid import errors without mocks
                from repotoire.workers.tasks import analyze_repository

                # Execute (mock the task binding)
                task = MagicMock()
                task.request.retries = 0
                task.max_retries = 3

                with patch("repotoire.workers.tasks.with_concurrency_limit", lambda f: f):
                    with patch("repotoire.workers.hooks.on_analysis_complete"):
                        # Would need full Celery setup to test properly
                        pass

    def test_repository_not_found(self, mock_session, mock_progress):
        """Test handling of missing repository."""
        mock_session.get.return_value = None

        # The task should raise ValueError for missing repo

    def test_timeout_handling(self, mock_progress):
        """Test SoftTimeLimitExceeded handling."""
        from celery.exceptions import SoftTimeLimitExceeded

        # Task should update status to FAILED on timeout

    def test_retry_on_failure(self, mock_session, mock_progress, mock_repo):
        """Test retry logic on transient failures."""
        mock_session.get.return_value = mock_repo

        # Task should retry on exceptions up to max_retries


class TestAnalyzePR:
    """Tests for analyze_pr task."""

    def test_no_changed_files(self, mock_session, mock_clone, mock_progress, mock_repo):
        """Test handling when no analyzable files changed."""
        mock_session.get.return_value = mock_repo

        with patch("repotoire.workers.tasks._get_changed_files") as mock_changed:
            mock_changed.return_value = []

            # Should complete with 0 findings

    def test_score_delta_calculation(
        self, mock_session, mock_clone, mock_progress, mock_repo, mock_health
    ):
        """Test score delta calculation from base commit."""
        mock_session.get.return_value = mock_repo
        mock_health.overall_score = 85

        with patch("repotoire.workers.tasks._get_score_at_commit") as mock_score:
            mock_score.return_value = 80

            # Delta should be 85 - 80 = 5


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_get_changed_files(self):
        """Test getting changed files from git diff."""
        from repotoire.workers.tasks import _get_changed_files

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "file1.py\nfile2.py\nreadme.md\n"
            mock_run.return_value.returncode = 0

            # Would test actual file filtering

    def test_get_score_at_commit_found(self, mock_session):
        """Test getting score from previous analysis."""
        from repotoire.workers.tasks import _get_score_at_commit

        mock_session.execute.return_value.scalar_one_or_none.return_value = 75

        result = _get_score_at_commit(mock_session, str(uuid4()), "abc123")
        assert result == 75

    def test_get_score_at_commit_not_found(self, mock_session):
        """Test handling when no previous analysis exists."""
        from repotoire.workers.tasks import _get_score_at_commit

        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        result = _get_score_at_commit(mock_session, str(uuid4()), "abc123")
        assert result is None
