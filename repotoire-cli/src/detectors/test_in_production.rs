//! Test Code in Production Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static TEST_CODE: OnceLock<Regex> = OnceLock::new();

fn test_code() -> &'static Regex {
    TEST_CODE.get_or_init(|| Regex::new(r"(?i)(mock\.|stub\.|fake\.|spy\.|jest\.|sinon\.|@pytest|@test|unittest\.|describe\(|it\(|expect\(|assert\.|fixture)").unwrap())
}

pub struct TestInProductionDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl TestInProductionDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for TestInProductionDetector {
    fn name(&self) -> &'static str { "test-in-production" }
    fn description(&self) -> &'static str { "Detects test code in production files" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            // Skip actual test files
            let path_str = path.to_string_lossy();
            if path_str.contains("test") || path_str.contains("spec") || 
               path_str.contains("__tests__") || path_str.contains("fixtures") {
                continue;
            }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"jsx"|"tsx"|"java"|"rb") { continue; }

            if let Ok(content) = std::fs::read_to_string(path) {
                for (i, line) in content.lines().enumerate() {
                    if test_code().is_match(line) {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "TestInProductionDetector".to_string(),
                            severity: Severity::Medium,
                            title: "Test code in production file".to_string(),
                            description: "Test utilities/patterns found in non-test file.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("Move test code to test files.".to_string()),
                            estimated_effort: Some("15 minutes".to_string()),
                            category: Some("code-quality".to_string()),
                            cwe_id: None,
                            why_it_matters: Some("Test code shouldn't ship to production.".to_string()),
                        });
                        break; // One finding per file is enough
                    }
                }
            }
        }
        Ok(findings)
    }
}
