//! Fast FP/TP classifier for findings
//!
//! A lightweight neural network that classifies findings as
//! true positives or false positives based on code context.
//!
//! Architecture: TF-IDF features → Linear classifier → Category thresholds
//! Speed: <1ms per finding
//!
//! Key insight from research: Different detector categories need different
//! thresholds. Security findings need high recall (don't miss real vulns),
//! while code quality findings can tolerate more filtering.

pub mod bootstrap;
pub mod debt;
mod features;
pub mod features_v2;
pub mod feedback;
pub mod gbdt_model;
pub mod model;
pub mod thresholds;
pub mod train;

pub use debt::{compute_debt, DebtTrend, DebtWeights, FileDebt};
pub use features::FeatureExtractor;
pub use features_v2::{CrossFindingFeatures, FeatureExtractorV2, FeaturesV2, GitFeatures};
pub use gbdt_model::{GbdtClassifier, GbdtPrediction};
pub use feedback::{FeedbackCollector, LabeledFinding};
pub use model::{FpClassifier, HeuristicClassifier, Prediction};
pub use thresholds::{
    CategoryAwarePrediction, CategoryThresholds, DetectorCategory, ThresholdConfig,
};
pub use train::{train, TrainConfig, TrainResult};

use crate::models::Finding;

/// Classify a batch of findings
pub fn classify_findings(findings: &[Finding], classifier: &FpClassifier) -> Vec<Prediction> {
    let extractor = FeatureExtractor::new();

    findings
        .iter()
        .map(|f| {
            let features = extractor.extract(f);
            classifier.predict(&features)
        })
        .collect()
}

/// Classify findings with category-aware thresholds
pub fn classify_findings_with_thresholds(
    findings: &[Finding],
    classifier: &FpClassifier,
    thresholds: &CategoryThresholds,
) -> Vec<CategoryAwarePrediction> {
    let extractor = FeatureExtractor::new();

    findings
        .iter()
        .map(|f| {
            let features = extractor.extract(f);
            let pred = classifier.predict(&features);
            CategoryAwarePrediction::from_prediction(pred.tp_probability, &f.detector, thresholds)
        })
        .collect()
}

/// Filter findings, keeping only likely true positives (legacy API)
pub fn filter_false_positives(
    findings: Vec<Finding>,
    classifier: &FpClassifier,
    threshold: f32,
) -> Vec<Finding> {
    let extractor = FeatureExtractor::new();

    findings
        .into_iter()
        .filter(|f| {
            let features = extractor.extract(f);
            let pred = classifier.predict(&features);
            pred.tp_probability >= threshold
        })
        .collect()
}

/// Filter findings using category-aware thresholds
///
/// This is the recommended API - uses different thresholds for different
/// detector categories (security, code quality, ML, performance).
pub fn filter_with_category_thresholds(
    findings: Vec<Finding>,
    classifier: &FpClassifier,
    thresholds: &CategoryThresholds,
) -> Vec<Finding> {
    let extractor = FeatureExtractor::new();

    findings
        .into_iter()
        .filter(|f| {
            let features = extractor.extract(f);
            let pred = classifier.predict(&features);
            !thresholds.should_filter(&f.detector, pred.tp_probability)
        })
        .collect()
}

/// Classify and annotate findings with FP probabilities
///
/// Returns findings with an optional annotation field set based on
/// the classifier's prediction. High-confidence TPs are left alone,
/// likely FPs get flagged for review.
pub fn annotate_findings(
    findings: Vec<Finding>,
    classifier: &FpClassifier,
    thresholds: &CategoryThresholds,
) -> Vec<(Finding, CategoryAwarePrediction)> {
    let extractor = FeatureExtractor::new();

    findings
        .into_iter()
        .map(|f| {
            let features = extractor.extract(&f);
            let pred = classifier.predict(&features);
            let cat_pred = CategoryAwarePrediction::from_prediction(
                pred.tp_probability,
                &f.detector,
                thresholds,
            );
            (f, cat_pred)
        })
        .collect()
}

/// Summary statistics for a batch of classified findings
#[derive(Debug, Clone)]
pub struct ClassificationSummary {
    pub total: usize,
    pub high_confidence_tp: usize,
    pub likely_tp: usize,
    pub uncertain: usize,
    pub likely_fp: usize,
    pub would_filter: usize,
    pub by_category: std::collections::HashMap<DetectorCategory, CategoryStats>,
}

#[derive(Debug, Clone, Default)]
pub struct CategoryStats {
    pub total: usize,
    pub high_confidence: usize,
    pub would_filter: usize,
}

impl ClassificationSummary {
    pub fn from_predictions(predictions: &[CategoryAwarePrediction]) -> Self {
        use std::collections::HashMap;

        let mut by_category: HashMap<DetectorCategory, CategoryStats> = HashMap::new();
        let mut high_confidence_tp = 0;
        let mut likely_tp = 0;
        let mut uncertain = 0;
        let mut likely_fp = 0;
        let mut would_filter = 0;

        for pred in predictions {
            // Category stats
            let stats = by_category.entry(pred.category).or_default();
            stats.total += 1;
            if pred.high_confidence {
                stats.high_confidence += 1;
            }
            if pred.should_filter {
                stats.would_filter += 1;
            }

            // Overall stats
            if pred.high_confidence {
                high_confidence_tp += 1;
            } else if pred.is_true_positive {
                likely_tp += 1;
            } else if pred.likely_fp {
                likely_fp += 1;
            } else {
                uncertain += 1;
            }

            if pred.should_filter {
                would_filter += 1;
            }
        }

        Self {
            total: predictions.len(),
            high_confidence_tp,
            likely_tp,
            uncertain,
            likely_fp,
            would_filter,
            by_category,
        }
    }
}
