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

/// Render report as formatted terminal output
pub fn render(report: &HealthReport) -> Result<String> {
    let mut out = String::new();

    // Header box
    let grade_c = grade_color(&report.grade);
    out.push_str(&format!(
        "\n{BOLD}╭────────────────────────────────────────────╮{RESET}\n"
    ));
    out.push_str(&format!(
        "{BOLD}│{RESET}  🎼 {BOLD}Repotoire Health Report{RESET}                 {BOLD}│{RESET}\n"
    ));
    out.push_str(&format!(
        "{BOLD}├────────────────────────────────────────────┤{RESET}\n"
    ));
    out.push_str(&format!(
        "{BOLD}│{RESET}     Score: {BOLD}{:.1}/100{RESET}   Grade: {grade_c}{BOLD}{}{RESET}         {BOLD}│{RESET}\n",
        report.overall_score, report.grade
    ));
    out.push_str(&format!(
        "{BOLD}╰────────────────────────────────────────────╯{RESET}\n\n"
    ));

    // Category scores
    out.push_str(&format!("{BOLD}📊 Category Scores{RESET}\n"));
    out.push_str(&format!(
        "   Structure:    {} {}\n",
        progress_bar(report.structure_score, 20),
        format_score(report.structure_score)
    ));
    out.push_str(&format!(
        "   Quality:      {} {}\n",
        progress_bar(report.quality_score, 20),
        format_score(report.quality_score)
    ));
    if let Some(arch) = report.architecture_score {
        out.push_str(&format!(
            "   Architecture: {} {}\n",
            progress_bar(arch, 20),
            format_score(arch)
        ));
    }
    out.push('\n');

    // Codebase metrics
    out.push_str(&format!("{BOLD}📈 Codebase{RESET}\n"));
    out.push_str(&format!("   📁 {} files  ", report.total_files));
    out.push_str(&format!("⚙️  {} functions  ", report.total_functions));
    out.push_str(&format!("🏛️  {} classes\n\n", report.total_classes));

    // Findings summary
    let fs = &report.findings_summary;
    out.push_str(&format!("{BOLD}🔍 Findings ({} total){RESET}\n", fs.total));
    if fs.critical > 0 {
        out.push_str(&format!(
            "   \x1b[31m🔴 Critical:  {}{RESET}\n",
            fs.critical
        ));
    }
    if fs.high > 0 {
        out.push_str(&format!("   \x1b[91m🟠 High:      {}{RESET}\n", fs.high));
    }
    if fs.medium > 0 {
        out.push_str(&format!("   \x1b[33m🟡 Medium:    {}{RESET}\n", fs.medium));
    }
    if fs.low > 0 {
        out.push_str(&format!("   \x1b[34m🔵 Low:       {}{RESET}\n", fs.low));
    }
    if fs.info > 0 {
        out.push_str(&format!("   {DIM}ℹ️  Info:      {}{RESET}\n", fs.info));
    }
    out.push('\n');

    // Top findings (up to 10)
    if !report.findings.is_empty() {
        out.push_str(&format!("{BOLD}📋 Top Issues{RESET}\n"));
        for (i, finding) in report.findings.iter().take(10).enumerate() {
            let sev_c = severity_color(&finding.severity);
            let sev_icon = match finding.severity {
                Severity::Critical => "🔴",
                Severity::High => "🟠",
                Severity::Medium => "🟡",
                Severity::Low => "🔵",
                Severity::Info => "ℹ️ ",
            };

            out.push_str(&format!(
                "   {DIM}{:2}.{RESET} {sev_c}{sev_icon} {}{RESET}\n",
                i + 1,
                finding.title
            ));

            // Show affected file
            if let Some(file) = finding.affected_files.first() {
                out.push_str(&format!("       {DIM}└─ {}{RESET}", file.display()));
                if let Some(line) = finding.line_start {
                    out.push_str(&format!(":{}", line));
                }
                out.push('\n');
            }
        }

        let remaining = report.findings.len().saturating_sub(10);
        if remaining > 0 {
            out.push_str(&format!(
                "   {DIM}...and {} more findings{RESET}\n",
                remaining
            ));
        }
        out.push('\n');
    }

    // Quick tips based on grade
    out.push_str(&format!("{BOLD}💡 Quick Tips{RESET}\n"));
    match report.grade.as_str() {
        "A" => out.push_str("   ✨ Excellent! Keep up the good work.\n"),
        "B" => out.push_str("   👍 Good shape. Address the remaining issues for an A.\n"),
        "C" => {
            out.push_str("   ⚠️  Fair. Focus on high-severity issues first.\n");
            if fs.critical > 0 {
                out.push_str(&format!(
                    "   🔴 Fix {} critical issues immediately.\n",
                    fs.critical
                ));
            }
        }
        "D" | "F" => {
            out.push_str("   🚨 Needs attention. Significant technical debt.\n");
            out.push_str("   📌 Run `repotoire findings` to see details.\n");
            out.push_str("   🔧 Run `repotoire fix <n>` for AI-assisted fixes.\n");
        }
        _ => {}
    }

    Ok(out)
}

/// Create an ASCII progress bar
fn progress_bar(score: f64, width: usize) -> String {
    let filled = ((score / 100.0) * width as f64).round() as usize;
    let empty = width.saturating_sub(filled);

    let color = if score >= 80.0 {
        "\x1b[32m"
    } else if score >= 60.0 {
        "\x1b[33m"
    } else {
        "\x1b[31m"
    };

    format!(
        "{color}[{}{}]{RESET}",
        "█".repeat(filled),
        "░".repeat(empty)
    )
}

/// Format score with color
fn format_score(score: f64) -> String {
    let color = if score >= 80.0 {
        "\x1b[32m"
    } else if score >= 60.0 {
        "\x1b[33m"
    } else {
        "\x1b[31m"
    };
    format!("{color}{:.1}{RESET}", score)
}
