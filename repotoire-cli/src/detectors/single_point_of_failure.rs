//! Single point of failure detector using dominator trees and PageRank.
//!
//! Identifies functions that dominate a large portion of the call graph,
//! meaning all paths to those dominated functions must pass through the
//! single point of failure. Combines domination count with PageRank
//! importance to assess severity.

use crate::detectors::base::{Detector, DetectorConfig, DetectorScope};
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::Arc;
/// Detects functions that are single points of failure in the call graph.
///
/// A function is a single point of failure when it dominates many other
/// functions — meaning all call paths to those functions must pass through
/// the dominator. Removing or breaking such a function would disconnect a
/// large portion of the codebase.
///
/// Uses pre-computed graph primitives:
/// - `dominated_by_idx()`: functions transitively dominated by this node
/// - `page_rank_idx()`: importance score for percentile ranking
/// - `domination_frontier_idx()`: blast radius boundary nodes
pub struct SinglePointOfFailureDetector {
    config: DetectorConfig,
    /// Minimum number of dominated functions to trigger a finding.
    min_dominated: usize,
}

impl SinglePointOfFailureDetector {
    /// Create a new detector with default config.
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            min_dominated: 20,
        }
    }

    /// Create with custom config.
    #[allow(dead_code)]
    pub fn with_config(config: DetectorConfig) -> Self {
        let min_dominated = config.get_option_or("min_dominated", 20);
        Self {
            config,
            min_dominated,
        }
    }
}

impl Default for SinglePointOfFailureDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for SinglePointOfFailureDetector {
    fn name(&self) -> &'static str {
        "SinglePointOfFailureDetector"
    }

    fn description(&self) -> &'static str {
        "Detects functions that dominate a large portion of the call graph, \
         creating single points of failure"
    }

    fn category(&self) -> &'static str {
        "architecture"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detector_scope(&self) -> DetectorScope {
        DetectorScope::GraphWide
    }

    fn detect(&self, ctx: &crate::detectors::analysis_context::AnalysisContext) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let gi = graph.interner();

        let functions = graph.functions_idx();
        let total_functions = functions.len();

        if total_functions == 0 {
            return Ok(vec![]);
        }

        // Collect all PageRank values for percentile computation.
        let mut all_ranks: Vec<f64> = functions
            .iter()
            .map(|&idx| graph.page_rank_idx(idx))
            .collect();
        all_ranks.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

        let mut findings = Vec::new();

        for &func_idx in functions {
            let dominated = graph.dominated_by_idx(func_idx);
            let dom_count = dominated.len();

            if dom_count < self.min_dominated {
                continue;
            }

            let node = match graph.node_idx(func_idx) {
                Some(n) => n,
                None => continue,
            };

            let func_name = node.qn(gi);
            let page_rank = graph.page_rank_idx(func_idx);
            let frontier = graph.domination_frontier_idx(func_idx);

            // Compute PageRank percentile.
            let rank_pos = all_ranks
                .partition_point(|&r| r < page_rank);
            let percentile = if all_ranks.is_empty() {
                0.0
            } else {
                (rank_pos as f64 / all_ranks.len() as f64) * 100.0
            };

            let dom_pct = (dom_count as f64 / total_functions as f64) * 100.0;

            // Calculate severity.
            let severity = if dom_pct > 20.0 && percentile >= 99.0 {
                Severity::Critical
            } else if dom_pct > 10.0 || (percentile >= 95.0 && dom_pct > 5.0) {
                Severity::High
            } else {
                Severity::Medium
            };

            // Collect frontier names (up to 5 for the message).
            let frontier_names: Vec<&str> = frontier
                .iter()
                .filter_map(|&idx| graph.node_idx(idx).map(|n| n.qn(gi)))
                .take(5)
                .collect();

            let frontier_display = if frontier_names.is_empty() {
                String::from("(none)")
            } else if frontier.len() > 5 {
                format!(
                    "{} (+{} more)",
                    frontier_names.join(", "),
                    frontier.len() - 5
                )
            } else {
                frontier_names.join(", ")
            };

            // Collect unique file paths from dominated nodes.
            let mut affected: HashSet<PathBuf> = HashSet::new();
            // Include the function's own file.
            affected.insert(PathBuf::from(node.path(gi)));
            for &dom_idx in dominated {
                if let Some(dom_node) = graph.node_idx(dom_idx) {
                    affected.insert(PathBuf::from(dom_node.path(gi)));
                }
            }

            let description = format!(
                "`{}` dominates {} of {} functions ({:.1}%). \
                 PageRank percentile: {:.0}%. \
                 Blast radius boundary: {}",
                func_name,
                dom_count,
                total_functions,
                dom_pct,
                percentile,
                frontier_display,
            );

            findings.push(Finding {
                id: String::new(),
                detector: "single-point-of-failure".to_string(),
                severity,
                confidence: Some(0.95), // Graph-theoretic: dominator tree is mathematically provable
                title: format!(
                    "Single point of failure: `{}` dominates {} functions ({:.0}%)",
                    func_name, dom_count, dom_pct
                ),
                description,
                affected_files: affected.into_iter().collect(),
                line_start: Some(node.line_start),
                line_end: Some(node.line_end),
                suggested_fix: Some(
                    "Consider splitting this function into smaller, independently callable units. \
                     Extract sub-functionality behind interfaces so callers have alternative paths."
                        .to_string(),
                ),
                estimated_effort: Some(if dom_pct > 20.0 {
                    "Large (2-5 days)".to_string()
                } else if dom_pct > 10.0 {
                    "Medium (1-2 days)".to_string()
                } else {
                    "Small (4-8 hours)".to_string()
                }),
                category: Some("architecture".to_string()),
                why_it_matters: Some(
                    "A single point of failure means all dependent code paths funnel through one function. \
                     If this function has a bug, performance issue, or API change, the blast radius \
                     is disproportionately large."
                        .to_string(),
                ),
                ..Default::default()
            });
        }

        // Sort by severity (highest first), then by domination count.
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        Ok(findings)
    }
}

