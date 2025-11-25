"""Unit tests for FastRP graph embeddings."""

import pytest
from unittest.mock import MagicMock, patch
import numpy as np

from repotoire.ml.graph_embeddings import (
    FastRPConfig,
    FastRPEmbedder,
    cosine_similarity,
)


class TestFastRPConfig:
    """Test FastRPConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = FastRPConfig()

        assert config.embedding_dimension == 128
        assert config.iteration_weights == [0.0, 1.0, 1.0, 0.5]
        assert config.property_ratio == 0.0
        assert config.feature_properties == []
        assert config.node_labels == ["Function", "Class", "File"]
        assert config.relationship_types == ["CALLS", "USES", "IMPORTS", "CONTAINS"]
        assert config.orientation == "UNDIRECTED"
        assert config.write_property == "fastrp_embedding"

    def test_custom_config(self):
        """Test custom configuration values."""
        config = FastRPConfig(
            embedding_dimension=256,
            iteration_weights=[0.0, 1.0, 2.0],
            node_labels=["Function"],
            relationship_types=["CALLS"],
            write_property="custom_embedding",
        )

        assert config.embedding_dimension == 256
        assert config.iteration_weights == [0.0, 1.0, 2.0]
        assert config.node_labels == ["Function"]
        assert config.relationship_types == ["CALLS"]
        assert config.write_property == "custom_embedding"


class TestFastRPEmbedder:
    """Test FastRPEmbedder class."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Neo4j client."""
        client = MagicMock()
        # Mock GDS version check
        client.execute_query.return_value = [{"version": "2.5.0"}]
        return client

    @pytest.fixture
    def embedder(self, mock_client):
        """Create an embedder with mocked client."""
        return FastRPEmbedder(mock_client)

    def test_init_verifies_gds(self, mock_client):
        """Test that initialization verifies GDS availability."""
        embedder = FastRPEmbedder(mock_client)

        # Should have called to check GDS version
        mock_client.execute_query.assert_called_with(
            "RETURN gds.version() AS version"
        )

    def test_init_with_custom_config(self, mock_client):
        """Test initialization with custom config."""
        config = FastRPConfig(embedding_dimension=64)
        embedder = FastRPEmbedder(mock_client, config)

        assert embedder.config.embedding_dimension == 64

    def test_init_fails_without_gds(self):
        """Test that init fails when GDS is not available."""
        client = MagicMock()
        client.execute_query.side_effect = Exception("GDS not found")

        with pytest.raises(RuntimeError, match="GDS.*not available"):
            FastRPEmbedder(client)

    def test_generate_embeddings(self, mock_client):
        """Test embedding generation."""
        # Setup mock responses
        mock_client.execute_query.side_effect = [
            # GDS version check
            [{"version": "2.5.0"}],
            # Drop graph (no error expected)
            [],
            # Create projection
            [{
                "graphName": "fastrp-code-graph",
                "nodeCount": 100,
                "relationshipCount": 200,
                "projectMillis": 50,
            }],
            # FastRP write
            [{
                "nodePropertiesWritten": 100,
                "computeMillis": 100,
                "writeMillis": 50,
            }],
        ]

        embedder = FastRPEmbedder(mock_client)
        stats = embedder.generate_embeddings()

        assert stats["node_count"] == 100
        assert stats["embedding_dimension"] == 128
        assert stats["compute_millis"] == 100
        assert stats["write_millis"] == 50

    def test_generate_embeddings_empty_graph(self, mock_client):
        """Test embedding generation with empty graph."""
        mock_client.execute_query.side_effect = [
            # GDS version check
            [{"version": "2.5.0"}],
            # Drop graph
            [],
            # Create projection - empty
            [{
                "graphName": "fastrp-code-graph",
                "nodeCount": 0,
                "relationshipCount": 0,
                "projectMillis": 10,
            }],
        ]

        embedder = FastRPEmbedder(mock_client)
        stats = embedder.generate_embeddings()

        assert stats["node_count"] == 0

    def test_get_embedding(self, mock_client):
        """Test getting embedding for a specific node."""
        embedding = [0.1] * 128
        mock_client.execute_query.side_effect = [
            [{"version": "2.5.0"}],  # GDS check
            [{"embedding": embedding}],  # Get embedding
        ]

        embedder = FastRPEmbedder(mock_client)
        result = embedder.get_embedding("my.module.function")

        assert result == embedding
        assert len(result) == 128

    def test_get_embedding_not_found(self, mock_client):
        """Test getting embedding for non-existent node."""
        mock_client.execute_query.side_effect = [
            [{"version": "2.5.0"}],  # GDS check
            [],  # No result
        ]

        embedder = FastRPEmbedder(mock_client)
        result = embedder.get_embedding("non.existent.function")

        assert result is None

    def test_find_similar(self, mock_client):
        """Test finding similar nodes."""
        mock_client.execute_query.side_effect = [
            [{"version": "2.5.0"}],  # GDS check
            [
                {"name": "module.func_a", "similarity": 0.95},
                {"name": "module.func_b", "similarity": 0.87},
                {"name": "module.func_c", "similarity": 0.75},
            ],
        ]

        embedder = FastRPEmbedder(mock_client)
        results = embedder.find_similar("module.target", top_k=3)

        assert len(results) == 3
        assert results[0] == ("module.func_a", 0.95)
        assert results[1] == ("module.func_b", 0.87)
        assert results[2] == ("module.func_c", 0.75)

    def test_find_similar_with_label_filter(self, mock_client):
        """Test finding similar nodes with label filter."""
        mock_client.execute_query.side_effect = [
            [{"version": "2.5.0"}],  # GDS check
            [{"name": "module.MyClass", "similarity": 0.9}],
        ]

        embedder = FastRPEmbedder(mock_client)
        results = embedder.find_similar(
            "module.OtherClass",
            top_k=5,
            node_labels=["Class"],
        )

        # Verify the query was called (label filter is in query string)
        call_args = mock_client.execute_query.call_args_list[-1]
        query = call_args[0][0]
        assert ":Class" in query

    def test_find_anomalies(self, mock_client):
        """Test finding anomalous nodes."""
        mock_client.execute_query.side_effect = [
            [{"version": "2.5.0"}],  # GDS check
            [
                {
                    "name": "module.isolated_func",
                    "file_path": "src/module.py",
                    "avg_neighbor_similarity": 0.15,
                },
                {
                    "name": "module.another_isolated",
                    "file_path": "src/other.py",
                    "avg_neighbor_similarity": 0.18,
                },
            ],
        ]

        embedder = FastRPEmbedder(mock_client)
        results = embedder.find_anomalies(threshold=0.2)

        assert len(results) == 2
        assert results[0]["qualified_name"] == "module.isolated_func"
        assert results[0]["avg_neighbor_similarity"] == 0.15

    def test_get_embedding_stats(self, mock_client):
        """Test getting embedding statistics."""
        mock_client.execute_query.side_effect = [
            [{"version": "2.5.0"}],  # GDS check
            [{"total": 100, "embedded": 95}],  # Total stats
            [
                {"label": "Function", "total": 80, "embedded": 78},
                {"label": "Class", "total": 15, "embedded": 14},
                {"label": "File", "total": 5, "embedded": 3},
            ],  # By label
        ]

        embedder = FastRPEmbedder(mock_client)
        stats = embedder.get_embedding_stats()

        assert stats["total_nodes"] == 100
        assert stats["nodes_with_embeddings"] == 95
        assert stats["coverage_percent"] == 95.0
        assert stats["by_label"]["Function"]["total"] == 80
        assert stats["by_label"]["Function"]["embedded"] == 78

    def test_cleanup(self, mock_client):
        """Test cleanup drops graph projection."""
        mock_client.execute_query.side_effect = [
            [{"version": "2.5.0"}],  # GDS check
            [],  # Drop graph
        ]

        embedder = FastRPEmbedder(mock_client)
        embedder.cleanup()

        # Should have called drop
        call_args = mock_client.execute_query.call_args_list[-1]
        query = call_args[0][0]
        assert "gds.graph.drop" in query


