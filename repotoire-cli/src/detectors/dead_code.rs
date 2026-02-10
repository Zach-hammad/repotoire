//! Dead code detector - finds unused functions and classes
//!
//! Detects code that is never called or instantiated, indicating:
//! - Leftover code from refactoring
//! - Unused features
//! - Test helpers that were never removed
//!
//! Uses graph analysis to find nodes with zero incoming CALLS relationships.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphClient;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::PathBuf;
use tracing::{debug, info};
use uuid::Uuid;

/// Entry points that should not be flagged as dead code
static ENTRY_POINTS: &[&str] = &[
    "main",
    "__main__",
    "__init__",
    "setUp",
    "tearDown",
];

/// Special methods that are called implicitly
static MAGIC_METHODS: &[&str] = &[
    "__str__",
    "__repr__",
    "__enter__",
    "__exit__",
    "__call__",
    "__len__",
    "__iter__",
    "__next__",
    "__getitem__",
    "__setitem__",
    "__delitem__",
    "__eq__",
    "__ne__",
    "__lt__",
    "__le__",
    "__gt__",
    "__ge__",
    "__hash__",
    "__bool__",
    "__add__",
    "__sub__",
    "__mul__",
    "__truediv__",
    "__floordiv__",
    "__mod__",
    "__pow__",
    "__post_init__",
    "__init_subclass__",
    "__set_name__",
];

/// Thresholds for dead code detection
#[derive(Debug, Clone)]
pub struct DeadCodeThresholds {
    /// Base confidence for graph-only detection
    pub base_confidence: f64,
    /// Maximum functions to return
    pub max_results: usize,
}

impl Default for DeadCodeThresholds {
    fn default() -> Self {
        Self {
            base_confidence: 0.70,
            max_results: 100,
        }
    }
}

/// Detects dead code (unused functions and classes)
pub struct DeadCodeDetector {
    config: DetectorConfig,
    thresholds: DeadCodeThresholds,
    entry_points: HashSet<String>,
    magic_methods: HashSet<String>,
}

impl DeadCodeDetector {
    /// Create a new detector with default thresholds
    pub fn new() -> Self {
        Self::with_thresholds(DeadCodeThresholds::default())
    }

    /// Create with custom thresholds
    pub fn with_thresholds(thresholds: DeadCodeThresholds) -> Self {
        let entry_points: HashSet<String> = ENTRY_POINTS.iter().map(|s| s.to_string()).collect();
        let magic_methods: HashSet<String> = MAGIC_METHODS.iter().map(|s| s.to_string()).collect();

        Self {
            config: DetectorConfig::new(),
            thresholds,
            entry_points,
            magic_methods,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        let thresholds = DeadCodeThresholds {
            base_confidence: config.get_option_or("base_confidence", 0.70),
            max_results: config.get_option_or("max_results", 100),
        };

        Self::with_thresholds(thresholds)
    }

    /// Check if a function name is an entry point
    fn is_entry_point(&self, name: &str) -> bool {
        self.entry_points.contains(name) || name.starts_with("test_")
    }

    /// Check if a function name is a magic method
    fn is_magic_method(&self, name: &str) -> bool {
        self.magic_methods.contains(name)
    }

    /// Check if function should be filtered out
    fn should_filter(&self, name: &str, is_method: bool, has_decorators: bool) -> bool {
        // Magic methods
        if self.is_magic_method(name) {
            return true;
        }

        // Entry points
        if self.is_entry_point(name) {
            return true;
        }

        // Public methods (may be called externally)
        if is_method && !name.starts_with('_') {
            return true;
        }

        // Decorated functions
        if has_decorators {
            return true;
        }

        // Common patterns that are often called dynamically
        let filter_patterns = [
            "handle",
            "on_",
            "callback",
            "load_data",
            "loader",
            "_loader",
            "load_",
            "create_",
            "build_",
            "make_",
            "_parse_",
            "_process_",
            "load_config",
            "generate_",
            "validate_",
            "setup_",
            "initialize_",
            "to_dict",
            "to_json",
            "from_dict",
            "from_json",
            "serialize",
            "deserialize",
            "_side_effect",
            "_effect",
            "_extract_",
            "_find_",
            "_calculate_",
            "_get_",
            "_set_",
            "_check_",
        ];

        let name_lower = name.to_lowercase();
        for pattern in filter_patterns {
            if name_lower.contains(pattern) {
                return true;
            }
        }

        false
    }

    /// Calculate severity for dead function
    fn calculate_function_severity(&self, complexity: usize) -> Severity {
        if complexity >= 20 {
            Severity::High
        } else if complexity >= 10 {
            Severity::Medium
        } else {
            Severity::Low
        }
    }

    /// Calculate severity for dead class
    fn calculate_class_severity(&self, method_count: usize, complexity: usize) -> Severity {
        if method_count >= 10 || complexity >= 50 {
            Severity::High
        } else if method_count >= 5 || complexity >= 20 {
            Severity::Medium
        } else {
            Severity::Low
        }
    }

    /// Create a finding for an unused function
    fn create_function_finding(
        &self,
        _qualified_name: String,
        name: String,
        file_path: String,
        line_start: Option<u32>,
        complexity: usize,
    ) -> Finding {
        let severity = self.calculate_function_severity(complexity);
        let confidence = self.thresholds.base_confidence;

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "DeadCodeDetector".to_string(),
            severity,
            title: format!("Unused function: {}", name),
            description: format!(
                "Function '{}' is never called in the codebase. \
                 It has complexity {}.\n\n\
                 **Confidence:** {:.0}% (graph analysis only)\n\
                 **Recommendation:** Review before removing",
                name,
                complexity,
                confidence * 100.0
            ),
            affected_files: vec![PathBuf::from(&file_path)],
            line_start,
            line_end: None,
            suggested_fix: Some(format!(
                "**REVIEW REQUIRED** (confidence: {:.0}%)\n\
                 1. Remove the function from {}\n\
                 2. Check for dynamic calls (getattr, eval) that might use it\n\
                 3. Verify it's not an API endpoint or callback",
                confidence * 100.0,
                file_path.split('/').last().unwrap_or(&file_path)
            )),
            estimated_effort: Some("Small (30-60 minutes)".to_string()),
            category: Some("dead_code".to_string()),
            cwe_id: Some("CWE-561".to_string()), // Dead Code
            why_it_matters: Some(
                "Dead code increases maintenance burden, confuses developers, \
                 and can hide bugs. Removing unused code improves readability \
                 and reduces the codebase size."
                    .to_string(),
            ),
        }
    }

