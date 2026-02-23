//! Shared query cache for detector parallelization
//!
//! Caches common graph data, enabling O(1) lookups instead of
//! repeated graph queries across detectors.

use crate::graph::GraphQuery;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};

/// Cached function node data
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct FunctionData {
    pub qualified_name: String,
    pub file_path: String,
    pub line_start: u32,
    pub line_end: u32,
    pub complexity: i32,
    pub loc: i32,
    pub parameters: Vec<String>,
    pub return_type: Option<String>,
    pub is_async: bool,
    pub decorators: Vec<String>,
    pub docstring: Option<String>,
}

/// Cached class node data
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ClassData {
    pub qualified_name: String,
    pub file_path: String,
    pub line_start: u32,
    pub line_end: u32,
    pub complexity: i32,
    pub method_count: i32,
    pub methods: Vec<String>,
}

/// Cached file node data
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct FileData {
    pub file_path: String,
    pub loc: i64,
    pub language: String,
}

/// Query cache for detector parallelization
#[derive(Debug, Clone, Default)]
pub struct QueryCache {
    /// Functions by qualified name
    pub functions: HashMap<String, FunctionData>,
    /// Classes by qualified name
    pub classes: HashMap<String, ClassData>,
    /// Files by path
    pub files: HashMap<String, FileData>,
    /// Call edges: caller -> [callees]
    pub calls: HashMap<String, HashSet<String>>,
    /// Import edges: file -> [imported files]
    pub imports: HashMap<String, HashSet<String>>,
    /// Inheritance: child class -> parent classes
    pub inheritance: HashMap<String, HashSet<String>>,
    /// Functions by file
    pub functions_by_file: HashMap<String, Vec<String>>,
    /// Classes by file
    pub classes_by_file: HashMap<String, Vec<String>>,
    /// Callers for each function (fan-in)
    pub callers: HashMap<String, HashSet<String>>,
}

impl QueryCache {
    /// Create a new empty cache
    pub fn new() -> Self {
        Self::default()
    }

