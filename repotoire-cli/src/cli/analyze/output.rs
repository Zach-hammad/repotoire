//! Output and formatting functions for the analyze command
//!
//! This module contains all output-related logic:
//! - Formatting reports (text, JSON, SARIF, etc.)
//! - Filtering and pagination
//! - Caching results
//! - Threshold checks for CI/CD

use crate::models::{Finding, FindingsSummary, HealthReport, Severity};
use crate::reporters;
use anyhow::Result;
use console::style;
use std::path::{Path, PathBuf};
use std::time::Instant;

/// Normalize a path to be relative
fn normalize_path(path: &Path) -> String {
    let s = path.display().to_string();
    if let Some(stripped) = s.strip_prefix("/tmp/") {
        if let Some(pos) = stripped.find('/') {
            return stripped[pos + 1..].to_string();
        }
    }
    if let Ok(home) = std::env::var("HOME") {
        if let Some(stripped) = s.strip_prefix(&home) {
            return stripped.trim_start_matches('/').to_string();
        }
    }
    s
}

/// Filter findings by severity and limit
pub(super) fn filter_findings(findings: &mut Vec<Finding>, severity: &Option<String>, top: Option<usize>) {
    if let Some(min_severity) = severity {
        let min = parse_severity(min_severity);
        findings.retain(|f| f.severity >= min);
    }

    findings.sort_by(|a, b| b.severity.cmp(&a.severity));

    if let Some(n) = top {
        findings.truncate(n);
    }
}

/// Paginate findings
pub(super) fn paginate_findings(
    mut findings: Vec<Finding>,
    page: usize,
    per_page: usize,
) -> (Vec<Finding>, Option<(usize, usize, usize, usize)>) {
    // Sort for deterministic output: severity (desc), then file, then line (#47)
    findings.sort_by(|a, b| {
        (b.severity as u8)
            .cmp(&(a.severity as u8))
            .then_with(|| {
                let a_file = a.affected_files.first().map(|f| f.to_string_lossy().to_string()).unwrap_or_default();
                let b_file = b.affected_files.first().map(|f| f.to_string_lossy().to_string()).unwrap_or_default();
                a_file.cmp(&b_file)
            })
            .then_with(|| a.line_start.cmp(&b.line_start))
            .then_with(|| a.detector.cmp(&b.detector))
    });

    let displayed_findings = findings.len();

    if per_page > 0 {
        let total_pages = displayed_findings.div_ceil(per_page);
        let page = page.max(1).min(total_pages.max(1));
        let start = (page - 1) * per_page;
        let end = (start + per_page).min(displayed_findings);
        let paginated: Vec<_> = findings[start..end].to_vec();
        (
            paginated,
            Some((page, total_pages, per_page, displayed_findings)),
        )
    } else {
        (findings, None)
    }
}

/// Format and output results
pub(super) fn format_and_output(
    report: &HealthReport,
    all_findings: &[Finding],
    format: &str,
    output_path: Option<&Path>,
    repotoire_dir: &Path,
    pagination_info: Option<(usize, usize, usize, usize)>,
    _displayed_findings: usize,
    no_emoji: bool,
) -> Result<()> {
    // For file-based export formats (SARIF, HTML, Markdown), use ALL findings
    // to avoid truncating to page size. Pagination is for terminal display only.
    // Use all findings for file-based exports; JSON only when writing to file (#58)
    let use_all = matches!(format, "sarif" | "html" | "markdown" | "md")
        || (format == "json" && output_path.is_some());
    let report_for_output = if use_all && !all_findings.is_empty() {
        let mut full_report = report.clone();
        full_report.findings = all_findings.to_vec();
        full_report.findings_summary = FindingsSummary::from_findings(all_findings);
        full_report
    } else {
        report.clone()
    };

    let output = reporters::report(&report_for_output, format)?;

    // Only write to file if --output was explicitly provided (#59)
    let write_to_file = output_path.is_some();

    if write_to_file {
        let out_path = if let Some(p) = output_path {
            p.to_path_buf()
        } else {
            let ext = match format {
                "html" => "html",
                "sarif" => "sarif.json",
                "markdown" | "md" => "md",
                "json" => "json",
                _ => "txt",
            };
            repotoire_dir.join(format!("report.{}", ext))
        };

        std::fs::write(&out_path, &output)?;
        let file_icon = if no_emoji { "" } else { "📄 " };
        // Use stderr for machine-readable formats to keep stdout clean
        eprintln!(
            "\n{}Report written to: {}",
            style(file_icon).bold(),
            style(out_path.display()).cyan()
        );
    } else {
        // For machine-readable formats, skip leading newline to keep stdout clean
        if format != "json" && format != "sarif" {
            println!();
        }
        println!("{}", output);
    }

    // Cache results
    cache_results(repotoire_dir, report, all_findings)?;

    // Show pagination info (suppress for machine-readable formats)
    let quiet_mode = format == "json" || format == "sarif";
    if !quiet_mode {
        if let Some((current_page, total_pages, per_page, total)) = pagination_info {
            let page_icon = if no_emoji { "" } else { "📑 " };
            println!(
                "\n{}Showing page {} of {} ({} findings per page, {} total)",
                style(page_icon).bold(),
                style(current_page).cyan(),
                style(total_pages).cyan(),
                style(per_page).dim(),
                style(total).cyan(),
            );
            if current_page < total_pages {
                println!(
                    "   Use {} to see more",
                    style(format!("--page {}", current_page + 1)).yellow()
                );
            }
        }
    }

    Ok(())
}

