//! Single Character Variable Names Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static SINGLE_CHAR: OnceLock<Regex> = OnceLock::new();

fn single_char() -> &'static Regex {
    SINGLE_CHAR.get_or_init(|| Regex::new(r"\b(let|var|const|def|int|string|float|double)\s+([a-zA-Z])\s*[=:]").unwrap())
}

pub struct SingleCharNamesDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl SingleCharNamesDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for SingleCharNamesDetector {
    fn name(&self) -> &'static str { "single-char-names" }
    fn description(&self) -> &'static str { "Detects single-character variable names" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"java"|"go"|"rs"|"cs") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    // Skip loop variables (for i in, for (int i, etc)
                    if line.contains("for ") || line.contains("for(") { continue; }
                    // Skip lambda parameters
                    if line.contains("=>") || line.contains("lambda ") { continue; }
                    
                    if let Some(caps) = single_char().captures(line) {
                        if let Some(var) = caps.get(2) {
                            let v = var.as_str();
                            // Allow common math variables
                            if matches!(v, "x"|"y"|"z"|"i"|"j"|"k"|"n"|"m") { continue; }
                            
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "SingleCharNamesDetector".to_string(),
                                severity: Severity::Low,
                                title: format!("Single-character variable: {}", v),
                                description: "Single-letter names reduce code readability.".to_string(),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some("Use a descriptive name.".to_string()),
                                estimated_effort: Some("2 minutes".to_string()),
                                category: Some("readability".to_string()),
                                cwe_id: None,
                                why_it_matters: Some("Hard to understand purpose.".to_string()),
                            });
                        }
                    }
                }
            }
        }
        Ok(findings)
    }
}
