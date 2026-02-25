//! Threshold resolver — bridges style profiles with detector thresholds
//!
//! Guardrails against baseline poisoning:
//! 1. Collector excludes tests/generated/vendor from calibration data
//! 2. Minimum 40 samples before adaptive activates (confident flag)
//! 3. Floor/ceiling clamps — adaptive can't go below default or above 5× default
//! 4. Explainability — `source()` and `explain()` for findings

use crate::calibrate::profile::{MetricKind, StyleProfile};

/// Maximum multiplier: adaptive threshold can't exceed 5× the default.
/// This prevents a pathologically messy codebase from disabling detection entirely.
const MAX_CEILING_MULTIPLIER: f64 = 5.0;

/// Resolves thresholds from adaptive profile or defaults.
/// Detectors call `resolve.warn(MetricKind::X, default)` to get the right value.
#[derive(Debug, Clone, Default)]
pub struct ThresholdResolver {
    profile: Option<StyleProfile>,
}

impl ThresholdResolver {
    pub fn new(profile: Option<StyleProfile>) -> Self {
        Self { profile }
    }

    /// Clamp a threshold: floor = default, ceiling = default × MAX_CEILING_MULTIPLIER
    fn clamp(value: f64, default: f64) -> f64 {
        let ceiling = default * MAX_CEILING_MULTIPLIER;
        value.max(default).min(ceiling)
    }

    /// Get warn-level threshold: max(default, p90), clamped to ceiling
    pub fn warn(&self, kind: MetricKind, default: f64) -> f64 {
        match &self.profile {
            Some(p) => Self::clamp(p.threshold_warn(kind, default), default),
            None => default,
        }
    }

    /// Get high-level threshold: max(default, p95), clamped to ceiling
    pub fn high(&self, kind: MetricKind, default: f64) -> f64 {
        match &self.profile {
            Some(p) => Self::clamp(p.threshold_high(kind, default), default),
            None => default,
        }
    }

    /// Get warn threshold as usize
    pub fn warn_usize(&self, kind: MetricKind, default: usize) -> usize {
        self.warn(kind, default as f64) as usize
    }

    /// Get high threshold as usize
    pub fn high_usize(&self, kind: MetricKind, default: usize) -> usize {
        self.high(kind, default as f64) as usize
    }

    /// Whether we have an adaptive profile
    pub fn is_adaptive(&self) -> bool {
        self.profile.is_some()
    }

    /// Get the source label for a metric threshold
    pub fn source(&self, kind: MetricKind) -> &'static str {
        let is_adaptive = self.profile.as_ref()
            .and_then(|p| p.get(kind))
            .is_some_and(|d| d.confident);
        if is_adaptive { "adaptive" } else { "default" }
    }

    /// Build explainability metadata for a finding.
    /// Returns (threshold_source, metric_value_str, percentile_used) or None if no adaptive data.
    pub fn explain(
        &self,
        kind: MetricKind,
        actual_value: f64,
        default_threshold: f64,
    ) -> ThresholdExplanation {
        let source = self.source(kind);
        let effective = self.warn(kind, default_threshold);
        let percentile = match &self.profile {
            Some(p) => p.get(kind).map(|d| {
                format!(
                    "p90={:.0}, p95={:.0}, mean={:.1}, n={}",
                    d.p90, d.p95, d.mean, d.count
                )
            }),
            None => None,
        };
        ThresholdExplanation {
            threshold_source: source,
            effective_threshold: effective,
            default_threshold,
            actual_value,
            percentile_info: percentile,
        }
    }
}

/// Explainability metadata attached to findings when adaptive thresholds are active
#[derive(Debug, Clone)]
pub struct ThresholdExplanation {
    pub threshold_source: &'static str,
    pub effective_threshold: f64,
    pub default_threshold: f64,
    pub actual_value: f64,
    pub percentile_info: Option<String>,
}

impl ThresholdExplanation {
    /// Format as a human-readable note for finding descriptions
    pub fn to_note(&self) -> String {
        let mut parts = vec![
            format!(
                "Threshold: {:.0} ({})",
                self.effective_threshold, self.threshold_source
            ),
            format!("Actual: {:.0}", self.actual_value),
        ];
        if self.threshold_source == "adaptive" {
            parts.push(format!("Default would be: {:.0}", self.default_threshold));
            if let Some(ref pinfo) = self.percentile_info {
                parts.push(format!("Profile: {}", pinfo));
            }
        }
        parts.join(" | ")
    }

