//! Inconsistent Returns Detector
//!
//! Graph-enhanced detection of functions with inconsistent return paths:
//! - Uses graph to check if callers use the return value (increases severity)
//! - Identifies functions that are awaited/assigned vs. called standalone
//! - Checks for None/null vs value return mismatches
//! - Skips test functions, constructors/factory functions, and guard-clause patterns

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphQueryExt;
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use tracing::info;

pub struct InconsistentReturnsDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
}

/// Names that indicate constructor/factory/builder functions.
/// These commonly have early-return validation patterns that are intentional.
const CONSTRUCTOR_NAMES: &[&str] = &[
    "new", "init", "__init__", "create", "build", "make", "from", "of",
    "with", "setup", "configure", "initialize",
];

/// Prefixes that indicate constructor/factory/builder functions.
const CONSTRUCTOR_PREFIXES: &[&str] = &[
    "new_", "init_", "create_", "build_", "make_", "from_", "setup_",
    "configure_", "initialize_",
];

impl InconsistentReturnsDetector {
    crate::detectors::detector_new!(50);

    /// Check if any caller uses the return value
    fn return_value_is_used(
        graph: &dyn crate::graph::GraphQuery,
        func: &crate::graph::CodeNode,
    ) -> (bool, usize) {
        let i = graph.interner();
        let callers = graph.get_callers(func.qn(i));
        let mut callers_using_value = 0;

        for caller in &callers {
            // Check caller's code for patterns that use return value
            if let Ok(content) = std::fs::read_to_string(caller.path(i)) {
                let lines: Vec<&str> = content.lines().collect();
                let start = caller.line_start.saturating_sub(1) as usize;
                let end = (caller.line_end as usize).min(lines.len());

                for line in lines.get(start..end).unwrap_or(&[]) {
                    // Look for patterns like: x = func(), if func(), return func()
                    if line.contains(func.node_name(i)) {
                        if line.contains("=") && line.contains(&format!("{}(", func.node_name(i))) {
                            callers_using_value += 1;
                            break;
                        }
                        if line.trim().starts_with("return")
                            && line.contains(&format!("{}(", func.node_name(i)))
                        {
                            callers_using_value += 1;
                            break;
                        }
                        if line.contains("if") && line.contains(&format!("{}(", func.node_name(i))) {
                            callers_using_value += 1;
                            break;
                        }
                        if line.contains("await") && line.contains(&format!("{}(", func.node_name(i))) {
                            callers_using_value += 1;
                            break;
                        }
                    }
                }
            }
        }

        (callers_using_value > 0, callers.len())
    }

    /// Analyze return patterns in function
    fn analyze_returns(func_text: &str) -> ReturnAnalysis {
        let mut has_return_value = false;
        let mut has_return_none = false;
        let mut has_implicit_return = true;
        let mut return_count = 0;
        let mut guard_clause_count = 0;
        let mut value_return_count = 0;

        let lines: Vec<&str> = func_text.lines().collect();
        for (idx, line) in lines.iter().enumerate() {
            let trimmed = line.trim();

            // Skip comments
            if trimmed.starts_with("//") || trimmed.starts_with("#") {
                continue;
            }

            if trimmed.starts_with("return") || trimmed.contains(" return ") {
                return_count += 1;
                has_implicit_return = false;

                // Check what kind of return
                let is_none_return = trimmed == "return"
                    || trimmed == "return;"
                    || trimmed.starts_with("return;")
                    || trimmed.starts_with("return\n")
                    || trimmed.contains("return None")
                    || trimmed.contains("return null")
                    || trimmed.contains("return undefined");

                let is_error_return = trimmed.contains("return Err(")
                    || trimmed.contains("return None")
                    || trimmed.contains("return null")
                    || trimmed.contains("return undefined")
                    || trimmed.contains("return nil")
                    || trimmed.contains("return false")
                    || trimmed.contains("return False")
                    || trimmed.contains("return -1")
                    || trimmed.contains("return 0")
                    || trimmed.contains("return \"\"")
                    || trimmed.contains("return ''")
                    || trimmed.contains("return []")
                    || trimmed.contains("return {}")
                    || trimmed.contains("return Err(");

                if is_none_return {
                    has_return_none = true;
                }

                if is_error_return && Self::is_guard_clause(&lines, idx) {
                    guard_clause_count += 1;
                } else if trimmed.starts_with("return ") && !is_none_return {
                    has_return_value = true;
                    value_return_count += 1;
                }
            }
        }

        ReturnAnalysis {
            has_return_value,
            has_return_none,
            has_implicit_return,
            return_count,
            guard_clause_count,
            value_return_count,
        }
    }

