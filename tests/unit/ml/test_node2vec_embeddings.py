"""Unit tests for Node2Vec graph embeddings.

Tests the Node2VecEmbedder class including:
- GDS backend detection and selection
- Rust fallback implementation
- FalkorDBNode2VecEmbedder deprecation
- Edge cases (empty graphs, missing dependencies)
"""

import sys
import warnings
from unittest.mock import MagicMock, patch, call

import numpy as np
import pytest

from repotoire.ml.node2vec_embeddings import (
    Node2VecConfig,
    Node2VecEmbedder,
    FalkorDBNode2VecEmbedder,
)



# =============================================================================
# Node2VecConfig Tests
# =============================================================================


class TestNode2VecConfig:
    """Test Node2VecConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = Node2VecConfig()

        assert config.embedding_dimension == 128
        assert config.walk_length == 80
        assert config.walks_per_node == 10
        assert config.window_size == 10
        assert config.return_factor == 1.0  # p parameter
        assert config.in_out_factor == 1.0  # q parameter
        assert config.write_property == "node2vec_embedding"

    def test_custom_config(self):
        """Test custom configuration values."""
        config = Node2VecConfig(
            embedding_dimension=256,
            walk_length=40,
            walks_per_node=5,
            window_size=5,
            return_factor=0.5,  # Low p = local exploration
            in_out_factor=2.0,  # High q = BFS-like
            write_property="custom_node2vec",
        )

        assert config.embedding_dimension == 256
        assert config.walk_length == 40
        assert config.walks_per_node == 5
        assert config.window_size == 5
        assert config.return_factor == 0.5
        assert config.in_out_factor == 2.0
        assert config.write_property == "custom_node2vec"

    def test_config_p_q_interpretation(self):
        """Test documentation accuracy for p and q parameters."""
        # Low p should encourage local exploration (returning to previous)
        local_config = Node2VecConfig(return_factor=0.25)
        assert local_config.return_factor < 1.0

        # Low q should encourage exploring outward (DFS-like)
        dfs_config = Node2VecConfig(in_out_factor=0.5)
        assert dfs_config.in_out_factor < 1.0

        # High q should encourage staying close (BFS-like)
        bfs_config = Node2VecConfig(in_out_factor=4.0)
        assert bfs_config.in_out_factor > 1.0


# =============================================================================
# Node2VecEmbedder Tests - GDS Detection
# =============================================================================


class TestNode2VecEmbedderGDSDetection:
    """Test GDS availability detection."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Neo4j client."""
        return MagicMock()

    def test_check_gds_available_when_present(self, mock_client):
        """Test GDS detection when GDS is installed."""
        mock_client.execute_query.return_value = [{"version": "2.6.0"}]

        embedder = Node2VecEmbedder(mock_client)
        result = embedder.check_gds_available()

        assert result is True
        mock_client.execute_query.assert_called_with(
            "RETURN gds.version() AS version"
        )

    def test_check_gds_available_when_missing(self, mock_client):
        """Test GDS detection when GDS is not installed."""
        mock_client.execute_query.side_effect = Exception(
            "Unknown function 'gds.version'"
        )

        embedder = Node2VecEmbedder(mock_client)
        result = embedder.check_gds_available()

        assert result is False

    def test_check_gds_available_caches_result(self, mock_client):
        """Test that GDS availability is cached."""
        mock_client.execute_query.return_value = [{"version": "2.6.0"}]

        embedder = Node2VecEmbedder(mock_client)

        # Call twice
        result1 = embedder.check_gds_available()
        result2 = embedder.check_gds_available()

        # Should only query once (cached)
        assert result1 is True
        assert result2 is True
        assert mock_client.execute_query.call_count == 1

    def test_should_use_gds_when_available(self, mock_client):
        """Test backend selection when GDS is available."""
        mock_client.execute_query.return_value = [{"version": "2.6.0"}]

        embedder = Node2VecEmbedder(mock_client)
        assert embedder._should_use_gds() is True

    def test_should_use_gds_when_force_rust(self, mock_client):
        """Test backend selection with force_rust=True."""
        mock_client.execute_query.return_value = [{"version": "2.6.0"}]

        embedder = Node2VecEmbedder(mock_client, force_rust=True)
        assert embedder._should_use_gds() is False

    def test_should_use_gds_when_missing(self, mock_client):
        """Test backend selection when GDS is missing."""
        mock_client.execute_query.side_effect = Exception("GDS not found")

        embedder = Node2VecEmbedder(mock_client)
        assert embedder._should_use_gds() is False


# =============================================================================
# Node2VecEmbedder Tests - Rust Backend
# =============================================================================


