//! Structural bridge risk detector using articulation points.
//!
//! Identifies nodes whose removal would disconnect the call/import graph
//! into separate components. These are structural bridges — removing or
//! breaking them partitions the codebase into isolated clusters.

use crate::detectors::base::{Detector, DetectorConfig, DetectorScope};
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use std::sync::Arc;
use tracing::debug;

/// Detects articulation points (bridge nodes) in the code graph.
///
/// An articulation point is a node whose removal disconnects the graph
/// into two or more components. This detector reports nodes where both
/// resulting components are non-trivial (above `min_component_size`),
/// indicating a fragile structural dependency.
///
/// Uses pre-computed graph primitives:
/// - `articulation_points_idx()`: all articulation points
/// - `separation_sizes_idx()`: component sizes after removal
pub struct StructuralBridgeRiskDetector {
    config: DetectorConfig,
    /// Minimum component size (smallest side) to trigger a finding.
    min_component_size: usize,
}

impl StructuralBridgeRiskDetector {
    /// Create a new detector with default config.
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            min_component_size: 10,
        }
    }

    /// Create with custom config.
    #[allow(dead_code)]
    pub fn with_config(config: DetectorConfig) -> Self {
        let min_component_size = config.get_option_or("min_component_size", 10);
        Self {
            config,
            min_component_size,
        }
    }
}

impl Default for StructuralBridgeRiskDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for StructuralBridgeRiskDetector {
    fn name(&self) -> &'static str {
        "StructuralBridgeRiskDetector"
    }

    fn description(&self) -> &'static str {
        "Detects articulation points whose removal would split the code graph \
         into disconnected components"
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

        let aps = graph.articulation_points_idx();

        if aps.is_empty() {
            return Ok(vec![]);
        }

        debug!(
            "StructuralBridgeRiskDetector: examining {} articulation points",
            aps.len()
        );

        let mut findings = Vec::new();

        for &ap_idx in aps {
            let sizes = match graph.separation_sizes_idx(ap_idx) {
                Some(s) => s,
                None => continue,
            };

            // Skip if no component information or components are trivial.
            if sizes.is_empty() {
                continue;
            }

            let smallest = *sizes.iter().min().unwrap_or(&0);
            if smallest < self.min_component_size {
                continue;
            }

            let node = match graph.node_idx(ap_idx) {
                Some(n) => n,
                None => continue,
            };

            let func_name = node.qn(gi);
            let file_path = node.path(gi);

            // Severity based on component sizes.
            let severity = if sizes.iter().all(|&s| s > 100) {
                Severity::Critical
            } else if sizes.iter().all(|&s| s > 30) {
                Severity::High
            } else {
                Severity::Medium
            };

            let sizes_display: Vec<String> = sizes.iter().map(|s| s.to_string()).collect();

            let description = format!(
                "`{}` is a structural bridge. Removing it would split the graph into \
                 components of sizes [{}]. All communication between these components \
                 currently flows through this single node.",
                func_name,
                sizes_display.join(", "),
            );

            findings.push(Finding {
                id: String::new(),
                detector: "structural-bridge-risk".to_string(),
                severity,
                confidence: Some(0.95), // Graph-theoretic: articulation points are mathematically provable
                title: format!(
                    "Structural bridge: `{}` separates components of [{}]",
                    func_name,
                    sizes_display.join(", "),
                ),
                description,
                affected_files: vec![PathBuf::from(file_path)],
                line_start: Some(node.line_start),
                line_end: Some(node.line_end),
                suggested_fix: Some(
                    "Reduce coupling through this node by introducing alternative call paths, \
                     extracting shared interfaces, or splitting the bridge function into \
                     independently accessible units."
                        .to_string(),
                ),
                estimated_effort: Some(if sizes.iter().all(|&s| s > 100) {
                    "Large (3-5 days)".to_string()
                } else if sizes.iter().all(|&s| s > 30) {
                    "Medium (1-3 days)".to_string()
                } else {
                    "Small (4-8 hours)".to_string()
                }),
                category: Some("architecture".to_string()),
                why_it_matters: Some(
                    "A structural bridge is a single node whose failure disconnects entire \
                     subsystems. This creates fragile architecture where a bug, API change, \
                     or refactor in one function can cascade to all dependent components."
                        .to_string(),
                ),
                ..Default::default()
            });
        }

        // Sort by severity (highest first).
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        debug!(
            "StructuralBridgeRiskDetector found {} findings",
            findings.len()
        );

        Ok(findings)
    }
}

