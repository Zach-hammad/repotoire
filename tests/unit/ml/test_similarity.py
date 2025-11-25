"""Unit tests for structural similarity analyzer."""

import pytest
from unittest.mock import MagicMock, patch

from repotoire.ml.similarity import (
    SimilarityResult,
    StructuralSimilarityAnalyzer,
)
from repotoire.ml.graph_embeddings import FastRPConfig


class TestSimilarityResult:
    """Test SimilarityResult dataclass."""

    def test_basic_result(self):
        """Test basic result creation."""
        result = SimilarityResult(
            qualified_name="module.function",
            similarity_score=0.95,
        )

        assert result.qualified_name == "module.function"
        assert result.similarity_score == 0.95
        assert result.file_path is None
        assert result.node_type is None
        assert result.name is None

    def test_full_result(self):
        """Test result with all fields."""
        result = SimilarityResult(
            qualified_name="module.MyClass.method",
            similarity_score=0.87,
            file_path="src/module.py",
            node_type="Function",
            name="method",
        )

        assert result.qualified_name == "module.MyClass.method"
        assert result.similarity_score == 0.87
        assert result.file_path == "src/module.py"
        assert result.node_type == "Function"
        assert result.name == "method"

    def test_to_dict(self):
        """Test conversion to dictionary."""
        result = SimilarityResult(
            qualified_name="module.func",
            similarity_score=0.9,
            file_path="src/mod.py",
            node_type="Function",
            name="func",
        )

        d = result.to_dict()

        assert d["qualified_name"] == "module.func"
        assert d["similarity_score"] == 0.9
        assert d["file_path"] == "src/mod.py"
        assert d["node_type"] == "Function"
        assert d["name"] == "func"

    def test_to_dict_with_none_values(self):
        """Test to_dict handles None values."""
        result = SimilarityResult(
            qualified_name="module.func",
            similarity_score=0.85,
        )

        d = result.to_dict()

        assert d["file_path"] is None
        assert d["node_type"] is None
        assert d["name"] is None


