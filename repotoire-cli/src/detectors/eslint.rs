//! ESLint-based TypeScript/JavaScript linter
//!
//! Uses ESLint for comprehensive JavaScript/TypeScript code quality analysis,
//! including security rules via eslint-plugin-security and framework-specific rules.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::external_tool::{batch_get_graph_context, run_js_tool, GraphContext};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use serde_json::Value as JsonValue;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use tracing::{debug, info, warn};
use uuid::Uuid;

/// ESLint code quality detector
pub struct ESLintDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
    extensions: Vec<String>,
}

impl ESLintDetector {
    /// Create a new ESLint detector
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            config: DetectorConfig::default(),
            repository_path: repository_path.into(),
            max_findings: 100,
            extensions: vec![
                ".ts".to_string(),
                ".tsx".to_string(),
                ".js".to_string(),
                ".jsx".to_string(),
            ],
        }
    }

    /// Set maximum findings
    pub fn with_max_findings(mut self, max: usize) -> Self {
        self.max_findings = max;
        self
    }

    /// Set file extensions to lint
    pub fn with_extensions(mut self, exts: Vec<String>) -> Self {
        self.extensions = exts;
        self
    }

    /// Run ESLint and parse results
    fn run_eslint(&self) -> Vec<JsonValue> {
        let mut args = vec![
            "--format".to_string(),
            "json".to_string(),
            "--no-error-on-unmatched-pattern".to_string(),
        ];

        for ext in &self.extensions {
            args.push("--ext".to_string());
            args.push(ext.clone());
        }

        args.push(self.repository_path.to_string_lossy().to_string());

        let result = run_js_tool(
            "eslint",
            &args,
            "eslint",
            120,
            Some(&self.repository_path),
            None,
        );

        if result.timed_out {
            warn!("ESLint timed out");
            return Vec::new();
        }

        // ESLint returns array of file results
        result.json_array().unwrap_or_default()
    }

    /// Map ESLint severity to our severity
    fn map_severity(rule_id: &str, eslint_severity: i64) -> Severity {
        // Check for specific rule severity first
        match rule_id {
            // Security rules - high severity
            r if r.starts_with("security/") => Severity::High,
            "no-eval" | "no-implied-eval" | "no-new-func" => Severity::Critical,

            // TypeScript rules
            r if r.starts_with("@typescript-eslint/") => {
                if r.contains("any") || r.contains("unsafe") {
                    Severity::Medium
                } else if r.contains("unused") {
                    Severity::Low
                } else {
                    Severity::Medium
                }
            }

            // Error-prone rules
            "no-undef" | "no-dupe-keys" | "no-duplicate-case" | "use-isnan" | "valid-typeof" => {
                Severity::High
            }
            "no-unreachable" | "no-constant-condition" | "no-func-assign" => Severity::Medium,

            // Best practices
            "eqeqeq" | "no-fallthrough" | "no-throw-literal" => Severity::Medium,

            // Style rules
            "indent" | "quotes" | "semi" | "comma-dangle" | "max-len" => Severity::Info,
            "prefer-const" | "no-var" => Severity::Low,

            _ => {
                match eslint_severity {
                    2 => Severity::Medium, // error
                    1 => Severity::Low,    // warning
                    _ => Severity::Info,
                }
            }
        }
    }

    /// Create finding from ESLint message
    fn create_finding(
        &self,
        file_path: &str,
        message: &JsonValue,
        file_contexts: &HashMap<String, GraphContext>,
    ) -> Option<Finding> {
        let rule_id = message
            .get("ruleId")
            .and_then(|r| r.as_str())
            .unwrap_or("unknown");
        let eslint_severity = message
            .get("severity")
            .and_then(|s| s.as_i64())
            .unwrap_or(1);
        let msg_text = message.get("message")?.as_str()?;
        let line = message.get("line")?.as_u64()? as u32;
        let column = message.get("column").and_then(|c| c.as_u64()).unwrap_or(0) as u32;
        let has_fix = message.get("fix").is_some();

        // Convert to relative path
        let rel_path = Path::new(file_path)
            .strip_prefix(&self.repository_path)
            .map(|p| p.to_string_lossy().to_string().replace('\\', "/"))
            .unwrap_or_else(|_| file_path.replace('\\', "/"));

        let ctx = file_contexts.get(&rel_path).cloned().unwrap_or_default();
        let severity = Self::map_severity(rule_id, eslint_severity);

        // Build description
        let mut description = format!(
            "{}\n\n\
             **Location**: {}:{}:{}\n\
             **Rule**: {}\n",
            msg_text, rel_path, line, column, rule_id
        );

        // Add documentation link
        if !rule_id.starts_with("@") {
            description.push_str(&format!(
                "**Documentation**: https://eslint.org/docs/rules/{}\n",
                rule_id
            ));
        } else if rule_id.starts_with("@typescript-eslint/") {
            let rule_name = rule_id.replace("@typescript-eslint/", "");
            description.push_str(&format!(
                "**Documentation**: https://typescript-eslint.io/rules/{}\n",
                rule_name
            ));
        }

        if let Some(loc) = ctx.file_loc {
            description.push_str(&format!("**File Size**: {} LOC\n", loc));
        }

        if ctx.max_complexity() > 0 {
            description.push_str(&format!("**Complexity**: {}\n", ctx.max_complexity()));
        }

        let suggested_fix = if has_fix {
            "ESLint can auto-fix this issue. Run: npx eslint --fix <file>".to_string()
        } else {
            Self::suggest_fix(rule_id, msg_text)
        };

        let _language = if rel_path.ends_with(".ts") || rel_path.ends_with(".tsx") {
            "typescript"
        } else {
            "javascript"
        };

        Some(Finding {
            id: Uuid::new_v4().to_string(),
            detector: "ESLintDetector".to_string(),
            severity,
            title: format!("ESLint: {}", rule_id),
            description,
            affected_files: vec![PathBuf::from(&rel_path)],
            line_start: Some(line),
            line_end: Some(line),
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some("Small (5-15 minutes)".to_string()),
            category: Some(Self::get_tag_from_rule(rule_id)),
            cwe_id: None,
            why_it_matters: None,
            ..Default::default()
        })
    }

    fn suggest_fix(rule_id: &str, message: &str) -> String {
        match rule_id {
            "no-unused-vars" | "@typescript-eslint/no-unused-vars" => {
                "Remove the unused variable or prefix with underscore".to_string()
            }
            "no-undef" => "Define the variable or add it to globals configuration".to_string(),
            "no-eval" => "Replace eval() with safer alternatives like JSON.parse()".to_string(),
            "eqeqeq" => "Use strict equality (=== or !==) instead of loose equality".to_string(),
            "@typescript-eslint/no-explicit-any" => {
                "Replace 'any' with a specific type or use 'unknown'".to_string()
            }
            "prefer-const" => {
                "Use 'const' instead of 'let' for variables that are never reassigned".to_string()
            }
            "no-var" => "Use 'let' or 'const' instead of 'var'".to_string(),
            "@typescript-eslint/no-non-null-assertion" => {
                "Use optional chaining (?.) or nullish coalescing (??)".to_string()
            }
            "no-console" => "Remove console statements or use a proper logging library".to_string(),
            _ => format!("Review ESLint suggestion: {}", message),
        }
    }

    fn get_tag_from_rule(rule_id: &str) -> String {
        if rule_id.starts_with("security/") {
            "security".to_string()
        } else if rule_id.starts_with("@typescript-eslint/") {
            if rule_id.contains("unused") {
                "unused_code".to_string()
            } else if rule_id.contains("any") {
                "type_safety".to_string()
            } else {
                "typescript".to_string()
            }
        } else if rule_id.starts_with("import/") {
            "imports".to_string()
        } else if rule_id.starts_with("react/") || rule_id.starts_with("react-hooks/") {
            "react".to_string()
        } else if rule_id.contains("unused") {
            "unused_code".to_string()
        } else if rule_id.contains("semi")
            || rule_id.contains("quotes")
            || rule_id.contains("indent")
        {
            "style".to_string()
        } else if rule_id.contains("security") || rule_id.contains("eval") {
            "security".to_string()
        } else {
            "general".to_string()
        }
    }
}

