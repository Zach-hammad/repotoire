//! Frozen, immutable, indexed code graph.
//!
//! `CodeGraph` is the read-only graph produced by `GraphBuilder::freeze()`.
//! All queries are O(1) lookups into pre-built indexes — no locks, no scans.
//! Safe to share across rayon threads via `&CodeGraph`.

use crate::graph::node_index::NodeIndex;
use std::collections::{BTreeMap, HashMap};

use super::builder::GraphBuilder;
use super::csr::CsrStorage;
use super::indexes::GraphIndexes;
use super::interner::{global_interner, StrKey, StringInterner};
use super::store_models::{CodeEdge, CodeNode, EdgeKind, ExtraProps};

/// Immutable code graph with pre-built indexes.
///
/// All queries are O(1) lookups. No locks — all methods take `&self`.
/// Safe to share across rayon threads. Produced by `GraphBuilder::freeze()`.
pub struct CodeGraph {
    /// Compact node array (no tombstones — compacted during build).
    nodes: Vec<CodeNode>,
    /// CSR edge storage for O(1) neighbor lookups.
    csr: CsrStorage,
    /// Node lookup by qualified name (interned StrKey → NodeIndex).
    node_index: HashMap<StrKey, NodeIndex>,
    /// Edge list (kept for into_parts / persistence).
    edges: Vec<(NodeIndex, NodeIndex, CodeEdge)>,
    /// Extra (cold) properties stored per qualified_name StrKey.
    extra_props: HashMap<StrKey, ExtraProps>,
    /// Pre-built indexes computed during build().
    indexes: GraphIndexes,
}

/// Empty slice constant for returning `&[]` from adjacency lookups.
const EMPTY_NODE_SLICE: &[NodeIndex] = &[];

impl CodeGraph {
    // ==================== Construction (crate-internal) ====================

    /// Build a CodeGraph from raw builder data.
    ///
    /// This is the freeze path:
    /// 1. Compact nodes (remove None tombstones, build old→new remap)
    /// 2. Remap edge indices
    /// 3. Build CsrStorage from edges
    /// 4. Build GraphIndexes
    /// 5. Compute GraphPrimitives (via shim StableGraph for now)
    pub(crate) fn build(
        opt_nodes: Vec<Option<CodeNode>>,
        old_node_index: HashMap<StrKey, NodeIndex>,
        old_edges: Vec<(NodeIndex, NodeIndex, CodeEdge)>,
        extra_props: HashMap<StrKey, ExtraProps>,
        co_change: Option<&crate::git::co_change::CoChangeMatrix>,
    ) -> Self {
        // Step 1: Compact nodes — remove tombstones, build remap
        let mut nodes = Vec::with_capacity(opt_nodes.len());
        let mut remap: HashMap<usize, usize> = HashMap::new();
        for (old_idx, slot) in opt_nodes.into_iter().enumerate() {
            if let Some(node) = slot {
                let new_idx = nodes.len();
                remap.insert(old_idx, new_idx);
                nodes.push(node);
            }
        }

        // Step 2: Remap node_index
        let node_index: HashMap<StrKey, NodeIndex> = old_node_index
            .into_iter()
            .filter_map(|(key, old_idx)| {
                remap
                    .get(&old_idx.index())
                    .map(|&new_idx| (key, NodeIndex::new(new_idx as u32)))
            })
            .collect();

        // Step 3: Remap and filter edges
        let edges: Vec<(NodeIndex, NodeIndex, CodeEdge)> = old_edges
            .into_iter()
            .filter_map(|(src, tgt, edge)| {
                let new_src = remap.get(&src.index())?;
                let new_tgt = remap.get(&tgt.index())?;
                Some((
                    NodeIndex::new(*new_src as u32),
                    NodeIndex::new(*new_tgt as u32),
                    edge,
                ))
            })
            .collect();

        // Step 4: Build CSR from remapped edges
        let csr_edges: Vec<(u32, u32, EdgeKind)> = edges
            .iter()
            .map(|&(src, tgt, ref e)| (src.as_u32(), tgt.as_u32(), e.kind))
            .collect();
        let csr = CsrStorage::build(nodes.len(), &csr_edges);

        // Step 5: Build indexes (without primitives — those need &CodeGraph)
        let indexes = GraphIndexes::build_from_vecs(&nodes, &edges, &node_index, co_change);

        // Step 6: Build CodeGraph struct (without primitives yet)
        let mut result = Self {
            nodes,
            csr,
            node_index,
            edges,
            extra_props,
            indexes,
        };

        // Step 7: Compute primitives using &CodeGraph, then assign them.
        // We extract the index data we need to avoid borrowing &result and &mut result.indexes
        // simultaneously. GraphPrimitives::compute() reads from CodeGraph (nodes, CSR).
        let primitives = super::primitives::GraphPrimitives::compute(
            &result,
            &result.indexes.functions.clone(),
            &result.indexes.files.clone(),
            &result.indexes.all_call_edges.clone(),
            &result.indexes.all_import_edges.clone(),
            result.indexes.edge_fingerprint,
            co_change,
        );
        result.indexes.set_primitives(primitives);

        result
    }

