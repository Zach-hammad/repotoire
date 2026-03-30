//! Dead Store Detector
//!
//! ValueStore-based detection of variables assigned but never read within
//! the same function body.  Uses the graph to iterate over functions and
//! the ValueStore for assignment data, eliminating the old regex approach.

use crate::detectors::analysis_context::AnalysisContext;
use crate::detectors::base::{Detector, DetectorConfig, DetectorScope};
use crate::graph::store_models::NodeKind;
use crate::graph::GraphQueryExt;
use crate::models::{Finding, Severity};
use crate::values::types::SymbolicValue;
use anyhow::Result;
use std::path::{Path, PathBuf};
use tracing::info;

/// Variables that should never be flagged as dead stores.
const SKIP_VARS: &[&str] = &["_", "self", "Self", "this", "cls", "super"];

/// Config/settings file basenames exempt from module-level dead-store detection.
const CONFIG_FILES: &[&str] = &[
    "__init__.py",
    "conf.py",
    "config.py",
    "settings.py",
    "constants.py",
    "defaults.py",
    "conftest.py",
];

pub struct DeadStoreDetector {
    #[allow(dead_code)]
    repository_path: PathBuf,
    max_findings: usize,
}

impl DeadStoreDetector {
    crate::detectors::detector_new!(50);

    /// Check whether `var` appears as a word-boundary token in `line`.
    fn word_appears_in_line(var: &str, line: &str) -> bool {
        let var_bytes = var.as_bytes();
        let line_bytes = line.as_bytes();
        if var_bytes.len() > line_bytes.len() {
            return false;
        }
        let mut start = 0;
        while let Some(pos) = line[start..].find(var) {
            let abs = start + pos;
            let before_ok = abs == 0 || !is_ident_char(line_bytes[abs - 1]);
            let after_ok =
                abs + var.len() >= line_bytes.len() || !is_ident_char(line_bytes[abs + var.len()]);
            if before_ok && after_ok {
                return true;
            }
            start = abs + 1;
        }
        false
    }

    /// Check if `var` is read in the source lines between `from_line`
    /// (exclusive) and `to_line` (inclusive), 0-indexed relative to the function.
    ///
    /// The `to_line` is included because a reassignment like `x = x + 1` still
    /// reads `x` on the RHS.  Pure reassignments (`x = other`) on intermediate
    /// or the final line are skipped.
    fn is_read_in_range(var: &str, func_lines: &[&str], from_line: usize, to_line: usize) -> bool {
        // Include to_line itself (+1 to make the range inclusive of to_line)
        let end = (to_line + 1).min(func_lines.len());
        for idx in (from_line + 1)..end {
            let line = func_lines[idx];
            let trimmed = line.trim();

            // Skip blank lines and comments
            if trimmed.is_empty()
                || trimmed.starts_with("//")
                || trimmed.starts_with('#')
                || trimmed.starts_with('*')
                || trimmed.starts_with("/*")
            {
                continue;
            }

            if !Self::word_appears_in_line(var, line) {
                continue;
            }

            // If the line is a pure reassignment of `var` where `var` does not
            // appear on the RHS, it doesn't count as a read.
            if Self::is_pure_reassignment(var, trimmed) {
                continue;
            }

            return true;
        }
        false
    }

    /// Returns true if `trimmed` is a simple `var = <expr>` where `var` does
    /// NOT appear in `<expr>`.  This is intentionally conservative: anything
    /// that isn't clearly a pure reassignment is treated as a potential read.
    fn is_pure_reassignment(var: &str, trimmed: &str) -> bool {
        // Match patterns like `var = ...` or `var := ...`
        let rest = if let Some(rest) = trimmed.strip_prefix(var) {
            rest.trim_start()
        } else {
            return false;
        };

        // After stripping `var`, the next non-whitespace char should be `=` or `:=`
        let rhs = if let Some(r) = rest.strip_prefix(":=") {
            r
        } else if let Some(r) = rest.strip_prefix('=') {
            // Make sure it's not `==`
            if r.starts_with('=') {
                return false;
            }
            r
        } else {
            return false;
        };

        // `var` must not appear in the RHS (otherwise it's a read like `x = x + 1`)
        !Self::word_appears_in_line(var, rhs)
    }

