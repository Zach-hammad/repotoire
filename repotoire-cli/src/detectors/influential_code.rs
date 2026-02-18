//! Influential code detector using PageRank
//!
//! Uses PageRank to identify truly important code components based on
//! incoming dependencies. Now enhanced with function context for smarter detection.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::function_context::{FunctionContextMap, FunctionRole};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use rayon::prelude::*;
use std::sync::Arc;
use tracing::debug;

/// Detects influential code and potential bloated code using PageRank.
///
/// PageRank measures the importance of a function/class based on how many
/// other components depend on it (and how important those dependents are).
///
/// Now uses FunctionContext to make smarter decisions:
/// - Utilities: High influence is expected, only flag if also complex
/// - Hubs: Genuinely important, flag with appropriate severity
/// - Test functions: Skipped entirely
pub struct InfluentialCodeDetector {
    config: DetectorConfig,
    /// Complexity threshold for flagging as complex
    high_complexity_threshold: u32,
    /// Lines of code threshold for being "large"
    high_loc_threshold: u32,
    /// Minimum fan-in to consider influential
    min_fan_in: usize,
}

impl InfluentialCodeDetector {
    /// Create a new detector with default config
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            high_complexity_threshold: 15,
            high_loc_threshold: 100,
            min_fan_in: 8,
        }
    }

    /// Create with custom config
    #[allow(dead_code)] // Builder pattern method
    pub fn with_config(config: DetectorConfig) -> Self {
        let min_fan_in = config.get_option_or("min_fan_in", 8);
        Self {
            high_complexity_threshold: config.get_option_or("high_complexity_threshold", 15),
            high_loc_threshold: config.get_option_or("high_loc_threshold", 100),
            min_fan_in,
            config,
        }
    }

    /// Calculate severity based on metrics and function role
    fn calculate_severity(
        &self,
        fan_in: usize,
        complexity: usize,
        loc: usize,
        role: FunctionRole,
    ) -> Severity {
        // Base severity from raw metrics
        // HIGH requires both significant fan-in AND high complexity
        let high_fan_in = self.min_fan_in.max(15);
        let base_severity = if fan_in >= high_fan_in && complexity >= 20 {
            Severity::High
        } else if fan_in >= self.min_fan_in
            && (complexity >= self.high_complexity_threshold as usize
                || loc >= self.high_loc_threshold as usize)
        {
            Severity::Medium
        } else {
            Severity::Low
        };

        // Adjust based on function role
        match role {
            FunctionRole::Utility => {
                // Utilities are expected to be influential
                // Only flag if complexity is problematic
                if complexity < self.high_complexity_threshold as usize * 2 {
                    Severity::Low
                } else {
                    base_severity.min(Severity::Medium)
                }
            }
            FunctionRole::Hub => {
                // Hubs are genuinely important - keep severity
                base_severity
            }
            FunctionRole::EntryPoint => {
                // Entry points are expected to be influential
                base_severity.min(Severity::Medium)
            }
            FunctionRole::Test => Severity::Low,
            _ => base_severity,
        }
    }

    /// Legacy name-based skip check (fallback when no context available)
    fn should_skip_by_name(&self, name: &str) -> bool {
        const SKIP_NAMES: &[&str] = &[
            "new",
            "default",
            "from",
            "into",
            "create",
            "build",
            "make",
            "with",
            "get",
            "set",
            "run",
            "main",
            "init",
            "setup",
            "start",
            "execute",
            "handle",
            "process",
            "parse",
            "format",
            "render",
            "display",
            "detect",
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

    /// Create a finding
    fn create_finding(
        &self,
        name: &str,
        file_path: &str,
        line_start: u32,
        line_end: u32,
        fan_in: usize,
        complexity: usize,
        loc: usize,
        role: FunctionRole,
        betweenness: Option<f64>,
    ) -> Finding {
        let severity = self.calculate_severity(fan_in, complexity, loc, role);

        let role_note = match role {
            FunctionRole::Utility => " (utility - high influence expected)",
            FunctionRole::Hub => " (architectural hub)",
            FunctionRole::EntryPoint => " (entry point)",
            _ => "",
        };

        let title = format!("Influential Code: {}{}", name, role_note);

        let mut description = format!(
            "Function '{}' influences {} dependents with complexity {} and {} LOC. \
            High-impact code.\n\n\
            **Metrics:**\n\
            - Callers (fan-in): {}\n\
            - Complexity: {}\n\
            - Lines of code: {}",
            name, fan_in, complexity, loc, fan_in, complexity, loc
        );

        if let Some(b) = betweenness {
            description.push_str(&format!("\n- Betweenness centrality: {:.4}", b));
        }

        let suggested_fix = match role {
            FunctionRole::Utility => "This utility is influential but complex. Consider:\n\
                - Breaking into smaller, focused helpers\n\
                - Adding comprehensive tests"
                .to_string(),
            FunctionRole::Hub => "This is a critical hub. Consider:\n\
                - Ensuring comprehensive test coverage\n\
                - Adding monitoring and observability\n\
                - Documenting thoroughly"
                .to_string(),
            _ => {
                "Consider refactoring to reduce complexity while maintaining interface".to_string()
            }
        };

        Finding {
            id: String::new(),
            detector: "InfluentialCodeDetector".to_string(),
            severity,
            title,
            description,
            affected_files: vec![file_path.into()],
            line_start: Some(line_start),
            line_end: Some(line_end),
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some("Large (4+ hours)".to_string()),
            category: Some("architecture".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Changes to influential code have wide-reaching effects across the codebase."
                    .to_string(),
            ),
            ..Default::default()
        }
    }
}

impl Default for InfluentialCodeDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for InfluentialCodeDetector {
    fn name(&self) -> &'static str {
        "InfluentialCodeDetector"
    }

    fn description(&self) -> &'static str {
        "Detects influential code using PageRank analysis and function context"
    }

    fn category(&self) -> &'static str {
        "architecture"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    /// Legacy detection without context
    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();

        for func in graph.get_functions() {
            // Skip by name pattern
            if self.should_skip_by_name(&func.name) {
                continue;
            }

            let fan_in = graph.call_fan_in(&func.qualified_name);
            let complexity = func.complexity().unwrap_or(1) as usize;
            let loc = func.loc() as usize;

            // Influential: high fan-in and large
            if fan_in >= self.min_fan_in
                && (complexity >= self.high_complexity_threshold as usize
                    || loc >= self.high_loc_threshold as usize)
            {
                findings.push(self.create_finding(
                    &func.name,
                    &func.file_path,
                    func.line_start,
                    func.line_end,
                    fan_in,
                    complexity,
                    loc,
                    FunctionRole::Unknown,
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
            "InfluentialCodeDetector: analyzing {} functions with context",
            funcs.len()
        );

        for func in funcs {
            let ctx = contexts.get(&func.qualified_name);

            // Skip test functions
            if let Some(c) = ctx {
                if c.is_test || c.role == FunctionRole::Test {
                    continue;
                }
            }

            let (fan_in, complexity, loc, role, betweenness) = if let Some(c) = ctx {
                (
                    c.in_degree,
                    c.complexity.unwrap_or(1) as usize,
                    c.loc as usize,
                    c.role,
                    Some(c.betweenness),
                )
            } else {
                let fan_in = graph.call_fan_in(&func.qualified_name);
                let complexity = func.complexity().unwrap_or(1) as usize;
                let loc = func.loc() as usize;
                (fan_in, complexity, loc, FunctionRole::Unknown, None)
            };

            // Role-aware filtering
            let should_flag = match role {
                FunctionRole::Utility => {
                    // Utilities can have high fan-in
                    // Only flag if complexity is extreme
                    fan_in >= self.min_fan_in * 2
                        && complexity >= self.high_complexity_threshold as usize * 2
                }
                FunctionRole::Hub => {
                    // Hubs are important - flag with normal threshold
                    fan_in >= self.min_fan_in
                        && (complexity >= self.high_complexity_threshold as usize
                            || loc >= self.high_loc_threshold as usize)
                }
                FunctionRole::EntryPoint => {
                    // Entry points are expected to be influential
                    // Only flag if very complex
                    fan_in >= self.min_fan_in
                        && complexity >= self.high_complexity_threshold as usize * 2
                }
                FunctionRole::Test => false,
                FunctionRole::Leaf | FunctionRole::Orchestrator | FunctionRole::Unknown => {
                    // Default threshold
                    fan_in >= self.min_fan_in
                        && (complexity >= self.high_complexity_threshold as usize
                            || loc >= self.high_loc_threshold as usize)
                }
            };

            if should_flag {
                findings.push(self.create_finding(
                    &func.name,
                    &func.file_path,
                    func.line_start,
                    func.line_end,
                    fan_in,
                    complexity,
                    loc,
                    role,
                    betweenness,
                ));
            }
        }

        debug!("InfluentialCodeDetector: found {} findings", findings.len());
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_severity_with_role() {
        let detector = InfluentialCodeDetector::new();

        // Utility with moderate complexity = Low (expected behavior)
        let sev = detector.calculate_severity(20, 10, 50, FunctionRole::Utility);
        assert_eq!(sev, Severity::Low);

        // Utility with extreme complexity = Medium (capped)
        let sev = detector.calculate_severity(20, 40, 200, FunctionRole::Utility);
        assert_eq!(sev, Severity::Medium);

        // Hub with high metrics = High
        let sev = detector.calculate_severity(20, 25, 100, FunctionRole::Hub);
        assert_eq!(sev, Severity::High);
    }

    #[test]
    fn test_skip_by_name() {
        let detector = InfluentialCodeDetector::new();

        // These should be skipped
        assert!(detector.should_skip_by_name("is_valid"));
        assert!(detector.should_skip_by_name("new"));
        assert!(detector.should_skip_by_name("check_pattern"));
        assert!(detector.should_skip_by_name("process_orders")); // "process" prefix

        // These should NOT be skipped
        assert!(!detector.should_skip_by_name("order_processor")); // not a prefix
        assert!(!detector.should_skip_by_name("transform_data"));
    }
}
