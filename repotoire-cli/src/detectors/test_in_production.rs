//! Test Code in Production Detector
//!
//! Graph-enhanced detection of test code in production:
//! - Distinguish actual test imports from false positives
//! - Check if test code is actually used in production paths
//! - Categorize test patterns (mocks, assertions, fixtures)

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static TEST_IMPORT: OnceLock<Regex> = OnceLock::new();
static TEST_USAGE: OnceLock<Regex> = OnceLock::new();
static DEBUG_PATTERN: OnceLock<Regex> = OnceLock::new();

fn test_import() -> &'static Regex {
    TEST_IMPORT.get_or_init(|| {
        Regex::new(r#"(?i)(import.*pytest|import.*unittest|import.*mock|from.*mock|require\(['"]jest|require\(['"]sinon|import.*@testing-library)"#).expect("valid regex")
    })
}

fn test_usage() -> &'static Regex {
    // Note: Removed expect( as it's used in production assertion/error libraries
    // Removed describe( and it( as they conflict with normal code patterns
    TEST_USAGE.get_or_init(|| {
        Regex::new(r"(?i)(mock\.|Mock\(|MagicMock|patch\(|stub\.|fake\.|spy\.|jest\.|sinon\.|@pytest|@test|unittest\.|\.toBe\(|\.toEqual\(|\.toHaveBeenCalled|\.toThrow\(|fixture|@Before|@After|@BeforeEach)").expect("valid regex")
    })
}

fn debug_pattern() -> &'static Regex {
    DEBUG_PATTERN.get_or_init(|| {
        Regex::new(
            r"(?i)(DEBUG\s*=\s*True|if\s+__debug__|if\s+DEBUG|#\s*TODO.*test|#\s*FIXME.*test)",
        )
        .expect("valid regex")
    })
}

pub struct TestInProductionDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl TestInProductionDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Categorize what kind of test code was found
    fn categorize_test_code(line: &str) -> (&'static str, &'static str) {
        let lower = line.to_lowercase();

        if lower.contains("mock") || lower.contains("patch") || lower.contains("stub") {
            return ("mock", "Mock/stub objects");
        }
        if lower.contains("assert") || lower.contains("expect") || lower.contains("tobe") {
            return ("assertion", "Test assertions");
        }
        if lower.contains("fixture") || lower.contains("setup") || lower.contains("teardown") {
            return ("fixture", "Test fixtures");
        }
        if lower.contains("describe(") || lower.contains("it(") || lower.contains("test(") {
            return ("framework", "Test framework");
        }
        if lower.contains("spy") {
            return ("spy", "Spy functions");
        }

        ("unknown", "Test code")
    }

    /// Check if this file is imported by production code
    fn is_imported_by_production(graph: &dyn crate::graph::GraphQuery, file_path: &str) -> bool {
        let funcs: Vec<_> = graph
            .get_functions()
            .into_iter()
            .filter(|f| f.file_path == file_path)
            .collect();

        for func in funcs {
            for caller in graph.get_callers(&func.qualified_name) {
                // Check if caller is not a test file
                let caller_path = &caller.file_path;
                if !crate::detectors::base::is_test_path(caller_path) {
                    return true;
                }
            }
        }

        false
    }

    /// Find containing function
    fn find_containing_function(
        graph: &dyn crate::graph::GraphQuery,
        file_path: &str,
        line: u32,
    ) -> Option<String> {
        graph
            .get_functions()
            .into_iter()
            .find(|f| f.file_path == file_path && f.line_start <= line && f.line_end >= line)
            .map(|f| f.name)
    }
}

