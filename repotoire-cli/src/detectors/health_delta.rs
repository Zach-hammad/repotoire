//! Health score delta calculator for estimating fix impact
//!
//! This module provides utilities for estimating how resolving a finding
//! would impact the overall health score. This enables before/after
//! comparisons when users review proposed fixes.
//!
//! # Example
//!
//! ```ignore
//! let calculator = HealthScoreDeltaCalculator::new();
//! let delta = calculator.calculate_delta(&current_metrics, &finding);
//! println!("Fixing this would improve score by {:.1} points", delta.score_delta);
//! ```

#![allow(dead_code)] // Module under development - structs/helpers used in tests only

use crate::models::{Finding, Severity};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Impact level classification for health score changes
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ImpactLevel {
    /// >5 points improvement or grade change
    Critical,
    /// 2-5 points improvement
    High,
    /// 0.5-2 points improvement
    Medium,
    /// <0.5 points improvement
    Low,
    /// <0.1 points improvement
    Negligible,
}

impl std::fmt::Display for ImpactLevel {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ImpactLevel::Critical => write!(f, "critical"),
            ImpactLevel::High => write!(f, "high"),
            ImpactLevel::Medium => write!(f, "medium"),
            ImpactLevel::Low => write!(f, "low"),
            ImpactLevel::Negligible => write!(f, "negligible"),
        }
    }
}

/// Metrics breakdown for health score calculation
#[derive(Debug, Clone, Default)]
pub struct MetricsBreakdown {
    // Structure metrics
    pub modularity: f64,
    pub avg_coupling: Option<f64>,
    pub circular_dependencies: i32,
    pub bottleneck_count: i32,

    // Quality metrics
    pub dead_code_percentage: f64,
    pub duplication_percentage: f64,
    pub god_class_count: i32,

    // Architecture metrics
    pub layer_violations: i32,
    pub boundary_violations: i32,
    pub abstraction_ratio: f64,

    // Totals for calculations
    pub total_classes: i32,
    pub total_functions: i32,
}

/// Result of a health score delta calculation
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HealthScoreDelta {
    pub before_score: f64,
    pub after_score: f64,
    pub score_delta: f64,
    pub before_grade: String,
    pub after_grade: String,
    pub grade_improved: bool,
    pub structure_delta: f64,
    pub quality_delta: f64,
    pub architecture_delta: f64,
    pub impact_level: ImpactLevel,
    pub affected_metric: String,
    pub finding_id: Option<String>,
    pub finding_severity: Option<Severity>,
}

impl HealthScoreDelta {
    /// Return grade change as string (e.g., "B → A") or None if unchanged
    pub fn grade_change_str(&self) -> Option<String> {
        if self.grade_improved {
            Some(format!("{} → {}", self.before_grade, self.after_grade))
        } else {
            None
        }
    }
}

/// Result of calculating delta for multiple findings
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BatchHealthScoreDelta {
    pub before_score: f64,
    pub after_score: f64,
    pub score_delta: f64,
    pub before_grade: String,
    pub after_grade: String,
    pub grade_improved: bool,
    pub findings_count: usize,
    pub individual_deltas: Vec<HealthScoreDelta>,
}

