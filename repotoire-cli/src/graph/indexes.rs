//! Pre-built graph indexes for O(1) query access.
//!
//! `GraphIndexes` is constructed once during `GraphBuilder::freeze()` and provides
//! pre-computed adjacency maps, kind indexes, spatial indexes, and expensive
//! analyses (import cycles, edge fingerprint). All subsequent queries are O(1)
//! lookups instead of O(N) or O(E) graph scans.

use std::collections::HashMap as FoldHashMap;
use petgraph::algo::tarjan_scc;
use petgraph::stable_graph::StableGraph;
use petgraph::visit::{EdgeRef, IntoEdgeReferences};
use std::collections::{HashMap, HashSet};
use std::hash::{Hash, Hasher};

// The old `build()` path (persistence) uses petgraph's NodeIndex.
// The new `build_from_vecs()` path uses our NodeIndex.
// Both are kept until Tasks 7-10 migrate primitives off petgraph.
use crate::graph::node_index::NodeIndex;
use super::interner::{global_interner, StrKey};
use super::store_models::{CodeEdge, CodeNode, EdgeKind, NodeKind};
use crate::git::co_change::CoChangeMatrix;

/// Convert petgraph NodeIndex to our NodeIndex.
#[inline]
fn from_pg(idx: petgraph::stable_graph::NodeIndex) -> NodeIndex {
    NodeIndex::new(idx.index() as u32)
}

