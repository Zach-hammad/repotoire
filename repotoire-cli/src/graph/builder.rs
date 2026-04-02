//! Mutable graph builder for the build phase (parse -> graph build -> git enrich).
//!
//! `GraphBuilder` owns `Vec<Option<CodeNode>>` + edge Vec and provides mutation
//! methods (`add_node`, `add_edge`, `update_node_property`, etc.) without any locking.
//! All methods take `&mut self` or `&self` -- no `RwLock`, no `DashMap`, no `Mutex`.
//!
//! After building is complete, call `freeze()` to consume the builder and produce
//! an immutable `CodeGraph` with pre-built indexes and CSR storage.

use crate::graph::node_index::NodeIndex;
use std::collections::{HashMap, HashSet};
use std::sync::OnceLock;

use super::frozen::CodeGraph;
use super::interner::{global_interner, StrKey, StringInterner};
use super::store_models::{CodeEdge, CodeNode, EdgeKind, ExtraProps, NodeKind};

/// Apply a single metric or flag property to a node.
/// Handles numeric metrics (complexity, paramCount, etc.) and boolean flags (is_async, etc.).
/// Author and last_modified are handled separately via pre-interning.
fn apply_node_metric(node: &mut CodeNode, key: &str, value: &serde_json::Value) {
    match key {
        "complexity" => node.complexity = value.as_i64().unwrap_or(0) as u16,
        "paramCount" => node.param_count = value.as_i64().unwrap_or(0) as u8,
        "methodCount" => node.method_count = value.as_i64().unwrap_or(0) as u16,
        "maxNesting" | "nesting_depth" => node.max_nesting = value.as_i64().unwrap_or(0) as u8,
        "returnCount" => node.return_count = value.as_i64().unwrap_or(0) as u8,
        "commit_count" => node.commit_count = value.as_i64().unwrap_or(0) as u16,
        "is_async" => apply_flag_if_true(node, value, super::store_models::FLAG_IS_ASYNC),
        "is_exported" => apply_flag_if_true(node, value, super::store_models::FLAG_IS_EXPORTED),
        "is_public" => apply_flag_if_true(node, value, super::store_models::FLAG_IS_PUBLIC),
        "is_method" => apply_flag_if_true(node, value, super::store_models::FLAG_IS_METHOD),
        "address_taken" => apply_flag_if_true(node, value, super::store_models::FLAG_ADDRESS_TAKEN),
        "has_decorators" => {
            apply_flag_if_true(node, value, super::store_models::FLAG_HAS_DECORATORS)
        }
        // author and last_modified handled via pre-interning in update_node_properties
        _ => {}
    }
}

/// Set a flag on a node if the JSON value is truthy.
fn apply_flag_if_true(node: &mut CodeNode, value: &serde_json::Value, flag: u8) {
    if value.as_bool().unwrap_or(false) {
        node.set_flag(flag);
    }
}

/// Apply extra string properties (author, last_modified) to the extra props map.
fn apply_extra_prop(
    extra_props: &mut HashMap<StrKey, ExtraProps>,
    intern_qn: StrKey,
    key: &str,
    value: &serde_json::Value,
) {
    match key {
        "author" => {
            if let Some(s) = value.as_str() {
                let ep = extra_props.entry(intern_qn).or_default();
                ep.author = Some(global_interner().intern(s));
            }
        }
        "last_modified" => {
            if let Some(s) = value.as_str() {
                let ep = extra_props.entry(intern_qn).or_default();
                ep.last_modified = Some(global_interner().intern(s));
            }
        }
        _ => {}
    }
}

