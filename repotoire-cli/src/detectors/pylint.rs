//! Pylint-based code quality detector
//!
//! Uses Pylint for comprehensive Python code quality analysis.
//! Note: Consider using RuffLintDetector for faster analysis (100x faster)
//! as it covers most Pylint rules.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::external_tool::{get_graph_context, run_external_tool};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use serde_json::Value as JsonValue;
use std::path::{Path, PathBuf};
use tracing::{info, warn};
use uuid::Uuid;

/// Pylint code quality detector
pub struct PylintDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
    parallel_jobs: usize,
    enable_only: Vec<String>,
    disable: Vec<String>,
}

impl PylintDetector {
    /// Create a new Pylint detector
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            config: DetectorConfig::default(),
            repository_path: repository_path.into(),
            max_findings: 100,
            parallel_jobs: num_cpus::get(),
            enable_only: Vec::new(),
            disable: Vec::new(),
        }
    }

    /// Set maximum findings
    pub fn with_max_findings(mut self, max: usize) -> Self {
        self.max_findings = max;
        self
    }

    /// Set parallel jobs
    pub fn with_parallel_jobs(mut self, jobs: usize) -> Self {
        self.parallel_jobs = jobs;
        self
    }

    /// Enable only specific checks
    pub fn with_enable_only(mut self, rules: Vec<String>) -> Self {
        self.enable_only = rules;
        self
    }

    /// Disable specific checks
    pub fn with_disable(mut self, rules: Vec<String>) -> Self {
        self.disable = rules;
        self
    }

    /// Run pylint and parse results
    fn run_pylint(&self) -> Vec<JsonValue> {
        let mut cmd = vec![
            "pylint".to_string(),
            "--output-format=json".to_string(),
            "--recursive=y".to_string(),
            format!("-j{}", self.parallel_jobs),
        ];

        if !self.enable_only.is_empty() {
            cmd.push("--disable=all".to_string());
            cmd.push(format!("--enable={}", self.enable_only.join(",")));
        } else if !self.disable.is_empty() {
            cmd.push(format!("--disable={}", self.disable.join(",")));
        }

        cmd.push(self.repository_path.to_string_lossy().to_string());

        let result = run_external_tool(&cmd, "pylint", 300, Some(&self.repository_path), None);

        if result.timed_out {
            warn!("Pylint timed out");
            return Vec::new();
        }

        result.json_array().unwrap_or_default()
    }

    /// Map pylint message type to severity
    fn map_severity(msg_type: &str) -> Severity {
        match msg_type.to_lowercase().as_str() {
            "fatal" => Severity::Critical,
            "error" => Severity::High,
            "warning" => Severity::Medium,
            "refactor" => Severity::Low,
            "convention" => Severity::Low,
            "info" => Severity::Info,
            _ => Severity::Low,
        }
    }

    /// Create finding from pylint result
    fn create_finding(
        &self,
        result: &JsonValue,
        graph: &GraphStore,
    ) -> Option<Finding> {
        let file_path = result.get("path")?.as_str()?;
        let line = result.get("line")?.as_u64()? as u32;
        let column = result.get("column").and_then(|c| c.as_u64()).unwrap_or(0) as u32;
        let message = result.get("message")?.as_str()?;
        let message_id = result.get("message-id").and_then(|m| m.as_str()).unwrap_or("");
        let symbol = result.get("symbol").and_then(|s| s.as_str()).unwrap_or("");
        let msg_type = result.get("type").and_then(|t| t.as_str()).unwrap_or("convention");

        // Convert to relative path
        let rel_path = if Path::new(file_path).is_absolute() {
            Path::new(file_path)
                .strip_prefix(&self.repository_path)
                .map(|p| p.to_string_lossy().to_string())
                .unwrap_or_else(|_| file_path.to_string())
        } else {
            file_path.to_string()
        };

        let ctx = get_graph_context(graph, &rel_path, Some(line));
        let severity = Self::map_severity(msg_type);

        // Build description
        let mut description = format!(
            "{}\n\n\
             **Location**: {}:{}\n\
             **Rule**: {} ({})\n",
            message, rel_path, line, symbol, message_id
        );

        if let Some(loc) = ctx.file_loc {
            description.push_str(&format!("**File Size**: {} LOC\n", loc));
        }

        if ctx.max_complexity() > 0 {
            description.push_str(&format!("**Complexity**: {}\n", ctx.max_complexity()));
        }

        if !ctx.affected_nodes.is_empty() {
            description.push_str(&format!(
                "**Affected**: {}\n",
                ctx.affected_nodes.iter().take(3).cloned().collect::<Vec<_>>().join(", ")
            ));
        }

        Some(Finding {
            id: Uuid::new_v4().to_string(),
            detector: "PylintDetector".to_string(),
            severity,
            title: format!("Code quality: {}", symbol),
            description,
            affected_files: vec![PathBuf::from(&rel_path)],
            line_start: Some(line),
            line_end: Some(line),
            suggested_fix: Some(Self::suggest_fix(symbol, message)),
            estimated_effort: Some("Small (5-15 minutes)".to_string()),
            category: Some(Self::get_category_tag(symbol)),
            cwe_id: None,
            why_it_matters: None,
        })
    }

    fn suggest_fix(symbol: &str, message: &str) -> String {
        match symbol {
            "unused-import" => "Remove the unused import statement".to_string(),
            "unused-variable" => "Remove the unused variable or prefix with underscore".to_string(),
            "too-many-arguments" => "Refactor to use a data class or reduce parameters".to_string(),
            "too-many-locals" => "Extract helper functions to reduce local variables".to_string(),
            "line-too-long" => "Break the line into multiple lines".to_string(),
            "missing-docstring" => "Add a docstring explaining the purpose".to_string(),
            "broad-except" => "Catch specific exceptions instead of broad Exception".to_string(),
            "consider-using-enumerate" => "Use enumerate() for cleaner iteration".to_string(),
            "consider-using-with" => "Use context manager (with statement)".to_string(),
            "redefined-outer-name" => "Rename variable to avoid shadowing outer scope".to_string(),
            _ => format!("Review pylint suggestion: {}", message),
        }
    }

    fn get_category_tag(symbol: &str) -> String {
        if symbol.contains("unused") {
            "unused_code".to_string()
        } else if symbol.contains("too-many") {
            "complexity".to_string()
        } else if symbol.contains("docstring") {
            "documentation".to_string()
        } else if symbol.contains("line-too-long") || symbol.contains("whitespace") || symbol.contains("indentation") {
            "style".to_string()
        } else if symbol.contains("except") {
            "error_handling".to_string()
        } else if symbol.contains("redefined") || symbol.contains("builtin") || symbol.contains("global") {
            "naming_scope".to_string()
        } else if symbol.contains("consider-using") || symbol.contains("unnecessary") {
            "refactoring".to_string()
        } else if symbol.contains("duplicate") {
            "duplication".to_string()
        } else {
            "general".to_string()
        }
    }
}

impl Detector for PylintDetector {
    fn name(&self) -> &'static str {
        "PylintDetector"
    }

    fn description(&self) -> &'static str {
        "Detects code quality issues in Python using Pylint"
    }

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        info!("Running Pylint on {:?}", self.repository_path);

        let results = self.run_pylint();

        if results.is_empty() {
            info!("No pylint violations found");
            return Ok(Vec::new());
        }

        let findings: Vec<Finding> = results
            .iter()
            .take(self.max_findings)
            .filter_map(|r| self.create_finding(r, graph))
            .collect();

        info!("Created {} code quality findings", findings.len());
        Ok(findings)
    }

    fn category(&self) -> &'static str {
        "code_quality"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_severity_mapping() {
        assert_eq!(PylintDetector::map_severity("fatal"), Severity::Critical);
        assert_eq!(PylintDetector::map_severity("error"), Severity::High);
        assert_eq!(PylintDetector::map_severity("warning"), Severity::Medium);
        assert_eq!(PylintDetector::map_severity("refactor"), Severity::Low);
        assert_eq!(PylintDetector::map_severity("convention"), Severity::Low);
    }
}
