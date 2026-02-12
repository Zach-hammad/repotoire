//! Shared query cache for detector parallelization
//!
//! Caches common graph data, enabling O(1) lookups instead of
//! repeated graph queries across detectors.

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
