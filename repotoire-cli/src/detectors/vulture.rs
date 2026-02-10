//! Vulture-based unused code detector
//!
//! Uses vulture for detecting dead Python code (unused functions, classes, variables).

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::external_tool::{get_graph_context, run_external_tool};
use crate::graph::GraphClient;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use tracing::{debug, info, warn};
use uuid::Uuid;

/// Vulture dead code detector
pub struct VultureDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
    min_confidence: u32,
    exclude_patterns: Vec<String>,
}

/// Compiled regex for parsing vulture output
static VULTURE_PATTERN: OnceLock<Regex> = OnceLock::new();

fn get_vulture_pattern() -> &'static Regex {
    VULTURE_PATTERN.get_or_init(|| {
        Regex::new(r"^(.+):(\d+):\s+unused\s+(\w+)\s+'([^']+)'\s+\((\d+)%\s+confidence\)").unwrap()
    })
}

impl VultureDetector {
    /// Create a new Vulture detector
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            config: DetectorConfig::default(),
            repository_path: repository_path.into(),
            max_findings: 100,
            min_confidence: 80,
            exclude_patterns: vec![
                "tests/".to_string(),
                "test_*.py".to_string(),
                "*_test.py".to_string(),
                "migrations/".to_string(),
                "scripts/".to_string(),
                "setup.py".to_string(),
                "conftest.py".to_string(),
            ],
        }
    }

    /// Set maximum findings
    pub fn with_max_findings(mut self, max: usize) -> Self {
        self.max_findings = max;
        self
    }

    /// Set minimum confidence level (0-100)
    pub fn with_min_confidence(mut self, confidence: u32) -> Self {
        self.min_confidence = confidence;
        self
    }

    /// Set exclude patterns
    pub fn with_exclude_patterns(mut self, patterns: Vec<String>) -> Self {
        self.exclude_patterns = patterns;
        self
    }

    /// Run vulture and parse results
    fn run_vulture(&self) -> Vec<VultureResult> {
        let mut cmd = vec![
            "vulture".to_string(),
            self.repository_path.to_string_lossy().to_string(),
            format!("--min-confidence={}", self.min_confidence),
        ];

        for pattern in &self.exclude_patterns {
            cmd.push("--exclude".to_string());
            cmd.push(pattern.clone());
        }

        let result = run_external_tool(&cmd, "vulture", 60, Some(&self.repository_path), None);

        if result.timed_out {
            warn!("Vulture timed out");
            return Vec::new();
        }

        let pattern = get_vulture_pattern();

        result
            .stdout
            .lines()
            .filter_map(|line| {
                let caps = pattern.captures(line)?;
                Some(VultureResult {
                    file: caps.get(1)?.as_str().to_string(),
                    line: caps.get(2)?.as_str().parse().ok()?,
                    item_type: caps.get(3)?.as_str().to_string(),
                    name: caps.get(4)?.as_str().to_string(),
                    confidence: caps.get(5)?.as_str().parse().ok()?,
                })
            })
            .filter(|r| !self.should_filter(r))
            .collect()
    }

    /// Check if result should be filtered (likely false positive)
    fn should_filter(&self, result: &VultureResult) -> bool {
        // Always keep high-confidence findings
        if result.confidence >= 95 {
            return false;
        }

        let name = &result.name;
        let item_type = &result.item_type;

        // Filter pytest fixtures
        if name.starts_with("fixture_") || name.ends_with("_fixture") {
            return true;
        }

        // Filter common framework callbacks
        let callback_patterns = [
            "on_", "handle_", "_handler", "_callback",
            "setUp", "tearDown", "setUpClass", "tearDownClass",
        ];
        for pattern in callback_patterns {
            if name.contains(pattern) {
                return true;
            }
        }

        // Filter factory/builder methods
        if item_type == "function" || item_type == "method" {
            let factory_patterns = ["factory", "create_", "build_", "make_", "get_handler", "dispatch"];
            for pattern in factory_patterns {
                if name.to_lowercase().contains(pattern) {
                    return true;
                }
            }
        }

        false
    }

    /// Map confidence and type to severity
    fn map_severity(confidence: u32, item_type: &str, ctx_complexity: i64) -> Severity {
        if confidence >= 95 {
            // Very high confidence
            if matches!(item_type, "function" | "class" | "method") {
                if ctx_complexity >= 5 {
                    Severity::High
                } else {
                    Severity::Medium
                }
            } else {
                Severity::Medium
            }
        } else if confidence >= 80 {
            if matches!(item_type, "function" | "class" | "method") && ctx_complexity >= 10 {
                Severity::Medium
            } else {
                Severity::Low
            }
        } else {
            Severity::Info
        }
    }

    /// Create finding from vulture result
    fn create_finding(&self, result: &VultureResult, graph: &GraphClient) -> Finding {
        let rel_path = Path::new(&result.file)
            .strip_prefix(&self.repository_path)
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_else(|_| result.file.clone());

        let ctx = get_graph_context(graph, &rel_path, Some(result.line));
        let severity = Self::map_severity(result.confidence, &result.item_type, ctx.max_complexity());

        let mut description = format!(
            "Unused {} '{}' detected by vulture.\n\n\
             **Confidence**: {}%\n",
            result.item_type,
            result.name,
            result.confidence
        );

        if let Some(loc) = ctx.file_loc {
            description.push_str(&format!("**File Size**: {} LOC\n", loc));
        }

        let impact = if matches!(result.item_type.as_str(), "function" | "class" | "method") {
            "\n**Impact**: Removing this would reduce code complexity and maintenance burden.\n"
        } else {
            "\n**Impact**: Dead code increases cognitive load and may confuse developers.\n"
        };
        description.push_str(impact);

        let suggested_fix = if result.confidence >= 95 {
            if matches!(result.item_type.as_str(), "function" | "class" | "method") {
                format!("Safe to remove: Delete unused {} '{}' and run tests to confirm", result.item_type, result.name)
            } else {
                format!("Remove unused {} '{}'", result.item_type, result.name)
            }
        } else if result.confidence >= 80 {
            format!("Investigate and remove if truly unused: Check for dynamic usage of '{}'", result.name)
        } else {
            "Review usage patterns: May be used dynamically or in external modules".to_string()
        };

        let effort = if result.confidence >= 95 {
            if matches!(result.item_type.as_str(), "function" | "class") {
                "Small (15-30 minutes)"
            } else {
                "Tiny (5 minutes)"
            }
        } else if result.confidence >= 80 {
            "Small (30 minutes - 1 hour)"
        } else {
            "Medium (1-2 hours for investigation)"
        };

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "VultureDetector".to_string(),
            severity,
            title: format!("Unused {}: {}", result.item_type, result.name),
            description,
            affected_files: vec![PathBuf::from(&rel_path)],
            line_start: Some(result.line),
            line_end: Some(result.line),
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some(effort.to_string()),
            category: Some(Self::get_category_tag(&result.item_type)),
            cwe_id: None,
            why_it_matters: Some(
                "Dead code adds maintenance burden and can mislead developers about actual code paths.".to_string()
            ),
        }
    }

    fn get_category_tag(item_type: &str) -> String {
        match item_type {
            "function" | "method" => "unused_function".to_string(),
            "class" => "unused_class".to_string(),
            "variable" | "attribute" => "unused_variable".to_string(),
            "import" => "unused_import".to_string(),
            "property" => "unused_property".to_string(),
            _ => "unused_other".to_string(),
        }
    }
}

