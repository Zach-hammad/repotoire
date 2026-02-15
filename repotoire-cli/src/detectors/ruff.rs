//! Ruff-based comprehensive linting detector
//!
//! Uses Ruff (fast Python linter written in Rust) for comprehensive code quality checks.
//! Ruff is 100x faster than Pylint while covering most of the same rules.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::external_tool::{get_graph_context, run_external_tool, GraphContext};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use serde_json::Value as JsonValue;
use std::path::{Path, PathBuf};
use tracing::{debug, info, warn};
use uuid::Uuid;

/// Ruff lint detector
pub struct RuffLintDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
    select_rules: Vec<String>,
    ignore_rules: Vec<String>,
}

impl RuffLintDetector {
    /// Create a new Ruff detector
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            config: DetectorConfig::default(),
            repository_path: repository_path.into(),
            max_findings: 100,
            select_rules: vec!["ALL".to_string()],
            ignore_rules: vec![
                "D100".to_string(),
                "D101".to_string(),
                "D102".to_string(),
                "D103".to_string(),
                "D104".to_string(), // Missing docstrings
                "ANN001".to_string(),
                "ANN002".to_string(),
                "ANN003".to_string(), // Type annotations
            ],
        }
    }

    /// Set maximum findings
    pub fn with_max_findings(mut self, max: usize) -> Self {
        self.max_findings = max;
        self
    }

    /// Set rules to select
    pub fn with_select_rules(mut self, rules: Vec<String>) -> Self {
        self.select_rules = rules;
        self
    }

    /// Set rules to ignore
    pub fn with_ignore_rules(mut self, rules: Vec<String>) -> Self {
        self.ignore_rules = rules;
        self
    }

    /// Run ruff and parse results
    fn run_ruff(&self) -> Vec<JsonValue> {
        let mut cmd = vec![
            "ruff".to_string(),
            "check".to_string(),
            "--output-format=json".to_string(),
            "--select".to_string(),
            self.select_rules.join(","),
        ];

        if !self.ignore_rules.is_empty() {
            cmd.push("--ignore".to_string());
            cmd.push(self.ignore_rules.join(","));
        }

        cmd.push(self.repository_path.to_string_lossy().to_string());

        let result = run_external_tool(&cmd, "ruff", 60, Some(&self.repository_path), None);

        if result.timed_out {
            warn!("Ruff timed out");
            return Vec::new();
        }

        // Ruff outputs JSON array directly
        result.json_array().unwrap_or_default()
    }

    /// Map ruff rule code to severity
    fn map_severity(code: &str) -> Severity {
        // See: https://docs.astral.sh/ruff/rules/
        if code.starts_with("F") {
            Severity::High // Pyflakes
        } else if code.starts_with("E9") {
            Severity::High // Syntax errors
        } else if code.starts_with("B") {
            Severity::Medium // Bugbear
        } else if code.starts_with("S") {
            Severity::Medium // Security
        } else if code.starts_with("C90") {
            Severity::Medium // Complexity
        } else if code.starts_with("N") {
            Severity::Low // Naming
        } else if code.starts_with("E") || code.starts_with("W") {
            Severity::Low // Style
        } else if code.starts_with("I") || code.starts_with("UP") {
            Severity::Low // Imports, upgrades
        } else if code.starts_with("D") || code.starts_with("ANN") {
            Severity::Info // Docs, annotations
        } else {
            Severity::Low
        }
    }

    /// Create finding from ruff result
    fn create_finding(&self, result: &JsonValue, graph: &dyn crate::graph::GraphQuery) -> Option<Finding> {
        let file_path = result.get("filename")?.as_str()?;
        let location = result.get("location")?;
        let line = location.get("row")?.as_u64()? as u32;
        let column = location.get("column")?.as_u64()? as u32;
        let message = result.get("message")?.as_str()?;
        let code = result.get("code")?.as_str().unwrap_or("");
        let url = result.get("url").and_then(|u| u.as_str());
        let has_fix = result.get("fix").is_some();

        // Convert to relative path
        let rel_path = Path::new(file_path)
            .strip_prefix(&self.repository_path)
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_else(|_| file_path.to_string());

        let ctx = get_graph_context(graph, &rel_path, Some(line));
        let severity = Self::map_severity(code);

        // Build description
        let mut description = format!(
            "{}\n\n\
             **Location**: {}:{}:{}\n\
             **Rule**: {}\n",
            message, rel_path, line, column, code
        );

        if let Some(doc_url) = url {
            description.push_str(&format!("**Documentation**: {}\n", doc_url));
        }

        if let Some(loc) = ctx.file_loc {
            description.push_str(&format!("**File Size**: {} LOC\n", loc));
        }

        if ctx.max_complexity() > 0 {
            description.push_str(&format!("**Complexity**: {}\n", ctx.max_complexity()));
        }

        if !ctx.affected_nodes.is_empty() {
            description.push_str(&format!(
                "**Affected**: {}\n",
                ctx.affected_nodes
                    .iter()
                    .take(3)
                    .cloned()
                    .collect::<Vec<_>>()
                    .join(", ")
            ));
        }

        let suggested_fix = if has_fix {
            format!(
                "Ruff can auto-fix this issue. Run: ruff check --fix {}",
                code
            )
        } else {
            Self::suggest_fix(code, message)
        };

        Some(Finding {
            id: Uuid::new_v4().to_string(),
            detector: "RuffLintDetector".to_string(),
            severity,
            title: format!("Code quality: {}", code),
            description,
            affected_files: vec![PathBuf::from(&rel_path)],
            line_start: Some(line),
            line_end: Some(line),
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some("Small (5-15 minutes)".to_string()),
            category: Some(Self::get_category(code)),
            cwe_id: None,
            why_it_matters: None,
            ..Default::default()
        })
    }

    fn suggest_fix(code: &str, message: &str) -> String {
        match code {
            "F401" => "Remove the unused import".to_string(),
            "F841" => "Remove the unused variable or prefix with underscore".to_string(),
            "E501" => "Break the line into multiple lines (max 88 chars)".to_string(),
            "B006" => "Use None as default, then initialize mutable in function".to_string(),
            "B008" => "Move function call outside of function signature".to_string(),
            "S101" => "Replace assert with proper error handling for production code".to_string(),
            "C901" => "Refactor to reduce complexity (extract helper functions)".to_string(),
            "N802" => "Use lowercase for function names (PEP 8)".to_string(),
            "UP008" => "Use super() without arguments in Python 3+".to_string(),
            "I001" => "Run: ruff check --fix to auto-sort imports".to_string(),
            _ => format!("Review ruff suggestion: {}", message),
        }
    }

    fn get_category(code: &str) -> String {
        if code.starts_with("F") {
            "error_prone".to_string()
        } else if code.starts_with("E") || code.starts_with("W") {
            "style".to_string()
        } else if code.starts_with("B") {
            "bug_risk".to_string()
        } else if code.starts_with("S") {
            "security".to_string()
        } else if code.starts_with("C90") {
            "complexity".to_string()
        } else if code.starts_with("N") {
            "naming".to_string()
        } else if code.starts_with("I") {
            "imports".to_string()
        } else if code.starts_with("D") {
            "documentation".to_string()
        } else if code.starts_with("ANN") {
            "type_hints".to_string()
        } else if code.starts_with("UP") {
            "modernization".to_string()
        } else {
            "general".to_string()
        }
    }
}

