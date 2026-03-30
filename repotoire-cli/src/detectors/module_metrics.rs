//! Per-module coupling/cohesion metrics and per-class cohesion (LCOM4).
//!
//! Pre-computed once during the startup phase and shared with all detectors
//! via `AnalysisContext`.

use crate::graph::builder::GraphBuilder;
use crate::graph::{GraphQuery, GraphQueryExt};
use std::collections::{HashMap, HashSet};

/// Per-module metrics computed from the call graph.
pub struct ModuleMetrics {
    pub function_count: usize,
    pub class_count: usize,
    pub incoming_calls: usize,
    pub outgoing_calls: usize,
    pub internal_calls: usize,
}

impl ModuleMetrics {
    /// Coupling ratio: fraction of calls that cross module boundaries.
    pub fn coupling(&self) -> f64 {
        let total = self.incoming_calls + self.outgoing_calls + self.internal_calls;
        if total == 0 {
            return 0.0;
        }
        (self.incoming_calls + self.outgoing_calls) as f64 / total as f64
    }

    /// Cohesion ratio: fraction of calls that stay within the module.
    pub fn cohesion(&self) -> f64 {
        let total = self.incoming_calls + self.outgoing_calls + self.internal_calls;
        if total == 0 {
            return 1.0;
        }
        self.internal_calls as f64 / total as f64
    }
}

/// Build per-module metrics from the call graph.
///
/// A "module" is the parent directory of a file.
/// Uses NodeIndex-based API when available (CodeGraph) to avoid
/// Vec<CodeNode> cloning in per-function callee lookups.
pub fn build_module_metrics(graph: &dyn GraphQuery) -> HashMap<String, ModuleMetrics> {
    let interner = graph.interner();
    let func_idxs = graph.functions_idx();

    // Use NodeIndex-based path when available (non-empty = CodeGraph)
    if !func_idxs.is_empty() {
        return build_module_metrics_indexed(graph, interner);
    }

    // Fallback: old API
    let functions = graph.get_functions_shared();
    let classes = graph.get_classes_shared();

    let mut metrics: HashMap<String, ModuleMetrics> = HashMap::new();

    for func in functions.iter() {
        let path = func.path(interner);
        let module = extract_module(path);
        let entry = metrics.entry(module).or_insert(ModuleMetrics {
            function_count: 0,
            class_count: 0,
            incoming_calls: 0,
            outgoing_calls: 0,
            internal_calls: 0,
        });
        entry.function_count += 1;
    }

    for class in classes.iter() {
        let path = class.path(interner);
        let module = extract_module(path);
        let entry = metrics.entry(module).or_insert(ModuleMetrics {
            function_count: 0,
            class_count: 0,
            incoming_calls: 0,
            outgoing_calls: 0,
            internal_calls: 0,
        });
        entry.class_count += 1;
    }

    for func in functions.iter() {
        let caller_module = extract_module(func.path(interner));
        let qn = func.qn(interner);
        for callee in graph.get_callees(qn) {
            let callee_module = extract_module(callee.path(interner));
            if caller_module == callee_module {
                if let Some(m) = metrics.get_mut(&caller_module) {
                    m.internal_calls += 1;
                }
            } else {
                if let Some(m) = metrics.get_mut(&caller_module) {
                    m.outgoing_calls += 1;
                }
                if let Some(m) = metrics.get_mut(&callee_module) {
                    m.incoming_calls += 1;
                }
            }
        }
    }

    metrics
}

