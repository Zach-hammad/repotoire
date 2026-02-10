//! NoSQL Injection Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static NOSQL_PATTERN: OnceLock<Regex> = OnceLock::new();

fn nosql_pattern() -> &'static Regex {
    NOSQL_PATTERN.get_or_init(|| Regex::new(r"(?i)(\.find\(|\.findOne\(|\.update\(|\.delete\(|\.aggregate\(|\$where|\$regex|\$ne|\$gt|\$lt)").unwrap())
}

pub struct NosqlInjectionDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl NosqlInjectionDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for NosqlInjectionDetector {
    fn name(&self) -> &'static str { "nosql-injection" }
    fn description(&self) -> &'static str { "Detects NoSQL injection risks" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "js"|"ts"|"py"|"rb"|"php") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    if nosql_pattern().is_match(line) {
                        let has_user_input = line.contains("req.") || line.contains("body") || 
                            line.contains("params") || line.contains("query");
                        if has_user_input {
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "NosqlInjectionDetector".to_string(),
                                severity: Severity::High,
                                title: "Potential NoSQL injection".to_string(),
                                description: "MongoDB query with user-controlled input.".to_string(),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some("Validate/sanitize input, avoid $where.".to_string()),
                                estimated_effort: Some("30 minutes".to_string()),
                                category: Some("security".to_string()),
                                cwe_id: Some("CWE-943".to_string()),
                                why_it_matters: Some("Attackers can bypass auth or dump data.".to_string()),
                            });
                        }
                    }
                }
            }
        }
        Ok(findings)
    }
}
