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
use std::sync::LazyLock;
use tracing::info;

static TIMEOUT_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?i)(timeout|sleep|delay|wait|setTimeout|setInterval|read_timeout|write_timeout|connect_timeout)\s*[\(=:]\s*(\d{4,})").expect("valid regex")
    });

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

}

impl Detector for HardcodedTimeoutDetector {
    fn name(&self) -> &'static str {
        "hardcoded-timeout"
    }
    fn description(&self) -> &'static str {
        "Detects hardcoded timeout values"
    }

    fn requires_graph(&self) -> bool {
        false
    }

    fn file_extensions(&self) -> &'static [&'static str] {
        &["py", "js", "ts", "jsx", "tsx", "java", "go", "rs"]
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let mut occurrence_counts: HashMap<u64, usize> = HashMap::new();

        // Single pass: collect matches AND count occurrences
        struct TimeoutMatch {
            path: std::path::PathBuf,
            line_num: u32,
            value: u64,
            line_text: String,
        }
        let mut matches: Vec<TimeoutMatch> = Vec::new();

        for path in files.files_with_extensions(&["py", "js", "ts", "java", "go", "rs", "rb", "jsx", "tsx"]) {
            let path_str = path.to_string_lossy();

            // Skip test files and config files
            if crate::detectors::base::is_test_path(&path_str) || path_str.contains("config") {
                continue;
            }

            // Cheap pre-filter: skip files without timeout-related keywords
            // to avoid expensive masked_content() tree-sitter parsing
            let raw = match files.content(path) {
                Some(c) => c,
                None => continue,
            };
            let raw_lower = raw.to_ascii_lowercase();
            if !raw_lower.contains("timeout") && !raw_lower.contains("sleep")
                && !raw_lower.contains("delay") && !raw_lower.contains("wait")
                && !raw_lower.contains("setinterval") && !raw_lower.contains("settimeout")
            {
                continue;
            }

            if let Some(content) = files.masked_content(path) {
                let lines: Vec<&str> = content.lines().collect();
                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    let trimmed = line.trim();
                    if trimmed.starts_with("//") || trimmed.starts_with("#") {
                        continue;
                    }

                    if let Some(caps) = TIMEOUT_PATTERN.captures(line) {
                        if let (Some(_keyword), Some(val)) = (caps.get(1), caps.get(2)) {
                            let value: u64 = val.as_str().parse().unwrap_or(0);
                            *occurrence_counts.entry(value).or_default() += 1;
                            matches.push(TimeoutMatch {
                                path: path.to_path_buf(),
                                line_num: (i + 1) as u32,
                                value,
                                line_text: line.to_string(),
                            });
                        }
                    }
                }
            }
        }

        // Generate findings using accumulated counts
        for m in &matches {
            if findings.len() >= self.max_findings {
                break;
            }
            let occurrences = occurrence_counts.get(&m.value).copied().unwrap_or(1);
            let (context, is_network) = Self::analyze_context(&m.line_text);
            let path_str = m.path.to_string_lossy();
            let containing_func =
                graph.find_function_at(&path_str, m.line_num).map(|f| f.node_name(crate::graph::interner::global_interner()).to_string());

            let severity = if is_network || occurrences > 3 {
                Severity::Medium
            } else {
                Severity::Low
            };

            let mut notes = Vec::new();
            notes.push(format!("⏱️ Duration: {}", format_duration(m.value)));
            notes.push(format!("📍 Context: {}", context));
            if occurrences > 1 {
                notes.push(format!("📊 Same value used {} times", occurrences));
            }
            if let Some(func) = containing_func {
                notes.push(format!("📦 In function: `{}`", func));
            }

            let context_notes = format!("\n\n**Analysis:**\n{}", notes.join("\n"));
            let const_name = suggest_constant_name(&m.line_text, m.value);

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
                    occurrences, const_name, m.value, format_duration(m.value),
                    const_name, const_name
                )
            } else if is_network {
                format!(
                    "Network timeouts should be configurable:\n\n\
                     ```python\n\
                     import os\n\
                     {} = int(os.environ.get('{}', '{}'))\n\
                     ```",
                    const_name, const_name, m.value
                )
            } else {
                format!(
                    "Extract to a named constant:\n\n\
                     ```python\n\
                     {} = {}  # {}\n\
                     ```",
                    const_name, m.value, format_duration(m.value)
                )
            };

            findings.push(Finding {
                id: String::new(),
                detector: "HardcodedTimeoutDetector".to_string(),
                severity,
                title: format!("Hardcoded timeout: {}", format_duration(m.value)),
                description: format!(
                    "Magic timeout value `{}` makes configuration and tuning difficult.{}",
                    m.value, context_notes
                ),
                affected_files: vec![m.path.clone()],
                line_start: Some(m.line_num),
                line_end: Some(m.line_num),
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
        let findings = detector.detect(&store, &files).expect("detection should succeed");
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
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag configurable timeout. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
