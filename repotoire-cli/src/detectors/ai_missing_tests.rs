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

#![allow(dead_code)] // Module under development - structs/helpers used in tests only

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::function_context::{FunctionContextMap, FunctionRole};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::Arc;
use tracing::debug;
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

    /// Check if a function is a test function or fixture
    /// This is a static method for use in detect() without &self
    fn is_test_function(name: &str, file_path: &str) -> bool {
        let name_lower = name.to_lowercase();
        let path_lower = file_path.to_lowercase();
        
        // Check function name patterns
        if name_lower.starts_with("test_") 
            || name_lower.starts_with("test")  // testSomething (camelCase)
            || name_lower.ends_with("_test")
            || name_lower == "setup"
            || name_lower == "teardown"
            || name_lower == "setup_module"
            || name_lower == "teardown_module"
            || name_lower == "setup_class"
            || name_lower == "teardown_class"
            || name_lower == "setuptestdata"  // Django
            || name_lower.starts_with("fixture_")
        {
            return true;
        }
        
        // Check if in test file/directory
        if path_lower.contains("/test/")
            || path_lower.contains("/tests/")
            || path_lower.contains("/__tests__/")
            || path_lower.contains("/spec/")
            || path_lower.contains("_test.py")
            || path_lower.contains("_test.go")
            || path_lower.contains("_test.rs")
            || path_lower.contains(".test.ts")
            || path_lower.contains(".test.js")
            || path_lower.contains(".spec.ts")
            || path_lower.contains(".spec.js")
            || path_lower.starts_with("test_")
        {
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
        _qualified_name: &str,
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
            ..Default::default()
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
    }    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();
        use std::collections::HashSet;
        
        // Get all test functions
        let test_funcs: HashSet<String> = graph.get_functions()
            .iter()
            .filter(|f| Self::is_test_function(&f.name, &f.file_path))
            .map(|f| f.name.clone())
            .collect();
        
        // Find complex public functions without tests
        for func in graph.get_functions() {
            // Skip test functions, fixtures, and test files
            if Self::is_test_function(&func.name, &func.file_path) {
                continue;
            }
            
            // Skip private functions
            if func.name.starts_with('_') && !func.name.starts_with("__") {
                continue;
            }
            
            // Skip dunder methods
            if func.name.starts_with("__") && func.name.ends_with("__") {
                continue;
            }
            
            let complexity = func.complexity().unwrap_or(1);
            let loc = func.loc();
            
            // Only flag complex/large functions
            if complexity < 5 && loc < 20 {
                continue;
            }
            
            // Check if there's a test for this function
            let test_name = format!("test_{}", func.name);
            if !test_funcs.contains(&test_name) && !test_funcs.iter().any(|t| t.contains(&func.name)) {
                let severity = if complexity > 15 {
                    Severity::High
                } else if complexity > 10 {
                    Severity::Medium
                } else {
                    Severity::Low
                };
                
                findings.push(Finding {
                    id: Uuid::new_v4().to_string(),
                    detector: "AIMissingTestsDetector".to_string(),
                    severity,
                    title: format!("Missing Test: {}", func.name),
                    description: format!(
                        "Function '{}' (complexity: {}, {} LOC) has no test coverage.",
                        func.name, complexity, loc
                    ),
                    affected_files: vec![func.file_path.clone().into()],
                    line_start: Some(func.line_start),
                    line_end: Some(func.line_end),
                    suggested_fix: Some(format!("Add test function: test_{}", func.name)),
                    estimated_effort: Some("Small (30 min)".to_string()),
                    category: Some("ai_watchdog".to_string()),
                    cwe_id: None,
                    why_it_matters: Some("Complex untested code is a maintenance risk".to_string()),
                    ..Default::default()
                });
            }
        }
        
        // Limit findings
        findings.truncate(50);
        Ok(findings)
    }

    fn uses_context(&self) -> bool {
        true
    }

    fn detect_with_context(
        &self,
        graph: &GraphStore,
        contexts: &Arc<FunctionContextMap>,
    ) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();
        
        // Get all test functions (from context or name/path patterns)
        let test_funcs: HashSet<String> = graph.get_functions()
            .iter()
            .filter(|f| {
                // Check context first
                if let Some(ctx) = contexts.get(&f.qualified_name) {
                    if ctx.is_test || ctx.role == FunctionRole::Test {
                        return true;
                    }
                }
                // Fall back to name/path patterns
                Self::is_test_function(&f.name, &f.file_path)
            })
            .map(|f| f.name.clone())
            .collect();
        
        debug!("AIMissingTestsDetector: found {} test functions", test_funcs.len());
        
        // Find complex public functions without tests
        for func in graph.get_functions() {
            // Check context first for test detection
            if let Some(ctx) = contexts.get(&func.qualified_name) {
                if ctx.is_test || ctx.role == FunctionRole::Test {
                    continue;
                }
            }
            
            // Fall back to name/path pattern check
            if Self::is_test_function(&func.name, &func.file_path) {
                continue;
            }
            
            // Skip private functions
            if func.name.starts_with('_') && !func.name.starts_with("__") {
                continue;
            }
            
            // Skip dunder methods
            if func.name.starts_with("__") && func.name.ends_with("__") {
                continue;
            }
            
            // Get complexity from context or graph
            let complexity = if let Some(ctx) = contexts.get(&func.qualified_name) {
                ctx.complexity.unwrap_or(1)
            } else {
                func.complexity().unwrap_or(1)
            };
            
            let loc = func.loc();
            
            // Only flag complex/large functions
            if complexity < 5 && loc < 20 {
                continue;
            }
            
            // Check if there's a test for this function
            let test_name = format!("test_{}", func.name);
            if !test_funcs.contains(&test_name) && !test_funcs.iter().any(|t| t.contains(&func.name)) {
                let severity = if complexity > 15 {
                    Severity::High
                } else if complexity > 10 {
                    Severity::Medium
                } else {
                    Severity::Low
                };
                
                findings.push(Finding {
                    id: Uuid::new_v4().to_string(),
                    detector: "AIMissingTestsDetector".to_string(),
                    severity,
                    title: format!("Missing Test: {}", func.name),
                    description: format!(
                        "Function '{}' (complexity: {}, {} LOC) has no test coverage.",
                        func.name, complexity, loc
                    ),
                    affected_files: vec![func.file_path.clone().into()],
                    line_start: Some(func.line_start),
                    line_end: Some(func.line_end),
                    suggested_fix: Some(format!("Add test function: test_{}", func.name)),
                    estimated_effort: Some("Small (30 min)".to_string()),
                    category: Some("ai_watchdog".to_string()),
                    cwe_id: None,
                    why_it_matters: Some("Complex untested code is a maintenance risk".to_string()),
                    ..Default::default()
                });
            }
        }
        
        // Limit findings
        findings.truncate(50);
        debug!("AIMissingTestsDetector: found {} missing test findings", findings.len());
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
