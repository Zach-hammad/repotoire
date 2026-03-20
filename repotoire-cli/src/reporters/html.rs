//! HTML reporter with embedded styles and charts
//!
//! Generates a standalone HTML report that can be viewed in any browser.
//! Includes:
//! - Overall grade and score visualization
//! - Category score progress bars
//! - Findings grouped by severity with syntax-highlighted code
//! - Responsive design for mobile and desktop

use crate::models::{Finding, HealthReport, Severity};
use anyhow::Result;
use chrono::Local;
use std::collections::HashMap;

use super::report_context::ReportContext;

/// Render report as standalone HTML
pub fn render(report: &HealthReport) -> Result<String> {
    let mut html = String::new();

    // DOCTYPE and head
    html.push_str(&render_head(report));

    // Body
    html.push_str("<body>\n<div class=\"container\">\n");

    // Header
    html.push_str(&render_header(report));

    // Content
    html.push_str("<div class=\"content\">\n");

    // Grade section
    html.push_str(&render_grade_section(report));

    // Category scores
    html.push_str(&render_category_scores(report));

    // Key metrics
    html.push_str(&render_metrics(report));

    // Findings summary
    html.push_str(&render_findings_summary(report));

    // Detailed findings
    html.push_str(&render_findings(report));

    html.push_str("</div>\n"); // content

    // Footer
    html.push_str(&render_footer());

    html.push_str("</div>\n</body>\n</html>");

    Ok(html)
}

