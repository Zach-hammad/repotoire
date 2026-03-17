//! Backward-compatible bridge methods for CodeGraph.
//!
//! These methods match the old `GraphStore` API surface and delegate to the new
//! indexed methods on `CodeGraph`. Each is marked `#[deprecated]` with a note
//! pointing to the replacement API.
//!
//! These bridges enable gradual migration: existing consumers continue to work
//! unchanged while being updated one file at a time. Once all consumers use the
//! new API, these bridges will be deleted.

use petgraph::stable_graph::NodeIndex;
use petgraph::visit::{EdgeRef, IntoEdgeReferences};
use petgraph::Direction;
use std::collections::HashMap;
use std::sync::Arc;

use super::frozen::CodeGraph;
use super::interner::StrKey;
use super::store_models::{CodeNode, EdgeKind, ExtraProps};

#[allow(deprecated)]
impl CodeGraph {
    // ==================== Kind-Based Node Queries ====================

    /// Get all functions as owned Vec. Use `functions()` + `node()` instead.
    #[deprecated(note = "Use functions() + node() instead")]
    pub fn get_functions_compat(&self) -> Vec<CodeNode> {
        self.functions()
            .iter()
            .filter_map(|&idx| self.node(idx).copied())
            .collect()
    }

    /// Get all classes as owned Vec. Use `classes()` + `node()` instead.
    #[deprecated(note = "Use classes() + node() instead")]
    pub fn get_classes_compat(&self) -> Vec<CodeNode> {
        self.classes()
            .iter()
            .filter_map(|&idx| self.node(idx).copied())
            .collect()
    }

    /// Get all files as owned Vec. Use `files()` + `node()` instead.
    #[deprecated(note = "Use files() + node() instead")]
    pub fn get_files_compat(&self) -> Vec<CodeNode> {
        self.files()
            .iter()
            .filter_map(|&idx| self.node(idx).copied())
            .collect()
    }

    /// Get all functions as shared Arc. Use `functions()` + `node()` instead.
    #[deprecated(note = "Use functions() + node() instead")]
    pub fn get_functions_shared_compat(&self) -> Arc<[CodeNode]> {
        Arc::from(
            self.functions()
                .iter()
                .filter_map(|&idx| self.node(idx).copied())
                .collect::<Vec<_>>(),
        )
    }

    /// Get all classes as shared Arc. Use `classes()` + `node()` instead.
    #[deprecated(note = "Use classes() + node() instead")]
    pub fn get_classes_shared_compat(&self) -> Arc<[CodeNode]> {
        Arc::from(
            self.classes()
                .iter()
                .filter_map(|&idx| self.node(idx).copied())
                .collect::<Vec<_>>(),
        )
    }

    /// Get all files as shared Arc. Use `files()` + `node()` instead.
    #[deprecated(note = "Use files() + node() instead")]
    pub fn get_files_shared_compat(&self) -> Arc<[CodeNode]> {
        Arc::from(
            self.files()
                .iter()
                .filter_map(|&idx| self.node(idx).copied())
                .collect::<Vec<_>>(),
        )
    }

    // ==================== Node Lookup ====================

    /// Get node by qualified name. Use `node_by_name()` instead.
    #[deprecated(note = "Use node_by_name() instead")]
    pub fn get_node_compat(&self, qn: &str) -> Option<CodeNode> {
        self.node_by_name(qn).map(|(_, node)| *node)
    }

    /// Get node index by qualified name. Use `node_by_name()` instead.
    #[deprecated(note = "Use node_by_name() instead")]
    pub fn get_node_index_compat(&self, qn: &str) -> Option<NodeIndex> {
        self.node_by_name(qn).map(|(idx, _)| idx)
    }

    // ==================== Adjacency Queries ====================

    /// Get callers as owned Vec<CodeNode>. Use `node_by_name()` + `callers()` + `node()`.
    #[deprecated(note = "Use node_by_name() + callers() + node() instead")]
    pub fn get_callers_compat(&self, qn: &str) -> Vec<CodeNode> {
        let Some((idx, _)) = self.node_by_name(qn) else {
            return vec![];
        };
        self.callers(idx)
            .iter()
            .filter_map(|&ci| self.node(ci).copied())
            .collect()
    }

