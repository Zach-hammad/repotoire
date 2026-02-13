//! Project-level configuration support
//!
//! Loads per-project configuration from `repotoire.toml`, `.repotoirerc.json`,
//! or `.repotoire.yaml` files in the repository root.
//!
//! # Configuration Format
//!
//! ```toml
//! # repotoire.toml
//!
//! [detectors.god-class]
//! enabled = true
//! thresholds = { method_count = 30, loc = 600 }
//!
//! [detectors.sql-injection]
//! severity = "high"  # Override default severity
//!
//! [scoring]
//! security_multiplier = 5.0
//! pillar_weights = { structure = 0.3, quality = 0.4, architecture = 0.3 }
//!
//! [exclude]
//! paths = ["generated/", "vendor/"]
//!
//! [defaults]
//! format = "text"
//! severity = "low"
//! workers = 8
//! ```

use serde::Deserialize;
use std::collections::HashMap;
use std::path::Path;
use tracing::{debug, warn};

/// Project-level configuration loaded from repotoire.toml or similar
#[derive(Debug, Clone, Deserialize, Default)]
pub struct ProjectConfig {
    /// Per-detector configuration overrides
    #[serde(default)]
    pub detectors: HashMap<String, DetectorConfigOverride>,

    /// Scoring configuration
    #[serde(default)]
    pub scoring: ScoringConfig,

    /// Path exclusion patterns
    #[serde(default)]
    pub exclude: ExcludeConfig,

    /// Default CLI flags
    #[serde(default)]
    pub defaults: CliDefaults,
}

/// Configuration override for a specific detector
#[derive(Debug, Clone, Deserialize, Default)]
pub struct DetectorConfigOverride {
    /// Whether the detector is enabled (default: true)
    #[serde(default)]
    pub enabled: Option<bool>,

    /// Override the default severity (critical, high, medium, low, info)
    #[serde(default)]
    pub severity: Option<String>,

    /// Detector-specific threshold overrides
    /// Keys depend on the detector (e.g., method_count, loc, max_params)
    #[serde(default)]
    pub thresholds: HashMap<String, ThresholdValue>,
}

/// A threshold value can be an integer, float, or boolean
#[derive(Debug, Clone, Deserialize)]
#[serde(untagged)]
pub enum ThresholdValue {
    Integer(i64),
    Float(f64),
    Boolean(bool),
    String(String),
}

impl ThresholdValue {
    /// Get as i64 (returns None for non-integer types)
    pub fn as_i64(&self) -> Option<i64> {
        match self {
            ThresholdValue::Integer(v) => Some(*v),
            ThresholdValue::Float(v) => Some(*v as i64),
            _ => None,
        }
    }

    /// Get as f64
    pub fn as_f64(&self) -> Option<f64> {
        match self {
            ThresholdValue::Integer(v) => Some(*v as f64),
            ThresholdValue::Float(v) => Some(*v),
            _ => None,
        }
    }

    /// Get as bool
    pub fn as_bool(&self) -> Option<bool> {
        match self {
            ThresholdValue::Boolean(v) => Some(*v),
            _ => None,
        }
    }

    /// Get as string
    pub fn as_str(&self) -> Option<&str> {
        match self {
            ThresholdValue::String(v) => Some(v.as_str()),
            _ => None,
        }
    }
}

/// Scoring configuration for health score calculation
#[derive(Debug, Clone, Deserialize)]
pub struct ScoringConfig {
    /// Multiplier for security-related findings (default: 3.0)
    #[serde(default = "default_security_multiplier")]
    pub security_multiplier: f64,

    /// Weights for each pillar (must sum to 1.0)
    #[serde(default)]
    pub pillar_weights: PillarWeights,
}

impl Default for ScoringConfig {
    fn default() -> Self {
        Self {
            security_multiplier: default_security_multiplier(),
            pillar_weights: PillarWeights::default(),
        }
    }
}

fn default_security_multiplier() -> f64 {
    3.0
}

/// Weights for the three scoring pillars
#[derive(Debug, Clone, Deserialize)]
pub struct PillarWeights {
    /// Weight for structure score (default: 0.4)
    #[serde(default = "default_structure_weight")]
    pub structure: f64,

    /// Weight for quality score (default: 0.3)
    #[serde(default = "default_quality_weight")]
    pub quality: f64,

    /// Weight for architecture score (default: 0.3)
    #[serde(default = "default_architecture_weight")]
    pub architecture: f64,
}

impl Default for PillarWeights {
    fn default() -> Self {
        Self {
            structure: default_structure_weight(),
            quality: default_quality_weight(),
            architecture: default_architecture_weight(),
        }
    }
}

fn default_structure_weight() -> f64 {
    0.4
}
fn default_quality_weight() -> f64 {
    0.3
}
fn default_architecture_weight() -> f64 {
    0.3
}

