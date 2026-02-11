//! Insecure Deserialization Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;

static DESERIALIZE_PATTERN: OnceLock<Regex> = OnceLock::new();

fn deserialize_pattern() -> &'static Regex {
    DESERIALIZE_PATTERN.get_or_init(|| Regex::new(r"(?i)(JSON\.parse|yaml\.load|yaml\.safe_load|unserialize|ObjectInputStream|Marshal\.load|eval\s*\()").unwrap())
}

pub struct InsecureDeserializeDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl InsecureDeserializeDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for InsecureDeserializeDetector {
    fn name(&self) -> &'static str { "insecure-deserialize" }
    fn description(&self) -> &'static str { "Detects insecure deserialization" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"java"|"php"|"rb") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    if deserialize_pattern().is_match(line) {
                        let has_user_input = line.contains("req.") || line.contains("request") ||
                            line.contains("body") || line.contains("input") || line.contains("params");
                        
                        if has_user_input {
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "InsecureDeserializeDetector".to_string(),
                                severity: Severity::High,
                                title: "Insecure deserialization with user input".to_string(),
                                description: "Deserializing user-controlled data can lead to RCE.".to_string(),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some("Validate schema before deserializing.".to_string()),
                                estimated_effort: Some("30 minutes".to_string()),
                                category: Some("security".to_string()),
                                cwe_id: Some("CWE-502".to_string()),
                                why_it_matters: Some("Can lead to remote code execution.".to_string()),
                            });
                        }
                    }
                }
            }
        }
        Ok(findings)
    }
}
