"""Tests for GitHub Check Run integration.

Tests for the post_check_run Celery task.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from repotoire.db.models import AnalysisStatus


@pytest.fixture
def mock_logger():
    """Mock the structlog logger."""
    with patch("repotoire.workers.hooks.logger") as mock:
        mock.bind.return_value = mock
        mock.info = MagicMock()
        mock.warning = MagicMock()
        mock.debug = MagicMock()
        mock.exception = MagicMock()
        yield mock


class TestPostCheckRunTask:
    """Tests for the post_check_run Celery task."""

    def test_post_check_run_analysis_not_found(self, mock_logger):
        """Test post_check_run when analysis is not found."""
        with patch(
            "repotoire.workers.hooks.get_sync_session"
        ) as mock_get_session:
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_session.get.return_value = None
            mock_get_session.return_value = mock_session

            from repotoire.workers.hooks import post_check_run

            result = post_check_run(
                repo_id=str(uuid4()),
                analysis_run_id=str(uuid4()),
            )

            assert result["status"] == "skipped"
            assert result["reason"] == "analysis_not_found"

    def test_post_check_run_analysis_not_completed(self, mock_logger):
        """Test post_check_run when analysis is not completed."""
        with patch(
            "repotoire.workers.hooks.get_sync_session"
        ) as mock_get_session:
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)

            mock_analysis = MagicMock()
            mock_analysis.status = AnalysisStatus.RUNNING
            mock_session.get.return_value = mock_analysis

            mock_get_session.return_value = mock_session

            from repotoire.workers.hooks import post_check_run

            result = post_check_run(
                repo_id=str(uuid4()),
                analysis_run_id=str(uuid4()),
            )

            assert result["status"] == "skipped"
            assert result["reason"] == "analysis_not_completed"

    def test_post_check_run_repo_not_found(self, mock_logger):
        """Test post_check_run when repository is not found."""
        with patch(
            "repotoire.workers.hooks.get_sync_session"
        ) as mock_get_session:
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)

            mock_analysis = MagicMock()
            mock_analysis.status = AnalysisStatus.COMPLETED

            # First call returns analysis, second returns None for repo
            mock_session.get.side_effect = [mock_analysis, None]
            mock_get_session.return_value = mock_session

            from repotoire.workers.hooks import post_check_run

            result = post_check_run(
                repo_id=str(uuid4()),
                analysis_run_id=str(uuid4()),
            )

            assert result["status"] == "skipped"
            assert result["reason"] == "repo_not_found"

    def test_post_check_run_no_github_installation(self, mock_logger):
        """Test post_check_run when no GitHub installation is found."""
        with patch(
            "repotoire.workers.hooks.get_sync_session"
        ) as mock_get_session:
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)

            mock_analysis = MagicMock()
            mock_analysis.status = AnalysisStatus.COMPLETED

            mock_repo = MagicMock()
            mock_repo.full_name = "testowner/testrepo"

            # First call returns analysis, second returns repo
            mock_session.get.side_effect = [mock_analysis, mock_repo]

            # Execute returns no GitHub repo
            mock_execute_result = MagicMock()
            mock_execute_result.scalar_one_or_none.return_value = None
            mock_session.execute.return_value = mock_execute_result

            mock_get_session.return_value = mock_session

            from repotoire.workers.hooks import post_check_run

            result = post_check_run(
                repo_id=str(uuid4()),
                analysis_run_id=str(uuid4()),
            )

            assert result["status"] == "skipped"
            assert result["reason"] == "no_github_installation"

    def test_post_check_run_success(self, mock_logger):
        """Test successful post_check_run execution."""
        with patch(
            "repotoire.workers.hooks.get_sync_session"
        ) as mock_get_session, patch(
            "repotoire.api.shared.services.github.GitHubAppClient"
        ) as mock_github_class:
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)

            mock_analysis = MagicMock()
            mock_analysis.id = uuid4()
            mock_analysis.status = AnalysisStatus.COMPLETED
            mock_analysis.health_score = 85
            mock_analysis.commit_sha = "abc123def456"

            mock_repo = MagicMock()
            mock_repo.full_name = "testowner/testrepo"

            # First call returns analysis, second returns repo
            mock_session.get.side_effect = [mock_analysis, mock_repo]

            # Setup GitHub repo with installation
            mock_installation = MagicMock()
            mock_installation.installation_id = 12345

            mock_github_repo = MagicMock()
            mock_github_repo.installation = mock_installation

            mock_execute_result = MagicMock()
            mock_execute_result.scalar_one_or_none.return_value = mock_github_repo

            # Two execute calls: one for GitHub repo, one for findings
            mock_findings_result = MagicMock()
            mock_findings_result.scalars.return_value.all.return_value = []

            mock_session.execute.side_effect = [
                mock_execute_result,
                mock_findings_result,
            ]

            mock_get_session.return_value = mock_session

            # Setup mock GitHub client
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            # Mock the async methods
            from unittest.mock import AsyncMock

            mock_github.create_check_run_for_analysis = AsyncMock(
                return_value={"check_run_id": 999}
            )
            mock_github.complete_check_run_with_results = AsyncMock(
                return_value={"id": 999, "conclusion": "success"}
            )

            from repotoire.workers.hooks import post_check_run

            result = post_check_run(
                repo_id=str(uuid4()),
                analysis_run_id=str(mock_analysis.id),
                head_sha="abc123def456",
            )

            assert result["status"] == "posted"
            assert result["check_run_id"] == 999
            assert result["health_score"] == 85
            assert result["findings_count"] == 0