/// Render report as standalone HTML with full context (graphs, narrative, snippets).
pub fn render_with_context(ctx: &ReportContext) -> Result<String> {
    let report = &ctx.health;
    let mut html = String::new();

    html.push_str(&render_head(report));
    html.push_str("<body>\n<div class=\"container\">\n");
    html.push_str(&render_header(report));
    html.push_str("<div class=\"content\">\n");

    // Narrative story section
    let narrative = super::narrative::generate_narrative(ctx);
    html.push_str(&format!(
        "<div class=\"card narrative\">\n<p style=\"font-size: 1.1rem; line-height: 1.8; color: #334155;\">{}</p>\n</div>\n",
        html_escape(&narrative)
    ));

    // Existing sections
    html.push_str(&render_grade_section(report));
    html.push_str(&render_category_scores(report));
    html.push_str(&render_metrics(report));

    // Architecture map (if graph data available)
    if let Some(ref gd) = ctx.graph_data {
        let arch_svg = super::svg::architecture::render_architecture_map(
            &gd.modules, &gd.module_edges, &gd.communities,
        );
        html.push_str(&format!(
            "<div class=\"card\">\n<h2>Architecture Map</h2>\n<p style=\"color: #64748b; margin-bottom: 1rem;\">Module dependencies colored by health score. Red edges indicate circular dependencies.</p>\n{}\n</div>\n",
            arch_svg
        ));
    }

    // Hotspot treemap (if graph data available)
    if let Some(ref gd) = ctx.graph_data {
        if !gd.modules.is_empty() {
            let treemap_items: Vec<super::svg::treemap::TreemapItem> = gd.modules.iter()
                .filter(|m| m.loc > 0)
                .map(|m| super::svg::treemap::TreemapItem {
                    label: m.path.clone(),
                    size: m.loc as f64,
                    color_value: (1.0 - m.health_score / 100.0).clamp(0.0, 1.0),
                })
                .collect();
            let treemap_svg = super::svg::treemap::render_treemap(&treemap_items, 800.0, 400.0);
            html.push_str(&format!(
                "<div class=\"card\">\n<h2>Hotspot Treemap</h2>\n<p style=\"color: #64748b; margin-bottom: 1rem;\">Rectangle size = lines of code. Color = finding density (green = healthy, red = hotspot).</p>\n{}\n</div>\n",
                treemap_svg
            ));
        }
    }

    // Bus factor (if git data available)
    if let Some(ref git) = ctx.git_data {
        if !git.bus_factor_files.is_empty() {
            // Aggregate bus factor by directory
            let mut dir_risk: HashMap<String, usize> = HashMap::new();
            for (path, _bf) in &git.bus_factor_files {
                let dir = std::path::Path::new(path)
                    .parent()
                    .and_then(|p| p.to_str())
                    .unwrap_or(".")
                    .to_string();
                *dir_risk.entry(dir).or_default() += 1;
            }
            let mut bar_items: Vec<super::svg::bar_chart::BarItem> = dir_risk.into_iter()
                .map(|(dir, risky)| {
                    let value = (risky as f64 / ctx.health.total_files.max(1) as f64).min(1.0);
                    let color = if value > 0.6 { "#ef4444".to_string() }
                        else if value > 0.3 { "#f97316".to_string() }
                        else { "#22c55e".to_string() };
                    super::svg::bar_chart::BarItem { label: dir, value, color }
                })
                .collect();
            bar_items.sort_by(|a, b| b.value.partial_cmp(&a.value).unwrap_or(std::cmp::Ordering::Equal));
            bar_items.truncate(10);

            if !bar_items.is_empty() {
                let bar_svg = super::svg::bar_chart::render_bar_chart(&bar_items, "Bus Factor Risk by Directory", 700.0, 0.0);
                html.push_str(&format!(
                    "<div class=\"card\">\n<h2>Bus Factor</h2>\n<p style=\"color: #64748b; margin-bottom: 1rem;\">Directories with files that have only 1-2 contributors.</p>\n{}\n</div>\n",
                    bar_svg
                ));
            }
        }
    }

    // Findings summary
    html.push_str(&render_findings_summary(report));

    // Enhanced findings with code snippets
    html.push_str(&render_findings_with_snippets(report, &ctx.source_snippets));

    // README badge
    let badge_url = format!(
        "https://img.shields.io/badge/repotoire-{}%20({:.0}%2F100)-{}",
        report.grade, report.overall_score,
        match report.grade.chars().next().unwrap_or('F') {
            'A' => "10b981", 'B' => "22c55e", 'C' => "eab308", 'D' => "f97316", _ => "ef4444",
        }
    );
    let badge_md = format!("[![Repotoire Grade]({})](https://repotoire.dev)", badge_url);
    html.push_str(&format!(
        "<div class=\"card badge-section\">\n<h2>Add to your README</h2>\n<code id=\"badge-code\" style=\"display:block; background: #f1f5f9; padding: 1rem; border-radius: 6px; font-size: 0.85rem; word-break: break-all;\">{}</code>\n<button onclick=\"navigator.clipboard.writeText(document.getElementById('badge-code').textContent)\" style=\"margin-top: 0.5rem; padding: 0.5rem 1rem; background: #6366f1; color: white; border: none; border-radius: 6px; cursor: pointer;\">Copy</button>\n</div>\n",
        html_escape(&badge_md)
    ));

    html.push_str("</div>\n"); // content
    html.push_str(&render_footer());
    html.push_str("</div>\n</body>\n</html>");

    Ok(html)
}

fn render_head(report: &HealthReport) -> String {
    format!(
        r#"<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Repotoire Report - Grade {}</title>
    <style>
{CSS}
    </style>
</head>
"#,
        report.grade
    )
}

fn render_header(_report: &HealthReport) -> String {
    let timestamp = Local::now().format("%Y-%m-%d %H:%M:%S");
    format!(
        r#"<div class="header">
    <h1>🎼 Repotoire Code Health Report</h1>
    <p class="timestamp">Generated {}</p>
</div>
"#,
        timestamp
    )
}

