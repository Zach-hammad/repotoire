//! Pre-computed graph algorithm results.
//!
//! `GraphPrimitives` is computed once during `GraphIndexes::build()` and provides
//! pre-computed dominator trees, articulation points, PageRank, betweenness
//! centrality, and call-graph SCCs. All fields are immutable after construction.
//! Detectors read them at O(1) — zero graph traversal at detection time.

use std::collections::HashMap;
use std::collections::HashSet;

use crate::graph::frozen::CodeGraph;
use crate::graph::node_index::NodeIndex;
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
    // Tuple: (file_a, file_b, weight, lift, confidence).
    pub hidden_coupling: Vec<(NodeIndex, NodeIndex, f32, f32, f32)>,
}

impl GraphPrimitives {
    /// Compute all graph primitives. Called by GraphIndexes::build().
    /// Returns Default for empty graphs.
    pub fn compute(
        code_graph: &CodeGraph,
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
        let call_cycles = compute_call_cycles(all_call_edges, code_graph);

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
            code_graph,
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
                        code_graph,
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
    use crate::graph::builder::GraphBuilder;
    use crate::graph::overlay::WeightedOverlay;
    use crate::graph::store_models::{CodeEdge, CodeNode};

    /// Helper: build a CodeGraph from nodes/edges for testing.
    fn build_test_graph(
        node_fns: &[(&str, &str)],    // (qn_suffix, file)
        file_nodes: &[&str],           // file paths
        call_edges: &[(usize, usize)], // indices into node_fns
        import_edges: &[(usize, usize)], // indices into file_nodes
    ) -> (
        crate::graph::frozen::CodeGraph,
        Vec<NodeIndex>,  // function indices (by name order after freeze)
        Vec<NodeIndex>,  // file indices
    ) {
        let mut builder = GraphBuilder::new();
        // Add files first
        let mut file_idxs = Vec::new();
        for &fp in file_nodes {
            file_idxs.push(builder.add_node(CodeNode::file(fp)));
        }
        // Add functions
        let mut func_idxs = Vec::new();
        for &(qn, file) in node_fns {
            func_idxs.push(builder.add_node(CodeNode::function(qn, file)));
        }
        // Add call edges
        for &(src, tgt) in call_edges {
            builder.add_edge(func_idxs[src], func_idxs[tgt], CodeEdge::calls());
        }
        // Add import edges
        for &(src, tgt) in import_edges {
            builder.add_edge(file_idxs[src], file_idxs[tgt], CodeEdge::imports());
        }

        let graph = builder.freeze();

        // Re-resolve indices by name (they may have been remapped during freeze)
        let resolved_funcs: Vec<NodeIndex> = node_fns
            .iter()
            .map(|&(qn, file)| {
                let full_qn = format!("{file}::{qn}");
                graph.node_by_name(&full_qn).unwrap().0
            })
            .collect();
        let resolved_files: Vec<NodeIndex> = file_nodes
            .iter()
            .map(|&fp| graph.node_by_name(fp).unwrap().0)
            .collect();

        (graph, resolved_funcs, resolved_files)
    }

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
        let builder = GraphBuilder::new();
        let graph = builder.freeze();
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
        let (graph, funcs, _files) = build_test_graph(
            &[("f1", "a.py"), ("f2", "a.py"), ("f3", "a.py")],
            &[],
            &[(0, 1), (1, 2), (2, 0)],
            &[],
        );

        let call_edges: Vec<(NodeIndex, NodeIndex)> =
            vec![(funcs[0], funcs[1]), (funcs[1], funcs[2]), (funcs[2], funcs[0])];
        let cycles = compute_call_cycles(&call_edges, &graph);

        assert_eq!(cycles.len(), 1, "Should find exactly 1 cycle");
        assert_eq!(cycles[0].len(), 3, "Cycle should contain 3 nodes");
    }

    #[test]
    fn test_call_cycles_dag_no_cycles() {
        let (graph, funcs, _files) = build_test_graph(
            &[("f1", "a.py"), ("f2", "a.py"), ("f3", "a.py")],
            &[],
            &[(0, 1), (1, 2)],
            &[],
        );

        let call_edges: Vec<(NodeIndex, NodeIndex)> =
            vec![(funcs[0], funcs[1]), (funcs[1], funcs[2])];
        let cycles = compute_call_cycles(&call_edges, &graph);

        assert!(cycles.is_empty(), "DAG should have no cycles");
    }

