//! Critical-path single-owner detector.
//!
//! Fires when a file has bus_factor == 1 AND sits on a critical path in the
//! dependency graph (high PageRank, high betweenness, or is an articulation point).

use crate::detectors::analysis_context::AnalysisContext;
use crate::detectors::base::{
    is_non_production_file, is_test_file, Detector, DetectorConfig, DetectorScope,
};
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use tracing::debug;

/// Detects files that are both single-owner AND on critical dependency paths.
///
/// A file with bus_factor == 1 is risky on its own, but when it also has
/// high centrality (PageRank > P90, betweenness > P90, or is an articulation
/// point), the risk is compounded: many other files depend on code that
/// only one person understands.
pub struct CriticalPathSingleOwnerDetector {
    config: DetectorConfig,
}

impl CriticalPathSingleOwnerDetector {
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
        }
    }

    #[allow(dead_code)]
    pub fn with_config(config: DetectorConfig) -> Self {
        Self { config }
    }
}

impl Default for CriticalPathSingleOwnerDetector {
    fn default() -> Self {
        Self::new()
    }
}

/// Compute the value at a given percentile from a sorted slice.
fn percentile(sorted: &[f64], p: usize) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let idx = ((p as f64 / 100.0) * (sorted.len() as f64 - 1.0)).ceil() as usize;
    sorted[idx.min(sorted.len() - 1)]
}

impl Detector for CriticalPathSingleOwnerDetector {
    fn name(&self) -> &'static str {
        "CriticalPathSingleOwnerDetector"
    }

    fn description(&self) -> &'static str {
        "Detects single-owner files on critical dependency paths"
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

    fn detect(&self, ctx: &AnalysisContext) -> Result<Vec<Finding>> {
        let ownership = match &ctx.ownership {
            Some(o) => o,
            None => return Ok(vec![]),
        };

        let graph = ctx.graph;
        let gi = graph.interner();
        let prims = graph.primitives();
        let centrality_percentile: usize = self.config.get_option_or("centrality_percentile", 90);

        // Build per-file centrality aggregation: max PageRank, max betweenness,
        // and whether any node in the file is an articulation point.
        let mut file_page_rank: HashMap<String, f64> = HashMap::new();
        let mut file_betweenness: HashMap<String, f64> = HashMap::new();
        let mut file_is_artic: HashMap<String, bool> = HashMap::new();

        let node_indices: Vec<_> = graph
            .functions_idx()
            .iter()
            .chain(graph.classes_idx().iter())
            .copied()
            .collect();

        for idx in &node_indices {
            let node = match graph.node_idx(*idx) {
                Some(n) => n,
                None => continue,
            };
            let file_path = gi.resolve(node.file_path).to_string();

            let pg_idx: petgraph::stable_graph::NodeIndex = (*idx).into();
            let pr = prims.page_rank.get(&pg_idx).copied().unwrap_or(0.0);
            let bt = prims.betweenness.get(&pg_idx).copied().unwrap_or(0.0);
            let is_ap = prims.articulation_point_set.contains(&pg_idx);

            let e = file_page_rank.entry(file_path.clone()).or_insert(0.0);
            if pr > *e {
                *e = pr;
            }
            let e = file_betweenness.entry(file_path.clone()).or_insert(0.0);
            if bt > *e {
                *e = bt;
            }
            if is_ap {
                file_is_artic.insert(file_path, true);
            }
        }

        // Compute percentile thresholds
        let mut pr_values: Vec<f64> = file_page_rank.values().copied().collect();
        pr_values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let pr_threshold = percentile(&pr_values, centrality_percentile);

        let mut bt_values: Vec<f64> = file_betweenness.values().copied().collect();
        bt_values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let bt_threshold = percentile(&bt_values, centrality_percentile);

        let mut findings = Vec::new();

        for (path, file_ownership) in &ownership.files {
            let p = std::path::Path::new(path);
            if is_non_production_file(p) || is_test_file(p) {
                continue;
            }
            if file_ownership.bus_factor != 1 {
                continue;
            }

            let pr = file_page_rank.get(path).copied().unwrap_or(0.0);
            let bt = file_betweenness.get(path).copied().unwrap_or(0.0);
            let is_ap = file_is_artic.get(path).copied().unwrap_or(false);

            let is_critical = pr > pr_threshold || bt > bt_threshold || is_ap;
            if !is_critical {
                continue;
            }

            let author = file_ownership
                .authors
                .first()
                .map(|a| a.author.as_str())
                .unwrap_or("unknown");

            let mut reasons = Vec::new();
            if pr > pr_threshold {
                reasons.push(format!("PageRank P{}", centrality_percentile));
            }
            if bt > bt_threshold {
                reasons.push(format!("betweenness P{}", centrality_percentile));
            }
            if is_ap {
                reasons.push("articulation point".to_string());
            }

            findings.push(Finding {
                id: String::new(),
                detector: "critical-path-single-owner".to_string(),
                severity: Severity::Critical,
                confidence: Some(0.95),
                deterministic: true,
                title: format!(
                    "Critical file '{}' has bus factor 1 ({})",
                    path,
                    reasons.join(", ")
                ),
                description: format!(
                    "File '{}' has bus factor 1 (sole author: {}) and is on a critical \
                     dependency path ({}). Many other files depend on this code, \
                     but only one person understands it.",
                    path,
                    author,
                    reasons.join(", ")
                ),
                affected_files: vec![PathBuf::from(path)],
                suggested_fix: Some(
                    "Urgently spread knowledge of this file. Schedule pair-programming \
                     sessions and ensure at least one additional engineer reviews every \
                     change to this file."
                        .to_string(),
                ),
                category: Some("architecture".to_string()),
                why_it_matters: Some(
                    "A single-owner file on a critical path is the highest-impact bus factor \
                     risk. A bug here affects many dependents, and only one person can fix it."
                        .to_string(),
                ),
                ..Default::default()
            });
        }

        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        debug!(
            "CriticalPathSingleOwnerDetector found {} findings",
            findings.len()
        );

        Ok(findings)
    }
}

