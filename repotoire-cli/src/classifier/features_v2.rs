//! Evidence-backed 28-feature extractor for the GBDT classifier
//!
//! Unlike the original `features.rs` (51 one-hot + text features for the linear
//! classifier), this extractor produces a compact 28-dimensional f64 vector
//! grounded in graph metrics, git history, and cross-finding context.
//!
//! Feature groups:
//!   0..5   — Finding identity (detector bucket, severity, confidence, category, CWE)
//!   5..15  — Code structure (entity type, LOC, complexity, fan-in/out, SCC)
//!  15..22  — Git history (age, churn, developers, ownership)
//!  22..25  — Path signals (depth, FP indicators, TP indicators)
//!  25..28  — Cross-finding context (density, same-detector, historical FP rate)

use std::collections::hash_map::DefaultHasher;
use std::collections::HashMap;
use std::hash::{Hash, Hasher};

use crate::classifier::thresholds::DetectorCategory;
use crate::graph::traits::GraphQuery;
use crate::models::{Finding, Severity};

/// Number of features produced by the V2 extractor.
pub const NUM_FEATURES: usize = 28;

/// Number of detector hash buckets (power of 2 for uniform distribution).
const DETECTOR_BUCKETS: u64 = 32;

/// Human-readable names for each feature, in extraction order.
pub const FEATURE_NAMES: [&str; NUM_FEATURES] = [
    "detector_bucket",
    "severity_ordinal",
    "confidence",
    "detector_category",
    "has_cwe",
    "entity_type",
    "function_loc",
    "file_loc",
    "function_count_in_file",
    "finding_line_span_norm",
    "cyclomatic_complexity",
    "max_nesting_depth",
    "fan_in",
    "fan_out",
    "scc_membership",
    "file_age_log",
    "recent_churn",
    "developer_count",
    "unique_change_count",
    "is_recently_created",
    "major_contributor_pct",
    "minor_contributor_count",
    "file_depth",
    "fp_path_indicator_count",
    "tp_path_indicator_count",
    "finding_density",
    "same_detector_findings",
    "historical_fp_rate",
];

// ---------------------------------------------------------------------------
// Path indicator word lists (shared with the old extractor by convention)
// ---------------------------------------------------------------------------

/// Path segments that correlate with false positives.
const FP_PATH_INDICATORS: &[&str] = &[
    "test",
    "tests",
    "spec",
    "specs",
    "__test__",
    "__tests__",
    "fixture",
    "fixtures",
    "mock",
    "mocks",
    "example",
    "examples",
    "demo",
    "sample",
    "vendor",
    "node_modules",
    "generated",
    "dist",
    "build",
    "scripts",
    "tools",
    "benchmark",
    "benchmarks",
    "docs",
];

/// Path segments that correlate with true positives.
const TP_PATH_INDICATORS: &[&str] = &[
    "src", "lib", "app", "api", "routes", "handlers", "controller", "service", "auth", "security",
];

// ---------------------------------------------------------------------------
// Data structures
// ---------------------------------------------------------------------------

/// 28-dimensional feature vector (f64 for GBDT compatibility).
#[derive(Debug, Clone)]
pub struct FeaturesV2 {
    pub values: [f64; NUM_FEATURES],
}

impl FeaturesV2 {
    /// Create from a fixed-size array.
    pub fn new(values: [f64; NUM_FEATURES]) -> Self {
        Self { values }
    }

    /// Return as a slice (for GBDT `Data`).
    pub fn as_slice(&self) -> &[f64] {
        &self.values
    }

    /// Return a Vec<f64> (for GBDT `Data::new_test_data`).
    pub fn to_vec(&self) -> Vec<f64> {
        self.values.to_vec()
    }
}

