//! Precision-weighted aggregation + concordance scoring.

use super::{CompoundScore, Level, LevelScore};
use crate::models::Severity;
use std::collections::HashMap;

/// Default per-level z-score thresholds.
pub fn default_thresholds() -> HashMap<Level, f64> {
    let mut m = HashMap::new();
    m.insert(Level::Token, 2.5);
    m.insert(Level::Structural, 2.0);
    m.insert(Level::DependencyChain, 2.0);
    m.insert(Level::Relational, 1.5);
    m.insert(Level::Architectural, 2.0);
    m
}

/// Compute empirical precision weights from z-score distributions.
/// precision_i = 1 / variance(z_scores_i), then normalize.
pub fn compute_precision_weights(all_scores: &HashMap<Level, Vec<f64>>) -> HashMap<Level, f64> {
    let mut precisions: HashMap<Level, f64> = HashMap::new();
    let mut total_precision = 0.0;

    for (level, scores) in all_scores {
        if scores.len() < 2 {
            precisions.insert(*level, 1.0);
            total_precision += 1.0;
            continue;
        }
        let n = scores.len() as f64;
        let mean = scores.iter().sum::<f64>() / n;
        let variance = scores.iter().map(|s| (s - mean).powi(2)).sum::<f64>() / n;
        let precision = if variance > 1e-10 { 1.0 / variance } else { 1.0 };
        precisions.insert(*level, precision);
        total_precision += precision;
    }

    if total_precision > 0.0 {
        for v in precisions.values_mut() {
            *v /= total_precision;
        }
    }
    precisions
}

