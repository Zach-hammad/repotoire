//! AI Missing Tests detector - identifies new code added without tests
//!
//! Detects functions/methods that don't have corresponding test coverage.
//! This is a common pattern when AI generates implementation code but neglects
//! to generate tests.
//!
//! Detection Strategy:
//! 1. Find all functions in non-test files (detected by path pattern)
//! 2. Exclude functions that are themselves test functions
//! 3. Check for corresponding test functions using naming conventions
//! 4. Flag functions without test coverage
//!
//! Test File Detection (by path pattern):
//! - Python: test_*.py, *_test.py, tests/*.py
//! - JavaScript/TypeScript: *.test.js, *.spec.ts, __tests__/*

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphClient;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::PathBuf;
use tracing::{debug, info};
use uuid::Uuid;

/// Default configuration
const DEFAULT_MIN_FUNCTION_LOC: usize = 5;
const DEFAULT_EXCLUDE_PRIVATE: bool = true;
const DEFAULT_EXCLUDE_DUNDER: bool = true;
const DEFAULT_MAX_FINDINGS: usize = 50;

/// Test file patterns for different languages
const TEST_FILE_PATTERNS: &[&str] = &[
    // Python
    r"test_.*\.py$",
    r".*_test\.py$",
    r"tests?/.*\.py$",
    r".*tests?\.py$",
    // JavaScript/TypeScript
    r".*\.test\.[jt]sx?$",
    r".*\.spec\.[jt]sx?$",
    r"__tests__/.*\.[jt]sx?$",
];

/// Detects functions/methods that lack corresponding tests
pub struct AIMissingTestsDetector {
    config: DetectorConfig,
    min_function_loc: usize,
    exclude_private: bool,
    exclude_dunder: bool,
    max_findings: usize,
    test_file_patterns: Vec<Regex>,
}

