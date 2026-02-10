//! Mutable Default Arguments Detector (Python)

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static MUTABLE_DEFAULT: OnceLock<Regex> = OnceLock::new();

fn mutable_default() -> &'static Regex {
    MUTABLE_DEFAULT.get_or_init(|| Regex::new(r"def\s+\w+\s*\([^)]*\w+\s*=\s*(\[\]|\{\}|set\(\)|list\(\)|dict\(\))").unwrap())
}

pub struct MutableDefaultArgsDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl MutableDefaultArgsDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for MutableDefaultArgsDetector {
    fn name(&self) -> &'static str { "mutable-default-args" }
    fn description(&self) -> &'static str { "Detects mutable default arguments in Python" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if ext != "py" { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    if mutable_default().is_match(line) {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "MutableDefaultArgsDetector".to_string(),
                            severity: Severity::Medium,
                            title: "Mutable default argument".to_string(),
                            description: "Mutable defaults are shared between calls - a common Python gotcha.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("Use None as default and create mutable in function body.".to_string()),
                            estimated_effort: Some("5 minutes".to_string()),
                            category: Some("bug-risk".to_string()),
                            cwe_id: None,
                            why_it_matters: Some("Causes surprising shared state bugs.".to_string()),
                        });
                    }
                }
            }
        }
        Ok(findings)
    }
}
