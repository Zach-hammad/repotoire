//! Diff command — compare findings between two analysis states
//!
//! Shows new findings, fixed findings, and score delta.

use crate::models::{Finding, FindingsSummary, HealthReport, Severity};
use console::style;
use serde_json::json;

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

/// Result of diffing two sets of findings.
#[derive(Debug)]
pub struct DiffResult {
    pub base_ref: String,
    pub head_ref: String,
    pub files_changed: usize,
    pub new_findings: Vec<Finding>,
    pub fixed_findings: Vec<Finding>,
    pub score_before: Option<f64>,
    pub score_after: Option<f64>,
}

impl DiffResult {
    pub fn score_delta(&self) -> Option<f64> {
        match (self.score_before, self.score_after) {
            (Some(before), Some(after)) => Some(after - before),
            _ => None,
        }
    }
}

/// Compute the diff between baseline and head findings.
pub fn diff_findings(
    baseline: &[Finding],
    head: &[Finding],
    base_ref: &str,
    head_ref: &str,
    files_changed: usize,
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
        files_changed,
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

/// Render the diff result as colored terminal text.
///
/// When `no_emoji` is true, plain ASCII markers are used instead of emoji.
pub fn format_text(result: &DiffResult, no_emoji: bool) -> String {
    let mut out = String::new();

    // Header
    out.push_str(&format!(
        "Repotoire Diff: {}..{} ({} files changed)\n\n",
        result.base_ref, result.head_ref, result.files_changed,
    ));

    // --- New findings ---
    out.push_str(&format!("{}\n", style("NEW FINDINGS").bold().underlined()));

    if result.new_findings.is_empty() {
        let check = if no_emoji { "[ok]" } else { "\u{2705}" };
        out.push_str(&format!(
            "  {} {}\n",
            check,
            style("No new findings").green()
        ));
    } else {
        for f in &result.new_findings {
            let icon = severity_icon(&f.severity);
            let file = f
                .affected_files
                .first()
                .map(|p| p.display().to_string())
                .unwrap_or_default();
            let location = match f.line_start {
                Some(l) => format!("{}:{}", file, l),
                None => file,
            };
            out.push_str(&format!(
                "  {} {} — {}\n",
                style(icon).red(),
                f.title,
                style(&location).dim(),
            ));
        }
    }
    out.push('\n');

    // --- Fixed findings ---
    out.push_str(&format!(
        "{}\n",
        style("FIXED FINDINGS").bold().underlined()
    ));

    if result.fixed_findings.is_empty() {
        out.push_str("  (none)\n");
    } else {
        for f in &result.fixed_findings {
            let check = if no_emoji { "[ok]" } else { "\u{2705}" };
            let file = f
                .affected_files
                .first()
                .map(|p| p.display().to_string())
                .unwrap_or_default();
            let location = match f.line_start {
                Some(l) => format!("{}:{}", file, l),
                None => file,
            };
            out.push_str(&format!(
                "  {} {} — {}\n",
                style(check).green(),
                f.title,
                style(&location).dim(),
            ));
        }
    }
    out.push('\n');

    // --- Score delta ---
    if let (Some(before), Some(after)) = (result.score_before, result.score_after) {
        let delta = after - before;
        let delta_str = if delta >= 0.0 {
            style(format!("+{:.1}", delta)).green().to_string()
        } else {
            style(format!("{:.1}", delta)).red().to_string()
        };
        out.push_str(&format!(
            "SCORE: {:.1} \u{2192} {:.1} ({})\n",
            before, after, delta_str,
        ));
    }

    out
}

/// Render the diff result as pretty-printed JSON.
pub fn format_json(result: &DiffResult) -> String {
    let new_summary = FindingsSummary::from_findings(&result.new_findings);
    let fixed_summary = FindingsSummary::from_findings(&result.fixed_findings);

    let score_delta = result.score_delta();

    let new_findings_json: Vec<serde_json::Value> = result
        .new_findings
        .iter()
        .map(|f| {
            json!({
                "detector": f.detector,
                "severity": f.severity.to_string(),
                "title": f.title,
                "description": f.description,
                "file": f.affected_files.first().map(|p| p.display().to_string()).unwrap_or_default(),
                "line": f.line_start,
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

/// Render the diff result as SARIF 2.1.0 (only new findings).
///
/// Builds a temporary `HealthReport` containing only the new findings,
/// then delegates to the existing SARIF reporter.
pub fn format_sarif(result: &DiffResult) -> anyhow::Result<String> {
    let report = HealthReport {
        overall_score: result.score_after.unwrap_or(0.0),
        grade: String::new(),
        structure_score: 0.0,
        quality_score: 0.0,
        architecture_score: None,
        findings: result.new_findings.clone(),
        findings_summary: FindingsSummary::from_findings(&result.new_findings),
        total_files: 0,
        total_functions: 0,
        total_classes: 0,
        total_loc: 0,
    };

    crate::reporters::report(&report, "sarif")
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

        let result = diff_findings(&baseline, &head, "main", "HEAD", 3, Some(96.0), Some(95.5));

        assert_eq!(result.new_findings.len(), 1);
        assert_eq!(result.new_findings[0].detector, "xss");

        assert_eq!(result.fixed_findings.len(), 1);
        assert_eq!(result.fixed_findings[0].detector, "magic_number");

        assert!((result.score_delta().unwrap() - (-0.5)).abs() < f64::EPSILON);
    }

    #[test]
    fn test_diff_no_changes() {
        let findings = vec![make_finding("dead_code", "src/foo.rs", Some(10))];
        let result = diff_findings(&findings, &findings, "main", "HEAD", 0, None, None);
        assert!(result.new_findings.is_empty());
        assert!(result.fixed_findings.is_empty());
        assert!(result.score_delta().is_none());
    }

    #[test]
    fn test_format_json_structure() {
        let result = DiffResult {
            base_ref: "main".to_string(),
            head_ref: "HEAD".to_string(),
            files_changed: 2,
            new_findings: vec![make_finding("xss", "src/web.rs", Some(5))],
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
        assert_eq!(parsed["fixed_findings"].as_array().unwrap().len(), 1);
        assert_eq!(parsed["score_delta"], -0.5);
    }

    #[test]
    fn test_format_text_no_new_findings() {
        let result = DiffResult {
            base_ref: "main".to_string(),
            head_ref: "HEAD".to_string(),
            files_changed: 1,
            new_findings: vec![],
            fixed_findings: vec![],
            score_before: Some(97.0),
            score_after: Some(97.0),
        };

        let text = format_text(&result, true);
        assert!(text.contains("No new findings"));
    }
}
