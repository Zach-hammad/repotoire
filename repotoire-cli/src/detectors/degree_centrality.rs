//! Degree centrality detector
//!
//! Uses in-degree and out-degree to detect coupling issues.
//! Now enhanced with function context for smarter role-based detection.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::function_context::{FunctionContextMap, FunctionRole};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::sync::Arc;
use tracing::debug;
use uuid::Uuid;

/// Detects coupling issues using degree centrality.
///
/// Degree centrality measures direct connections:
/// - In-degree: How many functions call this function
/// - Out-degree: How many functions this function calls
///
/// Now uses FunctionContext to make smarter decisions:
/// - Utility functions: High fan-in expected, only flag if also high fan-out
/// - Orchestrators: High fan-out expected, only flag if also high fan-in
/// - Hubs: Both high fan-in and fan-out - genuine coupling problems
pub struct DegreeCentralityDetector {
    config: DetectorConfig,
    /// Complexity threshold for severity escalation
    high_complexity_threshold: u32,
    /// Minimum total degree for coupling hotspot
    min_total_degree: usize,
    /// Minimum fan-in to be considered elevated
    min_elevated_fanin: usize,
    /// Minimum fan-out to be considered elevated
    min_elevated_fanout: usize,
}

impl DegreeCentralityDetector {
    /// Create a new detector with default config
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            high_complexity_threshold: 15,
            min_total_degree: 30,
            min_elevated_fanin: 8,
            min_elevated_fanout: 8,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        Self {
            high_complexity_threshold: config.get_option_or("high_complexity_threshold", 15),
            min_total_degree: config.get_option_or("min_total_degree", 30),
            min_elevated_fanin: config.get_option_or("min_elevated_fanin", 8),
            min_elevated_fanout: config.get_option_or("min_elevated_fanout", 8),
            config,
        }
    }

    /// Calculate severity based on metrics and function role
    fn calculate_severity(
        &self,
        fan_in: usize,
        fan_out: usize,
        role: FunctionRole,
    ) -> Severity {
        let total = fan_in + fan_out;

        // Base severity from raw metrics
        let base_severity = if total >= 60 && fan_in >= 15 && fan_out >= 15 {
            Severity::High
        } else if total >= 40 {
            Severity::Medium
        } else {
            Severity::Low
        };

        // Adjust based on function role
        match role {
            FunctionRole::Utility => {
                // Utilities are expected to have high fan-in
                // Only flag if fan-out is also problematic
                if fan_out < self.min_elevated_fanout * 2 {
                    Severity::Low // Expected behavior
                } else {
                    base_severity.min(Severity::Medium)
                }
            }
            FunctionRole::Orchestrator => {
                // Orchestrators are expected to have high fan-out
                // Only flag if fan-in is also problematic
                if fan_in < self.min_elevated_fanin * 2 {
                    Severity::Low // Expected behavior
                } else {
                    base_severity.min(Severity::Medium)
                }
            }
            FunctionRole::Leaf => {
                // Leaf functions shouldn't have high coupling
                base_severity.min(Severity::Medium)
            }
            FunctionRole::Test => {
                Severity::Low
            }
            FunctionRole::Hub => {
                // Hubs are genuine coupling concerns
                base_severity
            }
            FunctionRole::EntryPoint | FunctionRole::Unknown => {
                base_severity
            }
        }
    }

    /// Legacy name-based skip check (fallback when no context available)
    fn should_skip_by_name(&self, name: &str) -> bool {
        const SKIP_NAMES: &[&str] = &[
            "new", "default", "from", "into", "create", "build", "make", "with",
            "clone", "drop", "fmt", "eq", "hash", "cmp", "partial_cmp",
            "get", "set", "instance", "global", "shared", "current",
            "run", "main", "init", "setup", "start", "execute", "dispatch", "handle",
            "read", "write", "parse", "format", "render", "display", "detect", "analyze",
            "iter", "next", "map", "filter", "fold",
            "is_", "has_", "check_", "validate_", "should_", "can_", "find_",
            "calculate_", "compute_", "scan_", "extract_", "normalize_",
        ];

        let name_lower = name.to_lowercase();
        SKIP_NAMES.iter().any(|&skip| {
            name_lower == skip
                || name_lower.starts_with(&format!("{}_", skip))
                || name_lower.starts_with(skip)
        })
    }

    /// Check if path is a natural hub file
    fn is_hub_file(&self, path: &str) -> bool {
        const SKIP_PATHS: &[&str] = &[
            "/mod.rs", "/lib.rs", "/main.rs", "/cli/", "/handlers/",
            "/mcp/", "/parsers/", "/server.rs", "/router.rs",
        ];
        SKIP_PATHS.iter().any(|&pat| path.contains(pat))
    }

    /// Create a coupling finding
    fn create_finding(
        &self,
        name: &str,
        file_path: &str,
        line_start: u32,
        line_end: u32,
        fan_in: usize,
        fan_out: usize,
        role: FunctionRole,
    ) -> Finding {
        let severity = self.calculate_severity(fan_in, fan_out, role);
        let total = fan_in + fan_out;

        let role_note = match role {
            FunctionRole::Utility => " (utility - high fan-in expected)",
            FunctionRole::Orchestrator => " (orchestrator - high fan-out expected)",
            FunctionRole::Hub => " (architectural hub)",
            _ => "",
        };

        let title = format!("High Coupling: {}{}", name, role_note);

        let description = format!(
            "Function '{}' has {} connections ({} callers, {} callees). \
            High coupling increases change risk.\n\n\
            **Analysis:**\n\
            - In-degree (callers): {}\n\
            - Out-degree (callees): {}\n\
            - Total coupling: {}",
            name, total, fan_in, fan_out, fan_in, fan_out, total
        );

        let suggested_fix = match role {
            FunctionRole::Utility => {
                "This utility is more coupled than expected. Consider:\n\
                - Breaking into smaller, focused helpers\n\
                - Reducing its dependencies on other modules"
                    .to_string()
            }
            FunctionRole::Hub => {
                "This is a coupling hotspot. Consider:\n\
                - Introducing abstraction layers\n\
                - Applying facade pattern\n\
                - Splitting by responsibility"
                    .to_string()
            }
            _ => {
                "Consider breaking into smaller functions or using dependency injection"
                    .to_string()
            }
        };

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "DegreeCentralityDetector".to_string(),
            severity,
            title,
            description,
            affected_files: vec![file_path.into()],
            line_start: Some(line_start),
            line_end: Some(line_end),
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some("Medium (2-4 hours)".to_string()),
            category: Some("coupling".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Highly coupled code is harder to change and test. Changes cascade unpredictably."
                    .to_string(),
            ),
            ..Default::default()
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
        "Detects coupling issues using degree centrality"
    }

    fn category(&self) -> &'static str {
        "coupling"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    /// Legacy detection without context
    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();

        for func in graph.get_functions() {
            // Skip by name or hub file
            if self.should_skip_by_name(&func.name) || self.is_hub_file(&func.file_path) {
                continue;
            }

            let fan_in = graph.call_fan_in(&func.qualified_name);
            let fan_out = graph.call_fan_out(&func.qualified_name);
            let total_degree = fan_in + fan_out;

            // Skip expected patterns
            if fan_in > 20 && fan_out < 5 {
                continue; // Utility pattern
            }
            if fan_out > 20 && fan_in < 5 {
                continue; // Orchestrator pattern
            }

            // Only flag when BOTH are elevated
            if total_degree >= self.min_total_degree
                && fan_in >= self.min_elevated_fanin
                && fan_out >= self.min_elevated_fanout
            {
                findings.push(self.create_finding(
                    &func.name,
                    &func.file_path,
                    func.line_start,
                    func.line_end,
                    fan_in,
                    fan_out,
                    FunctionRole::Unknown,
                ));
            }
        }

        Ok(findings)
    }

    /// Whether this detector uses function context
    fn uses_context(&self) -> bool {
        true
    }

    /// Enhanced detection with function context
    fn detect_with_context(
        &self,
        graph: &GraphStore,
        contexts: &Arc<FunctionContextMap>,
    ) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();
        let funcs = graph.get_functions();

        debug!(
            "DegreeCentralityDetector: analyzing {} functions with context",
            funcs.len()
        );

        for func in funcs {
            // Skip common utility/trait method names
            if self.should_skip_by_name(&func.name) {
                continue;
            }

            let ctx = contexts.get(&func.qualified_name);

            // Skip test functions
            if let Some(c) = ctx {
                if c.is_test || c.role == FunctionRole::Test {
                    continue;
                }
            }

            // Skip hub files (even with context)
            if self.is_hub_file(&func.file_path) {
                continue;
            }

            let (fan_in, fan_out, role) = if let Some(c) = ctx {
                (c.in_degree, c.out_degree, c.role)
            } else {
                let fan_in = graph.call_fan_in(&func.qualified_name);
                let fan_out = graph.call_fan_out(&func.qualified_name);
                (fan_in, fan_out, FunctionRole::Unknown)
            };

            let total_degree = fan_in + fan_out;

            // Role-aware filtering
            match role {
                FunctionRole::Utility => {
                    // Utilities can have high fan-in, only flag if extreme
                    if fan_out < self.min_elevated_fanout || total_degree < 50 {
                        continue;
                    }
                }
                FunctionRole::Orchestrator => {
                    // Orchestrators can have high fan-out, only flag if extreme
                    if fan_in < self.min_elevated_fanin || total_degree < 50 {
                        continue;
                    }
                }
                FunctionRole::Leaf => {
                    // Leaf functions shouldn't have high coupling - flag if elevated
                    if total_degree < self.min_total_degree {
                        continue;
                    }
                }
                FunctionRole::Hub => {
                    // Hubs are expected to have high coupling, but still flag extreme cases
                    if total_degree < self.min_total_degree * 2 {
                        continue;
                    }
                }
                _ => {
                    // Default: require both elevated
                    if total_degree < self.min_total_degree
                        || fan_in < self.min_elevated_fanin
                        || fan_out < self.min_elevated_fanout
                    {
                        continue;
                    }
                }
            }

            findings.push(self.create_finding(
                &func.name,
                &func.file_path,
                func.line_start,
                func.line_end,
                fan_in,
                fan_out,
                role,
            ));
        }

        debug!(
            "DegreeCentralityDetector: found {} findings",
            findings.len()
        );
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_new_detector() {
        let detector = DegreeCentralityDetector::new();
        assert_eq!(detector.high_complexity_threshold, 15);
        assert_eq!(detector.min_elevated_fanin, 8);
    }

    #[test]
    fn test_severity_with_role() {
        let detector = DegreeCentralityDetector::new();

        // Utility with high fan-in but low fan-out = Low severity (expected behavior)
        let sev = detector.calculate_severity(50, 5, FunctionRole::Utility);
        assert_eq!(sev, Severity::Low);

        // Hub with high both = Medium (total=40, needs 60+ for High)
        let sev = detector.calculate_severity(20, 20, FunctionRole::Hub);
        assert_eq!(sev, Severity::Medium);
        
        // Hub with very high both = High
        let sev = detector.calculate_severity(35, 35, FunctionRole::Hub);
        assert_eq!(sev, Severity::High);
    }

    #[test]
    fn test_skip_by_name() {
        let detector = DegreeCentralityDetector::new();

        assert!(detector.should_skip_by_name("is_valid"));
        assert!(detector.should_skip_by_name("new"));
        assert!(detector.should_skip_by_name("get"));
        assert!(!detector.should_skip_by_name("process_orders"));
    }
}