class TestCosineSimilarity:
    """Test cosine similarity function."""

    def test_identical_vectors(self):
        """Test similarity of identical vectors."""
        vec = [1.0, 2.0, 3.0, 4.0]
        similarity = cosine_similarity(vec, vec)
        assert abs(similarity - 1.0) < 1e-6

    def test_orthogonal_vectors(self):
        """Test similarity of orthogonal vectors."""
        vec1 = [1.0, 0.0]
        vec2 = [0.0, 1.0]
        similarity = cosine_similarity(vec1, vec2)
        assert abs(similarity) < 1e-6

    def test_opposite_vectors(self):
        """Test similarity of opposite vectors."""
        vec1 = [1.0, 2.0, 3.0]
        vec2 = [-1.0, -2.0, -3.0]
        similarity = cosine_similarity(vec1, vec2)
        assert abs(similarity - (-1.0)) < 1e-6

    def test_similar_vectors(self):
        """Test similarity of similar vectors."""
        vec1 = [1.0, 2.0, 3.0]
        vec2 = [1.1, 2.1, 3.1]
        similarity = cosine_similarity(vec1, vec2)
        assert similarity > 0.99

    def test_high_dimensional(self):
        """Test with high-dimensional vectors (like embeddings)."""
        np.random.seed(42)
        vec1 = np.random.randn(128).tolist()
        vec2 = np.random.randn(128).tolist()
        similarity = cosine_similarity(vec1, vec2)
        # Random vectors should have low similarity
        assert -1.0 <= similarity <= 1.0


class TestGraphName:
    """Test graph projection naming."""

    def test_graph_name_constant(self):
        """Test that graph name is consistent."""
        mock_client = MagicMock()
        mock_client.execute_query.return_value = [{"version": "2.5.0"}]
        embedder = FastRPEmbedder(mock_client)

        assert embedder.GRAPH_NAME == "fastrp-code-graph"
