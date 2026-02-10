//! XXE Injection Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static XXE_PATTERN: OnceLock<Regex> = OnceLock::new();

fn xxe_pattern() -> &'static Regex {
    XXE_PATTERN.get_or_init(|| Regex::new(r"(?i)(xml\.parse|parseXML|XMLParser|DocumentBuilder|SAXParser|etree\.parse|lxml\.etree|xml\.etree|DOMParser|XMLReader)").unwrap())
}

pub struct XxeDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl XxeDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for XxeDetector {
    fn name(&self) -> &'static str { "xxe" }
    fn description(&self) -> &'static str { "Detects XXE vulnerabilities" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"java"|"php"|"cs"|"rb") { continue; }

            if let Ok(content) = std::fs::read_to_string(path) {
                for (i, line) in content.lines().enumerate() {
                    if xxe_pattern().is_match(line) {
                        let has_protection = content.contains("resolve_entities=False") ||
                            content.contains("FEATURE_EXTERNAL") || content.contains("disallow-doctype");
                        
                        if !has_protection {
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "XxeDetector".to_string(),
                                severity: Severity::High,
                                title: "Potential XXE vulnerability".to_string(),
                                description: "XML parsing without disabling external entities.".to_string(),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some("Disable external entity processing.".to_string()),
                                estimated_effort: Some("20 minutes".to_string()),
                                category: Some("security".to_string()),
                                cwe_id: Some("CWE-611".to_string()),
                                why_it_matters: Some("XXE allows file disclosure and SSRF.".to_string()),
                            });
                        }
                    }
                }
            }
        }
        Ok(findings)
    }
}