/// NodeIndex-based implementation for CodeGraph.
fn build_module_metrics_indexed(
    graph: &dyn GraphQuery,
    interner: &crate::graph::interner::StringInterner,
) -> HashMap<String, ModuleMetrics> {
    let func_idxs = graph.functions_idx();
    let class_idxs = graph.classes_idx();

    let mut metrics: HashMap<String, ModuleMetrics> = HashMap::new();

    for &idx in func_idxs {
        if let Some(func) = graph.node_idx(idx) {
            let path = func.path(interner);
            let module = extract_module(path);
            let entry = metrics.entry(module).or_insert(ModuleMetrics {
                function_count: 0,
                class_count: 0,
                incoming_calls: 0,
                outgoing_calls: 0,
                internal_calls: 0,
            });
            entry.function_count += 1;
        }
    }

    for &idx in class_idxs {
        if let Some(class) = graph.node_idx(idx) {
            let path = class.path(interner);
            let module = extract_module(path);
            let entry = metrics.entry(module).or_insert(ModuleMetrics {
                function_count: 0,
                class_count: 0,
                incoming_calls: 0,
                outgoing_calls: 0,
                internal_calls: 0,
            });
            entry.class_count += 1;
        }
    }

    for &func_idx in func_idxs {
        let Some(func) = graph.node_idx(func_idx) else {
            continue;
        };
        let caller_module = extract_module(func.path(interner));
        for &callee_idx in graph.callees_idx(func_idx) {
            let Some(callee) = graph.node_idx(callee_idx) else {
                continue;
            };
            let callee_module = extract_module(callee.path(interner));
            if caller_module == callee_module {
                if let Some(m) = metrics.get_mut(&caller_module) {
                    m.internal_calls += 1;
                }
            } else {
                if let Some(m) = metrics.get_mut(&caller_module) {
                    m.outgoing_calls += 1;
                }
                if let Some(m) = metrics.get_mut(&callee_module) {
                    m.incoming_calls += 1;
                }
            }
        }
    }

    metrics
}

/// Compute class cohesion (approximation of LCOM4).
///
/// For each class, finds methods within the class's line range,
/// then counts connected components where methods are connected
/// if one calls the other. Returns a normalized score:
/// - 1.0 = perfectly cohesive (single connected component, or 0-1 methods)
/// - Higher values = less cohesive (more disconnected method groups)
///
/// Uses NodeIndex-based API when available (CodeGraph) to avoid
/// Vec<CodeNode> cloning for both per-file function lookups and
/// per-method callee lookups.
pub fn build_class_cohesion(graph: &dyn GraphQuery) -> HashMap<String, f64> {
    let interner = graph.interner();
    let class_idxs = graph.classes_idx();

    // Use NodeIndex-based path when available (non-empty = CodeGraph)
    if !class_idxs.is_empty() {
        return build_class_cohesion_indexed(graph, interner);
    }

    // Fallback: old API
    let classes = graph.get_classes_shared();
    let mut cohesion = HashMap::new();

    for class in classes.iter() {
        let file_path = class.path(interner);
        let class_qn = class.qn(interner);

        let methods: Vec<_> = graph
            .get_functions_in_file(file_path)
            .into_iter()
            .filter(|f| f.line_start >= class.line_start && f.line_end <= class.line_end)
            .collect();

        if methods.len() <= 1 {
            cohesion.insert(class_qn.to_string(), 1.0);
            continue;
        }

        let n = methods.len();
        let mut parent: Vec<usize> = (0..n).collect();

        fn find(parent: &mut [usize], i: usize) -> usize {
            if parent[i] != i {
                parent[i] = find(parent, parent[i]);
            }
            parent[i]
        }

        fn union(parent: &mut [usize], a: usize, b: usize) {
            let ra = find(parent, a);
            let rb = find(parent, b);
            if ra != rb {
                parent[ra] = rb;
            }
        }

        for (i, m1) in methods.iter().enumerate() {
            let m1_qn = m1.qn(interner);
            let m1_callees = graph.get_callees(m1_qn);
            for (j, m2) in methods.iter().enumerate() {
                if i >= j {
                    continue;
                }
                let m2_qn = m2.qn(interner);
                let m2_callees = graph.get_callees(m2_qn);
                if m1_callees.iter().any(|c| c.qn(interner) == m2_qn)
                    || m2_callees.iter().any(|c| c.qn(interner) == m1_qn)
                {
                    union(&mut parent, i, j);
                }
            }
        }

        let components = (0..n)
            .map(|i| find(&mut parent, i))
            .collect::<HashSet<_>>()
            .len();
        let lcom = components as f64 / n as f64;
        cohesion.insert(class_qn.to_string(), lcom);
    }

    cohesion
}

