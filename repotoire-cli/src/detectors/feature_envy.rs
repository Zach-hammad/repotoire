//! Feature Envy Detector
//!
//! Detects methods that use other classes more than their own class,
//! indicating the method might belong in the other class.
//!
//! This is a code smell that traditional linters cannot detect because it requires
//! understanding cross-class relationships via the knowledge graph.
//!
//! Example:
//! ```text
//! class Order {
//!     fn calculate_discount(&self, customer: &Customer) {
//!         // Uses customer data 10 times, own data 1 time
//!         if customer.loyalty_level() > 5 &&
//!            customer.total_purchases() > 1000 &&
//!            customer.is_premium() { ... }
//!     }
//! }
//! ```
//! This method should probably be on Customer, not Order.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use tracing::{debug, info};
use uuid::Uuid;

/// Thresholds for feature envy detection
#[derive(Debug, Clone)]
pub struct FeatureEnvyThresholds {
    /// Minimum ratio of external to internal uses
    pub threshold_ratio: f64,
    /// Minimum external uses to trigger detection
    pub min_external_uses: usize,
    /// Ratio for critical severity
    pub critical_ratio: f64,
    /// Minimum uses for critical severity
    pub critical_min_uses: usize,
    /// Ratio for high severity
    pub high_ratio: f64,
    /// Minimum uses for high severity
    pub high_min_uses: usize,
    /// Ratio for medium severity
    pub medium_ratio: f64,
    /// Minimum uses for medium severity
    pub medium_min_uses: usize,
}

impl Default for FeatureEnvyThresholds {
    fn default() -> Self {
        Self {
            threshold_ratio: 3.0,
            min_external_uses: 15,
            critical_ratio: 10.0,
            critical_min_uses: 30,
            high_ratio: 5.0,
            high_min_uses: 20,
            medium_ratio: 3.0,
            medium_min_uses: 10,
        }
    }
}

/// Detects methods with feature envy
pub struct FeatureEnvyDetector {
    config: DetectorConfig,
    thresholds: FeatureEnvyThresholds,
}

impl FeatureEnvyDetector {
    /// Create a new detector with default thresholds
    pub fn new() -> Self {
        Self::with_thresholds(FeatureEnvyThresholds::default())
    }

    /// Create with custom thresholds
    pub fn with_thresholds(thresholds: FeatureEnvyThresholds) -> Self {
        Self {
            config: DetectorConfig::new(),
            thresholds,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        let thresholds = FeatureEnvyThresholds {
            threshold_ratio: config.get_option_or("threshold_ratio", 3.0),
            min_external_uses: config.get_option_or("min_external_uses", 15),
            critical_ratio: config.get_option_or("critical_ratio", 10.0),
            critical_min_uses: config.get_option_or("critical_min_uses", 30),
            high_ratio: config.get_option_or("high_ratio", 5.0),
            high_min_uses: config.get_option_or("high_min_uses", 20),
            medium_ratio: config.get_option_or("medium_ratio", 3.0),
            medium_min_uses: config.get_option_or("medium_min_uses", 10),
        };

        Self { config, thresholds }
    }

    /// Calculate severity based on ratio and uses
    fn calculate_severity(&self, ratio: f64, external_uses: usize) -> Severity {
        if ratio >= self.thresholds.critical_ratio
            && external_uses >= self.thresholds.critical_min_uses
        {
            Severity::Critical
        } else if ratio >= self.thresholds.high_ratio
            && external_uses >= self.thresholds.high_min_uses
        {
            Severity::High
        } else if ratio >= self.thresholds.medium_ratio
            && external_uses >= self.thresholds.medium_min_uses
        {
            Severity::Medium
        } else {
            Severity::Low
        }
    }

    /// Estimate effort based on severity
    fn estimate_effort(&self, severity: Severity) -> String {
        match severity {
            Severity::Critical => "Large (2-4 hours)".to_string(),
            Severity::High => "Medium (1-2 hours)".to_string(),
            Severity::Medium => "Small (30-60 minutes)".to_string(),
            Severity::Low | Severity::Info => "Small (15-30 minutes)".to_string(),
        }
    }

    /// Create a finding for feature envy
    fn create_finding(
        &self,
        _method_name: String,
        method_simple: String,
        owner_class: String,
        file_path: String,
        line_start: Option<u32>,
        line_end: Option<u32>,
        internal_uses: usize,
        external_uses: usize,
    ) -> Finding {
        let ratio = if internal_uses > 0 {
            external_uses as f64 / internal_uses as f64
        } else {
            f64::INFINITY
        };

        let severity = self.calculate_severity(ratio, external_uses);

        let suggestion = if internal_uses == 0 {
            format!(
                "Method '{}' uses external classes {} times but never uses its own class. \
                 Consider moving this method to the class it uses most, \
                 or making it a standalone utility function.",
                method_simple, external_uses
            )
        } else {
            format!(
                "Method '{}' uses external classes {} times vs its own class {} times (ratio: {:.1}x). \
                 Consider moving to the most-used external class or refactoring \
                 to reduce external dependencies.",
                method_simple, external_uses, internal_uses, ratio
            )
        };

        let ratio_display = if ratio.is_infinite() {
            "∞".to_string()
        } else {
            format!("{:.1}", ratio)
        };

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "FeatureEnvyDetector".to_string(),
            severity,
            title: format!("Feature Envy: {}", method_simple),
            description: format!(
                "Method '{}' in class '{}' shows feature envy by using external classes \
                 {} times compared to {} internal uses (ratio: {}x).\n\n\
                 This suggests the method may belong in a different class.",
                method_simple,
                owner_class.split('.').last().unwrap_or(&owner_class),
                external_uses,
                internal_uses,
                ratio_display
            ),
            affected_files: vec![PathBuf::from(&file_path)],
            line_start,
            line_end,
            suggested_fix: Some(suggestion),
            estimated_effort: Some(self.estimate_effort(severity)),
            category: Some("code_smell".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Feature envy indicates a method is in the wrong place. \
                 Moving it to the class it actually operates on improves cohesion, \
                 reduces coupling, and makes the code easier to understand and maintain."
                    .to_string(),
            ),
        }
    }
}

