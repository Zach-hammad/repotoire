//! Temporal bottleneck detector using weighted betweenness centrality.
//!
//! Identifies functions with high weighted betweenness centrality, meaning they
//! sit on the critical paths of change propagation. When weighted betweenness
//! significantly exceeds unweighted (structural) betweenness, the function acts
//! as a temporal amplifier: changes cascade through it at a rate beyond what
//! static structure would predict.

use crate::detectors::base::{Detector, DetectorConfig, DetectorScope};
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use std::sync::Arc;
use tracing::debug;

/// Detects functions with high weighted betweenness — on critical paths of change propagation.
///
/// Functions with high weighted betweenness centrality sit on frequently-used
/// change propagation paths. When this significantly exceeds structural betweenness,
/// it signals a temporal bottleneck: changes cascade through the function at a
/// rate beyond what static call-graph structure would suggest.
///
/// Uses pre-computed graph primitives:
/// - `functions_idx()`: all function NodeIndexes
/// - `weighted_betweenness_idx()`: change-weighted betweenness centrality
/// - `betweenness_idx()`: structural (unweighted) betweenness centrality
/// - `node_idx()`: node lookup for function names and file paths
pub struct TemporalBottleneckDetector {
    config: DetectorConfig,
    /// Percentile threshold above which a function is flagged (0-100).
    percentile_threshold: usize,
}

impl TemporalBottleneckDetector {
    /// Create a new detector with default config.
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            percentile_threshold: 97,
        }
    }

    /// Create with custom config.
    #[allow(dead_code)]
    pub fn with_config(config: DetectorConfig) -> Self {
        let percentile_threshold = config.get_option_or("percentile_threshold", 97);
        Self {
            config,
            percentile_threshold,
        }
    }
}

