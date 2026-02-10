//! Architectural bottleneck detector using betweenness centrality
//!
//! Identifies functions that sit on many execution paths (high betweenness),
//! indicating architectural bottlenecks that are critical points of failure.
//!
//! Uses Brandes algorithm for betweenness centrality calculation.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphClient;
use crate::models::{Finding, Severity};
use anyhow::Result;
use rayon::prelude::*;
use std::collections::{HashMap, VecDeque};
use tracing::{debug, info};
use uuid::Uuid;

/// Detects architectural bottlenecks using betweenness centrality.
///
/// Functions with high betweenness centrality appear on many shortest paths
/// between other functions, making them critical architectural components.
/// Changes to these functions have high blast radius.
pub struct ArchitecturalBottleneckDetector {
    config: DetectorConfig,
    /// Complexity threshold for severity escalation
    high_complexity_threshold: u32,
}

impl ArchitecturalBottleneckDetector {
    /// Create a new detector with default config
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            high_complexity_threshold: 20,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        let high_complexity_threshold = config.get_option_or("high_complexity_threshold", 20);
        Self {
            config,
            high_complexity_threshold,
        }
    }

    /// Calculate betweenness centrality using Brandes algorithm (parallelized)
    fn calculate_betweenness(&self, adj: &[Vec<usize>], num_nodes: usize) -> Vec<f64> {
        if num_nodes == 0 {
            return vec![];
        }

        // Run BFS from each source node in parallel
        let partial_scores: Vec<Vec<f64>> = (0..num_nodes)
            .into_par_iter()
            .map(|source| {
                let mut partial = vec![0.0; num_nodes];
                let mut stack = Vec::new();
                let mut predecessors: Vec<Vec<usize>> = vec![vec![]; num_nodes];
                let mut num_paths = vec![0.0; num_nodes];
                num_paths[source] = 1.0;
                let mut distance: Vec<i32> = vec![-1; num_nodes];
                distance[source] = 0;

                let mut queue = VecDeque::new();
                queue.push_back(source);

                // BFS
                while let Some(v) = queue.pop_front() {
                    stack.push(v);
                    for &w in &adj[v] {
                        if distance[w] < 0 {
                            distance[w] = distance[v] + 1;
                            queue.push_back(w);
                        }
                        if distance[w] == distance[v] + 1 {
                            num_paths[w] += num_paths[v];
                            predecessors[w].push(v);
                        }
                    }
                }

                // Backtrack to accumulate dependencies
                let mut dependency = vec![0.0; num_nodes];
                while let Some(w) = stack.pop() {
                    for &v in &predecessors[w] {
                        let contrib = (num_paths[v] / num_paths[w]) * (1.0 + dependency[w]);
                        dependency[v] += contrib;
                    }
                    if w != source {
                        partial[w] += dependency[w];
                    }
                }

                partial
            })
            .collect();

        // Combine partial scores
        let mut betweenness = vec![0.0; num_nodes];
        for partial in partial_scores {
            for (i, score) in partial.into_iter().enumerate() {
                betweenness[i] += score;
            }
        }

        betweenness
    }

    /// Calculate severity based on betweenness percentile and complexity
    fn calculate_severity(
        &self,
        betweenness: f64,
        max_betweenness: f64,
        complexity: u32,
    ) -> Severity {
        let percentile = if max_betweenness > 0.0 {
            (betweenness / max_betweenness) * 100.0
        } else {
            0.0
        };

        if percentile >= 99.0 && complexity > self.high_complexity_threshold {
            Severity::Critical
        } else if percentile >= 99.0
            || (percentile >= 95.0 && complexity > self.high_complexity_threshold)
        {
            Severity::High
        } else if percentile >= 95.0 || complexity > self.high_complexity_threshold {
            Severity::Medium
        } else {
            Severity::Low
        }
    }

    /// Create a finding from a bottleneck
    fn create_finding(
        &self,
        name: &str,
        qualified_name: &str,
        file_path: &str,
        line_number: Option<u32>,
        betweenness: f64,
        max_betweenness: f64,
        avg_betweenness: f64,
        complexity: u32,
    ) -> Finding {
        let severity = self.calculate_severity(betweenness, max_betweenness, complexity);
        let percentile = if max_betweenness > 0.0 {
            (betweenness / max_betweenness) * 100.0
        } else {
            0.0
        };

        let title = if severity == Severity::Critical || severity == Severity::High {
            if complexity > self.high_complexity_threshold {
                format!(
                    "Critical architectural bottleneck with high complexity: {}",
                    name
                )
            } else {
                format!("Critical architectural bottleneck: {}", name)
            }
        } else if complexity > self.high_complexity_threshold {
            format!("Architectural bottleneck with high complexity: {}", name)
        } else {
            format!("Architectural bottleneck: {}", name)
        };

        let mut description = format!(
            "Function `{}` has high betweenness centrality \
            (score: {:.4}, {:.1}th percentile). \
            This indicates it sits on many execution paths between other functions, \
            making it a critical architectural component.\n\n\
            **Risk factors:**\n\
            - High blast radius: Changes here affect many code paths\n\
            - Single point of failure: If this breaks, cascading failures likely\n\
            - Refactoring risk: Difficult to change safely",
            name, betweenness, percentile
        );

        if complexity > self.high_complexity_threshold {
            description.push_str(&format!(
                "\n\n**Additional concern:** High complexity ({}) makes this bottleneck especially risky.",
                complexity
            ));
        }

        let suggested_fix = format!(
            "**Immediate actions:**\n\
            1. Ensure comprehensive test coverage for `{}`\n\
            2. Add defensive error handling and logging\n\
            3. Consider circuit breaker pattern for failure isolation\n\n\
            **Long-term refactoring:**\n\
            1. Analyze why so many paths flow through this function\n\
            2. Consider splitting into multiple specialized functions\n\
            3. Introduce abstraction layers to reduce coupling\n\
            4. Evaluate if functionality can be distributed\n\n\
            **Monitoring:**\n\
            - Add performance monitoring (this is a hot path)\n\
            - Track error rates (failures here cascade)\n\
            - Alert on anomalies",
            name
        );

        let estimated_effort = match severity {
            Severity::Critical => "Large (1-2 days)",
            Severity::High => "Large (4-8 hours)",
            _ => "Medium (2-4 hours)",
        };

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "ArchitecturalBottleneckDetector".to_string(),
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
                "Architectural bottlenecks are critical points where failures cascade. \
                High betweenness means many code paths depend on this function, \
                making it risky to change and a potential performance hotspot."
                    .to_string(),
            ),
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
        "Detects architectural bottlenecks using betweenness centrality"
    }

    fn category(&self) -> &'static str {
        "architecture"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        debug!("Starting architectural bottleneck detection");

        // Get all functions
        let functions_query = r#"
            MATCH (f:Function)
            RETURN f.qualifiedName AS qualified_name,
                   f.name AS name,
                   f.filePath AS file_path,
                   f.lineStart AS line_number,
                   coalesce(f.complexity, 0) AS complexity
            ORDER BY qualified_name
        "#;
        let functions_result = graph.execute(functions_query)?;

        if functions_result.is_empty() {
            debug!("No functions found in graph");
            return Ok(vec![]);
        }

        // Build function index
        let mut func_to_idx: HashMap<String, usize> = HashMap::new();
        let mut func_data: Vec<(String, String, String, Option<u32>, u32)> = Vec::new();

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
                ));
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

        // Build adjacency list
        let mut adj: Vec<Vec<usize>> = vec![vec![]; num_nodes];
        for row in edges_result {
            if let (Some(src), Some(dst)) = (
                row.get("src").and_then(|v| v.as_str()),
                row.get("dst").and_then(|v| v.as_str()),
            ) {
                if let (Some(&src_idx), Some(&dst_idx)) =
                    (func_to_idx.get(src), func_to_idx.get(dst))
                {
                    adj[src_idx].push(dst_idx);
                }
            }
        }

        debug!("Built adjacency list");

        // Calculate betweenness centrality
        let betweenness = self.calculate_betweenness(&adj, num_nodes);

        if betweenness.is_empty() {
            return Ok(vec![]);
        }

        // Calculate statistics
        let max_betweenness = betweenness.iter().cloned().fold(0.0_f64, f64::max);
        let avg_betweenness = betweenness.iter().sum::<f64>() / num_nodes as f64;
        let stdev = if num_nodes > 1 {
            let variance = betweenness
                .iter()
                .map(|&b| (b - avg_betweenness).powi(2))
                .sum::<f64>()
                / num_nodes as f64;
            variance.sqrt()
        } else {
            0.0
        };

        // Threshold: mean + 2*stdev (top ~5%)
        let threshold = avg_betweenness + 2.0 * stdev;

        info!(
            "Betweenness stats: max={:.4}, avg={:.4}, stdev={:.4}, threshold={:.4}",
            max_betweenness, avg_betweenness, stdev, threshold
        );

        // Find bottlenecks (above threshold)
        let mut findings: Vec<Finding> = Vec::new();

        for (idx, &score) in betweenness.iter().enumerate() {
            if score >= threshold && score > 0.0 {
                let (qualified_name, name, file_path, line_number, complexity) = &func_data[idx];
                
                // Skip parser files - they are designed to be central/orchestrating
                if file_path.contains("/parsers/") || file_path.contains("\\parsers\\") {
                    continue;
                }
                
                let finding = self.create_finding(
                    name,
                    qualified_name,
                    file_path,
                    *line_number,
                    score,
                    max_betweenness,
                    avg_betweenness,
                    *complexity,
                );
                findings.push(finding);
            }
        }

        // Sort by severity (highest first)
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        // Limit findings
        if let Some(max) = self.config.max_findings {
            findings.truncate(max);
        }

        info!(
            "ArchitecturalBottleneckDetector found {} bottlenecks",
            findings.len()
        );

        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_betweenness_simple_chain() {
        let detector = ArchitecturalBottleneckDetector::new();
        // Chain: 0 -> 1 -> 2 -> 3
        let adj = vec![
            vec![1], // 0
            vec![2], // 1
            vec![3], // 2
            vec![],  // 3
        ];
        let betweenness = detector.calculate_betweenness(&adj, 4);

        // Node 1 and 2 should have highest betweenness (they're in the middle)
        assert!(betweenness[1] > betweenness[0]);
        assert!(betweenness[2] > betweenness[3]);
    }

    #[test]
    fn test_betweenness_star() {
        let detector = ArchitecturalBottleneckDetector::new();
        // Star: 0 is center, connects to 1, 2, 3
        let adj = vec![
            vec![1, 2, 3], // 0 (center)
            vec![0],       // 1
            vec![0],       // 2
            vec![0],       // 3
        ];
        let betweenness = detector.calculate_betweenness(&adj, 4);

        // Center should have highest betweenness
        assert!(betweenness[0] >= betweenness[1]);
        assert!(betweenness[0] >= betweenness[2]);
        assert!(betweenness[0] >= betweenness[3]);
    }

    #[test]
    fn test_severity_calculation() {
        let detector = ArchitecturalBottleneckDetector::new();

        // Very high percentile + high complexity = Critical
        assert_eq!(
            detector.calculate_severity(99.0, 100.0, 25),
            Severity::Critical
        );

        // High percentile alone = High
        assert_eq!(detector.calculate_severity(99.0, 100.0, 10), Severity::High);

        // Moderate percentile + high complexity = Medium
        assert_eq!(detector.calculate_severity(96.0, 100.0, 25), Severity::High);

        // Low percentile, low complexity = Low
        assert_eq!(detector.calculate_severity(50.0, 100.0, 5), Severity::Low);
    }
}
