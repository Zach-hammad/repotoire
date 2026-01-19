//! Fast parallel voting consensus calculation (REPO-405)
//!
//! Replaces Python's O(F + G * k^2) voting engine with parallel Rust implementation.
//! Uses rayon for parallel group processing and optimized confidence calculations.

use rayon::prelude::*;
use rustc_hash::FxHashMap;
use std::collections::HashMap;

/// Confidence calculation method
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConfidenceMethod {
    Average,
    Weighted,
    Max,
    Min,
    Bayesian,
}

/// Severity level for findings
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Severity {
    Info = 0,
    Low = 1,
    Medium = 2,
    High = 3,
    Critical = 4,
}

impl Severity {
    pub fn from_str(s: &str) -> Self {
        match s.to_uppercase().as_str() {
            "CRITICAL" => Severity::Critical,
            "HIGH" => Severity::High,
            "MEDIUM" => Severity::Medium,
            "LOW" => Severity::Low,
            _ => Severity::Info,
        }
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            Severity::Critical => "CRITICAL",
            Severity::High => "HIGH",
            Severity::Medium => "MEDIUM",
            Severity::Low => "LOW",
            Severity::Info => "INFO",
        }
    }
}

/// Severity resolution strategy
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SeverityResolution {
    Highest,
    Majority,
    Weighted,
}

/// A finding from a detector
#[derive(Debug, Clone)]
pub struct Finding {
    pub id: String,
    pub detector: String,
    pub severity: Severity,
    pub confidence: f64,
    pub entity_key: String,
}

/// Result of consensus calculation for a group
#[derive(Debug, Clone)]
pub struct ConsensusResult {
    pub entity_key: String,
    pub has_consensus: bool,
    pub confidence: f64,
    pub severity: Severity,
    pub detector_count: usize,
    pub finding_ids: Vec<String>,
}

/// Calculate consensus for multiple finding groups in parallel.
///
/// # Arguments
/// * `groups` - Vec of (entity_key, findings) groups
/// * `detector_weights` - Map of detector name to weight
/// * `confidence_method` - How to calculate aggregate confidence
/// * `severity_resolution` - How to resolve conflicting severities
/// * `min_detectors_for_boost` - Minimum detectors for consensus boost
/// * `confidence_threshold` - Minimum confidence for consensus
///
/// # Returns
/// Vec of ConsensusResult for each group
pub fn calculate_consensus_batch(
    groups: Vec<(String, Vec<Finding>)>,
    detector_weights: &HashMap<String, f64>,
    confidence_method: ConfidenceMethod,
    severity_resolution: SeverityResolution,
    min_detectors_for_boost: usize,
    confidence_threshold: f64,
) -> Vec<ConsensusResult> {
    groups
        .into_par_iter()
        .map(|(entity_key, findings)| {
            calculate_group_consensus(
                entity_key,
                findings,
                detector_weights,
                confidence_method,
                severity_resolution,
                min_detectors_for_boost,
                confidence_threshold,
            )
        })
        .collect()
}

/// Calculate consensus for a single group of findings
fn calculate_group_consensus(
    entity_key: String,
    findings: Vec<Finding>,
    detector_weights: &HashMap<String, f64>,
    confidence_method: ConfidenceMethod,
    severity_resolution: SeverityResolution,
    min_detectors_for_boost: usize,
    confidence_threshold: f64,
) -> ConsensusResult {
    if findings.is_empty() {
        return ConsensusResult {
            entity_key,
            has_consensus: false,
            confidence: 0.0,
            severity: Severity::Info,
            detector_count: 0,
            finding_ids: Vec::new(),
        };
    }

    // Get unique detectors (O(n) with HashSet)
    let unique_detectors: FxHashMap<&str, ()> = findings
        .iter()
        .map(|f| (f.detector.as_str(), ()))
        .collect();
    let detector_count = unique_detectors.len();

    // Calculate base confidence
    let mut confidence = calculate_confidence(
        &findings,
        detector_weights,
        confidence_method,
    );

    // Apply consensus boost if multiple detectors agree
    if detector_count >= min_detectors_for_boost {
        let boost = ((detector_count - 1) as f64 * 0.05).min(0.20);
        confidence = (confidence + boost).min(1.0);
    }

    // Resolve severity
    let severity = resolve_severity(&findings, detector_weights, severity_resolution);

    // Check if consensus achieved
    let has_consensus = detector_count >= 2 && confidence >= confidence_threshold;

    let finding_ids: Vec<String> = findings.iter().map(|f| f.id.clone()).collect();

    ConsensusResult {
        entity_key,
        has_consensus,
        confidence,
        severity,
        detector_count,
        finding_ids,
    }
}

