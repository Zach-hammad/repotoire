//! Graph store traits for detector compatibility

use super::CodeNode;
use crate::graph::interner::{StrKey, StringInterner};
use crate::graph::store_models::ExtraProps;
use petgraph::stable_graph::NodeIndex;
use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;

/// Core interface for graph stores.
///
/// Contains two categories of methods:
///
/// **Core methods** — identity, extra props, stats.
///
/// **NodeIndex-based methods** (`functions_idx()`, `callers_idx(idx)`, …) —
///   Return zero-copy `&[NodeIndex]` slices from pre-built indexes.
///   Implemented by `CodeGraph` (frozen, production path) and `GraphBuilder`
///   (via lazily-built `QuerySnapshot`).
///   Default implementations return empty results for backends that
///   don't support indexed access (e.g., lightweight test mocks).
///
/// String-based convenience methods (`get_functions()`, `get_callers()`, …)
/// and derived helpers (shared Arc wrappers, filtering, adjacency builders)
/// live on [`GraphQueryExt`], which is auto-implemented for every
/// `GraphQuery` implementor via a blanket impl.
#[allow(dead_code)] // Trait defines public API surface; not all methods called in binary
pub trait GraphQuery: Send + Sync {
    // ==================== Core identity ====================

    /// Access the pre-computed graph primitives (dominator trees, PageRank, etc.).
    fn primitives(&self) -> &crate::graph::primitives::GraphPrimitives;

    /// Access the string interner for resolving StrKey -> &str.
    fn interner(&self) -> &StringInterner;

    /// Get extra (cold) properties for a node by its qualified_name StrKey.
    fn extra_props(&self, qn: StrKey) -> Option<ExtraProps>;

    /// Extra properties for a node by qualified_name StrKey (returns reference).
    fn extra_props_ref(&self, _qn: StrKey) -> Option<&ExtraProps> {
        None
    }

    /// Get stats (BTreeMap for deterministic key order)
    fn stats(&self) -> BTreeMap<String, i64>;

    // ==================== NodeIndex-based API ====================
    //
    // Zero-copy, O(1) access using petgraph NodeIndex.
    // CodeGraph implements all of these via pre-built indexes.
    // GraphBuilder implements them via a lazily-built QuerySnapshot.
    // Default implementations return empty results for lightweight
    // test mocks that don't support indexed access.

    /// Get a node by its graph index.
    fn node_idx(&self, _idx: NodeIndex) -> Option<&CodeNode> {
        None
    }

    /// Look up a node by qualified name. Returns both index and reference.
    fn node_by_name_idx(&self, _qn: &str) -> Option<(NodeIndex, &CodeNode)> {
        None
    }

    /// All function NodeIndexes.
    fn functions_idx(&self) -> &[NodeIndex] {
        &[]
    }

    /// All class NodeIndexes.
    fn classes_idx(&self) -> &[NodeIndex] {
        &[]
    }

    /// All file NodeIndexes.
    fn files_idx(&self) -> &[NodeIndex] {
        &[]
    }

    /// Functions that call this node (incoming Calls edges).
    fn callers_idx(&self, _idx: NodeIndex) -> &[NodeIndex] {
        &[]
    }

    /// Functions this node calls (outgoing Calls edges).
    fn callees_idx(&self, _idx: NodeIndex) -> &[NodeIndex] {
        &[]
    }

    /// Modules/files that import this node (incoming Imports edges).
    fn importers_idx(&self, _idx: NodeIndex) -> &[NodeIndex] {
        &[]
    }

    /// Modules/files this node imports (outgoing Imports edges).
    fn importees_idx(&self, _idx: NodeIndex) -> &[NodeIndex] {
        &[]
    }

    /// Parent classes (outgoing Inherits edges).
    fn parent_classes_idx(&self, _idx: NodeIndex) -> &[NodeIndex] {
        &[]
    }

    /// Child classes (incoming Inherits edges).
    fn child_classes_idx(&self, _idx: NodeIndex) -> &[NodeIndex] {
        &[]
    }

    /// Number of callers (fan-in). O(1) on CodeGraph.
    fn call_fan_in_idx(&self, idx: NodeIndex) -> usize {
        self.callers_idx(idx).len()
    }

    /// Number of callees (fan-out). O(1) on CodeGraph.
    fn call_fan_out_idx(&self, idx: NodeIndex) -> usize {
        self.callees_idx(idx).len()
    }

