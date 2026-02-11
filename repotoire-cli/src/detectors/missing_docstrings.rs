//! Missing Docstrings Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;

pub struct MissingDocstringsDetector {
    repository_path: PathBuf,
    max_findings: usize,
    min_lines: u32,
}

impl MissingDocstringsDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 100, min_lines: 5 }
    }
}

impl Detector for MissingDocstringsDetector {
    fn name(&self) -> &'static str { "missing-docstrings" }
    fn description(&self) -> &'static str { "Detects functions without documentation" }

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        for func in graph.get_functions() {
            if findings.len() >= self.max_findings { break; }
            
            let lines = func.line_end.saturating_sub(func.line_start);
            if lines < self.min_lines { continue; }
            if func.name.starts_with('_') && !func.name.starts_with("__") { continue; }
            if func.name.starts_with("test_") { continue; }

            // Check for docstring
            let file_path = PathBuf::from(&func.file_path);
            if let Ok(content) = std::fs::read_to_string(&file_path) {
                let file_lines: Vec<&str> = content.lines().collect();
                let start = (func.line_start as usize).saturating_sub(1);
                let has_doc = file_lines.get(start..start+5).map(|s| {
                    s.iter().any(|l| l.contains("\"\"\"") || l.contains("///") || l.contains("/**"))
                }).unwrap_or(false);

                if !has_doc {
                    findings.push(Finding {
                        id: Uuid::new_v4().to_string(),
                        detector: "MissingDocstringsDetector".to_string(),
                        severity: Severity::Low,
                        title: format!("Missing docs for '{}'", func.name),
                        description: format!("Function '{}' ({} lines) has no documentation.", func.name, lines),
                        affected_files: vec![file_path.clone()],
                        line_start: Some(func.line_start),
                        line_end: Some(func.line_start),
                        suggested_fix: Some("Add docstring describing purpose and parameters.".to_string()),
                        estimated_effort: Some("10 minutes".to_string()),
                        category: Some("documentation".to_string()),
                        cwe_id: None,
                        why_it_matters: None,
                    });
                }
            }
        }
        Ok(findings)
    }
}
