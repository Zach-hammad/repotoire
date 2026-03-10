//! Graph store traits for detector compatibility

use super::CodeNode;
use crate::graph::interner::{StrKey, StringInterner};
use crate::graph::store_models::ExtraProps;
use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;

/// Common interface for graph stores
#[allow(dead_code)] // Trait defines public API surface; not all methods called in binary
pub trait GraphQuery: Send + Sync {
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
    ///
    /// CachedGraphQuery overrides this to return a cached Arc (zero-cost after first call).
    /// Default implementation wraps get_functions() in Arc for backward compatibility.
    fn get_functions_shared(&self) -> Arc<[CodeNode]> {
        Arc::from(self.get_functions())
    }

    /// Get all classes as shared Arc — see get_functions_shared.
    fn get_classes_shared(&self) -> Arc<[CodeNode]> {
        Arc::from(self.get_classes())
    }

    /// Get all files as shared Arc — see get_functions_shared.
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
    /// Default implementation calls `find_import_cycles()` and checks.
    /// `CachedGraphQuery` overrides with O(1) HashSet lookup.
    fn is_in_import_cycle(&self, file_path: &str) -> bool {
        let cycles = self.find_import_cycles();
        cycles.iter().any(|cycle| {
            cycle.iter().any(|qn| qn == file_path || file_path.contains(qn.as_str()))
        })
    }

    /// Get stats (BTreeMap for deterministic key order)
    fn stats(&self) -> BTreeMap<String, i64>;

    /// Find the function containing a specific line in a file.
    /// Default implementation uses get_functions_in_file (O(all_nodes) scan).
    /// GraphStore overrides this with a spatial index for O(1) lookup.
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

    /// Count unique files of callers — avoids cloning full CodeNodes.
    ///
    /// CachedGraphQuery overrides with a zero-copy implementation that resolves
    /// caller indices directly from the cached functions Arc.
    fn caller_file_spread(&self, qn: &str) -> usize {
        let i = self.interner();
        let callers = self.get_callers(qn);
        let files: std::collections::HashSet<StrKey> =
            callers.iter().map(|c| c.file_path).collect();
        let _ = i; // interner available if needed for resolution
        files.len()
    }

    /// Count callers of `qn` that are OUTSIDE a given class boundary.
    ///
    /// A caller is "external" if it's in a different file OR its line range
    /// doesn't overlap with the class range [class_start, class_end].
    /// CachedGraphQuery overrides with a zero-copy implementation.
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

    /// Count unique modules (parent directories) of callers — avoids cloning full CodeNodes.
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
    /// GraphStore overrides this to iterate petgraph edges directly, avoiding
    /// the 12.5M+ (StrKey, StrKey) allocation from `get_calls()`.
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
    /// CachedGraphQuery overrides this to reuse pre-built index maps,
    /// avoiding cloning millions of (StrKey, StrKey) pairs from `get_calls()`.
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
