//! Root Cause Analyzer for cross-detector pattern recognition
//!
//! Identifies root causes of issues by analyzing relationships between
//! findings from multiple detectors. Enables prioritized refactoring by showing
//! that fixing one issue (e.g., god class) resolves many cascading issues.
//!
//! # The "God Class Cascade" Pattern
//!
//! ```text
//! GodClass → CircularDependency (imports everything)
//!          → FeatureEnvy (methods use external classes)
//!          → ShotgunSurgery (everyone imports it)
//!          → InappropriateIntimacy (bidirectional coupling)
//!          → CodeDuplication (copy-paste instead of import)
//! ```

use crate::models::{Finding, Severity};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use tracing::{debug, info};

/// Analysis result showing root cause and cascading issues
#[derive(Debug, Clone)]
pub struct RootCauseAnalysis {
    pub root_cause_finding: Finding,
    pub root_cause_type: String, // "god_class", "circular_dependency", etc.
    pub cascading_findings: Vec<Finding>,
    pub impact_score: f64,            // Higher = more impact if fixed
    pub estimated_resolved_count: i32,
    pub refactoring_priority: String, // LOW, MEDIUM, HIGH, CRITICAL
    pub suggested_approach: String,
}

/// Summary statistics of root cause analysis
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct RootCauseSummary {
    pub total_root_causes: usize,
    pub total_cascading_issues: usize,
    pub root_causes_by_type: HashMap<String, usize>,
    pub average_impact_score: f64,
    pub high_priority_count: usize,
}

/// Detector names for categorization
const GOD_CLASS_DETECTOR: &str = "GodClassDetector";
const CIRCULAR_DEP_DETECTOR: &str = "CircularDependencyDetector";
const FEATURE_ENVY_DETECTOR: &str = "FeatureEnvyDetector";
const SHOTGUN_SURGERY_DETECTOR: &str = "ShotgunSurgeryDetector";
const INTIMACY_DETECTOR: &str = "InappropriateIntimacyDetector";
const MIDDLE_MAN_DETECTOR: &str = "MiddleManDetector";

/// Analyzes findings to identify root causes and cascading issues
///
/// Uses cross-detector patterns to find:
/// 1. God classes causing circular dependencies
/// 2. Feature envy caused by god classes
/// 3. Shotgun surgery linked to high-coupling classes
/// 4. Inappropriate intimacy from bidirectional god class dependencies
pub struct RootCauseAnalyzer {
    analyses: Vec<RootCauseAnalysis>,
}

impl Default for RootCauseAnalyzer {
    fn default() -> Self {
        Self::new()
    }
}

impl RootCauseAnalyzer {
    /// Create a new root cause analyzer
    pub fn new() -> Self {
        Self {
            analyses: Vec::new(),
        }
    }

    /// Analyze findings and enrich them with root cause information
    pub fn analyze(&mut self, findings: Vec<Finding>) -> Vec<Finding> {
        if findings.is_empty() {
            return findings;
        }

        // Group findings by detector
        let by_detector = self.group_by_detector(&findings);

        // Group findings by file
        let by_file = self.group_by_file(&findings);

        // Analyze god class cascade pattern
        self.analyze_god_class_cascade(&by_detector, &by_file);

        // Analyze circular dependency root causes
        self.analyze_circular_dep_causes(&by_detector, &by_file);

        // Enrich original findings with root cause info
        let enriched = self.enrich_findings(findings);

        info!(
            "RootCauseAnalyzer found {} root cause patterns affecting {} findings",
            self.analyses.len(),
            self.analyses
                .iter()
                .map(|a| a.estimated_resolved_count)
                .sum::<i32>()
        );

        enriched
    }

