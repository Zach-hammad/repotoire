//! Magic Numbers Detector
//!
//! Graph-enhanced detection of unexplained numeric literals:
//! - Tracks number usage across the codebase
//! - Increases severity for numbers used in multiple files
//! - Reduces severity for numbers in config/constants files
//! - Uses graph context to skip test & infrastructure functions
//! - Understands named constants, arithmetic idioms, and bit operations
//! - Suggests appropriate constant names based on context

use crate::detectors::base::Detector;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::LazyLock;
use tracing::info;

/// Matches 2+ digit integers.  We post-filter for floats/hex instead of
/// using look-around (which requires `fancy-regex`).
static NUMBER_PATTERN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\b(\d{2,})\b").expect("valid regex"));

/// Matches a named constant declaration pattern across languages:
///   const FOO = 42;  /  static BAR: i32 = 42;  /  final int BAZ = 42;
///   let MAX_RETRIES = 3;  /  UPPER_NAME = 42  (Python module-level)
static NAMED_CONST_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?x)
        (?:
            \b(?:const|static|final|readonly)\b  # explicit constant keyword
            .*=                                   # ... followed by assignment
        )
        |
        (?:
            \b(?:let|var)\s+                     # let/var binding
            [A-Z][A-Z0-9_]+                      # with UPPER_CASE name
            \s*(?::\s*\S+\s*)?=                  # optional type annotation, then =
        )
        |
        (?:
            ^[A-Z][A-Z0-9_]+\s*=                 # module-level UPPER_CASE = value (Python)
        )
    ",
    )
    .expect("valid regex")
});

/// Common arithmetic idioms where a magic number is expected/acceptable.
static ARITHMETIC_IDIOM: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?x)
        [%*/+-]\s*[012]\b       # x % 2, x * 2, x + 1, x - 1, x / 2
        | \b[012]\s*[%*/+-]     # 2 * x, 1 + x, etc.
        | \b(?:len|length|size|count)\s*-\s*1\b  # off-by-one: len - 1
    ",
    )
    .expect("valid regex")
});

/// Suggest a constant name based on the number and context
fn suggest_constant_name(num: i64, context_line: &str) -> String {
    let line_lower = context_line.to_lowercase();

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
    if (200..600).contains(&num)
        && (line_lower.contains("status") || line_lower.contains("http"))
    {
        return format!("HTTP_STATUS_{}", num);
    }

    format!("MAGIC_NUMBER_{}", num)
}

pub struct MagicNumbersDetector {
    #[allow(dead_code)]
    repository_path: PathBuf,
    max_findings: usize,
    acceptable: HashSet<i64>,
}

