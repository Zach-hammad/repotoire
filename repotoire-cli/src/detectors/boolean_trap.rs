//! Boolean Trap Detector
//!
//! Graph-enhanced detection of multiple boolean arguments in function calls.
//! Uses graph to:
//! - Find the target function definition to get param names
//! - Count how many call sites have this pattern
//! - Identify if it's a widely-used function (higher impact)

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static BOOL_ARGS: OnceLock<Regex> = OnceLock::new();
static FUNC_CALL: OnceLock<Regex> = OnceLock::new();

fn bool_args() -> &'static Regex {
    BOOL_ARGS.get_or_init(|| {
        Regex::new(r"\w+\s*\([^)]*\b(true|false|True|False)\s*,\s*(true|false|True|False)")
            .expect("valid regex")
    })
}

fn func_call() -> &'static Regex {
    FUNC_CALL.get_or_init(|| Regex::new(r"(\w+)\s*\(").expect("valid regex"))
}

pub struct BooleanTrapDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl BooleanTrapDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Extract function name from call
    fn extract_func_name(line: &str) -> Option<String> {
        func_call()
            .captures(line)
            .and_then(|c| c.get(1).map(|m| m.as_str().to_string()))
    }

    /// Count boolean args in a call
    fn count_bool_args(line: &str) -> usize {
        let bools = ["true", "false", "True", "False"];
        bools.iter().map(|b| line.matches(b).count()).sum()
    }
}

impl Detector for BooleanTrapDetector {
    fn name(&self) -> &'static str {
        "boolean-trap"
    }
    fn description(&self) -> &'static str {
        "Detects multiple boolean arguments"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let mut func_call_counts: HashMap<String, usize> = HashMap::new();
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        // First pass: collect all boolean trap calls and count per function
        let mut trap_calls: Vec<(PathBuf, u32, String, usize)> = Vec::new();

        for entry in walker.filter_map(|e| e.ok()) {
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py" | "js" | "ts" | "java" | "go" | "rb" | "cs") {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().masked_content(path) {
                let lines: Vec<&str> = content.lines().collect();
                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    if bool_args().is_match(line) {
                        if let Some(func_name) = Self::extract_func_name(line) {
                            let bool_count = Self::count_bool_args(line);
                            *func_call_counts.entry(func_name.clone()).or_default() += 1;
                            trap_calls.push((
                                path.to_path_buf(),
                                (i + 1) as u32,
                                func_name,
                                bool_count,
                            ));
                        }
                    }
                }
            }
        }

        // Second pass: create findings with graph context
        for (path, line_num, func_name, bool_count) in trap_calls {
            if findings.len() >= self.max_findings {
                break;
            }

            let call_count = func_call_counts.get(&func_name).copied().unwrap_or(1);

            // Find the function definition in graph
            let func_def = graph
                .get_functions()
                .into_iter()
                .find(|f| f.name == func_name);

            // Build context
            let mut notes = Vec::new();

            if call_count > 1 {
                notes.push(format!("📊 {} call sites with this pattern", call_count));
            }

            if bool_count > 2 {
                notes.push(format!(
                    "⚠️ {} boolean arguments (very confusing)",
                    bool_count
                ));
            }

            if let Some(ref def) = func_def {
                if let Some(params) = def.get_str("params") {
                    notes.push(format!("📝 Function params: {}", params));
                }
                let callers = graph.get_callers(&def.qualified_name);
                if callers.len() > 5 {
                    notes.push(format!(
                        "🔥 Widely used ({} callers) - high impact fix",
                        callers.len()
                    ));
                }
            }

            let context_notes = if notes.is_empty() {
                String::new()
            } else {
                format!("\n\n**Analysis:**\n{}", notes.join("\n"))
            };

            // Calculate severity based on usage
            let severity = if bool_count > 2 || call_count > 5 {
                Severity::Medium
            } else {
                Severity::Low
            };

            // Build suggestion based on language
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            let suggestion = match ext {
                "py" => format!(
                    "Use keyword arguments:\n\
                     ```python\n\
                     {}(verbose=True, debug=False)\n\
                     ```",
                    func_name
                ),
                "js" | "ts" => format!(
                    "Use an options object:\n\
                     ```javascript\n\
                     {}({{ verbose: true, debug: false }})\n\
                     ```",
                    func_name
                ),
                _ => "Use named arguments or an options object.".to_string(),
            };

            let file_str = path.to_string_lossy();
            let title = format!("Boolean trap: {}({} bools)", func_name, bool_count);

            findings.push(Finding {
                id: deterministic_finding_id("BooleanTrapDetector", &file_str, line_num, &title),
                detector: "BooleanTrapDetector".to_string(),
                severity,
                title,
                description: format!(
                    "`{}(true, false, ...)` is hard to understand at the call site.{}",
                    func_name, context_notes
                ),
                affected_files: vec![path],
                line_start: Some(line_num),
                line_end: Some(line_num),
                suggested_fix: Some(suggestion),
                estimated_effort: Some(if call_count > 5 {
                    "30 minutes".to_string()
                } else {
                    "15 minutes".to_string()
                }),
                category: Some("readability".to_string()),
                cwe_id: None,
                why_it_matters: Some(
                    "Boolean traps make APIs confusing and error-prone. \
                     It's easy to swap arguments or forget their meaning."
                        .to_string(),
                ),
                ..Default::default()
            });
        }

        info!(
            "BooleanTrapDetector found {} findings (graph-aware)",
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
    fn test_detects_boolean_trap() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("caller.py");
        std::fs::write(
            &file,
            r#"def main():
    process(data, True, False)
"#,
        )
        .unwrap();

        let store = GraphStore::in_memory();
        let detector = BooleanTrapDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(
            !findings.is_empty(),
            "Should detect boolean trap with True, False arguments"
        );
        assert!(
            findings[0].title.contains("Boolean trap"),
            "Title should mention boolean trap, got: {}",
            findings[0].title
        );
    }

    #[test]
    fn test_no_finding_without_multiple_booleans() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("caller.py");
        // Only one boolean argument - no trap
        std::fs::write(
            &file,
            r#"def main():
    process(data, True)
"#,
        )
        .unwrap();

        let store = GraphStore::in_memory();
        let detector = BooleanTrapDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag single boolean argument, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