impl PillarWeights {
    /// Validate that weights sum to 1.0 (with tolerance)
    pub fn is_valid(&self) -> bool {
        let sum = self.structure + self.quality + self.architecture;
        (sum - 1.0).abs() < 0.001
    }

    /// Normalize weights to sum to 1.0
    pub fn normalize(&mut self) {
        let sum = self.structure + self.quality + self.architecture;
        if sum > 0.0 {
            self.structure /= sum;
            self.quality /= sum;
            self.architecture /= sum;
        }
    }
}

/// Path exclusion configuration
#[derive(Debug, Clone, Deserialize, Default)]
pub struct ExcludeConfig {
    /// Paths/patterns to exclude from analysis
    #[serde(default)]
    pub paths: Vec<String>,
}

/// Default CLI flags that can be set in project config
#[derive(Debug, Clone, Deserialize, Default)]
pub struct CliDefaults {
    /// Default output format (text, json, sarif, html, markdown)
    #[serde(default)]
    pub format: Option<String>,

    /// Default minimum severity filter
    #[serde(default)]
    pub severity: Option<String>,

    /// Default number of workers
    #[serde(default)]
    pub workers: Option<usize>,

    /// Default findings per page
    #[serde(default)]
    pub per_page: Option<usize>,

    /// Skip detectors by default
    #[serde(default)]
    pub skip_detectors: Vec<String>,

    /// Enable thorough mode by default
    #[serde(default)]
    pub thorough: Option<bool>,

    /// Skip git enrichment by default
    #[serde(default)]
    pub no_git: Option<bool>,

    /// Disable emoji by default
    #[serde(default)]
    pub no_emoji: Option<bool>,

    /// Fail-on severity threshold for CI
    #[serde(default)]
    pub fail_on: Option<String>,
}

/// Load project configuration from the repository root.
///
/// Searches for configuration files in this order:
/// 1. `repotoire.toml`
/// 2. `.repotoirerc.json`
/// 3. `.repotoire.yaml` / `.repotoire.yml`
///
/// Returns default configuration if no config file is found.
pub fn load_project_config(repo_path: &Path) -> ProjectConfig {
    // Try TOML first (preferred format)
    let toml_path = repo_path.join("repotoire.toml");
    if toml_path.exists() {
        match load_toml_config(&toml_path) {
            Ok(config) => {
                debug!("Loaded project config from {}", toml_path.display());
                return config;
            }
            Err(e) => {
                warn!("Failed to load {}: {}", toml_path.display(), e);
            }
        }
    }

    // Try JSON
    let json_path = repo_path.join(".repotoirerc.json");
    if json_path.exists() {
        match load_json_config(&json_path) {
            Ok(config) => {
                debug!("Loaded project config from {}", json_path.display());
                return config;
            }
            Err(e) => {
                warn!("Failed to load {}: {}", json_path.display(), e);
            }
        }
    }

    // Try YAML (.yaml or .yml)
    for yaml_name in &[".repotoire.yaml", ".repotoire.yml"] {
        let yaml_path = repo_path.join(yaml_name);
        if yaml_path.exists() {
            match load_yaml_config(&yaml_path) {
                Ok(config) => {
                    debug!("Loaded project config from {}", yaml_path.display());
                    return config;
                }
                Err(e) => {
                    warn!("Failed to load {}: {}", yaml_path.display(), e);
                }
            }
        }
    }

    // No config found, return defaults
    debug!("No project config found, using defaults");
    ProjectConfig::default()
}

/// Load configuration from a TOML file
fn load_toml_config(path: &Path) -> anyhow::Result<ProjectConfig> {
    let content = std::fs::read_to_string(path)?;
    let config: ProjectConfig = toml::from_str(&content)?;
    Ok(config)
}

/// Load configuration from a JSON file
fn load_json_config(path: &Path) -> anyhow::Result<ProjectConfig> {
    let content = std::fs::read_to_string(path)?;
    let config: ProjectConfig = serde_json::from_str(&content)?;
    Ok(config)
}

/// Load configuration from a YAML file
fn load_yaml_config(path: &Path) -> anyhow::Result<ProjectConfig> {
    let content = std::fs::read_to_string(path)?;
    // Use serde_yaml if available, otherwise fall back to JSON-compatible subset
    // For now, we'll try parsing as JSON (YAML is a superset of JSON)
    // In a real implementation, add serde_yaml dependency
    let config: ProjectConfig = serde_json::from_str(&content).map_err(|e| {
        anyhow::anyhow!(
            "YAML parsing not fully supported yet (tried JSON fallback): {}",
            e
        )
    })?;
    Ok(config)
}

