//! Shared pre-computed data for detector execution.
//!
//! Built once during `precompute_gd_startup()` and injected into detectors
//! that override `set_detector_context()`. Avoids redundant graph queries
//! and Vec<CodeNode> cloning across 99 detectors.

use crate::detectors::class_context::ClassContextMap;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

/// Shared pre-computed data available to all detectors.
///
/// This is built in parallel with taint analysis and HMM (zero wall-clock cost)
/// and provides zero-copy access to commonly needed graph data.
#[allow(dead_code)] // Fields are scaffolding for detectors that will consume them
pub struct DetectorContext {
    /// QN -> Vec<caller QN> -- avoids Vec<CodeNode> cloning in get_callers()
    pub callers_by_qn: HashMap<String, Vec<String>>,
    /// QN -> Vec<callee QN> -- avoids Vec<CodeNode> cloning in get_callees()
    pub callees_by_qn: HashMap<String, Vec<String>>,
    /// Parent class QN -> Vec<child class QN>
    pub class_children: HashMap<String, Vec<String>>,
    /// Pre-loaded raw file content
    pub file_contents: HashMap<PathBuf, Arc<str>>,
    /// Pre-built class contexts for god class detection (built as 5th parallel thread)
    pub class_contexts: Option<Arc<ClassContextMap>>,
    /// Resolved variable values from graph-based constant propagation
    pub value_store: Option<Arc<crate::values::store::ValueStore>>,
}

