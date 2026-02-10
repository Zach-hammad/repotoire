//! Missing Await Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static ASYNC_CALL: OnceLock<Regex> = OnceLock::new();

fn async_call() -> &'static Regex {
    ASYNC_CALL.get_or_init(|| Regex::new(r"(?i)(fetch\(|axios\.|\.json\(\)|\.text\(\)|async_\w+\(|aio\w+\.)").unwrap())
}

pub struct MissingAwaitDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl MissingAwaitDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for MissingAwaitDetector {
    fn name(&self) -> &'static str { "missing-await" }
    fn description(&self) -> &'static str { "Detects async calls without await" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "js"|"ts"|"jsx"|"tsx"|"py") { continue; }

            if let Ok(content) = std::fs::read_to_string(path) {
                let mut in_async = false;
                
                for (i, line) in content.lines().enumerate() {
                    if line.contains("async ") { in_async = true; }
                    if line.trim().starts_with("}") && in_async { in_async = false; }
                    
                    if in_async && async_call().is_match(line) {
                        if !line.contains("await ") && !line.contains(".then(") && !line.contains("Promise") {
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "MissingAwaitDetector".to_string(),
                                severity: Severity::Medium,
                                title: "Async call without await".to_string(),
                                description: "Async function called without await - returns Promise, not value.".to_string(),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some("Add await before async call.".to_string()),
                                estimated_effort: Some("2 minutes".to_string()),
                                category: Some("bug-risk".to_string()),
                                cwe_id: None,
                                why_it_matters: Some("Will get Promise object instead of result.".to_string()),
                            });
                        }
                    }
                }
            }
        }
        Ok(findings)
    }
}
