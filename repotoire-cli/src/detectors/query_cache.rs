//! Shared query cache for detector parallelization
//!
//! Prefetches common graph data once, enabling O(1) lookups instead of
//! repeated graph queries across 42+ detectors.
//!
//! Target: 9min → <2min analysis time.
//!
//! # Example
//!
//! ```ignore
//! let cache = QueryCache::new();
//! cache.prefetch(&graph_client)?;
//!
//! // In detectors:
//! for func in cache.functions.values() {
//!     if func.complexity > 10 {
//!         // ...
//!     }
//! }
//! ```

use crate::graph::GraphClient;
use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::time::Instant;
use tracing::{debug, info};

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
    pub loc: i32,
    pub is_abstract: bool,
    pub decorators: Vec<String>,
    pub docstring: Option<String>,
    pub method_count: usize,
}

/// Cached file node data
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct FileData {
    pub qualified_name: String,
    pub file_path: String,
    pub loc: i32,
    pub language: String,
}

/// Shared cache for common detector queries
///
/// Prefetches all graph data once at analysis start, then provides
/// O(1) lookups for detectors running in parallel.
#[derive(Debug, Default)]
pub struct QueryCache {
    // Node caches (keyed by qualified_name)
    pub functions: HashMap<String, FunctionData>,
    pub classes: HashMap<String, ClassData>,
    pub files: HashMap<String, FileData>,

    // Relationship caches
    pub calls: HashMap<String, HashSet<String>>,       // caller -> set of callees
    pub called_by: HashMap<String, HashSet<String>>,   // callee -> set of callers
    pub imports: HashMap<String, HashSet<String>>,     // file -> set of imported modules
    pub inherits: HashMap<String, HashSet<String>>,    // child -> set of parents
    pub inherited_by: HashMap<String, HashSet<String>>, // parent -> set of children
    pub contains: HashMap<String, HashSet<String>>,    // class -> set of methods
    pub contained_by: HashMap<String, String>,         // method -> class

    // Aggregates (computed after prefetch)
    pub total_functions: usize,
    pub total_classes: usize,
    pub total_files: usize,
    pub total_loc: i32,

    // Prefetch timing
    pub prefetch_time_ms: u64,
    prefetched: bool,
}

impl QueryCache {
    /// Create a new empty cache
    pub fn new() -> Self {
        Self::default()
    }

    /// Check if cache has been prefetched
    pub fn is_prefetched(&self) -> bool {
        self.prefetched
    }

    /// Prefetch all common graph data
    ///
    /// Call once before running detectors. Subsequent calls are no-ops.
    pub fn prefetch(&mut self, graph: &GraphClient) -> Result<()> {
        if self.prefetched {
            return Ok(());
        }

        let start = Instant::now();
        info!("QueryCache: Starting prefetch...");

        self.prefetch_functions(graph)?;
        self.prefetch_classes(graph)?;
        self.prefetch_files(graph)?;
        self.prefetch_calls(graph)?;
        self.prefetch_imports(graph)?;
        self.prefetch_inheritance(graph)?;
        self.prefetch_contains(graph)?;
        self.compute_aggregates();

        self.prefetched = true;
        self.prefetch_time_ms = start.elapsed().as_millis() as u64;

        info!(
            "QueryCache: Prefetch complete in {}ms - {} functions, {} classes, {} files, {} call edges",
            self.prefetch_time_ms,
            self.total_functions,
            self.total_classes,
            self.total_files,
            self.calls.len()
        );

        Ok(())
    }