/// Mapping from detector names to the metrics they affect
fn detector_metric_mapping() -> HashMap<&'static str, (&'static str, &'static str)> {
    let mut map = HashMap::new();
    // (metric_name, category)
    map.insert(
        "CircularDependencyDetector",
        ("circular_dependencies", "structure"),
    );
    map.insert("GodClassDetector", ("god_class_count", "quality"));
    map.insert("DeadCodeDetector", ("dead_code_percentage", "quality"));
    map.insert("VultureDetector", ("dead_code_percentage", "quality"));
    map.insert(
        "ArchitecturalBottleneckDetector",
        ("bottleneck_count", "structure"),
    );
    map.insert("JscpdDetector", ("duplication_percentage", "quality"));
    map.insert(
        "DuplicateRustDetector",
        ("duplication_percentage", "quality"),
    );
    map.insert("LayerViolationDetector", ("layer_violations", "architecture"));
    map.insert(
        "BoundaryViolationDetector",
        ("boundary_violations", "architecture"),
    );
    map.insert("ModuleCohesionDetector", ("modularity", "structure"));
    map.insert(
        "InappropriateIntimacyDetector",
        ("avg_coupling", "structure"),
    );
    map.insert("FeatureEnvyDetector", ("avg_coupling", "structure"));
    map.insert("ShotgunSurgeryDetector", ("avg_coupling", "structure"));
    map.insert("MiddleManDetector", ("bottleneck_count", "structure"));
    map.insert("DataClumpsDetector", ("avg_coupling", "structure"));
    map
}

/// Grade thresholds
fn score_to_grade(score: f64) -> String {
    if score >= 90.0 {
        "A".to_string()
    } else if score >= 80.0 {
        "B".to_string()
    } else if score >= 70.0 {
        "C".to_string()
    } else if score >= 60.0 {
        "D".to_string()
    } else {
        "F".to_string()
    }
}

/// Calculate health score deltas for individual or batched findings
pub struct HealthScoreDeltaCalculator {
    // Category weights
    structure_weight: f64,
    quality_weight: f64,
    architecture_weight: f64,
}

impl Default for HealthScoreDeltaCalculator {
    fn default() -> Self {
        Self::new()
    }
}

impl HealthScoreDeltaCalculator {
    /// Create a new calculator with default weights
    pub fn new() -> Self {
        Self {
            structure_weight: 0.40,
            quality_weight: 0.30,
            architecture_weight: 0.30,
        }
    }

    /// Create with custom weights
    pub fn with_weights(structure: f64, quality: f64, architecture: f64) -> Self {
        Self {
            structure_weight: structure,
            quality_weight: quality,
            architecture_weight: architecture,
        }
    }

    /// Calculate health score delta for resolving a single finding
    pub fn calculate_delta(&self, metrics: &MetricsBreakdown, finding: &Finding) -> HealthScoreDelta {
        // Calculate current scores
        let current_structure = self.score_structure(metrics);
        let current_quality = self.score_quality(metrics);
        let current_architecture = self.score_architecture(metrics);
        let current_overall =
            self.calculate_overall(current_structure, current_quality, current_architecture);
        let current_grade = score_to_grade(current_overall);

        // Simulate removing the finding's impact
        let modified_metrics = self.remove_finding_impact(metrics, finding);

        // Calculate new scores
        let new_structure = self.score_structure(&modified_metrics);
        let new_quality = self.score_quality(&modified_metrics);
        let new_architecture = self.score_architecture(&modified_metrics);
        let new_overall = self.calculate_overall(new_structure, new_quality, new_architecture);
        let new_grade = score_to_grade(new_overall);

        // Calculate deltas
        let score_delta = new_overall - current_overall;
        let structure_delta = new_structure - current_structure;
        let quality_delta = new_quality - current_quality;
        let architecture_delta = new_architecture - current_architecture;

        // Determine affected metric
        let affected_metric = self.get_affected_metric(&finding.detector);

        // Classify impact level
        let impact_level = self.classify_impact(score_delta, &current_grade != &new_grade);

        HealthScoreDelta {
            before_score: current_overall,
            after_score: new_overall,
            score_delta,
            before_grade: current_grade.clone(),
            after_grade: new_grade.clone(),
            grade_improved: new_grade < current_grade, // A < B < C < D < F
            structure_delta,
            quality_delta,
            architecture_delta,
            impact_level,
            affected_metric,
            finding_id: Some(finding.id.clone()),
            finding_severity: Some(finding.severity),
        }
    }

