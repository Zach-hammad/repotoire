//! Unreachable Code Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static RETURN_PATTERN: OnceLock<Regex> = OnceLock::new();

fn return_pattern() -> &'static Regex {
    RETURN_PATTERN.get_or_init(|| Regex::new(r"^\s*(return\b|throw\b|raise\b|exit\(|sys\.exit|process\.exit|break;|continue;)").unwrap())
}

pub struct UnreachableCodeDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl UnreachableCodeDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for UnreachableCodeDetector {
    fn name(&self) -> &'static str { "unreachable-code" }
    fn description(&self) -> &'static str { "Detects code after return/throw" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"jsx"|"tsx"|"java"|"go"|"rs"|"rb"|"php"|"c"|"cpp") { continue; }

            if let Ok(content) = std::fs::read_to_string(path) {
                let lines: Vec<&str> = content.lines().collect();
                
                for i in 0..lines.len().saturating_sub(1) {
                    let line = lines[i];
                    let next = lines[i + 1].trim();
                    
                    // Skip if next line is empty, closing brace, or comment
                    if next.is_empty() || next == "}" || next == "]" || 
                       next.starts_with("//") || next.starts_with("#") ||
                       next.starts_with("else") || next.starts_with("elif") ||
                       next.starts_with("catch") || next.starts_with("finally") ||
                       next.starts_with("case") || next.starts_with("default") {
                        continue;
                    }
                    
                    if return_pattern().is_match(line) && !line.contains("if") && !line.contains("?") {
                        // Check same indentation level
                        let curr_indent = line.len() - line.trim_start().len();
                        let next_indent = lines[i + 1].len() - next.len();
                        
                        if next_indent >= curr_indent && !next.starts_with("}") {
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "UnreachableCodeDetector".to_string(),
                                severity: Severity::Medium,
                                title: "Unreachable code".to_string(),
                                description: "Code after return/throw/exit will never execute.".to_string(),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 2) as u32),
                                line_end: Some((i + 2) as u32),
                                suggested_fix: Some("Remove unreachable code or fix control flow.".to_string()),
                                estimated_effort: Some("10 minutes".to_string()),
                                category: Some("code-quality".to_string()),
                                cwe_id: Some("CWE-561".to_string()),
                                why_it_matters: Some("Dead code indicates logic errors.".to_string()),
                            });
                        }
                    }
                }
            }
        }
        Ok(findings)
    }
}
