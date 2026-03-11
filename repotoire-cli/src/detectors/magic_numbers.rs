//! Magic Numbers Detector
//!
//! Graph-enhanced detection of unexplained numeric literals:
//! - Tracks number usage across the codebase
//! - Increases severity for numbers used in multiple files
//! - Reduces severity for numbers in config/constants files
//! - Suggests appropriate constant names based on context

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::LazyLock;
use tracing::info;

static NUMBER_PATTERN: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b(\d{2,})\b").expect("valid regex"));

/// Suggest a constant name based on the number and context
fn suggest_constant_name(num: i64, context_line: &str) -> String {
    let line_lower = context_line.to_lowercase();

    // Common patterns
    if num == 3600 || line_lower.contains("hour") {
        return "SECONDS_PER_HOUR".to_string();
    }
    if num == 86400 || line_lower.contains("day") {
        return "SECONDS_PER_DAY".to_string();
    }
    if num == 604800 || line_lower.contains("week") {
        return "SECONDS_PER_WEEK".to_string();
    }
    if line_lower.contains("timeout") || line_lower.contains("delay") {
        return format!("TIMEOUT_MS_{}", num);
    }
    if line_lower.contains("port") {
        return format!("PORT_{}", num);
    }
    if line_lower.contains("retry") || line_lower.contains("attempt") {
        return "MAX_RETRIES".to_string();
    }
    if line_lower.contains("size") || line_lower.contains("limit") || line_lower.contains("max") {
        return format!("MAX_SIZE_{}", num);
    }
    if line_lower.contains("width") || line_lower.contains("height") {
        return format!("DIMENSION_{}", num);
    }
    if (200..600).contains(&num) && (line_lower.contains("status") || line_lower.contains("http")) {
        return format!("HTTP_STATUS_{}", num);
    }

    format!("MAGIC_NUMBER_{}", num)
}

pub struct MagicNumbersDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
    acceptable: HashSet<i64>,
}

impl MagicNumbersDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        // Common acceptable numbers
        let acceptable: HashSet<i64> = [
            0, 1, 2, 3, 4, 5, 10, 100, 1000, 60, 24, 365, 360, 180, 90, // Time/angles
            255, 256, 512, 1024, 2048, 4096, // Powers of 2
            200, 201, 204, 301, 302, 304, // HTTP success/redirect
            400, 401, 403, 404, 500, 502, 503, // HTTP errors
        ]
        .into_iter()
        .collect();
        Self {
            repository_path: repository_path.into(),
            max_findings: 100,
            acceptable,
        }
    }

    /// Check if path is a config/constants file (reduces severity to Low)
    fn is_constants_file(path: &str) -> bool {
        let path_lower = path.to_lowercase();
        path_lower.contains("const")
            || path_lower.contains("config")
            || path_lower.contains("settings")
            || path_lower.contains("defines")
            || path_lower.ends_with(".env")
            || path_lower.ends_with("values.yaml")
    }

    /// Check if number is used in a context where it's acceptable.
    /// Case-sensitive checks first (no allocation), then one lowercase pass.
    fn is_acceptable_context(line: &str, num: i64) -> bool {
        // --- Case-sensitive fast checks (no allocation) ---

        // Array/tuple indices and sizes
        if line.contains('[') && line.contains(']') {
            return true;
        }

        // Bit operations (masks, shifts)
        if line.contains("<<")
            || line.contains(">>")
            || line.contains("0x")
            || line.contains("& ")
            || line.contains("| ")
        {
            return true;
        }

        // String formatting
        if line.contains('%') || line.contains("format") || line.contains("printf") {
            return true;
        }

        // Object/map literals with numeric values
        if line.contains(": ") && (line.contains('{') || line.contains(',')) {
            return true;
        }

        // Enum-like values (ALL_CAPS_NAME = number)
        if line.contains(" = ")
            && line
                .bytes()
                .take_while(|&c| c != b'=')
                .all(|c| c.is_ascii_uppercase() || c == b'_' || c == b' ')
        {
            return true;
        }

        // Flags in switch/case
        if line.contains("case ") {
            return true;
        }

        // Mathematical angle constants
        if num == 360 || num == 180 || num == 90 || num == 45 {
            return true;
        }

        // --- Single lowercase pass for remaining checks ---
        let line_lower = line.to_ascii_lowercase();

        // Combined keyword check: bail if any context keyword present
        // Using a single scan with short-circuit OR for the most common keywords first
        line_lower.contains("size")
            || line_lower.contains("code")
            || line_lower.contains("assert")
            || line_lower.contains("color")
            || line_lower.contains("version")
            || line_lower.contains("weight")
            || line_lower.contains("timeout")
            || line_lower.contains("map")
            || line_lower.contains("table")
            || line_lower.contains("debug")
            || line_lower.contains("limit")
            || line_lower.contains("batch")
            || line_lower.contains("buffer")
            || line_lower.contains("score")
            || line_lower.contains("status")
            || line_lower.contains("expect")
            || line_lower.contains("pad")
            || line_lower.contains("width")
            || line_lower.contains("height")
            || line_lower.contains("margin")
            || line_lower.contains("font")
            || line_lower.contains("opacity")
            || line_lower.contains("rgb")
            || line_lower.contains("px")
            || line_lower.contains("major")
            || line_lower.contains("minor")
            || line_lower.contains("should")
            || line_lower.contains("codepoint")
            || line_lower.contains("charcode")
            || line_lower.contains("\\u")
            || line_lower.contains("errno")
            || line_lower.contains("ascii")
            || line_lower.contains("char")
            || line_lower.contains("lookup")
            || line_lower.contains("ms")
            || line_lower.contains("sec")
            || line_lower.contains("delay")
            || line_lower.contains("interval")
            || line_lower.contains("capacity")
            || line_lower.contains("chunk")
            || line_lower.contains("priority")
            || line_lower.contains("dev")
            || line_lower.contains("epoch")
            || line_lower.contains("layer")
            || line_lower.contains("hidden")
            || line_lower.contains("embed")
            || line_lower.contains("dim")
    }

}

