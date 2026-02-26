//! SSRF Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::taint::{TaintAnalysisResult, TaintAnalyzer, TaintCategory};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;

static HTTP_CLIENT: OnceLock<Regex> = OnceLock::new();

fn http_client() -> &'static Regex {
    HTTP_CLIENT.get_or_init(|| Regex::new(r"(?i)(requests\.(get|post|put|delete)|fetch\(|axios\.|http\.get|urllib|urlopen|HttpClient|curl)").expect("valid regex"))
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

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        // Run taint analysis for SSRF
        let mut taint_paths = self.taint_analyzer.trace_taint(graph, TaintCategory::Ssrf);
        let intra_paths = crate::detectors::data_flow::run_intra_function_taint(
            &self.taint_analyzer,
            graph,
            TaintCategory::Ssrf,
            &self.repository_path,
        );
        taint_paths.extend(intra_paths);
        let taint_result = TaintAnalysisResult::from_paths(taint_paths);

        for path in files.files_with_extensions(&["py", "js", "ts", "jsx", "tsx", "rb", "php", "java", "go"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            if let Some(content) = files.content(path) {
                let file_str = path.to_string_lossy();
                let lines: Vec<&str> = content.lines().collect();

                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

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
                                || (line.contains("${")
                                    && !line.contains("${API_URL")
                                    && !line.contains("${BASE_URL")
                                    && !line.contains("${SERVER_URL"));
                            if !has_dynamic_path {
                                continue;
                            }
                        }

                        // Check if the URL source is likely from env/config (safe)
                        // or from user input (dangerous)
                        let is_env_sourced = {
                            // Look at surrounding context (20 lines before) for env var / config patterns
                            let context_start = i.saturating_sub(20);
                            let context = &lines[context_start..=i];
                            let context_str = context.join("\n").to_lowercase();

                            // URL variable comes from environment
                            context_str.contains("process.env")
                                || context_str.contains("env.get(")
                                || context_str.contains("os.environ")
                                || context_str.contains("std::env")
                                || context_str.contains("config.")
                                || context_str.contains("options.base")
                                || context_str.contains("baseurl")
                                || context_str.contains("base_url")
                            // Function parameter named url/endpoint from config (not user input)
                            // Removed: "input" substring check caused false negatives (#23)
                        };

                        if is_env_sourced {
                            continue;
                        }

                        let has_user_input = line.contains("req.")
                            || line.contains("request.body")
                            || line.contains("request.query")
                            || line.contains("request.params")
                            || line.contains("ctx.params")
                            || line.contains("ctx.query");
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
                                id: String::new(),
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detectors::base::Detector;
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_requests_get_with_user_input() {
        let store = GraphStore::in_memory();
        let detector = SsrfDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("vuln.py", "import requests\n\ndef fetch_url(req):\n    url = req.body.get(\"url\")\n    response = requests.get(req.body[\"url\"])\n    return response.text\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect requests.get with user-controlled URL from req.body"
        );
        assert!(
            findings.iter().any(|f| f.title.contains("SSRF")),
            "Finding should mention SSRF. Titles: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
        assert!(
            findings.iter().any(|f| f.cwe_id.as_deref() == Some("CWE-918")),
            "Finding should have CWE-918"
        );
    }

    #[test]
    fn test_no_findings_for_hardcoded_url() {
        let store = GraphStore::in_memory();
        let detector = SsrfDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("safe.py", "import requests\n\ndef fetch_data():\n    response = requests.get(\"https://api.example.com/data\")\n    return response.json()\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Hardcoded URL should have no SSRF findings, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_detects_fetch_with_user_input_in_js() {
        let store = GraphStore::in_memory();
        let detector = SsrfDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("proxy.js", "async function proxyRequest(req, res) {\n    const targetUrl = req.body.url;\n    const response = await fetch(req.body.url);\n    const data = await response.json();\n    res.json(data);\n}\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect fetch() with user-controlled URL from req.body"
        );
        assert!(
            findings.iter().any(|f| f.cwe_id.as_deref() == Some("CWE-918")),
            "Finding should have CWE-918"
        );
    }

    #[test]
    fn test_detects_urllib_with_user_input_in_python() {
        let store = GraphStore::in_memory();
        let detector = SsrfDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("handler.py", "from urllib.request import urlopen\n\ndef fetch(request):\n    url = request.query.get('target')\n    response = urlopen(request.query['target'])\n    return response.read()\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect urlopen with user-controlled URL from request.query"
        );
        assert!(
            findings.iter().any(|f| f.title.contains("SSRF")),
            "Finding should mention SSRF"
        );
    }

    #[test]
    fn test_no_finding_for_env_sourced_url() {
        let store = GraphStore::in_memory();
        let detector = SsrfDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("client.py", "import os\nimport requests\n\ndef call_api():\n    base = os.environ.get('API_HOST')\n    response = requests.get(base + '/health')\n    return response.status_code\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "URL sourced from environment variable should not trigger SSRF, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_relative_fetch() {
        let store = GraphStore::in_memory();
        let detector = SsrfDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("api.js", "async function loadData(req, res) {\n    const data = await fetch('/api/users');\n    res.json(await data.json());\n}\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Relative URL fetch should not trigger SSRF, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
