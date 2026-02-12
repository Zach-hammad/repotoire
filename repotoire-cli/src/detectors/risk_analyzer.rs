//! Risk Analyzer for cross-detector risk amplification
//!
//! Analyzes findings from multiple detectors to identify compound risk factors
//! and escalate severity when architectural bottlenecks combine with complexity
//! and security issues.
//!
//! # Risk Matrix
//!
//! - Bottleneck alone: Original severity
//! - Bottleneck + High Complexity: +1 severity level
//! - Bottleneck + Security Issue: +1 severity level
//! - Bottleneck + High Complexity + Security: CRITICAL
//!
//! The "Complexity Amplifier" pattern: High-centrality nodes with high complexity
//! and security vulnerabilities represent critical risk that requires immediate
//! attention.

#![allow(dead_code)] // Module under development - structs/helpers used in tests only

use crate::models::{Finding, Severity};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use tracing::{debug, info};

/// Represents a risk factor from another detector
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RiskFactor {
    pub factor_type: String, // "complexity", "security", "dead_code", etc.
    pub detector: String,    // Source detector name
    pub severity: Severity,
    pub confidence: f64,
    pub evidence: Vec<String>,
    pub finding_id: Option<String>,
}

/// Complete risk assessment for an entity
#[derive(Debug, Clone, Default)]
pub struct RiskAssessment {
    pub entity: String, // Qualified name or file path
    pub risk_factors: Vec<RiskFactor>,
    pub original_severity: Option<Severity>,
    pub escalated_severity: Option<Severity>,
    pub risk_score: f64, // 0.0 to 1.0
    pub mitigation_plan: Vec<String>,
}

impl RiskAssessment {
    /// Check if this represents critical compound risk
    pub fn is_critical_risk(&self) -> bool {
        self.risk_factors.len() >= 2 && self.escalated_severity == Some(Severity::Critical)
    }

    /// Get set of risk factor types
    pub fn factor_types(&self) -> HashSet<&str> {
        self.risk_factors
            .iter()
            .map(|rf| rf.factor_type.as_str())
            .collect()
    }
}

/// Risk factor weights for scoring
fn risk_weights() -> HashMap<&'static str, f64> {
    let mut weights = HashMap::new();
    weights.insert("bottleneck", 0.4);
    weights.insert("high_complexity", 0.3);
    weights.insert("security_vulnerability", 0.3);
    weights.insert("dead_code", 0.1);
    weights
}

/// Severity order for comparison
const SEVERITY_ORDER: &[Severity] = &[
    Severity::Info,
    Severity::Low,
    Severity::Medium,
    Severity::High,
    Severity::Critical,
];

fn severity_index(s: Severity) -> usize {
    SEVERITY_ORDER.iter().position(|&x| x == s).unwrap_or(2)
}

/// Analyzes bottleneck findings for compound risk factors
///
/// Correlates findings from:
/// - ArchitecturalBottleneckDetector (centrality, coupling)
/// - RadonDetector (complexity metrics)
/// - BanditDetector (security vulnerabilities)
///
/// And escalates severity when multiple risk factors combine.
pub struct RiskAnalyzer {
    complexity_threshold: i32,
    security_severity_threshold: Severity,
}

impl Default for RiskAnalyzer {
    fn default() -> Self {
        Self::new()
    }
}

impl RiskAnalyzer {
    /// Create a new risk analyzer with default thresholds
    pub fn new() -> Self {
        Self {
            complexity_threshold: 15,
            security_severity_threshold: Severity::Medium,
        }
    }

    /// Create with custom thresholds
    pub fn with_thresholds(complexity_threshold: i32, security_severity_threshold: Severity) -> Self {
        Self {
            complexity_threshold,
            security_severity_threshold,
        }
    }

