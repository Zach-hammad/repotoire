//! Core data models for Repotoire
//!
//! These models are used throughout the codebase for representing
//! code entities, findings, and analysis results.

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

/// Generate a deterministic finding ID based on content hash.
///
/// This ensures findings have stable IDs across runs, enabling:
/// - Tracking findings over time (fixed vs new vs recurring)
/// - Suppression by ID in config files
/// - Reliable deduplication
///
/// The ID is a 16-character hex string derived from hashing:
/// - detector name (which detector found it)
/// - file path (where it was found)
/// - line number (specific location)
/// - title (what the issue is)
pub fn deterministic_finding_id(detector: &str, file: &str, line: u32, _title: &str) -> String {
    // Note: postprocessing overwrites all IDs via finding_id() (#73).
    // Cache invalidates on binary version change (#66), so DefaultHasher instability is fine.
    crate::detectors::base::finding_id(detector, file, line)
}

/// Severity levels for findings
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize, Default,
)]
#[serde(rename_all = "lowercase")]
pub enum Severity {
    #[default]
    Info,
    Low,
    Medium,
    High,
    Critical,
}

impl std::fmt::Display for Severity {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Severity::Info => write!(f, "info"),
            Severity::Low => write!(f, "low"),
            Severity::Medium => write!(f, "medium"),
            Severity::High => write!(f, "high"),
            Severity::Critical => write!(f, "critical"),
        }
    }
}

/// Deserialize a HashMap that may be `null` in JSON (treat null as empty map)
fn deserialize_null_as_empty_map<'de, D>( // repotoire:ignore[surprisal]
    deserializer: D,
) -> Result<std::collections::HashMap<String, String>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let opt: Option<std::collections::HashMap<String, String>> =
        Option::deserialize(deserializer)?;
    Ok(opt.unwrap_or_default())
}

/// A code smell or issue finding
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct Finding {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub detector: String,
    #[serde(default)]
    pub severity: Severity,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub affected_files: Vec<PathBuf>,
    #[serde(default)]
    pub line_start: Option<u32>,
    #[serde(default)]
    pub line_end: Option<u32>,
    #[serde(default)]
    pub suggested_fix: Option<String>,
    #[serde(default)]
    pub estimated_effort: Option<String>,
    #[serde(default)]
    pub category: Option<String>,
    #[serde(default)]
    pub cwe_id: Option<String>,
    #[serde(default)]
    pub why_it_matters: Option<String>,
    /// Confidence score from 0.0 to 1.0 (set by voting engine or detector)
    #[serde(default)]
    pub confidence: Option<f64>,
    /// Threshold metadata for adaptive explainability
    /// Keys: threshold_source, effective_threshold, actual_value, default_threshold
    #[serde(default, skip_serializing_if = "std::collections::HashMap::is_empty", deserialize_with = "deserialize_null_as_empty_map")]
    pub threshold_metadata: std::collections::HashMap<String, String>,
}

/// Summary of findings by severity
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct FindingsSummary {
    pub critical: usize,
    pub high: usize,
    pub medium: usize,
    pub low: usize,
    pub info: usize,
    pub total: usize,
}

impl FindingsSummary {
    pub fn from_findings(findings: &[Finding]) -> Self {
        let mut summary = Self::default();
        for f in findings {
            match f.severity {
                Severity::Critical => summary.critical += 1,
                Severity::High => summary.high += 1,
                Severity::Medium => summary.medium += 1,
                Severity::Low => summary.low += 1,
                Severity::Info => summary.info += 1,
            }
            summary.total += 1;
        }
        summary
    }
}

/// Overall health report for a codebase
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HealthReport {
    pub overall_score: f64,
    pub grade: String,
    pub structure_score: f64,
    pub quality_score: f64,
    pub architecture_score: Option<f64>,
    pub findings: Vec<Finding>,
    pub findings_summary: FindingsSummary,
    pub total_files: usize,
    pub total_functions: usize,
    pub total_classes: usize,
    pub total_loc: usize,
}

