"""Unit tests for graph algorithms (REPO-152, REPO-192).

Tests for community detection (Leiden), PageRank, and other graph algorithms.
REPO-192: Updated tests for Rust-based implementations (no GDS required).
"""

from unittest.mock import Mock, patch

import pytest

from repotoire.detectors.graph_algorithms import GraphAlgorithms


@pytest.fixture
def mock_client():
    """Create a mock Neo4j client."""
    client = Mock()
    client.execute_query = Mock()
    return client


@pytest.fixture
def graph_algorithms(mock_client):
    """Create GraphAlgorithms instance with mock client."""
    return GraphAlgorithms(mock_client)


class TestGDSAvailability:
    """Test GDS availability checking."""

    def test_gds_available(self, graph_algorithms, mock_client):
        """Test when GDS is available."""
        mock_client.execute_query.return_value = [{"version": "2.5.0"}]

        assert graph_algorithms.check_gds_available() is True

    def test_gds_not_available(self, graph_algorithms, mock_client):
        """Test when GDS is not available."""
        mock_client.execute_query.side_effect = Exception("GDS not installed")

        assert graph_algorithms.check_gds_available() is False


class TestCommunityDetection:
    """Test Leiden community detection (REPO-152, REPO-192)."""

    def test_create_community_projection_success(self, graph_algorithms, mock_client):
        """Test successful community graph projection creation (legacy GDS method)."""
        mock_client.execute_query.return_value = [{
            "graphName": "test-graph",
            "nodeCount": 100,
            "relationshipCount": 500
        }]

        result = graph_algorithms.create_community_projection("test-graph")

        assert result is True

    def test_create_community_projection_failure(self, graph_algorithms, mock_client):
        """Test community projection failure (legacy GDS method)."""
        mock_client.execute_query.side_effect = Exception("Projection failed")

        result = graph_algorithms.create_community_projection("test-graph")

        assert result is False

    @patch('repotoire.detectors.graph_algorithms.graph_leiden')
    def test_calculate_communities_success(self, mock_leiden, graph_algorithms, mock_client):
        """Test successful Leiden community calculation using Rust."""
        # Mock the execute_query for _extract_edges (first call) and _write_property_to_nodes (second call)
        mock_client.execute_query.side_effect = [
            # First call: _extract_edges
            [{
                'node_list': [
                    {'neo_id': 1, 'name': 'func1'},
                    {'neo_id': 2, 'name': 'func2'},
                    {'neo_id': 3, 'name': 'func3'},
                ],
                'edges': [
                    {'src': 1, 'dst': 2},
                    {'src': 2, 'dst': 3},
                ]
            }],
            # Second call: _write_property_to_nodes
            [{'updated': 3}],
        ]

        # Mock Rust Leiden to return community assignments
        mock_leiden.return_value = [0, 0, 1]  # 2 communities

        result = graph_algorithms.calculate_communities()

        assert result is not None
        assert result["communityCount"] == 2
        assert result["nodePropertiesWritten"] == 3
        # Rust impl doesn't return modularity
        assert "modularity" in result

    def test_calculate_communities_no_edges(self, graph_algorithms, mock_client):
        """Test communities calculation with no edges."""
        mock_client.execute_query.return_value = [{'node_list': [], 'edges': []}]

        result = graph_algorithms.calculate_communities()

        assert result is not None
        assert result["communityCount"] == 0

    def test_get_class_community_span_with_data(self, graph_algorithms, mock_client):
        """Test getting community span when data is available."""
        mock_client.execute_query.return_value = [{"community_span": 3}]

        span = graph_algorithms.get_class_community_span("TestClass")

        assert span == 3

    def test_get_class_community_span_no_data(self, graph_algorithms, mock_client):
        """Test getting community span with fallback when no community data."""
        # First call returns no community IDs, triggers fallback
        mock_client.execute_query.side_effect = [
            [{"community_span": None}],  # No pre-computed communities
            [{"estimated_communities": 2}]  # Fallback estimation
        ]

        span = graph_algorithms.get_class_community_span("TestClass")

        assert span == 2

    def test_get_class_community_span_error(self, graph_algorithms, mock_client):
        """Test community span returns default on error."""
        mock_client.execute_query.side_effect = Exception("Query failed")

        span = graph_algorithms.get_class_community_span("TestClass")

        assert span == 1  # Default to cohesive

    def test_get_all_community_assignments(self, graph_algorithms, mock_client):
        """Test getting all community assignments."""
        mock_client.execute_query.return_value = [
            {"qualified_name": "module.ClassA", "community_id": 1},
            {"qualified_name": "module.ClassB", "community_id": 1},
            {"qualified_name": "module.ClassC", "community_id": 2},
        ]

        # Clear cache first
        graph_algorithms.clear_caches()

        assignments = graph_algorithms.get_all_community_assignments()

        assert len(assignments) == 3
        assert assignments["module.ClassA"] == 1
        assert assignments["module.ClassC"] == 2


