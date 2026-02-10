//! Duplicate Code Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashMap;
use std::path::PathBuf;
use uuid::Uuid;

pub struct DuplicateCodeDetector {
    repository_path: PathBuf,
    max_findings: usize,
    min_lines: usize,
}

impl DuplicateCodeDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50, min_lines: 6 }
    }

    fn normalize_line(line: &str) -> String {
        // Normalize whitespace and remove comments
        let trimmed = line.trim();
        if trimmed.starts_with("//") || trimmed.starts_with("#") || trimmed.starts_with("*") {
            return String::new();
        }
        trimmed.split_whitespace().collect::<Vec<_>>().join(" ")
    }
}

impl Detector for DuplicateCodeDetector {
    fn name(&self) -> &'static str { "duplicate-code" }
    fn description(&self) -> &'static str { "Detects copy-pasted code blocks" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let mut blocks: HashMap<String, Vec<(PathBuf, usize)>> = HashMap::new();
        
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"jsx"|"tsx"|"java"|"go"|"rs"|"rb"|"php"|"c"|"cpp") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();
                
                // Sliding window of min_lines
                for i in 0..lines.len().saturating_sub(self.min_lines) {
                    let block: String = lines[i..i+self.min_lines]
                        .iter()
                        .map(|l| Self::normalize_line(l))
                        .filter(|l| !l.is_empty())
                        .collect::<Vec<_>>()
                        .join("\n");
                    
                    if block.len() > 50 { // Ignore trivial blocks
                        blocks.entry(block).or_default().push((path.to_path_buf(), i + 1));
                    }
                }
            }
        }

        // Find duplicates
        for (block, locations) in blocks {
            if locations.len() > 1 && findings.len() < self.max_findings {
                let files: Vec<_> = locations.iter().map(|(p, _)| p.clone()).collect();
                let first_line = locations[0].1;
                
                findings.push(Finding {
                    id: Uuid::new_v4().to_string(),
                    detector: "DuplicateCodeDetector".to_string(),
                    severity: if locations.len() > 3 { Severity::Medium } else { Severity::Low },
                    title: format!("Duplicate code ({} occurrences)", locations.len()),
                    description: format!("Same code block found in {} places.", locations.len()),
                    affected_files: files,
                    line_start: Some(first_line as u32),
                    line_end: Some((first_line + self.min_lines) as u32),
                    suggested_fix: Some("Extract into a shared function.".to_string()),
                    estimated_effort: Some("20 minutes".to_string()),
                    category: Some("maintainability".to_string()),
                    cwe_id: None,
                    why_it_matters: Some("Duplicate code means duplicate bugs.".to_string()),
                });
            }
        }
        Ok(findings)
    }
}
