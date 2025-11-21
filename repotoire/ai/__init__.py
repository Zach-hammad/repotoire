"""AI and NLP modules for semantic code analysis."""

from repotoire.ai.spacy_clue_generator import SpacyClueGenerator
from repotoire.ai.embeddings import CodeEmbedder, EmbeddingConfig, create_embedder
from repotoire.ai.retrieval import GraphRAGRetriever, RetrievalResult, create_retriever

__all__ = [
    "SpacyClueGenerator",
    "CodeEmbedder",
    "EmbeddingConfig",
    "create_embedder",
    "GraphRAGRetriever",
    "RetrievalResult",
    "create_retriever",
]