    /// Get callees as owned Vec<CodeNode>. Use `node_by_name()` + `callees()` + `node()`.
    #[deprecated(note = "Use node_by_name() + callees() + node() instead")]
    pub fn get_callees_compat(&self, qn: &str) -> Vec<CodeNode> {
        let Some((idx, _)) = self.node_by_name(qn) else {
            return vec![];
        };
        self.callees(idx)
            .iter()
            .filter_map(|&ci| self.node(ci).copied())
            .collect()
    }

    /// Get importers as owned Vec<CodeNode>. Use `node_by_name()` + `importers()` + `node()`.
    #[deprecated(note = "Use node_by_name() + importers() + node() instead")]
    pub fn get_importers_compat(&self, qn: &str) -> Vec<CodeNode> {
        let Some((idx, _)) = self.node_by_name(qn) else {
            return vec![];
        };
        self.importers(idx)
            .iter()
            .filter_map(|&ci| self.node(ci).copied())
            .collect()
    }

    /// Get child classes as owned Vec<CodeNode>. Use `node_by_name()` + `child_classes()` + `node()`.
    #[deprecated(note = "Use node_by_name() + child_classes() + node() instead")]
    pub fn get_child_classes_compat(&self, qn: &str) -> Vec<CodeNode> {
        let Some((idx, _)) = self.node_by_name(qn) else {
            return vec![];
        };
        self.child_classes(idx)
            .iter()
            .filter_map(|&ci| self.node(ci).copied())
            .collect()
    }

    // ==================== Fan-In/Fan-Out ====================

    /// Call fan-in by QN. Use `node_by_name()` + `call_fan_in(idx)`.
    #[deprecated(note = "Use node_by_name() + call_fan_in(idx) instead")]
    pub fn call_fan_in_by_name(&self, qn: &str) -> usize {
        self.node_by_name(qn)
            .map(|(idx, _)| self.call_fan_in(idx))
            .unwrap_or(0)
    }

    /// Call fan-out by QN. Use `node_by_name()` + `call_fan_out(idx)`.
    #[deprecated(note = "Use node_by_name() + call_fan_out(idx) instead")]
    pub fn call_fan_out_by_name(&self, qn: &str) -> usize {
        self.node_by_name(qn)
            .map(|(idx, _)| self.call_fan_out(idx))
            .unwrap_or(0)
    }

    // ==================== File-Scoped Queries ====================

    /// Get functions in file as owned Vec. Use `functions_in_file()` + `node()`.
    #[deprecated(note = "Use functions_in_file() + node() instead")]
    pub fn get_functions_in_file_compat(&self, file_path: &str) -> Vec<CodeNode> {
        self.functions_in_file(file_path)
            .iter()
            .filter_map(|&idx| self.node(idx).copied())
            .collect()
    }

    /// Get classes in file as owned Vec. Use `classes_in_file()` + `node()`.
    #[deprecated(note = "Use classes_in_file() + node() instead")]
    pub fn get_classes_in_file_compat(&self, file_path: &str) -> Vec<CodeNode> {
        self.classes_in_file(file_path)
            .iter()
            .filter_map(|&idx| self.node(idx).copied())
            .collect()
    }

    /// Find function at line as owned CodeNode. Use `function_at()` + `node()`.
    #[deprecated(note = "Use function_at() + node() instead")]
    pub fn find_function_at_compat(&self, file_path: &str, line: u32) -> Option<CodeNode> {
        self.function_at(file_path, line)
            .and_then(|idx| self.node(idx).copied())
    }

    // ==================== Bulk Edge Queries ====================

    /// Get all call edges as (StrKey, StrKey) pairs. Use `all_call_edges()` + `node()`.
    #[deprecated(note = "Use all_call_edges() + node() instead")]
    pub fn get_calls_compat(&self) -> Vec<(StrKey, StrKey)> {
        self.all_call_edges()
            .iter()
            .filter_map(|&(src, tgt)| {
                let s = self.node(src)?;
                let t = self.node(tgt)?;
                Some((s.qualified_name, t.qualified_name))
            })
            .collect()
    }

    /// Get all call edges as shared Arc.
    #[deprecated(note = "Use all_call_edges() + node() instead")]
    pub fn get_calls_shared_compat(&self) -> Arc<[(StrKey, StrKey)]> {
        #[allow(deprecated)]
        Arc::from(self.get_calls_compat())
    }

    /// Get all import edges as (StrKey, StrKey) pairs.
    #[deprecated(note = "Use all_import_edges() + node() instead")]
    pub fn get_imports_compat(&self) -> Vec<(StrKey, StrKey)> {
        self.all_import_edges()
            .iter()
            .filter_map(|&(src, tgt)| {
                let s = self.node(src)?;
                let t = self.node(tgt)?;
                Some((s.qualified_name, t.qualified_name))
            })
            .collect()
    }