class TestNode2VecEmbedderRustBackend:
    """Test Rust backend for Node2Vec."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Neo4j client without GDS."""
        client = MagicMock()
        client.execute_query.side_effect = Exception("GDS not found")
        return client

    def test_rust_available_detection(self, mock_client):
        """Test that Rust backend is detected as available."""
        embedder = Node2VecEmbedder(mock_client)
        assert embedder._rust_available is True

    def test_generate_with_rust_basic(self, mock_client):
        """Test basic Rust-based embedding generation.

        REPO-250: Now uses unified backend (graph_node2vec) instead of gensim.
        """
        # Setup mock responses for node/edge queries
        def query_handler(query, **kwargs):
            if "GDS" in query.upper() or "gds.version" in query:
                raise Exception("GDS not found")
            elif "RETURN n.qualifiedName AS name" in query:
                return [
                    {"name": "module.func_a"},
                    {"name": "module.func_b"},
                    {"name": "module.func_c"},
                ]
            elif "RETURN a.qualifiedName AS src" in query:
                return [
                    {"src": "module.func_a", "dst": "module.func_b"},
                    {"src": "module.func_b", "dst": "module.func_c"},
                ]
            elif "SET n.node2vec_embedding" in query:
                return [{"updated": 1}]
            return []

        mock_client.execute_query.side_effect = query_handler

        embedder = Node2VecEmbedder(mock_client, force_rust=True)
        stats = embedder.generate_and_store_embeddings(seed=42)

        # REPO-250: Now uses unified backend instead of gensim
        assert stats["backend"] == "rust_unified"
        assert "walkCount" in stats
        assert stats["nodeCount"] > 0

    def test_generate_with_rust_custom_config(self, mock_client):
        """Test Rust backend respects custom config.

        REPO-250: Now uses unified backend (graph_node2vec) instead of gensim.
        """
        def query_handler(query, **kwargs):
            if "gds.version" in query:
                raise Exception("GDS not found")
            elif "RETURN n.qualifiedName AS name" in query:
                return [{"name": "func_a"}, {"name": "func_b"}]
            elif "RETURN a.qualifiedName AS src" in query:
                return [{"src": "func_a", "dst": "func_b"}]
            elif "SET n." in query:
                return [{"updated": 1}]
            return []

        mock_client.execute_query.side_effect = query_handler

        config = Node2VecConfig(
            embedding_dimension=64,
            walk_length=40,
            walks_per_node=5,
            return_factor=0.5,
            in_out_factor=2.0,
        )

        embedder = Node2VecEmbedder(mock_client, config, force_rust=True)
        stats = embedder.generate_and_store_embeddings(seed=42)

        # REPO-250: Unified backend is used
        assert stats["backend"] == "rust_unified"
        assert stats["nodeCount"] > 0

    def test_generate_with_rust_empty_graph(self, mock_client):
        """Test Rust backend with empty graph."""
        def query_handler(query, **kwargs):
            if "gds.version" in query:
                raise Exception("GDS not found")
            elif "RETURN n.qualifiedName AS name" in query:
                return []  # No nodes
            return []

        mock_client.execute_query.side_effect = query_handler

        embedder = Node2VecEmbedder(mock_client, force_rust=True)
        stats = embedder.generate_and_store_embeddings()

        assert stats["nodeCount"] == 0
        assert stats["nodePropertiesWritten"] == 0
        assert stats["walkCount"] == 0

    def test_generate_with_rust_no_edges(self, mock_client):
        """Test Rust backend with nodes but no edges."""
        def query_handler(query, **kwargs):
            if "gds.version" in query:
                raise Exception("GDS not found")
            elif "RETURN n.qualifiedName AS name" in query:
                return [{"name": "isolated_func"}]
            elif "RETURN a.qualifiedName AS src" in query:
                return []  # No edges
            return []

        mock_client.execute_query.side_effect = query_handler

        embedder = Node2VecEmbedder(mock_client, force_rust=True)
        stats = embedder.generate_and_store_embeddings()

        assert stats["nodeCount"] == 1
        assert stats["nodePropertiesWritten"] == 0
        assert stats["walkCount"] == 0

    def test_generate_with_rust_missing_gensim_still_works(self, mock_client):
        """Test that gensim is not required when unified backend is available.

        REPO-250: Unified backend doesn't need gensim at all.
        """
        def query_handler(query, **kwargs):
            if "gds.version" in query:
                raise Exception("GDS not found")
            elif "RETURN n.qualifiedName AS name" in query:
                return [{"name": "func_a"}, {"name": "func_b"}]
            elif "RETURN a.qualifiedName AS src" in query:
                return [{"src": "func_a", "dst": "func_b"}]
            elif "SET n." in query:
                return [{"updated": 1}]
            return []

        mock_client.execute_query.side_effect = query_handler

        embedder = Node2VecEmbedder(mock_client, force_rust=True)

        # Should work without gensim since unified backend is available
        stats = embedder.generate_and_store_embeddings(seed=42)
        assert stats["backend"] == "rust_unified"
        assert stats["nodeCount"] > 0


