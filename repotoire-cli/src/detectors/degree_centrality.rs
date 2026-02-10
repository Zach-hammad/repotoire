//! Degree centrality detector
//!
//! Uses in-degree and out-degree to detect:
//! - God Classes: High in-degree (many dependents) + high complexity
//! - Feature Envy: High out-degree (reaching into many modules)
//! - Coupling hotspots: Both high in and out degree

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::{HashMap, HashSet};
use tracing::{debug, info};
use uuid::Uuid;

/// Detects coupling issues using degree centrality.
///
/// Degree centrality measures direct connections:
/// - In-degree: How many functions call this function
/// - Out-degree: How many functions this function calls
///
/// Detects:
/// - God Classes: High in-degree + complexity (many depend on complex code)
/// - Feature Envy: High out-degree (reaching into too many modules)
/// - Coupling Hotspots: Both high in and out degree
pub struct DegreeCentralityDetector {
    config: DetectorConfig,
    /// Complexity threshold for God Class detection
    high_complexity_threshold: u32,
    /// Percentile for "high" degree
    high_percentile: f64,
    /// Minimum in-degree threshold
    min_indegree: usize,
    /// Minimum out-degree threshold
    min_outdegree: usize,
}

impl DegreeCentralityDetector {
    /// Create a new detector with default config
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            high_complexity_threshold: 15,
            high_percentile: 95.0,
            min_indegree: 5,
            min_outdegree: 21, // Raised from 10 - orchestrator/parser functions legitimately call many helpers
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        Self {
            high_complexity_threshold: config.get_option_or("high_complexity_threshold", 15),
            high_percentile: config.get_option_or("high_percentile", 95.0),
            min_indegree: config.get_option_or("min_indegree", 5),
            min_outdegree: config.get_option_or("min_outdegree", 21),
            config,
        }
    }

    /// Create God Class finding
    fn create_god_class_finding(
        &self,
        name: &str,
        qualified_name: &str,
        file_path: &str,
        in_degree: usize,
        out_degree: usize,
        complexity: u32,
        loc: u32,
        max_in_degree: usize,
        threshold: usize,
    ) -> Finding {
        let percentile = if max_in_degree > 0 {
            (in_degree as f64 / max_in_degree as f64) * 100.0
        } else {
            0.0
        };

        let severity = if complexity >= self.high_complexity_threshold * 2 || percentile >= 99.0 {
            Severity::Critical
        } else if complexity >= (self.high_complexity_threshold * 3 / 2) || percentile >= 97.0 {
            Severity::High
        } else {
            Severity::Medium
        };

        let description = format!(
            "File `{}` is a potential **God Class**: high in-degree \
            ({} dependents) combined with high complexity ({}).\n\n\
            **What this means:**\n\
            - Many functions depend on this code ({} callers)\n\
            - The code itself is complex (complexity: {})\n\
            - Changes are high-risk with wide blast radius\n\
            - This is a maintainability bottleneck\n\n\
            **Metrics:**\n\
            - In-degree: {} (threshold: {})\n\
            - Complexity: {}\n\
            - Lines of code: {}\n\
            - Out-degree: {}",
            name,
            in_degree,
            complexity,
            in_degree,
            complexity,
            in_degree,
            threshold,
            complexity,
            loc,
            out_degree
        );

        let suggested_fix = "\
            **For God Classes:**\n\n\
            1. **Extract interfaces**: Define contracts to reduce coupling\n\n\
            2. **Split responsibilities**: Break into focused modules using SRP\n\n\
            3. **Use dependency injection**: Reduce direct imports\n\n\
            4. **Add abstraction layers**: Shield dependents from changes\n\n\
            5. **Prioritize test coverage**: High-risk code needs safety net"
            .to_string();

        let estimated_effort = match severity {
            Severity::Critical => "Large (1-2 days)",
            Severity::High => "Large (4-8 hours)",
            _ => "Medium (2-4 hours)",
        };

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "DegreeCentralityDetector".to_string(),
            severity,
            title: format!("God Class: {}", name),
            description,
            affected_files: vec![file_path.into()],
            line_start: None,
            line_end: None,
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some(estimated_effort.to_string()),
            category: Some("architecture".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "God Classes violate the Single Responsibility Principle. \
                They accumulate too many responsibilities, making them hard to \
                understand, test, and maintain."
                    .to_string(),
            ),
        }
    }

    /// Create Feature Envy finding
    fn create_feature_envy_finding(
        &self,
        name: &str,
        qualified_name: &str,
        file_path: &str,
        in_degree: usize,
        out_degree: usize,
        complexity: u32,
        loc: u32,
        max_out_degree: usize,
        threshold: usize,
    ) -> Finding {
        let percentile = if max_out_degree > 0 {
            (out_degree as f64 / max_out_degree as f64) * 100.0
        } else {
            0.0
        };

        let severity = if percentile >= 99.0 {
            Severity::High
        } else if percentile >= 97.0 {
            Severity::Medium
        } else {
            Severity::Low
        };

        let description = format!(
            "Function `{}` shows **Feature Envy**: calls {} other functions, \
            suggesting it reaches into too many modules.\n\n\
            **What this means:**\n\
            - This function depends on {} other functions\n\
            - May be handling responsibilities that belong elsewhere\n\
            - Tight coupling makes changes cascade\n\
            - Could be a 'God Module' orchestrating everything\n\n\
            **Metrics:**\n\
            - Out-degree: {} (threshold: {})\n\
            - In-degree: {}\n\
            - Complexity: {}\n\
            - Lines of code: {}",
            name, out_degree, out_degree, out_degree, threshold, in_degree, complexity, loc
        );

        let suggested_fix = "\
            **For Feature Envy:**\n\n\
            1. **Move logic to data**: Put behavior where data lives\n\n\
            2. **Extract classes**: Group related functionality\n\n\
            3. **Use delegation**: Have other modules handle their own logic\n\n\
            4. **Review module boundaries**: This may be misplaced code\n\n\
            5. **Apply facade pattern**: If orchestration is needed, make it explicit"
            .to_string();

        let estimated_effort = match severity {
            Severity::High => "Medium (2-4 hours)",
            Severity::Medium => "Medium (1-2 hours)",
            _ => "Small (30-60 minutes)",
        };

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "DegreeCentralityDetector".to_string(),
            severity,
            title: format!("Feature Envy: {}", name),
            description,
            affected_files: vec![file_path.into()],
            line_start: None,
            line_end: None,
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some(estimated_effort.to_string()),
            category: Some("coupling".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Feature Envy occurs when a function uses features of other classes \
                more than its own. This creates tight coupling and makes the code \
                harder to maintain and test."
                    .to_string(),
            ),
        }
    }

    /// Create Coupling Hotspot finding
    fn create_coupling_hotspot_finding(
        &self,
        name: &str,
        qualified_name: &str,
        file_path: &str,
        in_degree: usize,
        out_degree: usize,
        complexity: u32,
        loc: u32,
    ) -> Finding {
        let total_coupling = in_degree + out_degree;

        let severity = if complexity >= self.high_complexity_threshold {
            Severity::Critical
        } else {
            Severity::High
        };

        let description = format!(
            "Function `{}` is a **Coupling Hotspot**: high in-degree ({}) \
            AND high out-degree ({}).\n\n\
            **What this means:**\n\
            - Both heavily depended ON ({} callers)\n\
            - AND heavily dependent ON others ({} callees)\n\
            - Total coupling: {} connections\n\
            - Changes here cascade in both directions\n\
            - This is a critical architectural risk\n\n\
            **Metrics:**\n\
            - In-degree: {}\n\
            - Out-degree: {}\n\
            - Total coupling: {}\n\
            - Complexity: {}\n\
            - Lines of code: {}",
            name,
            in_degree,
            out_degree,
            in_degree,
            out_degree,
            total_coupling,
            in_degree,
            out_degree,
            total_coupling,
            complexity,
            loc
        );

        let suggested_fix = "\
            **For Coupling Hotspots (Critical):**\n\n\
            1. **Architectural review**: This function is a design bottleneck\n\n\
            2. **Split by responsibility**: Extract into focused modules\n\n\
            3. **Introduce layers**: Create abstraction boundaries\n\n\
            4. **Apply SOLID principles**:\n\
               - Single Responsibility (split concerns)\n\
               - Interface Segregation (smaller interfaces)\n\
               - Dependency Inversion (depend on abstractions)\n\n\
            5. **Consider strangler pattern**: Gradually replace with better design"
            .to_string();

        let estimated_effort = if severity == Severity::Critical {
            "Large (1-2 days)"
        } else {
            "Large (4-8 hours)"
        };

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "DegreeCentralityDetector".to_string(),
            severity,
            title: format!("Coupling Hotspot: {}", name),
            description,
            affected_files: vec![file_path.into()],
            line_start: None,
            line_end: None,
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some(estimated_effort.to_string()),
            category: Some("architecture".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Coupling hotspots are the most problematic code - they both depend on \
                many other parts AND are depended on by many parts. Any change here \
                cascades in all directions."
                    .to_string(),
            ),
        }
    }
}

impl Default for DegreeCentralityDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for DegreeCentralityDetector {
    fn name(&self) -> &'static str {
        "DegreeCentralityDetector"
    }

    fn description(&self) -> &'static str {
        "Detects coupling issues using degree centrality (God Classes, Feature Envy, Coupling Hotspots)"
    }

    fn category(&self) -> &'static str {
        "coupling"
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
    fn test_new_detector() {
        let detector = DegreeCentralityDetector::new();
        assert_eq!(detector.high_complexity_threshold, 15);
        assert_eq!(detector.min_indegree, 5);
    }

    #[test]
    fn test_with_config() {
        let config = DetectorConfig::new()
            .with_option("high_complexity_threshold", serde_json::json!(25))
            .with_option("min_indegree", serde_json::json!(10));
        let detector = DegreeCentralityDetector::with_config(config);
        assert_eq!(detector.high_complexity_threshold, 25);
        assert_eq!(detector.min_indegree, 10);
    }
}
