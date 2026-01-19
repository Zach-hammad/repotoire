"""AI and NLP modules for semantic code analysis."""

from repotoire.ai.spacy_clue_generator import SpacyClueGenerator
from repotoire.ai.embeddings import (
    CodeEmbedder,
    EmbeddingConfig,
    EmbeddingBackend,
    create_embedder,
    # Int8 quantization for memory-efficient storage
    quantize_embedding,
    dequantize_embedding,
    quantize_embeddings_batch,
    compute_cosine_similarity_quantized,
)
from repotoire.ai.retrieval import (
    GraphRAGRetriever,
    RetrievalResult,
    RetrieverConfig,
    create_retriever,
)
from repotoire.ai.llm import (
    LLMClient,
    LLMConfig,
    LLMBackend,
    create_llm_client,
)
from repotoire.ai.hybrid import (
    HybridSearchConfig,
    FusionMethod,
    reciprocal_rank_fusion,
    linear_fusion,
    fuse_results,
    escape_lucene_query,
)
from repotoire.ai.reranker import (
    Reranker,
    RerankerConfig,
    RerankerBackend,
    VoyageReranker,
    LocalReranker,
    create_reranker,
)
from repotoire.ai.contextual import (
    ContextGenerator,
    ContextualRetrievalConfig,
    CostTracker,
    CostLimitExceeded,
    ContextGenerationResult,
    create_context_generator,
)
from repotoire.ai.compression import (
    EmbeddingCompressor,
    TenantCompressor,
    create_compressor,
    estimate_memory_savings,
    DEFAULT_TARGET_DIMS,
)

__all__ = [
    # NLP
    "SpacyClueGenerator",
    # Embeddings
    "CodeEmbedder",
    "EmbeddingConfig",
    "EmbeddingBackend",
    "create_embedder",
    # Int8 quantization (4x memory reduction)
    "quantize_embedding",
    "dequantize_embedding",
    "quantize_embeddings_batch",
    "compute_cosine_similarity_quantized",
    # Retrieval
    "GraphRAGRetriever",
    "RetrievalResult",
    "RetrieverConfig",
    "create_retriever",
    # LLM
    "LLMClient",
    "LLMConfig",
    "LLMBackend",
    "create_llm_client",
    # Hybrid Search (REPO-243)
    "HybridSearchConfig",
    "FusionMethod",
    "reciprocal_rank_fusion",
    "linear_fusion",
    "fuse_results",
    "escape_lucene_query",
    # Reranking (REPO-241)
    "Reranker",
    "RerankerConfig",
    "RerankerBackend",
    "VoyageReranker",
    "LocalReranker",
    "create_reranker",
    # Contextual Retrieval (REPO-242)
    "ContextGenerator",
    "ContextualRetrievalConfig",
    "CostTracker",
    "CostLimitExceeded",
    "ContextGenerationResult",
    "create_context_generator",
    # Compression (memory optimization)
    "EmbeddingCompressor",
    "TenantCompressor",
    "create_compressor",
    "estimate_memory_savings",
    "DEFAULT_TARGET_DIMS",
]
