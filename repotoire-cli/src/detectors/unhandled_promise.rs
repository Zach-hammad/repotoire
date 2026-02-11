//! Unhandled Promise Rejection Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;

static PROMISE_PATTERN: OnceLock<Regex> = OnceLock::new();

fn promise_pattern() -> &'static Regex {
    PROMISE_PATTERN.get_or_init(|| Regex::new(r"new Promise|\.then\(").unwrap())
}

pub struct UnhandledPromiseDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl UnhandledPromiseDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for UnhandledPromiseDetector {
    fn name(&self) -> &'static str { "unhandled-promise" }
    fn description(&self) -> &'static str { "Detects promises without error handling" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "js"|"ts"|"jsx"|"tsx") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    if promise_pattern().is_match(line) {
                        // Check if this line or next few have .catch
                        let context = content.lines().skip(i).take(5).collect::<Vec<_>>().join(" ");
                        if !context.contains(".catch") && !context.contains("try") {
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "UnhandledPromiseDetector".to_string(),
                                severity: Severity::Medium,
                                title: "Promise without .catch()".to_string(),
                                description: "Promise rejection may be unhandled.".to_string(),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some("Add .catch() or wrap in try/catch.".to_string()),
                                estimated_effort: Some("5 minutes".to_string()),
                                category: Some("error-handling".to_string()),
                                cwe_id: None,
                                why_it_matters: Some("Unhandled rejections crash Node.js.".to_string()),
                            });
                        }
                    }
                }
            }
        }
        Ok(findings)
    }
}
