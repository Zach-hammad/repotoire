//! Mutual recursion detector using call-graph SCCs.
//!
//! Identifies groups of functions that form call cycles (mutually recursive
//! sets). These create tight coupling and make reasoning, testing, and
//! refactoring difficult — especially when combined with high complexity.

use crate::detectors::base::{Detector, DetectorConfig, DetectorScope};
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::Arc;
use tracing::debug;

/// Detects mutual recursion via call-graph strongly connected components.
///
/// Functions that call each other in a cycle create mutual recursion.
/// This detector reports call cycles with configurable size limits and
/// severity based on cycle size and aggregate complexity.
///
/// Uses pre-computed graph primitives:
/// - `call_cycles_idx()`: SCCs in the call graph with size >= 2
/// - `node_idx()`: node lookup for complexity and file path
pub struct MutualRecursionDetector {
    config: DetectorConfig,
    /// Maximum cycle size to report (skip very large SCCs).
    max_cycle_size: usize,
}

impl MutualRecursionDetector {
    /// Create a new detector with default config.
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            max_cycle_size: 50,
        }
    }

    /// Create with custom config.
    #[allow(dead_code)]
    pub fn with_config(config: DetectorConfig) -> Self {
        let max_cycle_size = config.get_option_or("max_cycle_size", 50);
        Self {
            config,
            max_cycle_size,
        }
    }
}

impl Default for MutualRecursionDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for MutualRecursionDetector {
    fn name(&self) -> &'static str {
        "MutualRecursionDetector"
    }

    fn description(&self) -> &'static str {
        "Detects mutual recursion (call cycles) between functions"
    }

    fn category(&self) -> &'static str {
        "code_smell"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detector_scope(&self) -> DetectorScope {
        DetectorScope::GraphWide
    }

    fn is_deterministic(&self) -> bool {
        true
    }

    fn detect(&self, ctx: &crate::detectors::analysis_context::AnalysisContext) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let gi = graph.interner();

        let cycles = graph.call_cycles_idx();

        if cycles.is_empty() {
            return Ok(vec![]);
        }

        debug!(
            "MutualRecursionDetector: examining {} call cycles",
            cycles.len()
        );

        let mut findings = Vec::new();

        for cycle in cycles {
            if cycle.len() > self.max_cycle_size {
                debug!(
                    "Skipping large call cycle with {} functions (max: {})",
                    cycle.len(),
                    self.max_cycle_size
                );
                continue;
            }

            // Sum complexity across all functions in the cycle.
            let total_complexity: u32 = cycle
                .iter()
                .filter_map(|&idx| graph.node_idx(idx))
                .map(|n| n.complexity as u32)
                .sum();

            let cycle_size = cycle.len();

            // Severity based on cycle size and aggregate complexity.
            let severity = if cycle_size > 5 || total_complexity > 30 {
                Severity::High
            } else if cycle_size > 2 {
                Severity::Medium
            } else {
                Severity::Low
            };

            // Collect function names and unique file paths.
            let mut func_names = Vec::new();
            let mut affected_files: HashSet<PathBuf> = HashSet::new();

            for &idx in cycle {
                if let Some(node) = graph.node_idx(idx) {
                    func_names.push(node.qn(gi).to_string());
                    affected_files.insert(PathBuf::from(node.path(gi)));
                }
            }

            let cycle_display = if func_names.len() <= 8 {
                func_names.join(" -> ")
            } else {
                let first_few: Vec<&str> = func_names.iter().take(6).map(|s| s.as_str()).collect();
                format!("{} ... (+{} more)", first_few.join(" -> "), func_names.len() - 6)
            };

            let description = format!(
                "Mutual recursion detected: {} functions form a call cycle. \
                 Cycle: {}. Aggregate complexity: {}.",
                cycle_size, cycle_display, total_complexity,
            );

            findings.push(Finding {
                id: String::new(),
                detector: "mutual-recursion".to_string(),
                severity,
                confidence: Some(0.95),
                deterministic: true, // Graph-theoretic: Tarjan SCC is mathematically provable
                title: format!(
                    "Mutual recursion: {} functions in call cycle (complexity {})",
                    cycle_size, total_complexity,
                ),
                description,
                affected_files: affected_files.into_iter().collect(),
                line_start: cycle
                    .first()
                    .and_then(|&idx| graph.node_idx(idx))
                    .map(|n| n.line_start),
                line_end: None,
                suggested_fix: Some(if cycle_size == 2 {
                    "Consider refactoring to eliminate direct mutual calls. \
                     Common strategies: merge the two functions, use a callback parameter, \
                     or introduce a shared data structure that both functions operate on."
                        .to_string()
                } else {
                    "Break the cycle by extracting shared logic into a common helper, \
                     introducing an event system, or restructuring the call chain \
                     to be unidirectional."
                        .to_string()
                }),
                estimated_effort: Some(if cycle_size > 5 {
                    "Large (1-3 days)".to_string()
                } else if cycle_size > 2 {
                    "Medium (4-8 hours)".to_string()
                } else {
                    "Small (1-4 hours)".to_string()
                }),
                category: Some("code_smell".to_string()),
                why_it_matters: Some(
                    "Mutual recursion creates tight coupling between functions, making them \
                     impossible to understand, test, or refactor independently. It can also \
                     cause stack overflows if the recursion depth is unbounded."
                        .to_string(),
                ),
                ..Default::default()
            });
        }

        // Sort by severity (highest first).
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        debug!(
            "MutualRecursionDetector found {} findings",
            findings.len()
        );

        Ok(findings)
    }
}

