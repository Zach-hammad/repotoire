//! Diff command — compare findings between two analysis states
//!
//! Shows new findings, fixed findings, and score delta.

use anyhow::{Context, Result};
use std::path::Path;
use std::time::Instant;

use crate::models::{Finding, FindingsSummary, Grade, HealthReport, Severity};
use console::style;
use serde_json::json;

use super::diff_hunks::{Attribution, DiffHunks};

/// Check if two findings refer to the same logical issue.
///
/// Uses fuzzy matching: same detector, same file, line within ±3.
/// File-level findings (no line) match if detector and file match.
fn findings_match(a: &Finding, b: &Finding) -> bool {
    a.detector == b.detector
        && a.affected_files.first() == b.affected_files.first()
        && match (a.line_start, b.line_start) {
            (Some(la), Some(lb)) => la.abs_diff(lb) <= 3,
            (None, None) => true,
            _ => false,
        }
}

/// Result of diffing two sets of findings (raw, before attribution).
#[derive(Debug)]
struct DiffResult {
    base_ref: String,
    head_ref: String,
    new_findings: Vec<Finding>,
    fixed_findings: Vec<Finding>,
    score_before: Option<f64>,
    score_after: Option<f64>,
}

/// A finding with its attribution (how it relates to the diff).
#[derive(Debug, Clone)]
pub struct AttributedFinding {
    pub finding: Finding,
    pub attribution: Attribution,
}

/// Result of a smart diff with attribution.
#[derive(Debug)]
pub struct SmartDiffResult {
    pub base_ref: String,
    pub head_ref: String,
    pub files_changed: usize,
    pub new_findings: Vec<AttributedFinding>,
    pub all_new_count: usize,
    pub fixed_findings: Vec<Finding>,
    pub score_before: Option<f64>,
    pub score_after: Option<f64>,
}

impl SmartDiffResult {
    pub fn score_delta(&self) -> Option<f64> {
        match (self.score_before, self.score_after) {
            (Some(before), Some(after)) => Some(after - before),
            _ => None,
        }
    }

    /// Extract just the Finding structs (for APIs that need &[Finding]).
    pub fn findings_only(&self) -> Vec<Finding> {
        self.new_findings
            .iter()
            .map(|af| af.finding.clone())
            .collect()
    }

    /// Extract hunk-level findings only.
    pub fn hunk_findings(&self) -> Vec<Finding> {
        self.new_findings
            .iter()
            .filter(|af| af.attribution == Attribution::InChangedHunk)
            .map(|af| af.finding.clone())
            .collect()
    }
}

/// Compute the diff between baseline and head findings.
fn diff_findings(
    baseline: &[Finding],
    head: &[Finding],
    base_ref: &str,
    head_ref: &str,
    score_before: Option<f64>,
    score_after: Option<f64>,
) -> DiffResult {
    let new_findings: Vec<Finding> = head
        .iter()
        .filter(|h| !baseline.iter().any(|b| findings_match(b, h)))
        .cloned()
        .collect();

    let fixed_findings: Vec<Finding> = baseline
        .iter()
        .filter(|b| !head.iter().any(|h| findings_match(b, h)))
        .cloned()
        .collect();

    DiffResult {
        base_ref: base_ref.to_string(),
        head_ref: head_ref.to_string(),
        new_findings,
        fixed_findings,
        score_before,
        score_after,
    }
}

// ---------------------------------------------------------------------------
// Output formatters
// ---------------------------------------------------------------------------

fn severity_icon(severity: &Severity) -> &'static str {
    match severity {
        Severity::Critical => "[C]",
        Severity::High => "[H]",
        Severity::Medium => "[M]",
        Severity::Low => "[L]",
        Severity::Info => "[I]",
    }
}

/// Format a single finding line for text output.
fn format_finding_line(out: &mut String, finding: &Finding, _no_emoji: bool) {
    let file = finding
        .affected_files
        .first()
        .map(|p| p.display().to_string())
        .unwrap_or_default();
    let line = finding
        .line_start
        .map(|l| format!(":{l}"))
        .unwrap_or_default();
    out.push_str(&format!(
        "  {} {:<40} {}{}\n",
        severity_icon(&finding.severity),
        &finding.title.chars().take(40).collect::<String>(),
        file,
        line
    ));
}