impl DetectorContext {
    /// Build the detector context from the graph and source files.
    ///
    /// Reads the call graph, inheritance edges, and file contents.
    /// Designed to run in parallel with other precompute work.
    pub fn build(
        graph: &dyn crate::graph::GraphQuery,
        source_files: &[PathBuf],
        value_store: Option<Arc<crate::values::store::ValueStore>>,
    ) -> Self {
        use rayon::prelude::*;

        // Build callers/callees from call maps
        let functions = graph.get_functions();
        let (_qn_to_idx, callers_by_idx, callees_by_idx) = graph.build_call_maps_raw();

        let mut callers_by_qn: HashMap<String, Vec<String>> = HashMap::with_capacity(callers_by_idx.len());
        let mut callees_by_qn: HashMap<String, Vec<String>> = HashMap::with_capacity(callees_by_idx.len());

        for (&callee_idx, caller_idxs) in &callers_by_idx {
            if let Some(callee_qn) = functions.get(callee_idx).map(|f| f.qualified_name.clone()) {
                let caller_qns: Vec<String> = caller_idxs
                    .iter()
                    .filter_map(|&ci| functions.get(ci).map(|f| f.qualified_name.clone()))
                    .collect();
                callers_by_qn.insert(callee_qn, caller_qns);
            }
        }

        for (&caller_idx, callee_idxs) in &callees_by_idx {
            if let Some(caller_qn) = functions.get(caller_idx).map(|f| f.qualified_name.clone()) {
                let callee_qns: Vec<String> = callee_idxs
                    .iter()
                    .filter_map(|&ci| functions.get(ci).map(|f| f.qualified_name.clone()))
                    .collect();
                callees_by_qn.insert(caller_qn, callee_qns);
            }
        }

        // Build class hierarchy
        let inheritance = graph.get_inheritance();
        let mut class_children: HashMap<String, Vec<String>> = HashMap::new();
        for (child, parent) in &inheritance {
            class_children
                .entry(parent.clone())
                .or_default()
                .push(child.clone());
        }

        // Pre-load file contents in parallel
        let file_contents: HashMap<PathBuf, Arc<str>> = source_files
            .par_iter()
            .filter_map(|f| {
                std::fs::read_to_string(f)
                    .ok()
                    .map(|c| (f.clone(), Arc::from(c.as_str())))
            })
            .collect();

        Self {
            callers_by_qn,
            callees_by_qn,
            class_children,
            file_contents,
            class_contexts: None,
            value_store,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::store_models::{CodeEdge, CodeNode};
    use crate::graph::GraphStore;

    #[test]
    fn test_empty_graph_produces_empty_context() {
        let graph = GraphStore::in_memory();
        let ctx = DetectorContext::build(&graph, &[], None);
        assert!(ctx.callers_by_qn.is_empty());
        assert!(ctx.callees_by_qn.is_empty());
        assert!(ctx.class_children.is_empty());
        assert!(ctx.file_contents.is_empty());
    }

    #[test]
    fn test_file_contents_loaded() {
        let graph = GraphStore::in_memory();
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("test.py");
        std::fs::write(&file_path, "def hello(): pass").unwrap();

        let ctx = DetectorContext::build(&graph, &[file_path.clone()], None);
        assert_eq!(ctx.file_contents.len(), 1);
        assert!(ctx.file_contents.contains_key(&file_path));
        assert_eq!(&*ctx.file_contents[&file_path], "def hello(): pass");
    }

    #[test]
    fn test_file_contents_skips_missing_files() {
        let graph = GraphStore::in_memory();
        let missing = PathBuf::from("/nonexistent/path/file.py");

        let ctx = DetectorContext::build(&graph, &[missing], None);
        assert!(ctx.file_contents.is_empty());
    }

    #[test]
    fn test_callers_callees_populated() {
        let graph = GraphStore::in_memory();

        graph.add_node(
            CodeNode::function("caller", "test.py").with_qualified_name("module.caller"),
        );
        graph.add_node(
            CodeNode::function("callee", "test.py").with_qualified_name("module.callee"),
        );
        graph.add_edge_by_name("module.caller", "module.callee", CodeEdge::calls());

        let ctx = DetectorContext::build(&graph, &[], None);

        // callers_by_qn: callee -> [caller]
        assert!(ctx.callers_by_qn.contains_key("module.callee"));
        assert!(ctx.callers_by_qn["module.callee"].contains(&"module.caller".to_string()));

        // callees_by_qn: caller -> [callee]
        assert!(ctx.callees_by_qn.contains_key("module.caller"));
        assert!(ctx.callees_by_qn["module.caller"].contains(&"module.callee".to_string()));
    }

    #[test]
    fn test_class_children_populated() {
        let graph = GraphStore::in_memory();

        graph.add_node(
            CodeNode::class("Parent", "test.py").with_qualified_name("module.Parent"),
        );
        graph.add_node(
            CodeNode::class("Child", "test.py").with_qualified_name("module.Child"),
        );
        graph.add_edge_by_name("module.Child", "module.Parent", CodeEdge::inherits());

        let ctx = DetectorContext::build(&graph, &[], None);
        assert!(ctx.class_children.contains_key("module.Parent"));
        assert!(ctx.class_children["module.Parent"].contains(&"module.Child".to_string()));
    }

    #[test]
    fn test_multiple_callers_for_same_callee() {
        let graph = GraphStore::in_memory();

        graph.add_node(
            CodeNode::function("a", "test.py").with_qualified_name("mod.a"),
        );
        graph.add_node(
            CodeNode::function("b", "test.py").with_qualified_name("mod.b"),
        );
        graph.add_node(
            CodeNode::function("target", "test.py").with_qualified_name("mod.target"),
        );
        graph.add_edge_by_name("mod.a", "mod.target", CodeEdge::calls());
        graph.add_edge_by_name("mod.b", "mod.target", CodeEdge::calls());

        let ctx = DetectorContext::build(&graph, &[], None);
        let callers = &ctx.callers_by_qn["mod.target"];
        assert_eq!(callers.len(), 2);
        assert!(callers.contains(&"mod.a".to_string()));
        assert!(callers.contains(&"mod.b".to_string()));
    }

    #[test]
    fn test_multiple_children_for_same_parent() {
        let graph = GraphStore::in_memory();

        graph.add_node(
            CodeNode::class("Base", "test.py").with_qualified_name("mod.Base"),
        );
        graph.add_node(
            CodeNode::class("ChildA", "test.py").with_qualified_name("mod.ChildA"),
        );
        graph.add_node(
            CodeNode::class("ChildB", "test.py").with_qualified_name("mod.ChildB"),
        );
        graph.add_edge_by_name("mod.ChildA", "mod.Base", CodeEdge::inherits());
        graph.add_edge_by_name("mod.ChildB", "mod.Base", CodeEdge::inherits());

        let ctx = DetectorContext::build(&graph, &[], None);
        let children = &ctx.class_children["mod.Base"];
        assert_eq!(children.len(), 2);
        assert!(children.contains(&"mod.ChildA".to_string()));
        assert!(children.contains(&"mod.ChildB".to_string()));
    }

    #[test]
    fn test_value_store_stored_when_provided() {
        let graph = GraphStore::in_memory();
        let store = Arc::new(crate::values::store::ValueStore::new());
        let ctx = DetectorContext::build(&graph, &[], Some(store));
        assert!(ctx.value_store.is_some());
    }

    #[test]
    fn test_value_store_none_when_not_provided() {
        let graph = GraphStore::in_memory();
        let ctx = DetectorContext::build(&graph, &[], None);
        assert!(ctx.value_store.is_none());
    }
}
