//! Global Variables Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static GLOBAL_PATTERN: OnceLock<Regex> = OnceLock::new();

fn global_pattern() -> &'static Regex {
    GLOBAL_PATTERN.get_or_init(|| Regex::new(r"^(var\s+\w+\s*=|let\s+\w+\s*=|global\s+\w+|\w+\s*=\s*[^=])").unwrap())
}

pub struct GlobalVariablesDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl GlobalVariablesDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for GlobalVariablesDetector {
    fn name(&self) -> &'static str { "global-variables" }
    fn description(&self) -> &'static str { "Detects mutable global variables" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts") { continue; }

            if let Ok(content) = std::fs::read_to_string(path) {
                let mut in_function = false;
                let mut brace_depth = 0;
                
                for (i, line) in content.lines().enumerate() {
                    let trimmed = line.trim();
                    
                    // Track function scope
                    if trimmed.starts_with("def ") || trimmed.starts_with("function ") || 
                       trimmed.contains("=> {") || trimmed.starts_with("async ") {
                        in_function = true;
                    }
                    brace_depth += line.matches('{').count() as i32;
                    brace_depth -= line.matches('}').count() as i32;
                    if brace_depth == 0 && in_function { in_function = false; }
                    
                    // Skip if inside function
                    if in_function { continue; }
                    
                    // Skip constants, imports, classes
                    if trimmed.starts_with("const ") || trimmed.starts_with("import ") ||
                       trimmed.starts_with("from ") || trimmed.starts_with("class ") ||
                       trimmed.starts_with("#") || trimmed.starts_with("//") ||
                       trimmed.is_empty() || trimmed.starts_with("export ") {
                        continue;
                    }
                    
                    // Check for global assignment
                    if ext == "py" && trimmed.contains("global ") {
                        findings.push(self.create_finding(path, i + 1));
                    } else if matches!(ext, "js"|"ts") && (trimmed.starts_with("var ") || trimmed.starts_with("let ")) {
                        findings.push(self.create_finding(path, i + 1));
                    }
                }
            }
        }
        Ok(findings)
    }
}

impl GlobalVariablesDetector {
    fn create_finding(&self, path: &std::path::Path, line: usize) -> Finding {
        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "GlobalVariablesDetector".to_string(),
            severity: Severity::Low,
            title: "Global mutable variable".to_string(),
            description: "Global mutable state makes code hard to reason about.".to_string(),
            affected_files: vec![path.to_path_buf()],
            line_start: Some(line as u32),
            line_end: Some(line as u32),
            suggested_fix: Some("Use const, or encapsulate in a module/class.".to_string()),
            estimated_effort: Some("15 minutes".to_string()),
            category: Some("code-quality".to_string()),
            cwe_id: None,
            why_it_matters: Some("Global state causes hidden dependencies.".to_string()),
        }
    }
}
