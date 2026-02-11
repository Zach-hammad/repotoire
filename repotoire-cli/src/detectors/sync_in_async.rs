//! Sync in Async Detector

use crate::detectors::base::{Detector, DetectorConfig};
use uuid::Uuid;
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;

static ASYNC_FUNC: OnceLock<Regex> = OnceLock::new();
static BLOCKING: OnceLock<Regex> = OnceLock::new();

fn async_func() -> &'static Regex {
    ASYNC_FUNC.get_or_init(|| Regex::new(r"(?i)(async\s+def|async\s+function|async\s+fn)").unwrap())
}

fn blocking() -> &'static Regex {
    BLOCKING.get_or_init(|| Regex::new(r"(?i)(time\.sleep|Thread\.sleep|readFileSync|writeFileSync|execSync|requests\.(get|post)|std::thread::sleep)").unwrap())
}

pub struct SyncInAsyncDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl SyncInAsyncDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for SyncInAsyncDetector {
    fn name(&self) -> &'static str { "sync-in-async" }
    fn description(&self) -> &'static str { "Detects blocking calls in async functions" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"jsx"|"tsx"|"rs") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let mut in_async = false;
                for (i, line) in content.lines().enumerate() {
                    if async_func().is_match(line) { in_async = true; }
                    if in_async && (line.trim().is_empty() || line.starts_with("def ") || line.starts_with("fn ") || line.starts_with("function ")) {
                        if !async_func().is_match(line) { in_async = false; }
                    }
                    
                    if in_async && blocking().is_match(line) {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "SyncInAsyncDetector".to_string(),
                            severity: Severity::Medium,
                            title: "Blocking call in async function".to_string(),
                            description: "Synchronous blocking call inside async function will block event loop.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("Use async equivalent (asyncio.sleep, aiohttp, etc).".to_string()),
                            estimated_effort: Some("20 minutes".to_string()),
                            category: Some("performance".to_string()),
                            cwe_id: None,
                            why_it_matters: Some("Blocks event loop, defeating async benefits.".to_string()),
                            ..Default::default()
                        });
                    }
                }
            }
        }
        Ok(findings)
    }
}
