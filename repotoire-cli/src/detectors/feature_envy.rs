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
use crate::graph::GraphClient;
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
    }

    fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        debug!("Starting feature envy detection");

        let query = r#"
            MATCH (c:Class)-[:CONTAINS]->(m:Function)
            WHERE m.is_method = true

            // Count internal uses (same class)
            OPTIONAL MATCH (m)-[r_internal:USES|CALLS]->()-[:CONTAINS*0..1]-(c)
            WITH m, c, count(DISTINCT r_internal) as internal_uses

            // Count external uses (other classes)
            OPTIONAL MATCH (m)-[r_external:USES|CALLS]->(target:Function)
            WHERE NOT (target)-[:CONTAINS*0..1]-(c)
            WITH m, c, internal_uses, count(DISTINCT r_external) as external_uses

            // Filter based on thresholds
            WHERE external_uses >= $min_external_uses
              AND (internal_uses = 0 OR external_uses > internal_uses * $threshold_ratio)

            RETURN m.qualifiedName as method,
                   m.name as method_name,
                   c.qualifiedName as owner_class,
                   m.filePath as file_path,
                   m.lineStart as line_start,
                   m.lineEnd as line_end,
                   internal_uses,
                   external_uses
            ORDER BY external_uses DESC
            LIMIT 100
        "#;

        let _params = serde_json::json!({
            "threshold_ratio": self.thresholds.threshold_ratio,
            "min_external_uses": self.thresholds.min_external_uses,
        });

        let results = graph.execute(query)?;

        if results.is_empty() {
            debug!("No feature envy detected");
            return Ok(vec![]);
        }

        let mut findings = Vec::new();

        for row in results {
            let method_name = row
                .get("method")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let method_simple = row
                .get("method_name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let owner_class = row
                .get("owner_class")
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

            let internal_uses = row
                .get("internal_uses")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as usize;

            let external_uses = row
                .get("external_uses")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as usize;

            findings.push(self.create_finding(
                method_name,
                method_simple,
                owner_class,
                file_path,
                line_start,
                line_end,
                internal_uses,
                external_uses,
            ));
        }

        // Sort by severity
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        info!(
            "FeatureEnvyDetector found {} methods with feature envy",
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
