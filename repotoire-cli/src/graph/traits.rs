//! Graph store traits for detector compatibility

use super::CodeNode;
use crate::graph::interner::{StrKey, StringInterner};
use crate::graph::store_models::ExtraProps;
use petgraph::stable_graph::NodeIndex;
use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;

/// Common interface for graph stores.
///
/// Two sets of methods coexist on this trait:
///
/// **String-based methods** (`get_functions()`, `get_callers(qn)`, …) —
///   Return owned `Vec<CodeNode>` or `Vec<(StrKey, StrKey)>`.
///   Implemented directly by `GraphStore` (mutable, test-friendly).
///   On `CodeGraph`, these delegate to the indexed methods via compat bridges.
///
/// **NodeIndex-based methods** (`functions_idx()`, `callers_idx(idx)`, …) —
///   Return zero-copy `&[NodeIndex]` slices from pre-built indexes.
///   Implemented by `CodeGraph` (frozen, production path).
///   Default implementations return empty results for backends that
///   don't support them (e.g., `GraphStore` in test code).
///
/// New code should prefer the `_idx` methods when working with CodeGraph
/// through the trait. The string-based methods are kept for backward
/// compatibility and test ergonomics.
#[allow(dead_code)] // Trait defines public API surface; not all methods called in binary
pub trait GraphQuery: Send + Sync {
    /// Access the pre-computed graph primitives (dominator trees, PageRank, etc.).
    fn primitives(&self) -> &crate::graph::primitives::GraphPrimitives;

    /// Access the string interner for resolving StrKey -> &str.
    fn interner(&self) -> &StringInterner;

    /// Get extra (cold) properties for a node by its qualified_name StrKey.
    fn extra_props(&self, qn: StrKey) -> Option<ExtraProps>;

    /// Get all functions
    fn get_functions(&self) -> Vec<CodeNode>;

    /// Get all classes
    fn get_classes(&self) -> Vec<CodeNode>;

    /// Get all files
    fn get_files(&self) -> Vec<CodeNode>;

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

    /// Get functions in a specific file
    fn get_functions_in_file(&self, file_path: &str) -> Vec<CodeNode>;

    /// Get classes in a specific file
    fn get_classes_in_file(&self, file_path: &str) -> Vec<CodeNode>;

    /// Get node by qualified name
    fn get_node(&self, qn: &str) -> Option<CodeNode>;

    /// Get functions that call this function
    fn get_callers(&self, qn: &str) -> Vec<CodeNode>;

    /// Get functions this function calls
    fn get_callees(&self, qn: &str) -> Vec<CodeNode>;

    /// Count of callers (fan-in)
    fn call_fan_in(&self, qn: &str) -> usize;

    /// Count of callees (fan-out)
    fn call_fan_out(&self, qn: &str) -> usize;

    /// Get all call edges
    fn get_calls(&self) -> Vec<(StrKey, StrKey)>;

    /// Get all call edges as shared Arc — avoids cloning 296K+ (StrKey, StrKey) pairs.
    fn get_calls_shared(&self) -> Arc<[(StrKey, StrKey)]> {
        Arc::from(self.get_calls())
    }

    /// Get all import edges
    fn get_imports(&self) -> Vec<(StrKey, StrKey)>;

    /// Get inheritance edges
    fn get_inheritance(&self) -> Vec<(StrKey, StrKey)>;

    /// Get child classes
    fn get_child_classes(&self, qn: &str) -> Vec<CodeNode>;

    /// Get files that import this file
    fn get_importers(&self, qn: &str) -> Vec<CodeNode>;

    /// Find import cycles
    fn find_import_cycles(&self) -> Vec<Vec<String>>;

    /// Check if a file participates in any import cycle.
    fn is_in_import_cycle(&self, file_path: &str) -> bool {
        let cycles = self.find_import_cycles();
        cycles.iter().any(|cycle| {
            cycle.iter().any(|qn| qn == file_path || file_path.contains(qn.as_str()))
        })
    }

    /// Get stats (BTreeMap for deterministic key order)
    fn stats(&self) -> BTreeMap<String, i64>;

    /// Find the function containing a specific line in a file.
    fn find_function_at(&self, file_path: &str, line: u32) -> Option<CodeNode> {
        self.get_functions_in_file(file_path)
            .into_iter()
            .find(|f| f.line_start <= line && f.line_end >= line)
    }

    /// Get complex functions (complexity > threshold)
    fn get_complex_functions(&self, min_complexity: i64) -> Vec<CodeNode> {
        self.get_functions()
            .into_iter()
            .filter(|f| f.get_i64("complexity").unwrap_or(0) >= min_complexity)
            .collect()
    }

    /// Get long parameter functions
    fn get_long_param_functions(&self, min_params: i64) -> Vec<CodeNode> {
        self.get_functions()
            .into_iter()
            .filter(|f| f.get_i64("paramCount").unwrap_or(0) >= min_params)
            .collect()
    }

    /// Count unique files of callers.
    fn caller_file_spread(&self, qn: &str) -> usize {
        let i = self.interner();
        let callers = self.get_callers(qn);
        let files: std::collections::HashSet<StrKey> =
            callers.iter().map(|c| c.file_path).collect();
        let _ = i;
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
        let i = self.interner();
        let callers = self.get_callers(qn);
        callers
            .iter()
            .filter(|c| {
                i.resolve(c.file_path) != class_file
                    || c.line_start < class_start
                    || c.line_end > class_end
            })
            .count()
    }

    /// Count unique modules (parent directories) of callers.
    fn caller_module_spread(&self, qn: &str) -> usize {
        let i = self.interner();
        let callers = self.get_callers(qn);
        let modules: std::collections::HashSet<&str> = callers
            .iter()
            .map(|c| {
                std::path::Path::new(i.resolve(c.file_path))
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

    // ==================== NodeIndex-based API ====================
    //
    // Zero-copy, O(1) access using petgraph NodeIndex.
    // CodeGraph implements all of these via pre-built indexes.
    // Default implementations return empty results for backends
    // that don't support indexed access (e.g., GraphStore in tests).

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

    /// Extra properties for a node by qualified_name StrKey (returns reference).
    fn extra_props_ref(&self, _qn: StrKey) -> Option<&ExtraProps> {
        None
    }

}