    /// Get all inheritance edges as (StrKey, StrKey) pairs.
    #[deprecated(note = "Use all_inheritance_edges() + node() instead")]
    pub fn get_inheritance_compat(&self) -> Vec<(StrKey, StrKey)> {
        self.all_inheritance_edges()
            .iter()
            .filter_map(|&(src, tgt)| {
                let s = self.node(src)?;
                let t = self.node(tgt)?;
                Some((s.qualified_name, t.qualified_name))
            })
            .collect()
    }

    // ==================== Call Maps ====================

    /// Build call maps as (qn_to_idx, callers_idx, callees_idx).
    /// Use `callers()` / `callees()` directly instead.
    #[deprecated(note = "Use callers()/callees() directly instead")]
    pub fn build_call_maps_raw_compat(
        &self,
    ) -> (
        HashMap<StrKey, usize>,
        HashMap<usize, Vec<usize>>,
        HashMap<usize, Vec<usize>>,
    ) {
        let functions = self.functions();
        let qn_to_idx: HashMap<StrKey, usize> = functions
            .iter()
            .enumerate()
            .filter_map(|(i, &idx)| {
                let node = self.node(idx)?;
                Some((node.qualified_name, i))
            })
            .collect();

        let mut callers_map: HashMap<usize, Vec<usize>> = HashMap::new();
        let mut callees_map: HashMap<usize, Vec<usize>> = HashMap::new();

        for (func_list_idx, &node_idx) in functions.iter().enumerate() {
            for &callee_idx in self.callees(node_idx) {
                if let Some(node) = self.node(callee_idx) {
                    if let Some(&callee_list_idx) = qn_to_idx.get(&node.qualified_name) {
                        callees_map
                            .entry(func_list_idx)
                            .or_default()
                            .push(callee_list_idx);
                        callers_map
                            .entry(callee_list_idx)
                            .or_default()
                            .push(func_list_idx);
                    }
                }
            }
        }

        (qn_to_idx, callers_map, callees_map)
    }

    /// Build call adjacency lists.
    #[deprecated(note = "Use callers()/callees() directly instead")]
    pub fn get_call_adjacency_compat(
        &self,
    ) -> (Vec<Vec<usize>>, Vec<Vec<usize>>, HashMap<StrKey, usize>) {
        let functions = self.functions();
        let n = functions.len();
        let qn_to_idx: HashMap<StrKey, usize> = functions
            .iter()
            .enumerate()
            .filter_map(|(i, &idx)| {
                let node = self.node(idx)?;
                Some((node.qualified_name, i))
            })
            .collect();

        let mut adj = vec![vec![]; n];
        let mut rev_adj = vec![vec![]; n];

        for (func_list_idx, &node_idx) in functions.iter().enumerate() {
            for &callee_idx in self.callees(node_idx) {
                if let Some(node) = self.node(callee_idx) {
                    if let Some(&callee_list_idx) = qn_to_idx.get(&node.qualified_name) {
                        adj[func_list_idx].push(callee_list_idx);
                        rev_adj[callee_list_idx].push(func_list_idx);
                    }
                }
            }
        }

        (adj, rev_adj, qn_to_idx)
    }

    // ==================== Import Cycles ====================

    /// Find import cycles as Vec<Vec<String>>. Use `import_cycles()` + node resolution.
    #[deprecated(note = "Use import_cycles() and resolve NodeIndexes instead")]
    pub fn find_import_cycles_compat(&self) -> Vec<Vec<String>> {
        let si = self.interner();
        self.import_cycles()
            .iter()
            .map(|cycle| {
                let mut names: Vec<String> = cycle
                    .iter()
                    .filter_map(|&idx| self.node(idx))
                    .map(|n| si.resolve(n.qualified_name).to_string())
                    .collect();
                names.sort();
                names
            })
            .collect()
    }

    /// Check if a file participates in any import cycle.
    #[deprecated(note = "Use import_cycles() and check manually")]
    pub fn is_in_import_cycle_compat(&self, file_path: &str) -> bool {
        let si = self.interner();
        self.import_cycles().iter().any(|cycle| {
            cycle.iter().any(|&idx| {
                if let Some(node) = self.node(idx) {
                    let qn = si.resolve(node.qualified_name);
                    qn == file_path || file_path.contains(qn)
                } else {
                    false
                }
            })
        })
    }

