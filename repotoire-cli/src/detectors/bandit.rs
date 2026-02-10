//! Bandit-based security vulnerability detector
//!
//! Uses bandit for Python security analysis, detecting issues like:
//! - SQL injection
//! - Command injection
//! - Hardcoded passwords
//! - Insecure cryptographic algorithms
//! - And more...

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::external_tool::{get_graph_context, run_external_tool, GraphContext};
use crate::graph::GraphClient;
use crate::models::{Finding, Severity};
use anyhow::Result;
use serde_json::Value as JsonValue;
use std::path::{Path, PathBuf};
use tracing::{debug, info, warn};
use uuid::Uuid;

/// Bandit security detector
pub struct BanditDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
    confidence_level: String,
}

impl BanditDetector {
    /// Create a new Bandit detector
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            config: DetectorConfig::default(),
            repository_path: repository_path.into(),
            max_findings: 100,
            confidence_level: "LOW".to_string(),
        }
    }

    /// Set maximum findings to report
    pub fn with_max_findings(mut self, max: usize) -> Self {
        self.max_findings = max;
        self
    }

    /// Set minimum confidence level (LOW, MEDIUM, HIGH)
    pub fn with_confidence_level(mut self, level: impl Into<String>) -> Self {
        self.confidence_level = level.into();
        self
    }

    /// Run bandit and parse results
    fn run_bandit(&self) -> Vec<JsonValue> {
        let cmd = vec![
            "bandit".to_string(),
            "-r".to_string(),
            "-f".to_string(),
            "json".to_string(),
            "--confidence-level".to_string(),
            self.confidence_level.clone(),
            self.repository_path.to_string_lossy().to_string(),
        ];

        let result = run_external_tool(&cmd, "bandit", 120, Some(&self.repository_path), None);

        if result.timed_out {
            warn!("Bandit timed out");
            return Vec::new();
        }

        // Bandit outputs JSON even on findings (non-zero exit)
        match result.json_output() {
            Some(json) => json
                .get("results")
                .and_then(|r| r.as_array())
                .cloned()
                .unwrap_or_default(),
            None => {
                if !result.stdout.is_empty() {
                    debug!("Failed to parse bandit output: {}", result.stdout);
                }
                Vec::new()
            }
        }
    }

    /// Map bandit severity to our severity
    fn map_severity(issue_severity: &str, issue_confidence: &str) -> Severity {
        let base = match issue_severity.to_uppercase().as_str() {
            "HIGH" => Severity::Critical,
            "MEDIUM" => Severity::High,
            "LOW" => Severity::Medium,
            _ => Severity::Medium,
        };

        // Downgrade if confidence is low
        if issue_confidence.to_uppercase() == "LOW" {
            match base {
                Severity::Critical => Severity::High,
                Severity::High => Severity::Medium,
                _ => base,
            }
        } else {
            base
        }
    }

    /// Create finding from bandit result
    fn create_finding(
        &self,
        result: &JsonValue,
        graph: &GraphClient,
    ) -> Option<Finding> {
        let file_path = result.get("filename")?.as_str()?;
        let line = result.get("line_number")?.as_u64()? as u32;
        let test_id = result.get("test_id")?.as_str().unwrap_or("");
        let test_name = result.get("test_name")?.as_str().unwrap_or("");
        let issue_severity = result.get("issue_severity")?.as_str().unwrap_or("MEDIUM");
        let issue_confidence = result.get("issue_confidence")?.as_str().unwrap_or("MEDIUM");
        let issue_text = result.get("issue_text")?.as_str().unwrap_or("Security issue");
        let code = result.get("code").and_then(|c| c.as_str()).unwrap_or("");

        // Convert absolute path to relative
        let rel_path = Path::new(file_path)
            .strip_prefix(&self.repository_path)
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_else(|_| file_path.to_string());

        // Get graph context
        let ctx = get_graph_context(graph, &rel_path, Some(line));
        let severity = Self::map_severity(issue_severity, issue_confidence);

        // Build description
        let mut description = format!(
            "**Security Issue**: {}\n\n\
             **Check**: {}\n\
             **Location**: {}:{}\n\
             **Severity**: {} (Confidence: {})\n",
            issue_text, test_name, rel_path, line, issue_severity, issue_confidence
        );

        if let Some(loc) = ctx.file_loc {
            description.push_str(&format!("**File Size**: {} LOC\n", loc));
        }

        if !ctx.affected_nodes.is_empty() {
            description.push_str(&format!(
                "**Affected Code**: {}\n",
                ctx.affected_nodes.iter().take(3).cloned().collect::<Vec<_>>().join(", ")
            ));
        }

        if !code.is_empty() {
            description.push_str(&format!("\n**Code Snippet**:\n```python\n{}\n```\n", code.trim()));
        }

        Some(Finding {
            id: Uuid::new_v4().to_string(),
            detector: "BanditDetector".to_string(),
            severity,
            title: format!("Security: {}", test_name.replace('_', " ")),
            description,
            affected_files: vec![PathBuf::from(&rel_path)],
            line_start: Some(line),
            line_end: Some(line),
            suggested_fix: Some(Self::suggest_fix(test_id, test_name, issue_text)),
            estimated_effort: Some(Self::estimate_effort(issue_severity)),
            category: Some("security".to_string()),
            cwe_id: Self::get_cwe(test_id),
            why_it_matters: Some(format!(
                "This security issue could expose your application to attacks. \
                 {} has {} severity and {} confidence.",
                test_name, issue_severity, issue_confidence
            )),
        })
    }

    fn suggest_fix(test_id: &str, test_name: &str, issue_text: &str) -> String {
        match test_id {
            "B201" => "Use Flask's built-in escaping or MarkupSafe for user input".to_string(),
            "B301" => "Avoid using pickle; use JSON or safer serialization".to_string(),
            "B303" => "Validate and sanitize all MD5/SHA1 usage; prefer SHA256".to_string(),
            "B304" => "Use secrets module instead of random for cryptographic purposes".to_string(),
            "B306" => "Avoid mktemp; use mkstemp or TemporaryFile instead".to_string(),
            "B311" => "Use secrets.SystemRandom() for cryptographic randomness".to_string(),
            "B501" => "Validate SSL/TLS certificates; don't use verify=False".to_string(),
            "B506" => "Use yaml.safe_load() instead of yaml.load()".to_string(),
            "B601" => "Avoid shell=True in subprocess calls; use list arguments".to_string(),
            "B602" => "Validate and sanitize shell command inputs".to_string(),
            "B608" => "Avoid SQL string concatenation; use parameterized queries".to_string(),
            _ => format!("Review security best practices for {}: {}", test_name, issue_text),
        }
    }

    fn estimate_effort(severity: &str) -> String {
        match severity.to_uppercase().as_str() {
            "HIGH" => "Medium (1-4 hours)".to_string(),
            "MEDIUM" => "Small (30-60 minutes)".to_string(),
            _ => "Small (15-30 minutes)".to_string(),
        }
    }

    fn get_cwe(test_id: &str) -> Option<String> {
        // Map common bandit tests to CWE IDs
        match test_id {
            "B301" | "B302" => Some("CWE-502".to_string()), // Deserialization
            "B303" | "B304" | "B311" => Some("CWE-330".to_string()), // Weak crypto
            "B501" | "B502" => Some("CWE-295".to_string()), // Certificate validation
            "B601" | "B602" | "B603" | "B604" => Some("CWE-78".to_string()), // Command injection
            "B608" | "B609" => Some("CWE-89".to_string()), // SQL injection
            _ => None,
        }
    }
}

impl Detector for BanditDetector {
    fn name(&self) -> &'static str {
        "BanditDetector"
    }

    fn description(&self) -> &'static str {
        "Detects security vulnerabilities in Python code using Bandit"
    }

    fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        info!("Running Bandit security scan on {:?}", self.repository_path);

        let results = self.run_bandit();

        if results.is_empty() {
            info!("No security vulnerabilities found");
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
        assert_eq!(BanditDetector::map_severity("HIGH", "HIGH"), Severity::Critical);
        assert_eq!(BanditDetector::map_severity("HIGH", "LOW"), Severity::High);
        assert_eq!(BanditDetector::map_severity("MEDIUM", "HIGH"), Severity::High);
        assert_eq!(BanditDetector::map_severity("LOW", "HIGH"), Severity::Medium);
    }
}
