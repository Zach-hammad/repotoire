//! Base detector trait and types
//!
//! This module defines the core abstractions for code smell detection:
//! - `Detector` trait that all detectors must implement
//! - `DetectorResult` for capturing execution results
//! - Helper types for detector configuration

use crate::detectors::function_context::FunctionContextMap;
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};

/// Generate a deterministic finding ID from detector name, file path, and line number (#73).
/// This enables proper dedup in incremental cache — Uuid::new_v4() creates new IDs each run.
pub fn finding_id(detector: &str, file: &str, line: u32) -> String {
    let mut hasher = DefaultHasher::new();
    detector.hash(&mut hasher);
    file.hash(&mut hasher);
    line.hash(&mut hasher);
    format!("{:016x}", hasher.finish())
}
use std::collections::HashMap;
use std::sync::Arc;

/// Scope of a detector - determines when it needs to be re-run
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum DetectorScope {
    /// Analyzes a single file in isolation (complexity, naming, etc.)
    /// Can be cached per-file and only re-run when that file changes.
    FileLocal,

    /// Analyzes relationships between files (coupling, circular deps)
    /// Must re-run if any related file changes.
    CrossFile,

    /// Uses full graph analysis (centrality, architectural patterns)
    /// Must re-run if graph structure changes.
    GraphBased,
}

/// Result from running a single detector
#[derive(Debug, Clone)]
pub struct DetectorResult {
    /// Name of the detector that produced these results
    pub detector_name: String,
    /// Findings produced by the detector
    pub findings: Vec<Finding>,
    /// Execution time in milliseconds
    pub duration_ms: u64,
    /// Whether the detector completed successfully
    pub success: bool,
    /// Error message if the detector failed
    pub error: Option<String>,
}

impl DetectorResult {
    /// Create a successful result
    pub fn success(detector_name: String, findings: Vec<Finding>, duration_ms: u64) -> Self {
        Self {
            detector_name,
            findings,
            duration_ms,
            success: true,
            error: None,
        }
    }

    /// Create a failed result
    pub fn failure(detector_name: String, error: String, duration_ms: u64) -> Self {
        Self {
            detector_name,
            findings: Vec::new(),
            duration_ms,
            success: false,
            error: Some(error),
        }
    }
}

/// Configuration options for detectors
#[derive(Debug, Clone, Default)]
pub struct DetectorConfig {
    /// Repository ID for multi-tenant filtering
    pub repo_id: Option<String>,
    /// Maximum findings to return per detector
    pub max_findings: Option<usize>,
    /// Detector-specific thresholds and options
    pub options: HashMap<String, serde_json::Value>,
    /// Coupling threshold multiplier based on project type (1.0 = web/CRUD, higher = more lenient)
    pub coupling_multiplier: f64,
    /// Complexity threshold multiplier based on project type
    pub complexity_multiplier: f64,
    /// Adaptive threshold resolver (from style profile)
    pub adaptive: crate::calibrate::ThresholdResolver,
}

impl DetectorConfig {
    /// Create a new config with default values
    pub fn new() -> Self {
        Self {
            repo_id: None,
            max_findings: None,
            options: HashMap::new(),
            coupling_multiplier: 1.0,
            complexity_multiplier: 1.0,
            adaptive: crate::calibrate::ThresholdResolver::default(),
        }
    }

    /// Create a config populated from project-level detector thresholds
    ///
    /// Looks up the detector by name in the project config and copies
    /// any threshold values into the options map.
    pub fn from_project_config(
        detector_name: &str,
        project_config: &crate::config::ProjectConfig,
    ) -> Self {
        let mut config = Self::new();

        // Normalize detector name for lookup (GodClassDetector -> god-class)
        let normalized = crate::config::normalize_detector_name(detector_name);

        // Look up detector config in project config
        if let Some(detector_override) = project_config
            .detectors
            .get(&normalized)
            .or_else(|| project_config.detectors.get(detector_name))
        {
            // Copy thresholds to options
            for (key, value) in &detector_override.thresholds {
                let json_value = match value {
                    crate::config::ThresholdValue::Integer(v) => serde_json::json!(*v),
                    crate::config::ThresholdValue::Float(v) => serde_json::json!(*v),
                    crate::config::ThresholdValue::Boolean(v) => serde_json::json!(*v),
                    crate::config::ThresholdValue::String(v) => serde_json::json!(v),
                };
                config.options.insert(key.clone(), json_value);
            }
        }

        config
    }

