//! Base detector trait and types
//!
//! This module defines the core abstractions for code smell detection:
//! - `Detector` trait that all detectors must implement
//! - `DetectorResult` for capturing execution results
//! - Helper types for detector configuration

use crate::graph::GraphClient;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashMap;

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
}

impl DetectorConfig {
    /// Create a new config with default values
    pub fn new() -> Self {
        Self::default()
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
///     fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
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
    /// 1. Query the graph database for relevant data
    /// 2. Analyze the data to find issues
    /// 3. Return a list of findings with appropriate severity
    ///
    /// # Arguments
    /// * `graph` - Graph database client for querying code structure
    ///
    /// # Returns
    /// A list of findings, or an error if detection fails
    fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>>;

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
}