    /// Prefetch all Function nodes
    fn prefetch_functions(&mut self, graph: &GraphClient) -> Result<()> {
        let query = r#"
            MATCH (n:Function)
            WHERE n.qualifiedName IS NOT NULL
            RETURN 
                n.qualifiedName AS name,
                n.filePath AS file_path,
                n.lineStart AS line_start,
                n.lineEnd AS line_end,
                n.complexity AS complexity,
                n.loc AS loc,
                n.parameters AS parameters,
                n.return_type AS return_type,
                n.is_async AS is_async,
                n.decorators AS decorators,
                n.docstring AS docstring
        "#;

        let results = graph.execute(query)?;

        for r in results {
            let name = match r.get("name").and_then(|v| v.as_str()) {
                Some(n) => n.to_string(),
                None => continue,
            };

            self.functions.insert(
                name.clone(),
                FunctionData {
                    qualified_name: name,
                    file_path: r
                        .get("file_path")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                    line_start: r.get("line_start").and_then(|v| v.as_u64()).unwrap_or(0) as u32,
                    line_end: r.get("line_end").and_then(|v| v.as_u64()).unwrap_or(0) as u32,
                    complexity: r.get("complexity").and_then(|v| v.as_i64()).unwrap_or(0) as i32,
                    loc: r.get("loc").and_then(|v| v.as_i64()).unwrap_or(0) as i32,
                    parameters: r
                        .get("parameters")
                        .and_then(|v| v.as_array())
                        .map(|arr| {
                            arr.iter()
                                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                                .collect()
                        })
                        .unwrap_or_default(),
                    return_type: r
                        .get("return_type")
                        .and_then(|v| v.as_str())
                        .map(|s| s.to_string()),
                    is_async: r.get("is_async").and_then(|v| v.as_bool()).unwrap_or(false),
                    decorators: r
                        .get("decorators")
                        .and_then(|v| v.as_array())
                        .map(|arr| {
                            arr.iter()
                                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                                .collect()
                        })
                        .unwrap_or_default(),
                    docstring: r
                        .get("docstring")
                        .and_then(|v| v.as_str())
                        .map(|s| s.to_string()),
                },
            );
        }

        debug!("QueryCache: Prefetched {} functions", self.functions.len());
        Ok(())
    }

    /// Prefetch all Class nodes
    fn prefetch_classes(&mut self, graph: &GraphClient) -> Result<()> {
        let query = r#"
            MATCH (n:Class)
            WHERE n.qualifiedName IS NOT NULL
            RETURN 
                n.qualifiedName AS name,
                n.filePath AS file_path,
                n.lineStart AS line_start,
                n.lineEnd AS line_end,
                n.complexity AS complexity,
                n.loc AS loc,
                n.is_abstract AS is_abstract,
                n.decorators AS decorators,
                n.docstring AS docstring
        "#;

        let results = graph.execute(query)?;

        for r in results {
            let name = match r.get("name").and_then(|v| v.as_str()) {
                Some(n) => n.to_string(),
                None => continue,
            };

            self.classes.insert(
                name.clone(),
                ClassData {
                    qualified_name: name,
                    file_path: r
                        .get("file_path")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                    line_start: r.get("line_start").and_then(|v| v.as_u64()).unwrap_or(0) as u32,
                    line_end: r.get("line_end").and_then(|v| v.as_u64()).unwrap_or(0) as u32,
                    complexity: r.get("complexity").and_then(|v| v.as_i64()).unwrap_or(0) as i32,
                    loc: r.get("loc").and_then(|v| v.as_i64()).unwrap_or(0) as i32,
                    is_abstract: r.get("is_abstract").and_then(|v| v.as_bool()).unwrap_or(false),
                    decorators: r
                        .get("decorators")
                        .and_then(|v| v.as_array())
                        .map(|arr| {
                            arr.iter()
                                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                                .collect()
                        })
                        .unwrap_or_default(),
                    docstring: r
                        .get("docstring")
                        .and_then(|v| v.as_str())
                        .map(|s| s.to_string()),
                    method_count: 0, // Computed in prefetch_contains
                },
            );
        }

        debug!("QueryCache: Prefetched {} classes", self.classes.len());
        Ok(())
    }