    /// Analyze bottleneck findings for compound risk factors
    pub fn analyze(
        &self,
        bottleneck_findings: &[Finding],
        radon_findings: Option<&[Finding]>,
        bandit_findings: Option<&[Finding]>,
        other_findings: Option<&[Finding]>,
    ) -> (Vec<Finding>, Vec<RiskAssessment>) {
        let radon = radon_findings.unwrap_or(&[]);
        let bandit = bandit_findings.unwrap_or(&[]);
        let other = other_findings.unwrap_or(&[]);

        // Index findings by affected entities for fast lookup
        let complexity_by_entity = self.index_by_entity(radon);
        let security_by_entity = self.index_by_entity(bandit);
        let other_by_entity = self.index_by_entity(other);

        let mut assessments: Vec<RiskAssessment> = Vec::new();
        let mut modified_findings: Vec<Finding> = Vec::new();

        for finding in bottleneck_findings {
            let assessment = self.assess_bottleneck_risk(
                finding,
                &complexity_by_entity,
                &security_by_entity,
                &other_by_entity,
            );
            let modified_finding = self.apply_risk_escalation(finding.clone(), &assessment);

            assessments.push(assessment);
            modified_findings.push(modified_finding);
        }

        info!(
            "RiskAnalyzer: analyzed {} bottlenecks, {} with compound risk",
            bottleneck_findings.len(),
            assessments.iter().filter(|a| a.is_critical_risk()).count()
        );

        (modified_findings, assessments)
    }