/// Score a single entity given its per-level z-scores.
pub fn score_entity(
    level_scores: Vec<LevelScore>,
    weights: &HashMap<Level, f64>,
) -> CompoundScore {
    let concordance = level_scores.iter().filter(|s| s.is_surprising).count();

    let compound_surprise: f64 = level_scores
        .iter()
        .filter(|s| s.is_surprising)
        .map(|s| weights.get(&s.level).copied().unwrap_or(0.2) * s.z_score)
        .sum();

    let severity = match concordance {
        0 | 1 => Severity::Info,
        2 => Severity::Low,
        3 => Severity::Medium,
        _ => Severity::High,
    };

    CompoundScore {
        level_scores,
        concordance,
        compound_surprise,
        severity,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_thresholds_returns_all_five_levels() {
        let thresholds = default_thresholds();
        assert_eq!(thresholds.len(), 5);
        assert!(thresholds.contains_key(&Level::Token));
        assert!(thresholds.contains_key(&Level::Structural));
        assert!(thresholds.contains_key(&Level::DependencyChain));
        assert!(thresholds.contains_key(&Level::Relational));
        assert!(thresholds.contains_key(&Level::Architectural));

        // Verify specific values
        assert!((thresholds[&Level::Token] - 2.5).abs() < f64::EPSILON);
        assert!((thresholds[&Level::Structural] - 2.0).abs() < f64::EPSILON);
        assert!((thresholds[&Level::DependencyChain] - 2.0).abs() < f64::EPSILON);
        assert!((thresholds[&Level::Relational] - 1.5).abs() < f64::EPSILON);
        assert!((thresholds[&Level::Architectural] - 2.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_compute_precision_weights_with_known_variance() {
        let mut all_scores: HashMap<Level, Vec<f64>> = HashMap::new();

        // Token: scores [1.0, 3.0] → mean=2.0, var=1.0, precision=1.0
        all_scores.insert(Level::Token, vec![1.0, 3.0]);
        // Structural: scores [1.0, 5.0] → mean=3.0, var=4.0, precision=0.25
        all_scores.insert(Level::Structural, vec![1.0, 5.0]);

        let weights = compute_precision_weights(&all_scores);

        assert_eq!(weights.len(), 2);

        // Total precision = 1.0 + 0.25 = 1.25
        // Token weight = 1.0 / 1.25 = 0.8
        // Structural weight = 0.25 / 1.25 = 0.2
        let token_w = weights[&Level::Token];
        let structural_w = weights[&Level::Structural];

        assert!((token_w - 0.8).abs() < 1e-9, "token weight: {token_w}");
        assert!(
            (structural_w - 0.2).abs() < 1e-9,
            "structural weight: {structural_w}"
        );

        // Weights should sum to 1.0
        let total: f64 = weights.values().sum();
        assert!((total - 1.0).abs() < 1e-9, "total: {total}");
    }

    #[test]
    fn test_compute_precision_weights_single_sample_fallback() {
        let mut all_scores: HashMap<Level, Vec<f64>> = HashMap::new();

        // Single sample → precision defaults to 1.0
        all_scores.insert(Level::Token, vec![5.0]);
        all_scores.insert(Level::Structural, vec![3.0]);

        let weights = compute_precision_weights(&all_scores);

        // Both have precision 1.0 → equal weights 0.5 each
        let token_w = weights[&Level::Token];
        let structural_w = weights[&Level::Structural];
        assert!((token_w - 0.5).abs() < 1e-9);
        assert!((structural_w - 0.5).abs() < 1e-9);
    }

    #[test]
    fn test_compute_precision_weights_zero_variance_fallback() {
        let mut all_scores: HashMap<Level, Vec<f64>> = HashMap::new();

        // All identical values → variance=0 → precision=1.0
        all_scores.insert(Level::Token, vec![2.0, 2.0, 2.0]);
        all_scores.insert(Level::Relational, vec![3.0, 3.0, 3.0]);

        let weights = compute_precision_weights(&all_scores);

        // Both have precision 1.0 → equal weights 0.5 each
        let token_w = weights[&Level::Token];
        let relational_w = weights[&Level::Relational];
        assert!((token_w - 0.5).abs() < 1e-9);
        assert!((relational_w - 0.5).abs() < 1e-9);
    }

    #[test]
    fn test_score_entity_concordance_severity_mapping() {
        let weights = uniform_weights();

        // 0 surprising → Info
        let scores = make_level_scores(&[false, false, false, false, false]);
        let result = score_entity(scores, &weights);
        assert_eq!(result.concordance, 0);
        assert_eq!(result.severity, Severity::Info);

        // 1 surprising → Info
        let scores = make_level_scores(&[true, false, false, false, false]);
        let result = score_entity(scores, &weights);
        assert_eq!(result.concordance, 1);
        assert_eq!(result.severity, Severity::Info);

        // 2 surprising → Low
        let scores = make_level_scores(&[true, true, false, false, false]);
        let result = score_entity(scores, &weights);
        assert_eq!(result.concordance, 2);
        assert_eq!(result.severity, Severity::Low);

        // 3 surprising → Medium
        let scores = make_level_scores(&[true, true, true, false, false]);
        let result = score_entity(scores, &weights);
        assert_eq!(result.concordance, 3);
        assert_eq!(result.severity, Severity::Medium);

        // 4 surprising → High
        let scores = make_level_scores(&[true, true, true, true, false]);
        let result = score_entity(scores, &weights);
        assert_eq!(result.concordance, 4);
        assert_eq!(result.severity, Severity::High);

        // 5 surprising → High
        let scores = make_level_scores(&[true, true, true, true, true]);
        let result = score_entity(scores, &weights);
        assert_eq!(result.concordance, 5);
        assert_eq!(result.severity, Severity::High);
    }

    #[test]
    fn test_score_entity_compound_surprise_calculation() {
        let mut weights: HashMap<Level, f64> = HashMap::new();
        weights.insert(Level::Token, 0.4);
        weights.insert(Level::Structural, 0.3);
        weights.insert(Level::DependencyChain, 0.1);
        weights.insert(Level::Relational, 0.1);
        weights.insert(Level::Architectural, 0.1);

        let level_scores = vec![
            LevelScore {
                level: Level::Token,
                z_score: 3.0,
                threshold: 2.5,
                is_surprising: true,
            },
            LevelScore {
                level: Level::Structural,
                z_score: 2.5,
                threshold: 2.0,
                is_surprising: true,
            },
            LevelScore {
                level: Level::DependencyChain,
                z_score: 1.0,
                threshold: 2.0,
                is_surprising: false,
            },
        ];

        let result = score_entity(level_scores, &weights);

        // compound_surprise = 0.4 * 3.0 + 0.3 * 2.5 = 1.2 + 0.75 = 1.95
        // (DependencyChain is not surprising, so excluded)
        assert!((result.compound_surprise - 1.95).abs() < 1e-9);
        assert_eq!(result.concordance, 2);
        assert_eq!(result.severity, Severity::Low);
    }

    #[test]
    fn test_score_entity_missing_weight_uses_default() {
        // Empty weights map → all levels use fallback weight of 0.2
        let weights: HashMap<Level, f64> = HashMap::new();

        let level_scores = vec![LevelScore {
            level: Level::Token,
            z_score: 5.0,
            threshold: 2.5,
            is_surprising: true,
        }];

        let result = score_entity(level_scores, &weights);

        // compound_surprise = 0.2 * 5.0 = 1.0
        assert!((result.compound_surprise - 1.0).abs() < 1e-9);
    }

    // --- helpers ---

    fn all_levels() -> [Level; 5] {
        [
            Level::Token,
            Level::Structural,
            Level::DependencyChain,
            Level::Relational,
            Level::Architectural,
        ]
    }

    fn uniform_weights() -> HashMap<Level, f64> {
        let mut w = HashMap::new();
        for level in all_levels() {
            w.insert(level, 0.2);
        }
        w
    }

    fn make_level_scores(surprising: &[bool]) -> Vec<LevelScore> {
        let levels = all_levels();
        surprising
            .iter()
            .enumerate()
            .map(|(i, &is_surprising)| LevelScore {
                level: levels[i],
                z_score: if is_surprising { 3.0 } else { 0.5 },
                threshold: 2.0,
                is_surprising,
            })
            .collect()
    }
}
