//! Threshold resolver — bridges style profiles with detector thresholds

use crate::calibrate::profile::{MetricKind, StyleProfile};

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

    /// Get warn-level threshold: max(default, p90) if confident profile exists
    pub fn warn(&self, kind: MetricKind, default: f64) -> f64 {
        match &self.profile {
            Some(p) => p.threshold_warn(kind, default),
            None => default,
        }
    }

    /// Get high-level threshold: max(default, p95) if confident profile exists
    pub fn high(&self, kind: MetricKind, default: f64) -> f64 {
        match &self.profile {
            Some(p) => p.threshold_high(kind, default),
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
}

impl Default for ThresholdResolver {
    fn default() -> Self {
        Self { profile: None }
    }
}