impl HealthReport {
    /// Calculate grade from score
    pub fn grade_from_score(score: f64) -> String {
        match score {
            s if s >= 90.0 => "A".to_string(),
            s if s >= 80.0 => "B".to_string(),
            s if s >= 70.0 => "C".to_string(),
            s if s >= 60.0 => "D".to_string(),
            _ => "F".to_string(),
        }
    }
}

/// A function in the code graph
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Function {
    pub name: String,
    pub qualified_name: String,
    pub file_path: PathBuf,
    pub line_start: u32,
    pub line_end: u32,
    pub parameters: Vec<String>,
    pub return_type: Option<String>,
    pub is_async: bool,
    pub complexity: Option<u32>,
    /// Maximum nesting depth within this function
    pub max_nesting: Option<u32>,
    /// Doc comment (Javadoc, JSDoc, Go doc, etc.)
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub doc_comment: Option<String>,
    /// Annotations/decorators (e.g., Java @Override, @Deprecated)
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub annotations: Vec<String>,
}

/// A class in the code graph
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Class {
    pub name: String,
    pub qualified_name: String,
    pub file_path: PathBuf,
    pub line_start: u32,
    pub line_end: u32,
    pub methods: Vec<String>,
    pub bases: Vec<String>,
    /// Doc comment (Javadoc, JSDoc, Go doc, etc.)
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub doc_comment: Option<String>,
    /// Annotations/decorators (e.g., Java @Override, @Deprecated)
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub annotations: Vec<String>,
}

/// A file in the code graph
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct File {
    pub path: PathBuf,
    pub language: String,
    pub lines_of_code: usize,
    pub functions: usize,
    pub classes: usize,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_finding_serde_round_trip() {
        let finding = Finding {
            id: "test-1".into(),
            detector: "TestDetector".into(),
            severity: Severity::High,
            title: "Test finding".into(),
            description: "A test".into(),
            threshold_metadata: {
                let mut m = std::collections::HashMap::new();
                m.insert("key".into(), "value".into());
                m
            },
            ..Default::default()
        };
        let json = serde_json::to_string(&finding).unwrap();
        let back: Finding = serde_json::from_str(&json).unwrap();
        assert_eq!(back.id, "test-1");
        assert_eq!(back.threshold_metadata.get("key").unwrap(), "value");
    }

    #[test]
    fn test_finding_deserialize_null_threshold_metadata() {
        let json = r#"{"id":"t1","detector":"D","severity":"high","title":"T","description":"","affected_files":[],"threshold_metadata":null}"#;
        let finding: Finding = serde_json::from_str(json).unwrap();
        assert!(finding.threshold_metadata.is_empty());
    }

    #[test]
    fn test_finding_deserialize_missing_threshold_metadata() {
        let json = r#"{"id":"t1","detector":"D","severity":"high","title":"T","description":"","affected_files":[]}"#;
        let finding: Finding = serde_json::from_str(json).unwrap();
        assert!(finding.threshold_metadata.is_empty());
    }

    #[test]
    fn test_health_report_grade_from_score() {
        assert_eq!(HealthReport::grade_from_score(95.0), "A");
        assert_eq!(HealthReport::grade_from_score(85.0), "B");
        assert_eq!(HealthReport::grade_from_score(75.0), "C");
        assert_eq!(HealthReport::grade_from_score(65.0), "D");
        assert_eq!(HealthReport::grade_from_score(50.0), "F");
    }

    #[test]
    fn test_findings_summary_from_findings() {
        let findings = vec![
            Finding { severity: Severity::Critical, ..Default::default() },
            Finding { severity: Severity::High, ..Default::default() },
            Finding { severity: Severity::High, ..Default::default() },
            Finding { severity: Severity::Medium, ..Default::default() },
            Finding { severity: Severity::Low, ..Default::default() },
        ];
        let summary = FindingsSummary::from_findings(&findings);
        assert_eq!(summary.critical, 1);
        assert_eq!(summary.high, 2);
        assert_eq!(summary.medium, 1);
        assert_eq!(summary.low, 1);
        assert_eq!(summary.total, 5);
    }
}