    /// Check if a return at line `idx` is a guard clause (early return after a condition).
    ///
    /// Guard clauses are early returns preceded by an `if` condition that check
    /// for error states or preconditions. These are idiomatic and intentional.
    fn is_guard_clause(lines: &[&str], idx: usize) -> bool {
        // Look at the preceding lines (up to 3) for an if/guard pattern
        let start = idx.saturating_sub(3);
        for i in (start..idx).rev() {
            let prev = lines[i].trim();
            // Direct if-then-return pattern
            if prev.starts_with("if ")
                || prev.starts_with("if(")
                || prev.starts_with("unless ")
                || prev.starts_with("guard ")
                || prev.starts_with("} else")
                || prev.starts_with("else:")
                || prev.starts_with("elif ")
                || prev.starts_with("else if")
            {
                return true;
            }
            // In Rust: match arms with error returns
            if prev.contains("=>") || prev.starts_with("Err(") {
                return true;
            }
            // Stop looking if we hit a non-whitespace, non-brace line that isn't a condition
            if !prev.is_empty() && prev != "{" && prev != "}" {
                break;
            }
        }
        false
    }

    /// Check if a function name matches constructor/factory/builder patterns.
    fn is_constructor_or_factory(name: &str) -> bool {
        let lower = name.to_lowercase();
        // Exact matches
        if CONSTRUCTOR_NAMES.iter().any(|&n| lower == n) {
            return true;
        }
        // Prefix matches
        if CONSTRUCTOR_PREFIXES.iter().any(|&p| lower.starts_with(p)) {
            return true;
        }
        // Suffix matches (e.g., "build_config", "create_user")
        if lower.ends_with("_new") || lower.ends_with("_create") || lower.ends_with("_build") {
            return true;
        }
        false
    }

    /// Check if a function is a test helper (lives in test module or has test-like path).
    fn is_test_context(func_path: &str, func_qn: &str) -> bool {
        func_path.contains("/test")
            || func_path.contains("/tests/")
            || func_path.contains("/spec/")
            || func_path.contains("_test.")
            || func_path.contains(".test.")
            || func_path.contains(".spec.")
            || func_qn.contains("::tests::")
            || func_qn.contains("::test_")
    }
}

struct ReturnAnalysis {
    has_return_value: bool,
    has_return_none: bool,
    has_implicit_return: bool,
    return_count: usize,
    /// Number of returns that are guard clauses (early error returns after if-checks)
    guard_clause_count: usize,
    /// Number of returns that return a non-None/null value
    value_return_count: usize,
}

impl ReturnAnalysis {
    /// Returns true if the inconsistency is entirely explained by guard clauses.
    ///
    /// Pattern: one or more early returns with error/sentinel values (guard clauses)
    /// followed by a happy-path value return. This is idiomatic and not a bug.
    fn is_guard_clause_pattern(&self) -> bool {
        // All none/error returns are guard clauses, and there's at least one value return
        self.guard_clause_count > 0
            && self.value_return_count > 0
            && self.guard_clause_count >= self.return_count.saturating_sub(self.value_return_count)
    }
}