impl Detector for RuffLintDetector {
    fn name(&self) -> &'static str {
        "RuffLintDetector"
    }

    fn description(&self) -> &'static str {
        "Detects code quality issues in Python using Ruff (100x faster than Pylint)"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        info!("Running Ruff on {:?}", self.repository_path);

        let results = self.run_ruff();

        if results.is_empty() {
            info!("No ruff violations found");
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

/// Ruff import detector (F401 only)
pub struct RuffImportDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
}

impl RuffImportDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            config: DetectorConfig::default(),
            repository_path: repository_path.into(),
        }
    }

    fn run_ruff(&self) -> Vec<JsonValue> {
        let cmd = vec![
            "ruff".to_string(),
            "check".to_string(),
            "--select".to_string(),
            "F401".to_string(),
            "--output-format".to_string(),
            "json".to_string(),
            self.repository_path.to_string_lossy().to_string(),
        ];

        let result = run_external_tool(&cmd, "ruff", 60, Some(&self.repository_path), None);
        result.json_array().unwrap_or_default()
    }
}

impl Detector for RuffImportDetector {
    fn name(&self) -> &'static str {
        "RuffImportDetector"
    }

    fn description(&self) -> &'static str {
        "Detects unused imports using Ruff's F401 rule"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        info!("Running Ruff import check on {:?}", self.repository_path);

        let results = self.run_ruff();

        if results.is_empty() {
            info!("No unused imports found");
            return Ok(Vec::new());
        }

        // Group by file
        let mut by_file: std::collections::HashMap<String, Vec<&JsonValue>> =
            std::collections::HashMap::new();
        for result in &results {
            if let Some(file) = result.get("filename").and_then(|f| f.as_str()) {
                by_file.entry(file.to_string()).or_default().push(result);
            }
        }

        let mut findings = Vec::new();
        for (file_path, file_results) in by_file {
            let rel_path = Path::new(&file_path)
                .strip_prefix(&self.repository_path)
                .map(|p| p.to_string_lossy().to_string())
                .unwrap_or_else(|_| file_path.clone());

            let ctx = get_graph_context(graph, &rel_path, None);
            let count = file_results.len();

            let severity = if count >= 5 {
                Severity::Medium
            } else {
                Severity::Low
            };

            let imports: Vec<String> = file_results
                .iter()
                .filter_map(|r| {
                    let msg = r.get("message")?.as_str()?;
                    let line = r
                        .get("location")
                        .and_then(|l| l.get("row"))
                        .and_then(|r| r.as_u64())?;
                    // Extract import name from message like "`os` imported but unused"
                    let name = msg.split('`').nth(1).unwrap_or("unknown");
                    Some(format!("  • {} (line {})", name, line))
                })
                .collect();

            let description = format!(
                "File '{}' has {} unused import(s):\n\n{}\n\n\
                 These imports are detected by ruff's AST analysis and are safe to remove.{}",
                rel_path,
                count,
                imports.join("\n"),
                ctx.file_loc
                    .map(|l| format!("\n\nFile context: {} LOC", l))
                    .unwrap_or_default()
            );

            findings.push(Finding {
                id: format!("ruff_imports_{}", rel_path.replace(['/', '.'], "_")),
                detector: "RuffImportDetector".to_string(),
                severity,
                title: format!(
                    "Unused imports in {}",
                    Path::new(&rel_path)
                        .file_name()
                        .map(|n| n.to_string_lossy())
                        .unwrap_or_default()
                ),
                description,
                affected_files: vec![PathBuf::from(&rel_path)],
                line_start: None,
                line_end: None,
                suggested_fix: Some(format!("Run: ruff check --select F401 --fix {}", rel_path)),
                estimated_effort: Some("Tiny (5 minutes)".to_string()),
                category: Some("imports".to_string()),
                cwe_id: None,
                why_it_matters: None,
                ..Default::default()
            });
        }

        info!("Created {} unused import findings", findings.len());
        Ok(findings)
    }

    fn category(&self) -> &'static str {
        "code_quality"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_severity_mapping() {
        assert_eq!(RuffLintDetector::map_severity("F401"), Severity::High);
        assert_eq!(RuffLintDetector::map_severity("E501"), Severity::Low);
        assert_eq!(RuffLintDetector::map_severity("B006"), Severity::Medium);
        assert_eq!(RuffLintDetector::map_severity("S101"), Severity::Medium);
        assert_eq!(RuffLintDetector::map_severity("D100"), Severity::Info);
    }

    #[test]
    fn test_category_mapping() {
        assert_eq!(RuffLintDetector::get_category("F401"), "error_prone");
        assert_eq!(RuffLintDetector::get_category("S101"), "security");
        assert_eq!(RuffLintDetector::get_category("C901"), "complexity");
    }
}
