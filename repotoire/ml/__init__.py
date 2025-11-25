"""Machine learning module for Repotoire.

This module provides ML capabilities including:
- Graph embeddings (FastRP, Node2Vec)
- Structural similarity search
- Bug prediction models
"""

from repotoire.ml.graph_embeddings import FastRPEmbedder, FastRPConfig
from repotoire.ml.similarity import StructuralSimilarityAnalyzer, SimilarityResult

__all__ = [
    "FastRPEmbedder",
    "FastRPConfig",
    "StructuralSimilarityAnalyzer",
    "SimilarityResult",
]
