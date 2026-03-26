//! Unreachable Code Detector
//!
//! Detects code after return/throw/raise/break/continue statements using
//! tree-sitter AST analysis. Walks block nodes in the parse tree and flags
//! sibling statements that appear after terminating nodes.
//!
//! This approach is correct by construction:
//! - String literals are leaf nodes — never contain "sibling statements"
//! - Multi-line expressions are single AST nodes — no false sibling detection
//! - Method chains are part of the same expression — not separate statements
//!
//! Dead function detection (fan_in == 0) is handled by DeadCodeDetector.

use crate::detectors::analysis_context::AnalysisContext;
use crate::graph::GraphQueryExt;
use crate::detectors::ast_fingerprint::{get_ts_language, parse_root_ext};
use crate::detectors::base::Detector;
use crate::models::{Finding, Severity};
use crate::parsers::lightweight::Language;
use anyhow::Result;
use std::path::{Path, PathBuf};
use tree_sitter::Node;

pub struct UnreachableCodeDetector {
    max_findings: usize,
}

impl UnreachableCodeDetector {
    pub fn new(_repository_path: impl Into<PathBuf>) -> Self {
        Self { max_findings: 50 }
    }

    // ── Conditional compilation helpers (kept for exemptions + tests) ──

    /// Rust conditional compilation attributes that mean a function is only compiled
    /// under certain conditions (cfg, test, bench, etc.) -- not truly unreachable.
    #[allow(dead_code)]
    const RUST_CONDITIONAL_ATTRS: &'static [&'static str] = &[
        "cfg(",
        "cfg_attr(",
        "test",
        "bench",
        "ignore",
        "cfg_eval",
    ];

    /// Check if a function is conditionally compiled via Rust attributes.
    ///
    /// Uses the `decorators` field from ExtraProps (populated by the Rust parser
    /// from `#[...]` attribute items preceding function definitions).
    #[allow(dead_code)]
    fn is_conditionally_compiled_rust(
        graph: &dyn crate::graph::GraphQuery,
        func: &crate::graph::store_models::CodeNode,
    ) -> bool {
        if let Some(ep) = graph.extra_props(func.qualified_name) {
            if let Some(decorators_key) = ep.decorators {
                let i = graph.interner();
                let decorators = i.resolve(decorators_key);
                // Decorators are stored as comma-separated: "cfg(test),derive(Debug)"
                for attr in decorators.split(',') {
                    let attr = attr.trim();
                    for pattern in Self::RUST_CONDITIONAL_ATTRS {
                        if attr.starts_with(pattern) || attr == *pattern {
                            return true;
                        }
                    }
                }
            }
        }
        false
    }

    /// Check if a function is inside a conditionally compiled block by examining
    /// the source file content around the function definition.
    ///
    /// Handles:
    /// - **Rust**: `#[cfg(test)] mod tests { ... }` blocks
    /// - **C/C++**: `#ifdef`, `#ifndef`, `#if` preprocessor guards
    /// - **Python**: `if __name__` guards
    #[allow(dead_code)]
    fn is_in_conditional_block(file_path: &str, func_line_start: u32, content: &str) -> bool {
        if file_path.ends_with(".rs") {
            return Self::is_in_rust_cfg_module(func_line_start, content);
        }
        if file_path.ends_with(".c")
            || file_path.ends_with(".cpp")
            || file_path.ends_with(".cc")
            || file_path.ends_with(".cxx")
            || file_path.ends_with(".h")
            || file_path.ends_with(".hpp")
        {
            return Self::is_in_preprocessor_guard(func_line_start, content);
        }
        if file_path.ends_with(".py") {
            return Self::is_in_python_name_guard(func_line_start, content);
        }
        false
    }

    /// Check if a Rust function is inside a `#[cfg(...)]` module block.
    #[allow(dead_code)]
    fn is_in_rust_cfg_module(func_line_start: u32, content: &str) -> bool {
        let lines: Vec<&str> = content.lines().collect();
        let func_idx = (func_line_start as usize).saturating_sub(1);

        if func_idx >= lines.len() {
            return false;
        }

        let mut i = func_idx;
        while i > 0 {
            i -= 1;
            let line = lines[i].trim();
            if (line.starts_with("mod ") || line.starts_with("pub mod ")) && line.contains('{') {
                let mut attr_line = i;
                while attr_line > 0 {
                    attr_line -= 1;
                    let prev = lines[attr_line].trim();
                    if prev.starts_with("#[cfg(") || prev.starts_with("#[cfg_attr(") {
                        return true;
                    }
                    if prev.starts_with("#[") || prev.starts_with("//") || prev.is_empty() {
                        continue;
                    }
                    break;
                }
            }
        }
        false
    }

    /// Check if a C/C++ function is inside a preprocessor conditional block.
    #[allow(dead_code)]
    fn is_in_preprocessor_guard(func_line_start: u32, content: &str) -> bool {
        let lines: Vec<&str> = content.lines().collect();
        let func_idx = (func_line_start as usize).saturating_sub(1);
        let mut pp_stack: Vec<usize> = Vec::new();

        for (line_idx, line) in lines.iter().enumerate() {
            if line_idx >= func_idx {
                break;
            }
            let trimmed = line.trim();
            if trimmed.starts_with("#ifdef")
                || trimmed.starts_with("#ifndef")
                || trimmed.starts_with("#if ")
                || trimmed == "#if"
            {
                pp_stack.push(line_idx);
            } else if trimmed.starts_with("#endif") {
                pp_stack.pop();
            }
        }
        !pp_stack.is_empty()
    }

    /// Check if a Python function is inside an `if __name__ == "__main__":` guard.
    #[allow(dead_code)]
    fn is_in_python_name_guard(func_line_start: u32, content: &str) -> bool {
        let lines: Vec<&str> = content.lines().collect();
        let func_idx = (func_line_start as usize).saturating_sub(1);

        if func_idx >= lines.len() {
            return false;
        }

        let func_indent = lines[func_idx].len() - lines[func_idx].trim_start().len();
        if func_indent == 0 {
            return false;
        }

        for i in (0..func_idx).rev() {
            let line = lines[i];
            let trimmed = line.trim();
            if trimmed.is_empty() || trimmed.starts_with('#') {
                continue;
            }
            let indent = line.len() - trimmed.len();
            if indent < func_indent {
                if trimmed.starts_with("if __name__") || trimmed.starts_with("if  __name__") {
                    return true;
                }
                break;
            }
        }
        false
    }

    // ── AST-based unreachable code detection ──────────────────────────

    /// Detect code after return/throw/raise/break/continue using tree-sitter AST.
    ///
    /// For each file, parses with tree-sitter and walks block nodes looking for
    /// sibling statements after terminating nodes.
    fn find_code_after_return(&self, ctx: &AnalysisContext<'_>) -> Vec<Finding> {
        let extensions = &[
            "py", "js", "ts", "jsx", "tsx", "java", "go", "rs", "c", "cpp", "cs",
        ];
        let mut findings = Vec::new();

        for entry in ctx.files.by_extensions(extensions) {
            if findings.len() >= self.max_findings {
                break;
            }

            let ext = entry
                .path
                .extension()
                .and_then(|e| e.to_str())
                .unwrap_or("");
            let lang = Language::from_extension(ext);

            // Skip languages without tree-sitter support (TSX is handled by parse_root_ext)
            if ext != "tsx" && get_ts_language(lang).is_none() {
                continue;
            }

            let tree = match parse_root_ext(&entry.content, lang, ext) {
                Some(t) => t,
                None => continue,
            };

            self.walk_for_unreachable(
                tree.root_node(),
                &entry.content,
                &entry.path,
                lang,
                ctx,
                &mut findings,
            );
        }
        findings
    }

    /// Recursively walk AST nodes, checking block containers for post-terminator siblings.
    fn walk_for_unreachable(
        &self,
        node: Node,
        source: &str,
        file_path: &Path,
        lang: Language,
        ctx: &AnalysisContext<'_>,
        findings: &mut Vec<Finding>,
    ) {
        if findings.len() >= self.max_findings {
            return;
        }

        if Self::is_block_node(node.kind(), lang) {
            self.check_block(node, source, file_path, lang, ctx, findings);
        }

        // Recurse into all named children
        let mut cursor = node.walk();
        for child in node.named_children(&mut cursor) {
            self.walk_for_unreachable(child, source, file_path, lang, ctx, findings);
        }
    }

    /// Check a block node for unreachable statements after terminators.
    ///
    /// Iterates direct named children of a block in order. When a child IS
    /// or CONTAINS a terminating node, the next sibling statement is flagged
    /// as unreachable (first one only per terminator).
    fn check_block(
        &self,
        block: Node,
        source: &str,
        file_path: &Path,
        lang: Language,
        ctx: &AnalysisContext<'_>,
        findings: &mut Vec<Finding>,
    ) {
        let mut saw_terminator = false;
        let mut cursor = block.walk();

        for child in block.named_children(&mut cursor) {
            // Skip tree-sitter "extras" (comments, shebangs, etc.).
            // Extras are injected nodes that aren't real statements — they
            // appear as named children of any block but should never be
            // flagged as unreachable. Using is_extra() is language-agnostic.
            if child.is_extra() {
                continue;
            }

            if saw_terminator {
                let line = child.start_position().row as u32 + 1;

                // Apply graph-based exemptions (test functions, conditional compilation)
                if self.should_exempt(ctx, file_path, line) {
                    saw_terminator = false;
                    continue;
                }

                let text = &source[child.start_byte()..child.end_byte()];
                let first_line = text.lines().next().unwrap_or("").trim();

                findings.push(Finding {
                    id: String::new(),
                    detector: "UnreachableCodeDetector".to_string(),
                    severity: Severity::Medium,
                    title: "Unreachable code after return".to_string(),
                    description: format!(
                        "Code after return/throw/exit will never execute:\n```\n{}\n```",
                        first_line,
                    ),
                    affected_files: vec![file_path.to_path_buf()],
                    line_start: Some(line),
                    line_end: Some(child.end_position().row as u32 + 1),
                    suggested_fix: Some(
                        "Remove unreachable code or fix control flow logic.".to_string(),
                    ),
                    estimated_effort: Some("10 minutes".to_string()),
                    category: Some("dead-code".to_string()),
                    cwe_id: Some("CWE-561".to_string()),
                    why_it_matters: Some(
                        "Unreachable code indicates logic errors and adds confusion.".to_string(),
                    ),
                    ..Default::default()
                });

                // Only flag first unreachable statement per terminator
                saw_terminator = false;
                continue;
            }

            if Self::is_terminating_node(child, lang) {
                saw_terminator = true;
            }
        }
    }

    /// Check if a node kind represents a block container in the given language.
    ///
    /// Go uses `block → statement_list → statements`, so we also match
    /// `statement_list` for Go to find the actual statement siblings.
    fn is_block_node(kind: &str, lang: Language) -> bool {
        match lang {
            Language::C | Language::Cpp => kind == "compound_statement",
            Language::JavaScript | Language::TypeScript => kind == "statement_block",
            Language::Go => kind == "block" || kind == "statement_list",
            _ => kind == "block",
        }
    }

    /// Check if a node is a terminating statement (possibly wrapped in expression_statement).
    fn is_terminating_node(node: Node, lang: Language) -> bool {
        let kind = node.kind();
        if Self::is_terminator_kind(kind, lang) {
            return true;
        }
        // Some grammars (e.g. Rust) wrap terminators in expression_statement
        if kind == "expression_statement" {
            if let Some(child) = node.named_child(0) {
                return Self::is_terminator_kind(child.kind(), lang);
            }
        }
        false
    }

    /// Check if a node kind is a terminator for the given language.
    fn is_terminator_kind(kind: &str, lang: Language) -> bool {
        match lang {
            Language::Rust => matches!(
                kind,
                "return_expression" | "break_expression" | "continue_expression"
            ),
            Language::Python => matches!(
                kind,
                "return_statement"
                    | "raise_statement"
                    | "break_statement"
                    | "continue_statement"
            ),
            _ => matches!(
                kind,
                "return_statement"
                    | "throw_statement"
                    | "break_statement"
                    | "continue_statement"
            ),
        }
    }

    /// Check if a finding should be exempt based on graph context.
    ///
    /// Exempts:
    /// - Test functions (via `is_test_function`)
    /// - Conditionally compiled functions (Rust `#[cfg(...)]`, `#[test]`, `#[bench]`)
    fn should_exempt(&self, ctx: &AnalysisContext<'_>, file_path: &Path, line: u32) -> bool {
        let path_str = file_path.to_string_lossy();
        if let Some(func) = ctx.graph.find_function_at(&path_str, line) {
            let i = ctx.graph.interner();
            let qn = func.qn(i);

            // Skip test functions
            if ctx.is_test_function(qn) {
                return true;
            }

            // Skip conditionally compiled (Rust #[cfg(...)], #[test], #[bench])
            for d in ctx.decorators(qn) {
                if d.starts_with("cfg(") || d == "test" || d == "bench" {
                    return true;
                }
            }
        }
        false
    }
}

