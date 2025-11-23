"""TimescaleDB client for storing and querying code health metrics over time."""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from pathlib import Path

logger = logging.getLogger(__name__)


class TimescaleClient:
    """Client for TimescaleDB operations.

    Handles connection management, metric storage, and querying
    for time-series code health metrics.

    Example:
        >>> client = TimescaleClient("postgresql://user:pass@localhost:5432/metrics")
        >>> client.connect()
        >>> client.record_metrics(health, repository="repotoire", branch="main")
        >>> trend = client.get_trend("repotoire", days=30)
        >>> client.close()
    """

    def __init__(self, connection_string: str):
        """Initialize TimescaleDB client.

        Args:
            connection_string: PostgreSQL connection string
                Format: postgresql://user:password@host:port/database
        """
        self.connection_string = connection_string
        self._conn = None
        self._connected = False

    def connect(self) -> None:
        """Establish connection to TimescaleDB.

        Raises:
            ImportError: If psycopg2 is not installed
            Exception: If connection fails
        """
        try:
            import psycopg2
        except ImportError:
            raise ImportError(
                "psycopg2-binary is required for TimescaleDB support. "
                "Install with: pip install repotoire[timescale]"
            )

        try:
            self._conn = psycopg2.connect(self.connection_string)
            self._connected = True
            logger.info("Connected to TimescaleDB")

            # Verify TimescaleDB extension is installed
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM pg_extension WHERE extname = 'timescaledb'"
                )
                if cur.fetchone()[0] == 0:
                    logger.warning(
                        "TimescaleDB extension not found. "
                        "Run: CREATE EXTENSION timescaledb;"
                    )
        except Exception as e:
            logger.error(f"Failed to connect to TimescaleDB: {e}")
            raise

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._connected = False
            logger.info("Disconnected from TimescaleDB")

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

    def record_metrics(
        self,
        metrics: Dict[str, Any],
        repository: str,
        branch: str = "main",
        commit_sha: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[datetime] = None
    ) -> None:
        """Record code health metrics.

        Args:
            metrics: Dictionary of metric values
            repository: Repository identifier (path or name)
            branch: Git branch name
            commit_sha: Git commit SHA
            metadata: Additional metadata (team, version, etc.)
            timestamp: Analysis timestamp (defaults to now)

        Example:
            >>> metrics = {
            ...     "overall_health": 85.5,
            ...     "critical_count": 2,
            ...     "total_files": 150,
            ...     "modularity": 0.65
            ... }
            >>> client.record_metrics(
            ...     metrics,
            ...     repository="/path/to/repo",
            ...     branch="main",
            ...     commit_sha="abc123"
            ... )
        """
        if not self._connected:
            raise RuntimeError("Not connected to TimescaleDB. Call connect() first.")

        if timestamp is None:
            timestamp = datetime.now(timezone.utc)

        # Prepare metadata as JSON
        import json
        metadata_json = json.dumps(metadata) if metadata else None

        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO code_health_metrics (
                    time, repository, branch, commit_sha,
                    overall_health, structure_health, quality_health, architecture_health,
                    critical_count, high_count, medium_count, low_count, total_findings,
                    total_files, total_classes, total_functions, total_loc,
                    modularity, avg_coupling, circular_dependencies, bottleneck_count,
                    dead_code_percentage, duplication_percentage, god_class_count,
                    layer_violations, boundary_violations, abstraction_ratio,
                    metadata
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s
                )
                ON CONFLICT (time, repository, branch) DO UPDATE SET
                    commit_sha = EXCLUDED.commit_sha,
                    overall_health = EXCLUDED.overall_health,
                    structure_health = EXCLUDED.structure_health,
                    quality_health = EXCLUDED.quality_health,
                    architecture_health = EXCLUDED.architecture_health,
                    critical_count = EXCLUDED.critical_count,
                    high_count = EXCLUDED.high_count,
                    medium_count = EXCLUDED.medium_count,
                    low_count = EXCLUDED.low_count,
                    total_findings = EXCLUDED.total_findings,
                    total_files = EXCLUDED.total_files,
                    total_classes = EXCLUDED.total_classes,
                    total_functions = EXCLUDED.total_functions,
                    total_loc = EXCLUDED.total_loc,
                    modularity = EXCLUDED.modularity,
                    avg_coupling = EXCLUDED.avg_coupling,
                    circular_dependencies = EXCLUDED.circular_dependencies,
                    bottleneck_count = EXCLUDED.bottleneck_count,
                    dead_code_percentage = EXCLUDED.dead_code_percentage,
                    duplication_percentage = EXCLUDED.duplication_percentage,
                    god_class_count = EXCLUDED.god_class_count,
                    layer_violations = EXCLUDED.layer_violations,
                    boundary_violations = EXCLUDED.boundary_violations,
                    abstraction_ratio = EXCLUDED.abstraction_ratio,
                    metadata = EXCLUDED.metadata
                """,
                (
                    timestamp, repository, branch, commit_sha,
                    metrics.get("overall_health"),
                    metrics.get("structure_health"),
                    metrics.get("quality_health"),
                    metrics.get("architecture_health"),
                    metrics.get("critical_count", 0),
                    metrics.get("high_count", 0),
                    metrics.get("medium_count", 0),
                    metrics.get("low_count", 0),
                    metrics.get("total_findings", 0),
                    metrics.get("total_files", 0),
                    metrics.get("total_classes", 0),
                    metrics.get("total_functions", 0),
                    metrics.get("total_loc", 0),
                    metrics.get("modularity", 0.0),
                    metrics.get("avg_coupling", 0.0),
                    metrics.get("circular_dependencies", 0),
                    metrics.get("bottleneck_count", 0),
                    metrics.get("dead_code_percentage", 0.0),
                    metrics.get("duplication_percentage", 0.0),
                    metrics.get("god_class_count", 0),
                    metrics.get("layer_violations", 0),
                    metrics.get("boundary_violations", 0),
                    metrics.get("abstraction_ratio", 0.0),
                    metadata_json
                )
            )

        self._conn.commit()
        logger.debug(f"Recorded metrics for {repository}:{branch} at {timestamp}")

    def get_trend(
        self,
        repository: str,
        branch: str = "main",
        days: int = 30
    ) -> List[Dict[str, Any]]:
        """Get health score trend over time.

        Args:
            repository: Repository identifier
            branch: Git branch name
            days: Number of days to look back

        Returns:
            List of time-series data points with metrics
        """
        if not self._connected:
            raise RuntimeError("Not connected to TimescaleDB")

        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    time,
                    overall_health,
                    structure_health,
                    quality_health,
                    architecture_health,
                    total_findings,
                    critical_count,
                    high_count,
                    commit_sha
                FROM code_health_metrics
                WHERE repository = %s
                  AND branch = %s
                  AND time > NOW() - INTERVAL '%s days'
                ORDER BY time ASC
                """,
                (repository, branch, days)
            )

            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    def detect_regression(
        self,
        repository: str,
        branch: str = "main",
        threshold: float = 5.0
    ) -> Optional[Dict[str, Any]]:
        """Detect if health score dropped significantly.

        Args:
            repository: Repository identifier
            branch: Git branch name
            threshold: Minimum health score drop to flag as regression

        Returns:
            Regression details if detected, None otherwise
        """
        if not self._connected:
            raise RuntimeError("Not connected to TimescaleDB")

        with self._conn.cursor() as cur:
            cur.execute(
                """
                WITH recent AS (
                    SELECT
                        time,
                        overall_health,
                        commit_sha
                    FROM code_health_metrics
                    WHERE repository = %s AND branch = %s
                    ORDER BY time DESC
                    LIMIT 2
                )
                SELECT
                    (SELECT overall_health FROM recent ORDER BY time ASC LIMIT 1) as prev_score,
                    (SELECT overall_health FROM recent ORDER BY time DESC LIMIT 1) as current_score,
                    (SELECT time FROM recent ORDER BY time ASC LIMIT 1) as prev_time,
                    (SELECT time FROM recent ORDER BY time DESC LIMIT 1) as current_time,
                    (SELECT commit_sha FROM recent ORDER BY time ASC LIMIT 1) as prev_commit,
                    (SELECT commit_sha FROM recent ORDER BY time DESC LIMIT 1) as current_commit
                """,
                (repository, branch)
            )

            result = cur.fetchone()
            if result and result[0] is not None and result[1] is not None:
                prev_score, current_score, prev_time, current_time, prev_commit, current_commit = result
                drop = prev_score - current_score

                if drop > threshold:
                    return {
                        "regression_detected": True,
                        "previous_score": prev_score,
                        "current_score": current_score,
                        "health_drop": drop,
                        "previous_time": prev_time,
                        "current_time": current_time,
                        "previous_commit": prev_commit,
                        "current_commit": current_commit
                    }

        return None

    def compare_periods(
        self,
        repository: str,
        start_date: datetime,
        end_date: datetime,
        branch: str = "main"
    ) -> Dict[str, Any]:
        """Compare metrics between two time periods.

        Args:
            repository: Repository identifier
            start_date: Start of comparison period
            end_date: End of comparison period
            branch: Git branch name

        Returns:
            Comparison statistics
        """
        if not self._connected:
            raise RuntimeError("Not connected to TimescaleDB")

        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    AVG(overall_health) as avg_health,
                    MIN(overall_health) as min_health,
                    MAX(overall_health) as max_health,
                    AVG(total_findings) as avg_issues,
                    SUM(critical_count) as total_critical,
                    SUM(high_count) as total_high,
                    COUNT(*) as num_analyses
                FROM code_health_metrics
                WHERE repository = %s
                  AND branch = %s
                  AND time BETWEEN %s AND %s
                """,
                (repository, branch, start_date, end_date)
            )

            row = cur.fetchone()
            if row:
                return {
                    "avg_health": float(row[0]) if row[0] else None,
                    "min_health": float(row[1]) if row[1] else None,
                    "max_health": float(row[2]) if row[2] else None,
                    "avg_issues": float(row[3]) if row[3] else None,
                    "total_critical": int(row[4]) if row[4] else 0,
                    "total_high": int(row[5]) if row[5] else 0,
                    "num_analyses": int(row[6]) if row[6] else 0
                }

        return {}

    def get_latest_metrics(
        self,
        repository: str,
        branch: str = "main"
    ) -> Optional[Dict[str, Any]]:
        """Get most recent metrics for a repository/branch.

        Args:
            repository: Repository identifier
            branch: Git branch name

        Returns:
            Latest metrics or None if not found
        """
        if not self._connected:
            raise RuntimeError("Not connected to TimescaleDB")

        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM code_health_metrics
                WHERE repository = %s AND branch = %s
                ORDER BY time DESC
                LIMIT 1
                """,
                (repository, branch)
            )

            row = cur.fetchone()
            if row:
                columns = [desc[0] for desc in cur.description]
                return dict(zip(columns, row))

        return None
