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
    """Generate Node2Vec embeddings using Neo4j GDS or Rust fallback.

    Node2Vec learns node embeddings by performing biased random walks
    on the code graph and applying Word2Vec to learn representations.

    The embeddings capture structural patterns that correlate with code quality:
    - Tightly coupled function clusters
    - Central bottleneck functions
    - Isolated functions with few dependencies
    - Functions with unusual call patterns

    **Backend Selection (REPO-247):**
    - If Neo4j GDS is available: Uses server-side GDS algorithm (fastest)
    - If GDS not available: Falls back to Rust random walks + gensim Word2Vec

    This works with:
    - Neo4j Aura Enterprise (has GDS)
    - Neo4j Aura Free/Professional (uses Rust fallback)
    - Neo4j self-hosted with GDS (has GDS)
    - Neo4j self-hosted Community (uses Rust fallback)
    - FalkorDB (uses Rust fallback)

    Example:
        >>> client = Neo4jClient.from_env()
        >>> embedder = Node2VecEmbedder(client)
        >>>
        >>> # Automatically uses GDS if available, otherwise Rust
        >>> stats = embedder.generate_and_store_embeddings()
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
        force_rust: bool = False,
    ):
        """Initialize embedder.

        Args:
            client: Neo4j/FalkorDB database client
            config: Node2Vec hyperparameters (uses defaults if not provided)
            force_rust: Force Rust implementation even if GDS available (for testing)
        """
        self.client = client
        self.config = config or Node2VecConfig()
        self._graph_name = "code-graph-node2vec"
        self._projection_exists = False
        self._force_rust = force_rust
        self._gds_available: Optional[bool] = None
        self._rust_available = self._check_rust_available()

    def _check_rust_available(self) -> bool:
        """Check if Rust implementation is available."""
        try:
            from repotoire_fast import node2vec_random_walks
            return True
        except ImportError:
            return False

    def _check_rust_word2vec_available(self) -> bool:
        """Check if Rust Word2Vec implementation is available (REPO-249)."""
        try:
            from repotoire_fast import train_word2vec_skipgram
            return True
        except ImportError:
            return False

    def _check_rust_unified_available(self) -> bool:
        """Check if unified Rust Node2Vec implementation is available (REPO-250)."""
        try:
            from repotoire_fast import graph_node2vec
            return True
        except ImportError:
            return False

    def check_gds_available(self) -> bool:
        """Check if Neo4j GDS library is available.

        Returns:
            True if GDS is installed and available
        """
        if self._gds_available is not None:
            return self._gds_available

        try:
            result = self.client.execute_query(
                "RETURN gds.version() AS version"
            )
            version = result[0]["version"] if result else None
            logger.info(f"GDS version: {version}")
            self._gds_available = version is not None
        except Exception as e:
            logger.info(f"GDS not available: {e}")
            self._gds_available = False

        return self._gds_available

    def _should_use_gds(self) -> bool:
        """Determine whether to use GDS or Rust backend."""
        if self._force_rust:
            return False
        return self.check_gds_available()

    def generate_and_store_embeddings(
        self,
        node_labels: Optional[List[str]] = None,
        relationship_types: Optional[List[str]] = None,
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Generate Node2Vec embeddings and store in graph nodes.

        Automatically selects the best available backend:
        - GDS if available (server-side, fastest)
        - Rust + gensim if GDS not available

        Args:
            node_labels: Node types to include (default: Function, Class, Module)
            relationship_types: Relationship types (default: CALLS, IMPORTS, USES)
            seed: Random seed for reproducibility (Rust backend only)

        Returns:
            Dict with generation statistics
        """
        node_labels = node_labels or ["Function", "Class", "Module"]
        relationship_types = relationship_types or ["CALLS", "IMPORTS", "USES"]

        if self._should_use_gds():
            logger.info("Using Neo4j GDS for Node2Vec (server-side)")
            return self._generate_with_gds(node_labels, relationship_types)
        elif self._rust_available:
            logger.info("Using Rust + gensim for Node2Vec (GDS not available)")
            return self._generate_with_rust(node_labels, relationship_types, seed)
        else:
            raise RuntimeError(
                "No Node2Vec backend available. Install either:\n"
                "- Neo4j GDS plugin (for server-side execution)\n"
                "- repotoire_fast + gensim (pip install repotoire[ml])"
            )

    def _generate_with_gds(
        self,
        node_labels: List[str],
        relationship_types: List[str],
    ) -> Dict[str, Any]:
        """Generate embeddings using Neo4j GDS."""
        self.create_projection(node_labels, relationship_types)
        return self.generate_embeddings()

    def _generate_with_rust(
        self,
        node_labels: List[str],
        relationship_types: List[str],
        seed: Optional[int],
    ) -> Dict[str, Any]:
        """Generate embeddings using Rust implementation.

        Backend priority (REPO-250):
        1. graph_node2vec (unified pipeline, most efficient - single Rust call)
        2. Rust random walks + Rust Word2Vec (two Rust calls)
        3. Rust random walks + gensim Word2Vec (fallback)
        """
        # Check available backends
        use_unified = self._check_rust_unified_available()
        use_rust_word2vec = self._check_rust_word2vec_available()

        if not use_unified and not use_rust_word2vec:
            try:
                from gensim.models import Word2Vec
            except ImportError:
                raise ImportError(
                    "gensim required when Rust Word2Vec not available: pip install gensim"
                )

        rel_pattern = "|".join(relationship_types)

        # Build OR condition for labels (Neo4j n:A:B means AND, we need OR)
        label_conditions = " OR ".join([f"n:{label}" for label in node_labels])

        # Step 1: Get all nodes and build ID mapping
        node_query = f"""
        MATCH (n)
        WHERE {label_conditions}
        RETURN n.qualifiedName AS name
        """
        nodes = self.client.execute_query(node_query)

        if not nodes:
            logger.warning(f"No nodes found with labels {node_labels}")
            return {"nodeCount": 0, "nodePropertiesWritten": 0, "walkCount": 0}

        # Build bidirectional mapping
        name_to_id: Dict[str, int] = {}
        id_to_name: Dict[int, str] = {}
        for i, node in enumerate(nodes):
            name = node.get("name")
            if name:
                name_to_id[name] = i
                id_to_name[i] = name

        num_nodes = len(id_to_name)
        logger.info(f"Found {num_nodes} nodes")

        # Step 2: Fetch all edges
        # Build OR conditions for source and destination nodes
        src_conditions = " OR ".join([f"a:{label}" for label in node_labels])
        dst_conditions = " OR ".join([f"b:{label}" for label in node_labels])

        edge_query = f"""
        MATCH (a)-[r:{rel_pattern}]->(b)
        WHERE ({src_conditions}) AND ({dst_conditions})
        RETURN a.qualifiedName AS src, b.qualifiedName AS dst
        """
        edge_results = self.client.execute_query(edge_query)

        edges: List[tuple] = []
        for edge in edge_results:
            src_name, dst_name = edge.get("src"), edge.get("dst")
            if src_name in name_to_id and dst_name in name_to_id:
                edges.append((name_to_id[src_name], name_to_id[dst_name]))

        logger.info(f"Found {len(edges)} edges")

        if not edges:
            return {"nodeCount": num_nodes, "nodePropertiesWritten": 0, "walkCount": 0}

        # Step 3 & 4: Generate embeddings using best available backend
        if use_unified:
            # REPO-250: Use unified pipeline (most efficient - single Rust call)
            from repotoire_fast import graph_node2vec

            logger.info(
                f"Using unified graph_node2vec pipeline "
                f"(p={self.config.return_factor}, q={self.config.in_out_factor})..."
            )

            node_ids, embeddings_matrix = graph_node2vec(
                edges=edges,
                num_nodes=num_nodes,
                embedding_dim=self.config.embedding_dimension,
                walk_length=self.config.walk_length,
                walks_per_node=self.config.walks_per_node,
                p=self.config.return_factor,
                q=self.config.in_out_factor,
                window_size=self.config.window_size,
                negative_samples=5,
                epochs=10,
                learning_rate=0.025,
                seed=seed,
            )

            # Convert to qualified names dict
            embeddings: Dict[str, Any] = {}
            for i, node_id in enumerate(node_ids):
                if node_id in id_to_name:
                    embeddings[id_to_name[node_id]] = embeddings_matrix[i].tolist()

            # Estimate walk count for stats
            walk_count = num_nodes * self.config.walks_per_node
            backend = "rust_unified"

        elif use_rust_word2vec:
            # REPO-249: Use Rust walks + Rust Word2Vec
            from repotoire_fast import node2vec_random_walks, train_word2vec_skipgram, PyWord2VecConfig

            logger.info(
                f"Generating {num_nodes * self.config.walks_per_node} walks "
                f"(p={self.config.return_factor}, q={self.config.in_out_factor})..."
            )

            walks_int = node2vec_random_walks(
                edges=edges,
                num_nodes=num_nodes,
                walk_length=self.config.walk_length,
                walks_per_node=self.config.walks_per_node,
                p=self.config.return_factor,
                q=self.config.in_out_factor,
                seed=seed,
            )

            walks_int = [w for w in walks_int if len(w) > 1]
            logger.info(f"Generated {len(walks_int)} walks")

            logger.info("Training Word2Vec with Rust (no gensim)...")
            config = PyWord2VecConfig(
                embedding_dim=self.config.embedding_dimension,
                window_size=self.config.window_size,
                min_count=1,
                negative_samples=5,
                learning_rate=0.025,
                epochs=10,
                seed=seed,
            )

            embeddings_dict = train_word2vec_skipgram(walks_int, config)

            embeddings = {}
            for node_id, embedding in embeddings_dict.items():
                if node_id in id_to_name:
                    embeddings[id_to_name[node_id]] = embedding

            walk_count = len(walks_int)
            backend = "rust+rust"

        else:
            # Fallback to gensim
            from repotoire_fast import node2vec_random_walks
            from gensim.models import Word2Vec

            logger.info(
                f"Generating {num_nodes * self.config.walks_per_node} walks "
                f"(p={self.config.return_factor}, q={self.config.in_out_factor})..."
            )

            walks_int = node2vec_random_walks(
                edges=edges,
                num_nodes=num_nodes,
                walk_length=self.config.walk_length,
                walks_per_node=self.config.walks_per_node,
                p=self.config.return_factor,
                q=self.config.in_out_factor,
                seed=seed,
            )

            walks_int = [w for w in walks_int if len(w) > 1]
            logger.info(f"Generated {len(walks_int)} walks, training with gensim...")

            walks: List[List[str]] = []
            for walk_int in walks_int:
                walk_names = [id_to_name[node_id] for node_id in walk_int]
                walks.append(walk_names)

            model = Word2Vec(
                sentences=walks,
                vector_size=self.config.embedding_dimension,
                window=self.config.window_size,
                min_count=1,
                workers=4,
                epochs=10,
            )

            embeddings = {word: model.wv[word].tolist() for word in model.wv.index_to_key}
            walk_count = len(walks_int)
            backend = "rust+gensim"

        # Step 5: Write embeddings to graph
        logger.info(f"Writing {len(embeddings)} embeddings to graph...")
        write_count = 0

        for name, embedding in embeddings.items():
            emb_list = embedding if isinstance(embedding, list) else embedding.tolist()
            query = f"""
            MATCH (n {{qualifiedName: $name}})
            SET n.{self.config.write_property} = $embedding
            RETURN count(n) AS updated
            """
            result = self.client.execute_query(
                query,
                parameters={"name": name, "embedding": emb_list},
            )
            if result and result[0].get("updated", 0) > 0:
                write_count += 1

        return {
            "nodeCount": len(embeddings),
            "nodePropertiesWritten": write_count,
            "walkCount": walk_count,
            "backend": backend,
        }

    def create_projection(
        self,
        node_labels: Optional[List[str]] = None,
        relationship_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create GDS graph projection for Node2Vec.

        Creates an in-memory graph projection containing the specified
        node types and relationship types for efficient algorithm execution.

        Note: This is only needed for GDS backend. The Rust backend doesn't
        require projections.

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
                "Use generate_and_store_embeddings() which auto-selects backend."
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


class FalkorDBNode2VecEmbedder(Node2VecEmbedder):
    """DEPRECATED: Use Node2VecEmbedder instead.

    This class is kept for backwards compatibility. Node2VecEmbedder now
    automatically falls back to Rust when GDS is not available.

    .. deprecated:: 0.2.0
        Use :class:`Node2VecEmbedder` instead, which auto-selects the best
        backend (GDS or Rust) based on availability.
    """

    def __init__(
        self,
        client: Neo4jClient,
        config: Optional[Node2VecConfig] = None,
        use_rust: bool = True,
    ):
        """Initialize embedder (deprecated, use Node2VecEmbedder).

        Args:
            client: Graph database client
            config: Node2Vec configuration
            use_rust: Ignored (always uses Rust when GDS unavailable)
        """
        import warnings
        warnings.warn(
            "FalkorDBNode2VecEmbedder is deprecated. Use Node2VecEmbedder instead, "
            "which automatically uses Rust when GDS is not available.",
            DeprecationWarning,
            stacklevel=2,
        )
        # force_rust=True to match old behavior (never tries GDS)
        super().__init__(client, config, force_rust=True)
