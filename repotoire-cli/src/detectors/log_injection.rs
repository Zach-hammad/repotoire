//! Log Injection Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::taint::{TaintAnalyzer, TaintCategory};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;

static LOG_PATTERN: OnceLock<Regex> = OnceLock::new();

fn log_pattern() -> &'static Regex {
    LOG_PATTERN
        .get_or_init(|| Regex::new(r"(?i)(logger\.|log\.|console\.log|print\(|logging\.)").expect("valid regex"))
}

pub struct LogInjectionDetector {
    repository_path: PathBuf,
    max_findings: usize,
    taint_analyzer: TaintAnalyzer,
}

impl LogInjectionDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
            taint_analyzer: TaintAnalyzer::new(),
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

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings {
                break;
            }
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py" | "js" | "ts" | "java" | "go" | "rb" | "php") {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    if log_pattern().is_match(line) {
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

        // Run taint analysis to adjust severity based on data flow
        let mut taint_results = self
            .taint_analyzer
            .trace_taint(graph, TaintCategory::LogInjection);
        let intra_paths = crate::detectors::data_flow::run_intra_function_taint(
            &self.taint_analyzer,
            graph,
            TaintCategory::LogInjection,
            &self.repository_path,
        );
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
