//! Pickle deserialization detector
//!
//! Detects unsafe deserialization patterns that can lead to Remote Code Execution:
//!
//! - pickle.load(), pickle.loads() - always unsafe
//! - torch.load() without weights_only=True
//! - joblib.load() - uses pickle internally
//! - numpy.load() with allow_pickle=True
//! - yaml.load() without SafeLoader
//! - marshal.load() - bytecode execution
//! - shelve.open() - uses pickle
//!
//! CWE-502: Deserialization of Untrusted Data

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use tracing::{debug, info};
use uuid::Uuid;

/// Default file patterns to exclude
const DEFAULT_EXCLUDE_PATTERNS: &[&str] = &[
    "tests/",
    "test_",
    "_test.py",
    "migrations/",
    "__pycache__/",
    ".git/",
    "node_modules/",
    "venv/",
    ".venv/",
];

/// Detects unsafe deserialization vulnerabilities
pub struct PickleDeserializationDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
    exclude_patterns: Vec<String>,
    // Compiled regex patterns
    pickle_load_pattern: Regex,
    torch_load_pattern: Regex,
    torch_safe_pattern: Regex,
    joblib_load_pattern: Regex,
    numpy_load_pattern: Regex,
    numpy_pickle_pattern: Regex,
    yaml_load_pattern: Regex,
    yaml_safe_loaders: Regex,
    marshal_load_pattern: Regex,
    shelve_pattern: Regex,
}

impl PickleDeserializationDetector {
    /// Create a new detector with default settings
    pub fn new() -> Self {
        Self::with_config(DetectorConfig::new(), PathBuf::from("."))
    }

    /// Create with custom repository path
    pub fn with_repository_path(repository_path: PathBuf) -> Self {
        Self::with_config(DetectorConfig::new(), repository_path)
    }

    /// Create with custom config and repository path
    pub fn with_config(config: DetectorConfig, repository_path: PathBuf) -> Self {
        let max_findings = config.get_option_or("max_findings", 100);
        let exclude_patterns = config
            .get_option::<Vec<String>>("exclude_patterns")
            .unwrap_or_else(|| {
                DEFAULT_EXCLUDE_PATTERNS
                    .iter()
                    .map(|s| s.to_string())
                    .collect()
            });

        // Compile patterns
        let pickle_load_pattern = Regex::new(
            r"(?i)\b(?:pickle|cPickle|_pickle|dill|cloudpickle)\.(?:load|loads)\s*\("
        ).unwrap();

        let torch_load_pattern = Regex::new(
            r"(?i)\btorch\.load\s*\([^)]*\)"
        ).unwrap();

        let torch_safe_pattern = Regex::new(
            r"(?i)weights_only\s*=\s*True"
        ).unwrap();

        let joblib_load_pattern = Regex::new(
            r"(?i)\bjoblib\.load\s*\("
        ).unwrap();

        let numpy_load_pattern = Regex::new(
            r"(?i)\b(?:numpy|np)\.load\s*\([^)]*\)"
        ).unwrap();

        let numpy_pickle_pattern = Regex::new(
            r"(?i)allow_pickle\s*=\s*True"
        ).unwrap();

        let yaml_load_pattern = Regex::new(
            r"(?i)\byaml\.(?:load|unsafe_load|full_load)\s*\([^)]*\)"
        ).unwrap();

        let yaml_safe_loaders = Regex::new(
            r"(?i)Loader\s*=\s*(?:yaml\.)?(?:Safe|CSafe|Base)Loader"
        ).unwrap();

        let marshal_load_pattern = Regex::new(
            r"(?i)\bmarshal\.(?:load|loads)\s*\("
        ).unwrap();

        let shelve_pattern = Regex::new(
            r"(?i)\bshelve\.open\s*\("
        ).unwrap();

        Self {
            config,
            repository_path,
            max_findings,
            exclude_patterns,
            pickle_load_pattern,
            torch_load_pattern,
            torch_safe_pattern,
            joblib_load_pattern,
            numpy_load_pattern,
            numpy_pickle_pattern,
            yaml_load_pattern,
            yaml_safe_loaders,
            marshal_load_pattern,
            shelve_pattern,
        }
    }

    /// Check if path should be excluded
    fn should_exclude(&self, path: &str) -> bool {
        for pattern in &self.exclude_patterns {
            if pattern.ends_with('/') {
                let dir = pattern.trim_end_matches('/');
                if path.split('/').any(|p| p == dir) {
                    return true;
                }
            } else if pattern.contains('*') {
                let pattern = pattern.replace('*', ".*");
                if let Ok(re) = Regex::new(&format!("^{}$", pattern)) {
                    let filename = Path::new(path)
                        .file_name()
                        .and_then(|s| s.to_str())
                        .unwrap_or("");
                    if re.is_match(path) || re.is_match(filename) {
                        return true;
                    }
                }
            } else if path.contains(pattern) {
                return true;
            }
        }
        false
    }

