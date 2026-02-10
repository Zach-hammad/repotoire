//! Middle Man Detector
//!
//! Detects classes that mostly delegate to other classes without adding value,
//! indicating unnecessary indirection.
//!
//! Traditional linters cannot detect this pattern as it requires analyzing
//! method call patterns across classes.
//!
//! Example:
//! ```text
//! class OrderManager {
//!     fn create_order(&self, data: OrderData) { self.order_service.create(data) }
//!     fn update_order(&self, id: u64, data: OrderData) { self.order_service.update(id, data) }
//!     fn delete_order(&self, id: u64) { self.order_service.delete(id) }
//!     fn get_order(&self, id: u64) { self.order_service.get(id) }
//! }
//! ```
//! This class just delegates - use OrderService directly.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use tracing::{debug, info};
use uuid::Uuid;

/// Thresholds for middle man detection
#[derive(Debug, Clone)]
pub struct MiddleManThresholds {
    /// Minimum methods that delegate to trigger detection
    pub min_delegation_methods: usize,
    /// Percentage of methods that delegate (0.0 - 1.0)
    pub delegation_threshold: f64,
    /// Maximum complexity for a method to be considered pure delegation
    pub max_complexity: usize,
}

impl Default for MiddleManThresholds {
    fn default() -> Self {
        Self {
            min_delegation_methods: 3,
            delegation_threshold: 0.7,
            max_complexity: 2,
        }
    }
}

/// Detects classes that mostly delegate to other classes
pub struct MiddleManDetector {
    config: DetectorConfig,
    thresholds: MiddleManThresholds,
}

impl MiddleManDetector {
    /// Create a new detector with default thresholds
    pub fn new() -> Self {
        Self::with_thresholds(MiddleManThresholds::default())
    }

    /// Create with custom thresholds
    pub fn with_thresholds(thresholds: MiddleManThresholds) -> Self {
        Self {
            config: DetectorConfig::new(),
            thresholds,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        let thresholds = MiddleManThresholds {
            min_delegation_methods: config.get_option_or("min_delegation_methods", 3),
            delegation_threshold: config.get_option_or("delegation_threshold", 0.7),
            max_complexity: config.get_option_or("max_complexity", 2),
        };

        Self { config, thresholds }
    }

    /// Calculate severity based on delegation percentage
    fn calculate_severity(&self, delegation_pct: f64) -> Severity {
        if delegation_pct >= 90.0 {
            Severity::High
        } else if delegation_pct >= 70.0 {
            Severity::Medium
        } else {
            Severity::Low
        }
    }

    /// Estimate effort based on severity
    fn estimate_effort(&self, severity: Severity) -> String {
        match severity {
            Severity::Critical | Severity::High => "Medium (1-2 hours)".to_string(),
            Severity::Medium => "Small (30-60 minutes)".to_string(),
            Severity::Low | Severity::Info => "Small (15-30 minutes)".to_string(),
        }
    }

    /// Create a finding for a middle man class
    fn create_finding(
        &self,
        _class_name: String,
        class_simple: String,
        _target_class: String,
        target_name: String,
        file_path: String,
        line_start: Option<u32>,
        line_end: Option<u32>,
        delegation_count: usize,
        total_methods: usize,
    ) -> Finding {
        let delegation_pct = (delegation_count as f64 / total_methods as f64) * 100.0;
        let severity = self.calculate_severity(delegation_pct);

        let suggestion = if delegation_pct >= 90.0 {
            format!(
                "Class '{}' delegates {:.0}% of methods ({}/{}) to '{}'. Consider:\n\
                 1. Remove the middle man and use '{}' directly\n\
                 2. If this is a facade, add value by combining operations\n\
                 3. Document the architectural reason if delegation is intentional",
                class_simple,
                delegation_pct,
                delegation_count,
                total_methods,
                target_name,
                target_name
            )
        } else {
            format!(
                "Class '{}' delegates {:.0}% of methods to '{}'. \
                 Consider whether this indirection adds value.",
                class_simple, delegation_pct, target_name
            )
        };

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "MiddleManDetector".to_string(),
            severity,
            title: format!("Middle Man: {}", class_simple),
            description: format!(
                "Class '{}' acts as a middle man, delegating {} out of {} methods \
                 ({:.0}%) to '{}' without adding significant value.\n\n\
                 This pattern adds unnecessary indirection and increases maintenance burden. \
                 Simple delegation methods with low complexity suggest the class may not be needed.",
                class_simple, delegation_count, total_methods, delegation_pct, target_name
            ),
            affected_files: vec![PathBuf::from(&file_path)],
            line_start,
            line_end,
            suggested_fix: Some(suggestion),
            estimated_effort: Some(self.estimate_effort(severity)),
            category: Some("design".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Middle man classes add unnecessary indirection without providing value. \
                 They make the codebase harder to navigate, increase call stack depth, \
                 and add maintenance overhead. If a class only forwards calls to another, \
                 consider removing it or giving it real responsibilities."
                    .to_string(),
            ),
        }
    }
}

