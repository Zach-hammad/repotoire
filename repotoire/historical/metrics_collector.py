"""Metrics collector for extracting time-series data from CodebaseHealth."""

import logging
from typing import Dict, Any
from repotoire.models import CodebaseHealth, Severity

logger = logging.getLogger(__name__)


class MetricsCollector:
    """Extract metrics from CodebaseHealth for time-series storage.

    Converts rich CodebaseHealth objects into flat metric dictionaries
    suitable for TimescaleDB storage.

    Example:
        >>> health = analyze_codebase()
        >>> collector = MetricsCollector()
        >>> metrics = collector.extract_metrics(health)
        >>> timescale_client.record_metrics(metrics, repository="myrepo")
    """

    def extract_metrics(self, health: CodebaseHealth) -> Dict[str, Any]:
        """Extract metrics from CodebaseHealth object.

        Args:
            health: CodebaseHealth instance from analysis

        Returns:
            Dictionary of metrics ready for TimescaleDB storage

        Example:
            >>> metrics = collector.extract_metrics(health)
            >>> metrics.keys()
            dict_keys(['overall_health', 'structure_health', 'critical_count', ...])
        """
        # Count findings by severity
        critical_count = sum(
            1 for f in health.findings
            if f.severity == Severity.CRITICAL
        )
        high_count = sum(
            1 for f in health.findings
            if f.severity == Severity.HIGH
        )
        medium_count = sum(
            1 for f in health.findings
            if f.severity == Severity.MEDIUM
        )
        low_count = sum(
            1 for f in health.findings
            if f.severity == Severity.LOW
        )

        # Extract metrics breakdown
        m = health.metrics

        return {
            # Overall health scores (0-100)
            "overall_health": health.overall_score,
            "structure_health": health.structure_score,
            "quality_health": health.quality_score,
            "architecture_health": health.architecture_score,

            # Issue counts by severity
            "critical_count": critical_count,
            "high_count": high_count,
            "medium_count": medium_count,
            "low_count": low_count,
            "total_findings": len(health.findings),

            # Codebase statistics
            "total_files": m.total_files,
            "total_classes": m.total_classes,
            "total_functions": m.total_functions,
            "total_loc": getattr(m, "total_loc", 0),  # Optional field

            # Structural metrics
            "modularity": m.modularity,
            "avg_coupling": m.avg_coupling,
            "circular_dependencies": m.circular_dependencies,
            "bottleneck_count": m.bottleneck_count,

            # Quality metrics
            "dead_code_percentage": m.dead_code_percentage,
            "duplication_percentage": m.duplication_percentage,
            "god_class_count": m.god_class_count,

            # Architecture metrics
            "layer_violations": m.layer_violations,
            "boundary_violations": m.boundary_violations,
            "abstraction_ratio": m.abstraction_ratio,
        }

    def extract_metadata(self, **kwargs) -> Dict[str, Any]:
        """Extract additional metadata for JSON storage.

        Args:
            **kwargs: Arbitrary key-value pairs for metadata

        Returns:
            Dictionary of metadata

        Example:
            >>> metadata = collector.extract_metadata(
            ...     team="platform",
            ...     version="1.2.3",
            ...     ci_build_id="build-456"
            ... )
        """
        return {k: v for k, v in kwargs.items() if v is not None}