    /// Check whether a file path corresponds to a test file.
    fn is_test_path(path_str: &str) -> bool {
        path_str.contains("/test")
            || path_str.contains("/tests/")
            || path_str.contains("_test.")
            || path_str.contains("/test_")
            || path_str.contains("/conftest")
            || path_str.ends_with("_test.py")
            || path_str.ends_with("_test.go")
            || path_str.ends_with("_test.rs")
            || path_str.ends_with("_test.js")
            || path_str.ends_with("_test.ts")
    }

    /// Check whether a file is a config/settings file.
    fn is_config_file(path: &Path) -> bool {
        let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
        CONFIG_FILES.contains(&name)
    }

    /// Core detection logic using ValueStore and graph.
    fn detect_with_analysis_ctx(&self, ctx: &AnalysisContext<'_>) -> Vec<Finding> {
        let value_store = match ctx.detector_ctx.value_store.as_ref() {
            Some(vs) => vs,
            None => {
                info!("DeadStoreDetector: no ValueStore available, skipping");
                return Vec::new();
            }
        };

        let interner = ctx.graph.interner();
        let functions = ctx.graph.get_functions_shared();
        let repo_path = ctx.repo_path();

        let mut findings = Vec::new();

        for func in functions.iter() {
            if func.kind != NodeKind::Function {
                continue;
            }
            if findings.len() >= self.max_findings {
                break;
            }

            let qn = func.qn(interner);
            let file_path_str = func.path(interner);
            let file_path = Path::new(file_path_str);

            // ── Skip: test functions ────────────────────────────────
            if ctx.is_test_function(qn) {
                continue;
            }

            // ── Skip: test file paths ──────────────────────────────
            if Self::is_test_path(file_path_str) {
                continue;
            }

            // ── Skip: config/settings files ────────────────────────
            if Self::is_config_file(file_path) {
                continue;
            }

            // ── Get assignments from ValueStore ────────────────────
            let assignments = value_store.assignments_in(qn);
            if assignments.is_empty() {
                continue;
            }

            // ── Get function source lines ──────────────────────────
            let content = ctx
                .files
                .get(file_path)
                .map(|e| e.content.clone())
                .or_else(|| {
                    crate::cache::global_cache()
                        .content(file_path)
                        .map(|s| s.as_str().into())
                });
            let content = match content {
                Some(c) => c,
                None => continue,
            };

            let all_lines: Vec<&str> = content.lines().collect();

            // Function line range (1-indexed in graph → 0-indexed for slicing)
            let func_start = func.line_start.saturating_sub(1) as usize;
            let func_end = if func.line_end == 0 {
                all_lines.len()
            } else {
                (func.line_end as usize).min(all_lines.len())
            };

            if func_start >= all_lines.len() || func_start >= func_end {
                continue;
            }

            let func_lines = &all_lines[func_start..func_end];

            // ── Determine severity (lower for utility functions) ───
            let severity = if ctx.is_utility_function(qn) {
                Severity::Info
            } else {
                Severity::Low
            };

            // ── Determine file extension for language-specific logic
            let ext = file_path.extension().and_then(|e| e.to_str()).unwrap_or("");
            let is_python = ext == "py";

            // ── Iterate over assignments ───────────────────────────
            for (idx, assignment) in assignments.iter().enumerate() {
                if findings.len() >= self.max_findings {
                    break;
                }

                let var = &assignment.variable;

                // Skip underscore-prefixed and well-known skip variables
                if var.starts_with('_') || SKIP_VARS.contains(&var.as_str()) {
                    continue;
                }

                // Skip attribute stores: variable contains `.` (e.g., self.x)
                if var.contains('.') {
                    continue;
                }

                // Skip if the RHS is a FieldAccess (attribute store pattern)
                if matches!(&assignment.value, SymbolicValue::FieldAccess(..)) {
                    continue;
                }

                // Skip if the LHS variable looks like it's being stored as
                // an attribute (value is a Parameter, which means `self.x = param`
                // was captured with the variable as just `x`). Actually this is
                // already covered by the variable containing `.` check for most
                // parsers.

                // ── Module-level scope exemption ───────────────────
                // If the QN has no function container (module-level assignment),
                // skip entirely for Python (public by convention, may be imported).
                if is_python {
                    // Module-level: QN typically looks like "module.VAR" with one dot
                    let dot_count = qn.chars().filter(|c| *c == '.').count();
                    if dot_count <= 1 {
                        // Likely module-level scope
                        continue;
                    }
                }

                // ── Find the line of the next assignment to the same variable
                let assignment_line = assignment.line as usize;
                // Convert to 0-indexed offset within function lines
                if assignment_line == 0 || assignment_line < func.line_start as usize {
                    continue;
                }
                let func_relative_line = assignment_line.saturating_sub(func.line_start as usize);

                let next_assignment_line = assignments[(idx + 1)..]
                    .iter()
                    .find(|a| a.variable == *var)
                    .map(|a| (a.line as usize).saturating_sub(func.line_start as usize))
                    .unwrap_or(func_lines.len());

                // ── Check if variable is read between assignment and next/end
                if !Self::is_read_in_range(
                    var,
                    func_lines,
                    func_relative_line,
                    next_assignment_line,
                ) {
                    // Extract the source line for the finding description
                    let source_line = if func_relative_line < func_lines.len() {
                        func_lines[func_relative_line].trim()
                    } else {
                        "<unavailable>"
                    };

                    let rel_path = file_path.strip_prefix(repo_path).unwrap_or(file_path);

                    findings.push(Finding {
                        id: String::new(),
                        detector: "DeadStoreDetector".to_string(),
                        severity,
                        title: format!("Dead store: {}", var),
                        description: format!(
                            "Variable '{}' is assigned but never read afterward.\n\n\
                             ```\n{}\n```",
                            var, source_line
                        ),
                        affected_files: vec![rel_path.to_path_buf()],
                        line_start: Some(assignment.line),
                        line_end: Some(assignment.line),
                        suggested_fix: Some(format!(
                            "Options:\n\
                             1. Remove the unused assignment\n\
                             2. Use the variable '{}' in subsequent code\n\
                             3. If intentional, prefix with underscore: _{}",
                            var, var
                        )),
                        estimated_effort: Some("5 minutes".to_string()),
                        category: Some("dead-code".to_string()),
                        cwe_id: Some("CWE-563".to_string()),
                        why_it_matters: Some(
                            "Dead stores indicate logic errors or leftover code. \
                             They add confusion and may hide bugs."
                                .to_string(),
                        ),
                        ..Default::default()
                    });
                }
            }
        }

        info!(
            "DeadStoreDetector found {} findings (ValueStore-based)",
            findings.len()
        );
        findings
    }
}

