"""Re-ranking support for improved retrieval precision.

Provides cross-encoder reranking to improve result quality after initial
embedding-based retrieval. Cross-encoders jointly encode query and document,
providing more accurate relevance scores than bi-encoder similarity.

Backends:
- Voyage AI: High-quality API-based reranking (rerank-2)
- Local: Free local reranking via sentence-transformers CrossEncoder

REPO-241: Re-ranking Support
"""

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Literal

from repotoire.logging_config import get_logger

logger = get_logger(__name__)


RerankerBackend = Literal["voyage", "local", "none"]

# Backend configurations
RERANKER_CONFIGS = {
    "voyage": {
        "model": "rerank-2",
        "env_key": "VOYAGE_API_KEY",
        "models": {
            "rerank-2": {"description": "Voyage rerank-2 (best quality)"},
            "rerank-2-lite": {"description": "Voyage rerank-2-lite (faster)"},
        },
    },
    "local": {
        "model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
        "models": {
            "cross-encoder/ms-marco-MiniLM-L-6-v2": {"description": "Fast MiniLM cross-encoder"},
            "cross-encoder/ms-marco-TinyBERT-L-2-v2": {"description": "Very fast TinyBERT"},
            "Qwen/Qwen3-Reranker-0.6B": {"description": "High quality Qwen3 reranker"},
        },
    },
}


@dataclass
class RerankerConfig:
    """Configuration for reranking.

    Attributes:
        enabled: Whether reranking is enabled (default: False, adds latency)
        backend: Reranker backend ("voyage", "local", or "none")
        model: Model name (uses backend default if not specified)
        top_k: Final number of results after reranking
        retrieve_multiplier: Retrieve top_k * multiplier candidates before reranking
    """

    enabled: bool = False  # Disabled by default (adds latency ~100-300ms)
    backend: RerankerBackend = "voyage"
    model: Optional[str] = None
    top_k: int = 10
    retrieve_multiplier: int = 3  # Retrieve 3x candidates for reranking

    def get_model(self) -> str:
        """Get the effective model name (user-specified or backend default)."""
        if self.model:
            return self.model
        if self.backend == "none":
            return ""
        return RERANKER_CONFIGS[self.backend]["model"]


class Reranker(ABC):
    """Abstract base class for rerankers."""

    @abstractmethod
    def rerank(
        self,
        query: str,
        documents: List[Dict],
        top_k: int = 10,
    ) -> List[Dict]:
        """Rerank documents by relevance to query.

        Args:
            query: The search query
            documents: List of document dicts with node data
            top_k: Number of top results to return

        Returns:
            Reranked documents with updated scores
        """
        pass

    def _extract_text(self, doc: Dict) -> str:
        """Extract searchable text from document node.

        Args:
            doc: Document dict with node data

        Returns:
            Concatenated text representation for reranking
        """
        node = doc.get("node", doc)
        parts = []

        if node.get("name"):
            parts.append(f"Name: {node['name']}")
        if node.get("docstring"):
            parts.append(f"Docstring: {node['docstring']}")
        if node.get("source_code"):
            # Limit source code to avoid context length issues
            parts.append(f"Code: {node['source_code'][:500]}")

        return "\n".join(parts) if parts else str(node.get("qualified_name", ""))


