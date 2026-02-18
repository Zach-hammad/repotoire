//! Architectural bottleneck detector using betweenness centrality
//!
//! Identifies functions that sit on many execution paths (high betweenness),
//! indicating architectural bottlenecks that are critical points of failure.
//!
//! Now enhanced with function context for smarter role-based detection.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::function_context::{FunctionContextMap, FunctionRole};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::sync::Arc;
use tracing::debug;

/// Detects architectural bottlenecks using graph metrics and function context.
///
/// Functions with high betweenness centrality appear on many shortest paths
/// between other functions, making them critical architectural components.
/// Changes to these functions have high blast radius.
///
/// Now uses FunctionContext to make smarter decisions:
/// - Utility functions are expected to have high fan-in → reduced severity
/// - Hub functions are flagged with higher priority
/// - Test functions are skipped entirely
pub struct ArchitecturalBottleneckDetector {
    config: DetectorConfig,
    /// Complexity threshold for severity escalation
    high_complexity_threshold: u32,
    /// Minimum fan-in to consider a bottleneck
    min_fan_in: usize,
    /// Minimum complexity to consider a bottleneck
    min_complexity: usize,
}

impl ArchitecturalBottleneckDetector {
    /// Create a new detector with default config
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            high_complexity_threshold: 20,
            min_fan_in: 15,
            min_complexity: 15,
        }
    }

    /// Create with custom config
    #[allow(dead_code)] // Builder pattern method
    pub fn with_config(config: DetectorConfig) -> Self {
        use crate::calibrate::MetricKind;
        let high_complexity_threshold = config.get_option_or("high_complexity_threshold",
            config.adaptive.warn_usize(MetricKind::Complexity, 20) as u32) as u32;
        let min_fan_in = config.get_option_or("min_fan_in",
            config.adaptive.warn_usize(MetricKind::FanIn, 15));
        let min_complexity = config.get_option_or("min_complexity",
            config.adaptive.warn_usize(MetricKind::Complexity, 15));
        Self {
            config,
            high_complexity_threshold,
            min_fan_in,
            min_complexity,
        }
    }

    /// Calculate severity based on metrics and function role
    fn calculate_severity(&self, fan_in: usize, complexity: usize, role: FunctionRole) -> Severity {
        // Base severity from raw metrics
        let base_severity = if fan_in >= 30 && complexity >= 25 {
            Severity::Critical
        } else if fan_in >= 20 && complexity >= 20 {
            Severity::High
        } else {
            Severity::Medium
        };

        // Adjust based on function role
        match role {
            FunctionRole::Utility => {
                // Utilities are expected to be called a lot - cap at Medium
                base_severity.min(Severity::Medium)
            }
            FunctionRole::Leaf => {
                // Leaf functions are low impact - cap at Medium
                base_severity.min(Severity::Medium)
            }
            FunctionRole::Test => {
                // Should have been filtered, but just in case
                Severity::Low
            }
            FunctionRole::Hub => {
                // Hubs are genuinely critical - keep severity
                base_severity
            }
            FunctionRole::Orchestrator => {
                // Orchestrators coordinate work - keep severity
                base_severity
            }
            FunctionRole::EntryPoint => {
                // Entry points are important - keep severity
                base_severity
            }
            FunctionRole::Unknown => {
                // Unknown - use base metrics
                base_severity
            }
        }
    }

    /// Legacy name-based skip check (fallback when no context available)
    fn should_skip_by_name(&self, name: &str) -> bool {
        const SKIP_NAMES: &[&str] = &[
            "run",
            "new",
            "default",
            "create",
            "build",
            "init",
            "setup",
            "get",
            "set",
            "parse",
            "format",
            "render",
            "display",
            "detect",
            "analyze",
            "execute",
            "process",
            "handle",
            "dispatch",
            "is_",
            "has_",
            "check_",
            "validate_",
            "should_",
            "can_",
            "find_",
            "calculate_",
            "compute_",
            "scan_",
            "extract_",
            "normalize_",
        ];

        let name_lower = name.to_lowercase();
        SKIP_NAMES.iter().any(|&skip| {
            name_lower == skip
                || name_lower.starts_with(&format!("{}_", skip))
                || name_lower.starts_with(skip)
        })
    }

    /// Create a finding from a bottleneck
    fn create_finding(
        &self,
        name: &str,
        file_path: &str,
        line_start: u32,
        line_end: u32,
        fan_in: usize,
        complexity: usize,
        role: FunctionRole,
        betweenness: Option<f64>,
    ) -> Finding {
        let severity = self.calculate_severity(fan_in, complexity, role);

        let role_note = match role {
            FunctionRole::Utility => " (utility function - expected high fan-in)",
            FunctionRole::Hub => " (architectural hub)",
            FunctionRole::Orchestrator => " (orchestrator)",
            _ => "",
        };

        let title = format!("Architectural Bottleneck: {}{}", name, role_note);

        let mut description = format!(
            "Function '{}' is called by {} functions and has complexity {}. \
            Changes here are high-risk.",
            name, fan_in, complexity
        );

        if let Some(b) = betweenness {
            description.push_str(&format!(
                "\n\nBetweenness centrality: {:.4} - this function sits on many \
                execution paths between other functions.",
                b
            ));
        }

        if matches!(role, FunctionRole::Utility) {
            description.push_str(
                "\n\n**Note:** This function was identified as a utility. High fan-in \
                is expected, but the high complexity may still warrant attention.",
            );
        }

        let suggested_fix = match role {
            FunctionRole::Utility => {
                "Consider splitting this utility into smaller, focused helpers. \
                High complexity in shared utilities is risky."
                    .to_string()
            }
            FunctionRole::Hub => "This is a critical hub. Ensure comprehensive test coverage, \
                add defensive error handling, and consider circuit breaker pattern."
                .to_string(),
            _ => "Reduce complexity or create a facade to isolate changes. \
                Add comprehensive tests before refactoring."
                .to_string(),
        };

        Finding {
            id: String::new(),
            detector: "ArchitecturalBottleneckDetector".to_string(),
            severity,
            title,
            description,
            affected_files: vec![file_path.into()],
            line_start: Some(line_start),
            line_end: Some(line_end),
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some("Large (4-8 hours)".to_string()),
            category: Some("architecture".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Bottlenecks are single points of failure that amplify bugs. \
                High fan-in with high complexity means many callers depend on risky code."
                    .to_string(),
            ),
            ..Default::default()
        }
    }
}

impl Default for ArchitecturalBottleneckDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for ArchitecturalBottleneckDetector {
    fn name(&self) -> &'static str {
        "ArchitecturalBottleneckDetector"
    }

    fn description(&self) -> &'static str {
        "Detects architectural bottlenecks using betweenness centrality and function context"
    }

    fn category(&self) -> &'static str {
        "architecture"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    /// Legacy detection without context (fallback)
    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();

        for func in graph.get_functions() {
            // Skip by name pattern
            if self.should_skip_by_name(&func.name) {
                continue;
            }

            // Skip test functions
            if func.file_path.contains("/tests/") || func.name.starts_with("test_") {
                continue;
            }

            let fan_in = graph.call_fan_in(&func.qualified_name);
            let complexity = func.complexity().unwrap_or(1) as usize;

            // Bottleneck: high fan-in AND high complexity
            if fan_in >= self.min_fan_in && complexity >= self.min_complexity {
                findings.push(self.create_finding(
                    &func.name,
                    &func.file_path,
                    func.line_start,
                    func.line_end,
                    fan_in,
                    complexity,
                    FunctionRole::Unknown, // No context available
                    None,
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
        graph: &dyn crate::graph::GraphQuery,
        contexts: &Arc<FunctionContextMap>,
    ) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();
        let funcs = graph.get_functions();

        debug!(
            "ArchitecturalBottleneckDetector: analyzing {} functions with context",
            funcs.len()
        );

        for func in funcs {
            // Skip by name pattern (CLI entry points, common utilities)
            if self.should_skip_by_name(&func.name) {
                continue;
            }

            // Skip CLI entry points (expected to coordinate many things)
            if func.file_path.contains("/cli/")
                && (func.name == "run" || func.name == "execute" || func.name == "main")
            {
                continue;
            }

            // Get context for this function
            let ctx = contexts.get(&func.qualified_name);

            // Skip test functions (from context or path)
            if let Some(c) = ctx {
                if c.is_test || c.role == FunctionRole::Test {
                    continue;
                }
            } else if func.file_path.contains("/tests/") || func.name.starts_with("test_") {
                continue;
            }

            let (fan_in, complexity, role, betweenness) = if let Some(c) = ctx {
                (
                    c.in_degree,
                    c.complexity.unwrap_or(1) as usize,
                    c.role,
                    Some(c.betweenness),
                )
            } else {
                // Fall back to graph queries
                let fan_in = graph.call_fan_in(&func.qualified_name);
                let complexity = func.complexity().unwrap_or(1) as usize;
                (fan_in, complexity, FunctionRole::Unknown, None)
            };

            // Bottleneck: high fan-in AND high complexity
            // For utilities, we still flag them but with reduced severity
            if fan_in >= self.min_fan_in && complexity >= self.min_complexity {
                // Skip pure utilities with only moderately high metrics
                // (they're expected to be called a lot)
                if role == FunctionRole::Utility && fan_in < 30 && complexity < 20 {
                    debug!(
                        "Skipping utility {} (fan_in={}, complexity={})",
                        func.name, fan_in, complexity
                    );
                    continue;
                }

                findings.push(self.create_finding(
                    &func.name,
                    &func.file_path,
                    func.line_start,
                    func.line_end,
                    fan_in,
                    complexity,
                    role,
                    betweenness,
                ));
            }
        }

        debug!(
            "ArchitecturalBottleneckDetector: found {} findings",
            findings.len()
        );
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_severity_calculation() {
        let detector = ArchitecturalBottleneckDetector::new();

        // High metrics + Hub = Critical
        assert_eq!(
            detector.calculate_severity(35, 30, FunctionRole::Hub),
            Severity::Critical
        );

        // High metrics + Utility = Medium (capped)
        assert_eq!(
            detector.calculate_severity(35, 30, FunctionRole::Utility),
            Severity::Medium
        );

        // Moderate metrics + Unknown = Medium
        assert_eq!(
            detector.calculate_severity(18, 18, FunctionRole::Unknown),
            Severity::Medium
        );
    }

    #[test]
    fn test_skip_by_name() {
        let detector = ArchitecturalBottleneckDetector::new();

        // These should be skipped (match patterns)
        assert!(detector.should_skip_by_name("is_valid"));
        assert!(detector.should_skip_by_name("is_sql_context"));
        assert!(detector.should_skip_by_name("check_pattern"));
        assert!(detector.should_skip_by_name("find_dead_code"));
        assert!(detector.should_skip_by_name("process")); // exact match
        assert!(detector.should_skip_by_name("process_orders")); // starts with "process"
        assert!(detector.should_skip_by_name("calculate_totals")); // starts with "calculate_"

        // These should NOT be skipped (don't match patterns)
        assert!(!detector.should_skip_by_name("order_processor")); // "process" in middle, not prefix
        assert!(!detector.should_skip_by_name("my_function"));
        assert!(!detector.should_skip_by_name("transform_data")); // doesn't match any pattern
    }

    #[test]
    fn test_role_based_severity_cap() {
        let detector = ArchitecturalBottleneckDetector::new();

        // Utility role should cap severity at Medium
        let utility_severity = detector.calculate_severity(50, 50, FunctionRole::Utility);
        assert!(utility_severity <= Severity::Medium);

        // Hub role should not cap severity
        let hub_severity = detector.calculate_severity(50, 50, FunctionRole::Hub);
        assert_eq!(hub_severity, Severity::Critical);
    }
}
