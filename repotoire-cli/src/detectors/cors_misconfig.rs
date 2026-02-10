//! CORS Misconfiguration Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static CORS_PATTERN: OnceLock<Regex> = OnceLock::new();

fn cors_pattern() -> &'static Regex {
    CORS_PATTERN.get_or_init(|| Regex::new(r#"(?i)(Access-Control-Allow-Origin|cors.*origin|allowedOrigins?)\s*[:=]\s*["'*]?\*"#).unwrap())
}

pub struct CorsMisconfigDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl CorsMisconfigDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for CorsMisconfigDetector {
    fn name(&self) -> &'static str { "cors-misconfig" }
    fn description(&self) -> &'static str { "Detects overly permissive CORS configuration" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"java"|"go"|"rb"|"php"|"yaml"|"yml"|"json"|"conf") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    if cors_pattern().is_match(line) {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "CorsMisconfigDetector".to_string(),
                            severity: Severity::Medium,
                            title: "Overly permissive CORS (*)".to_string(),
                            description: "Access-Control-Allow-Origin: * allows any site to make requests.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("Specify allowed origins explicitly.".to_string()),
                            estimated_effort: Some("15 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-942".to_string()),
                            why_it_matters: Some("Allows CSRF and data theft from any origin.".to_string()),
                        });
                    }
                }
            }
        }
        Ok(findings)
    }
}