/// Mutable graph builder. Used during parse, graph build, and git enrichment.
/// No locks -- all methods take `&mut self` or `&self`.
pub struct GraphBuilder {
    /// Nodes stored as Vec<Option<CodeNode>>. None = tombstone (removed).
    nodes: Vec<Option<CodeNode>>,
    /// Node lookup by qualified name (interned StrKey -> NodeIndex).
    node_index: HashMap<StrKey, NodeIndex>,
    /// Edge list: (source, target, CodeEdge).
    edges: Vec<(NodeIndex, NodeIndex, CodeEdge)>,
    /// Edge deduplication set: (source, target, kind).
    edge_set: HashSet<(NodeIndex, NodeIndex, EdgeKind)>,
    /// Extra (cold) properties stored per qualified_name StrKey.
    extra_props: HashMap<StrKey, ExtraProps>,
    /// Lazily-built frozen snapshot for `GraphQuery` trait impl.
    /// Built on first trait method call; NOT invalidated by mutations.
    /// Intended for test code that builds a graph then queries it.
    query_snapshot: OnceLock<CodeGraph>,
}

impl GraphBuilder {
    // ==================== Construction ====================

    /// Create a new empty builder.
    pub fn new() -> Self {
        Self {
            nodes: Vec::new(),
            node_index: HashMap::new(),
            edges: Vec::new(),
            edge_set: HashSet::new(),
            extra_props: HashMap::new(),
            query_snapshot: OnceLock::new(),
        }
    }

    /// Get or build the frozen query snapshot for `GraphQuery` trait access.
    ///
    /// Built lazily on first call by cloning the current graph state.
    /// NOT invalidated by subsequent mutations -- intended for test code.
    pub(crate) fn snapshot(&self) -> &CodeGraph {
        self.query_snapshot.get_or_init(|| {
            CodeGraph::build(
                self.nodes.clone(),
                self.node_index.clone(),
                self.edges.clone(),
                self.extra_props.clone(),
                None,
            )
        })
    }

