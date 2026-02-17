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
pub fn deterministic_finding_id(detector: &str, file: &str, line: u32, title: &str) -> String {
    // Use MD5 for stable cross-version hashing (#33).
    // DefaultHasher is intentionally not stable across Rust/compiler versions.
    let input = format!("{detector}\n{file}\n{line}\n{title}");
    let digest = md5::compute(input.as_bytes());
    format!("{:x}", digest)[..16].to_string()
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
