//! Text (terminal) reporter with colors and formatting

use crate::models::{Grade, HealthReport, Severity};
use crate::reporters::report_context::ReportContext;
use anyhow::Result;
use std::collections::HashMap;

/// Grade colors (ANSI escape codes)
fn grade_color(grade: &Grade) -> &'static str {
    match grade {
        Grade::APlus | Grade::A | Grade::AMinus => "\x1b[32m", // Green
        Grade::BPlus | Grade::B | Grade::BMinus => "\x1b[92m", // Light green
        Grade::CPlus | Grade::C | Grade::CMinus => "\x1b[33m", // Yellow
        Grade::DPlus | Grade::D | Grade::DMinus => "\x1b[91m", // Light red
        Grade::F => "\x1b[31m",                                 // Red
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

            // Confidence provenance line
            if let Some(conf) = finding.confidence {
                let pct = (conf * 100.0) as u32;
                let signals = finding
                    .threshold_metadata
                    .get("confidence_signals")
                    .cloned()
                    .unwrap_or_default();
                if signals.is_empty() {
                    out.push_str(&format!(
                        "       {DIM}[confidence: {}%]{RESET}\n",
                        pct
                    ));
                } else {
                    out.push_str(&format!(
                        "       {DIM}[confidence: {}% \u{2014} {}]{RESET}\n",
                        pct, signals
                    ));
                }
            }
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
    match report.grade {
        Grade::APlus | Grade::A | Grade::AMinus => {
            out.push_str(&format!("{DIM}Excellent! Keep up the good work.{RESET}\n"));
        }
        Grade::BPlus | Grade::B | Grade::BMinus => out.push_str(&format!(
            "{DIM}Good shape. Address remaining issues for an A.{RESET}\n"
        )),
        _ => {
            out.push_str(&format!(
                "{DIM}Run `repotoire findings -i` for interactive review.{RESET}\n"
            ));
        }
    }

    Ok(out)
}

/// Severity weight for ranking
fn severity_weight(severity: &Severity) -> f64 {
    match severity {
        Severity::Critical => 4.0,
        Severity::High => 3.0,
        Severity::Medium => 2.0,
        Severity::Low => 1.0,
        Severity::Info => 0.0,
    }
}