impl Default for MiddleManDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for MiddleManDetector {
    fn name(&self) -> &'static str {
        "MiddleManDetector"
    }

    fn description(&self) -> &'static str {
        "Detects classes that mostly delegate to other classes"
    }

    fn category(&self) -> &'static str {
        "design"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();
        
        for class in graph.get_classes() {
            let method_count = class.get_i64("methodCount").unwrap_or(0) as usize;
            if method_count == 0 {
                continue;
            }
            
            // Get methods of this class
            let methods: Vec<_> = graph.get_functions()
                .into_iter()
                .filter(|f| f.qualified_name.starts_with(&class.qualified_name))
                .collect();
            
            // Check how many methods just delegate
            let mut delegating = 0;
            for method in &methods {
                let callees = graph.get_callees(&method.qualified_name);
                // Simple delegation: 1 callee, low complexity
                if callees.len() == 1 && method.complexity().unwrap_or(1) <= 2 {
                    delegating += 1;
                }
            }
            
            // Middle man: most methods just delegate
            if methods.len() >= 3 && delegating as f64 / methods.len() as f64 > 0.7 {
                findings.push(Finding {
                    id: Uuid::new_v4().to_string(),
                    detector: "MiddleManDetector".to_string(),
                    severity: Severity::Medium,
                    title: format!("Middle Man: {}", class.name),
                    description: format!(
                        "Class '{}' delegates {} of {} methods. Consider removing the middle man.",
                        class.name, delegating, methods.len()
                    ),
                    affected_files: vec![class.file_path.clone().into()],
                    line_start: Some(class.line_start),
                    line_end: Some(class.line_end),
                    suggested_fix: Some("Remove the middle man by having clients call the delegate directly".to_string()),
                    estimated_effort: Some("Medium (1-2 hours)".to_string()),
                    category: Some("structure".to_string()),
                    cwe_id: None,
                    why_it_matters: Some("Middle man classes add indirection without value".to_string()),
                });
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
        let detector = MiddleManDetector::new();
        assert_eq!(detector.thresholds.min_delegation_methods, 3);
        assert!((detector.thresholds.delegation_threshold - 0.7).abs() < f64::EPSILON);
        assert_eq!(detector.thresholds.max_complexity, 2);
    }

    #[test]
    fn test_severity_calculation() {
        let detector = MiddleManDetector::new();

        assert_eq!(detector.calculate_severity(60.0), Severity::Low);
        assert_eq!(detector.calculate_severity(70.0), Severity::Medium);
        assert_eq!(detector.calculate_severity(85.0), Severity::Medium);
        assert_eq!(detector.calculate_severity(90.0), Severity::High);
        assert_eq!(detector.calculate_severity(100.0), Severity::High);
    }
}