    /// Create a config with project type multipliers
    ///
    /// Uses the project type (auto-detected or explicit) to set coupling and complexity
    /// multipliers. Interpreters/VMs get more lenient thresholds than web apps.
    pub fn from_project_config_with_type(
        detector_name: &str,
        project_config: &crate::config::ProjectConfig,
        repo_path: &std::path::Path,
    ) -> Self {
        let mut config = Self::from_project_config(detector_name, project_config);
        let project_type = project_config.project_type(repo_path);
        config.coupling_multiplier = project_type.coupling_multiplier();
        config.complexity_multiplier = project_type.complexity_multiplier();
        config
    }

    /// Set adaptive threshold resolver from style profile
    pub fn with_adaptive(mut self, resolver: crate::calibrate::ThresholdResolver) -> Self {
        self.adaptive = resolver;
        self
    }

    /// Set the repository ID
    pub fn with_repo_id(mut self, repo_id: impl Into<String>) -> Self {
        self.repo_id = Some(repo_id.into());
        self
    }

    /// Set maximum findings
    pub fn with_max_findings(mut self, max: usize) -> Self {
        self.max_findings = Some(max);
        self
    }

    /// Set a custom option
    pub fn with_option(mut self, key: impl Into<String>, value: serde_json::Value) -> Self {
        self.options.insert(key.into(), value);
        self
    }

    /// Get a typed option value
    pub fn get_option<T: serde::de::DeserializeOwned>(&self, key: &str) -> Option<T> {
        self.options
            .get(key)
            .and_then(|v| serde_json::from_value(v.clone()).ok())
    }

    /// Get an option with a default value
    pub fn get_option_or<T: serde::de::DeserializeOwned>(&self, key: &str, default: T) -> T {
        self.get_option(key).unwrap_or(default)
    }
}

/// Check if a file path appears to be a test file
/// Used by security detectors to avoid flagging test certificates, test fixtures, etc.
pub fn is_test_file(path: &std::path::Path) -> bool {
    let path_str = path.to_string_lossy().to_lowercase();
    let filename = path
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_lowercase();

    // Go test files
    path_str.ends_with("_test.go") ||
    // Python test files
    path_str.ends_with("_test.py") ||
    filename.starts_with("test_") ||  // test_foo.py
    // Test directories
    path_str.contains("/tests/") ||
    path_str.contains("/test/") ||
    path_str.contains("/__tests__/") ||
    // Ruby/JS spec files
    path_str.contains("/spec/") ||
    path_str.ends_with("_spec.rb") ||
    path_str.ends_with(".test.ts") ||
    path_str.ends_with(".test.js") ||
    path_str.ends_with(".test.tsx") ||
    path_str.ends_with(".test.jsx") ||
    path_str.ends_with(".spec.ts") ||
    path_str.ends_with(".spec.js") ||
    path_str.ends_with(".spec.tsx") ||
    path_str.ends_with(".spec.jsx") ||
    // Test fixtures/data
    path_str.contains("/fixtures/") ||
    path_str.contains("/testdata/") ||
    path_str.contains("/__fixtures__/") ||
    path_str.contains("/__mocks__/")
}

/// Check if a path string looks like a test/spec path using path-segment matching.
/// Unlike `contains("test")`, this won't match 'attestation', 'contest', etc. (#30)
pub fn is_test_path(path_str: &str) -> bool {
    let lower = path_str.to_lowercase();
    lower.contains("/test/")
        || lower.contains("/tests/")
        || lower.contains("/__tests__/")
        || lower.contains("/spec/")
        || lower.contains("/test_")
        || lower.contains("_test.")
        || lower.contains(".test.")
        || lower.contains(".spec.")
        || lower.contains("_spec.")
        // Handle relative paths starting with test directories
        || lower.starts_with("tests/")
        || lower.starts_with("test/")
        || lower.starts_with("__tests__/")
        || lower.starts_with("spec/")
}

