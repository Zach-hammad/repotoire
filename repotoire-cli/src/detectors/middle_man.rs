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
use crate::graph::GraphClient;
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
    }

    fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        debug!("Starting middle man detection");

        let query = r#"
            // First count total methods per class
            MATCH (c:Class)-[:CONTAINS]->(all_m:Function)
            WHERE all_m.is_method = true
            WITH c, count(all_m) as total_methods
            WHERE total_methods > 0

            // Find delegation patterns
            MATCH (c)-[:CONTAINS]->(m:Function)
            WHERE m.is_method = true
              AND (m.complexity IS NULL OR m.complexity <= $max_complexity)
            MATCH (m)-[:CALLS]->(delegated:Function)
            MATCH (delegated)<-[:CONTAINS]-(target:Class)
            WHERE c <> target

            WITH c, target, total_methods,
                 count(DISTINCT m) as delegation_count

            // Filter based on thresholds
            WHERE delegation_count >= $min_delegation_methods
              AND cast(delegation_count, "DOUBLE") / total_methods >= $delegation_threshold

            RETURN c.qualifiedName as middle_man,
                   c.name as class_name,
                   c.filePath as file_path,
                   c.lineStart as line_start,
                   c.lineEnd as line_end,
                   target.qualifiedName as delegates_to,
                   target.name as target_name,
                   delegation_count,
                   total_methods,
                   cast(delegation_count * 100, "DOUBLE") / total_methods as delegation_percentage
            ORDER BY delegation_percentage DESC
            LIMIT 50
        "#;

        let _params = serde_json::json!({
            "min_delegation_methods": self.thresholds.min_delegation_methods,
            "delegation_threshold": self.thresholds.delegation_threshold,
            "max_complexity": self.thresholds.max_complexity,
        });

        let results = graph.execute(query)?;

        if results.is_empty() {
            debug!("No middle man classes found");
            return Ok(vec![]);
        }

        let mut findings = Vec::new();

        for row in results {
            let class_name = row
                .get("middle_man")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let class_simple = row
                .get("class_name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let target_class = row
                .get("delegates_to")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let target_name = row
                .get("target_name")
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

            let delegation_count = row
                .get("delegation_count")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as usize;

            let total_methods = row
                .get("total_methods")
                .and_then(|v| v.as_u64())
                .unwrap_or(1) as usize;

            findings.push(self.create_finding(
                class_name,
                class_simple,
                target_class,
                target_name,
                file_path,
                line_start,
                line_end,
                delegation_count,
                total_methods,
            ));
        }

        // Sort by severity
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        info!(
            "MiddleManDetector found {} classes acting as middle men",
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
