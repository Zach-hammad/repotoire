//! Pre-computed graph algorithm results.
//!
//! `GraphPrimitives` is computed once during `GraphIndexes::build()` and provides
//! pre-computed dominator trees, articulation points, PageRank, betweenness
//! centrality, and call-graph SCCs. All fields are immutable after construction.
//! Detectors read them at O(1) — zero graph traversal at detection time.

use petgraph::stable_graph::{NodeIndex, StableGraph};
use std::collections::{HashMap, HashSet};

use super::store_models::{CodeEdge, CodeNode};

// SAFETY: GraphPrimitives contains only HashMap, HashSet, Vec, and f64 —
// all Send + Sync. Adding it to GraphIndexes (inside CodeGraph) does not
// violate the existing unsafe impl Send/Sync for CodeGraph.

/// Pre-computed graph algorithm results. Computed once during freeze().
/// All fields are immutable. O(1) access from any detector via CodeGraph.
#[derive(Default)]
pub struct GraphPrimitives {
    // ── Dominator analysis (directed call graph) ──
    pub(crate) idom: HashMap<NodeIndex, NodeIndex>,
    pub(crate) dominated: HashMap<NodeIndex, Vec<NodeIndex>>,
    pub(crate) frontier: HashMap<NodeIndex, Vec<NodeIndex>>,
    pub(crate) dom_depth: HashMap<NodeIndex, usize>,

    // ── Structural connectivity (undirected call+import graph) ──
    pub(crate) articulation_points: Vec<NodeIndex>,
    pub(crate) articulation_point_set: HashSet<NodeIndex>,
    pub(crate) bridges: Vec<(NodeIndex, NodeIndex)>,
    pub(crate) component_sizes: HashMap<NodeIndex, Vec<usize>>,

    // ── Call-graph cycles ──
    pub(crate) call_cycles: Vec<Vec<NodeIndex>>,

    // ── Centrality metrics ──
    pub(crate) page_rank: HashMap<NodeIndex, f64>,
    pub(crate) betweenness: HashMap<NodeIndex, f64>,

    // ── BFS call depth ──
    pub(crate) call_depth: HashMap<NodeIndex, usize>,
}

impl GraphPrimitives {
    /// Compute all graph primitives. Called by GraphIndexes::build().
    /// Returns Default for empty graphs.
    pub fn compute(
        graph: &StableGraph<CodeNode, CodeEdge>,
        functions: &[NodeIndex],
        files: &[NodeIndex],
        all_call_edges: &[(NodeIndex, NodeIndex)],
        all_import_edges: &[(NodeIndex, NodeIndex)],
        call_callers: &HashMap<NodeIndex, Vec<NodeIndex>>,
        call_callees: &HashMap<NodeIndex, Vec<NodeIndex>>,
        edge_fingerprint: u64,
    ) -> Self {
        if functions.is_empty() || all_call_edges.is_empty() {
            return Self::default();
        }
        // Algorithms added in subsequent tasks
        Self::default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_is_empty() {
        let p = GraphPrimitives::default();
        assert!(p.idom.is_empty());
        assert!(p.dominated.is_empty());
        assert!(p.page_rank.is_empty());
        assert!(p.call_cycles.is_empty());
        assert!(p.articulation_points.is_empty());
    }

    #[test]
    fn test_compute_empty_graph_returns_default() {
        let graph = StableGraph::new();
        let p = GraphPrimitives::compute(
            &graph, &[], &[], &[], &[], &HashMap::new(), &HashMap::new(), 0,
        );
        assert!(p.idom.is_empty());
        assert!(p.page_rank.is_empty());
    }
}
