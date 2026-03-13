//! Reachability analysis, public API detection, and decorator indexing.
//!
//! Pre-computed once during the startup phase and shared with all detectors
//! via `AnalysisContext`.

use crate::graph::GraphQuery;
use std::collections::{HashMap, HashSet, VecDeque};

/// Index of functions reachable from entry points via BFS on the call graph.
pub struct ReachabilityIndex {
    reachable: HashSet<String>,
}

impl ReachabilityIndex {
    /// Build reachability from all entry points (exported or zero fan-in functions).
    pub fn build(graph: &dyn GraphQuery) -> Self {
        let interner = graph.interner();
        let functions = graph.get_functions_shared();

        // Entry points: exported, or has zero callers (could be main/init/handler)
        let mut queue: VecDeque<String> = VecDeque::new();
        for func in functions.iter() {
            let qn = func.qn(interner);
            if func.is_exported() || graph.call_fan_in(qn) == 0 {
                queue.push_back(qn.to_string());
            }
        }

        // BFS from all entry points
        let mut reachable = HashSet::new();
        while let Some(qn) = queue.pop_front() {
            if !reachable.insert(qn.clone()) {
                continue;
            }
            for callee in graph.get_callees(&qn) {
                let cqn = callee.qn(interner).to_string();
                if !reachable.contains(&cqn) {
                    queue.push_back(cqn);
                }
            }
        }

        Self { reachable }
    }

    /// Create an empty index (for tests or when no graph is available).
    pub fn empty() -> Self {
        Self {
            reachable: HashSet::new(),
        }
    }

    /// Check if a function is reachable from any entry point.
    pub fn is_reachable(&self, qn: &str) -> bool {
        self.reachable.contains(qn)
    }

    /// Number of reachable functions.
    pub fn reachable_count(&self) -> usize {
        self.reachable.len()
    }
}

/// Build a set of exported/public function and class qualified names.
pub fn build_public_api(graph: &dyn GraphQuery) -> HashSet<String> {
    let interner = graph.interner();
    let mut api = HashSet::new();

    for func in graph.get_functions_shared().iter() {
        if func.is_exported() || func.is_public() {
            api.insert(func.qn(interner).to_string());
        }
    }
    for class in graph.get_classes_shared().iter() {
        if class.is_exported() || class.is_public() {
            api.insert(class.qn(interner).to_string());
        }
    }
    api
}

