"""Graph traversal utilities (BFS, DFS) for detector algorithms.

This module provides Python-side traversal utilities that complement Cypher queries
for cases where custom filtering or complex traversal logic is needed.
"""

from typing import List, Dict, Any, Set, Callable, Optional
from collections import deque
from repotoire.graph.client import Neo4jClient
from repotoire.validation import validate_identifier, ValidationError


class GraphTraversal:
    """Graph traversal utilities for BFS and DFS algorithms."""

    def __init__(self, client: Neo4jClient):
        """Initialize traversal utilities.

        Args:
            client: Neo4j client instance
        """
        self.client = client

    def bfs(
        self,
        start_node_id: str,
        relationship_type: str,
        direction: str = "OUTGOING",
        max_depth: Optional[int] = None,
        filter_fn: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> List[Dict[str, Any]]:
        """Breadth-first search traversal from a starting node.

        Args:
            start_node_id: elementId of starting node
            relationship_type: Relationship type to traverse
            direction: "OUTGOING", "INCOMING", or "BOTH"
            max_depth: Maximum depth to traverse (None = unlimited)
            filter_fn: Optional function to filter nodes (return True to include)

        Returns:
            List of node dictionaries in BFS order

        Example:
            >>> traversal = GraphTraversal(client)
            >>> # Find all files imported (directly or indirectly) from main.py
            >>> nodes = traversal.bfs(main_file_id, "IMPORTS", "OUTGOING", max_depth=5)
            >>> # Filter only Python files
            >>> nodes = traversal.bfs(
            ...     main_file_id,
            ...     "IMPORTS",
            ...     filter_fn=lambda n: n.get("language") == "python"
            ... )
        """
        visited: Set[str] = set()
        queue: deque = deque([(start_node_id, 0)])  # (node_id, depth)
        result: List[Dict[str, Any]] = []

        while queue:
            node_id, depth = queue.popleft()

            if node_id in visited:
                continue

            if max_depth is not None and depth > max_depth:
                continue

            # Get node properties
            node = self._get_node_properties(node_id)
            if not node:
                continue

            # Apply filter
            if filter_fn and not filter_fn(node):
                continue

            visited.add(node_id)
            result.append({**node, "depth": depth})

            # Get neighbors
            neighbors = self._get_neighbors(node_id, relationship_type, direction)
            for neighbor_id in neighbors:
                if neighbor_id not in visited:
                    queue.append((neighbor_id, depth + 1))

        return result

    def dfs(
        self,
        start_node_id: str,
        relationship_type: str,
        direction: str = "OUTGOING",
        max_depth: Optional[int] = None,
        filter_fn: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> List[Dict[str, Any]]:
        """Depth-first search traversal from a starting node.

        Args:
            start_node_id: elementId of starting node
            relationship_type: Relationship type to traverse
            direction: "OUTGOING", "INCOMING", or "BOTH"
            max_depth: Maximum depth to traverse (None = unlimited)
            filter_fn: Optional function to filter nodes (return True to include)

        Returns:
            List of node dictionaries in DFS order

        Example:
            >>> traversal = GraphTraversal(client)
            >>> # Find all dependencies (DFS order)
            >>> nodes = traversal.dfs(start_id, "IMPORTS", "OUTGOING")
        """
        visited: Set[str] = set()
        stack: List[tuple] = [(start_node_id, 0)]  # (node_id, depth)
        result: List[Dict[str, Any]] = []

        while stack:
            node_id, depth = stack.pop()

            if node_id in visited:
                continue

            if max_depth is not None and depth > max_depth:
                continue

            # Get node properties
            node = self._get_node_properties(node_id)
            if not node:
                continue

            # Apply filter
            if filter_fn and not filter_fn(node):
                continue

            visited.add(node_id)
            result.append({**node, "depth": depth})

            # Get neighbors (reversed so they're processed in order)
            neighbors = self._get_neighbors(node_id, relationship_type, direction)
            for neighbor_id in reversed(neighbors):
                if neighbor_id not in visited:
                    stack.append((neighbor_id, depth + 1))

        return result

    def find_path_with_condition(
        self,
        start_node_id: str,
        condition_fn: Callable[[Dict[str, Any]], bool],
        relationship_type: str,
        direction: str = "OUTGOING",
        max_depth: int = 10,
    ) -> Optional[List[Dict[str, Any]]]:
        """Find first path to a node matching a condition using BFS.

        Args:
            start_node_id: elementId of starting node
            condition_fn: Function to test if node matches condition
            relationship_type: Relationship type to traverse
            direction: "OUTGOING", "INCOMING", or "BOTH"
            max_depth: Maximum depth to search

        Returns:
            Path as list of nodes, or None if not found

        Example:
            >>> traversal = GraphTraversal(client)
            >>> # Find path to any test file
            >>> path = traversal.find_path_with_condition(
            ...     start_id,
            ...     lambda n: n.get("name", "").startswith("test_"),
            ...     "IMPORTS",
            ...     max_depth=5
            ... )
        """
        visited: Set[str] = set()
        queue: deque = deque([(start_node_id, [start_node_id], 0)])  # (node_id, path, depth)

        while queue:
            node_id, path, depth = queue.popleft()

            if node_id in visited:
                continue

            if depth > max_depth:
                continue

            # Get node properties
            node = self._get_node_properties(node_id)
            if not node:
                continue

            visited.add(node_id)

            # Check condition
            if condition_fn(node):
                # Found target - return full path with properties
                return [self._get_node_properties(nid) or {} for nid in path]

            # Get neighbors
            neighbors = self._get_neighbors(node_id, relationship_type, direction)
            for neighbor_id in neighbors:
                if neighbor_id not in visited:
                    queue.append((neighbor_id, path + [neighbor_id], depth + 1))

        return None

    def get_subgraph(
        self,
        start_node_ids: List[str],
        relationship_type: str,
        max_depth: int = 3,
    ) -> Dict[str, Any]:
        """Get subgraph reachable from starting nodes.

        Args:
            start_node_ids: List of starting node elementIds
            relationship_type: Relationship type to traverse
            max_depth: Maximum depth to traverse

        Returns:
            Dictionary with 'nodes' and 'relationships' keys

        Example:
            >>> traversal = GraphTraversal(client)
            >>> subgraph = traversal.get_subgraph([file1_id, file2_id], "IMPORTS", max_depth=2)
            >>> print(f"Subgraph has {len(subgraph['nodes'])} nodes")
        """
        all_nodes: Dict[str, Dict[str, Any]] = {}
        all_relationships: List[Dict[str, Any]] = []
        visited: Set[str] = set()

        for start_id in start_node_ids:
            # BFS from each starting node
            nodes = self.bfs(start_id, relationship_type, "BOTH", max_depth=max_depth)

            for node in nodes:
                node_id = node["id"]
                if node_id not in all_nodes:
                    all_nodes[node_id] = node

                # Get relationships for this node
                if node_id not in visited:
                    rels = self._get_node_relationships(node_id, relationship_type)
                    all_relationships.extend(rels)
                    visited.add(node_id)

        return {
            "nodes": list(all_nodes.values()),
            "relationships": all_relationships,
            "node_count": len(all_nodes),
            "relationship_count": len(all_relationships),
        }

    def _get_node_properties(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get properties of a node by elementId.

        Args:
            node_id: Node elementId

        Returns:
            Dictionary of node properties or None
        """
        query = """
        MATCH (n)
        WHERE elementId(n) = $node_id
        RETURN elementId(n) AS id,
               labels(n) AS labels,
               properties(n) AS properties
        """
        results = self.client.execute_query(query, parameters={"node_id": node_id})

        if results:
            r = results[0]
            return {
                "id": r["id"],
                "labels": r["labels"],
                **r["properties"],
            }
        return None

    def _get_neighbors(
        self,
        node_id: str,
        relationship_type: str,
        direction: str = "OUTGOING",
    ) -> List[str]:
        """Get neighbor node IDs.

        Args:
            node_id: Node elementId
            relationship_type: Relationship type
            direction: "OUTGOING", "INCOMING", or "BOTH"

        Returns:
            List of neighbor elementIds
        """
        # Validate inputs to prevent Cypher injection
        validated_rel_type = validate_identifier(relationship_type, "relationship type")

        # Validate direction parameter
        valid_directions = {"OUTGOING", "INCOMING", "BOTH"}
        if direction not in valid_directions:
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
        MATCH (n)
        WHERE elementId(n) = $node_id
        MATCH (n){rel_pattern}(neighbor)
        RETURN DISTINCT elementId(neighbor) AS neighbor_id
        """
        results = self.client.execute_query(query, parameters={"node_id": node_id})

        return [r["neighbor_id"] for r in results]

    def _get_node_relationships(
        self,
        node_id: str,
        relationship_type: str,
    ) -> List[Dict[str, Any]]:
        """Get relationships for a node.

        Args:
            node_id: Node elementId
            relationship_type: Relationship type

        Returns:
            List of relationship dictionaries
        """
        # Validate input to prevent Cypher injection
        validated_rel_type = validate_identifier(relationship_type, "relationship type")

        query = f"""
        MATCH (n)
        WHERE elementId(n) = $node_id
        MATCH (n)-[r:{validated_rel_type}]-(other)
        RETURN elementId(r) AS id,
               type(r) AS type,
               elementId(startNode(r)) AS source,
               elementId(endNode(r)) AS target,
               properties(r) AS properties
        """
        results = self.client.execute_query(query, parameters={"node_id": node_id})

        return [
            {
                "id": r["id"],
                "type": r["type"],
                "source": r["source"],
                "target": r["target"],
                **r["properties"],
            }
            for r in results
        ]