/// Vulture result
struct VultureResult {
    file: String,
    line: u32,
    item_type: String,
    name: String,
    confidence: u32,
}

impl Detector for VultureDetector {
    fn name(&self) -> &'static str {
        "VultureDetector"
    }

    fn description(&self) -> &'static str {
        "Detects unused Python code (dead code) using vulture"
    }

    fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        info!("Running Vulture on {:?}", self.repository_path);

        let results = self.run_vulture();

        if results.is_empty() {
            info!("No unused code found");
            return Ok(Vec::new());
        }

        let findings: Vec<Finding> = results
            .iter()
            .take(self.max_findings)
            .map(|r| self.create_finding(r, graph))
            .collect();

        info!("Created {} unused code findings", findings.len());
        Ok(findings)
    }

    fn category(&self) -> &'static str {
        "unused_code"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_regex_parsing() {
        let pattern = get_vulture_pattern();
        let line = "src/main.py:42: unused function 'my_func' (90% confidence)";
        let caps = pattern.captures(line).unwrap();
        assert_eq!(caps.get(1).unwrap().as_str(), "src/main.py");
        assert_eq!(caps.get(2).unwrap().as_str(), "42");
        assert_eq!(caps.get(3).unwrap().as_str(), "function");
        assert_eq!(caps.get(4).unwrap().as_str(), "my_func");
        assert_eq!(caps.get(5).unwrap().as_str(), "90");
    }

    #[test]
    fn test_severity_mapping() {
        assert_eq!(VultureDetector::map_severity(95, "function", 10), Severity::High);
        assert_eq!(VultureDetector::map_severity(95, "variable", 0), Severity::Medium);
        assert_eq!(VultureDetector::map_severity(80, "function", 5), Severity::Low);
        assert_eq!(VultureDetector::map_severity(70, "function", 0), Severity::Info);
    }
}
