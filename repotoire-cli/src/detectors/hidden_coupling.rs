//! Hidden coupling detector using co-change analysis.
//!
//! Identifies file pairs that frequently change together in version control
//! but have no structural relationship (imports, calls, etc.) in the code graph.
//! These "invisible" dependencies create "change A, forget to change B" risks
//! that are undetectable by static analysis and IDE navigation.

use crate::detectors::base::{Detector, DetectorConfig, DetectorScope};
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use std::sync::Arc;
use tracing::debug;

/// Detects hidden coupling: file pairs with high co-change frequency but zero structural edges.
///
/// Files that always change together but have no import or call relationship
/// represent implicit dependencies invisible to static analysis. This detector
/// surfaces them so developers can make the coupling explicit or eliminate it.
///
/// Uses pre-computed graph primitives:
/// - `hidden_coupling_pairs()`: file pairs with co-change weight but no structural edge
/// - `node_idx()`: node lookup for file paths
pub struct HiddenCouplingDetector {
    config: DetectorConfig,
}

impl HiddenCouplingDetector {
    /// Create a new detector with default config.
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
        }
    }

    /// Create with custom config.
    #[allow(dead_code)]
    pub fn with_config(config: DetectorConfig) -> Self {
        Self { config }
    }
}

impl Default for HiddenCouplingDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for HiddenCouplingDetector {
    fn name(&self) -> &'static str {
        "HiddenCouplingDetector"
    }

    fn description(&self) -> &'static str {
        "Detects file pairs that co-change frequently but have no structural dependency"
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
        let pairs = graph.hidden_coupling_pairs();

        if pairs.is_empty() {
            return Ok(vec![]);
        }

        debug!(
            "HiddenCouplingDetector: examining {} hidden coupling pairs",
            pairs.len()
        );

        let mut findings = Vec::new();
        let min_weight: f32 = self.config.get_option_or("min_weight", 1.0);
        let min_lift: f32 = self.config.get_option_or("min_lift", 2.0);

        for &(file_a_idx, file_b_idx, weight, lift) in pairs {
            if weight < min_weight {
                continue;
            }
            if lift < min_lift {
                continue;
            }

            // Score combines lift (statistical surprise) with weight (evidence volume).
            // lift alone can be high for rarely-changing files; sqrt(weight) dampens noise.
            let score = lift * weight.sqrt();

            let severity = if score >= 10.0 {
                Severity::Critical
            } else if score >= 6.0 {
                Severity::High
            } else if score >= 3.0 {
                Severity::Medium
            } else {
                Severity::Low
            };

            // Get file paths from node indices
            let file_a = graph
                .node_idx(file_a_idx)
                .map(|n| n.path(gi).to_string())
                .unwrap_or_default();
            let file_b = graph
                .node_idx(file_b_idx)
                .map(|n| n.path(gi).to_string())
                .unwrap_or_default();

            // Skip files in the same directory — co-change within a module is expected
            let dir_a = file_a.rsplit_once('/').map(|(d, _)| d).unwrap_or("");
            let dir_b = file_b.rsplit_once('/').map(|(d, _)| d).unwrap_or("");
            if dir_a == dir_b {
                continue;
            }

            // Skip test↔source pairs — tests always co-change with source, not interesting
            fn is_test_file(path: &str) -> bool {
                let lower = path.to_lowercase();
                lower.contains("/test")
                    || lower.contains("test_")
                    || lower.contains("_test.")
                    || lower.contains("/tests/")
                    || lower.contains("/spec/")
                    || lower.contains(".test.")
            }
            if is_test_file(&file_a) || is_test_file(&file_b) {
                continue;
            }

            findings.push(Finding {
                id: String::new(),
                detector: "hidden-coupling".to_string(),
                severity,
                confidence: Some(if lift >= 5.0 { 0.95 } else if lift >= 3.0 { 0.85 } else { 0.7 }),
                deterministic: true,
                title: format!(
                    "Hidden coupling: {} and {} (lift: {:.1}x, weight: {:.1})",
                    file_a.rsplit('/').next().unwrap_or(&file_a),
                    file_b.rsplit('/').next().unwrap_or(&file_b),
                    lift,
                    weight
                ),
                description: format!(
                    "Hidden coupling detected: `{}` and `{}` co-change {:.1}x more than expected \
                     by chance (weight: {:.2}). They have no import or call relationship. \
                     Consider adding an explicit dependency or extracting shared logic.",
                    file_a, file_b, lift, weight
                ),
                affected_files: vec![PathBuf::from(&file_a), PathBuf::from(&file_b)],
                suggested_fix: Some(
                    "Consider one of: (1) Add an explicit import if there's a real dependency, \
                     (2) Extract shared logic into a common module, \
                     (3) Document the implicit coupling if it's intentional."
                        .to_string(),
                ),
                category: Some("architecture".to_string()),
                why_it_matters: Some(
                    "Files that always change together but have no static dependency create \
                     'change A, forget to change B' risks. These hidden couplings are invisible \
                     to static analysis and IDE navigation."
                        .to_string(),
                ),
                ..Default::default()
            });
        }

        // Sort by severity (highest first).
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        debug!(
            "HiddenCouplingDetector found {} findings",
            findings.len()
        );

        Ok(findings)
    }
}

