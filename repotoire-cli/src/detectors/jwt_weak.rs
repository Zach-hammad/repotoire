//! JWT Weak Algorithm Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static JWT_PATTERN: OnceLock<Regex> = OnceLock::new();

fn jwt_pattern() -> &'static Regex {
    JWT_PATTERN.get_or_init(|| Regex::new(r#"(?i)(algorithm\s*[=:]\s*["']?(none|HS256)["']?|jwt\.(encode|decode|sign)|JWTAuth|jsonwebtoken)"#).unwrap())
}

pub struct JwtWeakDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl JwtWeakDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for JwtWeakDetector {
    fn name(&self) -> &'static str { "jwt-weak" }
    fn description(&self) -> &'static str { "Detects weak JWT algorithms" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"java"|"go"|"rb"|"php") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    let lower = line.to_lowercase();
                    if lower.contains("algorithm") && (lower.contains("none") || (lower.contains("hs256") && !lower.contains("rs256"))) {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "JwtWeakDetector".to_string(),
                            severity: Severity::Critical,
                            title: "Weak JWT algorithm".to_string(),
                            description: "JWT with 'none' or weak HS256 algorithm.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("Use RS256 or ES256 with proper key management.".to_string()),
                            estimated_effort: Some("30 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-327".to_string()),
                            why_it_matters: Some("Weak JWT allows token forgery.".to_string()),
                        });
                    }
                }
            }
        }
        Ok(findings)
    }
}
