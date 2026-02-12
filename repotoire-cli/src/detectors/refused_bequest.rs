//! Refused Bequest detector - identifies improper inheritance
//!
//! Graph-aware detection of classes that inherit but don't use parent functionality.
//! A "refused bequest" occurs when a child class overrides parent methods
//! without calling super() or using parent functionality.
//!
//! Enhanced with graph analysis:
//! - Check if child is used polymorphically (through parent type) - if so, higher severity
//! - Trace inheritance depth - deep hierarchies with refused bequest are worse
//! - Analyze if overridden methods are actually called differently by callers
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
use std::collections::HashSet;
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
static EXCLUDE_PARENT_PATTERNS: &[&str] =
    &["ABC", "Abstract", "Interface", "Base", "Mixin", "Protocol"];

/// Detects classes that inherit but don't use parent functionality
pub struct RefusedBequestDetector {
    #[allow(dead_code)] // Stored for future config access
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
    #[allow(dead_code)] // Builder pattern method
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
            ..Default::default()
        }
    }

    /// Check if child class is used polymorphically (through parent type)
    fn check_polymorphic_usage(&self, graph: &GraphStore, child_qn: &str, parent_qn: &str) -> bool {
        // Look for functions that reference the parent type and might receive child instances
        // This is heuristic - check if parent is used as parameter/return type in callers of child methods

        let child_methods: Vec<_> = graph
            .get_functions()
            .into_iter()
            .filter(|f| f.qualified_name.starts_with(child_qn))
            .collect();

        for method in &child_methods {
            let callers = graph.get_callers(&method.qualified_name);
            for caller in &callers {
                // If caller also calls parent methods, it might be polymorphic usage
                let caller_callees = graph.get_callees(&caller.qualified_name);
                for callee in &caller_callees {
                    if callee.qualified_name.starts_with(parent_qn) {
                        debug!(
                            "Polymorphic usage: {} calls both {} and parent {}",
                            caller.name, method.name, parent_qn
                        );
                        return true;
                    }
                }
            }
        }

        false
    }

    /// Get the depth of the inheritance hierarchy
    fn get_inheritance_depth(&self, graph: &GraphStore, class_qn: &str) -> usize {
        let mut depth = 0;
        let mut current = class_qn.to_string();
        let mut seen = HashSet::new();

        while let Some((_, parent)) = graph
            .get_inheritance()
            .into_iter()
            .find(|(child, _)| child == &current)
        {
            if seen.contains(&parent) {
                break; // Avoid cycles
            }
            seen.insert(parent.clone());
            current = parent;
            depth += 1;

            if depth > 10 {
                break; // Safety limit
            }
        }

        depth
    }

    /// Check if child class methods are called differently than parent
    fn check_divergent_callers(
        &self,
        graph: &GraphStore,
        child_methods: &[crate::graph::CodeNode],
        parent_qn: &str,
    ) -> bool {
        // Get parent methods
        let parent_methods: Vec<_> = graph
            .get_functions()
            .into_iter()
            .filter(|f| f.qualified_name.starts_with(parent_qn))
            .collect();

        // For each child method, check if its callers are different from parent's callers
        for child_method in child_methods {
            let method_name = child_method.name.clone();
            let child_callers: HashSet<_> = graph
                .get_callers(&child_method.qualified_name)
                .into_iter()
                .map(|c| c.qualified_name)
                .collect();

            // Find corresponding parent method
            if let Some(parent_method) = parent_methods.iter().find(|m| m.name == method_name) {
                let parent_callers: HashSet<_> = graph
                    .get_callers(&parent_method.qualified_name)
                    .into_iter()
                    .map(|c| c.qualified_name)
                    .collect();

                // If there are callers unique to child, behavior might diverge
                let unique_child_callers: HashSet<_> =
                    child_callers.difference(&parent_callers).collect();
                if unique_child_callers.len() >= 2 {
                    return true;
                }
            }
        }

        false
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
    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();

        // Build set of all class names for polymorphism detection
        let _all_classes: HashSet<String> = graph
            .get_classes()
            .into_iter()
            .map(|c| c.qualified_name.clone())
            .collect();

        for (child_qn, parent_qn) in graph.get_inheritance() {
            // Skip common patterns
            if parent_qn.contains("Base")
                || parent_qn.contains("Abstract")
                || parent_qn.contains("Mixin")
            {
                continue;
            }

            if let Some(child) = graph.get_node(&child_qn) {
                // Check if child overrides many methods without calling super
                let child_methods: Vec<_> = graph
                    .get_functions()
                    .into_iter()
                    .filter(|f| f.qualified_name.starts_with(&child_qn))
                    .collect();

                if child_methods.len() >= 3 {
                    // Count methods that might be refusing bequest (low complexity overrides)
                    let potential_refusals: Vec<_> = child_methods
                        .iter()
                        .filter(|m| m.complexity().unwrap_or(1) <= 2 && m.loc() <= 5)
                        .collect();

                    if potential_refusals.len() >= 2 {
                        // === Graph-enhanced analysis ===

                        // Check if child is used polymorphically (someone calls parent type methods on it)
                        let is_polymorphic =
                            self.check_polymorphic_usage(graph, &child_qn, &parent_qn);

                        // Check inheritance depth (deeper = worse)
                        let inheritance_depth = self.get_inheritance_depth(graph, &parent_qn);

                        // Calculate if child methods are called differently from parent
                        let has_divergent_callers =
                            self.check_divergent_callers(graph, &child_methods, &parent_qn);

                        // Determine severity based on graph analysis
                        let severity = if is_polymorphic && has_divergent_callers {
                            // Used polymorphically but behaves differently - LSP violation!
                            Severity::High
                        } else if is_polymorphic || inheritance_depth >= 3 {
                            // Either polymorphic usage or deep hierarchy
                            Severity::Medium
                        } else {
                            Severity::Low
                        };

                        // Build enhanced description
                        let mut notes = Vec::new();
                        if is_polymorphic {
                            notes.push("⚠️ Used polymorphically (through parent type)".to_string());
                        }
                        if has_divergent_callers {
                            notes.push("📞 Callers use it differently than parent".to_string());
                        }
                        if inheritance_depth >= 2 {
                            notes.push(format!("📊 Inheritance depth: {}", inheritance_depth));
                        }

                        let graph_notes = if notes.is_empty() {
                            String::new()
                        } else {
                            format!("\n\n**Graph analysis:**\n{}", notes.join("\n"))
                        };

                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "RefusedBequestDetector".to_string(),
                            severity,
                            title: format!("Refused Bequest: {}", child.name),
                            description: format!(
                                "Class '{}' inherits from '{}' but may not use inherited behavior properly.\n\n\
                                 {} of {} methods appear to override without using parent.{}",
                                child.name,
                                parent_qn.rsplit("::").next().unwrap_or(&parent_qn),
                                potential_refusals.len(),
                                child_methods.len(),
                                graph_notes
                            ),
                            affected_files: vec![child.file_path.clone().into()],
                            line_start: Some(child.line_start),
                            line_end: Some(child.line_end),
                            suggested_fix: Some(
                                if is_polymorphic {
                                    format!(
                                        "Since '{}' is used polymorphically, consider:\n\
                                         1. Fix the overrides to properly extend parent behavior\n\
                                         2. Extract a new interface that both classes implement\n\
                                         3. Use the Strategy pattern if behavior varies",
                                        child.name
                                    )
                                } else {
                                    "Consider composition over inheritance if not using parent behavior".to_string()
                                }
                            ),
                            estimated_effort: Some(if severity == Severity::High {
                                "Large (2-4 hours)".to_string()
                            } else {
                                "Medium (1-2 hours)".to_string()
                            }),
                            category: Some("structure".to_string()),
                            cwe_id: None,
                            why_it_matters: Some(
                                "Refused bequest indicates improper use of inheritance and may violate \
                                 the Liskov Substitution Principle. This makes code harder to reason about \
                                 and can lead to subtle bugs when the child is used in place of the parent."
                                    .to_string()
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!(
            "RefusedBequestDetector found {} findings (graph-aware)",
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