    /// Check a line for dangerous deserialization patterns
    fn check_line_for_patterns(&self, line: &str) -> Option<&'static str> {
        let stripped = line.trim();
        if stripped.starts_with('#') {
            return None;
        }

        // Pattern 1: pickle.load() / pickle.loads() - ALWAYS DANGEROUS
        if self.pickle_load_pattern.is_match(line) {
            return Some("pickle_load");
        }

        // Pattern 2: torch.load() without weights_only=True
        if self.torch_load_pattern.is_match(line) {
            if !self.torch_safe_pattern.is_match(line) {
                return Some("torch_load_unsafe");
            }
        }

        // Pattern 3: joblib.load() - uses pickle internally
        if self.joblib_load_pattern.is_match(line) {
            return Some("joblib_load");
        }

        // Pattern 4: numpy.load() with allow_pickle=True
        if self.numpy_load_pattern.is_match(line) {
            if self.numpy_pickle_pattern.is_match(line) {
                return Some("numpy_pickle");
            }
        }

        // Pattern 5: yaml.load() without SafeLoader
        if self.yaml_load_pattern.is_match(line) {
            if !self.yaml_safe_loaders.is_match(line) && !line.to_lowercase().contains("safe_load") {
                return Some("yaml_unsafe");
            }
        }

        // Pattern 6: marshal.load() - bytecode execution
        if self.marshal_load_pattern.is_match(line) {
            return Some("marshal_load");
        }

        // Pattern 7: shelve.open() - uses pickle
        if self.shelve_pattern.is_match(line) {
            return Some("shelve_open");
        }