    // ==================== Edge Fingerprint ====================

    /// Compute edge fingerprint. Use `edge_fingerprint()` (pre-computed).
    #[deprecated(note = "Use edge_fingerprint() instead (pre-computed during freeze)")]
    pub fn compute_edge_fingerprint_compat(&self) -> u64 {
        self.edge_fingerprint()
    }

    // ==================== Coupling Stats ====================

    /// Compute coupling stats from the graph.
    /// Returns (total_call_count, cross_module_call_count).
    #[deprecated(note = "Compute from all_call_edges() + node() instead")]
    pub fn compute_coupling_stats_compat(&self) -> (usize, usize) {
        let si = self.interner();
        let mut total = 0usize;
        let mut cross_module = 0usize;

        for &(src_idx, tgt_idx) in self.all_call_edges() {
            total += 1;
            if let (Some(src), Some(dst)) = (self.node(src_idx), self.node(tgt_idx)) {
                let src_path = si.resolve(src.file_path);
                let dst_path = si.resolve(dst.file_path);
                let src_mod = std::path::Path::new(src_path).parent();
                let dst_mod = std::path::Path::new(dst_path).parent();
                if src_mod != dst_mod {
                    cross_module += 1;
                }
            }
        }
        (total, cross_module)
    }

    // ==================== Extra Props ====================

    /// Get extra props as owned value. Use `extra_props()` (returns reference).
    #[deprecated(note = "Use extra_props() which returns &ExtraProps instead")]
    pub fn get_extra_props_compat(&self, qn: StrKey) -> Option<ExtraProps> {
        self.extra_props(qn).cloned()
    }

    // ==================== Complex/Long Param Functions ====================

    /// Get complex functions. Use `functions()` + filter by `node().complexity`.
    #[deprecated(note = "Use functions() + filter by node().complexity instead")]
    pub fn get_complex_functions_compat(&self, min_complexity: i64) -> Vec<CodeNode> {
        self.functions()
            .iter()
            .filter_map(|&idx| {
                let node = self.node(idx)?;
                if node
                    .complexity_opt()
                    .is_some_and(|c| c >= min_complexity)
                {
                    Some(*node)
                } else {
                    None
                }
            })
            .collect()
    }

    /// Get functions with many parameters. Use `functions()` + filter by `node().param_count`.
    #[deprecated(note = "Use functions() + filter by node().param_count instead")]
    pub fn get_long_param_functions_compat(&self, min_params: i64) -> Vec<CodeNode> {
        self.functions()
            .iter()
            .filter_map(|&idx| {
                let node = self.node(idx)?;
                if node.param_count_opt().is_some_and(|p| p >= min_params) {
                    Some(*node)
                } else {
                    None
                }
            })
            .collect()
    }

    // ==================== Caller Analysis ====================

    /// Count unique files of callers. Use `callers()` + `node()` + unique file_paths.
    #[deprecated(note = "Use callers() + node() + unique file_paths instead")]
    pub fn caller_file_spread_compat(&self, qn: &str) -> usize {
        let Some((idx, _)) = self.node_by_name(qn) else {
            return 0;
        };
        let files: std::collections::HashSet<StrKey> = self
            .callers(idx)
            .iter()
            .filter_map(|&ci| self.node(ci))
            .map(|n| n.file_path)
            .collect();
        files.len()
    }

    /// Count unique modules of callers.
    #[deprecated(note = "Use callers() + node() + unique parent dirs instead")]
    pub fn caller_module_spread_compat(&self, qn: &str) -> usize {
        let si = self.interner();
        let Some((idx, _)) = self.node_by_name(qn) else {
            return 0;
        };
        let modules: std::collections::HashSet<&str> = self
            .callers(idx)
            .iter()
            .filter_map(|&ci| self.node(ci))
            .map(|n| {
                std::path::Path::new(si.resolve(n.file_path))
                    .parent()
                    .and_then(|p| p.to_str())
                    .unwrap_or("root")
            })
            .collect();
        modules.len()
    }

    /// Count callers that are outside a given class boundary.
    #[deprecated(note = "Use callers() + node() + filter by file/line instead")]
    pub fn count_external_callers_of_compat(
        &self,
        qn: &str,
        class_file: &str,
        class_start: u32,
        class_end: u32,
    ) -> usize {
        let si = self.interner();
        let Some((idx, _)) = self.node_by_name(qn) else {
            return 0;
        };
        self.callers(idx)
            .iter()
            .filter_map(|&ci| self.node(ci))
            .filter(|c| {
                si.resolve(c.file_path) != class_file
                    || c.line_start < class_start
                    || c.line_end > class_end
            })
            .count()
    }

