//! Pure Rust graph storage using petgraph + redb
//!
//! Replaces Kuzu for better cross-platform compatibility.
//! No C++ dependencies, builds everywhere.

use anyhow::{Context, Result};
use dashmap::DashMap;
use petgraph::algo::tarjan_scc;
use petgraph::stable_graph::{NodeIndex, StableGraph};
use petgraph::visit::{EdgeRef, IntoEdgeReferences};
use petgraph::Direction;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::Path;
use std::sync::{Mutex, RwLock};

use super::interner::StrKey;
use super::store_models::ExtraProps;
pub use super::store_models::{CodeEdge, CodeNode, EdgeKind, NodeKind};

/// Call maps cache type: (qn_to_idx, callers_by_idx, callees_by_idx)
type CallMapsRaw = (
    HashMap<StrKey, usize>,
    HashMap<usize, Vec<usize>>,
    HashMap<usize, Vec<usize>>,
);

/// Pure Rust graph store - replaces Kuzu
pub struct GraphStore {
    /// In-memory graph
    graph: RwLock<StableGraph<CodeNode, CodeEdge>>,
    /// Node lookup by qualified name — DashMap for lock-free concurrent reads
    node_index: DashMap<StrKey, NodeIndex>,
    /// Spatial index: file_path → [(line_start, line_end, NodeIndex)] for O(1) function lookup.
    /// Populated during add_node/add_nodes_batch for Function nodes.
    function_spatial_index: DashMap<StrKey, Vec<(u32, u32, NodeIndex)>>,
    /// File-scoped function index: file_path → [NodeIndex] for O(1) get_functions_in_file().
    file_functions_index: DashMap<StrKey, Vec<NodeIndex>>,
    /// File-scoped class index: file_path → [NodeIndex] for O(1) get_classes_in_file().
    file_classes_index: DashMap<StrKey, Vec<NodeIndex>>,
    /// Reverse index: file_path → all NodeIndexes belonging to that file.
    /// Used for delta patching (removing a file's entities from the graph).
    file_all_nodes_index: DashMap<StrKey, Vec<NodeIndex>>,
    /// Cached graph metrics from detectors, reusable by scoring phase.
    /// Key format: "metric_name:entity_qn" (e.g., "degree_centrality:module.Class")
    metrics_cache: DashMap<String, f64>,
    /// Extra (cold) properties stored per qualified_name StrKey
    extra_props: DashMap<StrKey, ExtraProps>,
    /// Persistent edge dedup set: prevents duplicate (from, to, kind) edges across batches.
    /// O(1) lookup instead of O(degree) graph scan per insertion.
    edge_set: Mutex<HashSet<(NodeIndex, NodeIndex, EdgeKind)>>,
    /// Cached call maps from build_call_maps_raw() — computed once, reused by
    /// both the detector runner (CachedGraphQuery) and postprocess (FP filter).
    call_maps_cache: RwLock<Option<CallMapsRaw>>,
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
            graph: RwLock::new(StableGraph::new()),
            node_index: DashMap::new(),
            function_spatial_index: DashMap::new(),
            file_functions_index: DashMap::new(),
            file_classes_index: DashMap::new(),
            file_all_nodes_index: DashMap::new(),
            metrics_cache: DashMap::new(),

            extra_props: DashMap::new(),
            edge_set: Mutex::new(HashSet::new()),
            call_maps_cache: RwLock::new(None),
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
            graph: RwLock::new(StableGraph::new()),
            node_index: DashMap::new(),
            function_spatial_index: DashMap::new(),
            file_functions_index: DashMap::new(),
            file_classes_index: DashMap::new(),
            file_all_nodes_index: DashMap::new(),
            metrics_cache: DashMap::new(),

