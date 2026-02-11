//! Voting and Consensus Engine for Multi-Detector Validation
//!
//! Aggregates findings from multiple detectors to determine consensus
//! and confidence scores using configurable voting strategies.
//!
//! # Voting Strategies
//!
//! - `Majority`: 2+ detectors agree = consensus
//! - `Weighted`: Detectors have different weights based on accuracy
//! - `Threshold`: Only include findings above confidence threshold
//! - `Unanimous`: All detectors must agree
//!
//! # Example
//!
//! ```ignore
//! let engine = VotingEngine::new();
//! let (consensus_findings, stats) = engine.vote(all_findings);
//! ```

use crate::models::{Finding, Severity};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use tracing::{debug, info};

/// Voting strategy for consensus determination
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
pub enum VotingStrategy {
    /// 2+ detectors agree = consensus
    #[default]
    Majority,
    /// Weight by detector accuracy
    Weighted,
    /// Only high-confidence findings
    Threshold,
    /// All detectors must agree
    Unanimous,
}

/// Method for calculating aggregate confidence
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
pub enum ConfidenceMethod {
    /// Simple average
    Average,
    /// Weighted by detector accuracy
    #[default]
    Weighted,
    /// Prior + evidence strength
    Bayesian,
    /// Maximum (aggressive)
    Max,
    /// Minimum (conservative)
    Min,
}

/// Method for resolving severity conflicts
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
pub enum SeverityResolution {
    /// Use highest severity
    #[default]
    Highest,
    /// Use lowest (conservative)
    Lowest,
    /// Most common severity
    MajorityVote,
    /// Weight by confidence
    WeightedVote,
}

/// Weight configuration for a detector
#[derive(Debug, Clone)]
pub struct DetectorWeight {
    pub name: String,
    pub weight: f64,
    pub accuracy: f64,
}

impl DetectorWeight {
    pub fn new(name: impl Into<String>, weight: f64, accuracy: f64) -> Self {
        Self {
            name: name.into(),
            weight,
            accuracy,
        }
    }
}

impl Default for DetectorWeight {
    fn default() -> Self {
        Self {
            name: "default".to_string(),
            weight: 1.0,
            accuracy: 0.80,
        }
    }
}

/// Result of consensus calculation for a finding group
#[derive(Debug, Clone)]
pub struct ConsensusResult {
    pub has_consensus: bool,
    pub confidence: f64,
    pub severity: Severity,
    pub contributing_detectors: Vec<String>,
    pub vote_count: usize,
    pub total_detectors: usize,
    pub agreement_ratio: f64,
}

/// Statistics from voting engine run
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct VotingStats {
    pub total_input: usize,
    pub total_output: usize,
    pub groups_analyzed: usize,
    pub single_detector_findings: usize,
    pub multi_detector_findings: usize,
    pub boosted_by_consensus: usize,
    pub rejected_low_confidence: usize,
    pub strategy: String,
    pub confidence_method: String,
    pub threshold: f64,
}

/// Default detector weights based on typical accuracy
fn default_detector_weights() -> HashMap<String, DetectorWeight> {
    let weights = vec![
        // Graph-based detectors (lower false positive rate)
        ("CircularDependencyDetector", 1.2, 0.95),
        ("GodClassDetector", 1.1, 0.85),
        ("FeatureEnvyDetector", 1.0, 0.80),
        ("ShotgunSurgeryDetector", 1.0, 0.85),
        ("InappropriateIntimacyDetector", 1.0, 0.80),
        ("ArchitecturalBottleneckDetector", 1.1, 0.90),
        // Hybrid detectors (external tool + graph)
        ("RuffLintDetector", 1.3, 0.98),
        ("RuffImportDetector", 1.2, 0.95),
        ("MypyDetector", 1.3, 0.99),
        ("BanditDetector", 1.1, 0.85),
        ("SemgrepDetector", 1.2, 0.90),
        ("RadonDetector", 1.0, 0.95),
        ("JscpdDetector", 1.1, 0.90),
        ("VultureDetector", 0.9, 0.75),
        ("PylintDetector", 1.0, 0.85),
    ];

    let mut map = HashMap::new();
    for (name, weight, accuracy) in weights {
        map.insert(
            name.to_string(),
            DetectorWeight::new(name, weight, accuracy),
        );
    }
    map.insert("default".to_string(), DetectorWeight::default());
    map
}

/// Engine for aggregating findings and determining consensus
///
/// Supports multiple voting strategies and confidence scoring methods
/// to determine when multiple detectors agree on an issue.
pub struct VotingEngine {
    strategy: VotingStrategy,
    confidence_method: ConfidenceMethod,
    severity_resolution: SeverityResolution,
    confidence_threshold: f64,
    min_detectors_for_boost: usize,
    detector_weights: HashMap<String, DetectorWeight>,
}

