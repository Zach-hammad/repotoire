//! Mypy-based type checking detector
//!
//! Uses mypy for Python static type analysis, detecting type errors
//! and ensuring type safety across the codebase.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::external_tool::{get_graph_context, run_external_tool};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use serde_json::Value as JsonValue;
use std::path::{Path, PathBuf};
use tracing::{debug, info, warn};
use uuid::Uuid;

/// Mypy type checking detector
pub struct MypyDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
    strict_mode: bool,
}

impl MypyDetector {
    /// Create a new Mypy detector
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            config: DetectorConfig::default(),
            repository_path: repository_path.into(),
            max_findings: 100,
            strict_mode: false,
        }
    }

    /// Set maximum findings
    pub fn with_max_findings(mut self, max: usize) -> Self {
        self.max_findings = max;
        self
    }

    /// Enable strict mode
    pub fn with_strict_mode(mut self, strict: bool) -> Self {
        self.strict_mode = strict;
        self
    }

    /// Run mypy and parse results
    fn run_mypy(&self) -> Vec<JsonValue> {
        let mut cmd = vec![
            "python".to_string(),
            "-m".to_string(),
            "mypy".to_string(),
            "--output".to_string(),
            "json".to_string(),
            "--incremental".to_string(),
        ];

        if self.strict_mode {
            cmd.push("--strict".to_string());
        }

        cmd.push(self.repository_path.to_string_lossy().to_string());

        let result = run_external_tool(&cmd, "mypy", 300, Some(&self.repository_path), None);

        if result.timed_out {
            warn!("Mypy timed out");
            return Vec::new();
        }

        // Mypy outputs one JSON object per line
        result
            .stdout
            .lines()
            .filter(|line| !line.is_empty())
            .filter_map(|line| serde_json::from_str(line).ok())
            .collect()
    }

    /// Map mypy error code to severity
    fn map_severity(error_code: &str, mypy_severity: &str) -> Severity {
        // Check code-specific mapping first
        match error_code {
            "attr-defined" | "name-defined" | "call-arg" | "return-value" => Severity::High,
            "arg-type" | "return" | "override" | "type-arg" | "assignment" => Severity::Medium,
            "no-untyped-def" | "no-any-return" | "redundant-cast" | "misc" => Severity::Low,
            _ => {
                match mypy_severity {
                    "error" => Severity::Medium,
                    "warning" => Severity::Low,
                    _ => Severity::Info,
                }
            }
        }
    }

    /// Create finding from mypy result
    fn create_finding(
        &self,
        result: &JsonValue,
        graph: &GraphStore,
    ) -> Option<Finding> {
        let file_path = result.get("file")?.as_str()?;
        let line = result.get("line")?.as_u64()? as u32;
        let column = result.get("column").and_then(|c| c.as_u64()).unwrap_or(0) as u32;
        let message = result.get("message")?.as_str()?;
        let error_code = result.get("code").and_then(|c| c.as_str()).unwrap_or("misc");
        let mypy_severity = result.get("severity").and_then(|s| s.as_str()).unwrap_or("error");

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
        let severity = Self::map_severity(error_code, mypy_severity);

        // Build description
        let mut description = format!(
            "{}\n\n\
             **Location**: {}:{}\n\
             **Error Code**: {}\n",
            message, rel_path, line, error_code
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
            detector: "MypyDetector".to_string(),
            severity,
            title: format!("Type error: {}", message.chars().take(50).collect::<String>()),
            description,
            affected_files: vec![PathBuf::from(&rel_path)],
            line_start: Some(line),
            line_end: Some(line),
            suggested_fix: Some(Self::suggest_fix(error_code, message)),
            estimated_effort: Some(Self::estimate_effort(error_code)),
            category: Some(Self::get_category_tag(error_code)),
            cwe_id: None,
            why_it_matters: Some(
                "Type errors can cause runtime crashes. Static type checking catches these bugs before they reach production.".to_string()
            ),
            ..Default::default()
        })
    }

    fn suggest_fix(error_code: &str, _message: &str) -> String {
        match error_code {
            "attr-defined" => "Add the missing attribute or check if the object type is correct".to_string(),
            "name-defined" => "Define the name before using it or check for typos".to_string(),
            "call-arg" => "Check function signature and provide correct arguments".to_string(),
            "return-value" => "Ensure return value matches the declared return type".to_string(),
            "assignment" => "Check that assigned value matches the variable's type".to_string(),
            "arg-type" => "Ensure argument types match the function signature".to_string(),
            "no-untyped-def" => "Add type annotations to function signature".to_string(),
            "no-any-return" => "Specify a more specific return type instead of Any".to_string(),
            _ => "Review the type error and add appropriate type hints or fix the type mismatch".to_string(),
        }
    }

    fn estimate_effort(error_code: &str) -> String {
        match error_code {
            "redundant-cast" | "no-any-return" | "misc" => "Small (5-15 minutes)".to_string(),
            "no-untyped-def" | "arg-type" | "assignment" => "Medium (30-60 minutes)".to_string(),
            _ => "Medium (1-2 hours)".to_string(),
        }
    }

    fn get_category_tag(error_code: &str) -> String {
        match error_code {
            "attr-defined" | "name-defined" => "undefined_reference".to_string(),
            "call-arg" | "arg-type" => "function_signature".to_string(),
            "return-value" | "return" => "return_type".to_string(),
            "assignment" | "override" => "type_mismatch".to_string(),
            "no-untyped-def" | "no-any-return" => "missing_annotations".to_string(),
            "type-arg" => "generic_types".to_string(),
            "redundant-cast" => "unnecessary_cast".to_string(),
            _ => "general_type_error".to_string(),
        }
    }
}

impl Detector for MypyDetector {
    fn name(&self) -> &'static str {
        "MypyDetector"
    }

    fn description(&self) -> &'static str {
        "Detects type errors in Python code using mypy static type checker"
    }

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        info!("Running mypy on {:?}", self.repository_path);

        let results = self.run_mypy();

        if results.is_empty() {
            info!("No mypy violations found");
            return Ok(Vec::new());
        }

        let findings: Vec<Finding> = results
            .iter()
            .take(self.max_findings)
            .filter_map(|r| self.create_finding(r, graph))
            .collect();

        info!("Created {} type checking findings", findings.len());
        Ok(findings)
    }

    fn category(&self) -> &'static str {
        "type_safety"
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
        assert_eq!(MypyDetector::map_severity("attr-defined", "error"), Severity::High);
        assert_eq!(MypyDetector::map_severity("arg-type", "error"), Severity::Medium);
        assert_eq!(MypyDetector::map_severity("no-untyped-def", "warning"), Severity::Low);
        assert_eq!(MypyDetector::map_severity("unknown", "note"), Severity::Info);
    }
}