/// Render the diff result as colored terminal text.
///
/// When `no_emoji` is true, plain ASCII markers are used instead of emoji.
pub fn format_text(result: &SmartDiffResult, no_emoji: bool) -> String {
    let mut out = String::new();

    // Header
    out.push_str(&format!(
        "Repotoire Diff: {}..{} ({} files changed)\n\n",
        result.base_ref, result.head_ref, result.files_changed,
    ));

    // Group new findings by attribution
    let hunk_findings: Vec<_> = result
        .new_findings
        .iter()
        .filter(|af| af.attribution == Attribution::InChangedHunk)
        .collect();
    let file_findings: Vec<_> = result
        .new_findings
        .iter()
        .filter(|af| af.attribution == Attribution::InChangedFile)
        .collect();
    let unrelated_findings: Vec<_> = result
        .new_findings
        .iter()
        .filter(|af| af.attribution == Attribution::InUnchangedFile)
        .collect();

    if result.new_findings.is_empty() && result.all_new_count > 0 {
        // Hunk-only mode filtered everything, but findings exist elsewhere
        let check = if no_emoji { "[ok]" } else { "\u{2705}" };
        out.push_str(&format!(
            "  {} {}\n",
            check,
            style("No new findings in your changes").green()
        ));
        let info = if no_emoji { "i" } else { "\u{2139}\u{fe0f}" };
        out.push_str(&format!(
            "  {} {} finding{} in other files (use --all to see)\n\n",
            style(info).dim(),
            result.all_new_count,
            if result.all_new_count == 1 { "" } else { "s" }
        ));
    } else if result.new_findings.is_empty() {
        let check = if no_emoji { "[ok]" } else { "\u{2705}" };
        out.push_str(&format!(
            "  {} {}\n\n",
            check,
            style("No new findings").green()
        ));
    } else {
        // YOUR CHANGES section
        if !hunk_findings.is_empty() {
            out.push_str(&format!(
                "{}\n",
                style(format!(
                    "YOUR CHANGES ({} finding{})",
                    hunk_findings.len(),
                    if hunk_findings.len() == 1 { "" } else { "s" }
                ))
                .bold()
            ));
            for af in &hunk_findings {
                format_finding_line(&mut out, &af.finding, no_emoji);
            }
            out.push('\n');
        }

        // PRE-EXISTING section (only shown with --changed or --all)
        if !file_findings.is_empty() {
            out.push_str(&format!(
                "{}\n",
                style(format!(
                    "PRE-EXISTING ({} in changed files)",
                    file_findings.len()
                ))
                .dim()
            ));
            for af in &file_findings {
                format_finding_line(&mut out, &af.finding, no_emoji);
            }
            out.push('\n');
        }

        // UNRELATED section (only shown with --all)
        if !unrelated_findings.is_empty() {
            out.push_str(&format!(
                "{}\n",
                style(format!(
                    "UNRELATED ({} in unchanged files)",
                    unrelated_findings.len()
                ))
                .dim()
            ));
            for af in &unrelated_findings {
                format_finding_line(&mut out, &af.finding, no_emoji);
            }
            out.push('\n');
        }
    }

    // --- Score delta ---
    if let (Some(before), Some(after)) = (result.score_before, result.score_after) {
        let delta = after - before;
        let delta_str = if delta >= 0.0 {
            style(format!("+{:.1}", delta)).green().to_string()
        } else {
            style(format!("{:.1}", delta)).red().to_string()
        };
        out.push_str(&format!(
            "Score: {:.1} \u{2192} {:.1} ({})\n",
            before, after, delta_str,
        ));
    }

    // Fixed findings
    if !result.fixed_findings.is_empty() {
        let prefix = if no_emoji { "" } else { "\u{2728} " };
        out.push_str(&format!(
            "{}{} finding{} fixed\n",
            prefix,
            result.fixed_findings.len(),
            if result.fixed_findings.len() == 1 {
                ""
            } else {
                "s"
            }
        ));
    }

    out
}

