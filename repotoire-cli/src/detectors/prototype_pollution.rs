//! Prototype Pollution Detector (JavaScript)

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;

static POLLUTION_PATTERN: OnceLock<Regex> = OnceLock::new();

fn pollution_pattern() -> &'static Regex {
    POLLUTION_PATTERN.get_or_init(|| Regex::new(r"(__proto__|prototype\s*\[|Object\.assign\(|\.extend\(|lodash\.merge|_\.merge|deepmerge)").unwrap())
}

pub struct PrototypePollutionDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl PrototypePollutionDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for PrototypePollutionDetector {
    fn name(&self) -> &'static str { "prototype-pollution" }
    fn description(&self) -> &'static str { "Detects prototype pollution risks" }

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
                    if pollution_pattern().is_match(line) {
                        let has_user_input = line.contains("req.") || line.contains("body") || line.contains("params");
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "PrototypePollutionDetector".to_string(),
                            severity: if has_user_input { Severity::High } else { Severity::Medium },
                            title: "Potential prototype pollution".to_string(),
                            description: "Object merge/extend with possible __proto__ injection.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("Use Object.create(null) or validate keys.".to_string()),
                            estimated_effort: Some("20 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-1321".to_string()),
                            why_it_matters: Some("Can lead to RCE or DoS.".to_string()),
                        });
                    }
                }
            }
        }
        Ok(findings)
    }
}