    // ==================== Edge Queries ====================

    /// Get edges by kind as (StrKey, StrKey) pairs sorted for determinism.
    #[deprecated(note = "Use adjacency indexes or all_*_edges() instead")]
    pub fn get_edges_by_kind_compat(&self, kind: EdgeKind) -> Vec<(StrKey, StrKey)> {
        let si = self.interner();
        let bulk = match kind {
            EdgeKind::Calls => self.all_call_edges(),
            EdgeKind::Imports => self.all_import_edges(),
            EdgeKind::Inherits => self.all_inheritance_edges(),
            _ => {
                // For Contains, Uses, ModifiedIn — iterate raw_graph
                let graph = self.raw_graph();
                let mut edges: Vec<(StrKey, StrKey)> = graph
                    .edge_references()
                    .filter(|e| e.weight().kind == kind)
                    .filter_map(|e| {
                        let src = graph.node_weight(e.source())?;
                        let dst = graph.node_weight(e.target())?;
                        Some((src.qualified_name, dst.qualified_name))
                    })
                    .collect();
                edges.sort_unstable_by(|a, b| {
                    si.resolve(a.0)
                        .cmp(si.resolve(b.0))
                        .then_with(|| si.resolve(a.1).cmp(si.resolve(b.1)))
                });
                return edges;
            }
        };
        let mut edges: Vec<(StrKey, StrKey)> = bulk
            .iter()
            .filter_map(|&(src, tgt)| {
                let s = self.node(src)?;
                let t = self.node(tgt)?;
                Some((s.qualified_name, t.qualified_name))
            })
            .collect();
        edges.sort_unstable_by(|a, b| {
            si.resolve(a.0)
                .cmp(si.resolve(b.0))
                .then_with(|| si.resolve(a.1).cmp(si.resolve(b.1)))
        });
        edges
    }

    /// Get all edges as (String, String, EdgeKind) tuples sorted for determinism.
    #[deprecated(note = "Use adjacency indexes instead")]
    pub fn get_all_edges_compat(&self) -> Vec<(String, String, EdgeKind)> {
        let si = self.interner();
        let graph = self.raw_graph();
        let mut edges: Vec<_> = graph
            .edge_references()
            .filter_map(|e| {
                let src_node = graph.node_weight(e.source())?;
                let dst_node = graph.node_weight(e.target())?;
                Some((
                    si.resolve(src_node.qualified_name).to_string(),
                    si.resolve(dst_node.qualified_name).to_string(),
                    e.weight().kind,
                ))
            })
            .collect();
        edges.sort_unstable_by(|a, b| a.0.cmp(&b.0).then_with(|| a.1.cmp(&b.1)));
        edges
    }

    // ==================== Minimal Cycle ====================

    /// Find the minimal cycle through a specific node using BFS on the raw graph.
    #[deprecated(note = "Use raw_graph() for custom BFS traversals")]
    pub fn find_minimal_cycle_compat(
        &self,
        start_qn: &str,
        edge_kind: EdgeKind,
    ) -> Option<Vec<String>> {
        let si = self.interner();
        let (start_idx, _) = self.node_by_name(start_qn)?;
        let graph = self.raw_graph();

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
                if edge_kind == EdgeKind::Imports && edge.weight().is_type_only() {
                    continue;
                }
                let target = edge.target();

                if target == start_idx && path.len() > 1 {
                    return Some(
                        path.iter()
                            .filter_map(|&idx| graph.node_weight(idx))
                            .map(|n| si.resolve(n.qualified_name).to_string())
                            .collect(),
                    );
                }

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

    // ==================== Metrics Cache Stubs ====================

    /// Cache a metric. No-op stub — use MetricsCache separately.
    #[deprecated(note = "Use MetricsCache separately")]
    pub fn cache_metric_compat(&self, _key: &str, _value: f64) {
        tracing::warn!("cache_metric_compat called on CodeGraph — use MetricsCache separately");
    }

    /// Get a cached metric. Always returns None — use MetricsCache separately.
    #[deprecated(note = "Use MetricsCache separately")]
    pub fn get_cached_metric_compat(&self, _key: &str) -> Option<f64> {
        tracing::warn!(
            "get_cached_metric_compat called on CodeGraph — use MetricsCache separately"
        );
        None
    }

    /// Get cached metrics with prefix. Always returns empty — use MetricsCache separately.
    #[deprecated(note = "Use MetricsCache separately")]
    pub fn get_cached_metrics_with_prefix_compat(&self, _prefix: &str) -> Vec<(String, f64)> {
        tracing::warn!("get_cached_metrics_with_prefix_compat called on CodeGraph — use MetricsCache separately");
        vec![]
    }
}

// ==================== GraphQuery trait implementation ====================
//
// Enables `CodeGraph` to be used as `&dyn GraphQuery` by detector and scoring
// stages. All methods delegate to the compat bridges above or to native
// CodeGraph indexed methods.

#[allow(deprecated)]
impl super::traits::GraphQuery for CodeGraph {
    fn interner(&self) -> &super::interner::StringInterner {
        self.interner()
    }

