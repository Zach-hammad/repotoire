//! Insecure Random Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static INSECURE_RANDOM: OnceLock<Regex> = OnceLock::new();

fn insecure_random() -> &'static Regex {
    INSECURE_RANDOM.get_or_init(|| Regex::new(r"(?i)(Math\.random|random\.random|rand\(\)|srand|mt_rand|\brand\b)").unwrap())
}

pub struct InsecureRandomDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl InsecureRandomDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for InsecureRandomDetector {
    fn name(&self) -> &'static str { "insecure-random" }
    fn description(&self) -> &'static str { "Detects insecure random for security purposes" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"java"|"go"|"rb"|"php"|"c"|"cpp") { continue; }

            if let Ok(content) = std::fs::read_to_string(path) {
                for (i, line) in content.lines().enumerate() {
                    if insecure_random().is_match(line) {
                        // Check if used in security context
                        let security_context = line.contains("token") || line.contains("secret") ||
                            line.contains("password") || line.contains("key") || line.contains("salt") ||
                            line.contains("session") || line.contains("auth") || line.contains("crypto");
                        
                        if security_context {
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "InsecureRandomDetector".to_string(),
                                severity: Severity::High,
                                title: "Insecure random in security context".to_string(),
                                description: "Math.random/random.random are predictable and not suitable for security.".to_string(),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some("Use crypto.getRandomValues, secrets module, or /dev/urandom.".to_string()),
                                estimated_effort: Some("15 minutes".to_string()),
                                category: Some("security".to_string()),
                                cwe_id: Some("CWE-330".to_string()),
                                why_it_matters: Some("Predictable random allows attackers to guess tokens.".to_string()),
                            });
                        }
                    }
                }
            }
        }
        Ok(findings)
    }
}
