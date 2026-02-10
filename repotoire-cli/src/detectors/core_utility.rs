//! Core utility detector using Harmonic Centrality
//!
//! Uses harmonic centrality to identify central coordinator functions
//! and isolated/dead code. Harmonic centrality handles disconnected graphs
//! better than closeness centrality.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphClient;
use crate::models::{Finding, Severity};
use anyhow::Result;
use rayon::prelude::*;
use std::collections::{HashMap, VecDeque};
use tracing::{debug, info};
use uuid::Uuid;

/// Detects central coordinators and isolated code using Harmonic Centrality.
///
/// Harmonic centrality measures how close a function is to all other functions,
/// handling disconnected graphs gracefully (unlike closeness centrality).
///
/// Detects:
/// - Central coordinators: High harmonic + high complexity (bottleneck risk)
/// - Isolated code: Low harmonic + few connections (potential dead code)
pub struct CoreUtilityDetector {
    config: DetectorConfig,
    /// Complexity threshold for escalating central coordinator severity
    high_complexity_threshold: u32,
    /// Minimum callers to not be considered isolated
    min_callers_threshold: usize,
}

impl CoreUtilityDetector {
    /// Create a new detector with default config
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            high_complexity_threshold: 20,
            min_callers_threshold: 2,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        let high_complexity_threshold = config.get_option_or("high_complexity_threshold", 20);
        let min_callers_threshold = config.get_option_or("min_callers_threshold", 2);
        Self {
            config,
            high_complexity_threshold,
            min_callers_threshold,
        }
    }

    /// Calculate harmonic centrality for all nodes (parallelized)
    ///
    /// HC(v) = Σ (1 / d(v, u)) for all u ≠ v
    fn calculate_harmonic(
        &self,
        adj: &[Vec<usize>],
        num_nodes: usize,
        normalized: bool,
    ) -> Vec<f64> {
        if num_nodes == 0 {
            return vec![];
        }
        if num_nodes == 1 {
            return vec![0.0];
        }

        let norm_factor = if normalized {
            (num_nodes - 1) as f64
        } else {
            1.0
        };

        (0..num_nodes)
            .into_par_iter()
            .map(|source| {
                let mut distance: Vec<i32> = vec![-1; num_nodes];
                distance[source] = 0;
                let mut queue = VecDeque::new();
                queue.push_back(source);
                let mut score = 0.0;

                while let Some(v) = queue.pop_front() {
                    for &w in &adj[v] {
                        if distance[w] < 0 {
                            distance[w] = distance[v] + 1;
                            queue.push_back(w);
                            score += 1.0 / distance[w] as f64;
                        }
                    }
                }

                score / norm_factor
            })
            .collect()
    }

    /// Create a finding for central coordinator function
    fn create_central_coordinator_finding(
        &self,
        name: &str,
        qualified_name: &str,
        file_path: &str,
        line_number: Option<u32>,
        harmonic: f64,
        max_harmonic: f64,
        complexity: u32,
        loc: u32,
        caller_count: usize,
        callee_count: usize,
    ) -> Finding {
        let percentile = if max_harmonic > 0.0 {
            (harmonic / max_harmonic) * 100.0
        } else {
            0.0
        };

        let (severity, title) = if complexity > self.high_complexity_threshold {
            (
                Severity::High,
                format!("Central coordinator with high complexity: {}", name),
            )
        } else {
            (Severity::Medium, format!("Central coordinator: {}", name))
        };

        let mut description = format!(
            "Function `{}` has high harmonic centrality \
            (score: {:.3}, {:.0}th percentile).\n\n\
            **What this means:**\n\
            - Can reach most functions in the codebase quickly\n\
            - Acts as a coordination point for execution flow\n\
            - Changes here have wide-reaching effects\n\n\
            **Metrics:**\n\
            - Harmonic centrality: {:.3}\n\
            - Complexity: {}\n\
            - Lines of code: {}\n\
            - Callers: {}\n\
            - Callees: {}",
            name, harmonic, percentile, harmonic, complexity, loc, caller_count, callee_count
        );

        if complexity > self.high_complexity_threshold {
            description.push_str(&format!(
                "\n\n**Warning:** High complexity ({}) combined with \
                central position creates significant risk.",
                complexity
            ));
        }

        let suggested_fix = "\
            **For central coordinators:**\n\n\
            1. **Ensure test coverage**: This function affects many code paths\n\n\
            2. **Add monitoring**: Track performance and errors here\n\n\
            3. **Review complexity**: Consider splitting if too complex\n\n\
            4. **Document thoroughly**: Others need to understand this code\n\n\
            5. **Consider patterns**:\n\
               - Facade pattern to simplify interface\n\
               - Mediator pattern to manage interactions\n\
               - Event-driven design to reduce coupling"
            .to_string();

        let estimated_effort =
            if complexity > self.high_complexity_threshold * 2 || caller_count > 20 {
                "Large (2-4 hours)"
            } else if complexity > self.high_complexity_threshold || caller_count > 10 {
                "Medium (1-2 hours)"
            } else {
                "Small (30-60 minutes)"
            };

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "CoreUtilityDetector".to_string(),
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
                "Central coordinators are critical nexus points in the codebase. \
                They can reach most other code quickly, meaning changes here \
                have cascading effects across the system."
                    .to_string(),
            ),
        }
    }

    /// Create a finding for isolated/dead code
    fn create_isolated_code_finding(
        &self,
        name: &str,
        qualified_name: &str,
        file_path: &str,
        line_number: Option<u32>,
        harmonic: f64,
        max_harmonic: f64,
        loc: u32,
        caller_count: usize,
        callee_count: usize,
    ) -> Option<Finding> {
        // Skip very small functions (likely utilities or stubs)
        if loc < 5 {
            return None;
        }

        let percentile = if max_harmonic > 0.0 {
            (harmonic / max_harmonic) * 100.0
        } else {
            0.0
        };

        let (severity, isolation_level) = if caller_count == 0 && callee_count == 0 {
            (Severity::Medium, "completely isolated")
        } else if caller_count == 0 {
            (Severity::Low, "never called")
        } else {
            (Severity::Low, "barely connected")
        };

        let description = format!(
            "Function `{}` has very low harmonic centrality \
            (score: {:.3}, {:.0}th percentile).\n\n\
            **Status:** {}\n\n\
            **What this means:**\n\
            - Disconnected from most of the codebase\n\
            - May be dead code or unused functionality\n\
            - Could be misplaced or poorly integrated\n\n\
            **Metrics:**\n\
            - Harmonic centrality: {:.3}\n\
            - Callers: {}\n\
            - Callees: {}\n\
            - Lines of code: {}",
            name, harmonic, percentile, isolation_level, harmonic, caller_count, callee_count, loc
        );

        let suggested_fix = "\
            **Investigate isolated code:**\n\n\
            1. **Check if dead code**: Search for usages across the codebase\n\n\
            2. **Check if test-only**: May be called only from tests\n\n\
            3. **Check if entry point**: CLI commands, API endpoints, etc.\n\n\
            4. **Consider removal**: If truly unused, delete it\n\n\
            5. **Consider integration**: If needed, integrate properly with the codebase"
            .to_string();

        let estimated_effort = if loc < 50 {
            "Small (15-30 minutes)"
        } else {
            "Small (30-60 minutes)"
        };

        Some(Finding {
            id: Uuid::new_v4().to_string(),
            detector: "CoreUtilityDetector".to_string(),
            severity,
            title: format!("Isolated code: {} ({})", name, isolation_level),
            description,
            affected_files: vec![file_path.into()],
            line_start: line_number,
            line_end: None,
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some(estimated_effort.to_string()),
            category: Some("dead_code".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Isolated code increases maintenance burden without providing value. \
                It may confuse developers and add cognitive load when navigating the codebase."
                    .to_string(),
            ),
        })
    }
}