    /// Group findings by detector name
    fn group_by_detector<'a>(&self, findings: &'a [Finding]) -> HashMap<&'a str, Vec<&'a Finding>> {
        let mut grouped: HashMap<&str, Vec<&Finding>> = HashMap::new();
        for finding in findings {
            grouped
                .entry(finding.detector.as_str())
                .or_default()
                .push(finding);
        }
        grouped
    }

    /// Group findings by affected file
    fn group_by_file<'a>(&self, findings: &'a [Finding]) -> HashMap<String, Vec<&'a Finding>> {
        let mut grouped: HashMap<String, Vec<&Finding>> = HashMap::new();
        for finding in findings {
            for file_path in &finding.affected_files {
                let path_str = file_path.to_string_lossy().to_string();
                grouped.entry(path_str).or_default().push(finding);
            }
        }
        grouped
    }

    /// Identify god classes that cause cascading issues
    fn analyze_god_class_cascade(
        &mut self,
        by_detector: &HashMap<&str, Vec<&Finding>>,
        by_file: &HashMap<String, Vec<&Finding>>,
    ) {
        let god_classes = by_detector.get(GOD_CLASS_DETECTOR).cloned().unwrap_or_default();

        for god_class in god_classes {
            let mut cascading = Vec::new();
            let god_class_files: HashSet<String> = god_class
                .affected_files
                .iter()
                .map(|p| p.to_string_lossy().to_string())
                .collect();

            // Check for circular dependencies involving this god class
            if let Some(circ_deps) = by_detector.get(CIRCULAR_DEP_DETECTOR) {
                for circ_dep in circ_deps {
                    let circ_files: HashSet<String> = circ_dep
                        .affected_files
                        .iter()
                        .map(|p| p.to_string_lossy().to_string())
                        .collect();
                    if !god_class_files.is_disjoint(&circ_files) {
                        cascading.push((*circ_dep).clone());
                    }
                }
            }

            // Check for shotgun surgery (god class is widely used)
            if let Some(shotguns) = by_detector.get(SHOTGUN_SURGERY_DETECTOR) {
                for shotgun in shotguns {
                    let shotgun_files: HashSet<String> = shotgun
                        .affected_files
                        .iter()
                        .map(|p| p.to_string_lossy().to_string())
                        .collect();
                    if !god_class_files.is_disjoint(&shotgun_files) {
                        cascading.push((*shotgun).clone());
                    }
                }
            }

            // Check for inappropriate intimacy
            if let Some(intimacies) = by_detector.get(INTIMACY_DETECTOR) {
                for intimacy in intimacies {
                    let intimacy_files: HashSet<String> = intimacy
                        .affected_files
                        .iter()
                        .map(|p| p.to_string_lossy().to_string())
                        .collect();
                    if !god_class_files.is_disjoint(&intimacy_files) {
                        cascading.push((*intimacy).clone());
                    }
                }
            }

            // Check for findings in same files
            for file_path in &god_class_files {
                if let Some(file_findings) = by_file.get(file_path) {
                    for finding in file_findings {
                        if finding.id != god_class.id
                            && !cascading.iter().any(|c| c.id == finding.id)
                        {
                            // Only add if it's a related detector type
                            let related_detectors = [
                                CIRCULAR_DEP_DETECTOR,
                                FEATURE_ENVY_DETECTOR,
                                SHOTGUN_SURGERY_DETECTOR,
                                INTIMACY_DETECTOR,
                                MIDDLE_MAN_DETECTOR,
                            ];
                            if related_detectors.contains(&finding.detector.as_str()) {
                                cascading.push((*finding).clone());
                            }
                        }
                    }
                }
            }

            if !cascading.is_empty() {
                // Calculate impact score
                let impact = self.calculate_impact_score(god_class, &cascading);
                let priority = self.calculate_priority(god_class, &cascading);

                let analysis = RootCauseAnalysis {
                    root_cause_finding: god_class.clone(),
                    root_cause_type: "god_class".to_string(),
                    cascading_findings: cascading.clone(),
                    impact_score: impact,
                    estimated_resolved_count: (cascading.len() + 1) as i32,
                    refactoring_priority: priority,
                    suggested_approach: self.suggest_god_class_refactoring(god_class, &cascading),
                };
                self.analyses.push(analysis);
            }
        }
    }

    /// Identify root causes of circular dependencies
    fn analyze_circular_dep_causes(
        &mut self,
        by_detector: &HashMap<&str, Vec<&Finding>>,
        _by_file: &HashMap<String, Vec<&Finding>>,
    ) {
        let circular_deps = by_detector
            .get(CIRCULAR_DEP_DETECTOR)
            .cloned()
            .unwrap_or_default();

        // Collect files already identified as god class root causes
        let god_class_files: HashSet<String> = self
            .analyses
            .iter()
            .filter(|a| a.root_cause_type == "god_class")
            .flat_map(|a| {
                a.root_cause_finding
                    .affected_files
                    .iter()
                    .map(|p| p.to_string_lossy().to_string())
            })
            .collect();

        for circ_dep in circular_deps {
            // Skip if already linked to a god class
            let circ_files: HashSet<String> = circ_dep
                .affected_files
                .iter()
                .map(|p| p.to_string_lossy().to_string())
                .collect();
            if !god_class_files.is_disjoint(&circ_files) {
                continue;
            }

            // Check for inappropriate intimacy as root cause
            let mut cascading = Vec::new();
            if let Some(intimacies) = by_detector.get(INTIMACY_DETECTOR) {
                for intimacy in intimacies {
                    let intimacy_files: HashSet<String> = intimacy
                        .affected_files
                        .iter()
                        .map(|p| p.to_string_lossy().to_string())
                        .collect();
                    if !circ_files.is_disjoint(&intimacy_files) {
                        cascading.push((*intimacy).clone());
                    }
                }
            }

            if !cascading.is_empty() {
                // Circular dep is the root cause, intimacy is related
                let impact = self.calculate_impact_score(circ_dep, &cascading);
                let priority = self.calculate_priority(circ_dep, &cascading);

                let analysis = RootCauseAnalysis {
                    root_cause_finding: circ_dep.clone(),
                    root_cause_type: "circular_dependency".to_string(),
                    cascading_findings: cascading.clone(),
                    impact_score: impact,
                    estimated_resolved_count: (cascading.len() + 1) as i32,
                    refactoring_priority: priority,
                    suggested_approach: self.suggest_circular_dep_refactoring(circ_dep),
                };
                self.analyses.push(analysis);
            }
        }
    }

    /// Calculate impact score for fixing the root cause
    fn calculate_impact_score(&self, root_cause: &Finding, cascading: &[Finding]) -> f64 {
        let severity_scores: HashMap<Severity, f64> = [
            (Severity::Critical, 4.0),
            (Severity::High, 3.0),
            (Severity::Medium, 2.0),
            (Severity::Low, 1.0),
            (Severity::Info, 0.5),
        ]
        .into_iter()
        .collect();

        let base_score = severity_scores
            .get(&root_cause.severity)
            .copied()
            .unwrap_or(1.0);

        // Add score for each cascading issue
        let cascade_score: f64 = cascading
            .iter()
            .map(|f| severity_scores.get(&f.severity).copied().unwrap_or(1.0) * 0.5)
            .sum();

        // Bonus for number of cascading issues
        let count_bonus = (cascading.len() as f64 * 0.3).min(2.0);

        let total = base_score + cascade_score + count_bonus;

        // Normalize to 0-10 scale
        total.min(10.0)
    }

    /// Calculate refactoring priority
    fn calculate_priority(&self, root_cause: &Finding, cascading: &[Finding]) -> String {
        // Count high-severity cascading issues
        let critical_count = cascading
            .iter()
            .filter(|f| f.severity == Severity::Critical)
            .count();
        let high_count = cascading
            .iter()
            .filter(|f| f.severity == Severity::High)
            .count();

        if root_cause.severity == Severity::Critical || critical_count >= 1 {
            "CRITICAL".to_string()
        } else if root_cause.severity == Severity::High || high_count >= 2 {
            "HIGH".to_string()
        } else if cascading.len() >= 3 {
            "HIGH".to_string()
        } else if !cascading.is_empty() {
            "MEDIUM".to_string()
        } else {
            "LOW".to_string()
        }
    }

    /// Generate refactoring suggestion for god class root cause
    fn suggest_god_class_refactoring(&self, god_class: &Finding, cascading: &[Finding]) -> String {
        let class_name = god_class
            .title
            .split(':')
            .last()
            .unwrap_or("the class")
            .trim();

        // Count cascading issue types
        let has_circular = cascading
            .iter()
            .any(|f| f.detector == CIRCULAR_DEP_DETECTOR);
        let has_shotgun = cascading
            .iter()
            .any(|f| f.detector == SHOTGUN_SURGERY_DETECTOR);

        let mut suggestions = vec![format!(
            "ROOT CAUSE: God class '{}' is causing {} cascading issues.\n",
            class_name,
            cascading.len()
        )];
        suggestions.push("RECOMMENDED REFACTORING APPROACH:\n".to_string());

        let mut step = 1;
        if has_circular {
            suggestions.push(format!(
                "  {}. Extract interfaces to break circular dependencies\n",
                step
            ));
            step += 1;
        }

        suggestions.push(format!(
            "  {}. Split into focused classes by responsibility:\n\
                  - Group related methods (look at shared field access)\n\
                  - Extract each group into a dedicated class\n",
            step
        ));
        step += 1;

        if has_shotgun {
            suggestions.push(format!(
                "  {}. Create a facade to limit external coupling\n",
                step
            ));
        }

        suggestions.push(format!(
            "\nEXPECTED RESULT: Fixing '{}' will resolve ~{} related issues.",
            class_name,
            cascading.len()
        ));

        suggestions.join("")
    }

    /// Generate refactoring suggestion for circular dependency root cause
    fn suggest_circular_dep_refactoring(&self, circ_dep: &Finding) -> String {
        let cycle_length = circ_dep.affected_files.len();

        let mut suggestions = vec!["ROOT CAUSE: Circular dependency creating tight coupling.\n".to_string()];
        suggestions.push("RECOMMENDED REFACTORING APPROACH:\n".to_string());

        if cycle_length <= 3 {
            suggestions.push(
                "  1. Consider merging tightly coupled modules\n\
                   2. Or extract shared types to a common module\n\
                   3. Use TYPE_CHECKING for type-only imports\n"
                    .to_string(),
            );
        } else {
            suggestions.push(
                "  1. Identify the module with most incoming imports\n\
                   2. Extract its dependencies into interface module\n\
                   3. Apply Dependency Inversion Principle\n\
                   4. Consider using dependency injection\n"
                    .to_string(),
            );
        }

        suggestions.join("")
    }

    /// Enrich findings with root cause analysis information
    fn enrich_findings(&self, mut findings: Vec<Finding>) -> Vec<Finding> {
        // Build lookup of finding ID to analysis
        let mut root_cause_ids: HashMap<&str, &RootCauseAnalysis> = HashMap::new();
        let mut cascading_ids: HashMap<&str, &RootCauseAnalysis> = HashMap::new();

        for analysis in &self.analyses {
            root_cause_ids.insert(&analysis.root_cause_finding.id, analysis);
            for cascading in &analysis.cascading_findings {
                cascading_ids.insert(&cascading.id, analysis);
            }
        }

        // Enrich each finding
        for finding in &mut findings {
            // Check if this is a root cause
            if let Some(analysis) = root_cause_ids.get(finding.id.as_str()) {
                // Update description with root cause info
                finding.description = format!(
                    "{}\n\n\
                     📍 **ROOT CAUSE ANALYSIS**\n\
                     - Type: {}\n\
                     - Impact Score: {:.1}\n\
                     - Cascading Issues: {}\n\
                     - Priority: {}",
                    finding.description,
                    analysis.root_cause_type,
                    analysis.impact_score,
                    analysis.cascading_findings.len(),
                    analysis.refactoring_priority
                );

                // Update suggested fix with root cause approach
                if !analysis.suggested_approach.is_empty() {
                    finding.suggested_fix = Some(analysis.suggested_approach.clone());
                }
            }
            // Check if this is caused by a root cause
            else if let Some(analysis) = cascading_ids.get(finding.id.as_str()) {
                let root_name = analysis
                    .root_cause_finding
                    .title
                    .split(':')
                    .last()
                    .unwrap_or("unknown")
                    .trim();

                // Add note about root cause to description
                let root_note = if analysis.root_cause_type == "god_class" {
                    format!(
                        "\n\n📍 ROOT CAUSE: This issue is linked to god class '{}'. \
                         Fixing the god class may resolve this issue.",
                        root_name
                    )
                } else {
                    format!(
                        "\n\n📍 ROOT CAUSE: This issue is linked to {}. \
                         Fixing the root cause may resolve this issue.",
                        analysis.root_cause_type.replace('_', " ")
                    )
                };

                finding.description = format!("{}{}", finding.description, root_note);
            }
        }

        findings
    }

    /// Get all root cause analyses
    pub fn get_analyses(&self) -> &[RootCauseAnalysis] {
        &self.analyses
    }

    /// Get summary statistics of root cause analysis
    pub fn get_summary(&self) -> RootCauseSummary {
        let total_root_causes = self.analyses.len();
        let total_cascading: usize = self
            .analyses
            .iter()
            .map(|a| a.cascading_findings.len())
            .sum();

        let mut by_type: HashMap<String, usize> = HashMap::new();
        for analysis in &self.analyses {
            *by_type.entry(analysis.root_cause_type.clone()).or_insert(0) += 1;
        }

        let avg_impact = if total_root_causes > 0 {
            self.analyses.iter().map(|a| a.impact_score).sum::<f64>() / total_root_causes as f64
        } else {
            0.0
        };

        let high_priority_count = self
            .analyses
            .iter()
            .filter(|a| a.refactoring_priority == "HIGH" || a.refactoring_priority == "CRITICAL")
            .count();

        RootCauseSummary {
            total_root_causes,
            total_cascading_issues: total_cascading,
            root_causes_by_type: by_type,
            average_impact_score: (avg_impact * 100.0).round() / 100.0,
            high_priority_count,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn create_test_finding(id: &str, detector: &str, severity: Severity, file: &str) -> Finding {
        Finding {
            id: id.to_string(),
            detector: detector.to_string(),
            severity,
            title: format!("Test: {}", detector),
            description: "Test description".to_string(),
            affected_files: vec![PathBuf::from(file)],
            line_start: Some(10),
            line_end: Some(20),
            suggested_fix: Some("Fix it".to_string()),
            estimated_effort: None,
            category: None,
            cwe_id: None,
            why_it_matters: None,
        }
    }

    #[test]
    fn test_empty_findings() {
        let mut analyzer = RootCauseAnalyzer::new();
        let result = analyzer.analyze(vec![]);
        assert!(result.is_empty());
        assert!(analyzer.get_analyses().is_empty());
    }

    #[test]
    fn test_god_class_cascade() {
        let mut analyzer = RootCauseAnalyzer::new();
        let findings = vec![
            create_test_finding("1", GOD_CLASS_DETECTOR, Severity::High, "core/god.py"),
            create_test_finding(
                "2",
                CIRCULAR_DEP_DETECTOR,
                Severity::Medium,
                "core/god.py",
            ),
            create_test_finding("3", INTIMACY_DETECTOR, Severity::Medium, "core/god.py"),
        ];

        let enriched = analyzer.analyze(findings);

        assert_eq!(analyzer.get_analyses().len(), 1);
        let analysis = &analyzer.get_analyses()[0];
        assert_eq!(analysis.root_cause_type, "god_class");
        assert_eq!(analysis.cascading_findings.len(), 2);

        // Check enrichment
        let god_class = enriched.iter().find(|f| f.id == "1").unwrap();
        assert!(god_class.description.contains("ROOT CAUSE ANALYSIS"));
    }

    #[test]
    fn test_impact_score() {
        let analyzer = RootCauseAnalyzer::new();
        let root = create_test_finding("1", GOD_CLASS_DETECTOR, Severity::High, "test.py");
        let cascading = vec![
            create_test_finding("2", CIRCULAR_DEP_DETECTOR, Severity::Medium, "test.py"),
            create_test_finding("3", INTIMACY_DETECTOR, Severity::Low, "test.py"),
        ];

        let score = analyzer.calculate_impact_score(&root, &cascading);
        assert!(score > 0.0);
        assert!(score <= 10.0);
    }

    #[test]
    fn test_priority_calculation() {
        let analyzer = RootCauseAnalyzer::new();

        // Critical root cause
        let root = create_test_finding("1", GOD_CLASS_DETECTOR, Severity::Critical, "test.py");
        assert_eq!(analyzer.calculate_priority(&root, &[]), "CRITICAL");

        // High with many cascading
        let root = create_test_finding("2", GOD_CLASS_DETECTOR, Severity::Medium, "test.py");
        let cascading = vec![
            create_test_finding("3", CIRCULAR_DEP_DETECTOR, Severity::Low, "test.py"),
            create_test_finding("4", INTIMACY_DETECTOR, Severity::Low, "test.py"),
            create_test_finding("5", SHOTGUN_SURGERY_DETECTOR, Severity::Low, "test.py"),
        ];
        assert_eq!(analyzer.calculate_priority(&root, &cascading), "HIGH");
    }

    #[test]
    fn test_summary() {
        let mut analyzer = RootCauseAnalyzer::new();
        let findings = vec![
            create_test_finding("1", GOD_CLASS_DETECTOR, Severity::High, "test.py"),
            create_test_finding("2", CIRCULAR_DEP_DETECTOR, Severity::Medium, "test.py"),
        ];

        analyzer.analyze(findings);
        let summary = analyzer.get_summary();

        assert_eq!(summary.total_root_causes, 1);
        assert!(summary.root_causes_by_type.contains_key("god_class"));
    }
}