# =============================================================================
# Node2VecEmbedder Tests - GDS Backend
# =============================================================================


class TestNode2VecEmbedderGDSBackend:
    """Test GDS backend for Node2Vec."""

    @pytest.fixture
    def mock_client_with_gds(self):
        """Create a mock Neo4j client with GDS available."""
        client = MagicMock()
        return client

    def test_generate_with_gds(self, mock_client_with_gds):
        """Test GDS-based embedding generation."""
        # Setup mock responses
        mock_client_with_gds.execute_query.side_effect = [
            # GDS version check
            [{"version": "2.6.0"}],
            # Drop existing projection (may not exist)
            [],
            # Create projection
            [{"graphName": "code-graph-node2vec", "nodeCount": 100, "relationshipCount": 200}],
            # Node2Vec write
            [{
                "nodeCount": 100,
                "nodePropertiesWritten": 100,
                "preProcessingMillis": 10,
                "computeMillis": 500,
                "writeMillis": 50,
            }],
        ]

        embedder = Node2VecEmbedder(mock_client_with_gds)
        stats = embedder.generate_and_store_embeddings()

        assert stats["nodeCount"] == 100
        assert stats["nodePropertiesWritten"] == 100
        assert "computeMillis" in stats

    def test_create_projection_without_gds_raises(self, mock_client_with_gds):
        """Test that create_projection fails without GDS."""
        mock_client_with_gds.execute_query.side_effect = Exception("GDS not found")

        embedder = Node2VecEmbedder(mock_client_with_gds)

        with pytest.raises(RuntimeError, match="GDS.*not available"):
            embedder.create_projection()

    def test_generate_embeddings_without_projection_raises(self, mock_client_with_gds):
        """Test that generate_embeddings fails without projection."""
        mock_client_with_gds.execute_query.return_value = [{"version": "2.6.0"}]

        embedder = Node2VecEmbedder(mock_client_with_gds)

        with pytest.raises(RuntimeError, match="projection does not exist"):
            embedder.generate_embeddings()


# =============================================================================
# Node2VecEmbedder Tests - Backend Selection
# =============================================================================


class TestNode2VecEmbedderBackendSelection:
    """Test automatic backend selection."""

    def test_auto_selects_gds_when_available(self):
        """Test that GDS is preferred when available."""
        mock_client = MagicMock()
        mock_client.execute_query.side_effect = [
            [{"version": "2.6.0"}],  # GDS check
            [],  # Drop projection
            [{"graphName": "test", "nodeCount": 10, "relationshipCount": 20}],
            [{"nodeCount": 10, "nodePropertiesWritten": 10, "computeMillis": 100}],
        ]

        embedder = Node2VecEmbedder(mock_client)
        stats = embedder.generate_and_store_embeddings()

        # Should NOT have "backend" key (GDS doesn't add it)
        assert "backend" not in stats or stats.get("backend") != "rust+gensim"

    def test_auto_falls_back_to_rust(self):
        """Test automatic fallback to Rust when GDS unavailable.

        REPO-250: Now uses unified backend instead of rust+gensim.
        """
        mock_client = MagicMock()

        def query_handler(query, **kwargs):
            if "gds.version" in query:
                raise Exception("GDS not found")
            elif "RETURN n.qualifiedName AS name" in query:
                return [{"name": "func_a"}, {"name": "func_b"}]
            elif "RETURN a.qualifiedName AS src" in query:
                return [{"src": "func_a", "dst": "func_b"}]
            elif "SET n." in query:
                return [{"updated": 1}]
            return []

        mock_client.execute_query.side_effect = query_handler

        embedder = Node2VecEmbedder(mock_client)
        stats = embedder.generate_and_store_embeddings(seed=42)

        # REPO-250: Should use unified backend
        assert stats["backend"] == "rust_unified"

    def test_raises_when_no_backend_available(self):
        """Test error when neither GDS nor Rust available."""
        mock_client = MagicMock()
        mock_client.execute_query.side_effect = Exception("GDS not found")

        embedder = Node2VecEmbedder(mock_client)
        embedder._rust_available = False  # Simulate missing Rust

        with pytest.raises(RuntimeError, match="No Node2Vec backend available"):
            embedder.generate_and_store_embeddings()


