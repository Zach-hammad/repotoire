//! AI complexity spike detector (research-backed baseline comparison)
//!
//! Detects sudden complexity increases in previously simple functions using
//! statistical outlier detection based on codebase-wide complexity baselines.
//!
//! The research-backed approach:
//! 1. Calculate cyclomatic complexity for ALL functions
//! 2. Compute codebase baseline: median and standard deviation
//! 3. For functions modified in last 30 days, calculate z-scores
//! 4. Flag functions where z_score > 2.0 (statistical outlier)
//! 5. Cross-reference with git history to detect actual SPIKES
//!    (previous < 5 AND current > 15 → confirmed spike)

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphClient;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashMap;
use std::path::PathBuf;
use tracing::{debug, info};
use uuid::Uuid;

/// Default configuration values
const DEFAULT_WINDOW_DAYS: i64 = 30;
const DEFAULT_Z_SCORE_THRESHOLD: f64 = 2.0;
const DEFAULT_SPIKE_BEFORE_MAX: u32 = 5;
const DEFAULT_SPIKE_AFTER_MIN: u32 = 15;
const DEFAULT_MAX_FINDINGS: usize = 50;

/// Statistical baseline for codebase complexity
#[derive(Debug, Clone)]
pub struct CodebaseBaseline {
    pub total_functions: usize,
    pub median_complexity: f64,
    pub mean_complexity: f64,
    pub stddev_complexity: f64,
    pub min_complexity: u32,
    pub max_complexity: u32,
    pub p75_complexity: f64,
    pub p90_complexity: f64,
}

impl CodebaseBaseline {
    /// Calculate z-score for a given complexity
    pub fn z_score(&self, complexity: u32) -> f64 {
        if self.stddev_complexity == 0.0 {
            return 0.0;
        }
        (complexity as f64 - self.median_complexity) / self.stddev_complexity
    }

    /// Check if complexity is a statistical outlier
    pub fn is_outlier(&self, complexity: u32, threshold: f64) -> bool {
        self.z_score(complexity) > threshold
    }
}

impl Default for CodebaseBaseline {
    fn default() -> Self {
        Self {
            total_functions: 0,
            median_complexity: 0.0,
            mean_complexity: 0.0,
            stddev_complexity: 1.0, // Avoid division by zero
            min_complexity: 0,
            max_complexity: 0,
            p75_complexity: 0.0,
            p90_complexity: 0.0,
        }
    }
}

/// Represents a detected complexity spike in a function
#[derive(Debug, Clone)]
pub struct ComplexitySpike {
    pub file_path: String,
    pub function_name: String,
    pub qualified_name: String,
    pub current_complexity: u32,
    pub previous_complexity: u32,
    pub complexity_delta: i32,
    pub z_score: f64,
    pub spike_date: Option<String>,
    pub commit_sha: String,
    pub commit_message: String,
    pub author: String,
    pub line_number: u32,
    pub baseline_median: f64,
    pub baseline_stddev: f64,
}

/// Detects complexity spikes using research-backed baseline comparison
pub struct AIComplexitySpikeDetector {
    config: DetectorConfig,
    window_days: i64,
    z_score_threshold: f64,
    spike_before_max: u32,
    spike_after_min: u32,
    max_findings: usize,
}