/// Pre-computed per-file git history features.
///
/// Callers build this from `git2` blame/log data. Features 16-22 default to
/// 0.0 when the git context is unavailable.
#[derive(Debug, Clone, Default)]
pub struct GitFeatures {
    /// ln(file_age_days + 1)
    pub file_age_log: f64,
    /// Number of commits touching this file in the recent window (e.g. 90 days).
    pub recent_churn: f64,
    /// Number of distinct commit authors.
    pub developer_count: f64,
    /// Total number of distinct commits touching this file.
    pub unique_change_count: f64,
    /// 1.0 if the file was created within the recent window.
    pub is_recently_created: f64,
    /// Percentage of lines owned by the top contributor (0.0..1.0).
    pub major_contributor_pct: f64,
    /// Number of contributors with < 10% ownership.
    pub minor_contributor_count: f64,
}

impl GitFeatures {
    /// Convert a `FileChurn` from the git history module into `GitFeatures`.
    ///
    /// `now_epoch` should be the current Unix timestamp (seconds since epoch).
    pub fn from_file_churn(churn: &crate::git::history::FileChurn, now_epoch: i64) -> Self {
        let age_days = churn
            .last_modified
            .as_ref()
            .and_then(|ts| chrono::DateTime::parse_from_rfc3339(ts).ok())
            .map(|dt| ((now_epoch - dt.timestamp()) as f64 / 86400.0).max(0.0))
            .unwrap_or(0.0);
        let author_count = churn.authors.len();
        Self {
            file_age_log: (age_days + 1.0).ln(),
            recent_churn: churn.commit_count as f64,
            developer_count: author_count as f64,
            unique_change_count: churn.commit_count as f64,
            is_recently_created: if age_days < 30.0 { 1.0 } else { 0.0 },
            major_contributor_pct: 1.0 / (author_count.max(1) as f64),
            minor_contributor_count: author_count.saturating_sub(1) as f64,
        }
    }
}

/// Pre-computed cross-finding context for a file.
///
/// Callers aggregate these from the full finding set before extraction.
/// Features 26-28 default to 0.0 when unavailable.
#[derive(Debug, Clone, Default)]
pub struct CrossFindingFeatures {
    /// findings_in_file / kLOC
    pub finding_density: f64,
    /// Count of findings in the same file from the same detector.
    pub same_detector_findings: f64,
    /// Historical FP rate for this detector (0.0..1.0).
    pub historical_fp_rate: f64,
}

/// Compute per-file, per-detector cross-finding context from the full finding set.
///
/// Returns `file_path -> detector -> CrossFindingFeatures`.
pub fn compute_cross_features(
    findings: &[Finding],
    file_loc_map: &HashMap<String, f64>,
) -> HashMap<String, HashMap<String, CrossFindingFeatures>> {
    // Count findings per (file, detector)
    let mut file_detector_counts: HashMap<String, HashMap<String, usize>> = HashMap::new();
    let mut file_total_counts: HashMap<String, usize> = HashMap::new();

    for finding in findings {
        let file_path = finding
            .affected_files
            .first()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default();
        if file_path.is_empty() {
            continue;
        }

        *file_total_counts.entry(file_path.clone()).or_insert(0) += 1;
        *file_detector_counts
            .entry(file_path)
            .or_default()
            .entry(finding.detector.clone())
            .or_insert(0) += 1;
    }

    let mut result: HashMap<String, HashMap<String, CrossFindingFeatures>> = HashMap::new();

    for (file_path, detector_counts) in &file_detector_counts {
        let total_in_file = file_total_counts.get(file_path).copied().unwrap_or(0);
        let kloc = file_loc_map
            .get(file_path)
            .copied()
            .unwrap_or(0.0)
            / 1000.0;
        let kloc_safe = kloc.max(0.001);

        let file_map = result.entry(file_path.clone()).or_default();
        for (detector, &count) in detector_counts {
            file_map.insert(
                detector.clone(),
                CrossFindingFeatures {
                    finding_density: total_in_file as f64 / kloc_safe,
                    same_detector_findings: count as f64,
                    historical_fp_rate: 0.0, // no historical data for seed model
                },
            );
        }
    }

    result
}

/// The V2 feature extractor.
///
/// Stateless — all configuration is in the associated constants.
pub struct FeatureExtractorV2;