impl Default for TemporalBottleneckDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for TemporalBottleneckDetector {
    fn name(&self) -> &'static str {
        "TemporalBottleneckDetector"
    }

    fn description(&self) -> &'static str {
        "Detects functions on the critical path of change propagation via weighted betweenness"
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

        // Step 1-2: Collect weighted betweenness for all functions.
        let entries: Vec<(petgraph::graph::NodeIndex, f64)> = functions
            .iter()
            .map(|&idx| {
                let wbw = graph.weighted_betweenness_idx(idx);
                (idx, wbw)
            })
            .collect();

        // Step 3: If all weighted betweenness values are 0.0, no co-change data — bail.
        let all_zero = entries.iter().all(|&(_, w)| w == 0.0);
        if all_zero {
            debug!("TemporalBottleneckDetector: all weighted betweenness values are 0.0, skipping");
            return Ok(vec![]);
        }

        let n = entries.len();

        // Step 4: Compute percentile ranks for weighted betweenness.
        let mut weighted_order: Vec<usize> = (0..n).collect();
        weighted_order.sort_by(|&a, &b| {
            entries[a]
                .1
                .partial_cmp(&entries[b].1)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        let mut weighted_percentiles = vec![0usize; n];
        for (rank, &orig_idx) in weighted_order.iter().enumerate() {
            weighted_percentiles[orig_idx] = (rank * 100) / n;
        }

        // Compute percentile ranks for unweighted (structural) betweenness.
        let unweighted_entries: Vec<f64> = entries
            .iter()
            .map(|&(idx, _)| graph.betweenness_idx(idx))
            .collect();
        let mut unweighted_order: Vec<usize> = (0..n).collect();
        unweighted_order.sort_by(|&a, &b| {
            unweighted_entries[a]
                .partial_cmp(&unweighted_entries[b])
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        let mut unweighted_percentiles = vec![0usize; n];
        for (rank, &orig_idx) in unweighted_order.iter().enumerate() {
            unweighted_percentiles[orig_idx] = (rank * 100) / n;
        }

        let p95_threshold = self.percentile_threshold;

        debug!(
            "TemporalBottleneckDetector: {} functions, p{} threshold (percentile-based)",
            n, p95_threshold
        );

        // Step 5: Build findings for functions above the percentile threshold.
        let mut findings = Vec::new();

        for (i, &(func_idx, _weighted_bw)) in entries.iter().enumerate() {
            let weighted_pct = weighted_percentiles[i];
            let unweighted_pct = unweighted_percentiles[i];

            if weighted_pct < p95_threshold {
                continue;
            }

            let node = match graph.node_idx(func_idx) {
                Some(n) => n,
                None => continue,
            };

            let func_name = node.qn(gi);
            let file_path = node.path(gi);

            let pct_gap = weighted_pct as isize - unweighted_pct as isize;

            if weighted_pct > 99 && pct_gap > 30 {
                // High severity: much more important temporally than structurally
                findings.push(Finding {
                    id: String::new(),
                    detector: "temporal-bottleneck".to_string(),
                    severity: Severity::High,
                    confidence: Some(0.85),
                    deterministic: true,
                    title: format!(
                        "Temporal bottleneck: {} (weighted p{}, structural p{}, +{} gap)",
                        func_name, weighted_pct, unweighted_pct, pct_gap
                    ),
                    description: format!(
                        "`{}` ranks at p{} by change-weighted betweenness but only p{} by \
                         structural betweenness (+{} percentile gap). Changes cascade through \
                         this function far more than static structure would predict.",
                        func_name, weighted_pct, unweighted_pct, pct_gap
                    ),
                    affected_files: vec![PathBuf::from(file_path)],
                    line_start: Some(node.line_start),
                    line_end: Some(node.line_end),
                    suggested_fix: Some(
                        "Consider splitting this function's responsibilities, introducing \
                         an interface/abstraction layer, or reducing the number of modules \
                         that transitively depend on it through co-change patterns."
                            .to_string(),
                    ),
                    category: Some("architecture".to_string()),
                    why_it_matters: Some(
                        "A temporal bottleneck amplifies change propagation beyond what \
                         static structure suggests. Changes to this function cascade to \
                         far more code than a dependency graph would predict, increasing \
                         the risk of unintended side effects."
                            .to_string(),
                    ),
                    ..Default::default()
                });
            } else {
                // Medium severity: high weighted betweenness
                findings.push(Finding {
                    id: String::new(),
                    detector: "temporal-bottleneck".to_string(),
                    severity: Severity::Medium,
                    confidence: Some(0.80),
                    deterministic: true,
                    title: format!(
                        "Temporal bottleneck: {} (p{} weighted betweenness)",
                        func_name, weighted_pct
                    ),
                    description: format!(
                        "`{}` has high weighted betweenness centrality (p{}), indicating it sits \
                         on frequently-used change propagation paths.",
                        func_name, weighted_pct
                    ),
                    affected_files: vec![PathBuf::from(file_path)],
                    line_start: Some(node.line_start),
                    line_end: Some(node.line_end),
                    suggested_fix: Some(
                        "Monitor this function for cascading changes. Consider whether \
                         its central position in co-change paths reflects a design issue \
                         or a natural architectural role."
                            .to_string(),
                    ),
                    category: Some("architecture".to_string()),
                    why_it_matters: Some(
                        "Functions with high weighted betweenness sit on frequently-used \
                         change propagation paths. Changes to them are more likely to \
                         require coordinated changes in other parts of the codebase."
                            .to_string(),
                    ),
                    ..Default::default()
                });
            }
        }

        // Sort by severity (highest first).
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        debug!(
            "TemporalBottleneckDetector found {} findings",
            findings.len()
        );

        Ok(findings)
    }
}

impl super::RegisteredDetector for TemporalBottleneckDetector {
    fn create(init: &super::DetectorInit) -> Arc<dyn Detector> {
        Arc::new(Self::with_config(
            init.config_for("TemporalBottleneckDetector"),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeEdge, CodeNode, GraphBuilder};

    #[test]
    fn test_no_findings_without_weighted_betweenness() {
        // Graph with no co-change data -> all weighted betweenness is 0.0 -> no findings.
        let mut builder = GraphBuilder::new();

        let f1 = builder.add_node(CodeNode::function("init", "src/main.py"));
        let f2 = builder.add_node(CodeNode::function("handler", "src/api.py"));
        let f3 = builder.add_node(CodeNode::function("query", "src/db.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());
        builder.add_edge(f2, f3, CodeEdge::calls());

        let graph = builder.freeze();
        let detector = TemporalBottleneckDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Should have no findings without co-change data (all weighted betweenness = 0)"
        );
    }

    #[test]
    fn test_scope_and_category() {
        let detector = TemporalBottleneckDetector::new();
        assert_eq!(detector.detector_scope(), DetectorScope::GraphWide);
        assert_eq!(detector.category(), "architecture");
        assert!(detector.is_deterministic());
    }

    #[test]
    fn test_empty_graph() {
        let builder = GraphBuilder::new();
        let graph = builder.freeze();
        let detector = TemporalBottleneckDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(findings.is_empty(), "Empty graph should produce no findings");
    }

    #[test]
    fn test_positive_star_topology_bottleneck() {
        // Build a star topology with 25 leaf nodes and 1 hub. Co-change data
        // amplifies the hub's betweenness. With 26 functions and p97 threshold,
        // only the top ~1 function should be flagged.
        let mut builder = GraphBuilder::new();

        let hub = builder.add_node(CodeNode::function("hub", "src/core/hub.py"));
        let hub_file = builder.add_node(CodeNode::file("src/core/hub.py"));
        builder.add_edge(hub_file, hub, CodeEdge::contains());

        let mut leaf_paths = Vec::new();
        for i in 0..25 {
            let fname = format!("leaf_{i}");
            let path = format!("src/modules/mod{i}.py");
            let leaf = builder.add_node(CodeNode::function(&fname, &path));
            let file = builder.add_node(CodeNode::file(&path));
            builder.add_edge(file, leaf, CodeEdge::contains());
            // Star: every leaf calls the hub
            builder.add_edge(leaf, hub, CodeEdge::calls());
            leaf_paths.push(path);
        }

        // Co-change: hub file co-changes with every leaf file (amplifies betweenness)
        let now = chrono::Utc::now();
        let config = crate::git::co_change::CoChangeConfig {
            min_weight: 0.01,
            ..Default::default()
        };
        let mut commits = Vec::new();
        for leaf_path in &leaf_paths {
            for _ in 0..5 {
                commits.push((
                    now,
                    vec![
                        "src/core/hub.py".to_string(),
                        leaf_path.clone(),
                    ],
                ));
            }
        }

        let co_change =
            crate::git::co_change::CoChangeMatrix::from_commits(&commits, &config, now);
        let graph = builder.freeze_with_co_change(&co_change);

        // Use a lower percentile threshold to make detection more likely
        // with our 26-node graph (p90 = top ~3 nodes).
        let config = DetectorConfig::new()
            .with_option("percentile_threshold", serde_json::json!(90));
        let detector = TemporalBottleneckDetector::with_config(config);
        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            !findings.is_empty(),
            "Star topology with heavy co-change should produce at least one \
             temporal bottleneck finding"
        );
        for f in &findings {
            assert_eq!(f.detector, "temporal-bottleneck");
            assert!(
                f.severity == Severity::Medium || f.severity == Severity::High,
                "Temporal bottleneck findings should be Medium or High severity"
            );
        }
    }
}