impl crate::detectors::RegisteredDetector for CriticalPathSingleOwnerDetector {
    fn create(init: &crate::detectors::DetectorInit) -> Arc<dyn Detector> {
        Arc::new(Self::with_config(
            init.config_for("CriticalPathSingleOwnerDetector"),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphBuilder;

    #[test]
    fn test_empty_no_findings() {
        let builder = GraphBuilder::new();
        let graph = builder.freeze();
        let detector = CriticalPathSingleOwnerDetector::new();
        let ctx = AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("should succeed");
        assert!(findings.is_empty());
    }

    #[test]
    fn test_scope_is_graph_wide() {
        let detector = CriticalPathSingleOwnerDetector::new();
        assert_eq!(detector.detector_scope(), DetectorScope::GraphWide);
    }

    #[test]
    fn test_category_is_architecture() {
        let detector = CriticalPathSingleOwnerDetector::new();
        assert_eq!(detector.category(), "architecture");
    }

    #[test]
    fn test_percentile_helper() {
        assert!((percentile(&[], 90) - 0.0).abs() < f64::EPSILON);
        assert!((percentile(&[1.0, 2.0, 3.0, 4.0, 5.0], 90) - 5.0).abs() < f64::EPSILON);
        assert!((percentile(&[1.0], 50) - 1.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_fires_on_critical_single_owner_file() {
        use crate::git::ownership::{FileAuthorDOA, FileOwnershipDOA, OwnershipModel};
        use crate::graph::{CodeEdge, CodeNode};

        // Build a star graph where src/api.rs (the hub) has the highest
        // betweenness centrality. All traffic flows through the hub, while
        // leaf files at the edges have zero betweenness.
        let mut builder = GraphBuilder::new();
        let hub = builder.add_node(CodeNode::function("dispatch", "src/api.rs"));

        // Add 12 leaf files: 6 callers -> hub -> 6 callees
        // This makes the hub the sole bottleneck with maximum betweenness.
        for i in 0..6 {
            let name = format!("caller_{i}");
            let file = format!("src/caller_{i}.rs");
            let node = builder.add_node(CodeNode::function(&name, &file));
            builder.add_edge(node, hub, CodeEdge::calls());
        }
        for i in 0..6 {
            let name = format!("service_{i}");
            let file = format!("src/service_{i}.rs");
            let node = builder.add_node(CodeNode::function(&name, &file));
            builder.add_edge(hub, node, CodeEdge::calls());
        }

        let graph = builder.freeze();

        // Check that primitives actually computed PageRank
        let prims = graph.primitives();
        let has_pagerank = !prims.page_rank.is_empty();

        // Create ownership with bus_factor=1 for src/api.rs
        let mut files = std::collections::HashMap::new();
        files.insert(
            "src/api.rs".to_string(),
            FileOwnershipDOA {
                path: "src/api.rs".into(),
                authors: vec![FileAuthorDOA {
                    author: "alice".into(),
                    email: "alice@example.com".into(),
                    raw_doa: 5.0,
                    normalized_doa: 1.0,
                    is_author: true,
                    is_first_author: true,
                    commit_count: 10,
                    last_active: 0,
                    is_active: true,
                }],
                bus_factor: 1,
                hhi: 1.0,
                max_doa: 1.0,
            },
        );

        let model = OwnershipModel {
            files,
            modules: std::collections::HashMap::new(),
            project_bus_factor: 1,
            author_profiles: std::collections::HashMap::new(),
        };

        let mut ctx = AnalysisContext::test(&graph);
        ctx.ownership = Some(std::sync::Arc::new(model));

        let detector = CriticalPathSingleOwnerDetector::new();
        let findings = detector.detect(&ctx).unwrap();

        // If PageRank was computed and src/api.rs has high centrality, detector should fire
        if has_pagerank {
            // The detector should find src/api.rs as critical (it has 2 functions,
            // both called by main and calling db — high betweenness)
            let api_findings: Vec<_> = findings
                .iter()
                .filter(|f| f.title.contains("src/api.rs"))
                .collect();
            assert!(
                !api_findings.is_empty(),
                "Expected finding for src/api.rs (bus_factor=1, high centrality). \
                 PageRank entries: {}, findings: {:?}",
                prims.page_rank.len(),
                findings.iter().map(|f| &f.title).collect::<Vec<_>>()
            );
            assert_eq!(api_findings[0].severity, Severity::Critical);
            assert!(
                api_findings[0].description.contains("alice"),
                "Expected 'alice' in description: {}",
                api_findings[0].description
            );
        }
    }
}