impl FeatureExtractorV2 {
    pub fn new() -> Self {
        Self
    }

    /// Extract a 28-feature vector from a finding with optional graph, git,
    /// and cross-finding context.
    ///
    /// When `graph` is `None`, graph-derived features (6-14) are zero.
    /// When `git` is `None`, git features (15-21) are zero.
    /// When `cross` is `None`, cross-finding features (25-27) are zero.
    pub fn extract(
        &self,
        finding: &Finding,
        graph: Option<&dyn GraphQuery>,
        git: Option<&GitFeatures>,
        cross: Option<&CrossFindingFeatures>,
    ) -> FeaturesV2 {
        let mut f = [0.0_f64; NUM_FEATURES];

        // --- Finding identity (0..5) ---

        // 0: detector_bucket — hash detector name into 0..32
        f[0] = detector_bucket(&finding.detector) as f64;

        // 1: severity_ordinal
        f[1] = severity_ordinal(finding.severity);

        // 2: confidence
        f[2] = finding.confidence.unwrap_or(0.5);

        // 3: detector_category ordinal
        f[3] = category_ordinal(&finding.detector);

        // 4: has_cwe
        f[4] = if finding.cwe_id.is_some() { 1.0 } else { 0.0 };

        // --- Code structure (5..15) — require graph ---
        let file_path = finding
            .affected_files
            .first()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default();

        let finding_line = finding.line_start.unwrap_or(0);
        let finding_end = finding.line_end.unwrap_or(finding_line);

        if let Some(g) = graph {
            // Look up the containing function (the function whose line range
            // covers the finding's line_start).
            let functions_in_file = g.get_functions_in_file(&file_path);
            let containing_fn = functions_in_file.iter().find(|func| {
                finding_line >= func.line_start && finding_line <= func.line_end
            });

            // Look up the containing class.
            let classes_in_file = g.get_classes_in_file(&file_path);
            let containing_class = classes_in_file.iter().find(|cls| {
                finding_line >= cls.line_start && finding_line <= cls.line_end
            });

            // 5: entity_type — 0=file, 1=function, 2=class
            f[5] = if containing_fn.is_some() {
                1.0
            } else if containing_class.is_some() {
                2.0
            } else {
                0.0
            };

            // 6: function_loc — containing function's line count
            if let Some(func) = containing_fn {
                let loc = func.loc();
                f[6] = loc as f64;

                // 10: cyclomatic_complexity
                f[10] = func.complexity().unwrap_or(0) as f64;

                // 11: max_nesting_depth
                f[11] = func.get_i64("nesting_depth").unwrap_or(0) as f64;

                // 12: fan_in
                f[12] = g.call_fan_in(&func.qualified_name) as f64;

                // 13: fan_out
                f[13] = g.call_fan_out(&func.qualified_name) as f64;

                // 9: finding_line_span_norm — span / function_loc
                let span = finding_end.saturating_sub(finding_line).saturating_add(1) as f64;
                let fn_loc = loc.max(1) as f64;
                f[9] = (span / fn_loc).min(1.0);
            } else {
                // If no containing function, span norm uses a default.
                let span = finding_end.saturating_sub(finding_line).saturating_add(1) as f64;
                f[9] = (span / 100.0).min(1.0); // normalise against 100 lines
            }

            // 7: file_loc — sum of function sizes in file
            let file_loc: u32 = functions_in_file.iter().map(|func| func.loc()).sum();
            f[7] = file_loc as f64;

            // 8: function_count_in_file
            f[8] = functions_in_file.len() as f64;

            // 14: scc_membership — is the file part of an import cycle?
            let cycles = g.find_import_cycles();
            let in_cycle = cycles.iter().any(|cycle| {
                cycle.iter().any(|node_qn| {
                    // Cycle node qualified names might be module or file paths.
                    node_qn == &file_path || file_path.contains(node_qn.as_str())
                })
            });
            f[14] = if in_cycle { 1.0 } else { 0.0 };
        } else {
            // No graph — only compute normalised span with default.
            let span = finding_end.saturating_sub(finding_line).saturating_add(1) as f64;
            f[9] = (span / 100.0).min(1.0);
        }

        // --- Git history (15..22) ---
        if let Some(git) = git {
            f[15] = git.file_age_log;
            f[16] = git.recent_churn;
            f[17] = git.developer_count;
            f[18] = git.unique_change_count;
            f[19] = git.is_recently_created;
            f[20] = git.major_contributor_pct;
            f[21] = git.minor_contributor_count;
        }
        // else: already zero-initialised

        // --- Path signals (22..25) ---
        let path_lower = file_path.to_lowercase();

        // 22: file_depth — count of '/' separators
        f[22] = path_lower.matches('/').count() as f64;

        // 23: fp_path_indicator_count
        f[23] = FP_PATH_INDICATORS
            .iter()
            .filter(|p| path_lower.contains(**p))
            .count() as f64;

        // 24: tp_path_indicator_count
        f[24] = TP_PATH_INDICATORS
            .iter()
            .filter(|p| path_lower.contains(**p))
            .count() as f64;

        // --- Cross-finding context (25..28) ---
        if let Some(cross) = cross {
            f[25] = cross.finding_density;
            f[26] = cross.same_detector_findings;
            f[27] = cross.historical_fp_rate;
        }
        // else: already zero-initialised

        FeaturesV2::new(f)
    }
}