class VoyageReranker(Reranker):
    """Voyage AI reranker using rerank-2 model.

    Voyage AI provides high-quality reranking optimized for code and text.
    Requires VOYAGE_API_KEY environment variable.

    Example:
        >>> reranker = VoyageReranker()
        >>> results = reranker.rerank("JWT auth", documents, top_k=5)
    """

    def __init__(self, model: str = "rerank-2"):
        """Initialize Voyage reranker.

        Args:
            model: Voyage model name (default: "rerank-2")

        Raises:
            ValueError: If VOYAGE_API_KEY is not set
        """
        config = RERANKER_CONFIGS["voyage"]
        env_key = config["env_key"]

        api_key = os.getenv(env_key)
        if not api_key:
            raise ValueError(
                f"{env_key} environment variable required for Voyage reranker. "
                f"Get your API key at https://dash.voyageai.com"
            )

        try:
            import voyageai
        except ImportError:
            raise ImportError(
                "voyageai package required for Voyage reranker. "
                "Install with: pip install repotoire[voyage]"
            )

        self.client = voyageai.Client(api_key=api_key)
        self.model = model
        logger.info(f"Initialized VoyageReranker with model: {model}")

    def rerank(
        self,
        query: str,
        documents: List[Dict],
        top_k: int = 10,
    ) -> List[Dict]:
        """Rerank documents using Voyage AI.

        Args:
            query: The search query
            documents: List of document dicts
            top_k: Number of top results to return

        Returns:
            Reranked documents with rerank_score field
        """
        if not documents:
            return documents

        start_time = time.time()

        # Extract text content from documents
        doc_texts = [self._extract_text(doc) for doc in documents]

        result = self.client.rerank(
            query=query,
            documents=doc_texts,
            model=self.model,
            top_k=min(top_k, len(documents)),
        )

        # Map back to original documents with new scores
        reranked = []
        for r in result.results:
            doc = documents[r.index].copy()
            # Preserve original score in metadata
            doc["metadata"] = doc.get("metadata", {})
            doc["metadata"]["original_score"] = doc.get("score", 0)
            doc["metadata"]["rerank_score"] = r.relevance_score
            doc["score"] = r.relevance_score
            reranked.append(doc)

        duration = time.time() - start_time
        logger.debug(
            f"Voyage reranking: {len(documents)} docs -> {len(reranked)} results "
            f"in {duration:.3f}s"
        )

        return reranked


class LocalReranker(Reranker):
    """Local reranker using sentence-transformers CrossEncoder.

    Free, no API key required. Uses cross-encoder models that jointly
    encode query and document for accurate relevance scoring.

    Example:
        >>> reranker = LocalReranker()
        >>> results = reranker.rerank("find auth functions", documents, top_k=5)
    """

    def __init__(self, model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        """Initialize local cross-encoder reranker.

        Args:
            model: Model name (default: ms-marco-MiniLM-L-6-v2)

        Raises:
            ImportError: If sentence-transformers is not installed
        """
        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            raise ImportError(
                "sentence-transformers required for local reranker. "
                "Install with: pip install repotoire[local-embeddings]"
            )

        self.model_name = model
        logger.info(f"Loading cross-encoder model: {model}")
        self._model = CrossEncoder(model)
        logger.info(f"Initialized LocalReranker with model: {model}")

    def rerank(
        self,
        query: str,
        documents: List[Dict],
        top_k: int = 10,
    ) -> List[Dict]:
        """Rerank documents using local cross-encoder.

        Args:
            query: The search query
            documents: List of document dicts
            top_k: Number of top results to return

        Returns:
            Reranked documents with rerank_score field
        """
        if not documents:
            return documents

        start_time = time.time()

        # Extract text content and create query-document pairs
        doc_texts = [self._extract_text(doc) for doc in documents]
        pairs = [[query, doc_text] for doc_text in doc_texts]

        # Get cross-encoder scores
        scores = self._model.predict(pairs)

        # Sort by score and return top_k
        scored_docs = list(zip(documents, scores))
        scored_docs.sort(key=lambda x: x[1], reverse=True)

        reranked = []
        for doc, score in scored_docs[:top_k]:
            doc_copy = doc.copy()
            # Preserve original score in metadata
            doc_copy["metadata"] = doc_copy.get("metadata", {})
            doc_copy["metadata"]["original_score"] = doc_copy.get("score", 0)
            doc_copy["metadata"]["rerank_score"] = float(score)
            doc_copy["score"] = float(score)
            reranked.append(doc_copy)

        duration = time.time() - start_time
        logger.debug(
            f"Local cross-encoder reranking: {len(documents)} docs -> {len(reranked)} results "
            f"in {duration:.3f}s"
        )

        return reranked


def create_reranker(config: RerankerConfig) -> Optional[Reranker]:
    """Factory function to create a reranker from configuration.

    Args:
        config: Reranker configuration

    Returns:
        Configured Reranker instance, or None if disabled

    Raises:
        ValueError: If unknown backend is specified
    """
    if not config.enabled or config.backend == "none":
        return None

    if config.backend == "voyage":
        model = config.get_model()
        return VoyageReranker(model=model)
    elif config.backend == "local":
        model = config.get_model()
        return LocalReranker(model=model)
    else:
        raise ValueError(f"Unknown reranker backend: {config.backend}")
