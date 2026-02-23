//! Empty Catch Block Detector
//!
//! Graph-enhanced detection of empty catch/except blocks that swallow exceptions.
//! Uses graph to:
//! - Identify what functions are called in the try block (risk assessment)
//! - Check if the swallowed function does I/O or external calls (higher risk)

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use tracing::info;

pub struct EmptyCatchDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
}

impl EmptyCatchDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            config: DetectorConfig::default(),
            repository_path: repository_path.into(),
            max_findings: 100,
        }
    }

    /// Find try block start line for a catch at given line
    fn find_try_block_start(lines: &[&str], catch_line: usize) -> Option<usize> {
        for i in (0..catch_line).rev() {
            let trimmed = lines[i].trim();
            if trimmed.starts_with("try") || trimmed == "try:" || trimmed == "try {" {
                return Some(i);
            }
        }
        None
    }

    /// Extract function calls from a code block
    fn extract_calls(lines: &[&str], start: usize, end: usize) -> HashSet<String> {
        use std::sync::OnceLock;
        static CALL_RE: OnceLock<regex::Regex> = OnceLock::new();
        let call_re = CALL_RE.get_or_init(|| {
            regex::Regex::new(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(").expect("valid regex")
        });
        let mut calls = HashSet::new();

        for line in lines.get(start..end).unwrap_or(&[]) {
            for cap in call_re.captures_iter(line) {
                if let Some(m) = cap.get(1) {
                    let name = m.as_str();
                    // Skip common keywords and builtins
                    if ![
                        "if", "for", "while", "print", "len", "str", "int", "float", "bool",
                        "list", "dict", "set",
                    ]
                    .contains(&name)
                    {
                        calls.insert(name.to_string());
                    }
                }
            }
        }
        calls
    }

    /// Check if any of the called functions do I/O or external operations
    fn assess_risk(
        calls: &HashSet<String>,
        graph: &dyn crate::graph::GraphQuery,
    ) -> (Severity, Vec<String>) {
        let io_patterns = [
            "read", "write", "open", "close", "fetch", "request", "send", "recv", "connect",
            "query", "execute", "save", "load", "delete", "update",
        ];
        let mut risk_notes = Vec::new();
        let mut has_io = false;

        for call in calls {
            let call_lower = call.to_lowercase();
            if io_patterns.iter().any(|p| call_lower.contains(p)) {
                has_io = true;
                risk_notes.push(format!("⚠️ `{}` appears to do I/O", call));
            }
        }

        // Check graph for functions with many callees (complex operations)
        for call in calls {
            if let Some(func) = graph.get_functions().into_iter().find(|f| f.name == *call) {
                let callees = graph.get_callees(&func.qualified_name);
                if callees.len() > 5 {
                    risk_notes.push(format!(
                        "📊 `{}` is complex ({} internal calls)",
                        call,
                        callees.len()
                    ));
                }
            }
        }

        let severity = if has_io {
            Severity::High // Swallowing I/O exceptions is dangerous
        } else if !risk_notes.is_empty() {
            Severity::Medium
        } else {
            Severity::Low // Simple operations, less risky
        };

        (severity, risk_notes)
    }

    fn scan_file(
        &self,
        path: &Path,
        ext: &str,
        graph: &dyn crate::graph::GraphQuery,
    ) -> Vec<Finding> {
        let mut findings = vec![];
        let content = match std::fs::read_to_string(path) {
            Ok(c) => c,
            Err(_) => return findings,
        };
        let lines: Vec<&str> = content.lines().collect();

        for (i, line) in lines.iter().enumerate() {
            let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
            if crate::detectors::is_line_suppressed(line, prev_line) {
                continue;
            }

            let trimmed = line.trim();
            let mut is_empty_catch = false;
            let catch_line = i;

            // Python: except: followed by pass
            if ext == "py" && trimmed.starts_with("except") && trimmed.ends_with(":") {
                if let Some(next) = lines.get(i + 1) {
                    let next_trimmed = next.trim();
                    if next_trimmed == "pass" || next_trimmed == "..." {
                        is_empty_catch = true;
                    }
                }
            }

            // JS/TS/Java: catch (...) { }
            if matches!(ext, "js" | "ts" | "jsx" | "tsx" | "java" | "cs")
                && trimmed.contains("catch")
                && trimmed.contains("{")
                && trimmed.contains("}")
                && (trimmed.ends_with("{ }") || trimmed.ends_with("{}"))
            {
                is_empty_catch = true;
            }

            if is_empty_catch {
                // Find the try block and analyze it
                let (severity, risk_notes) =
                    if let Some(try_start) = Self::find_try_block_start(&lines, catch_line) {
                        let calls = Self::extract_calls(&lines, try_start, catch_line);
                        let (sev, notes) = Self::assess_risk(&calls, graph);

                        if !calls.is_empty() {
                            (sev, notes)
                        } else {
                            (Severity::Medium, vec![])
                        }
                    } else {
                        (Severity::Medium, vec![])
                    };

                let context_notes = if risk_notes.is_empty() {
                    String::new()
                } else {
                    format!("\n\n**Risk Assessment:**\n{}", risk_notes.join("\n"))
                };

                let suggestion = if severity == Severity::High {
                    "This swallows I/O or network exceptions - very dangerous!\n\
                     At minimum, log the exception:\n\
                     ```python\n\
                     except Exception as e:\n\
                         logger.error(f\"Operation failed: {e}\")\n\
                     ```"
                    .to_string()
                } else {
                    "Log the exception or handle it appropriately:\n\
                     - Add logging to track failures\n\
                     - Re-raise if recovery isn't possible\n\
                     - Handle specific exception types"
                        .to_string()
                };

                findings.push(Finding {
                    id: String::new(),
                    detector: "EmptyCatchDetector".to_string(),
                    severity,
                    title: "Empty catch block swallows exceptions".to_string(),
                    description: format!(
                        "This catch block silently swallows exceptions, hiding potential bugs.{}",
                        context_notes
                    ),
                    affected_files: vec![path.to_path_buf()],
                    line_start: Some((catch_line + 1) as u32),
                    line_end: Some((catch_line + 1) as u32),
                    suggested_fix: Some(suggestion),
                    estimated_effort: Some("10 minutes".to_string()),
                    category: Some("error-handling".to_string()),
                    cwe_id: Some("CWE-390".to_string()),
                    why_it_matters: Some(
                        "Swallowed exceptions hide bugs and make debugging extremely difficult. \
                         When something fails silently, you may not know until much later."
                            .to_string(),
                    ),
                    ..Default::default()
                });
            }
        }
        findings
    }
}

impl Detector for EmptyCatchDetector {
    fn name(&self) -> &'static str {
        "empty-catch-block"
    }
    fn description(&self) -> &'static str {
        "Detects empty catch/except blocks"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, _files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
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
            if matches!(ext, "py" | "js" | "ts" | "jsx" | "tsx" | "java" | "cs") {
                findings.extend(self.scan_file(path, ext, graph));
            }
        }

        info!(
            "EmptyCatchDetector found {} findings (graph-aware)",
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
    fn test_detects_empty_except_pass_in_python() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("handler.py");
        std::fs::write(
            &file,
            r#"def process():
    try:
        do_something()
    except:
        pass
"#,
        )
        .unwrap();

        let store = GraphStore::in_memory();
        let detector = EmptyCatchDetector::new(dir.path());
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&store, &empty_files).unwrap();
        assert!(
            !findings.is_empty(),
            "Should detect empty except: pass block"
        );
        assert!(
            findings[0].title.contains("Empty catch"),
            "Title should mention empty catch, got: {}",
            findings[0].title
        );
    }

    #[test]
    fn test_no_finding_for_handled_exception() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("handler.py");
        std::fs::write(
            &file,
            r#"def process():
    try:
        do_something()
    except ValueError as e:
        logger.error(e)
"#,
        )
        .unwrap();

        let store = GraphStore::in_memory();
        let detector = EmptyCatchDetector::new(dir.path());
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&store, &empty_files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag exception that is properly handled, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