impl Detector for InconsistentReturnsDetector {
    fn name(&self) -> &'static str {
        "inconsistent-returns"
    }
    fn description(&self) -> &'static str {
        "Detects functions with inconsistent return paths"
    }

    fn file_extensions(&self) -> &'static [&'static str] {
        &["py", "js", "ts", "jsx", "tsx", "java", "go", "rs"]
    }

    fn detect(&self, ctx: &crate::detectors::analysis_context::AnalysisContext) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let i = graph.interner();
        let mut findings = vec![];

        for func in graph.get_functions_shared().iter() {
            if findings.len() >= self.max_findings {
                break;
            }

            let qn = func.qn(i);
            let func_name = func.node_name(i);
            let func_path = func.path(i);

            // ── 1. Skip test functions ──────────────────────────────────
            // Test functions often have conditional returns that are intentional
            if ctx.is_test_function(qn) || func_name.starts_with("test_") || Self::is_test_context(func_path, qn) {
                continue;
            }

            // ── 2. Skip constructor/init/factory/builder functions ──────
            // These commonly return early on validation failures
            if Self::is_constructor_or_factory(func_name) {
                continue;
            }

            // ── 3. Skip very small functions ────────────────────────────
            let func_size = func.line_end.saturating_sub(func.line_start);
            if func_size < 3 {
                continue;
            }

            if let Ok(content) = std::fs::read_to_string(func_path) {
                let start = func.line_start.saturating_sub(1) as usize;
                let end = func.line_end as usize;
                let func_lines: Vec<&str> = content.lines().skip(start).take(end - start).collect();
                let func_text = func_lines.join("\n");

                let analysis = Self::analyze_returns(&func_text);

                // Check for inconsistent return patterns
                let is_inconsistent = analysis.has_return_value
                    && (analysis.has_return_none
                        || (analysis.has_implicit_return && analysis.return_count > 0));

                if !is_inconsistent {
                    continue;
                }

                // ── 4. Skip guard-clause patterns ───────────────────────
                // If inconsistency is explained by guard clauses (early
                // returns for error/validation), this is idiomatic.
                if analysis.is_guard_clause_pattern() {
                    continue;
                }

                // ── 5. Use graph callers for severity ───────────────────
                // Use pre-computed callers from DetectorContext instead of
                // expensive regex-based file scanning
                let graph_callers = ctx.detector_ctx.callers_by_qn
                    .get(qn)
                    .map(|v| v.len())
                    .unwrap_or(0);

                // Check if callers use the return value (only if there are callers)
                let (value_is_used, caller_count) = if graph_callers > 0 {
                    Self::return_value_is_used(graph, func)
                } else {
                    (false, 0)
                };

                // ── 6. Calculate severity ───────────────────────────────
                let mut severity = if value_is_used {
                    Severity::High // Callers expect a value!
                } else {
                    Severity::Medium
                };

                // If no callers in graph AND not public API, reduce severity
                // (nobody depends on the return consistency)
                if graph_callers == 0 && !ctx.is_public_api(qn) {
                    severity = match severity {
                        Severity::High => Severity::Medium,
                        Severity::Medium => Severity::Low,
                        _ => severity,
                    };
                }

                // ── 7. Reduce severity for unreachable non-public ───────
                if !ctx.is_reachable(qn) && !ctx.is_public_api(qn) {
                    severity = match severity {
                        Severity::High => Severity::Medium,
                        Severity::Medium => Severity::Low,
                        Severity::Low => Severity::Info,
                        _ => severity,
                    };
                }

                // Build context notes
                let mut notes = Vec::new();
                if analysis.return_count > 0 {
                    notes.push(format!(
                        "{} return statements found",
                        analysis.return_count
                    ));
                }
                if analysis.has_return_value {
                    notes.push(format!("{} paths return a value", analysis.value_return_count));
                }
                if analysis.has_return_none {
                    notes.push("Some paths return None/null".to_string());
                }
                if analysis.has_implicit_return {
                    notes.push("Some paths have no return (implicit None)".to_string());
                }
                if analysis.guard_clause_count > 0 {
                    notes.push(format!(
                        "{} guard clause returns (not counted as inconsistency)",
                        analysis.guard_clause_count
                    ));
                }
                if caller_count > 0 {
                    if value_is_used {
                        notes.push(format!(
                            "Called by {} functions - some USE the return value",
                            caller_count
                        ));
                    } else {
                        notes.push(format!("Called by {} functions", caller_count));
                    }
                }

                let context_notes = format!("\n\n**Analysis:**\n{}", notes.join("\n"));

                let suggestion = if value_is_used {
                    "**CRITICAL**: Callers expect a return value! Options:\n\
                     1. Return a default value on all paths\n\
                     2. Raise an exception for error cases\n\
                     3. Return an Optional type and have callers handle None"
                        .to_string()
                } else {
                    "Ensure all paths return consistently:\n\
                     1. Add explicit return None where implicit\n\
                     2. Return a value on all paths\n\
                     3. Use Optional type hint to document behavior"
                        .to_string()
                };

                findings.push(Finding {
                    id: String::new(),
                    detector: "InconsistentReturnsDetector".to_string(),
                    severity,
                    title: format!("Inconsistent returns in '{}'", func_name),
                    description: format!(
                        "Function has mixed return behavior - some paths return values, others don't.{}",
                        context_notes
                    ),
                    affected_files: vec![PathBuf::from(func_path)],
                    line_start: Some(func.line_start),
                    line_end: Some(func.line_end),
                    suggested_fix: Some(suggestion),
                    estimated_effort: Some("15 minutes".to_string()),
                    category: Some("bug-risk".to_string()),
                    cwe_id: Some("CWE-394".to_string()),
                    why_it_matters: Some(
                        "Inconsistent returns can cause unexpected None/undefined values, \
                         leading to TypeErrors or NullPointerExceptions in callers.".to_string()
                    ),
                    ..Default::default()
                });
            }
        }

        info!(
            "InconsistentReturnsDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}


impl super::RegisteredDetector for InconsistentReturnsDetector {
    fn create(init: &super::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::new(init.repo_path))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeNode, GraphStore};

    #[test]
    fn test_detects_inconsistent_returns() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let file = dir.path().join("logic.py");
        // Function that has both `return item` (value) and `return None` (none)
        // NOT a guard clause -- the None return is not preceded by an if-check for error
        let code = r#"def find_item(items, target):
    for item in items:
        if item.name == target:
            return item
    return None
"#;
        std::fs::write(&file, code).expect("should write test file");

        let store = GraphStore::in_memory();
        let file_path = file.to_string_lossy().to_string();
        let func = CodeNode::function("find_item", &file_path)
            .with_qualified_name("logic::find_item")
            .with_lines(1, 5);
        store.add_node(func);

        let detector = InconsistentReturnsDetector::new(dir.path());
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect function with mixed return (value + None)"
        );
        assert!(
            findings[0].title.contains("find_item"),
            "Title should mention function name, got: {}",
            findings[0].title
        );
    }

    #[test]
    fn test_no_finding_for_consistent_returns() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let file = dir.path().join("logic.py");
        let code = r#"def add(a, b):
    result = a + b
    return result
    return 0