/// Render report with full context — themed output with "What stands out" and "Quick wins"
pub fn render_with_context(ctx: &ReportContext) -> Result<String> {
    let report = &ctx.health;
    let mut out = String::new();

    // ── Header ──────────────────────────────────────────────────────
    let grade_c = grade_color(&report.grade);
    out.push_str(&format!("\n{BOLD}Repotoire Analysis{RESET}\n"));
    out.push_str(&format!(
        "{DIM}──────────────────────────────────────{RESET}\n"
    ));

    // Score line — with optional delta
    out.push_str(&format!(
        "Score: {BOLD}{:.1}/100{RESET}",
        report.overall_score
    ));

    if let Some(prev) = &ctx.previous_health {
        let delta = report.overall_score - prev.overall_score;
        let fixed = prev
            .findings
            .len()
            .saturating_sub(report.findings.len());
        let new_findings = report
            .findings
            .len()
            .saturating_sub(prev.findings.len());
        // Only show delta if it's meaningful (>= 0.05)
        if delta.abs() >= 0.05 {
            if delta >= 0.0 {
                out.push_str(&format!(" {BOLD}(+{:.1}){RESET}", delta));
            } else {
                out.push_str(&format!(" {BOLD}({:.1}){RESET}", delta));
            }
        }
        out.push_str(&format!(
            "  Grade: {grade_c}{BOLD}{}{RESET}",
            report.grade
        ));
        if fixed > 0 {
            out.push_str(&format!("  Fixed {} findings", fixed));
        }
        if new_findings > 0 {
            out.push_str(&format!("  {} new findings", new_findings));
        }
    } else {
        out.push_str(&format!(
            "  Grade: {grade_c}{BOLD}{}{RESET}",
            report.grade
        ));
    }

    out.push_str(&format!(
        "   Files: {}  Functions: {}  LOC: {}\n",
        format_number(report.total_files),
        format_number(report.total_functions),
        format_number(report.total_loc),
    ));

    // Sub-scores
    out.push_str(&format!(
        "\n  Structure: {}  Quality: {}",
        format_score(report.structure_score),
        format_score(report.quality_score)
    ));
    if let Some(arch) = report.architecture_score {
        out.push_str(&format!("  Architecture: {}", format_score(arch)));
    }
    out.push('\n');

    // ── What stands out ─────────────────────────────────────────────
    let buckets = build_category_buckets(&report.findings);
    let notable_buckets = top_notable_buckets(&buckets, 3);

    if !notable_buckets.is_empty() {
        out.push_str(&format!("\n{BOLD}What stands out{RESET}\n"));
        let max_weight = notable_buckets
            .first()
            .map(|(_, w, _)| *w)
            .unwrap_or(0.0);

        for (category, weight, summary) in &notable_buckets {
            let arrow = if *weight == max_weight && max_weight > 0.0 {
                format!("    {DIM}\u{2190} fix these first{RESET}")
            } else {
                String::new()
            };
            let display_cat = capitalize(category);
            out.push_str(&format!(
                "  {:<15}{}{}\n",
                display_cat, summary, arrow
            ));
        }
    }

    // ── Knowledge Risk ──────────────────────────────────────────────
    if let Some(knowledge_risk) = render_knowledge_risk(ctx) {
        out.push_str(&knowledge_risk);
    }

    // ── Quick wins ──────────────────────────────────────────────────
    let quick_wins = top_quick_wins(&report.findings, 3);
    if !quick_wins.is_empty() {
        out.push_str(&format!("\n{BOLD}Quick wins{RESET} (highest impact, lowest effort)\n"));

        for (i, finding) in quick_wins.iter().enumerate() {
            let sev_c = severity_color(&finding.severity);
            let sev_tag = severity_tag(&finding.severity);
            let title: String = finding.title.chars().take(35).collect();
            let title = if finding.title.chars().count() > 35 {
                format!("{}...", title)
            } else {
                title
            };
            let file_info = format_file_location(finding);

            out.push_str(&format!(
                "  {}. {sev_c}{}{RESET} {:<35}  {DIM}{}{RESET}\n",
                i + 1,
                sev_tag,
                title,
                file_info
            ));
        }

        out.push_str(&format!(
            "\n  {DIM}Fix the top one: repotoire fix <id>{RESET}\n"
        ));
        out.push_str(&format!(
            "  {DIM}Explore all:     repotoire findings -i{RESET}\n"
        ));
        out.push_str(&format!(
            "  {DIM}Full report:     repotoire analyze . --format html -o report.html{RESET}\n"
        ));
    }

    // ── First-run tips ──────────────────────────────────────────────
    if ctx.previous_health.is_none() && console::Term::stdout().is_term() {
        out.push_str(&format!(
            "\n{DIM}──────────────────────────────────────{RESET}\n"
        ));
        out.push_str(&format!("{BOLD}First analysis complete!{RESET} Next steps:\n"));
        out.push_str(&format!(
            "  {DIM}repotoire fix <id>            Fix the top finding{RESET}\n"
        ));
        out.push_str(&format!(
            "  {DIM}repotoire findings -i        Explore interactively{RESET}\n"
        ));
        out.push_str(&format!(
            "  {DIM}repotoire analyze --format html -o report.html   Shareable report{RESET}\n"
        ));
        out.push_str(&format!(
            "  {DIM}repotoire init               Customize thresholds and exclusions{RESET}\n"
        ));
    }

    Ok(out)
}