/// Render the diff result as pretty-printed JSON.
pub fn format_json(result: &SmartDiffResult) -> String {
    let all_findings = result.findings_only();
    let new_summary = FindingsSummary::from_findings(&all_findings);
    let fixed_summary = FindingsSummary::from_findings(&result.fixed_findings);

    let score_delta = result.score_delta();

    let new_findings_json: Vec<serde_json::Value> = result
        .new_findings
        .iter()
        .map(|af| {
            json!({
                "detector": af.finding.detector,
                "severity": af.finding.severity.to_string(),
                "title": af.finding.title,
                "description": af.finding.description,
                "file": af.finding.affected_files.first().map(|p| p.display().to_string()).unwrap_or_default(),
                "line": af.finding.line_start,
                "attribution": match af.attribution {
                    Attribution::InChangedHunk => "in_changed_hunk",
                    Attribution::InChangedFile => "in_changed_file",
                    Attribution::InUnchangedFile => "in_unchanged_file",
                },
            })
        })
        .collect();

    let fixed_findings_json: Vec<serde_json::Value> = result
        .fixed_findings
        .iter()
        .map(|f| {
            json!({
                "detector": f.detector,
                "severity": f.severity.to_string(),
                "title": f.title,
                "file": f.affected_files.first().map(|p| p.display().to_string()).unwrap_or_default(),
                "line": f.line_start,
            })
        })
        .collect();

    let output = json!({
        "base_ref": result.base_ref,
        "head_ref": result.head_ref,
        "files_changed": result.files_changed,
        "total_new_findings": result.all_new_count,
        "new_findings": new_findings_json,
        "fixed_findings": fixed_findings_json,
        "score_before": result.score_before,
        "score_after": result.score_after,
        "score_delta": score_delta,
        "summary": {
            "new": {
                "critical": new_summary.critical,
                "high": new_summary.high,
                "medium": new_summary.medium,
                "low": new_summary.low,
            },
            "fixed": {
                "critical": fixed_summary.critical,
                "high": fixed_summary.high,
                "medium": fixed_summary.medium,
                "low": fixed_summary.low,
            },
        },
    });

    serde_json::to_string_pretty(&output).expect("JSON serialization should not fail")
}

/// Render the diff result as SARIF 2.1.0 (only hunk-level findings).
///
/// Builds a temporary `HealthReport` containing only the hunk findings,
/// then delegates to the existing SARIF reporter.
pub fn format_sarif(result: &SmartDiffResult) -> anyhow::Result<String> {
    let hunk_findings = result.hunk_findings();
    let report = HealthReport {
        overall_score: result.score_after.unwrap_or(0.0),
        grade: Grade::default(),
        structure_score: 0.0,
        quality_score: 0.0,
        architecture_score: None,
        findings_summary: FindingsSummary::from_findings(&hunk_findings),
        findings: hunk_findings,
        total_files: 0,
        total_functions: 0,
        total_classes: 0,
        total_loc: 0,
    };

    crate::reporters::report_with_format(&report, crate::reporters::OutputFormat::Sarif)
}

/// Load baseline and head findings from cache, returning them along with scores.
fn load_baseline_and_head(
    repotoire_dir: &Path,
    _repo_path: &Path,
    _base_ref: Option<&str>,
) -> Result<(Vec<Finding>, Vec<Finding>, Option<f64>, Option<f64>)> {
    // Load baseline findings from snapshot (saved by a previous `analyze`)
    let baseline_path = repotoire_dir.join("baseline_findings.json");
    let baseline = if baseline_path.exists() {
        super::analyze::output::load_cached_findings_from(&baseline_path)
    } else {
        // Fall back to last_findings.json if no snapshot exists yet
        super::analyze::output::load_cached_findings(repotoire_dir)
    }
    .context(
        "No baseline found. Run 'repotoire analyze' to establish a baseline, then run it again after making changes.",
    )?;

    let score_before = load_score_from(&if baseline_path.exists() {
        repotoire_dir.join("baseline_health.json")
    } else {
        repotoire_dir.join("last_health.json")
    });

    // Load current findings from cache (from the most recent `analyze` run)
    let head = super::analyze::output::load_cached_findings(repotoire_dir)
        .context("No current analysis found. Run 'repotoire analyze' to generate findings.")?;
    let score_after = load_cached_score(repotoire_dir);

    Ok((baseline, head, score_before, score_after))
}

