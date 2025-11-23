"""Integration tests for TimescaleDB metrics tracking.

These tests require Docker and will start a TimescaleDB container automatically.
They test the full end-to-end flow:
1. Schema creation and initialization
2. Metrics recording from CodebaseHealth
3. Querying trends, regressions, and comparisons
4. CLI command integration
"""

import pytest
import os
import time
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Skip if TimescaleDB dependencies not installed
pytest.importorskip("psycopg2")

from repotoire.historical import TimescaleClient, MetricsCollector
from repotoire.models import CodebaseHealth, MetricsBreakdown, FindingsSummary


@pytest.fixture(scope="module")
def timescale_container():
    """Start TimescaleDB container for testing."""
    # Check if Docker is available
    try:
        subprocess.run(["docker", "version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("Docker not available")

    container_name = "repotoire-test-timescaledb"

    # Stop and remove existing container if present
    subprocess.run(
        ["docker", "rm", "-f", container_name],
        capture_output=True
    )

    # Start TimescaleDB container
    subprocess.run([
        "docker", "run",
        "-d",
        "--name", container_name,
        "-e", "POSTGRES_DB=repotoire_test",
        "-e", "POSTGRES_USER=repotoire",
        "-e", "POSTGRES_PASSWORD=test_password",
        "-p", "5433:5432",  # Use different port to avoid conflicts
        "timescale/timescaledb:latest-pg16"
    ], check=True)

    # Wait for database to be ready
    max_retries = 30
    for i in range(max_retries):
        try:
            result = subprocess.run(
                ["docker", "exec", container_name, "pg_isready", "-U", "repotoire"],
                capture_output=True,
                timeout=2
            )
            if result.returncode == 0:
                break
        except subprocess.TimeoutExpired:
            pass
        time.sleep(1)
    else:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
        pytest.fail("TimescaleDB container failed to start")

    # Additional wait for schema to initialize
    time.sleep(2)

    connection_string = "postgresql://repotoire:test_password@localhost:5433/repotoire_test"

    # Initialize schema
    schema_path = Path(__file__).parent.parent.parent / "repotoire" / "historical" / "schema.sql"
    subprocess.run([
        "docker", "exec", "-i", container_name,
        "psql", "-U", "repotoire", "-d", "repotoire_test"
    ], stdin=open(schema_path), check=True)

    yield connection_string

    # Cleanup
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)


@pytest.fixture
def sample_health():
    """Create a sample CodebaseHealth object for testing."""
    return CodebaseHealth(
        grade="B",
        overall_score=85.5,
        structure_score=90.0,
        quality_score=82.0,
        architecture_score=84.0,
        metrics=MetricsBreakdown(
            total_files=100,
            total_classes=50,
            total_functions=200,
            modularity=0.75,
            avg_coupling=3.2,
            circular_dependencies=2,
            bottleneck_count=3,
            dead_code_percentage=5.0,
            duplication_percentage=8.0,
            god_class_count=1,
            layer_violations=0,
            boundary_violations=1,
            abstraction_ratio=0.6,
        ),
        findings=[],
        findings_summary=FindingsSummary(
            critical=0,
            high=2,
            medium=5,
            low=10,
            info=15
        ),
    )


class TestMetricsCollector:
    """Test MetricsCollector extracts correct data from CodebaseHealth."""

    def test_extract_metrics(self, sample_health):
        """Test metrics extraction from CodebaseHealth."""
        collector = MetricsCollector()
        metrics = collector.extract_metrics(sample_health)

        # Verify health scores
        assert metrics["overall_health"] == 85.5
        assert metrics["structure_health"] == 90.0
        assert metrics["quality_health"] == 82.0
        assert metrics["architecture_health"] == 84.0

        # Verify codebase statistics
        assert metrics["total_files"] == 100
        assert metrics["total_classes"] == 50
        assert metrics["total_functions"] == 200

        # Verify structural metrics
        assert metrics["modularity"] == 0.75
        assert metrics["avg_coupling"] == 3.2
        assert metrics["circular_dependencies"] == 2

        # Verify issue counts (from findings, not summary)
        assert metrics["critical_count"] == 0
        assert metrics["high_count"] == 0
        assert metrics["total_findings"] == 0

    def test_extract_metadata(self):
        """Test metadata extraction."""
        collector = MetricsCollector()
        metadata = collector.extract_metadata(
            team="platform",
            version="1.2.3",
            ci_build_id="build-456",
            unknown_key=None
        )

        assert metadata["team"] == "platform"
        assert metadata["version"] == "1.2.3"
        assert metadata["ci_build_id"] == "build-456"
        assert "unknown_key" not in metadata  # None values filtered


class TestTimescaleClient:
    """Test TimescaleDB client operations."""

    def test_connection(self, timescale_container):
        """Test basic connection to TimescaleDB."""
        with TimescaleClient(timescale_container) as client:
            assert client._connected

    def test_record_metrics(self, timescale_container, sample_health):
        """Test recording metrics to TimescaleDB."""
        collector = MetricsCollector()
        metrics = collector.extract_metrics(sample_health)

        with TimescaleClient(timescale_container) as client:
            client.record_metrics(
                metrics=metrics,
                repository="/test/repo",
                branch="main",
                commit_sha="abc123",
            )

            # Verify data was recorded
            latest = client.get_latest_metrics("/test/repo", branch="main")
            assert latest is not None
            assert latest["overall_health"] == 85.5
            assert latest["commit_sha"] == "abc123"

    def test_upsert_behavior(self, timescale_container, sample_health):
        """Test that duplicate records are updated (UPSERT)."""
        collector = MetricsCollector()
        metrics = collector.extract_metrics(sample_health)

        timestamp = datetime.now(timezone.utc)

        with TimescaleClient(timescale_container) as client:
            # Record first time
            client.record_metrics(
                metrics=metrics,
                repository="/test/repo2",
                branch="main",
                timestamp=timestamp
            )

            # Update with same timestamp (should UPSERT)
            metrics["overall_health"] = 90.0
            client.record_metrics(
                metrics=metrics,
                repository="/test/repo2",
                branch="main",
                timestamp=timestamp
            )

            # Verify only one record with updated value
            latest = client.get_latest_metrics("/test/repo2", branch="main")
            assert latest["overall_health"] == 90.0

    def test_get_trend(self, timescale_container, sample_health):
        """Test trend retrieval over time."""
        collector = MetricsCollector()
        base_metrics = collector.extract_metrics(sample_health)

        with TimescaleClient(timescale_container) as client:
            # Record metrics for 5 days
            for i in range(5):
                timestamp = datetime.now(timezone.utc) - timedelta(days=4-i)
                metrics = base_metrics.copy()
                metrics["overall_health"] = 80.0 + i * 2  # Gradual improvement

                client.record_metrics(
                    metrics=metrics,
                    repository="/test/repo3",
                    branch="main",
                    commit_sha=f"commit-{i}",
                    timestamp=timestamp
                )

            # Get trend
            trend = client.get_trend("/test/repo3", branch="main", days=7)
            assert len(trend) == 5
            assert trend[0]["overall_health"] == 80.0
            assert trend[4]["overall_health"] == 88.0

    def test_detect_regression(self, timescale_container, sample_health):
        """Test regression detection."""
        collector = MetricsCollector()
        metrics = collector.extract_metrics(sample_health)

        with TimescaleClient(timescale_container) as client:
            # Record good health
            metrics["overall_health"] = 90.0
            client.record_metrics(
                metrics=metrics,
                repository="/test/repo4",
                branch="main",
                timestamp=datetime.now(timezone.utc) - timedelta(hours=2)
            )

            # Record regression
            metrics["overall_health"] = 80.0  # 10 point drop
            client.record_metrics(
                metrics=metrics,
                repository="/test/repo4",
                branch="main",
                timestamp=datetime.now(timezone.utc)
            )

            # Detect regression
            regression = client.detect_regression("/test/repo4", branch="main", threshold=5.0)
            assert regression is not None
            assert regression["regression_detected"] is True
            assert regression["health_drop"] == 10.0
            assert regression["previous_score"] == 90.0
            assert regression["current_score"] == 80.0

    def test_no_regression(self, timescale_container, sample_health):
        """Test no regression when health improves."""
        collector = MetricsCollector()
        metrics = collector.extract_metrics(sample_health)

        with TimescaleClient(timescale_container) as client:
            # Record baseline
            metrics["overall_health"] = 80.0
            client.record_metrics(
                metrics=metrics,
                repository="/test/repo5",
                branch="main",
                timestamp=datetime.now(timezone.utc) - timedelta(hours=2)
            )

            # Record improvement
            metrics["overall_health"] = 85.0
            client.record_metrics(
                metrics=metrics,
                repository="/test/repo5",
                branch="main",
                timestamp=datetime.now(timezone.utc)
            )

            # Should not detect regression
            regression = client.detect_regression("/test/repo5", branch="main", threshold=5.0)
            assert regression is None

    def test_compare_periods(self, timescale_container, sample_health):
        """Test period comparison."""
        collector = MetricsCollector()
        metrics = collector.extract_metrics(sample_health)

        start_date = datetime.now(timezone.utc) - timedelta(days=7)
        end_date = datetime.now(timezone.utc)

        with TimescaleClient(timescale_container) as client:
            # Record metrics over a week
            for i in range(7):
                timestamp = start_date + timedelta(days=i)
                metrics_copy = metrics.copy()
                metrics_copy["overall_health"] = 80.0 + i  # 80-86
                metrics_copy["critical_count"] = i % 3  # 0-2

                client.record_metrics(
                    metrics=metrics_copy,
                    repository="/test/repo6",
                    branch="main",
                    timestamp=timestamp
                )

            # Compare period
            stats = client.compare_periods("/test/repo6", start_date, end_date, branch="main")
            assert stats["num_analyses"] == 7
            assert 80.0 <= stats["avg_health"] <= 86.0
            assert stats["min_health"] == 80.0
            assert stats["max_health"] == 86.0
            assert stats["total_critical"] >= 0


class TestCLIIntegration:
    """Test CLI integration with TimescaleDB."""

    def test_analyze_with_track_metrics(self, timescale_container, sample_health, tmp_path, monkeypatch):
        """Test analyze command with --track-metrics flag."""
        # This is a unit-level test since full end-to-end would require Neo4j
        # We test that the _record_metrics_to_timescale function works correctly

        from repotoire.cli import _record_metrics_to_timescale, _extract_git_info
        from repotoire.config import FalkorConfig, TimescaleConfig

        # Set up config with TimescaleDB connection
        config = FalkorConfig()
        config.timescale = TimescaleConfig(
            enabled=True,
            connection_string=timescale_container,
            auto_track=False
        )

        # Test git extraction (should work in this repo)
        repo_path = Path(__file__).parent.parent.parent
        git_info = _extract_git_info(repo_path)
        assert git_info["branch"] is not None
        assert git_info["commit_sha"] is not None

        # Test metrics recording
        _record_metrics_to_timescale(
            health=sample_health,
            repo_path=repo_path,
            config=config,
            quiet=True
        )

        # Verify metrics were recorded
        with TimescaleClient(timescale_container) as client:
            latest = client.get_latest_metrics(str(repo_path), branch=git_info["branch"])
            assert latest is not None
            assert latest["overall_health"] == 85.5


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_database(self, timescale_container):
        """Test querying empty database."""
        with TimescaleClient(timescale_container) as client:
            # Should return None/empty results, not error
            latest = client.get_latest_metrics("/nonexistent/repo")
            assert latest is None

            trend = client.get_trend("/nonexistent/repo", days=30)
            assert trend == []

            regression = client.detect_regression("/nonexistent/repo")
            assert regression is None

    def test_invalid_connection_string(self):
        """Test handling of invalid connection string."""
        with pytest.raises(Exception):  # psycopg2 connection error
            client = TimescaleClient("postgresql://invalid:invalid@invalid:9999/invalid")
            client.connect()

    def test_branch_isolation(self, timescale_container, sample_health):
        """Test that different branches are isolated."""
        collector = MetricsCollector()
        metrics = collector.extract_metrics(sample_health)

        with TimescaleClient(timescale_container) as client:
            # Record metrics for main branch
            metrics["overall_health"] = 90.0
            client.record_metrics(
                metrics=metrics,
                repository="/test/repo7",
                branch="main",
            )

            # Record metrics for dev branch
            metrics["overall_health"] = 80.0
            client.record_metrics(
                metrics=metrics,
                repository="/test/repo7",
                branch="dev",
            )

            # Verify isolation
            main_latest = client.get_latest_metrics("/test/repo7", branch="main")
            dev_latest = client.get_latest_metrics("/test/repo7", branch="dev")

            assert main_latest["overall_health"] == 90.0
            assert dev_latest["overall_health"] == 80.0