class TestPageRank:
    """Test PageRank importance scoring (REPO-152, REPO-192)."""

    @patch('repotoire.detectors.graph_algorithms.graph_pagerank')
    def test_calculate_pagerank_success(self, mock_pagerank, graph_algorithms, mock_client):
        """Test successful PageRank calculation using Rust."""
        # Mock the execute_query for _extract_edges (first call) and _write_property_to_nodes (second call)
        mock_client.execute_query.side_effect = [
            # First call: _extract_edges
            [{
                'node_list': [
                    {'neo_id': 1, 'name': 'func1'},
                    {'neo_id': 2, 'name': 'func2'},
                    {'neo_id': 3, 'name': 'func3'},
                ],
                'edges': [
                    {'src': 1, 'dst': 2},
                    {'src': 2, 'dst': 3},
                ]
            }],
            # Second call: _write_property_to_nodes
            [{'updated': 3}],
        ]

        # Mock Rust PageRank to return scores
        mock_pagerank.return_value = [0.2, 0.5, 0.3]

        result = graph_algorithms.calculate_pagerank()

        assert result is not None
        assert result["nodePropertiesWritten"] == 3
        assert "computeMillis" in result

    def test_calculate_pagerank_no_edges(self, graph_algorithms, mock_client):
        """Test PageRank calculation with no edges."""
        mock_client.execute_query.return_value = [{'node_list': [], 'edges': []}]

        result = graph_algorithms.calculate_pagerank()

        assert result is not None
        assert result["nodePropertiesWritten"] == 0

    def test_get_class_importance_with_pagerank(self, graph_algorithms, mock_client):
        """Test getting class importance when PageRank data exists."""
        mock_client.execute_query.return_value = [{"importance": 0.75}]

        importance = graph_algorithms.get_class_importance("TestClass")

        assert importance == 0.75

    def test_get_class_importance_fallback(self, graph_algorithms, mock_client):
        """Test class importance fallback when no PageRank data."""
        # First call returns no PageRank, triggers fallback
        mock_client.execute_query.side_effect = [
            [{"importance": None}],  # No pre-computed PageRank
            [{"importance": 0.6}]  # Fallback from caller count
        ]

        importance = graph_algorithms.get_class_importance("TestClass")

        # Should use fallback
        assert 0.0 <= importance <= 1.0

    def test_get_class_importance_error(self, graph_algorithms, mock_client):
        """Test class importance returns default on error."""
        mock_client.execute_query.side_effect = Exception("Query failed")

        importance = graph_algorithms.get_class_importance("TestClass")

        assert importance == 0.5  # Neutral default

    def test_get_pagerank_statistics(self, graph_algorithms, mock_client):
        """Test getting PageRank statistics."""
        mock_client.execute_query.return_value = [{
            "min_pagerank": 0.15,
            "max_pagerank": 2.5,
            "avg_pagerank": 0.85,
            "median_pagerank": 0.7,
            "p90_pagerank": 1.5,
            "total_functions": 100
        }]

        stats = graph_algorithms.get_pagerank_statistics()

        assert stats is not None
        assert stats["avg_pagerank"] == 0.85
        assert stats["total_functions"] == 100


