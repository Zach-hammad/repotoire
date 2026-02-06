"""Graph traversal utilities (BFS, DFS) for detector algorithms.

This module provides Python-side traversal utilities that complement Cypher queries
for cases where custom filtering or complex traversal logic is needed.
"""

from collections import deque
from typing import Any, Callable, Dict, List, Optional, Set

from repotoire.graph import FalkorDBClient
from repotoire.validation import ValidationError, validate_identifier

# Try to import Rust accelerated versions (REPO-407)
try:
    from repotoire_fast import (
        batch_traverse_bfs as _rust_batch_bfs,
    )
    from repotoire_fast import (
        batch_traverse_dfs as _rust_batch_dfs,
    )
    from repotoire_fast import (
        extract_subgraph_parallel as _rust_extract_subgraph,
    )
    _HAS_RUST = True
except ImportError:
    _HAS_RUST = False


class GraphTraversal:
    """Graph traversal utilities for BFS and DFS algorithms."""

    def __init__(self, client: FalkorDBClient):
        """Initialize traversal utilities.

        Args:
            client: FalkorDB client instance
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

        Performance fix: Uses batch queries to fetch all relationships in 1 query
        instead of N queries (one per node).

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

        for start_id in start_node_ids:
            # BFS from each starting node
            nodes = self.bfs(start_id, relationship_type, "BOTH", max_depth=max_depth)

            for node in nodes:
                node_id = node["id"]
                if node_id not in all_nodes:
                    all_nodes[node_id] = node

        # Batch fetch all relationships for discovered nodes (single query instead of N)
        all_node_ids = list(all_nodes.keys())
        all_relationships = self._batch_get_node_relationships(all_node_ids, relationship_type)

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

    def _batch_get_node_properties(self, node_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Get properties of multiple nodes in a single query.

        Performance fix: Uses UNWIND to batch all node property fetches into one query,
        reducing N queries to 1 query.

        Args:
            node_ids: List of node elementIds

        Returns:
            Dictionary mapping node_id to node properties
        """
        if not node_ids:
            return {}

        query = """
        UNWIND $node_ids AS node_id
        MATCH (n)
        WHERE elementId(n) = node_id
        RETURN elementId(n) AS id,
               labels(n) AS labels,
               properties(n) AS properties
        """
        results = self.client.execute_query(query, parameters={"node_ids": node_ids})

        return {
            r["id"]: {
                "id": r["id"],
                "labels": r["labels"],
                **r["properties"],
            }
            for r in results
        }

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

    def _batch_get_neighbors(
        self,
        node_ids: List[str],
        relationship_type: str,
        direction: str = "OUTGOING",
    ) -> Dict[str, List[str]]:
        """Get neighbor node IDs for multiple nodes in a single query.

        Performance fix: Uses UNWIND to batch all neighbor fetches into one query,
        reducing N queries to 1 query.

        Args:
            node_ids: List of node elementIds
            relationship_type: Relationship type
            direction: "OUTGOING", "INCOMING", or "BOTH"

        Returns:
            Dictionary mapping node_id to list of neighbor elementIds
        """
        if not node_ids:
            return {}

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
        UNWIND $node_ids AS node_id
        MATCH (n)
        WHERE elementId(n) = node_id
        OPTIONAL MATCH (n){rel_pattern}(neighbor)
        RETURN node_id, collect(DISTINCT elementId(neighbor)) AS neighbor_ids
        """
        results = self.client.execute_query(query, parameters={"node_ids": node_ids})

        return {
            r["node_id"]: [nid for nid in r["neighbor_ids"] if nid is not None]
            for r in results
        }

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

    def _batch_get_node_relationships(
        self,
        node_ids: List[str],
        relationship_type: str,
    ) -> List[Dict[str, Any]]:
        """Get relationships for multiple nodes in a single query.

        Performance fix: Uses UNWIND to batch all relationship fetches into one query,
        reducing N queries to 1 query.

        Args:
            node_ids: List of node elementIds
            relationship_type: Relationship type

        Returns:
            List of relationship dictionaries (deduplicated)
        """
        if not node_ids:
            return []

        # Validate input to prevent Cypher injection
        validated_rel_type = validate_identifier(relationship_type, "relationship type")

        query = f"""
        UNWIND $node_ids AS node_id
        MATCH (n)
        WHERE elementId(n) = node_id
        MATCH (n)-[r:{validated_rel_type}]-(other)
        RETURN DISTINCT elementId(r) AS id,
               type(r) AS type,
               elementId(startNode(r)) AS source,
               elementId(endNode(r)) AS target,
               properties(r) AS properties
        """
        results = self.client.execute_query(query, parameters={"node_ids": node_ids})

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

    def batch_bfs(
        self,
        start_node_ids: List[str],
        relationship_type: str,
        direction: str = "OUTGOING",
        max_depth: int = 5,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Batch BFS traversal from multiple starting nodes (REPO-407).

        Uses Rust parallel processing when available and beneficial.

        Args:
            start_node_ids: List of starting node elementIds
            relationship_type: Relationship type to traverse
            direction: "OUTGOING", "INCOMING", or "BOTH"
            max_depth: Maximum depth to traverse

        Returns:
            Dictionary mapping start_node_id to list of reached nodes

        Example:
            >>> traversal = GraphTraversal(client)
            >>> results = traversal.batch_bfs(
            ...     [file1_id, file2_id, file3_id],
            ...     "IMPORTS",
            ...     max_depth=3
            ... )
        """
        if not start_node_ids:
            return {}

        # Use Rust for batch processing with 5+ starting nodes
        if _HAS_RUST and len(start_node_ids) >= 5:
            return self._batch_bfs_rust(start_node_ids, relationship_type, direction, max_depth)

        # Python fallback: sequential BFS
        results = {}
        for start_id in start_node_ids:
            nodes = self.bfs(start_id, relationship_type, direction, max_depth)
            results[start_id] = nodes
        return results

    def _batch_bfs_rust(
        self,
        start_node_ids: List[str],
        relationship_type: str,
        direction: str,
        max_depth: int,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Rust-accelerated batch BFS traversal."""
        # Load edges with relationship type (Rust expects triples)
        edges_raw = self._load_edges(relationship_type, direction)

        if not edges_raw:
            return {start_id: [] for start_id in start_node_ids}

        # Convert edges to triples: (from, to, rel_type)
        edges = [(e[0], e[1], relationship_type) for e in edges_raw]

        # Build minimal nodes list from edges (id, labels, properties)
        # We'll fetch full properties later for the results
        node_ids = set()
        for src, tgt, _ in edges:
            node_ids.add(src)
            node_ids.add(tgt)
        nodes = [(nid, [], {}) for nid in node_ids]

        # Map direction to Rust format
        direction_map = {"OUTGOING": "outgoing", "INCOMING": "incoming", "BOTH": "both"}
        rust_direction = direction_map.get(direction, "outgoing")

        # Call Rust batch BFS
        # Returns: (visited_nodes, node_properties, traversed_edges, depths)
        visited_nodes, _, _, depths = _rust_batch_bfs(
            nodes, edges, start_node_ids, max_depth, rust_direction, relationship_type
        )

        # Fetch node properties for visited nodes
        node_properties = self._batch_get_node_properties(visited_nodes)

        # Group visited nodes by their nearest start node using depths
        # For simplicity, return all visited nodes for each start node
        # (The Rust BFS traverses from all starts simultaneously)
        result_nodes = [
            {**node_properties.get(node_id, {}), "id": node_id}
            for node_id in visited_nodes
            if node_id in node_properties
        ]

        # Map each start node to all reachable nodes
        return {start_id: result_nodes for start_id in start_node_ids}

    def batch_dfs(
        self,
        start_node_ids: List[str],
        relationship_type: str,
        direction: str = "OUTGOING",
        max_depth: int = 5,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Batch DFS traversal from multiple starting nodes (REPO-407).

        Uses Rust parallel processing when available and beneficial.

        Args:
            start_node_ids: List of starting node elementIds
            relationship_type: Relationship type to traverse
            direction: "OUTGOING", "INCOMING", or "BOTH"
            max_depth: Maximum depth to traverse

        Returns:
            Dictionary mapping start_node_id to list of reached nodes

        Example:
            >>> traversal = GraphTraversal(client)
            >>> results = traversal.batch_dfs(
            ...     [class1_id, class2_id],
            ...     "INHERITS",
            ...     max_depth=5
            ... )
        """
        if not start_node_ids:
            return {}

        # Use Rust for batch processing with 5+ starting nodes
        if _HAS_RUST and len(start_node_ids) >= 5:
            return self._batch_dfs_rust(start_node_ids, relationship_type, direction, max_depth)

        # Python fallback: sequential DFS
        results = {}
        for start_id in start_node_ids:
            nodes = self.dfs(start_id, relationship_type, direction, max_depth)
            results[start_id] = nodes
        return results

    def _batch_dfs_rust(
        self,
        start_node_ids: List[str],
        relationship_type: str,
        direction: str,
        max_depth: int,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Rust-accelerated batch DFS traversal."""
        # Load edges with relationship type (Rust expects triples)
        edges_raw = self._load_edges(relationship_type, direction)

        if not edges_raw:
            return {start_id: [] for start_id in start_node_ids}

        # Convert edges to triples: (from, to, rel_type)
        edges = [(e[0], e[1], relationship_type) for e in edges_raw]

        # Build minimal nodes list from edges (id, labels, properties)
        node_ids = set()
        for src, tgt, _ in edges:
            node_ids.add(src)
            node_ids.add(tgt)
        nodes = [(nid, [], {}) for nid in node_ids]

        # Map direction to Rust format
        direction_map = {"OUTGOING": "outgoing", "INCOMING": "incoming", "BOTH": "both"}
        rust_direction = direction_map.get(direction, "outgoing")

        # Call Rust batch DFS
        # Returns: (visited_nodes, node_properties, traversed_edges, depths)
        visited_nodes, _, _, depths = _rust_batch_dfs(
            nodes, edges, start_node_ids, max_depth, rust_direction, relationship_type
        )

        # Fetch node properties for visited nodes
        node_properties = self._batch_get_node_properties(visited_nodes)

        # Group visited nodes by their nearest start node using depths
        # For simplicity, return all visited nodes for each start node
        result_nodes = [
            {**node_properties.get(node_id, {}), "id": node_id}
            for node_id in visited_nodes
            if node_id in node_properties
        ]

        # Map each start node to all reachable nodes
        return {start_id: result_nodes for start_id in start_node_ids}

    def get_subgraph_parallel(
        self,
        start_node_ids: List[str],
        relationship_type: str,
        max_depth: int = 3,
    ) -> Dict[str, Any]:
        """Get subgraph reachable from starting nodes using parallel traversal (REPO-407).

        Uses Rust parallel processing when available and beneficial.

        Args:
            start_node_ids: List of starting node elementIds
            relationship_type: Relationship type to traverse
            max_depth: Maximum depth to traverse

        Returns:
            Dictionary with 'nodes' and 'relationships' keys

        Example:
            >>> traversal = GraphTraversal(client)
            >>> subgraph = traversal.get_subgraph_parallel(
            ...     [file1_id, file2_id],
            ...     "IMPORTS",
            ...     max_depth=3
            ... )
        """
        if not start_node_ids:
            return {"nodes": [], "relationships": [], "node_count": 0, "relationship_count": 0}

        # Use Rust for parallel extraction with 5+ starting nodes
        if _HAS_RUST and len(start_node_ids) >= 5:
            return self._get_subgraph_rust(start_node_ids, relationship_type, max_depth)

        # Fallback to existing method
        return self.get_subgraph(start_node_ids, relationship_type, max_depth)

    def _get_subgraph_rust(
        self,
        start_node_ids: List[str],
        relationship_type: str,
        max_depth: int,
    ) -> Dict[str, Any]:
        """Rust-accelerated parallel subgraph extraction."""
        # Load edges from database
        edges_raw = self._load_edges(relationship_type, "BOTH")

        if not edges_raw:
            return {"nodes": [], "relationships": [], "node_count": 0, "relationship_count": 0}

        # Convert edges to triples: (from, to, rel_type)
        edges = [(e[0], e[1], relationship_type) for e in edges_raw]

        # Build minimal nodes list from edges (id, labels, properties)
        node_ids_set = set()
        for src, tgt, _ in edges:
            node_ids_set.add(src)
            node_ids_set.add(tgt)
        nodes = [(nid, [], {}) for nid in node_ids_set]

        # Call Rust parallel subgraph extraction
        # Returns: (visited_node_ids, traversed_edges as triples)
        visited_node_ids, traversed_edges = _rust_extract_subgraph(
            nodes, edges, start_node_ids, max_depth
        )

        # Fetch node properties
        node_properties = self._batch_get_node_properties(visited_node_ids)

        # Build relationships from traversed edges
        relationships = [
            {"source": e[0], "target": e[1], "type": e[2]}
            for e in traversed_edges
        ]

        return {
            "nodes": list(node_properties.values()),
            "relationships": relationships,
            "node_count": len(node_properties),
            "relationship_count": len(relationships),
            "rust_accelerated": True,
        }

    def _load_edges(
        self,
        relationship_type: str,
        direction: str,
    ) -> List[tuple]:
        """Load all edges of a relationship type from the database.

        Args:
            relationship_type: Relationship type
            direction: Direction filter ("OUTGOING", "INCOMING", "BOTH")

        Returns:
            List of (source_id, target_id) tuples
        """
        # Validate input to prevent Cypher injection
        validated_rel_type = validate_identifier(relationship_type, "relationship type")

        query = f"""
        MATCH (a)-[r:{validated_rel_type}]->(b)
        RETURN elementId(a) AS source, elementId(b) AS target
        """
        results = self.client.execute_query(query)

        edges = [(r["source"], r["target"]) for r in results]

        # For BOTH direction, include reverse edges
        if direction == "BOTH":
            edges.extend([(r["target"], r["source"]) for r in results])

        return edges
