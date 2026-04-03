//! Pre-built graph indexes for O(1) query access.
//!
//! `GraphIndexes` is constructed once during `GraphBuilder::freeze()` and provides
//! pre-computed adjacency maps, kind indexes, spatial indexes, and expensive
//! analyses (import cycles, edge fingerprint). All subsequent queries are O(1)
//! lookups instead of O(N) or O(E) graph scans.

use std::collections::HashMap as FoldHashMap;
use std::collections::{HashMap, HashSet};
use std::hash::{Hash, Hasher};

use super::interner::{global_interner, StrKey};
use super::store_models::{CodeEdge, CodeNode, EdgeKind, NodeKind};
use crate::git::co_change::CoChangeMatrix;
use crate::graph::node_index::NodeIndex;

/// All pre-built indexes, computed once during `freeze()`.
///
/// Every field is immutable after construction. Query methods on `CodeGraph`
/// read directly from these indexes — no locks, no scans.
#[derive(Default)]
pub struct GraphIndexes {
    // ── Kind indexes: which nodes are functions/classes/files ──
    pub(crate) functions: Vec<NodeIndex>,
    pub(crate) classes: Vec<NodeIndex>,
    pub(crate) files: Vec<NodeIndex>,

    // ── Spatial indexes: per-file node lookups ──
    pub(crate) functions_by_file: FoldHashMap<StrKey, Vec<NodeIndex>>,
    pub(crate) classes_by_file: FoldHashMap<StrKey, Vec<NodeIndex>>,
    pub(crate) all_nodes_by_file: FoldHashMap<StrKey, Vec<NodeIndex>>,
    /// Sorted by line_start for binary search in `function_at()`.
    pub(crate) function_spatial: FoldHashMap<StrKey, Vec<(u32, u32, NodeIndex)>>,

    // ── Pre-computed bulk edge lists ──
    pub(crate) all_call_edges: Vec<(NodeIndex, NodeIndex)>,
    pub(crate) all_import_edges: Vec<(NodeIndex, NodeIndex)>,
    pub(crate) all_inheritance_edges: Vec<(NodeIndex, NodeIndex)>,

    // ── Pre-computed expensive analyses ──
    pub(crate) import_cycles: Vec<Vec<NodeIndex>>,
    pub(crate) edge_fingerprint: u64,
    pub(crate) primitives: super::primitives::GraphPrimitives,
}

impl GraphIndexes {
    /// Build all indexes from node/edge data in one pass.
    ///
    /// Scans nodes once (populating kind/spatial indexes), scans edges once
    /// (bulk edge lists), computes import cycles via Tarjan SCC, and computes
    /// the edge fingerprint for topology change detection.
    pub fn build_from_vecs(
        nodes: &[CodeNode],
        edges: &[(NodeIndex, NodeIndex, CodeEdge)],
        _node_index: &HashMap<StrKey, NodeIndex>,
        _co_change: Option<&CoChangeMatrix>,
    ) -> Self {
        let mut indexes = Self::default();
        let si = global_interner();

        // Step 1: Build kind indexes from node array
        for (i, node) in nodes.iter().enumerate() {
            let idx = NodeIndex::new(i as u32);
            match node.kind {
                NodeKind::Function => {
                    indexes.functions.push(idx);
                    indexes
                        .functions_by_file
                        .entry(node.file_path)
                        .or_default()
                        .push(idx);
                    indexes
                        .function_spatial
                        .entry(node.file_path)
                        .or_default()
                        .push((node.line_start, node.line_end, idx));
                }
                NodeKind::Class => {
                    indexes.classes.push(idx);
                    indexes
                        .classes_by_file
                        .entry(node.file_path)
                        .or_default()
                        .push(idx);
                }
                NodeKind::File => {
                    indexes.files.push(idx);
                }
                _ => {}
            }
            indexes
                .all_nodes_by_file
                .entry(node.file_path)
                .or_default()
                .push(idx);
        }

        // Sort kind vectors by qualified name for determinism
        let sort_by_qn_vec = |idxs: &mut Vec<NodeIndex>| {
            idxs.sort_by(|a, b| {
                let a_qn = nodes
                    .get(a.index())
                    .map(|n| si.resolve(n.qualified_name))
                    .unwrap_or("");
                let b_qn = nodes
                    .get(b.index())
                    .map(|n| si.resolve(n.qualified_name))
                    .unwrap_or("");
                a_qn.cmp(b_qn)
            });
        };
        sort_by_qn_vec(&mut indexes.functions);
        sort_by_qn_vec(&mut indexes.classes);
        sort_by_qn_vec(&mut indexes.files);
        for v in indexes.functions_by_file.values_mut() {
            sort_by_qn_vec(v);
        }
        for v in indexes.classes_by_file.values_mut() {
            sort_by_qn_vec(v);
        }

        // Step 2: Build bulk edge lists from edge array
        for &(src, tgt, ref edge) in edges {
            match edge.kind {
                EdgeKind::Calls => {
                    indexes.all_call_edges.push((src, tgt));
                }
                EdgeKind::Imports => {
                    indexes.all_import_edges.push((src, tgt));
                }
                EdgeKind::Inherits => {
                    indexes.all_inheritance_edges.push((src, tgt));
                }
                _ => {}
            }
        }

        // Step 3: Sort spatial indexes
        for spans in indexes.function_spatial.values_mut() {
            spans.sort_unstable_by_key(|(start, _, _)| *start);
        }

        // Step 4: Compute expensive analyses
        indexes.import_cycles = compute_import_cycles(nodes, edges);
        indexes.edge_fingerprint = compute_edge_fingerprint(nodes, edges);

        // Primitives are computed later in CodeGraph::build() after the CSR is ready.

        indexes
    }

