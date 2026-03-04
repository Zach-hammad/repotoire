//! Pure Rust graph storage using petgraph + redb
//!
//! Replaces Kuzu for better cross-platform compatibility.
//! No C++ dependencies, builds everywhere.

use anyhow::{Context, Result};
use dashmap::DashMap;
use petgraph::algo::tarjan_scc;
use petgraph::graph::{DiGraph, NodeIndex};
use petgraph::visit::EdgeRef;
use petgraph::Direction;
use std::collections::{HashMap, HashSet};
use std::path::Path;
use std::sync::RwLock;

pub use super::store_models::{CodeEdge, CodeNode, EdgeKind, NodeKind};

/// Pure Rust graph store - replaces Kuzu
pub struct GraphStore {
    /// In-memory graph
    graph: RwLock<DiGraph<CodeNode, CodeEdge>>,
    /// Node lookup by qualified name — DashMap for lock-free concurrent reads
    node_index: DashMap<String, NodeIndex>,
    /// Spatial index: file_path → [(line_start, line_end, NodeIndex)] for O(1) function lookup.
    /// Populated during add_node/add_nodes_batch for Function nodes.
    function_spatial_index: DashMap<String, Vec<(u32, u32, NodeIndex)>>,
    /// File-scoped function index: file_path → [NodeIndex] for O(1) get_functions_in_file().
    file_functions_index: DashMap<String, Vec<NodeIndex>>,
    /// File-scoped class index: file_path → [NodeIndex] for O(1) get_classes_in_file().
    file_classes_index: DashMap<String, Vec<NodeIndex>>,
    /// Cached graph metrics from detectors, reusable by scoring phase.
    /// Key format: "metric_name:entity_qn" (e.g., "degree_centrality:module.Class")
    metrics_cache: DashMap<String, f64>,
    /// String interner for memory-efficient qualified name storage
    interner: super::interner::StringInterner,
    /// Persistence layer (optional) — uses redb (ACID, well-maintained)
    db: Option<redb::Database>,
    /// Database path for lazy loading
    #[allow(dead_code)]
    db_path: Option<std::path::PathBuf>,
    /// Lazy mode - query db directly instead of loading all into memory
    #[allow(dead_code)] // Config field for future lazy loading support
    lazy_mode: bool,
}

// redb table definitions
const NODES_TABLE: redb::TableDefinition<&str, &[u8]> = redb::TableDefinition::new("nodes");
const EDGES_TABLE: redb::TableDefinition<&str, &[u8]> = redb::TableDefinition::new("edges");

impl GraphStore {
    /// Create or open a graph store at the given path
    pub fn new(db_path: &Path) -> Result<Self> {
        std::fs::create_dir_all(db_path)?;

        // redb uses a single file, not a directory
        let db_file = db_path.join("graph.redb");
        let db = redb::Database::create(&db_file).context("Failed to open redb database")?;

        let store = Self {
            graph: RwLock::new(DiGraph::new()),
            node_index: DashMap::new(),
            function_spatial_index: DashMap::new(),
            file_functions_index: DashMap::new(),
            file_classes_index: DashMap::new(),
            metrics_cache: DashMap::new(),
            interner: super::interner::StringInterner::new(),
            db: Some(db),
            db_path: Some(db_path.to_path_buf()),
            lazy_mode: false,
        };

        // Load existing data
        store.load()?;

        Ok(store)
    }

    /// Create a low-memory graph store using lazy loading
    pub fn new_lazy(db_path: &Path) -> Result<Self> {
        std::fs::create_dir_all(db_path)?;

        let db_file = db_path.join("graph.redb");
        let db = redb::Database::create(&db_file).context("Failed to open redb database")?;

        Ok(Self {
            graph: RwLock::new(DiGraph::new()),
            node_index: DashMap::new(),
            function_spatial_index: DashMap::new(),
            file_functions_index: DashMap::new(),
            file_classes_index: DashMap::new(),
            metrics_cache: DashMap::new(),
            interner: super::interner::StringInterner::new(),
            db: Some(db),
            db_path: Some(db_path.to_path_buf()),
            lazy_mode: true,
        })
    }