fn render_grade_section(report: &HealthReport) -> String {
    let description = match report.grade.as_str() {
        "A" => "Excellent - Code is well-structured and maintainable",
        "B" => "Good - Minor improvements recommended",
        "C" => "Fair - Several issues should be addressed",
        "D" => "Poor - Significant refactoring needed",
        "F" => "Critical - Major technical debt present",
        _ => "",
    };

    format!(
        r#"<div class="grade-section">
    <div class="grade-badge grade-{}">{}</div>
    <div class="score">Overall Score: {:.1}/100</div>
    <p class="grade-description">{}</p>
</div>
"#,
        report.grade, report.grade, report.overall_score, description
    )
}

fn render_category_scores(report: &HealthReport) -> String {
    let arch_score = report.architecture_score.unwrap_or(0.0);

    format!(
        r#"<div class="section">
    <h2 class="section-title">📊 Category Scores</h2>
    <div class="metrics-grid">
        <div class="metric-card">
            <h3>Graph Structure (40%)</h3>
            <div class="metric-value">{:.1}</div>
            <div class="metric-bar">
                <div class="metric-bar-fill {}" style="width: {}%"></div>
            </div>
        </div>
        <div class="metric-card">
            <h3>Code Quality (30%)</h3>
            <div class="metric-value">{:.1}</div>
            <div class="metric-bar">
                <div class="metric-bar-fill {}" style="width: {}%"></div>
            </div>
        </div>
        <div class="metric-card">
            <h3>Architecture Health (30%)</h3>
            <div class="metric-value">{:.1}</div>
            <div class="metric-bar">
                <div class="metric-bar-fill {}" style="width: {}%"></div>
            </div>
        </div>
    </div>
</div>
"#,
        report.structure_score,
        bar_class(report.structure_score),
        report.structure_score,
        report.quality_score,
        bar_class(report.quality_score),
        report.quality_score,
        arch_score,
        bar_class(arch_score),
        arch_score
    )
}

fn render_metrics(report: &HealthReport) -> String {
    format!(
        r#"<div class="section">
    <h2 class="section-title">📈 Key Metrics</h2>
    <div class="stats-grid">
        <div class="stat-item">
            <div class="stat-value">{}</div>
            <div class="stat-label">📁 Files</div>
        </div>
        <div class="stat-item">
            <div class="stat-value">{}</div>
            <div class="stat-label">⚙️ Functions</div>
        </div>
        <div class="stat-item">
            <div class="stat-value">{}</div>
            <div class="stat-label">🏛️ Classes</div>
        </div>
        <div class="stat-item">
            <div class="stat-value">{}</div>
            <div class="stat-label">🔍 Issues</div>
        </div>
    </div>
</div>
"#,
        report.total_files,
        report.total_functions,
        report.total_classes,
        report.findings_summary.total
    )
}

fn render_findings_summary(report: &HealthReport) -> String {
    let fs = &report.findings_summary;
    format!(
        r#"<div class="section">
    <h2 class="section-title">🎯 Findings Summary</h2>
    <div class="severity-summary">
        <div class="severity-item severity-critical">
            <span class="severity-icon">🔴</span>
            <span class="severity-label">Critical</span>
            <span class="severity-count">{}</span>
        </div>
        <div class="severity-item severity-high">
            <span class="severity-icon">🟠</span>
            <span class="severity-label">High</span>
            <span class="severity-count">{}</span>
        </div>
        <div class="severity-item severity-medium">
            <span class="severity-icon">🟡</span>
            <span class="severity-label">Medium</span>
            <span class="severity-count">{}</span>
        </div>
        <div class="severity-item severity-low">
            <span class="severity-icon">🔵</span>
            <span class="severity-label">Low</span>
            <span class="severity-count">{}</span>
        </div>
        <div class="severity-item severity-info">
            <span class="severity-icon">ℹ️</span>
            <span class="severity-label">Info</span>
            <span class="severity-count">{}</span>
        </div>
    </div>
</div>
"#,
        fs.critical, fs.high, fs.medium, fs.low, fs.info
    )
}