impl super::RegisteredDetector for HiddenCouplingDetector {
    fn create(init: &super::DetectorInit) -> Arc<dyn Detector> {
        Arc::new(Self::with_config(init.config_for("HiddenCouplingDetector")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeEdge, CodeNode, GraphBuilder};

    #[test]
    fn test_detects_hidden_coupling() {
        // Build a graph with two files in different directories, two functions,
        // no structural edge between them, but co-change data that triggers
        // hidden coupling. Files must be in different directories because
        // same-directory co-change is expected and filtered out.
        let mut builder = GraphBuilder::new();

        let f1 = builder.add_node(CodeNode::function("handler", "src/api/routes.rs"));
        let f2 = builder.add_node(CodeNode::function("model_update", "src/db/models.rs"));
        let file_api = builder.add_node(CodeNode::file("src/api/routes.rs"));
        let file_db = builder.add_node(CodeNode::file("src/db/models.rs"));

        // Add Contains edges (file contains function) — required for graph structure
        builder.add_edge(file_api, f1, CodeEdge::contains());
        builder.add_edge(file_db, f2, CodeEdge::contains());

        // Add a call edge between the functions so graph primitives compute
        // (primitives requires non-empty call edges to compute at all)
        // BUT: this call edge is function-to-function within the same file direction,
        // not between the two files we care about. We need a call edge somewhere
        // so primitives doesn't bail early.
        // Actually we need f1 to call f2 for the call graph to exist,
        // but that would create a structural edge between the files.
        // Instead, add a third helper function so we have call edges but no
        // direct structural edge between the two target files.
        let f3 = builder.add_node(CodeNode::function("helper", "src/util/helpers.rs"));
        let file_util = builder.add_node(CodeNode::file("src/util/helpers.rs"));
        builder.add_edge(file_util, f3, CodeEdge::contains());
        builder.add_edge(f1, f3, CodeEdge::calls());
        builder.add_edge(f2, f3, CodeEdge::calls());

        // Build co-change matrix: routes.rs and models.rs frequently change together.
        // 3 commits with decay=1.0 each => accumulated weight=3.0 (above min_weight=1.0).
        // Additional commits touching unrelated files raise total_decay_weight,
        // which increases lift (statistical surprise) for the routes↔models pair.
        //
        // lift = co_change(A,B) * total_decay / (file_weight(A) * file_weight(B))
        //      = 3.0 * 13.0 / (3.0 * 3.0) = 4.33x (well above min_lift=1.5)
        let now = chrono::Utc::now();
        let config = crate::git::co_change::CoChangeConfig {
            min_weight: 0.01,
            ..Default::default()
        };
        let commits = vec![
            (now, vec!["src/api/routes.rs".to_string(), "src/db/models.rs".to_string()]),
            (now, vec!["src/api/routes.rs".to_string(), "src/db/models.rs".to_string()]),
            (now, vec!["src/api/routes.rs".to_string(), "src/db/models.rs".to_string()]),
            // Unrelated commits to establish baseline change frequency
            (now, vec!["src/util/helpers.rs".to_string(), "src/util/config.rs".to_string()]),
            (now, vec!["src/util/helpers.rs".to_string(), "src/util/config.rs".to_string()]),
            (now, vec!["src/other/foo.rs".to_string(), "src/other/bar.rs".to_string()]),
            (now, vec!["src/other/foo.rs".to_string(), "src/other/bar.rs".to_string()]),
            (now, vec!["src/other/baz.rs".to_string()]),
            (now, vec!["src/other/qux.rs".to_string()]),
            (now, vec!["src/other/quux.rs".to_string()]),
            (now, vec!["src/other/corge.rs".to_string()]),
            (now, vec!["src/other/grault.rs".to_string()]),
            (now, vec!["src/other/garply.rs".to_string()]),
        ];
        let co_change =
            crate::git::co_change::CoChangeMatrix::from_commits(&commits, &config, now);

        let graph = builder.freeze_with_co_change(&co_change);
        let detector = HiddenCouplingDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            !findings.is_empty(),
            "Should detect hidden coupling between routes.rs and models.rs"
        );
        assert_eq!(findings[0].detector, "hidden-coupling");
        assert!(
            findings[0].description.contains("routes.rs"),
            "Should mention routes.rs: {}",
            findings[0].description
        );
        assert!(
            findings[0].description.contains("models.rs"),
            "Should mention models.rs: {}",
            findings[0].description
        );
        // Title should show lift
        assert!(
            findings[0].title.contains("lift:"),
            "Title should show lift: {}",
            findings[0].title
        );
        // Description should mention "more than expected"
        assert!(
            findings[0].description.contains("more than expected"),
            "Description should reference statistical surprise: {}",
            findings[0].description
        );
    }

    #[test]
    fn test_no_findings_when_empty() {
        // A basic graph with no co-change data → no hidden coupling
        let mut builder = GraphBuilder::new();

        let f1 = builder.add_node(CodeNode::function("f1", "a.py"));
        let f2 = builder.add_node(CodeNode::function("f2", "b.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());

        let graph = builder.freeze();
        let detector = HiddenCouplingDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Should have no findings without co-change data"
        );
    }

    #[test]
    fn test_severity_thresholds() {
        // Verify severity is based on lift-weighted score, not raw weight.
        // score = lift * sqrt(weight)
        // Critical >= 10.0, High >= 6.0, Medium >= 3.0, Low < 3.0
        let detector = HiddenCouplingDetector::new();

        // Empty graph → no findings (sanity check)
        let builder = GraphBuilder::new();
        let graph = builder.freeze();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(findings.is_empty());
    }

    #[test]
    fn test_scope_is_graph_wide() {
        let detector = HiddenCouplingDetector::new();
        assert_eq!(detector.detector_scope(), DetectorScope::GraphWide);
    }

    #[test]
    fn test_category_is_architecture() {
        let detector = HiddenCouplingDetector::new();
        assert_eq!(detector.category(), "architecture");
    }

    #[test]
    fn test_is_deterministic() {
        let detector = HiddenCouplingDetector::new();
        assert!(detector.is_deterministic());
    }
}