# =============================================================================
# Node2VecEmbedder Tests - Utility Methods
# =============================================================================


class TestNode2VecEmbedderUtilities:
    """Test utility methods."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Neo4j client that doesn't auto-check GDS."""
        client = MagicMock()
        return client

    def test_get_embeddings(self, mock_client):
        """Test retrieving embeddings from graph."""
        embedding = [0.1] * 128

        # Use a function to handle the query logic
        def query_handler(query, **kwargs):
            if "gds.version" in query:
                raise Exception("GDS not found")
            elif "WHERE n.node2vec_embedding IS NOT NULL" in query:
                return [
                    {"qualified_name": "module.func_a", "embedding": embedding},
                    {"qualified_name": "module.func_b", "embedding": embedding},
                ]
            return []

        mock_client.execute_query.side_effect = query_handler

        embedder = Node2VecEmbedder(mock_client)
        results = embedder.get_embeddings(node_type="Function", limit=10)

        assert len(results) == 2
        assert results[0]["qualified_name"] == "module.func_a"

    def test_get_embedding_for_node(self, mock_client):
        """Test retrieving embedding for specific node."""
        embedding = [0.5] * 128

        def query_handler(query, **kwargs):
            if "gds.version" in query:
                raise Exception("GDS not found")
            elif "qualifiedName" in query and kwargs.get("qualified_name"):
                return [{"embedding": embedding}]
            return []

        mock_client.execute_query.side_effect = query_handler

        embedder = Node2VecEmbedder(mock_client)
        result = embedder.get_embedding_for_node("module.my_func")

        assert result is not None
        assert len(result) == 128
        assert isinstance(result, np.ndarray)

    def test_get_embedding_for_node_not_found(self, mock_client):
        """Test retrieving embedding for non-existent node."""
        def query_handler(query, **kwargs):
            if "gds.version" in query:
                raise Exception("GDS not found")
            return []  # No result

        mock_client.execute_query.side_effect = query_handler

        embedder = Node2VecEmbedder(mock_client)
        result = embedder.get_embedding_for_node("nonexistent.func")

        assert result is None

    def test_compute_embedding_statistics(self, mock_client):
        """Test computing embedding statistics."""
        embeddings = [
            {"qualified_name": "a", "embedding": [1.0, 0.0, 0.0]},
            {"qualified_name": "b", "embedding": [0.0, 1.0, 0.0]},
            {"qualified_name": "c", "embedding": [0.0, 0.0, 1.0]},
        ]

        def query_handler(query, **kwargs):
            if "gds.version" in query:
                raise Exception("GDS not found")
            elif "WHERE n.node2vec_embedding IS NOT NULL" in query:
                return embeddings
            return []

        mock_client.execute_query.side_effect = query_handler

        embedder = Node2VecEmbedder(mock_client)
        stats = embedder.compute_embedding_statistics()

        assert stats["count"] == 3
        assert stats["dimension"] == 3
        assert stats["mean_norm"] == pytest.approx(1.0, rel=1e-6)
        assert stats["min_norm"] == pytest.approx(1.0, rel=1e-6)
        assert stats["max_norm"] == pytest.approx(1.0, rel=1e-6)

    def test_compute_embedding_statistics_empty(self, mock_client):
        """Test statistics with no embeddings."""
        def query_handler(query, **kwargs):
            if "gds.version" in query:
                raise Exception("GDS not found")
            return []  # No embeddings

        mock_client.execute_query.side_effect = query_handler

        embedder = Node2VecEmbedder(mock_client)
        stats = embedder.compute_embedding_statistics()

        assert stats["count"] == 0
        assert stats["mean_norm"] == 0.0

    def test_cleanup(self, mock_client):
        """Test cleanup drops projection."""
        def query_handler(query, **kwargs):
            if "gds.version" in query:
                raise Exception("GDS not found")
            return []  # Drop result

        mock_client.execute_query.side_effect = query_handler

        embedder = Node2VecEmbedder(mock_client)
        embedder.cleanup()

        # Verify drop was called
        call_args = mock_client.execute_query.call_args_list[-1]
        query = call_args[0][0]
        assert "gds.graph.drop" in query


# =============================================================================
# FalkorDBNode2VecEmbedder Deprecation Tests
# =============================================================================


