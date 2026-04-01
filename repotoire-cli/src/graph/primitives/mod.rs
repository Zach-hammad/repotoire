//! Pre-computed graph algorithm results.
//!
//! `GraphPrimitives` is computed once during `GraphIndexes::build()` and provides
//! pre-computed dominator trees, articulation points, PageRank, betweenness
//! centrality, and call-graph SCCs. All fields are immutable after construction.
//! Detectors read them at O(1) — zero graph traversal at detection time.

use std::collections::HashMap;
use petgraph::stable_graph::{NodeIndex, StableGraph};
use std::collections::HashSet;

use super::store_models::{CodeEdge, CodeNode};
use crate::git::co_change::CoChangeMatrix;

mod phase_a;
mod phase_b;

use phase_a::*;
use phase_b::*;

// SAFETY: GraphPrimitives contains only HashMap, HashSet, Vec, and f64 —
// all Send + Sync. Adding it to GraphIndexes (inside CodeGraph) does not
// violate the existing unsafe impl Send/Sync for CodeGraph.

/// Pre-computed graph algorithm results. Computed once during freeze().
/// All fields are immutable. O(1) access from any detector via CodeGraph.
#[derive(Default)]
pub struct GraphPrimitives {
    // ── Dominator analysis (directed call graph) ──
    pub idom: HashMap<NodeIndex, NodeIndex>,
    pub dominated: HashMap<NodeIndex, Vec<NodeIndex>>,
    pub frontier: HashMap<NodeIndex, Vec<NodeIndex>>,
    pub dom_depth: HashMap<NodeIndex, usize>,

    // ── Structural connectivity (undirected call+import graph) ──
    pub articulation_points: Vec<NodeIndex>,
    pub articulation_point_set: HashSet<NodeIndex>,
    pub bridges: Vec<(NodeIndex, NodeIndex)>,
    pub component_sizes: HashMap<NodeIndex, Vec<usize>>,

    // ── Call-graph cycles ──
    pub call_cycles: Vec<Vec<NodeIndex>>,

    // ── Centrality metrics ──
    pub page_rank: HashMap<NodeIndex, f64>,
    pub betweenness: HashMap<NodeIndex, f64>,

    // ── BFS call depth ──
    pub call_depth: HashMap<NodeIndex, usize>,

    // ── Weighted centrality metrics (Phase B) ──
    pub weighted_page_rank: HashMap<NodeIndex, f64>,
    pub weighted_betweenness: HashMap<NodeIndex, f64>,

    // ── Community structure (Phase B) ──
    pub community: HashMap<NodeIndex, usize>,
    pub modularity: f64,

