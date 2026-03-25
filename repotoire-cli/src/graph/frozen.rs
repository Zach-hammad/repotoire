//! Frozen, immutable, indexed code graph.
//!
//! `CodeGraph` is the read-only graph produced by `GraphBuilder::freeze()`.
//! All queries are O(1) lookups into pre-built indexes — no locks, no scans.
//! Safe to share across rayon threads via `&CodeGraph`.

use petgraph::stable_graph::{NodeIndex, StableGraph};
use std::collections::{BTreeMap, HashMap};

use super::builder::GraphBuilder;
use super::indexes::GraphIndexes;
use super::interner::{global_interner, StrKey, StringInterner};
use super::store_models::{CodeEdge, CodeNode, EdgeKind, ExtraProps};

/// Immutable code graph with pre-built indexes.
///
/// All queries are O(1) lookups. No locks — all methods take `&self`.
/// Safe to share across rayon threads. Produced by `GraphBuilder::freeze()`.
pub struct CodeGraph {
    /// The underlying petgraph directed graph (immutable after freeze).
    graph: StableGraph<CodeNode, CodeEdge>,
    /// Node lookup by qualified name (interned StrKey → NodeIndex).
    node_index: HashMap<StrKey, NodeIndex>,
    /// Extra (cold) properties stored per qualified_name StrKey.
    extra_props: HashMap<StrKey, ExtraProps>,
    /// Pre-built indexes computed during freeze().
    indexes: GraphIndexes,
}

// SAFETY: CodeGraph is immutable after construction. All fields are Send + Sync.
// StableGraph, HashMap, and GraphIndexes are all Send + Sync when their
// element types are (CodeNode is Copy, NodeIndex is Copy, etc.).
unsafe impl Send for CodeGraph {}
unsafe impl Sync for CodeGraph {}

/// Empty slice constant for returning `&[]` from adjacency lookups.
const EMPTY_NODE_SLICE: &[NodeIndex] = &[];

impl CodeGraph {
    // ==================== Construction (crate-internal) ====================

    /// Create a CodeGraph from its constituent parts.
    /// Called by `GraphBuilder::freeze()`.
    pub(crate) fn from_parts(
        graph: StableGraph<CodeNode, CodeEdge>,
        node_index: HashMap<StrKey, NodeIndex>,
        extra_props: HashMap<StrKey, ExtraProps>,
        indexes: GraphIndexes,
    ) -> Self {
        Self {
            graph,
            node_index,
            extra_props,
            indexes,
        }
    }

    /// Decompose into constituent parts for conversion back to a builder.
    /// Called by `GraphBuilder::from_frozen()`.
    pub(crate) fn into_parts(
        self,
    ) -> (
        StableGraph<CodeNode, CodeEdge>,
        HashMap<StrKey, NodeIndex>,
        HashMap<StrKey, ExtraProps>,
    ) {
        (self.graph, self.node_index, self.extra_props)
    }

    // ==================== String Interner ====================

