//! Broad Exception Detector
//!
//! Graph-enhanced detection of overly broad exception catching.
//! Uses graph to:
//! - Analyze what functions are called in the try block
//! - Suggest specific exceptions based on called functions
//! - Assess risk based on operation types

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;
use uuid::Uuid;

static BROAD_EXCEPT: OnceLock<Regex> = OnceLock::new();

fn broad_except() -> &'static Regex {
    BROAD_EXCEPT.get_or_init(|| Regex::new(r"(?i)(except\s*:|catch\s*\(\s*(Exception|Error|Throwable|BaseException|\w)\s*\)|catch\s*\{)").unwrap())
}

pub struct BroadExceptionDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl BroadExceptionDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Find try block and extract function calls
    fn analyze_try_block(lines: &[&str], catch_line: usize) -> HashSet<String> {
        let call_re = Regex::new(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(").unwrap();
        let mut calls = HashSet::new();

        // Find try start
        let mut try_start = None;
        for i in (0..catch_line).rev() {
            let trimmed = lines[i].trim();
            if trimmed.starts_with("try") {
                try_start = Some(i);
                break;
            }
        }

        if let Some(start) = try_start {
            for line in lines.get(start..catch_line).unwrap_or(&[]) {
                for cap in call_re.captures_iter(line) {
                    if let Some(m) = cap.get(1) {
                        let name = m.as_str();
                        if !["try", "if", "for", "while", "print"].contains(&name) {
                            calls.insert(name.to_string());
                        }
                    }
                }
            }
        }
        calls
    }

    /// Suggest specific exceptions based on operations
    fn suggest_exceptions(calls: &HashSet<String>, ext: &str) -> Vec<String> {
        let mut suggestions = Vec::new();

        let file_ops = ["open", "read", "write", "close"];
        let network_ops = ["fetch", "request", "get", "post", "connect", "send"];
        let parse_ops = ["parse", "json", "loads", "dumps", "decode", "encode"];
        let db_ops = ["query", "execute", "commit", "rollback", "cursor"];

        for call in calls {
            let call_lower = call.to_lowercase();

            if file_ops.iter().any(|op| call_lower.contains(op)) {
                match ext {
                    "py" => {
                        suggestions.push("IOError, FileNotFoundError, PermissionError".to_string())
                    }
                    "java" => suggestions.push("IOException, FileNotFoundException".to_string()),
                    "js" | "ts" => {
                        suggestions.push("Error (check error.code for ENOENT, EACCES)".to_string())
                    }
                    _ => suggestions.push("File I/O exceptions".to_string()),
                }
            }

            if network_ops.iter().any(|op| call_lower.contains(op)) {
                match ext {
                    "py" => suggestions.push(
                        "requests.RequestException, urllib.error.URLError, ConnectionError"
                            .to_string(),
                    ),
                    "java" => suggestions
                        .push("IOException, SocketException, HttpClientErrorException".to_string()),
                    "js" | "ts" => {
                        suggestions.push("TypeError (network errors), AbortError".to_string())
                    }
                    _ => suggestions.push("Network/HTTP exceptions".to_string()),
                }
            }

            if parse_ops.iter().any(|op| call_lower.contains(op)) {
                match ext {
                    "py" => suggestions
                        .push("json.JSONDecodeError, ValueError, UnicodeDecodeError".to_string()),
                    "java" => {
                        suggestions.push("JsonParseException, NumberFormatException".to_string())
                    }
                    "js" | "ts" => suggestions.push("SyntaxError (for JSON.parse)".to_string()),
                    _ => suggestions.push("Parse/decode exceptions".to_string()),
                }
            }

            if db_ops.iter().any(|op| call_lower.contains(op)) {
                match ext {
                    "py" => {
                        suggestions.push("sqlite3.Error, psycopg2.Error, pymysql.Error".to_string())
                    }
                    "java" => suggestions.push("SQLException, DataAccessException".to_string()),
                    _ => suggestions.push("Database exceptions".to_string()),
                }
            }
        }

        suggestions.sort();
        suggestions.dedup();
        suggestions
    }
}

impl Detector for BroadExceptionDetector {
    fn name(&self) -> &'static str {
        "broad-exception"
    }
    fn description(&self) -> &'static str {
        "Detects overly broad exception catching"
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
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
            if !matches!(ext, "py" | "js" | "ts" | "java" | "cs" | "rb" | "go") {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();

                for (i, line) in lines.iter().enumerate() {
                    if broad_except().is_match(line) {
                        // Skip if it's re-raising
                        let next_lines = lines
                            .get(i + 1..i + 4)
                            .map(|s| s.join(" "))
                            .unwrap_or_default();
                        if next_lines.contains("raise") || next_lines.contains("throw") {
                            continue;
                        }

                        // Analyze try block
                        let calls = Self::analyze_try_block(&lines, i);
                        let suggestions = Self::suggest_exceptions(&calls, ext);

                        // Build context
                        let mut notes = Vec::new();
                        if !calls.is_empty() {
                            let call_list: Vec<_> = calls.iter().take(5).cloned().collect();
                            notes.push(format!("📞 Try block calls: {}", call_list.join(", ")));
                        }

                        let context_notes = if notes.is_empty() {
                            String::new()
                        } else {
                            format!("\n\n**Analysis:**\n{}", notes.join("\n"))
                        };

                        // Calculate severity
                        let severity = if suggestions.len() >= 2 {
                            Severity::Medium // Multiple distinct operation types
                        } else {
                            Severity::Low
                        };

                        // Build specific suggestion
                        let suggestion = if !suggestions.is_empty() {
                            format!(
                                "Based on the operations in your try block, consider catching:\n{}\n\n\
                                 Example:\n\
                                 ```python\n\
                                 except ({}) as e:\n\
                                     logger.error(f\"Operation failed: {{e}}\")\n\
                                 ```",
                                suggestions.iter().map(|s| format!("  • {}", s)).collect::<Vec<_>>().join("\n"),
                                suggestions.first().unwrap_or(&"SpecificException".to_string())
                            )
                        } else {
                            "Catch specific exceptions instead of generic Exception.".to_string()
                        };

                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "BroadExceptionDetector".to_string(),
                            severity,
                            title: "Broad exception catch".to_string(),
                            description: format!(
                                "Catching generic Exception hides bugs and makes debugging difficult.{}",
                                context_notes
                            ),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some(suggestion),
                            estimated_effort: Some("10 minutes".to_string()),
                            category: Some("error-handling".to_string()),
                            cwe_id: None,
                            why_it_matters: Some(
                                "Broad exception catches mask unexpected errors like TypeErrors or \
                                 AttributeErrors that indicate bugs in your code.".to_string()
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!(
            "BroadExceptionDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
