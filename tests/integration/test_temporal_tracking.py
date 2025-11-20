"""Integration tests for temporal tracking and Git history analysis."""

import pytest
from datetime import datetime
from unittest.mock import Mock, MagicMock, patch

from repotoire.models import GitCommit, SessionEntity, MetricTrend, CodeHotspot
from repotoire.integrations.git import GitRepository
from repotoire.detectors.temporal_metrics import TemporalMetrics


class TestGitRepository:
    """Test Git repository integration."""

    def test_git_repository_initialization(self, tmp_path):
        """Test GitRepository can be initialized with a valid repo."""
        # Create a mock Git repo
        with patch('git.Repo') as mock_repo_class:
            mock_repo = Mock()
            mock_repo_class.return_value = mock_repo

            repo = GitRepository(str(tmp_path))

            assert repo.repo_path == tmp_path
            assert repo.repo == mock_repo

    def test_git_repository_invalid_path(self, tmp_path):
        """Test GitRepository raises error for non-Git directory."""
        with patch('git.Repo') as mock_repo_class:
            from git.exc import InvalidGitRepositoryError
            mock_repo_class.side_effect = InvalidGitRepositoryError

            with pytest.raises(ValueError, match="Not a Git repository"):
                GitRepository(str(tmp_path))

    def test_get_commit_history(self, tmp_path):
        """Test retrieving commit history."""
        with patch('git.Repo') as mock_repo_class:
            # Create mock tree item (file blob)
            mock_blob = Mock()
            mock_blob.type = 'blob'
            mock_blob.path = 'test_file.py'

            # Create mock tree
            mock_tree = Mock()
            mock_tree.traverse.return_value = [mock_blob]

            # Create mock commit
            mock_commit = Mock()
            mock_commit.hexsha = "abc123def456"
            mock_commit.message = "Test commit"
            mock_commit.author.name = "Test Author"
            mock_commit.author.email = "test@example.com"
            mock_commit.committed_date = 1234567890
            mock_commit.parents = []
            mock_commit.tree = mock_tree
            mock_commit.stats.total = {"insertions": 10, "deletions": 5, "files": 2}

            # Mock active branch
            mock_branch = Mock()
            mock_branch.name = "main"

            # Mock repo
            mock_repo = Mock()
            mock_repo.iter_commits.return_value = [mock_commit]
            mock_repo.active_branch = mock_branch
            mock_repo.branches = [mock_branch]
            mock_repo_class.return_value = mock_repo

            repo = GitRepository(str(tmp_path))
            commits = repo.get_commit_history(max_commits=10)

            assert len(commits) == 1
            assert commits[0].hash == "abc123def456"
            assert commits[0].short_hash == "abc123d"
            assert commits[0].message == "Test commit"
            assert commits[0].author == "Test Author"
            assert commits[0].author_email == "test@example.com"


class TestSessionEntity:
    """Test Session entity model."""

    def test_session_entity_creation(self):
        """Test creating a SessionEntity."""
        session = SessionEntity(
            name="c5ec541",
            qualified_name="session::c5ec541abcd",
            file_path=".",
            line_start=0,
            line_end=0,
            commit_hash="c5ec541abcd1234567890",
            commit_message="Test commit",
            author="John Doe",
            author_email="john@example.com",
            committed_at=datetime.now(),
            branch="main",
            parent_hashes=["abc123"],
            files_changed=5,
            insertions=150,
            deletions=30,
        )

        assert session.commit_hash == "c5ec541abcd1234567890"
        assert session.author == "John Doe"
        assert session.branch == "main"
        assert session.files_changed == 5
        assert len(session.parent_hashes) == 1


class TestGitCommit:
    """Test GitCommit data class."""

    def test_git_commit_creation(self):
        """Test creating a GitCommit."""
        commit = GitCommit(
            hash="abc123def456",
            short_hash="abc123d",
            message="Add new feature",
            author="Jane Doe",
            author_email="jane@example.com",
            committed_at=datetime.now(),
            parent_hashes=["parent123"],
            branch="main",
            changed_files=["src/file.py"],
            stats={"insertions": 100, "deletions": 20, "files_changed": 1}
        )

        assert commit.hash == "abc123def456"
        assert commit.short_hash == "abc123d"
        assert commit.author == "Jane Doe"
        assert "src/file.py" in commit.changed_files
        assert commit.stats["insertions"] == 100


