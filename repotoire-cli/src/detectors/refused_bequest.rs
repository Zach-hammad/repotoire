//! Refused Bequest detector - identifies improper inheritance
//!
//! Detects classes that inherit but don't use parent functionality.
//! A "refused bequest" occurs when a child class overrides parent methods
//! without calling super() or using parent functionality.
//!
//! This often indicates that composition should be used instead of inheritance.
//!
//! Example:
//! ```text
//! class Bird {
//!     fn fly(&self) { ... }
//!     fn eat(&self) { ... }
//! }
//! class Penguin extends Bird {
//!     fn fly(&self) { panic!("Penguins can't fly!") }  // Refused bequest
//!     fn eat(&self) { ... }
//! }
//! ```
//! Penguin shouldn't inherit from Bird if it can't fly.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphClient;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use tracing::{debug, info};
use uuid::Uuid;

/// Thresholds for refused bequest detection
#[derive(Debug, Clone)]
pub struct RefusedBequestThresholds {
    /// Minimum overrides to consider
    pub min_overrides: usize,
    /// Flag if less than this ratio call parent
    pub max_parent_call_ratio: f64,
}

impl Default for RefusedBequestThresholds {
    fn default() -> Self {
        Self {
            min_overrides: 2,
            max_parent_call_ratio: 0.3,
        }
    }
}

/// Patterns to exclude from detection (abstract base classes)
static EXCLUDE_PARENT_PATTERNS: &[&str] = &[
    "ABC",
    "Abstract",
    "Interface",
    "Base",
    "Mixin",
    "Protocol",
];

/// Detects classes that inherit but don't use parent functionality
pub struct RefusedBequestDetector {
    config: DetectorConfig,
    thresholds: RefusedBequestThresholds,
}

impl RefusedBequestDetector {
    /// Create a new detector with default thresholds
    pub fn new() -> Self {
        Self::with_thresholds(RefusedBequestThresholds::default())
    }

    /// Create with custom thresholds
    pub fn with_thresholds(thresholds: RefusedBequestThresholds) -> Self {
        Self {
            config: DetectorConfig::new(),
            thresholds,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        let thresholds = RefusedBequestThresholds {
            min_overrides: config.get_option_or("min_overrides", 2),
            max_parent_call_ratio: config.get_option_or("max_parent_call_ratio", 0.3),
        };

        Self { config, thresholds }
    }

    /// Check if parent is an abstract class
    fn is_abstract_parent(&self, parent_name: &str) -> bool {
        if parent_name.is_empty() {
            return false;
        }

        let parent_lower = parent_name.to_lowercase();
        EXCLUDE_PARENT_PATTERNS
            .iter()
            .any(|pattern| parent_lower.contains(&pattern.to_lowercase()))
    }

    /// Calculate severity based on parent call ratio
    fn calculate_severity(&self, ratio: f64) -> Severity {
        if ratio == 0.0 {
            Severity::High
        } else if ratio < 0.2 {
            Severity::Medium
        } else {
            Severity::Low
        }
    }

    /// Estimate effort based on severity
    fn estimate_effort(&self, severity: Severity) -> String {
        match severity {
            Severity::Critical | Severity::High => "Medium (2-4 hours)".to_string(),
            Severity::Medium => "Medium (1-2 hours)".to_string(),
            Severity::Low | Severity::Info => "Small (30-60 minutes)".to_string(),
        }
    }

    /// Create a finding for refused bequest
    fn create_finding(
        &self,
        _child_name: String,
        child_class: String,
        _parent_name: String,
        parent_class: String,
        file_path: String,
        line_start: Option<u32>,
        line_end: Option<u32>,
        total_overrides: usize,
        overrides_calling_parent: usize,
    ) -> Finding {
        let ratio = if total_overrides > 0 {
            overrides_calling_parent as f64 / total_overrides as f64
        } else {
            0.0
        };

        let severity = self.calculate_severity(ratio);

        let severity_reason = if ratio == 0.0 {
            "No overrides call parent"
        } else if ratio < 0.2 {
            &format!("Only {:.0}% of overrides call parent", ratio * 100.0)
        } else {
            &format!("{:.0}% of overrides call parent", ratio * 100.0)
        };

        let parent_lower = parent_class.to_lowercase();
        let recommendation = format!(
            "Consider refactoring to use composition instead of inheritance:\n\
             1. Replace `class {}({})` with `class {}`\n\
             2. Add `{}` as a member: `self.{} = {}()`\n\
             3. Delegate only the methods you actually need\n\n\
             Benefits: Looser coupling, clearer intent, easier testing",
            child_class, parent_class, child_class, parent_lower, parent_lower, parent_class
        );

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "RefusedBequestDetector".to_string(),
            severity,
            title: format!("Refused bequest: {} inherits {}", child_class, parent_class),
            description: format!(
                "Class '{}' inherits from '{}' but overrides {} method(s) with only {} \
                 calling the parent ({:.0}%). {}. This suggests inheritance may be misused.",
                child_class,
                parent_class,
                total_overrides,
                overrides_calling_parent,
                ratio * 100.0,
                severity_reason
            ),
            affected_files: vec![PathBuf::from(&file_path)],
            line_start,
            line_end,
            suggested_fix: Some(recommendation),
            estimated_effort: Some(self.estimate_effort(severity)),
            category: Some("design".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Refused bequest violates the Liskov Substitution Principle. \
                 When a subclass overrides parent methods without calling super(), \
                 it suggests the inheritance relationship is incorrect. Using composition \
                 instead of inheritance leads to more flexible and maintainable code."
                    .to_string(),
            ),
        }
    }
}

