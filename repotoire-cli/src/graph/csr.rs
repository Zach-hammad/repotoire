//! Compressed Sparse Row (CSR) storage for the frozen graph.
//!
//! Each edge `(A → B, kind)` is stored under both endpoints:
//! - A's outgoing slot for that kind
//! - B's incoming slot for that kind
//!
//! Queries are O(1): one multiply, one add, two array reads, one slice.

use crate::graph::node_index::NodeIndex;
use crate::graph::store_models::EdgeKind;

/// Number of slots per node.
///
/// 5 bidirectional edge kinds × 2 directions + 1 unidirectional (ModifiedIn-Out).
pub const STRIDE: usize = 11;

// Slot constants: kind * 2 + direction, except ModifiedIn which has only Out.
pub mod slot {
    pub const CALLS_OUT: usize = 0;
    pub const CALLS_IN: usize = 1;
    pub const IMPORTS_OUT: usize = 2;
    pub const IMPORTS_IN: usize = 3;
    pub const CONTAINS_OUT: usize = 4;
    pub const CONTAINS_IN: usize = 5;
    pub const INHERITS_OUT: usize = 6;
    pub const INHERITS_IN: usize = 7;
    pub const USES_OUT: usize = 8;
    pub const USES_IN: usize = 9;
    pub const MODIFIED_IN_OUT: usize = 10;
}

/// Return the (out_slot, in_slot) for a given EdgeKind.
/// ModifiedIn returns (MODIFIED_IN_OUT, None) since it has no incoming slot.
fn edge_kind_slots(kind: EdgeKind) -> (usize, Option<usize>) {
    match kind {
        EdgeKind::Calls => (slot::CALLS_OUT, Some(slot::CALLS_IN)),
        EdgeKind::Imports => (slot::IMPORTS_OUT, Some(slot::IMPORTS_IN)),
        EdgeKind::Contains => (slot::CONTAINS_OUT, Some(slot::CONTAINS_IN)),
        EdgeKind::Inherits => (slot::INHERITS_OUT, Some(slot::INHERITS_IN)),
        EdgeKind::Uses => (slot::USES_OUT, Some(slot::USES_IN)),
        EdgeKind::ModifiedIn => (slot::MODIFIED_IN_OUT, None),
    }
}

/// Compressed Sparse Row storage for graph edges.
///
/// `offsets` has length `node_count * STRIDE + 1`.
/// `neighbors` has length equal to the total number of directed half-edges.
/// For each node `v` and slot `s`, the neighbors are:
///   `neighbors[offsets[v * STRIDE + s] .. offsets[v * STRIDE + s + 1]]`
pub struct CsrStorage {
    offsets: Vec<u32>,
    neighbors: Vec<u32>,
    node_count: usize,
}

impl CsrStorage {
    /// Build CSR from a node count and edge list.
    ///
    /// Each edge `(src, dst, kind)` is expanded into:
    /// - `(src, kind_out_slot, dst)` — stored under src
    /// - `(dst, kind_in_slot, src)` — stored under dst (skipped for ModifiedIn)
    ///
    /// Neighbor lists within each slot are sorted by target NodeIndex.
    pub fn build(node_count: usize, edges: &[(u32, u32, EdgeKind)]) -> Self {
        if node_count == 0 {
            return Self {
                offsets: vec![0],
                neighbors: Vec::new(),
                node_count: 0,
            };
        }

        // Step 1: Expand edges into (node, slot, neighbor) entries
        let mut entries: Vec<(u32, usize, u32)> = Vec::with_capacity(edges.len() * 2);
        for &(src, dst, kind) in edges {
            let (out_slot, in_slot) = edge_kind_slots(kind);
            entries.push((src, out_slot, dst));
            if let Some(in_s) = in_slot {
                entries.push((dst, in_s, src));
            }
        }

        // Step 2: Sort by (node, slot, neighbor) for deterministic ordering
        entries.sort_unstable();

        // Step 3: Build offsets array
        let total_slots = node_count * STRIDE;
        let mut offsets = vec![0u32; total_slots + 1];
        let mut neighbors = Vec::with_capacity(entries.len());

        // Count entries per slot
        for &(node, s, _) in &entries {
            let slot_idx = node as usize * STRIDE + s;
            offsets[slot_idx + 1] += 1;
        }

        // Prefix sum
        for i in 1..=total_slots {
            offsets[i] += offsets[i - 1];
        }

        // Fill neighbors (entries are already sorted, so just append in order)
        neighbors.extend(entries.iter().map(|&(_, _, neighbor)| neighbor));

        Self {
            offsets,
            neighbors,
            node_count,
        }
    }