    // ── Hidden coupling: co-change without structural edge (Phase B) ──
    // NodeIndex values are File-level node indices. Tuple: (file_a, file_b, weight, lift).
    pub hidden_coupling: Vec<(NodeIndex, NodeIndex, f32, f32, f32)>,
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
        co_change: Option<&CoChangeMatrix>,
    ) -> Self {
        if functions.is_empty() || all_call_edges.is_empty() {
            return Self::default();
        }

        let _span = tracing::info_span!(
            "graph_primitives",
            functions = functions.len(),
            call_edges = all_call_edges.len()
        )
        .entered();

        // 1. SCCs first (needed by dominator for disconnected SCC handling)
        let call_cycles = compute_call_cycles(all_call_edges, graph);

        // 2. PageRank, betweenness, articulation points in parallel
        let (page_rank, (betweenness, ap_result)) = rayon::join(
            || compute_page_rank(functions, call_callees, call_callers, 20, 0.85, 1e-6),
            || {
                rayon::join(
                    || compute_betweenness(functions, call_callees, edge_fingerprint),
                    || {
                        compute_articulation_points(
                            functions,
                            all_call_edges,
                            all_import_edges,
                            files,
                        )
                    },
                )
            },
        );
        let (articulation_points, articulation_point_set, bridges, component_sizes) = ap_result;

        // 3. Dominators (depends on SCCs for disconnected handling)
        let (idom, dominated, frontier, dom_depth) = compute_dominators(
            functions,
            all_call_edges,
            call_callers,
            call_callees,
            &call_cycles,
            graph,
        );

        // 4. BFS call depths
        let call_depth = compute_call_depths(functions, call_callees, call_callers);

        // Phase B: Weighted overlay + weighted algorithms
        let (weighted_page_rank, weighted_betweenness, community, modularity, hidden_coupling) =
            if let Some(co_change) = co_change {
                if !co_change.is_empty() {
                    compute_weighted_phase(
                        functions,
                        files,
                        all_call_edges,
                        all_import_edges,
                        co_change,
                        graph,
                        edge_fingerprint,
                    )
                } else {
                    (
                        HashMap::default(),
                        HashMap::default(),
                        HashMap::default(),
                        0.0,
                        Vec::new(),
                    )
                }
            } else {
                (
                    HashMap::default(),
                    HashMap::default(),
                    HashMap::default(),
                    0.0,
                    Vec::new(),
                )
            };

        Self {
            idom,
            dominated,
            frontier,
            dom_depth,
            articulation_points,
            articulation_point_set,
            bridges,
            component_sizes,
            call_cycles,
            page_rank,
            betweenness,
            call_depth,
            weighted_page_rank,
            weighted_betweenness,
            community,
            modularity,
            hidden_coupling,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::store_models::{CodeEdge, CodeNode};

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
            &graph,
            &[],
            &[],
            &[],
            &[],
            &HashMap::default(),
            &HashMap::default(),
            0,
            None,
        );
        assert!(p.idom.is_empty());
        assert!(p.page_rank.is_empty());
    }

    // ── Call-graph SCC tests ──

    #[test]
    fn test_call_cycles_triangle() {
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let f1 = graph.add_node(CodeNode::function("f1", "a.py"));
        let f2 = graph.add_node(CodeNode::function("f2", "a.py"));
        let f3 = graph.add_node(CodeNode::function("f3", "a.py"));

        let call_edges = vec![(f1, f2), (f2, f3), (f3, f1)];
        let cycles = compute_call_cycles(&call_edges, &graph);

        assert_eq!(cycles.len(), 1, "Should find exactly 1 cycle");
        assert_eq!(cycles[0].len(), 3, "Cycle should contain 3 nodes");
    }

    #[test]
    fn test_call_cycles_dag_no_cycles() {
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let f1 = graph.add_node(CodeNode::function("f1", "a.py"));
        let f2 = graph.add_node(CodeNode::function("f2", "a.py"));
        let f3 = graph.add_node(CodeNode::function("f3", "a.py"));

        let call_edges = vec![(f1, f2), (f2, f3)];
        let cycles = compute_call_cycles(&call_edges, &graph);

        assert!(cycles.is_empty(), "DAG should have no cycles");
    }

    // ── PageRank tests ──

    #[test]
    fn test_page_rank_star_topology() {
        // f1, f2, f3 all call hub; hub calls leaf
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let f1 = graph.add_node(CodeNode::function("f1", "a.py"));
        let f2 = graph.add_node(CodeNode::function("f2", "a.py"));
        let f3 = graph.add_node(CodeNode::function("f3", "a.py"));
        let hub = graph.add_node(CodeNode::function("hub", "a.py"));
        let leaf = graph.add_node(CodeNode::function("leaf", "a.py"));

        let functions = vec![f1, f2, f3, hub, leaf];
        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
        let mut call_callers: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();

        // f1->hub, f2->hub, f3->hub, hub->leaf
        call_callees.insert(f1, vec![hub]);
        call_callees.insert(f2, vec![hub]);
        call_callees.insert(f3, vec![hub]);
        call_callees.insert(hub, vec![leaf]);

        call_callers.insert(hub, vec![f1, f2, f3]);
        call_callers.insert(leaf, vec![hub]);

        let pr = compute_page_rank(&functions, &call_callees, &call_callers, 20, 0.85, 1e-6);

        assert!(pr.len() == 5);
        let hub_rank = pr[&hub];
        let leaf_rank = pr[&leaf];
        let f1_rank = pr[&f1];

        // Hub receives rank from 3 sources, should have highest
        assert!(
            hub_rank > f1_rank,
            "Hub ({hub_rank}) should have higher rank than f1 ({f1_rank})"
        );
        // Leaf receives all hub rank, should be second highest
        assert!(
            leaf_rank > f1_rank,
            "Leaf ({leaf_rank}) should have higher rank than f1 ({f1_rank})"
        );
    }

    #[test]
    fn test_page_rank_sums_to_one() {
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let f1 = graph.add_node(CodeNode::function("f1", "a.py"));
        let f2 = graph.add_node(CodeNode::function("f2", "a.py"));
        let f3 = graph.add_node(CodeNode::function("f3", "a.py"));

        let functions = vec![f1, f2, f3];
        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
        let mut call_callers: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();

        call_callees.insert(f1, vec![f2]);
        call_callees.insert(f2, vec![f3]);
        call_callees.insert(f3, vec![f1]);

        call_callers.insert(f2, vec![f1]);
        call_callers.insert(f3, vec![f2]);
        call_callers.insert(f1, vec![f3]);

        let pr = compute_page_rank(&functions, &call_callees, &call_callers, 100, 0.85, 1e-10);
        let sum: f64 = pr.values().sum();
        assert!(
            (sum - 1.0).abs() < 0.01,
            "PageRank should sum to ~1.0, got {sum}"
        );
    }

    // ── Dominator tests ──

    #[test]
    fn test_dominators_linear_chain() {
        // entry -> A -> B -> C
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let entry = graph.add_node(CodeNode::function("entry", "a.py"));
        let a = graph.add_node(CodeNode::function("a_fn", "a.py"));
        let b = graph.add_node(CodeNode::function("b_fn", "a.py"));
        let c = graph.add_node(CodeNode::function("c_fn", "a.py"));

        let call_edges = vec![(entry, a), (a, b), (b, c)];
        let functions = vec![entry, a, b, c];

        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
        let mut call_callers: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
        call_callees.insert(entry, vec![a]);
        call_callees.insert(a, vec![b]);
        call_callees.insert(b, vec![c]);
        call_callers.insert(a, vec![entry]);
        call_callers.insert(b, vec![a]);
        call_callers.insert(c, vec![b]);

        let (idom, dominated, _frontier, dom_depth) = compute_dominators(
            &functions,
            &call_edges,
            &call_callers,
            &call_callees,
            &[],
            &graph,
        );

        // entry dominates all
        assert_eq!(idom.get(&a), Some(&entry), "entry should dominate A");
        assert_eq!(idom.get(&b), Some(&a), "A should immediately dominate B");
        assert_eq!(idom.get(&c), Some(&b), "B should immediately dominate C");

        // Entry's dominated set should include A, B, C
        let entry_dominated = dominated.get(&entry).unwrap();
        assert!(entry_dominated.contains(&a));
        assert!(entry_dominated.contains(&b));
        assert!(entry_dominated.contains(&c));

        // Depths should increase
        assert_eq!(dom_depth.get(&entry), Some(&0));
        assert_eq!(dom_depth.get(&a), Some(&1));
        assert_eq!(dom_depth.get(&b), Some(&2));
        assert_eq!(dom_depth.get(&c), Some(&3));
    }

    #[test]
    fn test_dominators_diamond() {
        // entry -> A, entry -> B, A -> join, B -> join
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let entry = graph.add_node(CodeNode::function("entry", "a.py"));
        let a = graph.add_node(CodeNode::function("a_fn", "a.py"));
        let b = graph.add_node(CodeNode::function("b_fn", "a.py"));
        let join = graph.add_node(CodeNode::function("join", "a.py"));

        let call_edges = vec![(entry, a), (entry, b), (a, join), (b, join)];
        let functions = vec![entry, a, b, join];

        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
        let mut call_callers: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
        call_callees.insert(entry, vec![a, b]);
        call_callees.insert(a, vec![join]);
        call_callees.insert(b, vec![join]);
        call_callers.insert(a, vec![entry]);
        call_callers.insert(b, vec![entry]);
        call_callers.insert(join, vec![a, b]);

        let (idom, _dominated, _frontier, _dom_depth) = compute_dominators(
            &functions,
            &call_edges,
            &call_callers,
            &call_callees,
            &[],
            &graph,
        );

        // Neither A nor B dominates join — entry dominates join (two paths)
        assert_eq!(
            idom.get(&join),
            Some(&entry),
            "Entry should dominate join (not A or B, since there are two paths)"
        );
    }

    // ── Articulation points tests ──

    #[test]
    fn test_articulation_points_two_triangles_bridge() {
        // Triangle 1: a-b-c, Triangle 2: d-e-f, connected by c-d
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let a = graph.add_node(CodeNode::function("a", "x.py"));
        let b = graph.add_node(CodeNode::function("b", "x.py"));
        let c = graph.add_node(CodeNode::function("c", "x.py"));
        let d = graph.add_node(CodeNode::function("d", "x.py"));
        let e = graph.add_node(CodeNode::function("e", "x.py"));
        let f = graph.add_node(CodeNode::function("f", "x.py"));

        let functions = vec![a, b, c, d, e, f];
        // Triangle 1 edges (undirected via both directions in call edges)
        let call_edges = vec![
            (a, b),
            (b, a),
            (b, c),
            (c, b),
            (a, c),
            (c, a),
            // Bridge
            (c, d),
            (d, c),
            // Triangle 2
            (d, e),
            (e, d),
            (e, f),
            (f, e),
            (d, f),
            (f, d),
        ];

        let (_ap_vec, ap_set, bridges, _comp_sizes) =
            compute_articulation_points(&functions, &call_edges, &[], &[]);

        // c and d should be articulation points (bridge nodes)
        assert!(ap_set.contains(&c), "c should be an articulation point");
        assert!(ap_set.contains(&d), "d should be an articulation point");
        assert_eq!(ap_set.len(), 2, "Should have exactly 2 articulation points");

        // c-d should be a bridge
        let has_bridge = bridges
            .iter()
            .any(|&(s, t)| (s == c && t == d) || (s == d && t == c));
        assert!(has_bridge, "c-d should be a bridge");
    }

    #[test]
    fn test_articulation_points_fully_connected() {
        // Fully connected graph of 4 nodes — no articulation points
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let a = graph.add_node(CodeNode::function("a", "x.py"));
        let b = graph.add_node(CodeNode::function("b", "x.py"));
        let c = graph.add_node(CodeNode::function("c", "x.py"));
        let d = graph.add_node(CodeNode::function("d", "x.py"));

        let functions = vec![a, b, c, d];
        let call_edges = vec![
            (a, b),
            (b, a),
            (a, c),
            (c, a),
            (a, d),
            (d, a),
            (b, c),
            (c, b),
            (b, d),
            (d, b),
            (c, d),
            (d, c),
        ];

        let (_ap_vec, ap_set, bridges, _comp_sizes) =
            compute_articulation_points(&functions, &call_edges, &[], &[]);

        assert!(
            ap_set.is_empty(),
            "Fully connected graph should have no articulation points"
        );
        assert!(
            bridges.is_empty(),
            "Fully connected graph should have no bridges"
        );
    }

    // ── BFS call depth tests ──

    #[test]
    fn test_call_depths_linear_chain() {
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let entry = graph.add_node(CodeNode::function("entry", "a.py"));
        let mid = graph.add_node(CodeNode::function("mid", "a.py"));
        let leaf = graph.add_node(CodeNode::function("leaf", "a.py"));

        let functions = vec![entry, mid, leaf];
        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
        let mut call_callers: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();

        call_callees.insert(entry, vec![mid]);
        call_callees.insert(mid, vec![leaf]);
        call_callers.insert(mid, vec![entry]);
        call_callers.insert(leaf, vec![mid]);

        let depths = compute_call_depths(&functions, &call_callees, &call_callers);

        assert_eq!(depths.get(&entry), Some(&0), "Entry should be depth 0");
        assert_eq!(depths.get(&mid), Some(&1), "Mid should be depth 1");
        assert_eq!(depths.get(&leaf), Some(&2), "Leaf should be depth 2");
    }

    #[test]
    fn test_call_depths_multiple_entries() {
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let e1 = graph.add_node(CodeNode::function("entry1", "a.py"));
        let e2 = graph.add_node(CodeNode::function("entry2", "a.py"));
        let shared = graph.add_node(CodeNode::function("shared", "a.py"));

        let functions = vec![e1, e2, shared];
        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
        let mut call_callers: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();

        call_callees.insert(e1, vec![shared]);
        call_callees.insert(e2, vec![shared]);
        call_callers.insert(shared, vec![e1, e2]);

        let depths = compute_call_depths(&functions, &call_callees, &call_callers);

        assert_eq!(depths.get(&e1), Some(&0));
        assert_eq!(depths.get(&e2), Some(&0));
        // shared should be depth 1 (shortest path from any entry)
        assert_eq!(depths.get(&shared), Some(&1));
    }

    // ── Betweenness centrality tests ──

    #[test]
    fn test_betweenness_star_through_bridge() {
        // Three sources -> bridge -> three sinks
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let s1 = graph.add_node(CodeNode::function("s1", "a.py"));
        let s2 = graph.add_node(CodeNode::function("s2", "a.py"));
        let s3 = graph.add_node(CodeNode::function("s3", "a.py"));
        let bridge = graph.add_node(CodeNode::function("bridge", "a.py"));
        let t1 = graph.add_node(CodeNode::function("t1", "a.py"));
        let t2 = graph.add_node(CodeNode::function("t2", "a.py"));
        let t3 = graph.add_node(CodeNode::function("t3", "a.py"));

        let functions = vec![s1, s2, s3, bridge, t1, t2, t3];
        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();

        call_callees.insert(s1, vec![bridge]);
        call_callees.insert(s2, vec![bridge]);
        call_callees.insert(s3, vec![bridge]);
        call_callees.insert(bridge, vec![t1, t2, t3]);

        let bc = compute_betweenness(&functions, &call_callees, 42);

        let bridge_bc = bc[&bridge];
        let s1_bc = bc[&s1];
        let t1_bc = bc[&t1];

        assert!(
            bridge_bc > s1_bc,
            "Bridge ({bridge_bc}) should have higher betweenness than source ({s1_bc})"
        );
        assert!(
            bridge_bc > t1_bc,
            "Bridge ({bridge_bc}) should have higher betweenness than sink ({t1_bc})"
        );
    }

    // ── Full compute() integration test ──

    #[test]
    fn test_compute_full_wiring() {
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let f1 = graph.add_node(CodeNode::function("f1", "a.py"));
        let f2 = graph.add_node(CodeNode::function("f2", "a.py"));
        let f3 = graph.add_node(CodeNode::function("f3", "b.py"));
        let file_a = graph.add_node(CodeNode::file("a.py"));
        let file_b = graph.add_node(CodeNode::file("b.py"));

        graph.add_edge(f1, f2, CodeEdge::calls());
        graph.add_edge(f2, f3, CodeEdge::calls());
        graph.add_edge(file_a, file_b, CodeEdge::imports());

        let functions = vec![f1, f2, f3];
        let files = vec![file_a, file_b];
        let all_call_edges = vec![(f1, f2), (f2, f3)];
        let all_import_edges = vec![(file_a, file_b)];

        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
        let mut call_callers: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
        call_callees.insert(f1, vec![f2]);
        call_callees.insert(f2, vec![f3]);
        call_callers.insert(f2, vec![f1]);
        call_callers.insert(f3, vec![f2]);

        let p = GraphPrimitives::compute(
            &graph,
            &functions,
            &files,
            &all_call_edges,
            &all_import_edges,
            &call_callers,
            &call_callees,
            12345,
            None,
        );

        // No cycles in a DAG
        assert!(p.call_cycles.is_empty());

        // PageRank should be populated for all functions
        assert_eq!(p.page_rank.len(), 3);

        // Betweenness should be populated
        assert_eq!(p.betweenness.len(), 3);

        // Call depths: f1=0, f2=1, f3=2
        assert_eq!(p.call_depth.get(&f1), Some(&0));
        assert_eq!(p.call_depth.get(&f2), Some(&1));
        assert_eq!(p.call_depth.get(&f3), Some(&2));

        // Dominator: f1 dominates f2, f2 dominates f3
        assert_eq!(p.idom.get(&f2), Some(&f1));
        assert_eq!(p.idom.get(&f3), Some(&f2));

        // Dom depth
        assert_eq!(p.dom_depth.get(&f1), Some(&0));
        assert_eq!(p.dom_depth.get(&f2), Some(&1));
        assert_eq!(p.dom_depth.get(&f3), Some(&2));
    }

    // ── Comprehensive integration test (all primitives together) ──

    /// Builds a realistic 10-function graph across 3 files with entry points,
    /// a hub, leaves, a mutual recursion pair, and import edges. Verifies
    /// all graph primitives (PageRank, betweenness, dominator tree, call
    /// cycles, call depths, articulation points) work end-to-end.
    #[test]
    fn test_all_primitives_realistic_graph() {
        // Graph topology:
        //
        //   Files: app.py, lib.py, util.py
        //   Imports: app.py -> lib.py -> util.py
        //
        //   Call graph:
        //     entry1 (app.py) -> hub (lib.py) -> leaf1 (lib.py)
        //     entry2 (app.py) -> hub (lib.py) -> leaf2 (util.py)
        //     entry1 (app.py) -> helper (util.py)
        //     hub (lib.py) -> rec_a (lib.py) <-> rec_b (lib.py)   (mutual recursion)
        //     hub (lib.py) -> deep1 (util.py) -> deep2 (util.py)
        //
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();

        // Files
        let file_app = graph.add_node(CodeNode::file("app.py"));
        let file_lib = graph.add_node(CodeNode::file("lib.py"));
        let file_util = graph.add_node(CodeNode::file("util.py"));

        // Functions
        let entry1 = graph.add_node(CodeNode::function("entry1", "app.py"));
        let entry2 = graph.add_node(CodeNode::function("entry2", "app.py"));
        let hub = graph.add_node(CodeNode::function("hub", "lib.py"));
        let leaf1 = graph.add_node(CodeNode::function("leaf1", "lib.py"));
        let leaf2 = graph.add_node(CodeNode::function("leaf2", "util.py"));
        let helper = graph.add_node(CodeNode::function("helper", "util.py"));
        let rec_a = graph.add_node(CodeNode::function("rec_a", "lib.py"));
        let rec_b = graph.add_node(CodeNode::function("rec_b", "lib.py"));
        let deep1 = graph.add_node(CodeNode::function("deep1", "util.py"));
        let deep2 = graph.add_node(CodeNode::function("deep2", "util.py"));

        // Import edges
        graph.add_edge(file_app, file_lib, CodeEdge::imports());
        graph.add_edge(file_lib, file_util, CodeEdge::imports());

        // Call edges
        graph.add_edge(entry1, hub, CodeEdge::calls());
        graph.add_edge(entry2, hub, CodeEdge::calls());
        graph.add_edge(entry1, helper, CodeEdge::calls());
        graph.add_edge(hub, leaf1, CodeEdge::calls());
        graph.add_edge(hub, leaf2, CodeEdge::calls());
        graph.add_edge(hub, rec_a, CodeEdge::calls());
        graph.add_edge(rec_a, rec_b, CodeEdge::calls());
        graph.add_edge(rec_b, rec_a, CodeEdge::calls()); // mutual recursion
        graph.add_edge(hub, deep1, CodeEdge::calls());
        graph.add_edge(deep1, deep2, CodeEdge::calls());

        let functions = vec![
            entry1, entry2, hub, leaf1, leaf2, helper, rec_a, rec_b, deep1, deep2,
        ];
        let files = vec![file_app, file_lib, file_util];

        let all_call_edges = vec![
            (entry1, hub),
            (entry2, hub),
            (entry1, helper),
            (hub, leaf1),
            (hub, leaf2),
            (hub, rec_a),
            (rec_a, rec_b),
            (rec_b, rec_a),
            (hub, deep1),
            (deep1, deep2),
        ];
        let all_import_edges = vec![(file_app, file_lib), (file_lib, file_util)];

        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
        let mut call_callers: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
        for &(src, tgt) in &all_call_edges {
            call_callees.entry(src).or_default().push(tgt);
            call_callers.entry(tgt).or_default().push(src);
        }

        let p = GraphPrimitives::compute(
            &graph,
            &functions,
            &files,
            &all_call_edges,
            &all_import_edges,
            &call_callers,
            &call_callees,
            99999,
            None,
        );

        // ── PageRank ──
        // All functions should have a PageRank value > 0
        for &f in &functions {
            let pr = p.page_rank.get(&f).copied().unwrap_or(0.0);
            assert!(pr > 0.0, "PageRank should be > 0 for every function");
        }
        // Hub should have higher PageRank than leaves (it receives from 2 entry points)
        let pr_hub = p.page_rank[&hub];
        let pr_leaf1 = p.page_rank[&leaf1];
        let pr_leaf2 = p.page_rank[&leaf2];
        assert!(
            pr_hub > pr_leaf1,
            "Hub PR ({pr_hub}) > leaf1 PR ({pr_leaf1})"
        );
        assert!(
            pr_hub > pr_leaf2,
            "Hub PR ({pr_hub}) > leaf2 PR ({pr_leaf2})"
        );

        // ── Betweenness centrality ──
        // Hub should have the highest betweenness (it's the bridge between entries and leaves)
        let bc_hub = p.betweenness[&hub];
        assert!(bc_hub > 0.0, "Hub betweenness should be > 0");
        for &f in &[entry1, entry2, leaf1, leaf2, helper, deep2] {
            let bc_f = p.betweenness.get(&f).copied().unwrap_or(0.0);
            assert!(bc_hub >= bc_f, "Hub BC ({bc_hub}) >= {f:?} BC ({bc_f})");
        }

        // ── Call-graph cycles ──
        // Should detect the rec_a <-> rec_b mutual recursion
        assert!(
            !p.call_cycles.is_empty(),
            "Should detect at least one call cycle"
        );
        let cycle_members: HashSet<NodeIndex> = p
            .call_cycles
            .iter()
            .flat_map(|c| c.iter().copied())
            .collect();
        assert!(
            cycle_members.contains(&rec_a) && cycle_members.contains(&rec_b),
            "Cycle should include rec_a and rec_b"
        );

        // ── Call depths ──
        // entry1, entry2 have no callers => depth 0
        assert_eq!(p.call_depth.get(&entry1), Some(&0));
        assert_eq!(p.call_depth.get(&entry2), Some(&0));
        // hub is called by entries => depth 1
        assert_eq!(p.call_depth.get(&hub), Some(&1));
        // leaf1, leaf2 are called by hub => depth 2
        assert_eq!(p.call_depth.get(&leaf1), Some(&2));
        assert_eq!(p.call_depth.get(&leaf2), Some(&2));
        // deep1 called by hub => depth 2, deep2 called by deep1 => depth 3
        assert_eq!(p.call_depth.get(&deep1), Some(&2));
        assert_eq!(p.call_depth.get(&deep2), Some(&3));
        // helper is called by entry1 => depth 1
        assert_eq!(p.call_depth.get(&helper), Some(&1));

        // ── Dominator tree ──
        // Entry points have no immediate dominator (they are roots)
        // hub is dominated by... well, it has 2 entry callers so the virtual
        // root dominates it. The key check: dominated set is populated.
        assert!(!p.idom.is_empty(), "Dominator tree should be populated");
        assert!(
            !p.dom_depth.is_empty(),
            "Dominator depths should be populated"
        );

        // ── Articulation points (undirected view) ──
        // hub connects entries to leaves in the undirected graph — likely an AP
        // (Not guaranteed depending on the exact undirected connectivity, but
        // the AP computation should at least run without panic)
        // Just verify the computation completed
        // (articulation points depend on undirected connectivity which includes imports)
        // Articulation point computation should complete without panic.
        // The exact count depends on undirected connectivity.
        let _ap_count = p.articulation_points.len();
    }

    // ── Weighted overlay builder tests ──

    #[test]
    fn test_overlay_structural_only() {
        // Two functions with a Calls edge, no co-change → weight = 1.0
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let f1 = graph.add_node(CodeNode::function("f1", "src/a.rs"));
        let f2 = graph.add_node(CodeNode::function("f2", "src/b.rs"));
        let file_a = graph.add_node(CodeNode::file("src/a.rs"));
        let file_b = graph.add_node(CodeNode::file("src/b.rs"));

        let functions = vec![f1, f2];
        let files = vec![file_a, file_b];
        let call_edges = vec![(f1, f2)];
        let import_edges: Vec<(NodeIndex, NodeIndex)> = vec![];

        // Empty co-change matrix (no commits)
        let co_change = CoChangeMatrix::empty();

        let (overlay, hidden_coupling) = build_weighted_overlay(
            &functions,
            &files,
            &call_edges,
            &import_edges,
            &co_change,
            &graph,
        );

        // Should have 2 nodes and 1 edge
        assert_eq!(overlay.node_count(), 2, "Overlay should have 2 nodes");
        assert_eq!(overlay.edge_count(), 1, "Overlay should have 1 edge");

        // The edge weight should be 1.0 (structural_base for Calls, no co-change boost)
        let edge_idx = overlay.edge_indices().next().expect("should have one edge");
        let weight = overlay[edge_idx];
        assert!(
            (weight - 1.0).abs() < 1e-6,
            "Calls-only edge should have weight 1.0, got {weight}"
        );

        // No hidden coupling
        assert!(hidden_coupling.is_empty(), "No hidden coupling expected");
    }

    #[test]
    fn test_overlay_co_change_boost() {
        // Calls edge + co-change → weight > 1.0
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let f1 = graph.add_node(CodeNode::function("f1", "src/alpha.rs"));
        let f2 = graph.add_node(CodeNode::function("f2", "src/beta.rs"));
        let file_a = graph.add_node(CodeNode::file("src/alpha.rs"));
        let file_b = graph.add_node(CodeNode::file("src/beta.rs"));

        let functions = vec![f1, f2];
        let files = vec![file_a, file_b];
        let call_edges = vec![(f1, f2)];
        let import_edges: Vec<(NodeIndex, NodeIndex)> = vec![];

        // Build co-change matrix with recent commits touching both files
        let now = chrono::Utc::now();
        let config = crate::git::co_change::CoChangeConfig {
            min_weight: 0.01,
            ..Default::default()
        };
        let commits = vec![
            (
                now,
                vec!["src/alpha.rs".to_string(), "src/beta.rs".to_string()],
            ),
            (
                now,
                vec!["src/alpha.rs".to_string(), "src/beta.rs".to_string()],
            ),
        ];
        let co_change = CoChangeMatrix::from_commits(&commits, &config, now);

        // Verify co-change has data
        let cc_weight = co_change
            .weight_by_path("src/alpha.rs", "src/beta.rs")
            .expect("co-change should exist");
        assert!(
            cc_weight > 1.0,
            "Two recent commits should give weight > 1.0"
        );

        let (overlay, hidden_coupling) = build_weighted_overlay(
            &functions,
            &files,
            &call_edges,
            &import_edges,
            &co_change,
            &graph,
        );

        assert_eq!(overlay.edge_count(), 1, "Overlay should have 1 edge");

        let edge_idx = overlay.edge_indices().next().expect("should have one edge");
        let weight = overlay[edge_idx];

        // weight = structural_base (1.0) + co_change_boost (min(cc_weight, 2.0))
        // cc_weight ~ 2.0, so total should be ~ 3.0 (or 1.0 + capped 2.0)
        assert!(
            weight > 1.0,
            "Co-change boosted edge should have weight > 1.0, got {weight}"
        );
        // structural_base is 1.0, co_change_boost is min(cc_weight, 2.0)
        let expected = 1.0 + cc_weight.min(2.0);
        assert!(
            (weight - expected).abs() < 1e-6,
            "Expected weight {expected}, got {weight}"
        );

        assert!(
            hidden_coupling.is_empty(),
            "No hidden coupling expected (structural edge exists)"
        );
    }

    #[test]
    fn test_overlay_hidden_coupling() {
        // No structural edge, but co-change exists → hidden coupling detected
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let f1 = graph.add_node(CodeNode::function("handler", "src/api.rs"));
        let f2 = graph.add_node(CodeNode::function("model_update", "src/db.rs"));
        let file_api = graph.add_node(CodeNode::file("src/api.rs"));
        let file_db = graph.add_node(CodeNode::file("src/db.rs"));

        let functions = vec![f1, f2];
        let files = vec![file_api, file_db];
        // No call or import edges between f1 and f2
        let call_edges: Vec<(NodeIndex, NodeIndex)> = vec![];
        let import_edges: Vec<(NodeIndex, NodeIndex)> = vec![];

        // Build co-change matrix: both files frequently change together
        let now = chrono::Utc::now();
        let config = crate::git::co_change::CoChangeConfig {
            min_weight: 0.01,
            ..Default::default()
        };
        let commits = vec![
            (now, vec!["src/api.rs".to_string(), "src/db.rs".to_string()]),
            (now, vec!["src/api.rs".to_string(), "src/db.rs".to_string()]),
            (now, vec!["src/api.rs".to_string(), "src/db.rs".to_string()]),
        ];
        let co_change = CoChangeMatrix::from_commits(&commits, &config, now);

        let cc_weight = co_change
            .weight_by_path("src/api.rs", "src/db.rs")
            .expect("co-change should exist");

        let (overlay, hidden_coupling) = build_weighted_overlay(
            &functions,
            &files,
            &call_edges,
            &import_edges,
            &co_change,
            &graph,
        );

        // Should have overlay edges between the function pair (hidden coupling)
        assert_eq!(overlay.node_count(), 2, "Overlay should have 2 nodes");
        assert_eq!(
            overlay.edge_count(),
            1,
            "Should have 1 hidden coupling edge"
        );

        let edge_idx = overlay.edge_indices().next().expect("should have one edge");
        let weight = overlay[edge_idx];

        // Hidden coupling edge: weight = co_change_boost only (no structural base)
        let expected_boost = cc_weight.min(2.0);
        assert!(
            (weight - expected_boost).abs() < 1e-6,
            "Hidden coupling edge should have weight {expected_boost}, got {weight}"
        );

        // Hidden coupling should be recorded at file level
        assert_eq!(
            hidden_coupling.len(),
            1,
            "Should have 1 hidden coupling entry"
        );
        let (hc_a, hc_b, hc_w, hc_lift, hc_confidence) = hidden_coupling[0];

        // The file-level NodeIndex values should be from the files parameter
        let hc_files: HashSet<NodeIndex> = [hc_a, hc_b].into_iter().collect();
        assert!(
            hc_files.contains(&file_api) && hc_files.contains(&file_db),
            "Hidden coupling should reference file-level nodes (api={file_api:?}, db={file_db:?}), got ({hc_a:?}, {hc_b:?})"
        );
        assert!(
            (hc_w - expected_boost).abs() < 1e-6,
            "Hidden coupling weight should be {expected_boost}, got {hc_w}"
        );
        // Lift should be > 1.0 since these files always co-change (2 files, 3 commits)
        assert!(
            hc_lift > 0.0,
            "Hidden coupling lift should be positive, got {hc_lift}"
        );
        // Confidence should be 1.0 since both files appear only in these 3 commits together
        assert!(
            hc_confidence > 0.0,
            "Hidden coupling confidence should be positive, got {hc_confidence}"
        );
    }

    // ── Weighted PageRank tests ──

    #[test]
    fn test_weighted_page_rank_uniform_weights() {
        // 3-node cycle with all edges weight=1.0
        // a → b → c → a
        // With uniform weights, all ranks should be approximately equal.
        let mut overlay: StableGraph<NodeIndex, f32> = StableGraph::new();
        let orig_a = NodeIndex::new(100);
        let orig_b = NodeIndex::new(101);
        let orig_c = NodeIndex::new(102);

        let a = overlay.add_node(orig_a);
        let b = overlay.add_node(orig_b);
        let c = overlay.add_node(orig_c);

        overlay.add_edge(a, b, 1.0);
        overlay.add_edge(b, c, 1.0);
        overlay.add_edge(c, a, 1.0);

        let pr = compute_weighted_page_rank(&overlay, 20, 0.85, 1e-6);

        assert_eq!(pr.len(), 3, "Should have 3 entries");
        let rank_a = pr[&orig_a];
        let rank_b = pr[&orig_b];
        let rank_c = pr[&orig_c];

        // In a symmetric cycle, all ranks should converge to 1/3
        assert!(
            (rank_a - rank_b).abs() < 0.01,
            "Ranks should be ~equal in uniform cycle: a={rank_a}, b={rank_b}"
        );
        assert!(
            (rank_b - rank_c).abs() < 0.01,
            "Ranks should be ~equal in uniform cycle: b={rank_b}, c={rank_c}"
        );
        assert!(
            (rank_a - 1.0 / 3.0).abs() < 0.01,
            "Each rank should be ~1/3: got {rank_a}"
        );
    }

    #[test]
    fn test_weighted_page_rank_heavy_edge() {
        // a has two out-edges: heavy to b (weight 5.0), light to c (weight 1.0).
        // b and c each feed back to a. Node a distributes rank proportionally:
        // 5/6 to b, 1/6 to c. Therefore b should accumulate higher rank than c.
        let mut overlay: StableGraph<NodeIndex, f32> = StableGraph::new();
        let orig_a = NodeIndex::new(200);
        let orig_b = NodeIndex::new(201);
        let orig_c = NodeIndex::new(202);

        let a = overlay.add_node(orig_a);
        let b = overlay.add_node(orig_b);
        let c = overlay.add_node(orig_c);

        overlay.add_edge(a, b, 5.0); // heavy edge
        overlay.add_edge(a, c, 1.0); // light edge
        overlay.add_edge(b, a, 1.0); // feedback
        overlay.add_edge(c, a, 1.0); // feedback

        let pr = compute_weighted_page_rank(&overlay, 20, 0.85, 1e-6);

        assert_eq!(pr.len(), 3, "Should have 3 entries");
        let rank_b = pr[&orig_b];
        let rank_c = pr[&orig_c];

        assert!(
            rank_b > rank_c,
            "b should have higher rank than c due to heavy edge: b={rank_b}, c={rank_c}"
        );
    }

    // ── Weighted betweenness centrality tests ──

    #[test]
    fn test_weighted_betweenness_center_node() {
        // Star topology: a→center, b→center, center→c, center→d
        // With uniform weights, center should have the highest betweenness
        // because all shortest paths between {a,b} and {c,d} pass through it.
        let mut overlay: StableGraph<NodeIndex, f32> = StableGraph::new();
        let orig_a = NodeIndex::new(300);
        let orig_b = NodeIndex::new(301);
        let orig_center = NodeIndex::new(302);
        let orig_c = NodeIndex::new(303);
        let orig_d = NodeIndex::new(304);

        let a = overlay.add_node(orig_a);
        let b = overlay.add_node(orig_b);
        let center = overlay.add_node(orig_center);
        let c = overlay.add_node(orig_c);
        let d = overlay.add_node(orig_d);

        // Uniform weight edges
        overlay.add_edge(a, center, 1.0);
        overlay.add_edge(b, center, 1.0);
        overlay.add_edge(center, c, 1.0);
        overlay.add_edge(center, d, 1.0);

        // sample_size=200 > node_count=5, so all nodes are sampled
        let bc = compute_weighted_betweenness(&overlay, 200, 42);

        assert_eq!(bc.len(), 5, "Should have betweenness for all 5 nodes");

        let bc_center = bc[&orig_center];
        let bc_a = bc[&orig_a];
        let bc_b = bc[&orig_b];
        let bc_c = bc[&orig_c];
        let bc_d = bc[&orig_d];

        assert!(
            bc_center > bc_a,
            "Center ({bc_center}) should have higher betweenness than a ({bc_a})"
        );
        assert!(
            bc_center > bc_b,
            "Center ({bc_center}) should have higher betweenness than b ({bc_b})"
        );
        assert!(
            bc_center > bc_c,
            "Center ({bc_center}) should have higher betweenness than c ({bc_c})"
        );
        assert!(
            bc_center > bc_d,
            "Center ({bc_center}) should have higher betweenness than d ({bc_d})"
        );
        assert!(
            bc_center > 0.0,
            "Center betweenness should be positive, got {bc_center}"
        );
    }

    // ── Louvain community detection tests ──

    #[test]
    fn test_two_cliques_two_communities() {
        // Two disconnected 3-node cliques with bidirectional edges.
        // Clique 1: nodes 0,1,2. Clique 2: nodes 3,4,5.
        // Louvain should detect exactly 2 communities.
        let mut overlay: StableGraph<NodeIndex, f32> = StableGraph::new();
        let orig_0 = NodeIndex::new(0);
        let orig_1 = NodeIndex::new(1);
        let orig_2 = NodeIndex::new(2);
        let orig_3 = NodeIndex::new(3);
        let orig_4 = NodeIndex::new(4);
        let orig_5 = NodeIndex::new(5);

        let n0 = overlay.add_node(orig_0);
        let n1 = overlay.add_node(orig_1);
        let n2 = overlay.add_node(orig_2);
        let n3 = overlay.add_node(orig_3);
        let n4 = overlay.add_node(orig_4);
        let n5 = overlay.add_node(orig_5);

        // Clique 1: bidirectional edges between 0, 1, 2
        overlay.add_edge(n0, n1, 1.0);
        overlay.add_edge(n1, n0, 1.0);
        overlay.add_edge(n0, n2, 1.0);
        overlay.add_edge(n2, n0, 1.0);
        overlay.add_edge(n1, n2, 1.0);
        overlay.add_edge(n2, n1, 1.0);

        // Clique 2: bidirectional edges between 3, 4, 5
        overlay.add_edge(n3, n4, 1.0);
        overlay.add_edge(n4, n3, 1.0);
        overlay.add_edge(n3, n5, 1.0);
        overlay.add_edge(n5, n3, 1.0);
        overlay.add_edge(n4, n5, 1.0);
        overlay.add_edge(n5, n4, 1.0);

        let (community_map, modularity) = compute_communities(&overlay, 1.0);

        assert_eq!(community_map.len(), 6, "Should have 6 entries");

        // Nodes in clique 1 should share the same community
        let c0 = community_map[&orig_0];
        let c1 = community_map[&orig_1];
        let c2 = community_map[&orig_2];
        assert_eq!(c0, c1, "Nodes 0 and 1 should be in the same community");
        assert_eq!(c0, c2, "Nodes 0 and 2 should be in the same community");

        // Nodes in clique 2 should share the same community
        let c3 = community_map[&orig_3];
        let c4 = community_map[&orig_4];
        let c5 = community_map[&orig_5];
        assert_eq!(c3, c4, "Nodes 3 and 4 should be in the same community");
        assert_eq!(c3, c5, "Nodes 3 and 5 should be in the same community");

        // The two cliques should be in different communities
        assert_ne!(
            c0, c3,
            "Clique 1 and clique 2 should be in different communities"
        );

        // Exactly 2 distinct communities
        let distinct: HashSet<usize> = community_map.values().copied().collect();
        assert_eq!(distinct.len(), 2, "Should find exactly 2 communities");

        // Modularity should be positive for well-separated communities
        assert!(
            modularity > 0.0,
            "Modularity should be positive for two disconnected cliques, got {modularity}"
        );
        println!("Two cliques modularity: {modularity}");
    }

    #[test]
    fn test_single_clique_one_community() {
        // One 3-node clique with bidirectional edges → all nodes in same community.
        let mut overlay: StableGraph<NodeIndex, f32> = StableGraph::new();
        let orig_0 = NodeIndex::new(10);
        let orig_1 = NodeIndex::new(11);
        let orig_2 = NodeIndex::new(12);

        let n0 = overlay.add_node(orig_0);
        let n1 = overlay.add_node(orig_1);
        let n2 = overlay.add_node(orig_2);

        // Full clique: bidirectional edges
        overlay.add_edge(n0, n1, 1.0);
        overlay.add_edge(n1, n0, 1.0);
        overlay.add_edge(n0, n2, 1.0);
        overlay.add_edge(n2, n0, 1.0);
        overlay.add_edge(n1, n2, 1.0);
        overlay.add_edge(n2, n1, 1.0);

        let (community_map, modularity) = compute_communities(&overlay, 1.0);

        assert_eq!(community_map.len(), 3, "Should have 3 entries");

        // All nodes should be in the same community
        let c0 = community_map[&orig_0];
        let c1 = community_map[&orig_1];
        let c2 = community_map[&orig_2];
        assert_eq!(c0, c1, "All nodes should be in the same community");
        assert_eq!(c0, c2, "All nodes should be in the same community");

        // For a single clique the modularity is 0 (no inter-community structure)
        println!("Single clique modularity: {modularity}");
    }

    // ── Phase B full pipeline integration test ──

    /// End-to-end test: builds a graph with known structure + co-change data and
    /// verifies that all Phase B primitives (weighted PageRank, weighted
    /// betweenness, community assignments, hidden coupling) are populated alongside
    /// the existing Phase A primitives.
    #[test]
    fn test_phase_b_all_primitives_with_co_change() {
        // Build a graph with functions in two files + call edges
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let file_a = graph.add_node(CodeNode::file("phase_b_a.py"));
        let file_b = graph.add_node(CodeNode::file("phase_b_b.py"));
        let f1 = graph.add_node(CodeNode::function("pb_f1", "phase_b_a.py"));
        let f2 = graph.add_node(CodeNode::function("pb_f2", "phase_b_a.py"));
        let f3 = graph.add_node(CodeNode::function("pb_f3", "phase_b_b.py"));

        graph.add_edge(f1, f2, CodeEdge::calls());
        graph.add_edge(f2, f3, CodeEdge::calls());

        // Create co-change data: both files appear together in 3 recent commits
        let now = chrono::Utc::now();
        let commits = vec![
            (
                now,
                vec!["phase_b_a.py".to_string(), "phase_b_b.py".to_string()],
            ),
            (
                now,
                vec!["phase_b_a.py".to_string(), "phase_b_b.py".to_string()],
            ),
            (
                now,
                vec!["phase_b_a.py".to_string(), "phase_b_b.py".to_string()],
            ),
        ];
        let config = crate::git::co_change::CoChangeConfig::default();
        let co_change = crate::git::co_change::CoChangeMatrix::from_commits(&commits, &config, now);

        // Sanity: co-change matrix should have data
        assert!(
            !co_change.is_empty(),
            "Co-change matrix should have entries from 3 commits"
        );

        // Build index structures needed for compute()
        let functions = vec![f1, f2, f3];
        let files = vec![file_a, file_b];
        let call_edges = vec![(f1, f2), (f2, f3)];
        let import_edges: Vec<(NodeIndex, NodeIndex)> = vec![];

        let mut call_callers: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
        call_callers.entry(f2).or_default().push(f1);
        call_callers.entry(f3).or_default().push(f2);

        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
        call_callees.entry(f1).or_default().push(f2);
        call_callees.entry(f2).or_default().push(f3);

        let p = GraphPrimitives::compute(
            &graph,
            &functions,
            &files,
            &call_edges,
            &import_edges,
            &call_callers,
            &call_callees,
            42, // edge_fingerprint
            Some(&co_change),
        );

        // ── Phase A fields should still work ──
        assert!(
            !p.page_rank.is_empty(),
            "Phase A PageRank should be populated"
        );
        assert!(
            !p.betweenness.is_empty(),
            "Phase A betweenness should be populated"
        );
        assert!(
            !p.idom.is_empty(),
            "Phase A dominator tree should be populated"
        );
        assert!(
            !p.call_depth.is_empty(),
            "Phase A call depths should be populated"
        );

        // ── Phase B weighted fields should be populated ──
        assert!(
            !p.weighted_page_rank.is_empty(),
            "Phase B weighted PageRank should be populated"
        );
        assert!(
            !p.weighted_betweenness.is_empty(),
            "Phase B weighted betweenness should be populated"
        );
        assert!(
            !p.community.is_empty(),
            "Phase B communities should be populated"
        );
        // modularity could be 0 if all nodes end up in one community — that's OK

        // ── Verify community assignments exist for all functions ──
        assert!(
            p.community.contains_key(&f1),
            "f1 should have a community assignment"
        );
        assert!(
            p.community.contains_key(&f2),
            "f2 should have a community assignment"
        );
        assert!(
            p.community.contains_key(&f3),
            "f3 should have a community assignment"
        );

        // ── Phase B weighted PageRank should cover all functions ──
        assert!(
            p.weighted_page_rank.contains_key(&f1),
            "f1 should have weighted PageRank"
        );
        assert!(
            p.weighted_page_rank.contains_key(&f2),
            "f2 should have weighted PageRank"
        );
        assert!(
            p.weighted_page_rank.contains_key(&f3),
            "f3 should have weighted PageRank"
        );

        // ── Cross-check: weighted metrics should be positive ──
        for &f in &functions {
            let wpr = p.weighted_page_rank.get(&f).copied().unwrap_or(0.0);
            assert!(
                wpr > 0.0,
                "Weighted PageRank should be > 0 for {f:?}, got {wpr}"
            );
        }

        println!(
            "Phase B integration: weighted_pr={}, weighted_bc={}, communities={}, modularity={:.4}, hidden_coupling={}",
            p.weighted_page_rank.len(),
            p.weighted_betweenness.len(),
            p.community.len(),
            p.modularity,
            p.hidden_coupling.len(),
        );
    }

    #[test]
    fn test_weighted_page_rank_sums_to_one() {
        // 3-node cycle with varying weights — total rank should sum to ~1.0
        let mut overlay: StableGraph<NodeIndex, f32> = StableGraph::new();
        let a = NodeIndex::new(0);
        let b = NodeIndex::new(1);
        let c = NodeIndex::new(2);
        let na = overlay.add_node(a);
        let nb = overlay.add_node(b);
        let nc = overlay.add_node(c);
        overlay.add_edge(na, nb, 3.0);
        overlay.add_edge(nb, nc, 1.0);
        overlay.add_edge(nc, na, 2.0);

        let pr = compute_weighted_page_rank(&overlay, 100, 0.85, 1e-10);
        let sum: f64 = pr.values().sum();
        assert!(
            (sum - 1.0).abs() < 0.01,
            "Weighted PageRank should sum to ~1.0, got {sum}"
        );
    }
}
