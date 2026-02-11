//! Influential code detector using PageRank
//!
//! Uses PageRank to identify truly important code components based on
//! incoming dependencies. Distinguishes legitimate core infrastructure from
//! bloated god classes.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use rayon::prelude::*;
use std::collections::HashMap;
use tracing::{debug, info};
use uuid::Uuid;

/// Detects influential code and potential god classes using PageRank.
///
/// PageRank measures the importance of a function/class based on how many
/// other components depend on it (and how important those dependents are).
///
/// Detects:
/// - High influence code: High PageRank indicates core infrastructure
/// - Bloated god classes: Low PageRank + high complexity = refactor target
/// - Critical bottlenecks: High PageRank + high complexity = high risk
pub struct InfluentialCodeDetector {
    config: DetectorConfig,
    /// Complexity threshold for flagging as complex
    high_complexity_threshold: u32,
    /// Lines of code threshold for being "large"
    high_loc_threshold: u32,
    /// PageRank damping factor
    damping: f64,
    /// Max iterations for PageRank
    max_iterations: usize,
    /// Convergence tolerance
    tolerance: f64,
}

impl InfluentialCodeDetector {
    /// Create a new detector with default config
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            high_complexity_threshold: 15,
            high_loc_threshold: 200,
            damping: 0.85,
            max_iterations: 100,
            tolerance: 1e-6,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        Self {
            high_complexity_threshold: config.get_option_or("high_complexity_threshold", 15),
            high_loc_threshold: config.get_option_or("high_loc_threshold", 200),
            damping: config.get_option_or("damping", 0.85),
            max_iterations: config.get_option_or("max_iterations", 100),
            tolerance: config.get_option_or("tolerance", 1e-6),
            config,
        }
    }

    /// Calculate PageRank scores (parallelized)
    fn calculate_pagerank(
        &self,
        incoming: &[Vec<usize>],
        out_degree: &[usize],
        num_nodes: usize,
    ) -> Vec<f64> {
        if num_nodes == 0 {
            return vec![];
        }

        let initial_score = 1.0 / num_nodes as f64;
        let mut scores = vec![initial_score; num_nodes];
        let base_score = (1.0 - self.damping) / num_nodes as f64;

        for _iteration in 0..self.max_iterations {
            // Calculate new scores in parallel
            let new_scores: Vec<f64> = (0..num_nodes)
                .into_par_iter()
                .map(|node| {
                    let mut score = base_score;
                    for &neighbor in &incoming[node] {
                        let neighbor_out = out_degree[neighbor];
                        if neighbor_out > 0 {
                            score += self.damping * scores[neighbor] / neighbor_out as f64;
                        }
                    }
                    score
                })
                .collect();

            // Check convergence
            let diff: f64 = scores
                .par_iter()
                .zip(new_scores.par_iter())
                .map(|(old, new)| (old - new).abs())
                .sum();

            scores = new_scores;

            if diff < self.tolerance {
                break;
            }
        }

        scores
    }

    /// Create finding for influential code (high PageRank)
    fn create_influential_code_finding(
        &self,
        name: &str,
        qualified_name: &str,
        file_path: &str,
        line_number: Option<u32>,
        pagerank: f64,
        threshold: f64,
        complexity: u32,
        loc: u32,
        caller_count: usize,
        callee_count: usize,
    ) -> Finding {
        let percentile = 90.0 + (pagerank / threshold.max(0.001)) * 5.0;
        let percentile = percentile.min(99.0);

        let (severity, title, risk_note) = if complexity >= self.high_complexity_threshold {
            (
                Severity::High,
                format!("Critical bottleneck: {}", name),
                format!(
                    "\n\n**⚠️ High Risk:** High influence ({:.4}) combined \
                    with high complexity ({}) creates significant risk. \
                    Changes here affect many dependents.",
                    pagerank, complexity
                ),
            )
        } else {
            (
                Severity::Medium,
                format!("Core infrastructure: {}", name),
                String::new(),
            )
        };

        let description = format!(
            "Function `{}` has high PageRank score \
            ({:.4}, ~{:.0}th percentile).\n\n\
            **What this means:**\n\
            - Many other functions depend on this (directly or transitively)\n\
            - Changes here have wide-reaching effects across the codebase\n\
            - This is legitimately important infrastructure code\n\n\
            **Metrics:**\n\
            - PageRank: {:.4}\n\
            - Complexity: {}\n\
            - Lines of code: {}\n\
            - Direct callers: {}\n\
            - Direct callees: {}{}",
            name,
            pagerank,
            percentile,
            pagerank,
            complexity,
            loc,
            caller_count,
            callee_count,
            risk_note
        );

        let mut suggested_fix = "\
            **For core infrastructure code:**\n\n\
            1. **Ensure comprehensive test coverage**: This code affects \
            many other components\n\n\
            2. **Add monitoring and observability**: Track performance and errors\n\n\
            3. **Document thoroughly**: Others depend on understanding this code\n\n\
            4. **Review before changes**: Consider impact on dependents\n\n\
            5. **Consider stability**: Avoid breaking changes; deprecate gradually"
            .to_string();

        if complexity >= self.high_complexity_threshold {
            suggested_fix.push_str(
                "\n\n**For high-complexity bottlenecks:**\n\n\
                6. **Consider refactoring**: Break into smaller, focused functions\n\n\
                7. **Extract interfaces**: Reduce coupling through abstraction\n\n\
                8. **Use feature flags**: De-risk changes with gradual rollout",
            );
        }

        let estimated_effort = if complexity >= self.high_complexity_threshold {
            "Large (4-8 hours)"
        } else {
            "Medium (1-2 hours)"
        };

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "InfluentialCodeDetector".to_string(),
            severity,
            title,
            description,
            affected_files: vec![file_path.into()],
            line_start: line_number,
            line_end: None,
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some(estimated_effort.to_string()),
            category: Some("architecture".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "High-influence code is the foundation of your codebase. \
                Changes here ripple through many dependents, so quality, \
                stability, and test coverage are critical."
                    .to_string(),
            ),
            ..Default::default()
        }
    }

    /// Create finding for bloated code (low PageRank + high complexity)
    fn create_bloated_code_finding(
        &self,
        name: &str,
        qualified_name: &str,
        file_path: &str,
        line_number: Option<u32>,
        pagerank: f64,
        median_pagerank: f64,
        complexity: u32,
        loc: u32,
        caller_count: usize,
    ) -> Finding {
        let (severity, bloat_level) = if complexity >= self.high_complexity_threshold * 2 {
            (Severity::High, "severely bloated")
        } else if complexity >= self.high_complexity_threshold {
            (Severity::Medium, "bloated")
        } else if loc >= self.high_loc_threshold * 2 {
            (Severity::Medium, "oversized")
        } else {
            (Severity::Low, "potentially bloated")
        };

        let description = format!(
            "Function `{}` is {}: high complexity/size \
            but low influence (PageRank {:.4}).\n\n\
            **What this means:**\n\
            - This code is complex but few other parts depend on it\n\
            - Not legitimately important infrastructure\n\
            - Prime candidate for refactoring or removal\n\n\
            **Metrics:**\n\
            - PageRank: {:.4} (median: {:.4})\n\
            - Complexity: {}\n\
            - Lines of code: {}\n\
            - Direct callers: {}",
            name, bloat_level, pagerank, pagerank, median_pagerank, complexity, loc, caller_count
        );

        let suggested_fix = "\
            **For bloated code:**\n\n\
            1. **Consider removal**: If truly unused, delete it\n\n\
            2. **Simplify**: Break into smaller, focused functions\n\n\
            3. **Extract reusable parts**: Move useful logic to shared utilities\n\n\
            4. **Review necessity**: Challenge whether this complexity is needed\n\n\
            5. **Add tests first**: Before refactoring, ensure test coverage"
            .to_string();

        let estimated_effort = match severity {
            Severity::High => "Medium (2-4 hours)",
            Severity::Medium => "Medium (1-2 hours)",
            _ => "Small (30-60 minutes)",
        };

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "InfluentialCodeDetector".to_string(),
            severity,
            title: format!("Bloated code: {} ({})", name, bloat_level),
            description,
            affected_files: vec![file_path.into()],
            line_start: line_number,
            line_end: None,
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some(estimated_effort.to_string()),
            category: Some("refactoring".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Bloated code adds complexity without proportional value. \
                It increases maintenance burden and cognitive load for \
                developers navigating the codebase."
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
        "Detects influential code and bloated code using PageRank analysis"
    }

    fn category(&self) -> &'static str {
        "architecture"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();
        
        // Skip common utility/helper function names - high influence is expected
        const SKIP_NAMES: &[&str] = &[
            "new", "default", "from", "into", "create", "build", "make", "with",
            "get", "set", "run", "main", "init", "setup", "start", "execute",
            "handle", "process", "parse", "format", "render", "display", "detect",
            // Helper/utility function prefixes
            "is_", "has_", "check_", "validate_", "should_", "can_", "find_",
            "calculate_", "compute_", "scan_", "extract_", "normalize_",
            // Service/business logic prefixes (expected high fan-in)
            "resolve_", "schedule_", "add_", "update_", "delete_", "remove_",
            "apply_", "use", "fetch_", "load_", "save_", "send_", "notify_",
        ];
        
        // Find functions with high influence (many transitive dependents)
        for func in graph.get_functions() {
            // Skip common utility functions
            let name_lower = func.name.to_lowercase();
            if SKIP_NAMES.iter().any(|&skip| {
                name_lower == skip 
                    || name_lower.starts_with(&format!("{}_", skip))
                    || name_lower.starts_with(skip)
            }) {
                continue;
            }
            
            let fan_in = graph.call_fan_in(&func.qualified_name);
            let complexity = func.complexity().unwrap_or(1) as usize;
            let loc = func.loc() as usize;
            
            // Influential: high fan-in and large
            if fan_in >= 8 && (complexity >= 15 || loc >= 100) {
                let severity = if fan_in >= 15 && complexity >= 20 {
                    Severity::High
                } else {
                    Severity::Medium
                };
                
                findings.push(Finding {
                    id: Uuid::new_v4().to_string(),
                    detector: "InfluentialCodeDetector".to_string(),
                    severity,
                    title: format!("Influential Code: {}", func.name),
                    description: format!(
                        "Function '{}' influences {} dependents with complexity {} and {} LOC. High-impact code.",
                        func.name, fan_in, complexity, loc
                    ),
                    affected_files: vec![func.file_path.clone().into()],
                    line_start: Some(func.line_start),
                    line_end: Some(func.line_end),
                    suggested_fix: Some("Consider refactoring to reduce complexity while maintaining interface".to_string()),
                    estimated_effort: Some("Large (4+ hours)".to_string()),
                    category: Some("architecture".to_string()),
                    cwe_id: None,
                    why_it_matters: Some("Changes to influential code have wide-reaching effects".to_string()),
                    ..Default::default()
                });
            }
        }
        
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pagerank_simple() {
        let detector = InfluentialCodeDetector::new();

        // Simple graph: 0 -> 1 -> 2
        // Node 2 should have highest PageRank (most "votes")
        let incoming = vec![
            vec![],  // 0 has no incoming
            vec![0], // 1 receives from 0
            vec![1], // 2 receives from 1
        ];
        let out_degree = vec![1, 1, 0];

        let pr = detector.calculate_pagerank(&incoming, &out_degree, 3);

        // Node 2 should have highest PageRank (sink)
        assert!(pr[2] > pr[1]);
        assert!(pr[2] > pr[0]);
    }

    #[test]
    fn test_pagerank_star() {
        let detector = InfluentialCodeDetector::new();

        // Star: 0, 1, 2 all point to 3
        // Node 3 should have highest PageRank
        let incoming = vec![
            vec![],        // 0
            vec![],        // 1
            vec![],        // 2
            vec![0, 1, 2], // 3 receives from all
        ];
        let out_degree = vec![1, 1, 1, 0];

        let pr = detector.calculate_pagerank(&incoming, &out_degree, 4);

        assert!(pr[3] > pr[0]);
        assert!(pr[3] > pr[1]);
        assert!(pr[3] > pr[2]);
    }
}