    /// Set the pre-computed GraphPrimitives.
    /// Called by CodeGraph::build() after primitives are computed.
    pub(crate) fn set_primitives(&mut self, primitives: super::primitives::GraphPrimitives) {
        self.primitives = primitives;
    }
}

/// Compute import cycles using Tarjan SCC.
fn compute_import_cycles(
    nodes: &[CodeNode],
    edges: &[(NodeIndex, NodeIndex, CodeEdge)],
) -> Vec<Vec<NodeIndex>> {
    let si = global_interner();

    // Collect import edges (excluding type-only)
    let import_edges: Vec<(NodeIndex, NodeIndex)> = edges
        .iter()
        .filter(|(_, _, e)| e.kind == EdgeKind::Imports && !e.is_type_only())
        .map(|&(src, tgt, _)| (src, tgt))
        .collect();

    if import_edges.is_empty() {
        return Vec::new();
    }

    // Build adjacency list for import subgraph
    let mut relevant_nodes: HashSet<NodeIndex> = HashSet::new();
    for &(src, tgt) in &import_edges {
        relevant_nodes.insert(src);
        relevant_nodes.insert(tgt);
    }

    // Map our NodeIndex to sequential indices for Tarjan
    let mut sorted_nodes: Vec<NodeIndex> = relevant_nodes.into_iter().collect();
    sorted_nodes.sort();
    let idx_map: HashMap<NodeIndex, usize> = sorted_nodes
        .iter()
        .enumerate()
        .map(|(i, &idx)| (idx, i))
        .collect();
    let n = sorted_nodes.len();

    // Build adjacency list
    let mut adj: Vec<Vec<u32>> = vec![vec![]; n];
    for &(src, tgt) in &import_edges {
        if let (Some(&from), Some(&to)) = (idx_map.get(&src), idx_map.get(&tgt)) {
            adj[from].push(to as u32);
        }
    }

    // Run Tarjan SCC
    let sccs = crate::graph::algo::tarjan_scc(n, |v| &adj[v as usize]);

    // Convert back, keep only cycles (>1 node)
    let mut cycles: Vec<Vec<NodeIndex>> = sccs
        .into_iter()
        .filter(|scc| scc.len() > 1)
        .map(|scc| {
            let mut orig_indices: Vec<NodeIndex> = scc
                .iter()
                .filter_map(|&i| sorted_nodes.get(i as usize).copied())
                .collect();
            orig_indices.sort_by(|a, b| {
                let a_qn = nodes
                    .get(a.index())
                    .map(|n| si.resolve(n.qualified_name))
                    .unwrap_or("");
                let b_qn = nodes
                    .get(b.index())
                    .map(|n| si.resolve(n.qualified_name))
                    .unwrap_or("");
                a_qn.cmp(b_qn)
            });
            orig_indices
        })
        .collect();

    // Sort cycles: largest first, then by first node's QN
    cycles.sort_by(|a, b| {
        b.len().cmp(&a.len()).then_with(|| {
            let a_qn = a
                .first()
                .and_then(|idx| nodes.get(idx.index()))
                .map(|n| si.resolve(n.qualified_name))
                .unwrap_or("");
            let b_qn = b
                .first()
                .and_then(|idx| nodes.get(idx.index()))
                .map(|n| si.resolve(n.qualified_name))
                .unwrap_or("");
            a_qn.cmp(b_qn)
        })
    });

    cycles.dedup();
    cycles
}