impl AIMissingTestsDetector {
    /// Create a new detector with default settings
    pub fn new() -> Self {
        let test_file_patterns = TEST_FILE_PATTERNS
            .iter()
            .filter_map(|p| Regex::new(p).ok())
            .collect();

        Self {
            config: DetectorConfig::new(),
            min_function_loc: DEFAULT_MIN_FUNCTION_LOC,
            exclude_private: DEFAULT_EXCLUDE_PRIVATE,
            exclude_dunder: DEFAULT_EXCLUDE_DUNDER,
            max_findings: DEFAULT_MAX_FINDINGS,
            test_file_patterns,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        let test_file_patterns = TEST_FILE_PATTERNS
            .iter()
            .filter_map(|p| Regex::new(p).ok())
            .collect();

        Self {
            min_function_loc: config.get_option_or("min_function_loc", DEFAULT_MIN_FUNCTION_LOC),
            exclude_private: config.get_option_or("exclude_private", DEFAULT_EXCLUDE_PRIVATE),
            exclude_dunder: config.get_option_or("exclude_dunder", DEFAULT_EXCLUDE_DUNDER),
            max_findings: config.get_option_or("max_findings", DEFAULT_MAX_FINDINGS),
            config,
            test_file_patterns,
        }
    }

    /// Check if a file path matches test file patterns
    fn is_test_file(&self, file_path: &str) -> bool {
        let file_lower = file_path.to_lowercase();
        self.test_file_patterns
            .iter()
            .any(|p| p.is_match(&file_lower))
    }

    /// Check if function should be skipped
    fn should_skip_function(&self, name: &str, file_path: &str) -> bool {
        if name.is_empty() {
            return true;
        }

        let name_lower = name.to_lowercase();

        // Skip test functions themselves
        if name_lower.starts_with("test") || name_lower.ends_with("_test") {
            return true;
        }

        // Skip functions in test files
        if self.is_test_file(file_path) {
            return true;
        }

        // Skip private functions if configured
        if self.exclude_private && name.starts_with('_') && !name.starts_with("__") {
            return true;
        }

        // Skip dunder methods if configured
        if self.exclude_dunder && name.starts_with("__") && name.ends_with("__") {
            return true;
        }

        false
    }

    /// Get possible test function names for a given function
    fn get_test_function_variants(&self, func_name: &str) -> Vec<String> {
        let name_lower = func_name.to_lowercase();
        let mut variants = vec![
            format!("test_{}", name_lower),
            format!("test{}", name_lower),
            format!("{}_test", name_lower),
        ];

        // For methods, also check class-based test names
        if name_lower.contains('_') {
            for part in name_lower.split('_') {
                if part.len() > 2 {
                    variants.push(format!("test_{}", part));
                }
            }
        }

        variants
    }

    /// Get possible test file paths for a given source file
    fn get_test_file_variants(&self, file_path: &str) -> Vec<String> {
        let normalized = file_path.replace('\\', "/");
        let parts: Vec<&str> = normalized.split('/').collect();
        let filename = parts.last().unwrap_or(&"");

        let module_name = if filename.contains('.') {
            filename
                .rsplit_once('.')
                .map(|(name, _)| name)
                .unwrap_or(filename)
        } else {
            filename
        };

        if module_name.is_empty() {
            return vec![];
        }

        vec![
            format!("test_{}.py", module_name),
            format!("tests/test_{}.py", module_name),
            format!("test/test_{}.py", module_name),
            format!("{}_test.py", module_name),
            format!("tests/{}_test.py", module_name),
            format!("{}.test.js", module_name),
            format!("{}.spec.js", module_name),
            format!("{}.test.ts", module_name),
            format!("{}.spec.ts", module_name),
            format!("__tests__/{}.js", module_name),
            format!("__tests__/{}.ts", module_name),
        ]
    }

    /// Generate test suggestion for a function
    fn generate_test_suggestion(&self, func_name: &str, language: &str) -> String {
        let lang = language.to_lowercase();

        if lang == "python" || lang.is_empty() {
            format!(
                r#"Create a comprehensive test for '{}':

```python
def test_{}_success():
    """Test {} with valid input."""
    result = {}(valid_input)
    assert result is not None
    assert result == expected_value

def test_{}_edge_cases():
    """Test {} edge cases."""
    # Test boundary conditions
    assert {}(min_value) == expected_min
    assert {}(max_value) == expected_max

def test_{}_error_handling():
    """Test {} error handling."""
    with pytest.raises(ValueError):
        {}(invalid_input)
```"#,
                func_name,
                func_name,
                func_name,
                func_name,
                func_name,
                func_name,
                func_name,
                func_name,
                func_name,
                func_name,
                func_name
            )
        } else if lang == "javascript" || lang == "typescript" {
            format!(
                r#"Create a comprehensive test for '{}':

```{}
describe('{}', () => {{
  it('should handle valid input', () => {{
    const result = {}(validInput);
    expect(result).toBeDefined();
    expect(result).toEqual(expectedValue);
  }});

  it('should handle edge cases', () => {{
    expect({}(minValue)).toEqual(expectedMin);
    expect({}(maxValue)).toEqual(expectedMax);
  }});

  it('should throw on invalid input', () => {{
    expect(() => {}(invalidInput)).toThrow();
  }});
}});
```"#,
                func_name, lang, func_name, func_name, func_name, func_name, func_name
            )
        } else {
            format!(
                "Add comprehensive test coverage for '{}' with multiple assertions and error handling tests.",
                func_name
            )
        }
    }

    /// Create a finding for a function without tests
    fn create_finding(
        &self,
        qualified_name: &str,
        name: &str,
        file_path: &str,
        line_start: Option<u32>,
        line_end: Option<u32>,
        loc: usize,
        is_method: bool,
        language: &str,
    ) -> Finding {
        let func_type = if is_method { "method" } else { "function" };

        let description = format!(
            "The {} '{}' has no corresponding test. \
             This is a common pattern when AI generates implementation code without tests.{}",
            func_type,
            name,
            if loc > 0 {
                format!(" The {} has {} lines of code.", func_type, loc)
            } else {
                String::new()
            }
        );

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "AIMissingTestsDetector".to_string(),
            severity: Severity::Medium,
            title: format!("Missing tests for {}: {}", func_type, name),
            description,
            affected_files: vec![PathBuf::from(file_path)],
            line_start,
            line_end,
            suggested_fix: Some(self.generate_test_suggestion(name, language)),
            estimated_effort: Some("Small (15-45 minutes)".to_string()),
            category: Some("test_coverage".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Untested code is a risk. Tests catch bugs early, document expected behavior, \
                 and make refactoring safer. AI-generated code especially needs tests since \
                 AI may produce subtly incorrect implementations."
                    .to_string(),
            ),
        }
    }
}

