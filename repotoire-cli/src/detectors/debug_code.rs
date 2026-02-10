//! Debug Code Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static DEBUG_PATTERN: OnceLock<Regex> = OnceLock::new();

fn debug_pattern() -> &'static Regex {
    DEBUG_PATTERN.get_or_init(|| Regex::new(r"(?i)(console\.(log|debug|info|warn)|print\(|debugger;?|debug\s*=\s*True|DEBUG\s*=\s*true|binding\.pry|byebug|import\s+pdb|pdb\.set_trace)").unwrap())
}

pub struct DebugCodeDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl DebugCodeDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 100 }
    }
}

impl Detector for DebugCodeDetector {
    fn name(&self) -> &'static str { "debug-code" }
    fn description(&self) -> &'static str { "Detects debug statements left in code" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            // Skip test files
            let path_str = path.to_string_lossy();
            if path_str.contains("test") || path_str.contains("spec") { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"jsx"|"tsx"|"rb"|"java") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    let trimmed = line.trim();
                    if trimmed.starts_with("//") || trimmed.starts_with("#") { continue; }
                    
                    if debug_pattern().is_match(line) {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "DebugCodeDetector".to_string(),
                            severity: Severity::Low,
                            title: "Debug code left in".to_string(),
                            description: "Debug statements should be removed before production.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("Remove or replace with proper logging.".to_string()),
                            estimated_effort: Some("5 minutes".to_string()),
                            category: Some("code-quality".to_string()),
                            cwe_id: None,
                            why_it_matters: Some("Debug code can leak sensitive info and clutter logs.".to_string()),
                        });
                    }
                }
            }
        }
        Ok(findings)
    }
}