    /// Prefetch all File nodes
    fn prefetch_files(&mut self, graph: &GraphClient) -> Result<()> {
        let query = r#"
            MATCH (n:File)
            WHERE n.qualifiedName IS NOT NULL
            RETURN 
                n.qualifiedName AS name,
                n.filePath AS file_path,
                n.loc AS loc,
                n.language AS language
        "#;

        let results = graph.execute(query)?;

        for r in results {
            let name = match r.get("name").and_then(|v| v.as_str()) {
                Some(n) => n.to_string(),
                None => continue,
            };

            self.files.insert(
                name.clone(),
                FileData {
                    qualified_name: name,
                    file_path: r
                        .get("file_path")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                    loc: r.get("loc").and_then(|v| v.as_i64()).unwrap_or(0) as i32,
                    language: r
                        .get("language")
                        .and_then(|v| v.as_str())
                        .unwrap_or("python")
                        .to_string(),
                },
            );
        }

        debug!("QueryCache: Prefetched {} files", self.files.len());
        Ok(())
    }

    /// Prefetch all CALLS relationships
    fn prefetch_calls(&mut self, graph: &GraphClient) -> Result<()> {
        let query = r#"
            MATCH (a:Function)-[:CALLS]->(b:Function)
            WHERE a.qualifiedName IS NOT NULL AND b.qualifiedName IS NOT NULL
            RETURN a.qualifiedName AS caller, b.qualifiedName AS callee
        "#;

        let results = graph.execute(query)?;

        for r in results {
            let caller = match r.get("caller").and_then(|v| v.as_str()) {
                Some(c) => c.to_string(),
                None => continue,
            };
            let callee = match r.get("callee").and_then(|v| v.as_str()) {
                Some(c) => c.to_string(),
                None => continue,
            };

            self.calls.entry(caller.clone()).or_default().insert(callee.clone());
            self.called_by.entry(callee).or_default().insert(caller);
        }

        debug!("QueryCache: Prefetched {} call sources", self.calls.len());
        Ok(())
    }

    /// Prefetch all IMPORTS relationships
    fn prefetch_imports(&mut self, graph: &GraphClient) -> Result<()> {
        let query = r#"
            MATCH (a)-[:IMPORTS]->(b)
            WHERE a.qualifiedName IS NOT NULL AND b.qualifiedName IS NOT NULL
            RETURN a.qualifiedName AS importer, b.qualifiedName AS imported
        "#;

        let results = graph.execute(query)?;

        for r in results {
            let importer = match r.get("importer").and_then(|v| v.as_str()) {
                Some(i) => i.to_string(),
                None => continue,
            };
            let imported = match r.get("imported").and_then(|v| v.as_str()) {
                Some(i) => i.to_string(),
                None => continue,
            };

            self.imports.entry(importer).or_default().insert(imported);
        }

        debug!("QueryCache: Prefetched {} import sources", self.imports.len());
        Ok(())
    }

    /// Prefetch all INHERITS relationships
    fn prefetch_inheritance(&mut self, graph: &GraphClient) -> Result<()> {
        let query = r#"
            MATCH (child:Class)-[:INHERITS]->(parent:Class)
            WHERE child.qualifiedName IS NOT NULL AND parent.qualifiedName IS NOT NULL
            RETURN child.qualifiedName AS child, parent.qualifiedName AS parent
        "#;

        let results = graph.execute(query)?;

        for r in results {
            let child = match r.get("child").and_then(|v| v.as_str()) {
                Some(c) => c.to_string(),
                None => continue,
            };
            let parent = match r.get("parent").and_then(|v| v.as_str()) {
                Some(p) => p.to_string(),
                None => continue,
            };

            self.inherits.entry(child.clone()).or_default().insert(parent.clone());
            self.inherited_by.entry(parent).or_default().insert(child);
        }

        debug!(
            "QueryCache: Prefetched {} inheritance edges",
            self.inherits.len()
        );
        Ok(())
    }