impl Detector for TestInProductionDetector {
    fn name(&self) -> &'static str {
        "test-in-production"
    }
    fn description(&self) -> &'static str {
        "Detects test code in production files"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let mut issues_per_file: HashMap<PathBuf, Vec<(u32, String, String)>> = HashMap::new();

        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings {
                break;
            }
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let path_str = path.to_string_lossy().to_string();

            // Skip actual test files and devtools (devtools legitimately use debug patterns)
            if crate::detectors::base::is_test_path(&path_str)
                || path_str.contains("__tests__")
                || path_str.contains("fixtures")
                || path_str.contains("conftest")
                || path_str.contains("devtools")
                || path_str.contains("debug")
                || path_str.contains("/scripts/")
            {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(
                ext,
                "py" | "js" | "ts" | "jsx" | "tsx" | "java" | "rb" | "go"
            ) {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().content(path) {
                let lines: Vec<&str> = content.lines().collect();
                let mut file_issues = Vec::new();

                // Check for test imports
                let has_test_import = lines.iter().any(|l| test_import().is_match(l));

                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    // Skip comments
                    let trimmed = line.trim();
                    if trimmed.starts_with("//")
                        || trimmed.starts_with("#")
                        || trimmed.starts_with("*")
                    {
                        continue;
                    }

                    // Check for test code usage
                    if test_usage().is_match(line) {
                        let (category, desc) = Self::categorize_test_code(line);
                        file_issues.push(((i + 1) as u32, category.to_string(), desc.to_string()));
                    }

                    // Check for debug flags
                    if debug_pattern().is_match(line) {
                        file_issues.push((
                            (i + 1) as u32,
                            "debug".to_string(),
                            "Debug flag".to_string(),
                        ));
                    }
                }

                if !file_issues.is_empty() || has_test_import {
                    issues_per_file.insert(path.to_path_buf(), file_issues);
                }
            }
        }

        // Create findings with graph context
        for (file_path, issues) in issues_per_file {
            let path_str = file_path.to_string_lossy().to_string();

            // Check if this file is used by production code
            let is_used = Self::is_imported_by_production(graph, &path_str);

            // Group issues by type
            let mut by_type: HashMap<String, Vec<u32>> = HashMap::new();
            for (line, category, _) in &issues {
                by_type.entry(category.clone()).or_default().push(*line);
            }

            let severity = if is_used {
                Severity::High // Test code that's actually imported by production
            } else if by_type.contains_key("mock") || by_type.contains_key("assertion") {
                Severity::Medium
            } else {
                Severity::Low
            };

            // Build notes
            let mut notes = Vec::new();
            for (category, lines) in &by_type {
                let desc = match category.as_str() {
                    "mock" => "Mock/stub objects",
                    "assertion" => "Test assertions",
                    "fixture" => "Test fixtures",
                    "framework" => "Test framework code",
                    "spy" => "Spy functions",
                    "debug" => "Debug flags",
                    _ => "Test code",
                };
                notes.push(format!(
                    "• {} (line{})",
                    desc,
                    if lines.len() > 1 {
                        format!(
                            "s {}",
                            lines
                                .iter()
                                .map(|l| l.to_string())
                                .collect::<Vec<_>>()
                                .join(", ")
                        )
                    } else {
                        format!(" {}", lines[0])
                    }
                ));
            }
            if is_used {
                notes.push("⚠️ This file is imported by production code!".to_string());
            }

            let context_notes = format!("\n\n**Found:**\n{}", notes.join("\n"));

            let first_line = issues.first().map(|(l, _, _)| *l).unwrap_or(1);

            findings.push(Finding {
                id: String::new(),
                detector: "TestInProductionDetector".to_string(),
                severity,
                title: format!("Test code in production: {} issue{}", 
                    issues.len(),
                    if issues.len() > 1 { "s" } else { "" }
                ),
                description: format!(
                    "Test utilities and patterns found in what appears to be production code.{}",
                    context_notes
                ),
                affected_files: vec![file_path.clone()],
                line_start: Some(first_line),
                line_end: Some(issues.last().map(|(l, _, _)| *l).unwrap_or(first_line)),
                suggested_fix: Some(
                    "Options:\n\n\
                     1. **Move to test files:** If this is test code, move it to a test directory\n\
                     2. **Use TYPE_CHECKING:** For type hints only, wrap imports in `if TYPE_CHECKING:`\n\
                     3. **Environment check:** If needed at runtime, guard with environment variable\n\n\
                     ```python\n\
                     # Instead of:\n\
                     from unittest.mock import Mock\n\
                     \n\
                     # Use:\n\
                     from typing import TYPE_CHECKING\n\
                     if TYPE_CHECKING:\n\
                         from unittest.mock import Mock\n\
                     ```".to_string()
                ),
                estimated_effort: Some("15 minutes".to_string()),
                category: Some("code-quality".to_string()),
                cwe_id: Some("CWE-489".to_string()),  // Active Debug Code
                why_it_matters: Some(if is_used {
                    "This test code is imported by production code. Test dependencies add bloat, \
                     security risks, and can cause unexpected behavior in production.".to_string()
                } else {
                    "Test code in production files adds confusion and potential bloat. \
                     It may be accidentally executed or cause import errors in environments \
                     without test dependencies.".to_string()
                }),
                ..Default::default()
            });
        }

        info!(
            "TestInProductionDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_mock_in_production_code() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("service.py");
        std::fs::write(
            &file,
            r#"
from unittest.mock import Mock

def get_client():
    client = Mock()
    return client
"#,
        )
        .unwrap();

        let store = GraphStore::in_memory();
        let detector = TestInProductionDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(
            !findings.is_empty(),
            "Should detect test code (Mock) in production file"
        );
        assert!(findings.iter().any(|f| f.detector == "TestInProductionDetector"));
    }

    #[test]
    fn test_no_finding_for_clean_production_code() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("service.py");
        std::fs::write(
            &file,
            r#"
import os

def get_config():
    return os.environ.get("APP_CONFIG", "default")

def process_data(data):
    return [item.strip() for item in data]
"#,
        )
        .unwrap();

        let store = GraphStore::in_memory();
        let detector = TestInProductionDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag clean production code, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
