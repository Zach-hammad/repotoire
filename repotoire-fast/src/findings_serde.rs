//! Fast parallel finding serialization (REPO-408)
//!
//! Replaces Python's sequential finding-to-DB conversion with parallel Rust.
//! Uses zero-copy string slicing and O(1) enum lookups.

use rayon::prelude::*;
use std::collections::HashMap;

/// Severity level mapping (matches Python FindingSeverity enum)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[repr(u8)]
pub enum Severity {
    Info = 0,
    Low = 1,
    Medium = 2,
    High = 3,
    Critical = 4,
}

impl Severity {
    /// O(1) lookup from string using match
    #[inline]
    pub fn from_str(s: &str) -> Self {
        match s {
            "CRITICAL" => Severity::Critical,
            "HIGH" => Severity::High,
            "MEDIUM" => Severity::Medium,
            "LOW" => Severity::Low,
            _ => Severity::Info,
        }
    }

    #[inline]
    pub fn as_u8(self) -> u8 {
        self as u8
    }

    #[inline]
    pub fn as_str(self) -> &'static str {
        match self {
            Severity::Critical => "CRITICAL",
            Severity::High => "HIGH",
            Severity::Medium => "MEDIUM",
            Severity::Low => "LOW",
            Severity::Info => "INFO",
        }
    }
}

/// Input finding from Python
#[derive(Debug, Clone)]
pub struct InputFinding {
    pub detector: String,
    pub severity: String,
    pub title: String,
    pub description: Option<String>,
    pub affected_files: Vec<String>,
    pub affected_nodes: Vec<String>,
    pub line_start: Option<i32>,
    pub line_end: Option<i32>,
    pub suggested_fix: Option<String>,
    pub estimated_effort: Option<String>,
    pub graph_context: Option<String>,
}

/// Output finding for database insertion
#[derive(Debug, Clone)]
pub struct OutputFinding {
    pub detector: String,
    pub severity: u8,
    pub title: String,           // Truncated to 500 chars
    pub description: Option<String>,
    pub affected_files: Vec<String>,
    pub affected_nodes: Vec<String>,
    pub line_start: Option<i32>,
    pub line_end: Option<i32>,
    pub suggested_fix: Option<String>,
    pub estimated_effort: Option<String>,
    pub graph_context: Option<String>,
}

/// Serialize a single finding for database insertion
#[inline]
fn serialize_finding(finding: InputFinding) -> OutputFinding {
    OutputFinding {
        detector: finding.detector,
        severity: Severity::from_str(&finding.severity).as_u8(),
        // Truncate title to database column limit (500 chars)
        title: if finding.title.len() > 500 {
            finding.title.chars().take(500).collect()
        } else {
            finding.title
        },
        description: finding.description,
        affected_files: finding.affected_files,
        affected_nodes: finding.affected_nodes,
        line_start: finding.line_start,
        line_end: finding.line_end,
        suggested_fix: finding.suggested_fix,
        estimated_effort: finding.estimated_effort,
        graph_context: finding.graph_context,
    }
}

/// Serialize findings in parallel for bulk database insertion.
///
/// # Arguments
/// * `findings` - Vec of input findings from Python
///
/// # Returns
/// Vec of serialized findings ready for bulk_insert_mappings
pub fn serialize_findings_batch(findings: Vec<InputFinding>) -> Vec<OutputFinding> {
    if findings.len() < 50 {
        // Sequential for small batches
        findings.into_iter().map(serialize_finding).collect()
    } else {
        // Parallel for large batches
        findings.into_par_iter().map(serialize_finding).collect()
    }
}