    /// Prefetch all CONTAINS relationships (Class -> Function)
    fn prefetch_contains(&mut self, graph: &GraphClient) -> Result<()> {
        let query = r#"
            MATCH (c:Class)-[:CONTAINS]->(f:Function)
            WHERE c.qualifiedName IS NOT NULL AND f.qualifiedName IS NOT NULL
            RETURN c.qualifiedName AS class_name, f.qualifiedName AS method_name
        "#;

        let results = graph.execute(query)?;

        for r in results {
            let class_name = match r.get("class_name").and_then(|v| v.as_str()) {
                Some(c) => c.to_string(),
                None => continue,
            };
            let method_name = match r.get("method_name").and_then(|v| v.as_str()) {
                Some(m) => m.to_string(),
                None => continue,
            };

            self.contains
                .entry(class_name.clone())
                .or_default()
                .insert(method_name.clone());
            self.contained_by.insert(method_name, class_name);
        }

        // Update method counts on classes
        for (class_name, methods) in &self.contains {
            if let Some(class_data) = self.classes.get_mut(class_name) {
                class_data.method_count = methods.len();
            }
        }

        debug!(
            "QueryCache: Prefetched {} class->method edges",
            self.contains.len()
        );
        Ok(())
    }

    /// Compute aggregate statistics
    fn compute_aggregates(&mut self) {
        self.total_functions = self.functions.len();
        self.total_classes = self.classes.len();
        self.total_files = self.files.len();
        self.total_loc = self.files.values().map(|f| f.loc).sum();
    }

    // -------------------------------------------------------------------------
    // Query helpers for detectors
    // -------------------------------------------------------------------------

    /// Get function by qualified name
    pub fn get_function(&self, name: &str) -> Option<&FunctionData> {
        self.functions.get(name)
    }

    /// Get class by qualified name
    pub fn get_class(&self, name: &str) -> Option<&ClassData> {
        self.classes.get(name)
    }

    /// Get functions called by the given function
    pub fn get_callees(&self, func_name: &str) -> HashSet<&str> {
        self.calls
            .get(func_name)
            .map(|s| s.iter().map(|s| s.as_str()).collect())
            .unwrap_or_default()
    }

    /// Get functions that call the given function
    pub fn get_callers(&self, func_name: &str) -> HashSet<&str> {
        self.called_by
            .get(func_name)
            .map(|s| s.iter().map(|s| s.as_str()).collect())
            .unwrap_or_default()
    }

    /// Get methods contained by the given class
    pub fn get_methods(&self, class_name: &str) -> HashSet<&str> {
        self.contains
            .get(class_name)
            .map(|s| s.iter().map(|s| s.as_str()).collect())
            .unwrap_or_default()
    }

    /// Get the class containing the given method
    pub fn get_parent_class(&self, method_name: &str) -> Option<&str> {
        self.contained_by.get(method_name).map(|s| s.as_str())
    }

    /// Get parent classes of the given class
    pub fn get_parents(&self, class_name: &str) -> HashSet<&str> {
        self.inherits
            .get(class_name)
            .map(|s| s.iter().map(|s| s.as_str()).collect())
            .unwrap_or_default()
    }

    /// Get child classes of the given class
    pub fn get_children(&self, class_name: &str) -> HashSet<&str> {
        self.inherited_by
            .get(class_name)
            .map(|s| s.iter().map(|s| s.as_str()).collect())
            .unwrap_or_default()
    }

    /// Get modules imported by the given file
    pub fn get_imports(&self, file_name: &str) -> HashSet<&str> {
        self.imports
            .get(file_name)
            .map(|s| s.iter().map(|s| s.as_str()).collect())
            .unwrap_or_default()
    }

    /// Get functions with complexity above threshold
    pub fn get_high_complexity_functions(&self, threshold: i32) -> Vec<&FunctionData> {
        self.functions
            .values()
            .filter(|f| f.complexity >= threshold)
            .collect()
    }

