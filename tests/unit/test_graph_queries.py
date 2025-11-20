"""Unit tests for graph query utilities."""

import pytest
from unittest.mock import Mock, MagicMock
from repotoire.graph.queries.patterns import CypherPatterns
from repotoire.graph.queries.builders import QueryBuilder, DetectorQueryBuilder
from repotoire.graph.queries.traversal import GraphTraversal


class TestQueryBuilder:
    """Test QueryBuilder fluent API."""

    def test_basic_query(self):
        """Test basic query building."""
        builder = QueryBuilder()
        query, params = (
            builder
            .match("(n:File)")
            .where("n.language = $lang")
            .return_("n.filePath AS path")
            .build({"lang": "python"})
        )

        assert "MATCH (n:File)" in query
        assert "WHERE (n.language = $lang)" in query
        assert "RETURN n.filePath AS path" in query
        assert params == {"lang": "python"}

    def test_query_chaining(self):
        """Test method chaining."""
        builder = QueryBuilder()
        query, params = (
            builder
            .match("(n:File)")
            .optional_match("(n)-[:IMPORTS]->(m:Module)")
            .where("n.loc > $min_loc")
            .where("m.is_external = $external")
            .with_("n, count(m) AS imports")
            .return_("n.name, imports")
            .order_by("imports DESC")
            .limit(10)
            .skip(5)
            .build({"min_loc": 100, "external": True})
        )

        assert "MATCH (n:File)" in query
        assert "OPTIONAL MATCH (n)-[:IMPORTS]->(m:Module)" in query
        assert "WHERE (n.loc > $min_loc) AND (m.is_external = $external)" in query
        assert "WITH n, count(m) AS imports" in query
        assert "RETURN n.name, imports" in query
        assert "ORDER BY imports DESC" in query
        assert "SKIP 5" in query
        assert "LIMIT 10" in query
        assert params == {"min_loc": 100, "external": True}

    def test_multiple_where_clauses(self):
        """Test that multiple where() calls are AND-ed together."""
        builder = QueryBuilder()
        query, _ = (
            builder
            .match("(n:Function)")
            .where("n.complexity > $min_complexity")
            .where("n.loc > $min_loc")
            .return_("n")
            .build({"min_complexity": 10, "min_loc": 50})
        )

        assert "WHERE (n.complexity > $min_complexity) AND (n.loc > $min_loc)" in query

    def test_empty_parameters(self):
        """Test query building without parameters."""
        builder = QueryBuilder()
        query, params = (
            builder
            .match("(n:File)")
            .return_("n.name")
            .build()
        )

        assert "MATCH (n:File)" in query
        assert "RETURN n.name" in query
        assert params == {}


class TestDetectorQueryBuilder:
    """Test DetectorQueryBuilder specialized patterns."""

    def test_find_nodes_with_relationship_count_outgoing(self):
        """Test finding nodes by outgoing relationship count."""
        query, params = DetectorQueryBuilder.find_nodes_with_relationship_count(
            node_label="File",
            relationship_type="IMPORTS",
            direction="OUTGOING",
            min_count=5,
            max_count=20,
            limit=50
        )

        assert "MATCH (n:File)" in query
        assert "OPTIONAL MATCH (n)-[:IMPORTS]->" in query
        assert "rel_count >= $min_count" in query
        assert "rel_count <= $max_count" in query
        assert "LIMIT 50" in query
        assert params == {"min_count": 5, "max_count": 20}

    def test_find_nodes_with_relationship_count_incoming(self):
        """Test finding nodes by incoming relationship count."""
        query, params = DetectorQueryBuilder.find_nodes_with_relationship_count(
            node_label="Function",
            relationship_type="CALLS",
            direction="INCOMING",
            min_count=10,
            limit=100
        )

        assert "MATCH (n:Function)" in query
        assert "OPTIONAL MATCH (n)<-[:CALLS]-" in query
        assert "rel_count >= $min_count" in query
        assert "rel_count <= $max_count" not in query
        assert params == {"min_count": 10}

    def test_find_nodes_by_property(self):
        """Test finding nodes by property value."""
        query, params = DetectorQueryBuilder.find_nodes_by_property(
            node_label="Function",
            property_name="complexity",
            operator=">=",
            value=20,
            limit=50
        )

        assert "MATCH (n:Function)" in query
        assert "n.complexity >= $value" in query
        assert "LIMIT 50" in query
        assert params == {"value": 20}

    def test_find_nodes_without_relationship(self):
        """Test finding nodes without specific relationships (dead code pattern)."""
        query, params = DetectorQueryBuilder.find_nodes_without_relationship(
            node_label="Function",
            relationship_type="CALLS",
            direction="INCOMING",
            limit=100
        )

        assert "MATCH (n:Function)" in query
        assert "NOT (n)<-[:CALLS]-()" in query
        assert "LIMIT 100" in query
        assert params == {}

    def test_aggregate_by_property(self):
        """Test aggregating nodes by property."""
        query, params = DetectorQueryBuilder.aggregate_by_property(
            node_label="File",
            group_by_property="language",
            aggregate_property="loc",
            aggregate_function="sum",
            limit=10
        )

        assert "MATCH (n:File)" in query
        assert "n.language AS group_key" in query
        assert "sum(n.loc) AS agg_value" in query
        assert "ORDER BY agg_value DESC" in query
        assert "LIMIT 10" in query


