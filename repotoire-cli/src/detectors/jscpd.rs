//! jscpd-based duplicate code detector
//!
//! Uses jscpd for fast duplicate code detection across multiple languages.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::external_tool::{get_graph_context, get_js_exec_command, run_external_tool};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use serde_json::Value as JsonValue;
use std::path::{Path, PathBuf};
use tracing::{debug, info, warn};
use uuid::Uuid;

/// jscpd duplicate code detector
pub struct JscpdDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
    min_lines: u32,
    min_tokens: u32,
    threshold: f64,
    ignore_patterns: Vec<String>,
}

impl JscpdDetector {
    /// Create a new jscpd detector
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            config: DetectorConfig::default(),
            repository_path: repository_path.into(),
            max_findings: 50,
            min_lines: 5,
            min_tokens: 50,
            threshold: 10.0,
            ignore_patterns: vec![
                "**/node_modules/**".to_string(),
                "**/.venv/**".to_string(),
                "**/venv/**".to_string(),
                "**/__pycache__/**".to_string(),
                "**/*.pyc".to_string(),
                "**/.git/**".to_string(),
                "**/dist/**".to_string(),
                "**/build/**".to_string(),
            ],
        }
    }

    /// Set maximum findings
    pub fn with_max_findings(mut self, max: usize) -> Self {
        self.max_findings = max;
        self
    }

    /// Set minimum lines for duplicate detection
    pub fn with_min_lines(mut self, lines: u32) -> Self {
        self.min_lines = lines;
        self
    }

    /// Set minimum tokens for duplicate detection
    pub fn with_min_tokens(mut self, tokens: u32) -> Self {
        self.min_tokens = tokens;
        self
    }

    /// Set duplication threshold percentage
    pub fn with_threshold(mut self, threshold: f64) -> Self {
        self.threshold = threshold;
        self
    }

    /// Run jscpd and parse results
    fn run_jscpd(&self) -> Vec<Duplicate> {
        // Create temp directory for output
        let temp_dir = match TempDir::new() {
            Ok(dir) => dir,
            Err(e) => {
                warn!("Failed to create temp directory: {}", e);
                return Vec::new();
            }
        };

        let mut cmd = get_js_exec_command("jscpd");
        cmd.extend(vec![
            "--reporters".to_string(),
            "json".to_string(),
            "--output".to_string(),
            temp_dir.path().to_string_lossy().to_string(),
            "--min-lines".to_string(),
            self.min_lines.to_string(),
            "--min-tokens".to_string(),
            self.min_tokens.to_string(),
            "--threshold".to_string(),
            self.threshold.to_string(),
        ]);

        if !self.ignore_patterns.is_empty() {
            cmd.push("--ignore".to_string());
            cmd.push(self.ignore_patterns.join(","));
        }

        cmd.push(".".to_string());

        let result = run_external_tool(&cmd, "jscpd", 120, Some(&self.repository_path), None);

        if result.timed_out {
            warn!("jscpd timed out");
            return Vec::new();
        }

        // Read JSON report
        let report_path = temp_dir.path().join("jscpd-report.json");
        let report_content = match std::fs::read_to_string(&report_path) {
            Ok(content) => content,
            Err(e) => {
                debug!("Failed to read jscpd report: {}", e);
                return Vec::new();
            }
        };

        let report: JsonValue = match serde_json::from_str(&report_content) {
            Ok(json) => json,
            Err(e) => {
                warn!("Failed to parse jscpd JSON: {}", e);
                return Vec::new();
            }
        };

        let duplicates = report.get("duplicates").and_then(|d| d.as_array()).cloned().unwrap_or_default();

        duplicates
            .into_iter()
            .filter_map(|d| {
                let lines = d.get("lines")?.as_u64()? as u32;
                let first = d.get("firstFile")?;
                let second = d.get("secondFile")?;

                Some(Duplicate {
                    lines,
                    file1: first.get("name")?.as_str()?.to_string(),
                    file1_start: first.get("startLoc").and_then(|l| l.get("line")).and_then(|l| l.as_u64()).unwrap_or(0) as u32,
                    file1_end: first.get("endLoc").and_then(|l| l.get("line")).and_then(|l| l.as_u64()).unwrap_or(0) as u32,
                    file2: second.get("name")?.as_str()?.to_string(),
                    file2_start: second.get("startLoc").and_then(|l| l.get("line")).and_then(|l| l.as_u64()).unwrap_or(0) as u32,
                    file2_end: second.get("endLoc").and_then(|l| l.get("line")).and_then(|l| l.as_u64()).unwrap_or(0) as u32,
                })
            })
            .collect()
    }

    /// Map duplication size to severity
    fn map_severity(lines: u32) -> Severity {
        if lines >= 50 {
            Severity::High
        } else if lines >= 20 {
            Severity::Medium
        } else {
            Severity::Low
        }
    }

    /// Create finding from duplicate
    fn create_finding(&self, dup: &Duplicate, graph: &GraphStore) -> Finding {
        let severity = Self::map_severity(dup.lines);

        // Get graph context for both files
        let ctx1 = get_graph_context(graph, &dup.file1, Some(dup.file1_start));
        let ctx2 = get_graph_context(graph, &dup.file2, Some(dup.file2_start));

        let mut description = format!("Found {} lines of duplicated code.\n\n", dup.lines);
        description.push_str(&format!(
            "**Location 1**: {}:{}-{}\n",
            dup.file1, dup.file1_start, dup.file1_end
        ));
        if let Some(loc) = ctx1.file_loc {
            description.push_str(&format!("  - File Size: {} LOC\n", loc));
        }

        description.push_str(&format!(
            "\n**Location 2**: {}:{}-{}\n",
            dup.file2, dup.file2_start, dup.file2_end
        ));
        if let Some(loc) = ctx2.file_loc {
            description.push_str(&format!("  - File Size: {} LOC\n", loc));
        }

        description.push_str("\n**Impact**: Code duplication increases maintenance burden and bug risk.\n");

        let suggested_fix = if dup.lines >= 50 {
            "Extract large duplicated block into a shared utility function or class".to_string()
        } else if dup.lines >= 20 {
            "Refactor duplicated code into a shared helper function".to_string()
        } else {
            "Consider extracting common logic to reduce duplication".to_string()
        };

        let effort = if dup.lines >= 50 {
            "Medium (half day)"
        } else if dup.lines >= 20 {
            "Small (1-2 hours)"
        } else {
            "Small (30 minutes - 1 hour)"
        };

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "JscpdDetector".to_string(),
            severity,
            title: format!("Duplicate code: {} lines duplicated", dup.lines),
            description,
            affected_files: vec![PathBuf::from(&dup.file1), PathBuf::from(&dup.file2)],
            line_start: Some(dup.file1_start),
            line_end: Some(dup.file1_end),
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some(effort.to_string()),
            category: Some("duplication".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Duplicated code violates DRY (Don't Repeat Yourself) and makes maintenance harder. \
                 Bugs fixed in one location may not be fixed in duplicates.".to_string()
            ),
        }
    }
}

/// Duplicate code result
struct Duplicate {
    lines: u32,
    file1: String,
    file1_start: u32,
    file1_end: u32,
    file2: String,
    file2_start: u32,
    file2_end: u32,
}

impl Detector for JscpdDetector {
    fn name(&self) -> &'static str {
        "JscpdDetector"
    }

    fn description(&self) -> &'static str {
        "Detects duplicate code using jscpd"
    }

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        info!("Running jscpd on {:?}", self.repository_path);

        let duplicates = self.run_jscpd();

        if duplicates.is_empty() {
            info!("No code duplications found");
            return Ok(Vec::new());
        }

        let findings: Vec<Finding> = duplicates
            .iter()
            .take(self.max_findings)
            .map(|d| self.create_finding(d, graph))
            .collect();

        info!("Created {} duplicate code findings", findings.len());
        Ok(findings)
    }

    fn category(&self) -> &'static str {
        "duplication"
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
        assert_eq!(JscpdDetector::map_severity(60), Severity::High);
        assert_eq!(JscpdDetector::map_severity(30), Severity::Medium);
        assert_eq!(JscpdDetector::map_severity(10), Severity::Low);
    }
}
