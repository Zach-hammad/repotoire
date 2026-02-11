//! Magic Numbers Detector

use crate::detectors::base::{Detector, DetectorConfig};
use uuid::Uuid;
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::OnceLock;

static NUMBER_PATTERN: OnceLock<Regex> = OnceLock::new();

fn get_pattern() -> &'static Regex {
    // Match standalone numbers (2+ digits), filter context in logic
    NUMBER_PATTERN.get_or_init(|| Regex::new(r"\b(\d{2,})\b").unwrap())
}

pub struct MagicNumbersDetector {
    repository_path: PathBuf,
    max_findings: usize,
    acceptable: HashSet<i64>,
}

impl MagicNumbersDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        let acceptable: HashSet<i64> = [0,1,2,10,100,1000,60,24,365,360,180,90,255,256,1024].into_iter().collect();
        Self { repository_path: repository_path.into(), max_findings: 100, acceptable }
    }
}

impl Detector for MagicNumbersDetector {
    fn name(&self) -> &'static str { "magic-numbers" }
    fn description(&self) -> &'static str { "Detects unexplained numeric literals" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"jsx"|"tsx"|"rs"|"go"|"java"|"cs"|"cpp"|"c"|"rb"|"php") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (line_num, line) in content.lines().enumerate() {
                    let trimmed = line.trim();
                    if trimmed.starts_with("//") || trimmed.starts_with("#") || trimmed.starts_with("*") { continue; }
                    if trimmed.to_uppercase().contains("CONST") { continue; }

                    for cap in get_pattern().captures_iter(line) {
                        if let Some(m) = cap.get(1) {
                            if let Ok(num) = m.as_str().parse::<i64>() {
                                if !self.acceptable.contains(&num) {
                                    findings.push(Finding {
                                        id: Uuid::new_v4().to_string(),
                                        detector: "MagicNumbersDetector".to_string(),
                                        severity: Severity::Low,
                                        title: format!("Magic number: {}", num),
                                        description: format!("Number {} appears without explanation.", num),
                                        affected_files: vec![path.to_path_buf()],
                                        line_start: Some((line_num + 1) as u32),
                                        line_end: Some((line_num + 1) as u32),
                                        suggested_fix: Some("Extract into a named constant.".to_string()),
                                        estimated_effort: Some("5 minutes".to_string()),
                                        category: Some("readability".to_string()),
                                        cwe_id: None,
                                        why_it_matters: None,
                                        ..Default::default()
                                    });
                                    break;
                                }
                            }
                        }
                    }
                }
            }
        }
        Ok(findings)
    }
}
