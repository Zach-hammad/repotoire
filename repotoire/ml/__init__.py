"""Machine learning module for Repotoire.

This module provides ML capabilities including:
- Graph embeddings (FastRP, Node2Vec, GraphSAGE)
- Structural similarity search
- Bug prediction models
- Cross-project zero-shot defect prediction (GraphSAGE)
- Training data extraction from git history
- Active learning for human-in-the-loop refinement
- Fast Rust-based similarity functions
- Multimodal fusion (text + graph embeddings)
"""

from repotoire.ml.graph_embeddings import FastRPEmbedder, FastRPConfig, cosine_similarity
from repotoire.ml.similarity import StructuralSimilarityAnalyzer, SimilarityResult
from repotoire.ml.training_data import (
    GitBugLabelExtractor,
    ActiveLearningLabeler,
    TrainingExample,
    TrainingDataset,
    FunctionInfo,
    DEFAULT_BUG_KEYWORDS,
)
from repotoire.ml.node2vec_embeddings import Node2VecEmbedder, Node2VecConfig
from repotoire.ml.bug_predictor import (
    BugPredictor,
    BugPredictorConfig,
    FeatureExtractor,
    PredictionResult,
    ModelMetrics,
)

# Multimodal fusion (requires torch)
try:
    from repotoire.ml.multimodal_fusion import (
        MultimodalAttentionFusion,
        FusionConfig,
        CrossModalAttention,
        GatedFusion,
        MultiTaskLoss,
    )
    from repotoire.ml.multimodal_analyzer import (
        MultimodalAnalyzer,
        MultimodalDataset,
        TrainingConfig,
        PredictionExplanation,
    )
    _MULTIMODAL_AVAILABLE = True
except ImportError:
    _MULTIMODAL_AVAILABLE = False


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
    # Graph embeddings
    "FastRPEmbedder",
    "FastRPConfig",
    "StructuralSimilarityAnalyzer",
    "SimilarityResult",
    "cosine_similarity",
    "batch_cosine_similarity",
    "find_top_k_similar",
    # Training data extraction
    "GitBugLabelExtractor",
    "ActiveLearningLabeler",
    "TrainingExample",
    "TrainingDataset",
    "FunctionInfo",
    "DEFAULT_BUG_KEYWORDS",
    # Node2Vec embeddings
    "Node2VecEmbedder",
    "Node2VecConfig",
    # Bug prediction
    "BugPredictor",
    "BugPredictorConfig",
    "FeatureExtractor",
    "PredictionResult",
    "ModelMetrics",
]

# Add multimodal exports if available
if _MULTIMODAL_AVAILABLE:
    __all__.extend([
        # Multimodal fusion
        "MultimodalAttentionFusion",
        "FusionConfig",
        "CrossModalAttention",
        "GatedFusion",
        "MultiTaskLoss",
        "MultimodalAnalyzer",
        "MultimodalDataset",
        "TrainingConfig",
        "PredictionExplanation",
    ])

# GraphSAGE zero-shot defect prediction (requires torch + torch-geometric)
try:
    from repotoire.ml.graphsage_predictor import (
        GraphSAGEDefectPredictor,
        GraphSAGEWithAttention,
        GraphSAGEConfig,
        GraphFeatureExtractor,
    )
    from repotoire.ml.cross_project_trainer import (
        CrossProjectTrainer,
        CrossProjectDataLoader,
        CrossProjectTrainingConfig,
        ProjectGraphData,
        TrainingHistory,
    )
    _GRAPHSAGE_AVAILABLE = True
    __all__.extend([
        # GraphSAGE zero-shot prediction
        "GraphSAGEDefectPredictor",
        "GraphSAGEWithAttention",
        "GraphSAGEConfig",
        "GraphFeatureExtractor",
        "CrossProjectTrainer",
        "CrossProjectDataLoader",
        "CrossProjectTrainingConfig",
        "ProjectGraphData",
        "TrainingHistory",
    ])
except ImportError:
    _GRAPHSAGE_AVAILABLE = False