    fn extra_props(&self, qn: StrKey) -> Option<super::store_models::ExtraProps> {
        CodeGraph::extra_props(self, qn).cloned()
    }

    fn get_functions(&self) -> Vec<CodeNode> {
        self.get_functions_compat()
    }

    fn get_classes(&self) -> Vec<CodeNode> {
        self.get_classes_compat()
    }

    fn get_files(&self) -> Vec<CodeNode> {
        self.get_files_compat()
    }

    fn get_functions_shared(&self) -> std::sync::Arc<[CodeNode]> {
        self.get_functions_shared_compat()
    }

    fn get_classes_shared(&self) -> std::sync::Arc<[CodeNode]> {
        self.get_classes_shared_compat()
    }

    fn get_files_shared(&self) -> std::sync::Arc<[CodeNode]> {
        self.get_files_shared_compat()
    }

    fn get_functions_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        self.get_functions_in_file_compat(file_path)
    }

    fn get_classes_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        self.get_classes_in_file_compat(file_path)
    }

    fn get_node(&self, qn: &str) -> Option<CodeNode> {
        self.get_node_compat(qn)
    }

    fn get_callers(&self, qn: &str) -> Vec<CodeNode> {
        self.get_callers_compat(qn)
    }

    fn get_callees(&self, qn: &str) -> Vec<CodeNode> {
        self.get_callees_compat(qn)
    }

    fn call_fan_in(&self, qn: &str) -> usize {
        self.call_fan_in_by_name(qn)
    }

    fn call_fan_out(&self, qn: &str) -> usize {
        self.call_fan_out_by_name(qn)
    }

    fn get_calls(&self) -> Vec<(StrKey, StrKey)> {
        self.get_calls_compat()
    }

    fn get_calls_shared(&self) -> std::sync::Arc<[(StrKey, StrKey)]> {
        self.get_calls_shared_compat()
    }

    fn get_imports(&self) -> Vec<(StrKey, StrKey)> {
        self.get_imports_compat()
    }

    fn get_inheritance(&self) -> Vec<(StrKey, StrKey)> {
        self.get_inheritance_compat()
    }

    fn get_child_classes(&self, qn: &str) -> Vec<CodeNode> {
        self.get_child_classes_compat(qn)
    }

    fn get_importers(&self, qn: &str) -> Vec<CodeNode> {
        self.get_importers_compat(qn)
    }

    fn find_import_cycles(&self) -> Vec<Vec<String>> {
        self.find_import_cycles_compat()
    }

    fn is_in_import_cycle(&self, file_path: &str) -> bool {
        self.is_in_import_cycle_compat(file_path)
    }

    fn stats(&self) -> std::collections::BTreeMap<String, i64> {
        CodeGraph::stats(self)
    }

    fn find_function_at(&self, file_path: &str, line: u32) -> Option<CodeNode> {
        self.find_function_at_compat(file_path, line)
    }

    fn get_complex_functions(&self, min_complexity: i64) -> Vec<CodeNode> {
        self.get_complex_functions_compat(min_complexity)
    }

    fn get_long_param_functions(&self, min_params: i64) -> Vec<CodeNode> {
        self.get_long_param_functions_compat(min_params)
    }

    fn caller_file_spread(&self, qn: &str) -> usize {
        self.caller_file_spread_compat(qn)
    }

    fn count_external_callers_of(
        &self,
        qn: &str,
        class_file: &str,
        class_start: u32,
        class_end: u32,
    ) -> usize {
        self.count_external_callers_of_compat(qn, class_file, class_start, class_end)
    }

    fn caller_module_spread(&self, qn: &str) -> usize {
        self.caller_module_spread_compat(qn)
    }

    fn build_call_maps_raw(
        &self,
    ) -> (
        std::collections::HashMap<StrKey, usize>,
        std::collections::HashMap<usize, Vec<usize>>,
        std::collections::HashMap<usize, Vec<usize>>,
    ) {
        self.build_call_maps_raw_compat()
    }

    fn get_call_adjacency(
        &self,
    ) -> (
        Vec<Vec<usize>>,
        Vec<Vec<usize>>,
        std::collections::HashMap<StrKey, usize>,
    ) {
        self.get_call_adjacency_compat()
    }
}