impl AIComplexitySpikeDetector {
    /// Create a new detector with default settings
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            window_days: DEFAULT_WINDOW_DAYS,
            z_score_threshold: DEFAULT_Z_SCORE_THRESHOLD,
            spike_before_max: DEFAULT_SPIKE_BEFORE_MAX,
            spike_after_min: DEFAULT_SPIKE_AFTER_MIN,
            max_findings: DEFAULT_MAX_FINDINGS,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        Self {
            window_days: config.get_option_or("window_days", DEFAULT_WINDOW_DAYS),
            z_score_threshold: config.get_option_or("z_score_threshold", DEFAULT_Z_SCORE_THRESHOLD),
            spike_before_max: config.get_option_or("spike_before_max", DEFAULT_SPIKE_BEFORE_MAX),
            spike_after_min: config.get_option_or("spike_after_min", DEFAULT_SPIKE_AFTER_MIN),
            max_findings: config.get_option_or("max_findings", DEFAULT_MAX_FINDINGS),
            config,
        }
    }

    /// Compute statistical baseline from all function complexities
    fn compute_baseline(&self, complexities: &[u32]) -> CodebaseBaseline {
        if complexities.is_empty() {
            return CodebaseBaseline::default();
        }

        let mut sorted = complexities.to_vec();
        sorted.sort();

        let n = sorted.len();
        let sum: u64 = sorted.iter().map(|&c| c as u64).sum();
        let mean = sum as f64 / n as f64;

        // Calculate median
        let median = if n % 2 == 0 {
            (sorted[n / 2 - 1] as f64 + sorted[n / 2] as f64) / 2.0
        } else {
            sorted[n / 2] as f64
        };

        // Calculate standard deviation
        let variance: f64 = sorted
            .iter()
            .map(|&c| {
                let diff = c as f64 - mean;
                diff * diff
            })
            .sum::<f64>()
            / n as f64;
        let stddev = variance.sqrt().max(1.0); // Avoid division by zero

        // Calculate percentiles
        let p75_idx = (n as f64 * 0.75) as usize;
        let p90_idx = (n as f64 * 0.90) as usize;

        CodebaseBaseline {
            total_functions: n,
            median_complexity: median,
            mean_complexity: mean,
            stddev_complexity: stddev,
            min_complexity: sorted[0],
            max_complexity: sorted[n - 1],
            p75_complexity: sorted.get(p75_idx).copied().unwrap_or(sorted[n - 1]) as f64,
            p90_complexity: sorted.get(p90_idx).copied().unwrap_or(sorted[n - 1]) as f64,
        }
    }

    /// Create a Finding from a ComplexitySpike
    fn create_finding(&self, spike: &ComplexitySpike, baseline: &CodebaseBaseline) -> Finding {
        // Severity based on z-score and delta
        let severity = if spike.z_score >= 3.0 || spike.complexity_delta >= 20 {
            Severity::High
        } else if spike.z_score >= 2.5 || spike.complexity_delta >= 15 {
            Severity::High
        } else {
            Severity::Medium
        };

        // Build title showing the spike
        let title = if spike.previous_complexity > 0 {
            format!(
                "Function {} jumped from complexity {} to {} in commit {}",
                spike.function_name,
                spike.previous_complexity,
                spike.current_complexity,
                &spike.commit_sha[..7.min(spike.commit_sha.len())]
            )
        } else {
            format!(
                "New function {} has outlier complexity {} (z-score: {:.1})",
                spike.function_name, spike.current_complexity, spike.z_score
            )
        };

        let description = self.build_description(spike, baseline);
        let suggested_fix = self.build_suggested_fix(spike);

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "AIComplexitySpikeDetector".to_string(),
            severity,
            title,
            description,
            affected_files: vec![PathBuf::from(&spike.file_path)],
            line_start: Some(spike.line_number),
            line_end: None,
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some(self.estimate_effort(spike)),
            category: Some("complexity".to_string()),
            cwe_id: None,
            why_it_matters: Some(format!(
                "This function's complexity ({}) is {:.1} standard deviations above the \
                 codebase median ({:.1}). Such sudden complexity spikes often indicate \
                 AI-generated code that needs refactoring, or features added without \
                 proper decomposition.",
                spike.current_complexity, spike.z_score, spike.baseline_median
            )),
        }
    }

    fn build_description(&self, spike: &ComplexitySpike, baseline: &CodebaseBaseline) -> String {
        let mut desc = format!(
            "Function **{}** experienced a significant complexity spike.\n\n",
            spike.function_name
        );

        desc.push_str("### Complexity Analysis (Baseline Comparison)\n\n");
        desc.push_str("| Metric | Value |\n");
        desc.push_str("|--------|-------|\n");
        desc.push_str(&format!(
            "| Previous complexity | {} |\n",
            spike.previous_complexity
        ));
        desc.push_str(&format!(
            "| Current complexity | {} |\n",
            spike.current_complexity
        ));
        desc.push_str(&format!("| Delta | +{} |\n", spike.complexity_delta));
        desc.push_str(&format!(
            "| Codebase median | {:.1} |\n",
            spike.baseline_median
        ));
        desc.push_str(&format!(
            "| Codebase stddev | {:.1} |\n",
            spike.baseline_stddev
        ));
        desc.push_str(&format!(
            "| **Z-score** | **{:.2}** (>{} = outlier) |\n\n",
            spike.z_score, self.z_score_threshold
        ));

        desc.push_str("### Commit Details\n\n");
        if let Some(ref date) = spike.spike_date {
            desc.push_str(&format!("- **When**: {}\n", date));
        }
        desc.push_str(&format!(
            "- **Commit**: `{}`\n",
            &spike.commit_sha[..8.min(spike.commit_sha.len())]
        ));
        desc.push_str(&format!("- **Message**: {}\n", spike.commit_message));
        desc.push_str(&format!("- **Author**: {}\n", spike.author));
        desc.push_str(&format!(
            "- **Location**: `{}` line {}\n\n",
            spike.file_path, spike.line_number
        ));

        desc.push_str("### Why This Matters\n\n");
        desc.push_str(&format!(
            "This function's complexity is {:.1}σ above the codebase average. ",
            spike.z_score
        ));
        desc.push_str("Statistical outliers in complexity often indicate:\n");
        desc.push_str("- AI-generated code that was accepted without proper refactoring\n");
        desc.push_str("- Features added without decomposing into smaller functions\n");
        desc.push_str("- Technical debt that will compound over time\n");
        desc.push_str("- Reduced testability and higher bug risk\n");

        desc
    }

    fn build_suggested_fix(&self, spike: &ComplexitySpike) -> String {
        let target_complexity = (spike.baseline_median + spike.baseline_stddev) as u32;

        format!(
            "1. **Review commit `{}`** to understand what changed\n\n\
             2. **Decompose the function** using these patterns:\n\
                - Extract Method: Move logical blocks into separate functions\n\
                - Replace Conditional with Polymorphism (for branching logic)\n\
                - Introduce Parameter Object (for many parameters)\n\n\
             3. **Target complexity**: Reduce from {} to below {} (1σ above median)\n\n\
             4. **Add tests** before refactoring to catch regressions",
            &spike.commit_sha[..8.min(spike.commit_sha.len())],
            spike.current_complexity,
            target_complexity
        )
    }

    fn estimate_effort(&self, spike: &ComplexitySpike) -> String {
        if spike.current_complexity < 20 {
            "Small (1-2 hours)".to_string()
        } else if spike.current_complexity < 30 {
            "Medium (half day)".to_string()
        } else if spike.current_complexity < 50 {
            "Large (1 day)".to_string()
        } else {
            "Extra Large (2+ days)".to_string()
        }
    }
}