    // ── PageRank tests ──

    #[test]
    fn test_page_rank_star_topology() {
        let (graph, funcs, _files) = build_test_graph(
            &[("f1", "a.py"), ("f2", "a.py"), ("f3", "a.py"), ("hub", "a.py"), ("leaf", "a.py")],
            &[],
            &[(0, 3), (1, 3), (2, 3), (3, 4)],
            &[],
        );

        let [f1, f2, f3, hub, leaf] = [funcs[0], funcs[1], funcs[2], funcs[3], funcs[4]];

        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
        let mut call_callers: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();

        call_callees.insert(f1, vec![hub]);
        call_callees.insert(f2, vec![hub]);
        call_callees.insert(f3, vec![hub]);
        call_callees.insert(hub, vec![leaf]);

        call_callers.insert(hub, vec![f1, f2, f3]);
        call_callers.insert(leaf, vec![hub]);

        let functions = vec![f1, f2, f3, hub, leaf];
        let pr = compute_page_rank(&functions, &call_callees, &call_callers, 20, 0.85, 1e-6);

        assert!(pr.len() == 5);
        let hub_rank = pr[&hub];
        let leaf_rank = pr[&leaf];
        let f1_rank = pr[&f1];

        assert!(
            hub_rank > f1_rank,
            "Hub ({hub_rank}) should have higher rank than f1 ({f1_rank})"
        );
        assert!(
            leaf_rank > f1_rank,
            "Leaf ({leaf_rank}) should have higher rank than f1 ({f1_rank})"
        );
    }

    #[test]
    fn test_page_rank_sums_to_one() {
        let (graph, funcs, _files) = build_test_graph(
            &[("f1", "a.py"), ("f2", "a.py"), ("f3", "a.py")],
            &[],
            &[(0, 1), (1, 2), (2, 0)],
            &[],
        );

        let [f1, f2, f3] = [funcs[0], funcs[1], funcs[2]];
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
        let (graph, funcs, _files) = build_test_graph(
            &[("entry", "a.py"), ("a_fn", "a.py"), ("b_fn", "a.py"), ("c_fn", "a.py")],
            &[],
            &[(0, 1), (1, 2), (2, 3)],
            &[],
        );

        let [entry, a, b, c] = [funcs[0], funcs[1], funcs[2], funcs[3]];
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

        assert_eq!(idom.get(&a), Some(&entry), "entry should dominate A");
        assert_eq!(idom.get(&b), Some(&a), "A should immediately dominate B");
        assert_eq!(idom.get(&c), Some(&b), "B should immediately dominate C");

        let entry_dominated = dominated.get(&entry).unwrap();
        assert!(entry_dominated.contains(&a));
        assert!(entry_dominated.contains(&b));
        assert!(entry_dominated.contains(&c));

        assert_eq!(dom_depth.get(&entry), Some(&0));
        assert_eq!(dom_depth.get(&a), Some(&1));
        assert_eq!(dom_depth.get(&b), Some(&2));
        assert_eq!(dom_depth.get(&c), Some(&3));
    }

    #[test]
    fn test_dominators_diamond() {
        let (graph, funcs, _files) = build_test_graph(
            &[("entry", "a.py"), ("a_fn", "a.py"), ("b_fn", "a.py"), ("join", "a.py")],
            &[],
            &[(0, 1), (0, 2), (1, 3), (2, 3)],
            &[],
        );

        let [entry, a, b, join] = [funcs[0], funcs[1], funcs[2], funcs[3]];
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

        assert_eq!(
            idom.get(&join),
            Some(&entry),
            "Entry should dominate join (not A or B, since there are two paths)"
        );
    }

    // ── Articulation points tests ──