impl Detector for MagicNumbersDetector {
    fn name(&self) -> &'static str {
        "magic-numbers"
    }
    fn description(&self) -> &'static str {
        "Detects unexplained numeric literals"
    }

    fn requires_graph(&self) -> bool {
        false
    }

    fn file_extensions(&self) -> &'static [&'static str] {
        &["py", "js", "ts", "jsx", "tsx", "rb", "java", "go", "rs", "c", "cpp", "cs"]
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        // Single pass: collect occurrence locations AND finding candidates together
        let mut occurrences: HashMap<i64, Vec<(std::path::PathBuf, u32)>> = HashMap::new();

        struct NumberMatch {
            path: std::path::PathBuf,
            line_num: u32,
            number: i64,
            line_text: String,
        }
        let mut candidates: Vec<NumberMatch> = Vec::new();

        for path in files.files_with_extensions(&["py", "js", "ts", "jsx", "tsx", "rs", "go", "java", "cs", "cpp", "c", "rb", "php"]) {
            let path_str = path.to_string_lossy();
            let is_constants = Self::is_constants_file(&path_str);
            let is_skipped = path_str.contains("/scripts/")
                || path_str.contains("/bench/")
                || path_str.contains("/benchmark")
                || path_str.contains("/tools/");

            // Use raw content instead of masked_content() to avoid expensive
            // tree-sitter parsing.  The detector already skips comment lines
            // (starts with //, #, *) and has extensive acceptable-context
            // filtering that catches numbers in most string-literal contexts.
            if let Some(content) = files.content(path) {
                // Fast exit: skip files with no 2+ consecutive digit sequences
                if !content.as_bytes().windows(2).any(|w| w[0].is_ascii_digit() && w[1].is_ascii_digit()) {
                    continue;
                }
                let lines: Vec<&str> = content.lines().collect();
                for (line_num, line) in lines.iter().enumerate() {
                    let prev_line = if line_num > 0 { Some(lines[line_num - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    let trimmed = line.trim();
                    if trimmed.starts_with("//")
                        || trimmed.starts_with("#")
                        || trimmed.starts_with("*")
                    {
                        continue;
                    }
                    if trimmed.to_uppercase().contains("CONST") {
                        continue;
                    }

                    // Fast check: skip lines with no digits before running regex
                    if !trimmed.as_bytes().iter().any(|b| b.is_ascii_digit()) {
                        continue;
                    }

                    for cap in NUMBER_PATTERN.captures_iter(line) {
                        if let Some(m) = cap.get(1) {
                            if let Ok(num) = m.as_str().parse::<i64>() {
                                if self.acceptable.contains(&num) {
                                    continue;
                                }

                                // Always record occurrence for cross-file analysis
                                occurrences
                                    .entry(num)
                                    .or_default()
                                    .push((path.to_path_buf(), (line_num + 1) as u32));

                                // Only track as finding candidate if passes all filters
                                if !is_skipped
                                    && !is_constants
                                    && !Self::is_acceptable_context(line, num)
                                {
                                    candidates.push(NumberMatch {
                                        path: path.to_path_buf(),
                                        line_num: (line_num + 1) as u32,
                                        number: num,
                                        line_text: line.to_string(),
                                    });
                                }
                                break; // Only one finding per line
                            }
                        }
                    }
                }
            }
        }

        // Build multi-file set from accumulated occurrences
        let multi_file_numbers: HashSet<i64> = occurrences
            .iter()
            .filter(|(_, locs)| {
                let unique_files: HashSet<_> = locs.iter().map(|(p, _)| p).collect();
                unique_files.len() > 1
            })
            .map(|(num, _)| *num)
            .collect();

        // Generate findings from candidates
        for m in &candidates {
            if findings.len() >= self.max_findings {
                break;
            }

            let in_multiple_files = multi_file_numbers.contains(&m.number);
            let total_occurrences = occurrences.get(&m.number).map(|v| v.len()).unwrap_or(1);

            let severity = if in_multiple_files {
                Severity::Medium
            } else {
                Severity::Low
            };

            let mut notes = Vec::new();
            if in_multiple_files {
                let unique_files: HashSet<_> = occurrences
                    .get(&m.number)
                    .map(|v| v.iter().map(|(p, _)| p).collect())
                    .unwrap_or_default();
                notes.push(format!("⚠️ Used in {} different files", unique_files.len()));
            }
            if total_occurrences > 1 {
                notes.push(format!("📊 Appears {} times in codebase", total_occurrences));
            }

            let context_notes = if notes.is_empty() {
                String::new()
            } else {
                format!("\n\n**Analysis:**\n{}", notes.join("\n"))
            };

            let suggested_name = suggest_constant_name(m.number, &m.line_text);

            findings.push(Finding {
                id: String::new(),
                detector: "MagicNumbersDetector".to_string(),
                severity,
                title: format!("Magic number: {}", m.number),
                description: format!(
                    "Number {} appears without explanation.{}",
                    m.number, context_notes
                ),
                affected_files: vec![m.path.clone()],
                line_start: Some(m.line_num),
                line_end: Some(m.line_num),
                suggested_fix: Some(format!(
                    "Extract into a named constant:\n```\nconst {} = {};\n```",
                    suggested_name, m.number
                )),
                estimated_effort: Some(if in_multiple_files {
                    "15 minutes".to_string()
                } else {
                    "5 minutes".to_string()
                }),
                category: Some("readability".to_string()),
                cwe_id: None,
                why_it_matters: Some(if in_multiple_files {
                    "Magic numbers repeated across files are hard to update consistently \
                     and make the code harder to understand.".to_string()
                } else {
                    "Magic numbers make code harder to understand and maintain.".to_string()
                }),
                ..Default::default()
            });
        }

        info!(
            "MagicNumbersDetector found {} findings (graph-aware)",
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
    fn test_detects_magic_number() {
        let store = GraphStore::in_memory();
        let detector = MagicNumbersDetector::new("/mock/repo");
        // 42 is a 2+ digit number NOT in the acceptable set.
        // The line avoids all acceptable-context checks (no brackets, no format, etc.)
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("logic.py", "def check(x):\n    if x > 42:\n        return True\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect magic number 42"
        );
        assert!(
            findings.iter().any(|f| f.title.contains("42")),
            "Finding should mention 42. Titles: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_acceptable_numbers() {
        let store = GraphStore::in_memory();
        let detector = MagicNumbersDetector::new("/mock/repo");
        // 100 and 10 are in the acceptable set
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("clean.py", "def check(x):\n    if x > 100:\n        return True\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag acceptable number 100, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
