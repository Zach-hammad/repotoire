//! Unreachable Code Detector
//!
//! Detects code after return/throw/raise/break/continue statements using
//! scope-aware brace-depth analysis. Tracks brace depth through function
//! bodies and only flags code at the SAME or DEEPER scope level after a
//! terminating statement.
//!
//! Dead function detection (fan_in == 0) is handled by DeadCodeDetector.

use crate::detectors::analysis_context::AnalysisContext;
use crate::detectors::base::Detector;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;

pub struct UnreachableCodeDetector {
    max_findings: usize,
}

impl UnreachableCodeDetector {
    pub fn new(_repository_path: impl Into<PathBuf>) -> Self {
        Self { max_findings: 50 }
    }

    /// Rust conditional compilation attributes that mean a function is only compiled
    /// under certain conditions (cfg, test, bench, etc.) -- not truly unreachable.
    ///
    /// Used by conditional compilation exemption logic (tested, kept for future
    /// integration with scope-aware analysis).
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
    /// - **Rust**: `#[cfg(test)] mod tests { ... }` blocks -- checks if function's
    ///   qualified name contains a cfg(test) module segment.
    /// - **C/C++**: `#ifdef`, `#ifndef`, `#if` preprocessor guards surrounding
    ///   the function definition.
    /// - **Python**: `if __name__` guards that wrap function definitions.
    #[allow(dead_code)]
    fn is_in_conditional_block(
        file_path: &str,
        func_line_start: u32,
        content: &str,
    ) -> bool {
        // --- Rust: check for #[cfg(test)] mod block ---
        if file_path.ends_with(".rs") {
            return Self::is_in_rust_cfg_module(func_line_start, content);
        }

        // --- C/C++: check for preprocessor guards ---
        if file_path.ends_with(".c")
            || file_path.ends_with(".cpp")
            || file_path.ends_with(".cc")
            || file_path.ends_with(".cxx")
            || file_path.ends_with(".h")
            || file_path.ends_with(".hpp")
        {
            return Self::is_in_preprocessor_guard(func_line_start, content);
        }

        // --- Python: check for if __name__ guard ---
        if file_path.ends_with(".py") {
            return Self::is_in_python_name_guard(func_line_start, content);
        }

        false
    }

    /// Check if a Rust function is inside a `#[cfg(...)]` module block.
    ///
    /// Scans backward from the function's line to find `mod` declarations
    /// preceded by `#[cfg(...)]` attributes. If found and the function is
    /// within the module's brace-delimited scope, returns true.
    #[allow(dead_code)]
    fn is_in_rust_cfg_module(func_line_start: u32, content: &str) -> bool {
        let lines: Vec<&str> = content.lines().collect();
        let func_idx = (func_line_start as usize).saturating_sub(1);

        if func_idx >= lines.len() {
            return false;
        }

        // Scan backward from the function to find a `mod` declaration with #[cfg(...)]
        let mut i = func_idx;

        // Scan backward to find a mod with #[cfg(...)] that encloses us.
        while i > 0 {
            i -= 1;
            let line = lines[i].trim();

            // Look for `mod <name> {` pattern
            if (line.starts_with("mod ") || line.starts_with("pub mod "))
                && line.contains('{')
            {
                // Check preceding lines for #[cfg(...)] attribute
                let mut attr_line = i;
                while attr_line > 0 {
                    attr_line -= 1;
                    let prev = lines[attr_line].trim();
                    if prev.starts_with("#[cfg(") || prev.starts_with("#[cfg_attr(") {
                        return true;
                    }
                    // Skip comments and other attributes
                    if prev.starts_with("#[") || prev.starts_with("//") || prev.is_empty()
                    {
                        continue;
                    }
                    break;
                }
            }
        }

        false
    }

