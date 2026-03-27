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
    // Uses FNV-1a for cross-toolchain stability (see finding_id).
    crate::detectors::base::finding_id(detector, file, line)
}

/// Severity levels for findings
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize, Default,
    clap::ValueEnum,
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

impl std::str::FromStr for Severity {
    type Err = anyhow::Error;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.to_lowercase().as_str() {
            "critical" => Ok(Severity::Critical),
            "high" => Ok(Severity::High),
            "medium" => Ok(Severity::Medium),
            "low" => Ok(Severity::Low),
            "info" => Ok(Severity::Info),
            _ => Err(anyhow::anyhow!(
                "Unknown severity '{}'. Valid: critical, high, medium, low, info",
                s
            )),
        }
    }
}

/// Deserialize a BTreeMap that may be `null` in JSON (treat null as empty map)
fn deserialize_null_as_empty_map<'de, D>( // repotoire:ignore[surprisal]
    deserializer: D,
) -> Result<std::collections::BTreeMap<String, String>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let opt: Option<std::collections::BTreeMap<String, String>> =
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
    /// Whether this finding was produced by a deterministic (mathematically provable) detector.
    /// Deterministic findings bypass statistical FP classifiers.
    #[serde(default)]
    pub deterministic: bool,
    /// Threshold metadata for adaptive explainability
    /// Keys: threshold_source, effective_threshold, actual_value, default_threshold
    #[serde(default, skip_serializing_if = "std::collections::BTreeMap::is_empty", deserialize_with = "deserialize_null_as_empty_map")]
    pub threshold_metadata: std::collections::BTreeMap<String, String>,
}

/// Default confidence value when no category-specific default applies.
const DEFAULT_CONFIDENCE: f64 = 0.70;

impl Finding {
    /// Set a default confidence value if none has been set by a detector.
    ///
    /// This is a builder-style method: it returns `self` so it can be chained.
    /// If `self.confidence` is already `Some(_)`, the value is left untouched.
    pub fn with_default_confidence(mut self, default: f64) -> Self {
        if self.confidence.is_none() {
            self.confidence = Some(default);
        }
        self
    }

    /// Return the effective confidence for this finding.
    ///
    /// If a detector or the postprocess pipeline has set an explicit confidence,
    /// that value is returned. Otherwise falls back to 0.70.
    pub fn effective_confidence(&self) -> f64 {
        self.confidence.unwrap_or(DEFAULT_CONFIDENCE)
    }

    /// Return the default confidence for a finding based on its category string.
    ///
    /// | Category          | Default | Rationale                                  |
    /// |-------------------|---------|--------------------------------------------|
    /// | "architecture"    | 0.85    | Structural evidence is strong              |
    /// | "security"        | 0.75    | Taint analysis is good but not perfect     |
    /// | "design"          | 0.65    | Code smell detection has higher FP rate     |
    /// | "dead-code"/"dead_code" | 0.70 | Graph-based but may miss dynamic dispatch |
    /// | "ai_watchdog"     | 0.60    | Heuristic detection                        |
    /// | Others            | 0.70    | Reasonable default                         |
    pub fn default_confidence_for_category(category: Option<&str>) -> f64 {
        match category {
            Some("architecture") => 0.85,
            Some("security") => 0.75,
            Some("design") => 0.65,
            Some("dead-code") | Some("dead_code") => 0.70,
            Some("ai_watchdog") => 0.60,
            _ => DEFAULT_CONFIDENCE,
        }
    }
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

/// Letter grades for code health (13 levels: A+ through F).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize, Default)]
pub enum Grade {
    #[default]
    F,
    #[serde(rename = "D-")]
    DMinus,
    D,
    #[serde(rename = "D+")]
    DPlus,
    #[serde(rename = "C-")]
    CMinus,
    C,
    #[serde(rename = "C+")]
    CPlus,
    #[serde(rename = "B-")]
    BMinus,
    B,
    #[serde(rename = "B+")]
    BPlus,
    #[serde(rename = "A-")]
    AMinus,
    A,
    #[serde(rename = "A+")]
    APlus,
}

impl std::fmt::Display for Grade {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Grade::APlus => write!(f, "A+"),
            Grade::A => write!(f, "A"),
            Grade::AMinus => write!(f, "A-"),
            Grade::BPlus => write!(f, "B+"),
            Grade::B => write!(f, "B"),
            Grade::BMinus => write!(f, "B-"),
            Grade::CPlus => write!(f, "C+"),
            Grade::C => write!(f, "C"),
            Grade::CMinus => write!(f, "C-"),
            Grade::DPlus => write!(f, "D+"),
            Grade::D => write!(f, "D"),
            Grade::DMinus => write!(f, "D-"),
            Grade::F => write!(f, "F"),
        }
    }
}