        None
    }

    /// Scan source files for dangerous patterns
    fn scan_source_files(&self) -> Vec<Finding> {
        use crate::detectors::walk_source_files;
        
        let mut findings = Vec::new();
        let mut seen_locations: HashSet<(String, u32)> = HashSet::new();

        if !self.repository_path.exists() {
            return findings;
        }

        // Walk through Python files (respects .gitignore and .repotoireignore)
        for path in walk_source_files(&self.repository_path, Some(&["py"])) {
            let rel_path = path
                .strip_prefix(&self.repository_path)
                .unwrap_or(&path)
                .to_string_lossy()
                .to_string();

            if self.should_exclude(&rel_path) {
                continue;
            }

            let content = match std::fs::read_to_string(&path) {
                Ok(c) => c,
                Err(_) => continue,
            };

            // Skip very large files
            if content.len() > 500_000 {
                continue;
            }

            let lines: Vec<&str> = content.lines().collect();
            for (line_no, line) in lines.iter().enumerate() {
                let line_num = (line_no + 1) as u32;
                
                // Check for suppression comments
                let prev_line = if line_no > 0 { Some(lines[line_no - 1]) } else { None };
                if crate::detectors::is_line_suppressed(line, prev_line) {
                    continue;
                }

                if let Some(pattern_type) = self.check_line_for_patterns(line) {
                    let loc = (rel_path.clone(), line_num);
                    if seen_locations.contains(&loc) {
                        continue;
                    }
                    seen_locations.insert(loc);

                    findings.push(self.create_finding(
                        &rel_path,
                        line_num,
                        pattern_type,
                        line.trim(),
                    ));

                    if findings.len() >= self.max_findings {
                        return findings;
                    }
                }
            }
        }

        findings
    }

    /// Create a finding for detected deserialization vulnerability
    fn create_finding(
        &self,
        file_path: &str,
        line_start: u32,
        pattern_type: &str,
        snippet: &str,
    ) -> Finding {
        let pattern_descriptions = [
            ("pickle_load", "pickle.load()/loads() - arbitrary code execution on untrusted data"),
            ("torch_load_unsafe", "torch.load() without weights_only=True - can execute arbitrary code"),
            ("joblib_load", "joblib.load() - uses pickle internally, arbitrary code execution"),
            ("numpy_pickle", "numpy.load() with allow_pickle=True - enables pickle execution"),
            ("yaml_unsafe", "yaml.load() without SafeLoader - arbitrary code execution"),
            ("marshal_load", "marshal.load() - Python bytecode execution"),
            ("shelve_open", "shelve.open() - uses pickle internally"),
        ];

        let pattern_desc = pattern_descriptions
            .iter()
            .find(|(t, _)| *t == pattern_type)
            .map(|(_, d)| *d)
            .unwrap_or("unsafe deserialization");

        let title = "Unsafe Deserialization (CWE-502)".to_string();

        let description = format!(
            "**Unsafe Deserialization Vulnerability**\n\n\
             **Pattern detected**: {}\n\n\
             **Location**: {}:{}\n\n\
             **Code snippet**:\n```python\n{}\n```\n\n\
             Deserializing untrusted data can allow attackers to execute arbitrary code.\n\
             Pickle, joblib, torch.load, yaml.load, and similar functions execute code\n\
             embedded in the serialized data. An attacker who controls the input can\n\
             achieve Remote Code Execution (RCE).\n\n\
             This vulnerability is classified as:\n\
             - **CWE-502**: Deserialization of Untrusted Data\n\
             - **OWASP A8:2017**: Insecure Deserialization",
            pattern_desc, file_path, line_start, snippet
        );

        let suggested_fix = self.get_recommendation(pattern_type);

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "PickleDeserializationDetector".to_string(),
            severity: Severity::High,
            title,
            description,
            affected_files: vec![PathBuf::from(file_path)],
            line_start: Some(line_start),
            line_end: Some(line_start),
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some("Medium (2-8 hours)".to_string()),
            category: Some("security".to_string()),
            cwe_id: Some("CWE-502".to_string()),
            why_it_matters: Some(
                "Insecure deserialization can lead to Remote Code Execution, allowing attackers \
                 to take complete control of the application and server."
                    .to_string(),
            ),
        }
    }

    /// Get recommendation based on pattern type
    fn get_recommendation(&self, pattern_type: &str) -> String {
        match pattern_type {
            "pickle_load" => {
                "**Recommended fixes for pickle.load()**:\n\n\
                 1. **Avoid pickle for untrusted data** (preferred):\n\
                    ```python\n\
                    # Instead of pickle, use JSON for data exchange:\n\
                    import json\n\
                    data = json.loads(untrusted_input)\n\
                    ```\n\n\
                 2. **Use safer alternatives**:\n\
                    - JSON for structured data\n\
                    - Protocol Buffers for binary data\n\
                    - msgpack with strict mode\n\
                    - YAML with SafeLoader\n\n\
                 3. **If pickle is required**, validate the source:\n\
                    ```python\n\
                    # Only load from trusted, signed sources\n\
                    if verify_signature(file_path, trusted_key):\n\
                        data = pickle.load(open(file_path, 'rb'))\n\
                    ```".to_string()
            }
            "torch_load_unsafe" => {
                "**Recommended fixes for torch.load()**:\n\n\
                 1. **Use weights_only=True** (preferred):\n\
                    ```python\n\
                    # Safe: only loads tensor weights, no arbitrary code\n\
                    model = torch.load('model.pt', weights_only=True)\n\
                    ```\n\n\
                 2. **Use safetensors format**:\n\
                    ```python\n\
                    from safetensors.torch import load_file\n\
                    state_dict = load_file('model.safetensors')\n\
                    model.load_state_dict(state_dict)\n\
                    ```\n\n\
                 3. **Validate model source** before loading.".to_string()
            }
            "joblib_load" => {
                "**Recommended fixes for joblib.load()**:\n\n\
                 1. **Verify the source** - only load from trusted sources:\n\
                    ```python\n\
                    # Verify checksum before loading\n\
                    if verify_checksum(model_path, expected_hash):\n\
                        model = joblib.load(model_path)\n\
                    ```\n\n\
                 2. **Use ONNX format** for ML models (safer):\n\
                    ```python\n\
                    import onnxruntime as ort\n\
                    session = ort.InferenceSession('model.onnx')\n\
                    ```\n\n\
                 3. **Consider skops** for scikit-learn:\n\
                    ```python\n\
                    from skops.io import load\n\
                    model = load('model.skops', trusted=['sklearn.linear_model.LogisticRegression'])\n\
                    ```".to_string()
            }
            "numpy_pickle" => {
                "**Recommended fixes for numpy.load() with allow_pickle**:\n\n\
                 1. **Avoid allow_pickle=True** if possible:\n\
                    ```python\n\
                    # Load only array data (no pickle)\n\
                    data = np.load('data.npy', allow_pickle=False)\n\
                    ```\n\n\
                 2. **Use .npz files without pickle**:\n\
                    ```python\n\
                    # Save without object arrays\n\
                    np.savez('data.npz', array1=arr1, array2=arr2)\n\
                    ```\n\n\
                 3. **Verify source** before enabling pickle:\n\
                    ```python\n\
                    if is_trusted_source(file_path):\n\
                        data = np.load(file_path, allow_pickle=True)\n\
                    ```".to_string()
            }
            "yaml_unsafe" => {
                "**Recommended fixes for yaml.load()**:\n\n\
                 1. **Use SafeLoader** (preferred):\n\
                    ```python\n\
                    import yaml\n\
                    # Safe: only loads basic Python types\n\
                    data = yaml.load(content, Loader=yaml.SafeLoader)\n\n\
                    # Or use the safe_load shortcut:\n\
                    data = yaml.safe_load(content)\n\
                    ```\n\n\
                 2. **Use FullLoader with caution** (limited code execution):\n\
                    ```python\n\
                    # Less safe but more capable:\n\
                    data = yaml.load(content, Loader=yaml.FullLoader)\n\
                    ```\n\n\
                 3. **Never use yaml.unsafe_load()** on untrusted data.".to_string()
            }
            "marshal_load" => {
                "**Recommended fixes for marshal.load()**:\n\n\
                 1. **Avoid marshal for data exchange** - it's for Python bytecode:\n\
                    ```python\n\
                    # Use JSON or pickle for data serialization\n\
                    import json\n\
                    data = json.loads(content)\n\
                    ```\n\n\
                 2. **Validate source strictly** if marshal is required:\n\
                    ```python\n\
                    # Only load bytecode from verified, signed sources\n\
                    if verify_code_signature(path):\n\
                        code = marshal.load(open(path, 'rb'))\n\
                    ```".to_string()
            }
            "shelve_open" => {
                "**Recommended fixes for shelve.open()**:\n\n\
                 1. **Use safer alternatives** for key-value storage:\n\
                    ```python\n\
                    # Use SQLite for persistent storage:\n\
                    import sqlite3\n\
                    conn = sqlite3.connect('data.db')\n\n\
                    # Or use JSON files:\n\
                    import json\n\
                    with open('data.json') as f:\n\
                        data = json.load(f)\n\
                    ```\n\n\
                 2. **Validate source** before opening shelve databases from external sources.".to_string()
            }
            _ => {
                "**General recommendations**:\n\n\
                 1. Never deserialize untrusted data with pickle or similar libraries\n\
                 2. Use JSON, Protocol Buffers, or other safe formats for data exchange\n\
                 3. Verify the source and integrity of any serialized data before loading\n\
                 4. Consider using signed/encrypted containers for trusted data".to_string()
            }
        }
    }
}