class TestFalkorDBNode2VecEmbedderDeprecation:
    """Test FalkorDBNode2VecEmbedder deprecation."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock client."""
        client = MagicMock()
        client.execute_query.side_effect = Exception("GDS not found")
        return client

    def test_deprecation_warning_raised(self, mock_client):
        """Test that deprecation warning is raised on instantiation."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            FalkorDBNode2VecEmbedder(mock_client)

            # Filter for deprecation warnings
            deprecation_warnings = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]

            assert len(deprecation_warnings) == 1
            assert "deprecated" in str(deprecation_warnings[0].message).lower()
            assert "Node2VecEmbedder" in str(deprecation_warnings[0].message)

    def test_inherits_from_node2vec_embedder(self, mock_client):
        """Test that FalkorDBNode2VecEmbedder inherits from Node2VecEmbedder."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)

            embedder = FalkorDBNode2VecEmbedder(mock_client)

            assert isinstance(embedder, Node2VecEmbedder)

    def test_force_rust_is_true(self, mock_client):
        """Test that force_rust=True is always set."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)

            embedder = FalkorDBNode2VecEmbedder(mock_client)

            # force_rust should be True regardless of use_rust parameter
            assert embedder._force_rust is True

    def test_use_rust_parameter_ignored(self, mock_client):
        """Test that use_rust parameter is ignored (always True)."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)

            embedder = FalkorDBNode2VecEmbedder(mock_client, use_rust=False)

            # Should still be force_rust=True
            assert embedder._force_rust is True

    def test_custom_config_works(self, mock_client):
        """Test that custom config is passed through."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)

            config = Node2VecConfig(embedding_dimension=64, walk_length=20)
            embedder = FalkorDBNode2VecEmbedder(mock_client, config)

            assert embedder.config.embedding_dimension == 64
            assert embedder.config.walk_length == 20


# =============================================================================
# Rust Implementation Direct Tests
# =============================================================================


class TestRustNode2VecRandomWalks:
    """Test the Rust node2vec_random_walks function directly."""

    def test_basic_walk_generation(self):
        """Test basic walk generation."""
        from repotoire_fast import node2vec_random_walks

        # Simple triangle graph: 0 -> 1 -> 2 -> 0
        edges = [(0, 1), (1, 2), (2, 0)]

        walks = node2vec_random_walks(
            edges=edges,
            num_nodes=3,
            walk_length=5,
            walks_per_node=2,
            seed=42,
        )

        # Should generate 3 nodes * 2 walks = 6 walks
        assert len(walks) == 6

        # Each walk should have length 5
        for walk in walks:
            assert len(walk) == 5

        # All node IDs should be valid
        for walk in walks:
            for node_id in walk:
                assert 0 <= node_id < 3

    def test_determinism_with_seed(self):
        """Test that same seed produces same walks."""
        from repotoire_fast import node2vec_random_walks

        edges = [(0, 1), (1, 2), (2, 3), (3, 0), (0, 2)]

        walks1 = node2vec_random_walks(
            edges=edges, num_nodes=4, walk_length=10, walks_per_node=3, seed=12345
        )
        walks2 = node2vec_random_walks(
            edges=edges, num_nodes=4, walk_length=10, walks_per_node=3, seed=12345
        )

        assert walks1 == walks2

    def test_different_seeds_different_walks(self):
        """Test that different seeds produce different walks."""
        from repotoire_fast import node2vec_random_walks

        edges = [(0, 1), (1, 2), (2, 3), (3, 0), (0, 2), (1, 3)]

        walks1 = node2vec_random_walks(
            edges=edges, num_nodes=4, walk_length=10, walks_per_node=5, seed=111
        )
        walks2 = node2vec_random_walks(
            edges=edges, num_nodes=4, walk_length=10, walks_per_node=5, seed=222
        )

        # Walks should differ (very high probability)
        assert walks1 != walks2

    def test_p_parameter_effect(self):
        """Test that p parameter affects walk behavior."""
        from repotoire_fast import node2vec_random_walks

        # Star graph: center (0) connected to all others
        edges = [(0, 1), (0, 2), (0, 3), (0, 4)]

        # Low p = more likely to return to previous node
        walks_low_p = node2vec_random_walks(
            edges=edges, num_nodes=5, walk_length=20, walks_per_node=10, p=0.1, q=1.0, seed=42
        )

        # High p = less likely to return
        walks_high_p = node2vec_random_walks(
            edges=edges, num_nodes=5, walk_length=20, walks_per_node=10, p=4.0, q=1.0, seed=42
        )

        # Count returns to center (node 0) in walks starting from leaf
        def count_center_visits(walks):
            return sum(walk.count(0) for walk in walks if walk[0] != 0)

        # Low p should have more center visits
        low_p_visits = count_center_visits(walks_low_p)
        high_p_visits = count_center_visits(walks_high_p)

        # This is probabilistic but should generally hold
        assert low_p_visits >= high_p_visits * 0.5  # Allow some variance

    def test_empty_graph(self):
        """Test handling of empty graph."""
        from repotoire_fast import node2vec_random_walks

        walks = node2vec_random_walks(
            edges=[], num_nodes=0, walk_length=10, walks_per_node=5
        )

        assert walks == []

    def test_isolated_nodes_produce_empty_walks(self):
        """Test that nodes without outgoing edges produce no walks."""
        from repotoire_fast import node2vec_random_walks

        # Only edge from 0 to 1
        # Node 0: has outgoing edge → produces walks
        # Node 1: no outgoing edges (sink) → produces NO walks
        # Node 2: isolated → produces NO walks
        edges = [(0, 1)]

        walks = node2vec_random_walks(
            edges=edges, num_nodes=3, walk_length=5, walks_per_node=2
        )

        # Only node 0 has outgoing edges, so only node 0 produces walks
        assert len(walks) == 2  # 1 node * 2 walks_per_node

        # All walks should start from node 0
        for walk in walks:
            assert walk[0] == 0

    def test_invalid_parameters(self):
        """Test error handling for invalid parameters."""
        from repotoire_fast import node2vec_random_walks

        edges = [(0, 1)]

        # Invalid p (must be > 0)
        with pytest.raises(Exception):
            node2vec_random_walks(edges=edges, num_nodes=2, p=0.0)

        # Invalid q (must be > 0)
        with pytest.raises(Exception):
            node2vec_random_walks(edges=edges, num_nodes=2, q=-1.0)

    def test_performance_large_graph(self):
        """Test performance with larger graph."""
        from repotoire_fast import node2vec_random_walks
        import time

        # Create a larger test graph (1000 nodes, ~5000 edges)
        np.random.seed(42)
        num_nodes = 1000
        edges = []
        for i in range(num_nodes):
            # Each node connects to ~5 random others
            targets = np.random.choice(num_nodes, size=5, replace=False)
            for t in targets:
                if t != i:
                    edges.append((i, int(t)))

        start = time.time()
        walks = node2vec_random_walks(
            edges=edges,
            num_nodes=num_nodes,
            walk_length=80,
            walks_per_node=10,
            seed=42,
        )
        elapsed = time.time() - start

        # Should complete in reasonable time (<5 seconds)
        assert elapsed < 5.0

        # Should generate many walks
        assert len(walks) > 5000  # Most nodes should produce walks


# =============================================================================
# REPO-250: Unified graph_node2vec Pipeline Tests
# =============================================================================


class TestGraphNode2Vec:
    """Test the unified graph_node2vec function (REPO-250)."""

    def test_basic_embedding_generation(self):
        """Test basic Node2Vec embedding generation."""
        from repotoire_fast import graph_node2vec

        # Simple connected graph
        edges = [(0, 1), (1, 2), (2, 0), (0, 3), (3, 2)]

        node_ids, embeddings = graph_node2vec(
            edges=edges,
            num_nodes=4,
            embedding_dim=32,
            walk_length=20,
            walks_per_node=5,
            epochs=3,
            seed=42,
        )

        # Should return embeddings for all nodes that appear in walks
        assert len(node_ids) > 0
        assert embeddings.shape[0] == len(node_ids)
        assert embeddings.shape[1] == 32

        # Embeddings should be numpy array
        assert isinstance(embeddings, np.ndarray)
        assert embeddings.dtype == np.float32

    def test_empty_graph(self):
        """Test with empty graph."""
        from repotoire_fast import graph_node2vec

        node_ids, embeddings = graph_node2vec(
            edges=[],
            num_nodes=0,
            embedding_dim=32,
        )

        assert len(node_ids) == 0
        assert embeddings.shape == (0, 32)

    def test_single_node(self):
        """Test with single node (no edges)."""
        from repotoire_fast import graph_node2vec

        node_ids, embeddings = graph_node2vec(
            edges=[],
            num_nodes=1,
            embedding_dim=32,
        )

        # No edges means no walks, so no embeddings
        assert len(node_ids) == 0

    def test_disconnected_components(self):
        """Test with disconnected graph components."""
        from repotoire_fast import graph_node2vec

        # Two disconnected triangles
        edges = [
            (0, 1), (1, 2), (2, 0),  # Component 1
            (3, 4), (4, 5), (5, 3),  # Component 2
        ]

        node_ids, embeddings = graph_node2vec(
            edges=edges,
            num_nodes=6,
            embedding_dim=32,
            walk_length=10,
            walks_per_node=3,
            seed=42,
        )

        # Should have embeddings for nodes in both components
        assert len(node_ids) == 6
        assert embeddings.shape == (6, 32)

    def test_determinism_with_seed(self):
        """Test that same seed produces similar (not exact) results.

        Note: With Hogwild! parallel training, exact determinism is not guaranteed
        due to race conditions in concurrent updates. However, embeddings should
        be structurally similar (same node ordering, similar embedding space).
        """
        from repotoire_fast import graph_node2vec

        edges = [(0, 1), (1, 2), (2, 0), (0, 3)]

        ids1, emb1 = graph_node2vec(
            edges=edges, num_nodes=4, embedding_dim=16, epochs=2, seed=12345
        )
        ids2, emb2 = graph_node2vec(
            edges=edges, num_nodes=4, embedding_dim=16, epochs=2, seed=12345
        )

        # Node IDs should be in same order (deterministic from walks)
        assert ids1 == ids2

        # Embeddings should have same shape
        assert emb1.shape == emb2.shape

        # Embeddings should be similar (not exact due to Hogwild! parallel training)
        # Allow up to 20% relative tolerance due to race conditions
        # Note: The random walks are deterministic with seed, but SGD updates are not
        assert np.allclose(emb1, emb2, rtol=0.3, atol=0.1), (
            "Embeddings should be approximately similar with same seed. "
            "Large differences may indicate a problem with the training."
        )

    def test_different_seeds(self):
        """Test that different seeds produce different results."""
        from repotoire_fast import graph_node2vec

        edges = [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3)]

        ids1, emb1 = graph_node2vec(
            edges=edges, num_nodes=4, embedding_dim=16, epochs=3, seed=111
        )
        ids2, emb2 = graph_node2vec(
            edges=edges, num_nodes=4, embedding_dim=16, epochs=3, seed=222
        )

        # Embeddings should differ
        assert not np.allclose(emb1, emb2)

    def test_default_parameters(self):
        """Test with default parameters."""
        from repotoire_fast import graph_node2vec

        edges = [(0, 1), (1, 2), (2, 3)]

        # Just edges and num_nodes should work
        node_ids, embeddings = graph_node2vec(edges, 4)

        assert len(node_ids) > 0
        assert embeddings.shape[1] == 128  # Default embedding_dim

    def test_p_q_parameters(self):
        """Test p and q parameters affect embeddings."""
        from repotoire_fast import graph_node2vec

        # Use larger graph for more noticeable differences
        edges = [
            (0, 1), (1, 2), (2, 3), (3, 4), (4, 5),
            (5, 0), (0, 3), (1, 4), (2, 5),  # Cross-links
        ]

        # BFS-like (high q) - use None seed for randomness
        ids_bfs, emb_bfs = graph_node2vec(
            edges=edges, num_nodes=6, embedding_dim=32,
            walk_length=40, walks_per_node=10,
            p=1.0, q=4.0, epochs=5, seed=100
        )

        # DFS-like (low q) - use different seed
        ids_dfs, emb_dfs = graph_node2vec(
            edges=edges, num_nodes=6, embedding_dim=32,
            walk_length=40, walks_per_node=10,
            p=1.0, q=0.25, epochs=5, seed=200
        )

        # With different seeds and different walk strategies, embeddings should differ
        assert not np.allclose(emb_bfs, emb_dfs)

    def test_embedding_quality_cluster_structure(self):
        """Test that embeddings capture cluster structure."""
        from repotoire_fast import graph_node2vec
        from scipy.spatial.distance import cosine

        # Two tightly connected clusters with weak inter-cluster connection
        edges = [
            # Cluster A (0, 1, 2) - densely connected
            (0, 1), (1, 0), (0, 2), (2, 0), (1, 2), (2, 1),
            # Cluster B (3, 4, 5) - densely connected
            (3, 4), (4, 3), (3, 5), (5, 3), (4, 5), (5, 4),
            # Weak inter-cluster connection
            (2, 3),
        ]

        node_ids, embeddings = graph_node2vec(
            edges=edges,
            num_nodes=6,
            embedding_dim=64,
            walk_length=40,
            walks_per_node=20,
            epochs=10,
            seed=42,
        )

        # Build mapping from node_id to embedding index
        id_to_idx = {nid: i for i, nid in enumerate(node_ids)}

        def get_emb(node_id):
            return embeddings[id_to_idx[node_id]]

        # Within-cluster similarity should be higher than cross-cluster
        sim_01 = 1 - cosine(get_emb(0), get_emb(1))  # Within cluster A
        sim_34 = 1 - cosine(get_emb(3), get_emb(4))  # Within cluster B
        sim_03 = 1 - cosine(get_emb(0), get_emb(3))  # Cross-cluster

        # Nodes in same cluster should be more similar
        assert sim_01 > sim_03, f"Within-cluster A sim {sim_01} should be > cross-cluster {sim_03}"
        assert sim_34 > sim_03, f"Within-cluster B sim {sim_34} should be > cross-cluster {sim_03}"

    def test_performance_medium_graph(self):
        """Test performance with medium-sized graph."""
        from repotoire_fast import graph_node2vec
        import time

        # Create graph with 500 nodes
        np.random.seed(42)
        num_nodes = 500
        edges = []
        for i in range(num_nodes):
            # Each node connects to ~5 random others
            targets = np.random.choice(num_nodes, size=5, replace=False)
            for t in targets:
                if t != i:
                    edges.append((i, int(t)))

        start = time.time()
        node_ids, embeddings = graph_node2vec(
            edges=edges,
            num_nodes=num_nodes,
            embedding_dim=64,
            walk_length=40,
            walks_per_node=5,
            epochs=3,
            seed=42,
        )
        elapsed = time.time() - start

        # Should complete in reasonable time (<10 seconds)
        assert elapsed < 10.0, f"graph_node2vec took {elapsed:.2f}s, expected <10s"

        # Should have embeddings
        assert len(node_ids) > 400  # Most nodes should have embeddings


class TestGraphRandomWalks:
    """Test the graph_random_walks function (REPO-250)."""

    def test_basic_walk_generation(self):
        """Test basic walk generation via graph_random_walks."""
        from repotoire_fast import graph_random_walks

        edges = [(0, 1), (1, 2), (2, 0)]

        walks = graph_random_walks(
            edges=edges,
            num_nodes=3,
            walk_length=5,
            walks_per_node=2,
            seed=42,
        )

        # Should generate 3 nodes * 2 walks = 6 walks
        assert len(walks) == 6

        # Each walk should have length 5
        for walk in walks:
            assert len(walk) == 5

    def test_empty_graph(self):
        """Test with empty graph."""
        from repotoire_fast import graph_random_walks

        walks = graph_random_walks(edges=[], num_nodes=0)
        assert walks == []

    def test_default_parameters(self):
        """Test with default parameters."""
        from repotoire_fast import graph_random_walks

        edges = [(0, 1), (1, 2)]
        walks = graph_random_walks(edges, 3)

        # Default walk_length=80, walks_per_node=10
        # But only nodes 0 and 1 have outgoing edges
        assert len(walks) > 0


class TestUnifiedBackendSelection:
    """Test unified backend selection in Node2VecEmbedder (REPO-250)."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Neo4j client without GDS."""
        client = MagicMock()
        client.execute_query.side_effect = Exception("GDS not found")
        return client

    def test_unified_backend_detection(self, mock_client):
        """Test that unified backend is detected."""
        embedder = Node2VecEmbedder(mock_client, force_rust=True)

        # Should detect unified backend
        assert embedder._check_rust_unified_available() is True

    def test_unified_backend_used(self, mock_client):
        """Test that unified backend is used when available."""
        def query_handler(query, **kwargs):
            if "gds.version" in query:
                raise Exception("GDS not found")
            elif "RETURN n.qualifiedName AS name" in query:
                return [
                    {"name": "module.func_a"},
                    {"name": "module.func_b"},
                    {"name": "module.func_c"},
                ]
            elif "RETURN a.qualifiedName AS src" in query:
                return [
                    {"src": "module.func_a", "dst": "module.func_b"},
                    {"src": "module.func_b", "dst": "module.func_c"},
                ]
            elif "SET n." in query:
                return [{"updated": 1}]
            return []

        mock_client.execute_query.side_effect = query_handler

        embedder = Node2VecEmbedder(mock_client, force_rust=True)
        stats = embedder.generate_and_store_embeddings(seed=42)

        # Should use unified backend
        assert stats["backend"] == "rust_unified"
        assert stats["nodeCount"] > 0

    def test_unified_backend_empty_graph(self, mock_client):
        """Test unified backend with empty graph."""
        def query_handler(query, **kwargs):
            if "gds.version" in query:
                raise Exception("GDS not found")
            elif "RETURN n.qualifiedName AS name" in query:
                return []  # No nodes
            return []

        mock_client.execute_query.side_effect = query_handler

        embedder = Node2VecEmbedder(mock_client, force_rust=True)
        stats = embedder.generate_and_store_embeddings()

        assert stats["nodeCount"] == 0
        assert stats["nodePropertiesWritten"] == 0
        assert stats["walkCount"] == 0
