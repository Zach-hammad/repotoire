//! Insecure Cookie Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static COOKIE_PATTERN: OnceLock<Regex> = OnceLock::new();

fn cookie_pattern() -> &'static Regex {
    COOKIE_PATTERN.get_or_init(|| Regex::new(r"(?i)(set.cookie|cookie\s*=|res\.cookie|response\.set_cookie|setcookie)").unwrap())
}

pub struct InsecureCookieDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl InsecureCookieDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for InsecureCookieDetector {
    fn name(&self) -> &'static str { "insecure-cookie" }
    fn description(&self) -> &'static str { "Detects cookies without security flags" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"php"|"rb"|"java") { continue; }

            if let Ok(content) = std::fs::read_to_string(path) {
                for (i, line) in content.lines().enumerate() {
                    if cookie_pattern().is_match(line) {
                        let has_httponly = content.lines().skip(i).take(3).any(|l| l.to_lowercase().contains("httponly"));
                        let has_secure = content.lines().skip(i).take(3).any(|l| l.to_lowercase().contains("secure"));
                        
                        if !has_httponly || !has_secure {
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "InsecureCookieDetector".to_string(),
                                severity: Severity::Medium,
                                title: format!("Cookie missing {} flag", if !has_httponly { "HttpOnly" } else { "Secure" }),
                                description: "Cookies should have HttpOnly and Secure flags.".to_string(),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some("Add httponly=True and secure=True.".to_string()),
                                estimated_effort: Some("5 minutes".to_string()),
                                category: Some("security".to_string()),
                                cwe_id: Some("CWE-614".to_string()),
                                why_it_matters: Some("XSS can steal cookies without HttpOnly.".to_string()),
                            });
                        }
                    }
                }
            }
        }
        Ok(findings)
    }
}