/// Calculate aggregate confidence using the specified method
fn calculate_confidence(
    findings: &[Finding],
    detector_weights: &HashMap<String, f64>,
    method: ConfidenceMethod,
) -> f64 {
    if findings.is_empty() {
        return 0.0;
    }

    match method {
        ConfidenceMethod::Average => {
            let sum: f64 = findings.iter().map(|f| f.confidence).sum();
            sum / findings.len() as f64
        }
        ConfidenceMethod::Weighted => {
            let mut total_weight = 0.0;
            let mut weighted_sum = 0.0;
            for f in findings {
                let weight = detector_weights.get(&f.detector).copied().unwrap_or(1.0);
                weighted_sum += f.confidence * weight;
                total_weight += weight;
            }
            if total_weight > 0.0 {
                weighted_sum / total_weight
            } else {
                0.0
            }
        }
        ConfidenceMethod::Max => {
            findings.iter().map(|f| f.confidence).fold(0.0, f64::max)
        }
        ConfidenceMethod::Min => {
            findings.iter().map(|f| f.confidence).fold(1.0, f64::min)
        }
        ConfidenceMethod::Bayesian => {
            // Iterative Bayesian update: P(H|E) = P(E|H) * P(H) / P(E)
            // Simplified: accumulate evidence
            let mut prob = 0.5; // Prior
            for f in findings {
                let likelihood = f.confidence;
                prob = (likelihood * prob) / ((likelihood * prob) + ((1.0 - likelihood) * (1.0 - prob)));
            }
            prob
        }
    }
}

/// Resolve conflicting severities using the specified strategy
fn resolve_severity(
    findings: &[Finding],
    detector_weights: &HashMap<String, f64>,
    resolution: SeverityResolution,
) -> Severity {
    if findings.is_empty() {
        return Severity::Info;
    }

    match resolution {
        SeverityResolution::Highest => {
            findings.iter().map(|f| f.severity).max().unwrap_or(Severity::Info)
        }
        SeverityResolution::Majority => {
            // Count occurrences of each severity
            let mut counts: FxHashMap<Severity, usize> = FxHashMap::default();
            for f in findings {
                *counts.entry(f.severity).or_default() += 1;
            }
            // Return most common
            counts
                .into_iter()
                .max_by_key(|(_, count)| *count)
                .map(|(sev, _)| sev)
                .unwrap_or(Severity::Info)
        }
        SeverityResolution::Weighted => {
            // Weight severities by detector weight and confidence
            let mut severity_scores: FxHashMap<Severity, f64> = FxHashMap::default();
            for f in findings {
                let weight = detector_weights.get(&f.detector).copied().unwrap_or(1.0);
                *severity_scores.entry(f.severity).or_default() += f.confidence * weight;
            }
            // Return highest scored
            severity_scores
                .into_iter()
                .max_by(|a, b| a.1.partial_cmp(&b.1).unwrap())
                .map(|(sev, _)| sev)
                .unwrap_or(Severity::Info)
        }
    }
}

