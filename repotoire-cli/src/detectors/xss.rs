//! XSS Detection

use crate::detectors::base::{is_test_file, Detector, DetectorConfig};
use crate::detectors::taint::{TaintAnalysisResult, TaintAnalyzer, TaintCategory};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;

static XSS_PATTERN: OnceLock<Regex> = OnceLock::new();

fn xss_pattern() -> &'static Regex {
    XSS_PATTERN.get_or_init(|| Regex::new(r"(?i)(innerHTML|outerHTML|document\.write|dangerouslySetInnerHTML|v-html|ng-bind-html|\[innerHTML\])").unwrap())
}

pub struct XssDetector {
    repository_path: PathBuf,
    max_findings: usize,
    taint_analyzer: TaintAnalyzer,
}

impl XssDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
            taint_analyzer: TaintAnalyzer::new(),
        }
    }
}

impl Detector for XssDetector {
    fn name(&self) -> &'static str {
        "xss"
    }
    fn description(&self) -> &'static str {
        "Detects XSS vulnerabilities"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        // Run taint analysis for XSS
        let mut taint_paths = self.taint_analyzer.trace_taint(graph, TaintCategory::Xss);
        let intra_paths = crate::detectors::data_flow::run_intra_function_taint(
            &self.taint_analyzer,
            graph,
            TaintCategory::Xss,
            &self.repository_path,
        );
        taint_paths.extend(intra_paths);
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
            if !matches!(ext, "js" | "ts" | "jsx" | "tsx" | "vue" | "html" | "php") {
                continue;
            }

            // Skip test files - they often have test fixtures with XSS patterns
            if is_test_file(path) {
                continue;
            }

            // Skip framework internals (React/Vue/Angular core SSR code)
            let path_str_lower = path.to_string_lossy().to_lowercase();
            if path_str_lower.contains("fizzconfig")  // React SSR core
                || path_str_lower.contains("server/react")
                || path_str_lower.contains("dom-bindings")  // React DOM bindings
                || path_str_lower.contains("/packages/react-dom/")
                || path_str_lower.contains("/packages/vue/")
                || path_str_lower.contains("/packages/angular/")
            {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let file_str = path.to_string_lossy();

                for (i, line) in content.lines().enumerate() {
                    if xss_pattern().is_match(line) {
                        // Word-boundary checks to avoid FPs like inputStream, maxInput (#24)
                        let line_lower = line.to_lowercase();
                        let has_user_input = line_lower.contains("req.")
                            || line_lower.contains("props.")
                            || line_lower.contains("req.params")
                            || line_lower.contains("req.query")
                            || line_lower.contains(".params[")
                            || line_lower.contains(".query[")
                            || line_lower.contains("user_input")
                            || line_lower.contains("userinput")
                            || line_lower.contains("form_data")
                            || line_lower.contains("formdata")
                            || line_lower.contains("request.body")
                            || line_lower.contains("request.query");

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
                                    "Direct HTML injection can lead to XSS attacks.\n\n\
                                     **Taint Analysis Note**: A sanitizer function (`{}`) was found \
                                     in the data flow path, which may mitigate this vulnerability.",
                                    taint_path.sanitizer.as_deref().unwrap_or("unknown")
                                ))
                            }
                            Some(taint_path) => {
                                // Unsanitized taint path - critical
                                (Severity::Critical, format!(
                                    "Direct HTML injection can lead to XSS attacks.\n\n\
                                     **Taint Analysis Confirmed**: Data flow analysis traced a path \
                                     from user input to this XSS sink without sanitization:\n\n\
                                     `{}`",
                                    taint_path.path_string()
                                ))
                            }
                            None => {
                                // No taint path - use pattern-based severity
                                let sev = if has_user_input {
                                    Severity::Critical
                                } else {
                                    Severity::Medium
                                };
                                (
                                    sev,
                                    "Direct HTML injection can lead to XSS attacks.".to_string(),
                                )
                            }
                        };

                        findings.push(Finding {
                            id: String::new(),
                            detector: "XssDetector".to_string(),
                            severity,
                            title: "Potential XSS vulnerability".to_string(),
                            description,
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(
                                "Sanitize input or use textContent instead.".to_string(),
                            ),
                            estimated_effort: Some("30 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-79".to_string()),
                            why_it_matters: Some(
                                "XSS allows attackers to execute scripts in users' browsers."
                                    .to_string(),
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        // Filter out Low severity (sanitized) findings
        findings.retain(|f| f.severity != Severity::Low);

        Ok(findings)
    }
}
