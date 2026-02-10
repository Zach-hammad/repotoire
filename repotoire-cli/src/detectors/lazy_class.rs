//! Lazy Class detector - identifies underutilized classes
//!
//! Detects classes that do minimal work and may be unnecessary abstraction.
//! The opposite of god classes - these classes have very few methods that do very little.
//!
//! Example:
//! ```text
//! class DataWrapper {
//!     fn get_value(&self) -> &Value { &self.value }
//!     fn set_value(&mut self, v: Value) { self.value = v; }
//! }
//! ```
//! This class might be unnecessary - just use the Value directly.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use tracing::{debug, info};
use uuid::Uuid;

/// Thresholds for lazy class detection
#[derive(Debug, Clone)]
pub struct LazyClassThresholds {
    /// Maximum methods for a class to be considered lazy
    pub max_methods: usize,
    /// Maximum average LOC per method
    pub max_avg_loc_per_method: usize,
    /// Minimum total LOC (to avoid flagging empty classes)
    pub min_total_loc: usize,
}

impl Default for LazyClassThresholds {
    fn default() -> Self {
        Self {
            max_methods: 3,
            max_avg_loc_per_method: 5,
            min_total_loc: 10,
        }
    }
}

/// Patterns to exclude from lazy class detection
static EXCLUDE_PATTERNS: &[&str] = &[
    // Design patterns
    "Adapter",
    "Wrapper",
    "Proxy",
    "Decorator",
    "Facade",
    "Bridge",
    // Configuration classes
    "Config",
    "Settings",
    "Options",
    "Preferences",
    // Data transfer objects
    "Request",
    "Response",
    "DTO",
    "Entity",
    "Model",
    // Exceptions
    "Exception",
    "Error",
    // Base/abstract classes
    "Base",
    "Abstract",
    "Interface",
    "Mixin",
    // Test classes
    "Test",
    "Mock",
    "Stub",
    "Fake",
    // Protocols
    "Protocol",
];

/// Detects classes that do minimal work
pub struct LazyClassDetector {
    config: DetectorConfig,
    thresholds: LazyClassThresholds,
}

impl LazyClassDetector {
    /// Create a new detector with default thresholds
    pub fn new() -> Self {
        Self::with_thresholds(LazyClassThresholds::default())
    }

    /// Create with custom thresholds
    pub fn with_thresholds(thresholds: LazyClassThresholds) -> Self {
        Self {
            config: DetectorConfig::new(),
            thresholds,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        let thresholds = LazyClassThresholds {
            max_methods: config.get_option_or("max_methods", 3),
            max_avg_loc_per_method: config.get_option_or("max_avg_loc_per_method", 5),
            min_total_loc: config.get_option_or("min_total_loc", 10),
        };

        Self { config, thresholds }
    }

    /// Check if a class name matches an exclusion pattern
    fn should_exclude(&self, class_name: &str) -> bool {
        if class_name.is_empty() {
            return true;
        }

        let class_lower = class_name.to_lowercase();
        EXCLUDE_PATTERNS
            .iter()
            .any(|pattern| class_lower.contains(&pattern.to_lowercase()))
    }

    /// Create a finding for a lazy class
    fn create_finding(
        &self,
        _qualified_name: String,
        class_name: String,
        file_path: String,
        line_start: Option<u32>,
        line_end: Option<u32>,
        method_count: usize,
        total_loc: usize,
        avg_method_loc: f64,
    ) -> Finding {
        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "LazyClassDetector".to_string(),
            severity: Severity::Low,
            title: format!("Lazy class: {}", class_name),
            description: format!(
                "Class '{}' has only {} method(s) with an average of {:.1} lines each \
                 ({} total LOC). This may indicate unnecessary abstraction.",
                class_name, method_count, avg_method_loc, total_loc
            ),
            affected_files: vec![PathBuf::from(&file_path)],
            line_start,
            line_end,
            suggested_fix: Some(
                "Consider one of the following:\n\
                 1. Inline this class's functionality into its callers\n\
                 2. Expand the class with additional functionality\n\
                 3. If this is a deliberate design pattern (Adapter, Facade), \
                    add a docstring explaining its purpose"
                    .to_string(),
            ),
            estimated_effort: Some("Small (15-30 minutes)".to_string()),
            category: Some("design".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Lazy classes add cognitive overhead without providing value. \
                 They increase indirection and make the codebase harder to navigate. \
                 If a class doesn't justify its existence with meaningful behavior, \
                 consider removing or expanding it."
                    .to_string(),
            ),
        }
    }
}

impl Default for LazyClassDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for LazyClassDetector {
    fn name(&self) -> &'static str {
        "LazyClassDetector"
    }

    fn description(&self) -> &'static str {
        "Detects classes that do minimal work"
    }

    fn category(&self) -> &'static str {
        "design"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

        fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        // TODO: Migrate to GraphStore API
        Ok(vec![])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_thresholds() {
        let detector = LazyClassDetector::new();
        assert_eq!(detector.thresholds.max_methods, 3);
        assert_eq!(detector.thresholds.max_avg_loc_per_method, 5);
        assert_eq!(detector.thresholds.min_total_loc, 10);
    }

    #[test]
    fn test_should_exclude() {
        let detector = LazyClassDetector::new();

        assert!(detector.should_exclude("UserAdapter"));
        assert!(detector.should_exclude("DatabaseConfig"));
        assert!(detector.should_exclude("TestHelper"));
        assert!(detector.should_exclude("CustomException"));
        assert!(detector.should_exclude("BaseClass"));

        assert!(!detector.should_exclude("UserService"));
        assert!(!detector.should_exclude("OrderProcessor"));
    }
}