fn render_findings(report: &HealthReport) -> String {
    if report.findings.is_empty() {
        return r#"<div class="section">
    <h2 class="section-title">✅ No Issues Found</h2>
    <p>Great job! Your codebase has no detected issues.</p>
</div>
"#
        .to_string();
    }

    let mut html = format!(
        r#"<div class="section">
    <h2 class="section-title">🔍 Detailed Findings ({} total)</h2>
    <div class="findings-list">
"#,
        report.findings.len()
    );

    for finding in &report.findings {
        html.push_str(&render_finding(finding));
    }

    html.push_str("    </div>\n</div>\n");
    html
}

fn render_finding(finding: &Finding) -> String {
    let sev_class = match finding.severity {
        Severity::Critical => "severity-critical",
        Severity::High => "severity-high",
        Severity::Medium => "severity-medium",
        Severity::Low => "severity-low",
        Severity::Info => "severity-info",
    };

    let sev_label = match finding.severity {
        Severity::Critical => "🔴 Critical",
        Severity::High => "🟠 High",
        Severity::Medium => "🟡 Medium",
        Severity::Low => "🔵 Low",
        Severity::Info => "ℹ️ Info",
    };

    let detector = finding.detector.replace("Detector", "");

    let files_html = if finding.affected_files.is_empty() {
        String::new()
    } else {
        let files: Vec<String> = finding
            .affected_files
            .iter()
            .take(5)
            .map(|f| {
                format!(
                    "<div class=\"file-item\">{}</div>",
                    html_escape(&f.display().to_string())
                )
            })
            .collect();
        let more = if finding.affected_files.len() > 5 {
            format!(
                "<div class=\"file-item\">...and {} more files</div>",
                finding.affected_files.len() - 5
            )
        } else {
            String::new()
        };
        format!(
            r#"<div class="affected-files">
                <div class="affected-files-label">📂 Affected Files</div>
                <div class="file-list">{}{}</div>
            </div>"#,
            files.join("\n"),
            more
        )
    };

    let fix_html = finding
        .suggested_fix
        .as_ref()
        .map(|fix| {
            format!(
                r#"<div class="suggested-fix">
                <div class="suggested-fix-label">💡 Suggested Fix</div>
                <div class="suggested-fix-text">{}</div>
            </div>"#,
                html_escape(fix)
            )
        })
        .unwrap_or_default();

    let location = if let Some(line) = finding.line_start {
        if let Some(file) = finding.affected_files.first() {
            format!(" ({}:{})", file.display(), line)
        } else {
            format!(" (line {})", line)
        }
    } else {
        String::new()
    };

    // Confidence badge
    let confidence_html = if let Some(conf) = finding.confidence {
        let pct = (conf * 100.0) as u32;
        let conf_class = if conf >= 0.9 {
            "conf-high"
        } else if conf >= 0.7 {
            "conf-medium"
        } else {
            "conf-low"
        };
        let signals = finding
            .threshold_metadata
            .get("confidence_signals")
            .cloned()
            .unwrap_or_default();
        let title_attr = if signals.is_empty() {
            format!("Confidence: {}%", pct)
        } else {
            format!("Confidence: {}% — {}", pct, signals)
        };
        format!(
            r#"<span class="confidence-badge {}" title="{}">{}&thinsp;%</span>"#,
            conf_class,
            html_escape(&title_attr),
            pct
        )
    } else {
        String::new()
    };

    format!(
        r#"<div class="finding-card">
        <div class="finding-header">
            <span class="severity-badge {}">{}</span>
            <div class="finding-title">{}{}</div>
            {}
            <span class="detector-badge">{}</span>
        </div>
        <div class="finding-body">
            <div class="finding-description">{}</div>
            {}
            {}
        </div>
    </div>
"#,
        sev_class,
        sev_label,
        html_escape(&finding.title),
        html_escape(&location),
        confidence_html,
        html_escape(&detector),
        html_escape(&finding.description),
        files_html,
        fix_html
    )
}