    /// Functions in a file as NodeIndex slice.
    fn functions_in_file_idx(&self, _file_path: &str) -> &[NodeIndex] {
        &[]
    }

    /// Classes in a file as NodeIndex slice.
    fn classes_in_file_idx(&self, _file_path: &str) -> &[NodeIndex] {
        &[]
    }

    /// Find the function containing a line in a file (returns NodeIndex).
    fn function_at_idx(&self, _file_path: &str, _line: u32) -> Option<NodeIndex> {
        None
    }

    /// All call edges as (caller, callee) NodeIndex pairs.
    fn all_call_edges(&self) -> &[(NodeIndex, NodeIndex)] {
        &[]
    }

    /// All import edges as (importer, importee) NodeIndex pairs.
    fn all_import_edges(&self) -> &[(NodeIndex, NodeIndex)] {
        &[]
    }

    /// All inheritance edges as (child, parent) NodeIndex pairs.
    fn all_inheritance_edges(&self) -> &[(NodeIndex, NodeIndex)] {
        &[]
    }

    /// Import cycle groups. Each inner Vec contains NodeIndexes of nodes in the cycle.
    fn import_cycles_idx(&self) -> &[Vec<NodeIndex>] {
        &[]
    }

    /// Edge fingerprint for topology change detection.
    fn edge_fingerprint_idx(&self) -> u64 {
        0
    }
}

/// Convenience extension trait with derived methods.
///
/// Auto-implemented for every [`GraphQuery`] implementor via blanket impl.
/// Contains the 16 string-based convenience methods (delegating to `_idx`
/// methods), shared-Arc wrappers, filtering helpers, cycle checks,
/// fan-in/fan-out index methods, and adjacency builders.
#[allow(dead_code)] // Trait defines public API surface; not all methods called in binary
pub trait GraphQueryExt: GraphQuery {
    // ==================== String-based convenience methods ====================
    //
    // Default implementations delegate to the _idx methods on GraphQuery.

    /// Get all functions
    fn get_functions(&self) -> Vec<CodeNode> {
        self.functions_idx()
            .iter()
            .filter_map(|&idx| self.node_idx(idx).copied())
            .collect()
    }

    /// Get all classes
    fn get_classes(&self) -> Vec<CodeNode> {
        self.classes_idx()
            .iter()
            .filter_map(|&idx| self.node_idx(idx).copied())
            .collect()
    }

    /// Get all files
    fn get_files(&self) -> Vec<CodeNode> {
        self.files_idx()
            .iter()
            .filter_map(|&idx| self.node_idx(idx).copied())
            .collect()
    }

