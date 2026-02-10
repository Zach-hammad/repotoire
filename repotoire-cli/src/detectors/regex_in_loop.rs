//! Regex Compilation in Loop Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static LOOP: OnceLock<Regex> = OnceLock::new();
static REGEX_NEW: OnceLock<Regex> = OnceLock::new();

fn loop_pattern() -> &'static Regex {
    LOOP.get_or_init(|| Regex::new(r"(?i)(for\s+\w+\s+in|\.forEach|for\s*\(|while\s*\()").unwrap())
}

fn regex_new() -> &'static Regex {
    REGEX_NEW.get_or_init(|| Regex::new(r"(?i)(Regex::new|re\.compile|new RegExp|Pattern\.compile)").unwrap())
}

pub struct RegexInLoopDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl RegexInLoopDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for RegexInLoopDetector {
    fn name(&self) -> &'static str { "regex-in-loop" }
    fn description(&self) -> &'static str { "Detects regex compilation inside loops" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"java"|"rs"|"go") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let mut in_loop = false;
                let mut loop_line = 0;
                let mut brace_depth = 0;
                
                for (i, line) in content.lines().enumerate() {
                    if loop_pattern().is_match(line) {
                        in_loop = true;
                        loop_line = i + 1;
                        brace_depth = 0;
                    }
                    
                    if in_loop {
                        brace_depth += line.matches('{').count() as i32;
                        brace_depth -= line.matches('}').count() as i32;
                        if brace_depth < 0 { in_loop = false; continue; }
                        
                        if regex_new().is_match(line) {
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "RegexInLoopDetector".to_string(),
                                severity: Severity::Medium,
                                title: "Regex compiled inside loop".to_string(),
                                description: format!("Regex compiled in loop starting line {}.", loop_line),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some("Compile regex once outside loop.".to_string()),
                                estimated_effort: Some("10 minutes".to_string()),
                                category: Some("performance".to_string()),
                                cwe_id: None,
                                why_it_matters: Some("Regex compilation is expensive.".to_string()),
                            });
                            in_loop = false;
                        }
                    }
                }
            }
        }
        Ok(findings)
    }
}