/// Send telemetry event for the diff run.
fn send_diff_telemetry(
    telemetry: &crate::telemetry::Telemetry,
    repo_path: &Path,
    result: &SmartDiffResult,
) {
    if let crate::telemetry::Telemetry::Active(ref state) = *telemetry {
        if let Some(distinct_id) = &state.distinct_id {
            let repo_id = crate::telemetry::config::compute_repo_id(repo_path);
            let event = crate::telemetry::events::DiffRun {
                repo_id,
                score_before: result.score_before.unwrap_or(0.0),
                score_after: result.score_after.unwrap_or(0.0),
                score_delta: result.score_delta().unwrap_or(0.0),
                findings_added: result.all_new_count as u64,
                findings_removed: result.fixed_findings.len() as u64,
                version: env!("CARGO_PKG_VERSION").to_string(),
                ..Default::default()
            };
            let props = serde_json::to_value(&event).unwrap_or_default();
            crate::telemetry::posthog::capture_queued("diff_run", distinct_id, props);
        }
    }
}

/// Format the diff result and write it to the output destination.
fn emit_output(
    result: &SmartDiffResult,
    format: crate::reporters::OutputFormat,
    no_emoji: bool,
    output: Option<&Path>,
    start: Instant,
) -> Result<()> {
    use crate::reporters::OutputFormat;

    let output_str = match format {
        OutputFormat::Json => format_json(result),
        OutputFormat::Sarif => format_sarif(result)?,
        _ => format_text(result, no_emoji),
    };

    if let Some(out_path) = output {
        std::fs::write(out_path, &output_str)?;
        eprintln!("Report written to: {}", out_path.display());
    } else {
        println!("{}", output_str);
    }

    // Summary (text mode only)
    if !matches!(format, OutputFormat::Json | OutputFormat::Sarif) {
        let elapsed = start.elapsed();
        let prefix = if no_emoji { "" } else { "✨ " };
        eprintln!(
            "{}Diff complete in {:.2}s ({} new, {} fixed)",
            prefix,
            elapsed.as_secs_f64(),
            result.new_findings.len(),
            result.fixed_findings.len()
        );
    }

    Ok(())
}

/// Check whether new findings in changed hunks exceed the fail-on severity threshold.
fn check_fail_threshold(fail_on: Option<Severity>, result: &SmartDiffResult) -> Result<()> {
    if let Some(threshold) = fail_on {
        let hunk_findings = result.hunk_findings();
        let new_summary = FindingsSummary::from_findings(&hunk_findings);
        let should_fail = match threshold {
            Severity::Critical => new_summary.critical > 0,
            Severity::High => new_summary.critical > 0 || new_summary.high > 0,
            Severity::Medium => {
                new_summary.critical > 0 || new_summary.high > 0 || new_summary.medium > 0
            }
            Severity::Low | Severity::Info => {
                new_summary.critical > 0
                    || new_summary.high > 0
                    || new_summary.medium > 0
                    || new_summary.low > 0
            }
        };
        if should_fail {
            anyhow::bail!(
                "Failing due to --fail-on={}: {} new finding(s) in changed hunks",
                threshold,
                hunk_findings.len()
            );
        }
    }
    Ok(())
}

