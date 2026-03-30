//! Orphaned knowledge detector.
//!
//! Fires when all authors with `is_author=true` for a file have `is_active=false`,
//! meaning no active maintainer understands the code.

use crate::detectors::analysis_context::AnalysisContext;
use crate::detectors::base::{is_non_production_file, is_test_file, Detector, DetectorConfig, DetectorScope};
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use std::sync::Arc;
use tracing::debug;

/// Detects files where all knowledgeable authors are inactive.
///
/// A file is orphaned when every author (DOA-qualified) has not committed
/// within the inactive window. This is the highest bus-factor risk: the
/// knowledge has effectively left the organization.
pub struct OrphanedKnowledgeDetector {
    config: DetectorConfig,
}

impl OrphanedKnowledgeDetector {
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

impl Default for OrphanedKnowledgeDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for OrphanedKnowledgeDetector {
    fn name(&self) -> &'static str {
        "OrphanedKnowledgeDetector"
    }

    fn description(&self) -> &'static str {
        "Detects files where all knowledgeable authors are inactive"
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

        let mut findings = Vec::new();

        for (path, file_ownership) in &ownership.files {
            let p = std::path::Path::new(path);
            if is_non_production_file(p) || is_test_file(p) {
                continue;
            }

            let authors: Vec<_> = file_ownership
                .authors
                .iter()
                .filter(|a| a.is_author)
                .collect();

            // Need at least 1 author to be orphaned
            if authors.is_empty() {
                continue;
            }

            // All authors must be inactive
            let all_inactive = authors.iter().all(|a| !a.is_active);
            if !all_inactive {
                continue;
            }

            findings.push(Finding {
                id: String::new(),
                detector: "orphaned-knowledge".to_string(),
                severity: Severity::Critical,
                confidence: Some(0.95),
                deterministic: true,
                title: format!("No active maintainer for '{}'", path),
                description: format!(
                    "File '{}' has {} author(s) but none are active. \
                     All knowledgeable contributors have stopped committing, \
                     leaving this file without institutional knowledge.",
                    path,
                    authors.len()
                ),
                affected_files: vec![PathBuf::from(path)],
                suggested_fix: Some(
                    "Assign an active team member to review and understand this file. \
                     Consider scheduling a knowledge transfer session if former authors \
                     are still reachable."
                        .to_string(),
                ),
                category: Some("architecture".to_string()),
                why_it_matters: Some(
                    "Files with no active maintainer are the highest bus-factor risk. \
                     Bug fixes and feature changes will take significantly longer \
                     because no one on the team understands the code."
                        .to_string(),
                ),
                ..Default::default()
            });
        }

        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        debug!(
            "OrphanedKnowledgeDetector found {} findings",
            findings.len()
        );

        Ok(findings)
    }
}

impl crate::detectors::RegisteredDetector for OrphanedKnowledgeDetector {
    fn create(init: &crate::detectors::DetectorInit) -> Arc<dyn Detector> {
        Arc::new(Self::with_config(
            init.config_for("OrphanedKnowledgeDetector"),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::git::ownership::{FileAuthorDOA, FileOwnershipDOA, OwnershipModel};
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

    fn make_author(name: &str, is_author: bool, is_active: bool) -> FileAuthorDOA {
        FileAuthorDOA {
            author: name.to_string(),
            email: format!("{}@example.com", name),
            raw_doa: 5.0,
            normalized_doa: 1.0,
            is_author,
            is_first_author: false,
            commit_count: 10,
            last_active: 0,
            is_active,
        }
    }

    fn make_file(path: &str, authors: Vec<FileAuthorDOA>) -> (String, FileOwnershipDOA) {
        let bus_factor = authors.iter().filter(|a| a.is_author).count();
        (
            path.to_string(),
            FileOwnershipDOA {
                path: path.to_string(),
                authors,
                bus_factor,
                hhi: 1.0,
                max_doa: 1.0,
            },
        )
    }

    #[test]
    fn test_all_active_no_findings() {
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("f1", "src/lib.rs"));
        let f2 = builder.add_node(CodeNode::function("f2", "src/main.rs"));
        builder.add_edge(f1, f2, CodeEdge::calls());
        let graph = builder.freeze();

        let mut files = HashMap::new();
        let (k, v) = make_file(
            "src/lib.rs",
            vec![make_author("alice", true, true)],
        );
        files.insert(k, v);

        let model = OwnershipModel {
            files,
            modules: HashMap::new(),
            project_bus_factor: 1,
            author_profiles: HashMap::new(),
        };

        let detector = OrphanedKnowledgeDetector::new();
        let ctx = make_ctx_with_ownership(&graph, model);
        let findings = detector.detect(&ctx).expect("should succeed");
        assert!(findings.is_empty(), "Active authors should not fire");
    }

    #[test]
    fn test_all_inactive_fires() {
        let builder = GraphBuilder::new();
        let graph = builder.freeze();

        let mut files = HashMap::new();
        let (k, v) = make_file(
            "src/core/engine.rs",
            vec![
                make_author("alice", true, false),
                make_author("bob", true, false),
            ],
        );
        files.insert(k, v);

        let model = OwnershipModel {
            files,
            modules: HashMap::new(),
            project_bus_factor: 0,
            author_profiles: HashMap::new(),
        };

        let detector = OrphanedKnowledgeDetector::new();
        let ctx = make_ctx_with_ownership(&graph, model);
        let findings = detector.detect(&ctx).expect("should succeed");

        assert!(!findings.is_empty(), "All-inactive authors should fire");
        assert_eq!(findings[0].detector, "orphaned-knowledge");
        assert_eq!(findings[0].severity, Severity::Critical);
        assert!(findings[0].title.contains("engine.rs"));
    }

    #[test]
    fn test_mixed_active_inactive_no_finding() {
        let builder = GraphBuilder::new();
        let graph = builder.freeze();

        let mut files = HashMap::new();
        let (k, v) = make_file(
            "src/core/engine.rs",
            vec![
                make_author("alice", true, false),
                make_author("bob", true, true), // one active
            ],
        );
        files.insert(k, v);

        let model = OwnershipModel {
            files,
            modules: HashMap::new(),
            project_bus_factor: 1,
            author_profiles: HashMap::new(),
        };

        let detector = OrphanedKnowledgeDetector::new();
        let ctx = make_ctx_with_ownership(&graph, model);
        let findings = detector.detect(&ctx).expect("should succeed");
        assert!(findings.is_empty(), "Mixed active/inactive should not fire");
    }

    #[test]
    fn test_no_authors_no_finding() {
        let builder = GraphBuilder::new();
        let graph = builder.freeze();

        let mut files = HashMap::new();
        // Author exists but is_author=false (below DOA threshold)
        let (k, v) = make_file(
            "src/core/engine.rs",
            vec![make_author("alice", false, false)],
        );
        files.insert(k, v);

        let model = OwnershipModel {
            files,
            modules: HashMap::new(),
            project_bus_factor: 0,
            author_profiles: HashMap::new(),
        };

        let detector = OrphanedKnowledgeDetector::new();
        let ctx = make_ctx_with_ownership(&graph, model);
        let findings = detector.detect(&ctx).expect("should succeed");
        assert!(findings.is_empty(), "No qualified authors should not fire");
    }

    #[test]
    fn test_scope_is_graph_wide() {
        let detector = OrphanedKnowledgeDetector::new();
        assert_eq!(detector.detector_scope(), DetectorScope::GraphWide);
    }

    #[test]
    fn test_category_is_architecture() {
        let detector = OrphanedKnowledgeDetector::new();
        assert_eq!(detector.category(), "architecture");
    }
}
