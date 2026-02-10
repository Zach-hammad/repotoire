//! Shared query cache for detector parallelization
//!
//! Prefetches common graph data once, enabling O(1) lookups instead of
//! repeated graph queries across 42+ detectors.
//!
//! Target: 9min → <2min analysis time.

use crate::graph::GraphStore;
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

    /// Prefetch all data from the graph
    pub fn prefetch(&mut self, graph: &GraphStore) -> Result<()> {
        let start = Instant::now();
        info!("Prefetching graph data for detectors...");

        self.prefetch_functions(graph)?;
        self.prefetch_classes(graph)?;
        self.prefetch_files(graph)?;
        self.prefetch_calls(graph)?;
        self.prefetch_imports(graph)?;
        self.prefetch_inheritance(graph)?;

        let elapsed = start.elapsed();
        info!(
            "Prefetched {} functions, {} classes, {} files in {:?}",
            self.functions.len(),
            self.classes.len(),
            self.files.len(),
            elapsed
        );

        Ok(())
    }

    /// Prefetch all functions
    fn prefetch_functions(&mut self, graph: &GraphStore) -> Result<()> {
        debug!("Prefetching functions...");

        for func in graph.get_functions() {
            let qn = func.qualified_name.clone();
            
            self.functions.insert(
                qn.clone(),
                FunctionData {
                    qualified_name: qn.clone(),
                    file_path: func.file_path.clone(),
                    line_start: func.line_start,
                    line_end: func.line_end,
                    complexity: func.complexity().unwrap_or(1) as i32,
                    loc: func.loc() as i32,
                    parameters: vec![],
                    return_type: func.get_str("return_type").map(String::from),
                    is_async: func.get_bool("is_async").unwrap_or(false),
                    decorators: vec![],
                    docstring: func.get_str("docstring").map(String::from),
                },
            );

            // Track functions by file
            self.functions_by_file
                .entry(func.file_path.clone())
                .or_default()
                .push(qn);
        }

        Ok(())
    }

    /// Prefetch all classes
    fn prefetch_classes(&mut self, graph: &GraphStore) -> Result<()> {
        debug!("Prefetching classes...");

        for class in graph.get_classes() {
            let qn = class.qualified_name.clone();
            
            self.classes.insert(
                qn.clone(),
                ClassData {
                    qualified_name: qn.clone(),
                    file_path: class.file_path.clone(),
                    line_start: class.line_start,
                    line_end: class.line_end,
                    complexity: class.complexity().unwrap_or(1) as i32,
                    method_count: class.get_i64("methodCount").unwrap_or(0) as i32,
                    methods: vec![],
                },
            );

            // Track classes by file
            self.classes_by_file
                .entry(class.file_path.clone())
                .or_default()
                .push(qn);
        }

        Ok(())
    }

    /// Prefetch all files
    fn prefetch_files(&mut self, graph: &GraphStore) -> Result<()> {
        debug!("Prefetching files...");

        for file in graph.get_files() {
            self.files.insert(
                file.file_path.clone(),
                FileData {
                    file_path: file.file_path.clone(),
                    loc: file.get_i64("loc").unwrap_or(0),
                    language: file.language.clone().unwrap_or_default(),
                },
            );
        }

        Ok(())
    }

    /// Prefetch all call edges
    fn prefetch_calls(&mut self, graph: &GraphStore) -> Result<()> {
        debug!("Prefetching call edges...");

        for (caller, callee) in graph.get_calls() {
            self.calls
                .entry(caller.clone())
                .or_default()
                .insert(callee.clone());
            
            // Build reverse index for fan-in
            self.callers
                .entry(callee)
                .or_default()
                .insert(caller);
        }

        Ok(())
    }

    /// Prefetch all import edges
    fn prefetch_imports(&mut self, graph: &GraphStore) -> Result<()> {
        debug!("Prefetching import edges...");

        for (importer, imported) in graph.get_imports() {
            self.imports
                .entry(importer)
                .or_default()
                .insert(imported);
        }

        Ok(())
    }

    /// Prefetch inheritance relationships
    fn prefetch_inheritance(&mut self, graph: &GraphStore) -> Result<()> {
        debug!("Prefetching inheritance...");

        for (child, parent) in graph.get_inheritance() {
            self.inheritance
                .entry(child)
                .or_default()
                .insert(parent);
        }

        Ok(())
    }

    /// Get fan-in for a function (number of callers)
    pub fn get_fan_in(&self, qn: &str) -> usize {
        self.callers.get(qn).map(|s| s.len()).unwrap_or(0)
    }

    /// Get fan-out for a function (number of callees)
    pub fn get_fan_out(&self, qn: &str) -> usize {
        self.calls.get(qn).map(|s| s.len()).unwrap_or(0)
    }

    /// Get all functions in a file
    pub fn get_functions_in_file(&self, file_path: &str) -> Vec<&FunctionData> {
        self.functions_by_file
            .get(file_path)
            .map(|qns| {
                qns.iter()
                    .filter_map(|qn| self.functions.get(qn))
                    .collect()
            })
            .unwrap_or_default()
    }

    /// Get all classes in a file
    pub fn get_classes_in_file(&self, file_path: &str) -> Vec<&ClassData> {
        self.classes_by_file
            .get(file_path)
            .map(|qns| {
                qns.iter()
                    .filter_map(|qn| self.classes.get(qn))
                    .collect()
            })
            .unwrap_or_default()
    }
}