/// Run the diff command.
pub fn run(
    repo_path: &Path,
    base_ref: Option<String>,
    format: crate::reporters::OutputFormat,
    fail_on: Option<crate::models::Severity>,
    no_emoji: bool,
    output: Option<&Path>,
    all: bool,
    changed: bool,
    telemetry: &crate::telemetry::Telemetry,
) -> Result<()> {
    let start = Instant::now();
    let repo_path = repo_path
        .canonicalize()
        .context("Cannot resolve repository path")?;

    // Verify git repo
    if !repo_path.join(".git").exists() {
        anyhow::bail!(
            "diff requires a git repository (no .git found in {})",
            repo_path.display()
        );
    }

    let repotoire_dir =
        crate::cache::ensure_cache_dir(&repo_path).context("Failed to create cache directory")?;

    // 1-3. Load baseline, head, and scores
    let (baseline, head, score_before, score_after) =
        load_baseline_and_head(&repotoire_dir, &repo_path, base_ref.as_deref())?;

    // 4. Compute raw diff (existing function, unchanged)
    let base_label = base_ref.as_deref().unwrap_or("cached");
    let raw_diff = diff_findings(
        &baseline,
        &head,
        base_label,
        "HEAD",
        score_before,
        score_after,
    );

    // 5. Parse git diff hunks for attribution
    let effective_base = base_ref.as_deref().unwrap_or("HEAD~1");
    let hunks = DiffHunks::from_git_diff(&repo_path, effective_base).unwrap_or_else(|e| {
        tracing::debug!("git diff -U0 failed: {e}, attributing all as InUnchangedFile");
        DiffHunks::parse_diff("") // empty hunks = all findings unattributed
    });

    // 6. Attribute each new finding
    let all_attributed: Vec<AttributedFinding> = raw_diff
        .new_findings
        .into_iter()
        .map(|f| {
            let attr = f
                .affected_files
                .first()
                .map(|path| hunks.attribute(path, f.line_start))
                .unwrap_or(Attribution::InUnchangedFile);
            AttributedFinding {
                finding: f,
                attribution: attr,
            }
        })
        .collect();

    let all_new_count = all_attributed.len();

    // Filter based on flags
    let filtered: Vec<AttributedFinding> = if all {
        all_attributed
    } else if changed {
        all_attributed
            .into_iter()
            .filter(|af| af.attribution != Attribution::InUnchangedFile)
            .collect()
    } else {
        all_attributed
            .into_iter()
            .filter(|af| af.attribution == Attribution::InChangedHunk)
            .collect()
    };

    let result = SmartDiffResult {
        base_ref: raw_diff.base_ref,
        head_ref: raw_diff.head_ref,
        files_changed: hunks.changed_file_count(),
        new_findings: filtered,
        all_new_count,
        fixed_findings: raw_diff.fixed_findings,
        score_before: raw_diff.score_before,
        score_after: raw_diff.score_after,
    };

    // 7. Send telemetry
    send_diff_telemetry(telemetry, &repo_path, &result);

    // 8. Format and output
    emit_output(&result, format, no_emoji, output, start)?;

    // 9. Fail-on threshold (uses hunk findings only)
    check_fail_threshold(fail_on, &result)?;

    Ok(())
}

/// Load cached health score from last_health.json.
fn load_cached_score(repotoire_dir: &Path) -> Option<f64> {
    load_score_from(&repotoire_dir.join("last_health.json"))
}

