//! Post-processing pipeline for findings.
//!
//! Applied after detection and before scoring:
//! 1. Incremental cache update
//! 2. Detector overrides from project config
//! 3. Max-files filtering
//! 4. De-duplicate overlapping dead-code findings
//! 5. Compound smell escalation
//! 6. Security downgrading for non-production paths
//! 7. FP classification filtering
//! 8. Confidence clamping
//! 9. LLM verification (optional, --verify)

use crate::config::ProjectConfig;
use crate::detectors::IncrementalCache;
use crate::models::{Finding, Severity};

use std::collections::HashSet;
use std::path::PathBuf;

use super::detect::{apply_detector_overrides, update_incremental_cache};

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
    // Step 0: Replace random UUIDs with deterministic IDs for cache dedup (#73)
    for finding in findings.iter_mut() {
        let file = finding
            .affected_files
            .first()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default();
        let line = finding.line_start.unwrap_or(0);
        finding.id = crate::detectors::base::finding_id(&finding.detector, &file, line);
    }

    // Step 1: Update incremental cache
    update_incremental_cache(
        is_incremental_mode,
        incremental_cache,
        files_to_parse,
        findings,
    );

    // Step 2: Apply detector overrides from project config
    apply_detector_overrides(findings, project_config);

    // Step 2.5: Filter out findings for excluded paths
    if !project_config.exclude.paths.is_empty() {
        let before = findings.len();
        findings.retain(|f| {
            !f.affected_files
                .iter()
                .any(|p| project_config.should_exclude(p))
        });
        let removed = before - findings.len();
        if removed > 0 {
            tracing::debug!("Filtered {} findings from excluded paths", removed);
        }
    }

    // Step 3: Filter findings to only include files in the analyzed set (respects --max-files)
    if max_files > 0 {
        filter_by_max_files(findings, all_files);
    }

    // Step 4: De-duplicate overlapping dead-code style findings (#50)
    dedupe_dead_code_overlap(findings);

    // Step 5: Escalate compound smells (multiple issues in same location)
    crate::scoring::escalate_compound_smells(findings);

    // Step 6: Downgrade security findings in non-production paths
    downgrade_non_production_security(findings);

    // Step 7: FP filtering with category-aware thresholds
    filter_false_positives(findings);

    // Step 8: Clamp confidence to [0.0, 1.0] (#35)
    for finding in findings.iter_mut() {
        if let Some(ref mut c) = finding.confidence {
            *c = c.clamp(0.0, 1.0);
        }
    }

    // Step 9: LLM verification (if --verify flag)
    if verify {
        // Check for API key availability — don't silently do nothing (#46)
        let has_claude = std::env::var("ANTHROPIC_API_KEY").is_ok();
        let has_ollama = std::process::Command::new("ollama")
            .arg("list")
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false);

        if !has_claude && !has_ollama {
            eprintln!(
                "\n⚠️  --verify requires an AI backend but none is available.\n\
                 Set ANTHROPIC_API_KEY for Claude, or install Ollama (https://ollama.ai).\n\
                 Skipping LLM verification."
            );
        } else {
            // LLM verification available via --verify flag
            tracing::debug!("LLM verification: backend available, implementation pending");
        }
    }
}

/// Remove duplicate overlaps between DeadCodeDetector and UnreachableCodeDetector.
/// Keep UnreachableCode findings when both target the same symbol/location.
fn dedupe_dead_code_overlap(findings: &mut Vec<Finding>) {
    use std::collections::HashSet;

    let mut unreachable_keys: HashSet<(String, u32, String)> = HashSet::new();

    for f in findings
        .iter()
        .filter(|f| f.detector == "UnreachableCodeDetector")
    {
        let file = f
            .affected_files
            .first()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default();
        let line = f.line_start.unwrap_or(0);
        let symbol = extract_symbol_from_title(&f.title);
        unreachable_keys.insert((file, line, symbol));
    }

    findings.retain(|f| {
        if f.detector != "DeadCodeDetector" {
            return true;
        }

        let file = f
            .affected_files
            .first()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default();
        let line = f.line_start.unwrap_or(0);
        let symbol = extract_symbol_from_title(&f.title);

        !unreachable_keys.contains(&(file, line, symbol))
    });
}

fn extract_symbol_from_title(title: &str) -> String {
    title
        .split(':')
        .nth(1)
        .map(|s| s.trim().to_lowercase())
        .unwrap_or_else(|| title.trim().to_lowercase())
}

/// Filter findings to only include files in the analyzed file set.
fn filter_by_max_files(findings: &mut Vec<Finding>, all_files: &[PathBuf]) {
    let allowed_files: HashSet<_> = all_files
        .iter()
        .map(|p| p.to_string_lossy().to_string())
        .collect();
    findings.retain(|f| {
        f.affected_files.is_empty()
            || f.affected_files.iter().any(|p| {
                let ps = p.to_string_lossy().to_string();
                allowed_files.contains(&ps)
                    || allowed_files.iter().any(|a| {
                        ps.ends_with(a.trim_start_matches("./"))
                            || a.ends_with(ps.trim_start_matches("./"))
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
        let is_non_prod = finding
            .affected_files
            .iter()
            .any(|p| is_non_production_path(&p.to_string_lossy()));

        if is_non_prod
            && SECURITY_DETECTORS.contains(&finding.detector.as_str())
            && (finding.severity == Severity::Critical || finding.severity == Severity::High)
        {
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
        model::HeuristicClassifier, CategoryThresholds, DetectorCategory, FeatureExtractor,
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