class TestCypherPatterns:
    """Test CypherPatterns common graph analysis patterns."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Neo4j client."""
        client = Mock()
        client.execute_query = Mock()
        return client

    @pytest.fixture
    def patterns(self, mock_client):
        """Create CypherPatterns instance with mock client."""
        return CypherPatterns(mock_client)

    def test_find_cycles(self, patterns, mock_client):
        """Test cycle detection query."""
        mock_client.execute_query.return_value = [
            {"nodes": ["file_a.py", "file_b.py", "file_c.py"], "length": 3}
        ]

        result = patterns.find_cycles(
            node_label="File",
            relationship_type="IMPORTS",
            min_length=2,
            max_length=10,
            limit=50
        )

        # Check query was called
        assert mock_client.execute_query.called
        query = mock_client.execute_query.call_args[0][0]
        params = mock_client.execute_query.call_args[1]["parameters"]

        assert "MATCH (n1:File)" in query
        assert "MATCH (n2:File)" in query
        # Check for parameterized query (not hardcoded values)
        assert "shortestPath((n1)-[:IMPORTS*$min_length..$max_length]->(n2))" in query
        assert "shortestPath((n2)-[:IMPORTS*$min_length..$max_length]->(n1))" in query
        assert "LIMIT $limit" in query

        # Verify parameters
        assert params["min_length"] == 2
        assert params["max_length"] == 10
        assert params["limit"] == 50

        # Check result
        assert len(result) == 1
        assert result[0]["length"] == 3
        assert len(result[0]["nodes"]) == 3

    def test_calculate_degree_centrality(self, patterns, mock_client):
        """Test degree centrality calculation."""
        mock_client.execute_query.return_value = [
            {"node": "node1", "name": "file_a.py", "file_path": "/path/file_a.py", "degree": 15},
            {"node": "node2", "name": "file_b.py", "file_path": "/path/file_b.py", "degree": 10},
        ]

        result = patterns.calculate_degree_centrality(
            node_label="File",
            relationship_type="IMPORTS",
            direction="OUTGOING"
        )

        # Check query
        query = mock_client.execute_query.call_args[0][0]
        assert "MATCH (n:File)" in query
        assert "OPTIONAL MATCH (n)-[:IMPORTS]->" in query
        assert "count(connected) AS degree" in query

        # Check result
        assert len(result) == 2
        assert result[0]["degree"] == 15
        assert result[1]["degree"] == 10

    def test_find_shortest_path(self, patterns, mock_client):
        """Test shortest path finding."""
        mock_client.execute_query.return_value = [
            {
                "path": Mock(),
                "length": 3,
                "nodes": [
                    {"id": "node1", "name": "file_a.py"},
                    {"id": "node2", "name": "file_b.py"},
                    {"id": "node3", "name": "file_c.py"},
                ]
            }
        ]

        result = patterns.find_shortest_path(
            source_id="node1",
            target_id="node3",
            relationship_type="CALLS",
            max_depth=5
        )

        # Check query
        query = mock_client.execute_query.call_args[0][0]
        params = mock_client.execute_query.call_args[1]["parameters"]

        # Check for parameterized query (not hardcoded max_depth)
        assert "shortestPath((source)-[:CALLS*1..$max_depth]-(target))" in query
        assert params == {"source_id": "node1", "target_id": "node3", "max_depth": 5}

        # Check result
        assert result is not None
        assert result["length"] == 3
        assert len(result["nodes"]) == 3

    def test_find_shortest_path_not_found(self, patterns, mock_client):
        """Test shortest path when no path exists."""
        mock_client.execute_query.return_value = []

        result = patterns.find_shortest_path(
            source_id="node1",
            target_id="node3",
        )

        assert result is None

    def test_find_bottlenecks(self, patterns, mock_client):
        """Test bottleneck node detection."""
        mock_client.execute_query.return_value = [
            {
                "node": "node1",
                "name": "middleware.py",
                "file_path": "/path/middleware.py",
                "in_degree": 12,
                "out_degree": 8,
                "degree": 20
            }
        ]

        result = patterns.find_bottlenecks(
            node_label="File",
            relationship_type="IMPORTS",
            threshold=10
        )

        # Check query
        query = mock_client.execute_query.call_args[0][0]
        params = mock_client.execute_query.call_args[1]["parameters"]

        assert "count(DISTINCT out) AS out_degree" in query
        assert "count(DISTINCT in) AS in_degree" in query
        # Check for parameterized query (not hardcoded threshold)
        assert "WHERE total_degree >= $threshold" in query
        assert params["threshold"] == 10

        # Check result
        assert len(result) == 1
        assert result[0]["degree"] == 20
        assert result[0]["in_degree"] == 12
        assert result[0]["out_degree"] == 8

    def test_calculate_clustering_coefficient(self, patterns, mock_client):
        """Test clustering coefficient calculation."""
        mock_client.execute_query.return_value = [
            {"avg_clustering_coefficient": 0.45}
        ]

        result = patterns.calculate_clustering_coefficient(
            node_label="File",
            relationship_type="IMPORTS"
        )

        # Check query
        query = mock_client.execute_query.call_args[0][0]
        assert "triangles / possible" in query
        assert "avg(triangles / possible) AS avg_clustering_coefficient" in query

        # Check result
        assert result == 0.45

    def test_calculate_clustering_coefficient_empty(self, patterns, mock_client):
        """Test clustering coefficient with no results."""
        mock_client.execute_query.return_value = [{"avg_clustering_coefficient": None}]

        result = patterns.calculate_clustering_coefficient()

        assert result == 0.0


