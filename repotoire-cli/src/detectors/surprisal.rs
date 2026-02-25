//! Surprisal Detector — Predictive Coding for Code Analysis
//!
//! Flags functions whose token patterns are statistically unusual compared to
//! the rest of the project. Based on the "naturalness of software" research:
//! buggy and AI-generated code tends to have higher surprisal (entropy).
//!
//! How it works:
//! 1. An n-gram model is trained on the project's source during calibration
//! 2. Each function is scored: how "surprising" is its token sequence?
//! 3. Functions with surprisal > mean + 2σ are flagged
//!
//! This catches:
//! - AI-generated code that doesn't match project style
//! - Copy-pasted code from different codebases
//! - Unusual patterns that may indicate bugs
//! - Style drift over time

use crate::calibrate::NgramModel;
use crate::detectors::base::Detector;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::{Path, PathBuf};
use tracing::info;

pub struct SurprisalDetector {
    repository_path: PathBuf,
    model: NgramModel,
    max_findings: usize,
}

impl SurprisalDetector {
    pub fn new(repository_path: impl Into<PathBuf>, model: NgramModel) -> Self {
        Self {
            repository_path: repository_path.into(),
            model,
            max_findings: 30,
        }
    }

    /// Score all functions in a file and return findings for unusual ones
    fn analyze_file(
        &self,
        path: &Path,
        content: &str,
        graph: &dyn crate::graph::GraphQuery,
        baseline_mean: f64,
        baseline_std: f64,
    ) -> Vec<Finding> {
        let mut findings = Vec::new();
        let rel_path = path.strip_prefix(&self.repository_path).unwrap_or(path);
        let rel_str = rel_path.to_string_lossy();
        let lines: Vec<&str> = content.lines().collect();

        // Get functions from the graph for this file
        let functions: Vec<_> = graph.get_functions().into_iter()
            .filter(|f| f.file_path == *rel_str || rel_str.ends_with(&f.file_path))
            .collect();

        for func in &functions {
            let start = func.line_start.saturating_sub(1) as usize;
            let end = (func.line_end as usize).min(lines.len());
            if start >= end || end - start < 8 {
                continue; // Skip small functions — they're naturally more variable
            }

            let func_lines = &lines[start..end];

            // Respect inline suppression (check function lines + line before function)
            let prev_before_func = if start > 0 { lines.get(start - 1).copied() } else { None };
            let suppressed = func_lines.iter().enumerate().any(|(i, line)| {
                let prev = if i > 0 { Some(func_lines[i - 1]) } else { prev_before_func };
                super::is_line_suppressed_for(line, prev, "surprisal")
            });
            if suppressed {
                continue;
            }

            let (avg_surprisal, max_surprisal, peak_line) = self.model.function_surprisal(func_lines);

            if avg_surprisal <= 0.0 {
                continue;
            }

            // Flag if surprisal is significantly above baseline
            let z_score = if baseline_std > 0.0 {
                (avg_surprisal - baseline_mean) / baseline_std
            } else {
                0.0
            };

            // z > 2.0 = top ~2.5% most unusual functions
            if z_score < 2.0 {
                continue;
            }

            let severity = if z_score > 3.5 {
                Severity::High
            } else if z_score > 2.5 {
                Severity::Medium
            } else {
                Severity::Low
            };

            let _peak_line_num = start + peak_line + 1;
            let peak_content = func_lines.get(peak_line)
                .map(|l| l.trim())
                .unwrap_or("")
                .chars().take(80).collect::<String>();

            findings.push(Finding {
                id: String::new(),
                detector: "SurprisalDetector".to_string(),
                severity,
                title: format!(
                    "Unusual code pattern in `{}`",
                    func.name
                ),
                description: format!(
                    "Function `{}` has unusually high surprisal ({:.1} bits, project mean: {:.1}, z-score: {:.1}).\n\n\
                     This code doesn't match the typical patterns in this project. \
                     Most surprising line ({:.1} bits):\n```\n{}\n```\n\n\
                     **Possible causes:**\n\
                     - AI-generated code with different style\n\
                     - Copy-pasted from a different codebase\n\
                     - Unusual algorithm or pattern\n\
                     - Potential bug (buggy code tends to be more surprising)",
                    func.name, avg_surprisal, baseline_mean, z_score,
                    max_surprisal, peak_content
                ),
                affected_files: vec![path.to_path_buf()],
                line_start: Some(func.line_start),
                line_end: Some(func.line_end),
                suggested_fix: Some(
                    "Review this function for:\n\
                     1. Style consistency with the rest of the project\n\
                     2. Correctness — unusual patterns may indicate bugs\n\
                     3. If AI-generated, verify it does what you expect".to_string()
                ),
                estimated_effort: Some("15 minutes".to_string()),
                category: Some("ai-quality".to_string()),
                cwe_id: None,
                why_it_matters: Some(format!(
                    "Research shows that buggy code lines have significantly higher entropy \
                     than correct code (Ray & Hellendoorn, 2015). This function's token patterns \
                     are in the top {:.1}% most unusual in this project.",
                    (1.0 - normal_cdf(z_score)) * 100.0
                )),
                threshold_metadata: [
                    ("threshold_source".to_string(), "predictive".to_string()),
                    ("surprisal_bits".to_string(), format!("{:.2}", avg_surprisal)),
                    ("baseline_mean".to_string(), format!("{:.2}", baseline_mean)),
                    ("baseline_std".to_string(), format!("{:.2}", baseline_std)),
                    ("z_score".to_string(), format!("{:.2}", z_score)),
                ].into_iter().collect(),
                ..Default::default()
            });
        }

        findings
    }
}

