//! SSRF Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::taint::{TaintAnalysisResult, TaintAnalyzer, TaintCategory};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static HTTP_CLIENT: OnceLock<Regex> = OnceLock::new();

fn http_client() -> &'static Regex {
    HTTP_CLIENT.get_or_init(|| Regex::new(r"(?i)(requests\.(get|post|put|delete)|fetch\(|axios\.|http\.get|urllib|urlopen|HttpClient|curl)").unwrap())
}

pub struct SsrfDetector {
    repository_path: PathBuf,
    max_findings: usize,
    taint_analyzer: TaintAnalyzer,
}

impl SsrfDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
            taint_analyzer: TaintAnalyzer::new(),
        }
    }
}

impl Detector for SsrfDetector {
    fn name(&self) -> &'static str {
        "ssrf"
    }
    fn description(&self) -> &'static str {
        "Detects SSRF vulnerabilities"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        // Run taint analysis for SSRF
        let taint_paths = self.taint_analyzer.trace_taint(graph, TaintCategory::Ssrf);
        let taint_result = TaintAnalysisResult::from_paths(taint_paths);

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings {
                break;
            }
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(
                ext,
                "py" | "js" | "ts" | "jsx" | "tsx" | "rb" | "php" | "java" | "go"
            ) {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let file_str = path.to_string_lossy();

                for (i, line) in content.lines().enumerate() {
                    if http_client().is_match(line) {
                        // Skip relative URLs - they always hit same-origin server
                        // Pattern: fetch('/api/...) or fetch(`/api/...)
                        if line.contains("fetch('/")
                            || line.contains("fetch(`/")
                            || line.contains("fetch(\"/")
                        {
                            continue;
                        }

                        // Skip config constant URLs (API_URL, BASE_URL, etc.)
                        // These are from env/config, not user input
                        if line.contains("API_URL")
                            || line.contains("BASE_URL")
                            || line.contains("SERVER_URL")
                            || line.contains("BACKEND_URL")
                            || line.contains("apiUrl")
                            || line.contains("baseUrl")
                        {
                            // Additional check: must have interpolation to be potential SSRF
                            // If it's just API_URL + "/path", that's safe
                            let has_dynamic_path = line.contains("params")
                                || line.contains("query")
                                || line.contains("${")
                                    && !line.contains("${API_URL")
                                    && !line.contains("${BASE_URL")
                                    && !line.contains("${SERVER_URL");
                            if !has_dynamic_path {
                                continue;
                            }
                        }

                        let has_user_input = line.contains("req.")
                            || line.contains("request.")
                            || line.contains("params")
                            || line.contains("query")
                            || line.contains("input");
                        if has_user_input {
                            let line_num = (i + 1) as u32;

                            // Check taint analysis for this location
                            let matching_taint = taint_result.paths.iter().find(|p| {
                                (p.sink_file == file_str || p.source_file == file_str)
                                    && (p.sink_line == line_num || p.source_line == line_num)
                            });

                            // Adjust severity based on taint analysis
                            let (severity, description) = match matching_taint {
                                Some(taint_path) if taint_path.is_sanitized => {
                                    // Sanitizer found - lower severity
                                    (Severity::Low, format!(
                                        "HTTP request with user-controlled URL.\n\n\
                                         **Taint Analysis Note**: A sanitizer function (`{}`) was found \
                                         in the data flow path, which may mitigate this vulnerability.",
                                        taint_path.sanitizer.as_deref().unwrap_or("unknown")
                                    ))
                                }
                                Some(taint_path) => {
                                    // Unsanitized taint path - critical
                                    (Severity::Critical, format!(
                                        "HTTP request with user-controlled URL.\n\n\
                                         **Taint Analysis Confirmed**: Data flow analysis traced a path \
                                         from user input to this SSRF sink without sanitization:\n\n\
                                         `{}`",
                                        taint_path.path_string()
                                    ))
                                }
                                None => {
                                    // No taint path - use pattern-based severity
                                    (
                                        Severity::High,
                                        "HTTP request with user-controlled URL.".to_string(),
                                    )
                                }
                            };

                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "SsrfDetector".to_string(),
                                severity,
                                title: "Potential SSRF vulnerability".to_string(),
                                description,
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some(line_num),
                                line_end: Some(line_num),
                                suggested_fix: Some(
                                    "Validate URL against allowlist, block internal IPs."
                                        .to_string(),
                                ),
                                estimated_effort: Some("45 minutes".to_string()),
                                category: Some("security".to_string()),
                                cwe_id: Some("CWE-918".to_string()),
                                why_it_matters: Some(
                                    "Attackers could access internal services.".to_string(),
                                ),
                                ..Default::default()
                            });
                        }
                    }
                }
            }
        }

        // Filter out Low severity (sanitized) findings
        findings.retain(|f| f.severity != Severity::Low);

        Ok(findings)
    }
}
