//! Hierarchical Predictive Coding Detector
//!
//! Replaces the flat n-gram SurprisalDetector with a 5-level
//! hierarchical predictive coding engine. Each level independently
//! models "what's normal" and computes prediction errors (z-scores).
//! Concordance across levels drives severity — a function that is
//! surprising at multiple independent levels is a much stronger
//! signal than any single metric.

use crate::detectors::base::Detector;
use crate::detectors::function_context::FunctionContextMap;
use crate::models::Finding;
use crate::predictive::PredictiveCodingEngine;
use anyhow::Result;
use std::path::PathBuf;
use std::sync::Arc;

pub struct HierarchicalSurprisalDetector {
    max_findings: usize,
}

impl HierarchicalSurprisalDetector {
    pub fn new() -> Self {
        Self { max_findings: 30 }
    }
}

impl Default for HierarchicalSurprisalDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for HierarchicalSurprisalDetector {
    fn name(&self) -> &'static str {
        "hierarchical-surprisal"
    }

    fn description(&self) -> &'static str {
        "Detects unusual code using hierarchical predictive coding (5 levels)"
    }

    fn category(&self) -> &'static str {
        "predictive-coding"
    }

    fn detect(
        &self,
        _graph: &dyn crate::graph::GraphQuery,
        _files: &dyn crate::detectors::file_provider::FileProvider,
    ) -> Result<Vec<Finding>> {
        let i = graph.interner();
        // This detector requires function contexts; detect_with_context is used instead.
        Ok(vec![])
    }

    fn uses_context(&self) -> bool {
        true
    }

    fn detect_with_context(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        files: &dyn crate::detectors::file_provider::FileProvider,
        contexts: &Arc<FunctionContextMap>,
    ) -> Result<Vec<Finding>> {
        let i = graph.interner();
        let mut engine = PredictiveCodingEngine::new();
        engine.train_and_score(graph, files, contexts);

        let surprising = engine.get_surprising_entities(2); // min concordance 2 for findings

        let mut findings: Vec<Finding> = Vec::new();
        let functions = graph.get_functions_shared();

        for (qn, score) in surprising.iter().take(self.max_findings) {
            // Find the function node for file/line info
            let func = functions.iter().find(|f| f.qualified_name == *qn);
            let (file_path, line_start, line_end, func_name) = match func {
                Some(f) => (
                    PathBuf::from(f.path(i)),
                    Some(f.line_start),
                    Some(f.line_end),
                    f.node_name(i).to_string(),
                ),
                None => continue,
            };

            // Build per-level detail string
            let mut level_detail = String::new();
            for ls in &score.level_scores {
                let marker = if ls.is_surprising { " *" } else { "" };
                level_detail.push_str(&format!(
                    "  {:<20} z={:.1}{}\n",
                    ls.level.label(),
                    ls.z_score,
                    marker
                ));
            }

            let severity = score.severity;

            let description = format!(
                "Function `{}` is surprising at {} of 5 hierarchy levels:\n\n{}\n\
                 Compound surprise: {:.1} (precision-weighted)\n\
                 Concordance: {}/5 levels\n\n\
                 **Possible causes:**\n\
                 - AI-generated code with different style\n\
                 - Copy-pasted from a different codebase\n\
                 - Architectural misplacement\n\
                 - Unusual algorithm or potential bug",
                func_name,
                score.concordance,
                level_detail,
                score.compound_surprise,
                score.concordance,
            );

            // Build threshold_metadata with per-level info
            let mut metadata = std::collections::HashMap::new();
            metadata.insert(
                "threshold_source".to_string(),
                "predictive-coding".to_string(),
            );
            metadata.insert("concordance".to_string(), score.concordance.to_string());
            metadata.insert(
                "compound_surprise".to_string(),
                format!("{:.2}", score.compound_surprise),
            );
            for ls in &score.level_scores {
                let key = format!(
                    "{}_z_score",
                    ls.level.label().replace(' ', "_").to_lowercase()
                );
                metadata.insert(key, format!("{:.2}", ls.z_score));
            }

            findings.push(Finding {
                id: String::new(),
                detector: "HierarchicalSurprisalDetector".to_string(),
                severity,
                title: format!("Unusual code pattern in `{}`", func_name),
                description,
                affected_files: vec![file_path],
                line_start,
                line_end,
                suggested_fix: Some(
                    "Review this function for:\n\
                     1. Style consistency with the rest of the project\n\
                     2. Correctness — unusual patterns may indicate bugs\n\
                     3. Architectural fit — is this in the right module?"
                        .to_string(),
                ),
                estimated_effort: Some("15 minutes".to_string()),
                category: Some("predictive-coding".to_string()),
                why_it_matters: Some(format!(
                    "This function's patterns are unusual at {} of 5 independent hierarchy levels \
                     (token, structural, dependency, relational, architectural). \
                     Multi-level concordance is a stronger signal than any single metric.",
                    score.concordance
                )),
                threshold_metadata: metadata,
                ..Default::default()
            });
        }

        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detectors::file_provider::MockFileProvider;
    use crate::graph::GraphStore;

    #[test]
    fn test_detector_name_and_category() {
        let detector = HierarchicalSurprisalDetector::new();
        assert_eq!(detector.name(), "hierarchical-surprisal");
        assert_eq!(detector.category(), "predictive-coding");
    }

    #[test]
    fn test_detector_empty_graph_no_crash() {
        let store = GraphStore::in_memory();
        let files = MockFileProvider::new(vec![]);
        let detector = HierarchicalSurprisalDetector::new();
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(findings.is_empty());
    }
}
