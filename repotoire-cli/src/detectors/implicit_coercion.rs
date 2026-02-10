//! Implicit Coercion Detector (JavaScript)

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static LOOSE_EQUALITY: OnceLock<Regex> = OnceLock::new();

fn loose_equality() -> &'static Regex {
    LOOSE_EQUALITY.get_or_init(|| Regex::new(r"[^!=<>]==[^=]|[^!]==[^=]").unwrap())
}

pub struct ImplicitCoercionDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl ImplicitCoercionDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 100 }
    }
}

impl Detector for ImplicitCoercionDetector {
    fn name(&self) -> &'static str { "implicit-coercion" }
    fn description(&self) -> &'static str { "Detects == instead of ===" }

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
                for (i, line) in content.lines().enumerate() {
                    let trimmed = line.trim();
                    if trimmed.starts_with("//") { continue; }
                    
                    // Check for == but not === or !==
                    if loose_equality().is_match(line) && !line.contains("===") && !line.contains("!==") {
                        // Skip null checks which are sometimes intentional
                        if line.contains("== null") || line.contains("null ==") { continue; }
                        
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "ImplicitCoercionDetector".to_string(),
                            severity: Severity::Low,
                            title: "Loose equality (==) used".to_string(),
                            description: "== performs type coercion which can cause bugs.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("Use === for strict equality.".to_string()),
                            estimated_effort: Some("2 minutes".to_string()),
                            category: Some("code-quality".to_string()),
                            cwe_id: None,
                            why_it_matters: Some("Type coercion causes subtle bugs.".to_string()),
                        });
                    }
                }
            }
        }
        Ok(findings)
    }
}