    /// Build a fully populated cache from a graph store.
    ///
    /// Queries all functions, classes, files, and edge relationships once,
    /// then organises them into hash maps for O(1) lookups by detectors.
    pub fn from_graph(graph: &dyn GraphQuery) -> Self {
        let mut cache = Self::new();

        // --- Functions ---
        for node in graph.get_functions() {
            let qn = node.qualified_name.clone();
            let file_path = node.file_path.clone();

            let parameters: Vec<String> = node
                .properties
                .get("parameters")
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|p| p.as_str().map(String::from))
                        .collect()
                })
                .unwrap_or_default();

            let decorators: Vec<String> = node
                .properties
                .get("decorators")
                .and_then(|v| {
                    // Stored as either a JSON array or a comma-separated string
                    if let Some(arr) = v.as_array() {
                        Some(
                            arr.iter()
                                .filter_map(|d| d.as_str().map(String::from))
                                .collect(),
                        )
                    } else {
                        v.as_str().map(|s| {
                            s.split(',')
                                .map(|d| d.trim().to_string())
                                .filter(|d| !d.is_empty())
                                .collect()
                        })
                    }
                })
                .unwrap_or_default();

            let data = FunctionData {
                qualified_name: qn.clone(),
                file_path: file_path.clone(),
                line_start: node.line_start,
                line_end: node.line_end,
                complexity: node.complexity().unwrap_or(0) as i32,
                loc: node.get_i64("loc").unwrap_or_else(|| node.loc() as i64) as i32,
                parameters,
                return_type: node.get_str("returnType").map(String::from),
                is_async: node.get_bool("is_async").unwrap_or(false),
                decorators,
                docstring: node.get_str("docstring").map(String::from),
            };

            cache.functions.insert(qn.clone(), data);
            cache
                .functions_by_file
                .entry(file_path)
                .or_default()
                .push(qn);
        }

        // --- Classes ---
        for node in graph.get_classes() {
            let qn = node.qualified_name.clone();
            let file_path = node.file_path.clone();

            // Derive method list from functions whose qualified name is prefixed
            // by this class's qualified name (e.g. "mod.py::MyClass.method").
            let methods: Vec<String> = cache
                .functions_by_file
                .get(&file_path)
                .map(|fns| {
                    fns.iter()
                        .filter(|fqn| {
                            // Convention: method qn starts with "ClassName." after the file prefix
                            fqn.contains(&format!("{}.", node.name))
                        })
                        .cloned()
                        .collect()
                })
                .unwrap_or_default();

            let method_count_stored = node.get_i64("methodCount").unwrap_or(0) as i32;
            let method_count = if method_count_stored > 0 {
                method_count_stored
            } else {
                methods.len() as i32
            };

            let data = ClassData {
                qualified_name: qn.clone(),
                file_path: file_path.clone(),
                line_start: node.line_start,
                line_end: node.line_end,
                complexity: node.complexity().unwrap_or(0) as i32,
                method_count,
                methods,
            };

            cache.classes.insert(qn.clone(), data);
            cache
                .classes_by_file
                .entry(file_path)
                .or_default()
                .push(qn);
        }

        // --- Files ---
        for node in graph.get_files() {
            let path = node.file_path.clone();
            let data = FileData {
                file_path: path.clone(),
                loc: node.get_i64("loc").unwrap_or(0),
                language: node
                    .language
                    .clone()
                    .or_else(|| node.get_str("language").map(String::from))
                    .unwrap_or_default(),
            };
            cache.files.insert(path, data);
        }

        // --- Call edges ---
        for (caller, callee) in graph.get_calls() {
            cache
                .calls
                .entry(caller.clone())
                .or_default()
                .insert(callee.clone());
            cache.callers.entry(callee).or_default().insert(caller);
        }

        // --- Import edges ---
        for (importer, imported) in graph.get_imports() {
            cache
                .imports
                .entry(importer)
                .or_default()
                .insert(imported);
        }

        // --- Inheritance edges ---
        for (child, parent) in graph.get_inheritance() {
            cache
                .inheritance
                .entry(child)
                .or_default()
                .insert(parent);
        }

        cache
    }

    /// Fan-in for a function (number of callers)
    pub fn fan_in(&self, qn: &str) -> usize {
        self.callers.get(qn).map(|s| s.len()).unwrap_or(0)
    }

    /// Fan-out for a function (number of callees)
    pub fn fan_out(&self, qn: &str) -> usize {
        self.calls.get(qn).map(|s| s.len()).unwrap_or(0)
    }

    /// All functions in a file
    pub fn functions_in_file(&self, file_path: &str) -> Vec<&FunctionData> {
        self.functions_by_file
            .get(file_path)
            .map(|qns| qns.iter().filter_map(|qn| self.functions.get(qn)).collect())
            .unwrap_or_default()
    }

    /// All classes in a file
    pub fn classes_in_file(&self, file_path: &str) -> Vec<&ClassData> {
        self.classes_by_file
            .get(file_path)
            .map(|qns| qns.iter().filter_map(|qn| self.classes.get(qn)).collect())
            .unwrap_or_default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeEdge, CodeNode, GraphStore};

    /// Helper: build a small graph with two files, three functions, one class,
    /// call/import/inheritance edges, and return a cache built from it.
    fn sample_cache() -> QueryCache {
        let store = GraphStore::in_memory();

        // Files
        store.add_node(
            CodeNode::file("app.py")
                .with_language("python")
                .with_property("loc", 120i64),
        );
        store.add_node(
            CodeNode::file("utils.py")
                .with_language("python")
                .with_property("loc", 45i64),
        );

        // Functions
        store.add_node(
            CodeNode::function("main", "app.py")
                .with_qualified_name("app.py::main")
                .with_lines(1, 30)
                .with_property("complexity", 8i64)
                .with_property("loc", 30i64)
                .with_property("is_async", true),
        );
        store.add_node(
            CodeNode::function("helper", "utils.py")
                .with_qualified_name("utils.py::helper")
                .with_lines(1, 10)
                .with_property("complexity", 2i64)
                .with_property("loc", 10i64),
        );
        store.add_node(
            CodeNode::function("process", "app.py")
                .with_qualified_name("app.py::MyClass.process")
                .with_lines(40, 60)
                .with_property("complexity", 5i64)
                .with_property("loc", 21i64),
        );

        // Class
        store.add_node(
            CodeNode::class("MyClass", "app.py")
                .with_qualified_name("app.py::MyClass")
                .with_lines(35, 80)
                .with_property("methodCount", 3i64),
        );

        // Edges: main calls helper, main calls process
        store.add_edge_by_name("app.py::main", "utils.py::helper", CodeEdge::calls());
        store.add_edge_by_name("app.py::main", "app.py::MyClass.process", CodeEdge::calls());

        // Import: app.py imports utils.py
        store.add_edge_by_name("app.py", "utils.py", CodeEdge::imports());

        // Inheritance: MyClass inherits from BaseClass (add BaseClass first)
        store.add_node(
            CodeNode::class("BaseClass", "utils.py")
                .with_qualified_name("utils.py::BaseClass")
                .with_lines(20, 40),
        );
        store.add_edge_by_name("app.py::MyClass", "utils.py::BaseClass", CodeEdge::inherits());

        QueryCache::from_graph(&store)
    }

    #[test]
    fn test_from_graph_functions() {
        let cache = sample_cache();

        assert_eq!(cache.functions.len(), 3);
        assert!(cache.functions.contains_key("app.py::main"));
        assert!(cache.functions.contains_key("utils.py::helper"));
        assert!(cache.functions.contains_key("app.py::MyClass.process"));

        let main_fn = &cache.functions["app.py::main"];
        assert_eq!(main_fn.file_path, "app.py");
        assert_eq!(main_fn.line_start, 1);
        assert_eq!(main_fn.line_end, 30);
        assert_eq!(main_fn.complexity, 8);
        assert_eq!(main_fn.loc, 30);
        assert!(main_fn.is_async);
    }

    #[test]
    fn test_from_graph_classes() {
        let cache = sample_cache();

        assert_eq!(cache.classes.len(), 2);
        let my_class = &cache.classes["app.py::MyClass"];
        assert_eq!(my_class.file_path, "app.py");
        assert_eq!(my_class.method_count, 3); // from stored methodCount
        assert_eq!(my_class.line_start, 35);
        assert_eq!(my_class.line_end, 80);
    }

    #[test]
    fn test_from_graph_files() {
        let cache = sample_cache();

        assert_eq!(cache.files.len(), 2);

        let app_file = &cache.files["app.py"];
        assert_eq!(app_file.loc, 120);
        assert_eq!(app_file.language, "python");

        let utils_file = &cache.files["utils.py"];
        assert_eq!(utils_file.loc, 45);
    }

    #[test]
    fn test_from_graph_calls_and_callers() {
        let cache = sample_cache();

        // main calls two functions
        assert_eq!(cache.fan_out("app.py::main"), 2);
        assert!(cache.calls["app.py::main"].contains("utils.py::helper"));
        assert!(cache.calls["app.py::main"].contains("app.py::MyClass.process"));

        // helper is called by main
        assert_eq!(cache.fan_in("utils.py::helper"), 1);
        assert!(cache.callers["utils.py::helper"].contains("app.py::main"));

        // main has no callers
        assert_eq!(cache.fan_in("app.py::main"), 0);
    }

    #[test]
    fn test_from_graph_imports() {
        let cache = sample_cache();

        assert!(cache.imports.contains_key("app.py"));
        assert!(cache.imports["app.py"].contains("utils.py"));
    }

    #[test]
    fn test_from_graph_inheritance() {
        let cache = sample_cache();

        assert!(cache.inheritance.contains_key("app.py::MyClass"));
        assert!(cache.inheritance["app.py::MyClass"].contains("utils.py::BaseClass"));
    }

    #[test]
    fn test_from_graph_functions_by_file() {
        let cache = sample_cache();

        let app_fns = &cache.functions_by_file["app.py"];
        assert_eq!(app_fns.len(), 2);
        assert!(app_fns.contains(&"app.py::main".to_string()));
        assert!(app_fns.contains(&"app.py::MyClass.process".to_string()));

        let util_fns = &cache.functions_by_file["utils.py"];
        assert_eq!(util_fns.len(), 1);
        assert!(util_fns.contains(&"utils.py::helper".to_string()));
    }

    #[test]
    fn test_from_graph_classes_by_file() {
        let cache = sample_cache();

        let app_classes = &cache.classes_by_file["app.py"];
        assert_eq!(app_classes.len(), 1);
        assert!(app_classes.contains(&"app.py::MyClass".to_string()));
    }

    #[test]
    fn test_from_graph_helper_methods() {
        let cache = sample_cache();

        // functions_in_file
        let fns = cache.functions_in_file("app.py");
        assert_eq!(fns.len(), 2);

        // classes_in_file
        let cls = cache.classes_in_file("app.py");
        assert_eq!(cls.len(), 1);
        assert_eq!(cls[0].qualified_name, "app.py::MyClass");

        // empty file returns empty
        assert!(cache.functions_in_file("nonexistent.py").is_empty());
        assert!(cache.classes_in_file("nonexistent.py").is_empty());
    }

    #[test]
    fn test_from_graph_empty() {
        let store = GraphStore::in_memory();
        let cache = QueryCache::from_graph(&store);

        assert!(cache.functions.is_empty());
        assert!(cache.classes.is_empty());
        assert!(cache.files.is_empty());
        assert!(cache.calls.is_empty());
        assert!(cache.imports.is_empty());
        assert!(cache.inheritance.is_empty());
        assert_eq!(cache.fan_in("anything"), 0);
        assert_eq!(cache.fan_out("anything"), 0);
    }

    #[test]
    fn test_from_graph_function_defaults() {
        // Function with no optional properties set
        let store = GraphStore::in_memory();
        store.add_node(
            CodeNode::function("bare", "test.py")
                .with_qualified_name("test.py::bare")
                .with_lines(5, 10),
        );

        let cache = QueryCache::from_graph(&store);
        let f = &cache.functions["test.py::bare"];

        assert_eq!(f.complexity, 0);
        assert!(!f.is_async);
        assert!(f.return_type.is_none());
        assert!(f.docstring.is_none());
        assert!(f.parameters.is_empty());
        assert!(f.decorators.is_empty());
        // loc falls back to line-based calculation: 10 - 5 + 1 = 6
        assert_eq!(f.loc, 6);
    }

    #[test]
    fn test_from_graph_class_method_count_fallback() {
        // Class without methodCount property should fall back to discovered methods count
        let store = GraphStore::in_memory();
        store.add_node(
            CodeNode::class("Foo", "mod.py")
                .with_qualified_name("mod.py::Foo")
                .with_lines(1, 50),
        );
        store.add_node(
            CodeNode::function("bar", "mod.py")
                .with_qualified_name("mod.py::Foo.bar")
                .with_lines(5, 15),
        );
        store.add_node(
            CodeNode::function("baz", "mod.py")
                .with_qualified_name("mod.py::Foo.baz")
                .with_lines(20, 30),
        );

        let cache = QueryCache::from_graph(&store);
        let cls = &cache.classes["mod.py::Foo"];
        assert_eq!(cls.method_count, 2);
        assert_eq!(cls.methods.len(), 2);
    }
}