impl Detector for SurprisalDetector {
    fn name(&self) -> &'static str {
        "surprisal"
    }

    fn description(&self) -> &'static str {
        "Detects statistically unusual code patterns using predictive coding"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        if !self.model.is_confident() {
            info!(
                "SurprisalDetector: skipping analysis — n-gram model is not confident \
                 (insufficient training data). Run calibration on a larger codebase to enable."
            );
            return Ok(vec![]);
        }

        let mut all_surprisals = Vec::new();
        let mut file_data: Vec<(PathBuf, String)> = Vec::new();

        // First pass: compute per-function surprisal to build baseline
        for path in files.files_with_extensions(&["rs", "py", "ts", "tsx", "js", "jsx", "go", "java",
                "c", "cpp", "cc", "h", "hpp", "cs", "kt"]) {
            if crate::detectors::content_classifier::is_non_production_path(
                &path.to_string_lossy()
            ) {
                continue;
            }

            if let Some(content) = files.content(path) {
                let lines: Vec<&str> = content.lines().collect();
                let rel_path = path.strip_prefix(&self.repository_path).unwrap_or(path);
                let rel_str = rel_path.to_string_lossy();

                let functions: Vec<_> = graph.get_functions().into_iter()
                    .filter(|f| f.file_path == *rel_str || rel_str.ends_with(&f.file_path))
                    .collect();

                for func in &functions {
                    let start = func.line_start.saturating_sub(1) as usize;
                    let end = (func.line_end as usize).min(lines.len());
                    if start >= end || end - start < 8 { continue; }

                    let func_lines = &lines[start..end];
                    let (avg, _, _) = self.model.function_surprisal(func_lines);
                    if avg > 0.0 {
                        all_surprisals.push(avg);
                    }
                }

                file_data.push((path.to_path_buf(), content.to_string()));
            }
        }

        if all_surprisals.len() < 20 {
            info!("SurprisalDetector: not enough functions to establish baseline ({})", all_surprisals.len());
            return Ok(vec![]);
        }

        // Compute baseline statistics
        let n = all_surprisals.len() as f64;
        let mean = all_surprisals.iter().sum::<f64>() / n;
        let variance = all_surprisals.iter().map(|s| (s - mean).powi(2)).sum::<f64>() / n;
        let std = variance.sqrt();

        info!(
            "SurprisalDetector baseline: mean={:.2} bits, std={:.2}, n={} functions",
            mean, std, all_surprisals.len()
        );

        // Second pass: flag unusual functions
        let mut findings = Vec::new();
        for (path, content) in &file_data {
            if findings.len() >= self.max_findings { break; }
            let mut file_findings = self.analyze_file(path, content, graph, mean, std);
            findings.append(&mut file_findings);
        }

        // Sort by z-score (most unusual first)
        findings.sort_by(|a, b| {
            let za = a.threshold_metadata.get("z_score")
                .and_then(|s| s.parse::<f64>().ok()).unwrap_or(0.0);
            let zb = b.threshold_metadata.get("z_score")
                .and_then(|s| s.parse::<f64>().ok()).unwrap_or(0.0);
            zb.partial_cmp(&za).unwrap_or(std::cmp::Ordering::Equal)
        });

        findings.truncate(self.max_findings);

        info!("SurprisalDetector found {} unusual functions", findings.len());
        Ok(findings)
    }
}

/// Approximate CDF of the standard normal distribution
fn normal_cdf(z: f64) -> f64 {
    0.5 * (1.0 + erf(z / std::f64::consts::SQRT_2))
}

/// Approximation of the error function (Abramowitz & Stegun)
fn erf(x: f64) -> f64 {
    let a1 = 0.254829592;
    let a2 = -0.284496736;
    let a3 = 1.421413741;
    let a4 = -1.453152027;
    let a5 = 1.061405429;
    let coeff_p = 0.3275911;

    let sign = if x < 0.0 { -1.0 } else { 1.0 };
    let x = x.abs();
    let t = 1.0 / (1.0 + coeff_p * x);
    let y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * (-x * x).exp();

    sign * y
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::calibrate::NgramModel;
    use crate::graph::GraphStore;

    #[test]
    fn test_normal_cdf_known_values() {
        // CDF(0) should be 0.5 (symmetry of normal distribution)
        let cdf_zero = normal_cdf(0.0);
        assert!(
            (cdf_zero - 0.5).abs() < 1e-6,
            "normal_cdf(0) should be 0.5, got {}",
            cdf_zero
        );

        // CDF(large positive) should approach 1.0
        let cdf_large = normal_cdf(4.0);
        assert!(
            cdf_large > 0.99,
            "normal_cdf(4.0) should be > 0.99, got {}",
            cdf_large
        );

        // CDF(large negative) should approach 0.0
        let cdf_neg = normal_cdf(-4.0);
        assert!(
            cdf_neg < 0.01,
            "normal_cdf(-4.0) should be < 0.01, got {}",
            cdf_neg
        );
    }

    #[test]
    fn test_non_confident_model_returns_empty() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("module.py");
        std::fs::write(
            &file,
            r#"
def foo():
    return 42
"#,
        )
        .unwrap();

        let model = NgramModel::new(); // Empty model, not confident
        assert!(!model.is_confident());

        let store = GraphStore::in_memory();
        let detector = SurprisalDetector::new(dir.path(), model);
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&store, &empty_files).unwrap();
        assert!(
            findings.is_empty(),
            "Non-confident model should produce no findings, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