    /// Access the global string interner.
    pub fn interner(&self) -> &'static StringInterner {
        global_interner()
    }

    // ==================== Node Operations ====================

    /// Add a node to the graph.
    ///
    /// If a node with the same qualified_name already exists, it is updated in place.
    /// Returns the NodeIndex (existing or newly created).
    pub fn add_node(&mut self, node: CodeNode) -> NodeIndex {
        let qn = node.qualified_name;

        // Check if node already exists
        if let Some(&idx) = self.node_index.get(&qn) {
            self.nodes[idx.index()] = Some(node);
            return idx;
        }

        let idx = NodeIndex::new(self.nodes.len() as u32);
        self.nodes.push(Some(node));
        self.node_index.insert(qn, idx);
        idx
    }

    /// Add multiple nodes at once (batch operation).
    ///
    /// Nodes with duplicate qualified_names are updated in place (last write wins
    /// for nodes within this batch; existing nodes in the graph are overwritten).
    pub fn add_nodes_batch(&mut self, nodes: Vec<CodeNode>) -> Vec<NodeIndex> {
        let mut indices = Vec::with_capacity(nodes.len());

        for node in nodes {
            let qn = node.qualified_name;

            if let Some(&idx) = self.node_index.get(&qn) {
                self.nodes[idx.index()] = Some(node);
                indices.push(idx);
            } else {
                let idx = NodeIndex::new(self.nodes.len() as u32);
                self.nodes.push(Some(node));
                self.node_index.insert(qn, idx);
                indices.push(idx);
            }
        }

        indices
    }

    /// Add nodes and create Contains edges (file -> function/class) in one operation.
    ///
    /// This avoids buffering edge tuples for Contains edges that are always
    /// intra-file and always resolved.
    pub fn add_nodes_batch_with_contains(
        &mut self,
        nodes: Vec<CodeNode>,
        file_qn: &str,
    ) -> Vec<NodeIndex> {
        let mut indices = Vec::with_capacity(nodes.len());

        let file_qn_key = self.interner().intern(file_qn);
        let file_idx = self.node_index.get(&file_qn_key).copied();

        for node in nodes {
            let qn = node.qualified_name;
            let needs_contains = node.kind == NodeKind::Function || node.kind == NodeKind::Class;

            if let Some(&idx) = self.node_index.get(&qn) {
                self.nodes[idx.index()] = Some(node);
                indices.push(idx);
            } else {
                let idx = NodeIndex::new(self.nodes.len() as u32);
                self.nodes.push(Some(node));
                self.node_index.insert(qn, idx);

                // Add Contains edge: file -> function/class
                if needs_contains {
                    if let Some(f_idx) = file_idx {
                        if self.edge_set.insert((f_idx, idx, EdgeKind::Contains)) {
                            self.edges.push((f_idx, idx, CodeEdge::contains()));
                        }
                    }
                }

                indices.push(idx);
            }
        }

        indices
    }

    /// Get node index by qualified name.
    pub fn get_node_index(&self, qn: &str) -> Option<NodeIndex> {
        let key = self.interner().intern(qn);
        self.node_index.get(&key).copied()
    }

    /// Get node by qualified name. Returns a reference to the CodeNode.
    pub fn get_node(&self, qn: &str) -> Option<&CodeNode> {
        let key = self.interner().intern(qn);
        let &idx = self.node_index.get(&key)?;
        self.nodes[idx.index()].as_ref()
    }

    /// Get a mutable reference to a node by qualified name.
    pub fn get_node_mut(&mut self, qn: &str) -> Option<&mut CodeNode> {
        let key = self.interner().intern(qn);
        let &idx = self.node_index.get(&key)?;
        self.nodes[idx.index()].as_mut()
    }

    /// Update a single property on a node.
    pub fn update_node_property(
        &mut self,
        qn: &str,
        key: &str,
        value: impl Into<serde_json::Value>,
    ) -> bool {
        let intern_qn = self.interner().intern(qn);
        let idx = match self.node_index.get(&intern_qn).copied() {
            Some(idx) => idx,
            None => return false,
        };
        let val: serde_json::Value = value.into();
        if let Some(node) = self.nodes[idx.index()].as_mut() {
            apply_node_metric(node, key, &val);
            apply_extra_prop(&mut self.extra_props, intern_qn, key, &val);
            return true;
        }
        false
    }

    /// Update multiple properties on a node.
    pub fn update_node_properties(
        &mut self,
        qn: &str,
        props: &[(&str, serde_json::Value)],
    ) -> bool {
        let intern_qn = self.interner().intern(qn);
        let idx = match self.node_index.get(&intern_qn).copied() {
            Some(idx) => idx,
            None => return false,
        };
        // Pre-intern string values before taking mutable borrow on self.nodes
        let si = global_interner();
        let mut extras = ExtraProps::default();
        let mut has_extras = false;
        for (key, value) in props.iter() {
            match *key {
                "author" => {
                    if let Some(s) = value.as_str() {
                        extras.author = Some(si.intern(s));
                        has_extras = true;
                    }
                }
                "last_modified" => {
                    if let Some(s) = value.as_str() {
                        extras.last_modified = Some(si.intern(s));
                        has_extras = true;
                    }
                }
                _ => {}
            }
        }

        if let Some(node) = self.nodes[idx.index()].as_mut() {
            for (key, value) in props {
                apply_node_metric(node, key, value);
            }
            if has_extras {
                let ep = self.extra_props.entry(intern_qn).or_default();
                if let Some(a) = extras.author {
                    ep.author = Some(a);
                }
                if let Some(lm) = extras.last_modified {
                    ep.last_modified = Some(lm);
                }
            }
            return true;
        }
        false
    }

    /// Set extra properties (cold string data) for a node.
    pub fn set_extra_props(&mut self, qn_key: StrKey, props: ExtraProps) {
        self.extra_props.insert(qn_key, props);
    }

    /// Get extra properties for a node.
    pub fn get_extra_props(&self, qn_key: StrKey) -> Option<&ExtraProps> {
        self.extra_props.get(&qn_key)
    }

    // ==================== Edge Operations ====================

    /// Add an edge between nodes by index (skips if duplicate edge exists).
    pub fn add_edge(&mut self, from: NodeIndex, to: NodeIndex, edge: CodeEdge) {
        if !self.edge_set.insert((from, to, edge.kind)) {
            return; // duplicate
        }
        self.edges.push((from, to, edge));
    }

    /// Add edge by qualified names (returns false if either node doesn't exist).
    pub fn add_edge_by_name(&mut self, from_qn: &str, to_qn: &str, edge: CodeEdge) -> bool {
        let from_key = self.interner().intern(from_qn);
        let to_key = self.interner().intern(to_qn);
        let from = self.node_index.get(&from_key).copied();
        let to = self.node_index.get(&to_key).copied();

        if let (Some(from), Some(to)) = (from, to) {
            self.add_edge(from, to, edge);
            true
        } else {
            false
        }
    }

    /// Add multiple edges at once (batch operation, deduplicated).
    pub fn add_edges_batch(&mut self, edges: Vec<(String, String, CodeEdge)>) -> usize {
        let si = self.interner();
        // Resolve all node indices
        let resolved: Vec<_> = edges
            .into_iter()
            .filter_map(|(from_qn, to_qn, edge)| {
                let from_key = si.intern(&from_qn);
                let to_key = si.intern(&to_qn);
                let from = self.node_index.get(&from_key).copied()?;
                let to = self.node_index.get(&to_key).copied()?;
                Some((from, to, edge))
            })
            .collect();

        // Dedup and insert
        let mut added = 0;
        for (from, to, edge) in resolved {
            if self.edge_set.insert((from, to, edge.kind)) {
                self.edges.push((from, to, edge));
                added += 1;
            }
        }
        added
    }

    // ==================== Read Methods (needed during build phase) ====================

    /// Get all function nodes (O(N) scan -- acceptable during build phase).
    /// Sorted by qualified_name for determinism.
    pub fn get_functions(&self) -> Vec<CodeNode> {
        let si = self.interner();
        let mut nodes: Vec<CodeNode> = self
            .nodes
            .iter()
            .filter_map(|n| n.as_ref())
            .filter(|n| n.kind == NodeKind::Function)
            .copied()
            .collect();
        nodes.sort_by_cached_key(|n| si.resolve(n.qualified_name).to_owned());
        nodes
    }

    /// Get all class nodes (O(N) scan).
    /// Sorted by qualified_name for determinism.
    pub fn get_classes(&self) -> Vec<CodeNode> {
        let si = self.interner();
        let mut nodes: Vec<CodeNode> = self
            .nodes
            .iter()
            .filter_map(|n| n.as_ref())
            .filter(|n| n.kind == NodeKind::Class)
            .copied()
            .collect();
        nodes.sort_by_cached_key(|n| si.resolve(n.qualified_name).to_owned());
        nodes
    }

    /// Get all file nodes (O(N) scan).
    /// Sorted by qualified_name for determinism.
    pub fn get_files(&self) -> Vec<CodeNode> {
        let si = self.interner();
        let mut nodes: Vec<CodeNode> = self
            .nodes
            .iter()
            .filter_map(|n| n.as_ref())
            .filter(|n| n.kind == NodeKind::File)
            .copied()
            .collect();
        nodes.sort_by_cached_key(|n| si.resolve(n.qualified_name).to_owned());
        nodes
    }

    /// Node count (excluding tombstones).
    pub fn node_count(&self) -> usize {
        self.nodes.iter().filter(|n| n.is_some()).count()
    }

    /// Edge count.
    pub fn edge_count(&self) -> usize {
        self.edges.len()
    }

    // ==================== Edge Query Methods ====================

    /// Get all edges of a specific kind as (source_qn, target_qn) pairs.
    /// Sorted by qualified names for determinism.
    pub fn get_edges_by_kind(&self, kind: EdgeKind) -> Vec<(StrKey, StrKey)> {
        let si = self.interner();
        let mut edges: Vec<(StrKey, StrKey)> = self
            .edges
            .iter()
            .filter(|(_, _, e)| e.kind == kind)
            .filter_map(|&(src, tgt, _)| {
                let s = self.nodes[src.index()].as_ref()?;
                let d = self.nodes[tgt.index()].as_ref()?;
                Some((s.qualified_name, d.qualified_name))
            })
            .collect();
        edges.sort_unstable_by(|a, b| {
            si.resolve(a.0)
                .cmp(si.resolve(b.0))
                .then_with(|| si.resolve(a.1).cmp(si.resolve(b.1)))
        });
        edges
    }

    /// Get all call edges (function -> function).
    pub fn get_calls(&self) -> Vec<(StrKey, StrKey)> {
        self.get_edges_by_kind(EdgeKind::Calls)
    }

    /// Get all import edges (file -> file).
    pub fn get_imports(&self) -> Vec<(StrKey, StrKey)> {
        self.get_edges_by_kind(EdgeKind::Imports)
    }

    // ==================== Capacity ====================

    /// Reserve capacity for nodes and edges (optimization hint).
    ///
    /// Pre-allocates internal storage to avoid reallocations during bulk insert.
    pub fn reserve_capacity(&mut self, nodes: usize, edges: usize) {
        self.nodes.reserve(nodes);
        self.node_index.reserve(nodes);
        self.edges.reserve(edges);
        self.edge_set.reserve(edges);
    }

    // ==================== Delta Patching ====================

    /// Remove all nodes and edges belonging to a set of files.
    ///
    /// Returns the list of removed qualified name StrKeys.
    pub fn remove_file_entities(&mut self, files: &[std::path::PathBuf]) -> Vec<StrKey> {
        let si = self.interner();
        let mut removed_qns = Vec::new();

        // Collect all node indices to remove
        let mut removed_indices: HashSet<NodeIndex> = HashSet::new();

        for file in files {
            let file_str = file.to_string_lossy();
            let file_key = si.intern(file_str.as_ref());

            for (i, slot) in self.nodes.iter().enumerate() {
                if let Some(node) = slot {
                    if node.file_path == file_key {
                        let idx = NodeIndex::new(i as u32);
                        removed_indices.insert(idx);
                    }
                }
            }
        }

        // Remove nodes (tombstone) and clean up node_index
        for &idx in &removed_indices {
            if let Some(node) = &self.nodes[idx.index()] {
                let qn = node.qualified_name;
                self.node_index.remove(&qn);
                self.extra_props.remove(&qn);
                removed_qns.push(qn);
            }
            self.nodes[idx.index()] = None;
        }

        // Remove edges touching removed nodes
        self.edges.retain(|&(src, tgt, _)| {
            !removed_indices.contains(&src) && !removed_indices.contains(&tgt)
        });
        self.edge_set.retain(|&(src, tgt, _)| {
            !removed_indices.contains(&src) && !removed_indices.contains(&tgt)
        });

        removed_qns
    }

    // ==================== Lifecycle ====================

    /// Consume the builder, build all indexes, produce an immutable `CodeGraph`.
    ///
    /// This is the transition from the mutable build phase to the immutable
    /// query phase. All indexes are built in one pass during this call.
    pub fn freeze(self) -> CodeGraph {
        CodeGraph::build(self.nodes, self.node_index, self.edges, self.extra_props, None)
    }

    /// Consume the builder, build all indexes with co-change data, produce an immutable `CodeGraph`.
    ///
    /// Same as `freeze()` but also passes co-change data to `GraphPrimitives`
    /// so that weighted overlay, hidden coupling, etc. are computed.
    pub fn freeze_with_co_change(
        self,
        co_change: &crate::git::co_change::CoChangeMatrix,
    ) -> CodeGraph {
        CodeGraph::build(
            self.nodes,
            self.node_index,
            self.edges,
            self.extra_props,
            Some(co_change),
        )
    }

    /// Create a builder from a frozen CodeGraph (takes ownership).
    ///
    /// Reconstructs nodes (with tombstones) and edges from the frozen graph.
    /// Indexes are dropped (rebuilt on re-freeze).
    pub fn from_frozen(frozen: CodeGraph) -> Self {
        let (nodes, node_index, edges, extra_props) = frozen.into_parts();

        // Rebuild edge_set from edges
        let edge_set: HashSet<(NodeIndex, NodeIndex, EdgeKind)> = edges
            .iter()
            .map(|&(src, tgt, ref e)| (src, tgt, e.kind))
            .collect();

        // Convert compact nodes back to Option<CodeNode> (no tombstones from frozen)
        let opt_nodes: Vec<Option<CodeNode>> = nodes.into_iter().map(Some).collect();

        Self {
            nodes: opt_nodes,
            node_index,
            edges,
            edge_set,
            extra_props,
            query_snapshot: OnceLock::new(),
        }
    }
}