    /// Get the neighbors of `node` in the given `slot` as a `&[u32]` slice.
    ///
    /// Returns an empty slice if the node has no neighbors in that slot.
    #[inline]
    pub fn neighbors(&self, node: u32, s: usize) -> &[u32] {
        let slot_idx = node as usize * STRIDE + s;
        debug_assert!(slot_idx + 1 < self.offsets.len(), "slot index out of bounds");
        let start = self.offsets[slot_idx] as usize;
        let end = self.offsets[slot_idx + 1] as usize;
        &self.neighbors[start..end]
    }

    /// Get the neighbors as a `&[NodeIndex]` slice.
    ///
    /// SAFETY: `NodeIndex` is `#[repr(transparent)]` over `u32`, so the
    /// transmute from `&[u32]` to `&[NodeIndex]` is safe and zero-cost.
    #[inline]
    pub fn neighbors_as_node_index(&self, node: usize, s: usize) -> &[NodeIndex] {
        let raw = self.neighbors(node as u32, s);
        // SAFETY: NodeIndex is #[repr(transparent)] over u32
        unsafe { std::mem::transmute::<&[u32], &[NodeIndex]>(raw) }
    }

    /// Number of nodes in the CSR.
    #[inline]
    pub fn node_count(&self) -> usize {
        self.node_count
    }

    /// Total number of half-edge entries (for stats).
    #[inline]
    pub fn neighbor_count(&self) -> usize {
        self.neighbors.len()
    }
}

