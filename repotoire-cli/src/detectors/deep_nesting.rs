//! Deep Nesting Detector

use crate::detectors::base::{Detector, DetectorConfig};
use uuid::Uuid;
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::path::{Path, PathBuf};

pub struct DeepNestingDetector {
    repository_path: PathBuf,
    max_findings: usize,
    threshold: usize,
}

impl DeepNestingDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 100, threshold: 4 }
    }
}

impl Detector for DeepNestingDetector {
    fn name(&self) -> &'static str { "deep-nesting" }
    fn description(&self) -> &'static str { "Detects excessive nesting depth" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"jsx"|"tsx"|"rs"|"go"|"java"|"cs"|"cpp"|"c") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let mut max_depth = 0;
                let mut current_depth = 0;
                let mut max_line = 0;

                for (i, line) in content.lines().enumerate() {
                    for ch in line.chars() {
                        if ch == '{' { current_depth += 1; if current_depth > max_depth { max_depth = current_depth; max_line = i + 1; } }
                        else if ch == '}' && current_depth > 0 { current_depth -= 1; }
                    }
                }

                if max_depth > self.threshold {
                    findings.push(Finding {
                        id: Uuid::new_v4().to_string(),
                        detector: "DeepNestingDetector".to_string(),
                        severity: if max_depth > 8 { Severity::High } else { Severity::Medium },
                        title: format!("Excessive nesting: {} levels", max_depth),
                        description: format!("File has {} levels of nesting (threshold: {})", max_depth, self.threshold),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(max_line as u32),
                        line_end: Some(max_line as u32),
                        suggested_fix: Some("Extract nested logic into functions or use early returns.".to_string()),
                        estimated_effort: Some("30 minutes".to_string()),
                        category: Some("complexity".to_string()),
                        cwe_id: None,
                        why_it_matters: Some("Deep nesting makes code hard to read and maintain.".to_string()),
                        ..Default::default()
                    });
                }
            }
        }
        Ok(findings)
    }
}
