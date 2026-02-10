//! Message Chain detector for Law of Demeter violations
//!
//! Detects long method chains that violate the Law of Demeter principle:
//! - Deep coupling through chains of 4+ method calls
//! - Excessive knowledge of object internals
//! - Tight coupling that makes code brittle to changes
//!
//! Example violation:
//! ```text
//! user.get_profile().get_settings().get_notifications().is_email_enabled()
//! ```
//!
//! Better approach:
//! ```text
//! user.wants_email_notifications()
//! ```

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphClient;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use tracing::{debug, info};
use uuid::Uuid;

/// Thresholds for message chain detection
#[derive(Debug, Clone)]
pub struct MessageChainThresholds {
    /// Minimum chain depth to report
    pub min_chain_depth: usize,
    /// Chain depth for high severity
    pub high_severity_depth: usize,
    /// Chain depth for critical severity
    pub critical_severity_depth: usize,
    /// Report chains of depth 3 (low severity)
    pub report_low_severity: bool,
}

impl Default for MessageChainThresholds {
    fn default() -> Self {
        Self {
            min_chain_depth: 4,
            high_severity_depth: 5,
            critical_severity_depth: 7,
            report_low_severity: false,
        }
    }
}

/// Detects Law of Demeter violations through long method chains
pub struct MessageChainDetector {
    config: DetectorConfig,
    thresholds: MessageChainThresholds,
}

impl MessageChainDetector {
    /// Create a new detector with default thresholds
    pub fn new() -> Self {
        Self::with_thresholds(MessageChainThresholds::default())
    }

    /// Create with custom thresholds
    pub fn with_thresholds(thresholds: MessageChainThresholds) -> Self {
        Self {
            config: DetectorConfig::new(),
            thresholds,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        let thresholds = MessageChainThresholds {
            min_chain_depth: config.get_option_or("min_chain_depth", 4),
            high_severity_depth: config.get_option_or("high_severity_depth", 5),
            critical_severity_depth: config.get_option_or("critical_severity_depth", 7),
            report_low_severity: config.get_option_or("report_low_severity", false),
        };

        Self { config, thresholds }
    }

    /// Calculate severity based on chain depth
    fn calculate_severity(&self, chain_depth: usize) -> Severity {
        if chain_depth >= self.thresholds.critical_severity_depth {
            Severity::Critical
        } else if chain_depth >= self.thresholds.high_severity_depth {
            Severity::High
        } else {
            Severity::Medium
        }
    }

    /// Estimate effort based on chain depth
    fn estimate_effort(&self, chain_depth: usize) -> String {
        if chain_depth >= 7 {
            "Medium (2-4 hours)".to_string()
        } else if chain_depth >= 5 {
            "Small (1-2 hours)".to_string()
        } else {
            "Small (30-60 minutes)".to_string()
        }
    }

    /// Create a finding for a message chain violation
    fn create_finding(
        &self,
        _func_name: String,
        func_simple_name: String,
        file_path: String,
        line_number: Option<u32>,
        chain_depth: usize,
        chain_example: Option<String>,
    ) -> Finding {
        let severity = self.calculate_severity(chain_depth);

        let example_text = chain_example
            .as_ref()
            .map(|e| format!("\n\n**Example chain:**\n```\n{}\n```", e))
            .unwrap_or_default();

        let description = format!(
            "Method chain with **{} levels** violates the Law of Demeter.\n\n\
             Function `{}` contains method chains that traverse {} levels deep into object structures. \
             This indicates:\n\
             - Tight coupling to internal object structure\n\
             - Violation of encapsulation principles\n\
             - Fragile code that breaks when intermediate objects change{}",
            chain_depth, func_simple_name, chain_depth, example_text
        );

        let suggestion = 
            "**Refactoring approaches:**\n\n\
             1. **Delegate to intermediate object:**\n\
                Instead of `a.b().c().d()`, add `a.get_d()` that internally calls `b().c().d()`\n\n\
             2. **Use Tell, Don't Ask principle:**\n\
                Instead of `user.get_profile().get_settings().get_notifications().is_email_enabled()`\n\
                Use `user.wants_email_notifications()`\n\n\
             3. **Consider a Facade pattern:**\n\
                Create a simpler interface that hides the chain complexity\n\n\
             4. **Extract a method:**\n\
                If the chain retrieves data for computation, extract a method on the first object"
            .to_string();

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "MessageChainDetector".to_string(),
            severity,
            title: format!(
                "Law of Demeter violation: {}-level chain in {}",
                chain_depth, func_simple_name
            ),
            description,
            affected_files: if file_path.is_empty() {
                vec![]
            } else {
                vec![PathBuf::from(&file_path)]
            },
            line_start: line_number,
            line_end: None,
            suggested_fix: Some(suggestion),
            estimated_effort: Some(self.estimate_effort(chain_depth)),
            category: Some("coupling".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "The Law of Demeter (principle of least knowledge) states that a method \
                 should only talk to its immediate friends, not to strangers. Long method \
                 chains create tight coupling to the internal structure of objects, making \
                 the code fragile and hard to change."
                    .to_string(),
            ),
        }
    }
}