impl ProjectConfig {
    /// Check if a detector is enabled (defaults to true if not specified)
    pub fn is_detector_enabled(&self, name: &str) -> bool {
        // Normalize detector name for lookup (support both kebab-case and snake_case)
        let normalized = normalize_detector_name(name);

        self.detectors
            .get(&normalized)
            .or_else(|| self.detectors.get(name))
            .and_then(|c| c.enabled)
            .unwrap_or(true)
    }

    /// Get severity override for a detector (if any)
    pub fn get_severity_override(&self, name: &str) -> Option<&str> {
        let normalized = normalize_detector_name(name);

        self.detectors
            .get(&normalized)
            .or_else(|| self.detectors.get(name))
            .and_then(|c| c.severity.as_deref())
    }

    /// Get threshold value for a detector
    pub fn get_threshold(
        &self,
        detector_name: &str,
        threshold_name: &str,
    ) -> Option<&ThresholdValue> {
        let normalized = normalize_detector_name(detector_name);

        self.detectors
            .get(&normalized)
            .or_else(|| self.detectors.get(detector_name))
            .and_then(|c| c.thresholds.get(threshold_name))
    }

    /// Get threshold as i64
    pub fn get_threshold_i64(&self, detector_name: &str, threshold_name: &str) -> Option<i64> {
        self.get_threshold(detector_name, threshold_name)
            .and_then(|v| v.as_i64())
    }

    /// Get threshold as f64
    pub fn get_threshold_f64(&self, detector_name: &str, threshold_name: &str) -> Option<f64> {
        self.get_threshold(detector_name, threshold_name)
            .and_then(|v| v.as_f64())
    }

    /// Check if a path should be excluded
    pub fn should_exclude(&self, path: &Path) -> bool {
        let path_str = path.to_string_lossy();

        for pattern in &self.exclude.paths {
            // Simple glob matching (supports * and **)
            if glob_match(pattern, &path_str) {
                return true;
            }
        }

        false
    }

    /// Get all detector names that should be skipped
    pub fn get_disabled_detectors(&self) -> Vec<String> {
        let mut disabled = Vec::new();

        // From explicit enabled: false
        for (name, config) in &self.detectors {
            if config.enabled == Some(false) {
                disabled.push(name.clone());
            }
        }

        // From defaults.skip_detectors
        disabled.extend(self.defaults.skip_detectors.clone());

        disabled
    }
}

/// Normalize detector name for config lookup
/// Converts various formats to kebab-case for matching
pub fn normalize_detector_name(name: &str) -> String {
    // GodClassDetector -> god-class
    // SQLInjectionDetector -> sql-injection
    // god_class -> god-class
    // god-class -> god-class

    let mut result = String::new();
    let chars: Vec<char> = name.chars().collect();

    for (i, c) in chars.iter().enumerate() {
        if c.is_uppercase() {
            // Add hyphen if:
            // 1. Not first char AND previous is lowercase (e.g., godClass -> god-class)
            // 2. Not first char AND previous is uppercase AND next is lowercase (e.g., SQLInjection -> sql-injection)
            let prev_is_lower = i > 0 && chars[i - 1].is_lowercase();
            let is_acronym_end = i > 0
                && chars[i - 1].is_uppercase()
                && i + 1 < chars.len()
                && chars[i + 1].is_lowercase();

            if prev_is_lower || is_acronym_end {
                result.push('-');
            }
            result.push(c.to_lowercase().next().unwrap());
        } else if *c == '_' {
            result.push('-');
        } else {
            result.push(*c);
        }
    }

    // Remove common suffixes
    result.trim_end_matches("-detector").to_string()
}

