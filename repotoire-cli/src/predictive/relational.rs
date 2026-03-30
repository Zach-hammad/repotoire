//! L3: Relational surprise via Mahalanobis distance on graph features.
//!
//! Uses pre-computed graph metrics from `FunctionContext` (in_degree, out_degree,
//! betweenness, caller_modules, callee_modules, call_depth) to build a 6-dimensional
//! feature vector per function. Mahalanobis distance from the population centroid
//! identifies structurally unusual graph positions.
//!
//! This replaces the previous node2vec → word2vec → kNN pipeline (O(n²)) with an
//! O(n) approach that reuses the existing `StructuralScorer` infrastructure.

use super::structural::StructuralScorer;
use crate::detectors::function_context::{FunctionContext, FunctionContextMap};

/// Extract a 6-dimensional graph feature vector from a FunctionContext.
fn extract_graph_features(ctx: &FunctionContext) -> Vec<f64> {
    vec![
        ctx.in_degree as f64,
        ctx.out_degree as f64,
        ctx.betweenness,
        ctx.caller_modules as f64,
        ctx.callee_modules as f64,
        ctx.call_depth as f64,
    ]
}

/// Relational scorer using Mahalanobis distance on graph-derived features.
pub struct GraphRelationalScorer {
    scorer: StructuralScorer,
}

impl GraphRelationalScorer {
    /// Build from all function contexts in the codebase.
    pub fn from_contexts(contexts: &FunctionContextMap) -> Self {
        let features: Vec<Vec<f64>> = contexts.values().map(extract_graph_features).collect();
        Self {
            scorer: StructuralScorer::from_features(&features),
        }
    }

    /// Mahalanobis distance for a single function by qualified name.
    ///
    /// Returns 0.0 if the function is not found in contexts.
    pub fn distance(&self, qn: &str, contexts: &FunctionContextMap) -> f64 {
        contexts
            .get(qn)
            .map(|ctx| {
                self.scorer
                    .mahalanobis_distance(&extract_graph_features(ctx))
            })
            .unwrap_or(0.0)
    }
}

// ============================================================================
// UNIT TESTS
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detectors::function_context::FunctionRole;
    use std::collections::HashMap;

    fn make_context(
        qn: &str,
        in_deg: usize,
        out_deg: usize,
        betweenness: f64,
        caller_mods: usize,
        callee_mods: usize,
        depth: usize,
    ) -> FunctionContext {
        FunctionContext {
            qualified_name: qn.to_string(),
            name: qn.rsplit('.').next().unwrap_or(qn).to_string(),
            file_path: "test.rs".to_string(),
            module: "test".to_string(),
            in_degree: in_deg,
            out_degree: out_deg,
            betweenness,
            caller_modules: caller_mods,
            callee_modules: callee_mods,
            call_depth: depth,
            role: FunctionRole::Unknown,
            is_exported: false,
            is_test: false,
            is_in_utility_module: false,
            complexity: Some(5),
            loc: 20,
        }
    }

    #[test]
    fn test_graph_relational_scorer_basic() {
        let mut contexts: FunctionContextMap = HashMap::new();
        // Create a cluster of similar functions
        for i in 0..50 {
            let qn = format!("module.func_{i}");
            contexts.insert(
                qn.clone(),
                make_context(&qn, 2 + i % 3, 3 + i % 2, 0.01, 1, 1, 2),
            );
        }
        // Add an outlier
        contexts.insert(
            "module.outlier".to_string(),
            make_context("module.outlier", 50, 50, 0.95, 20, 20, 10),
        );

        let scorer = GraphRelationalScorer::from_contexts(&contexts);

        let dist_normal = scorer.distance("module.func_0", &contexts);
        let dist_outlier = scorer.distance("module.outlier", &contexts);

        assert!(
            dist_outlier > dist_normal,
            "Outlier ({dist_outlier}) should have higher distance than normal ({dist_normal})"
        );
    }

    #[test]
    fn test_empty_contexts() {
        let contexts: FunctionContextMap = HashMap::new();
        let scorer = GraphRelationalScorer::from_contexts(&contexts);
        assert_eq!(scorer.distance("anything", &contexts), 0.0);
    }

    #[test]
    fn test_missing_function() {
        let mut contexts: FunctionContextMap = HashMap::new();
        contexts.insert("a".to_string(), make_context("a", 1, 1, 0.0, 1, 1, 1));
        contexts.insert("b".to_string(), make_context("b", 2, 2, 0.0, 1, 1, 2));
        let scorer = GraphRelationalScorer::from_contexts(&contexts);
        assert_eq!(scorer.distance("nonexistent", &contexts), 0.0);
    }

    #[test]
    fn test_extract_graph_features() {
        let ctx = make_context("test.func", 5, 10, 0.42, 3, 7, 4);
        let features = extract_graph_features(&ctx);
        assert_eq!(features.len(), 6);
        assert!((features[0] - 5.0).abs() < f64::EPSILON);
        assert!((features[1] - 10.0).abs() < f64::EPSILON);
        assert!((features[2] - 0.42).abs() < f64::EPSILON);
        assert!((features[3] - 3.0).abs() < f64::EPSILON);
        assert!((features[4] - 7.0).abs() < f64::EPSILON);
        assert!((features[5] - 4.0).abs() < f64::EPSILON);
    }
}
