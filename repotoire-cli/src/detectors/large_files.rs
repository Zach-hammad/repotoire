//! Large Files Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;

pub struct LargeFilesDetector {
    repository_path: PathBuf,
    max_findings: usize,
    threshold: usize,
}

impl LargeFilesDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50, threshold: 500 }
    }
}

impl Detector for LargeFilesDetector {
    fn name(&self) -> &'static str { "large-files" }
    fn description(&self) -> &'static str { "Detects files exceeding size threshold" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"jsx"|"tsx"|"rs"|"go"|"java"|"cs"|"cpp"|"c"|"h"|"rb"|"php") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines = content.lines().count();
                if lines > self.threshold {
                    findings.push(Finding {
                        id: Uuid::new_v4().to_string(),
                        detector: "LargeFilesDetector".to_string(),
                        severity: if lines > 2000 { Severity::High } else if lines > 1000 { Severity::Medium } else { Severity::Low },
                        title: format!("Large file: {} lines", lines),
                        description: format!("File has {} lines (threshold: {})", lines, self.threshold),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(1),
                        line_end: Some(lines as u32),
                        suggested_fix: Some("Split into smaller, focused modules.".to_string()),
                        estimated_effort: Some("60 minutes".to_string()),
                        category: Some("maintainability".to_string()),
                        cwe_id: None,
                        why_it_matters: Some("Large files are hard to understand and maintain.".to_string()),
                    });
                }
            }
        }
        Ok(findings)
    }
}
