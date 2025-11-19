"""
Falkor - Graph-Powered Code Health Platform

Analyzes codebases using knowledge graphs to detect code smells,
architectural issues, and technical debt.
"""

__version__ = "0.1.0"

from falkor.pipeline import IngestionPipeline
from falkor.graph import Neo4jClient
from falkor.detectors import AnalysisEngine
from falkor.models import CodebaseHealth, Finding

__all__ = [
    "IngestionPipeline",
    "Neo4jClient",
    "AnalysisEngine",
    "CodebaseHealth",
    "Finding",
]
