//! Caching wrapper for GraphQuery that memoizes expensive full-scan methods.
//!
//! Wraps a `&dyn GraphQuery` and caches results of methods that scan all nodes
//! or all edges. Indexed/cheap methods delegate directly to the inner query.
//!
//! Used by DetectorEngine to avoid redundant graph scans across multiple
//! detectors in the same analysis run.

use super::store_models::CodeNode;
use super::traits::GraphQuery;
use std::collections::HashMap;
use std::sync::OnceLock;

/// Caching wrapper for GraphQuery.
///
/// Memoizes expensive full-scan methods (get_functions, get_classes, get_files,
/// get_calls, get_imports, get_inheritance) on first access. Cheap indexed
/// methods (get_node, get_callers, get_callees, call_fan_in, call_fan_out,
/// etc.) delegate directly to the inner GraphQuery.
pub struct CachedGraphQuery<'a> {
    inner: &'a dyn GraphQuery,
    functions: OnceLock<Vec<CodeNode>>,
    classes: OnceLock<Vec<CodeNode>>,
    files: OnceLock<Vec<CodeNode>>,
    calls: OnceLock<Vec<(String, String)>>,
    imports: OnceLock<Vec<(String, String)>>,
    inheritance: OnceLock<Vec<(String, String)>>,
}

impl<'a> CachedGraphQuery<'a> {
    pub fn new(inner: &'a dyn GraphQuery) -> Self {
        Self {
            inner,
            functions: OnceLock::new(),
            classes: OnceLock::new(),
            files: OnceLock::new(),
            calls: OnceLock::new(),
            imports: OnceLock::new(),
            inheritance: OnceLock::new(),
        }
    }
}

impl GraphQuery for CachedGraphQuery<'_> {
    // === Cached methods (expensive full-scan) ===

    fn get_functions(&self) -> Vec<CodeNode> {
        self.functions
            .get_or_init(|| self.inner.get_functions())
            .clone()
    }

    fn get_classes(&self) -> Vec<CodeNode> {
        self.classes
            .get_or_init(|| self.inner.get_classes())
            .clone()
    }

    fn get_files(&self) -> Vec<CodeNode> {
        self.files
            .get_or_init(|| self.inner.get_files())
            .clone()
    }

    fn get_calls(&self) -> Vec<(String, String)> {
        self.calls
            .get_or_init(|| self.inner.get_calls())
            .clone()
    }

    fn get_imports(&self) -> Vec<(String, String)> {
        self.imports
            .get_or_init(|| self.inner.get_imports())
            .clone()
    }

    fn get_inheritance(&self) -> Vec<(String, String)> {
        self.inheritance
            .get_or_init(|| self.inner.get_inheritance())
            .clone()
    }

    // === Delegated methods (already indexed/cheap) ===

    fn get_functions_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        self.inner.get_functions_in_file(file_path)
    }

    fn get_classes_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        self.inner.get_classes_in_file(file_path)
    }

    fn get_node(&self, qn: &str) -> Option<CodeNode> {
        self.inner.get_node(qn)
    }

    fn get_callers(&self, qn: &str) -> Vec<CodeNode> {
        self.inner.get_callers(qn)
    }

    fn get_callees(&self, qn: &str) -> Vec<CodeNode> {
        self.inner.get_callees(qn)
    }

    fn call_fan_in(&self, qn: &str) -> usize {
        self.inner.call_fan_in(qn)
    }

    fn call_fan_out(&self, qn: &str) -> usize {
        self.inner.call_fan_out(qn)
    }

    fn get_child_classes(&self, qn: &str) -> Vec<CodeNode> {
        self.inner.get_child_classes(qn)
    }

    fn get_importers(&self, qn: &str) -> Vec<CodeNode> {
        self.inner.get_importers(qn)
    }

    fn find_import_cycles(&self) -> Vec<Vec<String>> {
        self.inner.find_import_cycles()
    }

    fn stats(&self) -> HashMap<String, i64> {
        self.inner.stats()
    }

    fn find_function_at(&self, file_path: &str, line: u32) -> Option<CodeNode> {
        self.inner.find_function_at(file_path, line)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeEdge, CodeNode, GraphStore};

    #[test]
    fn test_cached_get_functions_returns_same_data() {
        let store = GraphStore::in_memory();
        store.add_node(CodeNode::function("foo", "app.py").with_qualified_name("mod.foo"));
        store.add_node(CodeNode::function("bar", "app.py").with_qualified_name("mod.bar"));

        let cached = CachedGraphQuery::new(&store);
        let first = cached.get_functions();
        let second = cached.get_functions();

        assert_eq!(first.len(), second.len());
        assert_eq!(first.len(), 2);
    }

    #[test]
    fn test_cached_delegates_indexed_methods() {
        let store = GraphStore::in_memory();
        store.add_node(CodeNode::function("foo", "app.py").with_qualified_name("mod.foo"));

        let cached = CachedGraphQuery::new(&store);
        let node = cached.get_node("mod.foo");
        assert!(node.is_some());
        assert_eq!(node.unwrap().name, "foo");
    }

    #[test]
    fn test_cached_get_calls_returns_same_data() {
        let store = GraphStore::in_memory();
        store.add_node(CodeNode::function("a", "a.py").with_qualified_name("a"));
        store.add_node(CodeNode::function("b", "b.py").with_qualified_name("b"));
        store.add_edge_by_name("a", "b", CodeEdge::calls());

        let cached = CachedGraphQuery::new(&store);
        let first = cached.get_calls();
        let second = cached.get_calls();

        assert_eq!(first.len(), second.len());
        assert_eq!(first.len(), 1);
    }
}
