//! Text (terminal) reporter with colors and formatting

use crate::models::{HealthReport, Severity};
use anyhow::Result;

/// Grade colors (ANSI escape codes)
fn grade_color(grade: &str) -> &'static str {
    match grade {
        "A" => "\x1b[32m", // Green
        "B" => "\x1b[92m", // Light green
        "C" => "\x1b[33m", // Yellow
        "D" => "\x1b[91m", // Light red
        "F" => "\x1b[31m", // Red
        _ => "\x1b[0m",
    }
}

/// Severity colors
fn severity_color(severity: &Severity) -> &'static str {
    match severity {
        Severity::Critical => "\x1b[31m", // Red
        Severity::High => "\x1b[91m",     // Light red
        Severity::Medium => "\x1b[33m",   // Yellow
        Severity::Low => "\x1b[34m",      // Blue
        Severity::Info => "\x1b[90m",     // Gray
    }
}

/// Reset ANSI color
const RESET: &str = "\x1b[0m";
const BOLD: &str = "\x1b[1m";
const DIM: &str = "\x1b[2m";

/// Severity tag
fn severity_tag(severity: &Severity) -> &'static str {
    match severity {
        Severity::Critical => "[C]",
        Severity::High => "[H]",
        Severity::Medium => "[M]",
        Severity::Low => "[L]",
        Severity::Info => "[I]",
    }
}

/// Render report as formatted terminal output
pub fn render(report: &HealthReport) -> Result<String> {
    let mut out = String::new();

    // Header
    let grade_c = grade_color(&report.grade);
    out.push_str(&format!("\n{BOLD}Repotoire Analysis{RESET}\n"));
    out.push_str(&format!(
        "{DIM}──────────────────────────────────────{RESET}\n"
    ));
    out.push_str(&format!(
        "Score: {BOLD}{:.1}/100{RESET}  Grade: {grade_c}{BOLD}{}{RESET}  ",
        report.overall_score, report.grade
    ));
    out.push_str(&format!(
        "Files: {}  Functions: {}  Classes: {}  LOC: {}\n\n",
        report.total_files, report.total_functions, report.total_classes, report.total_loc
    ));

    // Category scores (compact)
    out.push_str(&format!("{BOLD}SCORES{RESET}\n"));
    out.push_str(&format!(
        "  Structure: {}  Quality: {}",
        format_score(report.structure_score),
        format_score(report.quality_score)
    ));
    if let Some(arch) = report.architecture_score {
        out.push_str(&format!("  Architecture: {}", format_score(arch)));
    }
    out.push_str("\n\n");

    // Findings summary
    let fs = &report.findings_summary;
    out.push_str(&format!("{BOLD}FINDINGS{RESET} ({} total)\n", fs.total));

    let mut summary_parts = Vec::new();
    if fs.critical > 0 {
        summary_parts.push(format!("\x1b[31m{} critical{RESET}", fs.critical));
    }
    if fs.high > 0 {
        summary_parts.push(format!("\x1b[91m{} high{RESET}", fs.high));
    }
    if fs.medium > 0 {
        summary_parts.push(format!("\x1b[33m{} medium{RESET}", fs.medium));
    }
    if fs.low > 0 {
        summary_parts.push(format!("\x1b[34m{} low{RESET}", fs.low));
    }
    if !summary_parts.is_empty() {
        out.push_str(&format!("  {}\n\n", summary_parts.join(" | ")));
    }

    // Top findings as table
    if !report.findings.is_empty() {
        out.push_str(&format!(
            "{DIM}  #   SEV   TITLE                                    FILE{RESET}\n"
        ));
        out.push_str(&format!(
            "{DIM}  ─────────────────────────────────────────────────────────────────{RESET}\n"
        ));

        for (i, finding) in report.findings.iter().take(10).enumerate() {
            let sev_c = severity_color(&finding.severity);
            let sev_tag = severity_tag(&finding.severity);

            // Truncate title if too long — use chars() to avoid UTF-8 panic (#8)
            let title: String = finding.title.chars().take(35).collect();
            let title = if finding.title.chars().count() > 38 {
                format!("{}...", title)
            } else {
                finding.title.clone()
            };

            // Get file and line
            let file_info = format_file_location(finding);

            out.push_str(&format!(
                "  {DIM}{:>3}{RESET}  {sev_c}{}{RESET}  {:<40}  {DIM}{}{RESET}\n",
                i + 1,
                sev_tag,
                title,
                file_info
            ));
        }

        let remaining = report.findings.len().saturating_sub(10);
        if remaining > 0 {
            out.push_str(&format!(
                "\n  {DIM}...and {} more (use --page 2 or findings -i){RESET}\n",
                remaining
            ));
        }
        out.push('\n');
    }

    // Tips based on grade
    match report.grade.as_str() {
        "A" => out.push_str(&format!("{DIM}Excellent! Keep up the good work.{RESET}\n")),
        "B" => out.push_str(&format!(
            "{DIM}Good shape. Address remaining issues for an A.{RESET}\n"
        )),
        "C" | "D" | "F" => {
            out.push_str(&format!(
                "{DIM}Run `repotoire findings -i` for interactive review.{RESET}\n"
            ));
        }
        _ => {}
    }

    Ok(out)
}

/// Format score with color
fn format_file_location(finding: &crate::models::Finding) -> String {
    let Some(file) = finding.affected_files.first() else {
        return String::new();
    };
    let file_str = file.display().to_string();
    let short_file = if file_str.chars().count() > 25 {
        let skip = file_str.chars().count() - 22;
        format!("...{}", file_str.chars().skip(skip).collect::<String>())
    } else {
        file_str
    };
    match finding.line_start {
        Some(line) => format!("{}:{}", short_file, line),
        None => short_file,
    }
}

fn format_score(score: f64) -> String {
    let color = if score >= 80.0 {
        "\x1b[32m"
    } else if score >= 60.0 {
        "\x1b[33m"
    } else {
        "\x1b[31m"
    };
    format!("{color}{:.0}{RESET}", score)
}