impl super::RegisteredDetector for StructuralBridgeRiskDetector {
    fn create(init: &super::DetectorInit) -> Arc<dyn Detector> {
        Arc::new(Self::with_config(init.config_for("StructuralBridgeRiskDetector")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeEdge, CodeNode, GraphBuilder};

    /// Build a graph with two clusters connected by a single bridge node.
    ///
    /// Cluster A: a1-a2-a3 (fully connected)
    /// Cluster B: b1-b2-b3 (fully connected)
    /// Bridge: a3 <-> b1
    ///
    /// a3 and b1 should be articulation points.
    fn build_bridge_graph() -> crate::graph::CodeGraph {
        let mut builder = GraphBuilder::new();

        let a1 = builder.add_node(CodeNode::function("a1", "cluster_a.py"));
        let a2 = builder.add_node(CodeNode::function("a2", "cluster_a.py"));
        let a3 = builder.add_node(CodeNode::function("a3", "cluster_a.py"));
        let b1 = builder.add_node(CodeNode::function("b1", "cluster_b.py"));
        let b2 = builder.add_node(CodeNode::function("b2", "cluster_b.py"));
        let b3 = builder.add_node(CodeNode::function("b3", "cluster_b.py"));

        // Cluster A (fully connected)
        builder.add_edge(a1, a2, CodeEdge::calls());
        builder.add_edge(a2, a1, CodeEdge::calls());
        builder.add_edge(a2, a3, CodeEdge::calls());
        builder.add_edge(a3, a2, CodeEdge::calls());
        builder.add_edge(a1, a3, CodeEdge::calls());
        builder.add_edge(a3, a1, CodeEdge::calls());

        // Bridge
        builder.add_edge(a3, b1, CodeEdge::calls());
        builder.add_edge(b1, a3, CodeEdge::calls());

        // Cluster B (fully connected)
        builder.add_edge(b1, b2, CodeEdge::calls());
        builder.add_edge(b2, b1, CodeEdge::calls());
        builder.add_edge(b2, b3, CodeEdge::calls());
        builder.add_edge(b3, b2, CodeEdge::calls());
        builder.add_edge(b1, b3, CodeEdge::calls());
        builder.add_edge(b3, b1, CodeEdge::calls());

        builder.freeze()
    }

    #[test]
    fn test_detects_bridge_above_threshold() {
        let graph = build_bridge_graph();

        // With min_component_size=2, the bridge nodes should be detected.
        let config = DetectorConfig::new()
            .with_option("min_component_size", serde_json::json!(2));
        let detector = StructuralBridgeRiskDetector::with_config(config);

        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            !findings.is_empty(),
            "Should detect articulation points with component sizes >= 2"
        );

        let has_bridge_finding = findings.iter().any(|f| {
            f.description.contains("structural bridge")
        });
        assert!(
            has_bridge_finding,
            "Should describe the node as a structural bridge: {:?}",
            findings.iter().map(|f| &f.description).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_skips_below_threshold() {
        let graph = build_bridge_graph();

        // With min_component_size=100, nothing should trigger (clusters have 3 nodes each).
        let config = DetectorConfig::new()
            .with_option("min_component_size", serde_json::json!(100));
        let detector = StructuralBridgeRiskDetector::with_config(config);

        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Should not detect anything with high threshold"
        );
    }

    #[test]
    fn test_no_bridge_in_fully_connected() {
        // Fully connected graph of 4 nodes — no articulation points.
        let mut builder = GraphBuilder::new();

        let a = builder.add_node(CodeNode::function("a", "x.py"));
        let b = builder.add_node(CodeNode::function("b", "x.py"));
        let c = builder.add_node(CodeNode::function("c", "x.py"));
        let d = builder.add_node(CodeNode::function("d", "x.py"));

        builder.add_edge(a, b, CodeEdge::calls());
        builder.add_edge(b, a, CodeEdge::calls());
        builder.add_edge(a, c, CodeEdge::calls());
        builder.add_edge(c, a, CodeEdge::calls());
        builder.add_edge(a, d, CodeEdge::calls());
        builder.add_edge(d, a, CodeEdge::calls());
        builder.add_edge(b, c, CodeEdge::calls());
        builder.add_edge(c, b, CodeEdge::calls());
        builder.add_edge(b, d, CodeEdge::calls());
        builder.add_edge(d, b, CodeEdge::calls());
        builder.add_edge(c, d, CodeEdge::calls());
        builder.add_edge(d, c, CodeEdge::calls());

        let graph = builder.freeze();
        let config = DetectorConfig::new()
            .with_option("min_component_size", serde_json::json!(1));
        let detector = StructuralBridgeRiskDetector::with_config(config);

        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Fully connected graph should have no bridge findings"
        );
    }

    #[test]
    fn test_empty_graph() {
        let builder = GraphBuilder::new();
        let graph = builder.freeze();
        let detector = StructuralBridgeRiskDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(findings.is_empty());
    }

    #[test]
    fn test_scope_is_graph_wide() {
        let detector = StructuralBridgeRiskDetector::new();
        assert_eq!(detector.detector_scope(), DetectorScope::GraphWide);
    }

    #[test]
    fn test_category_is_architecture() {
        let detector = StructuralBridgeRiskDetector::new();
        assert_eq!(detector.category(), "architecture");
    }
}
