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

/// Built-in default exclusion patterns for vendored/third-party code.
/// These are applied automatically unless `skip_defaults = true` in config.
pub const DEFAULT_EXCLUDE_PATTERNS: &[&str] = &[
    "**/vendor/**",
    "**/node_modules/**",
    "**/third_party/**",
    "**/third-party/**",
    "**/bower_components/**",
    "**/dist/**",
    "**/*.min.js",
    "**/*.min.css",
    "**/*.bundle.js",
];

/// Project type affects detector thresholds and scoring
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum ProjectType {
    /// Web apps, REST APIs, CRUD - strictest coupling analysis (default)
    #[default]
    Web,
    /// Language interpreters, VMs - lenient coupling, skip dispatch tables
    Interpreter,
    /// Compilers, transpilers - pipeline architecture
    Compiler,
    /// Reusable libraries - focus on public API
    Library,
    /// UI frameworks, component libraries - high internal coupling expected
    Framework,
    /// Command-line tools - command dispatch patterns
    Cli,
    /// Operating systems, embedded - syscalls, interrupts
    Kernel,
    /// Game engines - ECS, tight loops
    Game,
    /// ML/AI, data science - notebooks, complex pipelines
    DataScience,
    /// iOS/Android mobile apps
    Mobile,
}

impl ProjectType {
    /// Coupling threshold multiplier (higher = more lenient)
    pub fn coupling_multiplier(&self) -> f64 {
        match self {
            ProjectType::Web => 1.0, // Strict - CRUD should have clean separation
            ProjectType::Interpreter => 2.5, // Very lenient - eval loops touch everything
            ProjectType::Compiler => 3.0, // Very lenient - HIR/MIR/AST shared everywhere
            ProjectType::Library => 1.5, // Moderate - internal coupling OK
            ProjectType::Framework => 3.0, // Very lenient - React/Vue cores couple heavily
            ProjectType::Cli => 1.3, // Slight leniency - command dispatch
            ProjectType::Kernel => 3.0, // Most lenient - syscalls, interrupts
            ProjectType::Game => 2.0, // Lenient - ECS, frame loops
            ProjectType::DataScience => 2.0, // Lenient - notebooks, pipelines
            ProjectType::Mobile => 1.5, // Moderate - MVC/MVVM patterns
        }
    }

    /// Complexity threshold multiplier
    pub fn complexity_multiplier(&self) -> f64 {
        match self {
            ProjectType::Web => 1.0,
            ProjectType::Interpreter => 1.8, // Opcodes switches are complex
            ProjectType::Compiler => 1.5,    // Parser/codegen complexity
            ProjectType::Library => 1.2,
            ProjectType::Framework => 1.5, // Core reconciler, scheduler complexity
            ProjectType::Cli => 1.1,
            ProjectType::Kernel => 2.0, // Interrupt handlers, state machines
            ProjectType::Game => 1.5,   // Frame update loops
            ProjectType::DataScience => 1.8, // Data pipelines, complex transforms
            ProjectType::Mobile => 1.3, // UI state, lifecycle complexity
        }
    }

    /// Whether to skip dead code analysis for dispatch-like patterns
    pub fn lenient_dead_code(&self) -> bool {
        matches!(
            self,
            ProjectType::Interpreter
                | ProjectType::Kernel
                | ProjectType::Game
                | ProjectType::Framework
                | ProjectType::DataScience
        )
    }

    /// Detect project type from directory structure and file contents
    pub fn detect(repo_path: &Path) -> ProjectType {
        // Score each project type and pick the highest
        let mut scores: Vec<(ProjectType, u32)> = vec![
            (
                ProjectType::Interpreter,
                score_interpreter_markers(repo_path),
            ),
            (ProjectType::Compiler, score_compiler_markers(repo_path)),
            (ProjectType::Framework, score_framework_markers(repo_path)),
            (ProjectType::Kernel, score_kernel_markers(repo_path)),
            (ProjectType::Game, score_game_markers(repo_path)),
            (
                ProjectType::DataScience,
                score_datascience_markers(repo_path),
            ),
            (ProjectType::Mobile, score_mobile_markers(repo_path)),
            (ProjectType::Cli, score_cli_markers(repo_path)),
            (ProjectType::Library, score_library_markers(repo_path)),
            (ProjectType::Web, score_web_markers(repo_path)),
        ];

        // Sort by score descending
        scores.sort_by(|a, b| b.1.cmp(&a.1));

        // If top score is 0 or very low, default to Library
        if scores[0].1 < 2 {
            return ProjectType::Library;
        }

        scores[0].0
    }
}

use super::project_type_scoring::{
    score_cli_markers, score_compiler_markers, score_datascience_markers, score_framework_markers,
    score_game_markers, score_interpreter_markers, score_kernel_markers, score_library_markers,
    score_mobile_markers, score_web_markers,
};