    /// Create a CodeGraph from pre-built parts (used by persistence load).
    pub(crate) fn from_parts(
        nodes: Vec<CodeNode>,
        node_index: HashMap<StrKey, NodeIndex>,
        edges: Vec<(NodeIndex, NodeIndex, CodeEdge)>,
        extra_props: HashMap<StrKey, ExtraProps>,
        indexes: GraphIndexes,
    ) -> Self {
        let csr_edges: Vec<(u32, u32, EdgeKind)> = edges
            .iter()
            .map(|&(src, tgt, ref e)| (src.as_u32(), tgt.as_u32(), e.kind))
            .collect();
        let csr = CsrStorage::build(nodes.len(), &csr_edges);

        Self {
            nodes,
            csr,
            node_index,
            edges,
            extra_props,
            indexes,
        }
    }

    /// Decompose into constituent parts for conversion back to a builder.
    /// Called by `GraphBuilder::from_frozen()`.
    pub(crate) fn into_parts(
        self,
    ) -> (
        Vec<CodeNode>,
        HashMap<StrKey, NodeIndex>,
        Vec<(NodeIndex, NodeIndex, CodeEdge)>,
        HashMap<StrKey, ExtraProps>,
    ) {
        (self.nodes, self.node_index, self.edges, self.extra_props)
    }

    // ==================== String Interner ====================

