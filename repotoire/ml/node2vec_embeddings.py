"""Node2Vec embedding generation for code graph nodes.

Node2Vec is a graph embedding algorithm that learns continuous feature representations
for nodes in a graph. It works by:
1. Performing biased random walks on the graph
2. Treating walk sequences as "sentences"
3. Applying Word2Vec skip-gram to learn node representations

The biased walks capture both:
- Local (BFS-like) structure: immediate neighbors and local communities
- Global (DFS-like) structure: long-range dependencies and structural roles

This allows embeddings to capture complex patterns that correlate with defect-prone code:
- Functions in tightly-coupled clusters
- Highly-central functions (high traffic)
- Functions with unusual structural positions
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import logging

import numpy as np

from repotoire.graph.client import Neo4jClient

logger = logging.getLogger(__name__)


@dataclass
class Node2VecConfig:
    """Configuration for Node2Vec embedding generation.

    Attributes:
        embedding_dimension: Size of embedding vectors (default: 128)
        walk_length: Number of nodes visited per random walk (default: 80)
        walks_per_node: Number of random walks started from each node (default: 10)
        window_size: Context window size for skip-gram (default: 10)
        return_factor: p parameter - likelihood of returning to previous node (default: 1.0)
            - Low p (< 1): Encourage local exploration (BFS-like)
            - High p (> 1): Encourage moving away from previous node
        in_out_factor: q parameter - controls explore vs exploit trade-off (default: 1.0)
            - Low q (< 1): Encourage exploring outward (DFS-like)
            - High q (> 1): Encourage staying close to starting node (BFS-like)
        write_property: Node property name to store embeddings (default: "node2vec_embedding")
    """
    embedding_dimension: int = 128
    walk_length: int = 80
    walks_per_node: int = 10
    window_size: int = 10
    return_factor: float = 1.0      # p parameter (return to previous node)
    in_out_factor: float = 1.0      # q parameter (explore vs exploit)
    write_property: str = "node2vec_embedding"


class Node2VecEmbedder:
    """Generate Node2Vec embeddings using Neo4j GDS.

    Node2Vec learns node embeddings by performing biased random walks
    on the code graph and applying Word2Vec to learn representations.

    The embeddings capture structural patterns that correlate with code quality:
    - Tightly coupled function clusters
    - Central bottleneck functions
    - Isolated functions with few dependencies
    - Functions with unusual call patterns

    Example:
        >>> client = Neo4jClient.from_env()
        >>> embedder = Node2VecEmbedder(client)
        >>>
        >>> # Create projection and generate embeddings
        >>> embedder.create_projection()
        >>> stats = embedder.generate_embeddings()
        >>> print(f"Generated {stats['nodePropertiesWritten']} embeddings")
        >>>
        >>> # Retrieve embeddings for analysis
        >>> embeddings = embedder.get_embeddings(node_type="Function")
        >>> embedder.cleanup()
    """

    def __init__(
        self,
        client: Neo4jClient,
        config: Optional[Node2VecConfig] = None,
    ):
        """Initialize embedder.

        Args:
            client: Neo4j database client with GDS plugin enabled
            config: Node2Vec hyperparameters (uses defaults if not provided)
        """
        self.client = client
        self.config = config or Node2VecConfig()
        self._graph_name = "code-graph-node2vec"
        self._projection_exists = False

    def check_gds_available(self) -> bool:
        """Check if Neo4j GDS library is available.

        Returns:
            True if GDS is installed and available
        """
        try:
            result = self.client.execute_query(
                "RETURN gds.version() AS version"
            )
            version = result[0]["version"] if result else None
            logger.info(f"GDS version: {version}")
            return version is not None
        except Exception as e:
            logger.warning(f"GDS not available: {e}")
            return False

    def create_projection(
        self,
        node_labels: Optional[List[str]] = None,
        relationship_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create GDS graph projection for Node2Vec.

        Creates an in-memory graph projection containing the specified
        node types and relationship types for efficient algorithm execution.

        Args:
            node_labels: Node types to include (default: Function, Class, Module)
            relationship_types: Relationship types (default: CALLS, IMPORTS, USES)

        Returns:
            Dict with projection statistics:
            - graphName: Name of the projection
            - nodeCount: Number of nodes in projection
            - relationshipCount: Number of relationships in projection

        Raises:
            RuntimeError: If GDS is not available or projection fails
        """
        node_labels = node_labels or ["Function", "Class", "Module"]
        relationship_types = relationship_types or ["CALLS", "IMPORTS", "USES"]

        # Check GDS availability
        if not self.check_gds_available():
            raise RuntimeError(
                "Neo4j Graph Data Science (GDS) library is not available. "
                "Please install GDS plugin or use FalkorDB alternative."
            )

        # Drop existing projection if exists
        self._drop_projection_if_exists()

        # Create new projection
        query = """
        CALL gds.graph.project(
            $graph_name,
            $node_labels,
            $relationship_types
        )
        YIELD graphName, nodeCount, relationshipCount
        RETURN graphName, nodeCount, relationshipCount
        """

        try:
            result = self.client.execute_query(
                query,
                graph_name=self._graph_name,
                node_labels=node_labels,
                relationship_types=relationship_types,
            )

            self._projection_exists = True
            stats = result[0] if result else {}

            logger.info(
                f"Created projection '{self._graph_name}': "
                f"{stats.get('nodeCount', 0)} nodes, "
                f"{stats.get('relationshipCount', 0)} relationships"
            )

            return stats

        except Exception as e:
            logger.error(f"Failed to create projection: {e}")
            raise RuntimeError(f"Failed to create GDS projection: {e}")

    def _drop_projection_if_exists(self) -> None:
        """Drop existing graph projection to free memory."""
        query = """
        CALL gds.graph.exists($graph_name) YIELD exists
        WITH exists WHERE exists
        CALL gds.graph.drop($graph_name) YIELD graphName
        RETURN graphName
        """
        try:
            self.client.execute_query(query, graph_name=self._graph_name)
            self._projection_exists = False
        except Exception:
            # Ignore errors if projection doesn't exist
            pass

    def generate_embeddings(self) -> Dict[str, Any]:
        """Generate Node2Vec embeddings and write to nodes.

        Executes the Node2Vec algorithm via Neo4j GDS and stores
        embeddings as node properties in the database.

        The algorithm:
        1. Performs walks_per_node random walks from each node
        2. Each walk visits walk_length nodes following biased transitions
        3. Walks are treated as sentences for Word2Vec skip-gram training
        4. Learns embedding_dimension-dimensional vectors for each node

        Returns:
            Dict with generation statistics:
            - nodeCount: Number of nodes processed
            - nodePropertiesWritten: Number of embeddings written
            - preProcessingMillis: Pre-processing time
            - computeMillis: Compute time
            - writeMillis: Write time

        Raises:
            RuntimeError: If projection doesn't exist or algorithm fails
        """
        if not self._projection_exists:
            raise RuntimeError(
                "Graph projection does not exist. Call create_projection() first."
            )

        query = """
        CALL gds.node2vec.write($graph_name, {
            embeddingDimension: $embedding_dimension,
            walkLength: $walk_length,
            walksPerNode: $walks_per_node,
            windowSize: $window_size,
            returnFactor: $return_factor,
            inOutFactor: $in_out_factor,
            writeProperty: $write_property
        })
        YIELD nodeCount, nodePropertiesWritten, preProcessingMillis, computeMillis, writeMillis
        RETURN nodeCount, nodePropertiesWritten, preProcessingMillis, computeMillis, writeMillis
        """

        try:
            result = self.client.execute_query(
                query,
                graph_name=self._graph_name,
                embedding_dimension=self.config.embedding_dimension,
                walk_length=self.config.walk_length,
                walks_per_node=self.config.walks_per_node,
                window_size=self.config.window_size,
                return_factor=self.config.return_factor,
                in_out_factor=self.config.in_out_factor,
                write_property=self.config.write_property,
            )

            stats = result[0] if result else {}

            logger.info(
                f"Generated embeddings: {stats.get('nodePropertiesWritten', 0)} nodes, "
                f"compute time: {stats.get('computeMillis', 0)}ms"
            )

            return stats

        except Exception as e:
            logger.error(f"Failed to generate embeddings: {e}")
            raise RuntimeError(f"Failed to generate Node2Vec embeddings: {e}")

    def get_embeddings(
        self,
        node_type: str = "Function",
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve generated embeddings from graph.

        Args:
            node_type: Type of nodes to retrieve (e.g., "Function", "Class")
            limit: Maximum number of nodes to return (None for all)

        Returns:
            List of dicts with:
            - qualified_name: Node identifier
            - embedding: List of floats (embedding vector)
        """
        limit_clause = "LIMIT $limit" if limit else ""
        query = f"""
        MATCH (n:{node_type})
        WHERE n.{self.config.write_property} IS NOT NULL
        RETURN n.qualifiedName AS qualified_name,
               n.{self.config.write_property} AS embedding
        {limit_clause}
        """

        params: Dict[str, Any] = {}
        if limit:
            params["limit"] = limit

        return self.client.execute_query(query, **params)

    def get_embedding_for_node(
        self,
        qualified_name: str,
    ) -> Optional[np.ndarray]:
        """Retrieve embedding for a specific node.

        Args:
            qualified_name: Node's qualified name

        Returns:
            Embedding vector as numpy array, or None if not found
        """
        query = f"""
        MATCH (n {{qualifiedName: $qualified_name}})
        WHERE n.{self.config.write_property} IS NOT NULL
        RETURN n.{self.config.write_property} AS embedding
        """

        result = self.client.execute_query(query, qualified_name=qualified_name)

        if result and result[0].get("embedding"):
            return np.array(result[0]["embedding"])
        return None

    def stream_embeddings(
        self,
        node_type: str = "Function",
    ) -> List[Dict[str, Any]]:
        """Stream embeddings without persisting (for experimentation).

        Returns embeddings without writing to database.
        Useful for hyperparameter tuning and testing.

        Args:
            node_type: Type of nodes to embed

        Returns:
            List of dicts with qualified_name and embedding

        Raises:
            RuntimeError: If projection doesn't exist
        """
        if not self._projection_exists:
            raise RuntimeError(
                "Graph projection does not exist. Call create_projection() first."
            )

        query = """
        CALL gds.node2vec.stream($graph_name, {
            embeddingDimension: $embedding_dimension,
            walkLength: $walk_length,
            walksPerNode: $walks_per_node,
            windowSize: $window_size,
            returnFactor: $return_factor,
            inOutFactor: $in_out_factor
        })
        YIELD nodeId, embedding
        WITH gds.util.asNode(nodeId) AS node, embedding
        WHERE $node_type IN labels(node)
        RETURN node.qualifiedName AS qualified_name, embedding
        """

        return self.client.execute_query(
            query,
            graph_name=self._graph_name,
            node_type=node_type,
            embedding_dimension=self.config.embedding_dimension,
            walk_length=self.config.walk_length,
            walks_per_node=self.config.walks_per_node,
            window_size=self.config.window_size,
            return_factor=self.config.return_factor,
            in_out_factor=self.config.in_out_factor,
        )

    def compute_embedding_statistics(
        self,
        node_type: str = "Function",
    ) -> Dict[str, Any]:
        """Compute statistics about generated embeddings.

        Args:
            node_type: Type of nodes to analyze

        Returns:
            Dict with statistics:
            - count: Number of nodes with embeddings
            - dimension: Embedding dimension
            - mean_norm: Average L2 norm of embeddings
            - std_norm: Standard deviation of L2 norms
        """
        embeddings = self.get_embeddings(node_type=node_type)

        if not embeddings:
            return {
                "count": 0,
                "dimension": self.config.embedding_dimension,
                "mean_norm": 0.0,
                "std_norm": 0.0,
            }

        vectors = np.array([e["embedding"] for e in embeddings])
        norms = np.linalg.norm(vectors, axis=1)

        return {
            "count": len(embeddings),
            "dimension": vectors.shape[1] if len(vectors.shape) > 1 else 0,
            "mean_norm": float(np.mean(norms)),
            "std_norm": float(np.std(norms)),
            "min_norm": float(np.min(norms)),
            "max_norm": float(np.max(norms)),
        }

    def cleanup(self) -> None:
        """Remove graph projection to free memory.

        Should be called after embedding generation is complete
        to release memory used by the GDS projection.
        """
        self._drop_projection_if_exists()
        logger.info(f"Cleaned up projection '{self._graph_name}'")


class FalkorDBNode2VecEmbedder:
    """Node2Vec implementation for FalkorDB using native random walks.

    FalkorDB doesn't have GDS, so we implement Node2Vec using:
    1. Cypher random walks (using FalkorDB's native support)
    2. Python-based Word2Vec (gensim)

    This is a fallback for environments without Neo4j GDS.
    """

    def __init__(
        self,
        client: Neo4jClient,
        config: Optional[Node2VecConfig] = None,
    ):
        """Initialize FalkorDB embedder.

        Args:
            client: FalkorDB client
            config: Node2Vec configuration
        """
        self.client = client
        self.config = config or Node2VecConfig()

    def generate_random_walks(
        self,
        node_type: str = "Function",
        relationship_types: Optional[List[str]] = None,
    ) -> List[List[str]]:
        """Generate random walks using Cypher queries.

        Args:
            node_type: Starting node type
            relationship_types: Relationship types to traverse

        Returns:
            List of walks, where each walk is a list of node IDs
        """
        relationship_types = relationship_types or ["CALLS", "IMPORTS"]
        rel_pattern = "|".join(relationship_types)

        walks = []

        # Get all nodes of the given type
        query = f"""
        MATCH (n:{node_type})
        RETURN n.qualifiedName AS name
        """
        nodes = self.client.execute_query(query)

        for node in nodes:
            start_name = node["name"]

            for _ in range(self.config.walks_per_node):
                walk = self._perform_random_walk(
                    start_name,
                    rel_pattern,
                    self.config.walk_length
                )
                if len(walk) > 1:  # Only add non-trivial walks
                    walks.append(walk)

        return walks

    def _perform_random_walk(
        self,
        start_name: str,
        rel_pattern: str,
        length: int,
    ) -> List[str]:
        """Perform a single random walk from a starting node.

        Uses Cypher with RAND() for randomization.
        """
        # FalkorDB random walk query
        query = f"""
        MATCH (start {{qualifiedName: $start_name}})
        CALL {{
            WITH start
            MATCH path = (start)-[:{rel_pattern}*1..{length}]-(end)
            RETURN [n IN nodes(path) | n.qualifiedName] AS walk
            ORDER BY rand()
            LIMIT 1
        }}
        RETURN walk
        """

        try:
            result = self.client.execute_query(query, start_name=start_name)
            if result and result[0].get("walk"):
                return result[0]["walk"]
        except Exception:
            pass

        return [start_name]

    def train_embeddings(
        self,
        walks: List[List[str]],
    ) -> Dict[str, np.ndarray]:
        """Train Word2Vec on random walks to get embeddings.

        Args:
            walks: List of random walks

        Returns:
            Dict mapping node names to embedding vectors
        """
        try:
            from gensim.models import Word2Vec
        except ImportError:
            raise ImportError(
                "gensim required for FalkorDB Node2Vec: pip install gensim"
            )

        model = Word2Vec(
            sentences=walks,
            vector_size=self.config.embedding_dimension,
            window=self.config.window_size,
            min_count=1,
            workers=4,
            epochs=10,
        )

        return {
            word: model.wv[word]
            for word in model.wv.index_to_key
        }

    def generate_and_store_embeddings(
        self,
        node_type: str = "Function",
        relationship_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Generate embeddings and store in FalkorDB nodes.

        Args:
            node_type: Type of nodes to embed
            relationship_types: Relationships to traverse

        Returns:
            Statistics about embedding generation
        """
        logger.info("Generating random walks...")
        walks = self.generate_random_walks(node_type, relationship_types)

        logger.info(f"Generated {len(walks)} walks, training Word2Vec...")
        embeddings = self.train_embeddings(walks)

        logger.info(f"Writing {len(embeddings)} embeddings to graph...")
        write_count = 0

        for name, embedding in embeddings.items():
            query = f"""
            MATCH (n {{qualifiedName: $name}})
            SET n.{self.config.write_property} = $embedding
            RETURN count(n) AS updated
            """
            result = self.client.execute_query(
                query,
                name=name,
                embedding=embedding.tolist(),
            )
            if result and result[0].get("updated", 0) > 0:
                write_count += 1

        return {
            "nodeCount": len(embeddings),
            "nodePropertiesWritten": write_count,
            "walkCount": len(walks),
        }
