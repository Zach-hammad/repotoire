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
#[derive(Debug, Clone)]
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
        match &self.profile {
            Some(p) => {
                if p.get(kind).map_or(false, |d| d.confident) {
                    "adaptive"
                } else {
                    "default"
                }
            }
            None => "default",
        }
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

impl Default for ThresholdResolver {
    fn default() -> Self {
        Self { profile: None }
    }
}
