//! Inappropriate Intimacy Detector
//!
//! Detects pairs of classes that are too tightly coupled, accessing each other's
//! internals excessively. This violates encapsulation and makes changes difficult.
//!
//! Traditional linters cannot detect this pattern as it requires tracking
//! bidirectional relationships between classes.
//!
//! Example:
//! ```text
//! class Order {
//!     fn process(&self, customer: &Customer) {
//!         customer.internal_field = ...;  // 10 times
//!     }
//! }
//! class Customer {
//!     fn validate(&self, order: &Order) {
//!         order.internal_field = ...;  // 8 times
//!     }
//! }
//! ```
//! These classes know too much about each other's internals.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphClient;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use tracing::{debug, info};
use uuid::Uuid;

/// Thresholds for inappropriate intimacy detection
#[derive(Debug, Clone)]
pub struct InappropriateIntimacyThresholds {
    /// Total coupling for high severity
    pub threshold_high: usize,
    /// Total coupling for medium severity
    pub threshold_medium: usize,
    /// Minimum mutual access to consider
    pub min_mutual_access: usize,
}

impl Default for InappropriateIntimacyThresholds {
    fn default() -> Self {
        Self {
            threshold_high: 20,
            threshold_medium: 10,
            min_mutual_access: 5,
        }
    }
}

/// Detects classes that are too tightly coupled
pub struct InappropriateIntimacyDetector {
    config: DetectorConfig,
    thresholds: InappropriateIntimacyThresholds,
}

impl InappropriateIntimacyDetector {
    /// Create a new detector with default thresholds
    pub fn new() -> Self {
        Self::with_thresholds(InappropriateIntimacyThresholds::default())
    }

    /// Create with custom thresholds
    pub fn with_thresholds(thresholds: InappropriateIntimacyThresholds) -> Self {
        Self {
            config: DetectorConfig::new(),
            thresholds,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        let thresholds = InappropriateIntimacyThresholds {
            threshold_high: config.get_option_or("threshold_high", 20),
            threshold_medium: config.get_option_or("threshold_medium", 10),
            min_mutual_access: config.get_option_or("min_mutual_access", 5),
        };

        Self { config, thresholds }
    }

    /// Calculate severity based on total coupling
    fn calculate_severity(&self, total_coupling: usize) -> Severity {
        if total_coupling >= self.thresholds.threshold_high {
            Severity::High
        } else if total_coupling >= self.thresholds.threshold_medium {
            Severity::Medium
        } else {
            Severity::Low
        }
    }

    /// Estimate effort based on severity
    fn estimate_effort(&self, severity: Severity) -> String {
        match severity {
            Severity::Critical => "Large (8+ hours)".to_string(),
            Severity::High => "Large (4-8 hours)".to_string(),
            Severity::Medium => "Medium (2-4 hours)".to_string(),
            Severity::Low | Severity::Info => "Medium (1-2 hours)".to_string(),
        }
    }

    /// Create a finding for inappropriate intimacy
    fn create_finding(
        &self,
        _class1: String,
        class1_name: String,
        _class2: String,
        class2_name: String,
        file1: String,
        file2: String,
        c1_to_c2: usize,
        c2_to_c1: usize,
    ) -> Finding {
        let total_coupling = c1_to_c2 + c2_to_c1;
        let severity = self.calculate_severity(total_coupling);
        let same_file = file1 == file2;
        let same_file_note = if same_file {
            " (same file)"
        } else {
            " (different files)"
        };

        let suggestion = if severity == Severity::High {
            format!(
                "Classes '{}' and '{}' have excessive mutual access ({} total accesses: \
                 {} and {} respectively).\n\n\
                 This tight coupling violates encapsulation. Consider:\n\
                 1. Merge the classes if they truly belong together\n\
                 2. Extract common data into a shared class\n\
                 3. Apply the Law of Demeter - don't access internals directly\n\
                 4. Introduce interfaces or abstract base classes to reduce coupling",
                class1_name, class2_name, total_coupling, c1_to_c2, c2_to_c1
            )
        } else {
            format!(
                "Classes '{}' and '{}' show inappropriate intimacy ({} mutual accesses). \
                 Consider refactoring to reduce coupling.",
                class1_name, class2_name, total_coupling
            )
        };

        let mut affected_files = vec![PathBuf::from(&file1)];
        if file1 != file2 {
            affected_files.push(PathBuf::from(&file2));
        }

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "InappropriateIntimacyDetector".to_string(),
            severity,
            title: format!("Inappropriate Intimacy: {} ↔ {}", class1_name, class2_name),
            description: format!(
                "Classes '{}' and '{}' are too tightly coupled{}:\n\
                 • {} → {}: {} accesses\n\
                 • {} → {}: {} accesses\n\
                 • Total coupling: {} mutual accesses\n\n\
                 This bidirectional coupling makes both classes difficult to change independently \
                 and violates encapsulation principles.",
                class1_name,
                class2_name,
                same_file_note,
                class1_name,
                class2_name,
                c1_to_c2,
                class2_name,
                class1_name,
                c2_to_c1,
                total_coupling
            ),
            affected_files,
            line_start: None,
            line_end: None,
            suggested_fix: Some(suggestion),
            estimated_effort: Some(self.estimate_effort(severity)),
            category: Some("coupling".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Inappropriate intimacy makes classes hard to change independently. \
                 When two classes know too much about each other's internals, changes \
                 to one often require changes to the other, leading to ripple effects \
                 and increased maintenance costs."
                    .to_string(),
            ),
        }
    }
}