/// Project-level configuration loaded from repotoire.toml or similar
#[derive(Debug, Clone, Deserialize, Default)]
pub struct ProjectConfig {
    /// Project type (auto-detected if not specified)
    #[serde(default)]
    pub project_type: Option<ProjectType>,

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

    /// Cached auto-detected project type (not serialized)
    #[serde(skip)]
    #[allow(dead_code)] // Set during detection, read in future project-type logic
    detected_type: Option<ProjectType>,
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
        // repotoire:ignore[surprisal]
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

    /// If true, disable built-in default exclusion patterns
    #[serde(default)]
    pub skip_defaults: bool,
}

impl ExcludeConfig {
    /// Returns effective exclusion patterns (defaults + user patterns).
    /// If `skip_defaults` is true, only user patterns are returned.
    pub fn effective_patterns(&self) -> Vec<String> {
        let mut patterns = Vec::new();

        if !self.skip_defaults {
            patterns.extend(DEFAULT_EXCLUDE_PATTERNS.iter().map(|s| s.to_string()));
        }

        for p in &self.paths {
            if !patterns.contains(p) {
                patterns.push(p.clone());
            }
        }

        patterns
    }
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
        if !yaml_path.exists() {
            continue;
        }
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

    // Try JSON first (YAML is a superset of JSON, so pure-JSON YAML files work)
    if let Ok(config) = serde_json::from_str::<ProjectConfig>(&content) {
        return Ok(config);
    }

    // For actual YAML syntax, give a clear error (#34)
    anyhow::bail!(
        "YAML config files with non-JSON syntax are not yet supported.\n\
         Please convert {} to TOML format (repotoire.toml) or use JSON syntax.\n\
         See: https://repotoire.com/docs/cli/config",
        path.display()
    )
}

impl ProjectConfig {
    /// Effective project type (explicit config > auto-detected > default)
    pub fn project_type(&self, repo_path: &Path) -> ProjectType {
        if let Some(explicit) = self.project_type {
            debug!("Using explicit project type: {:?}", explicit);
            return explicit;
        }
        // Auto-detect based on repo structure
        let detected = ProjectType::detect(repo_path);
        debug!(
            "Auto-detected project type: {:?} (coupling multiplier: {})",
            detected,
            detected.coupling_multiplier()
        );
        detected
    }

    /// Coupling threshold multiplier based on project type
    pub fn coupling_multiplier(&self, repo_path: &Path) -> f64 {
        self.project_type(repo_path).coupling_multiplier()
    }

    /// Complexity threshold multiplier based on project type
    pub fn complexity_multiplier(&self, repo_path: &Path) -> f64 {
        self.project_type(repo_path).complexity_multiplier()
    }

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

    /// Severity override for a detector (if any)
    pub fn severity_override(&self, name: &str) -> Option<&str> {
        let normalized = normalize_detector_name(name);

        self.detectors
            .get(&normalized)
            .or_else(|| self.detectors.get(name))
            .and_then(|c| c.severity.as_deref())
    }

    /// Threshold value for a detector
    pub fn threshold(
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

    /// Threshold as i64
    pub fn threshold_i64(&self, detector_name: &str, threshold_name: &str) -> Option<i64> {
        self.threshold(detector_name, threshold_name)
            .and_then(|v| v.as_i64())
    }

    /// Threshold as f64
    pub fn threshold_f64(&self, detector_name: &str, threshold_name: &str) -> Option<f64> {
        self.threshold(detector_name, threshold_name)
            .and_then(|v| v.as_f64())
    }

    /// Check if a path should be excluded
    pub fn should_exclude(&self, path: &Path) -> bool {
        let path_str = path.to_string_lossy();
        for pattern in &self.exclude.effective_patterns() {
            if glob_match(pattern, &path_str) {
                return true;
            }
        }
        false
    }

    /// All detector names that should be skipped
    pub fn disabled_detectors(&self) -> Vec<String> {
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
            result.push(c.to_lowercase().next().unwrap_or(*c));
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
pub fn glob_match(pattern: &str, path: &str) -> bool {
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

            // Check suffix (handle * wildcard within suffix, e.g. **/*.min.js)
            if !suffix.is_empty() && !suffix.contains('*') && !path.ends_with(suffix) {
                return false;
            }
            if !suffix.is_empty() && suffix.contains('*') {
                let star_parts: Vec<&str> = suffix.split('*').collect();
                if star_parts.len() == 2 {
                    let before = star_parts[0];
                    let after = star_parts[1];
                    let matches = if before.is_empty() {
                        path.ends_with(after)
                    } else {
                        path.contains(before) && path.ends_with(after)
                    };
                    if !matches {
                        return false;
                    }
                }
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
    // "vendor/" only matches "vendor/foo.py", NOT "src/vendor/foo.py"
    // Use "**/vendor/**" pattern for recursive matching
    path.starts_with(pattern) || path == pattern
}

#[cfg(test)]
mod tests;