    /// Get functions in a specific file
    fn get_functions_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        self.functions_in_file_idx(file_path)
            .iter()
            .filter_map(|&idx| self.node_idx(idx).copied())
            .collect()
    }

    /// Get classes in a specific file
    fn get_classes_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        self.classes_in_file_idx(file_path)
            .iter()
            .filter_map(|&idx| self.node_idx(idx).copied())
            .collect()
    }

    /// Get node by qualified name
    fn get_node(&self, qn: &str) -> Option<CodeNode> {
        self.node_by_name_idx(qn).map(|(_, node)| *node)
    }

    /// Get functions that call this function
    fn get_callers(&self, qn: &str) -> Vec<CodeNode> {
        let Some((idx, _)) = self.node_by_name_idx(qn) else {
            return vec![];
        };
        self.callers_idx(idx)
            .iter()
            .filter_map(|&ci| self.node_idx(ci).copied())
            .collect()
    }

    /// Get functions this function calls
    fn get_callees(&self, qn: &str) -> Vec<CodeNode> {
        let Some((idx, _)) = self.node_by_name_idx(qn) else {
            return vec![];
        };
        self.callees_idx(idx)
            .iter()
            .filter_map(|&ci| self.node_idx(ci).copied())
            .collect()
    }

    /// Count of callers (fan-in)
    fn call_fan_in(&self, qn: &str) -> usize {
        self.node_by_name_idx(qn)
            .map(|(idx, _)| self.callers_idx(idx).len())
            .unwrap_or(0)
    }

    /// Count of callees (fan-out)
    fn call_fan_out(&self, qn: &str) -> usize {
        self.node_by_name_idx(qn)
            .map(|(idx, _)| self.callees_idx(idx).len())
            .unwrap_or(0)
    }

    /// Get all call edges
    fn get_calls(&self) -> Vec<(StrKey, StrKey)> {
        self.all_call_edges()
            .iter()
            .filter_map(|&(src, tgt)| {
                let s = self.node_idx(src)?;
                let t = self.node_idx(tgt)?;
                Some((s.qualified_name, t.qualified_name))
            })
            .collect()
    }

    /// Get all import edges
    fn get_imports(&self) -> Vec<(StrKey, StrKey)> {
        self.all_import_edges()
            .iter()
            .filter_map(|&(src, tgt)| {
                let s = self.node_idx(src)?;
                let t = self.node_idx(tgt)?;
                Some((s.qualified_name, t.qualified_name))
            })
            .collect()
    }

    /// Get inheritance edges
    fn get_inheritance(&self) -> Vec<(StrKey, StrKey)> {
        self.all_inheritance_edges()
            .iter()
            .filter_map(|&(src, tgt)| {
                let s = self.node_idx(src)?;
                let t = self.node_idx(tgt)?;
                Some((s.qualified_name, t.qualified_name))
            })
            .collect()
    }

    /// Get child classes
    fn get_child_classes(&self, qn: &str) -> Vec<CodeNode> {
        let Some((idx, _)) = self.node_by_name_idx(qn) else {
            return vec![];
        };
        self.child_classes_idx(idx)
            .iter()
            .filter_map(|&ci| self.node_idx(ci).copied())
            .collect()
    }

    /// Get files that import this file
    fn get_importers(&self, qn: &str) -> Vec<CodeNode> {
        let Some((idx, _)) = self.node_by_name_idx(qn) else {
            return vec![];
        };
        self.importers_idx(idx)
            .iter()
            .filter_map(|&ci| self.node_idx(ci).copied())
            .collect()
    }

    /// Find import cycles
    fn find_import_cycles(&self) -> Vec<Vec<String>> {
        let si = self.interner();
        self.import_cycles_idx()
            .iter()
            .map(|cycle| {
                let mut names: Vec<String> = cycle
                    .iter()
                    .filter_map(|&idx| self.node_idx(idx))
                    .map(|n| si.resolve(n.qualified_name).to_string())
                    .collect();
                names.sort();
                names
            })
            .collect()
    }

    // ==================== Derived convenience methods ====================

    /// Get all functions as shared Arc — Arc::clone is ~10ns vs Vec::clone ~50ms.
    fn get_functions_shared(&self) -> Arc<[CodeNode]> {
        Arc::from(self.get_functions())
    }

    /// Get all classes as shared Arc.
    fn get_classes_shared(&self) -> Arc<[CodeNode]> {
        Arc::from(self.get_classes())
    }

    /// Get all files as shared Arc.
    fn get_files_shared(&self) -> Arc<[CodeNode]> {
        Arc::from(self.get_files())
    }

    /// Get all call edges as shared Arc — avoids cloning 296K+ (StrKey, StrKey) pairs.
    fn get_calls_shared(&self) -> Arc<[(StrKey, StrKey)]> {
        Arc::from(self.get_calls())
    }

    /// Check if a file participates in any import cycle.
    fn is_in_import_cycle(&self, file_path: &str) -> bool {
        let cycles = self.find_import_cycles();
        cycles.iter().any(|cycle| {
            cycle.iter().any(|qn| qn == file_path || file_path.contains(qn.as_str()))
        })
    }

    /// Find the function containing a specific line in a file.
    fn find_function_at(&self, file_path: &str, line: u32) -> Option<CodeNode> {
        // Try indexed path first (CodeGraph), fall back to string-based scan
        if let Some(idx) = self.function_at_idx(file_path, line) {
            return self.node_idx(idx).copied();
        }
        self.get_functions_in_file(file_path)
            .into_iter()
            .find(|f| f.line_start <= line && f.line_end >= line)
    }

    /// Get complex functions (complexity > threshold)
    fn get_complex_functions(&self, min_complexity: i64) -> Vec<CodeNode> {
        self.functions_idx()
            .iter()
            .filter_map(|&idx| {
                let node = self.node_idx(idx)?;
                if node.complexity_opt().is_some_and(|c| c >= min_complexity) {
                    Some(*node)
                } else {
                    None
                }
            })
            .collect()
    }

    /// Get long parameter functions
    fn get_long_param_functions(&self, min_params: i64) -> Vec<CodeNode> {
        self.functions_idx()
            .iter()
            .filter_map(|&idx| {
                let node = self.node_idx(idx)?;
                if node.param_count_opt().is_some_and(|p| p >= min_params) {
                    Some(*node)
                } else {
                    None
                }
            })
            .collect()
    }

    /// Count unique files of callers.
    fn caller_file_spread(&self, qn: &str) -> usize {
        let Some((idx, _)) = self.node_by_name_idx(qn) else {
            return 0;
        };
        let files: std::collections::HashSet<StrKey> = self
            .callers_idx(idx)
            .iter()
            .filter_map(|&ci| self.node_idx(ci))
            .map(|n| n.file_path)
            .collect();
        files.len()
    }

    /// Count callers of `qn` that are OUTSIDE a given class boundary.
    fn count_external_callers_of(
        &self,
        qn: &str,
        class_file: &str,
        class_start: u32,
        class_end: u32,
    ) -> usize {
        let si = self.interner();
        let Some((idx, _)) = self.node_by_name_idx(qn) else {
            return 0;
        };
        self.callers_idx(idx)
            .iter()
            .filter_map(|&ci| self.node_idx(ci))
            .filter(|c| {
                si.resolve(c.file_path) != class_file
                    || c.line_start < class_start
                    || c.line_end > class_end
            })
            .count()
    }

    /// Count unique modules (parent directories) of callers.
    fn caller_module_spread(&self, qn: &str) -> usize {
        let si = self.interner();
        let Some((idx, _)) = self.node_by_name_idx(qn) else {
            return 0;
        };
        let modules: std::collections::HashSet<&str> = self
            .callers_idx(idx)
            .iter()
            .filter_map(|&ci| self.node_idx(ci))
            .map(|n| {
                std::path::Path::new(si.resolve(n.file_path))
                    .parent()
                    .and_then(|p| p.to_str())
                    .unwrap_or("root")
            })
            .collect();
        modules.len()
    }

    /// Build call maps as (qn_to_idx, callers_idx, callees_idx).
    ///
    /// Returns index-based maps where indices correspond to positions in `get_functions()`.
    fn build_call_maps_raw(
        &self,
    ) -> (
        HashMap<StrKey, usize>,
        HashMap<usize, Vec<usize>>,
        HashMap<usize, Vec<usize>>,
    ) {
        let functions = self.get_functions();
        let calls = self.get_calls();
        let qn_to_idx: HashMap<StrKey, usize> = functions
            .iter()
            .enumerate()
            .map(|(i, f)| (f.qualified_name, i))
            .collect();
        let mut callers: HashMap<usize, Vec<usize>> = HashMap::new();
        let mut callees: HashMap<usize, Vec<usize>> = HashMap::new();
        for (caller, callee) in &calls {
            if let (Some(&from), Some(&to)) = (qn_to_idx.get(caller), qn_to_idx.get(callee)) {
                callers.entry(to).or_default().push(from);
                callees.entry(from).or_default().push(to);
            }
        }
        (qn_to_idx, callers, callees)
    }

    /// Build call adjacency lists: (forward_adj, reverse_adj, qn_to_idx).
    ///
    /// Indices correspond to positions in `get_functions()`.
    fn get_call_adjacency(&self) -> (Vec<Vec<usize>>, Vec<Vec<usize>>, HashMap<StrKey, usize>) {
        let functions = self.get_functions();
        let calls = self.get_calls();
        let qn_to_idx: HashMap<StrKey, usize> = functions
            .iter()
            .enumerate()
            .map(|(i, f)| (f.qualified_name, i))
            .collect();
        let n = functions.len();
        let mut adj = vec![vec![]; n];
        let mut rev_adj = vec![vec![]; n];
        for (caller, callee) in &calls {
            if let (Some(&from), Some(&to)) =
                (qn_to_idx.get(caller), qn_to_idx.get(callee))
            {
                adj[from].push(to);
                rev_adj[to].push(from);
            }
        }
        (adj, rev_adj, qn_to_idx)
    }
}

// Blanket impl — every GraphQuery type automatically gets the extension methods.
// `?Sized` ensures this covers `dyn GraphQuery` as well as concrete types.
impl<T: GraphQuery + ?Sized> GraphQueryExt for T {}
