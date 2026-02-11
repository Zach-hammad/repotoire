//! Unreachable Code Detector
//!
//! Graph-aware detection of unreachable code:
//! 1. Code after return/throw/exit (source pattern)
//! 2. Functions with zero callers in the call graph (dead functions)

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::{debug, info};
use uuid::Uuid;

static RETURN_PATTERN: OnceLock<Regex> = OnceLock::new();

fn return_pattern() -> &'static Regex {
    RETURN_PATTERN.get_or_init(|| {
        Regex::new(r"^\s*(return\b|throw\b|raise\b|exit\(|sys\.exit|process\.exit|break;|continue;)")
            .unwrap()
    })
}

/// Entry point patterns - these functions are called externally
const ENTRY_POINT_PATTERNS: &[&str] = &[
    "main", "test_", "setup", "teardown", "run", "start", "init",
    "handle", "on_", "get_", "post_", "put_", "delete_", "patch_",
    "__init__", "__new__", "__call__", "__enter__", "__exit__",
    "configure", "register", "setup_", "create_app",
];

/// Paths that indicate entry points
const ENTRY_POINT_PATHS: &[&str] = &[
    "/cli/", "/cmd/", "/main", "/handlers/", "/routes/", "/views/",
    "/api/", "/endpoints/", "/__main__", "/tests/", "_test.",
];

pub struct UnreachableCodeDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl UnreachableCodeDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Check if function is likely an entry point (called externally)
    fn is_entry_point(&self, func_name: &str, file_path: &str) -> bool {
        let name_lower = func_name.to_lowercase();
        
        // Check name patterns
        if ENTRY_POINT_PATTERNS.iter().any(|p| name_lower.starts_with(p) || name_lower == *p) {
            return true;
        }
        
        // Check path patterns
        if ENTRY_POINT_PATHS.iter().any(|p| file_path.contains(p)) {
            return true;
        }

        // Exported functions (capitalized in Go, pub in Rust implied by graph)
        if func_name.chars().next().map(|c| c.is_uppercase()).unwrap_or(false) {
            return true;
        }

        false
    }

    /// Find functions with zero callers using the call graph
    fn find_dead_functions(&self, graph: &GraphStore) -> Vec<Finding> {
        let mut findings = Vec::new();
        let functions = graph.get_functions();

        // Build set of all called functions
        let called_functions: HashSet<String> = graph.get_calls()
            .into_iter()
            .map(|(_, callee)| callee)
            .collect();

        for func in &functions {
            // Skip if it's called somewhere
            if called_functions.contains(&func.qualified_name) {
                continue;
            }

            // Skip entry points
            if self.is_entry_point(&func.name, &func.file_path) {
                continue;
            }

            // Skip test files for this check
            if func.file_path.contains("/test") || func.file_path.contains("_test.") 
                || func.file_path.contains("/tests/") || func.file_path.contains("conftest")
                || func.file_path.contains("type_check") {
                continue;
            }
            
            // Skip CLI-related functions (often entry points)
            if func.file_path.contains("/cli") || func.name.contains("locate") 
                || func.name.contains("app") || func.name.contains("create") {
                continue;
            }

            // Skip private/internal functions (underscore prefix)
            if func.name.starts_with('_') && !func.name.starts_with("__") {
                continue;
            }

            // Double-check with get_callers for accuracy
            let callers = graph.get_callers(&func.qualified_name);
            if !callers.is_empty() {
                continue;
            }

            debug!("Dead function found: {} in {}", func.name, func.file_path);

            findings.push(Finding {
                id: Uuid::new_v4().to_string(),
                detector: "UnreachableCodeDetector".to_string(),
                severity: Severity::Medium,
                title: format!("Dead function: {}", func.name),
                description: format!(
                    "Function '{}' has **zero callers** in the codebase.\n\n\
                     This function is never called and may be dead code that can be removed.",
                    func.name
                ),
                affected_files: vec![func.file_path.clone().into()],
                line_start: Some(func.line_start),
                line_end: Some(func.line_end),
                suggested_fix: Some(
                    "Options:\n\
                     1. Remove the dead function\n\
                     2. If it's an entry point, add it to exports or ensure it's registered\n\
                     3. If it's a callback, ensure it's passed to the caller"
                        .to_string()
                ),
                estimated_effort: Some("10 minutes".to_string()),
                category: Some("dead-code".to_string()),
                cwe_id: Some("CWE-561".to_string()),
                why_it_matters: Some(
                    "Dead functions add maintenance burden without providing value. \
                     They can confuse developers and increase cognitive load."
                        .to_string()
                ),
                ..Default::default()
            });
        }

        findings
    }

    /// Find code after return/throw statements using source scanning
    fn find_code_after_return(&self) -> Vec<Finding> {
        let mut findings = Vec::new();
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
            // Skip Rust - compiler catches this
            if ext == "rs" {
                continue;
            }
            if !matches!(ext, "py" | "js" | "ts" | "jsx" | "tsx" | "java" | "go" | "rb" | "php") {
                continue;
            }

            let rel_path = path.strip_prefix(&self.repository_path).unwrap_or(path);

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();

                for i in 0..lines.len().saturating_sub(1) {
                    let line = lines[i];
                    let next = lines[i + 1].trim();

                    // Skip if next line is empty, closing brace, or comment
                    if next.is_empty()
                        || next == "}"
                        || next == "]"
                        || next == ")"  // Closing paren (multi-line calls)
                        || next.starts_with("//")
                        || next.starts_with("#")
                        || next.starts_with("else")
                        || next.starts_with("elif")
                        || next.starts_with("except")
                        || next.starts_with("catch")
                        || next.starts_with("finally")
                        || next.starts_with("case")
                        || next.starts_with("default")
                        || next.starts_with(")")  // Multi-line function call closing
                        || next.starts_with("ctx")  // Common continuation pattern
                        || next.starts_with("param")  // Common continuation pattern
                    {
                        continue;
                    }
                    
                    // Skip if current line is inside a multi-line statement
                    if line.trim().ends_with(",") || line.trim().ends_with("(") {
                        continue;
                    }

                    if return_pattern().is_match(line) && !line.contains("if") && !line.contains("?") {
                        let curr_indent = line.len() - line.trim_start().len();
                        let next_indent = lines[i + 1].len() - next.len();

                        if next_indent >= curr_indent && !next.starts_with("}") {
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "UnreachableCodeDetector".to_string(),
                                severity: Severity::Medium,
                                title: "Unreachable code after return".to_string(),
                                description: format!(
                                    "Code after return/throw/exit will never execute:\n```\n{}\n{}\n```",
                                    line.trim(), next
                                ),
                                affected_files: vec![rel_path.to_path_buf()],
                                line_start: Some((i + 2) as u32),
                                line_end: Some((i + 2) as u32),
                                suggested_fix: Some(
                                    "Remove unreachable code or fix control flow logic.".to_string()
                                ),
                                estimated_effort: Some("10 minutes".to_string()),
                                category: Some("dead-code".to_string()),
                                cwe_id: Some("CWE-561".to_string()),
                                why_it_matters: Some(
                                    "Unreachable code indicates logic errors and adds confusion."
                                        .to_string()
                                ),
                                ..Default::default()
                            });
                        }
                    }
                }
            }
        }

        findings
    }
}