/// Compute edge fingerprint for topology change detection.
fn compute_edge_fingerprint(nodes: &[CodeNode], edges: &[(NodeIndex, NodeIndex, CodeEdge)]) -> u64 {
    use std::collections::hash_map::DefaultHasher;

    let mut fp_edges: Vec<(u32, u32, u8)> = edges
        .iter()
        .filter(|&&(src, tgt, _)| {
            let s = nodes.get(src.index());
            let t = nodes.get(tgt.index());
            match (s, t) {
                (Some(s), Some(t)) => s.file_path != t.file_path,
                _ => false,
            }
        })
        .map(|&(src, tgt, ref e)| {
            let s = &nodes[src.index()];
            let t = &nodes[tgt.index()];
            (
                s.qualified_name.as_u32(),
                t.qualified_name.as_u32(),
                e.kind as u8,
            )
        })
        .collect();
    fp_edges.sort_unstable();

    let mut hasher = DefaultHasher::new();
    for (src, tgt, kind) in &fp_edges {
        src.hash(&mut hasher);
        tgt.hash(&mut hasher);
        kind.hash(&mut hasher);
    }
    hasher.finish()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::builder::GraphBuilder;

    #[test]
    fn test_build_empty_graph() {
        let graph = GraphBuilder::new().freeze();
        assert!(graph.functions().is_empty());
        assert!(graph.classes().is_empty());
        assert!(graph.files().is_empty());
        assert!(graph.import_cycles().is_empty());
    }

    #[test]
    fn test_build_kind_indexes() {
        let mut builder = GraphBuilder::new();
        builder.add_node(CodeNode::function("foo", "a.py"));
        builder.add_node(CodeNode::function("bar", "a.py"));
        builder.add_node(CodeNode::class("MyClass", "a.py"));
        builder.add_node(CodeNode::file("a.py"));

        let graph = builder.freeze();
        assert_eq!(graph.functions().len(), 2);
        assert_eq!(graph.classes().len(), 1);
        assert_eq!(graph.files().len(), 1);
    }

    #[test]
    fn test_build_adjacency_indexes() {
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("foo", "a.py"));
        let f2 = builder.add_node(CodeNode::function("bar", "a.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());

        let graph = builder.freeze();

        let (new_f1, _) = graph.node_by_name("a.py::foo").expect("foo");
        let (new_f2, _) = graph.node_by_name("a.py::bar").expect("bar");

        assert_eq!(graph.callees(new_f1).len(), 1);
        assert_eq!(graph.callers(new_f2).len(), 1);
        assert!(graph.callees(new_f2).is_empty());
        assert!(graph.callers(new_f1).is_empty());
        assert_eq!(graph.all_call_edges().len(), 1);
    }

    #[test]
    fn test_import_cycle_detection() {
        let mut builder = GraphBuilder::new();
        let a = builder.add_node(CodeNode::file("a.py"));
        let b = builder.add_node(CodeNode::file("b.py"));
        let c = builder.add_node(CodeNode::file("c.py"));

        // a -> b -> c -> a (cycle)
        builder.add_edge(a, b, CodeEdge::imports());
        builder.add_edge(b, c, CodeEdge::imports());
        builder.add_edge(c, a, CodeEdge::imports());

        let graph = builder.freeze();
        assert_eq!(graph.import_cycles().len(), 1);
        assert_eq!(graph.import_cycles()[0].len(), 3);
    }

    #[test]
    fn test_spatial_index_sorted() {
        let mut builder = GraphBuilder::new();
        // Add functions out of order
        builder.add_node(CodeNode::function("bar", "test.py").with_lines(20, 30));
        builder.add_node(CodeNode::function("foo", "test.py").with_lines(1, 10));

        let graph = builder.freeze();
        // function_at should find the first function (lines 1-10) not the second
        let idx = graph.function_at("test.py", 5);
        assert!(idx.is_some());
        let node = graph.node(idx.unwrap()).expect("node");
        assert_eq!(
            graph.interner().resolve(node.qualified_name),
            "test.py::foo"
        );
    }

    #[test]
    fn test_edge_fingerprint_deterministic() {
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("foo", "a.py"));
        let f2 = builder.add_node(CodeNode::function("bar", "b.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());
        let graph = builder.freeze();

        let fp = graph.edge_fingerprint();
        assert_ne!(fp, 0);

        // Rebuild same graph — fingerprint should be identical
        let mut builder2 = GraphBuilder::new();
        let g1 = builder2.add_node(CodeNode::function("foo", "a.py"));
        let g2 = builder2.add_node(CodeNode::function("bar", "b.py"));
        builder2.add_edge(g1, g2, CodeEdge::calls());
        let graph2 = builder2.freeze();
        assert_eq!(fp, graph2.edge_fingerprint());
    }

    #[test]
    fn test_edge_fingerprint_ignores_same_file() {
        let empty = GraphBuilder::new().freeze();
        let empty_fp = empty.edge_fingerprint();

        // Graph with only same-file edges should match empty fingerprint
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("foo", "a.py"));
        let f2 = builder.add_node(CodeNode::function("bar", "a.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());
        let graph = builder.freeze();

        assert_eq!(graph.edge_fingerprint(), empty_fp);
    }
}
