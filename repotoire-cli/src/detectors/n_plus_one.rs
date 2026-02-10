//! N+1 Query Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static LOOP: OnceLock<Regex> = OnceLock::new();
static QUERY: OnceLock<Regex> = OnceLock::new();

fn loop_pattern() -> &'static Regex {
    LOOP.get_or_init(|| Regex::new(r"(?i)(for\s+\w+\s+in|\.forEach|\.map\(|\.each)").unwrap())
}

fn query_pattern() -> &'static Regex {
    QUERY.get_or_init(|| Regex::new(r"(?i)(\.get\(|\.find\(|\.filter\(|\.first\(|\.where\(|\.query\(|SELECT\s|Model\.\w+\.get|await\s+\w+\.findOne)").unwrap())
}

pub struct NPlusOneDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl NPlusOneDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for NPlusOneDetector {
    fn name(&self) -> &'static str { "n-plus-one" }
    fn description(&self) -> &'static str { "Detects N+1 query patterns" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let path_str = path.to_string_lossy();
            if path_str.contains("test") || path_str.contains("spec") { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"rb"|"java"|"go") { continue; }

            if let Ok(content) = std::fs::read_to_string(path) {
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
                        
                        if query_pattern().is_match(line) {
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "NPlusOneDetector".to_string(),
                                severity: Severity::High,
                                title: "Potential N+1 query".to_string(),
                                description: format!("Database query inside loop (started line {})", loop_line),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some("Use bulk fetch before loop or eager loading.".to_string()),
                                estimated_effort: Some("45 minutes".to_string()),
                                category: Some("performance".to_string()),
                                cwe_id: None,
                                why_it_matters: Some("Causes N database calls instead of 1.".to_string()),
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