/// Convert our NodeIndex to petgraph NodeIndex.
#[inline]
fn to_pg(idx: NodeIndex) -> petgraph::stable_graph::NodeIndex {
    petgraph::stable_graph::NodeIndex::new(idx.index())
}

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

    // ── Adjacency per edge kind (FoldHashMap for fast lookups on hot path) ──
    // Calls
    pub(crate) call_callers: FoldHashMap<NodeIndex, Vec<NodeIndex>>,
    pub(crate) call_callees: FoldHashMap<NodeIndex, Vec<NodeIndex>>,
    // Imports
    pub(crate) import_sources: FoldHashMap<NodeIndex, Vec<NodeIndex>>,
    pub(crate) import_targets: FoldHashMap<NodeIndex, Vec<NodeIndex>>,
    // Inherits
    pub(crate) inherit_parents: FoldHashMap<NodeIndex, Vec<NodeIndex>>,
    pub(crate) inherit_children: FoldHashMap<NodeIndex, Vec<NodeIndex>>,
    // Contains
    pub(crate) contains_children: FoldHashMap<NodeIndex, Vec<NodeIndex>>,
    pub(crate) contains_parent: FoldHashMap<NodeIndex, Vec<NodeIndex>>,
    // Uses
    pub(crate) uses_targets: FoldHashMap<NodeIndex, Vec<NodeIndex>>,
    pub(crate) uses_sources: FoldHashMap<NodeIndex, Vec<NodeIndex>>,
    // ModifiedIn (one-directional: entity → commit)
    pub(crate) modified_in: FoldHashMap<NodeIndex, Vec<NodeIndex>>,

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
    /// Build all indexes from a graph and node_index in one pass.
    ///
    /// This is called by `GraphBuilder::freeze()`. It scans nodes once (populating
    /// kind indexes and spatial indexes), scans edges once (populating adjacency
    /// indexes and bulk edge lists), sorts adjacency vectors by qualified name for
    /// determinism, computes import cycles via Tarjan SCC, and computes the edge
    /// fingerprint for topology change detection.
    pub fn build(
        graph: &StableGraph<CodeNode, CodeEdge>,
        _node_index: &HashMap<StrKey, NodeIndex>,
        co_change: Option<&CoChangeMatrix>,
    ) -> Self {
        let mut indexes = Self::default();

        // Steps 1-2: Populate kind/spatial indexes and adjacency maps
        indexes.build_kind_indexes(graph);
        indexes.build_adjacency_maps(graph);
        indexes.build_spatial_indexes(graph);

        // Steps 5-9: Expensive analyses
        indexes.import_cycles = compute_import_cycles(graph);
        indexes.edge_fingerprint = compute_edge_fingerprint(graph);

        // Convert our NodeIndex to petgraph's for primitives computation
        let pg_functions: Vec<petgraph::stable_graph::NodeIndex> =
            indexes.functions.iter().map(|idx| to_pg(*idx)).collect();
        let pg_files: Vec<petgraph::stable_graph::NodeIndex> =
            indexes.files.iter().map(|idx| to_pg(*idx)).collect();
        let pg_call_edges: Vec<(petgraph::stable_graph::NodeIndex, petgraph::stable_graph::NodeIndex)> =
            indexes.all_call_edges.iter().map(|&(s, t)| (to_pg(s), to_pg(t))).collect();
        let pg_import_edges: Vec<(petgraph::stable_graph::NodeIndex, petgraph::stable_graph::NodeIndex)> =
            indexes.all_import_edges.iter().map(|&(s, t)| (to_pg(s), to_pg(t))).collect();
        let pg_callers: HashMap<petgraph::stable_graph::NodeIndex, Vec<petgraph::stable_graph::NodeIndex>> =
            indexes.call_callers.iter().map(|(k, v)| (to_pg(*k), v.iter().map(|idx| to_pg(*idx)).collect())).collect();
        let pg_callees: HashMap<petgraph::stable_graph::NodeIndex, Vec<petgraph::stable_graph::NodeIndex>> =
            indexes.call_callees.iter().map(|(k, v)| (to_pg(*k), v.iter().map(|idx| to_pg(*idx)).collect())).collect();

        indexes.primitives = super::primitives::GraphPrimitives::compute(
            graph,
            &pg_functions,
            &pg_files,
            &pg_call_edges,
            &pg_import_edges,
            &pg_callers,
            &pg_callees,
            indexes.edge_fingerprint,
            co_change,
        );

        indexes
    }

    /// Scan all nodes and categorize by kind, populating kind indexes and
    /// per-file node lookups. Sorts kind vectors by qualified name for determinism.
    fn build_kind_indexes(&mut self, graph: &StableGraph<CodeNode, CodeEdge>) {
        let si = global_interner();

        for pg_idx in graph.node_indices() {
            let idx = from_pg(pg_idx);
            let node = &graph[pg_idx];
            match node.kind {
                NodeKind::Function => {
                    self.functions.push(idx);
                    self.functions_by_file
                        .entry(node.file_path)
                        .or_default()
                        .push(idx);
                    self.function_spatial
                        .entry(node.file_path)
                        .or_default()
                        .push((node.line_start, node.line_end, idx));
                }
                NodeKind::Class => {
                    self.classes.push(idx);
                    self.classes_by_file
                        .entry(node.file_path)
                        .or_default()
                        .push(idx);
                }
                NodeKind::File => {
                    self.files.push(idx);
                }
                _ => {}
            }
            self.all_nodes_by_file
                .entry(node.file_path)
                .or_default()
                .push(idx);
        }

        // Sort kind vectors by qualified name for determinism
        let sort_by_qn = |nodes: &mut Vec<NodeIndex>| {
            nodes.sort_by(|a, b| {
                let a_qn = graph
                    .node_weight(to_pg(*a))
                    .map(|n| si.resolve(n.qualified_name))
                    .unwrap_or("");
                let b_qn = graph
                    .node_weight(to_pg(*b))
                    .map(|n| si.resolve(n.qualified_name))
                    .unwrap_or("");
                a_qn.cmp(b_qn)
            });
        };

        sort_by_qn(&mut self.functions);
        sort_by_qn(&mut self.classes);
        sort_by_qn(&mut self.files);

        // Sort per-file indexes by QN
        for v in self.functions_by_file.values_mut() {
            sort_by_qn(v);
        }
        for v in self.classes_by_file.values_mut() {
            sort_by_qn(v);
        }
    }

    /// Scan all edges to build caller/callee, importer/importee, and other
    /// adjacency maps plus bulk edge lists. Sorts adjacency vectors by qualified
    /// name for determinism.
    fn build_adjacency_maps(&mut self, graph: &StableGraph<CodeNode, CodeEdge>) {
        let si = global_interner();

        for edge_ref in graph.edge_references() {
            let src = from_pg(edge_ref.source());
            let tgt = from_pg(edge_ref.target());
            match edge_ref.weight().kind {
                EdgeKind::Calls => {
                    self.call_callees.entry(src).or_default().push(tgt);
                    self.call_callers.entry(tgt).or_default().push(src);
                    self.all_call_edges.push((src, tgt));
                }
                EdgeKind::Imports => {
                    self.import_targets.entry(src).or_default().push(tgt);
                    self.import_sources.entry(tgt).or_default().push(src);
                    self.all_import_edges.push((src, tgt));
                }
                EdgeKind::Inherits => {
                    self.inherit_parents.entry(src).or_default().push(tgt);
                    self.inherit_children.entry(tgt).or_default().push(src);
                    self.all_inheritance_edges.push((src, tgt));
                }
                EdgeKind::Contains => {
                    self.contains_children.entry(src).or_default().push(tgt);
                    self.contains_parent.entry(tgt).or_default().push(src);
                }
                EdgeKind::Uses => {
                    self.uses_targets.entry(src).or_default().push(tgt);
                    self.uses_sources.entry(tgt).or_default().push(src);
                }
                EdgeKind::ModifiedIn => {
                    self.modified_in.entry(src).or_default().push(tgt);
                }
            }
        }

        // Sort all adjacency vectors by qualified name for determinism
        let sort_by_qn = |nodes: &mut Vec<NodeIndex>| {
            nodes.sort_by(|a, b| {
                let a_qn = graph
                    .node_weight(to_pg(*a))
                    .map(|n| si.resolve(n.qualified_name))
                    .unwrap_or("");
                let b_qn = graph
                    .node_weight(to_pg(*b))
                    .map(|n| si.resolve(n.qualified_name))
                    .unwrap_or("");
                a_qn.cmp(b_qn)
            });
        };

        for v in self.call_callers.values_mut() {
            sort_by_qn(v);
        }
        for v in self.call_callees.values_mut() {
            sort_by_qn(v);
        }
        for v in self.import_sources.values_mut() {
            sort_by_qn(v);
        }
        for v in self.import_targets.values_mut() {
            sort_by_qn(v);
        }
        for v in self.inherit_parents.values_mut() {
            sort_by_qn(v);
        }
        for v in self.inherit_children.values_mut() {
            sort_by_qn(v);
        }
        for v in self.contains_children.values_mut() {
            sort_by_qn(v);
        }
        for v in self.contains_parent.values_mut() {
            sort_by_qn(v);
        }
        for v in self.uses_targets.values_mut() {
            sort_by_qn(v);
        }
        for v in self.uses_sources.values_mut() {
            sort_by_qn(v);
        }
        for v in self.modified_in.values_mut() {
            sort_by_qn(v);
        }
    }

    /// Sort spatial indexes (function_spatial) by line_start for binary search
    /// in `function_at()`.
    fn build_spatial_indexes(&mut self, _graph: &StableGraph<CodeNode, CodeEdge>) {
        for spans in self.function_spatial.values_mut() {
            spans.sort_unstable_by_key(|(start, _, _)| *start);
        }
    }

    // ==================== Vec-based build path (new CSR pipeline) ====================

    /// Build all indexes from Vec-based node/edge data (the new freeze path).
    ///
    /// Internally constructs a shim StableGraph for import cycle detection,
    /// edge fingerprint computation, and GraphPrimitives. This shim will be
    /// removed in Tasks 7-10 when primitives are adapted to work directly
    /// with the CSR data.
    pub fn build_from_vecs(
        nodes: &[CodeNode],
        edges: &[(NodeIndex, NodeIndex, CodeEdge)],
        _node_index: &HashMap<StrKey, NodeIndex>,
        co_change: Option<&CoChangeMatrix>,
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

        // Step 2: Build adjacency maps from edge list
        for &(src, tgt, ref edge) in edges {
            match edge.kind {
                EdgeKind::Calls => {
                    indexes.call_callees.entry(src).or_default().push(tgt);
                    indexes.call_callers.entry(tgt).or_default().push(src);
                    indexes.all_call_edges.push((src, tgt));
                }
                EdgeKind::Imports => {
                    indexes.import_targets.entry(src).or_default().push(tgt);
                    indexes.import_sources.entry(tgt).or_default().push(src);
                    indexes.all_import_edges.push((src, tgt));
                }
                EdgeKind::Inherits => {
                    indexes.inherit_parents.entry(src).or_default().push(tgt);
                    indexes.inherit_children.entry(tgt).or_default().push(src);
                    indexes.all_inheritance_edges.push((src, tgt));
                }
                EdgeKind::Contains => {
                    indexes.contains_children.entry(src).or_default().push(tgt);
                    indexes.contains_parent.entry(tgt).or_default().push(src);
                }
                EdgeKind::Uses => {
                    indexes.uses_targets.entry(src).or_default().push(tgt);
                    indexes.uses_sources.entry(tgt).or_default().push(src);
                }
                EdgeKind::ModifiedIn => {
                    indexes.modified_in.entry(src).or_default().push(tgt);
                }
            }
        }

        // Sort adjacency vectors by qualified name for determinism
        for v in indexes.call_callers.values_mut() {
            sort_by_qn_vec(v);
        }
        for v in indexes.call_callees.values_mut() {
            sort_by_qn_vec(v);
        }
        for v in indexes.import_sources.values_mut() {
            sort_by_qn_vec(v);
        }
        for v in indexes.import_targets.values_mut() {
            sort_by_qn_vec(v);
        }
        for v in indexes.inherit_parents.values_mut() {
            sort_by_qn_vec(v);
        }
        for v in indexes.inherit_children.values_mut() {
            sort_by_qn_vec(v);
        }
        for v in indexes.contains_children.values_mut() {
            sort_by_qn_vec(v);
        }
        for v in indexes.contains_parent.values_mut() {
            sort_by_qn_vec(v);
        }
        for v in indexes.uses_targets.values_mut() {
            sort_by_qn_vec(v);
        }
        for v in indexes.uses_sources.values_mut() {
            sort_by_qn_vec(v);
        }
        for v in indexes.modified_in.values_mut() {
            sort_by_qn_vec(v);
        }

        // Step 3: Sort spatial indexes
        for spans in indexes.function_spatial.values_mut() {
            spans.sort_unstable_by_key(|(start, _, _)| *start);
        }

        // Step 4: Build shim StableGraph for primitives (uses petgraph's NodeIndex)
        let shim = build_shim_stable_graph(nodes, edges);

        // Step 5: Compute expensive analyses
        indexes.import_cycles = compute_import_cycles_from_vecs(nodes, edges);
        indexes.edge_fingerprint = compute_edge_fingerprint_from_vecs(nodes, edges);

        // Convert our NodeIndex slices/maps to petgraph's NodeIndex for primitives
        let pg_functions: Vec<petgraph::stable_graph::NodeIndex> =
            indexes.functions.iter().map(|idx| petgraph::stable_graph::NodeIndex::new(idx.index())).collect();
        let pg_files: Vec<petgraph::stable_graph::NodeIndex> =
            indexes.files.iter().map(|idx| petgraph::stable_graph::NodeIndex::new(idx.index())).collect();
        let pg_call_edges: Vec<(petgraph::stable_graph::NodeIndex, petgraph::stable_graph::NodeIndex)> =
            indexes.all_call_edges.iter().map(|&(s, t)| (petgraph::stable_graph::NodeIndex::new(s.index()), petgraph::stable_graph::NodeIndex::new(t.index()))).collect();
        let pg_import_edges: Vec<(petgraph::stable_graph::NodeIndex, petgraph::stable_graph::NodeIndex)> =
            indexes.all_import_edges.iter().map(|&(s, t)| (petgraph::stable_graph::NodeIndex::new(s.index()), petgraph::stable_graph::NodeIndex::new(t.index()))).collect();
        let pg_callers: HashMap<petgraph::stable_graph::NodeIndex, Vec<petgraph::stable_graph::NodeIndex>> =
            indexes.call_callers.iter().map(|(k, v)| (petgraph::stable_graph::NodeIndex::new(k.index()), v.iter().map(|idx| petgraph::stable_graph::NodeIndex::new(idx.index())).collect())).collect();
        let pg_callees: HashMap<petgraph::stable_graph::NodeIndex, Vec<petgraph::stable_graph::NodeIndex>> =
            indexes.call_callees.iter().map(|(k, v)| (petgraph::stable_graph::NodeIndex::new(k.index()), v.iter().map(|idx| petgraph::stable_graph::NodeIndex::new(idx.index())).collect())).collect();

        indexes.primitives = super::primitives::GraphPrimitives::compute(
            &shim,
            &pg_functions,
            &pg_files,
            &pg_call_edges,
            &pg_import_edges,
            &pg_callers,
            &pg_callees,
            indexes.edge_fingerprint,
            co_change,
        );

        indexes
    }
}

