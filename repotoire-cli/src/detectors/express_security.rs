//! Express Security Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static EXPRESS_APP: OnceLock<Regex> = OnceLock::new();

fn express_app() -> &'static Regex {
    EXPRESS_APP.get_or_init(|| Regex::new(r#"express\(\)|require\(["']express["']\)"#).unwrap())
}

pub struct ExpressSecurityDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl ExpressSecurityDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for ExpressSecurityDetector {
    fn name(&self) -> &'static str { "express-security" }
    fn description(&self) -> &'static str { "Detects Express.js security issues" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "js"|"ts") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                // Check if this is an Express app
                if !express_app().is_match(&content) { continue; }
                
                let has_helmet = content.contains("helmet");
                let has_cors = content.contains("cors(");
                let has_rate_limit = content.contains("rateLimit") || content.contains("rate-limit");
                
                if !has_helmet {
                    findings.push(Finding {
                        id: Uuid::new_v4().to_string(),
                        detector: "ExpressSecurityDetector".to_string(),
                        severity: Severity::Medium,
                        title: "Express app missing helmet".to_string(),
                        description: "Helmet sets security headers.".to_string(),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(1),
                        line_end: Some(1),
                        suggested_fix: Some("npm install helmet && app.use(helmet())".to_string()),
                        estimated_effort: Some("5 minutes".to_string()),
                        category: Some("security".to_string()),
                        cwe_id: Some("CWE-693".to_string()),
                        why_it_matters: Some("Missing security headers.".to_string()),
                    });
                }
                
                if !has_rate_limit {
                    findings.push(Finding {
                        id: Uuid::new_v4().to_string(),
                        detector: "ExpressSecurityDetector".to_string(),
                        severity: Severity::Low,
                        title: "Express app missing rate limiting".to_string(),
                        description: "Rate limiting prevents abuse.".to_string(),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(1),
                        line_end: Some(1),
                        suggested_fix: Some("Add express-rate-limit middleware.".to_string()),
                        estimated_effort: Some("15 minutes".to_string()),
                        category: Some("security".to_string()),
                        cwe_id: Some("CWE-770".to_string()),
                        why_it_matters: Some("Vulnerable to DoS/brute force.".to_string()),
                    });
                }
            }
        }
        Ok(findings)
    }
}
