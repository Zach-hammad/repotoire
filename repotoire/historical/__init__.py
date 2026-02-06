"""Historical tracking for code evolution and metrics.

This module provides time-series metrics tracking and git history RAG
for comprehensive historical analysis.

Components:
- TimescaleClient: Database operations for metrics storage
- MetricsCollector: Extract metrics from CodebaseHealth
- GitHistoryRAG: Natural language queries over git commits (replaces Graphiti)
- git_extractor: Client-side git commit extraction for cloud architecture
"""

from repotoire.historical.git_extractor import extract_commits, is_git_repository
from repotoire.historical.metrics_collector import MetricsCollector
from repotoire.historical.timescale_client import TimescaleClient

__all__ = [
    "TimescaleClient",
    "MetricsCollector",
    "is_git_repository",
    "extract_commits",
]