    /// Index findings by their affected entities (nodes and files)
    fn index_by_entity<'a>(&self, findings: &'a [Finding]) -> HashMap<String, Vec<&'a Finding>> {
        let mut index: HashMap<String, Vec<&Finding>> = HashMap::new();

        for finding in findings {
            // Index by affected files
            for file_path in &finding.affected_files {
                let path_str = file_path.to_string_lossy().to_string();
                index.entry(path_str.clone()).or_default().push(finding);

                // Also index by filename without path for broader matching
                if let Some(filename) = file_path.file_name() {
                    let name = filename.to_string_lossy().to_string();
                    index.entry(name).or_default().push(finding);
                }
            }
        }

        index
    }

    /// Assess compound risk for a bottleneck finding
    fn assess_bottleneck_risk(
        &self,
        bottleneck: &Finding,
        complexity_index: &HashMap<String, Vec<&Finding>>,
        security_index: &HashMap<String, Vec<&Finding>>,
        other_index: &HashMap<String, Vec<&Finding>>,
    ) -> RiskAssessment {
        let entity = bottleneck
            .affected_files
            .first()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default();

        let mut assessment = RiskAssessment {
            entity,
            original_severity: Some(bottleneck.severity),
            ..Default::default()
        };

        // Add bottleneck as base risk factor
        let bottleneck_factor = RiskFactor {
            factor_type: "bottleneck".to_string(),
            detector: "ArchitecturalBottleneckDetector".to_string(),
            severity: bottleneck.severity,
            confidence: 0.8,
            evidence: self.extract_bottleneck_evidence(bottleneck),
            finding_id: Some(bottleneck.id.clone()),
        };
        assessment.risk_factors.push(bottleneck_factor);

        // Check for complexity risk factors
        let complexity_factors = self.find_complexity_factors(bottleneck, complexity_index);
        assessment.risk_factors.extend(complexity_factors);

        // Check for security risk factors
        let security_factors = self.find_security_factors(bottleneck, security_index);
        assessment.risk_factors.extend(security_factors);

        // Check for other risk factors (dead code, etc.)
        let other_factors = self.find_other_factors(bottleneck, other_index);
        assessment.risk_factors.extend(other_factors);

        // Calculate risk score
        assessment.risk_score = self.calculate_risk_score(&assessment.risk_factors);

        // Determine escalated severity
        assessment.escalated_severity = Some(self.calculate_escalated_severity(&assessment));

        // Generate mitigation plan
        assessment.mitigation_plan = self.generate_mitigation_plan(&assessment);

        assessment
    }

    /// Extract evidence strings from bottleneck finding
    fn extract_bottleneck_evidence(&self, _finding: &Finding) -> Vec<String> {
        // In Rust, we don't have direct access to graph_context like in Python
        // Return basic evidence
        vec!["architectural_bottleneck".to_string()]
    }

    /// Find complexity risk factors related to the bottleneck
    fn find_complexity_factors(
        &self,
        bottleneck: &Finding,
        complexity_index: &HashMap<String, Vec<&Finding>>,
    ) -> Vec<RiskFactor> {
        let mut factors = Vec::new();

        // Check all entities associated with the bottleneck
        for file_path in &bottleneck.affected_files {
            let path_str = file_path.to_string_lossy().to_string();
            if let Some(complexity_findings) = complexity_index.get(&path_str) {
                for complexity_finding in complexity_findings {
                    // Check if this is a high complexity finding
                    if complexity_finding.severity >= Severity::Medium {
                        let factor = RiskFactor {
                            factor_type: "high_complexity".to_string(),
                            detector: "RadonDetector".to_string(),
                            severity: complexity_finding.severity,
                            confidence: 0.95,
                            evidence: vec![format!(
                                "high_complexity_in_{}",
                                file_path.file_name().unwrap_or_default().to_string_lossy()
                            )],
                            finding_id: Some(complexity_finding.id.clone()),
                        };
                        factors.push(factor);
                        break; // One complexity factor per bottleneck is enough
                    }
                }
            }
        }

        factors
    }

    /// Find security risk factors related to the bottleneck
    fn find_security_factors(
        &self,
        bottleneck: &Finding,
        security_index: &HashMap<String, Vec<&Finding>>,
    ) -> Vec<RiskFactor> {
        let mut factors = Vec::new();

        for file_path in &bottleneck.affected_files {
            let path_str = file_path.to_string_lossy().to_string();
            if let Some(security_findings) = security_index.get(&path_str) {
                for security_finding in security_findings {
                    // Check if severity meets threshold
                    if self.severity_meets_threshold(
                        security_finding.severity,
                        self.security_severity_threshold,
                    ) {
                        let factor = RiskFactor {
                            factor_type: "security_vulnerability".to_string(),
                            detector: "BanditDetector".to_string(),
                            severity: security_finding.severity,
                            confidence: 0.8,
                            evidence: vec![security_finding.title.clone()],
                            finding_id: Some(security_finding.id.clone()),
                        };
                        factors.push(factor);
                    }
                }
            }
        }

        factors
    }

    /// Find other risk factors (dead code, etc.) related to the bottleneck
    fn find_other_factors(
        &self,
        bottleneck: &Finding,
        other_index: &HashMap<String, Vec<&Finding>>,
    ) -> Vec<RiskFactor> {
        let mut factors = Vec::new();

        for file_path in &bottleneck.affected_files {
            let path_str = file_path.to_string_lossy().to_string();
            if let Some(other_findings) = other_index.get(&path_str) {
                for other_finding in other_findings {
                    let factor_type = self.determine_factor_type(&other_finding.detector);

                    let factor = RiskFactor {
                        factor_type,
                        detector: other_finding.detector.clone(),
                        severity: other_finding.severity,
                        confidence: 0.7,
                        evidence: vec![format!("from_{}", other_finding.detector)],
                        finding_id: Some(other_finding.id.clone()),
                    };
                    factors.push(factor);
                }
            }
        }

        factors
    }

    /// Determine risk factor type from detector name
    fn determine_factor_type(&self, detector_name: &str) -> String {
        let detector_lower = detector_name.to_lowercase();
        if detector_lower.contains("dead") || detector_lower.contains("vulture") {
            "dead_code".to_string()
        } else if detector_lower.contains("complexity") || detector_lower.contains("radon") {
            "high_complexity".to_string()
        } else if detector_lower.contains("security") || detector_lower.contains("bandit") {
            "security_vulnerability".to_string()
        } else {
            "other".to_string()
        }
    }

    /// Check if severity meets or exceeds threshold
    fn severity_meets_threshold(&self, severity: Severity, threshold: Severity) -> bool {
        severity_index(severity) >= severity_index(threshold)
    }

    /// Calculate overall risk score from factors
    fn calculate_risk_score(&self, factors: &[RiskFactor]) -> f64 {
        if factors.is_empty() {
            return 0.0;
        }

        let weights = risk_weights();
        let mut score = 0.0;

        for factor in factors {
            let weight = weights.get(factor.factor_type.as_str()).unwrap_or(&0.1);
            let severity_multiplier =
                (severity_index(factor.severity) + 1) as f64 / SEVERITY_ORDER.len() as f64;
            score += weight * severity_multiplier * factor.confidence;
        }

        // Normalize to 0-1 range
        score.min(1.0)
    }

    /// Calculate escalated severity based on risk factors
    ///
    /// Risk Matrix:
    /// - 1 factor (bottleneck only): Original severity
    /// - 2 factors: +1 severity level
    /// - 3+ factors: CRITICAL
    fn calculate_escalated_severity(&self, assessment: &RiskAssessment) -> Severity {
        let original_idx = assessment
            .original_severity
            .map(severity_index)
            .unwrap_or(2);

        // Get unique factor types (excluding base bottleneck)
        let additional_factors = assessment.factor_types().len().saturating_sub(1);

        if additional_factors >= 2 {
            // 3+ different factor types -> CRITICAL
            Severity::Critical
        } else if additional_factors == 1 {
            // 2 factor types -> escalate by 1
            let new_idx = (original_idx + 1).min(SEVERITY_ORDER.len() - 1);
            SEVERITY_ORDER[new_idx]
        } else {
            // Bottleneck only -> keep original
            assessment.original_severity.unwrap_or(Severity::Medium)
        }
    }

    /// Generate prioritized mitigation plan based on risk factors
    fn generate_mitigation_plan(&self, assessment: &RiskAssessment) -> Vec<String> {
        let mut plan = Vec::new();
        let factor_types = assessment.factor_types();

        // Priority 1: Security vulnerabilities (most urgent)
        if factor_types.contains("security_vulnerability") {
            plan.push(
                "1. [URGENT] Address security vulnerabilities first - \
                 review and fix identified security issues before other changes"
                    .to_string(),
            );
        }

        // Priority 2: Reduce bottleneck impact
        if factor_types.contains("bottleneck") {
            plan.push(
                "2. Reduce architectural coupling - consider extracting \
                 interfaces or introducing dependency injection"
                    .to_string(),
            );
        }

        // Priority 3: Simplify complexity
        if factor_types.contains("high_complexity") {
            plan.push(
                "3. Reduce cyclomatic complexity - break down complex methods \
                 into smaller, focused functions"
                    .to_string(),
            );
        }

        // Priority 4: Clean up dead code
        if factor_types.contains("dead_code") {
            plan.push(
                "4. Remove dead code - eliminate unused functions and classes \
                 to reduce maintenance burden"
                    .to_string(),
            );
        }

        // Add compound risk warning if critical
        if assessment.is_critical_risk() {
            plan.insert(
                0,
                "!!! CRITICAL COMPOUND RISK: Multiple risk factors combine \
                 to create systemic risk. Address all factors together."
                    .to_string(),
            );
        }

        plan
    }

    /// Apply risk escalation to a finding based on assessment
    fn apply_risk_escalation(&self, mut finding: Finding, assessment: &RiskAssessment) -> Finding {
        // Update severity if escalated
        if let Some(escalated) = assessment.escalated_severity {
            if escalated != assessment.original_severity.unwrap_or(Severity::Medium) {
                finding.severity = escalated;
            }
        }

        // Update description if critical compound risk
        if assessment.is_critical_risk() {
            let factor_names: Vec<&str> = assessment.factor_types().into_iter().collect();
            finding.description = format!(
                "**CRITICAL COMPOUND RISK**: {}\n\n\
                 Risk factors: {}\n\
                 Risk score: {:.2}",
                finding.description,
                factor_names.join(", "),
                assessment.risk_score
            );
        }

        // Update suggested fix with mitigation plan
        if !assessment.mitigation_plan.is_empty() {
            finding.suggested_fix = Some(assessment.mitigation_plan.join("\n"));
        }

        finding
    }
}

