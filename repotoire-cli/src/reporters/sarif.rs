//! SARIF 2.1.0 reporter for GitHub Code Scanning and VS Code integration
//!
//! Generates SARIF (Static Analysis Results Interchange Format) output
//! compliant with OASIS SARIF 2.1.0 specification.
//!
//! Reference: https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html

use crate::models::{Finding, HealthReport, Severity};
use anyhow::Result;
use chrono::Utc;
use serde::Serialize;
use std::collections::HashMap;

/// SARIF schema URI
const SARIF_SCHEMA: &str =
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json";
const SARIF_VERSION: &str = "2.1.0";

/// Map Repotoire severity to SARIF level
fn severity_to_sarif_level(severity: &Severity) -> &'static str {
    match severity {
        Severity::Critical | Severity::High => "error",
        Severity::Medium => "warning",
        Severity::Low | Severity::Info => "note",
    }
}

/// Map severity to security-severity score (0.0 - 10.0) for GitHub Code Scanning
fn severity_to_security_score(severity: &Severity) -> f64 {
    match severity {
        Severity::Critical => 9.5,
        Severity::High => 7.5,
        Severity::Medium => 5.0,
        Severity::Low => 2.5,
        Severity::Info => 1.0,
    }
}

// ============================================================================
// SARIF Data Structures
// ============================================================================

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SarifReport {
    #[serde(rename = "$schema")]
    schema: String,
    version: String,
    runs: Vec<SarifRun>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SarifRun {
    tool: SarifTool,
    results: Vec<SarifResult>,
    invocations: Vec<SarifInvocation>,
    #[serde(skip_serializing_if = "Option::is_none")]
    original_uri_base_ids: Option<HashMap<String, SarifArtifactLocation>>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SarifTool {
    driver: SarifDriver,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SarifDriver {
    name: String,
    version: String,
    information_uri: String,
    rules: Vec<SarifRule>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SarifRule {
    id: String,
    name: String,
    short_description: SarifMessage,
    full_description: SarifMessage,
    default_configuration: SarifConfiguration,
    properties: SarifRuleProperties,
    help_uri: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SarifConfiguration {
    level: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SarifRuleProperties {
    tags: Vec<String>,
    #[serde(rename = "security-severity")]
    security_severity: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SarifResult {
    rule_id: String,
    level: String,
    message: SarifMessage,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    locations: Vec<SarifLocation>,
    fingerprints: HashMap<String, String>,
    properties: SarifResultProperties,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    fixes: Vec<SarifFix>,
    /// Confidence ranking from 0.0 (lowest) to 100.0 (highest)
    /// See SARIF 2.1.0 spec §3.27.28
    #[serde(skip_serializing_if = "Option::is_none")]
    rank: Option<f64>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SarifLocation {
    physical_location: SarifPhysicalLocation,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SarifPhysicalLocation {
    artifact_location: SarifArtifactLocation,
    #[serde(skip_serializing_if = "Option::is_none")]
    region: Option<SarifRegion>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SarifArtifactLocation {
    uri: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    uri_base_id: Option<String>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SarifRegion {
    start_line: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    end_line: Option<u32>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SarifMessage {
    text: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SarifInvocation {
    execution_successful: bool,
    end_time_utc: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    tool_execution_notifications: Vec<SarifNotification>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SarifNotification {
    level: String,
    message: SarifMessage,
    descriptor: SarifDescriptor,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SarifDescriptor {
    id: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SarifResultProperties {
    severity: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    suggested_fix: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    estimated_effort: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    category: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    cwe_id: Option<String>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SarifFix {
    description: SarifMessage,
}

// ============================================================================
// Implementation
// ============================================================================

/// Render report as SARIF 2.1.0 JSON
pub fn render(report: &HealthReport) -> Result<String> {
    let sarif = build_sarif(report);
    Ok(serde_json::to_string_pretty(&sarif)?)
}

/// Build the complete SARIF document
fn build_sarif(report: &HealthReport) -> SarifReport {
    // Group findings by detector to create rules
    let mut findings_by_detector: HashMap<String, Vec<&Finding>> = HashMap::new();
    for finding in &report.findings {
        findings_by_detector
            .entry(finding.detector.clone())
            .or_default()
            .push(finding);
    }

    // Build rules from detectors
    let rules: Vec<SarifRule> = findings_by_detector
        .iter()
        .map(|(detector, findings)| build_rule(detector, findings))
        .collect();

    // Build results from findings
    let results: Vec<SarifResult> = report
        .findings
        .iter()
        .enumerate()
        .map(|(i, f)| build_result(f, i))
        .collect();

    SarifReport {
        schema: SARIF_SCHEMA.to_string(),
        version: SARIF_VERSION.to_string(),
        runs: vec![SarifRun {
            tool: SarifTool {
                driver: SarifDriver {
                    name: "Repotoire".to_string(),
                    version: env!("CARGO_PKG_VERSION").to_string(),
                    information_uri: "https://repotoire.com".to_string(),
                    rules,
                },
            },
            results,
            invocations: vec![SarifInvocation {
                execution_successful: true,
                end_time_utc: Utc::now().to_rfc3339(),
                tool_execution_notifications: vec![SarifNotification {
                    level: "note".to_string(),
                    message: SarifMessage {
                        text: format!(
                            "Analysis complete. Grade: {}, Score: {:.1}/100",
                            report.grade, report.overall_score
                        ),
                    },
                    descriptor: SarifDescriptor {
                        id: "summary".to_string(),
                    },
                }],
            }],
            original_uri_base_ids: None,
        }],
    }
}

/// Build a SARIF rule from a detector
fn build_rule(detector: &str, findings: &[&Finding]) -> SarifRule {
    // Get max severity from findings
    let max_severity = findings
        .iter()
        .map(|f| &f.severity)
        .max()
        .unwrap_or(&Severity::Info);

    let rule_id = normalize_rule_id(detector);
    let name = detector.replace("Detector", "");
    let description = get_detector_description(detector);
    let tags = get_detector_tags(detector);

    SarifRule {
        id: rule_id.clone(),
        name: name.clone(),
        short_description: SarifMessage {
            text: format!("Issue detected by {}", name),
        },
        full_description: SarifMessage { text: description },
        default_configuration: SarifConfiguration {
            level: severity_to_sarif_level(max_severity).to_string(),
        },
        properties: SarifRuleProperties {
            tags,
            security_severity: format!("{:.1}", severity_to_security_score(max_severity)),
        },
        help_uri: format!(
            "https://repotoire.com/docs/detectors/{}",
            rule_id.to_lowercase()
        ),
    }
}

/// Build a SARIF result from a finding
fn build_result(finding: &Finding, index: usize) -> SarifResult {
    let rule_id = normalize_rule_id(&finding.detector);

    // Build locations
    let locations: Vec<SarifLocation> = finding
        .affected_files
        .iter()
        .map(|file| SarifLocation {
            physical_location: SarifPhysicalLocation {
                artifact_location: SarifArtifactLocation {
                    uri: file.display().to_string(),
                    uri_base_id: Some("%SRCROOT%".to_string()),
                },
                region: finding.line_start.map(|start| SarifRegion {
                    start_line: start,
                    end_line: finding.line_end,
                }),
            },
        })
        .collect();

    // Build fingerprint
    let mut fingerprints = HashMap::new();
    fingerprints.insert(
        "repotoire/finding/v1".to_string(),
        if finding.id.is_empty() {
            format!("finding-{}", index)
        } else {
            finding.id.clone()
        },
    );

    // Build fixes
    let fixes: Vec<SarifFix> = finding
        .suggested_fix
        .as_ref()
        .map(|fix| {
            vec![SarifFix {
                description: SarifMessage { text: fix.clone() },
            }]
        })
        .unwrap_or_default();

    // Convert confidence (0.0-1.0) to SARIF rank (0.0-100.0)
    let rank = finding.confidence.map(|c| (c * 100.0).clamp(0.0, 100.0));

    SarifResult {
        rule_id,
        level: severity_to_sarif_level(&finding.severity).to_string(),
        message: SarifMessage {
            text: if finding.description.is_empty() {
                finding.title.clone()
            } else {
                finding.description.clone()
            },
        },
        locations,
        fingerprints,
        properties: SarifResultProperties {
            severity: finding.severity.to_string(),
            suggested_fix: finding.suggested_fix.clone(),
            estimated_effort: finding.estimated_effort.clone(),
            category: finding.category.clone(),
            cwe_id: finding.cwe_id.clone(),
        },
        fixes,
        rank,
    }
}

/// Normalize detector name to SARIF rule ID
fn normalize_rule_id(detector: &str) -> String {
    // Remove 'Detector' suffix and convert to kebab-case
    let name = detector.replace("Detector", "");

    // Convert CamelCase to kebab-case
    let mut result = String::new();
    for (i, ch) in name.chars().enumerate() {
        if ch.is_uppercase() && i > 0 {
            result.push('-');
        }
        result.push(ch.to_ascii_lowercase());
    }

    format!("repotoire/{}", result)
}

/// Get description for a detector
fn get_detector_description(detector: &str) -> String {
    match detector {
        "CircularDependencyDetector" => {
            "Detects circular import dependencies that can cause import errors and make the codebase harder to maintain.".to_string()
        }
        "GodClassDetector" => {
            "Identifies classes that have grown too large and complex, violating the Single Responsibility Principle.".to_string()
        }
        "LongParameterListDetector" => {
            "Detects functions with too many parameters, which reduces readability and maintainability.".to_string()
        }
        "DeadCodeDetector" => {
            "Graph-based detection of unreachable code that can be safely removed.".to_string()
        }
        "FeatureEnvyDetector" => {
            "Detects methods that use more features from other classes than their own.".to_string()
        }
        "DataClumpsDetector" => {
            "Identifies groups of data that frequently appear together and should be encapsulated.".to_string()
        }
        "ShotgunSurgeryDetector" => {
            "Identifies changes that require modifications in many different places.".to_string()
        }
        _ => format!("Code analysis performed by {} detector.", detector.replace("Detector", "")),
    }
}

/// Get tags for a detector
fn get_detector_tags(detector: &str) -> Vec<String> {
    let security = ["BanditDetector", "SemgrepDetector", "TaintDetector"];
    let quality = ["RuffLintDetector", "MypyDetector", "PylintDetector"];
    let complexity = [
        "RadonDetector",
        "GodClassDetector",
        "LongParameterListDetector",
    ];
    let architecture = [
        "CircularDependencyDetector",
        "FeatureEnvyDetector",
        "ShotgunSurgeryDetector",
        "DataClumpsDetector",
    ];
    let maintenance = ["DeadCodeDetector", "VultureDetector", "JscpdDetector"];

    let mut tags = Vec::new();

    if security.contains(&detector) {
        tags.extend(["security", "vulnerability"].map(String::from));
    }
    if quality.contains(&detector) {
        tags.extend(["quality", "style"].map(String::from));
    }
    if complexity.contains(&detector) {
        tags.extend(["complexity", "maintainability"].map(String::from));
    }
    if architecture.contains(&detector) {
        tags.extend(["architecture", "design"].map(String::from));
    }
    if maintenance.contains(&detector) {
        tags.extend(["maintenance", "technical-debt"].map(String::from));
    }

    if tags.is_empty() {
        tags.push("code-smell".to_string());
    }

    tags
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn test_normalize_rule_id() {
        assert_eq!(
            normalize_rule_id("CircularDependencyDetector"),
            "repotoire/circular-dependency"
        );
        assert_eq!(normalize_rule_id("GodClassDetector"), "repotoire/god-class");
    }

    #[test]
    fn test_severity_mapping() {
        assert_eq!(severity_to_sarif_level(&Severity::Critical), "error");
        assert_eq!(severity_to_sarif_level(&Severity::Medium), "warning");
        assert_eq!(severity_to_sarif_level(&Severity::Low), "note");
    }

    #[test]
    fn test_confidence_to_rank() {
        // Finding with high confidence (0.95) should produce rank 95.0
        let high_conf_finding = Finding {
            id: "test-1".to_string(),
            detector: "TestDetector".to_string(),
            severity: Severity::High,
            title: "High confidence finding".to_string(),
            description: "Test".to_string(),
            affected_files: vec![PathBuf::from("test.py")],
            line_start: Some(10),
            line_end: Some(20),
            confidence: Some(0.95),
            ..Default::default()
        };

        let result = build_result(&high_conf_finding, 0);
        assert_eq!(result.rank, Some(95.0));

        // Finding with medium confidence (0.7)
        let med_conf_finding = Finding {
            confidence: Some(0.7),
            ..high_conf_finding.clone()
        };
        let result = build_result(&med_conf_finding, 1);
        assert_eq!(result.rank, Some(70.0));

        // Finding with no confidence should produce None rank
        let no_conf_finding = Finding {
            confidence: None,
            ..high_conf_finding.clone()
        };
        let result = build_result(&no_conf_finding, 2);
        assert_eq!(result.rank, None);

        // Edge case: confidence > 1.0 should be clamped to 100.0
        let over_conf_finding = Finding {
            confidence: Some(1.5),
            ..high_conf_finding.clone()
        };
        let result = build_result(&over_conf_finding, 3);
        assert_eq!(result.rank, Some(100.0));

        // Edge case: confidence < 0.0 should be clamped to 0.0
        let neg_conf_finding = Finding {
            confidence: Some(-0.1),
            ..high_conf_finding
        };
        let result = build_result(&neg_conf_finding, 4);
        assert_eq!(result.rank, Some(0.0));
    }

    #[test]
    fn test_rank_in_sarif_output() {
        // Create a minimal report with a finding that has confidence
        let report = HealthReport {
            overall_score: 85.0,
            grade: "B".to_string(),
            structure_score: 90.0,
            quality_score: 80.0,
            architecture_score: Some(85.0),
            findings: vec![Finding {
                id: "test-sarif".to_string(),
                detector: "SecurityDetector".to_string(),
                severity: Severity::High,
                title: "Security issue".to_string(),
                description: "Potential vulnerability".to_string(),
                affected_files: vec![PathBuf::from("src/main.py")],
                line_start: Some(42),
                line_end: Some(42),
                confidence: Some(0.85),
                ..Default::default()
            }],
            findings_summary: crate::models::FindingsSummary::default(),
            total_files: 10,
            total_functions: 50,
            total_classes: 5,
            total_loc: 5000,
        };

        let sarif_json = render(&report).expect("SARIF render should succeed");

        // Verify rank appears in output
        assert!(
            sarif_json.contains("\"rank\": 85.0"),
            "SARIF output should contain rank: 85.0"
        );
    }
}
