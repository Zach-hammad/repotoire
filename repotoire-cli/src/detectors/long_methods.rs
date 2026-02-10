//! Long Methods Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use uuid::Uuid;

pub struct LongMethodsDetector {
    repository_path: PathBuf,
    max_findings: usize,
    threshold: u32,
}

impl LongMethodsDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 100, threshold: 50 }
    }
}

impl Detector for LongMethodsDetector {
    fn name(&self) -> &'static str { "long-methods" }
    fn description(&self) -> &'static str { "Detects methods/functions over 50 lines" }

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        for func in graph.get_functions() {
            if findings.len() >= self.max_findings { break; }
            
            let lines = func.line_end.saturating_sub(func.line_start);
            if lines > self.threshold {
                let severity = if lines > 200 { Severity::High } else if lines > 100 { Severity::Medium } else { Severity::Low };
                
                findings.push(Finding {
                    id: Uuid::new_v4().to_string(),
                    detector: "LongMethodsDetector".to_string(),
                    severity,
                    title: format!("Long method: {} ({} lines)", func.name, lines),
                    description: format!("Function '{}' has {} lines (threshold: {}).", func.name, lines, self.threshold),
                    affected_files: vec![PathBuf::from(&func.file_path)],
                    line_start: Some(func.line_start),
                    line_end: Some(func.line_end),
                    suggested_fix: Some("Break into smaller, focused functions.".to_string()),
                    estimated_effort: Some("30 minutes".to_string()),
                    category: Some("maintainability".to_string()),
                    cwe_id: None,
                    why_it_matters: Some("Long methods are hard to understand and test.".to_string()),
                });
            }
        }
        Ok(findings)
    }
}