/// Build a pre-parsed decorator index from graph ExtraProps.
///
/// Maps function qualified names to their parsed decorator lists.
pub fn build_decorator_index(graph: &dyn GraphQuery) -> HashMap<String, Vec<String>> {
    let interner = graph.interner();
    let functions = graph.get_functions_shared();
    let mut index = HashMap::new();

    for func in functions.iter() {
        if func.has_decorators() {
            let qn = func.qn(interner);
            if let Some(props) = graph.extra_props(func.qualified_name) {
                if let Some(decs) = &props.decorators {
                    let dec_str = interner.resolve(*decs);
                    let parsed: Vec<String> = dec_str
                        .split(',')
                        .map(|s| s.trim().to_string())
                        .filter(|s| !s.is_empty())
                        .collect();
                    if !parsed.is_empty() {
                        index.insert(qn.to_string(), parsed);
                    }
                }
            }
        }
    }
    index
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeEdge, CodeNode, GraphStore};

    #[test]
    fn test_bfs_reaches_callees() {
        let graph = GraphStore::in_memory();
        // A (entry, zero fan-in) -> B -> C
        let a = CodeNode::function("a", "src/main.py")
            .with_qualified_name("src/main.py::a");
        let b = CodeNode::function("b", "src/main.py")
            .with_qualified_name("src/main.py::b");
        let c = CodeNode::function("c", "src/main.py")
            .with_qualified_name("src/main.py::c");
        graph.add_node(a);
        graph.add_node(b);
        graph.add_node(c);
        graph.add_edge_by_name("src/main.py::a", "src/main.py::b", CodeEdge::calls());
        graph.add_edge_by_name("src/main.py::b", "src/main.py::c", CodeEdge::calls());

        let idx = ReachabilityIndex::build(&graph);
        assert!(idx.is_reachable("src/main.py::a"));
        assert!(idx.is_reachable("src/main.py::b"));
        assert!(idx.is_reachable("src/main.py::c"));
        assert_eq!(idx.reachable_count(), 3);
    }

    #[test]
    fn test_unreachable_function_not_in_set() {
        let graph = GraphStore::in_memory();
        // A -> B, D is isolated (but zero fan-in, so it's an entry point too)
        let a = CodeNode::function("a", "src/main.py")
            .with_qualified_name("src/main.py::a");
        let b = CodeNode::function("b", "src/main.py")
            .with_qualified_name("src/main.py::b");
        let d = CodeNode::function("d", "src/main.py")
            .with_qualified_name("src/main.py::d");
        graph.add_node(a);
        graph.add_node(b);
        graph.add_node(d);
        graph.add_edge_by_name("src/main.py::a", "src/main.py::b", CodeEdge::calls());

        // D has zero fan-in so it's also an entry point and reachable
        let idx = ReachabilityIndex::build(&graph);
        assert!(idx.is_reachable("src/main.py::a"));
        assert!(idx.is_reachable("src/main.py::b"));
        assert!(idx.is_reachable("src/main.py::d"));

        // Something that doesn't exist at all is not reachable
        assert!(!idx.is_reachable("src/main.py::nonexistent"));
    }

    #[test]
    fn test_cycle_handling() {
        let graph = GraphStore::in_memory();
        // A -> B -> A (cycle). A is entry point (exported).
        let mut a = CodeNode::function("a", "src/main.py")
            .with_qualified_name("src/main.py::a");
        a.flags |= crate::graph::store_models::FLAG_IS_EXPORTED;
        let b = CodeNode::function("b", "src/main.py")
            .with_qualified_name("src/main.py::b");
        graph.add_node(a);
        graph.add_node(b);
        graph.add_edge_by_name("src/main.py::a", "src/main.py::b", CodeEdge::calls());
        graph.add_edge_by_name("src/main.py::b", "src/main.py::a", CodeEdge::calls());

        let idx = ReachabilityIndex::build(&graph);
        // Both should be reachable (cycle doesn't cause infinite loop)
        assert!(idx.is_reachable("src/main.py::a"));
        assert!(idx.is_reachable("src/main.py::b"));
    }

    #[test]
    fn test_empty_graph() {
        let graph = GraphStore::in_memory();
        let idx = ReachabilityIndex::build(&graph);
        assert_eq!(idx.reachable_count(), 0);
        assert!(!idx.is_reachable("anything"));
    }

    #[test]
    fn test_empty_constructor() {
        let idx = ReachabilityIndex::empty();
        assert_eq!(idx.reachable_count(), 0);
        assert!(!idx.is_reachable("anything"));
    }

    #[test]
    fn test_build_public_api() {
        let graph = GraphStore::in_memory();
        let mut exported_fn = CodeNode::function("exported_fn", "src/lib.py")
            .with_qualified_name("src/lib.py::exported_fn");
        exported_fn.flags |= crate::graph::store_models::FLAG_IS_EXPORTED;

        let mut public_fn = CodeNode::function("public_fn", "src/lib.py")
            .with_qualified_name("src/lib.py::public_fn");
        public_fn.flags |= crate::graph::store_models::FLAG_IS_PUBLIC;

        let private_fn = CodeNode::function("private_fn", "src/lib.py")
            .with_qualified_name("src/lib.py::private_fn");

        let mut public_class = CodeNode::class("MyClass", "src/lib.py")
            .with_qualified_name("src/lib.py::MyClass");
        public_class.flags |= crate::graph::store_models::FLAG_IS_EXPORTED;

        graph.add_node(exported_fn);
        graph.add_node(public_fn);
        graph.add_node(private_fn);
        graph.add_node(public_class);

        let api = build_public_api(&graph);
        assert!(api.contains("src/lib.py::exported_fn"));
        assert!(api.contains("src/lib.py::public_fn"));
        assert!(!api.contains("src/lib.py::private_fn"));
        assert!(api.contains("src/lib.py::MyClass"));
    }

    #[test]
    fn test_build_decorator_index_empty() {
        let graph = GraphStore::in_memory();
        let idx = build_decorator_index(&graph);
        assert!(idx.is_empty());
    }
}