impl std::str::FromStr for Grade {
    type Err = anyhow::Error;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s {
            "A+" => Ok(Grade::APlus),
            "A" => Ok(Grade::A),
            "A-" => Ok(Grade::AMinus),
            "B+" => Ok(Grade::BPlus),
            "B" => Ok(Grade::B),
            "B-" => Ok(Grade::BMinus),
            "C+" => Ok(Grade::CPlus),
            "C" => Ok(Grade::C),
            "C-" => Ok(Grade::CMinus),
            "D+" => Ok(Grade::DPlus),
            "D" => Ok(Grade::D),
            "D-" => Ok(Grade::DMinus),
            "F" => Ok(Grade::F),
            _ => Err(anyhow::anyhow!("Unknown grade '{}'", s)),
        }
    }
}

/// Overall health report for a codebase
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HealthReport {
    pub overall_score: f64,
    pub grade: Grade,
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
    /// Number of struct fields (Rust named/tuple) or enum variants.
    #[serde(default)]
    pub field_count: usize,
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
                let mut m = std::collections::BTreeMap::new();
                m.insert("key".into(), "value".into());
                m
            },
            ..Default::default()
        };
        let json = serde_json::to_string(&finding).expect("serialize finding");
        let back: Finding = serde_json::from_str(&json).expect("deserialize finding");
        assert_eq!(back.id, "test-1");
        assert_eq!(back.threshold_metadata.get("key").expect("key exists"), "value");
    }

    #[test]
    fn test_finding_deserialize_null_threshold_metadata() {
        let json = r#"{"id":"t1","detector":"D","severity":"high","title":"T","description":"","affected_files":[],"threshold_metadata":null}"#;
        let finding: Finding = serde_json::from_str(json).expect("deserialize finding with null metadata");
        assert!(finding.threshold_metadata.is_empty());
    }

    #[test]
    fn test_finding_deserialize_missing_threshold_metadata() {
        let json = r#"{"id":"t1","detector":"D","severity":"high","title":"T","description":"","affected_files":[]}"#;
        let finding: Finding = serde_json::from_str(json).expect("deserialize finding with missing metadata");
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

    // ── with_default_confidence ────────────────────────────────────

    #[test]
    fn test_with_default_confidence_sets_when_none() {
        let finding = Finding { confidence: None, ..Default::default() };
        let finding = finding.with_default_confidence(0.85);
        assert_eq!(finding.confidence, Some(0.85));
    }

    #[test]
    fn test_with_default_confidence_preserves_existing() {
        let finding = Finding { confidence: Some(0.90), ..Default::default() };
        let finding = finding.with_default_confidence(0.50);
        assert_eq!(finding.confidence, Some(0.90));
    }

    // ── effective_confidence ────────────────────────────────────────

    #[test]
    fn test_effective_confidence_returns_set_value() {
        let finding = Finding { confidence: Some(0.42), ..Default::default() };
        assert!((finding.effective_confidence() - 0.42).abs() < f64::EPSILON);
    }

    #[test]
    fn test_effective_confidence_returns_default_when_none() {
        let finding = Finding { confidence: None, ..Default::default() };
        assert!((finding.effective_confidence() - 0.70).abs() < f64::EPSILON);
    }

    // ── default_confidence_for_category ─────────────────────────────

    #[test]
    fn test_default_confidence_architecture() {
        assert!((Finding::default_confidence_for_category(Some("architecture")) - 0.85).abs() < f64::EPSILON);
    }

    #[test]
    fn test_default_confidence_security() {
        assert!((Finding::default_confidence_for_category(Some("security")) - 0.75).abs() < f64::EPSILON);
    }

    #[test]
    fn test_default_confidence_design() {
        assert!((Finding::default_confidence_for_category(Some("design")) - 0.65).abs() < f64::EPSILON);
    }

    #[test]
    fn test_default_confidence_dead_code_hyphen() {
        assert!((Finding::default_confidence_for_category(Some("dead-code")) - 0.70).abs() < f64::EPSILON);
    }

    #[test]
    fn test_default_confidence_dead_code_underscore() {
        assert!((Finding::default_confidence_for_category(Some("dead_code")) - 0.70).abs() < f64::EPSILON);
    }

    #[test]
    fn test_default_confidence_ai_watchdog() {
        assert!((Finding::default_confidence_for_category(Some("ai_watchdog")) - 0.60).abs() < f64::EPSILON);
    }

    #[test]
    fn test_default_confidence_unknown_category() {
        assert!((Finding::default_confidence_for_category(Some("testing")) - 0.70).abs() < f64::EPSILON);
    }

    #[test]
    fn test_default_confidence_none_category() {
        assert!((Finding::default_confidence_for_category(None) - 0.70).abs() < f64::EPSILON);
    }
}