// Also implement for Arc<CodeGraph> to match the existing Arc<GraphStore> impl.
#[allow(deprecated)]
impl super::traits::GraphQuery for std::sync::Arc<CodeGraph> {
    fn interner(&self) -> &super::interner::StringInterner {
        (**self).interner()
    }

    fn extra_props(&self, qn: StrKey) -> Option<super::store_models::ExtraProps> {
        <CodeGraph as super::traits::GraphQuery>::extra_props(self, qn)
    }

    fn get_functions(&self) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_functions(self)
    }

    fn get_classes(&self) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_classes(self)
    }

    fn get_files(&self) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_files(self)
    }

    fn get_functions_shared(&self) -> std::sync::Arc<[CodeNode]> {
        <CodeGraph as super::traits::GraphQuery>::get_functions_shared(self)
    }

    fn get_classes_shared(&self) -> std::sync::Arc<[CodeNode]> {
        <CodeGraph as super::traits::GraphQuery>::get_classes_shared(self)
    }

    fn get_files_shared(&self) -> std::sync::Arc<[CodeNode]> {
        <CodeGraph as super::traits::GraphQuery>::get_files_shared(self)
    }

    fn get_functions_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_functions_in_file(self, file_path)
    }

    fn get_classes_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_classes_in_file(self, file_path)
    }

    fn get_node(&self, qn: &str) -> Option<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_node(self, qn)
    }

    fn get_callers(&self, qn: &str) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_callers(self, qn)
    }

    fn get_callees(&self, qn: &str) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_callees(self, qn)
    }

    fn call_fan_in(&self, qn: &str) -> usize {
        <CodeGraph as super::traits::GraphQuery>::call_fan_in(self, qn)
    }

    fn call_fan_out(&self, qn: &str) -> usize {
        <CodeGraph as super::traits::GraphQuery>::call_fan_out(self, qn)
    }

    fn get_calls(&self) -> Vec<(StrKey, StrKey)> {
        <CodeGraph as super::traits::GraphQuery>::get_calls(self)
    }

    fn get_calls_shared(&self) -> std::sync::Arc<[(StrKey, StrKey)]> {
        <CodeGraph as super::traits::GraphQuery>::get_calls_shared(self)
    }

    fn get_imports(&self) -> Vec<(StrKey, StrKey)> {
        <CodeGraph as super::traits::GraphQuery>::get_imports(self)
    }

    fn get_inheritance(&self) -> Vec<(StrKey, StrKey)> {
        <CodeGraph as super::traits::GraphQuery>::get_inheritance(self)
    }

    fn get_child_classes(&self, qn: &str) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_child_classes(self, qn)
    }

    fn get_importers(&self, qn: &str) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_importers(self, qn)
    }

    fn find_import_cycles(&self) -> Vec<Vec<String>> {
        <CodeGraph as super::traits::GraphQuery>::find_import_cycles(self)
    }

    fn is_in_import_cycle(&self, file_path: &str) -> bool {
        <CodeGraph as super::traits::GraphQuery>::is_in_import_cycle(self, file_path)
    }

    fn stats(&self) -> std::collections::BTreeMap<String, i64> {
        <CodeGraph as super::traits::GraphQuery>::stats(self)
    }

    fn find_function_at(&self, file_path: &str, line: u32) -> Option<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::find_function_at(self, file_path, line)
    }

    fn get_complex_functions(&self, min_complexity: i64) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_complex_functions(self, min_complexity)
    }

    fn get_long_param_functions(&self, min_params: i64) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_long_param_functions(self, min_params)
    }

    fn caller_file_spread(&self, qn: &str) -> usize {
        <CodeGraph as super::traits::GraphQuery>::caller_file_spread(self, qn)
    }

    fn count_external_callers_of(
        &self,
        qn: &str,
        class_file: &str,
        class_start: u32,
        class_end: u32,
    ) -> usize {
        <CodeGraph as super::traits::GraphQuery>::count_external_callers_of(
            self,
            qn,
            class_file,
            class_start,
            class_end,
        )
    }

    fn caller_module_spread(&self, qn: &str) -> usize {
        <CodeGraph as super::traits::GraphQuery>::caller_module_spread(self, qn)
    }

    fn build_call_maps_raw(
        &self,
    ) -> (
        std::collections::HashMap<StrKey, usize>,
        std::collections::HashMap<usize, Vec<usize>>,
        std::collections::HashMap<usize, Vec<usize>>,
    ) {
        <CodeGraph as super::traits::GraphQuery>::build_call_maps_raw(self)
    }

    fn get_call_adjacency(
        &self,
    ) -> (
        Vec<Vec<usize>>,
        Vec<Vec<usize>>,
        std::collections::HashMap<StrKey, usize>,
    ) {
        <CodeGraph as super::traits::GraphQuery>::get_call_adjacency(self)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::builder::GraphBuilder;
    use crate::graph::store_models::{CodeEdge, NodeKind};

    #[allow(deprecated)]
    #[test]
    fn test_get_functions_compat() {
        let mut builder = GraphBuilder::new();
        builder.add_node(CodeNode::function("foo", "a.py"));
        builder.add_node(CodeNode::function("bar", "a.py"));
        builder.add_node(CodeNode::class("MyClass", "a.py"));

        let graph = builder.freeze();
        let funcs = graph.get_functions_compat();
        assert_eq!(funcs.len(), 2);
        assert!(funcs.iter().all(|f| f.kind == NodeKind::Function));
    }

    #[allow(deprecated)]
    #[test]
    fn test_get_callers_compat() {
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("foo", "a.py"));
        let f2 = builder.add_node(CodeNode::function("bar", "a.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());

        let graph = builder.freeze();
        let callers = graph.get_callers_compat("a.py::bar");
        assert_eq!(callers.len(), 1);

        let si = graph.interner();
        assert_eq!(si.resolve(callers[0].qualified_name), "a.py::foo");
    }

    #[allow(deprecated)]
    #[test]
    fn test_find_import_cycles_compat() {
        let mut builder = GraphBuilder::new();
        let a = builder.add_node(CodeNode::file("a.py"));
        let b = builder.add_node(CodeNode::file("b.py"));
        builder.add_edge(a, b, CodeEdge::imports());
        builder.add_edge(b, a, CodeEdge::imports());

        let graph = builder.freeze();
        let cycles = graph.find_import_cycles_compat();
        assert_eq!(cycles.len(), 1);
        assert_eq!(cycles[0].len(), 2);
    }

    #[allow(deprecated)]
    #[test]
    fn test_build_call_maps_raw_compat() {
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("foo", "a.py"));
        let f2 = builder.add_node(CodeNode::function("bar", "a.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());

        let graph = builder.freeze();
        let (qn_to_idx, callers, callees) = graph.build_call_maps_raw_compat();

        assert_eq!(qn_to_idx.len(), 2);
        // foo calls bar, so callees should have an entry for foo's index
        assert!(!callees.is_empty());
        assert!(!callers.is_empty());
    }

    #[allow(deprecated)]
    #[test]
    fn test_compute_coupling_stats_compat() {
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("foo", "a.py"));
        let f2 = builder.add_node(CodeNode::function("bar", "b.py"));
        let f3 = builder.add_node(CodeNode::function("baz", "a.py"));

        builder.add_edge(f1, f2, CodeEdge::calls()); // cross-module
        builder.add_edge(f1, f3, CodeEdge::calls()); // same file

        let graph = builder.freeze();
        let (total, cross) = graph.compute_coupling_stats_compat();
        assert_eq!(total, 2);
        // a.py and b.py may or may not be in different parent dirs, but the files differ
        // For this test, they have no parent dir, so parent is None for both — same module
        // Actually both are root-level files with no parent dir
        assert_eq!(cross, 0); // both at root level, same "module" (no parent dir)
    }

    #[allow(deprecated)]
    #[test]
    fn test_get_calls_compat() {
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("foo", "a.py"));
        let f2 = builder.add_node(CodeNode::function("bar", "a.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());

        let graph = builder.freeze();
        let calls = graph.get_calls_compat();
        assert_eq!(calls.len(), 1);
    }
}
