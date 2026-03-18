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
    /// Amplification factor: weighted_bw must exceed this multiple of unweighted_bw for High severity.
    amplification_factor: f64,
}

impl TemporalBottleneckDetector {
    /// Create a new detector with default config.
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            percentile_threshold: 95,
            amplification_factor: 2.0,
        }
    }

    /// Create with custom config.
    #[allow(dead_code)]
    pub fn with_config(config: DetectorConfig) -> Self {
        let percentile_threshold = config.get_option_or("percentile_threshold", 95);
        let amplification_factor = config.get_option_or("amplification_factor", 2.0);
        Self {
            config,
            percentile_threshold,
            amplification_factor,
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

        // Step 4: Sort values, compute p95 and p99 thresholds.
        let mut sorted_values: Vec<f64> = entries.iter().map(|&(_, w)| w).collect();
        sorted_values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

        let n = sorted_values.len();
        let p_threshold_idx = (n * self.percentile_threshold) / 100;
        let p99_idx = (n * 99) / 100;
        let p_threshold_value = sorted_values[p_threshold_idx.min(n - 1)];
        let p99_value = sorted_values[p99_idx.min(n - 1)];

        debug!(
            "TemporalBottleneckDetector: {} functions, p{} threshold={:.6}, p99={:.6}",
            n, self.percentile_threshold, p_threshold_value, p99_value
        );

        // Step 5: Build findings for functions above the percentile threshold.
        let mut findings = Vec::new();

        for &(func_idx, weighted_bw) in &entries {
            if weighted_bw <= p_threshold_value {
                continue;
            }

            let node = match graph.node_idx(func_idx) {
                Some(n) => n,
                None => continue,
            };

            let func_name = node.qn(gi);
            let file_path = node.path(gi);

            // Compute the function's percentile rank.
            let rank = sorted_values
                .partition_point(|&v| v < weighted_bw);
            let percentile = (rank * 100) / n;

            let unweighted_bw = graph.betweenness_idx(func_idx);
            let ratio = weighted_bw / unweighted_bw.max(0.001);

            if weighted_bw > p99_value
                && weighted_bw > self.amplification_factor * unweighted_bw.max(0.001)
            {
                // High severity: temporal bottleneck amplified vs structural
                findings.push(Finding {
                    id: String::new(),
                    detector: "temporal-bottleneck".to_string(),
                    severity: Severity::High,
                    confidence: Some(0.85),
                    deterministic: true,
                    title: format!(
                        "Temporal bottleneck: {} (p{}, {:.1}\u{00d7} structural)",
                        func_name, percentile, ratio
                    ),
                    description: format!(
                        "`{}` is on the critical path of change propagation \
                         (weighted betweenness: p{}). Changes here cascade at {:.1}\u{00d7} \
                         the rate suggested by static structure.",
                        func_name, percentile, ratio
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
                // Medium severity: high weighted betweenness but not amplified
                findings.push(Finding {
                    id: String::new(),
                    detector: "temporal-bottleneck".to_string(),
                    severity: Severity::Medium,
                    confidence: Some(0.80),
                    deterministic: true,
                    title: format!(
                        "Temporal bottleneck: {} (p{} weighted betweenness)",
                        func_name, percentile
                    ),
                    description: format!(
                        "`{}` has high weighted betweenness centrality, indicating it sits \
                         on frequently-used change propagation paths.",
                        func_name
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
}