impl Default for RefusedBequestDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for RefusedBequestDetector {
    fn name(&self) -> &'static str {
        "RefusedBequestDetector"
    }

    fn description(&self) -> &'static str {
        "Detects classes that inherit but don't use parent functionality"
    }

    fn category(&self) -> &'static str {
        "design"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        debug!("Starting refused bequest detection");

        let query = r#"
            // Find classes that inherit from another class
            MATCH (child:Class)-[:INHERITS]->(parent:Class)
            WHERE parent.name IS NOT NULL
              AND child.name IS NOT NULL

            // Find overridden methods (same name in both child and parent)
            MATCH (child)-[:CONTAINS]->(method:Function)
            WHERE method.name IS NOT NULL

            // Check if method overrides a parent method
            OPTIONAL MATCH (parent)-[:CONTAINS]->(parent_method:Function)
            WHERE parent_method.name = method.name

            // Check if override calls the parent method (super() call)
            OPTIONAL MATCH (method)-[:CALLS]->(parent_method)

            WITH child, parent, method, parent_method,
                 CASE WHEN parent_method IS NOT NULL THEN 1 ELSE 0 END AS is_override,
                 CASE WHEN (method)-[:CALLS]->(parent_method) THEN 1 ELSE 0 END AS calls_parent

            // Aggregate per child class
            WITH child, parent,
                 sum(is_override) AS total_overrides,
                 sum(calls_parent) AS overrides_calling_parent

            // Filter for classes with enough overrides
            WHERE total_overrides >= $min_overrides

            // Calculate parent call ratio
            WITH child, parent, total_overrides, overrides_calling_parent,
                 CASE WHEN total_overrides > 0
                      THEN cast(overrides_calling_parent, "DOUBLE") / total_overrides
                      ELSE 0 END AS parent_call_ratio

            // Flag classes where most overrides don't call parent
            WHERE parent_call_ratio <= $max_parent_call_ratio

            // Get file path
            OPTIONAL MATCH (child)<-[:CONTAINS*]-(f:File)

            RETURN child.qualifiedName AS child_name,
                   child.name AS child_class,
                   child.lineStart AS line_start,
                   child.lineEnd AS line_end,
                   parent.qualifiedName AS parent_name,
                   parent.name AS parent_class,
                   total_overrides,
                   overrides_calling_parent,
                   parent_call_ratio,
                   f.filePath AS file_path
            ORDER BY total_overrides DESC
            LIMIT 50
        "#;

        let _params = serde_json::json!({
            "min_overrides": self.thresholds.min_overrides,
            "max_parent_call_ratio": self.thresholds.max_parent_call_ratio,
        });

        let results = graph.execute(query)?;

        if results.is_empty() {
            debug!("No refused bequest violations found");
            return Ok(vec![]);
        }

        let mut findings = Vec::new();

        for row in results {
            let parent_class = row
                .get("parent_class")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            // Skip if parent is an abstract base class
            if self.is_abstract_parent(&parent_class) {
                continue;
            }

            let child_name = row
                .get("child_name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let child_class = row
                .get("child_class")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let parent_name = row
                .get("parent_name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let file_path = row
                .get("file_path")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let line_start = row
                .get("line_start")
                .and_then(|v| v.as_u64())
                .map(|v| v as u32);

            let line_end = row
                .get("line_end")
                .and_then(|v| v.as_u64())
                .map(|v| v as u32);

            let total_overrides = row
                .get("total_overrides")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as usize;

            let overrides_calling_parent = row
                .get("overrides_calling_parent")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as usize;

            findings.push(self.create_finding(
                child_name,
                child_class,
                parent_name,
                parent_class,
                file_path,
                line_start,
                line_end,
                total_overrides,
                overrides_calling_parent,
            ));
        }

        // Sort by severity
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        info!(
            "RefusedBequestDetector found {} refused bequest violations",
            findings.len()
        );

        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_thresholds() {
        let detector = RefusedBequestDetector::new();
        assert_eq!(detector.thresholds.min_overrides, 2);
        assert!((detector.thresholds.max_parent_call_ratio - 0.3).abs() < f64::EPSILON);
    }

    #[test]
    fn test_is_abstract_parent() {
        let detector = RefusedBequestDetector::new();

        assert!(detector.is_abstract_parent("ABC"));
        assert!(detector.is_abstract_parent("AbstractBase"));
        assert!(detector.is_abstract_parent("BaseClass"));
        assert!(detector.is_abstract_parent("UserInterface"));
        assert!(detector.is_abstract_parent("MyMixin"));

        assert!(!detector.is_abstract_parent("User"));
        assert!(!detector.is_abstract_parent("OrderService"));
    }

    #[test]
    fn test_severity_calculation() {
        let detector = RefusedBequestDetector::new();

        assert_eq!(detector.calculate_severity(0.0), Severity::High);
        assert_eq!(detector.calculate_severity(0.1), Severity::Medium);
        assert_eq!(detector.calculate_severity(0.2), Severity::Low);
        assert_eq!(detector.calculate_severity(0.5), Severity::Low);
    }
}