/// Check if fail threshold is met
pub(super) fn check_fail_threshold(fail_on: &Option<String>, report: &HealthReport) -> Result<()> {
    if let Some(ref threshold) = fail_on {
        let should_fail = match threshold.to_lowercase().as_str() {
            "critical" => report.findings_summary.critical > 0,
            "high" => report.findings_summary.critical > 0 || report.findings_summary.high > 0,
            "medium" => {
                report.findings_summary.critical > 0
                    || report.findings_summary.high > 0
                    || report.findings_summary.medium > 0
            }
            "low" => {
                report.findings_summary.critical > 0
                    || report.findings_summary.high > 0
                    || report.findings_summary.medium > 0
                    || report.findings_summary.low > 0
            }
            _ => false,
        };
        if should_fail {
            eprintln!("Failing due to --fail-on={} threshold", threshold);
            std::process::exit(1);
        }
    }
    Ok(())
}

/// Load post-processed findings from last_findings.json cache
/// Returns None if the cache file doesn't exist or can't be parsed
pub(super) fn load_cached_findings(repotoire_dir: &Path) -> Option<Vec<Finding>> {
    let findings_cache = repotoire_dir.join("last_findings.json");
    let data = std::fs::read_to_string(&findings_cache).ok()?;
    let json: serde_json::Value = serde_json::from_str(&data).ok()?;
    let findings_arr = json.get("findings")?.as_array()?;
    
    let mut findings = Vec::new();
    for f in findings_arr {
        let severity = match f.get("severity")?.as_str()? {
            "critical" => Severity::Critical,
            "high" => Severity::High,
            "medium" => Severity::Medium,
            "low" => Severity::Low,
            _ => Severity::Info,
        };
        
        let affected_files: Vec<PathBuf> = f.get("affected_files")
            .and_then(|v| v.as_array())
            .map(|arr| arr.iter().filter_map(|v| v.as_str().map(PathBuf::from)).collect())
            .unwrap_or_default();
        
        findings.push(Finding {
            id: f.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            detector: f.get("detector").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            title: f.get("title").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            description: f.get("description").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            severity,
            affected_files,
            line_start: f.get("line_start").and_then(|v| v.as_u64()).map(|v| v as u32),
            line_end: f.get("line_end").and_then(|v| v.as_u64()).map(|v| v as u32),
            suggested_fix: f.get("suggested_fix").and_then(|v| v.as_str()).map(|s| s.to_string()),
            category: f.get("category").and_then(|v| v.as_str()).map(|s| s.to_string()),
            cwe_id: f.get("cwe_id").and_then(|v| v.as_str()).map(|s| s.to_string()),
            why_it_matters: f.get("why_it_matters").and_then(|v| v.as_str()).map(|s| s.to_string()),
            confidence: f.get("confidence").and_then(|v| v.as_f64()),
            ..Default::default()
        });
    }
    
    tracing::debug!("Loaded {} post-processed findings from cache", findings.len());
    Some(findings)
}

