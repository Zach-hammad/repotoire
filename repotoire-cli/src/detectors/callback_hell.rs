//! Callback Hell Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;

pub struct CallbackHellDetector {
    repository_path: PathBuf,
    max_findings: usize,
    max_nesting: usize,
}

impl CallbackHellDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50, max_nesting: 3 }
    }
}

impl Detector for CallbackHellDetector {
    fn name(&self) -> &'static str { "callback-hell" }
    fn description(&self) -> &'static str { "Detects deeply nested callbacks" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "js"|"ts"|"jsx"|"tsx") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let mut callback_depth = 0;
                let mut max_depth = 0;
                let mut max_line = 0;
                
                for (i, line) in content.lines().enumerate() {
                    // Count callback indicators
                    let callbacks = line.matches("function(").count() + 
                                   line.matches("=> {").count() +
                                   line.matches(".then(").count();
                    callback_depth += callbacks;
                    
                    // Track closings
                    if line.contains("});") || line.contains("})") {
                        callback_depth = callback_depth.saturating_sub(1);
                    }
                    
                    if callback_depth > max_depth {
                        max_depth = callback_depth;
                        max_line = i + 1;
                    }
                }
                
                if max_depth > self.max_nesting {
                    findings.push(Finding {
                        id: Uuid::new_v4().to_string(),
                        detector: "CallbackHellDetector".to_string(),
                        severity: Severity::Medium,
                        title: format!("Callback hell ({} levels deep)", max_depth),
                        description: "Deeply nested callbacks make code hard to follow.".to_string(),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(max_line as u32),
                        line_end: Some(max_line as u32),
                        suggested_fix: Some("Refactor to async/await or extract functions.".to_string()),
                        estimated_effort: Some("30 minutes".to_string()),
                        category: Some("readability".to_string()),
                        cwe_id: None,
                        why_it_matters: Some("Pyramid of doom hurts readability.".to_string()),
                    });
                }
            }
        }
        Ok(findings)
    }
}