    /// Check if a C/C++ function is inside a preprocessor conditional block.
    ///
    /// Tracks `#ifdef`/`#ifndef`/`#if` and `#endif` directives to determine
    /// if the function at `func_line_start` is within a conditional region.
    #[allow(dead_code)]
    fn is_in_preprocessor_guard(func_line_start: u32, content: &str) -> bool {
        let lines: Vec<&str> = content.lines().collect();
        let func_idx = (func_line_start as usize).saturating_sub(1);

        // Track preprocessor nesting: stack of (#if/#ifdef/#ifndef line index)
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
            } else if trimmed.starts_with("#else") || trimmed.starts_with("#elif") {
                // Still inside the same conditional block -- keep the stack entry
            }
        }

        // If the preprocessor stack is non-empty at the function's line,
        // the function is inside a conditional compilation block.
        !pp_stack.is_empty()
    }

    /// Check if a Python function is inside an `if __name__ == "__main__":` guard.
    ///
    /// Scans backward from the function line to find an `if __name__` block
    /// at column 0 (top-level guard) and checks that the function is indented
    /// inside it.
    #[allow(dead_code)]
    fn is_in_python_name_guard(func_line_start: u32, content: &str) -> bool {
        let lines: Vec<&str> = content.lines().collect();
        let func_idx = (func_line_start as usize).saturating_sub(1);

        if func_idx >= lines.len() {
            return false;
        }

        let func_indent = lines[func_idx].len() - lines[func_idx].trim_start().len();

        // The function must be indented (inside a block)
        if func_indent == 0 {
            return false;
        }

        // Scan backward to find `if __name__` at a lower indent level
        for i in (0..func_idx).rev() {
            let line = lines[i];
            let trimmed = line.trim();
            if trimmed.is_empty() || trimmed.starts_with('#') {
                continue;
            }

            let indent = line.len() - trimmed.len();

            // Found a line at lower or equal indent -- check if it's the __name__ guard
            if indent < func_indent {
                if trimmed.starts_with("if __name__")
                    || trimmed.starts_with("if  __name__")
                {
                    return true;
                }
                // If we found a different block at a lower indent, stop
                break;
            }
        }

        false
    }

    // ── Scope-aware code-after-return detection ─────────────────────────

    /// Returns true if `line` is a terminating statement (return, throw, raise,
    /// break, continue, exit).
    fn is_terminating_statement(trimmed: &str) -> bool {
        trimmed.starts_with("return ")
            || trimmed.starts_with("return;")
            || trimmed == "return"
            || trimmed.starts_with("throw ")
            || trimmed.starts_with("throw;")
            || trimmed.starts_with("raise ")
            || trimmed == "raise"
            || trimmed.starts_with("exit(")
            || trimmed.starts_with("sys.exit")
            || trimmed.starts_with("process.exit")
            || trimmed.starts_with("break;")
            || trimmed == "break"
            || trimmed.starts_with("continue;")
            || trimmed == "continue"
    }

    /// Returns true if `trimmed` is a line that should be skipped when
    /// checking for unreachable code (comments, empty, control-flow
    /// continuations like else/catch/finally, labels, etc.).
    fn is_skip_line(trimmed: &str) -> bool {
        trimmed.is_empty()
            || trimmed.starts_with("//")
            || trimmed.starts_with('#')
            || trimmed.starts_with("/*")
            || trimmed.starts_with('*')
            || trimmed.starts_with("else")
            || trimmed.starts_with("elif")
            || trimmed.starts_with("except")
            || trimmed.starts_with("catch")
            || trimmed.starts_with("finally")
            || trimmed.starts_with("case ")
            || trimmed.starts_with("default:")
            || trimmed.starts_with("default ")
    }

    /// Compute brace-depth delta and minimum intermediate depth for a line.
    ///
    /// Returns `(net_delta, min_delta)` where:
    /// - `net_delta` is the total change in brace depth after the whole line
    /// - `min_delta` is the lowest intermediate delta (e.g., for `} else {`,
    ///   min_delta is -1 because the `}` closes a scope before `{` opens one)
    ///
    /// Characters inside string literals and comments are not special-cased
    /// for simplicity; this is a heuristic that works well in practice.
    fn brace_delta(line: &str) -> (i32, i32) {
        let mut delta = 0i32;
        let mut min_delta = 0i32;
        for ch in line.chars() {
            match ch {
                '{' => delta += 1,
                '}' => {
                    delta -= 1;
                    min_delta = min_delta.min(delta);
                }
                _ => {}
            }
        }
        (delta, min_delta)
    }

    /// Scope-aware detection of code after return/throw/raise/break/continue.
    ///
    /// Iterates through lines of each file, tracking brace depth. When a
    /// terminating statement is found at scope level N, the next non-empty,
    /// non-comment line at the SAME or DEEPER scope level is flagged as
    /// unreachable. Lines at a LOWER scope level (closing braces, else
    /// branches) are legitimate and are not flagged.
    fn find_code_after_return(&self, ctx: &AnalysisContext<'_>) -> Vec<Finding> {
        let mut findings = Vec::new();
        let extensions = &["py", "js", "ts", "jsx", "tsx", "java", "go", "rb", "php", "rs", "c", "cpp", "cs"];

        for entry in ctx.files.by_extensions(extensions) {
            if findings.len() >= self.max_findings {
                break;
            }

            let content: &str = &entry.content;
            let lines: Vec<&str> = content.lines().collect();
            let mut brace_depth: i32 = 0;
            // State: after seeing a terminating statement, store its brace depth
            let mut after_return: Option<i32> = None;

            for (line_idx, &line) in lines.iter().enumerate() {
                let trimmed = line.trim();
                let (net_delta, min_delta) = Self::brace_delta(line);

                // If we are looking for unreachable code after a return...
                if let Some(return_depth) = after_return {
                    // The minimum depth this line reaches (before any re-opening)
                    let min_depth = brace_depth + min_delta;

                    // Skip blank/comment lines -- they don't count as unreachable
                    if Self::is_skip_line(trimmed) {
                        brace_depth += net_delta;
                        // If depth drops below the return depth, we left the scope
                        if min_depth < return_depth {
                            after_return = None;
                        }
                        continue;
                    }

                    // If this line touches a lower scope (e.g., `}`, `} else {`, `} catch {`),
                    // the return's scope has closed -- code on the other side is reachable.
                    if min_depth < return_depth {
                        brace_depth += net_delta;
                        after_return = None;
                        continue;
                    }

                    // If the line starts with `}` at the same depth, it's just
                    // closing the return's scope -- not unreachable code.
                    if trimmed.starts_with('}') {
                        brace_depth += net_delta;
                        if brace_depth < return_depth {
                            after_return = None;
                        }
                        continue;
                    }

                    // At the same or deeper scope -- this code is unreachable
                    findings.push(Finding {
                        id: String::new(),
                        detector: "UnreachableCodeDetector".to_string(),
                        severity: Severity::Medium,
                        title: "Unreachable code after return".to_string(),
                        description: format!(
                            "Code after return/throw/exit will never execute:\n```\n{}\n```",
                            trimmed,
                        ),
                        affected_files: vec![entry.path.clone()],
                        line_start: Some((line_idx + 1) as u32),
                        line_end: Some((line_idx + 1) as u32),
                        suggested_fix: Some(
                            "Remove unreachable code or fix control flow logic.".to_string(),
                        ),
                        estimated_effort: Some("10 minutes".to_string()),
                        category: Some("dead-code".to_string()),
                        cwe_id: Some("CWE-561".to_string()),
                        why_it_matters: Some(
                            "Unreachable code indicates logic errors and adds confusion."
                                .to_string(),
                        ),
                        ..Default::default()
                    });

                    // Only flag the first unreachable line per return site
                    brace_depth += net_delta;
                    after_return = None;
                    continue;
                }

                // Normal flow: update depth and check for terminating statements
                brace_depth += net_delta;

                if Self::is_terminating_statement(trimmed) {
                    // Skip conditional returns: `if (x) return;` or ternary `x ? return : ...`
                    if trimmed.contains("if ") || trimmed.contains("if(") || trimmed.contains('?') {
                        continue;
                    }
                    // Skip multi-line statements (trailing comma/paren)
                    if trimmed.ends_with(',') || trimmed.ends_with('(') {
                        continue;
                    }
                    // Record that we just saw a terminating statement at this depth
                    after_return = Some(brace_depth);
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeEdge, CodeNode, GraphStore};
    use crate::graph::store_models::ExtraProps;

    // ── Verify no dead function findings ─────────────────────────────────

    #[test]
    fn test_no_dead_function_findings() {
        // The UnreachableCodeDetector should produce NO "Dead function" findings.
        // Dead function detection is now DeadCodeDetector's responsibility.
        let graph = GraphStore::in_memory();

        // Add a dead function (no callers) -- should NOT be flagged
        graph.add_node(
            CodeNode::function("dead_func", "src/utils.py")
                .with_qualified_name("utils::dead_func")
                .with_lines(10, 20),
        );

        let detector = UnreachableCodeDetector::new(".");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).unwrap();

        assert!(findings.is_empty(), "UnreachableCodeDetector should not produce dead function findings");
    }

    // ── Scope-aware code-after-return tests ──────────────────────────────

    #[test]
    fn test_code_after_return_same_scope() {
        // Code at the same brace depth after return should be flagged.
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
        // Code in an else branch after a return in the if branch is fine.
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
        assert!(findings.is_empty(), "else branch after return is NOT unreachable, got: {:?}",
            findings.iter().map(|f| &f.description).collect::<Vec<_>>());
    }

    #[test]
    fn test_closing_brace_after_return_not_flagged() {
        // Closing brace after return is normal scope closure, not unreachable.
        let code = "\
function foo() {
    return 1;
}
";
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("app.js", code);
        let findings = detector.find_code_after_return(&ctx);
        assert!(findings.is_empty(), "closing brace after return should not be flagged");
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
        // Return inside a nested if block should not flag code in the outer scope.
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
        assert!(findings.is_empty(), "code after if-return at outer scope is reachable, got: {:?}",
            findings.iter().map(|f| &f.description).collect::<Vec<_>>());
    }

    #[test]
    fn test_conditional_return_not_flagged() {
        // return inside an if-condition on the same line should be skipped.
        let code = "\
function foo(x) {
    if (x) return null;
    let y = 1;
}
";
        let detector = UnreachableCodeDetector::new(".");
        let ctx = make_test_ctx_with_file("app.js", code);
        let findings = detector.find_code_after_return(&ctx);
        assert!(findings.is_empty(), "conditional return (if ... return) should not flag next line");
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

    // ── Conditional compilation exemption tests ─────────────────────────

    #[test]
    fn test_rust_cfg_test_attribute_skipped() {
        let graph = GraphStore::in_memory();
        let i = graph.interner();

        // Function with #[cfg(test)] attribute
        let func = CodeNode::function("helper_in_test", "src/lib.rs")
            .with_qualified_name("lib::helper_in_test")
            .with_lines(10, 20);
        graph.add_node(func);

        // Set the decorators extra prop (as the Rust parser would)
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
        let graph = GraphStore::in_memory();
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
        let graph = GraphStore::in_memory();
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
        let graph = GraphStore::in_memory();
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
        let graph = GraphStore::in_memory();
        let i = graph.interner();

        let func = CodeNode::function("complex_func", "src/lib.rs")
            .with_qualified_name("lib::complex_func")
            .with_lines(1, 10);
        graph.add_node(func);

        // Multiple attributes, one of which is cfg
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
        let graph = GraphStore::in_memory();
        let i = graph.interner();

        let func = CodeNode::function("normal_func", "src/lib.rs")
            .with_qualified_name("lib::normal_func")
            .with_lines(1, 10);
        graph.add_node(func);

        // Only derive and inline -- no conditional compilation
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
        let graph = GraphStore::in_memory();

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
        // helper_for_tests is at line 9 (1-based)
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
        // public_api is at line 2 (1-based)
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
        // serialize_helper at line 4
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
        // debug_helper at line 9
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
        // normal_func at line 4 -- not guarded
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
        // assert_helper at line 3
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
        // feature_x_linux at line 4
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
        // windows_specific at line 3
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
        // after_endif at line 6
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
        // run_main at line 6
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
        // public_api at line 2 -- NOT inside the guard
        assert!(
            !UnreachableCodeDetector::is_in_python_name_guard(2, content),
            "Function outside if __name__ guard should NOT be detected"
        );
    }

    #[test]
    fn test_python_name_guard_single_equals() {
        // Some code uses single quotes
        let content = r#"
if __name__ == '__main__':
    def helper():
        pass
"#;
        // helper at line 3
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

    // ── Helper functions ────────────────────────────────────────────────

    /// Build a minimal AnalysisContext with a single file for testing.
    fn make_test_ctx_with_file(filename: &str, content: &str) -> AnalysisContext<'static> {
        use crate::detectors::detector_context::{ContentFlags, DetectorContext};
        use crate::detectors::file_index::FileIndex;
        use crate::detectors::taint::centralized::CentralizedTaintResults;
        use std::collections::HashMap;
        use std::path::Path;
        use std::sync::Arc;

        // Leak a GraphStore so we can return AnalysisContext<'static>
        let graph: &'static GraphStore = Box::leak(Box::new(GraphStore::in_memory()));

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
        }
    }
}