    /// Get classes exceeding god class thresholds
    pub fn get_god_classes(&self, method_threshold: usize, loc_threshold: i32) -> Vec<&ClassData> {
        self.classes
            .values()
            .filter(|c| c.method_count >= method_threshold || c.loc >= loc_threshold)
            .collect()
    }

    /// Get functions with too many parameters
    pub fn get_long_parameter_functions(&self, threshold: usize) -> Vec<&FunctionData> {
        self.functions
            .values()
            .filter(|f| f.parameters.len() >= threshold)
            .collect()
    }

    /// Get hub functions (high in-degree and/or out-degree)
    pub fn get_hub_functions(
        &self,
        in_threshold: usize,
        out_threshold: usize,
    ) -> Vec<(&FunctionData, usize, usize)> {
        self.functions
            .iter()
            .filter_map(|(name, func)| {
                let in_degree = self.called_by.get(name).map(|s| s.len()).unwrap_or(0);
                let out_degree = self.calls.get(name).map(|s| s.len()).unwrap_or(0);
                if in_degree >= in_threshold || out_degree >= out_threshold {
                    Some((func, in_degree, out_degree))
                } else {
                    None
                }
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cache_creation() {
        let cache = QueryCache::new();
        assert!(!cache.is_prefetched());
        assert_eq!(cache.total_functions, 0);
    }

    #[test]
    fn test_helpers_empty_cache() {
        let cache = QueryCache::new();
        assert!(cache.get_function("test").is_none());
        assert!(cache.get_class("test").is_none());
        assert!(cache.get_callees("test").is_empty());
        assert!(cache.get_callers("test").is_empty());
        assert!(cache.get_methods("test").is_empty());
        assert!(cache.get_parent_class("test").is_none());
    }

    #[test]
    fn test_manual_population() {
        let mut cache = QueryCache::new();

        // Add a function
        cache.functions.insert(
            "test.func".to_string(),
            FunctionData {
                qualified_name: "test.func".to_string(),
                file_path: "test.py".to_string(),
                complexity: 15,
                ..Default::default()
            },
        );

        // Add call relationship
        cache
            .calls
            .entry("caller".to_string())
            .or_default()
            .insert("callee".to_string());
        cache
            .called_by
            .entry("callee".to_string())
            .or_default()
            .insert("caller".to_string());

        assert!(cache.get_function("test.func").is_some());
        assert_eq!(cache.get_callees("caller"), HashSet::from(["callee"]));
        assert_eq!(cache.get_callers("callee"), HashSet::from(["caller"]));
    }

    #[test]
    fn test_high_complexity_filter() {
        let mut cache = QueryCache::new();

        cache.functions.insert(
            "low".to_string(),
            FunctionData {
                qualified_name: "low".to_string(),
                complexity: 5,
                ..Default::default()
            },
        );
        cache.functions.insert(
            "high".to_string(),
            FunctionData {
                qualified_name: "high".to_string(),
                complexity: 20,
                ..Default::default()
            },
        );

        let high = cache.get_high_complexity_functions(10);
        assert_eq!(high.len(), 1);
        assert_eq!(high[0].qualified_name, "high");
    }

    #[test]
    fn test_god_classes_filter() {
        let mut cache = QueryCache::new();

        cache.classes.insert(
            "small".to_string(),
            ClassData {
                qualified_name: "small".to_string(),
                method_count: 5,
                loc: 100,
                ..Default::default()
            },
        );
        cache.classes.insert(
            "god".to_string(),
            ClassData {
                qualified_name: "god".to_string(),
                method_count: 25,
                loc: 800,
                ..Default::default()
            },
        );

        let gods = cache.get_god_classes(20, 500);
        assert_eq!(gods.len(), 1);
        assert_eq!(gods[0].qualified_name, "god");
    }
}
