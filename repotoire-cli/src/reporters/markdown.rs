//! Markdown reporter for GitHub-flavored Markdown output
//!
//! Generates reports suitable for:
//! - README files
//! - Pull request comments
//! - GitHub wikis
//! - Documentation

use crate::models::{Finding, HealthReport, Severity};
use anyhow::Result;
use chrono::Local;

/// Maximum findings to show per severity level
const MAX_FINDINGS_PER_SEVERITY: usize = 10;

/// Render report as GitHub-flavored Markdown
pub fn render(report: &HealthReport) -> Result<String> {
    let mut md = String::new();

    // Header
    md.push_str(&render_header(report));
    md.push('\n');

    // Table of Contents
    md.push_str(&render_toc());
    md.push('\n');

    // Summary
    md.push_str(&render_summary(report));
    md.push('\n');

    // Category Scores
    md.push_str(&render_category_scores(report));
    md.push('\n');

    // Key Metrics
    md.push_str(&render_metrics(report));
    md.push('\n');

    // Findings Summary
    md.push_str(&render_findings_summary(report));
    md.push('\n');

    // Detailed Findings
    md.push_str(&render_detailed_findings(report));
    md.push('\n');

    // Footer
    md.push_str(&render_footer());

    Ok(md)
}

fn render_header(report: &HealthReport) -> String {
    let grade_emoji = match report.grade.as_str() {
        "A" => "🏆",
        "B" => "⭐",
        "C" => "⚠️",
        "D" => "❌",
        "F" => "💀",
        _ => "❓",
    };

    let timestamp = Local::now().format("%Y-%m-%d %H:%M:%S");

    format!(
        r#"# {} Repotoire Code Health Report

**Grade: {}** | **Score: {:.1}/100**

Generated: {}
"#,
        grade_emoji, report.grade, report.overall_score, timestamp
    )
}