/// Render findings with inline code snippets where available.
fn render_findings_with_snippets(
    report: &HealthReport,
    snippets: &[super::report_context::FindingSnippet],
) -> String {
    if report.findings.is_empty() {
        return r#"<div class="section">
    <h2 class="section-title">No Issues Found</h2>
    <p>Great job! Your codebase has no detected issues.</p>
</div>
"#
        .to_string();
    }

    // Build a lookup from finding_id to snippet
    let snippet_map: HashMap<&str, &super::report_context::FindingSnippet> = snippets
        .iter()
        .map(|s| (s.finding_id.as_str(), s))
        .collect();

    let mut html = format!(
        r#"<div class="section">
    <h2 class="section-title">Detailed Findings ({} total)</h2>
    <div class="findings-list">
"#,
        report.findings.len()
    );

    for finding in &report.findings {
        html.push_str(&render_finding(finding));

        // Inject code snippet if available
        if let Some(snippet) = snippet_map.get(finding.id.as_str()) {
            if !snippet.code.is_empty() {
                html.push_str(&format!(
                    "<pre style=\"background: #1e293b; color: #e2e8f0; padding: 1rem; border-radius: 6px; overflow-x: auto; font-size: 0.85rem; line-height: 1.5; margin: 0 1rem 1rem 1rem;\"><code>{}</code></pre>\n",
                    html_escape(&snippet.code)
                ));
            }
        }
    }

    html.push_str("    </div>\n</div>\n");
    html
}

fn render_footer() -> String {
    r#"<div class="footer">
    <p>Generated by <a href="https://repotoire.com">Repotoire</a> - Graph-Powered Code Health Platform</p>
</div>
"#
    .to_string()
}

fn bar_class(score: f64) -> &'static str {
    if score >= 80.0 {
        "bar-good"
    } else if score >= 60.0 {
        "bar-moderate"
    } else {
        "bar-poor"
    }
}

fn html_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}

// Embedded CSS
const CSS: &str = r#"
:root {
    --primary-color: #6366f1;
    --background-color: #f8fafc;
    --text-color: #1e293b;
    --card-background: white;
    --border-color: #e2e8f0;
}

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    line-height: 1.6;
    color: var(--text-color);
    background: var(--background-color);
    padding: 2rem;
}

.container {
    max-width: 1200px;
    margin: 0 auto;
    background: var(--card-background);
    border-radius: 12px;
    box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
    overflow: hidden;
}

.header {
    background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
    color: white;
    padding: 3rem 2rem;
    text-align: center;
}

.header h1 { font-size: 2.5rem; margin-bottom: 0.5rem; }
.header .timestamp { opacity: 0.9; font-size: 0.95rem; }

.content { padding: 2rem; }

.grade-section {
    text-align: center;
    padding: 2rem;
    background: #f1f5f9;
    border-radius: 8px;
    margin-bottom: 2rem;
}

.grade-badge {
    display: inline-block;
    font-size: 4rem;
    font-weight: bold;
    width: 120px;
    height: 120px;
    line-height: 120px;
    border-radius: 50%;
    margin-bottom: 1rem;
    color: white;
}

