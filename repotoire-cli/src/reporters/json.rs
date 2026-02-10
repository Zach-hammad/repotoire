//! JSON reporter
//!
//! Outputs the full HealthReport as pretty-printed JSON.
//! Useful for machine consumption, piping to jq, or further processing.

use crate::models::HealthReport;
use anyhow::Result;

/// Render report as JSON
pub fn render(report: &HealthReport) -> Result<String> {
    Ok(serde_json::to_string_pretty(report)?)
}

/// Render report as compact JSON (single line)
pub fn render_compact(report: &HealthReport) -> Result<String> {
    Ok(serde_json::to_string(report)?)
}