    /// Calculate health score delta for resolving multiple findings
    pub fn calculate_batch_delta(
        &self,
        metrics: &MetricsBreakdown,
        findings: &[Finding],
    ) -> BatchHealthScoreDelta {
        if findings.is_empty() {
            let current_overall = self.calculate_overall(
                self.score_structure(metrics),
                self.score_quality(metrics),
                self.score_architecture(metrics),
            );
            let current_grade = score_to_grade(current_overall);
            return BatchHealthScoreDelta {
                before_score: current_overall,
                after_score: current_overall,
                score_delta: 0.0,
                before_grade: current_grade.clone(),
                after_grade: current_grade,
                grade_improved: false,
                findings_count: 0,
                individual_deltas: vec![],
            };
        }

        // Calculate current scores
        let current_structure = self.score_structure(metrics);
        let current_quality = self.score_quality(metrics);
        let current_architecture = self.score_architecture(metrics);
        let current_overall =
            self.calculate_overall(current_structure, current_quality, current_architecture);
        let current_grade = score_to_grade(current_overall);

        // Calculate individual deltas
        let individual_deltas: Vec<HealthScoreDelta> = findings
            .iter()
            .map(|f| self.calculate_delta(metrics, f))
            .collect();

        // Simulate removing all findings' impacts
        let mut modified_metrics = metrics.clone();
        for finding in findings {
            modified_metrics = self.remove_finding_impact(&modified_metrics, finding);
        }

        // Calculate new aggregate scores
        let new_structure = self.score_structure(&modified_metrics);
        let new_quality = self.score_quality(&modified_metrics);
        let new_architecture = self.score_architecture(&modified_metrics);
        let new_overall = self.calculate_overall(new_structure, new_quality, new_architecture);
        let new_grade = score_to_grade(new_overall);

        BatchHealthScoreDelta {
            before_score: current_overall,
            after_score: new_overall,
            score_delta: new_overall - current_overall,
            before_grade: current_grade.clone(),
            after_grade: new_grade.clone(),
            grade_improved: new_grade < current_grade,
            findings_count: findings.len(),
            individual_deltas,
        }
    }

    /// Create modified metrics by removing one finding's contribution
    fn remove_finding_impact(
        &self,
        metrics: &MetricsBreakdown,
        finding: &Finding,
    ) -> MetricsBreakdown {
        let mut modified = metrics.clone();
        let detector = &finding.detector;

        // Apply detector-specific adjustments
        if detector == "CircularDependencyDetector" {
            modified.circular_dependencies = (modified.circular_dependencies - 1).max(0);
        } else if detector == "GodClassDetector" {
            modified.god_class_count = (modified.god_class_count - 1).max(0);
        } else if detector == "DeadCodeDetector" || detector == "VultureDetector" {
            // Estimate one dead code item
            let total_nodes = modified.total_classes + modified.total_functions;
            if total_nodes > 0 {
                let per_item_pct = 1.0 / total_nodes as f64;
                modified.dead_code_percentage =
                    (modified.dead_code_percentage - per_item_pct).max(0.0);
            }
        } else if detector == "ArchitecturalBottleneckDetector" {
            modified.bottleneck_count = (modified.bottleneck_count - 1).max(0);
        } else if detector == "JscpdDetector" || detector == "DuplicateRustDetector" {
            // Estimate 0.5% reduction per duplicate finding
            modified.duplication_percentage = (modified.duplication_percentage - 0.005).max(0.0);
        } else if detector == "LayerViolationDetector" {
            modified.layer_violations = (modified.layer_violations - 1).max(0);
        } else if detector == "BoundaryViolationDetector" {
            modified.boundary_violations = (modified.boundary_violations - 1).max(0);
        } else if detector == "ModuleCohesionDetector" {
            // Estimate 0.02 modularity improvement
            modified.modularity = (modified.modularity + 0.02).min(1.0);
        } else if detector == "InappropriateIntimacyDetector"
            || detector == "FeatureEnvyDetector"
            || detector == "ShotgunSurgeryDetector"
            || detector == "DataClumpsDetector"
        {
            // Estimate 0.5 coupling reduction
            if let Some(coupling) = modified.avg_coupling {
                modified.avg_coupling = Some((coupling - 0.5).max(0.0));
            }
        } else if detector == "MiddleManDetector" {
            // Removing a middle man reduces bottlenecks
            modified.bottleneck_count = (modified.bottleneck_count - 1).max(0);
        }

        modified
    }