class TestStructuralSimilarityAnalyzer:
    """Test StructuralSimilarityAnalyzer class."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Neo4j client."""
        client = MagicMock()
        client.execute_query.return_value = [{"version": "2.5.0"}]
        return client

    @pytest.fixture
    def analyzer(self, mock_client):
        """Create an analyzer with mocked client."""
        return StructuralSimilarityAnalyzer(mock_client)

    def test_init_creates_embedder(self, mock_client):
        """Test that init creates a FastRPEmbedder."""
        analyzer = StructuralSimilarityAnalyzer(mock_client)

        assert analyzer.embedder is not None
        assert analyzer.client == mock_client

    def test_init_with_custom_config(self, mock_client):
        """Test initialization with custom config."""
        config = FastRPConfig(embedding_dimension=64)
        analyzer = StructuralSimilarityAnalyzer(mock_client, config=config)

        assert analyzer.config.embedding_dimension == 64

    def test_ensure_embeddings_generates_when_empty(self, mock_client):
        """Test that ensure_embeddings generates when none exist."""
        mock_client.execute_query.side_effect = [
            [{"version": "2.5.0"}],  # GDS check
            [{"total": 100, "embedded": 0}],  # Stats - no embeddings
            [{"label": "Function", "total": 100, "embedded": 0}],  # By label
            [],  # Drop graph
            [{  # Create projection
                "graphName": "fastrp-code-graph",
                "nodeCount": 100,
                "relationshipCount": 200,
                "projectMillis": 50,
            }],
            [{  # FastRP write
                "nodePropertiesWritten": 100,
                "computeMillis": 100,
                "writeMillis": 50,
            }],
            [{"total": 100, "embedded": 100}],  # Stats after
            [{"label": "Function", "total": 100, "embedded": 100}],
        ]

        analyzer = StructuralSimilarityAnalyzer(mock_client)
        stats = analyzer.ensure_embeddings()

        assert stats["nodes_with_embeddings"] == 100

    def test_ensure_embeddings_skips_when_exists(self, mock_client):
        """Test that ensure_embeddings skips when embeddings exist."""
        mock_client.execute_query.side_effect = [
            [{"version": "2.5.0"}],  # GDS check
            [{"total": 100, "embedded": 95}],  # Stats - has embeddings
            [{"label": "Function", "total": 100, "embedded": 95}],  # By label
        ]

        analyzer = StructuralSimilarityAnalyzer(mock_client)
        stats = analyzer.ensure_embeddings()

        # Should return existing stats without generating
        assert stats["nodes_with_embeddings"] == 95

    def test_ensure_embeddings_force(self, mock_client):
        """Test force regeneration of embeddings."""
        mock_client.execute_query.side_effect = [
            [{"version": "2.5.0"}],  # GDS check
            [{"total": 100, "embedded": 95}],  # Stats - has embeddings
            [{"label": "Function", "total": 100, "embedded": 95}],
            [],  # Drop graph
            [{  # Create projection
                "graphName": "fastrp-code-graph",
                "nodeCount": 100,
                "relationshipCount": 200,
                "projectMillis": 50,
            }],
            [{  # FastRP write
                "nodePropertiesWritten": 100,
                "computeMillis": 100,
                "writeMillis": 50,
            }],
            [{"total": 100, "embedded": 100}],  # Stats after
            [{"label": "Function", "total": 100, "embedded": 100}],
        ]

        analyzer = StructuralSimilarityAnalyzer(mock_client)
        stats = analyzer.ensure_embeddings(force=True)

        # Should have regenerated
        assert stats["nodes_with_embeddings"] == 100

    def test_find_similar_functions(self, mock_client):
        """Test finding similar functions."""
        mock_client.execute_query.side_effect = [
            [{"version": "2.5.0"}],  # GDS check
            [
                {
                    "qualified_name": "module.func_a",
                    "name": "func_a",
                    "file_path": "src/module.py",
                    "node_type": "Function",
                    "similarity": 0.95,
                },
                {
                    "qualified_name": "module.func_b",
                    "name": "func_b",
                    "file_path": "src/module.py",
                    "node_type": "Function",
                    "similarity": 0.85,
                },
            ],
        ]

        analyzer = StructuralSimilarityAnalyzer(mock_client)
        results = analyzer.find_similar_functions("module.target", top_k=5)

        assert len(results) == 2
        assert isinstance(results[0], SimilarityResult)
        assert results[0].qualified_name == "module.func_a"
        assert results[0].similarity_score == 0.95
        assert results[0].file_path == "src/module.py"
        assert results[0].node_type == "Function"

    def test_find_similar_classes(self, mock_client):
        """Test finding similar classes."""
        mock_client.execute_query.side_effect = [
            [{"version": "2.5.0"}],  # GDS check
            [
                {
                    "qualified_name": "module.ClassA",
                    "name": "ClassA",
                    "file_path": "src/module.py",
                    "node_type": "Class",
                    "similarity": 0.92,
                },
            ],
        ]

        analyzer = StructuralSimilarityAnalyzer(mock_client)
        results = analyzer.find_similar_classes("module.TargetClass", top_k=3)

        assert len(results) == 1
        assert results[0].node_type == "Class"

    def test_find_similar_generic(self, mock_client):
        """Test generic find_similar with custom labels."""
        mock_client.execute_query.side_effect = [
            [{"version": "2.5.0"}],  # GDS check
            [
                {
                    "qualified_name": "module.entity",
                    "name": "entity",
                    "file_path": "src/mod.py",
                    "node_type": "File",
                    "similarity": 0.88,
                },
            ],
        ]

        analyzer = StructuralSimilarityAnalyzer(mock_client)
        results = analyzer.find_similar(
            "module.target",
            top_k=10,
            node_labels=["File", "Module"],
        )

        assert len(results) == 1

    def test_find_potential_clones(self, mock_client):
        """Test finding potential code clones."""
        mock_client.execute_query.side_effect = [
            [{"version": "2.5.0"}],  # GDS check
            [
                {
                    "name_a": "module.func_a",
                    "short_name_a": "func_a",
                    "file_a": "src/module.py",
                    "name_b": "module.func_b",
                    "short_name_b": "func_b",
                    "file_b": "src/other.py",
                    "similarity": 0.98,
                },
                {
                    "name_a": "module.func_c",
                    "short_name_a": "func_c",
                    "file_a": "src/util.py",
                    "name_b": "module.func_d",
                    "short_name_b": "func_d",
                    "file_b": "src/helper.py",
                    "similarity": 0.96,
                },
            ],
        ]

        analyzer = StructuralSimilarityAnalyzer(mock_client)
        pairs = analyzer.find_potential_clones(threshold=0.95)

        assert len(pairs) == 2

        # Check first pair
        entity_a, entity_b = pairs[0]
        assert entity_a.qualified_name == "module.func_a"
        assert entity_b.qualified_name == "module.func_b"
        assert entity_a.similarity_score == 0.98
        assert entity_b.similarity_score == 0.98  # Same score for pair

    def test_find_potential_clones_custom_threshold(self, mock_client):
        """Test clone detection with custom threshold."""
        mock_client.execute_query.side_effect = [
            [{"version": "2.5.0"}],  # GDS check
            [],  # No clones at 0.99 threshold
        ]

        analyzer = StructuralSimilarityAnalyzer(mock_client)
        pairs = analyzer.find_potential_clones(threshold=0.99)

        assert len(pairs) == 0

    def test_find_potential_clones_custom_labels(self, mock_client):
        """Test clone detection for classes."""
        mock_client.execute_query.side_effect = [
            [{"version": "2.5.0"}],  # GDS check
            [
                {
                    "name_a": "module.ClassA",
                    "short_name_a": "ClassA",
                    "file_a": "src/a.py",
                    "name_b": "module.ClassB",
                    "short_name_b": "ClassB",
                    "file_b": "src/b.py",
                    "similarity": 0.97,
                },
            ],
        ]

        analyzer = StructuralSimilarityAnalyzer(mock_client)
        pairs = analyzer.find_potential_clones(
            threshold=0.95,
            node_labels=["Class"],
        )

        assert len(pairs) == 1
        entity_a, entity_b = pairs[0]
        assert entity_a.node_type == "Class"

    def test_find_isolated_entities(self, mock_client):
        """Test finding isolated entities."""
        mock_client.execute_query.side_effect = [
            [{"version": "2.5.0"}],  # GDS check
            [
                {
                    "name": "module.orphan",
                    "file_path": "src/orphan.py",
                    "avg_neighbor_similarity": 0.15,
                },
            ],
        ]

        analyzer = StructuralSimilarityAnalyzer(mock_client)
        results = analyzer.find_isolated_entities(threshold=0.2, limit=10)

        assert len(results) == 1
        assert results[0]["qualified_name"] == "module.orphan"
        assert results[0]["avg_neighbor_similarity"] == 0.15

    def test_get_stats(self, mock_client):
        """Test getting embedding stats."""
        mock_client.execute_query.side_effect = [
            [{"version": "2.5.0"}],  # GDS check
            [{"total": 100, "embedded": 90}],  # Stats
            [{"label": "Function", "total": 100, "embedded": 90}],  # By label
        ]

        analyzer = StructuralSimilarityAnalyzer(mock_client)
        stats = analyzer.get_stats()

        assert stats["total_nodes"] == 100
        assert stats["nodes_with_embeddings"] == 90
        assert stats["coverage_percent"] == 90.0


class TestSimilarityScoreRanges:
    """Test that similarity scores are in valid ranges."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Neo4j client."""
        client = MagicMock()
        client.execute_query.return_value = [{"version": "2.5.0"}]
        return client

    def test_similarity_scores_in_range(self, mock_client):
        """Test that returned similarity scores are between 0 and 1."""
        mock_client.execute_query.side_effect = [
            [{"version": "2.5.0"}],
            [
                {"qualified_name": "a", "name": "a", "file_path": "a.py",
                 "node_type": "Function", "similarity": 0.0},
                {"qualified_name": "b", "name": "b", "file_path": "b.py",
                 "node_type": "Function", "similarity": 0.5},
                {"qualified_name": "c", "name": "c", "file_path": "c.py",
                 "node_type": "Function", "similarity": 1.0},
            ],
        ]

        analyzer = StructuralSimilarityAnalyzer(mock_client)
        results = analyzer.find_similar_functions("target")

        for result in results:
            assert 0.0 <= result.similarity_score <= 1.0