impl Default for PickleDeserializationDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for PickleDeserializationDetector {
    fn name(&self) -> &'static str {
        "PickleDeserializationDetector"
    }

    fn description(&self) -> &'static str {
        "Detects unsafe deserialization patterns (pickle, torch.load, yaml.load, etc.)"
    }

    fn category(&self) -> &'static str {
        "security"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        debug!("Starting pickle deserialization detection");

        let findings = self.scan_source_files();

        info!("PickleDeserializationDetector found {} potential vulnerabilities", findings.len());

        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pickle_detection() {
        let detector = PickleDeserializationDetector::new();

        // Should detect pickle.load
        assert!(detector.check_line_for_patterns("data = pickle.load(f)").is_some());

        // Should detect pickle.loads
        assert!(detector.check_line_for_patterns("data = pickle.loads(data)").is_some());

        // Should detect dill
        assert!(detector.check_line_for_patterns("obj = dill.load(f)").is_some());
    }

    #[test]
    fn test_torch_load_detection() {
        let detector = PickleDeserializationDetector::new();

        // Should detect unsafe torch.load
        assert_eq!(
            detector.check_line_for_patterns("model = torch.load('model.pt')"),
            Some("torch_load_unsafe")
        );

        // Should NOT detect safe torch.load with weights_only=True
        assert!(detector.check_line_for_patterns("model = torch.load('model.pt', weights_only=True)").is_none());
    }

    #[test]
    fn test_yaml_detection() {
        let detector = PickleDeserializationDetector::new();

        // Should detect unsafe yaml.load
        assert_eq!(
            detector.check_line_for_patterns("data = yaml.load(content)"),
            Some("yaml_unsafe")
        );

        // Should NOT detect safe yaml.load with SafeLoader
        assert!(detector.check_line_for_patterns("data = yaml.load(content, Loader=yaml.SafeLoader)").is_none());

        // Should NOT detect yaml.safe_load
        assert!(detector.check_line_for_patterns("data = yaml.safe_load(content)").is_none());
    }

    #[test]
    fn test_numpy_detection() {
        let detector = PickleDeserializationDetector::new();

        // Should detect numpy.load with allow_pickle=True
        assert_eq!(
            detector.check_line_for_patterns("data = np.load('data.npy', allow_pickle=True)"),
            Some("numpy_pickle")
        );

        // Should NOT detect safe numpy.load
        assert!(detector.check_line_for_patterns("data = np.load('data.npy')").is_none());
    }
}