    /// Create an in-memory only store (no persistence)
    pub fn in_memory() -> Self {
        Self {
            graph: RwLock::new(DiGraph::new()),
            node_index: DashMap::new(),
            function_spatial_index: DashMap::new(),
            file_functions_index: DashMap::new(),
            file_classes_index: DashMap::new(),
            metrics_cache: DashMap::new(),
            interner: super::interner::StringInterner::new(),
            db: None,
            db_path: None,
            lazy_mode: false,
        }
    }

    // ==================== Lock Helpers ====================
    //
    // RwLock poisoning means a thread panicked while holding the lock,
    // leaving the protected data in a potentially inconsistent state.
    // This is genuinely unrecoverable — propagating it as Result would
    // force callers to handle an error they cannot meaningfully act on.
    // These helpers centralise the `.expect()` calls with clear messages.

    /// Acquire read lock on the graph. Panics if lock is poisoned (unrecoverable).
    fn read_graph(&self) -> std::sync::RwLockReadGuard<'_, DiGraph<CodeNode, CodeEdge>> {
        self.graph
            .read()
            .expect("graph lock poisoned — a thread panicked while holding this lock")
    }

    /// Acquire write lock on the graph. Panics if lock is poisoned (unrecoverable).
    fn write_graph(&self) -> std::sync::RwLockWriteGuard<'_, DiGraph<CodeNode, CodeEdge>> {
        self.graph
            .write()
            .expect("graph lock poisoned — a thread panicked while holding this lock")
    }

    /// Pre-allocate capacity for the graph and node index.
    ///
    /// Call this before bulk-inserting nodes and edges to avoid repeated
    /// reallocations of petgraph's internal `Vec`s. The `estimated_nodes`
    /// and `estimated_edges` values are hints — over-estimating is cheap
    /// (a bit of extra memory), under-estimating just means some
    /// reallocations still happen.
    pub fn reserve_capacity(&self, estimated_nodes: usize, estimated_edges: usize) {
        let mut graph = self.write_graph();
        graph.reserve_nodes(estimated_nodes);
        graph.reserve_edges(estimated_edges);
        // DashMap handles capacity internally — no explicit reserve needed
    }

    // ==================== String Interner ====================

    /// Get the string interner for memory-efficient qualified name storage.
    /// Use for interning frequently-repeated strings like file paths and qualified names.
    pub fn interner(&self) -> &super::interner::StringInterner {
        &self.interner
    }

    // ==================== Metrics Cache ====================

    /// Store a computed metric for cross-phase reuse.
    /// Key format: "metric_name:entity_qn" (e.g., "degree_centrality:module.Class")
    pub fn cache_metric(&self, key: &str, value: f64) {
        self.metrics_cache.insert(key.to_string(), value);
    }

    /// Retrieve a cached metric.
    pub fn get_cached_metric(&self, key: &str) -> Option<f64> {
        self.metrics_cache.get(key).map(|r| *r)
    }

    /// Get all cached metrics with a given prefix.
    /// Useful for retrieving all metrics of a type (e.g., all "modularity:" metrics).
    pub fn get_cached_metrics_with_prefix(&self, prefix: &str) -> Vec<(String, f64)> {
        self.metrics_cache
            .iter()
            .filter(|entry| entry.key().starts_with(prefix))
            .map(|entry| (entry.key().clone(), *entry.value()))
            .collect()
    }

    /// Clear all data
    pub fn clear(&self) -> Result<()> {
        let mut graph = self.write_graph();
        graph.clear();
        self.node_index.clear();
        self.function_spatial_index.clear();
        self.file_functions_index.clear();
        self.file_classes_index.clear();
        self.metrics_cache.clear();

        if let Some(ref db) = self.db {
            let write_txn = db.begin_write()?;
            // Delete tables to clear all data
            let _ = write_txn.delete_table(NODES_TABLE);
            let _ = write_txn.delete_table(EDGES_TABLE);
            write_txn.commit()?;
        }

        Ok(())
    }

    // ==================== Node Operations ====================

    /// Add a node to the graph
    pub fn add_node(&self, node: CodeNode) -> NodeIndex {
        let qn = node.qualified_name.clone();
        let is_function = node.kind == NodeKind::Function;
        let file_path = if is_function { Some(node.file_path.clone()) } else { None };
        let line_start = node.line_start;
        let line_end = node.line_end;
        let is_class = node.kind == NodeKind::Class;
        let class_file_path = if is_class { Some(node.file_path.clone()) } else { None };

        // Check if node already exists — read DashMap before acquiring graph write lock
        if let Some(idx_ref) = self.node_index.get(&qn) {
            let idx = *idx_ref;
            drop(idx_ref); // Drop DashMap ref before acquiring graph write lock
            let mut graph = self.write_graph();
            if let Some(existing) = graph.node_weight_mut(idx) {
                *existing = node;
            }
            return idx;
        }

        let mut graph = self.write_graph();
        // Double-check after acquiring write lock (another thread may have inserted)
        if let Some(idx_ref) = self.node_index.get(&qn) {
            let idx = *idx_ref;
            drop(idx_ref);
            if let Some(existing) = graph.node_weight_mut(idx) {
                *existing = node;
            }
            return idx;
        }

        let idx = graph.add_node(node);
        self.node_index.insert(qn, idx);

        // Populate spatial index and file-scoped function index
        if is_function {
            if let Some(fp) = file_path {
                self.function_spatial_index
                    .entry(fp.clone())
                    .or_default()
                    .push((line_start, line_end, idx));
                self.file_functions_index
                    .entry(fp)
                    .or_default()
                    .push(idx);
            }
        }
        // Populate file-scoped class index
        if is_class {
            if let Some(fp) = class_file_path {
                self.file_classes_index
                    .entry(fp)
                    .or_default()
                    .push(idx);
            }
        }

        idx
    }

    /// Add multiple nodes at once (batch operation, single graph lock acquisition)
    pub fn add_nodes_batch(&self, nodes: Vec<CodeNode>) -> Vec<NodeIndex> {
        let mut graph = self.write_graph();
        let mut indices = Vec::with_capacity(nodes.len());

        for node in nodes {
            let qn = node.qualified_name.clone();
            let is_function = node.kind == NodeKind::Function;
            let file_path = if is_function { Some(node.file_path.clone()) } else { None };
            let line_start = node.line_start;
            let line_end = node.line_end;
            let is_class = node.kind == NodeKind::Class;
            let class_file_path = if is_class { Some(node.file_path.clone()) } else { None };

            if let Some(idx_ref) = self.node_index.get(&qn) {
                let idx = *idx_ref;
                drop(idx_ref); // Drop DashMap ref while holding graph write lock
                if let Some(existing) = graph.node_weight_mut(idx) {
                    *existing = node;
                }
                indices.push(idx);
            } else {
                let idx = graph.add_node(node);
                self.node_index.insert(qn, idx);

                // Populate spatial index and file-scoped function index
                if is_function {
                    if let Some(fp) = file_path {
                        self.function_spatial_index
                            .entry(fp.clone())
                            .or_default()
                            .push((line_start, line_end, idx));
                        self.file_functions_index
                            .entry(fp)
                            .or_default()
                            .push(idx);
                    }
                }
                // Populate file-scoped class index
                if is_class {
                    if let Some(fp) = class_file_path {
                        self.file_classes_index
                            .entry(fp)
                            .or_default()
                            .push(idx);
                    }
                }

                indices.push(idx);
            }
        }

        indices
    }

    /// Get node index by qualified name
    pub fn get_node_index(&self, qn: &str) -> Option<NodeIndex> {
        self.node_index.get(qn).map(|r| *r)
    }

    /// Get node by qualified name
    pub fn get_node(&self, qn: &str) -> Option<CodeNode> {
        let idx = self.node_index.get(qn).map(|r| *r)?;
        let graph = self.read_graph();
        graph.node_weight(idx).cloned()
    }

    /// Update a node's property
    pub fn update_node_property(
        &self,
        qn: &str,
        key: &str,
        value: impl Into<serde_json::Value>,
    ) -> bool {
        // Read DashMap index first, then acquire graph write lock
        let idx = match self.node_index.get(qn).map(|r| *r) {
            Some(idx) => idx,
            None => return false,
        };
        let mut graph = self.write_graph();
        if let Some(node) = graph.node_weight_mut(idx) {
            node.set_property(key, value);
            return true;
        }
        false
    }

    /// Update multiple properties on a node
    pub fn update_node_properties(&self, qn: &str, props: &[(&str, serde_json::Value)]) -> bool {
        // Read DashMap index first, then acquire graph write lock
        let idx = match self.node_index.get(qn).map(|r| *r) {
            Some(idx) => idx,
            None => return false,
        };
        let mut graph = self.write_graph();
        if let Some(node) = graph.node_weight_mut(idx) {
            for (key, value) in props {
                node.set_property(key, value.clone());
            }
            return true;
        }
        false
    }

    /// Get all nodes of a specific kind
    pub fn get_nodes_by_kind(&self, kind: NodeKind) -> Vec<CodeNode> {
        let graph = self.read_graph();

        graph
            .node_weights()
            .filter(|n| n.kind == kind)
            .cloned()
            .collect()
    }

    /// Get all files
    pub fn get_files(&self) -> Vec<CodeNode> {
        self.get_nodes_by_kind(NodeKind::File)
    }

    /// Get all functions
    pub fn get_functions(&self) -> Vec<CodeNode> {
        self.get_nodes_by_kind(NodeKind::Function)
    }

    /// Get all classes
    pub fn get_classes(&self) -> Vec<CodeNode> {
        self.get_nodes_by_kind(NodeKind::Class)
    }

    /// Get functions in a specific file. O(1) DashMap lookup + O(K) node reads
    /// where K = number of functions in the file (typically <30).
    pub fn get_functions_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        if let Some(indices) = self.file_functions_index.get(file_path) {
            let graph = self.read_graph();
            indices.value().iter()
                .filter_map(|&idx| graph.node_weight(idx).cloned())
                .collect()
        } else {
            vec![]
        }
    }

    /// Find the function containing a specific line in a file. O(1) DashMap lookup +
    /// O(N) scan of functions in that file (typically <30 functions per file).
    pub fn find_function_at(&self, file_path: &str, line: u32) -> Option<CodeNode> {
        let entries = self.function_spatial_index.get(file_path)?;
        let graph = self.read_graph();
        for &(start, end, idx) in entries.value() {
            if start <= line && end >= line {
                return graph.node_weight(idx).cloned();
            }
        }
        None
    }

    /// Get classes in a specific file. O(1) DashMap lookup + O(K) node reads
    /// where K = number of classes in the file (typically <10).
    pub fn get_classes_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        if let Some(indices) = self.file_classes_index.get(file_path) {
            let graph = self.read_graph();
            indices.value().iter()
                .filter_map(|&idx| graph.node_weight(idx).cloned())
                .collect()
        } else {
            vec![]
        }
    }

    /// Get functions with complexity above threshold
    pub fn get_complex_functions(&self, min_complexity: i64) -> Vec<CodeNode> {
        let graph = self.read_graph();

        graph
            .node_weights()
            .filter(|n| {
                n.kind == NodeKind::Function && n.complexity().is_some_and(|c| c >= min_complexity)
            })
            .cloned()
            .collect()
    }

    /// Get functions with many parameters
    pub fn get_long_param_functions(&self, min_params: i64) -> Vec<CodeNode> {
        let graph = self.read_graph();

        graph
            .node_weights()
            .filter(|n| {
                n.kind == NodeKind::Function && n.param_count().is_some_and(|p| p >= min_params)
            })
            .cloned()
            .collect()
    }

    // ==================== Edge Operations ====================

    /// Add an edge between nodes by index
    pub fn add_edge(&self, from: NodeIndex, to: NodeIndex, edge: CodeEdge) {
        let mut graph = self.write_graph();
        graph.add_edge(from, to, edge);
    }

    /// Add edge by qualified names (returns false if either node doesn't exist)
    pub fn add_edge_by_name(&self, from_qn: &str, to_qn: &str, edge: CodeEdge) -> bool {
        let from = self.node_index.get(from_qn).map(|r| *r);
        let to = self.node_index.get(to_qn).map(|r| *r);

        if let (Some(from), Some(to)) = (from, to) {
            self.add_edge(from, to, edge);
            true
        } else {
            false
        }
    }

    /// Add multiple edges at once (batch operation)
    pub fn add_edges_batch(&self, edges: Vec<(String, String, CodeEdge)>) -> usize {
        // Resolve all node indices from DashMap first, before acquiring graph write lock
        let resolved: Vec<_> = edges
            .into_iter()
            .filter_map(|(from_qn, to_qn, edge)| {
                let from = self.node_index.get(&from_qn).map(|r| *r)?;
                let to = self.node_index.get(&to_qn).map(|r| *r)?;
                Some((from, to, edge))
            })
            .collect();

        let mut graph = self.write_graph();
        let added = resolved.len();
        for (from, to, edge) in resolved {
            graph.add_edge(from, to, edge);
        }

        added
    }

    /// Get all edges of a specific kind as (source_qn, target_qn) pairs
    pub fn get_edges_by_kind(&self, kind: EdgeKind) -> Vec<(String, String)> {
        let graph = self.read_graph();

        graph
            .edge_references()
            .filter(|e| e.weight().kind == kind)
            .filter_map(|e| {
                let src = graph.node_weight(e.source())?;
                let dst = graph.node_weight(e.target())?;
                Some((src.qualified_name.clone(), dst.qualified_name.clone()))
            })
            .collect()
    }

    /// Get all import edges (file -> file)
    pub fn get_imports(&self) -> Vec<(String, String)> {
        self.get_edges_by_kind(EdgeKind::Imports)
    }

    /// Get all call edges (function -> function)
    pub fn get_calls(&self) -> Vec<(String, String)> {
        self.get_edges_by_kind(EdgeKind::Calls)
    }

    /// Get all inheritance edges (child -> parent)
    pub fn get_inheritance(&self) -> Vec<(String, String)> {
        self.get_edges_by_kind(EdgeKind::Inherits)
    }

    /// Get callers of a function (who calls this?)
    pub fn get_callers(&self, qn: &str) -> Vec<CodeNode> {
        let idx = match self.node_index.get(qn).map(|r| *r) {
            Some(idx) => idx,
            None => return vec![],
        };
        let graph = self.read_graph();
        graph
            .edges_directed(idx, Direction::Incoming)
            .filter(|e| e.weight().kind == EdgeKind::Calls)
            .filter_map(|e| graph.node_weight(e.source()).cloned())
            .collect()
    }

    /// Get callees of a function (what does this call?)
    pub fn get_callees(&self, qn: &str) -> Vec<CodeNode> {
        let idx = match self.node_index.get(qn).map(|r| *r) {
            Some(idx) => idx,
            None => return vec![],
        };
        let graph = self.read_graph();
        graph
            .edges_directed(idx, Direction::Outgoing)
            .filter(|e| e.weight().kind == EdgeKind::Calls)
            .filter_map(|e| graph.node_weight(e.target()).cloned())
            .collect()
    }

    /// Get importers of a module/class (who imports this?)
    pub fn get_importers(&self, qn: &str) -> Vec<CodeNode> {
        let idx = match self.node_index.get(qn).map(|r| *r) {
            Some(idx) => idx,
            None => return vec![],
        };
        let graph = self.read_graph();
        graph
            .edges_directed(idx, Direction::Incoming)
            .filter(|e| e.weight().kind == EdgeKind::Imports)
            .filter_map(|e| graph.node_weight(e.source()).cloned())
            .collect()
    }

    /// Get parent classes (what does this inherit from?)
    pub fn get_parent_classes(&self, qn: &str) -> Vec<CodeNode> {
        let idx = match self.node_index.get(qn).map(|r| *r) {
            Some(idx) => idx,
            None => return vec![],
        };
        let graph = self.read_graph();
        graph
            .edges_directed(idx, Direction::Outgoing)
            .filter(|e| e.weight().kind == EdgeKind::Inherits)
            .filter_map(|e| graph.node_weight(e.target()).cloned())
            .collect()
    }

    /// Get child classes (what inherits from this?)
    pub fn get_child_classes(&self, qn: &str) -> Vec<CodeNode> {
        let idx = match self.node_index.get(qn).map(|r| *r) {
            Some(idx) => idx,
            None => return vec![],
        };
        let graph = self.read_graph();
        graph
            .edges_directed(idx, Direction::Incoming)
            .filter(|e| e.weight().kind == EdgeKind::Inherits)
            .filter_map(|e| graph.node_weight(e.source()).cloned())
            .collect()
    }

    // ==================== Graph Metrics ====================

    /// Get in-degree (fan-in) for a node
    pub fn fan_in(&self, qn: &str) -> usize {
        let idx = match self.node_index.get(qn).map(|r| *r) {
            Some(idx) => idx,
            None => return 0,
        };
        let graph = self.read_graph();
        graph.edges_directed(idx, Direction::Incoming).count()
    }

    /// Get out-degree (fan-out) for a node
    pub fn fan_out(&self, qn: &str) -> usize {
        let idx = match self.node_index.get(qn).map(|r| *r) {
            Some(idx) => idx,
            None => return 0,
        };
        let graph = self.read_graph();
        graph.edges_directed(idx, Direction::Outgoing).count()
    }

    /// Get call fan-in (how many functions call this?)
    pub fn call_fan_in(&self, qn: &str) -> usize {
        let idx = match self.node_index.get(qn).map(|r| *r) {
            Some(idx) => idx,
            None => return 0,
        };
        let graph = self.read_graph();
        graph
            .edges_directed(idx, Direction::Incoming)
            .filter(|e| e.weight().kind == EdgeKind::Calls)
            .count()
    }

    /// Get call fan-out (how many functions does this call?)
    pub fn call_fan_out(&self, qn: &str) -> usize {
        let idx = match self.node_index.get(qn).map(|r| *r) {
            Some(idx) => idx,
            None => return 0,
        };
        let graph = self.read_graph();
        graph
            .edges_directed(idx, Direction::Outgoing)
            .filter(|e| e.weight().kind == EdgeKind::Calls)
            .count()
    }

    /// Get node count
    pub fn node_count(&self) -> usize {
        self.read_graph().node_count()
    }

    /// Get edge count
    pub fn edge_count(&self) -> usize {
        self.read_graph().edge_count()
    }

    /// Get statistics
    pub fn stats(&self) -> HashMap<String, i64> {
        let graph = self.read_graph();
        let mut stats = HashMap::new();

        let mut file_count = 0i64;
        let mut func_count = 0i64;
        let mut class_count = 0i64;

        for node in graph.node_weights() {
            match node.kind {
                NodeKind::File => file_count += 1,
                NodeKind::Function => func_count += 1,
                NodeKind::Class => class_count += 1,
                _ => {}
            }
        }

        stats.insert("total_files".to_string(), file_count);
        stats.insert("total_functions".to_string(), func_count);
        stats.insert("total_classes".to_string(), class_count);
        stats.insert("total_nodes".to_string(), graph.node_count() as i64);
        stats.insert("total_edges".to_string(), graph.edge_count() as i64);

        stats
    }

    // ==================== Cycle Detection ====================
    //
    // Uses Tarjan's SCC algorithm for efficient cycle detection.
    // Instead of finding all possible cycles (which can be exponential),
    // we find strongly connected components (SCCs) - any SCC with >1 node
    // represents a circular dependency.
    //
    // This approach:
    // 1. Runs in O(V + E) time
    // 2. Reports each cycle exactly once (no duplicates)
    // 3. Handles large codebases efficiently

    /// Find circular dependencies in imports using SCC
    pub fn find_import_cycles(&self) -> Vec<Vec<String>> {
        self.find_cycles_scc(EdgeKind::Imports)
    }

    /// Find circular dependencies in calls using SCC
    pub fn find_call_cycles(&self) -> Vec<Vec<String>> {
        self.find_cycles_scc(EdgeKind::Calls)
    }

    /// Find cycles using Tarjan's SCC algorithm
    ///
    /// Returns strongly connected components with >1 node, which represent cycles.
    /// Each SCC is returned as a list of qualified names.
    fn find_cycles_scc(&self, edge_kind: EdgeKind) -> Vec<Vec<String>> {
        let graph = self.read_graph();

        // Build a filtered subgraph with only edges of the specified kind
        // This is more efficient than filtering during SCC traversal
        let mut filtered_graph: DiGraph<NodeIndex, ()> = DiGraph::new();
        let mut idx_map: HashMap<NodeIndex, NodeIndex> = HashMap::new();
        let mut reverse_map: HashMap<NodeIndex, NodeIndex> = HashMap::new();

        // Add all nodes that have at least one edge of the specified kind
        let relevant_nodes: HashSet<NodeIndex> = graph
            .edge_references()
            .filter(|e| {
                if e.weight().kind != edge_kind {
                    return false;
                }
                // Skip type-only imports
                if edge_kind == EdgeKind::Imports {
                    let is_type_only = e.weight().properties.get("is_type_only")
                        .map(|v| v.as_bool().unwrap_or(false) || v == "true")
                        .unwrap_or(false);
                    if is_type_only { return false; }
                }
                true
            })
            .flat_map(|e| [e.source(), e.target()])
            .collect();

        for orig_idx in relevant_nodes {
            let new_idx = filtered_graph.add_node(orig_idx);
            idx_map.insert(orig_idx, new_idx);
            reverse_map.insert(new_idx, orig_idx);
        }

        // Add filtered edges
        for edge in graph.edge_references() {
            if edge.weight().kind != edge_kind {
                continue;
            }
            // Skip type-only imports
            if edge_kind == EdgeKind::Imports {
                let is_type_only = edge.weight().properties.get("is_type_only")
                    .map(|v| v.as_bool().unwrap_or(false) || v == "true")
                    .unwrap_or(false);
                if is_type_only { continue; }
            }

            if let (Some(&from), Some(&to)) =
                (idx_map.get(&edge.source()), idx_map.get(&edge.target()))
            {
                filtered_graph.add_edge(from, to, ());
            }
        }

        // Run Tarjan's SCC algorithm on the filtered graph
        let sccs = tarjan_scc(&filtered_graph);

        // Convert SCCs back to qualified names
        // Only keep SCCs with >1 node (actual cycles)
        let mut cycles: Vec<Vec<String>> = sccs
            .into_iter()
            .filter(|scc| scc.len() > 1)
            .map(|scc| {
                let mut names: Vec<String> = scc
                    .iter()
                    .filter_map(|&filtered_idx| reverse_map.get(&filtered_idx))
                    .filter_map(|&orig_idx| graph.node_weight(orig_idx))
                    .map(|n| n.qualified_name.clone())
                    .collect();

                // Sort for consistent ordering and deduplication
                names.sort();
                names
            })
            .collect();

        // Deduplicate (shouldn't be needed with SCC, but just in case)
        cycles.sort();
        cycles.dedup();

        // Sort by size (largest cycles first - they're usually most important)
        cycles.sort_by_key(|c| std::cmp::Reverse(c.len()));

        cycles
    }

    /// Find the minimal cycle through a specific node (for detailed reporting)
    ///
    /// This is useful when you want to show the shortest cycle involving a particular file.
    pub fn find_minimal_cycle(&self, start_qn: &str, edge_kind: EdgeKind) -> Option<Vec<String>> {
        let start_idx = self.node_index.get(start_qn).map(|r| *r)?;
        let graph = self.read_graph();

        // BFS to find shortest cycle back to start
        let mut queue = std::collections::VecDeque::new();
        let mut visited: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();

        queue.push_back((start_idx, vec![start_idx]));
        visited.insert(start_idx, vec![start_idx]);

        while let Some((current, path)) = queue.pop_front() {
            for edge in graph.edges_directed(current, Direction::Outgoing) {
                if edge.weight().kind != edge_kind {
                    continue;
                }

                // Skip type-only imports
                let is_type_only_import = edge_kind == EdgeKind::Imports
                    && edge
                        .weight()
                        .properties
                        .get("is_type_only")
                        .map(|v| v.as_bool().unwrap_or(false) || v == "true")
                        .unwrap_or(false);
                if is_type_only_import {
                    continue;
                }

                let target = edge.target();

                // Found cycle back to start!
                if target == start_idx && path.len() > 1 {
                    return Some(
                        path.iter()
                            .filter_map(|&idx| graph.node_weight(idx))
                            .map(|n| n.qualified_name.clone())
                            .collect(),
                    );
                }

                // Continue BFS if not visited
                if let std::collections::hash_map::Entry::Vacant(e) = visited.entry(target) {
                    let mut new_path = path.clone();
                    new_path.push(target);
                    e.insert(new_path.clone());
                    queue.push_back((target, new_path));
                }
            }
        }

        None
    }

    // ==================== Persistence ====================

    /// Persist graph to redb
    pub fn save(&self) -> Result<()> {
        let db = match &self.db {
            Some(db) => db,
            None => return Ok(()),
        };

        let graph = self.read_graph();

        let write_txn = db.begin_write()?;
        {
            // Clear and rebuild nodes table
            let mut table = write_txn.open_table(NODES_TABLE)?;

            // Save nodes
            for node in graph.node_weights() {
                let key = format!("node:{}", node.qualified_name);
                let value = serde_json::to_vec(node)?;
                table.insert(key.as_str(), value.as_slice())?;
            }

            // Save edges as a single entry
            let edges: Vec<_> = graph
                .edge_references()
                .filter_map(|e| {
                    let src = graph.node_weight(e.source())?;
                    let dst = graph.node_weight(e.target())?;
                    Some((
                        src.qualified_name.clone(),
                        dst.qualified_name.clone(),
                        e.weight().clone(),
                    ))
                })
                .collect();

            let edges_data = serde_json::to_vec(&edges)?;

            let mut edges_table = write_txn.open_table(EDGES_TABLE)?;
            edges_table.insert("__edges__", edges_data.as_slice())?;
        }
        write_txn.commit()?;

        Ok(())
    }

    /// Load graph from redb
    fn load(&self) -> Result<()> {
        let db = match &self.db {
            Some(db) => db,
            None => return Ok(()),
        };

        let read_txn = db.begin_read()?;

        // Try to open tables — if they don't exist yet, this is a fresh db
        let nodes_table = match read_txn.open_table(NODES_TABLE) {
            Ok(t) => t,
            Err(redb::TableError::TableDoesNotExist(_)) => return Ok(()),
            Err(e) => return Err(e.into()),
        };

        let mut graph = self.write_graph();

        // Load nodes
        for item in nodes_table.range::<&str>(..)? {
            let (key, value) = item?;
            let key_str = key.value();
            if key_str.starts_with("node:") {
                let node: CodeNode = serde_json::from_slice(value.value())?;
                let qn = node.qualified_name.clone();
                let idx = graph.add_node(node);
                self.node_index.insert(qn, idx);
            }
        }

        // Load edges
        let edges_table = match read_txn.open_table(EDGES_TABLE) {
            Ok(t) => t,
            Err(redb::TableError::TableDoesNotExist(_)) => return Ok(()),
            Err(e) => return Err(e.into()),
        };

        if let Some(edges_entry) = edges_table.get("__edges__")? {
            let edges: Vec<(String, String, CodeEdge)> =
                serde_json::from_slice(edges_entry.value())?;
            for (src_qn, dst_qn, edge) in edges {
                let src = self.node_index.get(&src_qn).map(|r| *r);
                let dst = self.node_index.get(&dst_qn).map(|r| *r);
                if let (Some(src), Some(dst)) = (src, dst) {
                    graph.add_edge(src, dst, edge);
                }
            }
        }

        Ok(())
    }
}

// redb::Database handles cleanup on Drop automatically — no manual flush needed

// GraphQuery trait implementation lives in graph/store_query.rs

#[cfg(test)]
mod tests;