    /// Score graph structure metrics
    fn score_structure(&self, m: &MetricsBreakdown) -> f64 {
        let modularity_score = m.modularity * 100.0;
        let avg_coupling = m.avg_coupling.unwrap_or(0.0);
        let coupling_score = (100.0 - (avg_coupling * 10.0)).max(0.0);
        let cycle_penalty = (m.circular_dependencies * 10).min(50) as f64;
        let cycle_score = 100.0 - cycle_penalty;
        let bottleneck_penalty = (m.bottleneck_count * 5).min(30) as f64;
        let bottleneck_score = 100.0 - bottleneck_penalty;

        (modularity_score + coupling_score + cycle_score + bottleneck_score) / 4.0
    }

    /// Score code quality metrics
    fn score_quality(&self, m: &MetricsBreakdown) -> f64 {
        let dead_code_score = 100.0 - (m.dead_code_percentage * 100.0);
        let duplication_score = 100.0 - (m.duplication_percentage * 100.0);
        let god_class_penalty = (m.god_class_count * 15).min(40) as f64;
        let god_class_score = 100.0 - god_class_penalty;

        (dead_code_score + duplication_score + god_class_score) / 3.0
    }

    /// Score architecture health
    fn score_architecture(&self, m: &MetricsBreakdown) -> f64 {
        let layer_penalty = (m.layer_violations * 5).min(50) as f64;
        let layer_score = 100.0 - layer_penalty;

        let boundary_penalty = (m.boundary_violations * 3).min(40) as f64;
        let boundary_score = 100.0 - boundary_penalty;

        // Abstraction: 0.3-0.7 is ideal
        let abstraction_score = if (0.3..=0.7).contains(&m.abstraction_ratio) {
            100.0
        } else {
            let distance = (m.abstraction_ratio - 0.3)
                .abs()
                .min((m.abstraction_ratio - 0.7).abs());
            (100.0 - (distance * 100.0)).max(50.0)
        };

        (layer_score + boundary_score + abstraction_score) / 3.0
    }

    /// Calculate overall score from category scores
    fn calculate_overall(&self, structure: f64, quality: f64, architecture: f64) -> f64 {
        structure * self.structure_weight
            + quality * self.quality_weight
            + architecture * self.architecture_weight
    }

    /// Get the metric name affected by a detector
    fn get_affected_metric(&self, detector: &str) -> String {
        detector_metric_mapping()
            .get(detector)
            .map(|(metric, _)| metric.to_string())
            .unwrap_or_else(|| "unknown".to_string())
    }

    /// Classify the impact level based on score change
    fn classify_impact(&self, score_delta: f64, grade_changed: bool) -> ImpactLevel {
        if grade_changed || score_delta > 5.0 {
            ImpactLevel::Critical
        } else if score_delta > 2.0 {
            ImpactLevel::High
        } else if score_delta > 0.5 {
            ImpactLevel::Medium
        } else if score_delta > 0.1 {
            ImpactLevel::Low
        } else {
            ImpactLevel::Negligible
        }
    }
}

/// Convenience function to estimate impact of fixing a single finding
pub fn estimate_fix_impact(metrics: &MetricsBreakdown, finding: &Finding) -> HealthScoreDelta {
    let calculator = HealthScoreDeltaCalculator::new();
    calculator.calculate_delta(metrics, finding)
}

