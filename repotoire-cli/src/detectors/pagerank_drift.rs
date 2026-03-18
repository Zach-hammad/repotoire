//! PageRank drift detector comparing static vs weighted PageRank.
//!
//! Identifies functions where the static (structural) PageRank rank diverges
//! significantly from the change-weighted PageRank rank. Large divergence means
//! the mental model of "what's important" based on code structure doesn't match
//! operational reality based on co-change patterns.
//!
//! Two drift patterns:
//! - **Operationally critical, structurally hidden**: function ranks high by
//!   change frequency but low by static structure — a hidden integration point.
//! - **Structurally central, operationally dormant**: function ranks high by
//!   static structure but low by change frequency — stable foundation or dead weight.

use crate::detectors::base::{Detector, DetectorConfig, DetectorScope};
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use std::sync::Arc;
use tracing::debug;

/// Detects functions where static PageRank rank diverges from change-weighted PageRank rank.
///
/// Compares Phase A's unweighted PageRank with Phase B's weighted PageRank.
/// When a function's percentile rank differs by more than `min_percentile_drift`,
/// it signals a mismatch between structural importance and operational importance.
///
/// Uses pre-computed graph primitives:
/// - `functions_idx()`: all function NodeIndexes
/// - `page_rank_idx()`: static (structural) PageRank score
/// - `weighted_page_rank_idx()`: change-weighted PageRank score
/// - `node_idx()`: node lookup for function names and file paths
pub struct PageRankDriftDetector {
    config: DetectorConfig,
    /// Minimum percentile drift to trigger a finding (0-100).
    min_percentile_drift: usize,
}

impl PageRankDriftDetector {
    /// Create a new detector with default config.
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            min_percentile_drift: 60,
        }
    }

    /// Create with custom config.
    #[allow(dead_code)]
    pub fn with_config(config: DetectorConfig) -> Self {
        let min_percentile_drift = config.get_option_or("min_percentile_drift", 60);
        Self {
            config,
            min_percentile_drift,
        }
    }
}