fn render_toc() -> String {
    r#"## Table of Contents

- [Summary](#summary)
- [Category Scores](#category-scores)
- [Key Metrics](#key-metrics)
- [Findings Summary](#findings-summary)
- [Detailed Findings](#detailed-findings)
"#
    .to_string()
}

fn render_summary(report: &HealthReport) -> String {
    let assessment = match report.grade.as_str() {
        "A" => "Excellent - Code is well-structured and maintainable",
        "B" => "Good - Minor improvements recommended",
        "C" => "Fair - Several issues should be addressed",
        "D" => "Poor - Significant refactoring needed",
        "F" => "Critical - Major technical debt present",
        _ => "",
    };

    format!(
        r#"## Summary

| Metric | Value |
|--------|-------|
| **Overall Grade** | {} |
| **Overall Score** | {:.1}/100 |
| **Total Findings** | {} |
| **Assessment** | {} |
"#,
        report.grade, report.overall_score, report.findings_summary.total, assessment
    )
}

fn render_category_scores(report: &HealthReport) -> String {
    let arch = report.architecture_score.unwrap_or(0.0);

    format!(
        r#"## Category Scores

| Category | Weight | Score | Status |
|----------|--------|-------|--------|
| Graph Structure | 40% | {:.1}/100 | {} |
| Code Quality | 30% | {:.1}/100 | {} |
| Architecture Health | 30% | {:.1}/100 | {} |
"#,
        report.structure_score,
        score_indicator(report.structure_score),
        report.quality_score,
        score_indicator(report.quality_score),
        arch,
        score_indicator(arch)
    )
}

fn render_metrics(report: &HealthReport) -> String {
    format!(
        r#"## Key Metrics

### Codebase Size

| Metric | Value |
|--------|-------|
| Total Files | {} |
| Total Functions | {} |
| Total Classes | {} |
"#,
        report.total_files, report.total_functions, report.total_classes
    )
}

fn render_findings_summary(report: &HealthReport) -> String {
    let fs = &report.findings_summary;

    format!(
        r#"## Findings Summary

| Severity | Count | Emoji |
|----------|-------|-------|
| Critical | {} | 🔴 |
| High | {} | 🟠 |
| Medium | {} | 🟡 |
| Low | {} | 🔵 |
| Info | {} | ℹ️ |
| **Total** | **{}** | |
"#,
        fs.critical, fs.high, fs.medium, fs.low, fs.info, fs.total
    )
}

fn render_detailed_findings(report: &HealthReport) -> String {
    let mut md = String::from("## Detailed Findings\n\n");

    if report.findings.is_empty() {
        md.push_str("✅ No issues found! Your codebase is in great shape.\n");
        return md;
    }

    // Group findings by severity
    let severity_order = [
        Severity::Critical,
        Severity::High,
        Severity::Medium,
        Severity::Low,
        Severity::Info,
    ];

    for severity in severity_order {
        let findings: Vec<&Finding> = report
            .findings
            .iter()
            .filter(|f| f.severity == severity)
            .collect();

        if findings.is_empty() {
            continue;
        }

        let emoji = severity_emoji(&severity);
        let label = severity.to_string();

        md.push_str(&format!(
            "### {} {} Findings ({})\n\n",
            emoji,
            capitalize(&label),
            findings.len()
        ));

        // Show up to MAX_FINDINGS_PER_SEVERITY
        let shown: Vec<_> = findings.iter().take(MAX_FINDINGS_PER_SEVERITY).collect();
        let hidden = findings.len().saturating_sub(MAX_FINDINGS_PER_SEVERITY);

        for finding in shown {
            md.push_str(&render_finding(finding));
        }

        if hidden > 0 {
            md.push_str(&format!("*...and {} more {} findings*\n\n", hidden, label));
        }
    }

    md
}

fn render_finding(finding: &Finding) -> String {
    let mut md = String::new();

    // Title with detector badge
    let detector = finding.detector.replace("Detector", "");
    md.push_str(&format!("#### {}\n\n", finding.title));
    md.push_str(&format!("`{}` ", detector));

    // Location if available
    if let Some(line) = finding.line_start {
        if let Some(file) = finding.affected_files.first() {
            md.push_str(&format!("at `{}:{}`", file.display(), line));
        }
    }
    md.push_str("\n\n");

    // Description
    if !finding.description.is_empty() {
        md.push_str(&format!("{}\n\n", finding.description));
    }

    // Affected files
    if !finding.affected_files.is_empty() {
        let files: Vec<String> = finding
            .affected_files
            .iter()
            .take(5)
            .map(|f| format!("`{}`", f.display()))
            .collect();

        let more = if finding.affected_files.len() > 5 {
            format!(" (+{} more)", finding.affected_files.len() - 5)
        } else {
            String::new()
        };

        md.push_str(&format!("**Files:** {}{}\n\n", files.join(", "), more));
    }

    // Suggested fix
    if let Some(fix) = &finding.suggested_fix {
        md.push_str(&format!("> **💡 Fix:** {}\n\n", fix));
    }

    md
}

fn render_footer() -> String {
    r#"---

*Generated by [Repotoire](https://repotoire.com) - Graph-Powered Code Health Platform*
"#
    .to_string()
}

fn score_indicator(score: f64) -> &'static str {
    if score >= 80.0 {
        "✅ Good"
    } else if score >= 60.0 {
        "⚠️ Fair"
    } else {
        "❌ Poor"
    }
}

fn severity_emoji(severity: &Severity) -> &'static str {
    match severity {
        Severity::Critical => "🔴",
        Severity::High => "🟠",
        Severity::Medium => "🟡",
        Severity::Low => "🔵",
        Severity::Info => "ℹ️",
    }
}

fn capitalize(s: &str) -> String {
    let mut chars = s.chars();
    match chars.next() {
        None => String::new(),
        Some(c) => c.to_uppercase().collect::<String>() + chars.as_str(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::reporters::tests::test_report;

    #[test]
    fn test_markdown_render_has_header() {
        let report = test_report();
        let md = render(&report).unwrap();
        assert!(md.contains("# "));
        assert!(md.contains("Grade: B"));
        assert!(md.contains("85.0/100"));
    }

    #[test]
    fn test_markdown_render_has_findings() {
        let report = test_report();
        let md = render(&report).unwrap();
        assert!(md.contains("Test finding"));
        assert!(md.contains("src/main.rs"));
    }

    #[test]
    fn test_markdown_empty_findings() {
        let mut report = test_report();
        report.findings.clear();
        report.findings_summary = Default::default();
        let md = render(&report).unwrap();
        assert!(md.contains("No issues found"));
    }

    #[test]
    fn test_markdown_has_table_of_contents() {
        let report = test_report();
        let md = render(&report).unwrap();
        assert!(md.contains("## Table of Contents"));
    }
}