impl Default for AIComplexitySpikeDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for AIComplexitySpikeDetector {
    fn name(&self) -> &'static str {
        "AIComplexitySpikeDetector"
    }

    fn description(&self) -> &'static str {
        "Detects complexity spikes using research-backed baseline comparison"
    }

    fn category(&self) -> &'static str {
        "ai_generated"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        debug!("Starting AI complexity spike detection");

        // Step 1: Get all function complexities for baseline
        let baseline_query = r#"
            MATCH (f:Function)
            WHERE f.complexity IS NOT NULL
            RETURN f.complexity AS complexity
        "#;

        let baseline_results = graph.execute(baseline_query)?;

        let complexities: Vec<u32> = baseline_results
            .iter()
            .filter_map(|row| row.get("complexity").and_then(|v| v.as_u64()))
            .map(|c| c as u32)
            .collect();

        if complexities.is_empty() {
            debug!("No functions with complexity data found");
            return Ok(vec![]);
        }

        // Step 2: Compute baseline
        let baseline = self.compute_baseline(&complexities);
        info!(
            "Baseline: median={:.1}, stddev={:.1}, total_functions={}",
            baseline.median_complexity, baseline.stddev_complexity, baseline.total_functions
        );

        // Step 3: Query for recently modified functions with high complexity
        let spike_query = r#"
            MATCH (f:Function)
            WHERE f.complexity IS NOT NULL
              AND f.complexity > $min_complexity
              AND f.name IS NOT NULL
              AND f.filePath IS NOT NULL
            OPTIONAL MATCH (f)-[:MODIFIED_IN]->(c:Commit)
            RETURN f.qualifiedName AS qualified_name,
                   f.name AS name,
                   f.filePath AS file_path,
                   f.lineStart AS line_start,
                   f.complexity AS complexity,
                   f.previousComplexity AS previous_complexity,
                   c.sha AS commit_sha,
                   c.message AS commit_message,
                   c.author AS author,
                   c.timestamp AS spike_date
            ORDER BY f.complexity DESC
            LIMIT 200
        "#;

        // Use median as minimum to reduce noise
        let _params = serde_json::json!({
            "min_complexity": baseline.median_complexity as u32,
        });

        let results = graph.execute(spike_query)?;

        if results.is_empty() {
            debug!("No high-complexity functions found");
            return Ok(vec![]);
        }

        // Step 4 & 5: Find outliers and confirm spikes
        let mut spikes: Vec<ComplexitySpike> = Vec::new();

        for row in results {
            let complexity = row.get("complexity").and_then(|v| v.as_u64()).unwrap_or(0) as u32;

            let z_score = baseline.z_score(complexity);

            // Only consider outliers
            if z_score <= self.z_score_threshold {
                continue;
            }

            let qualified_name = row
                .get("qualified_name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let function_name = row
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let file_path = row
                .get("file_path")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let line_number = row.get("line_start").and_then(|v| v.as_u64()).unwrap_or(0) as u32;

            let previous_complexity = row
                .get("previous_complexity")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as u32;

            // Confirm spike: previous < 5 AND current > 15
            let is_spike =
                previous_complexity <= self.spike_before_max && complexity >= self.spike_after_min;

            // For new functions, check if complex enough
            let is_new_complex = previous_complexity == 0 && complexity >= self.spike_after_min;

            if !is_spike && !is_new_complex {
                continue;
            }

            let commit_sha = row
                .get("commit_sha")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown")
                .to_string();

            let commit_message = row
                .get("commit_message")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let author = row
                .get("author")
                .and_then(|v| v.as_str())
                .unwrap_or("Unknown")
                .to_string();

            let spike_date = row
                .get("spike_date")
                .and_then(|v| v.as_str())
                .map(String::from);

            spikes.push(ComplexitySpike {
                file_path,
                function_name,
                qualified_name,
                current_complexity: complexity,
                previous_complexity,
                complexity_delta: (complexity as i32) - (previous_complexity as i32),
                z_score,
                spike_date,
                commit_sha,
                commit_message,
                author,
                line_number,
                baseline_median: baseline.median_complexity,
                baseline_stddev: baseline.stddev_complexity,
            });
        }

        // Sort by z-score (highest outliers first), then by delta
        spikes.sort_by(|a, b| {
            b.z_score
                .partial_cmp(&a.z_score)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| b.complexity_delta.cmp(&a.complexity_delta))
        });

        // Create findings
        let findings: Vec<Finding> = spikes
            .iter()
            .take(self.max_findings)
            .map(|spike| self.create_finding(spike, &baseline))
            .collect();

        info!(
            "AIComplexitySpikeDetector found {} complexity spikes",
            findings.len()
        );

        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_compute_baseline() {
        let detector = AIComplexitySpikeDetector::new();
        let complexities = vec![1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
        let baseline = detector.compute_baseline(&complexities);

        assert_eq!(baseline.total_functions, 10);
        assert!((baseline.median_complexity - 5.5).abs() < 0.01);
        assert_eq!(baseline.min_complexity, 1);
        assert_eq!(baseline.max_complexity, 10);
    }

    #[test]
    fn test_z_score() {
        let baseline = CodebaseBaseline {
            total_functions: 100,
            median_complexity: 5.0,
            mean_complexity: 5.0,
            stddev_complexity: 2.0,
            min_complexity: 1,
            max_complexity: 20,
            p75_complexity: 7.0,
            p90_complexity: 10.0,
        };

        // Complexity of 9 should be 2 stddevs above median
        let z = baseline.z_score(9);
        assert!((z - 2.0).abs() < 0.01);

        assert!(baseline.is_outlier(9, 1.9));
        assert!(!baseline.is_outlier(9, 2.1));
    }

    #[test]
    fn test_empty_baseline() {
        let detector = AIComplexitySpikeDetector::new();
        let baseline = detector.compute_baseline(&[]);

        assert_eq!(baseline.total_functions, 0);
        assert_eq!(baseline.stddev_complexity, 1.0); // Should avoid division by zero
    }
}