/// Convenience function to estimate impact of fixing multiple findings
pub fn estimate_batch_fix_impact(
    metrics: &MetricsBreakdown,
    findings: &[Finding],
) -> BatchHealthScoreDelta {
    let calculator = HealthScoreDeltaCalculator::new();
    calculator.calculate_batch_delta(metrics, findings)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn create_test_finding(detector: &str) -> Finding {
        Finding {
            id: "test-1".to_string(),
            detector: detector.to_string(),
            severity: Severity::High,
            title: "Test finding".to_string(),
            description: "Test description".to_string(),
            affected_files: vec![PathBuf::from("test.py")],
            line_start: Some(10),
            line_end: Some(20),
            suggested_fix: None,
            estimated_effort: None,
            category: None,
            cwe_id: None,
            why_it_matters: None,
            ..Default::default()
        }
    }

    fn create_test_metrics() -> MetricsBreakdown {
        MetricsBreakdown {
            modularity: 0.7,
            avg_coupling: Some(3.0),
            circular_dependencies: 2,
            bottleneck_count: 3,
            dead_code_percentage: 0.05,
            duplication_percentage: 0.10,
            god_class_count: 2,
            layer_violations: 1,
            boundary_violations: 2,
            abstraction_ratio: 0.5,
            total_classes: 50,
            total_functions: 200,
        }
    }

    #[test]
    fn test_score_to_grade() {
        assert_eq!(score_to_grade(95.0), "A");
        assert_eq!(score_to_grade(85.0), "B");
        assert_eq!(score_to_grade(75.0), "C");
        assert_eq!(score_to_grade(65.0), "D");
        assert_eq!(score_to_grade(55.0), "F");
    }

    #[test]
    fn test_calculate_delta_circular_dep() {
        let calculator = HealthScoreDeltaCalculator::new();
        let metrics = create_test_metrics();
        let finding = create_test_finding("CircularDependencyDetector");

        let delta = calculator.calculate_delta(&metrics, &finding);

        assert!(delta.score_delta > 0.0);
        assert_eq!(delta.affected_metric, "circular_dependencies");
    }

    #[test]
    fn test_calculate_delta_god_class() {
        let calculator = HealthScoreDeltaCalculator::new();
        let metrics = create_test_metrics();
        let finding = create_test_finding("GodClassDetector");

        let delta = calculator.calculate_delta(&metrics, &finding);

        assert!(delta.score_delta > 0.0);
        assert_eq!(delta.affected_metric, "god_class_count");
    }

    #[test]
    fn test_batch_delta_empty() {
        let calculator = HealthScoreDeltaCalculator::new();
        let metrics = create_test_metrics();

        let delta = calculator.calculate_batch_delta(&metrics, &[]);

        assert_eq!(delta.score_delta, 0.0);
        assert_eq!(delta.findings_count, 0);
    }

    #[test]
    fn test_batch_delta_multiple() {
        let calculator = HealthScoreDeltaCalculator::new();
        let metrics = create_test_metrics();
        let findings = vec![
            create_test_finding("GodClassDetector"),
            create_test_finding("CircularDependencyDetector"),
        ];

        let delta = calculator.calculate_batch_delta(&metrics, &findings);

        assert!(delta.score_delta > 0.0);
        assert_eq!(delta.findings_count, 2);
        assert_eq!(delta.individual_deltas.len(), 2);
    }

    #[test]
    fn test_impact_classification() {
        let calculator = HealthScoreDeltaCalculator::new();

        assert_eq!(calculator.classify_impact(6.0, false), ImpactLevel::Critical);
        assert_eq!(calculator.classify_impact(3.0, false), ImpactLevel::High);
        assert_eq!(calculator.classify_impact(1.0, false), ImpactLevel::Medium);
        assert_eq!(calculator.classify_impact(0.3, false), ImpactLevel::Low);
        assert_eq!(
            calculator.classify_impact(0.05, false),
            ImpactLevel::Negligible
        );
        // Grade change always critical
        assert_eq!(calculator.classify_impact(0.1, true), ImpactLevel::Critical);
    }
}