impl MagicNumbersDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        // Generous acceptable set — these numbers appear frequently in correct
        // code and flagging them generates noise without actionable value.
        // We intentionally accept all integers 0..99, as numbers below 100 are
        // overwhelmingly used as array indices, loop bounds, small thresholds,
        // percentage values, or configuration constants.  Flagging them creates
        // far more FPs than TPs.
        let mut acceptable: HashSet<i64> = (0..=99).collect();
        // Add common round numbers and powers of 2
        for &n in &[
            // Round multiples of 10/100
            100, 110, 120, 150, 200, 250, 300, 350, 400, 450, 500, 600, 700, 800, 900,
            1000, 1100, 1200, 1500, 2000, 2500, 3000, 5000, 10000, 20000, 50000, 100000,
            // Powers of 2 and related
            128, 255, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65535, 65536,
            // HTTP status codes
            200, 201, 204, 301, 302, 304, 400, 401, 403, 404, 405, 409,
            422, 429, 500, 502, 503, 504,
            // Common ports
            443, 3000, 3306, 5432, 5672, 6379, 8000, 8080, 8443, 9090, 9200, 27017,
            // Angles
            180, 270, 360, 365,
            // Time constants
            3600, 86400, 604800,
            // File permissions
            644, 755, 777,
        ] {
            acceptable.insert(n);
        }
        Self {
            repository_path: repository_path.into(),
            max_findings: 100,
            acceptable,
        }
    }

    /// Check if path is a config/constants file (skip entirely).
    fn is_constants_file(path: &str) -> bool {
        let path_lower = path.to_lowercase();
        path_lower.contains("const")
            || path_lower.contains("config")
            || path_lower.contains("settings")
            || path_lower.contains("defines")
            || path_lower.contains("defaults")
            || path_lower.contains("threshold")
            || path_lower.ends_with(".env")
            || path_lower.ends_with("values.yaml")
    }

    /// Check if number is on a line that is a named constant declaration.
    fn is_named_constant_line(trimmed: &str) -> bool {
        NAMED_CONST_PATTERN.is_match(trimmed)
    }

    /// Check if a number appears in a float literal context (e.g. `3.14`, `1e10`).
    fn is_part_of_float(line: &str, match_start: usize, match_end: usize) -> bool {
        if match_start > 0 {
            let prev = line.as_bytes()[match_start - 1];
            // "3.14" — the "14" part
            if prev == b'.' {
                if match_start >= 2 && line.as_bytes()[match_start - 2].is_ascii_digit() {
                    return true;
                }
            }
            // "1e10" — the "10" part
            if prev == b'e' || prev == b'E' {
                return true;
            }
        }
        if match_end < line.len() {
            let next = line.as_bytes()[match_end];
            // "42.0" — the "42" part
            if next == b'.' && match_end + 1 < line.len() && line.as_bytes()[match_end + 1].is_ascii_digit() {
                return true;
            }
            // "42e3" — the "42" part
            if next == b'e' || next == b'E' {
                return true;
            }
        }
        false
    }

    /// Check if the match is inside a hex literal like `0xFF00`.
    fn is_part_of_hex(line: &str, match_start: usize) -> bool {
        if match_start >= 2 {
            let before = &line[..match_start];
            if before.ends_with("0x") || before.ends_with("0X") {
                return true;
            }
        }
        false
    }

    /// Classify whether the line context makes this number acceptable.
    ///
    /// Uses precise, targeted checks rather than broad substring matching
    /// to avoid filtering out real magic numbers on lines that happen to
    /// contain common identifiers.
    fn is_acceptable_context(line: &str, _num: i64) -> bool {
        let trimmed = line.trim();

        // ── Named constant declarations ─────────────────────────────────
        if Self::is_named_constant_line(trimmed) {
            return true;
        }

        // ── Bit operations (shifts, hex literals) ─────────────────────────
        if trimmed.contains("<<") || trimmed.contains(">>") || trimmed.contains("0x") || trimmed.contains("0X") {
            return true;
        }

        // ── Enum/variant values: ALL_CAPS = number ──────────────────────
        if trimmed.contains(" = ") {
            let lhs = trimmed.split('=').next().unwrap_or("").trim();
            if !lhs.is_empty()
                && lhs.bytes().all(|c| c.is_ascii_uppercase() || c == b'_' || c == b' ')
            {
                return true;
            }
        }

        // ── Switch/match arms ───────────────────────────────────────────
        if trimmed.starts_with("case ") || trimmed.starts_with("case(") {
            return true;
        }
        // Rust match arm: `42 => ...` or `42 | 43 => ...`
        if trimmed.contains("=>") {
            let arm = trimmed.split("=>").next().unwrap_or("");
            if arm.trim().chars().all(|c| c.is_ascii_digit() || c == ' ' || c == '|' || c == '_') {
                return true;
            }
        }

        // ── Arithmetic idioms: % 2, + 1, - 1, * 2, len-1 ──────────────
        if ARITHMETIC_IDIOM.is_match(trimmed) {
            return true;
        }

        // ── Return value (exit codes, error codes) ──────────────────────
        if trimmed.starts_with("return ") && trimmed.len() < 20 {
            return true;
        }

        // ── String formatting / interpolation ───────────────────────────
        if trimmed.contains("printf") || trimmed.contains("format!") || trimmed.contains("f\"") {
            return true;
        }

        // ── Range/slice expressions: 0..42, [:42], [1:42] ──────────────
        if trimmed.contains("..") {
            return true;
        }

        // ── Lines dominated by a string literal ─────────────────────────
        if is_string_literal_line(trimmed) {
            return true;
        }

        // ── Single lowercase pass for targeted context keywords ─────────
        let line_lower = trimmed.to_ascii_lowercase();

        // Version literals
        if line_lower.contains("version") {
            return true;
        }
        // Color/CSS values
        if line_lower.contains("color") || line_lower.contains("rgb(") || line_lower.contains("opacity") {
            return true;
        }
        // Character codes / Unicode
        if line_lower.contains("codepoint") || line_lower.contains("charcode") || line_lower.contains("\\u") {
            return true;
        }
        // Epoch/timestamp
        if line_lower.contains("epoch") || line_lower.contains("timestamp") {
            return true;
        }
        // Assertions in any context (test-like even in production)
        if line_lower.contains("assert") {
            return true;
        }
        // Explicit timeout/delay/interval naming
        if line_lower.contains("timeout") || line_lower.contains("_delay") || line_lower.contains("_interval") {
            return true;
        }
        // CWE identifiers (e.g., CWE-561, "cwe_id")
        if line_lower.contains("cwe") {
            return true;
        }
        // CSS properties (font-weight, font-size, line-height, max-width, etc.)
        if line_lower.contains("font-") || line_lower.contains("line-height") || line_lower.contains("max-width") {
            return true;
        }
        // Year constants (1900-2100)
        if _num >= 1900 && _num <= 2100 {
            return true;
        }
        // Date strings or patterns
        if line_lower.contains("date") || line_lower.contains("year") {
            return true;
        }

        false
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
        &[
            "py", "js", "ts", "jsx", "tsx", "rb", "java", "go", "rs", "c", "cpp", "cs",
        ]
    }

    fn detect(
        &self,
        ctx: &crate::detectors::analysis_context::AnalysisContext,
    ) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let files = &ctx.as_file_provider();
        let mut findings = vec![];

        let mut occurrences: HashMap<i64, Vec<(std::path::PathBuf, u32)>> = HashMap::new();

        struct NumberMatch {
            path: std::path::PathBuf,
            line_num: u32,
            number: i64,
            line_text: String,
        }
        let mut candidates: Vec<NumberMatch> = Vec::new();

        for path in files.files_with_extensions(&[
            "py", "js", "ts", "jsx", "tsx", "rs", "go", "java", "cs", "cpp", "c", "rb", "php",
        ]) {
            let path_str = path.to_string_lossy();

            // Skip test files entirely
            {
                let p = path_str.as_ref();
                if p.contains("/tests/")
                    || p.contains("/test_")
                    || p.contains("_test.")
                    || p.contains(".test.")
                    || p.contains("/spec/")
                    || p.contains(".spec.")
                    || p.contains("/test/")
                    || p.contains("_spec.")
                    || p.contains("/fixtures/")
                    || p.contains("/testdata/")
                    || p.contains("/testing/")
                {
                    continue;
                }
            }

            let is_constants = Self::is_constants_file(&path_str);
            let is_skipped = path_str.contains("/scripts/")
                || path_str.contains("/bench/")
                || path_str.contains("/benchmark")
                || path_str.contains("/tools/")
                || path_str.contains("/migrations/")
                || path_str.contains("/generated/")
                || path_str.contains("/proto/");

            if is_constants || is_skipped {
                continue;
            }

            if let Some(content) = files.content(path) {
                // Fast exit: skip files with no 2+ consecutive digit sequences
                if !content
                    .as_bytes()
                    .windows(2)
                    .any(|w| w[0].is_ascii_digit() && w[1].is_ascii_digit())
                {
                    continue;
                }
                let lines: Vec<&str> = content.lines().collect();
                for (line_num, line) in lines.iter().enumerate() {
                    let prev_line = if line_num > 0 {
                        Some(lines[line_num - 1])
                    } else {
                        None
                    };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    let trimmed = line.trim();

                    // Skip empty and very short lines
                    if trimmed.len() < 3 {
                        continue;
                    }

                    // Skip comment lines
                    if trimmed.starts_with("//")
                        || trimmed.starts_with('#')
                        || trimmed.starts_with('*')
                        || trimmed.starts_with("/*")
                        || trimmed.starts_with("--")
                    {
                        continue;
                    }

                    // Fast check: skip lines with no digits before running regex
                    if !trimmed.as_bytes().iter().any(|b| b.is_ascii_digit()) {
                        continue;
                    }

                    for cap in NUMBER_PATTERN.captures_iter(line) {
                        if let Some(m) = cap.get(1) {
                            let num_str = m.as_str();
                            if let Ok(num) = num_str.parse::<i64>() {
                                if self.acceptable.contains(&num) {
                                    continue;
                                }

                                // Skip numbers that are part of float literals
                                if Self::is_part_of_float(line, m.start(), m.end()) {
                                    continue;
                                }

                                // Skip numbers that are part of hex literals
                                if Self::is_part_of_hex(line, m.start()) {
                                    continue;
                                }

                                // Always record occurrence for cross-file analysis
                                occurrences
                                    .entry(num)
                                    .or_default()
                                    .push((path.to_path_buf(), (line_num + 1) as u32));

                                // Only track as finding candidate if passes all filters
                                if !Self::is_acceptable_context(line, num) {
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

            // Graph-based context: look up containing function
            let path_str = m.path.to_string_lossy();
            let containing_func = graph.find_function_at(&path_str, m.line_num);

            if let Some(ref func) = containing_func {
                let i = graph.interner();
                let qn = func.qn(i);

                // Skip magic numbers inside test functions entirely
                if ctx.is_test_function(qn) {
                    continue;
                }
            }

            let in_multiple_files = multi_file_numbers.contains(&m.number);
            let total_occurrences = occurrences.get(&m.number).map(|v| v.len()).unwrap_or(1);

            let mut severity = if in_multiple_files {
                Severity::Medium
            } else {
                Severity::Low
            };

            // If containing function is infrastructure, cap severity at Info
            if let Some(ref func) = containing_func {
                let i = graph.interner();
                let qn = func.qn(i);
                if ctx.is_infrastructure(qn) {
                    severity = Severity::Info;
                }
            }

            let mut notes = Vec::new();
            if in_multiple_files {
                let unique_files: HashSet<_> = occurrences
                    .get(&m.number)
                    .map(|v| v.iter().map(|(p, _)| p).collect())
                    .unwrap_or_default();
                notes.push(format!("Used in {} different files", unique_files.len()));
            }
            if total_occurrences > 1 {
                notes.push(format!("Appears {} times in codebase", total_occurrences));
            }

            let context_notes = if notes.is_empty() {
                String::new()
            } else {
                format!("\n\n**Analysis:**\n{}", notes.join("\n"))
            };

            let suggested_name = suggest_constant_name(m.number, &m.line_text);

            // High confidence: the detector has extensive contextual filtering
            // (named constants, bit ops, string literals, match arms, assertions,
            // ranges, etc.). Findings that survive all those checks are genuine.
            let confidence = if in_multiple_files { 0.90 } else { 0.80 };

            findings.push(Finding {
                id: String::new(),
                detector: "MagicNumbersDetector".to_string(),
                severity,
                confidence: Some(confidence),
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
                     and make the code harder to understand."
                        .to_string()
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

/// Check if a line is primarily a string literal (the number is likely part
/// of a message or label, not a magic number).
fn is_string_literal_line(trimmed: &str) -> bool {
    let double_quotes = trimmed.bytes().filter(|&b| b == b'"').count();
    let single_quotes = trimmed.bytes().filter(|&b| b == b'\'').count();

    if double_quotes >= 2 || single_quotes >= 2 {
        if let (Some(f), Some(l)) = (trimmed.find('"'), trimmed.rfind('"')) {
            if l > f {
                let quoted_len = l - f;
                if quoted_len as f64 / trimmed.len() as f64 > 0.5 {
                    // Check if there's a number outside the quoted region
                    let before_str = &trimmed[..f];
                    let after_str = if l + 1 < trimmed.len() { &trimmed[l + 1..] } else { "" };
                    let has_number_outside = (before_str.bytes().any(|b| b.is_ascii_digit())
                        && !before_str.trim().is_empty())
                        || after_str.bytes().any(|b| b.is_ascii_digit());
                    if !has_number_outside {
                        return true;
                    }
                }
            }
        }
    }
    false
}


impl super::RegisteredDetector for MagicNumbersDetector {
    fn create(init: &super::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::new(init.repo_path))
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
        // 9999 is a 4-digit number NOT in the acceptable set.
        let ctx =
            crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
                &store,
                vec![(
                    "logic.py",
                    "def check(x):\n    if x > 9999:\n        return True\n",
                )],
            );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect magic number 9999"
        );
        assert!(
            findings.iter().any(|f| f.title.contains("9999")),
            "Finding should mention 9999. Titles: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_acceptable_numbers() {
        let store = GraphStore::in_memory();
        let detector = MagicNumbersDetector::new("/mock/repo");
        let ctx =
            crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
                &store,
                vec![(
                    "clean.py",
                    "def check(x):\n    if x > 100:\n        return True\n",
                )],
            );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag acceptable number 100, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_skips_named_constants() {
        let store = GraphStore::in_memory();
        let detector = MagicNumbersDetector::new("/mock/repo");
        let ctx =
            crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
                &store,
                vec![(
                    "constants.rs",
                    "const MAX_RETRIES: u32 = 42;\nstatic TIMEOUT: u64 = 3000;\nlet MAX_ITEMS = 99;\n",
                )],
            );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag named constants, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_skips_bit_operations() {
        let store = GraphStore::in_memory();
        let detector = MagicNumbersDetector::new("/mock/repo");
        let ctx =
            crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
                &store,
                vec![(
                    "bits.rs",
                    "fn mask(x: u32) -> u32 {\n    x << 16\n}\nfn flag(x: u32) -> u32 {\n    x >> 24\n}\n",
                )],
            );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag bit operations, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_skips_match_arms() {
        let store = GraphStore::in_memory();
        let detector = MagicNumbersDetector::new("/mock/repo");
        let ctx =
            crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
                &store,
                vec![(
                    "match.rs",
                    "fn dispatch(code: u32) {\n    match code {\n        42 => println!(\"found\"),\n        _ => {}\n    }\n}\n",
                )],
            );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag match arms, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_skips_float_literals() {
        let store = GraphStore::in_memory();
        let detector = MagicNumbersDetector::new("/mock/repo");
        let ctx =
            crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
                &store,
                vec![(
                    "math.py",
                    "def area(r):\n    return 3.14 * r * r\n",
                )],
            );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag numbers in float literals, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_skips_enum_values() {
        let store = GraphStore::in_memory();
        let detector = MagicNumbersDetector::new("/mock/repo");
        let ctx =
            crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
                &store,
                vec![(
                    "enums.py",
                    "STATUS_OK = 42\nERROR_CODE = 99\n",
                )],
            );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag UPPER_CASE assignments, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_skips_assertions() {
        let store = GraphStore::in_memory();
        let detector = MagicNumbersDetector::new("/mock/repo");
        let ctx =
            crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
                &store,
                vec![(
                    "check.py",
                    "def validate():\n    assert len(items) == 42\n",
                )],
            );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag assertions, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_skips_range_expressions() {
        let store = GraphStore::in_memory();
        let detector = MagicNumbersDetector::new("/mock/repo");
        let ctx =
            crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
                &store,
                vec![(
                    "range.rs",
                    "fn foo() {\n    for i in 0..42 {\n        println!(\"{}\", i);\n    }\n}\n",
                )],
            );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag range expressions, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_detects_real_magic_number_in_business_logic() {
        let store = GraphStore::in_memory();
        let detector = MagicNumbersDetector::new("/mock/repo");
        let ctx =
            crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
                &store,
                vec![(
                    "billing.py",
                    "def apply_discount(total):\n    if total > 9999:\n        total = total * 85 / 100\n    return total\n",
                )],
            );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect magic numbers 9999 and/or 85 in business logic"
        );
    }

    #[test]
    fn test_skips_test_file_paths() {
        let store = GraphStore::in_memory();
        let detector = MagicNumbersDetector::new("/mock/repo");
        let ctx =
            crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
                &store,
                vec![
                    ("tests/test_billing.py", "def test_apply():\n    if total > 9999:\n        pass\n"),
                    ("src/billing.test.ts", "it('applies', () => {\n    if (total > 9999) {}\n});\n"),
                ],
            );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag numbers in test files, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
