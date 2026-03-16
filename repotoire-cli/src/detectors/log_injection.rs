//! Log Injection Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::taint::{TaintAnalyzer, TaintCategory};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::LazyLock;

static LOG_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?i)(logger\.|log\.|console\.log|print\(|logging\.)").expect("valid regex")
    });

pub struct LogInjectionDetector {
    repository_path: PathBuf,
    max_findings: usize,
    taint_analyzer: TaintAnalyzer,
    precomputed_cross: std::sync::OnceLock<Vec<crate::detectors::taint::TaintPath>>,
    precomputed_intra: std::sync::OnceLock<Vec<crate::detectors::taint::TaintPath>>,
}

impl LogInjectionDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
            taint_analyzer: TaintAnalyzer::new(),
            precomputed_cross: std::sync::OnceLock::new(),
            precomputed_intra: std::sync::OnceLock::new(),
        }
    }
}

impl Detector for LogInjectionDetector {
    fn name(&self) -> &'static str {
        "log-injection"
    }
    fn description(&self) -> &'static str {
        "Detects user input in logs"
    }

    crate::detectors::impl_taint_precompute!();

    fn taint_category(&self) -> Option<crate::detectors::taint::TaintCategory> {
        Some(TaintCategory::LogInjection)
    }

    fn file_extensions(&self) -> &'static [&'static str] {
        &["py", "js", "ts", "jsx", "tsx", "rb", "php", "java", "go"]
    }

    // No content_requirements — logging is everywhere, don't filter

    fn detect(&self, ctx: &crate::detectors::analysis_context::AnalysisContext) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let files = &ctx.as_file_provider();
        let mut findings = vec![];

        for path in files.files_with_extensions(&["py", "js", "ts", "java", "go", "rb", "php"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            if let Some(content) = files.content(path) {
                let lines: Vec<&str> = content.lines().collect();
                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    if LOG_PATTERN.is_match(line) {
                        let has_user_input = line.contains("req.")
                            || line.contains("request")
                            || line.contains("input")
                            || line.contains("user")
                            || line.contains("params");
                        if has_user_input
                            && (line.contains("f\"") || line.contains("${") || line.contains("+ "))
                        {
                            findings.push(Finding {
                                id: String::new(),
                                detector: "LogInjectionDetector".to_string(),
                                severity: Severity::Medium,
                                title: "User input in log statement".to_string(),
                                description:
                                    "Unsanitized user input in logs can enable log forging."
                                        .to_string(),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some(
                                    "Sanitize newlines and control chars from input.".to_string(),
                                ),
                                estimated_effort: Some("10 minutes".to_string()),
                                category: Some("security".to_string()),
                                cwe_id: Some("CWE-117".to_string()),
                                why_it_matters: Some(
                                    "Attackers can forge log entries.".to_string(),
                                ),
                                ..Default::default()
                            });
                        }
                    }
                }
            }
        }

        // Run taint analysis to adjust severity based on data flow (precomputed or fallback)
        let mut taint_results = if let Some(cross) = self.precomputed_cross.get() {
            cross.clone()
        } else {
            self.taint_analyzer.trace_taint(graph, TaintCategory::LogInjection)
        };
        let intra_paths = if let Some(intra) = self.precomputed_intra.get() {
            intra.clone()
        } else {
            crate::detectors::taint::run_intra_function_taint(
                &self.taint_analyzer,
                graph,
                TaintCategory::LogInjection,
                &self.repository_path,
            )
        };
        taint_results.extend(intra_paths);

        // Adjust severity based on taint analysis
        for finding in &mut findings {
            let file_path = finding
                .affected_files
                .first()
                .map(|p| p.to_string_lossy().to_string())
                .unwrap_or_default();
            let line = finding.line_start.unwrap_or(0);

            for taint in &taint_results {
                if taint.sink_file == file_path && taint.sink_line == line {
                    if taint.is_sanitized {
                        finding.severity = Severity::Low;
                    } else {
                        finding.severity = Severity::High;
                        finding.description = format!(
                            "{}\n\n**Taint Analysis:** Unsanitized data flow from {} (line {}).",
                            finding.description, taint.source_function, taint.source_line
                        );
                    }
                    break;
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
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_user_input_in_log() {
        let store = GraphStore::in_memory();
        let detector = LogInjectionDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("app.py", "import logging\n\ndef handle_request(request):\n    username = request.get(\"user\")\n    logging.info(f\"Login attempt for user: {username}\")\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect user input in log statement with f-string"
        );
        assert!(
            findings.iter().any(|f| f.detector == "LogInjectionDetector"),
            "Finding should come from LogInjectionDetector"
        );
    }

    #[test]
    fn test_no_finding_for_static_log() {
        let store = GraphStore::in_memory();
        let detector = LogInjectionDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("app.py", "import logging\n\ndef startup():\n    logging.info(\"Application started successfully\")\n    logging.debug(\"Debug mode enabled\")\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Static log messages should produce no findings, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_detects_console_log_with_user_input_js() {
        let store = GraphStore::in_memory();
        let detector = LogInjectionDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("server.js", "function handleLogin(req, res) {\n    const username = req.body.username;\n    console.log(`Login attempt: ${req.body.username}`);\n    res.sendStatus(200);\n}\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect console.log with user input via template literal"
        );
        assert!(
            findings.iter().any(|f| f.cwe_id.as_deref() == Some("CWE-117")),
            "Finding should have CWE-117"
        );
    }

    #[test]
    fn test_detects_logger_with_user_input_python() {
        let store = GraphStore::in_memory();
        let detector = LogInjectionDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("views.py", "import logging\nlogger = logging.getLogger(__name__)\n\ndef process_request(request):\n    user_agent = request.headers.get('User-Agent')\n    logger.info(f\"Request from user agent: {user_agent}\")\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect logger.info with user input from request via f-string"
        );
        assert!(
            findings.iter().any(|f| f.detector == "LogInjectionDetector"),
            "Finding should come from LogInjectionDetector"
        );
    }

    #[test]
    fn test_no_finding_for_log_pattern_in_string_literal() {
        let store = GraphStore::in_memory();
        let detector = LogInjectionDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("config.js", "const config = {\n    message: \"Use console.log for debugging\",\n    level: \"info\"\n};\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "console.log mentioned in a string literal should not trigger, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_log_without_interpolation() {
        let store = GraphStore::in_memory();
        let detector = LogInjectionDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("app.js", "function handleRequest(req) {\n    console.log(\"Received request from user\");\n    processData(req);\n}\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Log with user-related words but no interpolation should not trigger, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
