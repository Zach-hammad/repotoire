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
use crate::graph::GraphStore;
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
    }    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();
        
        for (child_qn, parent_qn) in graph.get_inheritance() {
            // Skip common patterns
            if parent_qn.contains("Base") || parent_qn.contains("Abstract") || parent_qn.contains("Mixin") {
                continue;
            }
            
            if let Some(child) = graph.get_node(&child_qn) {
                // Check if child overrides many methods without calling super
                let child_methods: Vec<_> = graph.get_functions()
                    .into_iter()
                    .filter(|f| f.qualified_name.starts_with(&child_qn))
                    .collect();
                
                if child_methods.len() >= 3 {
                    // Count methods that might be refusing bequest (low complexity overrides)
                    let potential_refusals: Vec<_> = child_methods.iter()
                        .filter(|m| m.complexity().unwrap_or(1) <= 2 && m.loc() <= 5)
                        .collect();
                    
                    if potential_refusals.len() >= 2 {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "RefusedBequestDetector".to_string(),
                            severity: Severity::Low,
                            title: format!("Refused Bequest: {}", child.name),
                            description: format!(
                                "Class '{}' inherits from '{}' but may not use inherited behavior properly.",
                                child.name, parent_qn
                            ),
                            affected_files: vec![child.file_path.clone().into()],
                            line_start: Some(child.line_start),
                            line_end: Some(child.line_end),
                            suggested_fix: Some("Consider composition over inheritance if not using parent behavior".to_string()),
                            estimated_effort: Some("Medium (1-2 hours)".to_string()),
                            category: Some("structure".to_string()),
                            cwe_id: None,
                            why_it_matters: Some("Refused bequest indicates improper use of inheritance".to_string()),
                        });
                    }
                }
            }
        }
        
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
