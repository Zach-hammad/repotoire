"""Observability package for Repotoire (REPO-224).

Provides production-grade monitoring through:
- Prometheus metrics (counters, histograms, gauges)
- OpenTelemetry distributed tracing
- Graceful degradation when dependencies unavailable

Install with: pip install repotoire[observability]
"""

from repotoire.observability.metrics import (
    DETECTOR_DURATION,
    EMBEDDINGS_COVERAGE,
    EMBEDDINGS_GENERATED,
    ENTITIES_TOTAL,
    FINDINGS_TOTAL,
    HAS_PROMETHEUS,
    INGESTION_DURATION,
    QUERIES_TOTAL,
    QUERY_DURATION,
    MetricsManager,
    get_metrics,
)
from repotoire.observability.tracing import (
    HAS_OPENTELEMETRY,
    TracingManager,
    get_tracer,
    init_tracing,
    traced,
)

__all__ = [
    # Metrics
    "MetricsManager",
    "get_metrics",
    "FINDINGS_TOTAL",
    "QUERIES_TOTAL",
    "EMBEDDINGS_GENERATED",
    "DETECTOR_DURATION",
    "QUERY_DURATION",
    "INGESTION_DURATION",
    "ENTITIES_TOTAL",
    "EMBEDDINGS_COVERAGE",
    "HAS_PROMETHEUS",
    # Tracing
    "TracingManager",
    "get_tracer",
    "traced",
    "init_tracing",
    "HAS_OPENTELEMETRY",
]