    /// Create a finding for an unused class
    fn create_class_finding(
        &self,
        _qualified_name: String,
        name: String,
        file_path: String,
        method_count: usize,
        complexity: usize,
    ) -> Finding {
        let severity = self.calculate_class_severity(method_count, complexity);
        let confidence = self.thresholds.base_confidence;

        let effort = if method_count >= 10 {
            "Medium (2-4 hours)"
        } else if method_count >= 5 {
            "Small (1-2 hours)"
        } else {
            "Small (30 minutes)"
        };

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "DeadCodeDetector".to_string(),
            severity,
            title: format!("Unused class: {}", name),
            description: format!(
                "Class '{}' is never instantiated or inherited from. \
                 It has {} methods and complexity {}.\n\n\
                 **Confidence:** {:.0}% (graph analysis only)\n\
                 **Recommendation:** Review before removing",
                name,
                method_count,
                complexity,
                confidence * 100.0
            ),
            affected_files: vec![PathBuf::from(&file_path)],
            line_start: None,
            line_end: None,
            suggested_fix: Some(format!(
                "**REVIEW REQUIRED** (confidence: {:.0}%)\n\
                 1. Remove the class and its {} methods\n\
                 2. Check for dynamic instantiation (factory patterns, reflection)\n\
                 3. Verify it's not used in configuration or plugins",
                confidence * 100.0,
                method_count
            )),
            estimated_effort: Some(effort.to_string()),
            category: Some("dead_code".to_string()),
            cwe_id: Some("CWE-561".to_string()),
            why_it_matters: Some(
                "Unused classes bloat the codebase and increase cognitive load. \
                 They may also cause confusion about the system's actual behavior."
                    .to_string(),
            ),
        }
    }

    /// Find dead functions
    fn find_dead_functions(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        let query = r#"
            MATCH (f:Function)
            WHERE NOT (f.name STARTS WITH 'test_')
              AND NOT f.name IN ['main', '__main__', '__init__', 'setUp', 'tearDown']
            OPTIONAL MATCH (f)<-[rel:CALLS]-()
            OPTIONAL MATCH (f)<-[use:USES]-()
            WITH f, count(rel) AS call_count, count(use) AS use_count
            WHERE call_count = 0 AND use_count = 0
            OPTIONAL MATCH (file:File)-[:CONTAINS]->(f)
            RETURN f.qualifiedName AS qualified_name,
                   f.name AS name,
                   f.filePath AS file_path,
                   f.lineStart AS line_start,
                   f.complexity AS complexity,
                   file.filePath AS containing_file,
                   f.decorators AS decorators,
                   f.is_method AS is_method
            ORDER BY f.complexity DESC
            LIMIT 100
        "#;

        let results = graph.execute(query)?;
        let mut findings = Vec::new();

        for row in results {
            let name = row
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let is_method = row
                .get("is_method")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);

            let decorators = row
                .get("decorators")
                .and_then(|v| v.as_array())
                .map(|v| !v.is_empty())
                .unwrap_or(false);

            // Apply filters
            if self.should_filter(&name, is_method, decorators) {
                continue;
            }

            let qualified_name = row
                .get("qualified_name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let file_path = row
                .get("containing_file")
                .or_else(|| row.get("file_path"))
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let line_start = row
                .get("line_start")
                .and_then(|v| v.as_u64())
                .map(|v| v as u32);

            let complexity = row
                .get("complexity")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as usize;

            findings.push(self.create_function_finding(
                qualified_name,
                name,
                file_path,
                line_start,
                complexity,
            ));

            if findings.len() >= self.thresholds.max_results {
                break;
            }
        }

        Ok(findings)
    }

    /// Find dead classes
    fn find_dead_classes(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        let query = r#"
            MATCH (file:File)-[:CONTAINS]->(c:Class)
            OPTIONAL MATCH (c)<-[rel:CALLS]-()
            OPTIONAL MATCH (c)<-[inherit:INHERITS]-()
            OPTIONAL MATCH (c)<-[use:USES]-()
            WITH c, file, count(rel) AS call_count, count(inherit) AS inherit_count, count(use) AS use_count
            WHERE call_count = 0 AND inherit_count = 0 AND use_count = 0
            RETURN c.qualifiedName AS qualified_name,
                   c.name AS name,
                   c.filePath AS file_path,
                   c.complexity AS complexity,
                   file.filePath AS containing_file,
                   c.decorators AS decorators
            ORDER BY c.complexity DESC
            LIMIT 50
        "#;

        let results = graph.execute(query)?;
        let mut findings = Vec::new();

        for row in results {
            let name = row
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            // Skip common patterns
            if name.ends_with("Error")
                || name.ends_with("Exception")
                || name.ends_with("Mixin")
                || name.contains("Mixin")
                || name.starts_with("Test")
                || name.ends_with("Test")
                || name == "ABC"
                || name == "Enum"
                || name == "Exception"
                || name == "BaseException"
            {
                continue;
            }

            // Skip decorated classes
            let decorators = row
                .get("decorators")
                .and_then(|v| v.as_array())
                .map(|v| !v.is_empty())
                .unwrap_or(false);

            if decorators {
                continue;
            }

            let qualified_name = row
                .get("qualified_name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let file_path = row
                .get("containing_file")
                .or_else(|| row.get("file_path"))
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let complexity = row
                .get("complexity")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as usize;

            // TODO: Get actual method count from graph
            let method_count = 0usize;

            findings.push(self.create_class_finding(
                qualified_name,
                name,
                file_path,
                method_count,
                complexity,
            ));
        }

        Ok(findings)
    }
}