impl Default for MessageChainDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for MessageChainDetector {
    fn name(&self) -> &'static str {
        "MessageChainDetector"
    }

    fn description(&self) -> &'static str {
        "Detects Law of Demeter violations through long method chains"
    }

    fn category(&self) -> &'static str {
        "coupling"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        debug!("Starting message chain detection");

        // Query for functions with high chain depth
        // This property would be set by the parser if it extracts chain depths
        let query = r#"
            MATCH (f:Function)
            WHERE COALESCE(f.max_chain_depth, 0) >= $min_depth
            OPTIONAL MATCH (file:File)-[:CONTAINS*]->(f)
            RETURN f.qualifiedName AS func_name,
                   f.name AS func_simple_name,
                   f.filePath AS func_file,
                   f.lineStart AS func_line,
                   f.max_chain_depth AS chain_depth,
                   f.chain_example AS chain_example,
                   file.filePath AS containing_file
            ORDER BY f.max_chain_depth DESC
            LIMIT 100
        "#;

        let _params = serde_json::json!({
            "min_depth": self.thresholds.min_chain_depth,
        });

        let results = graph.execute(query)?;

        if results.is_empty() {
            debug!("No message chain violations found");
            return Ok(vec![]);
        }

        let mut findings = Vec::new();

        for row in results {
            let func_name = row
                .get("func_name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let func_simple_name = row
                .get("func_simple_name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let file_path = row
                .get("containing_file")
                .or_else(|| row.get("func_file"))
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let line_number = row
                .get("func_line")
                .and_then(|v| v.as_u64())
                .map(|v| v as u32);

            let chain_depth = row
                .get("chain_depth")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as usize;

            let chain_example = row
                .get("chain_example")
                .and_then(|v| v.as_str())
                .map(String::from);

            if chain_depth < self.thresholds.min_chain_depth {
                continue;
            }

            findings.push(self.create_finding(
                func_name,
                func_simple_name,
                file_path,
                line_number,
                chain_depth,
                chain_example,
            ));
        }

        // Sort by severity
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        info!(
            "MessageChainDetector found {} message chain violations",
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
        let detector = MessageChainDetector::new();
        assert_eq!(detector.thresholds.min_chain_depth, 4);
        assert_eq!(detector.thresholds.high_severity_depth, 5);
        assert_eq!(detector.thresholds.critical_severity_depth, 7);
    }

    #[test]
    fn test_severity_calculation() {
        let detector = MessageChainDetector::new();

        assert_eq!(detector.calculate_severity(4), Severity::Medium);
        assert_eq!(detector.calculate_severity(5), Severity::High);
        assert_eq!(detector.calculate_severity(6), Severity::High);
        assert_eq!(detector.calculate_severity(7), Severity::Critical);
        assert_eq!(detector.calculate_severity(10), Severity::Critical);
    }
}
