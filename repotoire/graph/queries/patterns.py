"""Common Cypher query patterns for graph analysis.

This module provides reusable Cypher patterns for detecting common graph structures
and calculating graph metrics used by detectors.
"""

from typing import List, Dict, Any, Optional
from repotoire.graph.client import Neo4jClient
from repotoire.validation import validate_identifier


class CypherPatterns:
    """Reusable Cypher patterns for common graph analysis tasks."""

    def __init__(self, client: Neo4jClient):
        """Initialize patterns helper.

        Args:
            client: Neo4j client instance
        """
        self.client = client

    def find_cycles(
        self,
        node_label: str = "File",
        relationship_type: str = "IMPORTS",
        min_length: int = 2,
        max_length: int = 15,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Find circular dependencies in the graph.

        Uses shortest path queries to detect cycles between nodes.

        Args:
            node_label: Label of nodes to check for cycles
            relationship_type: Relationship type to traverse
            min_length: Minimum cycle length (default: 2)
            max_length: Maximum cycle length to search (default: 15)
            limit: Maximum number of cycles to return

        Returns:
            List of cycle dictionaries with 'nodes' and 'length' keys

        Example:
            >>> patterns = CypherPatterns(client)
            >>> cycles = patterns.find_cycles("File", "IMPORTS")
            >>> for cycle in cycles:
            ...     print(f"Cycle of length {cycle['length']}: {cycle['nodes']}")
        """
        # Validate inputs to prevent Cypher injection
        validated_label = validate_identifier(node_label, "node label")
        validated_rel_type = validate_identifier(relationship_type, "relationship type")

        # Use parameterized query for numeric values
        query = f"""
        MATCH (n1:{validated_label})
        MATCH (n2:{validated_label})
        WHERE elementId(n1) < elementId(n2) AND n1 <> n2
        MATCH path = shortestPath((n1)-[:{validated_rel_type}*$min_length..$max_length]->(n2))
        MATCH cyclePath = shortestPath((n2)-[:{validated_rel_type}*$min_length..$max_length]->(n1))
        WITH DISTINCT [node IN nodes(path) + nodes(cyclePath) WHERE node:{validated_label} | node.filePath] AS cycle
        WHERE size(cycle) > 1
        RETURN cycle AS nodes, size(cycle) AS length
        ORDER BY length DESC
        LIMIT $limit
        """
        results = self.client.execute_query(query, parameters={
            "min_length": min_length,
            "max_length": max_length,
            "limit": limit
        })
        return [{"nodes": r["nodes"], "length": r["length"]} for r in results]

    def calculate_degree_centrality(
        self,
        node_label: str = "File",
        relationship_type: str = "IMPORTS",
        direction: str = "OUTGOING",
    ) -> List[Dict[str, Any]]:
        """Calculate degree centrality for nodes.

        Degree centrality measures how many connections a node has.

        Args:
            node_label: Label of nodes to analyze
            relationship_type: Relationship type to count
            direction: "OUTGOING", "INCOMING", or "BOTH"

        Returns:
            List of dictionaries with 'node', 'name', and 'degree' keys

        Example:
            >>> patterns = CypherPatterns(client)
            >>> centrality = patterns.calculate_degree_centrality("File", "IMPORTS", "OUTGOING")
            >>> for node in centrality[:10]:
            ...     print(f"{node['name']}: {node['degree']} outgoing connections")
        """
        # Validate inputs to prevent Cypher injection
        validated_label = validate_identifier(node_label, "node label")
        validated_rel_type = validate_identifier(relationship_type, "relationship type")

        # Validate direction parameter
        valid_directions = {"OUTGOING", "INCOMING", "BOTH"}
        if direction not in valid_directions:
            from repotoire.validation import ValidationError
            raise ValidationError(
                f"Invalid direction: {direction}",
                f"Direction must be one of: {', '.join(valid_directions)}"
            )

        if direction == "OUTGOING":
            rel_pattern = f"-[:{validated_rel_type}]->"
        elif direction == "INCOMING":
            rel_pattern = f"<-[:{validated_rel_type}]-"
        else:  # BOTH
            rel_pattern = f"-[:{validated_rel_type}]-"

        query = f"""
        MATCH (n:{validated_label})
        OPTIONAL MATCH (n){rel_pattern}(connected)
        WITH n, count(connected) AS degree
        RETURN elementId(n) AS node,
               n.name AS name,
               n.filePath AS file_path,
               degree
        ORDER BY degree DESC
        """
        results = self.client.execute_query(query)
        return [
            {
                "node": r["node"],
                "name": r["name"],
                "file_path": r.get("file_path"),
                "degree": r["degree"],
            }
            for r in results
        ]

    def find_connected_components(
        self,
        node_label: str = "File",
        relationship_type: str = "IMPORTS",
    ) -> List[Dict[str, Any]]:
        """Find connected components (groups of connected nodes).

        Args:
            node_label: Label of nodes to analyze
            relationship_type: Relationship type to traverse

        Returns:
            List of dictionaries with 'component_id' and 'nodes' keys

        Example:
            >>> patterns = CypherPatterns(client)
            >>> components = patterns.find_connected_components("File", "IMPORTS")
            >>> print(f"Found {len(components)} connected components")
        """
        # Validate inputs to prevent Cypher injection
        validated_label = validate_identifier(node_label, "node label")
        validated_rel_type = validate_identifier(relationship_type, "relationship type")

        # Simple connected components using APOC or manual approach
        # For MVP, we'll use a simple approach: find all nodes reachable from each node
        query = f"""
        MATCH (n:{validated_label})
        OPTIONAL MATCH path = (n)-[:{validated_rel_type}*]-(connected:{validated_label})
        WITH n, collect(DISTINCT connected) + [n] AS component
        RETURN elementId(n) AS component_id,
               [node IN component | node.filePath] AS nodes,
               size(component) AS size
        ORDER BY size DESC
        """
        results = self.client.execute_query(query)

        # Deduplicate components (same nodes in different order)
        seen_components = set()
        unique_components = []

        for r in results:
            nodes_tuple = tuple(sorted(r["nodes"]))
            if nodes_tuple not in seen_components:
                seen_components.add(nodes_tuple)
                unique_components.append({
                    "component_id": r["component_id"],
                    "nodes": r["nodes"],
                    "size": r["size"],
                })

        return unique_components

    def find_shortest_path(
        self,
        source_id: str,
        target_id: str,
        relationship_type: Optional[str] = None,
        max_depth: int = 10,
    ) -> Optional[Dict[str, Any]]:
        """Find shortest path between two nodes.

        Args:
            source_id: elementId of source node
            target_id: elementId of target node
            relationship_type: Optional relationship type to traverse
            max_depth: Maximum path length to search

        Returns:
            Dictionary with 'path', 'length', and 'nodes' keys, or None if no path

        Example:
            >>> patterns = CypherPatterns(client)
            >>> path = patterns.find_shortest_path(node1_id, node2_id, "CALLS")
            >>> if path:
            ...     print(f"Path length: {path['length']}")
        """
        # Validate relationship_type if provided
        if relationship_type:
            validated_rel_type = validate_identifier(relationship_type, "relationship type")
            rel_filter = f":{validated_rel_type}"
        else:
            rel_filter = ""

        # Use parameterized query for max_depth
        query = f"""
        MATCH (source), (target)
        WHERE elementId(source) = $source_id AND elementId(target) = $target_id
        MATCH path = shortestPath((source)-[{rel_filter}*1..$max_depth]-(target))
        RETURN path,
               length(path) AS length,
               [node IN nodes(path) | {{id: elementId(node), name: node.name}}] AS nodes
        """
        results = self.client.execute_query(
            query,
            parameters={
                "source_id": source_id,
                "target_id": target_id,
                "max_depth": max_depth
            }
        )

        if results:
            r = results[0]
            return {
                "path": r["path"],
                "length": r["length"],
                "nodes": r["nodes"],
            }
        return None

    def find_all_paths(
        self,
        source_id: str,
        target_id: str,
        relationship_type: Optional[str] = None,
        max_depth: int = 5,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Find all paths between two nodes up to a maximum depth.

        Args:
            source_id: elementId of source node
            target_id: elementId of target node
            relationship_type: Optional relationship type to traverse
            max_depth: Maximum path length to search
            limit: Maximum number of paths to return

        Returns:
            List of path dictionaries with 'length' and 'nodes' keys

        Example:
            >>> patterns = CypherPatterns(client)
            >>> paths = patterns.find_all_paths(node1_id, node2_id, "CALLS", max_depth=3)
            >>> print(f"Found {len(paths)} paths")
        """
        # Validate relationship_type if provided
        if relationship_type:
            validated_rel_type = validate_identifier(relationship_type, "relationship type")
            rel_filter = f":{validated_rel_type}"
        else:
            rel_filter = ""

        # Use parameterized query for numeric values
        query = f"""
        MATCH (source), (target)
        WHERE elementId(source) = $source_id AND elementId(target) = $target_id
        MATCH path = (source)-[{rel_filter}*1..$max_depth]-(target)
        RETURN length(path) AS length,
               [node IN nodes(path) | {{id: elementId(node), name: node.name}}] AS nodes
        ORDER BY length
        LIMIT $limit
        """
        results = self.client.execute_query(
            query,
            parameters={
                "source_id": source_id,
                "target_id": target_id,
                "max_depth": max_depth,
                "limit": limit
            }
        )

        return [{"length": r["length"], "nodes": r["nodes"]} for r in results]

    def find_bottlenecks(
        self,
        node_label: str = "File",
        relationship_type: str = "IMPORTS",
        threshold: int = 10,
    ) -> List[Dict[str, Any]]:
        """Find bottleneck nodes (high betweenness centrality).

        Bottleneck nodes are those that appear in many paths between other nodes.

        Args:
            node_label: Label of nodes to analyze
            relationship_type: Relationship type to traverse
            threshold: Minimum degree to be considered a bottleneck

        Returns:
            List of bottleneck node dictionaries

        Example:
            >>> patterns = CypherPatterns(client)
            >>> bottlenecks = patterns.find_bottlenecks("File", "IMPORTS", threshold=5)
            >>> for node in bottlenecks:
            ...     print(f"Bottleneck: {node['name']} (degree: {node['degree']})")
        """
        # Validate inputs to prevent Cypher injection
        validated_label = validate_identifier(node_label, "node label")
        validated_rel_type = validate_identifier(relationship_type, "relationship type")

        # Simple approximation: nodes with high combined in/out degree
        query = f"""
        MATCH (n:{validated_label})
        OPTIONAL MATCH (n)-[:{validated_rel_type}]->(out)
        OPTIONAL MATCH (n)<-[:{validated_rel_type}]-(in)
        WITH n,
             count(DISTINCT out) AS out_degree,
             count(DISTINCT in) AS in_degree,
             count(DISTINCT out) + count(DISTINCT in) AS total_degree
        WHERE total_degree >= $threshold
        RETURN elementId(n) AS node,
               n.name AS name,
               n.filePath AS file_path,
               in_degree,
               out_degree,
               total_degree AS degree
        ORDER BY total_degree DESC
        """
        results = self.client.execute_query(query, parameters={"threshold": threshold})
        return [
            {
                "node": r["node"],
                "name": r["name"],
                "file_path": r.get("file_path"),
                "in_degree": r["in_degree"],
                "out_degree": r["out_degree"],
                "degree": r["degree"],
            }
            for r in results
        ]

    def calculate_clustering_coefficient(
        self,
        node_label: str = "File",
        relationship_type: str = "IMPORTS",
    ) -> float:
        """Calculate average clustering coefficient for the graph.

        Clustering coefficient measures how connected a node's neighbors are to each other.

        Args:
            node_label: Label of nodes to analyze
            relationship_type: Relationship type to traverse

        Returns:
            Average clustering coefficient (0.0 to 1.0)

        Example:
            >>> patterns = CypherPatterns(client)
            >>> coef = patterns.calculate_clustering_coefficient("File", "IMPORTS")
            >>> print(f"Clustering coefficient: {coef:.3f}")
        """
        # Validate inputs to prevent Cypher injection
        validated_label = validate_identifier(node_label, "node label")
        validated_rel_type = validate_identifier(relationship_type, "relationship type")

        # For each node, count triangles and possible triangles
        query = f"""
        MATCH (n:{validated_label})
        OPTIONAL MATCH (n)-[:{validated_rel_type}]-(neighbor:{validated_label})
        WITH n, collect(DISTINCT neighbor) AS neighbors, count(DISTINCT neighbor) AS degree
        WHERE degree >= 2
        UNWIND neighbors AS neighbor1
        UNWIND neighbors AS neighbor2
        WITH n, neighbor1, neighbor2, degree
        WHERE neighbor1 <> neighbor2
        OPTIONAL MATCH (neighbor1)-[:{validated_rel_type}]-(neighbor2)
        WITH n, degree, count(DISTINCT neighbor2) AS triangles
        WITH n, degree, triangles, (degree * (degree - 1)) / 2.0 AS possible
        WHERE possible > 0
        RETURN avg(triangles / possible) AS avg_clustering_coefficient
        """
        results = self.client.execute_query(query)
        if results and results[0]["avg_clustering_coefficient"] is not None:
            return float(results[0]["avg_clustering_coefficient"])
        return 0.0