.grade-A { background: #10b981; }
.grade-B { background: #22c55e; }
.grade-C { background: #eab308; }
.grade-D { background: #f97316; }
.grade-F { background: #ef4444; }

.score { font-size: 1.5rem; color: #64748b; margin-bottom: 0.5rem; }
.grade-description { color: #64748b; font-style: italic; }

.section { margin-bottom: 2rem; }
.section-title {
    font-size: 1.5rem;
    margin-bottom: 1rem;
    padding-bottom: 0.5rem;
    border-bottom: 2px solid var(--border-color);
}

.metrics-grid, .stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 1rem;
}

.metric-card, .stat-item {
    background: var(--card-background);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    padding: 1.5rem;
}

.metric-card h3 {
    font-size: 0.875rem;
    color: #64748b;
    margin-bottom: 0.5rem;
    text-transform: uppercase;
}

.metric-value, .stat-value {
    font-size: 2rem;
    font-weight: bold;
    margin-bottom: 0.5rem;
}

.stat-item { text-align: center; }
.stat-label { font-size: 0.875rem; color: #64748b; }

.metric-bar {
    height: 8px;
    background: #e2e8f0;
    border-radius: 4px;
    overflow: hidden;
}

.metric-bar-fill { height: 100%; border-radius: 4px; }
.bar-good { background: #10b981; }
.bar-moderate { background: #f59e0b; }
.bar-poor { background: #ef4444; }

.severity-summary {
    display: flex;
    flex-wrap: wrap;
    gap: 1rem;
    justify-content: center;
}

.severity-item {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.75rem 1.5rem;
    border-radius: 8px;
    background: #f8fafc;
    border: 1px solid var(--border-color);
}

.severity-count { font-weight: bold; font-size: 1.25rem; }

.findings-list { display: flex; flex-direction: column; gap: 1rem; }

.finding-card {
    border: 1px solid var(--border-color);
    border-radius: 8px;
    overflow: hidden;
}

.finding-header {
    padding: 1rem;
    background: #f8fafc;
    display: flex;
    align-items: center;
    gap: 1rem;
    flex-wrap: wrap;
}

.severity-badge {
    padding: 0.25rem 0.75rem;
    border-radius: 6px;
    font-size: 0.875rem;
    font-weight: 600;
    color: white;
    white-space: nowrap;
}

.severity-critical { background: #dc2626; }
.severity-high { background: #ea580c; }
.severity-medium { background: #ca8a04; }
.severity-low { background: #2563eb; }
.severity-info { background: #64748b; }

.finding-title { flex: 1; font-weight: 600; }

.confidence-badge {
    padding: 0.25rem 0.75rem;
    border-radius: 6px;
    font-size: 0.875rem;
    font-weight: 600;
    white-space: nowrap;
    cursor: help;
}

.conf-high { background: #dcfce7; color: #166534; }
.conf-medium { background: #fef9c3; color: #854d0e; }
.conf-low { background: #fee2e2; color: #991b1b; }

.detector-badge {
    background: #e0e7ff;
    color: #4f46e5;
    padding: 0.25rem 0.75rem;
    border-radius: 6px;
    font-size: 0.875rem;
}

.finding-body { padding: 1rem; }
.finding-description { color: #64748b; margin-bottom: 1rem; }

.affected-files { margin-bottom: 1rem; }
.affected-files-label {
    font-weight: 600;
    color: #64748b;
    margin-bottom: 0.5rem;
    font-size: 0.875rem;
}

.file-item {
    font-family: monospace;
    font-size: 0.875rem;
    color: #64748b;
    padding: 0.5rem;
    background: #f8fafc;
    border-radius: 4px;
    margin-bottom: 0.25rem;
}

.suggested-fix {
    padding: 1rem;
    background: #ecfdf5;
    border-left: 4px solid #10b981;
    border-radius: 4px;
}

.suggested-fix-label { font-weight: 600; color: #059669; margin-bottom: 0.5rem; }
.suggested-fix-text { color: #065f46; }

.footer {
    text-align: center;
    padding: 2rem;
    color: #64748b;
    border-top: 1px solid var(--border-color);
}

.footer a { color: var(--primary-color); text-decoration: none; }
.footer a:hover { text-decoration: underline; }

@media (max-width: 768px) {
    body { padding: 1rem; }
    .header { padding: 2rem 1rem; }
    .header h1 { font-size: 1.75rem; }
    .grade-badge { width: 80px; height: 80px; line-height: 80px; font-size: 2.5rem; }
}

@media print {
    body { background: white; padding: 0; }
    .card { box-shadow: none; border: 1px solid #ccc; }
    .container { box-shadow: none; }
    .header { background: #6366f1 !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    .finding-card { page-break-inside: avoid; }
    .badge-section { display: none; }
    svg { max-width: 100%; height: auto; }
}

.card {
    background: var(--card-background);
    border-radius: 8px;
    padding: 1.5rem;
    margin-bottom: 2rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.card h2 {
    font-size: 1.5rem;
    margin-bottom: 1rem;
    padding-bottom: 0.5rem;
    border-bottom: 2px solid var(--border-color);
}
"#;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::reporters::tests::test_report;
    use crate::reporters::report_context::*;
    use crate::models::*;

    #[test]
    fn test_html_render_valid() {
        let report = test_report();
        let html_str = render(&report).expect("render HTML");
        assert!(html_str.contains("<!DOCTYPE html>") || html_str.contains("<html"));
        assert!(html_str.contains("</html>"));
    }

    #[test]
    fn test_html_contains_score() {
        let report = test_report();
        let html_str = render(&report).expect("render HTML");
        assert!(html_str.contains("85")); // score
        assert!(html_str.contains("B")); // grade
    }

    #[test]
    fn test_html_empty_findings() {
        let mut report = test_report();
        report.findings.clear();
        report.findings_summary = Default::default();
        let html_str = render(&report).expect("render HTML");
        assert!(html_str.contains("</html>"));
    }

    fn test_ctx() -> ReportContext {
        let findings = vec![Finding {
            id: "f1".into(),
            detector: "test".into(),
            severity: Severity::High,
            title: "Test finding".into(),
            description: "A test".into(),
            affected_files: vec!["src/main.rs".into()],
            line_start: Some(10),
            suggested_fix: Some("Fix it".into()),
            ..Default::default()
        }];
        ReportContext {
            health: HealthReport {
                overall_score: 85.0,
                grade: "B".into(),
                structure_score: 90.0,
                quality_score: 80.0,
                architecture_score: Some(85.0),
                findings_summary: FindingsSummary::from_findings(&findings),
                findings,
                total_files: 100,
                total_functions: 500,
                total_classes: 50,
                total_loc: 10000,
            },
            graph_data: None,
            git_data: None,
            source_snippets: vec![],
            previous_health: None,
            style_profile: None,
        }
    }

    #[test]
    fn test_html_with_context_contains_narrative() {
        let ctx = test_ctx();
        let html = render_with_context(&ctx).unwrap();
        assert!(
            html.contains("LOC") || html.contains("loc") || html.contains("10,000"),
            "should have narrative"
        );
    }

    #[test]
    fn test_html_degrades_without_graph() {
        let ctx = test_ctx();
        let html = render_with_context(&ctx).unwrap();
        assert!(
            !html.contains("Architecture Map"),
            "no arch map without graph data"
        );
        assert!(
            html.contains("Score:") || html.contains("score") || html.contains("85"),
            "basic report still works"
        );
    }

    #[test]
    fn test_html_contains_badge() {
        let ctx = test_ctx();
        let html = render_with_context(&ctx).unwrap();
        assert!(html.contains("shields.io"), "should have badge");
    }

    #[test]
    fn test_html_contains_print_css() {
        let ctx = test_ctx();
        let html = render_with_context(&ctx).unwrap();
        assert!(html.contains("@media print"), "should have print CSS");
    }

    #[test]
    fn test_html_with_snippets() {
        let mut ctx = test_ctx();
        ctx.source_snippets = vec![FindingSnippet {
            finding_id: "f1".into(),
            code: "fn main() {\n    println!(\"hello\");\n}".into(),
            highlight_lines: vec![2],
            language: "rust".into(),
        }];
        let html = render_with_context(&ctx).unwrap();
        assert!(
            html.contains("fn main()"),
            "should contain code snippet"
        );
    }
}
