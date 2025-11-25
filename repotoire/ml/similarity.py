"""Structural similarity search using graph embeddings.

This module provides APIs for finding structurally similar code entities
using FastRP embeddings. It can be combined with text embeddings for
hybrid semantic + structural search.

Example:
    >>> from repotoire.ml.similarity import StructuralSimilarityAnalyzer
    >>> analyzer = StructuralSimilarityAnalyzer(neo4j_client)
    >>>
    >>> # Find similar functions
    >>> results = analyzer.find_similar_functions(
    ...     "api.handlers.process_request",
    ...     top_k=5
    ... )
    >>>
    >>> # Find code clones (very high similarity)
    >>> clones = analyzer.find_potential_clones(threshold=0.95)
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple

from repotoire.graph.client import Neo4jClient
from repotoire.ml.graph_embeddings import FastRPEmbedder, FastRPConfig
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class SimilarityResult:
    """Result from similarity search.

    Attributes:
        qualified_name: Qualified name of the similar entity
        similarity_score: Cosine similarity (0 to 1)
        file_path: Path to the file containing the entity
        node_type: Type of node (Function, Class, etc.)
        name: Short name of the entity
    """

    qualified_name: str
    similarity_score: float
    file_path: Optional[str] = None
    node_type: Optional[str] = None
    name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "qualified_name": self.qualified_name,
            "similarity_score": self.similarity_score,
            "file_path": self.file_path,
            "node_type": self.node_type,
            "name": self.name,
        }


class StructuralSimilarityAnalyzer:
    """Analyze structural similarity between code entities.

    Uses FastRP embeddings to find entities with similar structural patterns
    in the code graph (call relationships, imports, containment).

    Example:
        >>> client = Neo4jClient(uri="bolt://localhost:7687")
        >>> analyzer = StructuralSimilarityAnalyzer(client)
        >>>
        >>> # Ensure embeddings are generated
        >>> analyzer.ensure_embeddings()
        >>>
        >>> # Find similar functions
        >>> similar = analyzer.find_similar_functions(
        ...     "my.module.MyClass.process",
        ...     top_k=10
        ... )
        >>> for result in similar:
        ...     print(f"{result.name}: {result.similarity_score:.3f}")
    """

    def __init__(
        self,
        client: Neo4jClient,
        embedder: Optional[FastRPEmbedder] = None,
        config: Optional[FastRPConfig] = None,
    ):
        """Initialize similarity analyzer.

        Args:
            client: Neo4j client instance
            embedder: Optional pre-configured FastRPEmbedder
            config: FastRP configuration (used if embedder not provided)
        """
        self.client = client
        self.embedder = embedder or FastRPEmbedder(client, config)
        self.config = self.embedder.config

    def ensure_embeddings(self, force: bool = False) -> Dict[str, Any]:
        """Ensure FastRP embeddings exist, generating if needed.

        Args:
            force: Force regeneration even if embeddings exist

        Returns:
            Embedding statistics
        """
        stats = self.embedder.get_embedding_stats()

        if force or stats["nodes_with_embeddings"] == 0:
            logger.info("Generating FastRP embeddings...")
            gen_stats = self.embedder.generate_embeddings()
            return self.embedder.get_embedding_stats()

        logger.info(
            f"Using existing embeddings: {stats['nodes_with_embeddings']} nodes "
            f"({stats['coverage_percent']:.1f}% coverage)"
        )
        return stats

    def find_similar_functions(
        self,
        qualified_name: str,
        top_k: int = 10,
    ) -> List[SimilarityResult]:
        """Find functions structurally similar to the given function.

        Args:
            qualified_name: Qualified name of the source function
            top_k: Number of results to return

        Returns:
            List of SimilarityResult objects sorted by similarity
        """
        return self._find_similar(qualified_name, top_k, ["Function"])

    def find_similar_classes(
        self,
        qualified_name: str,
        top_k: int = 10,
    ) -> List[SimilarityResult]:
        """Find classes structurally similar to the given class.

        Args:
            qualified_name: Qualified name of the source class
            top_k: Number of results to return

        Returns:
            List of SimilarityResult objects sorted by similarity
        """
        return self._find_similar(qualified_name, top_k, ["Class"])

    def find_similar(
        self,
        qualified_name: str,
        top_k: int = 10,
        node_labels: Optional[List[str]] = None,
    ) -> List[SimilarityResult]:
        """Find entities structurally similar to the given entity.

        Args:
            qualified_name: Qualified name of the source entity
            top_k: Number of results to return
            node_labels: Filter to specific node types

        Returns:
            List of SimilarityResult objects sorted by similarity
        """
        return self._find_similar(qualified_name, top_k, node_labels)

    def _find_similar(
        self,
        qualified_name: str,
        top_k: int,
        node_labels: Optional[List[str]],
    ) -> List[SimilarityResult]:
        """Internal method to find similar entities with full metadata."""
        # Build label filter
        label_filter = ""
        if node_labels:
            labels = ":".join(node_labels)
            label_filter = f":{labels}"

        query = f"""
        MATCH (source {{qualifiedName: $name}})
        WHERE source.{self.config.write_property} IS NOT NULL
        WITH source, source.{self.config.write_property} AS sourceEmb
        MATCH (other{label_filter})
        WHERE other.{self.config.write_property} IS NOT NULL
          AND other.qualifiedName <> $name
        WITH other, sourceEmb, other.{self.config.write_property} AS otherEmb
        WITH other,
             gds.similarity.cosine(sourceEmb, otherEmb) AS similarity
        ORDER BY similarity DESC
        LIMIT $top_k
        RETURN other.qualifiedName AS qualified_name,
               other.name AS name,
               other.filePath AS file_path,
               labels(other)[0] AS node_type,
               similarity
        """

        result = self.client.execute_query(
            query, {"name": qualified_name, "top_k": top_k}
        )

        return [
            SimilarityResult(
                qualified_name=r["qualified_name"],
                similarity_score=r["similarity"],
                file_path=r["file_path"],
                node_type=r["node_type"],
                name=r["name"],
            )
            for r in result
        ]

    def find_potential_clones(
        self,
        threshold: float = 0.95,
        node_labels: Optional[List[str]] = None,
        limit: int = 100,
    ) -> List[Tuple[SimilarityResult, SimilarityResult]]:
        """Find pairs of entities that may be code clones.

        Identifies entity pairs with very high structural similarity,
        which may indicate duplicated code.

        Args:
            threshold: Minimum similarity to be considered a clone
            node_labels: Filter to specific node types (default: Function)
            limit: Maximum number of pairs to return

        Returns:
            List of (entity1, entity2) pairs with high similarity
        """
        labels = node_labels or ["Function"]
        label_filter = ":".join(labels)

        query = f"""
        MATCH (a:{label_filter}), (b:{label_filter})
        WHERE a.{self.config.write_property} IS NOT NULL
          AND b.{self.config.write_property} IS NOT NULL
          AND id(a) < id(b)
        WITH a, b,
             gds.similarity.cosine(
                 a.{self.config.write_property},
                 b.{self.config.write_property}
             ) AS similarity
        WHERE similarity >= $threshold
        ORDER BY similarity DESC
        LIMIT $limit
        RETURN a.qualifiedName AS name_a,
               a.name AS short_name_a,
               a.filePath AS file_a,
               b.qualifiedName AS name_b,
               b.name AS short_name_b,
               b.filePath AS file_b,
               similarity
        """

        result = self.client.execute_query(
            query, {"threshold": threshold, "limit": limit}
        )

        pairs = []
        for r in result:
            entity_a = SimilarityResult(
                qualified_name=r["name_a"],
                similarity_score=r["similarity"],
                file_path=r["file_a"],
                name=r["short_name_a"],
                node_type=labels[0] if labels else None,
            )
            entity_b = SimilarityResult(
                qualified_name=r["name_b"],
                similarity_score=r["similarity"],
                file_path=r["file_b"],
                name=r["short_name_b"],
                node_type=labels[0] if labels else None,
            )
            pairs.append((entity_a, entity_b))

        return pairs

    def find_isolated_entities(
        self,
        threshold: float = 0.2,
        node_labels: Optional[List[str]] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Find entities that are structurally isolated.

        Identifies entities whose embeddings are dissimilar to their
        graph neighbors, suggesting they may be:
        - Misplaced in the wrong module
        - Dead code with no real connections
        - Anomalous implementations

        Args:
            threshold: Maximum average neighbor similarity to be "isolated"
            node_labels: Filter to specific node types
            limit: Maximum results

        Returns:
            List of isolated entities with metrics
        """
        return self.embedder.find_anomalies(
            threshold=threshold, node_labels=node_labels
        )[:limit]

    def get_stats(self) -> Dict[str, Any]:
        """Get embedding coverage statistics.

        Returns:
            Dictionary with embedding statistics
        """
        return self.embedder.get_embedding_stats()
