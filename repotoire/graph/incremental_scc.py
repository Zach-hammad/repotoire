"""Incremental SCC cache manager for 10-100x faster circular dependency detection (REPO-412).

This module provides a Python wrapper around the Rust incremental SCC implementation,
with Neo4j integration for:
1. Extracting edges from the graph database
2. Tracking edge changes during ingestion
3. Persisting SCC IDs to nodes for querying
4. Caching the Rust SCC cache across analysis runs

Usage:
    from repotoire.graph.incremental_scc import IncrementalSCCManager

    manager = IncrementalSCCManager(neo4j_client)

    # First run: full computation
    cycles = manager.get_cycles()

    # After file changes: incremental update
    manager.update_edges(added=[(0, 1)], removed=[(2, 3)])
    cycles = manager.get_cycles()

    # Persist to Neo4j
    manager.persist_scc_ids()
"""

from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass
import time

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Try to import Rust implementation
try:
    from repotoire_fast import (
        PyIncrementalSCC,
        incremental_scc_new,
        find_sccs_one_shot,
    )
    RUST_AVAILABLE = True
except ImportError:
    RUST_AVAILABLE = False
    logger.warning("Rust incremental SCC not available, falling back to full Tarjan's")


@dataclass
class SCCUpdateResult:
    """Result of an incremental SCC update."""
    update_type: str  # "no_change", "updated", "full_recompute"
    nodes_updated: int = 0
    sccs_affected: int = 0
    total_sccs: int = 0
    compute_micros: int = 0

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SCCUpdateResult":
        """Create from Rust dict result."""
        update_type = d.get("type", "unknown")
        return cls(
            update_type=update_type,
            nodes_updated=d.get("nodes_updated", 0),
            sccs_affected=d.get("sccs_affected", 0),
            total_sccs=d.get("total_sccs", 0),
            compute_micros=d.get("compute_micros", 0),
        )