/// Helper: check if a byte is a valid identifier character.
fn is_ident_char(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_'
}

impl Detector for DeadStoreDetector {
    fn name(&self) -> &'static str {
        "DeadStoreDetector"
    }

    fn description(&self) -> &'static str {
        "Detects variables assigned but never read"
    }

    fn category(&self) -> &'static str {
        "dead-code"
    }

    fn file_extensions(&self) -> &'static [&'static str] {
        &["py", "js", "ts", "jsx", "tsx", "go", "rs"]
    }

    fn scope(&self) -> DetectorScope {
        DetectorScope::FileScopedGraph
    }

    fn requires_graph(&self) -> bool {
        true
    }

    fn detect(
        &self,
        ctx: &crate::detectors::analysis_context::AnalysisContext,
    ) -> Result<Vec<Finding>> {
        Ok(self.detect_with_analysis_ctx(ctx))
    }
}

impl crate::detectors::RegisteredDetector for DeadStoreDetector {
    fn create(init: &crate::detectors::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::new(init.repo_path))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── Unit tests for helper functions ──────────────────────────────────

    #[test]
    fn test_word_appears_in_line() {
        assert!(DeadStoreDetector::word_appears_in_line("x", "  x = 5"));
        assert!(DeadStoreDetector::word_appears_in_line("x", "y = x + 1"));
        assert!(!DeadStoreDetector::word_appears_in_line("x", "fox = 1"));
        assert!(!DeadStoreDetector::word_appears_in_line("x", "extra = 1"));
        assert!(DeadStoreDetector::word_appears_in_line(
            "result",
            "return result"
        ));
        assert!(!DeadStoreDetector::word_appears_in_line(
            "result",
            "no_result_here = 1"
        ));
    }

