"""
Repotoire - Graph-Powered Code Health Platform

Analyzes codebases using knowledge graphs to detect code smells,
architectural issues, and technical debt.
"""

__version__ = "0.1.35"


def __getattr__(name: str):
    """Lazy imports for public API - avoids loading heavy modules at import time."""
    if name == "IngestionPipeline":
        from repotoire.pipeline import IngestionPipeline
        return IngestionPipeline
    if name == "AnalysisEngine":
        from repotoire.detectors import AnalysisEngine
        return AnalysisEngine
    if name == "CodebaseHealth":
        from repotoire.models import CodebaseHealth
        return CodebaseHealth
    if name == "Finding":
        from repotoire.models import Finding
        return Finding
    if name == "FalkorDBClient":
        from repotoire.graph import FalkorDBClient
        return FalkorDBClient
    if name == "DatabaseClient":
        from repotoire.graph import DatabaseClient
        return DatabaseClient
    raise AttributeError(f"module 'repotoire' has no attribute {name!r}")


__all__ = [
    "__version__",
    "IngestionPipeline",
    "AnalysisEngine",
    "CodebaseHealth",
    "Finding",
    "FalkorDBClient",
    "DatabaseClient",
]
