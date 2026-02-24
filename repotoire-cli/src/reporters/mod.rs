//! Output reporters for Repotoire analysis results
//!
//! Supports multiple output formats:
//! - `text` - Terminal output with colors and emoji
//! - `json` - Machine-readable JSON
//! - `sarif` - SARIF 2.1.0 for GitHub Code Scanning / VS Code
//! - `html` - Standalone HTML report with graphs
//! - `markdown` - GitHub-flavored Markdown

mod html;
mod json;
mod markdown;
mod sarif;
mod text;

use crate::models::HealthReport;
use anyhow::{anyhow, Result};
use std::str::FromStr;

/// Supported output formats
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OutputFormat {
    Text,
    Json,
    Sarif,
    Html,
    Markdown,
}

impl FromStr for OutputFormat {
    type Err = anyhow::Error;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.to_lowercase().as_str() {
            "text" | "txt" | "terminal" => Ok(OutputFormat::Text),
            "json" => Ok(OutputFormat::Json),
            "sarif" => Ok(OutputFormat::Sarif),
            "html" => Ok(OutputFormat::Html),
            "markdown" | "md" => Ok(OutputFormat::Markdown),
            _ => Err(anyhow!(
                "Unknown format '{}'. Valid formats: text, json, sarif, html, markdown",
                s
            )),
        }
    }
}

impl std::fmt::Display for OutputFormat {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            OutputFormat::Text => write!(f, "text"),
            OutputFormat::Json => write!(f, "json"),
            OutputFormat::Sarif => write!(f, "sarif"),
            OutputFormat::Html => write!(f, "html"),
            OutputFormat::Markdown => write!(f, "markdown"),
        }
    }
}

/// Render a health report in the specified format
pub fn report(report: &HealthReport, format: &str) -> Result<String> {
    let fmt = OutputFormat::from_str(format)?;
    report_with_format(report, fmt)
}

/// Render a health report using an OutputFormat enum
pub fn report_with_format(report: &HealthReport, format: OutputFormat) -> Result<String> {
    match format {
        OutputFormat::Text => text::render(report),
        OutputFormat::Json => json::render(report),
        OutputFormat::Sarif => sarif::render(report),
        OutputFormat::Html => html::render(report),
        OutputFormat::Markdown => markdown::render(report),
    }
}

/// Get the recommended file extension for a format
#[allow(dead_code)] // Public API helper
pub fn file_extension(format: OutputFormat) -> &'static str {
    match format {
        OutputFormat::Text => "txt",
        OutputFormat::Json => "json",
        OutputFormat::Sarif => "sarif.json",
        OutputFormat::Html => "html",
        OutputFormat::Markdown => "md",
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    /// Create a minimal HealthReport for testing
    pub(crate) fn test_report() -> HealthReport {
        use crate::models::{Finding, FindingsSummary, Severity};

        let findings = vec![Finding {
            id: "f1".into(),
            detector: "TestDetector".into(),
            severity: Severity::High,
            title: "Test finding".into(),
            description: "A test issue".into(),
            affected_files: vec!["src/main.rs".into()],
            line_start: Some(10),
            suggested_fix: Some("Fix it".into()),
            ..Default::default()
        }];

        HealthReport {
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
        }
    }

    #[test]
    fn test_format_parsing() {
        assert_eq!(OutputFormat::from_str("text").unwrap(), OutputFormat::Text);
        assert_eq!(OutputFormat::from_str("JSON").unwrap(), OutputFormat::Json);
        assert_eq!(
            OutputFormat::from_str("sarif").unwrap(),
            OutputFormat::Sarif
        );
        assert_eq!(OutputFormat::from_str("html").unwrap(), OutputFormat::Html);
        assert_eq!(
            OutputFormat::from_str("md").unwrap(),
            OutputFormat::Markdown
        );
        assert!(OutputFormat::from_str("invalid").is_err());
    }
}