impl Default for DeadCodeDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for DeadCodeDetector {
    fn name(&self) -> &'static str {
        "DeadCodeDetector"
    }

    fn description(&self) -> &'static str {
        "Detects unused functions and classes"
    }

    fn category(&self) -> &'static str {
        "dead_code"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        debug!("Starting dead code detection");

        let mut findings = Vec::new();

        // Find dead functions
        let function_findings = self.find_dead_functions(graph)?;
        findings.extend(function_findings);

        // Find dead classes
        let class_findings = self.find_dead_classes(graph)?;
        findings.extend(class_findings);

        // Sort by severity
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        info!("DeadCodeDetector found {} dead code issues", findings.len());

        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_entry_points() {
        let detector = DeadCodeDetector::new();

        assert!(detector.is_entry_point("main"));
        assert!(detector.is_entry_point("__init__"));
        assert!(detector.is_entry_point("test_something"));
        assert!(!detector.is_entry_point("my_function"));
    }

    #[test]
    fn test_magic_methods() {
        let detector = DeadCodeDetector::new();

        assert!(detector.is_magic_method("__str__"));
        assert!(detector.is_magic_method("__repr__"));
        assert!(!detector.is_magic_method("my_method"));
    }

    #[test]
    fn test_should_filter() {
        let detector = DeadCodeDetector::new();

        // Magic methods
        assert!(detector.should_filter("__str__", false, false));

        // Entry points
        assert!(detector.should_filter("main", false, false));
        assert!(detector.should_filter("test_foo", false, false));

        // Public methods
        assert!(detector.should_filter("public_method", true, false));
        assert!(!detector.should_filter("_private_method", true, false));

        // Decorated
        assert!(detector.should_filter("any_func", false, true));

        // Patterns
        assert!(detector.should_filter("load_config", false, false));
        assert!(detector.should_filter("to_dict", false, false));
    }

    #[test]
    fn test_severity() {
        let detector = DeadCodeDetector::new();

        assert_eq!(detector.calculate_function_severity(5), Severity::Low);
        assert_eq!(detector.calculate_function_severity(10), Severity::Medium);
        assert_eq!(detector.calculate_function_severity(25), Severity::High);

        assert_eq!(detector.calculate_class_severity(3, 10), Severity::Low);
        assert_eq!(detector.calculate_class_severity(5, 10), Severity::Medium);
        assert_eq!(detector.calculate_class_severity(10, 10), Severity::High);
    }
}
