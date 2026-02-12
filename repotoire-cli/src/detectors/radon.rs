//! Radon-based complexity and maintainability detector
//!
//! Uses radon for Python complexity analysis:
//! - Cyclomatic complexity
//! - Maintainability index

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::external_tool::{get_graph_context, run_external_tool};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use serde_json::Value as JsonValue;
use std::path::{Path, PathBuf};
use tracing::{debug, info, warn};
use uuid::Uuid;

/// Radon complexity detector
pub struct RadonDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
    complexity_threshold: u32,
    maintainability_threshold: f64,
}

impl RadonDetector {
    /// Create a new Radon detector
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            config: DetectorConfig::default(),
            repository_path: repository_path.into(),
            max_findings: 100,
            complexity_threshold: 10,
            maintainability_threshold: 65.0,
        }
    }

    /// Set maximum findings
    pub fn with_max_findings(mut self, max: usize) -> Self {
        self.max_findings = max;
        self
    }

    /// Set complexity threshold
    pub fn with_complexity_threshold(mut self, threshold: u32) -> Self {
        self.complexity_threshold = threshold;
        self
    }

    /// Set maintainability index threshold
    pub fn with_maintainability_threshold(mut self, threshold: f64) -> Self {
        self.maintainability_threshold = threshold;
        self
    }

    /// Run radon cyclomatic complexity and parse results
    fn run_radon_cc(&self) -> Vec<CcResult> {
        let cmd = vec![
            "radon".to_string(),
            "cc".to_string(),
            "--json".to_string(),
            "--min".to_string(),
            self.complexity_threshold.to_string(),
            self.repository_path.to_string_lossy().to_string(),
        ];

        let result = run_external_tool(&cmd, "radon", 60, Some(&self.repository_path), None);

        if result.timed_out {
            warn!("Radon cc timed out");
            return Vec::new();
        }

        match result.json_output() {
            Some(JsonValue::Object(obj)) => {
                let mut results = Vec::new();
                for (file_path, items) in obj {
                    if let Some(arr) = items.as_array() {
                        for item in arr {
                            results.push(CcResult {
                                file: file_path.clone(),
                                name: item
                                    .get("name")
                                    .and_then(|n| n.as_str())
                                    .unwrap_or("")
                                    .to_string(),
                                complexity: item
                                    .get("complexity")
                                    .and_then(|c| c.as_u64())
                                    .unwrap_or(0)
                                    as u32,
                                rank: item
                                    .get("rank")
                                    .and_then(|r| r.as_str())
                                    .unwrap_or("A")
                                    .to_string(),
                                lineno: item.get("lineno").and_then(|l| l.as_u64()).unwrap_or(0)
                                    as u32,
                                entity_type: item
                                    .get("type")
                                    .and_then(|t| t.as_str())
                                    .unwrap_or("function")
                                    .to_string(),
                            });
                        }
                    }
                }
                results
            }
            _ => Vec::new(),
        }
    }

    /// Run radon maintainability index and parse results
    fn run_radon_mi(&self) -> Vec<MiResult> {
        let cmd = vec![
            "radon".to_string(),
            "mi".to_string(),
            "--json".to_string(),
            "--min".to_string(),
            "C".to_string(),
            self.repository_path.to_string_lossy().to_string(),
        ];

        let result = run_external_tool(&cmd, "radon", 60, Some(&self.repository_path), None);

        if result.timed_out {
            warn!("Radon mi timed out");
            return Vec::new();
        }

        match result.json_output() {
            Some(JsonValue::Object(obj)) => obj
                .into_iter()
                .filter_map(|(file_path, data)| {
                    let mi = data.get("mi")?.as_f64()?;
                    if mi >= self.maintainability_threshold {
                        return None;
                    }
                    Some(MiResult {
                        file: file_path,
                        mi,
                        rank: data
                            .get("rank")
                            .and_then(|r| r.as_str())
                            .unwrap_or("A")
                            .to_string(),
                    })
                })
                .collect(),
            _ => Vec::new(),
        }
    }

    /// Map complexity grade to severity
    fn cc_severity(rank: &str) -> Option<Severity> {
        match rank.to_uppercase().as_str() {
            "A" | "B" => None,                 // Simple, no issue
            "C" => Some(Severity::Low),        // Somewhat complex
            "D" => Some(Severity::Medium),     // More complex
            "E" | "F" => Some(Severity::High), // Too complex
            _ => None,
        }
    }

    /// Map MI score to severity
    fn mi_severity(mi_score: f64) -> Option<Severity> {
        if mi_score >= 65.0 {
            None
        } else if mi_score >= 50.0 {
            Some(Severity::Low)
        } else if mi_score >= 25.0 {
            Some(Severity::Medium)
        } else {
            Some(Severity::High)
        }
    }

    /// Create finding from cyclomatic complexity result
    fn create_cc_finding(&self, result: &CcResult, graph: &GraphStore) -> Option<Finding> {
        let severity = Self::cc_severity(&result.rank)?;

        let rel_path = Path::new(&result.file)
            .strip_prefix(&self.repository_path)
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_else(|_| result.file.clone());

        let ctx = get_graph_context(graph, &rel_path, Some(result.lineno));

        let mut description = format!(
            "Function/method **{}** has high cyclomatic complexity.\n\n\
             **Complexity**: {} (Grade: {})\n\
             **Location**: {}:{}\n\
             **Threshold**: {} (exceeded by {})\n",
            result.name,
            result.complexity,
            result.rank,
            rel_path,
            result.lineno,
            self.complexity_threshold,
            result.complexity.saturating_sub(self.complexity_threshold)
        );

        if let Some(loc) = ctx.file_loc {
            description.push_str(&format!("**File Size**: {} LOC\n", loc));
        }

        description.push_str(
            "\n**Impact**: High complexity makes code harder to test, understand, and maintain.\n",
        );

        Some(Finding {
            id: Uuid::new_v4().to_string(),
            detector: "RadonDetector".to_string(),
            severity,
            title: format!("High complexity in {} '{}'", result.entity_type, result.name),
            description,
            affected_files: vec![PathBuf::from(&rel_path)],
            line_start: Some(result.lineno),
            line_end: Some(result.lineno),
            suggested_fix: Some(format!(
                "Refactor '{}' to reduce complexity (current: {}, target: <{})",
                result.name, result.complexity, self.complexity_threshold
            )),
            estimated_effort: Some(Self::estimate_cc_effort(result.complexity)),
            category: Some("complexity".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "High cyclomatic complexity increases bug risk and makes code harder to test comprehensively.".to_string()
            ),
            ..Default::default()
        })
    }

    /// Create finding from maintainability index result
    fn create_mi_finding(&self, result: &MiResult, graph: &GraphStore) -> Option<Finding> {
        let severity = Self::mi_severity(result.mi)?;

        let rel_path = Path::new(&result.file)
            .strip_prefix(&self.repository_path)
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_else(|_| result.file.clone());

        let ctx = get_graph_context(graph, &rel_path, None);

        let mut description = format!(
            "File has low maintainability index.\n\n\
             **MI Score**: {:.1}/100 (Grade: {})\n\
             **File**: {}\n\
             **Target**: {}+ (deficit: {:.1})\n",
            result.mi,
            result.rank,
            rel_path,
            self.maintainability_threshold,
            self.maintainability_threshold - result.mi
        );

        if let Some(loc) = ctx.file_loc {
            description.push_str(&format!("**File Size**: {} LOC\n", loc));
        }

        description.push_str(
            "\n**Impact**: Low maintainability increases bug risk and slows development.\n",
        );

        Some(Finding {
            id: Uuid::new_v4().to_string(),
            detector: "RadonDetector".to_string(),
            severity,
            title: format!("Low maintainability index ({:.1}/100)", result.mi),
            description,
            affected_files: vec![PathBuf::from(&rel_path)],
            line_start: None,
            line_end: None,
            suggested_fix: Some(format!(
                "Improve code maintainability (current MI: {:.1}, target: >{})",
                result.mi, self.maintainability_threshold
            )),
            estimated_effort: Some(Self::estimate_mi_effort(result.mi)),
            category: Some("maintainability".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Low maintainability makes the codebase harder to understand and modify, increasing technical debt.".to_string()
            ),
            ..Default::default()
        })
    }

    fn estimate_cc_effort(complexity: u32) -> String {
        if complexity < 15 {
            "Small (1-2 hours)".to_string()
        } else if complexity < 25 {
            "Medium (half day)".to_string()
        } else {
            "Large (1-2 days)".to_string()
        }
    }

    fn estimate_mi_effort(mi_score: f64) -> String {
        if mi_score >= 50.0 {
            "Small (half day)".to_string()
        } else if mi_score >= 25.0 {
            "Medium (1-2 days)".to_string()
        } else {
            "Large (3-5 days)".to_string()
        }
    }
}

