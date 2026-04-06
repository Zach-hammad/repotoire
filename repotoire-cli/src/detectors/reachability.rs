//! Reachability analysis, public API detection, and decorator indexing.
//!
//! Pre-computed once during the startup phase and shared with all detectors
//! via `AnalysisContext`.

use crate::graph::builder::GraphBuilder;
use crate::graph::{GraphQuery, GraphQueryExt};
use std::collections::{HashMap, HashSet, VecDeque};

/// Index of functions reachable from entry points via BFS on the call graph.
#[derive(Clone, serde::Serialize, serde::Deserialize)]
pub struct ReachabilityIndex {
    reachable: HashSet<String>,
}

impl ReachabilityIndex {
    /// Create an empty index (no functions reachable).
    pub fn empty() -> Self {
        Self {
            reachable: HashSet::new(),
        }
    }

    /// Build reachability from all entry points (exported or zero fan-in functions).
    ///
    /// Uses NodeIndex-based API when available (CodeGraph), avoiding
    /// Vec<CodeNode> cloning in per-function callee lookups.
    pub fn build(graph: &dyn GraphQuery) -> Self {
        let interner = graph.interner();
        let func_idxs = graph.functions_idx();

        // Fast path: use NodeIndex-based API if available (returns non-empty for CodeGraph)
        if !func_idxs.is_empty() {
            return Self::build_indexed(graph, interner, func_idxs);
        }

        // Fallback: old API for non-CodeGraph implementors
        let functions = graph.get_functions_shared();

        let mut queue: VecDeque<String> = VecDeque::new();
        for func in functions.iter() {
            let qn = func.qn(interner);
            if func.is_exported() || graph.call_fan_in(qn) == 0 {
                queue.push_back(qn.to_string());
            }
        }

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

    /// Build using NodeIndex-based API (zero-copy BFS).
    fn build_indexed(
        graph: &dyn GraphQuery,
        interner: &crate::graph::interner::StringInterner,
        func_idxs: &[crate::graph::node_index::NodeIndex],
    ) -> Self {
        use crate::graph::node_index::NodeIndex;

        // Entry points: exported, or has zero callers
        let mut queue: VecDeque<NodeIndex> = VecDeque::new();
        let mut visited: HashSet<NodeIndex> = HashSet::new();

        for &idx in func_idxs {
            if let Some(func) = graph.node_idx(idx) {
                if func.is_exported() || graph.call_fan_in_idx(idx) == 0 {
                    queue.push_back(idx);
                }
            }
        }

        // BFS from all entry points
        while let Some(idx) = queue.pop_front() {
            if !visited.insert(idx) {
                continue;
            }
            for &callee_idx in graph.callees_idx(idx) {
                if !visited.contains(&callee_idx) {
                    queue.push_back(callee_idx);
                }
            }
        }

        // Convert to string set for the public API
        let reachable: HashSet<String> = visited
            .iter()
            .filter_map(|&idx| graph.node_idx(idx))
            .map(|n| n.qn(interner).to_string())
            .collect();

        Self { reachable }
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
///
/// Uses NodeIndex-based API when available (CodeGraph).
pub fn build_public_api(graph: &dyn GraphQuery) -> HashSet<String> {
    let interner = graph.interner();
    let mut api = HashSet::new();

    for &idx in graph.functions_idx() {
        if let Some(func) = graph.node_idx(idx) {
            if func.is_exported() || func.is_public() {
                api.insert(func.qn(interner).to_string());
            }
        }
    }
    for &idx in graph.classes_idx() {
        if let Some(class) = graph.node_idx(idx) {
            if class.is_exported() || class.is_public() {
                api.insert(class.qn(interner).to_string());
            }
        }
    }

    // Fallback: if NodeIndex API returned nothing, try old API
    if api.is_empty() && (!graph.functions_idx().is_empty() || !graph.classes_idx().is_empty()) {
        return api; // Indexes available but empty — genuinely no public API
    }
    if graph.functions_idx().is_empty() {
        // Old implementor: use legacy API
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
    }
    api
}

/// Build a pre-parsed decorator index from graph ExtraProps.
///
/// Maps function qualified names to their parsed decorator lists.
/// Uses NodeIndex-based API when available (CodeGraph).
pub fn build_decorator_index(graph: &dyn GraphQuery) -> HashMap<String, Vec<String>> {
    let interner = graph.interner();
    let func_idxs = graph.functions_idx();
    let mut index = HashMap::new();

    if !func_idxs.is_empty() {
        // NodeIndex-based path (CodeGraph)
        for &idx in func_idxs {
            if let Some(func) = graph.node_idx(idx) {
                if func.has_decorators() {
                    let qn = func.qn(interner);
                    if let Some(props) = graph.extra_props_ref(func.qualified_name) {
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
        }
    } else {
        // Fallback: old API
        let functions = graph.get_functions_shared();
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
    }
    index
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::builder::GraphBuilder;
    use crate::graph::{CodeEdge, CodeNode};

    #[test]
    fn test_bfs_reaches_callees() {
        let mut graph = GraphBuilder::new();
        // A (entry, zero fan-in) -> B -> C
        let a = CodeNode::function("a", "src/main.py").with_qualified_name("src/main.py::a");
        let b = CodeNode::function("b", "src/main.py").with_qualified_name("src/main.py::b");
        let c = CodeNode::function("c", "src/main.py").with_qualified_name("src/main.py::c");
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
        let mut graph = GraphBuilder::new();
        // A -> B, D is isolated (but zero fan-in, so it's an entry point too)
        let a = CodeNode::function("a", "src/main.py").with_qualified_name("src/main.py::a");
        let b = CodeNode::function("b", "src/main.py").with_qualified_name("src/main.py::b");
        let d = CodeNode::function("d", "src/main.py").with_qualified_name("src/main.py::d");
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
        let mut graph = GraphBuilder::new();
        // A -> B -> A (cycle). A is entry point (exported).
        let mut a = CodeNode::function("a", "src/main.py").with_qualified_name("src/main.py::a");
        a.flags |= crate::graph::store_models::FLAG_IS_EXPORTED;
        let b = CodeNode::function("b", "src/main.py").with_qualified_name("src/main.py::b");
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
        let graph = GraphBuilder::new();
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
        let mut graph = GraphBuilder::new();
        let mut exported_fn = CodeNode::function("exported_fn", "src/lib.py")
            .with_qualified_name("src/lib.py::exported_fn");
        exported_fn.flags |= crate::graph::store_models::FLAG_IS_EXPORTED;

        let mut public_fn = CodeNode::function("public_fn", "src/lib.py")
            .with_qualified_name("src/lib.py::public_fn");
        public_fn.flags |= crate::graph::store_models::FLAG_IS_PUBLIC;

        let private_fn = CodeNode::function("private_fn", "src/lib.py")
            .with_qualified_name("src/lib.py::private_fn");

        let mut public_class =
            CodeNode::class("MyClass", "src/lib.py").with_qualified_name("src/lib.py::MyClass");
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
        let graph = GraphBuilder::new();
        let idx = build_decorator_index(&graph);
        assert!(idx.is_empty());
    }
}