    #[test]
    fn test_articulation_points_two_triangles_bridge() {
        let (graph, funcs, _files) = build_test_graph(
            &[("a", "x.py"), ("b", "x.py"), ("c", "x.py"), ("d", "x.py"), ("e", "x.py"), ("f", "x.py")],
            &[],
            &[
                (0, 1), (1, 0), (1, 2), (2, 1), (0, 2), (2, 0),
                (2, 3), (3, 2),
                (3, 4), (4, 3), (4, 5), (5, 4), (3, 5), (5, 3),
            ],
            &[],
        );

        let [a, b, c, d, e, f] = [funcs[0], funcs[1], funcs[2], funcs[3], funcs[4], funcs[5]];
        let functions = vec![a, b, c, d, e, f];
        let call_edges = vec![
            (a, b), (b, a), (b, c), (c, b), (a, c), (c, a),
            (c, d), (d, c),
            (d, e), (e, d), (e, f), (f, e), (d, f), (f, d),
        ];

        let (_ap_vec, ap_set, bridges, _comp_sizes) =
            compute_articulation_points(&functions, &call_edges, &[], &[]);

        assert!(ap_set.contains(&c), "c should be an articulation point");
        assert!(ap_set.contains(&d), "d should be an articulation point");
        assert_eq!(ap_set.len(), 2, "Should have exactly 2 articulation points");

        let has_bridge = bridges
            .iter()
            .any(|&(s, t)| (s == c && t == d) || (s == d && t == c));
        assert!(has_bridge, "c-d should be a bridge");
    }

    #[test]
    fn test_articulation_points_fully_connected() {
        let (graph, funcs, _files) = build_test_graph(
            &[("a", "x.py"), ("b", "x.py"), ("c", "x.py"), ("d", "x.py")],
            &[],
            &[
                (0, 1), (1, 0), (0, 2), (2, 0), (0, 3), (3, 0),
                (1, 2), (2, 1), (1, 3), (3, 1), (2, 3), (3, 2),
            ],
            &[],
        );

        let [a, b, c, d] = [funcs[0], funcs[1], funcs[2], funcs[3]];
        let functions = vec![a, b, c, d];
        let call_edges = vec![
            (a, b), (b, a), (a, c), (c, a), (a, d), (d, a),
            (b, c), (c, b), (b, d), (d, b), (c, d), (d, c),
        ];

        let (_ap_vec, ap_set, bridges, _comp_sizes) =
            compute_articulation_points(&functions, &call_edges, &[], &[]);

        assert!(ap_set.is_empty(), "Fully connected graph should have no articulation points");
        assert!(bridges.is_empty(), "Fully connected graph should have no bridges");
    }

    // ── BFS call depth tests ──

    #[test]
    fn test_call_depths_linear_chain() {
        let (_graph, funcs, _files) = build_test_graph(
            &[("entry", "a.py"), ("mid", "a.py"), ("leaf", "a.py")],
            &[],
            &[(0, 1), (1, 2)],
            &[],
        );

        let [entry, mid, leaf] = [funcs[0], funcs[1], funcs[2]];
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
        let (_graph, funcs, _files) = build_test_graph(
            &[("entry1", "a.py"), ("entry2", "a.py"), ("shared", "a.py")],
            &[],
            &[(0, 2), (1, 2)],
            &[],
        );

        let [e1, e2, shared] = [funcs[0], funcs[1], funcs[2]];
        let functions = vec![e1, e2, shared];
        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
        let mut call_callers: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();

        call_callees.insert(e1, vec![shared]);
        call_callees.insert(e2, vec![shared]);
        call_callers.insert(shared, vec![e1, e2]);

        let depths = compute_call_depths(&functions, &call_callees, &call_callers);

        assert_eq!(depths.get(&e1), Some(&0));
        assert_eq!(depths.get(&e2), Some(&0));
        assert_eq!(depths.get(&shared), Some(&1));
    }

    // ── Betweenness centrality tests ──