impl Default for VotingEngine {
    fn default() -> Self {
        Self::new()
    }
}

impl VotingEngine {
    /// Create a new voting engine with default settings
    pub fn new() -> Self {
        Self {
            strategy: VotingStrategy::default(),
            confidence_method: ConfidenceMethod::default(),
            severity_resolution: SeverityResolution::default(),
            confidence_threshold: 0.6,
            min_detectors_for_boost: 2,
            detector_weights: default_detector_weights(),
        }
    }

    /// Create with custom configuration
    pub fn with_config(
        strategy: VotingStrategy,
        confidence_method: ConfidenceMethod,
        severity_resolution: SeverityResolution,
        confidence_threshold: f64,
        min_detectors_for_boost: usize,
    ) -> Self {
        Self {
            strategy,
            confidence_method,
            severity_resolution,
            confidence_threshold,
            min_detectors_for_boost,
            detector_weights: default_detector_weights(),
        }
    }

    /// Apply voting to findings and return consensus findings
    pub fn vote(&self, findings: Vec<Finding>) -> (Vec<Finding>, VotingStats) {
        if findings.is_empty() {
            return (
                vec![],
                VotingStats {
                    total_input: 0,
                    total_output: 0,
                    ..Default::default()
                },
            );
        }

        // Group findings by entity
        let groups = self.group_by_entity(&findings);

        let mut consensus_findings = Vec::new();
        let mut rejected_count = 0;
        let mut boosted_count = 0;

        for (_entity_key, group_findings) in &groups {
            if group_findings.len() == 1 {
                // Single detector - check threshold
                let finding = &group_findings[0];
                let confidence = self.get_finding_confidence(finding);

                if confidence >= self.confidence_threshold {
                    consensus_findings.push(finding.clone());
                } else {
                    rejected_count += 1;
                }
            } else {
                // Multiple detectors - calculate consensus
                let consensus = self.calculate_consensus(group_findings);

                if consensus.has_consensus && consensus.confidence >= self.confidence_threshold {
                    let merged = self.create_consensus_finding(group_findings, &consensus);
                    consensus_findings.push(merged);
                    boosted_count += 1;
                } else {
                    rejected_count += 1;
                }
            }
        }

        let stats = VotingStats {
            total_input: findings.len(),
            total_output: consensus_findings.len(),
            groups_analyzed: groups.len(),
            single_detector_findings: groups.values().filter(|g| g.len() == 1).count(),
            multi_detector_findings: groups.values().filter(|g| g.len() > 1).count(),
            boosted_by_consensus: boosted_count,
            rejected_low_confidence: rejected_count,
            strategy: format!("{:?}", self.strategy),
            confidence_method: format!("{:?}", self.confidence_method),
            threshold: self.confidence_threshold,
        };

        info!(
            "VotingEngine: {} -> {} findings ({} boosted, {} rejected)",
            findings.len(),
            consensus_findings.len(),
            boosted_count,
            rejected_count
        );

        (consensus_findings, stats)
    }

    /// Group findings by the entity they target
    fn group_by_entity(&self, findings: &[Finding]) -> HashMap<String, Vec<Finding>> {
        let mut groups: HashMap<String, Vec<Finding>> = HashMap::new();

        for finding in findings {
            let key = self.get_entity_key(finding);
            groups.entry(key).or_default().push(finding.clone());
        }

        groups
    }

    /// Generate unique key for entity identification
    fn get_entity_key(&self, finding: &Finding) -> String {
        // Get issue category to prevent merging different issue types
        let category = self.get_issue_category(finding);

        // Build key from affected nodes/files
        let location = if !finding.affected_files.is_empty() {
            let file = finding.affected_files[0].to_string_lossy();
            match (finding.line_start, finding.line_end) {
                (Some(start), Some(end)) => {
                    // Use line bucket for proximity matching
                    let bucket = start / 5;
                    format!("{}:{}:{}", file, bucket, end / 5)
                }
                (Some(start), None) => {
                    let bucket = start / 5;
                    format!("{}:{}", file, bucket)
                }
                _ => file.to_string(),
            }
        } else {
            "unknown".to_string()
        };

        format!("{}::{}", category, location)
    }