impl Default for CoreUtilityDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for CoreUtilityDetector {
    fn name(&self) -> &'static str {
        "CoreUtilityDetector"
    }

    fn description(&self) -> &'static str {
        "Detects central coordinators and isolated code using harmonic centrality"
    }

    fn category(&self) -> &'static str {
        "architecture"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        debug!("Starting core utility detection");

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
            debug!("No functions found in graph");
            return Ok(vec![]);
        }

        // Build function index and data
        let mut func_to_idx: HashMap<String, usize> = HashMap::new();
        let mut func_data: Vec<(String, String, String, Option<u32>, u32, u32, usize, usize)> =
            Vec::new();

        for (idx, row) in functions_result.iter().enumerate() {
            if let Some(qname) = row.get("qualified_name").and_then(|v| v.as_str()) {
                func_to_idx.insert(qname.to_string(), idx);
                func_data.push((
                    qname.to_string(),
                    row.get("name")
                        .and_then(|v| v.as_str())
                        .unwrap_or("unknown")
                        .to_string(),
                    row.get("file_path")
                        .and_then(|v| v.as_str())
                        .unwrap_or("unknown")
                        .to_string(),
                    row.get("line_number")
                        .and_then(|v| v.as_i64())
                        .map(|n| n as u32),
                    row.get("complexity").and_then(|v| v.as_i64()).unwrap_or(0) as u32,
                    row.get("loc").and_then(|v| v.as_i64()).unwrap_or(0) as u32,
                    row.get("caller_count")
                        .and_then(|v| v.as_i64())
                        .unwrap_or(0) as usize,
                    row.get("callee_count")
                        .and_then(|v| v.as_i64())
                        .unwrap_or(0) as usize,
                ));
            }
        }

        let num_nodes = func_data.len();
        debug!("Found {} functions", num_nodes);

        // Get call edges (bidirectional for centrality)
        let edges_query = r#"
            MATCH (a:Function)-[:CALLS]->(b:Function)
            RETURN a.qualifiedName AS src, b.qualifiedName AS dst
        "#;
        let edges_result = graph.execute(edges_query)?;

        // Build bidirectional adjacency list
        let mut adj: Vec<Vec<usize>> = vec![vec![]; num_nodes];
        for row in edges_result {
            if let (Some(src), Some(dst)) = (
                row.get("src").and_then(|v| v.as_str()),
                row.get("dst").and_then(|v| v.as_str()),
            ) {
                if let (Some(&src_idx), Some(&dst_idx)) =
                    (func_to_idx.get(src), func_to_idx.get(dst))
                {
                    if src_idx != dst_idx {
                        adj[src_idx].push(dst_idx);
                        adj[dst_idx].push(src_idx);
                    }
                }
            }
        }

        // Calculate harmonic centrality
        let harmonic = self.calculate_harmonic(&adj, num_nodes, true);

        if harmonic.is_empty() {
            return Ok(vec![]);
        }

        // Calculate statistics
        let max_harmonic = harmonic.iter().cloned().fold(0.0_f64, f64::max);
        let avg_harmonic = harmonic.iter().sum::<f64>() / num_nodes as f64;

        // Sort for percentiles
        let mut sorted_harmonic: Vec<f64> = harmonic.clone();
        sorted_harmonic.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

        let p95_idx = ((num_nodes as f64 * 0.95) as usize).min(num_nodes - 1);
        let p10_idx = ((num_nodes as f64 * 0.10) as usize).min(num_nodes - 1);
        let p95 = sorted_harmonic.get(p95_idx).copied().unwrap_or(0.8);
        let p10 = sorted_harmonic.get(p10_idx).copied().unwrap_or(0.2);

        info!(
            "Harmonic centrality stats: avg={:.3}, p10={:.3}, p95={:.3}, max={:.3}",
            avg_harmonic, p10, p95, max_harmonic
        );

        let mut findings: Vec<Finding> = Vec::new();

        // Find central coordinators (top 5%)
        for (idx, &score) in harmonic.iter().enumerate() {
            let (
                qualified_name,
                name,
                file_path,
                line_number,
                complexity,
                loc,
                caller_count,
                callee_count,
            ) = &func_data[idx];

            if score >= p95 {
                let finding = self.create_central_coordinator_finding(
                    name,
                    qualified_name,
                    file_path,
                    *line_number,
                    score,
                    max_harmonic,
                    *complexity,
                    *loc,
                    *caller_count,
                    *callee_count,
                );
                findings.push(finding);
            }

            // Find isolated code (bottom 10% + few callers)
            if score <= p10 && *caller_count < self.min_callers_threshold {
                if let Some(finding) = self.create_isolated_code_finding(
                    name,
                    qualified_name,
                    file_path,
                    *line_number,
                    score,
                    max_harmonic,
                    *loc,
                    *caller_count,
                    *callee_count,
                ) {
                    findings.push(finding);
                }
            }
        }

        // Sort by severity
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        // Limit findings
        if let Some(max) = self.config.max_findings {
            findings.truncate(max);
        }

        info!("CoreUtilityDetector found {} findings", findings.len());

        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_harmonic_centrality_star() {
        let detector = CoreUtilityDetector::new();
        // Star: 0 is center, bidirectional edges to 1, 2, 3
        let adj = vec![
            vec![1, 2, 3], // 0 (center)
            vec![0],       // 1
            vec![0],       // 2
            vec![0],       // 3
        ];
        let harmonic = detector.calculate_harmonic(&adj, 4, false);

        // Center should have highest harmonic (can reach all nodes in 1 step)
        assert!(harmonic[0] > harmonic[1]);
        assert!(harmonic[0] > harmonic[2]);
        assert!(harmonic[0] > harmonic[3]);

        // Center's harmonic = 1/1 + 1/1 + 1/1 = 3
        assert!((harmonic[0] - 3.0).abs() < 0.001);
    }

    #[test]
    fn test_harmonic_centrality_chain() {
        let detector = CoreUtilityDetector::new();
        // Chain: 0 - 1 - 2 - 3 (bidirectional)
        let adj = vec![
            vec![1],    // 0
            vec![0, 2], // 1
            vec![1, 3], // 2
            vec![2],    // 3
        ];
        let harmonic = detector.calculate_harmonic(&adj, 4, false);

        // Middle nodes should have higher harmonic
        assert!(harmonic[1] > harmonic[0]);
        assert!(harmonic[2] > harmonic[3]);
    }
}