impl Default for PageRankDriftDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for PageRankDriftDetector {
    fn name(&self) -> &'static str {
        "PageRankDriftDetector"
    }

    fn description(&self) -> &'static str {
        "Detects functions where static PageRank rank diverges from change-weighted PageRank rank"
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

    fn is_deterministic(&self) -> bool {
        true
    }

    fn detect(
        &self,
        ctx: &crate::detectors::analysis_context::AnalysisContext,
    ) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let gi = graph.interner();
        let functions = graph.functions_idx();

        if functions.is_empty() {
            return Ok(vec![]);
        }

        // Collect static and weighted PageRank for each function.
        let entries: Vec<(petgraph::graph::NodeIndex, f64, f64)> = functions
            .iter()
            .map(|&idx| {
                let static_pr = graph.page_rank_idx(idx);
                let weighted_pr = graph.weighted_page_rank_idx(idx);
                (idx, static_pr, weighted_pr)
            })
            .collect();

        // If ALL weighted PageRank values are 0.0, there's no co-change data — bail.
        let all_weighted_zero = entries.iter().all(|&(_, _, w)| w == 0.0);
        if all_weighted_zero {
            debug!("PageRankDriftDetector: all weighted PageRank values are 0.0, skipping");
            return Ok(vec![]);
        }

        let n = entries.len();

        // Compute percentile ranks for static PageRank.
        // Sort by static_pr, assign percentile based on position.
        let mut static_order: Vec<usize> = (0..n).collect();
        static_order
            .sort_by(|&a, &b| entries[a].1.partial_cmp(&entries[b].1).unwrap_or(std::cmp::Ordering::Equal));
        let mut static_percentiles = vec![0usize; n];
        for (rank, &orig_idx) in static_order.iter().enumerate() {
            static_percentiles[orig_idx] = (rank * 100) / n;
        }

        // Compute percentile ranks for weighted PageRank.
        let mut weighted_order: Vec<usize> = (0..n).collect();
        weighted_order
            .sort_by(|&a, &b| entries[a].2.partial_cmp(&entries[b].2).unwrap_or(std::cmp::Ordering::Equal));
        let mut weighted_percentiles = vec![0usize; n];
        for (rank, &orig_idx) in weighted_order.iter().enumerate() {
            weighted_percentiles[orig_idx] = (rank * 100) / n;
        }

        debug!(
            "PageRankDriftDetector: examining {} functions with min_percentile_drift={}",
            n, self.min_percentile_drift
        );

        let mut findings = Vec::new();

        for (i, &(func_idx, _static_pr, _weighted_pr)) in entries.iter().enumerate() {
            let static_pct = static_percentiles[i];
            let weighted_pct = weighted_percentiles[i];

            // Skip functions that are unimportant in both distributions
            if static_pct < 50 && weighted_pct < 50 {
                continue;
            }

            let drift = (static_pct as isize - weighted_pct as isize).unsigned_abs();

            if drift <= self.min_percentile_drift {
                continue;
            }

            let node = match graph.node_idx(func_idx) {
                Some(n) => n,
                None => continue,
            };

            let func_name = node.qn(gi);
            let file_path = node.path(gi);

            if weighted_pct > static_pct {
                // Operationally critical, structurally hidden
                findings.push(Finding {
                    id: String::new(),
                    detector: "pagerank-drift".to_string(),
                    severity: Severity::Medium,
                    confidence: Some(0.80),
                    deterministic: true,
                    title: format!(
                        "PageRank drift: {} is operationally critical but structurally hidden",
                        func_name
                    ),
                    description: format!(
                        "`{}` ranks at p{} by change frequency but p{} by static structure. \
                         This function isn't called by much, but it changes with everything \
                         \u{2014} hidden integration point.",
                        func_name, weighted_pct, static_pct
                    ),
                    affected_files: vec![PathBuf::from(file_path)],
                    line_start: Some(node.line_start),
                    line_end: Some(node.line_end),
                    suggested_fix: Some(
                        "Investigate why this function co-changes with so many others despite \
                         low structural centrality. It may encode implicit contracts or shared \
                         assumptions that should be made explicit via interfaces or shared types."
                            .to_string(),
                    ),
                    category: Some("architecture".to_string()),
                    why_it_matters: Some(
                        "A function that is operationally critical but structurally hidden \
                         won't show up in dependency analysis or impact assessment tools. \
                         Developers may underestimate the blast radius of changes to it."
                            .to_string(),
                    ),
                    ..Default::default()
                });
            } else {
                // Structurally central, operationally dormant
                findings.push(Finding {
                    id: String::new(),
                    detector: "pagerank-drift".to_string(),
                    severity: Severity::Medium,
                    confidence: Some(0.80),
                    deterministic: true,
                    title: format!(
                        "PageRank drift: {} is structurally central but operationally dormant",
                        func_name
                    ),
                    description: format!(
                        "`{}` ranks at p{} by static structure but p{} by change frequency. \
                         This is a hub function that rarely changes \u{2014} stable foundation \
                         or dead weight?",
                        func_name, static_pct, weighted_pct
                    ),
                    affected_files: vec![PathBuf::from(file_path)],
                    line_start: Some(node.line_start),
                    line_end: Some(node.line_end),
                    suggested_fix: Some(
                        "If this function is genuinely stable infrastructure, document it as such. \
                         If it's unused legacy code propped up by structural position, consider \
                         removing or replacing it."
                            .to_string(),
                    ),
                    category: Some("architecture".to_string()),
                    why_it_matters: Some(
                        "A structurally central function that never changes operationally may \
                         be dead weight inflating complexity metrics, or it may be stable \
                         infrastructure that deserves documentation and protection."
                            .to_string(),
                    ),
                    ..Default::default()
                });
            }
        }

        // Sort by severity (highest first).
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        debug!("PageRankDriftDetector found {} findings", findings.len());

        Ok(findings)
    }
}

