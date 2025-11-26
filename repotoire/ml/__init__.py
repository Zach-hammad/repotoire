"""Machine learning module for Repotoire.

This module provides ML capabilities including:
- Graph embeddings (FastRP, Node2Vec)
- Structural similarity search
- Bug prediction models
- Fast Rust-based similarity functions
"""

from repotoire.ml.graph_embeddings import FastRPEmbedder, FastRPConfig, cosine_similarity
from repotoire.ml.similarity import StructuralSimilarityAnalyzer, SimilarityResult


def batch_cosine_similarity(query, matrix):
    """Calculate cosine similarity between query and all rows in matrix.

    Uses Rust parallel implementation for ~2.5x speedup over NumPy.

    Args:
        query: 1D numpy array (e.g., embedding vector)
        matrix: 2D numpy array (e.g., matrix of embeddings)

    Returns:
        List of similarity scores
    """
    try:
        from repotoire_fast import batch_cosine_similarity_fast
        import numpy as np
        q = np.asarray(query, dtype=np.float32)
        m = np.asarray(matrix, dtype=np.float32)
        return batch_cosine_similarity_fast(q, m)
    except ImportError:
        import numpy as np
        q = np.asarray(query)
        m = np.asarray(matrix)
        norms = np.linalg.norm(m, axis=1) * np.linalg.norm(q)
        return list(np.dot(m, q) / norms)


def find_top_k_similar(query, matrix, k):
    """Find top k most similar vectors in matrix.

    Uses Rust parallel implementation for ~5.8x speedup over NumPy.

    Args:
        query: 1D numpy array (e.g., embedding vector)
        matrix: 2D numpy array (e.g., matrix of embeddings)
        k: Number of top results to return

    Returns:
        List of (index, score) tuples sorted by similarity descending
    """
    try:
        from repotoire_fast import find_top_k_similar as rust_find_top_k
        import numpy as np
        q = np.asarray(query, dtype=np.float32)
        m = np.asarray(matrix, dtype=np.float32)
        return rust_find_top_k(q, m, k)
    except ImportError:
        import numpy as np
        q = np.asarray(query)
        m = np.asarray(matrix)
        norms = np.linalg.norm(m, axis=1) * np.linalg.norm(q)
        scores = np.dot(m, q) / norms
        top_indices = np.argsort(scores)[-k:][::-1]
        return [(int(i), float(scores[i])) for i in top_indices]


__all__ = [
    "FastRPEmbedder",
    "FastRPConfig",
    "StructuralSimilarityAnalyzer",
    "SimilarityResult",
    "cosine_similarity",
    "batch_cosine_similarity",
    "find_top_k_similar",
]
