//! Knowledge silo detector.
//!
//! Fires when a module has high ownership concentration (HHI) indicating
//! that knowledge is siloed in one or two contributors.

use crate::detectors::analysis_context::AnalysisContext;
use crate::detectors::base::{Detector, DetectorConfig, DetectorScope};
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use std::sync::Arc;
use tracing::debug;

/// Detects modules where knowledge is concentrated in a small number of authors.
///
/// Uses the Herfindahl-Hirschman Index (HHI) of ownership shares. High HHI
/// means a few authors dominate the module, creating knowledge silos.
pub struct KnowledgeSiloDetector {
    config: DetectorConfig,
}

impl KnowledgeSiloDetector {
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

impl Default for KnowledgeSiloDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for KnowledgeSiloDetector {
    fn name(&self) -> &'static str {
        "KnowledgeSiloDetector"
    }

    fn description(&self) -> &'static str {
        "Detects modules with concentrated ownership indicating knowledge silos"
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

        let hhi_threshold: f64 = self.config.get_option_or("hhi_threshold", 0.65);

        let mut findings = Vec::new();

        for (path, module) in &ownership.modules {
            if module.file_count < 2 {
                continue;
            }
            if module.hhi <= hhi_threshold {
                continue;
            }

            let (author, pct) = module
                .top_authors
                .first()
                .map(|(a, d)| (a.as_str(), d / module.file_count as f64 * 100.0))
                .unwrap_or(("unknown", 0.0));

            findings.push(Finding {
                id: String::new(),
                detector: "knowledge-silo".to_string(),
                severity: Severity::Medium,
                confidence: Some(0.85),
                deterministic: true,
                title: format!(
                    "Knowledge silo in '{}' \u{2014} {} owns {:.0}%",
                    path, author, pct
                ),
                description: format!(
                    "Module '{}' has an HHI of {:.2} (threshold: {:.2}), indicating \
                     ownership is concentrated. {} dominates with ~{:.0}% of contributions \
                     across {} files. This creates a knowledge silo risk.",
                    path, module.hhi, hhi_threshold, author, pct, module.file_count
                ),
                affected_files: vec![PathBuf::from(path)],
                suggested_fix: Some(
                    "Rotate code reviews and pair-program to distribute knowledge. \
                     Encourage other team members to contribute to this module."
                        .to_string(),
                ),
                category: Some("architecture".to_string()),
                why_it_matters: Some(
                    "Knowledge silos slow down development when the primary author \
                     is unavailable and increase review bottleneck risk."
                        .to_string(),
                ),
                ..Default::default()
            });
        }

        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        debug!("KnowledgeSiloDetector found {} findings", findings.len());

        Ok(findings)
    }
}

impl crate::detectors::RegisteredDetector for KnowledgeSiloDetector {
    fn create(init: &crate::detectors::DetectorInit) -> Arc<dyn Detector> {
        Arc::new(Self::with_config(init.config_for("KnowledgeSiloDetector")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::git::ownership::{ModuleOwnershipSummary, OwnershipModel};
    use crate::graph::{CodeEdge, CodeNode, GraphBuilder};
    use std::collections::HashMap;
    use std::sync::Arc;

    fn make_ctx_with_ownership(
        graph: &dyn crate::graph::GraphQuery,
        model: OwnershipModel,
    ) -> AnalysisContext<'_> {
        let mut ctx = AnalysisContext::test(graph);
        ctx.ownership = Some(Arc::new(model));
        ctx
    }

    #[test]
    fn test_empty_ownership_no_findings() {
        let builder = GraphBuilder::new();
        let graph = builder.freeze();
        let detector = KnowledgeSiloDetector::new();
        let ctx = make_ctx_with_ownership(&graph, OwnershipModel::empty());
        let findings = detector.detect(&ctx).expect("should succeed");
        assert!(findings.is_empty());
    }

    #[test]
    fn test_high_hhi_fires() {
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("f1", "src/core/a.rs"));
        let f2 = builder.add_node(CodeNode::function("f2", "src/core/b.rs"));
        builder.add_edge(f1, f2, CodeEdge::calls());
        let graph = builder.freeze();

        let mut modules = HashMap::new();
        modules.insert(
            "src/core".to_string(),
            ModuleOwnershipSummary {
                path: "src/core".to_string(),
                bus_factor: 2,
                avg_bus_factor: 1.5,
                hhi: 0.80, // above default 0.65 threshold
                top_authors: vec![("alice".to_string(), 8.0)],
                risk_score: 0.6,
                file_count: 5,
                at_risk_file_count: 2,
                at_risk_pct: 0.4,
            },
        );

        let model = OwnershipModel {
            files: HashMap::new(),
            modules,
            project_bus_factor: 2,
            author_profiles: HashMap::new(),
        };

        let detector = KnowledgeSiloDetector::new();
        let ctx = make_ctx_with_ownership(&graph, model);
        let findings = detector.detect(&ctx).expect("should succeed");

        assert!(!findings.is_empty(), "Should detect knowledge silo");
        assert_eq!(findings[0].detector, "knowledge-silo");
        assert!(findings[0].title.contains("alice"));
    }

    #[test]
    fn test_low_hhi_skipped() {
        let builder = GraphBuilder::new();
        let graph = builder.freeze();

        let mut modules = HashMap::new();
        modules.insert(
            "src/shared".to_string(),
            ModuleOwnershipSummary {
                path: "src/shared".to_string(),
                bus_factor: 4,
                avg_bus_factor: 3.0,
                hhi: 0.30, // well below threshold
                top_authors: vec![("alice".to_string(), 2.0), ("bob".to_string(), 2.0)],
                risk_score: 0.2,
                file_count: 6,
                at_risk_file_count: 0,
                at_risk_pct: 0.0,
            },
        );

        let model = OwnershipModel {
            files: HashMap::new(),
            modules,
            project_bus_factor: 4,
            author_profiles: HashMap::new(),
        };

        let detector = KnowledgeSiloDetector::new();
        let ctx = make_ctx_with_ownership(&graph, model);
        let findings = detector.detect(&ctx).expect("should succeed");

        assert!(findings.is_empty(), "Low HHI module should be skipped");
    }

    #[test]
    fn test_scope_is_graph_wide() {
        let detector = KnowledgeSiloDetector::new();
        assert_eq!(detector.detector_scope(), DetectorScope::GraphWide);
    }

    #[test]
    fn test_category_is_architecture() {
        let detector = KnowledgeSiloDetector::new();
        assert_eq!(detector.category(), "architecture");
    }
}