class TestMetricTrend:
    """Test MetricTrend model."""

    def test_metric_trend_creation(self):
        """Test creating a MetricTrend."""
        trend = MetricTrend(
            metric_name="modularity",
            values=[0.68, 0.65, 0.60, 0.52],
            timestamps=[datetime.now() for _ in range(4)],
            trend_direction="decreasing",
            change_percentage=-23.5,
            velocity=-0.002,
            is_degrading=True
        )

        assert trend.metric_name == "modularity"
        assert len(trend.values) == 4
        assert trend.trend_direction == "decreasing"
        assert trend.is_degrading is True


class TestCodeHotspot:
    """Test CodeHotspot model."""

    def test_code_hotspot_creation(self):
        """Test creating a CodeHotspot."""
        hotspot = CodeHotspot(
            file_path="auth/session_manager.py",
            churn_count=23,
            complexity_velocity=1.4,
            coupling_velocity=0.3,
            risk_score=32.2,
            last_modified=datetime.now(),
            top_authors=["Jane Doe", "John Smith"]
        )

        assert hotspot.file_path == "auth/session_manager.py"
        assert hotspot.churn_count == 23
        assert hotspot.risk_score == 32.2
        assert len(hotspot.top_authors) == 2


class TestTemporalMetrics:
    """Test TemporalMetrics analyzer."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Neo4j client."""
        return Mock()

    def test_temporal_metrics_initialization(self, mock_client):
        """Test TemporalMetrics initializes correctly."""
        analyzer = TemporalMetrics(mock_client)
        assert analyzer.client == mock_client

    def test_calculate_trend_direction_increasing(self, mock_client):
        """Test trend direction calculation for increasing values."""
        analyzer = TemporalMetrics(mock_client)
        values = [1.0, 1.5, 2.0, 2.5, 3.0]
        direction = analyzer._calculate_trend_direction(values)
        assert direction == "increasing"

    def test_calculate_trend_direction_decreasing(self, mock_client):
        """Test trend direction calculation for decreasing values."""
        analyzer = TemporalMetrics(mock_client)
        values = [3.0, 2.5, 2.0, 1.5, 1.0]
        direction = analyzer._calculate_trend_direction(values)
        assert direction == "decreasing"

    def test_calculate_trend_direction_stable(self, mock_client):
        """Test trend direction calculation for stable values."""
        analyzer = TemporalMetrics(mock_client)
        values = [2.0, 2.0, 2.0, 2.0, 2.0]
        direction = analyzer._calculate_trend_direction(values)
        assert direction == "stable"

    def test_calculate_velocity(self, mock_client):
        """Test velocity calculation."""
        analyzer = TemporalMetrics(mock_client)
        values = [1.0, 2.0, 3.0]
        timestamps = [
            datetime(2024, 1, 1),
            datetime(2024, 1, 11),  # 10 days later
            datetime(2024, 1, 21),  # 20 days from start
        ]
        velocity = analyzer._calculate_velocity(values, timestamps)
        # Total change: 3.0 - 1.0 = 2.0
        # Time span: 20 days
        # Velocity: 2.0 / 20 = 0.1
        assert velocity == pytest.approx(0.1, 0.01)

    def test_get_metric_trend_with_no_data(self, mock_client):
        """Test get_metric_trend returns None when no data."""
        mock_client.execute_query.return_value = []

        analyzer = TemporalMetrics(mock_client)
        trend = analyzer.get_metric_trend("modularity", window_days=90)

        assert trend is None

    def test_find_code_hotspots_empty_result(self, mock_client):
        """Test find_code_hotspots returns empty list when no hotspots."""
        mock_client.execute_query.return_value = []

        analyzer = TemporalMetrics(mock_client)
        hotspots = analyzer.find_code_hotspots(window_days=90, min_churn=5)

        assert hotspots == []

    def test_compare_commits_not_found(self, mock_client):
        """Test compare_commits returns empty dict when commits not found."""
        mock_client.execute_query.return_value = []

        analyzer = TemporalMetrics(mock_client)
        comparison = analyzer.compare_commits("abc123", "def456")

        assert comparison == {}