    #[test]
    fn test_betweenness_star_through_bridge() {
        let (_graph, funcs, _files) = build_test_graph(
            &[("s1", "a.py"), ("s2", "a.py"), ("s3", "a.py"), ("bridge", "a.py"), ("t1", "a.py"), ("t2", "a.py"), ("t3", "a.py")],
            &[],
            &[(0, 3), (1, 3), (2, 3), (3, 4), (3, 5), (3, 6)],
            &[],
        );

        let [s1, _s2, _s3, bridge, t1, _t2, _t3] = [funcs[0], funcs[1], funcs[2], funcs[3], funcs[4], funcs[5], funcs[6]];
        let functions = funcs.clone();
        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();

        call_callees.insert(funcs[0], vec![bridge]);
        call_callees.insert(funcs[1], vec![bridge]);
        call_callees.insert(funcs[2], vec![bridge]);
        call_callees.insert(bridge, vec![funcs[4], funcs[5], funcs[6]]);

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
        let (graph, funcs, files) = build_test_graph(
            &[("f1", "a.py"), ("f2", "a.py"), ("f3", "b.py")],
            &["a.py", "b.py"],
            &[(0, 1), (1, 2)],
            &[(0, 1)],
        );

        let [f1, f2, f3] = [funcs[0], funcs[1], funcs[2]];
        let all_call_edges = vec![(f1, f2), (f2, f3)];
        let all_import_edges = vec![(files[0], files[1])];

        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
        let mut call_callers: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
        call_callees.insert(f1, vec![f2]);
        call_callees.insert(f2, vec![f3]);
        call_callers.insert(f2, vec![f1]);
        call_callers.insert(f3, vec![f2]);

        let p = GraphPrimitives::compute(
            &graph,
            &funcs,
            &files,
            &all_call_edges,
            &all_import_edges,
            &call_callers,
            &call_callees,
            12345,
            None,
        );

        assert!(p.call_cycles.is_empty());
        assert_eq!(p.page_rank.len(), 3);
        assert_eq!(p.betweenness.len(), 3);
        assert_eq!(p.call_depth.get(&f1), Some(&0));
        assert_eq!(p.call_depth.get(&f2), Some(&1));
        assert_eq!(p.call_depth.get(&f3), Some(&2));
        assert_eq!(p.idom.get(&f2), Some(&f1));
        assert_eq!(p.idom.get(&f3), Some(&f2));
        assert_eq!(p.dom_depth.get(&f1), Some(&0));
        assert_eq!(p.dom_depth.get(&f2), Some(&1));
        assert_eq!(p.dom_depth.get(&f3), Some(&2));
    }

    // ── Weighted PageRank tests ──

    #[test]
    fn test_weighted_page_rank_uniform_weights() {
        let mut overlay = WeightedOverlay::new(3);
        let idx_to_orig = vec![NodeIndex::new(100), NodeIndex::new(101), NodeIndex::new(102)];

        overlay.add_edge(0, 1, 1.0);
        overlay.add_edge(1, 2, 1.0);
        overlay.add_edge(2, 0, 1.0);

        let pr = compute_weighted_page_rank(&overlay, &idx_to_orig, 20, 0.85, 1e-6);

        assert_eq!(pr.len(), 3, "Should have 3 entries");
        let rank_a = pr[&NodeIndex::new(100)];
        let rank_b = pr[&NodeIndex::new(101)];
        let rank_c = pr[&NodeIndex::new(102)];

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
        let mut overlay = WeightedOverlay::new(3);
        let idx_to_orig = vec![NodeIndex::new(200), NodeIndex::new(201), NodeIndex::new(202)];

        overlay.add_edge(0, 1, 5.0); // heavy edge
        overlay.add_edge(0, 2, 1.0); // light edge
        overlay.add_edge(1, 0, 1.0); // feedback
        overlay.add_edge(2, 0, 1.0); // feedback

        let pr = compute_weighted_page_rank(&overlay, &idx_to_orig, 20, 0.85, 1e-6);

        assert_eq!(pr.len(), 3, "Should have 3 entries");
        let rank_b = pr[&NodeIndex::new(201)];
        let rank_c = pr[&NodeIndex::new(202)];

        assert!(
            rank_b > rank_c,
            "b should have higher rank than c due to heavy edge: b={rank_b}, c={rank_c}"
        );
    }

    // ── Weighted betweenness centrality tests ──

    #[test]
    fn test_weighted_betweenness_center_node() {
        let mut overlay = WeightedOverlay::new(5);
        let idx_to_orig = vec![
            NodeIndex::new(300), NodeIndex::new(301), NodeIndex::new(302),
            NodeIndex::new(303), NodeIndex::new(304),
        ];

        overlay.add_edge(0, 2, 1.0);
        overlay.add_edge(1, 2, 1.0);
        overlay.add_edge(2, 3, 1.0);
        overlay.add_edge(2, 4, 1.0);

        let bc = compute_weighted_betweenness(&overlay, &idx_to_orig, 200, 42);

        assert_eq!(bc.len(), 5, "Should have betweenness for all 5 nodes");

        let bc_center = bc[&NodeIndex::new(302)];
        let bc_a = bc[&NodeIndex::new(300)];
        let bc_b = bc[&NodeIndex::new(301)];

        assert!(
            bc_center > bc_a,
            "Center ({bc_center}) should have higher betweenness than a ({bc_a})"
        );
        assert!(
            bc_center > bc_b,
            "Center ({bc_center}) should have higher betweenness than b ({bc_b})"
        );
        assert!(bc_center > 0.0, "Center betweenness should be positive, got {bc_center}");
    }

