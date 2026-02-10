//! Influential code detector using PageRank
//!
//! Uses PageRank to identify truly important code components based on
//! incoming dependencies. Distinguishes legitimate core infrastructure from
//! bloated god classes.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphClient;
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
    }

    fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        debug!("Starting influential code detection");

        // Get all functions with metrics
        let functions_query = r#"
            MATCH (f:Function)
            OPTIONAL MATCH (caller:Function)-[:CALLS]->(f)
            OPTIONAL MATCH (f)-[:CALLS]->(callee:Function)
            WITH f,
                 count(DISTINCT caller) AS caller_count,
                 count(DISTINCT callee) AS callee_count
            RETURN f.qualifiedName AS qualified_name,
                   f.name AS name,
                   f.filePath AS file_path,
                   f.lineStart AS line_number,
                   coalesce(f.complexity, 0) AS complexity,
                   coalesce(f.loc, 0) AS loc,
                   caller_count,
                   callee_count
            ORDER BY qualified_name
        "#;
        let functions_result = graph.execute(functions_query)?;

        if functions_result.is_empty() {
            debug!("No functions found");
            return Ok(vec![]);
        }

        // Build function index and data
        struct FuncData {
            qualified_name: String,
            name: String,
            file_path: String,
            line_number: Option<u32>,
            complexity: u32,
            loc: u32,
            caller_count: usize,
            callee_count: usize,
        }

        let mut func_to_idx: HashMap<String, usize> = HashMap::new();
        let mut func_data: Vec<FuncData> = Vec::new();

        for (idx, row) in functions_result.iter().enumerate() {
            if let Some(qname) = row.get("qualified_name").and_then(|v| v.as_str()) {
                func_to_idx.insert(qname.to_string(), idx);
                func_data.push(FuncData {
                    qualified_name: qname.to_string(),
                    name: row
                        .get("name")
                        .and_then(|v| v.as_str())
                        .unwrap_or("unknown")
                        .to_string(),
                    file_path: row
                        .get("file_path")
                        .and_then(|v| v.as_str())
                        .unwrap_or("unknown")
                        .to_string(),
                    line_number: row
                        .get("line_number")
                        .and_then(|v| v.as_i64())
                        .map(|n| n as u32),
                    complexity: row.get("complexity").and_then(|v| v.as_i64()).unwrap_or(0) as u32,
                    loc: row.get("loc").and_then(|v| v.as_i64()).unwrap_or(0) as u32,
                    caller_count: row
                        .get("caller_count")
                        .and_then(|v| v.as_i64())
                        .unwrap_or(0) as usize,
                    callee_count: row
                        .get("callee_count")
                        .and_then(|v| v.as_i64())
                        .unwrap_or(0) as usize,
                });
            }
        }

        let num_nodes = func_data.len();
        debug!("Found {} functions", num_nodes);

        // Get call edges
        let edges_query = r#"
            MATCH (caller:Function)-[:CALLS]->(callee:Function)
            RETURN caller.qualifiedName AS src, callee.qualifiedName AS dst
        "#;
        let edges_result = graph.execute(edges_query)?;

        // Build incoming adjacency and out-degree
        let mut incoming: Vec<Vec<usize>> = vec![vec![]; num_nodes];
        let mut out_degree: Vec<usize> = vec![0; num_nodes];

        for row in edges_result {
            if let (Some(src), Some(dst)) = (
                row.get("src").and_then(|v| v.as_str()),
                row.get("dst").and_then(|v| v.as_str()),
            ) {
                if let (Some(&src_idx), Some(&dst_idx)) =
                    (func_to_idx.get(src), func_to_idx.get(dst))
                {
                    incoming[dst_idx].push(src_idx);
                    out_degree[src_idx] += 1;
                }
            }
        }

        // Calculate PageRank
        let pagerank = self.calculate_pagerank(&incoming, &out_degree, num_nodes);

        if pagerank.is_empty() {
            return Ok(vec![]);
        }

        // Calculate statistics
        let mut sorted_pr: Vec<f64> = pagerank.clone();
        sorted_pr.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

        let p90_idx = ((num_nodes as f64 * 0.90) as usize).min(num_nodes - 1);
        let p50_idx = num_nodes / 2;
        let p90_threshold = sorted_pr.get(p90_idx).copied().unwrap_or(0.0);
        let median_pr = sorted_pr.get(p50_idx).copied().unwrap_or(0.0);

        info!(
            "PageRank stats: median={:.6}, p90={:.6}",
            median_pr, p90_threshold
        );

        let mut findings = Vec::new();

        for (idx, &pr) in pagerank.iter().enumerate() {
            let f = &func_data[idx];

            // High PageRank = influential code
            if pr >= p90_threshold && pr > 0.0 {
                let finding = self.create_influential_code_finding(
                    &f.name,
                    &f.qualified_name,
                    &f.file_path,
                    f.line_number,
                    pr,
                    p90_threshold,
                    f.complexity,
                    f.loc,
                    f.caller_count,
                    f.callee_count,
                );
                findings.push(finding);
            }

            // Low PageRank + high complexity/loc = bloated code
            if pr <= median_pr
                && (f.complexity >= self.high_complexity_threshold
                    || f.loc >= self.high_loc_threshold)
            {
                let finding = self.create_bloated_code_finding(
                    &f.name,
                    &f.qualified_name,
                    &f.file_path,
                    f.line_number,
                    pr,
                    median_pr,
                    f.complexity,
                    f.loc,
                    f.caller_count,
                );
                findings.push(finding);
            }
        }

        // Sort by severity
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        // Limit findings
        if let Some(max) = self.config.max_findings {
            findings.truncate(max);
        }

        info!("InfluentialCodeDetector found {} findings", findings.len());

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
