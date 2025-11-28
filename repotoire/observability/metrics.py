"""Prometheus metrics for Repotoire (REPO-224).

Provides production-grade metrics for:
- Detector execution (duration, findings count by severity)
- Graph queries (count, latency)
- Ingestion pipeline (files processed, duration)
- Entity counts and embedding coverage

All metrics gracefully degrade when prometheus_client is not installed.

Usage:
    from repotoire.observability import get_metrics, DETECTOR_DURATION

    # Record detector execution time
    with DETECTOR_DURATION.labels(detector="GodClassDetector").time():
        findings = detector.detect()

    # Increment counters
    FINDINGS_TOTAL.labels(detector="GodClassDetector", severity="high").inc()

Install with: pip install repotoire[observability]
"""

import functools
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Generator, Optional

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Check for prometheus_client availability
try:
    from prometheus_client import (
        Counter,
        Histogram,
        Gauge,
        CollectorRegistry,
        start_http_server,
        REGISTRY,
        generate_latest,
    )
    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False
    Counter = None  # type: ignore
    Histogram = None  # type: ignore
    Gauge = None  # type: ignore
    CollectorRegistry = None  # type: ignore
    REGISTRY = None  # type: ignore


class NoOpMetric:
    """No-op metric that does nothing when prometheus is not available."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def labels(self, *args: Any, **kwargs: Any) -> "NoOpMetric":
        return self

    def inc(self, amount: float = 1) -> None:
        pass

    def dec(self, amount: float = 1) -> None:
        pass

    def set(self, value: float) -> None:
        pass

    def observe(self, value: float) -> None:
        pass

    @contextmanager
    def time(self) -> Generator[None, None, None]:
        yield


# ============================================================================
# COUNTERS - Track cumulative values
# ============================================================================

if HAS_PROMETHEUS:
    FINDINGS_TOTAL = Counter(
        "repotoire_findings_total",
        "Total findings detected",
        ["detector", "severity"],
    )

    QUERIES_TOTAL = Counter(
        "repotoire_queries_total",
        "Total graph queries executed",
        ["query_type"],
    )

    EMBEDDINGS_GENERATED = Counter(
        "repotoire_embeddings_generated_total",
        "Total embeddings generated",
        ["entity_type", "backend"],
    )

    FILES_PROCESSED = Counter(
        "repotoire_files_processed_total",
        "Total files processed during ingestion",
        ["status"],  # success, error, skipped
    )
else:
    FINDINGS_TOTAL = NoOpMetric()
    QUERIES_TOTAL = NoOpMetric()
    EMBEDDINGS_GENERATED = NoOpMetric()
    FILES_PROCESSED = NoOpMetric()

# ============================================================================
# HISTOGRAMS - Track distributions (with buckets)
# ============================================================================

if HAS_PROMETHEUS:
    DETECTOR_DURATION = Histogram(
        "repotoire_detector_duration_seconds",
        "Detector execution time in seconds",
        ["detector"],
        buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0],
    )

    QUERY_DURATION = Histogram(
        "repotoire_query_duration_seconds",
        "Graph query latency in seconds",
        ["query_type"],
        buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
    )

    INGESTION_DURATION = Histogram(
        "repotoire_ingestion_duration_seconds",
        "Ingestion time per file in seconds",
        buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0],
    )

    RAG_QUERY_DURATION = Histogram(
        "repotoire_rag_query_duration_seconds",
        "RAG query latency including embedding, search, and reranking",
        ["stage"],  # embedding, vector_search, reranking, graph_enrichment, total
        buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
    )
else:
    DETECTOR_DURATION = NoOpMetric()
    QUERY_DURATION = NoOpMetric()
    INGESTION_DURATION = NoOpMetric()
    RAG_QUERY_DURATION = NoOpMetric()

# ============================================================================
# GAUGES - Track current values
# ============================================================================

if HAS_PROMETHEUS:
    ENTITIES_TOTAL = Gauge(
        "repotoire_entities_total",
        "Total entities in the knowledge graph",
        ["entity_type"],
    )

    EMBEDDINGS_COVERAGE = Gauge(
        "repotoire_embeddings_coverage_ratio",
        "Ratio of entities with embeddings (0.0 to 1.0)",
        ["entity_type"],
    )

    ANALYSIS_HEALTH_SCORE = Gauge(
        "repotoire_analysis_health_score",
        "Overall health score from last analysis (0-100)",
        ["category"],  # overall, structure, quality, architecture
    )

    ACTIVE_DETECTORS = Gauge(
        "repotoire_active_detectors",
        "Number of detectors currently running",
    )
else:
    ENTITIES_TOTAL = NoOpMetric()
    EMBEDDINGS_COVERAGE = NoOpMetric()
    ANALYSIS_HEALTH_SCORE = NoOpMetric()
    ACTIVE_DETECTORS = NoOpMetric()


class MetricsManager:
    """Manager for Prometheus metrics server and utilities.

    Example:
        metrics = MetricsManager()
        metrics.start_server(port=9090)

        # Record metrics
        metrics.record_finding("GodClassDetector", "high")
        with metrics.time_detector("MypyDetector"):
            detector.detect()
    """

    def __init__(self, enabled: bool = True) -> None:
        """Initialize metrics manager.

        Args:
            enabled: Whether metrics collection is enabled
        """
        self.enabled = enabled and HAS_PROMETHEUS
        self._server_started = False

        if enabled and not HAS_PROMETHEUS:
            logger.info(
                "Prometheus metrics disabled (prometheus_client not installed). "
                "Install with: pip install repotoire[observability]"
            )

    def start_server(self, port: int = 9090, addr: str = "") -> bool:
        """Start the Prometheus metrics HTTP server.

        Args:
            port: Port to expose metrics on (default: 9090)
            addr: Address to bind to (default: all interfaces)

        Returns:
            True if server started successfully
        """
        if not self.enabled:
            logger.warning("Metrics server not started (metrics disabled)")
            return False

        if self._server_started:
            logger.warning(f"Metrics server already running on port {port}")
            return True

        try:
            start_http_server(port, addr)
            self._server_started = True
            logger.info(f"Prometheus metrics server started on :{port}/metrics")
            return True
        except Exception as e:
            logger.error(f"Failed to start metrics server: {e}")
            return False

    def get_metrics_text(self) -> str:
        """Get metrics in Prometheus text format.

        Returns:
            Metrics text suitable for scraping
        """
        if not self.enabled:
            return ""
        return generate_latest(REGISTRY).decode("utf-8")

    def record_finding(self, detector: str, severity: str, count: int = 1) -> None:
        """Record a detected finding.

        Args:
            detector: Name of the detector
            severity: Severity level (critical, high, medium, low, info)
            count: Number of findings to record
        """
        if self.enabled:
            FINDINGS_TOTAL.labels(detector=detector, severity=severity).inc(count)

    def record_query(self, query_type: str, duration: float) -> None:
        """Record a graph query execution.

        Args:
            query_type: Type of query (e.g., "vector_search", "graph_traversal")
            duration: Query duration in seconds
        """
        if self.enabled:
            QUERIES_TOTAL.labels(query_type=query_type).inc()
            QUERY_DURATION.labels(query_type=query_type).observe(duration)

    def record_embedding(self, entity_type: str, backend: str, count: int = 1) -> None:
        """Record embedding generation.

        Args:
            entity_type: Type of entity (Function, Class, File)
            backend: Embedding backend (openai, local)
            count: Number of embeddings generated
        """
        if self.enabled:
            EMBEDDINGS_GENERATED.labels(
                entity_type=entity_type, backend=backend
            ).inc(count)

    def set_entity_count(self, entity_type: str, count: int) -> None:
        """Set the current count of entities.

        Args:
            entity_type: Type of entity (Function, Class, File, etc.)
            count: Current count
        """
        if self.enabled:
            ENTITIES_TOTAL.labels(entity_type=entity_type).set(count)

    def set_embedding_coverage(self, entity_type: str, coverage: float) -> None:
        """Set the embedding coverage ratio.

        Args:
            entity_type: Type of entity
            coverage: Coverage ratio (0.0 to 1.0)
        """
        if self.enabled:
            EMBEDDINGS_COVERAGE.labels(entity_type=entity_type).set(coverage)

    def set_health_score(
        self,
        overall: float,
        structure: float,
        quality: float,
        architecture: float,
    ) -> None:
        """Set health scores from analysis.

        Args:
            overall: Overall health score (0-100)
            structure: Structure score (0-100)
            quality: Quality score (0-100)
            architecture: Architecture score (0-100)
        """
        if self.enabled:
            ANALYSIS_HEALTH_SCORE.labels(category="overall").set(overall)
            ANALYSIS_HEALTH_SCORE.labels(category="structure").set(structure)
            ANALYSIS_HEALTH_SCORE.labels(category="quality").set(quality)
            ANALYSIS_HEALTH_SCORE.labels(category="architecture").set(architecture)

    @contextmanager
    def time_detector(self, detector_name: str) -> Generator[None, None, None]:
        """Context manager to time detector execution.

        Args:
            detector_name: Name of the detector

        Yields:
            Context for timing
        """
        if not self.enabled:
            yield
            return

        with DETECTOR_DURATION.labels(detector=detector_name).time():
            yield

    @contextmanager
    def time_ingestion(self) -> Generator[None, None, None]:
        """Context manager to time file ingestion.

        Yields:
            Context for timing
        """
        if not self.enabled:
            yield
            return

        with INGESTION_DURATION.time():
            yield


# Singleton metrics manager
_metrics_manager: Optional[MetricsManager] = None


def get_metrics(enabled: bool = True) -> MetricsManager:
    """Get or create the global metrics manager.

    Args:
        enabled: Whether to enable metrics collection

    Returns:
        MetricsManager instance
    """
    global _metrics_manager
    if _metrics_manager is None:
        _metrics_manager = MetricsManager(enabled=enabled)
    return _metrics_manager


def timed_metric(metric_name: str, labels: Optional[Dict[str, str]] = None) -> Callable:
    """Decorator to time function execution with a histogram metric.

    Args:
        metric_name: Name of the histogram to use
        labels: Optional labels to apply

    Returns:
        Decorated function
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not HAS_PROMETHEUS:
                return func(*args, **kwargs)

            start = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                duration = time.perf_counter() - start
                # Get the appropriate histogram
                if metric_name == "detector":
                    detector_name = labels.get("detector", func.__name__) if labels else func.__name__
                    DETECTOR_DURATION.labels(detector=detector_name).observe(duration)
                elif metric_name == "query":
                    query_type = labels.get("query_type", "unknown") if labels else "unknown"
                    QUERY_DURATION.labels(query_type=query_type).observe(duration)

        return wrapper
    return decorator