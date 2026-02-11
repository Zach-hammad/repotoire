//! TODO/FIXME Scanner

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

static TODO_PATTERN: OnceLock<Regex> = OnceLock::new();

fn get_pattern() -> &'static Regex {
    TODO_PATTERN.get_or_init(|| Regex::new(r"(?i)\b(TODO|FIXME|HACK|XXX|BUG)[\s:]+(.{0,80})").unwrap())
}

pub struct TodoScanner {
    repository_path: PathBuf,
    max_findings: usize,
}

impl TodoScanner {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 200 }
    }
}

impl Detector for TodoScanner {
    fn name(&self) -> &'static str { "todo-scanner" }
    fn description(&self) -> &'static str { "Finds TODO, FIXME, HACK comments" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"jsx"|"tsx"|"rs"|"go"|"java"|"rb"|"php"|"cs"|"cpp"|"c"|"h") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (line_num, line) in content.lines().enumerate() {
                    if let Some(caps) = get_pattern().captures(line) {
                        let tag = caps.get(1).map(|m| m.as_str()).unwrap_or("TODO");
                        let msg = caps.get(2).map(|m| m.as_str().trim()).unwrap_or("");
                        let severity = if tag.eq_ignore_ascii_case("FIXME") || tag.eq_ignore_ascii_case("BUG") { Severity::Medium } else { Severity::Low };
                        
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "TodoScanner".to_string(),
                            severity,
                            title: format!("{}: {}", tag.to_uppercase(), if msg.is_empty() { "(no description)" } else { msg }),
                            description: format!("Found {} comment indicating unfinished work.", tag.to_uppercase()),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((line_num + 1) as u32),
                            line_end: Some((line_num + 1) as u32),
                            suggested_fix: Some("Address this or create a ticket.".to_string()),
                            estimated_effort: None,
                            category: Some("technical-debt".to_string()),
                            cwe_id: None,
                            why_it_matters: None,
                        });
                    }
                }
            }
        }
        Ok(findings)
    }
}