/// Render knowledge risk section for the text report.
fn render_knowledge_risk(ctx: &ReportContext) -> Option<String> {
    let git = ctx.git_data.as_ref()?;
    if git.bus_factor_files.is_empty() && git.file_ownership.is_empty() {
        return None;
    }

    let mut out = String::new();
    out.push_str(&format!("\n{BOLD}Knowledge Risk{RESET}\n"));

    // Project bus factor
    if let Some(pbf) = git.project_bus_factor {
        let interp = match pbf {
            0 => " (critical)",
            1 => " (high risk)",
            2..=3 => " (moderate)",
            _ => " (healthy)",
        };
        out.push_str(&format!("  Project bus factor: {pbf}{interp}\n"));
    }

    // At-risk modules
    let mut dir_risk: HashMap<String, (usize, usize)> = HashMap::new();
    for fo in &git.file_ownership {
        let dir = std::path::Path::new(&fo.path)
            .parent()
            .and_then(|p| p.to_str())
            .unwrap_or(".")
            .to_string();
        let entry = dir_risk.entry(dir).or_insert((0, 0));
        if fo.bus_factor <= 1 {
            entry.0 += 1;
        }
        entry.1 += 1;
    }
    let mut risky_dirs: Vec<_> = dir_risk.into_iter().filter(|(_, (r, _))| *r > 0).collect();
    risky_dirs.sort_by(|a, b| b.1 .0.cmp(&a.1 .0));

    if !risky_dirs.is_empty() {
        out.push_str(&format!(
            "\n  {DIM}At-risk modules (bus factor \u{2264} 1):{RESET}\n"
        ));
        for (dir, (risky, total)) in risky_dirs.iter().take(5) {
            out.push_str(&format!(
                "    {:<30} \u{2502} {risky}/{total} files at risk\n",
                dir
            ));
        }
    }

    // Top riskiest files
    let mut risky_files: Vec<_> = git.bus_factor_files.iter().collect();
    risky_files.sort_by_key(|(_, bf)| *bf);
    risky_files.truncate(10);

    if !risky_files.is_empty() {
        out.push_str(&format!("\n  {DIM}Top riskiest files:{RESET}\n"));
        for (path, bf) in &risky_files {
            out.push_str(&format!(
                "    {:<40} \u{2502} bus factor {bf}\n",
                path
            ));
        }
    }

    Some(out)
}

/// A category bucket: counts by severity
struct CategoryBucket {
    critical: usize,
    high: usize,
    medium: usize,
    low: usize,
}

impl CategoryBucket {
    fn weighted_score(&self) -> f64 {
        self.critical as f64 * 4.0
            + self.high as f64 * 3.0
            + self.medium as f64 * 2.0
            + self.low as f64 * 1.0
    }

    fn has_notable_findings(&self) -> bool {
        self.critical > 0 || self.high > 0 || self.medium > 0
    }

    fn summary_line(&self) -> String {
        let mut parts = Vec::new();
        if self.critical > 0 {
            parts.push(format!("{} critical", self.critical));
        }
        if self.high > 0 {
            parts.push(format!("{} high", self.high));
        }
        if self.medium > 0 {
            parts.push(format!("{} medium", self.medium));
        }
        parts.join(", ")
    }
}

/// Group findings by category
fn build_category_buckets(
    findings: &[crate::models::Finding],
) -> HashMap<String, CategoryBucket> {
    let mut buckets: HashMap<String, CategoryBucket> = HashMap::new();
    for f in findings {
        let cat = f
            .category
            .as_deref()
            .unwrap_or("other")
            .to_lowercase();
        let bucket = buckets.entry(cat).or_insert(CategoryBucket {
            critical: 0,
            high: 0,
            medium: 0,
            low: 0,
        });
        match f.severity {
            Severity::Critical => bucket.critical += 1,
            Severity::High => bucket.high += 1,
            Severity::Medium => bucket.medium += 1,
            Severity::Low => bucket.low += 1,
            Severity::Info => {}
        }
    }
    buckets
}

/// Return top N notable buckets sorted by weighted score
fn top_notable_buckets(
    buckets: &HashMap<String, CategoryBucket>,
    n: usize,
) -> Vec<(String, f64, String)> {
    let mut entries: Vec<(String, f64, String)> = buckets
        .iter()
        .filter(|(_, b)| b.has_notable_findings())
        .map(|(cat, b)| (cat.clone(), b.weighted_score(), b.summary_line()))
        .collect();
    entries.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    entries.truncate(n);
    entries
}