/// Build a shim StableGraph from Vec-based data for use with petgraph algorithms
/// that haven't been migrated yet (primitives, etc.).
///
/// The petgraph NodeIndex values will match our NodeIndex values (both are
/// sequential from 0) since we add nodes in order.
fn build_shim_stable_graph(
    nodes: &[CodeNode],
    edges: &[(NodeIndex, NodeIndex, CodeEdge)],
) -> StableGraph<CodeNode, CodeEdge> {
    let mut graph = StableGraph::with_capacity(nodes.len(), edges.len());
    for node in nodes {
        graph.add_node(*node);
    }
    for &(src, tgt, ref edge) in edges {
        let pg_src = petgraph::stable_graph::NodeIndex::new(src.index());
        let pg_tgt = petgraph::stable_graph::NodeIndex::new(tgt.index());
        graph.add_edge(pg_src, pg_tgt, *edge);
    }
    graph
}

/// Compute import cycles from Vec-based data using our hand-rolled Tarjan SCC.
fn compute_import_cycles_from_vecs(
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

/// Compute edge fingerprint from Vec-based data.
fn compute_edge_fingerprint_from_vecs(
    nodes: &[CodeNode],
    edges: &[(NodeIndex, NodeIndex, CodeEdge)],
) -> u64 {
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

/// Compute import cycles using Tarjan's SCC algorithm.
///
/// Builds a filtered subgraph containing only Import edges (excluding type-only imports),
/// runs Tarjan SCC, and returns SCCs with >1 node (actual cycles) as Vec<Vec<NodeIndex>>.
/// Results are sorted by cycle size descending, then by node names for determinism.
fn compute_import_cycles(graph: &StableGraph<CodeNode, CodeEdge>) -> Vec<Vec<NodeIndex>> {
    use petgraph::stable_graph::NodeIndex as PgNodeIndex;
    let si = global_interner();

    // Build a filtered subgraph with only non-type-only Import edges
    let mut filtered_graph: StableGraph<PgNodeIndex, ()> = StableGraph::new();
    let mut idx_map: HashMap<PgNodeIndex, PgNodeIndex> = HashMap::default();
    let mut reverse_map: HashMap<PgNodeIndex, PgNodeIndex> = HashMap::default();

    // Collect nodes that have at least one non-type-only import edge
    let relevant_nodes: HashSet<PgNodeIndex> = graph
        .edge_references()
        .filter(|e| {
            if e.weight().kind != EdgeKind::Imports {
                return false;
            }
            if e.weight().is_type_only() {
                return false;
            }
            true
        })
        .flat_map(|e| [e.source(), e.target()])
        .collect();

    let mut sorted_nodes: Vec<PgNodeIndex> = relevant_nodes.into_iter().collect();
    sorted_nodes.sort_by_key(|idx| idx.index());

    for orig_idx in sorted_nodes {
        let new_idx = filtered_graph.add_node(orig_idx);
        idx_map.insert(orig_idx, new_idx);
        reverse_map.insert(new_idx, orig_idx);
    }

    for edge in graph.edge_references() {
        if edge.weight().kind != EdgeKind::Imports {
            continue;
        }
        if edge.weight().is_type_only() {
            continue;
        }
        if let (Some(&from), Some(&to)) = (idx_map.get(&edge.source()), idx_map.get(&edge.target()))
        {
            filtered_graph.add_edge(from, to, ());
        }
    }

    let sccs = tarjan_scc(&filtered_graph);

    let mut cycles: Vec<Vec<NodeIndex>> = sccs
        .into_iter()
        .filter(|scc| scc.len() > 1)
        .map(|scc| {
            let mut orig_indices: Vec<NodeIndex> = scc
                .iter()
                .filter_map(|&filtered_idx| reverse_map.get(&filtered_idx).map(|pg| from_pg(*pg)))
                .collect();

            orig_indices.sort_by(|a, b| {
                let a_qn = graph
                    .node_weight(to_pg(*a))
                    .map(|n| si.resolve(n.qualified_name))
                    .unwrap_or("");
                let b_qn = graph
                    .node_weight(to_pg(*b))
                    .map(|n| si.resolve(n.qualified_name))
                    .unwrap_or("");
                a_qn.cmp(b_qn)
            });
            orig_indices
        })
        .collect();

    cycles.sort_by(|a, b| {
        b.len().cmp(&a.len()).then_with(|| {
            let a_qn = a
                .first()
                .and_then(|idx| graph.node_weight(to_pg(*idx)))
                .map(|n| si.resolve(n.qualified_name))
                .unwrap_or("");
            let b_qn = b
                .first()
                .and_then(|idx| graph.node_weight(to_pg(*idx)))
                .map(|n| si.resolve(n.qualified_name))
                .unwrap_or("");
            a_qn.cmp(b_qn)
        })
    });

    cycles.dedup();
    cycles
}

/// Compute a fingerprint of all cross-file edges for topology change detection.
///
/// Hashes (source_qn, target_qn, kind) tuples for edges where source and target
/// are in different files. Replicates the logic from `GraphBuilder::compute_edge_fingerprint()`.
fn compute_edge_fingerprint(graph: &StableGraph<CodeNode, CodeEdge>) -> u64 {
    use std::collections::hash_map::DefaultHasher;

    let mut edges: Vec<(u32, u32, u8)> = graph
        .edge_references()
        .filter(|e| {
            let src = &graph[e.source()];
            let tgt = &graph[e.target()];
            src.file_path != tgt.file_path
        })
        .map(|e| {
            let src = &graph[e.source()];
            let tgt = &graph[e.target()];
            (
                src.qualified_name.as_u32(),
                tgt.qualified_name.as_u32(),
                e.weight().kind as u8,
            )
        })
        .collect();
    edges.sort_unstable();

    let mut hasher = DefaultHasher::new();
    for (src, tgt, kind) in &edges {
        src.hash(&mut hasher);
        tgt.hash(&mut hasher);
        kind.hash(&mut hasher);
    }
    hasher.finish()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_build_empty_graph() {
        let graph = StableGraph::new();
        let node_index = HashMap::default();
        let indexes = GraphIndexes::build(&graph, &node_index, None);
        assert!(indexes.functions.is_empty());
        assert!(indexes.classes.is_empty());
        assert!(indexes.files.is_empty());
        assert!(indexes.import_cycles.is_empty());
        // Empty graph has no cross-file edges, so fingerprint is just the
        // DefaultHasher's initial state (not necessarily 0)
    }

    #[test]
    fn test_build_kind_indexes() {
        let mut graph = StableGraph::new();
        let f1 = graph.add_node(CodeNode::function("foo", "a.py"));
        let f2 = graph.add_node(CodeNode::function("bar", "a.py"));
        let c1 = graph.add_node(CodeNode::class("MyClass", "a.py"));
        let file = graph.add_node(CodeNode::file("a.py"));

        let si = global_interner();
        let mut node_index: HashMap<StrKey, NodeIndex> = HashMap::default();
        node_index.insert(si.intern("a.py::foo"), from_pg(f1));
        node_index.insert(si.intern("a.py::bar"), from_pg(f2));
        node_index.insert(si.intern("a.py::MyClass"), from_pg(c1));
        node_index.insert(si.intern("a.py"), from_pg(file));

        let indexes = GraphIndexes::build(&graph, &node_index, None);
        assert_eq!(indexes.functions.len(), 2);
        assert_eq!(indexes.classes.len(), 1);
        assert_eq!(indexes.files.len(), 1);
    }

    #[test]
    fn test_build_adjacency_indexes() {
        let mut graph = StableGraph::new();
        let pg_f1 = graph.add_node(CodeNode::function("foo", "a.py"));
        let pg_f2 = graph.add_node(CodeNode::function("bar", "a.py"));
        graph.add_edge(pg_f1, pg_f2, CodeEdge::calls());
        let f1: NodeIndex = from_pg(pg_f1);
        let f2: NodeIndex = from_pg(pg_f2);

        let node_index = HashMap::default();
        let indexes = GraphIndexes::build(&graph, &node_index, None);

        // f1 calls f2
        assert_eq!(indexes.call_callees.get(&f1).map(|v| v.len()), Some(1));
        assert_eq!(indexes.call_callers.get(&f2).map(|v| v.len()), Some(1));

        // f2 doesn't call anything
        assert!(indexes.call_callees.get(&f2).is_none());
        // f1 has no callers
        assert!(indexes.call_callers.get(&f1).is_none());

        // Bulk edge list
        assert_eq!(indexes.all_call_edges.len(), 1);
    }

    #[test]
    fn test_import_cycle_detection() {
        let mut graph = StableGraph::new();
        let a = graph.add_node(CodeNode::file("a.py"));
        let b = graph.add_node(CodeNode::file("b.py"));
        let c = graph.add_node(CodeNode::file("c.py"));

        // a -> b -> c -> a (cycle)
        graph.add_edge(a, b, CodeEdge::imports());
        graph.add_edge(b, c, CodeEdge::imports());
        graph.add_edge(c, a, CodeEdge::imports());

        let node_index = HashMap::default();
        let indexes = GraphIndexes::build(&graph, &node_index, None);

        assert_eq!(indexes.import_cycles.len(), 1);
        assert_eq!(indexes.import_cycles[0].len(), 3);
    }

    #[test]
    fn test_spatial_index_sorted() {
        let si = global_interner();
        let fp = si.intern("test.py");
        let mut graph = StableGraph::new();

        // Add functions out of order
        let f2 = graph.add_node(CodeNode::function("bar", "test.py").with_lines(20, 30));
        let f1 = graph.add_node(CodeNode::function("foo", "test.py").with_lines(1, 10));
        let _ = (f1, f2);

        let node_index = HashMap::default();
        let indexes = GraphIndexes::build(&graph, &node_index, None);

        let spatial = indexes.function_spatial.get(&fp).unwrap();
        // Should be sorted by line_start
        assert!(spatial[0].0 <= spatial[1].0);
    }

    #[test]
    fn test_edge_fingerprint_deterministic() {
        let mut graph = StableGraph::new();
        let f1 = graph.add_node(CodeNode::function("foo", "a.py"));
        let f2 = graph.add_node(CodeNode::function("bar", "b.py"));
        graph.add_edge(f1, f2, CodeEdge::calls());

        let fp1 = compute_edge_fingerprint(&graph);
        let fp2 = compute_edge_fingerprint(&graph);
        assert_eq!(fp1, fp2);
        assert_ne!(fp1, 0); // cross-file edge should produce non-zero fingerprint
    }

    #[test]
    fn test_edge_fingerprint_ignores_same_file() {
        // Graph with only same-file edges should produce the same fingerprint
        // as an empty graph (no cross-file edges to hash)
        let empty_graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let empty_fp = compute_edge_fingerprint(&empty_graph);

        let mut graph = StableGraph::new();
        let f1 = graph.add_node(CodeNode::function("foo", "a.py"));
        let f2 = graph.add_node(CodeNode::function("bar", "a.py"));
        graph.add_edge(f1, f2, CodeEdge::calls());

        let fp = compute_edge_fingerprint(&graph);
        assert_eq!(fp, empty_fp); // same-file edge → same as empty hash
    }
}
