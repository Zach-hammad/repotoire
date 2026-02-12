//! Findings command implementation

use anyhow::{Context, Result};
use console::style;
use std::fs;
use std::path::Path;

use crate::models::{Finding, Severity};
use super::tui;

/// Run interactive TUI mode
pub fn run_interactive(path: &Path) -> Result<()> {
    let findings = load_findings(path)?;
    if findings.is_empty() {
        println!("No findings! Your code looks clean.");
        return Ok(());
    }
    tui::run(findings, path.to_path_buf())
}

/// Load findings from last analysis
fn load_findings(path: &Path) -> Result<Vec<Finding>> {
    let findings_path = crate::cache::get_findings_cache_path(path);
    if !findings_path.exists() {
        anyhow::bail!(
            "No findings found. Run `repotoire analyze` first.\n\
             Looking for: {}",
            findings_path.display()
        );
    }

    let findings_json = fs::read_to_string(&findings_path)
        .context("Failed to read findings file")?;
    
    let parsed: serde_json::Value = serde_json::from_str(&findings_json)
        .context("Failed to parse findings file")?;
    let findings: Vec<Finding> = serde_json::from_value(
        parsed.get("findings").cloned().unwrap_or(serde_json::json!([]))
    ).context("Failed to parse findings array")?;

    Ok(findings)
}

pub fn run(path: &Path, index: Option<usize>, json: bool, top: Option<usize>, severity: Option<String>, page: usize, per_page: usize) -> Result<()> {
    let mut findings = load_findings(path)?;
    
    // Filter by severity if specified
    if let Some(min_sev) = &severity {
        let min = match min_sev.to_lowercase().as_str() {
            "critical" => Severity::Critical,
            "high" => Severity::High,
            "medium" => Severity::Medium,
            "low" => Severity::Low,
            _ => Severity::Info,
        };
        findings.retain(|f| f.severity >= min);
    }
    
    // Sort by severity (critical first)
    findings.sort_by(|a, b| b.severity.cmp(&a.severity));
    
    // Apply top N limit
    if let Some(n) = top {
        findings.truncate(n);
    }

    if findings.is_empty() {
        println!("{}", style("No findings! Your code looks clean.").green());
        return Ok(());
    }

    // If JSON output requested
    if json {
        println!("{}", serde_json::to_string_pretty(&findings)?);
        return Ok(());
    }

    // If specific index requested
    if let Some(idx) = index {
        if idx == 0 || idx > findings.len() {
            anyhow::bail!(
                "Invalid finding index: {}. Valid range: 1-{}",
                idx,
                findings.len()
            );
        }
        let finding = &findings[idx - 1];
        print_finding_detail(finding, idx);
        return Ok(());
    }

    // Print summary of all findings
    println!("{}", style("🔍 Code Findings").bold());
    println!();

    // Group by severity
    let critical: Vec<_> = findings.iter().filter(|f| f.severity == Severity::Critical).collect();
    let high: Vec<_> = findings.iter().filter(|f| f.severity == Severity::High).collect();
    let medium: Vec<_> = findings.iter().filter(|f| f.severity == Severity::Medium).collect();
    let low: Vec<_> = findings.iter().filter(|f| f.severity == Severity::Low).collect();

    println!("   {} {} critical", style(critical.len()).red().bold(), if critical.len() == 1 { "finding" } else { "findings" });
    println!("   {} {} high", style(high.len()).yellow().bold(), if high.len() == 1 { "finding" } else { "findings" });
    println!("   {} {} medium", style(medium.len()).cyan(), if medium.len() == 1 { "finding" } else { "findings" });
    println!("   {} {} low", style(low.len()).dim(), if low.len() == 1 { "finding" } else { "findings" });
    println!();

    // Apply pagination (per_page = 0 means all)
    let total_findings = findings.len();
    let (start_idx, end_idx, current_page, total_pages) = if per_page > 0 {
        let total_pages = total_findings.div_ceil(per_page);
        let current_page = page.max(1).min(total_pages.max(1));
        let start = (current_page - 1) * per_page;
        let end = (start + per_page).min(total_findings);
        (start, end, current_page, total_pages)
    } else {
        (0, total_findings, 1, 1)
    };

    for (i, finding) in findings.iter().enumerate().skip(start_idx).take(end_idx - start_idx) {
        let idx = i + 1;  // 1-indexed for user display
        let severity_icon = match finding.severity {
            Severity::Critical => style("🔴").red(),
            Severity::High => style("🟠").yellow(),
            Severity::Medium => style("🟡").cyan(),
            Severity::Low => style("⚪").dim(),
            Severity::Info => style("ℹ️").dim(),
        };
        
        let file = finding.affected_files.first()
            .map(|p| p.display().to_string())
            .unwrap_or_else(|| "unknown".to_string());
        
        let line = finding.line_start
            .map(|l| format!(":{}", l))
            .unwrap_or_default();

        println!(
            "{:>3}. {} {}",
            style(idx).dim(),
            severity_icon,
            style(&finding.title).bold()
        );
        println!("     {} {}{}", style("└─").dim(), style(&file).dim(), style(&line).dim());
    }

    // Show pagination info
    if per_page > 0 && total_pages > 1 {
        println!();
        println!(
            "{}Showing page {} of {} ({} per page, {} total)",
            style("📑 ").bold(),
            style(current_page).cyan(),
            style(total_pages).cyan(),
            style(per_page).dim(),
            style(total_findings).cyan(),
        );
        if current_page < total_pages {
            println!(
                "   Use {} to see more",
                style(format!("--page {}", current_page + 1)).yellow()
            );
        }
    }

    println!();
    println!("{}", style("💡 Tips").bold());
    println!("   • Run {} for details on a specific finding", style("repotoire findings <n>").cyan());
    println!("   • Run {} for AI-assisted fixes", style("repotoire fix <n>").cyan());
    println!("   • Run {} for JSON output", style("repotoire findings --json").cyan());

    Ok(())
}

