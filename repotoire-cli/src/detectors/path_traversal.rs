//! Path Traversal Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static FILE_OP: OnceLock<Regex> = OnceLock::new();

fn file_op() -> &'static Regex {
    FILE_OP.get_or_init(|| Regex::new(r"(?i)(open|read|write|readFile|writeFile|unlink|remove|delete)\s*\(").unwrap())
}

pub struct PathTraversalDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl PathTraversalDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for PathTraversalDetector {
    fn name(&self) -> &'static str { "path-traversal" }
    fn description(&self) -> &'static str { "Detects path traversal vulnerabilities" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"rb"|"php"|"java"|"go") { continue; }

            if let Ok(content) = std::fs::read_to_string(path) {
                for (i, line) in content.lines().enumerate() {
                    if file_op().is_match(line) {
                        let has_user_input = line.contains("req.") || line.contains("request.") ||
                            line.contains("params") || line.contains("input") || line.contains("argv");
                        if has_user_input {
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "PathTraversalDetector".to_string(),
                                severity: Severity::High,
                                title: "Potential path traversal".to_string(),
                                description: "File operation with user-controlled input detected.".to_string(),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some("Validate path with os.path.basename() or ensure resolved path is within allowed directory.".to_string()),
                                estimated_effort: Some("30 minutes".to_string()),
                                category: Some("security".to_string()),
                                cwe_id: Some("CWE-22".to_string()),
                                why_it_matters: Some("Attackers could access files outside intended directory.".to_string()),
                            });
                        }
                    }
                }
            }
        }
        Ok(findings)
    }
}