/// Load health score from a specific JSON file.
fn load_score_from(path: &Path) -> Option<f64> {
    let data = std::fs::read_to_string(path).ok()?;
    let json_val: serde_json::Value = serde_json::from_str(&data).ok()?;
    json_val.get("health_score").and_then(|v| v.as_f64())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::Severity;
    use std::path::PathBuf;

    fn make_finding(detector: &str, file: &str, line: Option<u32>) -> Finding {
        Finding {
            detector: detector.to_string(),
            affected_files: vec![PathBuf::from(file)],
            line_start: line,
            severity: Severity::Medium,
            title: "test".to_string(),
            ..Default::default()
        }
    }

    #[test]
    fn test_exact_match() {
        let a = make_finding("dead_code", "src/foo.rs", Some(10));
        let b = make_finding("dead_code", "src/foo.rs", Some(10));
        assert!(findings_match(&a, &b));
    }

    #[test]
    fn test_fuzzy_line_match_within_tolerance() {
        let a = make_finding("dead_code", "src/foo.rs", Some(10));
        let b = make_finding("dead_code", "src/foo.rs", Some(13)); // +3
        assert!(findings_match(&a, &b));

        let c = make_finding("dead_code", "src/foo.rs", Some(7)); // -3
        assert!(findings_match(&a, &c));
    }

    #[test]
    fn test_fuzzy_line_beyond_tolerance() {
        let a = make_finding("dead_code", "src/foo.rs", Some(10));
        let b = make_finding("dead_code", "src/foo.rs", Some(14)); // +4
        assert!(!findings_match(&a, &b));
    }

    #[test]
    fn test_different_detector_no_match() {
        let a = make_finding("dead_code", "src/foo.rs", Some(10));
        let b = make_finding("magic_number", "src/foo.rs", Some(10));
        assert!(!findings_match(&a, &b));
    }

    #[test]
    fn test_different_file_no_match() {
        let a = make_finding("dead_code", "src/foo.rs", Some(10));
        let b = make_finding("dead_code", "src/bar.rs", Some(10));
        assert!(!findings_match(&a, &b));
    }

    #[test]
    fn test_file_level_findings_match() {
        let a = make_finding("circular_dependency", "src/foo.rs", None);
        let b = make_finding("circular_dependency", "src/foo.rs", None);
        assert!(findings_match(&a, &b));
    }

    #[test]
    fn test_line_vs_no_line_no_match() {
        let a = make_finding("dead_code", "src/foo.rs", Some(10));
        let b = make_finding("dead_code", "src/foo.rs", None);
        assert!(!findings_match(&a, &b));
    }

    #[test]
    fn test_diff_new_and_fixed() {
        let baseline = vec![
            make_finding("dead_code", "src/foo.rs", Some(10)),
            make_finding("magic_number", "src/bar.rs", Some(20)),
        ];
        let head = vec![
            make_finding("dead_code", "src/foo.rs", Some(11)), // shifted by 1, same issue
            make_finding("xss", "src/web.rs", Some(5)),        // new
        ];

        let result = diff_findings(&baseline, &head, "main", "HEAD", Some(96.0), Some(95.5));

        assert_eq!(result.new_findings.len(), 1);
        assert_eq!(result.new_findings[0].detector, "xss");

        assert_eq!(result.fixed_findings.len(), 1);
        assert_eq!(result.fixed_findings[0].detector, "magic_number");

        let delta = result.score_after.unwrap() - result.score_before.unwrap();
        assert!((delta - (-0.5)).abs() < f64::EPSILON);
    }

    #[test]
    fn test_diff_no_changes() {
        let findings = vec![make_finding("dead_code", "src/foo.rs", Some(10))];
        let result = diff_findings(&findings, &findings, "main", "HEAD", None, None);
        assert!(result.new_findings.is_empty());
        assert!(result.fixed_findings.is_empty());
        assert!(result.score_before.is_none());
        assert!(result.score_after.is_none());
    }

    #[test]
    fn test_format_json_structure() {
        let result = SmartDiffResult {
            base_ref: "main".to_string(),
            head_ref: "HEAD".to_string(),
            files_changed: 2,
            new_findings: vec![AttributedFinding {
                finding: make_finding("xss", "src/web.rs", Some(5)),
                attribution: Attribution::InChangedHunk,
            }],
            all_new_count: 1,
            fixed_findings: vec![make_finding("dead_code", "src/old.rs", Some(10))],
            score_before: Some(96.0),
            score_after: Some(95.5),
        };

        let json_str = format_json(&result);
        let parsed: serde_json::Value = serde_json::from_str(&json_str).expect("valid JSON");

        assert_eq!(parsed["base_ref"], "main");
        assert_eq!(parsed["head_ref"], "HEAD");
        assert_eq!(parsed["files_changed"], 2);
        assert_eq!(parsed["new_findings"].as_array().unwrap().len(), 1);
        assert_eq!(parsed["new_findings"][0]["attribution"], "in_changed_hunk");
        assert_eq!(parsed["fixed_findings"].as_array().unwrap().len(), 1);
        assert_eq!(parsed["score_delta"], -0.5);
    }

    #[test]
    fn test_format_text_no_new_findings() {
        let result = SmartDiffResult {
            base_ref: "main".to_string(),
            head_ref: "HEAD".to_string(),
            files_changed: 1,
            new_findings: vec![],
            all_new_count: 0,
            fixed_findings: vec![],
            score_before: Some(97.0),
            score_after: Some(97.0),
        };

        let text = format_text(&result, true);
        assert!(text.contains("No new findings"));
        assert!(!text.contains("--all"));
    }

    #[test]
    fn test_format_text_filtered_hint() {
        let result = SmartDiffResult {
            base_ref: "main".to_string(),
            head_ref: "HEAD".to_string(),
            files_changed: 1,
            new_findings: vec![],
            all_new_count: 5,
            fixed_findings: vec![],
            score_before: Some(90.0),
            score_after: Some(88.0),
        };

        let text = format_text(&result, true);
        assert!(text.contains("No new findings in your changes"));
        assert!(text.contains("5 findings in other files"));
        assert!(text.contains("--all"));
    }

    #[test]
    fn test_format_json_total_new_findings() {
        let result = SmartDiffResult {
            base_ref: "main".to_string(),
            head_ref: "HEAD".to_string(),
            files_changed: 1,
            new_findings: vec![],
            all_new_count: 3,
            fixed_findings: vec![],
            score_before: Some(90.0),
            score_after: Some(90.0),
        };

        let json_str = format_json(&result);
        let parsed: serde_json::Value = serde_json::from_str(&json_str).unwrap();
        assert_eq!(parsed["total_new_findings"], 3);
    }
}
