//! Core utility detector using Harmonic Centrality
//!
//! Uses harmonic centrality to identify central coordinator functions
//! and isolated/dead code. Harmonic centrality handles disconnected graphs
//! better than closeness centrality.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
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
    }    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();
        
        for func in graph.get_functions() {
            let fan_in = graph.call_fan_in(&func.qualified_name);
            let fan_out = graph.call_fan_out(&func.qualified_name);
            
            // Core utility: high fan-in, low fan-out (many depend on it, it depends on few)
            if fan_in >= 10 && fan_out <= 2 {
                findings.push(Finding {
                    id: Uuid::new_v4().to_string(),
                    detector: "CoreUtilityDetector".to_string(),
                    severity: Severity::Info,
                    title: format!("Core Utility: {}", func.name),
                    description: format!(
                        "Function '{}' is used by {} callers. This is a core utility - ensure it's well-tested.",
                        func.name, fan_in
                    ),
                    affected_files: vec![func.file_path.clone().into()],
                    line_start: Some(func.line_start),
                    line_end: Some(func.line_end),
                    suggested_fix: Some("Ensure comprehensive test coverage for this core function".to_string()),
                    estimated_effort: Some("Small (1 hour)".to_string()),
                    category: Some("architecture".to_string()),
                    cwe_id: None,
                    why_it_matters: Some("Core utilities need extra attention as bugs affect many callers".to_string()),
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