    /// Determine the category/type of issue for grouping
    fn get_issue_category(&self, finding: &Finding) -> &str {
        let detector = finding.detector.to_lowercase();

        if detector.contains("circular") || detector.contains("dependency") {
            "circular_dependency"
        } else if detector.contains("god") || detector.contains("class") {
            "god_class"
        } else if detector.contains("dead") || detector.contains("vulture") {
            "dead_code"
        } else if detector.contains("security") || detector.contains("bandit") {
            "security"
        } else if detector.contains("complexity") || detector.contains("radon") {
            "complexity"
        } else if detector.contains("duplicate") || detector.contains("clone") {
            "duplication"
        } else if detector.contains("type") || detector.contains("mypy") {
            "type_error"
        } else if detector.contains("lint") || detector.contains("ruff") {
            "lint"
        } else {
            "other"
        }
    }

    /// Calculate consensus for a group of findings
    fn calculate_consensus(&self, findings: &[Finding]) -> ConsensusResult {
        let detectors: Vec<&str> = findings.iter().map(|f| f.detector.as_str()).collect();
        let unique_detectors: HashSet<&str> = detectors.iter().copied().collect();
        let unique_vec: Vec<String> = unique_detectors.iter().map(|s| s.to_string()).collect();

        // Calculate confidence
        let confidence = self.calculate_confidence(findings);

        // Resolve severity
        let severity = self.resolve_severity(findings);

        // Check if consensus achieved based on strategy
        let has_consensus = self.check_consensus(findings, &unique_vec);

        let agreement_ratio = unique_detectors.len() as f64 / findings.len().max(1) as f64;

        ConsensusResult {
            has_consensus,
            confidence,
            severity,
            contributing_detectors: unique_vec,
            vote_count: unique_detectors.len(),
            total_detectors: findings.len(),
            agreement_ratio,
        }
    }

    /// Check if consensus is achieved based on voting strategy
    fn check_consensus(&self, findings: &[Finding], unique_detectors: &[String]) -> bool {
        let detector_count = unique_detectors.len();

        match self.strategy {
            VotingStrategy::Unanimous => {
                // All findings must be from different detectors
                detector_count >= 2 && detector_count == findings.len()
            }
            VotingStrategy::Majority => {
                // At least 2 detectors agree
                detector_count >= 2
            }
            VotingStrategy::Weighted => {
                // Calculate weighted vote score
                let total_weight: f64 = findings
                    .iter()
                    .map(|f| self.get_detector_weight(&f.detector))
                    .sum();
                // Need combined weight >= 2.0 for consensus
                total_weight >= 2.0
            }
            VotingStrategy::Threshold => {
                // Check if aggregate confidence meets threshold
                let confidence = self.calculate_confidence(findings);
                confidence >= self.confidence_threshold
            }
        }
    }

    /// Calculate aggregate confidence using configured method
    fn calculate_confidence(&self, findings: &[Finding]) -> f64 {
        let mut confidences = Vec::new();
        let mut weights = Vec::new();

        for finding in findings {
            let conf = self.get_finding_confidence(finding);
            let weight = self.get_detector_weight(&finding.detector);
            confidences.push(conf);
            weights.push(weight);
        }

        if confidences.is_empty() {
            return 0.0;
        }

        let base = match self.confidence_method {
            ConfidenceMethod::Average => confidences.iter().sum::<f64>() / confidences.len() as f64,

            ConfidenceMethod::Weighted => {
                let total_weight: f64 = weights.iter().sum();
                if total_weight > 0.0 {
                    confidences
                        .iter()
                        .zip(weights.iter())
                        .map(|(c, w)| c * w)
                        .sum::<f64>()
                        / total_weight
                } else {
                    confidences.iter().sum::<f64>() / confidences.len() as f64
                }
            }

            ConfidenceMethod::Max => confidences.iter().cloned().fold(0.0, f64::max),

            ConfidenceMethod::Min => confidences.iter().cloned().fold(1.0, f64::min),

            ConfidenceMethod::Bayesian => {
                // Bayesian: Start with prior (0.5), update with evidence
                let mut prior = 0.5;
                for &conf in &confidences {
                    let likelihood = conf;
                    prior = (prior * likelihood)
                        / (prior * likelihood + (1.0 - prior) * (1.0 - likelihood));
                }
                prior
            }
        };

        // Apply consensus boost if multiple detectors agree
        let unique_detectors: HashSet<&str> =
            findings.iter().map(|f| f.detector.as_str()).collect();
        if unique_detectors.len() >= self.min_detectors_for_boost {
            // Boost: +5% per additional detector, max +20%
            let boost = ((unique_detectors.len() - 1) as f64 * 0.05).min(0.20);
            (base + boost).min(1.0)
        } else {
            base
        }
    }

