//! Hardcoded Timeout Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static TIMEOUT_PATTERN: OnceLock<Regex> = OnceLock::new();

fn timeout_pattern() -> &'static Regex {
    TIMEOUT_PATTERN.get_or_init(|| Regex::new(r"(?i)(timeout|sleep|delay|wait|setTimeout|setInterval)\s*[\(=:]\s*(\d{4,})").unwrap())
}

pub struct HardcodedTimeoutDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl HardcodedTimeoutDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for HardcodedTimeoutDetector {
    fn name(&self) -> &'static str { "hardcoded-timeout" }
    fn description(&self) -> &'static str { "Detects hardcoded timeout values" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            // Skip test files
            let path_str = path.to_string_lossy();
            if path_str.contains("test") { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"java"|"go"|"rs"|"rb") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    if let Some(caps) = timeout_pattern().captures(line) {
                        if let Some(val) = caps.get(2) {
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "HardcodedTimeoutDetector".to_string(),
                                severity: Severity::Low,
                                title: format!("Hardcoded timeout: {}ms", val.as_str()),
                                description: "Magic timeout values are hard to tune.".to_string(),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some("Extract to a named constant or config.".to_string()),
                                estimated_effort: Some("5 minutes".to_string()),
                                category: Some("maintainability".to_string()),
                                cwe_id: None,
                                why_it_matters: Some("Hard to find and adjust timeouts.".to_string()),
                            });
                        }
                    }
                }
            }
        }
        Ok(findings)
    }
}
