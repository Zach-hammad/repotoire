//! Hardcoded IPs Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static IP_PATTERN: OnceLock<Regex> = OnceLock::new();

fn ip_pattern() -> &'static Regex {
    IP_PATTERN.get_or_init(|| Regex::new(r#"["']?(127\.0\.0\.1|0\.0\.0\.0|localhost|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|192\.168\.\d+\.\d+)["']?"#).unwrap())
}

pub struct HardcodedIpsDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl HardcodedIpsDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for HardcodedIpsDetector {
    fn name(&self) -> &'static str { "hardcoded-ips" }
    fn description(&self) -> &'static str { "Detects hardcoded IPs and localhost" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            // Skip config files where this is expected
            let fname = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
            if fname.contains("config") || fname.contains("test") || fname.contains(".env") { continue; }
            
            // Skip detector files (they contain patterns, not actual usage)
            if fname.contains("detector") || fname.contains("scanner") { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"java"|"go"|"rs"|"rb"|"php"|"cs") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    let trimmed = line.trim();
                    if trimmed.starts_with("//") || trimmed.starts_with("#") { continue; }
                    
                    // Skip lines with local development services and common dev ports
                    let lower = line.to_lowercase();
                    if lower.contains("ollama") || lower.contains("local") || 
                       lower.contains("dev") || lower.contains("default") ||
                       lower.contains(":11434") || lower.contains(":3000") ||
                       lower.contains(":8080") || lower.contains(":5000") { continue; }

                    if let Some(m) = ip_pattern().find(line) {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "HardcodedIpsDetector".to_string(),
                            severity: Severity::Medium,
                            title: format!("Hardcoded IP: {}", m.as_str()),
                            description: "Hardcoded IPs make deployment inflexible.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("Use environment variables or config files.".to_string()),
                            estimated_effort: Some("10 minutes".to_string()),
                            category: Some("configuration".to_string()),
                            cwe_id: None,
                            why_it_matters: Some("Hardcoded IPs break in different environments.".to_string()),
                        });
                    }
                }
            }
        }
        Ok(findings)
    }
}