class IncrementalSCCManager:
    """Manages incremental SCC computation with Neo4j integration.

    Provides 10-100x speedup for circular dependency detection by:
    1. Caching SCC assignments across runs
    2. Only recomputing affected SCCs on edge changes
    3. Integrating with Neo4j for edge extraction and SCC persistence

    Example:
        >>> manager = IncrementalSCCManager(client)
        >>> cycles = manager.get_cycles(min_size=2)
        >>> for cycle in cycles:
        ...     print(f"Cycle: {[manager.id_to_name[n] for n in cycle]}")
    """

    def __init__(self, client, node_label: str = "File", rel_type: str = "IMPORTS"):
        """Initialize the SCC manager.

        Args:
            client: Neo4j or FalkorDB client
            node_label: Node label to analyze (default: "File")
            rel_type: Relationship type to follow (default: "IMPORTS")
        """
        self.client = client
        self.node_label = node_label
        self.rel_type = rel_type

        # Rust cache (if available)
        self._cache: Optional["PyIncrementalSCC"] = None

        # Node ID mappings (Neo4j ID or name -> sequential ID)
        self.name_to_id: Dict[str, int] = {}
        self.id_to_name: Dict[int, str] = {}

        # Current edge set for tracking changes
        self._current_edges: Set[Tuple[int, int]] = set()

        # Whether cache is initialized
        self._initialized = False

    def _extract_graph(self) -> Tuple[List[Tuple[int, int]], int]:
        """Extract edges and nodes from Neo4j.

        Returns:
            Tuple of (edges, num_nodes) where edges are sequential IDs
        """
        # Query for nodes and edges
        query = f"""
        MATCH (n:{self.node_label})
        WITH collect(n) AS nodes
        UNWIND nodes AS n
        WITH n, id(n) AS neo_id
        ORDER BY neo_id
        WITH collect({{neo_id: neo_id, name: n.qualifiedName}}) AS node_list
        OPTIONAL MATCH (a:{self.node_label})-[r:{self.rel_type}]->(b:{self.node_label})
        WITH node_list, collect({{src: id(a), dst: id(b)}}) AS edges
        RETURN node_list, edges
        """
        result = self.client.execute_query(query)

        if not result or not result[0]["node_list"]:
            return [], 0

        # Build node ID mappings
        node_list = result[0]["node_list"]
        neo_to_seq: Dict[int, int] = {}
        self.name_to_id.clear()
        self.id_to_name.clear()

        for seq_id, node in enumerate(node_list):
            neo_id = node["neo_id"]
            name = node["name"] or f"unknown_{seq_id}"
            neo_to_seq[neo_id] = seq_id
            self.name_to_id[name] = seq_id
            self.id_to_name[seq_id] = name

        # Convert edges to sequential IDs
        edges: List[Tuple[int, int]] = []
        for edge in result[0]["edges"]:
            if edge["src"] is None or edge["dst"] is None:
                continue
            src = neo_to_seq.get(edge["src"])
            dst = neo_to_seq.get(edge["dst"])
            if src is not None and dst is not None:
                edges.append((src, dst))

        return edges, len(node_list)

    def initialize(self, force_full: bool = False) -> SCCUpdateResult:
        """Initialize or reinitialize the SCC cache.

        Args:
            force_full: Force full recomputation even if cache exists

        Returns:
            Update result with timing info
        """
        start = time.time()

        edges, num_nodes = self._extract_graph()
        self._current_edges = set(edges)

        if not RUST_AVAILABLE:
            # Fallback: use petgraph via Rust, but without caching
            logger.warning("Rust incremental SCC not available")
            self._initialized = False
            return SCCUpdateResult(
                update_type="rust_unavailable",
                total_sccs=0,
                compute_micros=int((time.time() - start) * 1_000_000),
            )

        if self._cache is None or force_full:
            # Create new cache
            self._cache = PyIncrementalSCC()
            self._cache.initialize(edges, num_nodes)
            self._initialized = True

            elapsed_micros = int((time.time() - start) * 1_000_000)
            logger.info(
                f"Initialized SCC cache: {self._cache.scc_count} SCCs, "
                f"{len(self.get_cycles(2))} cycles in {elapsed_micros}µs"
            )

            return SCCUpdateResult(
                update_type="full_recompute",
                total_sccs=self._cache.scc_count,
                compute_micros=elapsed_micros,
            )

        # Already initialized, just return current state
        return SCCUpdateResult(
            update_type="already_initialized",
            total_sccs=self._cache.scc_count,
            compute_micros=int((time.time() - start) * 1_000_000),
        )

    def update_edges(
        self,
        added: Optional[List[Tuple[int, int]]] = None,
        removed: Optional[List[Tuple[int, int]]] = None,
    ) -> SCCUpdateResult:
        """Incrementally update the SCC cache after edge changes.

        This is the core optimization: only affected SCCs are recomputed.

        Args:
            added: List of (src_id, dst_id) edges added
            removed: List of (src_id, dst_id) edges removed

        Returns:
            Update result with type and timing info
        """
        added = added or []
        removed = removed or []

        if not RUST_AVAILABLE or self._cache is None:
            # Fallback to full recompute
            return self.initialize(force_full=True)

        # Update internal edge tracking
        for edge in removed:
            self._current_edges.discard(edge)
        for edge in added:
            self._current_edges.add(edge)

        # Call Rust incremental update
        all_edges = list(self._current_edges)
        result = self._cache.update(added, removed, all_edges)

        update_result = SCCUpdateResult.from_dict(result)
        logger.debug(
            f"Incremental SCC update: type={update_result.update_type}, "
            f"nodes_updated={update_result.nodes_updated}, "
            f"sccs_affected={update_result.sccs_affected}"
        )

        return update_result

    def refresh_from_neo4j(self) -> SCCUpdateResult:
        """Refresh the cache by comparing current Neo4j state to cached state.

        Computes the edge diff automatically and performs incremental update.

        Returns:
            Update result
        """
        edges, num_nodes = self._extract_graph()
        new_edges = set(edges)

        if not self._initialized:
            # First time, do full initialization
            self._current_edges = new_edges
            return self.initialize()

        # Compute diff
        added = list(new_edges - self._current_edges)
        removed = list(self._current_edges - new_edges)

        if not added and not removed:
            return SCCUpdateResult(update_type="no_change")

        return self.update_edges(added=added, removed=removed)

    def get_cycles(self, min_size: int = 2) -> List[List[int]]:
        """Get all cycles (SCCs with size >= min_size).

        Args:
            min_size: Minimum SCC size to include

        Returns:
            List of cycles, where each cycle is a list of node IDs
        """
        if not RUST_AVAILABLE or self._cache is None:
            # Fallback to one-shot computation
            edges = list(self._current_edges)
            if not edges:
                return []
            return find_sccs_one_shot(edges, len(self.id_to_name), min_size)

        return self._cache.get_cycles(min_size)

    def get_cycles_with_names(self, min_size: int = 2) -> List[List[str]]:
        """Get all cycles with qualified names instead of IDs.

        Args:
            min_size: Minimum SCC size

        Returns:
            List of cycles with qualified names
        """
        cycles = self.get_cycles(min_size)
        return [
            [self.id_to_name.get(node_id, f"unknown_{node_id}") for node_id in cycle]
            for cycle in cycles
        ]

    def get_scc(self, node_name: str) -> Optional[int]:
        """Get the SCC ID for a given node.

        Args:
            node_name: Qualified name of the node

        Returns:
            SCC ID, or None if node not found
        """
        if not RUST_AVAILABLE or self._cache is None:
            return None

        node_id = self.name_to_id.get(node_name)
        if node_id is None:
            return None

        return self._cache.get_scc(node_id)

    def persist_scc_ids(self, property_name: str = "scc_component") -> int:
        """Persist SCC IDs to Neo4j nodes.

        Args:
            property_name: Property name to store SCC IDs

        Returns:
            Number of nodes updated
        """
        if not RUST_AVAILABLE or self._cache is None:
            return 0

        # Build updates
        updates = []
        for name, node_id in self.name_to_id.items():
            scc_id = self._cache.get_scc(node_id)
            if scc_id is not None:
                updates.append({"name": name, "scc_id": int(scc_id)})

        if not updates:
            return 0

        # Batch update in chunks
        updated = 0
        chunk_size = 500

        for i in range(0, len(updates), chunk_size):
            chunk = updates[i : i + chunk_size]
            query = f"""
            UNWIND $updates AS update
            MATCH (n:{self.node_label} {{qualifiedName: update.name}})
            SET n.{property_name} = update.scc_id
            RETURN count(n) AS updated
            """
            result = self.client.execute_query(query, {"updates": chunk})
            if result:
                updated += result[0]["updated"]

        logger.info(f"Persisted SCC IDs to {updated} nodes")
        return updated

    @property
    def version(self) -> int:
        """Get the current cache version."""
        if not RUST_AVAILABLE or self._cache is None:
            return 0
        return self._cache.version

    @property
    def scc_count(self) -> int:
        """Get the total number of SCCs."""
        if not RUST_AVAILABLE or self._cache is None:
            return 0
        return self._cache.scc_count

    def verify(self) -> bool:
        """Verify cache correctness against full Tarjan's computation.

        Returns:
            True if cache matches full computation
        """
        if not RUST_AVAILABLE or self._cache is None:
            return True  # Nothing to verify

        edges = list(self._current_edges)
        return self._cache.verify(edges, len(self.id_to_name))

    def __repr__(self) -> str:
        if not RUST_AVAILABLE:
            return "IncrementalSCCManager(rust_unavailable)"
        if self._cache is None:
            return "IncrementalSCCManager(uninitialized)"
        return (
            f"IncrementalSCCManager("
            f"version={self.version}, "
            f"scc_count={self.scc_count}, "
            f"cycles={len(self.get_cycles(2))})"
        )


# Convenience function for one-shot SCC computation
def find_cycles(
    client,
    node_label: str = "File",
    rel_type: str = "IMPORTS",
    min_size: int = 2,
) -> List[Dict[str, Any]]:
    """Find all cycles in a graph without caching.

    For quick one-off analysis. Use IncrementalSCCManager for repeated analysis.

    Args:
        client: Neo4j or FalkorDB client
        node_label: Node label to analyze
        rel_type: Relationship type to follow
        min_size: Minimum cycle size

    Returns:
        List of cycle info dicts with file_paths and cycle_size
    """
    manager = IncrementalSCCManager(client, node_label, rel_type)
    manager.initialize()

    cycles = manager.get_cycles_with_names(min_size)

    return [
        {
            "cycle_size": len(cycle),
            "file_names": cycle,
        }
        for cycle in cycles
    ]
