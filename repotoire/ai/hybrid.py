"""Hybrid search combining dense embeddings with BM25 sparse keyword search.

Implements:
- Reciprocal Rank Fusion (RRF) for combining result lists
- Linear interpolation fusion with configurable weights
- Configuration for hybrid search pipeline

REPO-243: Hybrid Search (Dense + BM25)
"""

from dataclasses import dataclass, field
from typing import List, Dict, Literal

from repotoire.logging_config import get_logger

logger = get_logger(__name__)


FusionMethod = Literal["rrf", "linear"]


@dataclass
class HybridSearchConfig:
    """Configuration for hybrid dense + BM25 search.

    Attributes:
        enabled: Whether hybrid search is enabled (default: True)
        alpha: Weight for dense search in linear fusion (1-alpha for BM25)
        dense_top_k: Number of initial dense embedding results
        bm25_top_k: Number of initial BM25 keyword results
        fusion_method: How to combine results ("rrf" or "linear")
        rrf_k: RRF ranking constant (higher = flatter score distribution)
    """

    enabled: bool = True
    alpha: float = 0.7  # 70% dense, 30% BM25
    dense_top_k: int = 100
    bm25_top_k: int = 100
    fusion_method: FusionMethod = "rrf"
    rrf_k: int = 60  # Standard RRF constant


def reciprocal_rank_fusion(
    dense_results: List[Dict],
    bm25_results: List[Dict],
    k: int = 60,
    id_field: str = "qualified_name",
) -> List[Dict]:
    """Combine results using Reciprocal Rank Fusion (RRF).

    RRF is a simple but effective method for combining ranked lists.
    The score for each document is: sum(1 / (k + rank_i)) across all lists.

    Benefits:
    - No score normalization needed (uses only ranks)
    - Robust to different score scales between dense and sparse search
    - Documents appearing in both lists get boosted

    Args:
        dense_results: Results from dense embedding search
        bm25_results: Results from BM25 keyword search
        k: Ranking constant (default: 60, higher = flatter distribution)
        id_field: Field to use as unique document ID

    Returns:
        Fused results sorted by RRF score (descending)

    Example:
        >>> dense = [{"qualified_name": "a", "score": 0.9}, {"qualified_name": "b", "score": 0.8}]
        >>> bm25 = [{"qualified_name": "b", "score": 5.0}, {"qualified_name": "c", "score": 4.0}]
        >>> fused = reciprocal_rank_fusion(dense, bm25)
        >>> # "b" ranks highest because it appears in both lists
    """
    scores: Dict[str, float] = {}
    nodes: Dict[str, Dict] = {}

    # Score from dense results (rank-based, not score-based)
    for rank, result in enumerate(dense_results):
        # Handle both nested node dict and flat dict structures
        node = result.get("node", result)
        node_id = node.get(id_field)
        if node_id is None:
            continue

        scores[node_id] = scores.get(node_id, 0) + 1 / (k + rank + 1)
        nodes[node_id] = node

    # Score from BM25 results
    for rank, result in enumerate(bm25_results):
        node = result.get("node", result)
        node_id = node.get(id_field)
        if node_id is None:
            continue

        scores[node_id] = scores.get(node_id, 0) + 1 / (k + rank + 1)
        # Don't overwrite if we already have the node from dense results
        if node_id not in nodes:
            nodes[node_id] = node

    # Sort by combined RRF score (descending)
    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

    return [
        {"node": nodes[node_id], "score": scores[node_id]}
        for node_id in sorted_ids
    ]


def linear_fusion(
    dense_results: List[Dict],
    bm25_results: List[Dict],
    alpha: float = 0.7,
    id_field: str = "qualified_name",
) -> List[Dict]:
    """Combine results using linear interpolation of normalized scores.

    Combined score = alpha * dense_score_normalized + (1-alpha) * bm25_score_normalized

    Use when:
    - You want explicit control over the relative weight of dense vs sparse
    - Score distributions are relatively stable across queries
    - Fine-tuning the balance is important

    Args:
        dense_results: Results from dense embedding search
        bm25_results: Results from BM25 keyword search
        alpha: Weight for dense scores (default: 0.7 = 70% dense, 30% BM25)
        id_field: Field to use as unique document ID

    Returns:
        Fused results sorted by combined score (descending)

    Example:
        >>> dense = [{"qualified_name": "a", "score": 0.9}]
        >>> bm25 = [{"qualified_name": "b", "score": 5.0}]
        >>> fused = linear_fusion(dense, bm25, alpha=0.7)
    """
    scores: Dict[str, float] = {}
    nodes: Dict[str, Dict] = {}

    # Normalize and weight dense scores
    if dense_results:
        max_dense = max(r.get("score", 0) for r in dense_results)
        for result in dense_results:
            node = result.get("node", result)
            node_id = node.get(id_field)
            if node_id is None:
                continue

            raw_score = result.get("score", 0)
            normalized = raw_score / max_dense if max_dense > 0 else 0
            scores[node_id] = alpha * normalized
            nodes[node_id] = node

    # Normalize and weight BM25 scores
    if bm25_results:
        max_bm25 = max(r.get("score", 0) for r in bm25_results)
        for result in bm25_results:
            node = result.get("node", result)
            node_id = node.get(id_field)
            if node_id is None:
                continue

            raw_score = result.get("score", 0)
            normalized = raw_score / max_bm25 if max_bm25 > 0 else 0
            # Add BM25 contribution to existing score
            scores[node_id] = scores.get(node_id, 0) + (1 - alpha) * normalized
            if node_id not in nodes:
                nodes[node_id] = node

    # Sort by combined score (descending)
    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

    return [
        {"node": nodes[node_id], "score": scores[node_id]}
        for node_id in sorted_ids
    ]


def fuse_results(
    dense_results: List[Dict],
    bm25_results: List[Dict],
    config: HybridSearchConfig,
    id_field: str = "qualified_name",
) -> List[Dict]:
    """Fuse dense and BM25 results using configured method.

    Args:
        dense_results: Results from dense embedding search
        bm25_results: Results from BM25 keyword search
        config: Hybrid search configuration
        id_field: Field to use as unique document ID

    Returns:
        Fused results sorted by combined score

    Raises:
        ValueError: If unknown fusion method is specified
    """
    if config.fusion_method == "rrf":
        return reciprocal_rank_fusion(
            dense_results,
            bm25_results,
            k=config.rrf_k,
            id_field=id_field,
        )
    elif config.fusion_method == "linear":
        return linear_fusion(
            dense_results,
            bm25_results,
            alpha=config.alpha,
            id_field=id_field,
        )
    else:
        raise ValueError(f"Unknown fusion method: {config.fusion_method}")


# Lucene special characters that need escaping for full-text search
LUCENE_SPECIAL_CHARS = r'+-&|!(){}[]^"~*?:\/'


def escape_lucene_query(query: str) -> str:
    """Escape special characters for Lucene/Neo4j full-text query syntax.

    Neo4j full-text indexes use Apache Lucene under the hood. Special
    characters must be escaped to be treated as literal text.

    Args:
        query: Raw query string

    Returns:
        Query with special characters escaped

    Example:
        >>> escape_lucene_query("foo:bar && baz")
        'foo\\:bar \\&\\& baz'
    """
    escaped = query
    for char in LUCENE_SPECIAL_CHARS:
        escaped = escaped.replace(char, f"\\{char}")
    return escaped
