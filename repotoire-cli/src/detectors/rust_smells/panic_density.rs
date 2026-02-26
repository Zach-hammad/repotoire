use crate::detectors::base::Detector;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

use super::is_test_context;

static PANIC_MACRO: OnceLock<Regex> = OnceLock::new();

fn panic_macro() -> &'static Regex {
    PANIC_MACRO.get_or_init(|| Regex::new(r"\bpanic!\s*\(").expect("valid regex"))
}

/// Threshold for per-function panic-family call count (Medium severity).
const FUNCTION_THRESHOLD: usize = 3;
/// Threshold for per-file panic-family call count outside tests (Low severity).
const FILE_THRESHOLD: usize = 10;

/// A span tracking a single function definition.
#[derive(Debug)]
struct FunctionSpan {
    name: String,
    start_line: usize,
    /// Count of panic-family calls (.unwrap(), .expect(, panic!() ) inside this function.
    panic_count: usize,
    /// Line numbers where panic-family calls occur.
    panic_lines: Vec<usize>,
}

/// Detects Rust files and functions with a high density of panic-family calls
/// (`.unwrap()`, `.expect(`, `panic!(`).
///
/// Unlike `UnwrapWithoutContextDetector` which flags individual unwrap/expect calls,
/// this detector aggregates counts and flags *density* -- functions with many panic
/// points or files with a large total count.
pub struct PanicDensityDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
}

impl PanicDensityDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 30,
        }
    }
}

impl Detector for PanicDensityDetector {
    fn name(&self) -> &'static str {
        "rust-panic-density"
    }

    fn description(&self) -> &'static str {
        "Detects Rust files/functions with a high density of .unwrap(), .expect(), and panic!() calls"
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();

        for path in files.files_with_extension("rs") {
            if findings.len() >= self.max_findings {
                break;
            }

            let Some(content) = files.content(path) else {
                continue;
            };

            let file_str = path.to_string_lossy();
            let lines: Vec<&str> = content.lines().collect();

            // --- Extract function spans (skipping test regions) ---
            let functions = extract_function_spans(&lines, &content);

            // --- Per-function findings ---
            for func in &functions {
                if func.panic_count > FUNCTION_THRESHOLD && findings.len() < self.max_findings {
                    let line_num = (func.start_line + 1) as u32;
                    let locations: Vec<String> = func
                        .panic_lines
                        .iter()
                        .map(|l| format!("line {}", l + 1))
                        .collect();
                    let title = format!(
                        "High panic density in `{}`: {} panic-family calls",
                        func.name, func.panic_count
                    );
                    findings.push(Finding {
                        id: deterministic_finding_id(
                            "PanicDensityDetector",
                            &file_str,
                            line_num,
                            &title,
                        ),
                        detector: "PanicDensityDetector".to_string(),
                        severity: Severity::Medium,
                        title,
                        description: format!(
                            "Function `{}` contains {} calls to `.unwrap()`, `.expect()`, or `panic!()` at {}. \
                            This makes the function fragile and likely to panic at runtime.",
                            func.name,
                            func.panic_count,
                            locations.join(", ")
                        ),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(line_num),
                        line_end: None,
                        suggested_fix: Some(
                            "Reduce panic points by:\n\
                            - Using `?` operator for error propagation\n\
                            - Using `unwrap_or` / `unwrap_or_default` / `unwrap_or_else`\n\
                            - Returning `Result<T, E>` instead of panicking\n\
                            - Extracting fallible logic into a helper that returns Result"
                                .to_string(),
                        ),
                        estimated_effort: Some("30 minutes".to_string()),
                        category: Some("reliability".to_string()),
                        why_it_matters: Some(
                            "Functions with many panic points are fragile. A single unexpected \
                            None/Err will crash the program. Consolidating error handling makes \
                            code more robust and easier to reason about."
                                .to_string(),
                        ),
                        ..Default::default()
                    });
                }
            }

            // --- Per-file finding ---
            let total_file_panics: usize = functions.iter().map(|f| f.panic_count).sum();
            if total_file_panics > FILE_THRESHOLD && findings.len() < self.max_findings {
                let title = format!(
                    "High file-level panic density: {} panic-family calls",
                    total_file_panics
                );
                findings.push(Finding {
                    id: deterministic_finding_id(
                        "PanicDensityDetector-file",
                        &file_str,
                        0,
                        &title,
                    ),
                    detector: "PanicDensityDetector".to_string(),
                    severity: Severity::Low,
                    title,
                    description: format!(
                        "File `{}` contains {} total panic-family calls (`.unwrap()`, `.expect()`, `panic!()`) \
                        outside of test code. Consider refactoring to reduce panic surface.",
                        file_str, total_file_panics
                    ),
                    affected_files: vec![path.to_path_buf()],
                    line_start: None,
                    line_end: None,
                    suggested_fix: Some(
                        "Consider:\n\
                        - Introducing a module-level error type\n\
                        - Replacing unwrap chains with `?` propagation\n\
                        - Using `anyhow::Context` for better error messages"
                            .to_string(),
                    ),
                    estimated_effort: Some("1-2 hours".to_string()),
                    category: Some("reliability".to_string()),
                    why_it_matters: Some(
                        "Files with many panic points are hard to maintain and make the overall \
                        application fragile. Centralizing error handling improves reliability."
                            .to_string(),
                    ),
                    ..Default::default()
                });
            }
        }

        info!(
            "PanicDensityDetector found {} findings",
            findings.len()
        );
        Ok(findings)
    }
}