impl Detector for UnreachableCodeDetector {
    fn name(&self) -> &'static str {
        "UnreachableCodeDetector"
    }

    fn description(&self) -> &'static str {
        "Detects unreachable code after return/throw/raise/break/continue statements"
    }

    fn category(&self) -> &'static str {
        "dead-code"
    }

    fn file_extensions(&self) -> &'static [&'static str] {
        &["py", "js", "ts", "jsx", "tsx", "java", "go", "rs", "c", "cpp", "cs"]
    }

    fn detect(
        &self,
        ctx: &crate::detectors::analysis_context::AnalysisContext,
    ) -> Result<Vec<Finding>> {
        let findings = self.find_code_after_return(ctx);
        Ok(findings)
    }
}


impl super::RegisteredDetector for UnreachableCodeDetector {
    fn create(init: &super::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::new(init.repo_path))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::store_models::ExtraProps;
    use crate::graph::{CodeEdge, CodeNode};
    use crate::graph::builder::GraphBuilder;

    // ── Verify no dead function findings ─────────────────────────────────

    #[test]
    fn test_no_dead_function_findings() {
        let mut graph = GraphBuilder::new();
        graph.add_node(
            CodeNode::function("dead_func", "src/utils.py")
                .with_qualified_name("utils::dead_func")
                .with_lines(10, 20),
        );

        let detector = UnreachableCodeDetector::new(".");
        let ctx =
            crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).unwrap();
        assert!(
            findings.is_empty(),
            "UnreachableCodeDetector should not produce dead function findings"
        );
    }

    // ── AST-based code-after-return tests ────────────────────────────────

    #[test]
    fn test_code_after_return_same_scope() {
        let code = "\
function foo() {
    return 1;
    let x = 2;
}
";
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("app.js", code);
        let findings = detector.find_code_after_return(&ctx);
        assert_eq!(findings.len(), 1, "should flag 'let x = 2' as unreachable");
        assert!(findings[0].description.contains("let x = 2"));
    }

    #[test]
    fn test_code_in_different_branch_not_flagged() {
        let code = "\
function foo(x) {
    if (x) {
        return 1;
    } else {
        let y = 2;
    }
}
";
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("app.js", code);
        let findings = detector.find_code_after_return(&ctx);
        assert!(
            findings.is_empty(),
            "else branch after return is NOT unreachable, got: {:?}",
            findings
                .iter()
                .map(|f| &f.description)
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_closing_brace_after_return_not_flagged() {
        let code = "\
function foo() {
    return 1;
}
";
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("app.js", code);
        let findings = detector.find_code_after_return(&ctx);
        assert!(
            findings.is_empty(),
            "closing brace after return should not be flagged"
        );
    }

    #[test]
    fn test_code_after_throw() {
        let code = "\
function bar() {
    throw new Error('fail');
    cleanup();
}
";
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("app.js", code);
        let findings = detector.find_code_after_return(&ctx);
        assert_eq!(findings.len(), 1, "should flag cleanup() after throw");
    }

    #[test]
    fn test_code_after_raise_python() {
        let code = "\
def foo():
    raise ValueError('bad')
    x = 1
";
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("app.py", code);
        let findings = detector.find_code_after_return(&ctx);
        assert_eq!(findings.len(), 1, "should flag x = 1 after raise");
    }

    #[test]
    fn test_nested_scope_return_does_not_flag_outer() {
        let code = "\
function foo(x) {
    if (x > 0) {
        return x;
    }
    let y = x + 1;
    return y;
}
";
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("app.js", code);
        let findings = detector.find_code_after_return(&ctx);
        assert!(
            findings.is_empty(),
            "code after if-return at outer scope is reachable, got: {:?}",
            findings
                .iter()
                .map(|f| &f.description)
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_conditional_return_not_flagged() {
        // `if (x) return null;` — the return is inside an if_statement, not a
        // direct child of the function block, so it does not trigger unreachable.
        let code = "\
function foo(x) {
    if (x) return null;
    let y = 1;
}
";
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("app.js", code);
        let findings = detector.find_code_after_return(&ctx);
        assert!(
            findings.is_empty(),
            "conditional return (if ... return) should not flag next line"
        );
    }

    #[test]
    fn test_break_in_loop_flags_code_after() {
        let code = "\
function foo() {
    while (true) {
        break;
        doSomething();
    }
}
";
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("app.js", code);
        let findings = detector.find_code_after_return(&ctx);
        assert_eq!(findings.len(), 1, "should flag doSomething() after break");
    }

    // ── AST FP prevention tests (new) ───────────────────────────────────

    #[test]
    fn test_no_fp_return_in_string_literal() {
        // String containing "return" should NOT trigger unreachable code detection.
        // AST parsing sees this as a string_literal leaf node, not a return statement.
        let code = r#"
fn foo() -> String {
    let msg = "return value is here";
    msg.to_string()
}
"#;
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("lib.rs", code);
        let findings = detector.find_code_after_return(&ctx);
        assert!(
            findings.is_empty(),
            "return inside string literal should not be flagged, got: {:?}",
            findings
                .iter()
                .map(|f| &f.description)
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_fp_return_in_raw_string_literal() {
        // Rust raw string with "return" keyword — was a major FP source.
        let code = r##"
fn foo() -> &'static str {
    let s = r#"
        return something;
        more code here;
    "#;
    s
}
"##;
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("lib.rs", code);
        let findings = detector.find_code_after_return(&ctx);
        assert!(
            findings.is_empty(),
            "return inside raw string should not be flagged, got: {:?}",
            findings
                .iter()
                .map(|f| &f.description)
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_fp_multiline_return_expression() {
        // Multi-line return expression — the entire expression is a single AST node.
        let code = r#"
fn foo() -> Result<(), Error> {
    return Err(MyError {
        code: 42,
        message: "failed",
    });
}
"#;
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("lib.rs", code);
        let findings = detector.find_code_after_return(&ctx);
        assert!(
            findings.is_empty(),
            "multi-line return should not flag continuation lines, got: {:?}",
            findings
                .iter()
                .map(|f| &f.description)
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_fp_method_chain_after_return() {
        // Method chain on return value — tree-sitter sees this as one expression.
        let code = "\
function foo() {
    return items
        .filter(x => x > 0)
        .map(x => x * 2)
        .reduce((a, b) => a + b, 0);
}
";
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("app.js", code);
        let findings = detector.find_code_after_return(&ctx);
        assert!(
            findings.is_empty(),
            "method chain continuation should not be flagged, got: {:?}",
            findings
                .iter()
                .map(|f| &f.description)
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_rust_return_with_match() {
        // `return match x { ... }` is a single expression spanning multiple lines.
        let code = r#"
fn foo(x: i32) -> &'static str {
    return match x {
        0 => "zero",
        1 => "one",
        _ => "other",
    };
}
"#;
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("lib.rs", code);
        let findings = detector.find_code_after_return(&ctx);
        assert!(
            findings.is_empty(),
            "return match should not flag match arms, got: {:?}",
            findings
                .iter()
                .map(|f| &f.description)
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_python_return_in_docstring() {
        // Python docstring containing "return" should not trigger.
        let code = r#"
def foo():
    """This function will return a value.

    Returns:
        The return value.
    """
    return 42
"#;
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("app.py", code);
        let findings = detector.find_code_after_return(&ctx);
        assert!(
            findings.is_empty(),
            "return in docstring should not be flagged, got: {:?}",
            findings
                .iter()
                .map(|f| &f.description)
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_true_positive_rust_code_after_return() {
        // Genuine unreachable code in Rust.
        let code = r#"
fn foo() -> i32 {
    return 42;
    let x = 1;
}
"#;
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("lib.rs", code);
        let findings = detector.find_code_after_return(&ctx);
        assert_eq!(
            findings.len(),
            1,
            "should flag unreachable code after return in Rust"
        );
    }

    #[test]
    fn test_true_positive_go_code_after_return() {
        let code = r#"
package main

func foo() int {
    return 42
    x := 1
    return x
}
"#;
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("main.go", code);
        let findings = detector.find_code_after_return(&ctx);
        assert!(
            !findings.is_empty(),
            "should flag unreachable code after return in Go"
        );
    }

    // ── Comment-after-terminator tests ────────────────────────────────

    #[test]
    fn test_no_fp_comment_after_return_js() {
        let code = "\
function foo() {
    return 1;
    // This is a comment explaining the early return
}
";
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("app.js", code);
        let findings = detector.find_code_after_return(&ctx);
        assert!(
            findings.is_empty(),
            "comment after return should not be flagged, got: {:?}",
            findings
                .iter()
                .map(|f| &f.description)
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_fp_comment_after_return_rust() {
        let code = r#"
fn foo() -> i32 {
    return 42;
    // TODO: handle edge case later
}
"#;
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("lib.rs", code);
        let findings = detector.find_code_after_return(&ctx);
        assert!(
            findings.is_empty(),
            "comment after return in Rust should not be flagged, got: {:?}",
            findings
                .iter()
                .map(|f| &f.description)
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_fp_block_comment_after_return() {
        let code = "\
function foo() {
    return 1;
    /* This block comment should not be flagged */
}
";
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("app.js", code);
        let findings = detector.find_code_after_return(&ctx);
        assert!(
            findings.is_empty(),
            "block comment after return should not be flagged, got: {:?}",
            findings
                .iter()
                .map(|f| &f.description)
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_real_code_after_comment_after_return_still_flagged() {
        // Comment after return is fine, but real code after that comment
        // should still be flagged.
        let code = "\
function foo() {
    return 1;
    // This comment is fine
    let x = 2;
}
";
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("app.js", code);
        let findings = detector.find_code_after_return(&ctx);
        assert_eq!(
            findings.len(),
            1,
            "real code after comment-after-return should be flagged"
        );
    }

    // ── Conditional compilation exemption tests ─────────────────────────

    #[test]
    fn test_rust_cfg_test_attribute_skipped() {
        let mut graph = GraphBuilder::new();
        let i = graph.interner();

        let func = CodeNode::function("helper_in_test", "src/lib.rs")
            .with_qualified_name("lib::helper_in_test")
            .with_lines(10, 20);
        graph.add_node(func);

        let qn_key = i.intern("lib::helper_in_test");
        let ep = ExtraProps {
            decorators: Some(i.intern("cfg(test)")),
            ..Default::default()
        };
        graph.set_extra_props(qn_key, ep);

        assert!(
            UnreachableCodeDetector::is_conditionally_compiled_rust(&graph, &func),
            "Function with #[cfg(test)] should be recognized as conditionally compiled"
        );
    }

    #[test]
    fn test_rust_cfg_feature_attribute_skipped() {
        let mut graph = GraphBuilder::new();
        let i = graph.interner();

        let func = CodeNode::function("optional_feature", "src/lib.rs")
            .with_qualified_name("lib::optional_feature")
            .with_lines(5, 15);
        graph.add_node(func);

        let qn_key = i.intern("lib::optional_feature");
        let ep = ExtraProps {
            decorators: Some(i.intern("cfg(feature = \"serde\")")),
            ..Default::default()
        };
        graph.set_extra_props(qn_key, ep);

        assert!(
            UnreachableCodeDetector::is_conditionally_compiled_rust(&graph, &func),
            "Function with #[cfg(feature = ...)] should be conditionally compiled"
        );
    }

    #[test]
    fn test_rust_test_attribute_skipped() {
        let mut graph = GraphBuilder::new();
        let i = graph.interner();

        let func = CodeNode::function("my_test", "src/lib.rs")
            .with_qualified_name("lib::my_test")
            .with_lines(50, 60);
        graph.add_node(func);

        let qn_key = i.intern("lib::my_test");
        let ep = ExtraProps {
            decorators: Some(i.intern("test")),
            ..Default::default()
        };
        graph.set_extra_props(qn_key, ep);

        assert!(
            UnreachableCodeDetector::is_conditionally_compiled_rust(&graph, &func),
            "#[test] functions should be conditionally compiled"
        );
    }

    #[test]
    fn test_rust_bench_attribute_skipped() {
        let mut graph = GraphBuilder::new();
        let i = graph.interner();

        let func = CodeNode::function("bench_algo", "src/bench.rs")
            .with_qualified_name("bench::bench_algo")
            .with_lines(10, 30);
        graph.add_node(func);

        let qn_key = i.intern("bench::bench_algo");
        let ep = ExtraProps {
            decorators: Some(i.intern("bench")),
            ..Default::default()
        };
        graph.set_extra_props(qn_key, ep);

        assert!(
            UnreachableCodeDetector::is_conditionally_compiled_rust(&graph, &func),
            "#[bench] functions should be conditionally compiled"
        );
    }

    #[test]
    fn test_rust_multiple_attrs_with_cfg() {
        let mut graph = GraphBuilder::new();
        let i = graph.interner();

        let func = CodeNode::function("complex_func", "src/lib.rs")
            .with_qualified_name("lib::complex_func")
            .with_lines(1, 10);
        graph.add_node(func);

        let qn_key = i.intern("lib::complex_func");
        let ep = ExtraProps {
            decorators: Some(i.intern("inline,cfg(target_os = \"linux\")")),
            ..Default::default()
        };
        graph.set_extra_props(qn_key, ep);

        assert!(
            UnreachableCodeDetector::is_conditionally_compiled_rust(&graph, &func),
            "Function with cfg among multiple attrs should be conditionally compiled"
        );
    }

    #[test]
    fn test_rust_non_conditional_attr_not_skipped() {
        let mut graph = GraphBuilder::new();
        let i = graph.interner();

        let func = CodeNode::function("normal_func", "src/lib.rs")
            .with_qualified_name("lib::normal_func")
            .with_lines(1, 10);
        graph.add_node(func);

        let qn_key = i.intern("lib::normal_func");
        let ep = ExtraProps {
            decorators: Some(i.intern("inline,derive(Debug)")),
            ..Default::default()
        };
        graph.set_extra_props(qn_key, ep);

        assert!(
            !UnreachableCodeDetector::is_conditionally_compiled_rust(&graph, &func),
            "Function with only #[inline] and #[derive] should NOT be conditionally compiled"
        );
    }

    #[test]
    fn test_rust_no_extra_props_not_skipped() {
        let mut graph = GraphBuilder::new();

        let func = CodeNode::function("bare_func", "src/lib.rs")
            .with_qualified_name("lib::bare_func")
            .with_lines(1, 10);
        graph.add_node(func);

        assert!(
            !UnreachableCodeDetector::is_conditionally_compiled_rust(&graph, &func),
            "Function without extra props should NOT be conditionally compiled"
        );
    }

    // --- Rust cfg module block tests ---

    #[test]
    fn test_rust_cfg_test_module_block() {
        let content = r#"
pub fn public_api() {
    // ...
}

#[cfg(test)]
mod tests {
    fn helper_for_tests() {
        // This should be exempt
    }
}
"#;
        assert!(
            UnreachableCodeDetector::is_in_rust_cfg_module(9, content),
            "Function inside #[cfg(test)] mod should be in conditional block"
        );
    }

    #[test]
    fn test_rust_cfg_test_module_function_outside() {
        let content = r#"
pub fn public_api() {
    // ...
}

#[cfg(test)]
mod tests {
    fn helper_for_tests() {
        // ...
    }
}
"#;
        assert!(
            !UnreachableCodeDetector::is_in_rust_cfg_module(2, content),
            "Function OUTSIDE #[cfg(test)] mod should not be in conditional block"
        );
    }

    #[test]
    fn test_rust_cfg_feature_module_block() {
        let content = r#"
#[cfg(feature = "serde")]
mod serde_impl {
    fn serialize_helper() {
        // conditional
    }
}
"#;
        assert!(
            UnreachableCodeDetector::is_in_rust_cfg_module(4, content),
            "Function inside #[cfg(feature)] mod should be in conditional block"
        );
    }

    // --- C/C++ preprocessor guard tests ---

    #[test]
    fn test_c_ifdef_guard() {
        let content = r#"
#include <stdio.h>

void normal_func() {
    // always compiled
}

#ifdef DEBUG
void debug_helper() {
    printf("debug\n");
}
#endif
"#;
        assert!(
            UnreachableCodeDetector::is_in_preprocessor_guard(9, content),
            "Function inside #ifdef DEBUG should be in preprocessor guard"
        );
    }

    #[test]
    fn test_c_ifdef_guard_normal_func() {
        let content = r#"
#include <stdio.h>

void normal_func() {
    // always compiled
}

#ifdef DEBUG
void debug_helper() {
    printf("debug\n");
}
#endif
"#;
        assert!(
            !UnreachableCodeDetector::is_in_preprocessor_guard(4, content),
            "Function outside #ifdef should NOT be in preprocessor guard"
        );
    }

    #[test]
    fn test_c_ifndef_guard() {
        let content = r#"
#ifndef NDEBUG
void assert_helper() {
    // only in debug builds
}
#endif
"#;
        assert!(
            UnreachableCodeDetector::is_in_preprocessor_guard(3, content),
            "Function inside #ifndef should be in preprocessor guard"
        );
    }

    #[test]
    fn test_c_nested_ifdef() {
        let content = r#"
#ifdef PLATFORM_LINUX
#ifdef HAS_FEATURE_X
void feature_x_linux() {
    // double-guarded
}
#endif
#endif
"#;
        assert!(
            UnreachableCodeDetector::is_in_preprocessor_guard(4, content),
            "Function inside nested #ifdef should be in preprocessor guard"
        );
    }

    #[test]
    fn test_c_if_expression_guard() {
        let content = r#"
#if defined(WIN32) || defined(_WIN64)
void windows_specific() {
    // Windows only
}
#endif
"#;
        assert!(
            UnreachableCodeDetector::is_in_preprocessor_guard(3, content),
            "Function inside #if should be in preprocessor guard"
        );
    }

    #[test]
    fn test_c_after_endif_not_guarded() {
        let content = r#"
#ifdef DEBUG
void debug_only() {}
#endif

void after_endif() {
    // This is NOT guarded
}
"#;
        assert!(
            !UnreachableCodeDetector::is_in_preprocessor_guard(6, content),
            "Function after #endif should NOT be in preprocessor guard"
        );
    }

    // --- Python __name__ guard tests ---

    #[test]
    fn test_python_name_guard() {
        let content = r#"
def public_api():
    pass

if __name__ == "__main__":
    def run_main():
        public_api()
"#;
        assert!(
            UnreachableCodeDetector::is_in_python_name_guard(6, content),
            "Function inside if __name__ guard should be detected"
        );
    }

    #[test]
    fn test_python_name_guard_outside() {
        let content = r#"
def public_api():
    pass

if __name__ == "__main__":
    def run_main():
        public_api()
"#;
        assert!(
            !UnreachableCodeDetector::is_in_python_name_guard(2, content),
            "Function outside if __name__ guard should NOT be detected"
        );
    }

    #[test]
    fn test_python_name_guard_single_equals() {
        let content = r#"
if __name__ == '__main__':
    def helper():
        pass
"#;
        assert!(
            UnreachableCodeDetector::is_in_python_name_guard(3, content),
            "Function inside if __name__ == '__main__' should be detected"
        );
    }

    // --- is_in_conditional_block dispatch tests ---

    #[test]
    fn test_conditional_block_dispatch_rust() {
        let content = r#"
#[cfg(test)]
mod tests {
    fn test_helper() {}
}
"#;
        assert!(
            UnreachableCodeDetector::is_in_conditional_block("src/lib.rs", 4, content),
            "Rust dispatch should detect cfg(test) module"
        );
    }

    #[test]
    fn test_conditional_block_dispatch_c() {
        let content = r#"
#ifdef TEST
void test_func() {}
#endif
"#;
        assert!(
            UnreachableCodeDetector::is_in_conditional_block("src/main.c", 3, content),
            "C dispatch should detect #ifdef guard"
        );
    }

    #[test]
    fn test_conditional_block_dispatch_cpp() {
        let content = r#"
#ifdef TEST
void test_func() {}
#endif
"#;
        assert!(
            UnreachableCodeDetector::is_in_conditional_block("src/main.cpp", 3, content),
            "C++ dispatch should detect #ifdef guard"
        );
    }

    #[test]
    fn test_conditional_block_dispatch_python() {
        let content = r#"
if __name__ == "__main__":
    def main():
        pass
"#;
        assert!(
            UnreachableCodeDetector::is_in_conditional_block("app.py", 3, content),
            "Python dispatch should detect __name__ guard"
        );
    }

    #[test]
    fn test_conditional_block_dispatch_js_not_affected() {
        let content = "function foo() {}\n";
        assert!(
            !UnreachableCodeDetector::is_in_conditional_block("app.js", 1, content),
            "JS files should not match any conditional block pattern"
        );
    }

    // ── TSX/JSX multiline return tests ──────────────────────────────────

    #[test]
    fn test_no_fp_tsx_multiline_jsx_return() {
        // React component returning multiline JSX — should not flag the next
        // export/function as unreachable. This was a false positive because
        // tree-sitter was parsing .tsx files with the plain TypeScript grammar
        // (which doesn't understand JSX), producing error nodes.
        let code = r#"
export default function Page() {
  return (
    <div>
      <h1>Hello</h1>
    </div>
  )
}

export function Other() {
  return <span>World</span>
}
"#;
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("page.tsx", code);
        let findings = detector.find_code_after_return(&ctx);
        assert!(
            findings.is_empty(),
            "TSX multiline JSX return should not flag next function as unreachable, got: {:?}",
            findings
                .iter()
                .map(|f| &f.description)
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_fp_tsx_component_with_hooks() {
        // Typical React component with hooks and JSX return.
        let code = r#"
import React, { useState } from 'react';

export function Counter() {
  const [count, setCount] = useState(0);
  return (
    <button onClick={() => setCount(count + 1)}>
      Count: {count}
    </button>
  )
}
"#;
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("Counter.tsx", code);
        let findings = detector.find_code_after_return(&ctx);
        assert!(
            findings.is_empty(),
            "TSX component with hooks should not produce false positives, got: {:?}",
            findings
                .iter()
                .map(|f| &f.description)
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_true_positive_tsx_code_after_return() {
        // Genuine unreachable code in a TSX file should still be flagged.
        let code = r#"
export function Broken() {
  return <div>Hello</div>;
  const x = 42;
}
"#;
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("Broken.tsx", code);
        let findings = detector.find_code_after_return(&ctx);
        assert_eq!(
            findings.len(),
            1,
            "genuine unreachable code in TSX should still be flagged"
        );
    }

    // ── Helper functions ────────────────────────────────────────────────

    /// Build a minimal AnalysisContext with a single file for testing.
    fn make_test_ctx_with_file(filename: &str, content: &str) -> AnalysisContext<'static> {
        use crate::detectors::detector_context::{ContentFlags, DetectorContext};
        use crate::detectors::file_index::FileIndex;
        use crate::detectors::taint::centralized::CentralizedTaintResults;
        use std::collections::HashMap;
        use std::path::Path;
        use std::sync::Arc;

        // Leak a GraphBuilder so we can return AnalysisContext<'static>
        let graph: &'static crate::graph::CodeGraph = Box::leak(Box::new(GraphBuilder::new().freeze()));

        let file_data = vec![(
            PathBuf::from(filename),
            Arc::from(content),
            ContentFlags::empty(),
        )];

        let files = Arc::new(FileIndex::new(file_data));
        let functions = Arc::new(HashMap::new());
        let taint = Arc::new(CentralizedTaintResults {
            cross_function: HashMap::new(),
            intra_function: HashMap::new(),
        });

        let (det_ctx, _file_data) =
            DetectorContext::build(graph, &[], None, Path::new("/repo"));
        let detector_ctx = Arc::new(det_ctx);

        AnalysisContext {
            graph,
            files,
            functions,
            taint,
            detector_ctx,
            hmm_classifications: Arc::new(HashMap::new()),
            resolver: Arc::new(crate::calibrate::ThresholdResolver::default()),
            reachability: Arc::new(crate::detectors::reachability::ReachabilityIndex::empty()),
            public_api: Arc::new(std::collections::HashSet::new()),
            module_metrics: Arc::new(HashMap::new()),
            class_cohesion: Arc::new(HashMap::new()),
            decorator_index: Arc::new(HashMap::new()),
            git_churn: Arc::new(HashMap::new()),
            co_change_summary: Arc::new(HashMap::new()),
            co_change_matrix: None,
        }
    }
}
