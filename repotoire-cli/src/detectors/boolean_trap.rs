//! Boolean Trap Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;

static BOOL_ARGS: OnceLock<Regex> = OnceLock::new();

fn bool_args() -> &'static Regex {
    BOOL_ARGS.get_or_init(|| Regex::new(r"\w+\s*\([^)]*\b(true|false|True|False)\s*,\s*(true|false|True|False)").unwrap())
}

pub struct BooleanTrapDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl BooleanTrapDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for BooleanTrapDetector {
    fn name(&self) -> &'static str { "boolean-trap" }
    fn description(&self) -> &'static str { "Detects multiple boolean arguments" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"java"|"go"|"rb"|"cs") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    if bool_args().is_match(line) {
                        let title = "Boolean trap (multiple bool args)".to_string();
                        let line_num = (i + 1) as u32;
                        let file_str = path.to_string_lossy();
                        findings.push(Finding {
                            id: deterministic_finding_id("BooleanTrapDetector", &file_str, line_num, &title),
                            detector: "BooleanTrapDetector".to_string(),
                            severity: Severity::Low,
                            title,
                            description: "foo(true, false) is hard to understand at call site.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some("Use named arguments or an options object.".to_string()),
                            estimated_effort: Some("15 minutes".to_string()),
                            category: Some("readability".to_string()),
                            cwe_id: None,
                            why_it_matters: Some("Confusing API, easy to get wrong.".to_string()),
                            ..Default::default()
                        });
                    }
                }
            }
        }
        Ok(findings)
    }
}