impl Default for InappropriateIntimacyDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for InappropriateIntimacyDetector {
    fn name(&self) -> &'static str {
        "InappropriateIntimacyDetector"
    }

    fn description(&self) -> &'static str {
        "Detects classes that are too tightly coupled"
    }

    fn category(&self) -> &'static str {
        "coupling"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        debug!("Starting inappropriate intimacy detection");

        let query = r#"
            // Find pairs of classes with excessive mutual access
            MATCH (c1:Class)-[:CONTAINS]->(m1:Function)
            MATCH (m1)-[r:USES|CALLS]->()-[:CONTAINS*0..1]-(c2:Class)
            WHERE c1 <> c2
            WITH c1, c2, count(r) as c1_to_c2

            // Get the reverse direction
            MATCH (c2)-[:CONTAINS]->(m2:Function)
            MATCH (m2)-[r:USES|CALLS]->()-[:CONTAINS*0..1]-(c1)
            WITH c1, c2, c1_to_c2, count(r) as c2_to_c1

            // Filter for mutual high coupling
            WHERE c1_to_c2 >= $min_mutual_access
              AND c2_to_c1 >= $min_mutual_access
              AND id(c1) < id(c2)

            RETURN c1.qualifiedName as class1,
                   c1.name as class1_name,
                   c2.qualifiedName as class2,
                   c2.name as class2_name,
                   c1.filePath as file1,
                   c2.filePath as file2,
                   c1_to_c2,
                   c2_to_c1,
                   (c1_to_c2 + c2_to_c1) as total_coupling
            ORDER BY total_coupling DESC
            LIMIT 50
        "#;

        let _params = serde_json::json!({
            "min_mutual_access": self.thresholds.min_mutual_access,
        });

        let results = graph.execute(query)?;

        if results.is_empty() {
            debug!("No inappropriate intimacy detected");
            return Ok(vec![]);
        }

        let mut findings = Vec::new();

        for row in results {
            let class1 = row
                .get("class1")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let class1_name = row
                .get("class1_name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let class2 = row
                .get("class2")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let class2_name = row
                .get("class2_name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let file1 = row
                .get("file1")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let file2 = row
                .get("file2")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let c1_to_c2 = row
                .get("c1_to_c2")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as usize;

            let c2_to_c1 = row
                .get("c2_to_c1")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as usize;

            findings.push(self.create_finding(
                class1,
                class1_name,
                class2,
                class2_name,
                file1,
                file2,
                c1_to_c2,
                c2_to_c1,
            ));
        }

        // Sort by severity
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        info!(
            "InappropriateIntimacyDetector found {} tightly coupled class pairs",
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
        let detector = InappropriateIntimacyDetector::new();
        assert_eq!(detector.thresholds.threshold_high, 20);
        assert_eq!(detector.thresholds.threshold_medium, 10);
        assert_eq!(detector.thresholds.min_mutual_access, 5);
    }

    #[test]
    fn test_severity_calculation() {
        let detector = InappropriateIntimacyDetector::new();

        assert_eq!(detector.calculate_severity(5), Severity::Low);
        assert_eq!(detector.calculate_severity(10), Severity::Medium);
        assert_eq!(detector.calculate_severity(15), Severity::Medium);
        assert_eq!(detector.calculate_severity(20), Severity::High);
        assert_eq!(detector.calculate_severity(30), Severity::High);
    }
}