impl Detector for ESLintDetector {
    fn name(&self) -> &'static str {
        "ESLintDetector"
    }

    fn description(&self) -> &'static str {
        "Detects code quality issues in TypeScript/JavaScript using ESLint"
    }

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        info!("Running ESLint on {:?}", self.repository_path);

        let results = self.run_eslint();

        if results.is_empty() {
            info!("No ESLint violations found");
            return Ok(Vec::new());
        }

        // Collect unique file paths for batch context fetching
        let mut unique_files: Vec<String> = results
            .iter()
            .filter_map(|r| r.get("filePath").and_then(|f| f.as_str()))
            .map(|f| {
                Path::new(f)
                    .strip_prefix(&self.repository_path)
                    .map(|p| p.to_string_lossy().to_string().replace('\\', "/"))
                    .unwrap_or_else(|_| f.replace('\\', "/"))
            })
            .collect();
        unique_files.sort();
        unique_files.dedup();

        // Batch fetch graph context
        let file_contexts = batch_get_graph_context(graph, &unique_files);
        debug!(
            "Batch fetched graph context for {} files",
            file_contexts.len()
        );

        // Process all messages
        let mut findings = Vec::new();
        for file_result in &results {
            let file_path = file_result
                .get("filePath")
                .and_then(|f| f.as_str())
                .unwrap_or("");
            let messages = file_result
                .get("messages")
                .and_then(|m| m.as_array())
                .map(|a| a.as_slice())
                .unwrap_or(&[]);

            for message in messages {
                if findings.len() >= self.max_findings {
                    break;
                }

                if let Some(finding) = self.create_finding(file_path, message, &file_contexts) {
                    findings.push(finding);
                }
            }

            if findings.len() >= self.max_findings {
                break;
            }
        }

        info!("Created {} ESLint findings", findings.len());
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
        assert_eq!(
            ESLintDetector::map_severity("no-eval", 2),
            Severity::Critical
        );
        assert_eq!(
            ESLintDetector::map_severity("security/detect-eval", 2),
            Severity::High
        );
        assert_eq!(ESLintDetector::map_severity("no-undef", 2), Severity::High);
        assert_eq!(ESLintDetector::map_severity("eqeqeq", 2), Severity::Medium);
        assert_eq!(
            ESLintDetector::map_severity("prefer-const", 1),
            Severity::Low
        );
    }
}