/// Cyclomatic complexity result
struct CcResult {
    file: String,
    name: String,
    complexity: u32,
    rank: String,
    lineno: u32,
    entity_type: String,
}

/// Maintainability index result
struct MiResult {
    file: String,
    mi: f64,
    rank: String,
}

impl Detector for RadonDetector {
    fn name(&self) -> &'static str {
        "RadonDetector"
    }

    fn description(&self) -> &'static str {
        "Detects complexity and maintainability issues in Python using radon"
    }

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        info!("Running Radon on {:?}", self.repository_path);

        let cc_results = self.run_radon_cc();
        let mi_results = self.run_radon_mi();

        let mut findings = Vec::new();

        // Process CC findings
        for result in cc_results.iter().take(self.max_findings / 2) {
            if let Some(finding) = self.create_cc_finding(result, graph) {
                findings.push(finding);
            }
        }

        // Process MI findings
        for result in mi_results.iter().take(self.max_findings / 2) {
            if let Some(finding) = self.create_mi_finding(result, graph) {
                findings.push(finding);
            }
        }

        info!(
            "Created {} complexity/maintainability findings",
            findings.len()
        );
        Ok(findings.into_iter().take(self.max_findings).collect())
    }

    fn category(&self) -> &'static str {
        "complexity"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cc_severity() {
        assert!(RadonDetector::cc_severity("A").is_none());
        assert!(RadonDetector::cc_severity("B").is_none());
        assert_eq!(RadonDetector::cc_severity("C"), Some(Severity::Low));
        assert_eq!(RadonDetector::cc_severity("D"), Some(Severity::Medium));
        assert_eq!(RadonDetector::cc_severity("E"), Some(Severity::High));
    }

    #[test]
    fn test_mi_severity() {
        assert!(RadonDetector::mi_severity(70.0).is_none());
        assert_eq!(RadonDetector::mi_severity(60.0), Some(Severity::Low));
        assert_eq!(RadonDetector::mi_severity(40.0), Some(Severity::Medium));
        assert_eq!(RadonDetector::mi_severity(20.0), Some(Severity::High));
    }
}
