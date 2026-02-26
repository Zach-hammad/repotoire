//! TODO/FIXME Scanner
//!
//! Graph-enhanced scanning of TODO, FIXME, and other task comments.
//! Uses graph to:
//! - Group TODOs by containing function
//! - Check if TODO is in dead code (lower priority)
//! - Identify critical paths with TODOs (higher priority)

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use tracing::info;

static TODO_PATTERN: OnceLock<Regex> = OnceLock::new();

fn get_pattern() -> &'static Regex {
    TODO_PATTERN.get_or_init(|| {
        Regex::new(r"(?i)\b(TODO|FIXME|HACK|XXX)[\s:]+(.{0,80})|\b(BUG)\s*:\s*(.{0,80})")
            .expect("valid regex")
    })
}

pub struct TodoScanner {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
}

impl TodoScanner {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 200,
        }
    }

    /// Find containing function
    fn find_containing_function(
        graph: &dyn crate::graph::GraphQuery,
        file_path: &str,
        line: u32,
    ) -> Option<(String, usize)> {
        graph
            .get_functions()
            .into_iter()
            .find(|f| f.file_path == file_path && f.line_start <= line && f.line_end >= line)
            .map(|f| {
                let callers = graph.get_callers(&f.qualified_name).len();
                (f.name, callers)
            })
    }

    /// Check if function is dead code (no callers and not an entry point)
    fn is_in_dead_code(graph: &dyn crate::graph::GraphQuery, file_path: &str, line: u32) -> bool {
        if let Some(func) = graph
            .get_functions()
            .into_iter()
            .find(|f| f.file_path == file_path && f.line_start <= line && f.line_end >= line)
        {
            let callers = graph.get_callers(&func.qualified_name);
            let is_entry = func.name.starts_with("main")
                || func.name.starts_with("test_")
                || func.name.contains("handler")
                || func.name.contains("route");
            callers.is_empty() && !is_entry
        } else {
            false
        }
    }

    /// Categorize the TODO for better prioritization
    fn categorize_todo(msg: &str) -> (&'static str, Option<&'static str>) {
        let msg_lower = msg.to_lowercase();

        if msg_lower.contains("security")
            || msg_lower.contains("auth")
            || msg_lower.contains("password")
        {
            return ("security", Some("⚠️ Security-related TODO"));
        }
        if msg_lower.contains("performance")
            || msg_lower.contains("slow")
            || msg_lower.contains("optimize")
        {
            return ("performance", Some("🐌 Performance-related TODO"));
        }
        if msg_lower.contains("test") || msg_lower.contains("coverage") {
            return ("testing", Some("🧪 Testing-related TODO"));
        }
        if msg_lower.contains("refactor")
            || msg_lower.contains("cleanup")
            || msg_lower.contains("remove")
        {
            return ("refactoring", Some("🧹 Refactoring TODO"));
        }

        ("general", None)
    }
}

