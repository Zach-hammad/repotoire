//! Inconsistent Returns Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use uuid::Uuid;

pub struct InconsistentReturnsDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl InconsistentReturnsDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for InconsistentReturnsDetector {
    fn name(&self) -> &'static str { "inconsistent-returns" }
    fn description(&self) -> &'static str { "Detects functions with inconsistent return paths" }

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        for func in graph.get_functions() {
            if findings.len() >= self.max_findings { break; }
            
            if let Ok(content) = std::fs::read_to_string(&func.file_path) {
                let start = func.line_start.saturating_sub(1) as usize;
                let end = func.line_end as usize;
                let func_lines: Vec<&str> = content.lines().skip(start).take(end - start).collect();
                let func_text = func_lines.join("\n");
                
                let has_return_value = func_text.contains("return ") && !func_text.contains("return;") && !func_text.contains("return\n");
                let has_return_none = func_text.contains("return;") || func_text.contains("return\n") || func_text.contains("return None");
                let has_implicit_return = !func_text.contains("return");
                
                // Check for mix of return styles
                if has_return_value && (has_return_none || has_implicit_return) {
                    findings.push(Finding {
                        id: Uuid::new_v4().to_string(),
                        detector: "InconsistentReturnsDetector".to_string(),
                        severity: Severity::Medium,
                        title: format!("Inconsistent returns in '{}'", func.name),
                        description: "Some paths return a value, others don't.".to_string(),
                        affected_files: vec![PathBuf::from(&func.file_path)],
                        line_start: Some(func.line_start),
                        line_end: Some(func.line_end),
                        suggested_fix: Some("Ensure all paths return consistently.".to_string()),
                        estimated_effort: Some("15 minutes".to_string()),
                        category: Some("bug-risk".to_string()),
                        cwe_id: None,
                        why_it_matters: Some("Can cause unexpected None/undefined.".to_string()),
                    });
                }
            }
        }
        Ok(findings)
    }
}
