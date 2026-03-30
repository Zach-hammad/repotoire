//! Single-owner module detector.
//!
//! Fires when a module (directory) with 3+ files has a bus factor of 1 or less,
//! meaning all knowledge is concentrated in a single contributor.

use crate::detectors::analysis_context::AnalysisContext;
use crate::detectors::base::{Detector, DetectorConfig, DetectorScope};
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use std::sync::Arc;
use tracing::debug;

/// Detects modules where all files are owned by a single author.
///
/// A module with bus_factor <= 1 and multiple files represents a knowledge
/// concentration risk: if that author leaves, no one else understands the code.
pub struct SingleOwnerModuleDetector {
    config: DetectorConfig,
}

impl SingleOwnerModuleDetector {
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

impl Default for SingleOwnerModuleDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for SingleOwnerModuleDetector {
    fn name(&self) -> &'static str {
        "SingleOwnerModuleDetector"
    }

    fn description(&self) -> &'static str {
        "Detects modules where all files depend on a single author"
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

        let min_module_files: usize = self.config.get_option_or("min_module_files", 3);

        let mut findings = Vec::new();

        for (path, module) in &ownership.modules {
            if module.file_count < min_module_files {
                continue;
            }
            if module.bus_factor > 1 {
                continue;
            }

            let author = module
                .top_authors
                .first()
                .map(|(a, _)| a.as_str())
                .unwrap_or("unknown");

            findings.push(Finding {
                id: String::new(),
                detector: "single-owner-module".to_string(),
                severity: Severity::High,
                confidence: Some(0.90),
                deterministic: true,
                title: format!("Module '{}' depends entirely on {}", path, author),
                description: format!(
                    "Module '{}' has {} files but a bus factor of {}, meaning all knowledge \
                     is concentrated in {}. If this author becomes unavailable, no one else \
                     has sufficient context to maintain this module.",
                    path, module.file_count, module.bus_factor, author
                ),
                affected_files: vec![PathBuf::from(path)],
                suggested_fix: Some(
                    "Pair-program or rotate code reviews to spread knowledge. \
                     Consider having a second engineer make meaningful contributions \
                     to this module."
                        .to_string(),
                ),
                category: Some("architecture".to_string()),
                why_it_matters: Some(
                    "Single-owner modules are a bus factor risk: if the sole author leaves \
                     or is unavailable, the team loses institutional knowledge for this code."
                        .to_string(),
                ),
                ..Default::default()
            });
        }

        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        debug!(
            "SingleOwnerModuleDetector found {} findings",
            findings.len()
        );

        Ok(findings)
    }
}

impl crate::detectors::RegisteredDetector for SingleOwnerModuleDetector {
    fn create(init: &crate::detectors::DetectorInit) -> Arc<dyn Detector> {
        Arc::new(Self::with_config(
            init.config_for("SingleOwnerModuleDetector"),
        ))
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
        let detector = SingleOwnerModuleDetector::new();
        let ctx = make_ctx_with_ownership(&graph, OwnershipModel::empty());
        let findings = detector.detect(&ctx).expect("should succeed");
        assert!(findings.is_empty(), "Empty ownership should produce no findings");
    }

    #[test]
    fn test_single_owner_module_fires() {
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
                bus_factor: 1,
                avg_bus_factor: 1.0,
                hhi: 1.0,
                top_authors: vec![("alice".to_string(), 5.0)],
                risk_score: 0.8,
                file_count: 4,
                at_risk_file_count: 4,
                at_risk_pct: 1.0,
            },
        );

        let model = OwnershipModel {
            files: HashMap::new(),
            modules,
            project_bus_factor: 1,
            author_profiles: HashMap::new(),
        };

        let detector = SingleOwnerModuleDetector::new();
        let ctx = make_ctx_with_ownership(&graph, model);
        let findings = detector.detect(&ctx).expect("should succeed");

        assert!(!findings.is_empty(), "Should detect single-owner module");
        assert_eq!(findings[0].detector, "single-owner-module");
        assert!(findings[0].title.contains("alice"));
        assert!(findings[0].title.contains("src/core"));
    }

    #[test]
    fn test_small_module_skipped() {
        let builder = GraphBuilder::new();
        let graph = builder.freeze();

        let mut modules = HashMap::new();
        modules.insert(
            "src/tiny".to_string(),
            ModuleOwnershipSummary {
                path: "src/tiny".to_string(),
                bus_factor: 1,
                avg_bus_factor: 1.0,
                hhi: 1.0,
                top_authors: vec![("alice".to_string(), 3.0)],
                risk_score: 0.5,
                file_count: 2, // below threshold of 3
                at_risk_file_count: 2,
                at_risk_pct: 1.0,
            },
        );

        let model = OwnershipModel {
            files: HashMap::new(),
            modules,
            project_bus_factor: 1,
            author_profiles: HashMap::new(),
        };

        let detector = SingleOwnerModuleDetector::new();
        let ctx = make_ctx_with_ownership(&graph, model);
        let findings = detector.detect(&ctx).expect("should succeed");

        assert!(
            findings.is_empty(),
            "Module with <3 files should be skipped"
        );
    }

    #[test]
    fn test_scope_is_graph_wide() {
        let detector = SingleOwnerModuleDetector::new();
        assert_eq!(detector.detector_scope(), DetectorScope::GraphWide);
    }

    #[test]
    fn test_category_is_architecture() {
        let detector = SingleOwnerModuleDetector::new();
        assert_eq!(detector.category(), "architecture");
    }
}
