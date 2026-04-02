//! Graph-aware delta detection.
//!
//! Given changed files and line ranges (hunks), maps changed lines to
//! graph nodes (functions/classes) and walks one hop along the call graph
//! to find impacted callers.

use std::collections::HashSet;

use crate::graph::traits::GraphQuery;

/// Map changed file lines to the qualified names of containing functions or classes.
///
/// `changed_files` is a slice of `(file_path, hunks)` where each hunk is
/// a `(start_line, end_line)` range (1-based, inclusive).
///
/// Returns the set of qualified names for every function or class that
/// overlaps a changed hunk.
pub fn map_changed_nodes(
    graph: &dyn GraphQuery,
    changed_files: &[(String, Vec<(u32, u32)>)],
) -> HashSet<String> {
    let interner = graph.interner();
    let mut result = HashSet::new();

    for (file_path, hunks) in changed_files {
        for &(start, end) in hunks {
            // Check each line in the hunk for a containing function.
            // function_at_idx uses a spatial index, so per-line lookups are fast.
            let mut matched_functions: HashSet<petgraph::stable_graph::NodeIndex> = HashSet::new();
            for line in start..=end {
                if let Some(idx) = graph.function_at_idx(file_path, line) {
                    matched_functions.insert(idx);
                }
            }
            for idx in matched_functions {
                if let Some(node) = graph.node_idx(idx) {
                    result.insert(interner.resolve(node.qualified_name).to_string());
                }
            }

            // Also check classes whose span overlaps the hunk.
            for &cls_idx in graph.classes_in_file_idx(file_path) {
                if let Some(cls) = graph.node_idx(cls_idx) {
                    if cls.line_start <= end && cls.line_end >= start {
                        result.insert(interner.resolve(cls.qualified_name).to_string());
                    }
                }
            }
        }
    }

    result
}

/// Walk one hop along the call graph from `changed_qnames` and return
/// the qualified names of callers that are **not** themselves in the
/// changed set.
pub fn find_callers_of_changed(
    graph: &dyn GraphQuery,
    changed_qnames: &HashSet<String>,
) -> HashSet<String> {
    let interner = graph.interner();
    let mut callers = HashSet::new();

    for qn in changed_qnames {
        if let Some((idx, _)) = graph.node_by_name_idx(qn) {
            for &caller_idx in graph.callers_idx(idx) {
                if let Some(caller_node) = graph.node_idx(caller_idx) {
                    let caller_qn = interner.resolve(caller_node.qualified_name).to_string();
                    if !changed_qnames.contains(&caller_qn) {
                        callers.insert(caller_qn);
                    }
                }
            }
        }
    }

    callers
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_input_returns_empty() {
        // Use a minimal mock — GraphQuery default impls all return empty.
        struct EmptyGraph;

        impl GraphQuery for EmptyGraph {
            fn primitives(&self) -> &crate::graph::primitives::GraphPrimitives {
                // We never call primitives in these functions, so this is unreachable
                // in practice. Provide a static empty one.
                use std::sync::LazyLock;
                static PRIMS: LazyLock<crate::graph::primitives::GraphPrimitives> =
                    LazyLock::new(crate::graph::primitives::GraphPrimitives::default);
                &PRIMS
            }

            fn interner(&self) -> &crate::graph::interner::StringInterner {
                crate::graph::interner::global_interner()
            }

            fn extra_props(
                &self,
                _qn: crate::graph::interner::StrKey,
            ) -> Option<crate::graph::store_models::ExtraProps> {
                None
            }

            fn stats(&self) -> std::collections::BTreeMap<String, i64> {
                std::collections::BTreeMap::new()
            }
        }

        let graph = EmptyGraph;

        // map_changed_nodes with no files
        let empty: Vec<(String, Vec<(u32, u32)>)> = vec![];
        let nodes = map_changed_nodes(&graph, &empty);
        assert!(nodes.is_empty(), "empty files => empty nodes");

        // map_changed_nodes with files but graph has no nodes
        let files = vec![("src/main.rs".to_string(), vec![(1, 10)])];
        let nodes = map_changed_nodes(&graph, &files);
        assert!(nodes.is_empty(), "no graph nodes => empty result");

        // find_callers_of_changed with empty set
        let changed: HashSet<String> = HashSet::new();
        let callers = find_callers_of_changed(&graph, &changed);
        assert!(callers.is_empty(), "empty changed => empty callers");

        // find_callers_of_changed with names not in graph
        let mut changed = HashSet::new();
        changed.insert("nonexistent::func".to_string());
        let callers = find_callers_of_changed(&graph, &changed);
        assert!(callers.is_empty(), "missing nodes => empty callers");
    }
}