impl Detector for UnreachableCodeDetector {
    fn name(&self) -> &'static str {
        "UnreachableCodeDetector"
    }

    fn description(&self) -> &'static str {
        "Detects unreachable code and dead functions"
    }

    fn category(&self) -> &'static str {
        "dead-code"
    }

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();

        // Graph-based: find functions with zero callers
        findings.extend(self.find_dead_functions(graph));

        // Source-based: find code after return/throw
        findings.extend(self.find_code_after_return());

        info!("UnreachableCodeDetector found {} findings", findings.len());
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeNode, CodeEdge, GraphStore};

    #[test]
    fn test_is_entry_point() {
        let detector = UnreachableCodeDetector::new(".");

        assert!(detector.is_entry_point("main", "src/main.py"));
        assert!(detector.is_entry_point("test_something", "tests/test_foo.py"));
        assert!(detector.is_entry_point("handle_request", "handlers/api.py"));
        assert!(detector.is_entry_point("GetUser", "api/user.go")); // Capitalized = exported
        assert!(!detector.is_entry_point("helper_func", "src/utils.py"));
    }

    #[test]
    fn test_find_dead_functions() {
        let graph = GraphStore::in_memory();

        // Add a dead function (no callers)
        graph.add_node(
            CodeNode::function("dead_func", "src/utils.py")
                .with_qualified_name("utils::dead_func")
                .with_lines(10, 20),
        );

        // Add a live function with a caller
        graph.add_node(
            CodeNode::function("live_func", "src/utils.py")
                .with_qualified_name("utils::live_func")
                .with_lines(30, 40),
        );
        graph.add_node(
            CodeNode::function("caller", "src/main.py")
                .with_qualified_name("main::caller")
                .with_lines(1, 10),
        );
        graph.add_edge_by_name("main::caller", "utils::live_func", CodeEdge::calls());

        let detector = UnreachableCodeDetector::new(".");
        let findings = detector.find_dead_functions(&graph);

        assert_eq!(findings.len(), 1);
        assert!(findings[0].title.contains("dead_func"));
    }
}