class TestGraphTraversal:
    """Test GraphTraversal BFS/DFS utilities."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Neo4j client."""
        client = Mock()
        client.execute_query = Mock()
        return client

    @pytest.fixture
    def traversal(self, mock_client):
        """Create GraphTraversal instance with mock client."""
        return GraphTraversal(mock_client)

    def test_bfs_basic(self, traversal, mock_client):
        """Test basic BFS traversal."""
        # Mock _get_node_properties
        traversal._get_node_properties = Mock(side_effect=lambda node_id: {
            "id": node_id,
            "labels": ["File"],
            "name": f"file_{node_id[-1]}.py"
        })

        # Mock _get_neighbors
        traversal._get_neighbors = Mock(side_effect=lambda node_id, rel, dir: {
            "node1": ["node2", "node3"],
            "node2": ["node4"],
            "node3": [],
            "node4": []
        }.get(node_id, []))

        result = traversal.bfs(
            start_node_id="node1",
            relationship_type="IMPORTS",
            direction="OUTGOING"
        )

        # Check all nodes visited in BFS order
        assert len(result) == 4
        assert result[0]["id"] == "node1"
        assert result[0]["depth"] == 0

        # Verify BFS order (breadth-first)
        depths = [node["depth"] for node in result]
        assert sorted(depths) == depths  # Depths should be non-decreasing

    def test_bfs_with_max_depth(self, traversal, mock_client):
        """Test BFS with max depth limit."""
        traversal._get_node_properties = Mock(side_effect=lambda node_id: {
            "id": node_id,
            "labels": ["File"],
            "name": f"file_{node_id[-1]}.py"
        })

        traversal._get_neighbors = Mock(side_effect=lambda node_id, rel, dir: {
            "node1": ["node2"],
            "node2": ["node3"],
            "node3": ["node4"]
        }.get(node_id, []))

        result = traversal.bfs(
            start_node_id="node1",
            relationship_type="IMPORTS",
            max_depth=1
        )

        # Should only get nodes at depth 0 and 1
        assert len(result) == 2
        assert all(node["depth"] <= 1 for node in result)

    def test_bfs_with_filter(self, traversal, mock_client):
        """Test BFS with custom filter function."""
        traversal._get_node_properties = Mock(side_effect=lambda node_id: {
            "id": node_id,
            "labels": ["File"],
            "name": f"file_{node_id[-1]}.py",
            "language": "python" if int(node_id[-1]) % 2 == 0 else "javascript"
        })

        traversal._get_neighbors = Mock(side_effect=lambda node_id, rel, dir: {
            "node1": ["node2", "node3"],
            "node2": ["node4"],
            "node3": [],
            "node4": []
        }.get(node_id, []))

        # Only include Python files
        result = traversal.bfs(
            start_node_id="node1",
            relationship_type="IMPORTS",
            filter_fn=lambda n: n.get("language") == "python"
        )

        # Should only get even-numbered nodes (Python files)
        assert all(node.get("language") == "python" for node in result)

    def test_dfs_basic(self, traversal, mock_client):
        """Test basic DFS traversal."""
        traversal._get_node_properties = Mock(side_effect=lambda node_id: {
            "id": node_id,
            "labels": ["File"],
            "name": f"file_{node_id[-1]}.py"
        })

        traversal._get_neighbors = Mock(side_effect=lambda node_id, rel, dir: {
            "node1": ["node2", "node3"],
            "node2": ["node4"],
            "node3": ["node5"],
            "node4": [],
            "node5": []
        }.get(node_id, []))

        result = traversal.dfs(
            start_node_id="node1",
            relationship_type="IMPORTS",
            direction="OUTGOING"
        )

        # Check all nodes visited
        assert len(result) == 5
        assert result[0]["id"] == "node1"

    def test_find_path_with_condition(self, traversal, mock_client):
        """Test finding path with custom condition."""
        traversal._get_node_properties = Mock(side_effect=lambda node_id: {
            "id": node_id,
            "labels": ["File"],
            "name": f"test_{node_id[-1]}.py" if node_id == "node4" else f"file_{node_id[-1]}.py"
        })

        traversal._get_neighbors = Mock(side_effect=lambda node_id, rel, dir: {
            "node1": ["node2", "node3"],
            "node2": ["node4"],
            "node3": ["node5"],
            "node4": [],
            "node5": []
        }.get(node_id, []))

        # Find path to test file
        result = traversal.find_path_with_condition(
            start_node_id="node1",
            condition_fn=lambda n: n.get("name", "").startswith("test_"),
            relationship_type="IMPORTS",
            max_depth=5
        )

        # Should find path: node1 -> node2 -> node4
        assert result is not None
        assert len(result) == 3
        assert result[-1]["name"] == "test_4.py"

    def test_find_path_with_condition_not_found(self, traversal, mock_client):
        """Test find_path_with_condition when no path exists."""
        traversal._get_node_properties = Mock(side_effect=lambda node_id: {
            "id": node_id,
            "labels": ["File"],
            "name": f"file_{node_id[-1]}.py"
        })

        traversal._get_neighbors = Mock(side_effect=lambda node_id, rel, dir: {
            "node1": ["node2"],
            "node2": []
        }.get(node_id, []))

        result = traversal.find_path_with_condition(
            start_node_id="node1",
            condition_fn=lambda n: n.get("name") == "nonexistent.py",
            relationship_type="IMPORTS",
            max_depth=5
        )

        assert result is None

    def test_get_subgraph(self, traversal, mock_client):
        """Test subgraph extraction."""
        traversal._get_node_properties = Mock(side_effect=lambda node_id: {
            "id": node_id,
            "labels": ["File"],
            "name": f"file_{node_id[-1]}.py"
        })

        traversal._get_neighbors = Mock(side_effect=lambda node_id, rel, dir: {
            "node1": ["node2"],
            "node2": ["node3"],
            "node3": []
        }.get(node_id, []))

        traversal._get_node_relationships = Mock(return_value=[])

        result = traversal.get_subgraph(
            start_node_ids=["node1"],
            relationship_type="IMPORTS",
            max_depth=2
        )

        assert "nodes" in result
        assert "relationships" in result
        assert "node_count" in result
        assert "relationship_count" in result
        assert result["node_count"] >= 1

    def test_direction_patterns(self, traversal, mock_client):
        """Test different relationship direction patterns."""
        traversal._get_node_properties = Mock(return_value={"id": "node1", "labels": ["File"]})
        traversal._get_neighbors = Mock(return_value=[])

        # Test OUTGOING
        traversal.bfs("node1", "IMPORTS", direction="OUTGOING", max_depth=1)
        query = traversal._get_neighbors.call_args[0]
        assert query[2] == "OUTGOING"

        # Test INCOMING
        traversal.bfs("node1", "IMPORTS", direction="INCOMING", max_depth=1)
        query = traversal._get_neighbors.call_args[0]
        assert query[2] == "INCOMING"

        # Test BOTH
        traversal.bfs("node1", "IMPORTS", direction="BOTH", max_depth=1)
        query = traversal._get_neighbors.call_args[0]
        assert query[2] == "BOTH"