    // ── Louvain community detection tests ──

    #[test]
    fn test_two_cliques_two_communities() {
        let mut overlay = WeightedOverlay::new(6);
        let idx_to_orig = vec![
            NodeIndex::new(0), NodeIndex::new(1), NodeIndex::new(2),
            NodeIndex::new(3), NodeIndex::new(4), NodeIndex::new(5),
        ];

        // Clique 1: bidirectional edges between 0, 1, 2
        overlay.add_edge(0, 1, 1.0);
        overlay.add_edge(1, 0, 1.0);
        overlay.add_edge(0, 2, 1.0);
        overlay.add_edge(2, 0, 1.0);
        overlay.add_edge(1, 2, 1.0);
        overlay.add_edge(2, 1, 1.0);

        // Clique 2: bidirectional edges between 3, 4, 5
        overlay.add_edge(3, 4, 1.0);
        overlay.add_edge(4, 3, 1.0);
        overlay.add_edge(3, 5, 1.0);
        overlay.add_edge(5, 3, 1.0);
        overlay.add_edge(4, 5, 1.0);
        overlay.add_edge(5, 4, 1.0);

        let (community_map, modularity) = compute_communities(&overlay, &idx_to_orig, 1.0);

        assert_eq!(community_map.len(), 6, "Should have 6 entries");

        let c0 = community_map[&NodeIndex::new(0)];
        let c1 = community_map[&NodeIndex::new(1)];
        let c2 = community_map[&NodeIndex::new(2)];
        assert_eq!(c0, c1, "Nodes 0 and 1 should be in the same community");
        assert_eq!(c0, c2, "Nodes 0 and 2 should be in the same community");

        let c3 = community_map[&NodeIndex::new(3)];
        let c4 = community_map[&NodeIndex::new(4)];
        let c5 = community_map[&NodeIndex::new(5)];
        assert_eq!(c3, c4, "Nodes 3 and 4 should be in the same community");
        assert_eq!(c3, c5, "Nodes 3 and 5 should be in the same community");

        assert_ne!(c0, c3, "Clique 1 and clique 2 should be in different communities");

        let distinct: HashSet<usize> = community_map.values().copied().collect();
        assert_eq!(distinct.len(), 2, "Should find exactly 2 communities");

        assert!(
            modularity > 0.0,
            "Modularity should be positive for two disconnected cliques, got {modularity}"
        );
    }

    #[test]
    fn test_single_clique_one_community() {
        let mut overlay = WeightedOverlay::new(3);
        let idx_to_orig = vec![NodeIndex::new(10), NodeIndex::new(11), NodeIndex::new(12)];

        overlay.add_edge(0, 1, 1.0);
        overlay.add_edge(1, 0, 1.0);
        overlay.add_edge(0, 2, 1.0);
        overlay.add_edge(2, 0, 1.0);
        overlay.add_edge(1, 2, 1.0);
        overlay.add_edge(2, 1, 1.0);

        let (community_map, _modularity) = compute_communities(&overlay, &idx_to_orig, 1.0);

        assert_eq!(community_map.len(), 3, "Should have 3 entries");

        let c0 = community_map[&NodeIndex::new(10)];
        let c1 = community_map[&NodeIndex::new(11)];
        let c2 = community_map[&NodeIndex::new(12)];
        assert_eq!(c0, c1, "All nodes should be in the same community");
        assert_eq!(c0, c2, "All nodes should be in the same community");
    }

    #[test]
    fn test_weighted_page_rank_sums_to_one() {
        let mut overlay = WeightedOverlay::new(3);
        let idx_to_orig = vec![NodeIndex::new(0), NodeIndex::new(1), NodeIndex::new(2)];
        overlay.add_edge(0, 1, 3.0);
        overlay.add_edge(1, 2, 1.0);
        overlay.add_edge(2, 0, 2.0);

        let pr = compute_weighted_page_rank(&overlay, &idx_to_orig, 100, 0.85, 1e-10);
        let sum: f64 = pr.values().sum();
        assert!(
            (sum - 1.0).abs() < 0.01,
            "Weighted PageRank should sum to ~1.0, got {sum}"
        );
    }
}