/// Simple glob pattern matching
fn glob_match(pattern: &str, path: &str) -> bool {
    // Handle **/X/** patterns (match if path contains X as a directory)
    if pattern.starts_with("**/") && pattern.ends_with("/**") {
        let middle = pattern.trim_start_matches("**/").trim_end_matches("/**");
        // Check if path contains /middle/ or starts with middle/
        return path.contains(&format!("/{}/", middle))
            || path.starts_with(&format!("{}/", middle));
    }

    // Handle ** (match any path segments)
    if pattern.contains("**") {
        let parts: Vec<&str> = pattern.split("**").collect();
        if parts.len() == 2 {
            let prefix = parts[0].trim_end_matches('/');
            let suffix = parts[1].trim_start_matches('/');

            // Check prefix
            if !prefix.is_empty() && !path.starts_with(prefix) {
                return false;
            }

            // Check suffix
            if !suffix.is_empty() && !path.ends_with(suffix) {
                return false;
            }

            return true;
        }
    }

    // Handle single * (match within segment)
    if pattern.contains('*') {
        let parts: Vec<&str> = pattern.split('*').collect();
        if parts.len() == 2 {
            let prefix = parts[0];
            let suffix = parts[1];
            return path.starts_with(prefix) && path.ends_with(suffix);
        }
    }

    // Exact match or prefix match (for directories)
    path.starts_with(pattern) || path == pattern
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_normalize_detector_name() {
        assert_eq!(normalize_detector_name("GodClassDetector"), "god-class");
        assert_eq!(normalize_detector_name("god_class"), "god-class");
        assert_eq!(normalize_detector_name("god-class"), "god-class");
        // Consecutive uppercase stays together: SQL -> sql
        assert_eq!(
            normalize_detector_name("SQLInjectionDetector"),
            "sql-injection"
        );
        assert_eq!(normalize_detector_name("NPlusOneDetector"), "n-plus-one");
    }

    #[test]
    fn test_glob_match() {
        // ** patterns
        assert!(glob_match("**/vendor/**", "src/vendor/lib/foo.py"));
        assert!(glob_match("generated/", "generated/model.py"));
        assert!(glob_match("*.test.ts", "foo.test.ts"));

        // Prefix patterns
        assert!(glob_match("vendor/", "vendor/lib/foo.py"));
        assert!(!glob_match("vendor/", "src/vendor/foo.py"));
    }

    #[test]
    fn test_pillar_weights_validation() {
        let valid = PillarWeights {
            structure: 0.4,
            quality: 0.3,
            architecture: 0.3,
        };
        assert!(valid.is_valid());

        let invalid = PillarWeights {
            structure: 0.5,
            quality: 0.5,
            architecture: 0.5,
        };
        assert!(!invalid.is_valid());
    }

    #[test]
    fn test_pillar_weights_normalize() {
        let mut weights = PillarWeights {
            structure: 2.0,
            quality: 1.0,
            architecture: 1.0,
        };
        weights.normalize();
        assert!((weights.structure - 0.5).abs() < 0.001);
        assert!((weights.quality - 0.25).abs() < 0.001);
        assert!((weights.architecture - 0.25).abs() < 0.001);
    }

    #[test]
    fn test_threshold_value() {
        let int_val = ThresholdValue::Integer(42);
        assert_eq!(int_val.as_i64(), Some(42));
        assert_eq!(int_val.as_f64(), Some(42.0));
        assert_eq!(int_val.as_bool(), None);

        let float_val = ThresholdValue::Float(2.5);
        assert_eq!(float_val.as_i64(), Some(2));
        assert_eq!(float_val.as_f64(), Some(2.5));

        let bool_val = ThresholdValue::Boolean(true);
        assert_eq!(bool_val.as_bool(), Some(true));
        assert_eq!(bool_val.as_i64(), None);
    }

    #[test]
    fn test_default_config() {
        let config = ProjectConfig::default();

        // All detectors enabled by default
        assert!(config.is_detector_enabled("god-class"));
        assert!(config.is_detector_enabled("unknown-detector"));

        // No severity overrides
        assert!(config.get_severity_override("god-class").is_none());

        // Default scoring
        assert!((config.scoring.security_multiplier - 3.0).abs() < 0.001);
        assert!(config.scoring.pillar_weights.is_valid());
    }

    #[test]
    fn test_parse_toml_config() {
        let toml_content = r#"
[detectors.god-class]
enabled = true
thresholds = { method_count = 30, loc = 600 }

[detectors.sql-injection]
severity = "high"
enabled = false

[scoring]
security_multiplier = 5.0

[scoring.pillar_weights]
structure = 0.3
quality = 0.4
architecture = 0.3

[exclude]
paths = ["generated/", "vendor/"]

[defaults]
format = "json"
workers = 4
skip_detectors = ["debug-code"]
"#;

        let config: ProjectConfig = toml::from_str(toml_content).unwrap();

        // Check detectors
        assert!(config.is_detector_enabled("god-class"));
        assert!(!config.is_detector_enabled("sql-injection"));
        assert_eq!(config.get_severity_override("sql-injection"), Some("high"));
        assert_eq!(
            config.get_threshold_i64("god-class", "method_count"),
            Some(30)
        );
        assert_eq!(config.get_threshold_i64("god-class", "loc"), Some(600));

        // Check scoring
        assert!((config.scoring.security_multiplier - 5.0).abs() < 0.001);
        assert!((config.scoring.pillar_weights.structure - 0.3).abs() < 0.001);

        // Check exclude
        assert_eq!(config.exclude.paths.len(), 2);
        assert!(config.should_exclude(Path::new("generated/foo.py")));

        // Check defaults
        assert_eq!(config.defaults.format, Some("json".to_string()));
        assert_eq!(config.defaults.workers, Some(4));
        assert!(config
            .defaults
            .skip_detectors
            .contains(&"debug-code".to_string()));
    }
}