impl Default for FeatureExtractorV2 {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// Helper functions
// ---------------------------------------------------------------------------

/// Hash the detector name into a bucket in [0, DETECTOR_BUCKETS).
fn detector_bucket(detector: &str) -> u64 {
    let mut hasher = DefaultHasher::new();
    detector.hash(&mut hasher);
    hasher.finish() % DETECTOR_BUCKETS
}

/// Map `Severity` to an ordinal: Critical=3, High=2, Medium=1, Low/Info=0.
fn severity_ordinal(s: Severity) -> f64 {
    match s {
        Severity::Critical => 3.0,
        Severity::High => 2.0,
        Severity::Medium => 1.0,
        Severity::Low | Severity::Info => 0.0,
    }
}

/// Map a detector name to a category ordinal.
fn category_ordinal(detector: &str) -> f64 {
    match DetectorCategory::from_detector(detector) {
        DetectorCategory::Security => 0.0,
        DetectorCategory::CodeQuality => 1.0,
        DetectorCategory::MachineLearning => 2.0,
        DetectorCategory::Performance => 3.0,
        DetectorCategory::Other => 4.0,
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::store_models::CodeNode;
    use crate::graph::traits::GraphQuery;
    use std::collections::HashMap;
    use std::path::PathBuf;

    // -----------------------------------------------------------------------
    // Minimal in-memory graph for testing
    // -----------------------------------------------------------------------

    /// A tiny graph implementation sufficient for feature extraction tests.
    struct TestGraph {
        functions: Vec<CodeNode>,
        classes: Vec<CodeNode>,
        cycles: Vec<Vec<String>>,
    }

    impl TestGraph {
        fn empty() -> Self {
            Self {
                functions: Vec::new(),
                classes: Vec::new(),
                cycles: Vec::new(),
            }
        }

        fn with_function(mut self, func: CodeNode) -> Self {
            self.functions.push(func);
            self
        }

        fn with_class(mut self, cls: CodeNode) -> Self {
            self.classes.push(cls);
            self
        }

        #[allow(dead_code)]
        fn with_cycle(mut self, cycle: Vec<String>) -> Self {
            self.cycles.push(cycle);
            self
        }
    }

    impl GraphQuery for TestGraph {
        fn get_functions(&self) -> Vec<CodeNode> {
            self.functions.clone()
        }
        fn get_classes(&self) -> Vec<CodeNode> {
            self.classes.clone()
        }
        fn get_files(&self) -> Vec<CodeNode> {
            Vec::new()
        }
        fn get_functions_in_file(&self, file_path: &str) -> Vec<CodeNode> {
            self.functions
                .iter()
                .filter(|f| f.file_path == file_path)
                .cloned()
                .collect()
        }
        fn get_classes_in_file(&self, file_path: &str) -> Vec<CodeNode> {
            self.classes
                .iter()
                .filter(|c| c.file_path == file_path)
                .cloned()
                .collect()
        }
        fn get_node(&self, _qn: &str) -> Option<CodeNode> {
            None
        }
        fn get_callers(&self, _qn: &str) -> Vec<CodeNode> {
            Vec::new()
        }
        fn get_callees(&self, _qn: &str) -> Vec<CodeNode> {
            Vec::new()
        }
        fn call_fan_in(&self, _qn: &str) -> usize {
            3 // fixed for tests
        }
        fn call_fan_out(&self, _qn: &str) -> usize {
            2 // fixed for tests
        }
        fn get_calls(&self) -> Vec<(String, String)> {
            Vec::new()
        }
        fn get_imports(&self) -> Vec<(String, String)> {
            Vec::new()
        }
        fn get_inheritance(&self) -> Vec<(String, String)> {
            Vec::new()
        }
        fn get_child_classes(&self, _qn: &str) -> Vec<CodeNode> {
            Vec::new()
        }
        fn get_importers(&self, _qn: &str) -> Vec<CodeNode> {
            Vec::new()
        }
        fn find_import_cycles(&self) -> Vec<Vec<String>> {
            self.cycles.clone()
        }
        fn stats(&self) -> HashMap<String, i64> {
            HashMap::new()
        }
    }

    // -----------------------------------------------------------------------
    // Helper: build a test finding
    // -----------------------------------------------------------------------

    fn make_finding() -> Finding {
        Finding {
            id: "test-1".into(),
            detector: "SQLInjectionDetector".into(),
            severity: Severity::High,
            title: "SQL injection in query".into(),
            description: "User input flows to exec()".into(),
            affected_files: vec![PathBuf::from("src/api/users.py")],
            line_start: Some(20),
            line_end: Some(25),
            suggested_fix: Some("Use parameterized queries".into()),
            cwe_id: Some("CWE-89".into()),
            confidence: Some(0.92),
            ..Default::default()
        }
    }

    fn make_graph() -> TestGraph {
        let func = CodeNode::function("handle_query", "src/api/users.py")
            .with_qualified_name("src.api.users.handle_query")
            .with_lines(10, 50)
            .with_property("complexity", 8)
            .with_property("nesting_depth", 3);

        TestGraph::empty().with_function(func)
    }

    // -----------------------------------------------------------------------
    // Tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_extracts_28_features() {
        let extractor = FeatureExtractorV2::new();
        let finding = make_finding();
        let graph = make_graph();

        let git = GitFeatures {
            file_age_log: 4.5,
            recent_churn: 12.0,
            developer_count: 3.0,
            unique_change_count: 45.0,
            is_recently_created: 0.0,
            major_contributor_pct: 0.65,
            minor_contributor_count: 1.0,
        };

        let cross = CrossFindingFeatures {
            finding_density: 5.2,
            same_detector_findings: 2.0,
            historical_fp_rate: 0.15,
        };

        let features =
            extractor.extract(&finding, Some(&graph), Some(&git), Some(&cross));

        // Must produce exactly 28 features.
        assert_eq!(features.values.len(), NUM_FEATURES);
        assert_eq!(features.values.len(), 28);

        // Spot-check a few values.
        // severity_ordinal: High = 2.0
        assert!((features.values[1] - 2.0).abs() < f64::EPSILON);
        // confidence: 0.92
        assert!((features.values[2] - 0.92).abs() < f64::EPSILON);
        // detector_category: Security = 0.0
        assert!((features.values[3] - 0.0).abs() < f64::EPSILON);
        // has_cwe: 1.0
        assert!((features.values[4] - 1.0).abs() < f64::EPSILON);
        // entity_type: inside function = 1.0
        assert!((features.values[5] - 1.0).abs() < f64::EPSILON);
        // function_loc: 50 - 10 + 1 = 41
        assert!((features.values[6] - 41.0).abs() < f64::EPSILON);
        // cyclomatic_complexity: 8
        assert!((features.values[10] - 8.0).abs() < f64::EPSILON);
        // max_nesting_depth: 3
        assert!((features.values[11] - 3.0).abs() < f64::EPSILON);
        // fan_in: 3 (test graph fixed)
        assert!((features.values[12] - 3.0).abs() < f64::EPSILON);
        // fan_out: 2 (test graph fixed)
        assert!((features.values[13] - 2.0).abs() < f64::EPSILON);
        // git: file_age_log = 4.5
        assert!((features.values[15] - 4.5).abs() < f64::EPSILON);
        // cross: finding_density = 5.2
        assert!((features.values[25] - 5.2).abs() < f64::EPSILON);
    }

    #[test]
    fn test_features_without_graph_context() {
        let extractor = FeatureExtractorV2::new();
        let finding = make_finding();

        // No graph, no git, no cross-finding context.
        let features = extractor.extract(&finding, None, None, None);

        assert_eq!(features.values.len(), NUM_FEATURES);

        // Identity features should still be populated.
        assert!((features.values[1] - 2.0).abs() < f64::EPSILON); // severity
        assert!((features.values[2] - 0.92).abs() < f64::EPSILON); // confidence
        assert!((features.values[4] - 1.0).abs() < f64::EPSILON); // has_cwe

        // Graph features should be zero (no graph).
        assert!((features.values[5]).abs() < f64::EPSILON); // entity_type = file-level
        assert!((features.values[6]).abs() < f64::EPSILON); // function_loc
        assert!((features.values[10]).abs() < f64::EPSILON); // complexity
        assert!((features.values[12]).abs() < f64::EPSILON); // fan_in
        assert!((features.values[13]).abs() < f64::EPSILON); // fan_out

        // Git features should be zero.
        for i in 15..22 {
            assert!(
                features.values[i].abs() < f64::EPSILON,
                "git feature {} should be 0.0, got {}",
                i,
                features.values[i]
            );
        }

        // Cross-finding features should be zero.
        for i in 25..28 {
            assert!(
                features.values[i].abs() < f64::EPSILON,
                "cross-finding feature {} should be 0.0, got {}",
                i,
                features.values[i]
            );
        }

        // Path features should still be populated.
        // file_depth: "src/api/users.py" has 2 slashes
        assert!((features.values[22] - 2.0).abs() < f64::EPSILON);
        // tp_path_indicator: "src" and "api" match
        assert!(features.values[24] >= 2.0);
    }

    #[test]
    fn test_git_features_populated() {
        let extractor = FeatureExtractorV2::new();
        let finding = make_finding();

        let git = GitFeatures {
            file_age_log: 6.2,
            recent_churn: 20.0,
            developer_count: 5.0,
            unique_change_count: 100.0,
            is_recently_created: 1.0,
            major_contributor_pct: 0.80,
            minor_contributor_count: 3.0,
        };

        let features = extractor.extract(&finding, None, Some(&git), None);

        assert!((features.values[15] - 6.2).abs() < f64::EPSILON);
        assert!((features.values[16] - 20.0).abs() < f64::EPSILON);
        assert!((features.values[17] - 5.0).abs() < f64::EPSILON);
        assert!((features.values[18] - 100.0).abs() < f64::EPSILON);
        assert!((features.values[19] - 1.0).abs() < f64::EPSILON);
        assert!((features.values[20] - 0.80).abs() < f64::EPSILON);
        assert!((features.values[21] - 3.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_cross_finding_features() {
        let extractor = FeatureExtractorV2::new();
        let finding = make_finding();

        let cross = CrossFindingFeatures {
            finding_density: 12.5,
            same_detector_findings: 4.0,
            historical_fp_rate: 0.33,
        };

        let features = extractor.extract(&finding, None, None, Some(&cross));

        assert!((features.values[25] - 12.5).abs() < f64::EPSILON);
        assert!((features.values[26] - 4.0).abs() < f64::EPSILON);
        assert!((features.values[27] - 0.33).abs() < f64::EPSILON);
    }

    #[test]
    fn test_feature_names_match_count() {
        assert_eq!(
            FEATURE_NAMES.len(),
            NUM_FEATURES,
            "FEATURE_NAMES array length must equal NUM_FEATURES ({})",
            NUM_FEATURES
        );
    }

    #[test]
    fn test_severity_ordinal_mapping() {
        assert!((severity_ordinal(Severity::Critical) - 3.0).abs() < f64::EPSILON);
        assert!((severity_ordinal(Severity::High) - 2.0).abs() < f64::EPSILON);
        assert!((severity_ordinal(Severity::Medium) - 1.0).abs() < f64::EPSILON);
        assert!((severity_ordinal(Severity::Low) - 0.0).abs() < f64::EPSILON);
        assert!((severity_ordinal(Severity::Info) - 0.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_detector_bucket_deterministic() {
        let a = detector_bucket("SQLInjectionDetector");
        let b = detector_bucket("SQLInjectionDetector");
        assert_eq!(a, b);
        assert!(a < DETECTOR_BUCKETS);
    }

    #[test]
    fn test_default_confidence() {
        let extractor = FeatureExtractorV2::new();
        let mut finding = make_finding();
        finding.confidence = None; // no confidence set

        let features = extractor.extract(&finding, None, None, None);
        // Should default to 0.5
        assert!((features.values[2] - 0.5).abs() < f64::EPSILON);
    }

    #[test]
    fn test_entity_type_class() {
        let extractor = FeatureExtractorV2::new();

        // Finding at line 15, no function covers it, but a class does.
        let mut finding = make_finding();
        finding.line_start = Some(5);
        finding.line_end = Some(5);

        let cls = CodeNode::class("UserModel", "src/api/users.py")
            .with_qualified_name("src.api.users.UserModel")
            .with_lines(1, 100);

        // Graph has only a class, no function covering line 5.
        let graph = TestGraph::empty().with_class(cls);

        let features = extractor.extract(&finding, Some(&graph), None, None);
        // entity_type should be 2.0 (class)
        assert!((features.values[5] - 2.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_scc_membership() {
        let extractor = FeatureExtractorV2::new();
        let finding = make_finding();

        let graph = TestGraph {
            functions: Vec::new(),
            classes: Vec::new(),
            cycles: vec![vec![
                "src/api/users.py".to_string(),
                "src/api/orders.py".to_string(),
            ]],
        };

        let features = extractor.extract(&finding, Some(&graph), None, None);
        // File is in a cycle.
        assert!((features.values[14] - 1.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_git_features_from_file_churn() {
        use crate::git::history::FileChurn;

        let churn = FileChurn {
            total_insertions: 100,
            total_deletions: 20,
            commit_count: 15,
            authors: vec![
                "alice".to_string(),
                "bob".to_string(),
                "charlie".to_string(),
            ],
            last_modified: Some("2025-01-01T12:00:00+00:00".to_string()),
            last_author: Some("alice".to_string()),
        };

        // Set "now" to 2025-03-01 → ~59 days after last_modified
        let now_epoch = chrono::DateTime::parse_from_rfc3339("2025-03-01T12:00:00+00:00")
            .unwrap()
            .timestamp();

        let git = GitFeatures::from_file_churn(&churn, now_epoch);

        // age ~59 days → ln(60) ≈ 4.09
        assert!(git.file_age_log > 4.0 && git.file_age_log < 4.2);
        assert!((git.recent_churn - 15.0).abs() < f64::EPSILON);
        assert!((git.developer_count - 3.0).abs() < f64::EPSILON);
        assert!((git.unique_change_count - 15.0).abs() < f64::EPSILON);
        // 59 days > 30, so not recently created
        assert!((git.is_recently_created - 0.0).abs() < f64::EPSILON);
        // 1/3 ≈ 0.333
        assert!((git.major_contributor_pct - 1.0 / 3.0).abs() < 0.01);
        assert!((git.minor_contributor_count - 2.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_git_features_from_file_churn_recently_created() {
        use crate::git::history::FileChurn;

        let churn = FileChurn {
            total_insertions: 50,
            total_deletions: 0,
            commit_count: 2,
            authors: vec!["dev".to_string()],
            last_modified: Some("2025-02-20T12:00:00+00:00".to_string()),
            last_author: Some("dev".to_string()),
        };

        // "now" = 2025-02-25 → 5 days since last modified
        let now_epoch = chrono::DateTime::parse_from_rfc3339("2025-02-25T12:00:00+00:00")
            .unwrap()
            .timestamp();

        let git = GitFeatures::from_file_churn(&churn, now_epoch);

        // 5 days < 30 → recently created
        assert!((git.is_recently_created - 1.0).abs() < f64::EPSILON);
        assert!((git.developer_count - 1.0).abs() < f64::EPSILON);
        // 1 author → major_contributor_pct = 1.0
        assert!((git.major_contributor_pct - 1.0).abs() < f64::EPSILON);
        // 0 minor contributors
        assert!((git.minor_contributor_count - 0.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_git_features_from_file_churn_no_timestamp() {
        use crate::git::history::FileChurn;

        let churn = FileChurn {
            total_insertions: 10,
            total_deletions: 5,
            commit_count: 3,
            authors: vec!["dev".to_string()],
            last_modified: None,
            last_author: None,
        };

        let git = GitFeatures::from_file_churn(&churn, 0);
        // No timestamp → age_days = 0 → ln(1) = 0
        assert!((git.file_age_log - 0.0).abs() < f64::EPSILON);
        // No timestamp → is_recently_created = 1.0 (age 0 < 30)
        assert!((git.is_recently_created - 1.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_compute_cross_features_basic() {
        let findings = vec![
            Finding {
                id: "f1".into(),
                detector: "SQLInjectionDetector".into(),
                affected_files: vec![PathBuf::from("src/api/users.py")],
                ..Default::default()
            },
            Finding {
                id: "f2".into(),
                detector: "SQLInjectionDetector".into(),
                affected_files: vec![PathBuf::from("src/api/users.py")],
                ..Default::default()
            },
            Finding {
                id: "f3".into(),
                detector: "DeadCodeDetector".into(),
                affected_files: vec![PathBuf::from("src/api/users.py")],
                ..Default::default()
            },
            Finding {
                id: "f4".into(),
                detector: "GodClassDetector".into(),
                affected_files: vec![PathBuf::from("src/models.py")],
                ..Default::default()
            },
        ];

        let mut file_loc_map = HashMap::new();
        file_loc_map.insert("src/api/users.py".to_string(), 500.0);
        file_loc_map.insert("src/models.py".to_string(), 200.0);

        let result = compute_cross_features(&findings, &file_loc_map);

        // users.py has 3 findings total, 500 LOC = 0.5 kLOC → density = 3/0.5 = 6.0
        let users = result.get("src/api/users.py").unwrap();
        let sql = users.get("SQLInjectionDetector").unwrap();
        assert!((sql.finding_density - 6.0).abs() < 0.01);
        assert!((sql.same_detector_findings - 2.0).abs() < f64::EPSILON);
        assert!((sql.historical_fp_rate - 0.0).abs() < f64::EPSILON);

        let dead = users.get("DeadCodeDetector").unwrap();
        assert!((dead.finding_density - 6.0).abs() < 0.01); // same file density
        assert!((dead.same_detector_findings - 1.0).abs() < f64::EPSILON);

        // models.py has 1 finding, 200 LOC = 0.2 kLOC → density = 1/0.2 = 5.0
        let models = result.get("src/models.py").unwrap();
        let god = models.get("GodClassDetector").unwrap();
        assert!((god.finding_density - 5.0).abs() < 0.01);
        assert!((god.same_detector_findings - 1.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_compute_cross_features_empty() {
        let result = compute_cross_features(&[], &HashMap::new());
        assert!(result.is_empty());
    }
}