/// Cache analysis results for other commands
pub(super) fn cache_results(
    repotoire_dir: &Path,
    report: &HealthReport,
    all_findings: &[Finding],
) -> Result<()> {
    use std::fs;

    let health_cache = repotoire_dir.join("last_health.json");
    let health_json = serde_json::json!({
        "health_score": report.overall_score,
        "structure_score": report.structure_score,
        "quality_score": report.quality_score,
        "architecture_score": report.architecture_score,
        "grade": report.grade,
        "total_files": report.total_files,
        "total_functions": report.total_functions,
        "total_classes": report.total_classes,
    });
    fs::write(&health_cache, serde_json::to_string_pretty(&health_json)?)?;

    let findings_cache = repotoire_dir.join("last_findings.json");
    let findings_json = serde_json::json!({
        "findings": all_findings.iter().map(|f| {
            serde_json::json!({
                "id": f.id,
                "detector": f.detector,
                "title": f.title,
                "description": f.description,
                "severity": f.severity.to_string(),
                "affected_files": f.affected_files.iter().map(|p| normalize_path(p)).collect::<Vec<_>>(),
                "line_start": f.line_start,
                "line_end": f.line_end,
                "suggested_fix": f.suggested_fix,
                "category": f.category,
                "cwe_id": f.cwe_id,
                "why_it_matters": f.why_it_matters,
                "confidence": f.confidence,
            })
        }).collect::<Vec<_>>()
    });
    fs::write(
        &findings_cache,
        serde_json::to_string_pretty(&findings_json)?,
    )?;

    tracing::debug!("Cached analysis results to {}", repotoire_dir.display());
    Ok(())
}

/// Output results from fully cached data (fast path)
pub(super) fn output_cached_results(
    no_emoji: bool,
    quiet_mode: bool,
    config_fail_on: &Option<String>,
    mut findings: Vec<Finding>,
    cached_score: &crate::detectors::CachedScoreResult,
    format: &str,
    output_path: Option<&Path>,
    start_time: Instant,
    _explain_score: bool,
    severity: &Option<String>,
    top: Option<usize>,
    page: usize,
    per_page: usize,
    skip_detector: &[String],
    repotoire_dir: &Path,
) -> Result<()> {
    // Apply skip-detector filter
    if !skip_detector.is_empty() {
        let skip_set: std::collections::HashSet<&str> = skip_detector.iter().map(|s| s.as_str()).collect();
        findings.retain(|f| !skip_set.contains(f.detector.as_str()));
    }
    
    // Apply severity and top filters (same as normal path)
    filter_findings(&mut findings, severity, top);
    
    // Paginate
    let (paginated_findings, pagination_info) = paginate_findings(findings.clone(), page, per_page);
    
    let findings_summary = FindingsSummary::from_findings(&paginated_findings);
    
    // Build health report with filtered+paginated findings
    let health_report = HealthReport {
        overall_score: cached_score.score,
        grade: cached_score.grade.clone(),
        structure_score: cached_score.structure_score.unwrap_or(cached_score.score),
        quality_score: cached_score.quality_score.unwrap_or(cached_score.score),
        architecture_score: cached_score.architecture_score.or(Some(cached_score.score)),
        findings: paginated_findings.clone(),
        findings_summary: findings_summary.clone(),
        total_files: cached_score.total_files,
        total_functions: cached_score.total_functions,
        total_classes: cached_score.total_classes,
    };
    
    // Use format_and_output for consistent behavior with normal path
    format_and_output(
        &health_report,
        &findings, // all filtered findings (for JSON "all" output)
        format,
        output_path,
        repotoire_dir,
        pagination_info,
        paginated_findings.len(),
        no_emoji,
    )?;
    
    // Final summary (text only)
    if format != "json" && format != "sarif" {
        let elapsed = start_time.elapsed();
        let done_prefix = if no_emoji { "" } else { "✨ " };
        if !quiet_mode {
            println!(
                "\n{}Analysis complete in {:.2}s (cached)",
                style(done_prefix).bold(),
                elapsed.as_secs_f64()
            );
        }
    }
    
    // CI/CD threshold check (use unfiltered findings for fail-on)
    check_fail_threshold(config_fail_on, &health_report)?;
    
    Ok(())
}

/// Parse a severity string
fn parse_severity(s: &str) -> Severity {
    match s.to_lowercase().as_str() {
        "critical" => Severity::Critical,
        "high" => Severity::High,
        "medium" => Severity::Medium,
        "low" => Severity::Low,
        _ => Severity::Info,
    }
}
