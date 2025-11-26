"""Graph-based embeddings using Neo4j GDS algorithms.

This module provides structural embeddings for code entities using
Neo4j Graph Data Science algorithms like FastRP.

FastRP (Fast Random Projection) creates low-dimensional vector representations
by propagating random vectors through the graph structure. This captures
structural patterns like:
- Call graph topology
- Import relationships
- Code coupling patterns

Example:
    >>> from repotoire.ml.graph_embeddings import FastRPEmbedder
    >>> embedder = FastRPEmbedder(neo4j_client)
    >>> embedder.generate_embeddings()
    >>> similar = embedder.find_similar("my.module.function", top_k=5)
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
import numpy as np

from repotoire.graph.client import Neo4jClient
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class FastRPConfig:
    """Configuration for FastRP embedding generation.

    Attributes:
        embedding_dimension: Dimensionality of output embeddings (default: 128)
        iteration_weights: Weights for each propagation iteration.
            First weight is for initial features, subsequent for neighbors.
            [0.0, 1.0, 1.0, 0.5] means: no initial features, equal weight
            for 1-hop and 2-hop neighbors, half weight for 3-hop.
        property_ratio: Ratio of embedding from node properties vs structure (0-1)
        feature_properties: Node properties to use as input features
        node_labels: Node labels to include in embedding generation
        relationship_types: Relationship types to traverse
        orientation: Relationship orientation ('NATURAL', 'REVERSE', 'UNDIRECTED')
        write_property: Property name to store embeddings on nodes
    """

    embedding_dimension: int = 128
    iteration_weights: List[float] = field(
        default_factory=lambda: [0.0, 1.0, 1.0, 0.5]
    )
    property_ratio: float = 0.0  # Pure structural by default
    feature_properties: List[str] = field(default_factory=list)
    node_labels: List[str] = field(
        default_factory=lambda: ["Function", "Class", "File"]
    )
    relationship_types: List[str] = field(
        default_factory=lambda: ["CALLS", "USES", "IMPORTS", "CONTAINS"]
    )
    orientation: str = "UNDIRECTED"
    write_property: str = "fastrp_embedding"


class FastRPEmbedder:
    """Generate structural embeddings using Neo4j GDS FastRP algorithm.

    FastRP creates embeddings that capture the structural position of nodes
    in the code graph. Functions with similar call patterns, import structures,
    and relationships will have similar embeddings.

    This is complementary to text embeddings (CodeEmbedder):
    - Text embeddings capture semantic meaning from names/docs
    - FastRP embeddings capture structural patterns from graph topology

    Example:
        >>> client = Neo4jClient(uri="bolt://localhost:7687")
        >>> embedder = FastRPEmbedder(client)
        >>>
        >>> # Generate embeddings for all functions
        >>> stats = embedder.generate_embeddings()
        >>> print(f"Generated embeddings for {stats['node_count']} nodes")
        >>>
        >>> # Find structurally similar functions
        >>> similar = embedder.find_similar(
        ...     "api.handlers.process_request",
        ...     top_k=5
        ... )
        >>> for name, score in similar:
        ...     print(f"  {name}: {score:.3f}")
    """

    # Graph projection name (will be created/replaced each time)
    GRAPH_NAME = "fastrp-code-graph"

    def __init__(
        self,
        client: Neo4jClient,
        config: Optional[FastRPConfig] = None,
    ):
        """Initialize FastRP embedder.

        Args:
            client: Neo4j client instance
            config: FastRP configuration (uses defaults if not provided)
        """
        self.client = client
        self.config = config or FastRPConfig()

        # Verify GDS is available
        self._verify_gds_available()

        logger.info(
            f"Initialized FastRPEmbedder with dim={self.config.embedding_dimension}, "
            f"nodes={self.config.node_labels}, rels={self.config.relationship_types}"
        )

    def _verify_gds_available(self) -> None:
        """Verify Neo4j GDS plugin is installed and accessible.

        Raises:
            RuntimeError: If GDS is not available
        """
        try:
            result = self.client.execute_query("RETURN gds.version() AS version")
            version = result[0]["version"] if result else "unknown"
            logger.info(f"Neo4j GDS version: {version}")
        except Exception as e:
            raise RuntimeError(
                "Neo4j Graph Data Science (GDS) plugin is not available. "
                "Please install GDS or use the Neo4j Docker image with GDS: "
                "docker run -e NEO4J_PLUGINS='[\"graph-data-science\"]' neo4j:latest"
            ) from e

    def _drop_graph_if_exists(self) -> None:
        """Drop existing graph projection if it exists."""
        try:
            self.client.execute_query(
                f"CALL gds.graph.drop('{self.GRAPH_NAME}', false)"
            )
            logger.debug(f"Dropped existing graph projection: {self.GRAPH_NAME}")
        except Exception:
            # Graph doesn't exist, ignore
            pass

    def _create_graph_projection(self) -> Dict[str, Any]:
        """Create a graph projection for FastRP.

        Returns:
            Dictionary with projection statistics
        """
        self._drop_graph_if_exists()

        # Build node projection
        node_projection = {
            label: {"properties": self.config.feature_properties}
            for label in self.config.node_labels
        }

        # Build relationship projection
        rel_projection = {
            rel_type: {"orientation": self.config.orientation}
            for rel_type in self.config.relationship_types
        }

        # Create projection using native projection
        query = """
        CALL gds.graph.project(
            $graph_name,
            $node_projection,
            $rel_projection
        )
        YIELD graphName, nodeCount, relationshipCount, projectMillis
        RETURN graphName, nodeCount, relationshipCount, projectMillis
        """

        result = self.client.execute_query(
            query,
            {
                "graph_name": self.GRAPH_NAME,
                "node_projection": node_projection,
                "rel_projection": rel_projection,
            },
        )

        if result:
            stats = result[0]
            logger.info(
                f"Created graph projection '{stats['graphName']}': "
                f"{stats['nodeCount']} nodes, {stats['relationshipCount']} relationships "
                f"({stats['projectMillis']}ms)"
            )
            return dict(stats)

        return {}

    def generate_embeddings(self) -> Dict[str, Any]:
        """Generate FastRP embeddings for all nodes in projection.

        Creates a graph projection, runs FastRP, and writes embeddings
        as node properties.

        Returns:
            Dictionary with generation statistics:
            - node_count: Number of nodes processed
            - embedding_dimension: Dimension of embeddings
            - compute_millis: Time to compute embeddings
            - write_millis: Time to write embeddings
        """
        # Create fresh graph projection
        projection_stats = self._create_graph_projection()

        if projection_stats.get("nodeCount", 0) == 0:
            logger.warning("No nodes found in graph projection")
            return {"node_count": 0, "embedding_dimension": self.config.embedding_dimension}

        # Run FastRP and write embeddings
        query = """
        CALL gds.fastRP.write($graph_name, {
            embeddingDimension: $dimension,
            iterationWeights: $weights,
            writeProperty: $write_property
        })
        YIELD nodePropertiesWritten, computeMillis, writeMillis
        RETURN nodePropertiesWritten, computeMillis, writeMillis
        """

        result = self.client.execute_query(
            query,
            {
                "graph_name": self.GRAPH_NAME,
                "dimension": self.config.embedding_dimension,
                "weights": self.config.iteration_weights,
                "write_property": self.config.write_property,
            },
        )

        if result:
            stats = result[0]
            logger.info(
                f"Generated {stats['nodePropertiesWritten']} FastRP embeddings "
                f"(compute: {stats['computeMillis']}ms, write: {stats['writeMillis']}ms)"
            )
            return {
                "node_count": stats["nodePropertiesWritten"],
                "embedding_dimension": self.config.embedding_dimension,
                "compute_millis": stats["computeMillis"],
                "write_millis": stats["writeMillis"],
            }

        return {"node_count": 0, "embedding_dimension": self.config.embedding_dimension}

    def get_embedding(self, qualified_name: str) -> Optional[List[float]]:
        """Get the FastRP embedding for a specific node.

        Args:
            qualified_name: Qualified name of the entity

        Returns:
            Embedding vector as list of floats, or None if not found
        """
        query = f"""
        MATCH (n {{qualifiedName: $name}})
        WHERE n.{self.config.write_property} IS NOT NULL
        RETURN n.{self.config.write_property} AS embedding
        """

        result = self.client.execute_query(query, {"name": qualified_name})

        if result and result[0]["embedding"]:
            return list(result[0]["embedding"])
        return None

    def find_similar(
        self,
        qualified_name: str,
        top_k: int = 10,
        node_labels: Optional[List[str]] = None,
    ) -> List[Tuple[str, float]]:
        """Find nodes structurally similar to the given node.

        Uses cosine similarity between FastRP embeddings.

        Args:
            qualified_name: Qualified name of the source entity
            top_k: Number of similar nodes to return
            node_labels: Filter results to specific labels (default: all)

        Returns:
            List of (qualified_name, similarity_score) tuples, sorted by similarity
        """
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
        RETURN other.qualifiedName AS name, similarity
        """

        result = self.client.execute_query(
            query, {"name": qualified_name, "top_k": top_k}
        )

        return [(r["name"], r["similarity"]) for r in result]

    def find_anomalies(
        self,
        threshold: float = 0.2,
        node_labels: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Find nodes with unusual structural patterns.

        Identifies nodes whose average similarity to their neighbors
        is below a threshold, indicating unusual structural position.

        Args:
            threshold: Maximum average similarity to be considered anomalous
            node_labels: Filter to specific labels (default: Function only)

        Returns:
            List of anomalous nodes with their metrics
        """
        labels = node_labels or ["Function"]
        label_filter = ":".join(labels)

        query = f"""
        MATCH (n:{label_filter})
        WHERE n.{self.config.write_property} IS NOT NULL
        WITH n, n.{self.config.write_property} AS emb
        MATCH (n)-[]-(neighbor)
        WHERE neighbor.{self.config.write_property} IS NOT NULL
        WITH n, emb, collect(neighbor.{self.config.write_property}) AS neighborEmbs
        WHERE size(neighborEmbs) > 0
        WITH n,
             [ne IN neighborEmbs | gds.similarity.cosine(emb, ne)] AS similarities
        WITH n,
             reduce(sum = 0.0, s IN similarities | sum + s) / size(similarities) AS avgSimilarity
        WHERE avgSimilarity < $threshold
        RETURN n.qualifiedName AS name,
               n.filePath AS file_path,
               avgSimilarity AS avg_neighbor_similarity
        ORDER BY avgSimilarity ASC
        LIMIT 50
        """

        result = self.client.execute_query(query, {"threshold": threshold})

        return [
            {
                "qualified_name": r["name"],
                "file_path": r["file_path"],
                "avg_neighbor_similarity": r["avg_neighbor_similarity"],
            }
            for r in result
        ]

    def get_embedding_stats(self) -> Dict[str, Any]:
        """Get statistics about generated embeddings.

        Returns:
            Dictionary with embedding statistics:
            - total_nodes: Total nodes in graph
            - nodes_with_embeddings: Nodes that have FastRP embeddings
            - coverage_percent: Percentage of nodes with embeddings
            - by_label: Breakdown by node label
        """
        # Count total and embedded nodes
        query = f"""
        MATCH (n)
        WHERE any(label IN labels(n) WHERE label IN $labels)
        WITH count(n) AS total,
             count(CASE WHEN n.{self.config.write_property} IS NOT NULL THEN 1 END) AS embedded
        RETURN total, embedded
        """

        result = self.client.execute_query(
            query, {"labels": self.config.node_labels}
        )

        if not result:
            return {"total_nodes": 0, "nodes_with_embeddings": 0, "coverage_percent": 0}

        total = result[0]["total"]
        embedded = result[0]["embedded"]

        # Get breakdown by label
        label_query = f"""
        MATCH (n)
        WHERE any(label IN labels(n) WHERE label IN $labels)
        WITH labels(n)[0] AS label,
             count(n) AS total,
             count(CASE WHEN n.{self.config.write_property} IS NOT NULL THEN 1 END) AS embedded
        RETURN label, total, embedded
        ORDER BY total DESC
        """

        label_result = self.client.execute_query(
            label_query, {"labels": self.config.node_labels}
        )

        by_label = {
            r["label"]: {"total": r["total"], "embedded": r["embedded"]}
            for r in label_result
        }

        return {
            "total_nodes": total,
            "nodes_with_embeddings": embedded,
            "coverage_percent": (embedded / total * 100) if total > 0 else 0,
            "embedding_dimension": self.config.embedding_dimension,
            "by_label": by_label,
        }

    def cleanup(self) -> None:
        """Clean up resources (drop graph projection)."""
        self._drop_graph_if_exists()
        logger.debug("Cleaned up FastRP resources")


def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """Calculate cosine similarity between two vectors.

    Uses Rust implementation for 2x speedup over NumPy.

    Args:
        vec1: First vector
        vec2: Second vector

    Returns:
        Cosine similarity score (0 to 1)
    """
    try:
        from repotoire_fast import cosine_similarity_fast
        a = np.array(vec1, dtype=np.float32)
        b = np.array(vec2, dtype=np.float32)
        return float(cosine_similarity_fast(a, b))
    except ImportError:
        # Fallback to NumPy if Rust extension not available
        a = np.array(vec1)
        b = np.array(vec2)
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