/// Convenience function to analyze compound risks from mixed findings
pub fn analyze_compound_risks(
    all_findings: &[Finding],
    complexity_threshold: i32,
    security_severity_threshold: Severity,
) -> (Vec<Finding>, Vec<RiskAssessment>) {
    // Separate findings by detector type
    let mut bottleneck_findings = Vec::new();
    let mut radon_findings = Vec::new();
    let mut bandit_findings = Vec::new();
    let mut other_findings = Vec::new();

    for finding in all_findings {
        let detector_lower = finding.detector.to_lowercase();
        if detector_lower.contains("bottleneck") || detector_lower.contains("centrality") {
            bottleneck_findings.push(finding.clone());
        } else if detector_lower.contains("radon") || detector_lower.contains("complexity") {
            radon_findings.push(finding.clone());
        } else if detector_lower.contains("bandit") || detector_lower.contains("security") {
            bandit_findings.push(finding.clone());
        } else {
            other_findings.push(finding.clone());
        }
    }

    // Run analysis
    let analyzer = RiskAnalyzer::with_thresholds(complexity_threshold, security_severity_threshold);

    analyzer.analyze(
        &bottleneck_findings,
        Some(&radon_findings),
        Some(&bandit_findings),
        Some(&other_findings),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn create_test_finding(detector: &str, severity: Severity, file: &str) -> Finding {
        Finding {
            id: uuid::Uuid::new_v4().to_string(),
            detector: detector.to_string(),
            severity,
            title: format!("Test finding from {}", detector),
            description: "Test description".to_string(),
            affected_files: vec![PathBuf::from(file)],
            line_start: Some(10),
            line_end: Some(20),
            suggested_fix: Some("Fix it".to_string()),
            estimated_effort: None,
            category: None,
            cwe_id: None,
            why_it_matters: None,
            ..Default::default()
        }
    }

    #[test]
    fn test_single_bottleneck() {
        let analyzer = RiskAnalyzer::new();
        let bottlenecks = vec![create_test_finding(
            "ArchitecturalBottleneckDetector",
            Severity::Medium,
            "test.py",
        )];

        let (modified, assessments) = analyzer.analyze(&bottlenecks, None, None, None);

        assert_eq!(modified.len(), 1);
        assert_eq!(assessments.len(), 1);
        assert_eq!(assessments[0].risk_factors.len(), 1);
        // No escalation for single factor
        assert_eq!(assessments[0].escalated_severity, Some(Severity::Medium));
    }

    #[test]
    fn test_bottleneck_with_complexity() {
        let analyzer = RiskAnalyzer::new();
        let bottlenecks = vec![create_test_finding(
            "ArchitecturalBottleneckDetector",
            Severity::Medium,
            "test.py",
        )];
        let radon = vec![create_test_finding(
            "RadonDetector",
            Severity::High,
            "test.py",
        )];

        let (modified, assessments) = analyzer.analyze(&bottlenecks, Some(&radon), None, None);

        assert_eq!(assessments[0].risk_factors.len(), 2);
        // Should escalate by 1 level
        assert_eq!(assessments[0].escalated_severity, Some(Severity::High));
        assert_eq!(modified[0].severity, Severity::High);
    }

    #[test]
    fn test_compound_risk_critical() {
        let analyzer = RiskAnalyzer::new();
        let bottlenecks = vec![create_test_finding(
            "ArchitecturalBottleneckDetector",
            Severity::High,
            "test.py",
        )];
        let radon = vec![create_test_finding(
            "RadonDetector",
            Severity::High,
            "test.py",
        )];
        let bandit = vec![create_test_finding(
            "BanditDetector",
            Severity::High,
            "test.py",
        )];

        let (modified, assessments) =
            analyzer.analyze(&bottlenecks, Some(&radon), Some(&bandit), None);

        // Should be critical with 3 factor types
        assert!(assessments[0].is_critical_risk());
        assert_eq!(assessments[0].escalated_severity, Some(Severity::Critical));
        assert_eq!(modified[0].severity, Severity::Critical);
        assert!(modified[0].description.contains("CRITICAL COMPOUND RISK"));
    }

    #[test]
    fn test_risk_score_calculation() {
        let analyzer = RiskAnalyzer::new();
        let factors = vec![
            RiskFactor {
                factor_type: "bottleneck".to_string(),
                detector: "Test".to_string(),
                severity: Severity::High,
                confidence: 0.9,
                evidence: vec![],
                finding_id: None,
            },
            RiskFactor {
                factor_type: "security_vulnerability".to_string(),
                detector: "Test".to_string(),
                severity: Severity::Critical,
                confidence: 0.8,
                evidence: vec![],
                finding_id: None,
            },
        ];

        let score = analyzer.calculate_risk_score(&factors);
        assert!(score > 0.0);
        assert!(score <= 1.0);
    }

    #[test]
    fn test_mitigation_plan_priority() {
        let analyzer = RiskAnalyzer::new();
        let mut assessment = RiskAssessment {
            entity: "test.py".to_string(),
            original_severity: Some(Severity::High),
            ..Default::default()
        };
        assessment.risk_factors.push(RiskFactor {
            factor_type: "bottleneck".to_string(),
            detector: "Test".to_string(),
            severity: Severity::High,
            confidence: 0.9,
            evidence: vec![],
            finding_id: None,
        });
        assessment.risk_factors.push(RiskFactor {
            factor_type: "security_vulnerability".to_string(),
            detector: "Test".to_string(),
            severity: Severity::Critical,
            confidence: 0.8,
            evidence: vec![],
            finding_id: None,
        });

        let plan = analyzer.generate_mitigation_plan(&assessment);

        // Security should be first priority
        assert!(plan[0].contains("URGENT") || plan[0].contains("security"));
    }
}