"#;
        std::fs::write(&file, code).expect("should write test file");

        let store = GraphStore::in_memory();
        let file_path = file.to_string_lossy().to_string();
        let func = CodeNode::function("add", &file_path)
            .with_qualified_name("logic::add")
            .with_lines(1, 4);
        store.add_node(func);

        let detector = InconsistentReturnsDetector::new(dir.path());
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag function with consistent return values, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_skips_test_functions() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let file = dir.path().join("test_logic.py");
        let code = r#"def test_find_item():
    result = find_item(items, "foo")
    if result:
        return result
    return None
"#;
        std::fs::write(&file, code).expect("should write test file");

        let store = GraphStore::in_memory();
        let file_path = file.to_string_lossy().to_string();
        let func = CodeNode::function("test_find_item", &file_path)
            .with_qualified_name("test_logic::test_find_item")
            .with_lines(1, 5);
        store.add_node(func);

        let detector = InconsistentReturnsDetector::new(dir.path());
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should skip test functions, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_skips_constructor_functions() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let file = dir.path().join("factory.py");
        let code = r#"def create_user(name, email):
    if not name:
        return None
    if not email:
        return None
    return User(name, email)
"#;
        std::fs::write(&file, code).expect("should write test file");

        let store = GraphStore::in_memory();
        let file_path = file.to_string_lossy().to_string();
        let func = CodeNode::function("create_user", &file_path)
            .with_qualified_name("factory::create_user")
            .with_lines(1, 6);
        store.add_node(func);

        let detector = InconsistentReturnsDetector::new(dir.path());
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should skip constructor/factory functions, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_skips_guard_clause_pattern() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let file = dir.path().join("handler.py");
        let code = r#"def process_request(request):
    if not request.is_valid():
        return None
    if request.is_empty():
        return None
    data = transform(request.data)
    return data
