//! React Hooks Rules Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static HOOK_CALL: OnceLock<Regex> = OnceLock::new();
static CONDITIONAL: OnceLock<Regex> = OnceLock::new();

fn hook_call() -> &'static Regex {
    HOOK_CALL.get_or_init(|| Regex::new(r"\b(useState|useEffect|useContext|useReducer|useCallback|useMemo|useRef|useImperativeHandle|useLayoutEffect|useDebugValue)\s*\(").unwrap())
}

fn conditional() -> &'static Regex {
    CONDITIONAL.get_or_init(|| Regex::new(r"^\s*(if|else|switch|for|while|\?|&&|\|\|)").unwrap())
}

pub struct ReactHooksDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl ReactHooksDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for ReactHooksDetector {
    fn name(&self) -> &'static str { "react-hooks" }
    fn description(&self) -> &'static str { "Detects React hooks rules violations" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "js"|"jsx"|"ts"|"tsx") { continue; }

            if let Ok(content) = std::fs::read_to_string(path) {
                let mut in_conditional = false;
                let mut cond_depth = 0;
                
                for (i, line) in content.lines().enumerate() {
                    // Track conditional blocks
                    if conditional().is_match(line) {
                        in_conditional = true;
                        cond_depth = 0;
                    }
                    if in_conditional {
                        cond_depth += line.matches('{').count() as i32;
                        cond_depth -= line.matches('}').count() as i32;
                        if cond_depth <= 0 { in_conditional = false; }
                    }
                    
                    // Check for hooks in conditional
                    if in_conditional && hook_call().is_match(line) {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "ReactHooksDetector".to_string(),
                            severity: Severity::High,
                            title: "React hook called conditionally".to_string(),
                            description: "Hooks must be called in the same order every render.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("Move hook outside conditional or use early return.".to_string()),
                            estimated_effort: Some("15 minutes".to_string()),
                            category: Some("bug-risk".to_string()),
                            cwe_id: None,
                            why_it_matters: Some("Violates Rules of Hooks, causes bugs.".to_string()),
                        });
                    }
                }
            }
        }
        Ok(findings)
    }
}