class TestCombinedAnalysis:
    """Test combined graph analysis (REPO-152, REPO-192)."""

    @patch('repotoire.detectors.graph_algorithms.graph_find_sccs')
    @patch('repotoire.detectors.graph_algorithms.graph_betweenness_centrality')
    @patch('repotoire.detectors.graph_algorithms.graph_pagerank')
    @patch('repotoire.detectors.graph_algorithms.graph_leiden')
    def test_run_full_analysis_success(
        self, mock_leiden, mock_pagerank, mock_betweenness, mock_sccs,
        graph_algorithms, mock_client
    ):
        """Test successful full analysis using Rust algorithms."""
        # Helper: each algorithm calls _extract_edges then _write_property_to_nodes
        edge_data = [{
            'node_list': [
                {'neo_id': 1, 'name': 'entity1'},
                {'neo_id': 2, 'name': 'entity2'},
            ],
            'edges': [{'src': 1, 'dst': 2}]
        }]
        write_result = [{'updated': 2}]

        # Mock execute_query for all algorithm calls (4 algos x 2 calls each)
        mock_client.execute_query.side_effect = [
            edge_data, write_result,  # communities
            edge_data, write_result,  # pagerank
            edge_data, write_result,  # betweenness
            edge_data, write_result,  # scc
        ]

        # Mock Rust algorithms
        mock_leiden.return_value = [0, 0]
        mock_pagerank.return_value = [0.5, 0.5]
        mock_betweenness.return_value = [1.0, 0.0]
        mock_sccs.return_value = [[0, 1]]

        results = graph_algorithms.run_full_analysis()

        assert results["rust_algorithms"] is True
        assert results["communities"] is not None
        assert results["pagerank"] is not None
        assert results["betweenness"] is not None
        assert results["scc"] is not None

    def test_run_full_analysis_handles_errors(self, graph_algorithms, mock_client):
        """Test full analysis handles errors gracefully but still returns structure."""
        # All algorithms will fail with empty results
        mock_client.execute_query.return_value = [{'node_list': [], 'edges': []}]

        results = graph_algorithms.run_full_analysis()

        # Should return structure even when algos return None/empty results
        assert results["rust_algorithms"] is True
        # Note: algorithms return {communityCount: 0} etc for empty graphs, not errors

    def test_clear_caches(self, graph_algorithms):
        """Test cache clearing."""
        # Should not raise
        graph_algorithms.clear_caches()


class TestHarmonicCentrality:
    """Test Harmonic Centrality calculation (REPO-198)."""

    @patch('repotoire.detectors.graph_algorithms.graph_harmonic_centrality')
    def test_calculate_harmonic_centrality_success(self, mock_harmonic, graph_algorithms, mock_client):
        """Test successful harmonic centrality calculation using Rust."""
        # Mock the execute_query for _extract_edges and _write_property_to_nodes
        mock_client.execute_query.side_effect = [
            # First call: _extract_edges
            [{
                'node_list': [
                    {'neo_id': 1, 'name': 'func1'},
                    {'neo_id': 2, 'name': 'func2'},
                    {'neo_id': 3, 'name': 'func3'},
                ],
                'edges': [
                    {'src': 1, 'dst': 2},
                    {'src': 2, 'dst': 3},
                ]
            }],
            # Second call: _write_property_to_nodes
            [{'updated': 3}],
        ]

        # Mock Rust harmonic centrality to return scores
        # In a line graph 0-1-2: middle node (1) has highest harmonic centrality
        mock_harmonic.return_value = [0.5, 1.0, 0.5]

        result = graph_algorithms.calculate_harmonic_centrality()

        assert result is not None
        assert result["nodePropertiesWritten"] == 3
        assert "computeMillis" in result

    def test_calculate_harmonic_centrality_no_edges(self, graph_algorithms, mock_client):
        """Test harmonic centrality calculation with no edges."""
        mock_client.execute_query.return_value = [{'node_list': [], 'edges': []}]

        result = graph_algorithms.calculate_harmonic_centrality()

        assert result is not None
        assert result["nodePropertiesWritten"] == 0


class TestValidation:
    """Test input validation for security."""

    def test_invalid_projection_name(self, graph_algorithms):
        """Test that invalid projection names are rejected."""
        from repotoire.validation import ValidationError

        with pytest.raises(ValidationError):
            graph_algorithms.create_community_projection("'; DROP DATABASE;--")

    def test_invalid_property_name(self, graph_algorithms):
        """Test that invalid property names are rejected."""
        from repotoire.validation import ValidationError

        with pytest.raises(ValidationError):
            graph_algorithms.calculate_communities("test", "'; injection;--")
