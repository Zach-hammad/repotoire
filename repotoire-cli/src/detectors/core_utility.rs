//! Core utility detector using Harmonic Centrality
//!
//! Uses harmonic centrality to identify central coordinator functions
//! and isolated/dead code. Harmonic centrality handles disconnected graphs
//! better than closeness centrality.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphQueryExt;
use crate::models::{Finding, Severity};
use anyhow::Result;
use rayon::prelude::*;
use std::collections::{HashMap, VecDeque};
use tracing::{debug, info};

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
    #[allow(dead_code)] // Config field
    high_complexity_threshold: u32,
    /// Minimum callers to not be considered isolated
    #[allow(dead_code)] // Config field
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
    #[allow(dead_code)] // Builder pattern method
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
    #[allow(dead_code)] // Graph algorithm helper
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
    #[allow(dead_code)] // Helper for graph-based detection
    fn create_central_coordinator_finding(
        &self,
        name: &str,
        _qualified_name: &str,
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
            id: String::new(),
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
            ..Default::default()
        }
    }

    /// Create a finding for isolated/dead code
    #[allow(dead_code)] // Helper for graph-based detection
    fn create_isolated_code_finding(
        &self,
        name: &str,
        _qualified_name: &str,
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
            id: String::new(),
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
            ..Default::default()
        })
    }
}

impl Default for CoreUtilityDetector {
    fn default() -> Self {
        Self::new()
    }
}