    /// Format as JSON-compatible metadata for SARIF/JSON export
    pub fn to_metadata(&self) -> Vec<(String, String)> {
        let mut meta = vec![
            (
                "threshold_source".to_string(),
                self.threshold_source.to_string(),
            ),
            (
                "effective_threshold".to_string(),
                format!("{:.0}", self.effective_threshold),
            ),
            (
                "actual_value".to_string(),
                format!("{:.0}", self.actual_value),
            ),
        ];
        if self.threshold_source == "adaptive" {
            meta.push((
                "default_threshold".to_string(),
                format!("{:.0}", self.default_threshold),
            ));
        }
        meta
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::calibrate::profile::{MetricDistribution, MetricKind, StyleProfile};
    use std::collections::HashMap;

    /// Build a StyleProfile with a single metric from the given values.
    fn profile_with_metric(kind: MetricKind, values: &mut [f64]) -> StyleProfile {
        let dist = MetricDistribution::from_values(values);
        let mut metrics = HashMap::new();
        metrics.insert(kind, dist);
        StyleProfile {
            version: StyleProfile::VERSION,
            generated_at: String::new(),
            commit_sha: None,
            total_files: 0,
            total_functions: values.len(),
            metrics,
        }
    }

    // ── No-profile (default) behaviour ──────────────────────────────────

    #[test]
    fn test_no_profile_returns_defaults() {
        let resolver = ThresholdResolver::new(None);
        assert_eq!(resolver.warn(MetricKind::Complexity, 10.0), 10.0);
        assert_eq!(resolver.high(MetricKind::Complexity, 20.0), 20.0);
        assert!(!resolver.is_adaptive());
        assert_eq!(resolver.source(MetricKind::Complexity), "default");
    }

    #[test]
    fn test_no_profile_usize_helpers() {
        let resolver = ThresholdResolver::new(None);
        assert_eq!(resolver.warn_usize(MetricKind::FunctionLength, 50), 50);
        assert_eq!(resolver.high_usize(MetricKind::NestingDepth, 5), 5);
    }

    // ── Adaptive with confident profile ─────────────────────────────────

    #[test]
    fn test_adaptive_uses_p90_when_higher_than_default() {
        // 100 values 1..=100 → p90 ≈ 90, well above default of 10
        let mut values: Vec<f64> = (1..=100).map(|i| i as f64).collect();
        let profile = profile_with_metric(MetricKind::Complexity, &mut values);
        let resolver = ThresholdResolver::new(Some(profile));

        let warn = resolver.warn(MetricKind::Complexity, 10.0);
        // p90 ≈ 90, default=10. Result should be clamped to ceiling = 10*5 = 50
        assert!(
            (warn - 50.0).abs() < 0.01,
            "expected ceiling-clamped 50, got {warn}"
        );
        assert!(resolver.is_adaptive());
        assert_eq!(resolver.source(MetricKind::Complexity), "adaptive");
    }

    #[test]
    fn test_adaptive_floor_never_below_default() {
        // All values are very low (1..=100 but default is 200)
        let mut values: Vec<f64> = (1..=100).map(|i| i as f64).collect();
        let profile = profile_with_metric(MetricKind::FunctionLength, &mut values);
        let resolver = ThresholdResolver::new(Some(profile));

        let warn = resolver.warn(MetricKind::FunctionLength, 200.0);
        assert!(
            (warn - 200.0).abs() < 0.01,
            "floor should clamp to default 200, got {warn}"
        );
    }

    #[test]
    fn test_adaptive_ceiling_clamps_at_5x_default() {
        // Very high p90 (e.g. 900), default=10 → ceiling=50
        let mut values: Vec<f64> = (1..=100).map(|i| i as f64 * 10.0).collect();
        let profile = profile_with_metric(MetricKind::Complexity, &mut values);
        let resolver = ThresholdResolver::new(Some(profile));

        let warn = resolver.warn(MetricKind::Complexity, 10.0);
        assert!(
            (warn - 50.0).abs() < 0.01,
            "ceiling should clamp to 5*10=50, got {warn}"
        );

        let high = resolver.high(MetricKind::Complexity, 10.0);
        assert!(
            (high - 50.0).abs() < 0.01,
            "high ceiling should also clamp to 50, got {high}"
        );
    }

    // ── Small sample → not confident → falls back to default ────────────

    #[test]
    fn test_small_sample_falls_back_to_default() {
        let mut values = vec![1.0, 2.0, 3.0]; // n=3, below 40 threshold
        let profile = profile_with_metric(MetricKind::NestingDepth, &mut values);
        let resolver = ThresholdResolver::new(Some(profile));

        // Should report "default" because the distribution is not confident
        assert_eq!(resolver.source(MetricKind::NestingDepth), "default");
        assert_eq!(resolver.warn(MetricKind::NestingDepth, 5.0), 5.0);
        assert_eq!(resolver.high(MetricKind::NestingDepth, 8.0), 8.0);
    }

    // ── Missing metric in profile → falls back to default ───────────────

    #[test]
    fn test_missing_metric_returns_default() {
        // Profile has Complexity but we ask for FanOut
        let mut values: Vec<f64> = (1..=100).map(|i| i as f64).collect();
        let profile = profile_with_metric(MetricKind::Complexity, &mut values);
        let resolver = ThresholdResolver::new(Some(profile));

        assert_eq!(resolver.warn(MetricKind::FanOut, 7.0), 7.0);
        assert_eq!(resolver.high(MetricKind::FanOut, 12.0), 12.0);
    }

    // ── ThresholdExplanation formatting ─────────────────────────────────

    #[test]
    fn test_explain_default_source() {
        let resolver = ThresholdResolver::new(None);
        let explanation = resolver.explain(MetricKind::Complexity, 15.0, 10.0);

        assert_eq!(explanation.threshold_source, "default");
        assert!((explanation.effective_threshold - 10.0).abs() < 0.01);
        assert!((explanation.actual_value - 15.0).abs() < 0.01);
        assert!(explanation.percentile_info.is_none());
    }

    #[test]
    fn test_explain_adaptive_includes_percentile_info() {
        let mut values: Vec<f64> = (1..=100).map(|i| i as f64).collect();
        let profile = profile_with_metric(MetricKind::Complexity, &mut values);
        let resolver = ThresholdResolver::new(Some(profile));

        let explanation = resolver.explain(MetricKind::Complexity, 55.0, 10.0);
        assert_eq!(explanation.threshold_source, "adaptive");
        assert!(explanation.percentile_info.is_some());
        let info = explanation.percentile_info.unwrap();
        assert!(info.contains("p90="));
        assert!(info.contains("p95="));
        assert!(info.contains("mean="));
        assert!(info.contains("n=100"));
    }

    #[test]
    fn test_to_note_default_format() {
        let explanation = ThresholdExplanation {
            threshold_source: "default",
            effective_threshold: 10.0,
            default_threshold: 10.0,
            actual_value: 15.0,
            percentile_info: None,
        };
        let note = explanation.to_note();
        assert!(note.contains("Threshold: 10 (default)"));
        assert!(note.contains("Actual: 15"));
        // Should NOT contain "Default would be" for default source
        assert!(!note.contains("Default would be"));
    }

    #[test]
    fn test_to_note_adaptive_format() {
        let explanation = ThresholdExplanation {
            threshold_source: "adaptive",
            effective_threshold: 50.0,
            default_threshold: 10.0,
            actual_value: 55.0,
            percentile_info: Some("p90=45, p95=48, mean=25.0, n=200".to_string()),
        };
        let note = explanation.to_note();
        assert!(note.contains("Threshold: 50 (adaptive)"));
        assert!(note.contains("Default would be: 10"));
        assert!(note.contains("Profile: p90=45"));
    }

    #[test]
    fn test_to_metadata_default_has_three_entries() {
        let explanation = ThresholdExplanation {
            threshold_source: "default",
            effective_threshold: 10.0,
            default_threshold: 10.0,
            actual_value: 15.0,
            percentile_info: None,
        };
        let meta = explanation.to_metadata();
        assert_eq!(meta.len(), 3);
    }

    #[test]
    fn test_to_metadata_adaptive_has_four_entries() {
        let explanation = ThresholdExplanation {
            threshold_source: "adaptive",
            effective_threshold: 50.0,
            default_threshold: 10.0,
            actual_value: 55.0,
            percentile_info: Some("info".to_string()),
        };
        let meta = explanation.to_metadata();
        assert_eq!(meta.len(), 4);
        // Should include default_threshold entry
        assert!(meta.iter().any(|(k, _)| k == "default_threshold"));
    }
}

