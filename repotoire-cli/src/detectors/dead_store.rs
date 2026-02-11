//! Dead Store Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::OnceLock;

static ASSIGNMENT: OnceLock<Regex> = OnceLock::new();

fn assignment() -> &'static Regex {
    ASSIGNMENT.get_or_init(|| Regex::new(r"^\s*(let|var|const|int|float|string|auto)?\s*(\w+)\s*=").unwrap())
}

pub struct DeadStoreDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl DeadStoreDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for DeadStoreDetector {
    fn name(&self) -> &'static str { "dead-store" }
    fn description(&self) -> &'static str { "Detects variables assigned but never read" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"go"|"rs") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();
                let mut assignments: HashMap<String, usize> = HashMap::new();
                
                for (i, line) in lines.iter().enumerate() {
                    if let Some(caps) = assignment().captures(line) {
                        if let Some(var) = caps.get(2) {
                            let var_name = var.as_str().to_string();
                            // Skip common patterns
                            if var_name.starts_with('_') || var_name == "self" || var_name == "this" { continue; }
                            assignments.insert(var_name, i);
                        }
                    }
                }
                
                // Check if variables are used after assignment
                for (var, assign_line) in &assignments {
                    let used_after = lines.iter().skip(assign_line + 1).any(|l| {
                        l.contains(var.as_str()) && !l.trim().starts_with("//") && !l.trim().starts_with("#")
                    });
                    
                    if !used_after && findings.len() < self.max_findings {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "DeadStoreDetector".to_string(),
                            severity: Severity::Low,
                            title: format!("Dead store: {} assigned but not used", var),
                            description: "Variable is assigned but never read afterward.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((*assign_line + 1) as u32),
                            line_end: Some((*assign_line + 1) as u32),
                            suggested_fix: Some("Remove unused assignment or use the variable.".to_string()),
                            estimated_effort: Some("5 minutes".to_string()),
                            category: Some("code-quality".to_string()),
                            cwe_id: Some("CWE-563".to_string()),
                            why_it_matters: Some("Indicates logic error or dead code.".to_string()),
                        });
                    }
                }
            }
        }
        Ok(findings)
    }
}
