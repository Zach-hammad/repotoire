//! Semgrep-based advanced security detector
//!
//! Uses Semgrep for pattern-based security scanning, detecting issues like:
//! - OWASP Top 10 vulnerabilities
//! - Language-specific security issues
//! - Custom security patterns

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::external_tool::{get_graph_context, run_external_tool};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use serde_json::Value as JsonValue;
use std::path::{Path, PathBuf};
use tracing::{debug, info, warn};
use uuid::Uuid;

/// Semgrep security detector
pub struct SemgrepDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
    semgrep_config: String,
    severity_threshold: String,
    exclude_patterns: Vec<String>,
}

impl SemgrepDetector {
    /// Create a new Semgrep detector
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            config: DetectorConfig::default(),
            repository_path: repository_path.into(),
            max_findings: 50,
            semgrep_config: "auto".to_string(),
            severity_threshold: "INFO".to_string(),
            exclude_patterns: vec![
                "tests/".to_string(),
                "test_*.py".to_string(),
                "*_test.py".to_string(),
                "migrations/".to_string(),
                ".venv/".to_string(),
                "venv/".to_string(),
                "node_modules/".to_string(),
                "__pycache__/".to_string(),
            ],
        }
    }

    /// Set maximum findings
    pub fn with_max_findings(mut self, max: usize) -> Self {
        self.max_findings = max;
        self
    }

    /// Set semgrep config/ruleset
    pub fn with_config(mut self, config: impl Into<String>) -> Self {
        self.semgrep_config = config.into();
        self
    }

    /// Set minimum severity threshold
    pub fn with_severity_threshold(mut self, threshold: impl Into<String>) -> Self {
        self.severity_threshold = threshold.into();
        self
    }

    /// Set exclude patterns
    pub fn with_exclude_patterns(mut self, patterns: Vec<String>) -> Self {
        self.exclude_patterns = patterns;
        self
    }

    /// Run semgrep and parse results
    fn run_semgrep(&self) -> Vec<JsonValue> {
        let mut cmd = vec![
            "semgrep".to_string(),
            "scan".to_string(),
            "--json".to_string(),
            "--quiet".to_string(),
            format!("--config={}", self.semgrep_config),
            "--jobs=4".to_string(),
            "--max-memory=2000".to_string(),
        ];

        for pattern in &self.exclude_patterns {
            cmd.push("--exclude".to_string());
            cmd.push(pattern.clone());
        }

        cmd.push(self.repository_path.to_string_lossy().to_string());

        let result = run_external_tool(&cmd, "semgrep", 180, Some(&self.repository_path), None);

        if result.timed_out {
            warn!("Semgrep timed out");
            return Vec::new();
        }

        match result.json_output() {
            Some(json) => {
                let results = json
                    .get("results")
                    .and_then(|r| r.as_array())
                    .cloned()
                    .unwrap_or_default();

                // Filter by severity threshold
                let severity_order = ["INFO", "WARNING", "ERROR"];
                let threshold_level = severity_order
                    .iter()
                    .position(|&s| s == self.severity_threshold.to_uppercase())
                    .unwrap_or(0);

                results
                    .into_iter()
                    .filter(|r| {
                        let severity = r
                            .get("extra")
                            .and_then(|e| e.get("severity"))
                            .and_then(|s| s.as_str())
                            .unwrap_or("INFO");
                        severity_order
                            .iter()
                            .position(|&s| s == severity)
                            .unwrap_or(0)
                            >= threshold_level
                    })
                    .collect()
            }
            None => {
                info!("Semgrep produced no output");
                Vec::new()
            }
        }
    }

    /// Map semgrep severity to our severity
    fn map_severity(semgrep_severity: &str) -> Severity {
        match semgrep_severity.to_uppercase().as_str() {
            "ERROR" => Severity::High,
            "WARNING" => Severity::Medium,
            "INFO" => Severity::Low,
            _ => Severity::Low,
        }
    }

    /// Create finding from semgrep result
    fn create_finding(&self, result: &JsonValue, graph: &dyn crate::graph::GraphQuery) -> Option<Finding> {
        let path = result.get("path")?.as_str()?;
        let check_id = result.get("check_id")?.as_str().unwrap_or("");
        let extra = result.get("extra")?;
        let message = extra.get("message")?.as_str().unwrap_or("");
        let severity_str = extra.get("severity")?.as_str().unwrap_or("INFO");
        let metadata = extra.get("metadata");

        let start = result.get("start")?;
        let start_line = start.get("line")?.as_u64()? as u32;
        let end_line = result
            .get("end")
            .and_then(|e| e.get("line"))
            .and_then(|l| l.as_u64())
            .unwrap_or(start_line as u64) as u32;

        // Convert to relative path
        let rel_path = Path::new(path)
            .strip_prefix(&self.repository_path)
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_else(|_| path.to_string());

        let ctx = get_graph_context(graph, &rel_path, Some(start_line));
        let severity = Self::map_severity(severity_str);

        // Build description
        let mut description = format!("{}\n\n", message);

        if let Some(meta) = metadata {
            if let Some(cwe) = meta.get("cwe").and_then(|c| c.as_array()) {
                let cwe_list: Vec<&str> = cwe.iter().filter_map(|c| c.as_str()).collect();
                if !cwe_list.is_empty() {
                    description.push_str(&format!("**CWE**: {}\n", cwe_list.join(", ")));
                }
            }

            if let Some(owasp) = meta.get("owasp").and_then(|o| o.as_array()) {
                let owasp_list: Vec<&str> = owasp.iter().filter_map(|o| o.as_str()).collect();
                if !owasp_list.is_empty() {
                    description.push_str(&format!("**OWASP**: {}\n", owasp_list.join(", ")));
                }
            }

            if let Some(category) = meta.get("category").and_then(|c| c.as_str()) {
                description.push_str(&format!("**Category**: {}\n", category));
            }
        }

        if let Some(loc) = ctx.file_loc {
            description.push_str(&format!("**File Size**: {} LOC\n", loc));
        }

        description.push_str(
            "\n**Impact**: Security vulnerability detected by Semgrep pattern matching.\n",
        );

        // Extract rule name from check_id
        let rule_name = check_id.split('.').next_back().unwrap_or(check_id);

        let suggested_fix = Self::suggest_fix(metadata, message);
        let effort = Self::estimate_effort(severity_str);

        let cwe_id = metadata
            .and_then(|m| m.get("cwe"))
            .and_then(|c| c.as_array())
            .and_then(|arr| arr.first())
            .and_then(|c| c.as_str())
            .map(String::from);

        Some(Finding {
            id: Uuid::new_v4().to_string(),
            detector: "SemgrepDetector".to_string(),
            severity,
            title: format!("Security issue: {}", rule_name),
            description,
            affected_files: vec![PathBuf::from(&rel_path)],
            line_start: Some(start_line),
            line_end: Some(end_line),
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some(effort),
            category: Some(Self::get_category_tag(check_id, metadata)),
            cwe_id,
            why_it_matters: Some(
                "Security vulnerabilities can be exploited by attackers to compromise your application.".to_string()
            ),
            ..Default::default()
        })
    }

    fn suggest_fix(metadata: Option<&JsonValue>, message: &str) -> String {
        // Try to get fix from metadata
        if let Some(meta) = metadata {
            if let Some(fix) = meta.get("fix").and_then(|f| f.as_str()) {
                return format!("Recommended fix: {}", fix);
            }
        }

        // Generic suggestions based on category
        let msg_lower = message.to_lowercase();
        if msg_lower.contains("injection") || msg_lower.contains("sql") {
            "Use parameterized queries or ORM methods to prevent injection attacks".to_string()
        } else if msg_lower.contains("xss") || msg_lower.contains("cross-site") {
            "Sanitize user input and escape output properly".to_string()
        } else if msg_lower.contains("auth") {
            "Review authentication logic and ensure proper access controls".to_string()
        } else if msg_lower.contains("crypto") || msg_lower.contains("encryption") {
            "Use cryptographically secure algorithms and proper key management".to_string()
        } else if msg_lower.contains("path") || msg_lower.contains("traversal") {
            "Validate and sanitize file paths, use allowlist approach".to_string()
        } else {
            "Review the code and apply security best practices as per Semgrep recommendation"
                .to_string()
        }
    }

    fn estimate_effort(severity: &str) -> String {
        match severity.to_uppercase().as_str() {
            "ERROR" => "High (half day to full day)".to_string(),
            "WARNING" => "Medium (2-4 hours)".to_string(),
            _ => "Small (1-2 hours)".to_string(),
        }
    }

    fn get_category_tag(check_id: &str, metadata: Option<&JsonValue>) -> String {
        if let Some(meta) = metadata {
            if let Some(category) = meta.get("category").and_then(|c| c.as_str()) {
                let cat_lower = category.to_lowercase();
                if cat_lower.contains("injection") || cat_lower.contains("sql") {
                    return "injection".to_string();
                } else if cat_lower.contains("xss") || cat_lower.contains("cross-site") {
                    return "xss".to_string();
                } else if cat_lower.contains("auth") {
                    return "authentication".to_string();
                } else if cat_lower.contains("crypto") {
                    return "cryptography".to_string();
                }
            }
        }

        let check_lower = check_id.to_lowercase();
        if check_lower.contains("injection") || check_lower.contains("sql") {
            "injection".to_string()
        } else if check_lower.contains("xss") {
            "xss".to_string()
        } else if check_lower.contains("auth") {
            "authentication".to_string()
        } else if check_lower.contains("crypto") || check_lower.contains("encryption") {
            "cryptography".to_string()
        } else if check_lower.contains("path") || check_lower.contains("traversal") {
            "path_traversal".to_string()
        } else if check_lower.contains("command") || check_lower.contains("exec") {
            "command_injection".to_string()
        } else if check_lower.contains("xxe") {
            "xxe".to_string()
        } else if check_lower.contains("ssrf") {
            "ssrf".to_string()
        } else {
            "security_general".to_string()
        }
    }
}

impl Detector for SemgrepDetector {
    fn name(&self) -> &'static str {
        "SemgrepDetector"
    }

    fn description(&self) -> &'static str {
        "Detects security vulnerabilities using Semgrep pattern matching"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        info!("Running Semgrep on {:?}", self.repository_path);

        let results = self.run_semgrep();

        if results.is_empty() {
            info!("No security issues found by Semgrep");
            return Ok(Vec::new());
        }

        let findings: Vec<Finding> = results
            .iter()
            .take(self.max_findings)
            .filter_map(|r| self.create_finding(r, graph))
            .collect();

        info!("Created {} security findings", findings.len());
        Ok(findings)
    }

    fn category(&self) -> &'static str {
        "security"
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
        assert_eq!(SemgrepDetector::map_severity("ERROR"), Severity::High);
        assert_eq!(SemgrepDetector::map_severity("WARNING"), Severity::Medium);
        assert_eq!(SemgrepDetector::map_severity("INFO"), Severity::Low);
    }
}
