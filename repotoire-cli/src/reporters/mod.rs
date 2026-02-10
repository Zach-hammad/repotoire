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
mod tests {
    use super::*;

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