    #[test]
    fn test_is_pure_reassignment() {
        // Pure reassignment — var does not appear on RHS
        assert!(DeadStoreDetector::is_pure_reassignment("x", "x = 42"));
        assert!(DeadStoreDetector::is_pure_reassignment(
            "x",
            "x = some_func()"
        ));
        assert!(DeadStoreDetector::is_pure_reassignment("x", "x := 42"));

        // Not pure — var appears on RHS (it's a read)
        assert!(!DeadStoreDetector::is_pure_reassignment("x", "x = x + 1"));

        // Not an assignment at all
        assert!(!DeadStoreDetector::is_pure_reassignment("x", "print(x)"));
        assert!(!DeadStoreDetector::is_pure_reassignment("x", "x == 5"));
    }

    #[test]
    fn test_is_read_in_range_basic() {
        let lines = vec!["x = 5", "y = x + 1", "print(y)"];
        // x is read on line 1 (the range 0..3)
        assert!(DeadStoreDetector::is_read_in_range("x", &lines, 0, 3));
        // y is read on line 2
        assert!(DeadStoreDetector::is_read_in_range("y", &lines, 1, 3));
        // z is never used
        assert!(!DeadStoreDetector::is_read_in_range("z", &lines, 0, 3));
    }

    #[test]
    fn test_is_read_in_range_reassignment_not_a_read() {
        let lines = vec!["x = 5", "x = 10", "print(x)"];
        // Between line 0 and line 1 (the next assignment), x is NOT read
        assert!(!DeadStoreDetector::is_read_in_range("x", &lines, 0, 1));
        // Between line 1 and end, x IS read (on the print line)
        assert!(DeadStoreDetector::is_read_in_range("x", &lines, 1, 3));
    }

    #[test]
    fn test_skip_patterns() {
        assert!(SKIP_VARS.contains(&"self"));
        assert!(SKIP_VARS.contains(&"_"));
        assert!(SKIP_VARS.contains(&"cls"));
    }

    #[test]
    fn test_is_test_path() {
        assert!(DeadStoreDetector::is_test_path("src/tests/test_app.py"));
        assert!(DeadStoreDetector::is_test_path("app_test.py"));
        assert!(DeadStoreDetector::is_test_path("tests/conftest.py"));
        assert!(!DeadStoreDetector::is_test_path("src/app.py"));
    }

    #[test]
    fn test_is_config_file() {
        assert!(DeadStoreDetector::is_config_file(Path::new(
            "src/__init__.py"
        )));
        assert!(DeadStoreDetector::is_config_file(Path::new(
            "myapp/config.py"
        )));
        assert!(DeadStoreDetector::is_config_file(Path::new(
            "myapp/settings.py"
        )));
        assert!(!DeadStoreDetector::is_config_file(Path::new(
            "myapp/views.py"
        )));
    }

    #[test]
    fn test_is_read_skips_comments() {
        let lines = vec!["x = 5", "# x is great", "return 42"];
        assert!(!DeadStoreDetector::is_read_in_range("x", &lines, 0, 3));
    }

    #[test]
    fn test_self_increment_is_read() {
        let lines = vec!["x = 0", "x = x + 1", "print(x)"];
        // Between line 0 and line 1, x IS read because line 1 is `x = x + 1`
        // (the RHS contains x, so it's not a pure reassignment)
        assert!(DeadStoreDetector::is_read_in_range("x", &lines, 0, 1));
    }
}
