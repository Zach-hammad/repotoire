"""Unit tests for graph algorithms (REPO-152).

Tests for community detection (Louvain) and PageRank importance scoring.
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
    """Test Louvain community detection (REPO-152)."""

    def test_create_community_projection_success(self, graph_algorithms, mock_client):
        """Test successful community graph projection creation."""
        mock_client.execute_query.return_value = [{
            "graphName": "test-graph",
            "nodeCount": 100,
            "relationshipCount": 500
        }]

        result = graph_algorithms.create_community_projection("test-graph")

        assert result is True

    def test_create_community_projection_failure(self, graph_algorithms, mock_client):
        """Test community projection failure."""
        mock_client.execute_query.side_effect = Exception("Projection failed")

        result = graph_algorithms.create_community_projection("test-graph")

        assert result is False

    def test_calculate_communities_success(self, graph_algorithms, mock_client):
        """Test successful Louvain community calculation."""
        mock_client.execute_query.return_value = [{
            "nodePropertiesWritten": 100,
            "communityCount": 5,
            "modularity": 0.65,
            "computeMillis": 150
        }]

        result = graph_algorithms.calculate_communities("test-graph")

        assert result is not None
        assert result["communityCount"] == 5
        assert result["modularity"] == 0.65

    def test_calculate_communities_gds_unavailable(self, graph_algorithms, mock_client):
        """Test communities calculation when GDS is not available."""
        mock_client.execute_query.side_effect = Exception("GDS not available")

        result = graph_algorithms.calculate_communities("test-graph")

        assert result is None

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
    """Test PageRank importance scoring (REPO-152)."""

    def test_calculate_pagerank_success(self, graph_algorithms, mock_client):
        """Test successful PageRank calculation."""
        mock_client.execute_query.return_value = [{
            "nodePropertiesWritten": 100,
            "ranIterations": 20,
            "computeMillis": 50
        }]

        result = graph_algorithms.calculate_pagerank("test-graph")

        assert result is not None
        assert result["nodePropertiesWritten"] == 100

    def test_calculate_pagerank_gds_unavailable(self, graph_algorithms, mock_client):
        """Test PageRank calculation when GDS is not available."""
        mock_client.execute_query.side_effect = Exception("GDS not available")

        result = graph_algorithms.calculate_pagerank("test-graph")

        assert result is None

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
    """Test combined graph analysis (REPO-152)."""

    def test_run_full_analysis_gds_unavailable(self, graph_algorithms, mock_client):
        """Test full analysis when GDS is not available."""
        mock_client.execute_query.side_effect = Exception("GDS not installed")

        results = graph_algorithms.run_full_analysis()

        assert results["gds_available"] is False
        assert len(results["errors"]) > 0

    def test_run_full_analysis_success(self, graph_algorithms, mock_client):
        """Test successful full analysis."""
        # Mock GDS version check
        mock_client.execute_query.side_effect = [
            [{"version": "2.5.0"}],  # GDS available
            None,  # Drop existing projection
            [{"graphName": "proj", "nodeCount": 100, "relationshipCount": 500}],  # Create community projection
            [{"nodePropertiesWritten": 100, "communityCount": 5, "modularity": 0.65, "computeMillis": 100}],  # Louvain
            None,  # Cleanup community projection
            None,  # Drop existing projection
            [{"graphName": "proj", "nodeCount": 100, "relationshipCount": 500}],  # Create calls projection
            [{"nodePropertiesWritten": 100, "ranIterations": 20, "computeMillis": 50}],  # PageRank
            None,  # Cleanup calls projection
        ]

        results = graph_algorithms.run_full_analysis()

        assert results["gds_available"] is True
        assert results["communities"] is not None
        assert results["pagerank"] is not None

    def test_clear_caches(self, graph_algorithms):
        """Test cache clearing."""
        # Should not raise
        graph_algorithms.clear_caches()


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
