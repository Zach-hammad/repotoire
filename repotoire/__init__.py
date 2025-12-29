"""
Repotoire - Graph-Powered Code Health Platform

Analyzes codebases using knowledge graphs to detect code smells,
architectural issues, and technical debt.
"""

__version__ = "0.1.1"

from repotoire.pipeline import IngestionPipeline
from repotoire.graph import Neo4jClient
from repotoire.detectors import AnalysisEngine
from repotoire.models import CodebaseHealth, Finding

__all__ = [
    "IngestionPipeline",
    "Neo4jClient",
    "AnalysisEngine",
    "CodebaseHealth",
    "Finding",
]
