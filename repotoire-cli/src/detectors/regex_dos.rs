//! Regex DoS Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static REGEX_CREATE: OnceLock<Regex> = OnceLock::new();
static VULNERABLE: OnceLock<Regex> = OnceLock::new();

fn regex_create() -> &'static Regex {
    REGEX_CREATE.get_or_init(|| Regex::new(r"(?i)(Regex::new|re\.compile|new RegExp|Pattern\.compile)").unwrap())
}

fn vulnerable() -> &'static Regex {
    VULNERABLE.get_or_init(|| Regex::new(r"\([^)]*[+*][^)]*\)[+*]|\.\*\.\*").unwrap())
}

pub struct RegexDosDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl RegexDosDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for RegexDosDetector {
    fn name(&self) -> &'static str { "regex-dos" }
    fn description(&self) -> &'static str { "Detects ReDoS vulnerable patterns" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"java"|"rs"|"go"|"rb"|"php") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    if regex_create().is_match(line) && vulnerable().is_match(line) {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "RegexDosDetector".to_string(),
                            severity: Severity::High,
                            title: "Potential ReDoS vulnerability".to_string(),
                            description: "Regex with nested quantifiers may cause catastrophic backtracking.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("Rewrite regex to avoid nested quantifiers.".to_string()),
                            estimated_effort: Some("30 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-1333".to_string()),
                            why_it_matters: Some("Attackers could cause denial of service.".to_string()),
                        });
                    }
                }
            }
        }
        Ok(findings)
    }
}