impl Default for AIMissingTestsDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for AIMissingTestsDetector {
    fn name(&self) -> &'static str {
        "AIMissingTestsDetector"
    }

    fn description(&self) -> &'static str {
        "Detects functions/methods that lack corresponding tests"
    }

    fn category(&self) -> &'static str {
        "ai_generated"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        debug!("Starting AI missing tests detection");

        // Step 1: Get all test function names
        let test_query = r#"
            MATCH (f:Function)
            WHERE f.name IS NOT NULL
              AND (f.name STARTS WITH 'test_' 
                   OR f.name STARTS WITH 'test'
                   OR f.name ENDS WITH '_test')
            RETURN DISTINCT lower(f.name) AS test_name
        "#;

        let test_results = graph.execute(test_query)?;
        let test_names: HashSet<String> = test_results
            .iter()
            .filter_map(|row| row.get("test_name").and_then(|v| v.as_str()))
            .map(String::from)
            .collect();

        debug!("Found {} test functions", test_names.len());

        // Step 2: Get test files
        let file_query = r#"
            MATCH (f:File)
            WHERE f.filePath IS NOT NULL
            RETURN f.filePath AS file_path
        "#;

        let file_results = graph.execute(file_query)?;
        let test_files: HashSet<String> = file_results
            .iter()
            .filter_map(|row| row.get("file_path").and_then(|v| v.as_str()))
            .filter(|p| self.is_test_file(p))
            .map(|p| p.to_lowercase())
            .collect();

        debug!("Found {} test files", test_files.len());

        // Step 3: Get all functions
        let func_query = r#"
            MATCH (file:File)-[:CONTAINS*]->(func:Function)
            WHERE func.name IS NOT NULL
              AND (func.loc >= $min_loc OR func.loc IS NULL)
            RETURN DISTINCT 
                   func.qualifiedName AS qualified_name,
                   func.name AS name,
                   func.lineStart AS line_start,
                   func.lineEnd AS line_end,
                   func.loc AS loc,
                   func.isMethod AS is_method,
                   file.filePath AS file_path,
                   file.language AS language
            LIMIT $max_results
        "#;

        let _params = serde_json::json!({
            "min_loc": self.min_function_loc,
            "max_results": self.max_findings * 3,
        });

        let func_results = graph.execute(func_query)?;

        if func_results.is_empty() {
            debug!("No functions found for test coverage analysis");
            return Ok(vec![]);
        }

        // Step 4: Find functions without tests
        let mut findings: Vec<Finding> = Vec::new();

        for row in func_results {
            let name = row.get("name").and_then(|v| v.as_str()).unwrap_or("");

            let file_path = row.get("file_path").and_then(|v| v.as_str()).unwrap_or("");

            if self.should_skip_function(name, file_path) {
                continue;
            }

            let qualified_name = row
                .get("qualified_name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let line_start = row
                .get("line_start")
                .and_then(|v| v.as_u64())
                .map(|v| v as u32);
            let line_end = row
                .get("line_end")
                .and_then(|v| v.as_u64())
                .map(|v| v as u32);
            let loc = row.get("loc").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
            let is_method = row
                .get("is_method")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            let language = row
                .get("language")
                .and_then(|v| v.as_str())
                .unwrap_or("python");

            // Check if any test function variant exists
            let test_variants = self.get_test_function_variants(name);
            let has_test = test_variants.iter().any(|v| test_names.contains(v));

            if has_test {
                continue;
            }

            // Check if test file exists
            let test_file_variants = self.get_test_file_variants(file_path);
            let has_test_file = test_file_variants
                .iter()
                .any(|v| test_files.contains(&v.to_lowercase()));

            if has_test_file {
                // Test file exists but no specific test function found
                // Give benefit of doubt
                continue;
            }

            findings.push(self.create_finding(
                &qualified_name,
                name,
                file_path,
                line_start,
                line_end,
                loc,
                is_method,
                language,
            ));

            if findings.len() >= self.max_findings {
                break;
            }
        }

        info!(
            "AIMissingTestsDetector found {} functions without tests",
            findings.len()
        );

        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_test_file() {
        let detector = AIMissingTestsDetector::new();

        assert!(detector.is_test_file("test_module.py"));
        assert!(detector.is_test_file("module_test.py"));
        assert!(detector.is_test_file("tests/module.py"));
        assert!(detector.is_test_file("app.test.js"));
        assert!(detector.is_test_file("app.spec.ts"));
        assert!(detector.is_test_file("__tests__/app.js"));

        assert!(!detector.is_test_file("module.py"));
        assert!(!detector.is_test_file("app.js"));
    }

    #[test]
    fn test_should_skip_function() {
        let detector = AIMissingTestsDetector::new();

        // Test functions should be skipped
        assert!(detector.should_skip_function("test_something", "module.py"));
        assert!(detector.should_skip_function("something_test", "module.py"));

        // Functions in test files should be skipped
        assert!(detector.should_skip_function("helper", "test_module.py"));

        // Private functions should be skipped (by default)
        assert!(detector.should_skip_function("_private", "module.py"));

        // Dunder methods should be skipped (by default)
        assert!(detector.should_skip_function("__init__", "module.py"));

        // Regular functions should not be skipped
        assert!(!detector.should_skip_function("process_data", "module.py"));
    }

    #[test]
    fn test_get_test_function_variants() {
        let detector = AIMissingTestsDetector::new();
        let variants = detector.get_test_function_variants("process_data");

        assert!(variants.contains(&"test_process_data".to_string()));
        assert!(variants.contains(&"testprocess_data".to_string()));
        assert!(variants.contains(&"process_data_test".to_string()));
        assert!(variants.contains(&"test_process".to_string()));
        assert!(variants.contains(&"test_data".to_string()));
    }
}