"#;
        std::fs::write(&file, code).expect("should write test file");

        let store = GraphStore::in_memory();
        let file_path = file.to_string_lossy().to_string();
        let func = CodeNode::function("process_request", &file_path)
            .with_qualified_name("handler::process_request")
            .with_lines(1, 7);
        store.add_node(func);

        let detector = InconsistentReturnsDetector::new(dir.path());
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should skip guard-clause pattern (early returns for validation), but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_guard_clause_detection() {
        // Guard clause: if-check followed by error return
        let lines = vec![
            "def foo(x):",
            "    if x is None:",
            "        return None",
            "    return x + 1",
        ];
        assert!(InconsistentReturnsDetector::is_guard_clause(&lines, 2));
        assert!(!InconsistentReturnsDetector::is_guard_clause(&lines, 3));
    }

    #[test]
    fn test_constructor_name_detection() {
        assert!(InconsistentReturnsDetector::is_constructor_or_factory("new"));
        assert!(InconsistentReturnsDetector::is_constructor_or_factory("__init__"));
        assert!(InconsistentReturnsDetector::is_constructor_or_factory("create_user"));
        assert!(InconsistentReturnsDetector::is_constructor_or_factory("build_config"));
        assert!(InconsistentReturnsDetector::is_constructor_or_factory("from_str"));
        assert!(InconsistentReturnsDetector::is_constructor_or_factory("setup_logging"));
        assert!(!InconsistentReturnsDetector::is_constructor_or_factory("process_data"));
        assert!(!InconsistentReturnsDetector::is_constructor_or_factory("find_item"));
    }

    #[test]
    fn test_analyze_returns_guard_clauses() {
        let code = r#"def process(x):
    if x is None:
        return None
    if x < 0:
        return None
    return x * 2
"#;
        let analysis = InconsistentReturnsDetector::analyze_returns(code);
        assert!(analysis.has_return_value);
        assert!(analysis.has_return_none);
        assert_eq!(analysis.guard_clause_count, 2);
        assert_eq!(analysis.value_return_count, 1);
        assert!(analysis.is_guard_clause_pattern());
    }

    #[test]
    fn test_analyze_returns_not_guard_clause() {
        // Return None at end without guard clause pattern
        let code = r#"def process(items):
    for item in items:
        if item.valid:
            return item
    return None
"#;
        let analysis = InconsistentReturnsDetector::analyze_returns(code);
        assert!(analysis.has_return_value);
        assert!(analysis.has_return_none);
        // The `return None` at the end is NOT a guard clause (not preceded by a
        // validation if-check -- it's a fallthrough after a loop)
        // Note: the `return item` IS inside an if, so it could be detected as guard,
        // but `return item` is a value return, not an error return
    }
}