            extra_props: DashMap::new(),
            edge_set: Mutex::new(HashSet::new()),
            call_maps_cache: RwLock::new(None),
            db: Some(db),
            db_path: Some(db_path.to_path_buf()),
            lazy_mode: true,
        })
    }

    /// Create an in-memory only store (no persistence)
    pub fn in_memory() -> Self {
        Self {
            graph: RwLock::new(StableGraph::new()),
            node_index: DashMap::new(),
            function_spatial_index: DashMap::new(),
            file_functions_index: DashMap::new(),
            file_classes_index: DashMap::new(),
            file_all_nodes_index: DashMap::new(),
            metrics_cache: DashMap::new(),

            extra_props: DashMap::new(),
            edge_set: Mutex::new(HashSet::new()),
            call_maps_cache: RwLock::new(None),
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
    fn read_graph(&self) -> std::sync::RwLockReadGuard<'_, StableGraph<CodeNode, CodeEdge>> {
        self.graph
            .read()
            .expect("graph lock poisoned — a thread panicked while holding this lock")
    }

    /// Acquire write lock on the graph. Panics if lock is poisoned (unrecoverable).
    fn write_graph(&self) -> std::sync::RwLockWriteGuard<'_, StableGraph<CodeNode, CodeEdge>> {
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
    pub fn reserve_capacity(&self, _estimated_nodes: usize, _estimated_edges: usize) {
        // StableGraph (petgraph 0.7) does not expose reserve_nodes/reserve_edges.
        // Pre-allocation happens via StableGraph::with_capacity at construction
        // time if needed. This is a no-op to keep the public API stable.
    }

    // ==================== String Interner ====================

    /// Get the string interner for memory-efficient qualified name storage.
    /// Use for interning frequently-repeated strings like file paths and qualified names.
    /// Returns the global singleton interner shared by all GraphStore instances and
    /// CodeNode convenience builders.
    pub fn interner(&self) -> &'static super::interner::StringInterner {
        super::interner::global_interner()
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
        let mut results: Vec<(String, f64)> = self.metrics_cache
            .iter()
            .filter(|entry| entry.key().starts_with(prefix))
            .map(|entry| (entry.key().clone(), *entry.value()))
            .collect();
        results.sort_unstable_by(|a, b| a.0.cmp(&b.0));
        results
    }

    /// Clear all data
    pub fn clear(&self) -> Result<()> {
        let mut graph = self.write_graph();
        graph.clear();
        self.node_index.clear();
        self.function_spatial_index.clear();
        self.file_functions_index.clear();
        self.file_classes_index.clear();
        self.file_all_nodes_index.clear();
        self.metrics_cache.clear();
        self.extra_props.clear();
        {
            let mut guard = self.call_maps_cache.write()
                .expect("call_maps_cache lock poisoned");
            *guard = None;
        }

        if let Some(ref db) = self.db {
            let write_txn = db.begin_write()?;
            // Delete tables to clear all data
            let _ = write_txn.delete_table(NODES_TABLE);
            let _ = write_txn.delete_table(EDGES_TABLE);
            write_txn.commit()?;
        }

        Ok(())
    }

    /// Release build-phase caches that are not needed during detection.
    /// Call after graph building is complete to reclaim memory (~1.8MB for edge_set).
    pub fn clear_build_caches(&self) {
        let mut set = self.edge_set.lock().expect("edge_set lock");
        set.clear();
        set.shrink_to_fit();
    }

    // ==================== Node Operations ====================

    /// Add a node to the graph
    pub fn add_node(&self, node: CodeNode) -> NodeIndex {
        let qn = node.qualified_name;
        let node_file_path = node.file_path;
        let is_function = node.kind == NodeKind::Function;
        let file_path = if is_function { Some(node.file_path) } else { None };
        let line_start = node.line_start;
        let line_end = node.line_end;
        let is_class = node.kind == NodeKind::Class;
        let class_file_path = if is_class { Some(node.file_path) } else { None };

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
                    .entry(fp)
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

        // Populate file_all_nodes_index for delta patching
        self.file_all_nodes_index.entry(node_file_path).or_default().push(idx);

        idx
    }

    /// Add multiple nodes at once (batch operation, single graph lock acquisition)
    pub fn add_nodes_batch(&self, nodes: Vec<CodeNode>) -> Vec<NodeIndex> {
        let mut graph = self.write_graph();
        let mut indices = Vec::with_capacity(nodes.len());

        for node in nodes {
            let qn = node.qualified_name;
            let node_file_path = node.file_path;
            let is_function = node.kind == NodeKind::Function;
            let file_path = if is_function { Some(node.file_path) } else { None };
            let line_start = node.line_start;
            let line_end = node.line_end;
            let is_class = node.kind == NodeKind::Class;
            let class_file_path = if is_class { Some(node.file_path) } else { None };

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
                            .entry(fp)
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

                // Populate file_all_nodes_index for delta patching
                self.file_all_nodes_index.entry(node_file_path).or_default().push(idx);

                indices.push(idx);
            }
        }

        indices
    }

    /// Add nodes and create Contains edges (file -> function/class) in one operation.
    /// This avoids buffering 84K+ (String, String, CodeEdge) tuples for Contains edges
    /// that are always intra-file and always resolved.
    pub fn add_nodes_batch_with_contains(
        &self,
        nodes: Vec<CodeNode>,
        file_qn: &str,
    ) -> Vec<NodeIndex> {
        let mut graph = self.write_graph();
        let mut indices = Vec::with_capacity(nodes.len());

        // Resolve file node index (should already exist from a prior add_nodes_batch call)
        let file_qn_key = self.interner().intern(file_qn);
        let file_idx = self.node_index.get(&file_qn_key).map(|r| *r);

        for node in nodes {
            let qn = node.qualified_name;
            let node_file_path = node.file_path;
            let is_function = node.kind == NodeKind::Function;
            let file_path = if is_function { Some(node.file_path) } else { None };
            let line_start = node.line_start;
            let line_end = node.line_end;
            let is_class = node.kind == NodeKind::Class;
            let class_file_path = if is_class { Some(node.file_path) } else { None };
            let needs_contains = is_function || is_class;

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
                            .entry(fp)
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

                // Populate file_all_nodes_index for delta patching
                self.file_all_nodes_index.entry(node_file_path).or_default().push(idx);

                // Add Contains edge: file -> function/class (in same write lock)
                if needs_contains {
                    if let Some(f_idx) = file_idx {
                        let mut set = self.edge_set.lock().expect("edge_set lock");
                        if set.insert((f_idx, idx, EdgeKind::Contains)) {
                            graph.add_edge(f_idx, idx, CodeEdge::contains());
                        }
                    }
                }

                indices.push(idx);
            }
        }

        indices
    }

    /// Get node index by qualified name
    pub fn get_node_index(&self, qn: &str) -> Option<NodeIndex> {
        let key = self.interner().intern(qn);
        self.node_index.get(&key).map(|r| *r)
    }

    /// Get node by qualified name
    pub fn get_node(&self, qn: &str) -> Option<CodeNode> {
        let key = self.interner().intern(qn);
        let idx = self.node_index.get(&key).map(|r| *r)?;
        let graph = self.read_graph();
        graph.node_weight(idx).copied()
    }

    /// Update a node's property
    pub fn update_node_property(
        &self,
        qn: &str,
        key: &str,
        value: impl Into<serde_json::Value>,
    ) -> bool {
        let intern_qn = self.interner().intern(qn);
        // Read DashMap index first, then acquire graph write lock
        let idx = match self.node_index.get(&intern_qn).map(|r| *r) {
            Some(idx) => idx,
            None => return false,
        };
        let val: serde_json::Value = value.into();
        let mut graph = self.write_graph();
        if let Some(node) = graph.node_weight_mut(idx) {
            match key {
                "complexity" => node.complexity = val.as_i64().unwrap_or(0) as u16,
                "paramCount" => node.param_count = val.as_i64().unwrap_or(0) as u8,
                "methodCount" => node.method_count = val.as_i64().unwrap_or(0) as u16,
                "maxNesting" | "nesting_depth" => node.max_nesting = val.as_i64().unwrap_or(0) as u8,
                "returnCount" => node.return_count = val.as_i64().unwrap_or(0) as u8,
                "commit_count" => node.commit_count = val.as_i64().unwrap_or(0) as u16,
                "is_async" => if val.as_bool().unwrap_or(false) { node.set_flag(super::store_models::FLAG_IS_ASYNC); },
                "is_exported" => if val.as_bool().unwrap_or(false) { node.set_flag(super::store_models::FLAG_IS_EXPORTED); },
                "is_public" => if val.as_bool().unwrap_or(false) { node.set_flag(super::store_models::FLAG_IS_PUBLIC); },
                "is_method" => if val.as_bool().unwrap_or(false) { node.set_flag(super::store_models::FLAG_IS_METHOD); },
                "address_taken" => if val.as_bool().unwrap_or(false) { node.set_flag(super::store_models::FLAG_ADDRESS_TAKEN); },
                "has_decorators" => if val.as_bool().unwrap_or(false) { node.set_flag(super::store_models::FLAG_HAS_DECORATORS); },
                "author" => if let Some(s) = val.as_str() {
                    let mut ep = self.extra_props.entry(intern_qn).or_default();
                    ep.author = Some(self.interner().intern(s));
                },
                "last_modified" => if let Some(s) = val.as_str() {
                    let mut ep = self.extra_props.entry(intern_qn).or_default();
                    ep.last_modified = Some(self.interner().intern(s));
                },
                _ => {}
            }
            return true;
        }
        false
    }

    /// Update multiple properties on a node
    pub fn update_node_properties(&self, qn: &str, props: &[(&str, serde_json::Value)]) -> bool {
        let intern_qn = self.interner().intern(qn);
        // Read DashMap index first, then acquire graph write lock
        let idx = match self.node_index.get(&intern_qn).map(|r| *r) {
            Some(idx) => idx,
            None => return false,
        };
        let mut graph = self.write_graph();
        if let Some(node) = graph.node_weight_mut(idx) {
            let mut extras = ExtraProps::default();
            let mut has_extras = false;
            for (key, value) in props {
                match *key {
                    "complexity" => node.complexity = value.as_i64().unwrap_or(0) as u16,
                    "paramCount" => node.param_count = value.as_i64().unwrap_or(0) as u8,
                    "methodCount" => node.method_count = value.as_i64().unwrap_or(0) as u16,
                    "maxNesting" | "nesting_depth" => node.max_nesting = value.as_i64().unwrap_or(0) as u8,
                    "returnCount" => node.return_count = value.as_i64().unwrap_or(0) as u8,
                    "commit_count" => node.commit_count = value.as_i64().unwrap_or(0) as u16,
                    "is_async" => if value.as_bool().unwrap_or(false) { node.set_flag(super::store_models::FLAG_IS_ASYNC); },
                    "is_exported" => if value.as_bool().unwrap_or(false) { node.set_flag(super::store_models::FLAG_IS_EXPORTED); },
                    "is_public" => if value.as_bool().unwrap_or(false) { node.set_flag(super::store_models::FLAG_IS_PUBLIC); },
                    "is_method" => if value.as_bool().unwrap_or(false) { node.set_flag(super::store_models::FLAG_IS_METHOD); },
                    "address_taken" => if value.as_bool().unwrap_or(false) { node.set_flag(super::store_models::FLAG_ADDRESS_TAKEN); },
                    "has_decorators" => if value.as_bool().unwrap_or(false) { node.set_flag(super::store_models::FLAG_HAS_DECORATORS); },
                    "author" => if let Some(s) = value.as_str() {
                        extras.author = Some(self.interner().intern(s));
                        has_extras = true;
                    },
                    "last_modified" => if let Some(s) = value.as_str() {
                        extras.last_modified = Some(self.interner().intern(s));
                        has_extras = true;
                    },
                    _ => {}
                }
            }
            if has_extras {
                drop(graph);
                let mut ep = self.extra_props.entry(intern_qn).or_default();
                if let Some(a) = extras.author { ep.author = Some(a); }
                if let Some(lm) = extras.last_modified { ep.last_modified = Some(lm); }
            }
            return true;
        }
        false
    }

    /// Set extra properties (cold string data) for a node by its qualified_name StrKey.
    ///
    /// This is used by graph builders to store string properties like params,
    /// doc_comment, and decorators that don't fit in the compact CodeNode struct.
    pub fn set_extra_props(&self, qn_key: StrKey, props: ExtraProps) {
        self.extra_props.insert(qn_key, props);
    }

    /// Get extra properties for a node by its qualified_name StrKey.
    pub fn get_extra_props(&self, qn_key: StrKey) -> Option<ExtraProps> {
        self.extra_props.get(&qn_key).map(|r| r.clone())
    }

    /// Get all nodes of a specific kind (sorted by qualified_name for determinism)
    pub fn get_nodes_by_kind(&self, kind: NodeKind) -> Vec<CodeNode> {
        let graph = self.read_graph();

        let mut nodes: Vec<CodeNode> = graph
            .node_weights()
            .filter(|n| n.kind == kind)
            .copied()
            .collect();
        let si = self.interner();
        nodes.sort_by_cached_key(|n| si.resolve(n.qualified_name).to_owned());
        nodes
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
        let key = self.interner().intern(file_path);
        if let Some(indices) = self.file_functions_index.get(&key) {
            let graph = self.read_graph();
            indices.value().iter()
                .filter_map(|&idx| graph.node_weight(idx).copied())
                .collect()
        } else {
            vec![]
        }
    }

    /// Find the function containing a specific line in a file. O(1) DashMap lookup +
    /// O(N) scan of functions in that file (typically <30 functions per file).
    pub fn find_function_at(&self, file_path: &str, line: u32) -> Option<CodeNode> {
        let key = self.interner().intern(file_path);
        let entries = self.function_spatial_index.get(&key)?;
        let graph = self.read_graph();
        for &(start, end, idx) in entries.value() {
            if start <= line && end >= line {
                return graph.node_weight(idx).copied();
            }
        }
        None
    }

    /// Get classes in a specific file. O(1) DashMap lookup + O(K) node reads
    /// where K = number of classes in the file (typically <10).
    pub fn get_classes_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        let key = self.interner().intern(file_path);
        if let Some(indices) = self.file_classes_index.get(&key) {
            let graph = self.read_graph();
            indices.value().iter()
                .filter_map(|&idx| graph.node_weight(idx).copied())
                .collect()
        } else {
            vec![]
        }
    }

    /// Get functions with complexity above threshold (sorted by qualified_name for determinism)
    pub fn get_complex_functions(&self, min_complexity: i64) -> Vec<CodeNode> {
        let graph = self.read_graph();

        let mut nodes: Vec<CodeNode> = graph
            .node_weights()
            .filter(|n| {
                n.kind == NodeKind::Function && n.complexity_opt().is_some_and(|c| c >= min_complexity)
            })
            .copied()
            .collect();
        let si = self.interner();
        nodes.sort_by_cached_key(|n| si.resolve(n.qualified_name).to_owned());
        nodes
    }

    /// Get functions with many parameters (sorted by qualified_name for determinism)
    pub fn get_long_param_functions(&self, min_params: i64) -> Vec<CodeNode> {
        let graph = self.read_graph();

        let mut nodes: Vec<CodeNode> = graph
            .node_weights()
            .filter(|n| {
                n.kind == NodeKind::Function && n.param_count_opt().is_some_and(|p| p >= min_params)
            })
            .copied()
            .collect();
        let si = self.interner();
        nodes.sort_by_cached_key(|n| si.resolve(n.qualified_name).to_owned());
        nodes
    }

    // ==================== Edge Operations ====================

    /// Add an edge between nodes by index (skips if duplicate edge exists)
    pub fn add_edge(&self, from: NodeIndex, to: NodeIndex, edge: CodeEdge) {
        let mut set = self.edge_set.lock().expect("edge_set lock poisoned");
        if !set.insert((from, to, edge.kind)) {
            return; // duplicate
        }
        drop(set);
        let mut graph = self.write_graph();
        graph.add_edge(from, to, edge);
    }

    /// Add edge by qualified names (returns false if either node doesn't exist)
    pub fn add_edge_by_name(&self, from_qn: &str, to_qn: &str, edge: CodeEdge) -> bool {
        let from_key = self.interner().intern(from_qn);
        let to_key = self.interner().intern(to_qn);
        let from = self.node_index.get(&from_key).map(|r| *r);
        let to = self.node_index.get(&to_key).map(|r| *r);

        if let (Some(from), Some(to)) = (from, to) {
            self.add_edge(from, to, edge);
            true
        } else {
            false
        }
    }

    /// Add multiple edges at once (batch operation, deduplicated across all batches)
    pub fn add_edges_batch(&self, edges: Vec<(String, String, CodeEdge)>) -> usize {
        // Phase 1: Resolve all node indices from DashMap (lock-free reads)
        let resolved: Vec<_> = edges
            .into_iter()
            .filter_map(|(from_qn, to_qn, edge)| {
                let from_key = self.interner().intern(&from_qn);
                let to_key = self.interner().intern(&to_qn);
                let from = self.node_index.get(&from_key).map(|r| *r)?;
                let to = self.node_index.get(&to_key).map(|r| *r)?;
                Some((from, to, edge))
            })
            .collect();

        // Phase 2: Dedup under edge_set mutex only (no graph write lock needed)
        let unique: Vec<_> = {
            let mut set = self.edge_set.lock().expect("edge_set lock poisoned");
            resolved
                .into_iter()
                .filter(|(from, to, edge)| set.insert((*from, *to, edge.kind)))
                .collect()
        };
        // edge_set mutex released here

        // Phase 3: Insert deduplicated edges under graph write lock
        let added = unique.len();
        if added > 0 {
            let mut graph = self.write_graph();
            for (from, to, edge) in unique {
                graph.add_edge(from, to, edge);
            }
        }

        added
    }

    /// Get all edges of a specific kind as (source_qn, target_qn) StrKey pairs (sorted for determinism)
    pub fn get_edges_by_kind(&self, kind: EdgeKind) -> Vec<(StrKey, StrKey)> {
        let graph = self.read_graph();

        let mut edges: Vec<(StrKey, StrKey)> = graph
            .edge_references()
            .filter(|e| e.weight().kind == kind)
            .filter_map(|e| {
                let src = graph.node_weight(e.source())?;
                let dst = graph.node_weight(e.target())?;
                Some((src.qualified_name, dst.qualified_name))
            })
            .collect();
        let si = self.interner();
        edges.sort_unstable_by(|a, b| {
            si.resolve(a.0).cmp(si.resolve(b.0))
                .then_with(|| si.resolve(a.1).cmp(si.resolve(b.1)))
        });
        edges
    }

    /// Build call maps directly from petgraph, avoiding 12.5M+ (String, String) allocation.
    ///
    /// Iterates petgraph edges once, resolving NodeIndex -> function list index
    /// via an intermediate HashMap. No String allocation for edge data.
    /// Function ordering matches get_functions() (sorted by qualified_name).
    pub fn build_call_maps_raw(
        &self,
    ) -> (
        HashMap<StrKey, usize>,
        HashMap<usize, Vec<usize>>,
        HashMap<usize, Vec<usize>>,
    ) {
        // Return cached result if already computed (avoids 74-103ms rebuild in postprocess)
        {
            let guard = self.call_maps_cache.read()
                .expect("call_maps_cache lock poisoned");
            if let Some(cached) = guard.as_ref() {
                return cached.clone();
            }
        }

        use petgraph::visit::EdgeRef;
        let graph = self.read_graph();

        // Collect function nodes with their petgraph NodeIndex
        let mut funcs_pg: Vec<(NodeIndex, &CodeNode)> = graph
            .node_indices()
            .filter_map(|idx| {
                let node = graph.node_weight(idx)?;
                if node.kind == NodeKind::Function {
                    Some((idx, node))
                } else {
                    None
                }
            })
            .collect();

        // Sort by qualified_name to match get_functions() ordering
        let si = self.interner();
        funcs_pg.sort_by_cached_key(|item| si.resolve(item.1.qualified_name).to_owned());

        // petgraph NodeIndex -> function list position
        let func_count = funcs_pg.len();
        let pg_to_func: HashMap<NodeIndex, usize> = {
            let mut m = HashMap::with_capacity(func_count);
            m.extend(
                funcs_pg
                    .iter()
                    .enumerate()
                    .map(|(i, (pg_idx, _))| (*pg_idx, i)),
            );
            m
        };

        // qn -> function list index
        let qn_to_idx: HashMap<StrKey, usize> = {
            let mut m = HashMap::with_capacity(func_count);
            m.extend(
                funcs_pg
                    .iter()
                    .enumerate()
                    .map(|(i, (_, node))| (node.qualified_name, i)),
            );
            m
        };

        // Build callers/callees from call edges — zero String allocation
        let mut callers: HashMap<usize, Vec<usize>> = HashMap::with_capacity(func_count / 2);
        let mut callees: HashMap<usize, Vec<usize>> = HashMap::with_capacity(func_count / 2);

        for edge in graph.edge_references() {
            if edge.weight().kind == EdgeKind::Calls {
                if let (Some(&from), Some(&to)) = (
                    pg_to_func.get(&edge.source()),
                    pg_to_func.get(&edge.target()),
                ) {
                    callers.entry(to).or_default().push(from);
                    callees.entry(from).or_default().push(to);
                }
            }
        }

        let result = (qn_to_idx, callers, callees);
        // Cache for subsequent calls (postprocess FP filter reuse)
        let mut guard = self.call_maps_cache.write()
            .expect("call_maps_cache lock poisoned");
        *guard = Some(result.clone());
        result
    }

    /// Get all edges in the graph as (source_qn, dest_qn, edge_kind) tuples.
    /// Results are sorted for deterministic comparison in tests.
    /// Returns resolved strings for output/test compatibility.
    pub fn get_all_edges(&self) -> Vec<(String, String, EdgeKind)> {
        let graph = self.read_graph();
        let mut edges: Vec<_> = graph
            .edge_references()
            .filter_map(|e| {
                let src_node = graph.node_weight(e.source())?;
                let dst_node = graph.node_weight(e.target())?;
                Some((
                    self.interner().resolve(src_node.qualified_name).to_string(),
                    self.interner().resolve(dst_node.qualified_name).to_string(),
                    e.weight().kind,
                ))
            })
            .collect();
        edges.sort_unstable_by(|a, b| a.0.cmp(&b.0).then_with(|| a.1.cmp(&b.1)));
        edges
    }

    /// Compute coupling stats directly from the graph without materializing string pairs.
    /// Returns (total_call_count, cross_module_call_count) where "module" is the
    /// parent directory of each function's file_path.
    pub fn compute_coupling_stats(&self) -> (usize, usize) {
        let graph = self.read_graph();
        let mut total = 0usize;
        let mut cross_module = 0usize;

        for e in graph.edge_references() {
            if e.weight().kind != EdgeKind::Calls {
                continue;
            }
            total += 1;
            if let (Some(src), Some(dst)) = (graph.node_weight(e.source()), graph.node_weight(e.target())) {
                let src_path = self.interner().resolve(src.file_path);
                let dst_path = self.interner().resolve(dst.file_path);
                let src_mod = std::path::Path::new(src_path).parent();
                let dst_mod = std::path::Path::new(dst_path).parent();
                if src_mod != dst_mod {
                    cross_module += 1;
                }
            }
        }
        (total, cross_module)
    }

    /// Get all import edges (file -> file)
    pub fn get_imports(&self) -> Vec<(StrKey, StrKey)> {
        self.get_edges_by_kind(EdgeKind::Imports)
    }

    /// Get all call edges (function -> function)
    pub fn get_calls(&self) -> Vec<(StrKey, StrKey)> {
        self.get_edges_by_kind(EdgeKind::Calls)
    }

    /// Get all inheritance edges (child -> parent)
    pub fn get_inheritance(&self) -> Vec<(StrKey, StrKey)> {
        self.get_edges_by_kind(EdgeKind::Inherits)
    }

    /// Get callers of a function (who calls this?) — sorted by qualified_name for determinism
    pub fn get_callers(&self, qn: &str) -> Vec<CodeNode> {
        let key = self.interner().intern(qn);
        let idx = match self.node_index.get(&key).map(|r| *r) {
            Some(idx) => idx,
            None => return vec![],
        };
        let graph = self.read_graph();
        let mut nodes: Vec<CodeNode> = graph
            .edges_directed(idx, Direction::Incoming)
            .filter(|e| e.weight().kind == EdgeKind::Calls)
            .filter_map(|e| graph.node_weight(e.source()).copied())
            .collect();
        let si = self.interner();
        nodes.sort_unstable_by(|a, b| si.resolve(a.qualified_name).cmp(si.resolve(b.qualified_name)));
        nodes
    }

    /// Get callees of a function (what does this call?) — sorted by qualified_name for determinism
    pub fn get_callees(&self, qn: &str) -> Vec<CodeNode> {
        let key = self.interner().intern(qn);
        let idx = match self.node_index.get(&key).map(|r| *r) {
            Some(idx) => idx,
            None => return vec![],
        };
        let graph = self.read_graph();
        let mut nodes: Vec<CodeNode> = graph
            .edges_directed(idx, Direction::Outgoing)
            .filter(|e| e.weight().kind == EdgeKind::Calls)
            .filter_map(|e| graph.node_weight(e.target()).copied())
            .collect();
        let si = self.interner();
        nodes.sort_unstable_by(|a, b| si.resolve(a.qualified_name).cmp(si.resolve(b.qualified_name)));
        nodes
    }

    /// Get importers of a module/class (who imports this?) — sorted by qualified_name for determinism
    pub fn get_importers(&self, qn: &str) -> Vec<CodeNode> {
        let key = self.interner().intern(qn);
        let idx = match self.node_index.get(&key).map(|r| *r) {
            Some(idx) => idx,
            None => return vec![],
        };
        let graph = self.read_graph();
        let mut nodes: Vec<CodeNode> = graph
            .edges_directed(idx, Direction::Incoming)
            .filter(|e| e.weight().kind == EdgeKind::Imports)
            .filter_map(|e| graph.node_weight(e.source()).copied())
            .collect();
        let si = self.interner();
        nodes.sort_unstable_by(|a, b| si.resolve(a.qualified_name).cmp(si.resolve(b.qualified_name)));
        nodes
    }

    /// Get parent classes (what does this inherit from?) — sorted by qualified_name for determinism
    pub fn get_parent_classes(&self, qn: &str) -> Vec<CodeNode> {
        let key = self.interner().intern(qn);
        let idx = match self.node_index.get(&key).map(|r| *r) {
            Some(idx) => idx,
            None => return vec![],
        };
        let graph = self.read_graph();
        let mut nodes: Vec<CodeNode> = graph
            .edges_directed(idx, Direction::Outgoing)
            .filter(|e| e.weight().kind == EdgeKind::Inherits)
            .filter_map(|e| graph.node_weight(e.target()).copied())
            .collect();
        let si = self.interner();
        nodes.sort_unstable_by(|a, b| si.resolve(a.qualified_name).cmp(si.resolve(b.qualified_name)));
        nodes
    }

    /// Get child classes (what inherits from this?) — sorted by qualified_name for determinism
    pub fn get_child_classes(&self, qn: &str) -> Vec<CodeNode> {
        let key = self.interner().intern(qn);
        let idx = match self.node_index.get(&key).map(|r| *r) {
            Some(idx) => idx,
            None => return vec![],
        };
        let graph = self.read_graph();
        let mut nodes: Vec<CodeNode> = graph
            .edges_directed(idx, Direction::Incoming)
            .filter(|e| e.weight().kind == EdgeKind::Inherits)
            .filter_map(|e| graph.node_weight(e.source()).copied())
            .collect();
        let si = self.interner();
        nodes.sort_unstable_by(|a, b| si.resolve(a.qualified_name).cmp(si.resolve(b.qualified_name)));
        nodes
    }

    // ==================== Graph Metrics ====================

    /// Get in-degree (fan-in) for a node
    pub fn fan_in(&self, qn: &str) -> usize {
        let key = self.interner().intern(qn);
        let idx = match self.node_index.get(&key).map(|r| *r) {
            Some(idx) => idx,
            None => return 0,
        };
        let graph = self.read_graph();
        graph.edges_directed(idx, Direction::Incoming).count()
    }

    /// Get out-degree (fan-out) for a node
    pub fn fan_out(&self, qn: &str) -> usize {
        let key = self.interner().intern(qn);
        let idx = match self.node_index.get(&key).map(|r| *r) {
            Some(idx) => idx,
            None => return 0,
        };
        let graph = self.read_graph();
        graph.edges_directed(idx, Direction::Outgoing).count()
    }

    /// Get call fan-in (how many functions call this?)
    pub fn call_fan_in(&self, qn: &str) -> usize {
        let key = self.interner().intern(qn);
        let idx = match self.node_index.get(&key).map(|r| *r) {
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
        let key = self.interner().intern(qn);
        let idx = match self.node_index.get(&key).map(|r| *r) {
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

    /// Get statistics (BTreeMap for deterministic key order)
    pub fn stats(&self) -> BTreeMap<String, i64> {
        let graph = self.read_graph();
        let mut stats = BTreeMap::new();

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
        let mut filtered_graph: StableGraph<NodeIndex, ()> = StableGraph::new();
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
                if edge_kind == EdgeKind::Imports && e.weight().is_type_only() {
                    return false;
                }
                true
            })
            .flat_map(|e| [e.source(), e.target()])
            .collect();

        // Sort by NodeIndex for deterministic filtered-graph construction
        let mut sorted_nodes: Vec<NodeIndex> = relevant_nodes.into_iter().collect();
        sorted_nodes.sort_by_key(|idx| idx.index());

        for orig_idx in sorted_nodes {
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
            if edge_kind == EdgeKind::Imports && edge.weight().is_type_only() {
                continue;
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
                    .map(|n| self.interner().resolve(n.qualified_name).to_string())
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
        let start_key = self.interner().intern(start_qn);
        let start_idx = self.node_index.get(&start_key).map(|r| *r)?;
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
                if edge_kind == EdgeKind::Imports && edge.weight().is_type_only() {
                    continue;
                }

                let target = edge.target();

                // Found cycle back to start!
                if target == start_idx && path.len() > 1 {
                    return Some(
                        path.iter()
                            .filter_map(|&idx| graph.node_weight(idx))
                            .map(|n| self.interner().resolve(n.qualified_name).to_string())
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

            // Save nodes — resolve StrKeys to strings for serialization
            for node in graph.node_weights() {
                let qn_str = self.interner().resolve(node.qualified_name);
                let key = format!("node:{}", qn_str);
                // Serialize as intermediate JSON with resolved strings
                let node_data = serde_json::json!({
                    "kind": node.kind,
                    "name": self.interner().resolve(node.name),
                    "qualified_name": qn_str,
                    "file_path": self.interner().resolve(node.file_path),
                    "language": self.interner().resolve(node.language),
                    "line_start": node.line_start,
                    "line_end": node.line_end,
                    "complexity": node.complexity,
                    "param_count": node.param_count,
                    "method_count": node.method_count,
                    "max_nesting": node.max_nesting,
                    "return_count": node.return_count,
                    "commit_count": node.commit_count,
                    "flags": node.flags,
                });
                let value = serde_json::to_vec(&node_data)?;
                table.insert(key.as_str(), value.as_slice())?;
            }

            // Save edges as a single entry — resolve StrKeys for serialization
            let edges: Vec<_> = graph
                .edge_references()
                .filter_map(|e| {
                    let src = graph.node_weight(e.source())?;
                    let dst = graph.node_weight(e.target())?;
                    Some((
                        self.interner().resolve(src.qualified_name).to_string(),
                        self.interner().resolve(dst.qualified_name).to_string(),
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

        // Load nodes — intern strings from serialized data
        for item in nodes_table.range::<&str>(..)? {
            let (key, value) = item?;
            let key_str = key.value();
            if key_str.starts_with("node:") {
                let data: serde_json::Value = serde_json::from_slice(value.value())?;
                let kind: NodeKind = serde_json::from_value(data["kind"].clone())?;
                let empty_key = self.interner().empty_key();
                let mut node = CodeNode::empty(kind, empty_key);
                node.name = self.interner().intern(data["name"].as_str().unwrap_or(""));
                node.qualified_name = self.interner().intern(data["qualified_name"].as_str().unwrap_or(""));
                node.file_path = self.interner().intern(data["file_path"].as_str().unwrap_or(""));
                node.language = self.interner().intern(data["language"].as_str().unwrap_or(""));
                node.line_start = data["line_start"].as_u64().unwrap_or(0) as u32;
                node.line_end = data["line_end"].as_u64().unwrap_or(0) as u32;
                node.complexity = data["complexity"].as_u64().unwrap_or(0) as u16;
                node.param_count = data["param_count"].as_u64().unwrap_or(0) as u8;
                node.method_count = data["method_count"].as_u64().unwrap_or(0) as u16;
                node.max_nesting = data["max_nesting"].as_u64().unwrap_or(0) as u8;
                node.return_count = data["return_count"].as_u64().unwrap_or(0) as u8;
                node.commit_count = data["commit_count"].as_u64().unwrap_or(0) as u16;
                node.flags = data["flags"].as_u64().unwrap_or(0) as u8;

                let qn = node.qualified_name;
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
            let mut set = self.edge_set.lock().expect("edge_set lock poisoned");
            for (src_qn, dst_qn, edge) in edges {
                let src_key = self.interner().intern(&src_qn);
                let dst_key = self.interner().intern(&dst_qn);
                let src = self.node_index.get(&src_key).map(|r| *r);
                let dst = self.node_index.get(&dst_key).map(|r| *r);
                if let (Some(src), Some(dst)) = (src, dst) {
                    set.insert((src, dst, edge.kind));
                    graph.add_edge(src, dst, edge);
                }
            }
        }

        Ok(())
    }

    // ==================== Delta Patching ====================

    /// Remove all nodes and edges belonging to a set of files.
    /// Used for delta patching: remove stale data before re-parsing changed files.
    ///
    /// StableGraph allows safe node removal without invalidating other indexes.
    pub fn remove_file_entities(&self, files: &[std::path::PathBuf]) {
        let mut graph = self.write_graph();
        let mut edge_set = self.edge_set.lock().unwrap();

        for file in files {
            let file_str = file.to_string_lossy();
            let file_key = self.interner().intern(file_str.as_ref());

            // Get all nodes in this file from the reverse index
            let node_idxs: Vec<NodeIndex> = self
                .file_all_nodes_index
                .remove(&file_key)
                .map(|(_, v)| v)
                .unwrap_or_default();

            for idx in &node_idxs {
                // Collect all edges (outgoing and incoming) connected to this node
                let mut edge_ids: Vec<_> = graph
                    .edges_directed(*idx, Direction::Outgoing)
                    .map(|e| e.id())
                    .collect();
                let incoming: Vec<_> = graph
                    .edges_directed(*idx, Direction::Incoming)
                    .map(|e| e.id())
                    .collect();
                edge_ids.extend(incoming);
                edge_ids.sort();
                edge_ids.dedup();

                // Remove edges from edge_set and graph
                for eid in edge_ids {
                    if let Some((src, tgt)) = graph.edge_endpoints(eid) {
                        if let Some(edge) = graph.edge_weight(eid) {
                            edge_set.remove(&(src, tgt, edge.kind));
                        }
                    }
                    graph.remove_edge(eid);
                }

                // Remove metrics_cache entries for this node's qualified name
                if let Some(node) = graph.node_weight(*idx) {
                    let qn_str = self.interner().resolve(node.qualified_name);
                    let suffix = format!(":{}", qn_str);
                    self.metrics_cache.retain(|k, _| !k.ends_with(&suffix));
                }

                // Remove the node from QN index
                if let Some(node) = graph.node_weight(*idx) {
                    self.node_index.remove(&node.qualified_name);
                }

                // Remove the node from graph (StableGraph handles this safely)
                graph.remove_node(*idx);
            }

            // Clean up file-scoped indexes
            self.file_functions_index.remove(&file_key);
            self.file_classes_index.remove(&file_key);
            self.function_spatial_index.remove(&file_key);
        }

        // Invalidate call maps cache — stale after node/edge removal
        {
            let mut guard = self.call_maps_cache.write()
                .expect("call_maps_cache lock poisoned");
            *guard = None;
        }
    }

    // ==================== Graph Cache (bincode) ====================

    /// Save the in-memory graph to a bincode cache file for fast reload.
    ///
    /// The RwLock read guard is dropped immediately after cloning the graph,
    /// so serialization and I/O happen without holding the lock. The file is
    /// written atomically via write-to-temp-then-rename so a crash mid-write
    /// never leaves a corrupt cache on disk.
    pub fn save_graph_cache(&self, cache_path: &std::path::Path) -> Result<()> {
        // Clone graph under lock, then drop the guard immediately.
        // The temporary RwLockReadGuard is dropped at the end of this statement.
        let graph_clone = self.read_graph().clone();

        // DashMap iteration doesn't hold the graph RwLock
        // Resolve StrKeys to strings for serialization
        let node_index: HashMap<String, NodeIndex> = self.node_index.iter()
            .map(|entry| (self.interner().resolve(*entry.key()).to_string(), *entry.value()))
            .collect();
        let file_all_nodes: HashMap<String, Vec<NodeIndex>> = self.file_all_nodes_index.iter()
            .map(|entry| (self.interner().resolve(*entry.key()).to_string(), entry.value().clone()))
            .collect();

        // Build string table: raw Spur u32 → interned string for all StrKeys in nodes
        let i = self.interner();
        let mut string_table: HashMap<u32, String> = HashMap::new();
        for node in graph_clone.node_weights() {
            for &key in &[node.name, node.qualified_name, node.file_path, node.language] {
                let raw = key.into_inner().get();
                string_table.entry(raw).or_insert_with(|| i.resolve(key).to_string());
            }
        }

        // Serialize ExtraProps with resolved strings (StrKeys are process-local)
        let extra_props_ser: Vec<(String, SerializableExtraProps)> = self.extra_props.iter()
            .map(|entry| {
                let qn_str = i.resolve(*entry.key()).to_string();
                let ep = entry.value();
                let ser = SerializableExtraProps {
                    params: ep.params.map(|k| i.resolve(k).to_string()),
                    doc_comment: ep.doc_comment.map(|k| i.resolve(k).to_string()),
                    decorators: ep.decorators.map(|k| i.resolve(k).to_string()),
                    author: ep.author.map(|k| i.resolve(k).to_string()),
                    last_modified: ep.last_modified.map(|k| i.resolve(k).to_string()),
                };
                (qn_str, ser)
            })
            .collect();

        let cache = GraphCache {
            version: GRAPH_CACHE_VERSION,
            binary_version: env!("CARGO_PKG_VERSION").to_string(),
            graph: graph_clone,
            node_index,
            file_all_nodes,
            string_table,
            extra_props: extra_props_ser,
        };

        // Serialize and write — no lock held
        let bytes = bincode::serialize(&cache)
            .context("Failed to serialize graph cache")?;

        if let Some(parent) = cache_path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        // Atomic write: write to .tmp then rename, so a crash mid-write
        // never leaves a corrupt cache file.
        let tmp_path = cache_path.with_extension("bin.tmp");
        std::fs::write(&tmp_path, &bytes)
            .context("Failed to write graph cache")?;
        std::fs::rename(&tmp_path, cache_path)
            .context("Failed to finalize graph cache")?;

        Ok(())
    }

    /// Load a graph from a bincode cache file, rebuilding all indexes.
    /// Returns None if cache is missing, corrupt, or version-mismatched.
    pub fn load_graph_cache(cache_path: &std::path::Path) -> Option<Self> {
        let bytes = std::fs::read(cache_path).ok()?;
        let cache: GraphCache = bincode::deserialize(&bytes).ok()?;

        // Version check
        if cache.version != GRAPH_CACHE_VERSION
            || cache.binary_version != env!("CARGO_PKG_VERSION")
        {
            tracing::info!("Graph cache version mismatch, rebuilding");
            return None;
        }

        let store = Self {
            graph: RwLock::new(cache.graph),
            node_index: DashMap::new(),
            function_spatial_index: DashMap::new(),
            file_functions_index: DashMap::new(),
            file_classes_index: DashMap::new(),
            file_all_nodes_index: DashMap::new(),
            metrics_cache: DashMap::new(),

            extra_props: DashMap::new(),
            edge_set: Mutex::new(HashSet::new()),
            call_maps_cache: RwLock::new(None),
            db: None,
            db_path: None,
            lazy_mode: false,
        };

        // Re-intern StrKeys from the string table: old raw u32 → new StrKey
        let i = store.interner();
        let remap: HashMap<u32, StrKey> = cache.string_table.iter()
            .map(|(&raw, s)| (raw, i.intern(s)))
            .collect();

        // Remap all CodeNode StrKey fields in the deserialized graph
        {
            let mut graph = store.write_graph();
            for idx in graph.node_indices().collect::<Vec<_>>() {
                if let Some(node) = graph.node_weight_mut(idx) {
                    if let Some(&new) = remap.get(&node.name.into_inner().get()) {
                        node.name = new;
                    }
                    if let Some(&new) = remap.get(&node.qualified_name.into_inner().get()) {
                        node.qualified_name = new;
                    }
                    if let Some(&new) = remap.get(&node.file_path.into_inner().get()) {
                        node.file_path = new;
                    }
                    if let Some(&new) = remap.get(&node.language.into_inner().get()) {
                        node.language = new;
                    }
                }
            }
        }

        // Rebuild DashMap indexes from cached data (intern strings back to StrKeys)
        for (key_str, idx) in cache.node_index {
            let key = i.intern(&key_str);
            store.node_index.insert(key, idx);
        }
        for (file_str, nodes) in cache.file_all_nodes {
            let key = i.intern(&file_str);
            store.file_all_nodes_index.insert(key, nodes);
        }

        // Rebuild ExtraProps from serialized string values
        for (qn_str, ser) in cache.extra_props {
            let qn_key = i.intern(&qn_str);
            let ep = ExtraProps {
                params: ser.params.as_deref().map(|s| i.intern(s)),
                doc_comment: ser.doc_comment.as_deref().map(|s| i.intern(s)),
                decorators: ser.decorators.as_deref().map(|s| i.intern(s)),
                author: ser.author.as_deref().map(|s| i.intern(s)),
                last_modified: ser.last_modified.as_deref().map(|s| i.intern(s)),
            };
            store.extra_props.insert(qn_key, ep);
        }

        // Rebuild file_functions_index, file_classes_index, and spatial_index from graph
        {
            let graph = store.read_graph();
            for idx in graph.node_indices() {
                if let Some(node) = graph.node_weight(idx) {
                    match node.kind {
                        NodeKind::Function => {
                            store.file_functions_index
                                .entry(node.file_path)
                                .or_default()
                                .push(idx);
                            store.function_spatial_index
                                .entry(node.file_path)
                                .or_default()
                                .push((node.line_start, node.line_end, idx));
                        }
                        NodeKind::Class => {
                            store.file_classes_index
                                .entry(node.file_path)
                                .or_default()
                                .push(idx);
                        }
                        _ => {}
                    }
                }
            }
        }

        // Rebuild edge_set from graph edges
        {
            let graph = store.read_graph();
            let mut edge_set = store.edge_set.lock().unwrap();
            for edge_ref in graph.edge_references() {
                edge_set.insert((edge_ref.source(), edge_ref.target(), edge_ref.weight().kind));
            }
        }

        tracing::info!("Loaded graph cache ({} nodes)", store.node_index.len());
        Some(store)
    }

    /// Compute a fingerprint of all cross-file edges. Used to detect topology changes
    /// for incremental analysis — if this value changes, GraphWide detectors must re-run.
    pub fn compute_edge_fingerprint(&self) -> u64 {
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};

        let graph = self.graph.read().unwrap();
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
                    src.qualified_name.into_inner().get(),
                    tgt.qualified_name.into_inner().get(),
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
}

/// Serializable graph cache for persistent storage between runs.
#[derive(serde::Serialize, serde::Deserialize)]
struct GraphCache {
    version: u32,
    binary_version: String,
    graph: StableGraph<CodeNode, CodeEdge>,
    node_index: HashMap<String, NodeIndex>,
    file_all_nodes: HashMap<String, Vec<NodeIndex>>,
    /// Maps raw Spur u32 values to their interned strings, enabling cross-process
    /// re-interning of StrKey fields in deserialized CodeNode structs.
    string_table: HashMap<u32, String>,
    /// ExtraProps serialized with string values (not StrKeys) for cross-process safety.
    extra_props: Vec<(String, SerializableExtraProps)>,
}

/// ExtraProps with string values for serialization (StrKeys are process-local).
#[derive(serde::Serialize, serde::Deserialize)]
struct SerializableExtraProps {
    params: Option<String>,
    doc_comment: Option<String>,
    decorators: Option<String>,
    author: Option<String>,
    last_modified: Option<String>,
}

const GRAPH_CACHE_VERSION: u32 = 3; // Bumped for string_table + ExtraProps persistence

// redb::Database handles cleanup on Drop automatically — no manual flush needed

// GraphQuery trait implementation lives in graph/store_query.rs

#[cfg(test)]
mod tests;
