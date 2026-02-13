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
use std::sync::OnceLock;
use tracing::info;
use uuid::Uuid;

static NUMBER_PATTERN: OnceLock<Regex> = OnceLock::new();

fn get_pattern() -> &'static Regex {
    NUMBER_PATTERN.get_or_init(|| Regex::new(r"\b(\d{2,})\b").unwrap())
}

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
    
    /// Check if number is used in a context where it's acceptable
    fn is_acceptable_context(line: &str, num: i64) -> bool {
        let line_lower = line.to_lowercase();
        
        // Array/tuple indices and sizes
        if line.contains('[') && line.contains(']') {
            return true;
        }
        
        // Bit operations (masks, shifts)
        if line.contains("<<") || line.contains(">>") || line.contains("0x") || line.contains("& ") || line.contains("| ") {
            return true;
        }
        
        // String formatting (precision, padding)
        if line.contains('%') || line.contains("format") || line.contains("printf") || line.contains("pad") {
            return true;
        }
        
        // CSS/UI constants (colors, dimensions, weights)
        if line_lower.contains("color") || line_lower.contains("rgb") || line_lower.contains("px") 
            || line_lower.contains("width") || line_lower.contains("height") 
            || line_lower.contains("margin") || line_lower.contains("padding")
            || line_lower.contains("font") || line_lower.contains("weight")
            || line_lower.contains("size") || line_lower.contains("opacity")
        {
            return true;
        }
        
        // Version numbers
        if line_lower.contains("version") || line_lower.contains("major") || line_lower.contains("minor") {
            return true;
        }
        
        // Mathematical constants (angles, percentages)
        if num == 360 || num == 180 || num == 90 || num == 45 {
            return true;
        }
        
        // Common test data assertions
        if line_lower.contains("expect") || line_lower.contains("assert") || line_lower.contains("should") {
            return true;
        }
        
        // Enum-like values (ALL_CAPS_NAME = number)
        if line.contains(" = ") && line.chars().take_while(|c| *c != '=').all(|c| c.is_uppercase() || c == '_' || c.is_whitespace()) {
            return true;
        }
        
        // Object/map literals with numeric values (key: value pattern)
        if line.contains(": ") && (line.contains("{") || line.contains(",")) {
            return true;
        }
        
        // Unicode codepoints and character codes
        if line_lower.contains("codepoint") || line_lower.contains("charcode") || line_lower.contains("\\u") {
            return true;
        }
        
        // Error codes and status codes (named)
        if line_lower.contains("code") || line_lower.contains("status") || line_lower.contains("errno") {
            return true;
        }
        
        // ASCII/character values (common: 32=space, 48=0, 65=A, 97=a)
        if (32..=127).contains(&num) && (line_lower.contains("char") || line_lower.contains("ascii")) {
            return true;
        }
        
        // Lookup tables and mappings (common in compilers/parsers)
        if line_lower.contains("table") || line_lower.contains("lookup") || line_lower.contains("map") {
            return true;
        }
        
        // Time constants (milliseconds, seconds)
        if line_lower.contains("ms") || line_lower.contains("sec") || line_lower.contains("timeout") 
            || line_lower.contains("delay") || line_lower.contains("interval")
        {
            return true;
        }
        
        // Capacity/buffer sizes (named appropriately)
        if line_lower.contains("capacity") || line_lower.contains("buffer") || line_lower.contains("chunk") {
            return true;
        }
        
        // Flags in switch/case or if/else chains
        if line.trim().starts_with("case ") || line.contains("case ") {
            return true;
        }
        
        // Priority/weight/score constants
        if line_lower.contains("priority") || line_lower.contains("weight") || line_lower.contains("score") {
            return true;
        }
        
        // Dev constants (debug levels, limits)
        if line_lower.contains("debug") || line_lower.contains("dev") || line_lower.contains("limit") {
            return true;
        }
        
        false
    }

    /// First pass: count occurrences of each magic number across files
    fn count_occurrences(&self) -> HashMap<i64, Vec<(PathBuf, u32)>> {
        let mut occurrences: HashMap<i64, Vec<(PathBuf, u32)>> = HashMap::new();
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(
                ext,
                "py" | "js"
                    | "ts"
                    | "jsx"
                    | "tsx"
                    | "rs"
                    | "go"
                    | "java"
                    | "cs"
                    | "cpp"
                    | "c"
                    | "rb"
                    | "php"
            ) {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (line_num, line) in content.lines().enumerate() {
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

                    for cap in get_pattern().captures_iter(line) {
                        if let Some(m) = cap.get(1) {
                            if let Ok(num) = m.as_str().parse::<i64>() {
                                if !self.acceptable.contains(&num) {
                                    occurrences
                                        .entry(num)
                                        .or_default()
                                        .push((path.to_path_buf(), (line_num + 1) as u32));
                                }
                            }
                        }
                    }
                }
            }
        }

        occurrences
    }
}

