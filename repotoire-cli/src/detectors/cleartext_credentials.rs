//! Cleartext Credentials Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static LOG_PATTERN: OnceLock<Regex> = OnceLock::new();

fn log_pattern() -> &'static Regex {
    // Match logging statements that include actual credential variable names
    // Use word boundaries and avoid partial matches like "tokenizer" matching "token"
    LOG_PATTERN.get_or_init(|| Regex::new(r"(?i)(log|print|console|logger|debug|info|warn|error)\s*[\.(]\s*[^)]*\b(password|passwd|secret|api_key|apikey|auth_token|access_token|private_key|credentials?)\b(?!_path|_file|_dir|izer)").unwrap())
}

pub struct CleartextCredentialsDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl CleartextCredentialsDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for CleartextCredentialsDetector {
    fn name(&self) -> &'static str { "cleartext-credentials" }
    fn description(&self) -> &'static str { "Detects credentials in logs" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"java"|"go"|"rb"|"php"|"cs") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    if log_pattern().is_match(line) {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "CleartextCredentialsDetector".to_string(),
                            severity: Severity::High,
                            title: "Credentials may be logged in cleartext".to_string(),
                            description: "Sensitive data appears in logging statement.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("Remove sensitive data from logs or mask it.".to_string()),
                            estimated_effort: Some("10 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-312".to_string()),
                            why_it_matters: Some("Credentials in logs can be stolen.".to_string()),
                        });
                    }
                }
            }
        }
        Ok(findings)
    }
}