/// NodeIndex-based class cohesion implementation for CodeGraph.
fn build_class_cohesion_indexed(
    graph: &dyn GraphQuery,
    interner: &crate::graph::interner::StringInterner,
) -> HashMap<String, f64> {
    let class_idxs = graph.classes_idx();
    let mut cohesion = HashMap::new();

    for &class_idx in class_idxs {
        let Some(class) = graph.node_idx(class_idx) else {
            continue;
        };
        let file_path = class.path(interner);
        let class_qn = class.qn(interner);

        let method_idxs: Vec<_> = graph
            .functions_in_file_idx(file_path)
            .iter()
            .copied()
            .filter(|&idx| {
                graph.node_idx(idx).is_some_and(|f| {
                    f.line_start >= class.line_start && f.line_end <= class.line_end
                })
            })
            .collect();

        if method_idxs.len() <= 1 {
            cohesion.insert(class_qn.to_string(), 1.0);
            continue;
        }

        let n = method_idxs.len();
        let mut parent: Vec<usize> = (0..n).collect();

        fn find(parent: &mut [usize], i: usize) -> usize {
            if parent[i] != i {
                parent[i] = find(parent, parent[i]);
            }
            parent[i]
        }

        fn union(parent: &mut [usize], a: usize, b: usize) {
            let ra = find(parent, a);
            let rb = find(parent, b);
            if ra != rb {
                parent[ra] = rb;
            }
        }

        for i in 0..n {
            let m1_callees = graph.callees_idx(method_idxs[i]);
            for j in (i + 1)..n {
                let m2_idx = method_idxs[j];
                let m1_idx = method_idxs[i];
                let m2_callees = graph.callees_idx(m2_idx);
                if m1_callees.contains(&m2_idx) || m2_callees.contains(&m1_idx) {
                    union(&mut parent, i, j);
                }
            }
        }

        let components = (0..n)
            .map(|i| find(&mut parent, i))
            .collect::<HashSet<_>>()
            .len();
        let lcom = components as f64 / n as f64;
        cohesion.insert(class_qn.to_string(), lcom);
    }

    cohesion
}