impl Default for FeatureEnvyDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for FeatureEnvyDetector {
    fn name(&self) -> &'static str {
        "FeatureEnvyDetector"
    }

    fn description(&self) -> &'static str {
        "Detects methods that use other classes more than their own"
    }

    fn category(&self) -> &'static str {
        "code_smell"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();
        
        for func in graph.get_functions() {
            let callees = graph.get_callees(&func.qualified_name);
            if callees.is_empty() {
                continue;
            }
            
            // Count calls to own file vs other files
            let own_file = &func.file_path;
            let mut internal_calls = 0;
            let mut external_calls = 0;
            
            for callee in &callees {
                if callee.file_path == *own_file {
                    internal_calls += 1;
                } else {
                    external_calls += 1;
                }
            }
            
            // Feature envy: more external than internal calls
            // Threshold: at least 10 external calls to avoid noise on small utility functions
            if external_calls > internal_calls && external_calls >= 10 {
                let ratio = external_calls as f64 / (internal_calls + 1) as f64;
                let severity = if ratio > 8.0 && external_calls >= 25 {
                    Severity::High
                } else if ratio > 5.0 && external_calls >= 15 {
                    Severity::Medium
                } else {
                    Severity::Low
                };
                
                findings.push(Finding {
                    id: Uuid::new_v4().to_string(),
                    detector: "FeatureEnvyDetector".to_string(),
                    severity,
                    title: format!("Feature Envy: {}", func.name),
                    description: format!(
                        "Function '{}' calls {} external functions but only {} internal. It may belong elsewhere.",
                        func.name, external_calls, internal_calls
                    ),
                    affected_files: vec![func.file_path.clone().into()],
                    line_start: Some(func.line_start),
                    line_end: Some(func.line_end),
                    suggested_fix: Some("Consider moving this function to the class it uses most".to_string()),
                    estimated_effort: Some("Medium (1-2 hours)".to_string()),
                    category: Some("coupling".to_string()),
                    cwe_id: None,
                    why_it_matters: Some("Feature envy indicates misplaced functionality".to_string()),
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
        let detector = FeatureEnvyDetector::new();
        assert!((detector.thresholds.threshold_ratio - 3.0).abs() < f64::EPSILON);
        assert_eq!(detector.thresholds.min_external_uses, 15);
    }

    #[test]
    fn test_severity_calculation() {
        let detector = FeatureEnvyDetector::new();

        // Low
        assert_eq!(detector.calculate_severity(2.0, 5), Severity::Low);

        // Medium
        assert_eq!(detector.calculate_severity(3.0, 10), Severity::Medium);

        // High
        assert_eq!(detector.calculate_severity(5.0, 20), Severity::High);

        // Critical
        assert_eq!(detector.calculate_severity(10.0, 30), Severity::Critical);
    }
}