impl Default for GraphBuilder {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_builder_new_empty() {
        let builder = GraphBuilder::new();
        assert_eq!(builder.node_count(), 0);
        assert_eq!(builder.edge_count(), 0);
    }

    #[test]
    fn test_add_node_dedup() {
        let mut builder = GraphBuilder::new();
        let n1 = CodeNode::function("foo", "a.py").with_lines(1, 10);
        let n2 = CodeNode::function("foo", "a.py").with_lines(1, 20); // same QN, different line_end

        let idx1 = builder.add_node(n1);
        let idx2 = builder.add_node(n2);

        // Same NodeIndex (deduped)
        assert_eq!(idx1, idx2);
        assert_eq!(builder.node_count(), 1);

        // Updated to latest values
        let node = builder.get_node("a.py::foo").unwrap();
        assert_eq!(node.line_end, 20);
    }

    #[test]
    fn test_add_nodes_batch() {
        let mut builder = GraphBuilder::new();
        let nodes = vec![
            CodeNode::function("foo", "a.py"),
            CodeNode::function("bar", "a.py"),
            CodeNode::class("MyClass", "a.py"),
        ];

        let indices = builder.add_nodes_batch(nodes);
        assert_eq!(indices.len(), 3);
        assert_eq!(builder.node_count(), 3);
    }