impl super::RegisteredDetector for SinglePointOfFailureDetector {
    fn create(init: &super::DetectorInit) -> Arc<dyn Detector> {
        Arc::new(Self::with_config(init.config_for("SinglePointOfFailureDetector")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeEdge, CodeNode, GraphBuilder};

    /// Build a graph: entry -> auth -> handler -> db, entry -> auth -> helpers
    /// Auth should dominate handler, db, and helpers.
    fn build_domination_graph() -> crate::graph::CodeGraph {
        let mut builder = GraphBuilder::new();

        let entry = builder.add_node(CodeNode::function("entry", "main.py"));
        let auth = builder.add_node(CodeNode::function("auth", "auth.py"));
        let handler = builder.add_node(CodeNode::function("handler", "handler.py"));
        let db = builder.add_node(CodeNode::function("db", "db.py"));
        let helpers = builder.add_node(CodeNode::function("helpers", "helpers.py"));

        builder.add_edge(entry, auth, CodeEdge::calls());
        builder.add_edge(auth, handler, CodeEdge::calls());
        builder.add_edge(handler, db, CodeEdge::calls());
        builder.add_edge(auth, helpers, CodeEdge::calls());

        builder.freeze()
    }

    #[test]
    fn test_detects_dominator_above_threshold() {
        let graph = build_domination_graph();

        // With min_dominated=2, auth dominates handler+db+helpers (3 nodes).
        let config = DetectorConfig::new()
            .with_option("min_dominated", serde_json::json!(2));
        let detector = SinglePointOfFailureDetector::with_config(config);

        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        // auth should be detected (dominates 3), entry should also (dominates 4).
        assert!(
            !findings.is_empty(),
            "Should detect at least one single point of failure"
        );

        let detectors: Vec<&str> = findings
            .iter()
            .map(|f| f.description.as_str())
            .collect();

        // At least one finding should mention "auth" or "entry" dominating.
        let has_domination = findings.iter().any(|f| {
            f.description.contains("dominates")
        });
        assert!(has_domination, "Findings should describe domination: {:?}", detectors);
    }

    #[test]
    fn test_skips_below_threshold() {
        let graph = build_domination_graph();

        // With min_dominated=100, nothing should trigger.
        let config = DetectorConfig::new()
            .with_option("min_dominated", serde_json::json!(100));
        let detector = SinglePointOfFailureDetector::with_config(config);

        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Should not detect anything with high threshold"
        );
    }

    #[test]
    fn test_empty_graph() {
        let builder = GraphBuilder::new();
        let graph = builder.freeze();
        let detector = SinglePointOfFailureDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(findings.is_empty());
    }

    #[test]
    fn test_scope_is_graph_wide() {
        let detector = SinglePointOfFailureDetector::new();
        assert_eq!(detector.detector_scope(), DetectorScope::GraphWide);
    }

    #[test]
    fn test_category_is_architecture() {
        let detector = SinglePointOfFailureDetector::new();
        assert_eq!(detector.category(), "architecture");
    }
}
