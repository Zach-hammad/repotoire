"""Temporal metrics analyzer for code evolution tracking.

Analyzes how code metrics change over time to detect degradation patterns,
code hotspots, and technical debt velocity.
"""

import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Union
from statistics import mean

from repotoire.graph.base import DatabaseClient
from repotoire.graph.client import Neo4jClient
from repotoire.models import MetricTrend, CodeHotspot
from repotoire.logging_config import get_logger
from repotoire.validation import validate_identifier

logger = get_logger(__name__)


class TemporalMetrics:
    """Analyze code evolution metrics over time.

    Provides methods to:
    - Track metric trends (modularity, coupling, complexity)
    - Detect code hotspots (high churn + increasing complexity)
    - Calculate technical debt velocity
    - Compare commits (before/after analysis)

    Example:
        >>> analyzer = TemporalMetrics(neo4j_client)
        >>> trend = analyzer.get_metric_trend("modularity", window_days=90)
        >>> hotspots = analyzer.find_code_hotspots(window_days=90)
    """

    def __init__(self, client: Union[Neo4jClient, DatabaseClient]):
        """Initialize temporal metrics analyzer.

        Args:
            client: Database client (Neo4j or FalkorDB)
        """
        self.client = client
        self._is_falkordb = getattr(client, 'is_falkordb', False)
        logger.info("Initialized TemporalMetrics analyzer")

    def get_metric_trend(
        self,
        metric_name: str,
        window_days: int = 90
    ) -> Optional[MetricTrend]:
        """Get trend for a specific metric over time.

        Args:
            metric_name: Name of metric (e.g., "modularity", "coupling")
            window_days: Time window in days

        Returns:
            MetricTrend object or None if no data

        Example:
            >>> trend = analyzer.get_metric_trend("modularity", window_days=30)
            >>> trend.trend_direction in ["increasing", "decreasing", "stable"]
            True
        """
        # Validate metric_name to prevent Cypher injection
        # Cypher doesn't support parameterized property access (object.$prop),
        # so we validate the metric name and use it in f-string
        validated_metric_name = validate_identifier(metric_name, "metric name")

        # FalkorDB doesn't support datetime() or duration() - use UNIX timestamps
        if self._is_falkordb:
            cutoff_timestamp = int((datetime.now(timezone.utc) - timedelta(days=window_days)).timestamp())
            query = f"""
            MATCH (s:Session)
            WHERE s.committedAt >= $cutoff_timestamp
            AND s.metricsSnapshot IS NOT NULL
            AND s.metricsSnapshot.{validated_metric_name} IS NOT NULL
            RETURN
                s.committedAt as timestamp,
                s.metricsSnapshot.{validated_metric_name} as value
            ORDER BY s.committedAt ASC
            """
            params = {"cutoff_timestamp": cutoff_timestamp}
        else:
            query = f"""
            MATCH (s:Session)
            WHERE s.committedAt >= datetime() - duration({{days: $window_days}})
            AND s.metricsSnapshot IS NOT NULL
            AND s.metricsSnapshot.{validated_metric_name} IS NOT NULL
            RETURN
                s.committedAt as timestamp,
                s.metricsSnapshot.{validated_metric_name} as value
            ORDER BY s.committedAt ASC
            """
            params = {"window_days": window_days}

        try:
            results = self.client.execute_query(query, params)

            if not results or len(results) < 2:
                logger.warning(f"Insufficient data for metric '{metric_name}'")
                return None

            timestamps = [r["timestamp"] for r in results]
            values = [float(r["value"]) for r in results]

            # Calculate trend statistics
            trend_direction = self._calculate_trend_direction(values)
            change_pct = ((values[-1] - values[0]) / values[0]) * 100 if values[0] != 0 else 0
            velocity = self._calculate_velocity(values, timestamps)

            # Determine if degrading (depends on metric type)
            degrading_metrics = {"coupling", "circular_dependencies", "dead_code_percentage"}
            improving_metrics = {"modularity", "abstraction_ratio"}

            is_degrading = False
            if metric_name in degrading_metrics and trend_direction == "increasing":
                is_degrading = True
            elif metric_name in improving_metrics and trend_direction == "decreasing":
                is_degrading = True

            return MetricTrend(
                metric_name=metric_name,
                values=values,
                timestamps=timestamps,
                trend_direction=trend_direction,
                change_percentage=change_pct,
                velocity=velocity,
                is_degrading=is_degrading,
            )

        except Exception as e:
            logger.error(f"Failed to get metric trend for '{metric_name}': {e}")
            return None

    def find_code_hotspots(
        self,
        window_days: int = 90,
        min_churn: int = 5
    ) -> List[CodeHotspot]:
        """Find code hotspots with high churn and increasing complexity.

        Hotspots are files that:
        - Have been modified frequently (high churn)
        - Have increasing complexity or coupling
        - Represent high technical debt risk

        Args:
            window_days: Time window in days
            min_churn: Minimum number of modifications to qualify

        Returns:
            List of CodeHotspot objects sorted by risk score

        Example:
            >>> hotspots = analyzer.find_code_hotspots(window_days=90, min_churn=5)
            >>> all(h.churn_count >= 5 for h in hotspots)
            True
        """
        # FalkorDB doesn't support datetime() or duration() - use UNIX timestamps
        if self._is_falkordb:
            cutoff_timestamp = int((datetime.now(timezone.utc) - timedelta(days=window_days)).timestamp())
            query = """
            MATCH (s:Session)-[:MODIFIED]->(f:File)
            WHERE s.committedAt >= $cutoff_timestamp
            WITH f.filePath as path, count(s) as churn_count, max(s.committedAt) as last_modified
            WHERE churn_count >= $min_churn
            RETURN path, churn_count, last_modified
            ORDER BY churn_count DESC
            LIMIT 50
            """
            params = {"cutoff_timestamp": cutoff_timestamp, "min_churn": min_churn}
        else:
            query = """
            MATCH (s:Session)-[:MODIFIED]->(f:File)
            WHERE s.committedAt >= datetime() - duration({days: $window_days})
            WITH f.filePath as path, count(s) as churn_count, max(s.committedAt) as last_modified
            WHERE churn_count >= $min_churn
            RETURN path, churn_count, last_modified
            ORDER BY churn_count DESC
            LIMIT 50
            """
            params = {"window_days": window_days, "min_churn": min_churn}

        try:
            results = self.client.execute_query(query, params)

            if not results:
                logger.info("No code hotspots found")
                return []

            hotspots = []

            for result in results:
                file_path = result["path"]
                churn_count = result["churn_count"]
                last_modified = result["last_modified"]

                # Calculate complexity and coupling velocity
                complexity_vel = self._calculate_file_complexity_velocity(file_path, window_days)
                coupling_vel = self._calculate_file_coupling_velocity(file_path, window_days)

                # Calculate risk score (churn * complexity velocity)
                risk_score = churn_count * max(complexity_vel, 0.1)

                # Get top authors
                top_authors = self._get_file_top_authors(file_path, window_days)

                hotspots.append(CodeHotspot(
                    file_path=file_path,
                    churn_count=churn_count,
                    complexity_velocity=complexity_vel,
                    coupling_velocity=coupling_vel,
                    risk_score=risk_score,
                    last_modified=last_modified,
                    top_authors=top_authors[:3]  # Top 3 authors
                ))

            # Sort by risk score descending
            hotspots.sort(key=lambda h: h.risk_score, reverse=True)

            logger.info(f"Found {len(hotspots)} code hotspots")
            return hotspots

        except Exception as e:
            logger.error(f"Failed to find code hotspots: {e}")
            return []

    def compare_commits(
        self,
        before_hash: str,
        after_hash: str
    ) -> dict:
        """Compare metrics between two commits.

        Args:
            before_hash: Commit hash for before state
            after_hash: Commit hash for after state

        Returns:
            Dict with metric comparisons and changes

        Example:
            >>> comparison = analyzer.compare_commits("abc123", "def456")
            >>> "improvements" in comparison
            True
        """
        query = """
        MATCH (before:Session {commitHash: $before_hash})
        MATCH (after:Session {commitHash: $after_hash})
        RETURN
            before.metricsSnapshot as before_metrics,
            after.metricsSnapshot as after_metrics,
            before.committedAt as before_date,
            after.committedAt as after_date
        """

        try:
            results = self.client.execute_query(query, {
                "before_hash": before_hash,
                "after_hash": after_hash
            })

            if not results:
                logger.warning(f"Could not find sessions for commits {before_hash[:7]}, {after_hash[:7]}")
                return {}

            result = results[0]
            before = result["before_metrics"] or {}
            after = result["after_metrics"] or {}

            comparison = {
                "before_commit": before_hash[:7],
                "after_commit": after_hash[:7],
                "before_date": result["before_date"],
                "after_date": result["after_date"],
                "improvements": [],
                "regressions": [],
                "changes": {}
            }

            # Compare each metric
            for metric_name in ["modularity", "coupling", "circular_dependencies", "dead_code_percentage"]:
                if metric_name in before and metric_name in after:
                    before_val = before[metric_name]
                    after_val = after[metric_name]
                    change = after_val - before_val
                    change_pct = (change / before_val * 100) if before_val != 0 else 0

                    comparison["changes"][metric_name] = {
                        "before": before_val,
                        "after": after_val,
                        "change": change,
                        "change_percentage": change_pct
                    }

                    # Determine if improvement or regression
                    if metric_name in ["modularity"]:
                        if change > 0:
                            comparison["improvements"].append(metric_name)
                        elif change < 0:
                            comparison["regressions"].append(metric_name)
                    else:  # Higher is worse
                        if change < 0:
                            comparison["improvements"].append(metric_name)
                        elif change > 0:
                            comparison["regressions"].append(metric_name)

            return comparison

        except Exception as e:
            logger.error(f"Failed to compare commits: {e}")
            return {}

    def _calculate_trend_direction(self, values: List[float]) -> str:
        """Calculate trend direction from values.

        Args:
            values: List of metric values over time

        Returns:
            "increasing", "decreasing", or "stable"
        """
        if len(values) < 2:
            return "stable"

        # Simple linear regression approach
        n = len(values)
        x_vals = list(range(n))
        x_mean = mean(x_vals)
        y_mean = mean(values)

        # Calculate slope
        numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, values))
        denominator = sum((x - x_mean) ** 2 for x in x_vals)

        if denominator == 0:
            return "stable"

        slope = numerator / denominator

        # Determine direction based on slope
        if abs(slope) < 0.01:  # Small threshold for stability
            return "stable"
        elif slope > 0:
            return "increasing"
        else:
            return "decreasing"

    def _calculate_velocity(self, values: List[float], timestamps: List[datetime]) -> float:
        """Calculate average rate of change per day.

        Args:
            values: List of metric values
            timestamps: Corresponding timestamps

        Returns:
            Average change per day
        """
        if len(values) < 2 or len(timestamps) < 2:
            return 0.0

        total_change = values[-1] - values[0]
        time_span = (timestamps[-1] - timestamps[0]).days

        if time_span == 0:
            return 0.0

        return total_change / time_span

    def _calculate_file_complexity_velocity(self, file_path: str, window_days: int) -> float:
        """Calculate average complexity increase per commit for a file.

        Args:
            file_path: Path to file
            window_days: Time window in days

        Returns:
            Average complexity change per commit
        """
        # This is simplified - in production you'd track file complexity over time
        # For MVP, return a placeholder
        return 0.0

    def _calculate_file_coupling_velocity(self, file_path: str, window_days: int) -> float:
        """Calculate average coupling increase per commit for a file.

        Args:
            file_path: Path to file
            window_days: Time window in days

        Returns:
            Average coupling change per commit
        """
        # This is simplified - in production you'd track file coupling over time
        # For MVP, return a placeholder
        return 0.0

    def _get_file_top_authors(self, file_path: str, window_days: int) -> List[str]:
        """Get top authors who modified a file.

        Args:
            file_path: Path to file
            window_days: Time window in days

        Returns:
            List of author names sorted by modification count
        """
        # FalkorDB doesn't support datetime() or duration() - use UNIX timestamps
        if self._is_falkordb:
            cutoff_timestamp = int((datetime.now(timezone.utc) - timedelta(days=window_days)).timestamp())
            query = """
            MATCH (s:Session)-[:MODIFIED]->(f:File {filePath: $file_path})
            WHERE s.committedAt >= $cutoff_timestamp
            WITH s.author as author, count(*) as mod_count
            RETURN author
            ORDER BY mod_count DESC
            LIMIT 5
            """
            params = {"file_path": file_path, "cutoff_timestamp": cutoff_timestamp}
        else:
            query = """
            MATCH (s:Session)-[:MODIFIED]->(f:File {filePath: $file_path})
            WHERE s.committedAt >= datetime() - duration({days: $window_days})
            WITH s.author as author, count(*) as mod_count
            RETURN author
            ORDER BY mod_count DESC
            LIMIT 5
            """
            params = {"file_path": file_path, "window_days": window_days}

        try:
            results = self.client.execute_query(query, params)

            return [r["author"] for r in results]

        except Exception as e:
            logger.debug(f"Could not get top authors for {file_path}: {e}")
            return []
