//! Empty Catch Block Detector
//!
//! Detects empty or minimal catch/except blocks that swallow exceptions.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::{Path, PathBuf};
use uuid::Uuid;

pub struct EmptyCatchDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
}

impl EmptyCatchDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            config: DetectorConfig::default(),
            repository_path: repository_path.into(),
            max_findings: 100,
        }
    }

    fn scan_file(&self, path: &Path, ext: &str) -> Vec<Finding> {
        let mut findings = vec![];
        let content = match std::fs::read_to_string(path) {
            Ok(c) => c,
            Err(_) => return findings,
        };
        let lines: Vec<&str> = content.lines().collect();

        for (i, line) in lines.iter().enumerate() {
            let trimmed = line.trim();

            // Python: except: followed by pass
            if ext == "py" && trimmed.starts_with("except") && trimmed.ends_with(":") {
                if let Some(next) = lines.get(i + 1) {
                    let next_trimmed = next.trim();
                    if next_trimmed == "pass" || next_trimmed == "..." {
                        findings.push(self.create_finding(path, (i + 1) as u32));
                    }
                }
            }

            // JS/TS/Java: catch (...) { }
            if matches!(ext, "js" | "ts" | "jsx" | "tsx" | "java" | "cs") {
                if trimmed.contains("catch") && trimmed.contains("{") && trimmed.contains("}") {
                    // Single line empty catch
                    if trimmed.ends_with("{ }") || trimmed.ends_with("{}") {
                        findings.push(self.create_finding(path, (i + 1) as u32));
                    }
                }
            }
        }
        findings
    }

    fn create_finding(&self, path: &Path, line: u32) -> Finding {
        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "EmptyCatchDetector".to_string(),
            severity: Severity::Medium,
            title: "Empty catch block swallows exceptions".to_string(),
            description: "This catch block silently swallows exceptions. This can hide bugs.".to_string(),
            affected_files: vec![path.to_path_buf()],
            line_start: Some(line),
            line_end: Some(line),
            suggested_fix: Some("Log the exception or handle it appropriately.".to_string()),
            estimated_effort: Some("10 minutes".to_string()),
            category: Some("error-handling".to_string()),
            cwe_id: Some("CWE-390".to_string()),
            why_it_matters: Some("Swallowed exceptions hide bugs and make debugging difficult.".to_string()),
        }
    }
}

impl Detector for EmptyCatchDetector {
    fn name(&self) -> &'static str { "empty-catch-block" }
    fn description(&self) -> &'static str { "Detects empty catch/except blocks" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if matches!(ext, "py" | "js" | "ts" | "jsx" | "tsx" | "java" | "cs") {
                findings.extend(self.scan_file(path, ext));
            }
        }
        Ok(findings)
    }
}