    #[test]
    fn test_add_nodes_batch_with_contains() {
        let mut builder = GraphBuilder::new();

        // First add the file node
        builder.add_node(CodeNode::file("a.py"));

        let nodes = vec![
            CodeNode::function("foo", "a.py"),
            CodeNode::class("MyClass", "a.py"),
        ];

        let indices = builder.add_nodes_batch_with_contains(nodes, "a.py");
        assert_eq!(indices.len(), 2);
        // File + 2 entities
        assert_eq!(builder.node_count(), 3);
        // 2 Contains edges (file -> foo, file -> MyClass)
        assert_eq!(builder.edge_count(), 2);
    }

    #[test]
    fn test_add_edge_dedup() {
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("foo", "a.py"));
        let f2 = builder.add_node(CodeNode::function("bar", "a.py"));

        builder.add_edge(f1, f2, CodeEdge::calls());
        builder.add_edge(f1, f2, CodeEdge::calls()); // duplicate

        assert_eq!(builder.edge_count(), 1);
    }

    #[test]
    fn test_add_edge_by_name() {
        let mut builder = GraphBuilder::new();
        builder.add_node(CodeNode::function("foo", "a.py"));
        builder.add_node(CodeNode::function("bar", "a.py"));

        assert!(builder.add_edge_by_name("a.py::foo", "a.py::bar", CodeEdge::calls()));
        assert!(!builder.add_edge_by_name("a.py::foo", "nonexistent", CodeEdge::calls()));

        assert_eq!(builder.edge_count(), 1);
    }

    #[test]
    fn test_add_edges_batch() {
        let mut builder = GraphBuilder::new();
        builder.add_node(CodeNode::function("foo", "a.py"));
        builder.add_node(CodeNode::function("bar", "a.py"));
        builder.add_node(CodeNode::function("baz", "a.py"));

        let edges = vec![
            (
                "a.py::foo".to_string(),
                "a.py::bar".to_string(),
                CodeEdge::calls(),
            ),
            (
                "a.py::foo".to_string(),
                "a.py::baz".to_string(),
                CodeEdge::calls(),
            ),
            (
                "a.py::foo".to_string(),
                "nonexistent".to_string(),
                CodeEdge::calls(),
            ), // should be filtered
        ];

        let added = builder.add_edges_batch(edges);
        assert_eq!(added, 2);
        assert_eq!(builder.edge_count(), 2);
    }

    #[test]
    fn test_update_node_property() {
        let mut builder = GraphBuilder::new();
        builder.add_node(CodeNode::function("foo", "a.py"));

        assert!(builder.update_node_property("a.py::foo", "complexity", 15));
        assert!(!builder.update_node_property("nonexistent", "complexity", 5));

        let node = builder.get_node("a.py::foo").unwrap();
        assert_eq!(node.complexity, 15);
    }

    #[test]
    fn test_freeze_and_query() {
        let mut builder = GraphBuilder::new();
        builder.add_node(CodeNode::file("a.py"));
        let f1 = builder.add_node(CodeNode::function("foo", "a.py"));
        let f2 = builder.add_node(CodeNode::function("bar", "a.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());

        let graph = builder.freeze();

        // Kind indexes
        assert_eq!(graph.functions().len(), 2);
        assert_eq!(graph.files().len(), 1);

        // Adjacency -- note: NodeIndex values may change after compaction
        let (new_f1, _) = graph.node_by_name("a.py::foo").unwrap();
        let (new_f2, _) = graph.node_by_name("a.py::bar").unwrap();
        assert_eq!(graph.callees(new_f1).len(), 1);
        assert_eq!(graph.callers(new_f2).len(), 1);
        assert!(graph.callers(new_f1).is_empty());
    }

    #[test]
    fn test_freeze_roundtrip() {
        let mut builder = GraphBuilder::new();
        let _f1 = builder.add_node(CodeNode::function("foo", "a.py"));
        let _f2 = builder.add_node(CodeNode::function("bar", "a.py"));
        builder.add_edge(_f1, _f2, CodeEdge::calls());

        // Freeze
        let graph = builder.freeze();
        assert_eq!(graph.functions().len(), 2);

        // Convert back to builder
        let mut builder2 = graph.into_builder();
        assert_eq!(builder2.node_count(), 2);
        assert_eq!(builder2.edge_count(), 1);

        // Add more nodes
        let f3 = builder2.add_node(CodeNode::function("baz", "a.py"));
        // Get the current idx of foo from the builder
        let f1_new = builder2.get_node_index("a.py::foo").unwrap();
        builder2.add_edge(f1_new, f3, CodeEdge::calls());

        // Re-freeze
        let graph2 = builder2.freeze();
        assert_eq!(graph2.functions().len(), 3);
        let (f1_frozen, _) = graph2.node_by_name("a.py::foo").unwrap();
        assert_eq!(graph2.callees(f1_frozen).len(), 2);
    }

    #[test]
    fn test_remove_file_entities() {
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("foo", "a.py"));
        let f2 = builder.add_node(CodeNode::function("bar", "b.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());

        let removed = builder.remove_file_entities(&[std::path::PathBuf::from("a.py")]);
        assert_eq!(removed.len(), 1);
        assert_eq!(builder.node_count(), 1); // only bar remains
        assert_eq!(builder.edge_count(), 0); // edge was removed with foo
    }

    #[test]
    fn test_get_functions_sorted() {
        let mut builder = GraphBuilder::new();
        builder.add_node(CodeNode::function("zebra", "a.py"));
        builder.add_node(CodeNode::function("alpha", "a.py"));

        let funcs = builder.get_functions();
        let si = builder.interner();
        assert_eq!(si.resolve(funcs[0].qualified_name), "a.py::alpha");
        assert_eq!(si.resolve(funcs[1].qualified_name), "a.py::zebra");
    }
}