/// Compute a BFS vertex permutation for cache-friendly traversal order.
///
/// Seeds BFS from the highest-degree node. Returns a permutation array where
/// `perm[old_idx] = new_idx`. Disconnected components are handled by finding
/// a new seed among unvisited nodes.
///
/// Degree ties are broken by lowest original index for determinism.
pub fn bfs_reorder(node_count: usize, edges: &[(u32, u32, EdgeKind)]) -> Vec<u32> {
    if node_count == 0 {
        return Vec::new();
    }

    // Count total degree per node (all edge kinds, both directions)
    let mut degree = vec![0u32; node_count];
    for &(src, dst, _) in edges {
        degree[src as usize] += 1;
        degree[dst as usize] += 1;
    }

    // Build undirected adjacency list for BFS
    let mut adj: Vec<Vec<u32>> = vec![vec![]; node_count];
    for &(src, dst, _) in edges {
        adj[src as usize].push(dst);
        adj[dst as usize].push(src);
    }
    // Sort neighbors for determinism
    for list in &mut adj {
        list.sort_unstable();
        list.dedup();
    }

    let mut perm = vec![u32::MAX; node_count];
    let mut visited = vec![false; node_count];
    let mut next_id = 0u32;
    let mut queue = std::collections::VecDeque::with_capacity(node_count);

    while (next_id as usize) < node_count {
        // Find unvisited node with highest degree (ties: lowest index)
        let seed = (0..node_count)
            .filter(|&i| !visited[i])
            .max_by_key(|&i| (degree[i], std::cmp::Reverse(i)))
            .expect("unvisited node must exist");

        visited[seed] = true;
        perm[seed] = next_id;
        next_id += 1;
        queue.push_back(seed);

        while let Some(v) = queue.pop_front() {
            for &neighbor in &adj[v] {
                let n = neighbor as usize;
                if !visited[n] {
                    visited[n] = true;
                    perm[n] = next_id;
                    next_id += 1;
                    queue.push_back(n);
                }
            }
        }
    }

    perm
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_empty_csr() {
        let csr = CsrStorage::build(0, &[]);
        assert_eq!(csr.node_count(), 0);
        assert_eq!(csr.neighbor_count(), 0);
    }

    #[test]
    fn test_basic_edges() {
        // Node 0 calls Node 1
        let edges = vec![(0u32, 1u32, EdgeKind::Calls)];
        let csr = CsrStorage::build(2, &edges);

        // Node 0's outgoing calls should be [1]
        assert_eq!(csr.neighbors(0, slot::CALLS_OUT), &[1]);
        // Node 1's incoming calls should be [0]
        assert_eq!(csr.neighbors(1, slot::CALLS_IN), &[0]);
        // Node 0 has no incoming calls
        assert!(csr.neighbors(0, slot::CALLS_IN).is_empty());
        // Node 1 has no outgoing calls
        assert!(csr.neighbors(1, slot::CALLS_OUT).is_empty());
    }

    #[test]
    fn test_bidirectional_consistency() {
        // A calls B, B calls C
        let edges = vec![
            (0u32, 1u32, EdgeKind::Calls),
            (1u32, 2u32, EdgeKind::Calls),
        ];
        let csr = CsrStorage::build(3, &edges);

        // A's callees: [B]
        assert_eq!(csr.neighbors(0, slot::CALLS_OUT), &[1]);
        // B's callers: [A]
        assert_eq!(csr.neighbors(1, slot::CALLS_IN), &[0]);
        // B's callees: [C]
        assert_eq!(csr.neighbors(1, slot::CALLS_OUT), &[2]);
        // C's callers: [B]
        assert_eq!(csr.neighbors(2, slot::CALLS_IN), &[1]);
    }

    #[test]
    fn test_sorted_neighbors() {
        // Node 0 calls nodes 2, 1, 3 — neighbors should be sorted
        let edges = vec![
            (0u32, 2u32, EdgeKind::Calls),
            (0u32, 1u32, EdgeKind::Calls),
            (0u32, 3u32, EdgeKind::Calls),
        ];
        let csr = CsrStorage::build(4, &edges);

        assert_eq!(csr.neighbors(0, slot::CALLS_OUT), &[1, 2, 3]);
    }

    #[test]
    fn test_modified_in_unidirectional() {
        // Node 0 ModifiedIn Node 1 — only outgoing, no incoming slot
        let edges = vec![(0u32, 1u32, EdgeKind::ModifiedIn)];
        let csr = CsrStorage::build(2, &edges);

        // Node 0's ModifiedIn-Out should be [1]
        assert_eq!(csr.neighbors(0, slot::MODIFIED_IN_OUT), &[1]);
        // Node 1 should have no ModifiedIn-Out entries (it's the target, not source)
        assert!(csr.neighbors(1, slot::MODIFIED_IN_OUT).is_empty());
    }

    #[test]
    fn test_neighbors_as_node_index() {
        let edges = vec![(0u32, 1u32, EdgeKind::Calls)];
        let csr = CsrStorage::build(2, &edges);

        let ni = csr.neighbors_as_node_index(0, slot::CALLS_OUT);
        assert_eq!(ni.len(), 1);
        assert_eq!(ni[0], NodeIndex::new(1));
    }

    #[test]
    fn test_multiple_edge_kinds() {
        let edges = vec![
            (0u32, 1u32, EdgeKind::Calls),
            (0u32, 1u32, EdgeKind::Contains),
            (0u32, 1u32, EdgeKind::Imports),
        ];
        let csr = CsrStorage::build(2, &edges);

        assert_eq!(csr.neighbors(0, slot::CALLS_OUT), &[1]);
        assert_eq!(csr.neighbors(0, slot::CONTAINS_OUT), &[1]);
        assert_eq!(csr.neighbors(0, slot::IMPORTS_OUT), &[1]);
        assert_eq!(csr.neighbors(1, slot::CALLS_IN), &[0]);
        assert_eq!(csr.neighbors(1, slot::CONTAINS_IN), &[0]);
        assert_eq!(csr.neighbors(1, slot::IMPORTS_IN), &[0]);
    }

    #[test]
    fn test_bfs_reorder_highest_degree_first() {
        // Node 2 has highest degree (5 edges), should get index 0
        let edges = vec![
            (0u32, 2, EdgeKind::Calls),
            (1, 2, EdgeKind::Calls),
            (2, 0, EdgeKind::Calls),
            (2, 1, EdgeKind::Calls),
            (2, 3, EdgeKind::Calls),
        ];
        let perm = bfs_reorder(4, &edges);
        assert_eq!(perm[2], 0); // highest degree → new index 0
    }

    #[test]
    fn test_bfs_reorder_deterministic() {
        let edges = vec![
            (0u32, 1, EdgeKind::Calls),
            (1, 2, EdgeKind::Calls),
        ];
        let perm1 = bfs_reorder(3, &edges);
        let perm2 = bfs_reorder(3, &edges);
        assert_eq!(perm1, perm2);
    }

    #[test]
    fn test_bfs_reorder_empty() {
        let perm = bfs_reorder(0, &[]);
        assert!(perm.is_empty());
    }

    #[test]
    fn test_bfs_reorder_disconnected() {
        // Two disconnected components: {0,1} and {2,3}
        let edges = vec![
            (0u32, 1, EdgeKind::Calls),
            (2, 3, EdgeKind::Calls),
        ];
        let perm = bfs_reorder(4, &edges);
        // All nodes assigned unique indices 0..4
        let mut sorted = perm.clone();
        sorted.sort();
        assert_eq!(sorted, vec![0, 1, 2, 3]);
    }

    #[test]
    fn test_bfs_reorder_is_permutation() {
        let edges = vec![
            (0u32, 1, EdgeKind::Calls),
            (1, 2, EdgeKind::Imports),
            (2, 3, EdgeKind::Contains),
            (3, 0, EdgeKind::Uses),
        ];
        let perm = bfs_reorder(4, &edges);
        let mut sorted = perm.clone();
        sorted.sort();
        assert_eq!(sorted, vec![0, 1, 2, 3]);
    }
}