impl Detector for MagicNumbersDetector {
    fn name(&self) -> &'static str {
        "magic-numbers"
    }
    fn description(&self) -> &'static str {
        "Detects unexplained numeric literals"
    }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        // First pass: count all occurrences
        let occurrences = self.count_occurrences();

        // Find numbers used in multiple files (definite refactor targets)
        let multi_file_numbers: HashSet<i64> = occurrences
            .iter()
            .filter(|(_, locs)| {
                let unique_files: HashSet<_> = locs.iter().map(|(p, _)| p).collect();
                unique_files.len() > 1
            })
            .map(|(num, _)| *num)
            .collect();

        // Second pass: create findings with context
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

            let path_str = path.to_string_lossy().to_string();
            let is_constants = Self::is_constants_file(&path_str);
            
            // Skip scripts/benchmarks (developer tools with test data)
            if path_str.contains("/scripts/") 
                || path_str.contains("/bench/")
                || path_str.contains("/benchmark")
                || path_str.contains("/tools/")
            {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(
                ext,
                "py" | "js"
                    | "ts"
                    | "jsx"
                    | "tsx"
                    | "rs"
                    | "go"
                    | "java"
                    | "cs"
                    | "cpp"
                    | "c"
                    | "rb"
                    | "php"
            ) {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (line_num, line) in content.lines().enumerate() {
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

                    for cap in get_pattern().captures_iter(line) {
                        if let Some(m) = cap.get(1) {
                            if let Ok(num) = m.as_str().parse::<i64>() {
                                if self.acceptable.contains(&num) {
                                    continue;
                                }

                                // Skip if in acceptable context (arrays, bit ops, etc.)
                                if Self::is_acceptable_context(line, num) {
                                    continue;
                                }

                                // Skip if in constants file
                                if is_constants {
                                    continue;
                                }

                                // Calculate severity based on usage
                                let in_multiple_files = multi_file_numbers.contains(&num);
                                let total_occurrences =
                                    occurrences.get(&num).map(|v| v.len()).unwrap_or(1);

                                let severity = if in_multiple_files {
                                    Severity::Medium // Used across files = definite refactor target
                                } else if total_occurrences > 3 {
                                    Severity::Low // Repeated in same file
                                } else {
                                    Severity::Low // Single use
                                };

                                // Build description with context
                                let mut notes = Vec::new();
                                if in_multiple_files {
                                    let unique_files: HashSet<_> = occurrences
                                        .get(&num)
                                        .map(|v| v.iter().map(|(p, _)| p).collect())
                                        .unwrap_or_default();
                                    notes.push(format!(
                                        "⚠️ Used in {} different files",
                                        unique_files.len()
                                    ));
                                }
                                if total_occurrences > 1 {
                                    notes.push(format!(
                                        "📊 Appears {} times in codebase",
                                        total_occurrences
                                    ));
                                }

                                let context_notes = if notes.is_empty() {
                                    String::new()
                                } else {
                                    format!("\n\n**Analysis:**\n{}", notes.join("\n"))
                                };

                                let suggested_name = suggest_constant_name(num, line);

                                findings.push(Finding {
                                    id: Uuid::new_v4().to_string(),
                                    detector: "MagicNumbersDetector".to_string(),
                                    severity,
                                    title: format!("Magic number: {}", num),
                                    description: format!(
                                        "Number {} appears without explanation.{}",
                                        num, context_notes
                                    ),
                                    affected_files: vec![path.to_path_buf()],
                                    line_start: Some((line_num + 1) as u32),
                                    line_end: Some((line_num + 1) as u32),
                                    suggested_fix: Some(format!(
                                        "Extract into a named constant:\n```\nconst {} = {};\n```",
                                        suggested_name, num
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
                                break; // Only one finding per line
                            }
                        }
                    }
                }
            }
        }

        info!(
            "MagicNumbersDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
