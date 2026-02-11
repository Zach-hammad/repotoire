//! Broad Exception Detector

use crate::detectors::base::{Detector, DetectorConfig};
use uuid::Uuid;
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;

static BROAD_EXCEPT: OnceLock<Regex> = OnceLock::new();

fn broad_except() -> &'static Regex {
    BROAD_EXCEPT.get_or_init(|| Regex::new(r"(?i)(except\s*:|catch\s*\(\s*(Exception|Error|Throwable|BaseException|\w)\s*\)|catch\s*\{)").unwrap())
}

pub struct BroadExceptionDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl BroadExceptionDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for BroadExceptionDetector {
    fn name(&self) -> &'static str { "broad-exception" }
    fn description(&self) -> &'static str { "Detects overly broad exception catching" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"java"|"cs"|"rb"|"go") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    if broad_except().is_match(line) {
                        // Skip if it's re-raising
                        let next_lines = content.lines().skip(i+1).take(3).collect::<Vec<_>>().join(" ");
                        if next_lines.contains("raise") || next_lines.contains("throw") { continue; }
                        
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "BroadExceptionDetector".to_string(),
                            severity: Severity::Low,
                            title: "Broad exception catch".to_string(),
                            description: "Catching generic Exception hides bugs.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("Catch specific exceptions.".to_string()),
                            estimated_effort: Some("10 minutes".to_string()),
                            category: Some("error-handling".to_string()),
                            cwe_id: None,
                            why_it_matters: Some("Masks unexpected errors.".to_string()),
                            ..Default::default()
                        });
                    }
                }
            }
        }
        Ok(findings)
    }
}
