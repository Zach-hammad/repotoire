//! Post-processing pipeline for findings.
//!
//! Applied after detection and before scoring:
//! 1. Incremental cache update
//! 2. Detector overrides from project config
//! 3. Max-files filtering
//! 4. Compound smell escalation
//! 5. Security downgrading for non-production paths
//! 6. FP classification filtering
//! 7. LLM verification (optional, --verify)

use crate::config::ProjectConfig;
use crate::detectors::IncrementalCache;
use crate::models::{Finding, Severity};

use std::collections::HashSet;
use std::path::PathBuf;

use super::detect::{update_incremental_cache, apply_detector_overrides};

/// Run the full post-processing pipeline on findings.
pub(super) fn postprocess_findings(
    findings: &mut Vec<Finding>,
    project_config: &ProjectConfig,
    incremental_cache: &mut IncrementalCache,
    is_incremental_mode: bool,
    files_to_parse: &[PathBuf],
    all_files: &[PathBuf],
    max_files: usize,
    verify: bool,
) {
    // Step 1: Update incremental cache
    update_incremental_cache(is_incremental_mode, incremental_cache, files_to_parse, findings);

    // Step 2: Apply detector overrides from project config
    apply_detector_overrides(findings, project_config);

    // Step 3: Filter findings to only include files in the analyzed set (respects --max-files)
    if max_files > 0 {
        filter_by_max_files(findings, all_files);
    }

    // Step 4: Escalate compound smells (multiple issues in same location)
    crate::scoring::escalate_compound_smells(findings);

    // Step 5: Downgrade security findings in non-production paths
    downgrade_non_production_security(findings);

    // Step 6: FP filtering with category-aware thresholds
    filter_false_positives(findings);

    // Step 7: LLM verification (if --verify flag)
    if verify {
        // TODO: Wire up LLM verification for remaining HIGH+ findings
        // This uses Ollama/Claude to double-check ambiguous cases
        tracing::debug!("LLM verification requested but not yet wired up");
    }
}

/// Filter findings to only include files in the analyzed file set.
fn filter_by_max_files(findings: &mut Vec<Finding>, all_files: &[PathBuf]) {
    let allowed_files: HashSet<_> = all_files.iter()
        .map(|p| p.to_string_lossy().to_string())
        .collect();
    findings.retain(|f| {
        f.affected_files.is_empty() || f.affected_files.iter().any(|p| {
            let ps = p.to_string_lossy().to_string();
            allowed_files.contains(&ps) || allowed_files.iter().any(|a| {
                ps.ends_with(a.trim_start_matches("./")) || a.ends_with(ps.trim_start_matches("./"))
            })
        })
    });
}

/// Downgrade security findings in non-production paths (scripts, tests, fixtures).
fn downgrade_non_production_security(findings: &mut [Finding]) {
    use crate::detectors::content_classifier::is_non_production_path;

    const SECURITY_DETECTORS: &[&str] = &[
        "CommandInjectionDetector",
        "SQLInjectionDetector",
        "XssDetector",
        "SsrfDetector",
        "PathTraversalDetector",
        "LogInjectionDetector",
        "EvalDetector",
        "InsecureRandomDetector",
        "HardcodedCredentialsDetector",
        "CleartextCredentialsDetector",
    ];

    for finding in findings.iter_mut() {
        let is_non_prod = finding.affected_files.iter().any(|p| {
            is_non_production_path(&p.to_string_lossy())
        });

        if is_non_prod && SECURITY_DETECTORS.contains(&finding.detector.as_str())
            && (finding.severity == Severity::Critical || finding.severity == Severity::High) {
                finding.severity = Severity::Medium;
                finding.description = format!("[Non-production path] {}", finding.description);
            }
    }
}

/// FP filtering with category-aware thresholds.
///
/// Uses different thresholds for different detector types:
/// - Security: conservative (0.35) — don't miss real vulnerabilities
/// - Code Quality: aggressive (0.55) — filter noisy complexity warnings
/// - ML/AI: moderate (0.45) — domain-specific accuracy
fn filter_false_positives(findings: &mut Vec<Finding>) {
    use crate::classifier::{
        FeatureExtractor,
        model::HeuristicClassifier,
        CategoryThresholds,
        DetectorCategory,
    };

    let extractor = FeatureExtractor::new();
    let classifier = HeuristicClassifier;
    let thresholds = CategoryThresholds::default();

    let before_count = findings.len();
    let mut filtered_by_category: std::collections::HashMap<DetectorCategory, usize> =
        std::collections::HashMap::new();

    findings.retain(|f| {
        let features = extractor.extract(f);
        let tp_prob = classifier.score(&features);
        let category = DetectorCategory::from_detector(&f.detector);
        let config = thresholds.get_category(category);

        if tp_prob >= config.filter_threshold {
            true
        } else {
            *filtered_by_category.entry(category).or_insert(0) += 1;
            false
        }
    });

    let total_filtered = before_count - findings.len();
    if total_filtered > 0 {
        tracing::info!(
            "FP classifier filtered {} findings (Security: {}, Quality: {}, ML: {}, Perf: {}, Other: {})",
            total_filtered,
            filtered_by_category.get(&DetectorCategory::Security).unwrap_or(&0),
            filtered_by_category.get(&DetectorCategory::CodeQuality).unwrap_or(&0),
            filtered_by_category.get(&DetectorCategory::MachineLearning).unwrap_or(&0),
            filtered_by_category.get(&DetectorCategory::Performance).unwrap_or(&0),
            filtered_by_category.get(&DetectorCategory::Other).unwrap_or(&0),
        );
    }
}
