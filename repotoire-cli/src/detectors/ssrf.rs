//! SSRF Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;

static HTTP_CLIENT: OnceLock<Regex> = OnceLock::new();

fn http_client() -> &'static Regex {
    HTTP_CLIENT.get_or_init(|| Regex::new(r"(?i)(requests\.(get|post|put|delete)|fetch\(|axios\.|http\.get|urllib|urlopen|HttpClient|curl)").unwrap())
}

pub struct SsrfDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl SsrfDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for SsrfDetector {
    fn name(&self) -> &'static str { "ssrf" }
    fn description(&self) -> &'static str { "Detects SSRF vulnerabilities" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"jsx"|"tsx"|"rb"|"php"|"java"|"go") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    if http_client().is_match(line) {
                        let has_user_input = line.contains("req.") || line.contains("request.") ||
                            line.contains("params") || line.contains("query") || line.contains("input");
                        if has_user_input {
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "SsrfDetector".to_string(),
                                severity: Severity::High,
                                title: "Potential SSRF vulnerability".to_string(),
                                description: "HTTP request with user-controlled URL.".to_string(),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some("Validate URL against allowlist, block internal IPs.".to_string()),
                                estimated_effort: Some("45 minutes".to_string()),
                                category: Some("security".to_string()),
                                cwe_id: Some("CWE-918".to_string()),
                                why_it_matters: Some("Attackers could access internal services.".to_string()),
                            });
                        }
                    }
                }
            }
        }
        Ok(findings)
    }
}
