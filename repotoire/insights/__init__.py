"""Insights engine for enriching analysis with ML and advanced graph metrics.

This module provides post-analysis enrichment that adds:
- Bug probability scoring (from ML models)
- Impact radius calculation (graph traversal)
- Bottleneck detection (high fan-in nodes)
- Coupling metrics (cross-module dependencies)
"""

from repotoire.insights.engine import CodebaseInsights, InsightsConfig, InsightsEngine

__all__ = ["InsightsEngine", "InsightsConfig", "CodebaseInsights"]
