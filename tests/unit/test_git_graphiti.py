"""Unit tests for Git-Graphiti integration."""

import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import git

# Skip if graphiti not installed (optional dependency)
pytest.importorskip("graphiti_core")

from repotoire.historical import GitGraphitiIntegration

# Mark all async tests with anyio
pytestmark = pytest.mark.anyio


@pytest.fixture
def mock_graphiti():
    """Create a mock Graphiti instance."""
    graphiti = Mock()
    graphiti.add_episode = AsyncMock()
    graphiti.search = AsyncMock(return_value="Mock search results")
    return graphiti


@pytest.fixture
def mock_repo():
    """Create a mock GitPython repository."""
    repo = Mock(spec=git.Repo)

    # Create a mock commit
    commit = Mock(spec=git.Commit)
    commit.hexsha = "abc123def456"
    commit.summary = "Add authentication feature"
    commit.message = "Add authentication feature\n\nImplemented OAuth2 login flow"
    commit.author.name = "John Doe"
    commit.author.email = "john@example.com"
    commit.committed_datetime = datetime(2024, 11, 15, 10, 30, tzinfo=timezone.utc)
    commit.stats.total = {"insertions": 50, "deletions": 10, "files": 3}
    commit.stats.files = {
        "auth/login.py": {"insertions": 30, "deletions": 5},
        "auth/oauth.py": {"insertions": 20, "deletions": 5},
    }
    commit.parents = []

    # Mock iter_commits
    repo.iter_commits.return_value = [commit]

    return repo


class TestGitGraphitiIntegration:
    """Test GitGraphitiIntegration class."""

    @patch("repotoire.historical.git_graphiti.git.Repo")
    def test_initialization(self, mock_repo_class, mock_graphiti):
        """Test that GitGraphitiIntegration initializes correctly."""
        mock_repo_class.return_value = Mock()

        integration = GitGraphitiIntegration("/fake/repo", mock_graphiti)

        assert integration.graphiti == mock_graphiti
        assert integration.repo_path == Path("/fake/repo")
        mock_repo_class.assert_called_once_with("/fake/repo")

    @patch("repotoire.historical.git_graphiti.git.Repo")
    async def test_ingest_git_history(self, mock_repo_class, mock_repo, mock_graphiti):
        """Test git history ingestion."""
        mock_repo_class.return_value = mock_repo

        integration = GitGraphitiIntegration("/fake/repo", mock_graphiti)
        integration.repo = mock_repo

        stats = await integration.ingest_git_history(
            branch="main",
            max_commits=10,
            batch_size=5
        )

        # Verify stats
        assert stats["commits_processed"] == 1
        assert stats["errors"] == 0

        # Verify Graphiti was called
        assert mock_graphiti.add_episode.call_count == 1

        # Verify episode format
        call_args = mock_graphiti.add_episode.call_args
        assert "Add authentication feature" in call_args.kwargs["name"]
        assert "Implemented OAuth2 login flow" in call_args.kwargs["episode_body"]
        assert call_args.kwargs["reference_time"] == datetime(2024, 11, 15, 10, 30, tzinfo=timezone.utc)

    def test_format_commit_no_parent(self, mock_graphiti):
        """Test formatting commit with no parent (initial commit)."""
        integration = GitGraphitiIntegration.__new__(GitGraphitiIntegration)
        integration.graphiti = mock_graphiti

        commit = Mock(spec=git.Commit)
        commit.hexsha = "initial123"
        commit.summary = "Initial commit"
        commit.message = "Initial commit"
        commit.author.name = "John Doe"
        commit.author.email = "john@example.com"
        commit.committed_datetime = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        commit.stats.total = {"insertions": 100, "deletions": 0, "files": 5}
        commit.stats.files = {"main.py": {}, "README.md": {}}
        commit.parents = []

        result = integration._format_commit(commit)

        assert "Commit: initial123" in result
        assert "Summary: Initial commit" in result
        assert "John Doe" in result
        assert "+100 insertions" in result
        assert "-0 deletions" in result
        assert "2 files changed" in result  # Based on stats.files length, not stats.total

    def test_extract_code_changes(self, mock_graphiti):
        """Test extracting code changes from diffs."""
        integration = GitGraphitiIntegration.__new__(GitGraphitiIntegration)
        integration.graphiti = mock_graphiti

        # Create mock diff
        diff_mock = Mock()
        diff_mock.a_path = "auth/login.py"
        diff_mock.b_path = "auth/login.py"
        diff_mock.diff = b"+def authenticate_user(username, password):\n+    pass\n+class UserSession:\n+    pass"

        diffs = [diff_mock]

        changes = integration._extract_code_changes(diffs)

        assert len(changes) > 0
        assert any("authenticate_user" in change for change in changes)
        assert any("UserSession" in change for change in changes)

    @patch("repotoire.historical.git_graphiti.git.Repo")
    async def test_query_history(self, mock_repo_class, mock_graphiti):
        """Test querying git history."""
        mock_repo_class.return_value = Mock()

        integration = GitGraphitiIntegration("/fake/repo", mock_graphiti)

        result = await integration.query_history("When did we add authentication?")

        assert result == "Mock search results"
        mock_graphiti.search.assert_called_once()

    @patch("repotoire.historical.git_graphiti.git.Repo")
    async def test_get_entity_timeline(self, mock_repo_class, mock_graphiti):
        """Test getting entity timeline."""
        mock_repo_class.return_value = Mock()

        integration = GitGraphitiIntegration("/fake/repo", mock_graphiti)

        result = await integration.get_entity_timeline("authenticate_user", "function")

        assert result == "Mock search results"
        mock_graphiti.search.assert_called_once()
        call_args = mock_graphiti.search.call_args
        assert "authenticate_user" in call_args.kwargs["query"]
        assert "function" in call_args.kwargs["query"]

    @patch("repotoire.historical.git_graphiti.git.Repo")
    async def test_ingest_with_date_filter(self, mock_repo_class, mock_repo, mock_graphiti):
        """Test ingestion with date filtering."""
        # Create multiple commits with different dates
        commit1 = Mock(spec=git.Commit)
        commit1.hexsha = "commit1"
        commit1.summary = "Old commit"
        commit1.message = "Old commit"
        commit1.author.name = "John"
        commit1.author.email = "john@example.com"
        commit1.committed_datetime = datetime(2024, 1, 1, tzinfo=timezone.utc)
        commit1.stats.total = {"insertions": 10, "deletions": 0, "files": 1}
        commit1.stats.files = {"old.py": {}}
        commit1.parents = []

        commit2 = Mock(spec=git.Commit)
        commit2.hexsha = "commit2"
        commit2.summary = "New commit"
        commit2.message = "New commit"
        commit2.author.name = "John"
        commit2.author.email = "john@example.com"
        commit2.committed_datetime = datetime(2024, 11, 1, tzinfo=timezone.utc)
        commit2.stats.total = {"insertions": 20, "deletions": 0, "files": 1}
        commit2.stats.files = {"new.py": {}}
        commit2.parents = []

        mock_repo.iter_commits.return_value = [commit2, commit1]  # Newest first
        mock_repo_class.return_value = mock_repo

        integration = GitGraphitiIntegration("/fake/repo", mock_graphiti)
        integration.repo = mock_repo

        # Filter commits since October 2024
        stats = await integration.ingest_git_history(
            since=datetime(2024, 10, 1, tzinfo=timezone.utc),
            max_commits=10
        )

        # Should only process commit2
        assert stats["commits_processed"] == 1
        assert mock_graphiti.add_episode.call_count == 1