/// Trait for all code smell detectors
///
/// Detectors analyze the code graph to find issues like:
/// - Circular dependencies
/// - God classes (classes that do too much)
/// - Long parameter lists
/// - Dead code
/// - And more...
///
/// # Example Implementation
///
/// ```ignore
/// pub struct MyDetector {
///     config: DetectorConfig,
/// }
///
/// impl Detector for MyDetector {
///     fn name(&self) -> &'static str {
///         "MyDetector"
///     }
///
///     fn description(&self) -> &'static str {
///         "Detects my specific code smell"
///     }
///
///     fn detect(&self, graph: &dyn crate::graph::GraphQuery, _files: &dyn super::file_provider::FileProvider) -> Result<Vec<Finding>> {
///         // Query the graph and analyze results
///         Ok(vec![])
///     }
/// }
/// ```
pub trait Detector: Send + Sync {
    /// Unique identifier for this detector
    ///
    /// Should match the Python detector name for consistency
    /// (e.g., "CircularDependencyDetector")
    fn name(&self) -> &'static str;

    /// Human-readable description of what this detector finds
    fn description(&self) -> &'static str;

    /// Run detection and return findings
    ///
    /// This is the main entry point for detection. Implementations should:
    /// 1. Query the graph store for relevant data
    /// 2. Analyze the data to find issues
    /// 3. Return a list of findings with appropriate severity
    ///
    /// # Arguments
    /// * `graph` - Graph store implementing GraphQuery trait
    /// * `files` - File provider for accessing source files and their contents
    ///
    /// # Returns
    /// A list of findings, or an error if detection fails
    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn super::file_provider::FileProvider) -> Result<Vec<Finding>>;

    /// Run detection with function context
    ///
    /// Enhanced version of detect() that receives pre-computed function contexts.
    /// Detectors that benefit from knowing function roles (utility, hub, etc.)
    /// should override this method.
    ///
    /// Default implementation just calls detect() and ignores contexts.
    ///
    /// # Arguments
    /// * `graph` - Graph store implementing GraphQuery trait
    /// * `files` - File provider for accessing source files and their contents
    /// * `contexts` - Pre-computed function contexts with roles and metrics
    ///
    /// # Returns
    /// A list of findings, or an error if detection fails
    fn detect_with_context(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        files: &dyn super::file_provider::FileProvider,
        _contexts: &Arc<FunctionContextMap>,
    ) -> Result<Vec<Finding>> {
        // Default: ignore context, just call regular detect
        self.detect(graph, files)
    }

    /// Whether this detector uses function context
    ///
    /// If true, the engine will call detect_with_context instead of detect.
    /// Override this to return true if your detector benefits from context.
    fn uses_context(&self) -> bool {
        false
    }

    /// Whether this detector depends on results from other detectors
    ///
    /// Dependent detectors run sequentially after all independent detectors
    /// have completed. This allows them to use findings from other detectors.
    ///
    /// Default: `false` (independent)
    fn is_dependent(&self) -> bool {
        false
    }

    /// Optional: Dependencies on other detectors
    ///
    /// Only meaningful if `is_dependent()` returns true.
    /// Returns names of detectors that must run before this one.
    #[allow(dead_code)] // Reserved for future dependent detector support
    fn dependencies(&self) -> Vec<&'static str> {
        vec![]
    }

    /// Category of issues this detector finds
    ///
    /// Used for grouping and filtering findings in reports.
    fn category(&self) -> &'static str {
        "code_smell"
    }

    /// Get the configuration for this detector
    fn config(&self) -> Option<&DetectorConfig> {
        None
    }

    /// Scope of this detector - determines when it needs to re-run
    ///
    /// - `FileLocal`: Only analyzes individual files, can be cached per-file
    /// - `CrossFile`: Analyzes relationships, re-run if any related file changes  
    /// - `GraphBased`: Uses full graph, re-run if graph structure changes
    ///
    /// Default is GraphBased (conservative - always re-runs)
    fn scope(&self) -> DetectorScope {
        DetectorScope::GraphBased
    }
}

/// Progress callback for detector execution
pub type ProgressCallback = Box<dyn Fn(&str, usize, usize) + Send + Sync>;

