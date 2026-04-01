//! Post-processing pipeline for findings.
//!
//! Applied after detection and before scoring:
//! 0.5. Assign default confidence by category (preserves detector-set values)
//! 0.6. Confidence enrichment with contextual signals (bundled, non-prod, multi-detector, test)
//! 0.65. Apply user FP/TP labels from feedback command
//! 0.7. Confidence threshold filter (--min-confidence, skipped with --show-all)
//! 1. Incremental cache update
//! 2. Detector overrides from project config
//!    2.5. Path exclusion filtering
//!    2.6. File-level suppression (`repotoire:ignore-file`)
//!    2.7. Auto-suppress detector test fixtures
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

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

// ── Functions moved from detect.rs ───────────────────────────────────────────

/// Apply detector config overrides from project config
pub(super) fn apply_detector_overrides(
    findings: &mut Vec<Finding>,
    project_config: &ProjectConfig,
) {
    if project_config.detectors.is_empty() {
        return;
    }

    let detector_configs = &project_config.detectors;

    // Filter out disabled detectors
    findings.retain(|f| {
        let detector_name = crate::config::normalize_detector_name(&f.detector);
        if let Some(config) = detector_configs.get(&detector_name) {
            if let Some(false) = config.enabled {
                return false;
            }
        }
        true
    });

    // Apply severity overrides
    for finding in findings.iter_mut() {
        let detector_name = crate::config::normalize_detector_name(&finding.detector);
        if let Some(config) = detector_configs.get(&detector_name) {
            if let Some(sev) = config.severity {
                finding.severity = sev;
            }
        }
    }
}

/// Update incremental cache with new findings
pub(super) fn update_incremental_cache(
    is_incremental_mode: bool,
    incremental_cache: &mut IncrementalCache,
    files: &[PathBuf],
    findings: &[Finding],
    repo_path: &Path,
) {
    if !is_incremental_mode {
        return;
    }

    for file_path in files {
        let rel_path = file_path.strip_prefix(repo_path).unwrap_or(file_path);
        let file_findings: Vec<_> = findings
            .iter()
            .filter(|f| {
                f.affected_files
                    .iter()
                    .any(|af| af == file_path || af == rel_path)
            })
            .cloned()
            .collect();
        incremental_cache.cache_findings(file_path, &file_findings);
    }

    if let Err(e) = incremental_cache.save_cache() {
        tracing::warn!("Failed to save incremental cache: {}", e);
    }
}

/// Core label-application logic. Separated from I/O for testability.
/// - FP-labeled findings are removed (or kept with low confidence if show_all).
/// - TP-labeled findings get confidence 0.95 + deterministic = true.
fn apply_labels_to_findings(
    findings: &mut Vec<Finding>,
    labels: &HashMap<String, bool>,
    show_all: bool,
) {
    if labels.is_empty() {
        return;
    }

    let mut fp_findings: Vec<Finding> = Vec::new();
    let mut applied = 0u32;

    findings.retain_mut(|f| {
        match labels.get(&f.id) {
            Some(false) => {
                // FP label: remove from findings
                applied += 1;
                if show_all {
                    f.confidence = Some(0.05);
                    f.threshold_metadata
                        .insert("user_label".to_string(), "false_positive".to_string());
                    fp_findings.push(f.clone());
                }
                false // remove from main vec
            }
            Some(true) => {
                // TP label: pin with high confidence
                applied += 1;
                f.confidence = Some(0.95);
                f.deterministic = true;
                f.threshold_metadata
                    .insert("user_label".to_string(), "true_positive".to_string());
                true // keep
            }
            None => true, // no label
        }
    });

    // Re-insert FP findings for --show-all visibility
    if show_all {
        findings.extend(fp_findings);
    }

    if applied > 0 {
        tracing::info!(
            "Applied {} user feedback labels ({} in training data)",
            applied,
            labels.len()
        );
    }
}

/// Load user FP/TP labels from training_data.jsonl and apply them.
/// Called after Step 0.6 (enrichment), before Step 0.7 (min-confidence filter).
fn apply_user_labels(findings: &mut Vec<Finding>, show_all: bool) {
    let labels = crate::classifier::FeedbackCollector::default().load_label_map();
    apply_labels_to_findings(findings, &labels, show_all);
}

