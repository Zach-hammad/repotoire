"""Historical tracking for code evolution and metrics.

This module provides both time-series metrics tracking and temporal knowledge graph
integration for comprehensive historical analysis.

Components:
- TimescaleClient: Database operations for metrics storage
- MetricsCollector: Extract metrics from CodebaseHealth
- GitGraphitiIntegration: Git history integration with Graphiti temporal knowledge graph (server-side)
- git_extractor: Client-side git commit extraction for cloud architecture
"""

from repotoire.historical.timescale_client import TimescaleClient
from repotoire.historical.metrics_collector import MetricsCollector
from repotoire.historical.git_graphiti import GitGraphitiIntegration
from repotoire.historical.git_extractor import is_git_repository, extract_commits

__all__ = [
    "TimescaleClient",
    "MetricsCollector",
    "GitGraphitiIntegration",
    "is_git_repository",
    "extract_commits",
]