/// Summary statistics from running all detectors
#[derive(Debug, Clone, Default)]
pub struct DetectionSummary {
    /// Total number of detectors run
    pub detectors_run: usize,
    /// Number of detectors that succeeded
    pub detectors_succeeded: usize,
    /// Number of detectors that failed
    pub detectors_failed: usize,
    /// Total findings across all detectors
    pub total_findings: usize,
    /// Findings by severity
    pub by_severity: HashMap<Severity, usize>,
    /// Total execution time in milliseconds
    pub total_duration_ms: u64,
}

impl DetectionSummary {
    /// Update summary with a detector result
    pub fn add_result(&mut self, result: &DetectorResult) {
        self.detectors_run += 1;
        self.total_duration_ms += result.duration_ms;

        if result.success {
            self.detectors_succeeded += 1;
            self.total_findings += result.findings.len();

            for finding in &result.findings {
                *self.by_severity.entry(finding.severity).or_insert(0) += 1;
            }
        } else {
            self.detectors_failed += 1;
        }
    }
}

/// Pre-compile glob patterns from exclude list into regexes
pub fn compile_glob_patterns(patterns: &[String]) -> Vec<regex::Regex> {
    patterns
        .iter()
        .filter(|p| p.contains('*'))
        .filter_map(|p| {
            let re_str = format!("^{}$", p.replace('*', ".*"));
            regex::Regex::new(&re_str).ok()
        })
        .collect()
}

/// Check if a path should be excluded based on patterns and pre-compiled globs
pub fn should_exclude_path(
    path: &str,
    patterns: &[String],
    compiled_globs: &[regex::Regex],
) -> bool {
    for pattern in patterns {
        if pattern.ends_with('/') {
            let dir = pattern.trim_end_matches('/');
            if path.split('/').any(|p| p == dir) {
                return true;
            }
        } else if pattern.contains('*') {
            continue; // handled by compiled_globs below
        } else if path.contains(pattern) {
            return true;
        }
    }
    let filename = std::path::Path::new(path)
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("");
    for re in compiled_globs {
        if re.is_match(path) || re.is_match(filename) {
            return true;
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_detector_config() {
        let config = DetectorConfig::new()
            .with_repo_id("test-repo")
            .with_max_findings(100)
            .with_option("threshold", serde_json::json!(10));

        assert_eq!(config.repo_id, Some("test-repo".to_string()));
        assert_eq!(config.max_findings, Some(100));
        assert_eq!(config.get_option::<i32>("threshold"), Some(10));
        assert_eq!(config.get_option_or("missing", 5), 5);
    }

    #[test]
    fn test_detector_result_success() {
        let result = DetectorResult::success("TestDetector".to_string(), vec![], 100);
        assert!(result.success);
        assert!(result.error.is_none());
        assert_eq!(result.duration_ms, 100);
    }

    #[test]
    fn test_detector_result_failure() {
        let result = DetectorResult::failure("TestDetector".to_string(), "oops".to_string(), 50);
        assert!(!result.success);
        assert_eq!(result.error, Some("oops".to_string()));
    }

    #[test]
    fn test_detection_summary() {
        let mut summary = DetectionSummary::default();

        let result1 = DetectorResult::success("D1".to_string(), vec![], 100);
        let result2 = DetectorResult::failure("D2".to_string(), "err".to_string(), 50);

        summary.add_result(&result1);
        summary.add_result(&result2);

        assert_eq!(summary.detectors_run, 2);
        assert_eq!(summary.detectors_succeeded, 1);
        assert_eq!(summary.detectors_failed, 1);
        assert_eq!(summary.total_duration_ms, 150);
    }

    #[test]
    fn test_is_test_file() {
        use super::is_test_file;
        use std::path::Path;

        assert!(is_test_file(Path::new("foo_test.go")));
        assert!(is_test_file(Path::new("test_foo.py")));
        assert!(is_test_file(Path::new("src/tests/helper.py")));
        assert!(is_test_file(Path::new("app.spec.ts")));
        assert!(!is_test_file(Path::new("src/main.py")));
        assert!(!is_test_file(Path::new("testing_utils.py"))); // "testing" != "test"
    }
}
