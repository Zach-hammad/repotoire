"""Historical metrics tracking using TimescaleDB.

This module provides time-series storage for code health metrics, enabling:
- Trend analysis over time
- Regression detection
- Team accountability tracking
- Historical reporting for leadership

Components:
- TimescaleClient: Database operations for metrics storage
- MetricsCollector: Extract metrics from CodebaseHealth
"""

from repotoire.historical.timescale_client import TimescaleClient
from repotoire.historical.metrics_collector import MetricsCollector

__all__ = ["TimescaleClient", "MetricsCollector"]