    /// Access the global string interner.
    pub fn interner(&self) -> &'static StringInterner {
        global_interner()
    }

    // ==================== Node Access (O(1)) ====================

    /// Get a node by its graph index.
    pub fn node(&self, idx: NodeIndex) -> Option<&CodeNode> {
        self.nodes.get(idx.index())
    }

    /// Look up a node by qualified name. Returns both index and reference.
    pub fn node_by_name(&self, qn: &str) -> Option<(NodeIndex, &CodeNode)> {
        let key = self.interner().intern(qn);
        let &idx = self.node_index.get(&key)?;
        let node = self.nodes.get(idx.index())?;
        Some((idx, node))
    }

    /// Look up a node by interned StrKey. Returns both index and reference.
    pub fn node_by_key(&self, key: StrKey) -> Option<(NodeIndex, &CodeNode)> {
        let &idx = self.node_index.get(&key)?;
        let node = self.nodes.get(idx.index())?;
        Some((idx, node))
    }

    // ==================== Kind-Indexed Collections (O(1)) ====================

    /// All function NodeIndexes (sorted by qualified_name).
    pub fn functions(&self) -> &[NodeIndex] {
        &self.indexes.functions
    }

    /// All class NodeIndexes (sorted by qualified_name).
    pub fn classes(&self) -> &[NodeIndex] {
        &self.indexes.classes
    }

    /// All file NodeIndexes (sorted by qualified_name).
    pub fn files(&self) -> &[NodeIndex] {
        &self.indexes.files
    }

    // ==================== Adjacency Queries (O(1) via CSR) ====================

    /// Functions that call this node (incoming Calls edges).
    pub fn callers(&self, idx: NodeIndex) -> &[NodeIndex] {
        if idx.index() >= self.nodes.len() {
            return EMPTY_NODE_SLICE;
        }
        self.csr
            .neighbors_as_node_index(idx.index(), super::csr::slot::CALLS_IN)
    }

    /// Functions this node calls (outgoing Calls edges).
    pub fn callees(&self, idx: NodeIndex) -> &[NodeIndex] {
        if idx.index() >= self.nodes.len() {
            return EMPTY_NODE_SLICE;
        }
        self.csr
            .neighbors_as_node_index(idx.index(), super::csr::slot::CALLS_OUT)
    }

    /// Modules/files that import this node (incoming Imports edges).
    pub fn importers(&self, idx: NodeIndex) -> &[NodeIndex] {
        if idx.index() >= self.nodes.len() {
            return EMPTY_NODE_SLICE;
        }
        self.csr
            .neighbors_as_node_index(idx.index(), super::csr::slot::IMPORTS_IN)
    }

    /// Modules/files this node imports (outgoing Imports edges).
    pub fn importees(&self, idx: NodeIndex) -> &[NodeIndex] {
        if idx.index() >= self.nodes.len() {
            return EMPTY_NODE_SLICE;
        }
        self.csr
            .neighbors_as_node_index(idx.index(), super::csr::slot::IMPORTS_OUT)
    }

    /// Parent classes (outgoing Inherits edges).
    pub fn parent_classes(&self, idx: NodeIndex) -> &[NodeIndex] {
        if idx.index() >= self.nodes.len() {
            return EMPTY_NODE_SLICE;
        }
        self.csr
            .neighbors_as_node_index(idx.index(), super::csr::slot::INHERITS_OUT)
    }

    /// Child classes (incoming Inherits edges).
    pub fn child_classes(&self, idx: NodeIndex) -> &[NodeIndex] {
        if idx.index() >= self.nodes.len() {
            return EMPTY_NODE_SLICE;
        }
        self.csr
            .neighbors_as_node_index(idx.index(), super::csr::slot::INHERITS_IN)
    }

    /// Entities contained by this node (outgoing Contains edges).
    pub fn contains_children(&self, idx: NodeIndex) -> &[NodeIndex] {
        if idx.index() >= self.nodes.len() {
            return EMPTY_NODE_SLICE;
        }
        self.csr
            .neighbors_as_node_index(idx.index(), super::csr::slot::CONTAINS_OUT)
    }

    /// Parent container of this node (incoming Contains edges).
    pub fn contains_parent(&self, idx: NodeIndex) -> &[NodeIndex] {
        if idx.index() >= self.nodes.len() {
            return EMPTY_NODE_SLICE;
        }
        self.csr
            .neighbors_as_node_index(idx.index(), super::csr::slot::CONTAINS_IN)
    }

    /// Entities this node uses (outgoing Uses edges).
    pub fn uses_targets(&self, idx: NodeIndex) -> &[NodeIndex] {
        if idx.index() >= self.nodes.len() {
            return EMPTY_NODE_SLICE;
        }
        self.csr
            .neighbors_as_node_index(idx.index(), super::csr::slot::USES_OUT)
    }

    /// Entities that use this node (incoming Uses edges).
    pub fn uses_sources(&self, idx: NodeIndex) -> &[NodeIndex] {
        if idx.index() >= self.nodes.len() {
            return EMPTY_NODE_SLICE;
        }
        self.csr
            .neighbors_as_node_index(idx.index(), super::csr::slot::USES_IN)
    }

    /// Commits that modified this entity (outgoing ModifiedIn edges).
    pub fn modified_in(&self, idx: NodeIndex) -> &[NodeIndex] {
        if idx.index() >= self.nodes.len() {
            return EMPTY_NODE_SLICE;
        }
        self.csr
            .neighbors_as_node_index(idx.index(), super::csr::slot::MODIFIED_IN_OUT)
    }

    /// Number of callers (fan-in). O(1).
    pub fn call_fan_in(&self, idx: NodeIndex) -> usize {
        self.callers(idx).len()
    }

    /// Number of callees (fan-out). O(1).
    pub fn call_fan_out(&self, idx: NodeIndex) -> usize {
        self.callees(idx).len()
    }

    // ==================== File-Scoped Queries (O(1)) ====================

    /// Functions in a file (sorted by qualified_name).
    pub fn functions_in_file(&self, file_path: &str) -> &[NodeIndex] {
        let key = self.interner().intern(file_path);
        self.functions_in_file_by_key(key)
    }

    /// Functions in a file by interned key (O(1)).
    pub fn functions_in_file_by_key(&self, key: StrKey) -> &[NodeIndex] {
        self.indexes
            .functions_by_file
            .get(&key)
            .map(|v| v.as_slice())
            .unwrap_or(EMPTY_NODE_SLICE)
    }

    /// Classes in a file (sorted by qualified_name).
    pub fn classes_in_file(&self, file_path: &str) -> &[NodeIndex] {
        let key = self.interner().intern(file_path);
        self.classes_in_file_by_key(key)
    }

    /// Classes in a file by interned key (O(1)).
    pub fn classes_in_file_by_key(&self, key: StrKey) -> &[NodeIndex] {
        self.indexes
            .classes_by_file
            .get(&key)
            .map(|v| v.as_slice())
            .unwrap_or(EMPTY_NODE_SLICE)
    }

    /// All nodes in a file.
    pub fn all_nodes_in_file(&self, file_path: &str) -> &[NodeIndex] {
        let key = self.interner().intern(file_path);
        self.indexes
            .all_nodes_by_file
            .get(&key)
            .map(|v| v.as_slice())
            .unwrap_or(EMPTY_NODE_SLICE)
    }

    /// Find the function containing a line in a file.
    ///
    /// Uses binary search on the spatial index — O(log N) where N is the number
    /// of functions in the file.
    pub fn function_at(&self, file_path: &str, line: u32) -> Option<NodeIndex> {
        let key = self.interner().intern(file_path);
        let spans = self.indexes.function_spatial.get(&key)?;

        // Binary search for the first function whose line_start <= line
        // Since functions can overlap (nested), we scan from the binary search point
        let pos = spans.partition_point(|(start, _, _)| *start <= line);
        // Check entries before the partition point (functions that started before this line)
        for &(start, end, idx) in spans[..pos].iter().rev() {
            if start <= line && end >= line {
                return Some(idx);
            }
            // Optimization: if we've gone past possible containing functions, stop
            if end < line && start < line {
                // This function ends before our line and all previous start even earlier
                // but they could have larger spans, so we can't break early here.
                // Continue scanning.
            }
        }
        None
    }

    // ==================== Pre-Computed Analyses ====================

    /// Import cycle groups (computed during freeze). Each inner Vec contains
    /// NodeIndexes of nodes in the cycle, sorted by qualified_name.
    pub fn import_cycles(&self) -> &[Vec<NodeIndex>] {
        &self.indexes.import_cycles
    }

    /// Edge fingerprint for topology change detection.
    /// Changes when cross-file edges are added/removed.
    pub fn edge_fingerprint(&self) -> u64 {
        self.indexes.edge_fingerprint
    }

    // ==================== Graph Primitives (O(1)) ====================

    /// Access the pre-computed graph primitives struct directly.
    pub fn primitives(&self) -> &super::primitives::GraphPrimitives {
        &self.indexes.primitives
    }

    pub fn immediate_dominator(&self, idx: NodeIndex) -> Option<NodeIndex> {
        self.indexes.primitives.idom.get(&idx).copied()
    }

    pub fn dominated_by(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.indexes
            .primitives
            .dominated
            .get(&idx)
            .map(|v| v.as_slice())
            .unwrap_or(EMPTY_NODE_SLICE)
    }

    pub fn domination_frontier(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.indexes
            .primitives
            .frontier
            .get(&idx)
            .map(|v| v.as_slice())
            .unwrap_or(EMPTY_NODE_SLICE)
    }

    pub fn dominator_depth(&self, idx: NodeIndex) -> usize {
        self.indexes
            .primitives
            .dom_depth
            .get(&idx)
            .copied()
            .unwrap_or(0)
    }

    pub fn domination_count(&self, idx: NodeIndex) -> usize {
        self.dominated_by(idx).len()
    }

    pub fn is_articulation_point(&self, idx: NodeIndex) -> bool {
        self.indexes
            .primitives
            .articulation_point_set
            .contains(&idx)
    }

    /// Articulation points (our NodeIndex).
    pub fn articulation_points(&self) -> &[NodeIndex] {
        &self.indexes.primitives.articulation_points
    }

    /// Bridges (our NodeIndex pairs).
    pub fn bridges(&self) -> &[(NodeIndex, NodeIndex)] {
        &self.indexes.primitives.bridges
    }

    pub fn separation_sizes(&self, idx: NodeIndex) -> Option<&[usize]> {
        self.indexes
            .primitives
            .component_sizes
            .get(&idx)
            .map(|v| v.as_slice())
    }

    /// Call cycles (our NodeIndex).
    pub fn call_cycles(&self) -> &[Vec<NodeIndex>] {
        &self.indexes.primitives.call_cycles
    }

    pub fn page_rank(&self, idx: NodeIndex) -> f64 {
        self.indexes
            .primitives
            .page_rank
            .get(&idx)
            .copied()
            .unwrap_or(0.0)
    }

    pub fn betweenness(&self, idx: NodeIndex) -> f64 {
        self.indexes
            .primitives
            .betweenness
            .get(&idx)
            .copied()
            .unwrap_or(0.0)
    }

    pub fn call_depth(&self, idx: NodeIndex) -> usize {
        self.indexes
            .primitives
            .call_depth
            .get(&idx)
            .copied()
            .unwrap_or(0)
    }

    pub fn weighted_page_rank(&self, idx: NodeIndex) -> f64 {
        self.indexes
            .primitives
            .weighted_page_rank
            .get(&idx)
            .copied()
            .unwrap_or(0.0)
    }

    pub fn weighted_betweenness(&self, idx: NodeIndex) -> f64 {
        self.indexes
            .primitives
            .weighted_betweenness
            .get(&idx)
            .copied()
            .unwrap_or(0.0)
    }

    pub fn community(&self, idx: NodeIndex) -> Option<usize> {
        self.indexes.primitives.community.get(&idx).copied()
    }

    pub fn graph_modularity(&self) -> f64 {
        self.indexes.primitives.modularity
    }

    /// Hidden coupling (our NodeIndex).
    pub fn hidden_coupling(&self) -> &[(NodeIndex, NodeIndex, f32, f32, f32)] {
        &self.indexes.primitives.hidden_coupling
    }

    /// Graph statistics (BTreeMap for deterministic key order).
    pub fn stats(&self) -> BTreeMap<String, i64> {
        let mut stats = BTreeMap::new();
        stats.insert("total_files".to_string(), self.indexes.files.len() as i64);
        stats.insert(
            "total_functions".to_string(),
            self.indexes.functions.len() as i64,
        );
        stats.insert(
            "total_classes".to_string(),
            self.indexes.classes.len() as i64,
        );
        stats.insert("total_nodes".to_string(), self.nodes.len() as i64);
        stats.insert("total_edges".to_string(), self.edges.len() as i64);
        stats
    }

    // ==================== Bulk Edge Access ====================

    /// All call edges as (caller, callee) NodeIndex pairs.
    pub fn all_call_edges(&self) -> &[(NodeIndex, NodeIndex)] {
        &self.indexes.all_call_edges
    }

    /// All import edges as (importer, importee) NodeIndex pairs.
    pub fn all_import_edges(&self) -> &[(NodeIndex, NodeIndex)] {
        &self.indexes.all_import_edges
    }

    /// All inheritance edges as (child, parent) NodeIndex pairs.
    pub fn all_inheritance_edges(&self) -> &[(NodeIndex, NodeIndex)] {
        &self.indexes.all_inheritance_edges
    }

    // ==================== Cold Properties ====================

    /// Extra properties for a node (cold string data like params, doc_comment).
    pub fn extra_props(&self, qn: StrKey) -> Option<&ExtraProps> {
        self.extra_props.get(&qn)
    }

    // ==================== Raw Access ====================

    /// Access the node_index map (qualified_name → NodeIndex).
    pub fn node_index_map(&self) -> &HashMap<StrKey, NodeIndex> {
        &self.node_index
    }

    /// Access the compact node array.
    pub fn nodes(&self) -> &[CodeNode] {
        &self.nodes
    }

    /// Access the edge list.
    pub fn edge_list(&self) -> &[(NodeIndex, NodeIndex, CodeEdge)] {
        &self.edges
    }

    // ==================== Node/Edge Counts ====================

    /// Total node count.
    pub fn node_count(&self) -> usize {
        self.nodes.len()
    }

    /// Total edge count.
    pub fn edge_count(&self) -> usize {
        self.edges.len()
    }

    // ==================== Lifecycle ====================

    /// Convert back to a mutable builder for incremental patching.
    ///
    /// Moves nodes and edges without copying.
    /// Rebuilds edge_set from edges — O(E).
    /// Indexes are dropped (rebuilt on re-freeze).
    pub fn into_builder(self) -> GraphBuilder {
        GraphBuilder::from_frozen(self)
    }

    /// Clone the graph into a new builder (leaves this CodeGraph intact).
    ///
    /// O(N + E) — clones all nodes and edges.
    pub fn clone_into_builder(&self) -> GraphBuilder {
        let cloned_edges = self.edges.clone();
        let cloned_node_index = self.node_index.clone();
        let cloned_extra_props = self.extra_props.clone();

        // Build a temporary CodeGraph to pass to from_frozen
        let temp = CodeGraph::from_parts(
            self.nodes.clone(),
            cloned_node_index,
            cloned_edges,
            cloned_extra_props,
            GraphIndexes::default(),
        );
        GraphBuilder::from_frozen(temp)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::builder::GraphBuilder;
    use crate::graph::store_models::NodeKind;

    #[test]
    fn test_node_access() {
        let mut builder = GraphBuilder::new();
        let idx = builder.add_node(CodeNode::function("foo", "a.py"));
        let graph = builder.freeze();

        // By index — note: index may be remapped after compaction
        let (found_idx, found_node) = graph.node_by_name("a.py::foo").unwrap();
        assert_eq!(found_node.kind, NodeKind::Function);

        // By name
        let node = graph.node(found_idx).unwrap();
        assert_eq!(node.kind, NodeKind::Function);

        // Non-existent
        assert!(graph.node_by_name("nonexistent").is_none());
    }

    #[test]
    fn test_kind_indexes() {
        let mut builder = GraphBuilder::new();
        builder.add_node(CodeNode::file("a.py"));
        builder.add_node(CodeNode::function("foo", "a.py"));
        builder.add_node(CodeNode::function("bar", "a.py"));
        builder.add_node(CodeNode::class("MyClass", "a.py"));

        let graph = builder.freeze();

        assert_eq!(graph.functions().len(), 2);
        assert_eq!(graph.classes().len(), 1);
        assert_eq!(graph.files().len(), 1);
    }

    #[test]
    fn test_adjacency_queries() {
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("foo", "a.py"));
        let f2 = builder.add_node(CodeNode::function("bar", "a.py"));
        let f3 = builder.add_node(CodeNode::function("baz", "a.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());
        builder.add_edge(f1, f3, CodeEdge::calls());

        let graph = builder.freeze();

        // Look up by name since indices may be remapped
        let (new_f1, _) = graph.node_by_name("a.py::foo").unwrap();
        let (new_f2, _) = graph.node_by_name("a.py::bar").unwrap();
        let (new_f3, _) = graph.node_by_name("a.py::baz").unwrap();

        assert_eq!(graph.callees(new_f1).len(), 2);
        assert_eq!(graph.callers(new_f2).len(), 1);
        assert_eq!(graph.callers(new_f3).len(), 1);
        assert!(graph.callers(new_f1).is_empty());
        assert_eq!(graph.call_fan_out(new_f1), 2);
        assert_eq!(graph.call_fan_in(new_f2), 1);
    }

    #[test]
    fn test_file_scoped_queries() {
        let mut builder = GraphBuilder::new();
        builder.add_node(CodeNode::function("foo", "a.py").with_lines(1, 10));
        builder.add_node(CodeNode::function("bar", "a.py").with_lines(12, 20));
        builder.add_node(CodeNode::class("MyClass", "a.py"));
        builder.add_node(CodeNode::function("baz", "b.py"));

        let graph = builder.freeze();

        assert_eq!(graph.functions_in_file("a.py").len(), 2);
        assert_eq!(graph.functions_in_file("b.py").len(), 1);
        assert_eq!(graph.functions_in_file("c.py").len(), 0);

        assert_eq!(graph.classes_in_file("a.py").len(), 1);
        assert_eq!(graph.classes_in_file("b.py").len(), 0);
    }

    #[test]
    fn test_function_at() {
        let mut builder = GraphBuilder::new();
        builder.add_node(CodeNode::function("foo", "a.py").with_lines(1, 10));
        builder.add_node(CodeNode::function("bar", "a.py").with_lines(12, 20));

        let graph = builder.freeze();

        let (f1, _) = graph.node_by_name("a.py::foo").unwrap();
        let (f2, _) = graph.node_by_name("a.py::bar").unwrap();

        assert_eq!(graph.function_at("a.py", 5), Some(f1));
        assert_eq!(graph.function_at("a.py", 15), Some(f2));
        assert_eq!(graph.function_at("a.py", 11), None); // gap between functions
        assert_eq!(graph.function_at("b.py", 1), None); // non-existent file
    }

    #[test]
    fn test_stats() {
        let mut builder = GraphBuilder::new();
        builder.add_node(CodeNode::file("a.py"));
        builder.add_node(CodeNode::function("foo", "a.py"));
        builder.add_node(CodeNode::class("MyClass", "a.py"));
        let f1 = builder.add_node(CodeNode::function("bar", "a.py"));
        let f2 = builder.add_node(CodeNode::function("baz", "a.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());

        let graph = builder.freeze();
        let stats = graph.stats();

        assert_eq!(stats["total_files"], 1);
        assert_eq!(stats["total_functions"], 3);
        assert_eq!(stats["total_classes"], 1);
        assert_eq!(stats["total_nodes"], 5);
        assert_eq!(stats["total_edges"], 1);
    }

    #[test]
    fn test_import_cycles() {
        let mut builder = GraphBuilder::new();
        let a = builder.add_node(CodeNode::file("a.py"));
        let b = builder.add_node(CodeNode::file("b.py"));
        builder.add_edge(a, b, CodeEdge::imports());
        builder.add_edge(b, a, CodeEdge::imports());

        let graph = builder.freeze();
        assert_eq!(graph.import_cycles().len(), 1);
        assert_eq!(graph.import_cycles()[0].len(), 2);
    }

    #[test]
    fn test_into_builder_and_refreeze() {
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("foo", "a.py"));
        let f2 = builder.add_node(CodeNode::function("bar", "a.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());

        let graph = builder.freeze();
        let mut builder = graph.into_builder();

        // Verify state is preserved
        assert_eq!(builder.node_count(), 2);
        assert_eq!(builder.edge_count(), 1);

        // Add more data
        let f3 = builder.add_node(CodeNode::function("baz", "a.py"));
        let f1_new = builder.get_node_index("a.py::foo").unwrap();
        builder.add_edge(f1_new, f3, CodeEdge::calls());

        let graph = builder.freeze();
        assert_eq!(graph.functions().len(), 3);
        let (f1_frozen, _) = graph.node_by_name("a.py::foo").unwrap();
        assert_eq!(graph.callees(f1_frozen).len(), 2);
    }

    #[test]
    fn test_primitive_accessors_empty_graph() {
        let builder = GraphBuilder::new();
        let graph = builder.freeze();
        let fake_idx = NodeIndex::new(0);
        assert!(graph.dominated_by(fake_idx).is_empty());
        assert!(graph.domination_frontier(fake_idx).is_empty());
        assert_eq!(graph.dominator_depth(fake_idx), 0);
        assert_eq!(graph.domination_count(fake_idx), 0);
        assert!(!graph.is_articulation_point(fake_idx));
        assert!(graph.articulation_points().is_empty());
        assert!(graph.bridges().is_empty());
        assert!(graph.separation_sizes(fake_idx).is_none());
        assert!(graph.call_cycles().is_empty());
        assert_eq!(graph.page_rank(fake_idx), 0.0);
        assert_eq!(graph.betweenness(fake_idx), 0.0);
        assert!(graph.immediate_dominator(fake_idx).is_none());
    }

    #[test]
    fn test_clone_into_builder() {
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("foo", "a.py"));
        let f2 = builder.add_node(CodeNode::function("bar", "a.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());

        let graph = builder.freeze();
        let builder2 = graph.clone_into_builder();

        // Original graph still accessible
        assert_eq!(graph.functions().len(), 2);

        // Clone has same data
        assert_eq!(builder2.node_count(), 2);
        assert_eq!(builder2.edge_count(), 1);
    }

    #[test]
    fn test_extra_props() {
        let mut builder = GraphBuilder::new();
        builder.add_node(CodeNode::function("foo", "a.py"));

        let si = builder.interner();
        let qn_key = si.intern("a.py::foo");
        let mut ep = ExtraProps::default();
        ep.author = Some(si.intern("alice"));
        builder.set_extra_props(qn_key, ep);

        let graph = builder.freeze();
        let props = graph.extra_props(qn_key).unwrap();
        assert_eq!(si.resolve(props.author.unwrap()), "alice");
    }

    #[test]
    fn test_inheritance_queries() {
        let mut builder = GraphBuilder::new();
        let child = builder.add_node(CodeNode::class("Child", "a.py"));
        let parent = builder.add_node(CodeNode::class("Parent", "a.py"));
        builder.add_edge(child, parent, CodeEdge::inherits());

        let graph = builder.freeze();

        // Look up by name since indices may remap
        let (new_child, _) = graph.node_by_name("a.py::Child").unwrap();
        let (new_parent, _) = graph.node_by_name("a.py::Parent").unwrap();

        assert_eq!(graph.parent_classes(new_child).len(), 1);
        assert_eq!(graph.parent_classes(new_child)[0], new_parent);

        assert_eq!(graph.child_classes(new_parent).len(), 1);
        assert_eq!(graph.child_classes(new_parent)[0], new_child);
    }

    #[test]
    fn test_bulk_edge_lists() {
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("foo", "a.py"));
        let f2 = builder.add_node(CodeNode::function("bar", "a.py"));
        let a = builder.add_node(CodeNode::file("a.py"));
        let b = builder.add_node(CodeNode::file("b.py"));

        builder.add_edge(f1, f2, CodeEdge::calls());
        builder.add_edge(a, b, CodeEdge::imports());

        let graph = builder.freeze();

        assert_eq!(graph.all_call_edges().len(), 1);
        assert_eq!(graph.all_import_edges().len(), 1);
        assert!(graph.all_inheritance_edges().is_empty());
    }
}