/// Python-friendly function that takes and returns dicts.
///
/// Input dict keys: detector, severity, title, description, affected_files,
///                  affected_nodes, line_start, line_end, suggested_fix,
///                  estimated_effort, graph_context
///
/// Output dict keys: Same, but severity is now u8 and title is truncated
pub fn serialize_findings_batch_py(
    findings: Vec<HashMap<String, serde_json::Value>>,
) -> Vec<HashMap<String, serde_json::Value>> {
    // Convert from Python dicts to InputFinding
    let input_findings: Vec<InputFinding> = findings
        .into_iter()
        .map(|f| {
            InputFinding {
                detector: f.get("detector")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                severity: f.get("severity")
                    .and_then(|v| v.as_str())
                    .unwrap_or("INFO")
                    .to_string(),
                title: f.get("title")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                description: f.get("description")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string()),
                affected_files: f.get("affected_files")
                    .and_then(|v| v.as_array())
                    .map(|arr| arr.iter()
                        .filter_map(|v| v.as_str())
                        .map(|s| s.to_string())
                        .collect())
                    .unwrap_or_default(),
                affected_nodes: f.get("affected_nodes")
                    .and_then(|v| v.as_array())
                    .map(|arr| arr.iter()
                        .filter_map(|v| v.as_str())
                        .map(|s| s.to_string())
                        .collect())
                    .unwrap_or_default(),
                line_start: f.get("line_start")
                    .and_then(|v| v.as_i64())
                    .map(|n| n as i32),
                line_end: f.get("line_end")
                    .and_then(|v| v.as_i64())
                    .map(|n| n as i32),
                suggested_fix: f.get("suggested_fix")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string()),
                estimated_effort: f.get("estimated_effort")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string()),
                graph_context: f.get("graph_context")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string()),
            }
        })
        .collect();

    // Serialize in parallel
    let output = serialize_findings_batch(input_findings);

    // Convert back to Python dicts
    output
        .into_iter()
        .map(|f| {
            let mut m = HashMap::new();
            m.insert("detector".to_string(), serde_json::Value::String(f.detector));
            m.insert("severity".to_string(), serde_json::Value::Number(f.severity.into()));
            m.insert("title".to_string(), serde_json::Value::String(f.title));

            if let Some(desc) = f.description {
                m.insert("description".to_string(), serde_json::Value::String(desc));
            }

            m.insert("affected_files".to_string(),
                serde_json::Value::Array(f.affected_files.into_iter()
                    .map(serde_json::Value::String)
                    .collect()));

            m.insert("affected_nodes".to_string(),
                serde_json::Value::Array(f.affected_nodes.into_iter()
                    .map(serde_json::Value::String)
                    .collect()));

            if let Some(line) = f.line_start {
                m.insert("line_start".to_string(), serde_json::Value::Number(line.into()));
            }
            if let Some(line) = f.line_end {
                m.insert("line_end".to_string(), serde_json::Value::Number(line.into()));
            }
            if let Some(fix) = f.suggested_fix {
                m.insert("suggested_fix".to_string(), serde_json::Value::String(fix));
            }
            if let Some(effort) = f.estimated_effort {
                m.insert("estimated_effort".to_string(), serde_json::Value::String(effort));
            }
            if let Some(ctx) = f.graph_context {
                m.insert("graph_context".to_string(), serde_json::Value::String(ctx));
            }

            m
        })
        .collect()
}

/// Validate findings before serialization (parallel validation)
pub fn validate_findings_batch(
    findings: &[InputFinding],
) -> Vec<(usize, Vec<String>)> {
    findings
        .par_iter()
        .enumerate()
        .filter_map(|(idx, f)| {
            let mut errors = Vec::new();

            if f.detector.is_empty() {
                errors.push("detector is required".to_string());
            }
            if f.title.is_empty() {
                errors.push("title is required".to_string());
            }

            if errors.is_empty() {
                None
            } else {
                Some((idx, errors))
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_severity_from_str() {
        assert_eq!(Severity::from_str("CRITICAL"), Severity::Critical);
        assert_eq!(Severity::from_str("HIGH"), Severity::High);
        assert_eq!(Severity::from_str("MEDIUM"), Severity::Medium);
        assert_eq!(Severity::from_str("LOW"), Severity::Low);
        assert_eq!(Severity::from_str("INFO"), Severity::Info);
        assert_eq!(Severity::from_str("unknown"), Severity::Info);
    }

    #[test]
    fn test_serialize_finding() {
        let finding = InputFinding {
            detector: "ruff".to_string(),
            severity: "HIGH".to_string(),
            title: "Test finding".to_string(),
            description: Some("Description".to_string()),
            affected_files: vec!["test.py".to_string()],
            affected_nodes: vec![],
            line_start: Some(10),
            line_end: Some(20),
            suggested_fix: None,
            estimated_effort: None,
            graph_context: None,
        };

        let output = serialize_finding(finding);
        assert_eq!(output.detector, "ruff");
        assert_eq!(output.severity, 3); // HIGH = 3
        assert_eq!(output.title, "Test finding");
    }

    #[test]
    fn test_title_truncation() {
        let long_title = "x".repeat(600);
        let finding = InputFinding {
            detector: "test".to_string(),
            severity: "LOW".to_string(),
            title: long_title,
            description: None,
            affected_files: vec![],
            affected_nodes: vec![],
            line_start: None,
            line_end: None,
            suggested_fix: None,
            estimated_effort: None,
            graph_context: None,
        };

        let output = serialize_finding(finding);
        assert_eq!(output.title.len(), 500);
    }

    #[test]
    fn test_batch_serialization() {
        let findings: Vec<InputFinding> = (0..100)
            .map(|i| InputFinding {
                detector: format!("detector_{}", i),
                severity: "MEDIUM".to_string(),
                title: format!("Finding {}", i),
                description: None,
                affected_files: vec![],
                affected_nodes: vec![],
                line_start: Some(i as i32),
                line_end: None,
                suggested_fix: None,
                estimated_effort: None,
                graph_context: None,
            })
            .collect();

        let output = serialize_findings_batch(findings);
        assert_eq!(output.len(), 100);
        assert!(output.iter().all(|f| f.severity == 2)); // MEDIUM = 2
    }
}