    /// Resolve severity conflicts between detectors
    fn resolve_severity(&self, findings: &[Finding]) -> Severity {
        if findings.is_empty() {
            return Severity::Medium;
        }

        match self.severity_resolution {
            SeverityResolution::Highest => findings
                .iter()
                .map(|f| f.severity)
                .max()
                .unwrap_or(Severity::Medium),

            SeverityResolution::Lowest => findings
                .iter()
                .map(|f| f.severity)
                .min()
                .unwrap_or(Severity::Medium),

            SeverityResolution::MajorityVote => {
                // Most common severity
                let mut counts: HashMap<Severity, usize> = HashMap::new();
                for finding in findings {
                    *counts.entry(finding.severity).or_insert(0) += 1;
                }
                counts
                    .into_iter()
                    .max_by_key(|(_, count)| *count)
                    .map(|(sev, _)| sev)
                    .unwrap_or(Severity::Medium)
            }

            SeverityResolution::WeightedVote => {
                // Weight by confidence
                let mut severity_scores: HashMap<Severity, f64> = HashMap::new();
                for finding in findings {
                    let conf = self.get_finding_confidence(finding);
                    let weight = self.get_detector_weight(&finding.detector);
                    *severity_scores.entry(finding.severity).or_insert(0.0) += conf * weight;
                }
                severity_scores
                    .into_iter()
                    .max_by(|(_, a), (_, b)| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal))
                    .map(|(sev, _)| sev)
                    .unwrap_or(Severity::Medium)
            }
        }
    }

    /// Get confidence score for a finding
    fn get_finding_confidence(&self, finding: &Finding) -> f64 {
        // Read from finding if available, otherwise use detector accuracy as proxy
        if let Some(conf) = finding.confidence {
            return conf.clamp(0.0, 1.0);
        }
        
        // Fall back to detector's accuracy rating as confidence proxy
        self.detector_weights
            .get(&finding.detector)
            .or_else(|| self.detector_weights.get("default"))
            .map(|w| w.accuracy)
            .unwrap_or(0.7)
    }

    /// Get weight for a detector
    fn get_detector_weight(&self, detector_name: &str) -> f64 {
        self.detector_weights
            .get(detector_name)
            .or_else(|| self.detector_weights.get("default"))
            .map(|w| w.weight)
            .unwrap_or(1.0)
    }

    /// Create merged finding from consensus
    fn create_consensus_finding(
        &self,
        findings: &[Finding],
        consensus: &ConsensusResult,
    ) -> Finding {
        // Use highest severity finding as base
        let mut sorted_findings = findings.to_vec();
        sorted_findings.sort_by(|a, b| b.severity.cmp(&a.severity));
        let base = &sorted_findings[0];

        // Create descriptive detector name
        let detector_names: Vec<&str> = consensus
            .contributing_detectors
            .iter()
            .take(3)
            .map(|s| s.as_str())
            .collect();

        let detector_str = if consensus.contributing_detectors.len() > 3 {
            format!(
                "Consensus[{}+{}more]",
                detector_names.join("+"),
                consensus.contributing_detectors.len() - 3
            )
        } else {
            format!("Consensus[{}]", detector_names.join("+"))
        };

        let consensus_note = format!(
            "\n\n**Consensus Analysis**\n\
             - {} detectors agree on this issue\n\
             - Confidence: {:.0}%\n\
             - Detectors: {}",
            consensus.vote_count,
            consensus.confidence * 100.0,
            consensus.contributing_detectors.join(", ")
        );

        Finding {
            id: base.id.clone(),
            detector: detector_str,
            severity: consensus.severity,
            title: format!("{} [{} detectors]", base.title, consensus.vote_count),
            description: format!("{}{}", base.description, consensus_note),
            affected_files: base.affected_files.clone(),
            line_start: base.line_start,
            line_end: base.line_end,
            suggested_fix: self.merge_suggestions(findings),
            estimated_effort: base.estimated_effort.clone(),
            category: base.category.clone(),
            cwe_id: base.cwe_id.clone(),
            why_it_matters: base.why_it_matters.clone(),
            confidence: Some(consensus.confidence),
            ..Default::default()
        }
    }

    /// Merge fix suggestions from multiple findings
    fn merge_suggestions(&self, findings: &[Finding]) -> Option<String> {
        let mut suggestions = Vec::new();
        let mut seen = HashSet::new();

        for f in findings {
            if let Some(ref fix) = f.suggested_fix {
                if !seen.contains(fix) {
                    suggestions.push(format!("[{}] {}", f.detector, fix));
                    seen.insert(fix.clone());
                }
            }
        }

        if suggestions.is_empty() {
            findings.first().and_then(|f| f.suggested_fix.clone())
        } else {
            Some(suggestions.join("\n\n"))
        }
    }
}

