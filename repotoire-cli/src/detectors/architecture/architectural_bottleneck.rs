//! Architectural bottleneck detector using betweenness centrality
//!
//! Identifies functions that sit on many execution paths (high betweenness),
//! indicating architectural bottlenecks that are critical points of failure.
//!
//! Now enhanced with function context for smarter role-based detection.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::function_context::FunctionRole;
use crate::models::{Finding, Severity};
use anyhow::Result;
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
    #[allow(dead_code)] // Config field
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
    pub fn with_config(config: DetectorConfig) -> Self {
        use crate::calibrate::MetricKind;
        let high_complexity_threshold = config.get_option_or(
            "high_complexity_threshold",
            config.adaptive.warn_usize(MetricKind::Complexity, 20) as u32,
        );
        let min_fan_in = config.get_option_or(
            "min_fan_in",
            config.adaptive.warn_usize(MetricKind::FanIn, 15),
        );
        let min_complexity = config.get_option_or(
            "min_complexity",
            config.adaptive.warn_usize(MetricKind::Complexity, 15),
        );
        Self {
            config,
            high_complexity_threshold,
            min_fan_in,
            min_complexity,
        }
    }

    /// Calculate severity based on metrics and function role.
    ///
    /// Architectural bottlenecks are structural observations, not code quality
    /// issues — all severities are capped at Low.
    fn calculate_severity(
        &self,
        _fan_in: usize,
        _complexity: usize,
        _role: FunctionRole,
    ) -> Severity {
        Severity::Low
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
            "find",
            "find_",
            "map",
            "filter",
            "collect",
            "contains",
            "push",
            "insert",
            "remove",
            "unwrap",
            "expect",
            "read",
            "write",
            "lock",
            "send",
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

        let explanation = self.config.adaptive.explain(
            crate::calibrate::MetricKind::Complexity,
            complexity as f64,
            20.0, // default high_complexity_threshold
        );
        let threshold_metadata = explanation.to_metadata().into_iter().collect();
        let description = format!("{}\n\n📊 {}", description, explanation.to_note());

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
            threshold_metadata,
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

    fn detect(
        &self,
        ctx: &crate::detectors::analysis_context::AnalysisContext,
    ) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let contexts = &ctx.functions;
        let i = graph.interner();
        let mut findings = Vec::new();
        let func_idxs = graph.functions_idx();

        debug!(
            "ArchitecturalBottleneckDetector: analyzing {} functions with context",
            func_idxs.len()
        );

        for &func_idx in func_idxs {
            let Some(func) = graph.node_idx(func_idx) else {
                continue;
            };
            // Skip by name pattern (CLI entry points, common utilities)
            if self.should_skip_by_name(func.node_name(i)) {
                continue;
            }

            // Skip CLI entry points (expected to coordinate many things)
            if func.path(i).contains("/cli/")
                && (func.node_name(i) == "run"
                    || func.node_name(i) == "execute"
                    || func.node_name(i) == "main")
            {
                continue;
            }

            // Get context for this function
            let fctx = contexts.get(func.qn(i));

            // Skip test functions (from context or path)
            if let Some(c) = fctx {
                if c.is_test || c.role == FunctionRole::Test {
                    continue;
                }
            } else if func.path(i).contains("/tests/") || func.node_name(i).starts_with("test_") {
                continue;
            }

            let (fan_in, complexity, role, betweenness) = if let Some(c) = fctx {
                (
                    c.in_degree,
                    c.complexity.unwrap_or(1) as usize,
                    c.role,
                    Some(c.betweenness),
                )
            } else {
                // Fall back to graph queries + pre-computed betweenness
                let fan_in = graph.call_fan_in_idx(func_idx);
                let complexity = func.complexity_opt().unwrap_or(1) as usize;
                let pg_idx: petgraph::stable_graph::NodeIndex = func_idx.into();
                let raw_b = graph
                    .primitives()
                    .betweenness
                    .get(&pg_idx)
                    .copied()
                    .unwrap_or(0.0);
                let betweenness = if raw_b > 0.0 { Some(raw_b) } else { None };
                (fan_in, complexity, FunctionRole::Unknown, betweenness)
            };

            // Bottleneck: high fan-in AND high complexity
            // For utilities, we still flag them but with reduced severity
            if fan_in >= self.min_fan_in && complexity >= self.min_complexity {
                // Skip pure utilities with only moderately high metrics
                // (they're expected to be called a lot)
                if role == FunctionRole::Utility && fan_in < 30 && complexity < 20 {
                    debug!(
                        "Skipping utility {} (fan_in={}, complexity={})",
                        func.node_name(i),
                        fan_in,
                        complexity
                    );
                    continue;
                }

                findings.push(self.create_finding(
                    func.node_name(i),
                    func.path(i),
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

impl crate::detectors::RegisteredDetector for ArchitecturalBottleneckDetector {
    fn create(init: &crate::detectors::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::with_config(
            init.config_for("ArchitecturalBottleneckDetector"),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_severity_calculation() {
        let detector = ArchitecturalBottleneckDetector::new();

        // All architectural bottleneck severities are capped at Low
        assert_eq!(
            detector.calculate_severity(35, 30, FunctionRole::Hub),
            Severity::Low
        );

        assert_eq!(
            detector.calculate_severity(35, 30, FunctionRole::Utility),
            Severity::Low
        );

        assert_eq!(
            detector.calculate_severity(18, 18, FunctionRole::Unknown),
            Severity::Low
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

        // All roles capped at Low (informational detector)
        let utility_severity = detector.calculate_severity(50, 50, FunctionRole::Utility);
        assert_eq!(utility_severity, Severity::Low);

        let hub_severity = detector.calculate_severity(50, 50, FunctionRole::Hub);
        assert_eq!(hub_severity, Severity::Low);
    }
}