/// Extract function spans from the source, counting panic-family calls per function.
/// Skips functions inside test contexts (#[cfg(test)], #[test], mod tests).
fn extract_function_spans(lines: &[&str], content: &str) -> Vec<FunctionSpan> {
    let unwrap_re = super::unwrap_call();
    let expect_re = super::expect_call();
    let panic_re = panic_macro();

    let mut functions: Vec<FunctionSpan> = Vec::new();
    // Simple brace-depth tracker for the current function.
    let mut current_fn: Option<FunctionSpan> = None;
    let mut brace_depth: i32 = 0;
    // Track the brace depth at which the function body started.
    let mut fn_brace_start: i32 = 0;

    for (i, line) in lines.iter().enumerate() {
        // Skip test contexts entirely
        if is_test_context(line, content, i) {
            // If we're currently inside a function, discard it -- it's test code.
            if current_fn.is_some() {
                current_fn = None;
            }
            continue;
        }

        let trimmed = line.trim();

        // Detect function start: "fn name(" or "pub fn name(" etc.
        if current_fn.is_none() {
            if let Some(fn_name) = parse_fn_name(trimmed) {
                current_fn = Some(FunctionSpan {
                    name: fn_name,
                    start_line: i,
                    panic_count: 0,
                    panic_lines: Vec::new(),
                });
                // Reset brace depth tracking for this function scope.
                // We will count opening braces from the function line onward.
                fn_brace_start = brace_depth;
            }
        }

        // Update brace depth
        for ch in line.chars() {
            match ch {
                '{' => brace_depth += 1,
                '}' => brace_depth -= 1,
                _ => {}
            }
        }

        // If we are inside a function body, count panic-family calls
        if let Some(ref mut func) = current_fn {
            // Only count once we've entered the function body (past the opening brace)
            let is_panic_line = unwrap_re.is_match(line)
                || expect_re.is_match(line)
                || panic_re.is_match(line);
            if is_panic_line {
                // Check for suppression
                let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                if !crate::detectors::is_line_suppressed(line, prev_line) {
                    // Don't double-count safe unwrap contexts
                    if !super::is_safe_unwrap_context(line, content, i) {
                        func.panic_count += 1;
                        func.panic_lines.push(i);
                    }
                }
            }

            // Check if the function has ended (brace depth returned to pre-function level)
            if brace_depth <= fn_brace_start && i > func.start_line {
                let completed = current_fn.take().expect("checked Some above");
                functions.push(completed);
            }
        }
    }

    // If a function was still open at EOF (e.g. missing closing brace), include it anyway.
    if let Some(func) = current_fn.take() {
        functions.push(func);
    }

    functions
}