/// Run the full post-processing pipeline on findings.
pub fn postprocess_findings(
    findings: &mut Vec<Finding>,
    project_config: &ProjectConfig,
    incremental_cache: &mut IncrementalCache,
    is_incremental_mode: bool,
    files_to_parse: &[PathBuf],
    all_files: &[PathBuf],
    max_files: usize,
    verify: bool,
    graph: &dyn crate::graph::GraphQuery,
    rank: bool,
    min_confidence: Option<f64>,
    show_all: bool,
    repo_path: &Path,
    bypass_set: &HashSet<String>,
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

    // Step 0.5: Assign default confidence to findings that don't have one.
    // Detectors may set confidence explicitly (e.g. voting engine); those are
    // left untouched.  For the rest, we assign a category-specific default so
    // every finding flowing into scoring and reporting has a confidence value.
    assign_default_confidence(findings);

    // Step 0.6: Enrich confidence with contextual signals.
    // Adjusts confidence based on path-based heuristics (bundled code, test
    // files, non-production paths) and multi-detector agreement.  Only fires
    // when signals match; unmatched findings are left untouched.
    crate::detectors::confidence_enrichment::enrich_all(findings);

    // Step 1: Update incremental cache — stores enriched findings BEFORE
    // any filtering. Labels and filters are applied fresh on every run.
    update_incremental_cache(
        is_incremental_mode,
        incremental_cache,
        files_to_parse,
        findings,
        repo_path,
    );

    // Touch last_used marker for stale cache pruning
    incremental_cache.touch_last_used();

    // Step 1.5: Apply user FP/TP labels from feedback command.
    // FP-labeled findings are removed; TP-labeled findings are pinned.
    // Runs after cache write so cache always has full pre-label findings.
    apply_user_labels(findings, show_all);

    // Step 1.6: Confidence threshold filter (--min-confidence).
    // Runs after labels so TP-pinned findings (confidence 0.95) survive.
    // Skipped when --show-all is set or no threshold is configured.
    filter_by_min_confidence(findings, min_confidence, show_all);

    // Step 2: Apply detector overrides from project config
    apply_detector_overrides(findings, project_config);

    // Step 2.5: Filter out findings for excluded paths (including built-in defaults)
    if !project_config.exclude.effective_patterns().is_empty() {
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

    // Step 2.6: File-level suppression — filter findings from files with repotoire:ignore-file
    filter_file_level_suppressed(findings);

    // Step 2.65: Inline suppression — filter findings where the affected line has
    // a repotoire:ignore comment. This catches suppressions for GraphWide detectors
    // (mutual-recursion, infinite-loop, etc.) that don't read file content themselves.
    filter_inline_suppressed(findings);

    // Step 2.7: Auto-suppress detector test fixtures (e.g. SQL injection detector's own test files)
    filter_detector_test_fixtures(findings);

    // Step 3: Filter findings to only include files in the analyzed set (respects --max-files)
    if max_files > 0 {
        filter_by_max_files(findings, all_files);
    }

    // Step 4: De-duplicate overlapping dead-code style findings (#50)
    dedupe_dead_code_overlap(findings);

    // Step 4.5: Deduplicate exact-match findings (same detector, title, file, line)
    deduplicate_findings(findings);

    // Step 5: Escalate compound smells (multiple issues in same location)
    crate::scoring::escalate_compound_smells(findings);

    // Step 6: Downgrade security findings in non-production paths
    downgrade_non_production_security(findings);

    // Step 7: FP filtering with category-aware thresholds
    filter_false_positives(findings, graph, bypass_set);

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

    // Step 10: Rank by actionability score (if --rank flag)
    if rank {
        rank_findings(findings, graph);
    }
}

/// Assign a category-based default confidence to every finding that lacks one.
///
/// Detectors that already set `confidence` (e.g. the voting engine) are left
/// untouched.  For the rest a default is chosen based on the finding's category:
///
/// | Category            | Default | Rationale                                  |
/// |---------------------|---------|--------------------------------------------|
/// | "architecture"      | 0.85    | Structural evidence is strong              |
/// | "security"          | 0.75    | Taint analysis is good but not perfect     |
/// | "design"            | 0.65    | Code smell detection has higher FP rate     |
/// | "dead-code"/"dead_code" | 0.70 | Graph-based but may miss dynamic dispatch |
/// | "ai_watchdog"       | 0.60    | Heuristic detection                        |
/// | Others / None       | 0.70    | Reasonable default                         |
fn assign_default_confidence(findings: &mut [Finding]) {
    let mut assigned = 0usize;
    for finding in findings.iter_mut() {
        if finding.confidence.is_none() {
            let default = Finding::default_confidence_for_category(finding.category.as_deref());
            finding.confidence = Some(default);
            assigned += 1;
        }
    }
    if assigned > 0 {
        tracing::debug!(
            "Assigned default confidence to {} findings without explicit confidence",
            assigned
        );
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

/// Rank findings by actionability score (0-100).
///
/// Uses the heuristic classifier to score findings. Sorted in descending order
/// so the most actionable findings appear first.
pub(crate) fn rank_findings(findings: &mut Vec<Finding>, _graph: &dyn crate::graph::GraphQuery) {
    let extractor = crate::classifier::FeatureExtractor::new();
    let classifier = crate::classifier::HeuristicClassifier;
    let mut scored: Vec<(f32, usize)> = findings
        .iter()
        .enumerate()
        .map(|(i, f)| {
            let features = extractor.extract(f);
            (classifier.score(&features), i)
        })
        .collect();
    scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    let reordered: Vec<Finding> = scored
        .into_iter()
        .map(|(_, i)| findings[i].clone())
        .collect();
    *findings = reordered;
}

/// FP filtering with category-aware thresholds.
///
/// Uses the heuristic classifier with per-category filter thresholds:
/// - Security: conservative (0.35) — don't miss real vulnerabilities
/// - Code Quality: aggressive (0.52) — filter noisy complexity warnings
/// - ML/AI: moderate (0.45) — domain-specific accuracy
fn filter_false_positives(
    findings: &mut Vec<Finding>,
    _graph: &dyn crate::graph::GraphQuery,
    bypass_set: &HashSet<String>,
) {
    use crate::classifier::{
        model::HeuristicClassifier, CategoryThresholds, DetectorCategory, FeatureExtractor,
    };

    let thresholds = CategoryThresholds::default();
    let extractor = FeatureExtractor::new();
    let classifier = HeuristicClassifier;

    let before_count = findings.len();
    let mut filtered_by_category: std::collections::HashMap<DetectorCategory, usize> =
        std::collections::HashMap::new();

    findings.retain(|f| {
        if f.deterministic || bypass_set.contains(&f.detector) {
            return true;
        }

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

/// Filter out findings from files that have `repotoire:ignore-file` in the first 10 lines.
///
/// Reads the first 10 lines of each unique affected file. Files that contain
/// the directive are fully suppressed. Uses a cache to avoid re-reading the
/// same file multiple times.
fn filter_file_level_suppressed(findings: &mut Vec<Finding>) {
    use std::collections::HashMap;

    // Build a cache of file path -> suppressed status
    let mut suppressed_files: HashMap<PathBuf, bool> = HashMap::new();

    let before = findings.len();
    findings.retain(|f| {
        for path in &f.affected_files {
            let is_suppressed = suppressed_files.entry(path.clone()).or_insert_with(|| {
                // Read first ~10 lines of the file
                match std::fs::read_to_string(path) {
                    Ok(content) => crate::detectors::is_file_suppressed(&content),
                    Err(_) => false, // Can't read file — don't suppress
                }
            });
            if *is_suppressed {
                return false;
            }
        }
        true
    });

    let removed = before - findings.len();
    if removed > 0 {
        tracing::debug!("File-level suppression filtered {} findings", removed);
    }
}

/// Filter findings where the affected line has an inline `repotoire:ignore` comment.
///
/// This is the postprocessor-level suppression check that catches comments for
/// GraphWide detectors (mutual-recursion, infinite-loop, etc.) which don't read
/// file content themselves. Checks both the finding's line and the previous line
/// for suppression comments, with optional targeted detector matching.
fn filter_inline_suppressed(findings: &mut Vec<Finding>) {
    use std::collections::HashMap;

    // Cache file contents to avoid re-reading
    let mut file_cache: HashMap<PathBuf, Vec<String>> = HashMap::new();

    let before = findings.len();
    findings.retain(|f| {
        let line_start = match f.line_start {
            Some(l) if l > 0 => l as usize,
            _ => return true, // No line info — keep
        };

        for path in &f.affected_files {
            let lines = file_cache.entry(path.clone()).or_insert_with(|| {
                std::fs::read_to_string(path)
                    .map(|c| c.lines().map(String::from).collect())
                    .unwrap_or_default()
            });

            if lines.is_empty() {
                continue;
            }

            // Scan a window around line_start: 3 lines before through 3 lines after.
            // This handles suppression comments placed before doc comments, on the
            // function signature, or between decorators/attributes and the definition.
            let line_idx = line_start.saturating_sub(1); // 0-indexed
            let scan_start = line_idx.saturating_sub(3);
            let scan_end = (line_idx + 3).min(lines.len().saturating_sub(1));

            for i in scan_start..=scan_end {
                let line = lines.get(i).map(|s| s.as_str()).unwrap_or("");
                let prev = if i > 0 {
                    lines.get(i - 1).map(|s| s.as_str())
                } else {
                    None
                };
                if crate::detectors::is_line_suppressed_for(line, prev, &f.detector) {
                    return false; // Suppressed
                }
            }
        }
        true
    });

    let removed = before - findings.len();
    if removed > 0 {
        tracing::debug!("Inline suppression filtered {} findings", removed);
    }
}

/// Deduplicate findings with identical detector, title, file, and line_start.
fn deduplicate_findings(findings: &mut Vec<Finding>) {
    use std::collections::HashSet;
    let before = findings.len();
    let mut seen = HashSet::new();
    findings.retain(|f| {
        let file = f
            .affected_files
            .first()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default();
        let key = (f.detector.clone(), f.title.clone(), file, f.line_start);
        seen.insert(key)
    });
    let removed = before - findings.len();
    if removed > 0 {
        tracing::debug!("Deduplicated {} findings", removed);
    }
}

/// Auto-suppress findings from detector test fixture files.
///
/// When a detector reports a finding in its OWN test infrastructure, the finding
/// is suppressed. This prevents false positives like the SQL injection detector
/// flagging patterns in `sql_injection/tests.rs` or `taint/mod.rs`.
///
/// Matching logic:
/// - The file path must contain `/detectors/` or `/tests/`
/// - The detector name (converted from PascalCase to snake_case components) must
///   appear as a path segment in the file path
///
/// For example, `SQLInjectionDetector` matches paths containing `sql_injection/`.
fn filter_detector_test_fixtures(findings: &mut Vec<Finding>) {
    let before = findings.len();
    findings.retain(|f| !is_detector_test_fixture(&f.detector, &f.affected_files));

    let removed = before - findings.len();
    if removed > 0 {
        tracing::debug!(
            "Auto-suppressed {} findings from detector test fixtures",
            removed
        );
    }
}

/// Check if a finding is from a detector's own test fixture.
///
/// Returns `true` if the affected file is part of the detector's own test
/// infrastructure, meaning the finding is a false positive from self-analysis.
fn is_detector_test_fixture(detector_name: &str, affected_files: &[PathBuf]) -> bool {
    // Convert PascalCase detector name to snake_case path segment.
    // E.g., "SQLInjectionDetector" -> "sql_injection"
    let slug = detector_name_to_path_slug(detector_name);

    for path in affected_files {
        let path_str = path.to_string_lossy();

        // Must be inside a detectors/ or tests/ directory
        let in_detector_dir =
            path_str.contains("/detectors/") || path_str.contains("\\detectors\\");
        let in_tests_dir = path_str.contains("/tests/") || path_str.contains("\\tests\\");

        if !in_detector_dir && !in_tests_dir {
            continue;
        }

        // Check if the detector's slug appears as a path component or file name
        // E.g., for slug "sql_injection", match paths like:
        //   src/detectors/sql_injection/tests.rs
        //   src/detectors/sql_injection/mod.rs
        //   src/detectors/sql_injection/patterns.rs
        if path_str.contains(&format!("/{}/", &slug))
            || path_str.contains(&format!("\\{}\\", &slug))
            || path_str.contains(&format!("/{}.rs", &slug))
            || path_str.contains(&format!("\\{}.rs", &slug))
        {
            return true;
        }

        // Also match the taint module for security detectors that use taint analysis
        // The taint module contains test fixtures for multiple security detectors
        if is_security_detector(detector_name)
            && (path_str.contains("/taint/")
                || path_str.contains("\\taint\\")
                || path_str.contains("/taint.rs")
                || path_str.contains("\\taint.rs"))
        {
            return true;
        }
    }

    false
}

/// Convert a PascalCase detector name to a snake_case path slug.
///
/// Examples:
/// - `"SQLInjectionDetector"` -> `"sql_injection"`
/// - `"XssDetector"` -> `"xss"`
/// - `"CommandInjectionDetector"` -> `"command_injection"`
/// - `"GodClassDetector"` -> `"god_class"`
fn detector_name_to_path_slug(name: &str) -> String {
    // Strip "Detector" suffix
    let name = name.strip_suffix("Detector").unwrap_or(name);

    let mut slug = String::with_capacity(name.len() + 4);
    let chars: Vec<char> = name.chars().collect();

    for (i, &ch) in chars.iter().enumerate() {
        if ch.is_uppercase() {
            // Insert underscore before uppercase if:
            // - Not the first character
            // - Previous char was lowercase (camelCase boundary)
            // - OR next char is lowercase and previous was uppercase (end of acronym like "SQL")
            if i > 0 {
                let prev_upper = chars[i - 1].is_uppercase();
                let next_lower = chars.get(i + 1).is_some_and(|c| c.is_lowercase());

                if !prev_upper || next_lower {
                    slug.push('_');
                }
            }
            slug.push(
                ch.to_lowercase()
                    .next()
                    .expect("to_lowercase always yields at least one char"),
            );
        } else {
            slug.push(ch);
        }
    }

    slug
}

/// Filter findings below a minimum confidence threshold.
///
/// If `show_all` is true, the filter is bypassed entirely.
/// If `min_confidence` is `None`, no filtering is applied.
/// The threshold is clamped to [0.0, 1.0].
pub(crate) fn filter_by_min_confidence(
    findings: &mut Vec<Finding>,
    min_confidence: Option<f64>,
    show_all: bool,
) {
    if show_all {
        return;
    }
    let Some(threshold) = min_confidence else {
        return;
    };
    let threshold = threshold.clamp(0.0, 1.0);
    let before = findings.len();
    findings.retain(|f| f.effective_confidence() >= threshold);
    let removed = before - findings.len();
    if removed > 0 {
        tracing::debug!(
            "Confidence filter (threshold={:.2}): removed {} findings below threshold",
            threshold,
            removed,
        );
    }
}

/// Check if a detector name is a security-related detector.
fn is_security_detector(name: &str) -> bool {
    const SECURITY_DETECTORS: &[&str] = &[
        "SQLInjectionDetector",
        "CommandInjectionDetector",
        "XssDetector",
        "SsrfDetector",
        "PathTraversalDetector",
        "LogInjectionDetector",
        "EvalDetector",
        "InsecureRandomDetector",
        "HardcodedCredentialsDetector",
        "CleartextCredentialsDetector",
        "NosqlInjectionDetector",
        "XxeDetector",
        "PrototypePollutionDetector",
        "InsecureCryptoDetector",
        "InsecureTlsDetector",
        "JwtWeakDetector",
        "CorsMisconfigDetector",
        "SecretDetector",
        "InsecureCookieDetector",
        "InsecureDeserializeDetector",
    ];
    SECURITY_DETECTORS.contains(&name)
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── detector_name_to_path_slug ───────────────────────────────────

    #[test]
    fn test_slug_sql_injection() {
        assert_eq!(
            detector_name_to_path_slug("SQLInjectionDetector"),
            "sql_injection"
        );
    }

    #[test]
    fn test_slug_xss() {
        assert_eq!(detector_name_to_path_slug("XssDetector"), "xss");
    }

    #[test]
    fn test_slug_command_injection() {
        assert_eq!(
            detector_name_to_path_slug("CommandInjectionDetector"),
            "command_injection"
        );
    }

    #[test]
    fn test_slug_god_class() {
        assert_eq!(detector_name_to_path_slug("GodClassDetector"), "god_class");
    }

    #[test]
    fn test_slug_ssrf() {
        assert_eq!(detector_name_to_path_slug("SsrfDetector"), "ssrf");
    }

    #[test]
    fn test_slug_n_plus_one() {
        assert_eq!(detector_name_to_path_slug("NPlusOneDetector"), "n_plus_one");
    }

    #[test]
    fn test_slug_ai_boilerplate() {
        assert_eq!(
            detector_name_to_path_slug("AIBoilerplateDetector"),
            "ai_boilerplate"
        );
    }

    // ── is_detector_test_fixture ─────────────────────────────────────

    #[test]
    fn test_fixture_match_sql_injection_tests() {
        let files = vec![PathBuf::from("src/detectors/sql_injection/tests.rs")];
        assert!(is_detector_test_fixture("SQLInjectionDetector", &files));
    }

    #[test]
    fn test_fixture_match_sql_injection_mod() {
        let files = vec![PathBuf::from("src/detectors/sql_injection/mod.rs")];
        assert!(is_detector_test_fixture("SQLInjectionDetector", &files));
    }

    #[test]
    fn test_fixture_match_taint_for_security() {
        let files = vec![PathBuf::from("src/detectors/taint/mod.rs")];
        assert!(is_detector_test_fixture("SQLInjectionDetector", &files));
    }

    #[test]
    fn test_fixture_no_match_different_detector() {
        // XSS detector should NOT match sql_injection directory
        let files = vec![PathBuf::from("src/detectors/sql_injection/tests.rs")];
        assert!(!is_detector_test_fixture("XssDetector", &files));
    }

    #[test]
    fn test_fixture_no_match_regular_source() {
        // Regular source file should NOT be suppressed
        let files = vec![PathBuf::from("src/main.rs")];
        assert!(!is_detector_test_fixture("SQLInjectionDetector", &files));
    }

    #[test]
    fn test_fixture_no_match_user_code() {
        // User code in a tests/ directory should NOT match unless detector name matches
        let files = vec![PathBuf::from("tests/integration_test.rs")];
        assert!(!is_detector_test_fixture("SQLInjectionDetector", &files));
    }

    #[test]
    fn test_fixture_match_god_class_file() {
        let files = vec![PathBuf::from("src/detectors/god_class.rs")];
        assert!(is_detector_test_fixture("GodClassDetector", &files));
    }

    #[test]
    fn test_fixture_taint_not_matched_for_non_security() {
        // Taint module should NOT be auto-suppressed for non-security detectors
        let files = vec![PathBuf::from("src/detectors/taint/mod.rs")];
        assert!(!is_detector_test_fixture("GodClassDetector", &files));
    }

    #[test]
    fn test_is_security_detector() {
        assert!(is_security_detector("SQLInjectionDetector"));
        assert!(is_security_detector("XssDetector"));
        assert!(is_security_detector("CommandInjectionDetector"));
        assert!(!is_security_detector("GodClassDetector"));
        assert!(!is_security_detector("DeadCodeDetector"));
    }

    // ── assign_default_confidence ──────────────────────────────────

    #[test]
    fn test_assign_default_confidence_sets_architecture() {
        let mut findings = vec![Finding {
            category: Some("architecture".into()),
            confidence: None,
            ..Default::default()
        }];
        assign_default_confidence(&mut findings);
        assert_eq!(findings[0].confidence, Some(0.85));
    }

    #[test]
    fn test_assign_default_confidence_sets_security() {
        let mut findings = vec![Finding {
            category: Some("security".into()),
            confidence: None,
            ..Default::default()
        }];
        assign_default_confidence(&mut findings);
        assert_eq!(findings[0].confidence, Some(0.75));
    }

    #[test]
    fn test_assign_default_confidence_sets_design() {
        let mut findings = vec![Finding {
            category: Some("design".into()),
            confidence: None,
            ..Default::default()
        }];
        assign_default_confidence(&mut findings);
        assert_eq!(findings[0].confidence, Some(0.65));
    }

    #[test]
    fn test_assign_default_confidence_sets_dead_code() {
        let mut findings = vec![
            Finding {
                category: Some("dead-code".into()),
                confidence: None,
                ..Default::default()
            },
            Finding {
                category: Some("dead_code".into()),
                confidence: None,
                ..Default::default()
            },
        ];
        assign_default_confidence(&mut findings);
        assert_eq!(findings[0].confidence, Some(0.70));
        assert_eq!(findings[1].confidence, Some(0.70));
    }

    #[test]
    fn test_assign_default_confidence_sets_ai_watchdog() {
        let mut findings = vec![Finding {
            category: Some("ai_watchdog".into()),
            confidence: None,
            ..Default::default()
        }];
        assign_default_confidence(&mut findings);
        assert_eq!(findings[0].confidence, Some(0.60));
    }

    #[test]
    fn test_assign_default_confidence_sets_unknown_category() {
        let mut findings = vec![Finding {
            category: Some("testing".into()),
            confidence: None,
            ..Default::default()
        }];
        assign_default_confidence(&mut findings);
        assert_eq!(findings[0].confidence, Some(0.70));
    }

    #[test]
    fn test_assign_default_confidence_sets_none_category() {
        let mut findings = vec![Finding {
            category: None,
            confidence: None,
            ..Default::default()
        }];
        assign_default_confidence(&mut findings);
        assert_eq!(findings[0].confidence, Some(0.70));
    }

    #[test]
    fn test_assign_default_confidence_does_not_overwrite_existing() {
        let mut findings = vec![Finding {
            category: Some("architecture".into()),
            confidence: Some(0.42),
            ..Default::default()
        }];
        assign_default_confidence(&mut findings);
        // Must preserve the detector-set confidence, NOT overwrite with 0.85
        assert_eq!(findings[0].confidence, Some(0.42));
    }

    #[test]
    fn test_assign_default_confidence_mixed_findings() {
        let mut findings = vec![
            Finding {
                category: Some("security".into()),
                confidence: Some(0.99),
                ..Default::default()
            },
            Finding {
                category: Some("architecture".into()),
                confidence: None,
                ..Default::default()
            },
            Finding {
                category: None,
                confidence: None,
                ..Default::default()
            },
        ];
        assign_default_confidence(&mut findings);
        assert_eq!(findings[0].confidence, Some(0.99)); // preserved
        assert_eq!(findings[1].confidence, Some(0.85)); // architecture default
        assert_eq!(findings[2].confidence, Some(0.70)); // fallback default
    }

    // ── filter_by_min_confidence ────────────────────────────────────

    #[test]
    fn test_min_confidence_filters_below_threshold() {
        let mut findings = vec![
            Finding {
                confidence: Some(0.9),
                ..Default::default()
            },
            Finding {
                confidence: Some(0.5),
                ..Default::default()
            },
            Finding {
                confidence: Some(0.7),
                ..Default::default()
            },
        ];
        filter_by_min_confidence(&mut findings, Some(0.6), false);
        assert_eq!(findings.len(), 2);
        assert_eq!(findings[0].confidence, Some(0.9));
        assert_eq!(findings[1].confidence, Some(0.7));
    }

    #[test]
    fn test_min_confidence_none_does_not_filter() {
        let mut findings = vec![
            Finding {
                confidence: Some(0.1),
                ..Default::default()
            },
            Finding {
                confidence: Some(0.9),
                ..Default::default()
            },
        ];
        filter_by_min_confidence(&mut findings, None, false);
        assert_eq!(findings.len(), 2);
    }

    #[test]
    fn test_min_confidence_show_all_bypasses_filter() {
        let mut findings = vec![
            Finding {
                confidence: Some(0.1),
                ..Default::default()
            },
            Finding {
                confidence: Some(0.2),
                ..Default::default()
            },
        ];
        filter_by_min_confidence(&mut findings, Some(0.99), true);
        assert_eq!(findings.len(), 2); // nothing removed
    }

    #[test]
    fn test_min_confidence_exact_threshold_kept() {
        let mut findings = vec![Finding {
            confidence: Some(0.7),
            ..Default::default()
        }];
        filter_by_min_confidence(&mut findings, Some(0.7), false);
        assert_eq!(findings.len(), 1); // exactly at threshold is kept
    }

    #[test]
    fn test_min_confidence_clamps_above_one() {
        let mut findings = vec![Finding {
            confidence: Some(0.99),
            ..Default::default()
        }];
        // Threshold > 1.0 should be clamped to 1.0, filtering everything below 1.0
        filter_by_min_confidence(&mut findings, Some(1.5), false);
        assert_eq!(findings.len(), 0);
    }

    #[test]
    fn test_min_confidence_clamps_below_zero() {
        let mut findings = vec![Finding {
            confidence: Some(0.01),
            ..Default::default()
        }];
        // Threshold < 0.0 should be clamped to 0.0, keeping everything
        filter_by_min_confidence(&mut findings, Some(-0.5), false);
        assert_eq!(findings.len(), 1);
    }

    #[test]
    fn test_min_confidence_uses_effective_confidence_for_none() {
        // Finding with confidence=None should use effective_confidence() which is 0.70
        let mut findings = vec![Finding {
            confidence: None,
            ..Default::default()
        }];
        // 0.70 (effective default) >= 0.5 threshold => kept
        filter_by_min_confidence(&mut findings, Some(0.5), false);
        assert_eq!(findings.len(), 1);

        // 0.70 (effective default) < 0.8 threshold => removed
        filter_by_min_confidence(&mut findings, Some(0.8), false);
        assert_eq!(findings.len(), 0);
    }
}

#[cfg(test)]
mod label_tests {
    use super::*;
    use crate::models::{Finding, Severity};
    use std::collections::HashMap;

    fn make_finding(id: &str, detector: &str) -> Finding {
        Finding {
            id: id.into(),
            detector: detector.into(),
            severity: Severity::Medium,
            title: format!("Finding {}", id),
            ..Default::default()
        }
    }

    #[test]
    fn test_fp_label_removes_finding() {
        let mut findings = vec![make_finding("aaa", "Det1"), make_finding("bbb", "Det2")];
        let labels = HashMap::from([("aaa".to_string(), false)]);

        apply_labels_to_findings(&mut findings, &labels, false);

        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].id, "bbb");
    }

    #[test]
    fn test_fp_label_show_all_reinserts_with_low_confidence() {
        let mut findings = vec![make_finding("aaa", "Det1"), make_finding("bbb", "Det2")];
        let labels = HashMap::from([("aaa".to_string(), false)]);

        apply_labels_to_findings(&mut findings, &labels, true);

        assert_eq!(findings.len(), 2, "show_all should keep FP finding");
        let fp = findings
            .iter()
            .find(|f| f.id == "aaa")
            .expect("FP finding should exist");
        assert_eq!(fp.confidence, Some(0.05));
        assert_eq!(
            fp.threshold_metadata.get("user_label").map(|s| s.as_str()),
            Some("false_positive")
        );
    }

    #[test]
    fn test_tp_label_pins_finding() {
        let mut findings = vec![make_finding("aaa", "Det1")];
        let labels = HashMap::from([("aaa".to_string(), true)]);

        apply_labels_to_findings(&mut findings, &labels, false);

        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].confidence, Some(0.95));
        assert!(findings[0].deterministic);
        assert_eq!(
            findings[0]
                .threshold_metadata
                .get("user_label")
                .map(|s| s.as_str()),
            Some("true_positive")
        );
    }

    #[test]
    fn test_unlabeled_findings_unchanged() {
        let mut findings = vec![make_finding("aaa", "Det1")];
        let labels = HashMap::from([("zzz".to_string(), false)]); // no match

        apply_labels_to_findings(&mut findings, &labels, false);

        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].id, "aaa");
        assert!(findings[0].confidence.is_none()); // unchanged
    }

    #[test]
    fn test_empty_labels_is_noop() {
        let mut findings = vec![make_finding("aaa", "Det1")];
        let labels = HashMap::new();

        apply_labels_to_findings(&mut findings, &labels, false);

        assert_eq!(findings.len(), 1);
    }
}