fn print_finding_detail(finding: &Finding, index: usize) {
    let severity_str = match finding.severity {
        Severity::Critical => style("CRITICAL").red().bold(),
        Severity::High => style("HIGH").yellow().bold(),
        Severity::Medium => style("MEDIUM").cyan(),
        Severity::Low => style("LOW").dim(),
        Severity::Info => style("INFO").dim(),
    };

    println!();
    println!("{} Finding #{}", style("📋").bold(), index);
    println!();
    println!("   {} {}", style("Title:").bold(), finding.title);
    println!("   {} {}", style("Severity:").bold(), severity_str);
    println!("   {} {}", style("Detector:").bold(), finding.detector);
    
    if let Some(cat) = &finding.category {
        println!("   {} {}", style("Category:").bold(), cat);
    }
    
    if let Some(cwe) = &finding.cwe_id {
        println!("   {} {}", style("CWE:").bold(), cwe);
    }

    println!();
    println!("{}", style("📁 Affected Files").bold());
    for file in &finding.affected_files {
        let line_info = match (finding.line_start, finding.line_end) {
            (Some(start), Some(end)) if start != end => format!(" (lines {}-{})", start, end),
            (Some(start), _) => format!(" (line {})", start),
            _ => String::new(),
        };
        println!("   • {}{}", file.display(), style(&line_info).dim());
    }

    println!();
    println!("{}", style("📝 Description").bold());
    for line in finding.description.lines() {
        println!("   {}", line);
    }

    if let Some(fix) = &finding.suggested_fix {
        println!();
        println!("{}", style("🔧 Suggested Fix").bold());
        for line in fix.lines() {
            println!("   {}", line);
        }
    }

    if let Some(why) = &finding.why_it_matters {
        println!();
        println!("{}", style("❓ Why It Matters").bold());
        for line in why.lines() {
            println!("   {}", line);
        }
    }

    if let Some(effort) = &finding.estimated_effort {
        println!();
        println!("   {} {}", style("⏱️  Estimated Effort:").bold(), effort);
    }

    println!();
    println!("{}", style("💡 Next Steps").bold());
    println!("   • Run {} for AI-assisted fix", style(format!("repotoire fix {}", index)).cyan());
}