impl super::RegisteredDetector for PageRankDriftDetector {
    fn create(init: &super::DetectorInit) -> Arc<dyn Detector> {
        Arc::new(Self::with_config(init.config_for("PageRankDriftDetector")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeEdge, CodeNode, GraphBuilder};

    #[test]
    fn test_no_findings_without_weighted_pr() {
        // Graph with no co-change data → all weighted PageRank is 0.0 → no findings.
        let mut builder = GraphBuilder::new();

        let f1 = builder.add_node(CodeNode::function("init", "src/main.py"));
        let f2 = builder.add_node(CodeNode::function("handler", "src/api.py"));
        let f3 = builder.add_node(CodeNode::function("query", "src/db.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());
        builder.add_edge(f2, f3, CodeEdge::calls());

        let graph = builder.freeze();
        let detector = PageRankDriftDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Should have no findings without co-change data (all weighted PR = 0)"
        );
    }

    #[test]
    fn test_no_findings_similar_ranks() {
        // Build a graph where static and weighted PageRank produce similar rankings.
        // A linear chain with co-change matching the structural ordering should
        // produce little or no drift.
        let mut builder = GraphBuilder::new();

        let f1 = builder.add_node(CodeNode::function("entry", "src/main.py"));
        let file1 = builder.add_node(CodeNode::file("src/main.py"));
        let f2 = builder.add_node(CodeNode::function("handler", "src/api.py"));
        let file2 = builder.add_node(CodeNode::file("src/api.py"));
        let f3 = builder.add_node(CodeNode::function("query", "src/db.py"));
        let file3 = builder.add_node(CodeNode::file("src/db.py"));

        builder.add_edge(file1, f1, CodeEdge::contains());
        builder.add_edge(file2, f2, CodeEdge::contains());
        builder.add_edge(file3, f3, CodeEdge::contains());
        builder.add_edge(f1, f2, CodeEdge::calls());
        builder.add_edge(f2, f3, CodeEdge::calls());

        // Co-change matches structural: files that call each other also change together.
        let now = chrono::Utc::now();
        let config = crate::git::co_change::CoChangeConfig {
            min_weight: 0.01,
            ..Default::default()
        };
        let commits = vec![
            (
                now,
                vec!["src/main.py".to_string(), "src/api.py".to_string()],
            ),
            (
                now,
                vec!["src/api.py".to_string(), "src/db.py".to_string()],
            ),
        ];
        let co_change =
            crate::git::co_change::CoChangeMatrix::from_commits(&commits, &config, now);

        let graph = builder.freeze_with_co_change(&co_change);

        // Use a high drift threshold so small differences don't trigger.
        let config = DetectorConfig::new()
            .with_option("min_percentile_drift", serde_json::json!(50));
        let detector = PageRankDriftDetector::with_config(config);
        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Should have no findings when static and weighted ranks are similar \
             (with high drift threshold). Got {} findings: {:?}",
            findings.len(),
            findings
                .iter()
                .map(|f| &f.title)
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_scope_and_category() {
        let detector = PageRankDriftDetector::new();
        assert_eq!(detector.detector_scope(), DetectorScope::GraphWide);
        assert_eq!(detector.category(), "architecture");
    }

    #[test]
    fn test_is_deterministic() {
        let detector = PageRankDriftDetector::new();
        assert!(detector.is_deterministic());
    }

    #[test]
    fn test_empty_graph() {
        let builder = GraphBuilder::new();
        let graph = builder.freeze();
        let detector = PageRankDriftDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(findings.is_empty());
    }

    #[test]
    fn test_default_threshold_no_findings_without_co_change() {
        // Uses PageRankDriftDetector::new() (default min_percentile_drift=60)
        // on a graph without co-change data. Should produce no findings because
        // all weighted PageRank values are 0.0 and the detector bails early.
        let mut builder = GraphBuilder::new();

        let f1 = builder.add_node(CodeNode::function("entry", "src/main.py"));
        let file1 = builder.add_node(CodeNode::file("src/main.py"));
        let f2 = builder.add_node(CodeNode::function("handler", "src/api.py"));
        let file2 = builder.add_node(CodeNode::file("src/api.py"));
        let f3 = builder.add_node(CodeNode::function("query", "src/db.py"));
        let file3 = builder.add_node(CodeNode::file("src/db.py"));
        let f4 = builder.add_node(CodeNode::function("validate", "src/validate.py"));
        let file4 = builder.add_node(CodeNode::file("src/validate.py"));

        builder.add_edge(file1, f1, CodeEdge::contains());
        builder.add_edge(file2, f2, CodeEdge::contains());
        builder.add_edge(file3, f3, CodeEdge::contains());
        builder.add_edge(file4, f4, CodeEdge::contains());
        builder.add_edge(f1, f2, CodeEdge::calls());
        builder.add_edge(f2, f3, CodeEdge::calls());
        builder.add_edge(f2, f4, CodeEdge::calls());

        let graph = builder.freeze();

        // Default detector — no custom config, exercises the default threshold of 60
        let detector = PageRankDriftDetector::new();
        assert_eq!(
            detector.min_percentile_drift, 60,
            "Default min_percentile_drift should be 60"
        );

        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Default-threshold detector should produce no findings without co-change data. \
             Got {} findings.",
            findings.len()
        );
    }
}