/// Check if a function is a core utility (high fan-in, low fan-out, cross-module callers).
/// Used by other detectors to adjust their behavior.
pub fn is_core_utility_node(graph: &dyn crate::graph::traits::GraphQuery, qualified_name: &str) -> bool {
    let fan_in = graph.call_fan_in(qualified_name);
    let fan_out = graph.call_fan_out(qualified_name);
    if fan_in < 10 || fan_out > 2 {
        return false;
    }
    let module_spread = graph.caller_module_spread(qualified_name);
    module_spread >= 3
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
    fn detect(&self, ctx: &crate::detectors::analysis_context::AnalysisContext) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let i = graph.interner();
        use std::collections::HashSet;

        // Pre-build skip set per FILE (not per function) — avoids redundant content
        // classifier scans.  On CPython (3.4K files, 71K functions) this turns
        // 71K × 4 content scans into 3.4K × 4.
        let all_functions = graph.get_functions_shared();

        let mut skip_files: HashSet<String> = HashSet::new();
        {
            let mut seen_files: HashSet<String> = HashSet::new();
            for func in all_functions.iter() {
                if !seen_files.insert(func.path(i).to_string()) {
                    continue; // already classified this file
                }
                if crate::detectors::content_classifier::is_likely_bundled_path(func.path(i)) {
                    skip_files.insert(func.path(i).to_string());
                    continue;
                }
                if let Some(content) =
                    crate::cache::global_cache().content(std::path::Path::new(func.path(i)))
                {
                    if crate::detectors::content_classifier::is_bundled_code(&content)
                        || crate::detectors::content_classifier::is_minified_code(&content)
                        || crate::detectors::content_classifier::is_fixture_code(
                            func.path(i),
                            &content,
                        )
                    {
                        skip_files.insert(func.path(i).to_string());
                    }
                }
            }
        }

        let mut findings = Vec::new();

        // Adaptive fan-in threshold (default 10)
        let fan_in_threshold = ctx.threshold(crate::calibrate::MetricKind::FanIn, 10.0) as usize;

        // Minimum cross-module callers to be flagged as a true cross-cutting utility.
        // A function called from only 1-2 directories is a local concern, not architectural.
        let min_cross_module_callers: usize = 3;

        for func in all_functions.iter() {
            if skip_files.contains(func.path(i)) {
                continue;
            }

            let qn = func.qn(i);

            // ── Early cheap filters ─────────────────────────────────────

            // fan_in check first (cheap O(1) lookup) — skip 99%+ of functions early
            let fan_in = graph.call_fan_in(qn);
            if fan_in < fan_in_threshold {
                continue;
            }

            // Utilities have low fan-out (they are called, not callers)
            let fan_out = graph.call_fan_out(qn);
            if fan_out > 2 {
                continue;
            }

            // ── Role-based FP reduction ─────────────────────────────────

            // Skip test functions — not architectural concerns
            if ctx.is_test_function(qn) {
                continue;
            }

            // Skip Hub and Orchestrator roles — these are infrastructure/coordination
            // code, not utilities that need extra test coverage attention
            if let Some(role) = ctx.function_role(qn) {
                use crate::detectors::function_context::FunctionRole;
                if matches!(role,
                    FunctionRole::Hub
                    | FunctionRole::Orchestrator
                    | FunctionRole::EntryPoint
                ) {
                    continue;
                }
            }

            // Skip HMM-classified handlers (request handlers, CLI handlers, etc.)
            if ctx.is_handler(qn) {
                continue;
            }

            // Skip unreachable code (dead code) — unless it's public API
            if !ctx.is_reachable(qn) && !ctx.is_public_api(qn) {
                continue;
            }

            // ── Cross-module fan-in analysis ────────────────────────────
            //
            // A function called 20 times within its own file/directory isn't an
            // architectural concern — it's a well-used local helper. We only flag
            // functions with significant cross-module (cross-directory) callers,
            // which indicates true cross-cutting utility status.

            let func_dir = std::path::Path::new(func.path(i))
                .parent()
                .unwrap_or_else(|| std::path::Path::new(""))
                .to_string_lossy();

            let mut cross_module_dirs: HashSet<String> = HashSet::new();
            let mut total_cross_module: usize = 0;

            if let Some(callers) = ctx.detector_ctx.callers_by_qn.get(qn) {
                for caller_qn in callers {
                    // Look up the caller's file path via the graph
                    if let Some(caller_node) = graph.get_node(caller_qn) {
                        let caller_dir = std::path::Path::new(caller_node.path(i))
                            .parent()
                            .unwrap_or_else(|| std::path::Path::new(""))
                            .to_string_lossy()
                            .to_string();

                        if caller_dir != func_dir.as_ref() {
                            cross_module_dirs.insert(caller_dir);
                            total_cross_module += 1;
                        }
                    }
                }
            }

            // Not enough cross-module callers — this is a local utility, skip
            if cross_module_dirs.len() < min_cross_module_callers {
                continue;
            }

            // ── Content-based FP reduction ──────────────────────────────

            // Per-function AST manipulation check (needs func name — can't pre-compute)
            if let Some(content) =
                crate::cache::global_cache().content(std::path::Path::new(func.path(i)))
            {
                if crate::detectors::content_classifier::is_ast_manipulation_code(
                    func.node_name(i), &content,
                ) {
                    continue;
                }
            }

            // ── Severity & finding construction ─────────────────────────

            // Public API functions are designed to be widely called — Info only
            let is_public = ctx.is_public_api(qn);
            let severity = if is_public {
                Severity::Info
            } else if cross_module_dirs.len() >= 8 && fan_in >= fan_in_threshold * 3 {
                // Very high cross-module fan-in AND high total fan-in
                Severity::Medium
            } else {
                Severity::Info
            };

            let cross_pct = if fan_in > 0 {
                (total_cross_module as f64 / fan_in as f64 * 100.0) as u32
            } else {
                0
            };

            let description = format!(
                "Function `{}` is called from {} distinct directories ({} cross-module callers \
                out of {} total, {}%). Changes here have wide-reaching effects across the codebase.\n\n\
                **Metrics:**\n\
                - Total callers (fan-in): {}\n\
                - Cross-module callers: {} (from {} directories)\n\
                - Fan-out: {}{}",
                func.node_name(i),
                cross_module_dirs.len(),
                total_cross_module,
                fan_in,
                cross_pct,
                fan_in,
                total_cross_module,
                cross_module_dirs.len(),
                fan_out,
                if is_public { "\n- **Public API** — widely called by design" } else { "" },
            );

            findings.push(Finding {
                id: String::new(),
                detector: "CoreUtilityDetector".to_string(),
                severity,
                title: format!("Core Utility: {} ({} cross-module callers)", func.node_name(i), total_cross_module),
                description,
                affected_files: vec![func.path(i).to_string().into()],
                line_start: Some(func.line_start),
                line_end: Some(func.line_end),
                suggested_fix: Some(
                    "**For core utilities:**\n\n\
                    1. **Ensure comprehensive test coverage** — bugs here affect many callers\n\n\
                    2. **Avoid breaking changes** — consider deprecation cycles\n\n\
                    3. **Document the contract** — callers depend on stable behavior\n\n\
                    4. **Monitor performance** — hot path across many modules".to_string()
                ),
                estimated_effort: Some("Small (1 hour)".to_string()),
                category: Some("architecture".to_string()),
                cwe_id: None,
                why_it_matters: Some(format!(
                    "Called from {} different directories — a bug or breaking change in this \
                    function cascades across the codebase.",
                    cross_module_dirs.len()
                )),
                ..Default::default()
            });
        }

        info!(
            "CoreUtilityDetector: {} findings (fan_in_threshold={}, min_cross_module={})",
            findings.len(), fan_in_threshold, min_cross_module_callers
        );

        Ok(findings)
    }
}

impl super::RegisteredDetector for CoreUtilityDetector {
    fn create(_init: &super::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::new())
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