/// Try to parse a function name from a line like `fn foo(`, `pub fn bar(`, `pub(crate) fn baz(`.
fn parse_fn_name(trimmed: &str) -> Option<String> {
    // Skip comments
    if trimmed.starts_with("//") || trimmed.starts_with("/*") {
        return None;
    }

    // Find " fn " or line starting with "fn "
    let fn_idx = if trimmed.starts_with("fn ") {
        Some(3)
    } else {
        trimmed.find(" fn ").map(|idx| idx + 4)
    };

    let fn_idx = fn_idx?;

    // Extract the identifier after "fn "
    let rest = &trimmed[fn_idx..];
    let name_end = rest.find(|c: char| !c.is_alphanumeric() && c != '_')?;
    let name = &rest[..name_end];
    if name.is_empty() {
        return None;
    }
    Some(name.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detectors::base::Detector;
    use crate::graph::GraphStore;

    #[test]
    fn test_function_above_threshold() {
        let graph = GraphStore::in_memory();
        let detector = PanicDensityDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("test.rs", "\nfn fragile() {\n    let a = foo().unwrap();\n    let b = bar().unwrap();\n    let c = baz().unwrap();\n    let d = qux().expect(\"oops\");\n}\n"),
        ]);
        let findings = detector.detect(&graph, &files).expect("detection should succeed");
        assert_eq!(findings.len(), 1, "should flag function with 4 panic calls");
        assert_eq!(findings[0].severity, Severity::Medium);
        assert!(findings[0].title.contains("fragile"));
        assert!(findings[0].title.contains("4"));
    }

    #[test]
    fn test_function_at_threshold_not_flagged() {
        // Exactly 3 calls should NOT be flagged (threshold is >3)
        let graph = GraphStore::in_memory();
        let detector = PanicDensityDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("test.rs", "\nfn borderline() {\n    let a = foo().unwrap();\n    let b = bar().unwrap();\n    let c = baz().unwrap();\n}\n"),
        ]);
        let findings = detector.detect(&graph, &files).expect("detection should succeed");
        assert!(findings.is_empty(), "3 calls should not be flagged");
    }

    #[test]
    fn test_file_level_threshold() {
        // 11 unwraps spread across multiple functions, all outside tests
        let graph = GraphStore::in_memory();
        let detector = PanicDensityDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("test.rs", "\nfn one() {\n    let a = foo().unwrap();\n    let b = bar().unwrap();\n    let c = baz().unwrap();\n}\nfn two() {\n    let a = foo().unwrap();\n    let b = bar().unwrap();\n    let c = baz().unwrap();\n}\nfn three() {\n    let a = foo().unwrap();\n    let b = bar().unwrap();\n    let c = baz().unwrap();\n}\nfn four() {\n    let a = foo().unwrap();\n    let b = bar().unwrap();\n}\n"),
        ]);
        let findings = detector.detect(&graph, &files).expect("detection should succeed");
        // No function exceeds 3, but file total is 11 > 10
        let file_findings: Vec<_> = findings
            .iter()
            .filter(|f| f.title.contains("file-level"))
            .collect();
        assert_eq!(file_findings.len(), 1, "should flag file with 11 panic calls");
        assert_eq!(file_findings[0].severity, Severity::Low);
    }

    #[test]
    fn test_test_code_skipped() {
        let graph = GraphStore::in_memory();
        let detector = PanicDensityDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("test.rs", "\n#[cfg(test)]\nmod tests {\n    fn test_something() {\n        let a = foo().unwrap();\n        let b = bar().unwrap();\n        let c = baz().unwrap();\n        let d = qux().unwrap();\n        let e = quux().unwrap();\n    }\n}\n"),
        ]);
        let findings = detector.detect(&graph, &files).expect("detection should succeed");
        assert!(findings.is_empty(), "test code should be skipped");
    }

    #[test]
    fn test_panic_macro_counted() {
        let graph = GraphStore::in_memory();
        let detector = PanicDensityDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("test.rs", "\nfn panicky() {\n    if bad { panic!(\"oh no\"); }\n    let a = foo().unwrap();\n    let b = bar().unwrap();\n    panic!(\"fatal\");\n}\n"),
        ]);
        let findings = detector.detect(&graph, &files).expect("detection should succeed");
        assert_eq!(findings.len(), 1);
        assert!(findings[0].title.contains("4"));
    }

    #[test]
    fn test_parse_fn_name() {
        assert_eq!(parse_fn_name("fn foo(bar: i32) {"), Some("foo".to_string()));
        assert_eq!(
            parse_fn_name("pub fn my_func() {"),
            Some("my_func".to_string())
        );
        assert_eq!(
            parse_fn_name("pub(crate) fn internal() {"),
            Some("internal".to_string())
        );
        assert_eq!(parse_fn_name("// fn commented() {"), None);
        assert_eq!(parse_fn_name("let x = 5;"), None);
    }

    #[test]
    fn test_safe_unwrap_not_counted() {
        let graph = GraphStore::in_memory();
        let detector = PanicDensityDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("test.rs", "\nfn init() {\n    REGEX.get_or_init(|| make_regex().unwrap());\n    do_stuff_a();\n    do_stuff_b();\n    do_stuff_c();\n    do_stuff_d();\n    let a = foo().unwrap();\n    let b = bar().unwrap();\n    let c = baz().unwrap();\n    let d = qux().unwrap();\n}\n"),
        ]);
        let findings = detector.detect(&graph, &files).expect("detection should succeed");
        // The get_or_init line is safe; remaining 4 should trigger
        assert_eq!(findings.len(), 1);
        assert!(findings[0].title.contains("4"), "should count 4 non-safe panics");
    }

    #[test]
    fn test_no_findings_for_clean_code() {
        let graph = GraphStore::in_memory();
        let detector = PanicDensityDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("test.rs", "\nfn clean() -> Result<(), Error> {\n    let a = foo()?;\n    let b = bar().unwrap_or_default();\n    Ok(())\n}\n"),
        ]);
        let findings = detector.detect(&graph, &files).expect("detection should succeed");
        assert!(findings.is_empty());
    }
}
