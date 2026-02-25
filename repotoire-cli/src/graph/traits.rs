//! Graph store traits for detector compatibility

use super::CodeNode;
use std::collections::HashMap;

/// Common interface for graph stores
#[allow(dead_code)] // Trait defines public API surface; not all methods called in binary
pub trait GraphQuery: Send + Sync {
    /// Get all functions
    fn get_functions(&self) -> Vec<CodeNode>;

    /// Get all classes
    fn get_classes(&self) -> Vec<CodeNode>;

    /// Get all files
    fn get_files(&self) -> Vec<CodeNode>;

    /// Get functions in a specific file
    fn get_functions_in_file(&self, file_path: &str) -> Vec<CodeNode>;

    /// Get classes in a specific file
    fn get_classes_in_file(&self, file_path: &str) -> Vec<CodeNode>;

    /// Get node by qualified name
    fn get_node(&self, qn: &str) -> Option<CodeNode>;

    /// Get functions that call this function
    fn get_callers(&self, qn: &str) -> Vec<CodeNode>;

    /// Get functions this function calls
    fn get_callees(&self, qn: &str) -> Vec<CodeNode>;

    /// Count of callers (fan-in)
    fn call_fan_in(&self, qn: &str) -> usize;

    /// Count of callees (fan-out)
    fn call_fan_out(&self, qn: &str) -> usize;

    /// Get all call edges
    fn get_calls(&self) -> Vec<(String, String)>;

    /// Get all import edges
    fn get_imports(&self) -> Vec<(String, String)>;

    /// Get inheritance edges
    fn get_inheritance(&self) -> Vec<(String, String)>;

    /// Get child classes
    fn get_child_classes(&self, qn: &str) -> Vec<CodeNode>;

    /// Get files that import this file
    fn get_importers(&self, qn: &str) -> Vec<CodeNode>;

    /// Find import cycles
    fn find_import_cycles(&self) -> Vec<Vec<String>>;

    /// Get stats
    fn stats(&self) -> HashMap<String, i64>;

    /// Get complex functions (complexity > threshold)
    fn get_complex_functions(&self, min_complexity: i64) -> Vec<CodeNode> {
        self.get_functions()
            .into_iter()
            .filter(|f| f.get_i64("complexity").unwrap_or(0) >= min_complexity)
            .collect()
    }

    /// Get long parameter functions
    fn get_long_param_functions(&self, min_params: i64) -> Vec<CodeNode> {
        self.get_functions()
            .into_iter()
            .filter(|f| f.get_i64("paramCount").unwrap_or(0) >= min_params)
            .collect()
    }
}
