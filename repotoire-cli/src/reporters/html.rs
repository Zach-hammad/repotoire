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

fn render_header(report: &HealthReport) -> String {
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

    format!(
        r#"<div class="finding-card">
        <div class="finding-header">
            <span class="severity-badge {}">{}</span>
            <div class="finding-title">{}{}</div>
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
        location,
        detector,
        html_escape(&finding.description),
        files_html,
        fix_html
    )
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
    body { padding: 0; background: white; }
    .container { box-shadow: none; }
    .finding-card { page-break-inside: avoid; }
}
"#;
