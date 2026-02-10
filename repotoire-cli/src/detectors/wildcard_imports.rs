//! Wildcard Imports Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static WILDCARD_PATTERN: OnceLock<Regex> = OnceLock::new();

fn wildcard_pattern() -> &'static Regex {
    WILDCARD_PATTERN.get_or_init(|| Regex::new(r"(?i)(from\s+\S+\s+import\s+\*|import\s+\*\s+from|import\s+\*\s*;|\.\*;)").unwrap())
}

pub struct WildcardImportsDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl WildcardImportsDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 100 }
    }
}

impl Detector for WildcardImportsDetector {
    fn name(&self) -> &'static str { "wildcard-imports" }
    fn description(&self) -> &'static str { "Detects wildcard imports" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"java") { continue; }

            if let Ok(content) = std::fs::read_to_string(path) {
                for (i, line) in content.lines().enumerate() {
                    if wildcard_pattern().is_match(line) {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "WildcardImportsDetector".to_string(),
                            severity: Severity::Low,
                            title: "Wildcard import".to_string(),
                            description: "Wildcard imports pollute namespace and hide dependencies.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("Import specific names instead.".to_string()),
                            estimated_effort: Some("5 minutes".to_string()),
                            category: Some("code-quality".to_string()),
                            cwe_id: None,
                            why_it_matters: Some("Makes code harder to understand and refactor.".to_string()),
                        });
                    }
                }
            }
        }
        Ok(findings)
    }
}