/// Extract module name from file path (parent directory).
fn extract_module(path: &str) -> String {
    std::path::Path::new(path)
        .parent()
        .and_then(|p| p.to_str())
        .unwrap_or("")
        .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::builder::GraphBuilder;
    use crate::graph::{CodeEdge, CodeNode};

    #[test]
    fn test_extract_module() {
        assert_eq!(extract_module("src/foo/bar.py"), "src/foo");
        assert_eq!(extract_module("main.py"), "");
        assert_eq!(extract_module("src/lib.rs"), "src");
    }

    #[test]
    fn test_module_metrics_coupling() {
        let m = ModuleMetrics {
            function_count: 5,
            class_count: 1,
            incoming_calls: 3,
            outgoing_calls: 2,
            internal_calls: 5,
        };
        // coupling = (3+2) / (3+2+5) = 0.5
        assert!((m.coupling() - 0.5).abs() < f64::EPSILON);
        // cohesion = 5 / 10 = 0.5
        assert!((m.cohesion() - 0.5).abs() < f64::EPSILON);
    }

    #[test]
    fn test_module_metrics_no_calls() {
        let m = ModuleMetrics {
            function_count: 2,
            class_count: 0,
            incoming_calls: 0,
            outgoing_calls: 0,
            internal_calls: 0,
        };
        assert!((m.coupling() - 0.0).abs() < f64::EPSILON);
        assert!((m.cohesion() - 1.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_build_module_metrics_basic() {
        let mut graph = GraphBuilder::new();

        let a =
            CodeNode::function("a", "src/mod_a/foo.py").with_qualified_name("src/mod_a/foo.py::a");
        let b =
            CodeNode::function("b", "src/mod_a/foo.py").with_qualified_name("src/mod_a/foo.py::b");
        let c =
            CodeNode::function("c", "src/mod_b/bar.py").with_qualified_name("src/mod_b/bar.py::c");

        graph.add_node(a);
        graph.add_node(b);
        graph.add_node(c);

        // a -> b (internal to mod_a), a -> c (cross-module)
        graph.add_edge_by_name(
            "src/mod_a/foo.py::a",
            "src/mod_a/foo.py::b",
            CodeEdge::calls(),
        );
        graph.add_edge_by_name(
            "src/mod_a/foo.py::a",
            "src/mod_b/bar.py::c",
            CodeEdge::calls(),
        );

        let metrics = build_module_metrics(&graph);

        let mod_a = metrics.get("src/mod_a").expect("mod_a should exist");
        assert_eq!(mod_a.function_count, 2);
        assert_eq!(mod_a.internal_calls, 1);
        assert_eq!(mod_a.outgoing_calls, 1);

        let mod_b = metrics.get("src/mod_b").expect("mod_b should exist");
        assert_eq!(mod_b.function_count, 1);
        assert_eq!(mod_b.incoming_calls, 1);
    }

    #[test]
    fn test_build_module_metrics_empty_graph() {
        let graph = GraphBuilder::new();
        let metrics = build_module_metrics(&graph);
        assert!(metrics.is_empty());
    }

    #[test]
    fn test_class_cohesion_single_method() {
        let mut graph = GraphBuilder::new();

        let class = CodeNode::class("Foo", "src/foo.py")
            .with_qualified_name("src/foo.py::Foo")
            .with_lines(1, 10);
        let method = CodeNode::function("do_thing", "src/foo.py")
            .with_qualified_name("src/foo.py::Foo.do_thing")
            .with_lines(2, 9);

        graph.add_node(class);
        graph.add_node(method);

        let cohesion = build_class_cohesion(&graph);
        // Single method = perfectly cohesive
        assert_eq!(cohesion.get("src/foo.py::Foo"), Some(&1.0));
    }

    #[test]
    fn test_class_cohesion_connected_methods() {
        let mut graph = GraphBuilder::new();

        let class = CodeNode::class("Foo", "src/foo.py")
            .with_qualified_name("src/foo.py::Foo")
            .with_lines(1, 20);
        let m1 = CodeNode::function("m1", "src/foo.py")
            .with_qualified_name("src/foo.py::Foo.m1")
            .with_lines(2, 9);
        let m2 = CodeNode::function("m2", "src/foo.py")
            .with_qualified_name("src/foo.py::Foo.m2")
            .with_lines(10, 19);

        graph.add_node(class);
        graph.add_node(m1);
        graph.add_node(m2);

        // m1 calls m2 -> connected, 1 component / 2 methods = 0.5
        graph.add_edge_by_name(
            "src/foo.py::Foo.m1",
            "src/foo.py::Foo.m2",
            CodeEdge::calls(),
        );

        let cohesion = build_class_cohesion(&graph);
        assert_eq!(cohesion.get("src/foo.py::Foo"), Some(&0.5));
    }

    #[test]
    fn test_class_cohesion_disconnected_methods() {
        let mut graph = GraphBuilder::new();

        let class = CodeNode::class("Foo", "src/foo.py")
            .with_qualified_name("src/foo.py::Foo")
            .with_lines(1, 30);
        let m1 = CodeNode::function("m1", "src/foo.py")
            .with_qualified_name("src/foo.py::Foo.m1")
            .with_lines(2, 9);
        let m2 = CodeNode::function("m2", "src/foo.py")
            .with_qualified_name("src/foo.py::Foo.m2")
            .with_lines(10, 19);
        let m3 = CodeNode::function("m3", "src/foo.py")
            .with_qualified_name("src/foo.py::Foo.m3")
            .with_lines(20, 29);

        graph.add_node(class);
        graph.add_node(m1);
        graph.add_node(m2);
        graph.add_node(m3);

        // No call edges between methods -> 3 components / 3 methods = 1.0
        let cohesion = build_class_cohesion(&graph);
        assert_eq!(cohesion.get("src/foo.py::Foo"), Some(&1.0));
    }

    #[test]
    fn test_build_class_cohesion_empty() {
        let graph = GraphBuilder::new();
        let cohesion = build_class_cohesion(&graph);
        assert!(cohesion.is_empty());
    }
}