    /// Access the global string interner.
    pub fn interner(&self) -> &'static StringInterner {
        global_interner()
    }

    // ==================== Node Access (O(1)) ====================

    /// Get a node by its graph index.
    pub fn node(&self, idx: NodeIndex) -> Option<&CodeNode> {
        self.graph.node_weight(idx)
    }

    /// Look up a node by qualified name. Returns both index and reference.
    pub fn node_by_name(&self, qn: &str) -> Option<(NodeIndex, &CodeNode)> {
        let key = self.interner().intern(qn);
        let &idx = self.node_index.get(&key)?;
        let node = self.graph.node_weight(idx)?;
        Some((idx, node))
    }

    /// Look up a node by interned StrKey. Returns both index and reference.
    pub fn node_by_key(&self, key: StrKey) -> Option<(NodeIndex, &CodeNode)> {
        let &idx = self.node_index.get(&key)?;
        let node = self.graph.node_weight(idx)?;
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

    // ==================== Adjacency Queries (O(1)) ====================

    /// Functions that call this node (incoming Calls edges).
    pub fn callers(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.indexes
            .call_callers
            .get(&idx)
            .map(|v| v.as_slice())
            .unwrap_or(EMPTY_NODE_SLICE)
    }

    /// Functions this node calls (outgoing Calls edges).
    pub fn callees(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.indexes
            .call_callees
            .get(&idx)
            .map(|v| v.as_slice())
            .unwrap_or(EMPTY_NODE_SLICE)
    }

    /// Modules/files that import this node (incoming Imports edges).
    pub fn importers(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.indexes
            .import_sources
            .get(&idx)
            .map(|v| v.as_slice())
            .unwrap_or(EMPTY_NODE_SLICE)
    }

    /// Modules/files this node imports (outgoing Imports edges).
    pub fn importees(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.indexes
            .import_targets
            .get(&idx)
            .map(|v| v.as_slice())
            .unwrap_or(EMPTY_NODE_SLICE)
    }

    /// Parent classes (outgoing Inherits edges).
    pub fn parent_classes(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.indexes
            .inherit_parents
            .get(&idx)
            .map(|v| v.as_slice())
            .unwrap_or(EMPTY_NODE_SLICE)
    }

    /// Child classes (incoming Inherits edges).
    pub fn child_classes(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.indexes
            .inherit_children
            .get(&idx)
            .map(|v| v.as_slice())
            .unwrap_or(EMPTY_NODE_SLICE)
    }

    /// Entities contained by this node (outgoing Contains edges).
    pub fn contains_children(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.indexes
            .contains_children
            .get(&idx)
            .map(|v| v.as_slice())
            .unwrap_or(EMPTY_NODE_SLICE)
    }

    /// Parent container of this node (incoming Contains edges).
    pub fn contains_parent(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.indexes
            .contains_parent
            .get(&idx)
            .map(|v| v.as_slice())
            .unwrap_or(EMPTY_NODE_SLICE)
    }

    /// Entities this node uses (outgoing Uses edges).
    pub fn uses_targets(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.indexes
            .uses_targets
            .get(&idx)
            .map(|v| v.as_slice())
            .unwrap_or(EMPTY_NODE_SLICE)
    }

    /// Entities that use this node (incoming Uses edges).
    pub fn uses_sources(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.indexes
            .uses_sources
            .get(&idx)
            .map(|v| v.as_slice())
            .unwrap_or(EMPTY_NODE_SLICE)
    }

    /// Commits that modified this entity (outgoing ModifiedIn edges).
    pub fn modified_in(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.indexes
            .modified_in
            .get(&idx)
            .map(|v| v.as_slice())
            .unwrap_or(EMPTY_NODE_SLICE)
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
        self.indexes.primitives.dominated.get(&idx).map(|v| v.as_slice()).unwrap_or(EMPTY_NODE_SLICE)
    }

    pub fn domination_frontier(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.indexes.primitives.frontier.get(&idx).map(|v| v.as_slice()).unwrap_or(EMPTY_NODE_SLICE)
    }

    pub fn dominator_depth(&self, idx: NodeIndex) -> usize {
        self.indexes.primitives.dom_depth.get(&idx).copied().unwrap_or(0)
    }

    pub fn domination_count(&self, idx: NodeIndex) -> usize {
        self.dominated_by(idx).len()
    }

    pub fn is_articulation_point(&self, idx: NodeIndex) -> bool {
        self.indexes.primitives.articulation_point_set.contains(&idx)
    }

    pub fn articulation_points(&self) -> &[NodeIndex] {
        &self.indexes.primitives.articulation_points
    }

    pub fn bridges(&self) -> &[(NodeIndex, NodeIndex)] {
        &self.indexes.primitives.bridges
    }

    pub fn separation_sizes(&self, idx: NodeIndex) -> Option<&[usize]> {
        self.indexes.primitives.component_sizes.get(&idx).map(|v| v.as_slice())
    }

    pub fn call_cycles(&self) -> &[Vec<NodeIndex>] {
        &self.indexes.primitives.call_cycles
    }

    pub fn page_rank(&self, idx: NodeIndex) -> f64 {
        self.indexes.primitives.page_rank.get(&idx).copied().unwrap_or(0.0)
    }

    pub fn betweenness(&self, idx: NodeIndex) -> f64 {
        self.indexes.primitives.betweenness.get(&idx).copied().unwrap_or(0.0)
    }

    pub fn call_depth(&self, idx: NodeIndex) -> usize {
        self.indexes.primitives.call_depth.get(&idx).copied().unwrap_or(0)
    }

    pub fn weighted_page_rank(&self, idx: NodeIndex) -> f64 {
        self.indexes.primitives.weighted_page_rank.get(&idx).copied().unwrap_or(0.0)
    }

    pub fn weighted_betweenness(&self, idx: NodeIndex) -> f64 {
        self.indexes.primitives.weighted_betweenness.get(&idx).copied().unwrap_or(0.0)
    }

    pub fn community(&self, idx: NodeIndex) -> Option<usize> {
        self.indexes.primitives.community.get(&idx).copied()
    }

    pub fn graph_modularity(&self) -> f64 {
        self.indexes.primitives.modularity
    }

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
        stats.insert("total_nodes".to_string(), self.graph.node_count() as i64);
        stats.insert("total_edges".to_string(), self.graph.edge_count() as i64);
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

    // ==================== Raw Graph Access ====================

    /// Direct access to the underlying petgraph for custom traversals
    /// (BFS, DFS, Dijkstra, etc.) that don't fit the indexed query API.
    ///
    /// Only available on the concrete type, not through trait objects.
    pub fn raw_graph(&self) -> &StableGraph<CodeNode, CodeEdge> {
        &self.graph
    }

    /// Access the node_index map (qualified_name → NodeIndex).
    pub fn node_index_map(&self) -> &HashMap<StrKey, NodeIndex> {
        &self.node_index
    }

    // ==================== Node/Edge Counts ====================

    /// Total node count.
    pub fn node_count(&self) -> usize {
        self.graph.node_count()
    }

    /// Total edge count.
    pub fn edge_count(&self) -> usize {
        self.graph.edge_count()
    }

    // ==================== Lifecycle ====================

    /// Convert back to a mutable builder for incremental patching.
    ///
    /// Moves the StableGraph without copying nodes/edges.
    /// Rebuilds edge_set from graph edges — O(E).
    /// Indexes are dropped (rebuilt on re-freeze).
    pub fn into_builder(self) -> GraphBuilder {
        GraphBuilder::from_frozen(self)
    }

    /// Clone the graph into a new builder (leaves this CodeGraph intact).
    ///
    /// O(N + E) — clones all nodes and edges.
    pub fn clone_into_builder(&self) -> GraphBuilder {
        use petgraph::visit::{EdgeRef, IntoEdgeReferences};
        let edge_set: std::collections::HashSet<(NodeIndex, NodeIndex, EdgeKind)> = self
            .graph
            .edge_references()
            .map(|e| (e.source(), e.target(), e.weight().kind))
            .collect();

        // We need to reconstruct a GraphBuilder with cloned data
        // Since GraphBuilder::new() creates empty fields, we build manually
        // via from_frozen on a clone.
        let cloned_graph = self.graph.clone();
        let cloned_node_index = self.node_index.clone();
        let cloned_extra_props = self.extra_props.clone();

        // Build a temporary CodeGraph just to pass to from_frozen
        // (avoids duplicating the edge_set rebuild logic)
        let _ = edge_set; // not needed, from_frozen rebuilds it
        let temp = CodeGraph {
            graph: cloned_graph,
            node_index: cloned_node_index,
            extra_props: cloned_extra_props,
            indexes: GraphIndexes::default(),
        };
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

        // By index
        let node = graph.node(idx).unwrap();
        assert_eq!(node.kind, NodeKind::Function);

        // By name
        let (found_idx, found_node) = graph.node_by_name("a.py::foo").unwrap();
        assert_eq!(found_idx, idx);
        assert_eq!(found_node.kind, NodeKind::Function);

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

        assert_eq!(graph.callees(f1).len(), 2);
        assert_eq!(graph.callers(f2).len(), 1);
        assert_eq!(graph.callers(f3).len(), 1);
        assert!(graph.callers(f1).is_empty());
        assert_eq!(graph.call_fan_out(f1), 2);
        assert_eq!(graph.call_fan_in(f2), 1);
    }

    #[test]
    fn test_file_scoped_queries() {
        let mut builder = GraphBuilder::new();
        builder.add_node(
            CodeNode::function("foo", "a.py").with_lines(1, 10),
        );
        builder.add_node(
            CodeNode::function("bar", "a.py").with_lines(12, 20),
        );
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
        let f1 = builder.add_node(
            CodeNode::function("foo", "a.py").with_lines(1, 10),
        );
        let f2 = builder.add_node(
            CodeNode::function("bar", "a.py").with_lines(12, 20),
        );

        let graph = builder.freeze();

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
        builder.add_edge(f2, f3, CodeEdge::calls());

        let graph = builder.freeze();
        assert_eq!(graph.functions().len(), 3);
        assert_eq!(graph.callees(f2).len(), 1);
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

        assert_eq!(graph.parent_classes(child).len(), 1);
        assert_eq!(graph.parent_classes(child)[0], parent);

        assert_eq!(graph.child_classes(parent).len(), 1);
        assert_eq!(graph.child_classes(parent)[0], child);
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
