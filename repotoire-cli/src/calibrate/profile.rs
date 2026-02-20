//! Style profile schema and I/O

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::Path;

/// What metric this distribution describes
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MetricKind {
    /// Cyclomatic complexity per function
    Complexity,
    /// Lines per function
    FunctionLength,
    /// Nesting depth per function
    NestingDepth,
    /// Parameters per function
    ParameterCount,
    /// Lines per file
    FileLength,
    /// Methods per class
    ClassMethodCount,
    /// Fan-in (callers) per function
    FanIn,
    /// Fan-out (callees) per function
    FanOut,
}

impl MetricKind {
    pub fn all() -> &'static [MetricKind] {
        &[
            MetricKind::Complexity,
            MetricKind::FunctionLength,
            MetricKind::NestingDepth,
            MetricKind::ParameterCount,
            MetricKind::FileLength,
            MetricKind::ClassMethodCount,
            MetricKind::FanIn,
            MetricKind::FanOut,
        ]
    }

    pub fn name(&self) -> &'static str {
        match self {
            MetricKind::Complexity => "complexity",
            MetricKind::FunctionLength => "function_length",
            MetricKind::NestingDepth => "nesting_depth",
            MetricKind::ParameterCount => "parameter_count",
            MetricKind::FileLength => "file_length",
            MetricKind::ClassMethodCount => "class_method_count",
            MetricKind::FanIn => "fan_in",
            MetricKind::FanOut => "fan_out",
        }
    }
}

/// Statistical distribution for a single metric
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MetricDistribution {
    /// Number of data points (functions, files, classes)
    pub count: usize,
    pub mean: f64,
    pub stddev: f64,
    pub p50: f64,
    pub p75: f64,
    pub p90: f64,
    pub p95: f64,
    pub max: f64,
    /// Minimum sample size met for reliable thresholds
    pub confident: bool,
}

impl MetricDistribution {
    /// Compute distribution from a sorted list of values.
    pub fn from_values(values: &mut [f64]) -> Self {
        if values.is_empty() {
            return Self {
                count: 0,
                mean: 0.0,
                stddev: 0.0,
                p50: 0.0,
                p75: 0.0,
                p90: 0.0,
                p95: 0.0,
                max: 0.0,
                confident: false,
            };
        }

        values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let n = values.len();
        let mean = values.iter().sum::<f64>() / n as f64;
        let variance = values.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / n as f64;
        let stddev = variance.sqrt();

        Self {
            count: n,
            mean,
            stddev,
            p50: percentile(values, 50.0),
            p75: percentile(values, 75.0),
            p90: percentile(values, 90.0),
            p95: percentile(values, 95.0),
            max: *values.last().unwrap_or(&0.0),
            confident: n >= 40,
        }
    }

    /// Get adaptive threshold: max(default, p90) — only if confident.
    pub fn adaptive_warn(&self, default: f64) -> f64 {
        if !self.confident {
            return default;
        }
        default.max(self.p90)
    }

    /// Get adaptive high threshold: max(default, p95) — only if confident.
    pub fn adaptive_high(&self, default: f64) -> f64 {
        if !self.confident {
            return default;
        }
        default.max(self.p95)
    }
}

fn percentile(sorted: &[f64], pct: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let idx = (pct / 100.0 * (sorted.len() - 1) as f64).round() as usize;
    sorted[idx.min(sorted.len() - 1)]
}

/// Complete style profile for a repository
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StyleProfile {
    /// Schema version
    pub version: u32,
    /// When the profile was generated
    pub generated_at: String,
    /// Git commit SHA at calibration time (if available)
    pub commit_sha: Option<String>,
    /// Total files analyzed
    pub total_files: usize,
    /// Total functions analyzed
    pub total_functions: usize,
    /// Distributions keyed by MetricKind
    pub metrics: HashMap<MetricKind, MetricDistribution>,
}

impl StyleProfile {
    pub const VERSION: u32 = 1;
    pub const FILENAME: &'static str = "style-profile.json";

    /// Load profile from the .repotoire directory
    pub fn load(repo_path: &Path) -> Option<Self> {
        let profile_path = repo_path.join(".repotoire").join(Self::FILENAME);
        let data = std::fs::read_to_string(&profile_path).ok()?;
        let profile: Self = serde_json::from_str(&data).ok()?;
        if profile.version != Self::VERSION {
            tracing::warn!(
                "Style profile version mismatch ({} vs {}), ignoring",
                profile.version,
                Self::VERSION
            );
            return None;
        }
        Some(profile)
    }

    /// Save profile to the .repotoire directory
    pub fn save(&self, repo_path: &Path) -> anyhow::Result<()> {
        let dir = repo_path.join(".repotoire");
        std::fs::create_dir_all(&dir)?;
        let path = dir.join(Self::FILENAME);
        let json = serde_json::to_string_pretty(self)?;
        std::fs::write(&path, json)?;
        Ok(())
    }

    /// Get distribution for a metric kind
    pub fn get(&self, kind: MetricKind) -> Option<&MetricDistribution> {
        self.metrics.get(&kind)
    }

    /// Get adaptive threshold for a metric, falling back to default
    pub fn threshold_warn(&self, kind: MetricKind, default: f64) -> f64 {
        self.metrics
            .get(&kind)
            .map(|d| d.adaptive_warn(default))
            .unwrap_or(default)
    }

    /// Get adaptive high threshold for a metric, falling back to default
    pub fn threshold_high(&self, kind: MetricKind, default: f64) -> f64 {
        self.metrics
            .get(&kind)
            .map(|d| d.adaptive_high(default))
            .unwrap_or(default)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_distribution_from_values() {
        let mut values: Vec<f64> = (1..=100).map(|i| i as f64).collect();
        let dist = MetricDistribution::from_values(&mut values);

        assert_eq!(dist.count, 100);
        assert!((dist.mean - 50.5).abs() < 0.1);
        assert!((dist.p50 - 50.5).abs() < 2.0);
        assert!((dist.p90 - 90.0).abs() < 2.0);
        assert!((dist.p95 - 95.0).abs() < 2.0);
        assert!((dist.max - 100.0).abs() < 0.1);
        assert!(dist.confident);
    }

    #[test]
    fn test_small_sample_not_confident() {
        let mut values = vec![1.0, 2.0, 3.0];
        let dist = MetricDistribution::from_values(&mut values);
        assert!(!dist.confident);
    }

    #[test]
    fn test_adaptive_threshold_uses_default_when_not_confident() {
        let mut values = vec![1.0, 2.0, 3.0];
        let dist = MetricDistribution::from_values(&mut values);
        assert_eq!(dist.adaptive_warn(10.0), 10.0); // Falls back to default
    }

    #[test]
    fn test_adaptive_threshold_uses_p90_when_higher() {
        let mut values: Vec<f64> = (1..=100).map(|i| i as f64).collect();
        let dist = MetricDistribution::from_values(&mut values);
        // p90 ≈ 90, default = 5 → should use 90
        assert!(dist.adaptive_warn(5.0) >= 89.0);
        // p90 ≈ 90, default = 100 → should use 100 (default is higher)
        assert_eq!(dist.adaptive_warn(100.0), 100.0);
    }

    #[test]
    fn test_empty_values() {
        let mut values: Vec<f64> = vec![];
        let dist = MetricDistribution::from_values(&mut values);
        assert_eq!(dist.count, 0);
        assert!(!dist.confident);
    }
}
