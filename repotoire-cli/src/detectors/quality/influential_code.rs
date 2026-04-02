//! Influential code detector using PageRank
//!
//! Uses pre-computed PageRank from graph primitives to identify truly important
//! code components based on transitive dependency weight. Enhanced with function
//! context for smarter role-aware detection.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::function_context::FunctionRole;
use crate::models::{Finding, Severity};
use anyhow::Result;
use tracing::debug;

/// Detects influential code using PageRank from graph primitives.
///
/// PageRank measures the importance of a function based on how many
/// other components depend on it (and how important those dependents are).
/// Unlike simple fan-in, PageRank accounts for transitive influence.
///
/// Uses FunctionContext to make smarter decisions:
/// - Utilities: High influence is expected, only flag if also complex
/// - Hubs: Genuinely important, flag with appropriate severity
/// - Test functions: Skipped entirely
pub struct InfluentialCodeDetector {
    config: DetectorConfig,
    /// Complexity threshold for flagging as complex
    high_complexity_threshold: u32,
    /// Lines of code threshold for being "large"
    high_loc_threshold: u32,
    /// PageRank percentile threshold (0.0-1.0) for flagging as influential.
    /// Functions above this percentile are considered influential.
    pagerank_percentile_threshold: f64,
}

impl InfluentialCodeDetector {
    /// Create a new detector with default config
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            high_complexity_threshold: 15,
            high_loc_threshold: 100,
            pagerank_percentile_threshold: 0.90,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        let pagerank_percentile_threshold: f64 =
            config.get_option_or("pagerank_percentile_threshold", 90) as f64 / 100.0;
        Self {
            high_complexity_threshold: config.get_option_or("high_complexity_threshold", 15),
            high_loc_threshold: config.get_option_or("high_loc_threshold", 100),
            pagerank_percentile_threshold,
            config,
        }
    }

    /// Calculate severity based on metrics and function role.
    ///
    /// `pagerank_pct` is the node's percentile position (0.0..1.0).
    fn calculate_severity(
        &self,
        pagerank_pct: f64,
        complexity: usize,
        loc: usize,
        role: FunctionRole,
    ) -> Severity {
        // Base severity from PageRank percentile + complexity
        let base_severity = if pagerank_pct >= 0.99 && complexity >= 20 {
            Severity::High
        } else if pagerank_pct >= 0.95
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

    /// Create a finding
    fn create_finding(
        &self,
        name: &str,
        file_path: &str,
        line_start: u32,
        line_end: u32,
        page_rank: f64,
        pagerank_pct: f64,
        fan_in: usize,
        complexity: usize,
        loc: usize,
        role: FunctionRole,
        betweenness: Option<f64>,
    ) -> Finding {
        let severity = self.calculate_severity(pagerank_pct, complexity, loc, role);

        let role_note = match role {
            FunctionRole::Utility => " (utility - high influence expected)",
            FunctionRole::Hub => " (architectural hub)",
            FunctionRole::EntryPoint => " (entry point)",
            _ => "",
        };

        let title = format!("Influential Code: {}{}", name, role_note);

        let mut description = format!(
            "Function '{}' has high transitive influence (PageRank p{:.0}) with \
            complexity {} and {} LOC. High-impact code.\n\n\
            **Metrics:**\n\
            - PageRank: {:.6} (p{:.0})\n\
            - Callers (fan-in): {}\n\
            - Complexity: {}\n\
            - Lines of code: {}",
            name,
            pagerank_pct * 100.0,
            complexity,
            loc,
            page_rank,
            pagerank_pct * 100.0,
            fan_in,
            complexity,
            loc
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
            "InfluentialCodeDetector: analyzing {} functions with PageRank",
            func_idxs.len()
        );

        // Collect PageRank values for percentile computation
        let mut pagerank_values: Vec<f64> = func_idxs
            .iter()
            .map(|&idx| {
                let pg_idx: petgraph::stable_graph::NodeIndex = idx.into();
                graph
                    .primitives()
                    .page_rank
                    .get(&pg_idx)
                    .copied()
                    .unwrap_or(0.0)
            })
            .collect();
        pagerank_values
            .sort_unstable_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

        let total = pagerank_values.len();
        if total == 0 {
            return Ok(findings);
        }

        // Helper: compute percentile rank for a given PageRank value
        let percentile_of = |pr: f64| -> f64 {
            if total <= 1 {
                return 1.0;
            }
            let rank = pagerank_values.partition_point(|&v| v < pr);
            rank as f64 / total as f64
        };

        for &func_idx in func_idxs {
            let Some(func) = graph.node_idx(func_idx) else {
                continue;
            };

            let fctx = contexts.get(func.qn(i));

            // Skip test functions
            if let Some(c) = fctx {
                if c.is_test || c.role == FunctionRole::Test {
                    continue;
                }
            }

            let pg_func: petgraph::stable_graph::NodeIndex = func_idx.into();
            let page_rank = graph
                .primitives()
                .page_rank
                .get(&pg_func)
                .copied()
                .unwrap_or(0.0);
            let pagerank_pct = percentile_of(page_rank);

            // Skip functions below the PageRank percentile threshold
            if pagerank_pct < self.pagerank_percentile_threshold {
                continue;
            }

            let (fan_in, complexity, loc, role, betweenness) = if let Some(c) = fctx {
                (
                    c.in_degree,
                    c.complexity.unwrap_or(1) as usize,
                    c.loc as usize,
                    c.role,
                    Some(c.betweenness),
                )
            } else {
                let fan_in = graph.call_fan_in_idx(func_idx);
                let complexity = func.complexity_opt().unwrap_or(1) as usize;
                let loc = func.loc() as usize;
                let pg_fidx: petgraph::stable_graph::NodeIndex = func_idx.into();
                let raw_b = graph
                    .primitives()
                    .betweenness
                    .get(&pg_fidx)
                    .copied()
                    .unwrap_or(0.0);
                let betweenness = if raw_b > 0.0 { Some(raw_b) } else { None };
                (fan_in, complexity, loc, FunctionRole::Unknown, betweenness)
            };

            // Role-aware filtering: still require complexity or size to flag
            let should_flag = match role {
                FunctionRole::Utility => {
                    // Utilities can have high PageRank. Only flag if complexity is extreme.
                    complexity >= self.high_complexity_threshold as usize * 2
                }
                FunctionRole::Hub => {
                    // Hubs are important - flag if complex or large
                    complexity >= self.high_complexity_threshold as usize
                        || loc >= self.high_loc_threshold as usize
                }
                FunctionRole::EntryPoint => {
                    // Entry points are expected to be influential. Only flag if very complex.
                    complexity >= self.high_complexity_threshold as usize * 2
                }
                FunctionRole::Test => false,
                FunctionRole::Leaf | FunctionRole::Orchestrator | FunctionRole::Unknown => {
                    // Default: require complexity or size
                    complexity >= self.high_complexity_threshold as usize
                        || loc >= self.high_loc_threshold as usize
                }
            };

            if should_flag {
                findings.push(self.create_finding(
                    func.node_name(i),
                    func.path(i),
                    func.line_start,
                    func.line_end,
                    page_rank,
                    pagerank_pct,
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

impl crate::detectors::RegisteredDetector for InfluentialCodeDetector {
    fn create(init: &crate::detectors::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::with_config(
            init.config_for("InfluentialCodeDetector"),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_severity_with_role() {
        let detector = InfluentialCodeDetector::new();

        // Utility with moderate complexity = Low (expected behavior)
        let sev = detector.calculate_severity(0.99, 10, 50, FunctionRole::Utility);
        assert_eq!(sev, Severity::Low);

        // Utility with extreme complexity = Medium (capped)
        let sev = detector.calculate_severity(0.99, 40, 200, FunctionRole::Utility);
        assert_eq!(sev, Severity::Medium);

        // Hub with high PageRank + high complexity = High
        let sev = detector.calculate_severity(0.99, 25, 100, FunctionRole::Hub);
        assert_eq!(sev, Severity::High);

        // Low PageRank percentile = Low severity regardless
        let sev = detector.calculate_severity(0.50, 25, 100, FunctionRole::Hub);
        assert_eq!(sev, Severity::Low);
    }

    #[test]
    fn test_severity_thresholds() {
        let detector = InfluentialCodeDetector::new();

        // p99 + complexity >= 20 => High
        assert_eq!(
            detector.calculate_severity(0.99, 20, 50, FunctionRole::Unknown),
            Severity::High
        );

        // p95 + complexity >= 15 => Medium
        assert_eq!(
            detector.calculate_severity(0.96, 15, 50, FunctionRole::Unknown),
            Severity::Medium
        );

        // p95 + low complexity => Low
        assert_eq!(
            detector.calculate_severity(0.96, 5, 50, FunctionRole::Unknown),
            Severity::Low
        );
    }

    #[test]
    fn test_default_percentile_threshold() {
        let detector = InfluentialCodeDetector::new();
        assert!((detector.pagerank_percentile_threshold - 0.90).abs() < f64::EPSILON);
    }
}