impl Detector for TodoScanner {
    fn name(&self) -> &'static str {
        "todo-scanner"
    }
    fn description(&self) -> &'static str {
        "Finds TODO, FIXME, HACK comments"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let mut todos_per_function: HashMap<String, usize> = HashMap::new();

        for path in files.files_with_extensions(&["py", "js", "ts", "jsx", "tsx", "rs", "go", "java", "rb", "php", "cs", "cpp", "c", "h"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            if let Some(content) = files.content(path) {
                let path_str = path.to_string_lossy().to_string();
                let lines: Vec<&str> = content.lines().collect();

                for (line_num, line) in lines.iter().enumerate() {
                    let prev_line = if line_num > 0 { Some(lines[line_num - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    // Only scan comment lines — skip string literals and code
                    let trimmed = line.trim_start();
                    // Skip doc comments — TODOs in documentation describe behavior, not tasks
                    if trimmed.starts_with("//!") || trimmed.starts_with("///") {
                        continue;
                    }
                    let is_comment = trimmed.starts_with("//")
                        || trimmed.starts_with('#')
                        || trimmed.starts_with('*')
                        || trimmed.starts_with("/*")
                        || trimmed.starts_with("--")
                        || trimmed.starts_with("<!--");
                    if !is_comment {
                        continue;
                    }

                    if let Some(caps) = get_pattern().captures(line) {
                        let tag = caps
                            .get(1)
                            .or(caps.get(3))
                            .map(|m| m.as_str())
                            .unwrap_or("TODO");
                        let msg = caps
                            .get(2)
                            .or(caps.get(4))
                            .map(|m| m.as_str().trim())
                            .unwrap_or("");
                        let line_u32 = (line_num + 1) as u32;

                        // Graph-enhanced analysis
                        let containing_func =
                            Self::find_containing_function(graph, &path_str, line_u32);
                        let is_dead = Self::is_in_dead_code(graph, &path_str, line_u32);
                        let (category, category_note) = Self::categorize_todo(msg);

                        // Track TODOs per function
                        if let Some((ref func_name, _)) = containing_func {
                            *todos_per_function.entry(func_name.clone()).or_default() += 1;
                        }

                        // Calculate severity with graph context
                        let mut severity = match tag.to_uppercase().as_str() {
                            "FIXME" | "BUG" => Severity::Medium,
                            "HACK" | "XXX" => Severity::Medium,
                            _ => Severity::Low,
                        };

                        // Boost for security TODOs
                        if category == "security" {
                            severity = Severity::High;
                        }

                        // Reduce for dead code
                        if is_dead {
                            severity = Severity::Low;
                        }

                        // Boost if in heavily-used function
                        if let Some((_, callers)) = &containing_func {
                            if *callers > 10 {
                                severity = match severity {
                                    Severity::Low => Severity::Medium,
                                    _ => severity,
                                };
                            }
                        }

                        // Build notes
                        let mut notes = Vec::new();
                        if let Some(note) = category_note {
                            notes.push(note.to_string());
                        }
                        if let Some((func_name, callers)) = &containing_func {
                            notes.push(format!(
                                "📦 In function: `{}` ({} callers)",
                                func_name, callers
                            ));
                        }
                        if is_dead {
                            notes.push("💀 In dead code (no callers)".to_string());
                        }

                        let context_notes = if notes.is_empty() {
                            String::new()
                        } else {
                            format!("\n\n**Analysis:**\n{}", notes.join("\n"))
                        };

                        let suggestion = match category {
                            "security" => "Security TODOs should be addressed before release. Create a high-priority ticket.".to_string(),
                            "performance" => "Consider benchmarking before and after addressing this.".to_string(),
                            "testing" => "Add tests to improve coverage and confidence.".to_string(),
                            "refactoring" => "Schedule time for technical debt cleanup.".to_string(),
                            _ => "Address this or create a ticket to track it.".to_string(),
                        };

                        findings.push(Finding {
                            id: String::new(),
                            detector: "TodoScanner".to_string(),
                            severity,
                            title: format!(
                                "{}: {}",
                                tag.to_uppercase(),
                                if msg.is_empty() {
                                    "(no description)"
                                } else {
                                    msg
                                }
                            ),
                            description: format!(
                                "Found {} comment indicating unfinished work.{}",
                                tag.to_uppercase(),
                                context_notes
                            ),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_u32),
                            line_end: Some(line_u32),
                            suggested_fix: Some(suggestion),
                            estimated_effort: None,
                            category: Some("technical-debt".to_string()),
                            cwe_id: if category == "security" {
                                Some("CWE-1078".to_string())
                            } else {
                                None
                            },
                            why_it_matters: Some(
                                "TODOs represent known issues or incomplete work. \
                                 Tracking and prioritizing them helps manage technical debt."
                                    .to_string(),
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!(
            "TodoScanner found {} findings (graph-aware)",
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
    fn test_detects_todo_fixme_hack() {
        let store = GraphStore::in_memory();
        let detector = TodoScanner::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("tasks.py", "def process():\n    # TODO: refactor this function\n    # FIXME: handle edge case\n    # HACK: workaround for upstream bug\n    return 42\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            findings.len() >= 3,
            "Should detect TODO, FIXME, and HACK. Found {} findings: {:?}",
            findings.len(),
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_clean_comments() {
        let store = GraphStore::in_memory();
        let detector = TodoScanner::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("clean.py", "# This function processes data.\n# It handles all edge cases properly.\ndef process():\n    return 42\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag normal comments. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
