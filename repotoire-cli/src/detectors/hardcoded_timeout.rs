//! Hardcoded Timeout Detector
//!
//! Graph-enhanced detection of hardcoded timeout values:
//! - Uses graph to find where timeouts are used (network, DB, etc.)
//! - Counts occurrences across codebase
//! - Higher severity for network/database timeouts

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static TIMEOUT_PATTERN: OnceLock<Regex> = OnceLock::new();

fn timeout_pattern() -> &'static Regex {
    TIMEOUT_PATTERN.get_or_init(|| {
        Regex::new(r"(?i)(timeout|sleep|delay|wait|setTimeout|setInterval|read_timeout|write_timeout|connect_timeout)\s*[\(=:]\s*(\d{4,})").expect("valid regex")
    })
}

/// Format milliseconds to human-readable string
fn format_duration(ms: u64) -> String {
    if ms >= 60000 {
        format!("{} minutes", ms / 60000)
    } else if ms >= 1000 {
        format!("{} seconds", ms / 1000)
    } else {
        format!("{} ms", ms)
    }
}

/// Suggest an appropriate constant name
fn suggest_constant_name(context: &str, value: u64) -> String {
    let ctx_lower = context.to_lowercase();

    if ctx_lower.contains("connect") {
        return "CONNECTION_TIMEOUT_MS".to_string();
    }
    if ctx_lower.contains("read") {
        return "READ_TIMEOUT_MS".to_string();
    }
    if ctx_lower.contains("write") {
        return "WRITE_TIMEOUT_MS".to_string();
    }
    if ctx_lower.contains("request") || ctx_lower.contains("http") {
        return "REQUEST_TIMEOUT_MS".to_string();
    }
    if ctx_lower.contains("database") || ctx_lower.contains("db") || ctx_lower.contains("query") {
        return "DB_TIMEOUT_MS".to_string();
    }
    if ctx_lower.contains("socket") {
        return "SOCKET_TIMEOUT_MS".to_string();
    }
    if ctx_lower.contains("retry") || ctx_lower.contains("backoff") {
        return "RETRY_DELAY_MS".to_string();
    }
    if ctx_lower.contains("poll") || ctx_lower.contains("interval") {
        return "POLL_INTERVAL_MS".to_string();
    }

    format!("TIMEOUT_{}MS", value)
}

pub struct HardcodedTimeoutDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
}

impl HardcodedTimeoutDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Analyze timeout context to determine risk level
    fn analyze_context(line: &str) -> (String, bool) {
        let line_lower = line.to_lowercase();

        // High-risk: Network/database operations
        if line_lower.contains("connect")
            || line_lower.contains("request")
            || line_lower.contains("http")
            || line_lower.contains("socket")
            || line_lower.contains("database")
            || line_lower.contains("query")
            || line_lower.contains("grpc")
            || line_lower.contains("rpc")
        {
            return ("Network/database operation".to_string(), true);
        }

        // Medium-risk: Read/write operations
        if line_lower.contains("read")
            || line_lower.contains("write")
            || line_lower.contains("file")
            || line_lower.contains("stream")
        {
            return ("I/O operation".to_string(), false);
        }

        // Low-risk: UI/animation
        if line_lower.contains("animation")
            || line_lower.contains("transition")
            || line_lower.contains("debounce")
            || line_lower.contains("throttle")
        {
            return ("UI/animation".to_string(), false);
        }

        ("General timeout".to_string(), false)
    }

    /// Count occurrences of the same timeout value
    fn count_occurrences(&self, files: &dyn crate::detectors::file_provider::FileProvider) -> HashMap<u64, usize> {
        let mut counts: HashMap<u64, usize> = HashMap::new();

        for path in files.files() {
            if let Some(content) = files.masked_content(path) {
                for line in content.lines() {
                    if let Some(caps) = timeout_pattern().captures(line) {
                        if let Some(val) = caps.get(2) {
                            if let Ok(v) = val.as_str().parse::<u64>() {
                                *counts.entry(v).or_default() += 1;
                            }
                        }
                    }
                }
            }
        }

        counts
    }

    /// Find containing function
    fn find_containing_function(
        graph: &dyn crate::graph::GraphQuery,
        file_path: &str,
        line: u32,
    ) -> Option<String> {
        graph
            .get_functions()
            .into_iter()
            .find(|f| f.file_path == file_path && f.line_start <= line && f.line_end >= line)
            .map(|f| f.name)
    }
}