/// Return top N findings ranked by impact score
fn top_quick_wins(
    findings: &[crate::models::Finding],
    n: usize,
) -> Vec<&crate::models::Finding> {
    let mut scored: Vec<(f64, &crate::models::Finding)> = findings
        .iter()
        .map(|f| {
            let base = severity_weight(&f.severity);
            let boost = if f.suggested_fix.is_some() {
                1.5
            } else {
                1.0
            };
            (base * boost, f)
        })
        .collect();
    scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    scored.truncate(n);
    scored.into_iter().map(|(_, f)| f).collect()
}

/// Format a number with comma separators
fn format_number(n: usize) -> String {
    let s = n.to_string();
    let mut result = String::new();
    for (i, c) in s.chars().rev().enumerate() {
        if i > 0 && i % 3 == 0 {
            result.push(',');
        }
        result.push(c);
    }
    result.chars().rev().collect()
}

/// Capitalize first letter
fn capitalize(s: &str) -> String {
    let mut chars = s.chars();
    match chars.next() {
        None => String::new(),
        Some(c) => c.to_uppercase().to_string() + chars.as_str(),
    }
}

/// Format file location for a finding
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::reporters::report_context::ReportContext;
    use crate::models::{Finding, FindingsSummary, HealthReport, Severity};

    fn test_context() -> ReportContext {
        let findings = vec![
            Finding {
                id: "f1".into(),
                detector: "HardcodedSecret".into(),
                severity: Severity::Critical,
                title: "Hardcoded AWS key".into(),
                category: Some("security".into()),
                suggested_fix: Some("Use env var".into()),
                affected_files: vec!["auth/config.py".into()],
                line_start: Some(34),
                ..Default::default()
            },
            Finding {
                id: "f2".into(),
                detector: "GodClass".into(),
                severity: Severity::High,
                title: "God class (47 methods)".into(),
                category: Some("architecture".into()),
                affected_files: vec!["engine/pipeline.rs".into()],
                line_start: Some(1),
                ..Default::default()
            },
            Finding {
                id: "f3".into(),
                detector: "DeepNesting".into(),
                severity: Severity::Medium,
                title: "Deep nesting (6 levels)".into(),
                category: Some("complexity".into()),
                affected_files: vec!["engine/parser.rs".into()],
                line_start: Some(55),
                ..Default::default()
            },
        ];
        ReportContext {
            health: HealthReport {
                overall_score: 82.5,
                grade: Grade::B,
                structure_score: 85.0,
                quality_score: 80.0,
                architecture_score: Some(82.0),
                findings_summary: FindingsSummary::from_findings(&findings),
                findings,
                total_files: 456,
                total_functions: 4348,
                total_classes: 200,
                total_loc: 23456,
            },
            graph_data: None,
            git_data: None,
            source_snippets: vec![],
            previous_health: None,
            style_profile: None,
        }
    }

    #[test]
    fn test_themed_output_contains_sections() {
        let ctx = test_context();
        let output = render_with_context(&ctx).unwrap();
        assert!(output.contains("What stands out"), "missing themed section");
        assert!(output.contains("Quick wins"), "missing quick wins");
        assert!(output.contains("findings -i"), "missing CTA");
    }

    #[test]
    fn test_score_delta_shown() {
        let mut ctx = test_context();
        let mut prev = ctx.health.clone();
        prev.overall_score = 80.0;
        ctx.previous_health = Some(prev);
        let output = render_with_context(&ctx).unwrap();
        assert!(output.contains("+2.5"), "missing score delta");
    }

    #[test]
    fn test_no_delta_on_first_run() {
        let ctx = test_context();
        let output = render_with_context(&ctx).unwrap();
        assert!(!output.contains("Fixed"), "should not show delta on first run");
        assert!(!output.contains("+"), "should not show + on first run");
    }

    #[test]
    fn test_categories_grouped() {
        let ctx = test_context();
        let output = render_with_context(&ctx).unwrap();
        // Should show security category (has critical finding)
        assert!(
            output.contains("security") || output.contains("Security"),
            "should group by category"
        );
    }

    #[test]
    fn test_format_number() {
        assert_eq!(format_number(0), "0");
        assert_eq!(format_number(999), "999");
        assert_eq!(format_number(1000), "1,000");
        assert_eq!(format_number(23456), "23,456");
        assert_eq!(format_number(1234567), "1,234,567");
    }
}