impl super::RegisteredDetector for MutualRecursionDetector {
    fn create(init: &super::DetectorInit) -> Arc<dyn Detector> {
        Arc::new(Self::with_config(init.config_for("MutualRecursionDetector")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeEdge, CodeNode, GraphBuilder};

    #[test]
    fn test_detects_mutual_recursion_pair() {
        // f1 calls f2, f2 calls f1.
        let mut builder = GraphBuilder::new();

        let mut f1_node = CodeNode::function("f1", "a.py");
        f1_node.complexity = 5;
        let mut f2_node = CodeNode::function("f2", "a.py");
        f2_node.complexity = 3;

        let f1 = builder.add_node(f1_node);
        let f2 = builder.add_node(f2_node);

        builder.add_edge(f1, f2, CodeEdge::calls());
        builder.add_edge(f2, f1, CodeEdge::calls());

        let graph = builder.freeze();
        let detector = MutualRecursionDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert_eq!(findings.len(), 1, "Should detect exactly one call cycle");
        assert_eq!(findings[0].severity, Severity::Low, "Pair should be Low severity");
        assert!(
            findings[0].description.contains("2 functions"),
            "Should mention 2 functions: {}",
            findings[0].description
        );
    }

    #[test]
    fn test_detects_triangle_recursion() {
        // f1 -> f2 -> f3 -> f1
        let mut builder = GraphBuilder::new();

        let f1 = builder.add_node(CodeNode::function("f1", "a.py"));
        let f2 = builder.add_node(CodeNode::function("f2", "a.py"));
        let f3 = builder.add_node(CodeNode::function("f3", "a.py"));

        builder.add_edge(f1, f2, CodeEdge::calls());
        builder.add_edge(f2, f3, CodeEdge::calls());
        builder.add_edge(f3, f1, CodeEdge::calls());

        let graph = builder.freeze();
        let detector = MutualRecursionDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert_eq!(findings.len(), 1, "Should detect exactly one call cycle");
        assert_eq!(findings[0].severity, Severity::Medium, "Triangle should be Medium");
    }

    #[test]
    fn test_no_cycle_in_dag() {
        // f1 -> f2 -> f3 (no back-edge)
        let mut builder = GraphBuilder::new();

        let f1 = builder.add_node(CodeNode::function("f1", "a.py"));
        let f2 = builder.add_node(CodeNode::function("f2", "a.py"));
        let f3 = builder.add_node(CodeNode::function("f3", "a.py"));

        builder.add_edge(f1, f2, CodeEdge::calls());
        builder.add_edge(f2, f3, CodeEdge::calls());

        let graph = builder.freeze();
        let detector = MutualRecursionDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(findings.is_empty(), "DAG should have no mutual recursion");
    }

    #[test]
    fn test_skips_large_cycle() {
        // Create a cycle of 6 functions but set max_cycle_size=3.
        let mut builder = GraphBuilder::new();

        let mut nodes = Vec::new();
        for i in 0..6 {
            let n = builder.add_node(CodeNode::function(
                &format!("f{}", i),
                "big.py",
            ));
            nodes.push(n);
        }
        for i in 0..6 {
            builder.add_edge(nodes[i], nodes[(i + 1) % 6], CodeEdge::calls());
        }

        let graph = builder.freeze();
        let config = DetectorConfig::new()
            .with_option("max_cycle_size", serde_json::json!(3));
        let detector = MutualRecursionDetector::with_config(config);

        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Should skip cycle larger than max_cycle_size"
        );
    }

    #[test]
    fn test_high_severity_for_large_or_complex_cycle() {
        // 6-function cycle should be High severity.
        let mut builder = GraphBuilder::new();

        let mut nodes = Vec::new();
        for i in 0..6 {
            let mut n = CodeNode::function(&format!("f{}", i), "complex.py");
            n.complexity = 3;
            nodes.push(builder.add_node(n));
        }
        for i in 0..6 {
            builder.add_edge(nodes[i], nodes[(i + 1) % 6], CodeEdge::calls());
        }

        let graph = builder.freeze();
        let detector = MutualRecursionDetector::new();

        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].severity, Severity::High);
    }

    #[test]
    fn test_empty_graph() {
        let builder = GraphBuilder::new();
        let graph = builder.freeze();
        let detector = MutualRecursionDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(findings.is_empty());
    }

    #[test]
    fn test_scope_is_graph_wide() {
        let detector = MutualRecursionDetector::new();
        assert_eq!(detector.detector_scope(), DetectorScope::GraphWide);
    }

    #[test]
    fn test_category_is_code_smell() {
        let detector = MutualRecursionDetector::new();
        assert_eq!(detector.category(), "code_smell");
    }
}