impl Detector for HardcodedTimeoutDetector {
    fn name(&self) -> &'static str {
        "hardcoded-timeout"
    }
    fn description(&self) -> &'static str {
        "Detects hardcoded timeout values"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        // Count occurrences for context
        let occurrence_counts = self.count_occurrences(files);

        for path in files.files_with_extensions(&["py", "js", "ts", "java", "go", "rs", "rb", "jsx", "tsx"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            let path_str = path.to_string_lossy().to_string();

            // Skip test files and config files
            if crate::detectors::base::is_test_path(&path_str) || path_str.contains("config") {
                continue;
            }

            if let Some(content) = files.masked_content(path) {
                let lines: Vec<&str> = content.lines().collect();
                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    // Skip comments
                    let trimmed = line.trim();
                    if trimmed.starts_with("//") || trimmed.starts_with("#") {
                        continue;
                    }

                    if let Some(caps) = timeout_pattern().captures(line) {
                        if let (Some(keyword), Some(val)) = (caps.get(1), caps.get(2)) {
                            let _keyword_str = keyword.as_str();
                            let value: u64 = val.as_str().parse().unwrap_or(0);
                            let occurrences = occurrence_counts.get(&value).copied().unwrap_or(1);
                            let (context, is_network) = Self::analyze_context(line);
                            let containing_func =
                                Self::find_containing_function(graph, &path_str, (i + 1) as u32);

                            // Calculate severity
                            let severity = if is_network || occurrences > 3 {
                                Severity::Medium // Network or repeated timeout constants
                            } else {
                                Severity::Low
                            };

                            // Build context notes
                            let mut notes = Vec::new();
                            notes.push(format!("⏱️ Duration: {}", format_duration(value)));
                            notes.push(format!("📍 Context: {}", context));
                            if occurrences > 1 {
                                notes.push(format!("📊 Same value used {} times", occurrences));
                            }
                            if let Some(func) = containing_func {
                                notes.push(format!("📦 In function: `{}`", func));
                            }

                            let context_notes = format!("\n\n**Analysis:**\n{}", notes.join("\n"));

                            let const_name = suggest_constant_name(line, value);

                            let suggestion = if occurrences > 3 {
                                format!(
                                    "This timeout value appears {} times. Extract to a centralized config:\n\n\
                                     ```python\n\
                                     # config.py\n\
                                     {} = {}  # {}\n\
                                     \n\
                                     # usage\n\
                                     from config import {}\n\
                                     requests.get(url, timeout={}/1000)\n\
                                     ```",
                                    occurrences, const_name, value, format_duration(value),
                                    const_name, const_name
                                )
                            } else if is_network {
                                format!(
                                    "Network timeouts should be configurable:\n\n\
                                     ```python\n\
                                     import os\n\
                                     {} = int(os.environ.get('{}', '{}'))\n\
                                     ```",
                                    const_name, const_name, value
                                )
                            } else {
                                format!(
                                    "Extract to a named constant:\n\n\
                                     ```python\n\
                                     {} = {}  # {}\n\
                                     ```",
                                    const_name,
                                    value,
                                    format_duration(value)
                                )
                            };

                            findings.push(Finding {
                                id: String::new(),
                                detector: "HardcodedTimeoutDetector".to_string(),
                                severity,
                                title: format!("Hardcoded timeout: {}", format_duration(value)),
                                description: format!(
                                    "Magic timeout value `{}` makes configuration and tuning difficult.{}",
                                    value, context_notes
                                ),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some(suggestion),
                                estimated_effort: Some("5 minutes".to_string()),
                                category: Some("maintainability".to_string()),
                                cwe_id: None,
                                why_it_matters: Some(
                                    "Hardcoded timeouts are hard to find, tune, and change across environments. \
                                     Network timeouts especially need to be configurable based on deployment.".to_string()
                                ),
                                ..Default::default()
                            });
                        }
                    }
                }
            }
        }

        info!(
            "HardcodedTimeoutDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_hardcoded_timeout() {
        // Regex requires 4+ digit number
        let store = GraphStore::in_memory();
        let detector = HardcodedTimeoutDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("client.py", "import time\n\ndef wait_for_response():\n    time.sleep(30000)\n    return get_data()\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            !findings.is_empty(),
            "Should detect hardcoded timeout value. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_no_timeout() {
        let store = GraphStore::in_memory();
        let detector = HardcodedTimeoutDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("client.py", "import os\n\nTIMEOUT = int(os.environ.get('REQUEST_TIMEOUT', '5000'))\n\ndef fetch_data():\n    return request(url, timeout=TIMEOUT)\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag configurable timeout. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