/// Python-friendly wrapper that converts string severities
pub fn calculate_consensus_batch_py(
    groups: Vec<(String, Vec<(String, String, String, f64, String)>)>, // (id, detector, severity, confidence, entity_key)
    detector_weights: HashMap<String, f64>,
    confidence_method: &str,
    severity_resolution: &str,
    min_detectors_for_boost: usize,
    confidence_threshold: f64,
) -> Vec<(String, bool, f64, String, usize, Vec<String>)> {
    let method = match confidence_method.to_uppercase().as_str() {
        "WEIGHTED" => ConfidenceMethod::Weighted,
        "MAX" => ConfidenceMethod::Max,
        "MIN" => ConfidenceMethod::Min,
        "BAYESIAN" => ConfidenceMethod::Bayesian,
        _ => ConfidenceMethod::Average,
    };

    let resolution = match severity_resolution.to_uppercase().as_str() {
        "MAJORITY" => SeverityResolution::Majority,
        "WEIGHTED" => SeverityResolution::Weighted,
        _ => SeverityResolution::Highest,
    };

    // Convert input to internal Finding format
    let converted_groups: Vec<(String, Vec<Finding>)> = groups
        .into_iter()
        .map(|(key, findings)| {
            let converted: Vec<Finding> = findings
                .into_iter()
                .map(|(id, detector, severity, confidence, entity_key)| Finding {
                    id,
                    detector,
                    severity: Severity::from_str(&severity),
                    confidence,
                    entity_key,
                })
                .collect();
            (key, converted)
        })
        .collect();

    // Calculate consensus
    let results = calculate_consensus_batch(
        converted_groups,
        &detector_weights,
        method,
        resolution,
        min_detectors_for_boost,
        confidence_threshold,
    );

    // Convert back to Python-friendly format
    results
        .into_iter()
        .map(|r| {
            (
                r.entity_key,
                r.has_consensus,
                r.confidence,
                r.severity.as_str().to_string(),
                r.detector_count,
                r.finding_ids,
            )
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_consensus_basic() {
        let findings = vec![
            Finding {
                id: "f1".to_string(),
                detector: "ruff".to_string(),
                severity: Severity::High,
                confidence: 0.9,
                entity_key: "foo.py:10".to_string(),
            },
            Finding {
                id: "f2".to_string(),
                detector: "pylint".to_string(),
                severity: Severity::High,
                confidence: 0.85,
                entity_key: "foo.py:10".to_string(),
            },
        ];

        let groups = vec![("foo.py:10".to_string(), findings)];
        let weights = HashMap::new();

        let results = calculate_consensus_batch(
            groups,
            &weights,
            ConfidenceMethod::Average,
            SeverityResolution::Highest,
            2,
            0.5,
        );

        assert_eq!(results.len(), 1);
        assert!(results[0].has_consensus);
        assert!(results[0].confidence > 0.8);
        assert_eq!(results[0].severity, Severity::High);
    }

    #[test]
    fn test_confidence_methods() {
        let findings = vec![
            Finding {
                id: "f1".to_string(),
                detector: "d1".to_string(),
                severity: Severity::Medium,
                confidence: 0.8,
                entity_key: "test".to_string(),
            },
            Finding {
                id: "f2".to_string(),
                detector: "d2".to_string(),
                severity: Severity::Medium,
                confidence: 0.6,
                entity_key: "test".to_string(),
            },
        ];

        let weights = HashMap::new();

        assert_eq!(calculate_confidence(&findings, &weights, ConfidenceMethod::Average), 0.7);
        assert_eq!(calculate_confidence(&findings, &weights, ConfidenceMethod::Max), 0.8);
        assert_eq!(calculate_confidence(&findings, &weights, ConfidenceMethod::Min), 0.6);
    }

    #[test]
    fn test_severity_resolution() {
        let findings = vec![
            Finding {
                id: "f1".to_string(),
                detector: "d1".to_string(),
                severity: Severity::High,
                confidence: 0.9,
                entity_key: "test".to_string(),
            },
            Finding {
                id: "f2".to_string(),
                detector: "d2".to_string(),
                severity: Severity::Medium,
                confidence: 0.8,
                entity_key: "test".to_string(),
            },
            Finding {
                id: "f3".to_string(),
                detector: "d3".to_string(),
                severity: Severity::Medium,
                confidence: 0.7,
                entity_key: "test".to_string(),
            },
        ];

        let weights = HashMap::new();

        assert_eq!(resolve_severity(&findings, &weights, SeverityResolution::Highest), Severity::High);
        assert_eq!(resolve_severity(&findings, &weights, SeverityResolution::Majority), Severity::Medium);
    }
}
