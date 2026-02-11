//! Architectural bottleneck detector using betweenness centrality
//!
//! Identifies functions that sit on many execution paths (high betweenness),
//! indicating architectural bottlenecks that are critical points of failure.
//!
//! Uses Brandes algorithm for betweenness centrality calculation.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
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
        "Detects architectural bottlenecks using betweenness centrality"
    }

    fn category(&self) -> &'static str {
        "architecture"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();
        
        // Skip common function names that are expected to be widely called
        const SKIP_NAMES: &[&str] = &[
            "run", "new", "default", "create", "build", "init", "setup",
            "get", "set", "parse", "format", "render", "display", "detect",
            "analyze", "execute", "process", "handle", "dispatch",
        ];
        
        for func in graph.get_functions() {
            // Skip common utility functions
            let name_lower = func.name.to_lowercase();
            if SKIP_NAMES.iter().any(|&skip| name_lower == skip || name_lower.starts_with(&format!("{}_", skip))) {
                continue;
            }
            
            // Skip test functions
            if func.file_path.contains("/tests/") || func.name.starts_with("test_") {
                continue;
            }
            
            let fan_in = graph.call_fan_in(&func.qualified_name);
            let _fan_out = graph.call_fan_out(&func.qualified_name);
            let complexity = func.complexity().unwrap_or(1) as usize;
            
            // Bottleneck: high fan-in AND high complexity (increased thresholds)
            if fan_in >= 15 && complexity >= 15 {
                let severity = if fan_in >= 30 && complexity >= 25 {
                    Severity::Critical
                } else if fan_in >= 20 && complexity >= 20 {
                    Severity::High
                } else {
                    Severity::Medium
                };
                
                findings.push(Finding {
                    id: Uuid::new_v4().to_string(),
                    detector: "ArchitecturalBottleneckDetector".to_string(),
                    severity,
                    title: format!("Architectural Bottleneck: {}", func.name),
                    description: format!(
                        "Function '{}' is called by {} functions and has complexity {}. Changes here are high-risk.",
                        func.name, fan_in, complexity
                    ),
                    affected_files: vec![func.file_path.clone().into()],
                    line_start: Some(func.line_start),
                    line_end: Some(func.line_end),
                    suggested_fix: Some("Reduce complexity or create a facade to isolate changes".to_string()),
                    estimated_effort: Some("Large (4-8 hours)".to_string()),
                    category: Some("architecture".to_string()),
                    cwe_id: None,
                    why_it_matters: Some("Bottlenecks are single points of failure that amplify bugs".to_string()),
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
