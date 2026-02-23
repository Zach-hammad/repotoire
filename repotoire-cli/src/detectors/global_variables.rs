//! Global Variables Detector
//!
//! Graph-enhanced detection of mutable global variables.
//! Uses graph to:
//! - Count how many functions read/write the global (impact analysis)
//! - Detect cross-module usage (higher severity)
//! - Find potential encapsulation points

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static GLOBAL_PATTERN: OnceLock<Regex> = OnceLock::new();
static VAR_NAME: OnceLock<Regex> = OnceLock::new();

fn global_pattern() -> &'static Regex {
    GLOBAL_PATTERN.get_or_init(|| {
        Regex::new(r"^(var\s+\w+\s*=|let\s+\w+\s*=|global\s+\w+|\w+\s*=\s*[^=])")
            .expect("valid regex")
    })
}

fn var_name_pattern() -> &'static Regex {
    VAR_NAME.get_or_init(|| Regex::new(r"^(?:var|let|global)\s+(\w+)").expect("valid regex"))
}

pub struct GlobalVariablesDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl GlobalVariablesDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Extract variable name from declaration
    fn extract_var_name(line: &str) -> Option<String> {
        if let Some(caps) = var_name_pattern().captures(line.trim()) {
            return caps.get(1).map(|m| m.as_str().to_string());
        }
        // Handle Python global statement
        if line.trim().starts_with("global ") {
            return line
                .trim()
                .strip_prefix("global ")
                .and_then(|s| s.split_whitespace().next())
                .map(|s| s.to_string());
        }
        None
    }

    /// Count how many functions in the file reference this variable
    fn count_usages(&self, content: &str, var_name: &str, declaration_line: usize) -> usize {
        let mut count = 0;
        let var_pattern = format!(r"\b{}\b", regex::escape(var_name));
        if let Ok(re) = Regex::new(&var_pattern) {
            for (i, line) in content.lines().enumerate() {
                if i == declaration_line - 1 {
                    continue;
                } // Skip declaration
                if re.is_match(line) {
                    count += 1;
                }
            }
        }
        count
    }

    /// Check if variable is used by functions in other files
    fn check_cross_module_usage(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        file_path: &str,
        _var_name: &str,
    ) -> bool {
        // Check if any function in other files might reference this
        // This is heuristic - we check if the file is imported by others
        let file_name = file_path.rsplit('/').next().unwrap_or(file_path);
        let module_name = file_name.split('.').next().unwrap_or("");

        // Look for imports of this module
        for (_, import_target) in graph.get_imports() {
            if import_target.contains(module_name) {
                return true;
            }
        }
        false
    }

    fn create_finding(
        &self,
        path: &std::path::Path,
        line: usize,
        var_name: &str,
        usage_count: usize,
        is_cross_module: bool,
    ) -> Finding {
        // Calculate severity based on impact
        let severity = if is_cross_module && usage_count > 5 {
            Severity::High // Cross-module globals with many usages are dangerous
        } else if is_cross_module || usage_count > 10 {
            Severity::Medium
        } else {
            Severity::Low
        };

        let mut notes = Vec::new();
        if usage_count > 0 {
            notes.push(format!("📊 Used {} times in this file", usage_count));
        }
        if is_cross_module {
            notes.push("⚠️ Module is imported by others - global may leak".to_string());
        }

        let context_notes = if notes.is_empty() {
            String::new()
        } else {
            format!("\n\n**Impact Analysis:**\n{}", notes.join("\n"))
        };

        let suggestion = if is_cross_module {
            let capitalized = format!(
                "{}{}",
                var_name
                    .chars()
                    .next()
                    .map(|c| c.to_uppercase().to_string())
                    .unwrap_or_default(),
                var_name.chars().skip(1).collect::<String>()
            );
            format!(
                "Cross-module globals are especially dangerous. Consider:\n\
                 1. Export a getter/setter function instead: `get{0}()`, `set{0}()`\n\
                 2. Encapsulate in a class with controlled access\n\
                 3. Use dependency injection",
                capitalized
            )
        } else if usage_count > 5 {
            "Many usages - consider:\n\
             1. Encapsulate in a module with getter/setter\n\
             2. Convert to a class instance\n\
             3. Pass as parameter instead"
                .to_string()
        } else {
            "Use const if immutable, or encapsulate in a module/class.".to_string()
        };

        Finding {
            id: String::new(),
            detector: "GlobalVariablesDetector".to_string(),
            severity,
            title: format!("Global mutable variable: {}", var_name),
            description: format!(
                "Global mutable state '{}' makes code hard to reason about.{}",
                var_name, context_notes
            ),
            affected_files: vec![path.to_path_buf()],
            line_start: Some(line as u32),
            line_end: Some(line as u32),
            suggested_fix: Some(suggestion),
            estimated_effort: Some(if is_cross_module {
                "30 minutes".to_string()
            } else {
                "15 minutes".to_string()
            }),
            category: Some("code-quality".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Global state causes hidden dependencies between functions. \
                 Changes to globals can have unexpected effects throughout the codebase."
                    .to_string(),
            ),
            ..Default::default()
        }
    }
}

impl Detector for GlobalVariablesDetector {
    fn name(&self) -> &'static str {
        "global-variables"
    }
    fn description(&self) -> &'static str {
        "Detects mutable global variables"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let mut seen_globals: HashSet<(PathBuf, String)> = HashSet::new();

        for path in files.files_with_extensions(&["py", "js", "ts"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            let path_str = path.to_string_lossy().to_string();

            // Skip bundled/generated code (path-based fallback first since we load content anyway)
            if crate::detectors::content_classifier::is_likely_bundled_path(&path_str) {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");

            if let Some(content) = files.content(path) {
                // Skip bundled/generated code (content-based detection)
                if crate::detectors::content_classifier::is_bundled_code(&content)
                    || crate::detectors::content_classifier::is_minified_code(&content)
                    || crate::detectors::content_classifier::is_fixture_code(&path_str, &content)
                {
                    continue;
                }
                // Track Python function scope with indentation depth
                let mut py_indent_stack: Vec<usize> = Vec::new(); // indent levels of open def blocks
                let mut py_in_function = false;
                let mut in_docstring = false;
                let all_lines: Vec<&str> = content.lines().collect();

                for (i, line) in all_lines.iter().enumerate() {
                    let trimmed = line.trim();

                    // Track Python triple-quoted strings (docstrings)
                    if ext == "py" {
                        let triple_double = trimmed.matches("\"\"\"").count();
                        let triple_single = trimmed.matches("'''").count();
                        let triple_count = triple_double + triple_single;
                        if triple_count % 2 != 0 {
                            in_docstring = !in_docstring;
                        }
                        if in_docstring {
                            continue;
                        }
                    }

                    // --- Python scope tracking via indentation ---
                    if ext == "py" {
                        let indent = line.len() - line.trim_start().len();
                        if !trimmed.is_empty() {
                            // Pop any indent levels that are >= current indent (we've left those blocks)
                            py_indent_stack.retain(|&lvl| lvl < indent);
                            py_in_function = !py_indent_stack.is_empty();
                        }
                        if trimmed.starts_with("def ") || trimmed.starts_with("async def ") {
                            py_indent_stack.push(indent);
                            py_in_function = true;
                        }
                    }

                    // Skip constants, imports, classes
                    if trimmed.starts_with("const ")
                        || trimmed.starts_with("import ")
                        || trimmed.starts_with("from ")
                        || trimmed.starts_with("class ")
                        || trimmed.starts_with("#")
                        || trimmed.starts_with("//")
                        || trimmed.is_empty()
                        || trimmed.starts_with("export ")
                    {
                        continue;
                    }

                    // Check for global assignment
                    let is_global = if ext == "py" {
                        // Only flag explicit `global varname` statements that are INSIDE functions
                        // (that's their purpose: declaring a global from within a function)
                        py_in_function && trimmed.starts_with("global ")
                    } else {
                        // For JS/TS: only flag `var`/`let` at module scope (no leading indentation).
                        // Any indented declaration is inside a function, block, or class method —
                        // JSX's {{ }} braces would break brace-counting, so indentation is safer.
                        let at_module_scope = !line.starts_with(' ') && !line.starts_with('\t');
                        at_module_scope
                            && (trimmed.starts_with("var ") || trimmed.starts_with("let "))
                    };

                    if is_global {
                        let prev_line = if i > 0 { Some(all_lines[i - 1]) } else { None };
                        if crate::detectors::is_line_suppressed(line, prev_line) {
                            continue;
                        }

                        if let Some(var_name) = Self::extract_var_name(trimmed) {
                            let key = (path.to_path_buf(), var_name.clone());
                            if seen_globals.contains(&key) {
                                continue;
                            }
                            seen_globals.insert(key);

                            let usage_count = self.count_usages(&content, &var_name, i + 1);
                            let is_cross_module =
                                self.check_cross_module_usage(graph, &path_str, &var_name);

                            findings.push(self.create_finding(
                                path,
                                i + 1,
                                &var_name,
                                usage_count,
                                is_cross_module,
                            ));
                        }
                    }
                }
            }
        }

        info!(
            "GlobalVariablesDetector found {} findings (graph-aware)",
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
    fn test_detects_global_statement_in_python() {
        // Python: `global counter` inside a function body triggers detection
        let store = GraphStore::in_memory();
        let detector = GlobalVariablesDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("state.py", "counter = 0\n\ndef increment():\n    global counter\n    counter += 1\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            !findings.is_empty(),
            "Should detect 'global counter' inside function"
        );
        assert!(
            findings[0].title.contains("counter"),
            "Title should mention variable name, got: {}",
            findings[0].title
        );
    }

    #[test]
    fn test_no_finding_for_global_in_docstring() {
        let store = GraphStore::in_memory();
        let detector = GlobalVariablesDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("widgets.py", "class Widget:\n    def merge(self):\n        \"\"\"\n        global or in CSS you might want to override a style.\n        \"\"\"\n        pass\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(findings.is_empty(), "Should not flag 'global' in docstring. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>());
    }

    #[test]
    fn test_dedup_same_variable_across_functions() {
        let store = GraphStore::in_memory();
        let detector = GlobalVariablesDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("trans.py", "def func_a():\n    global _default\n    _default = get_default()\n\ndef func_b():\n    global _default\n    return _default\n\ndef func_c():\n    global _default\n    _default = None\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert_eq!(findings.len(), 1, "Should deduplicate same variable across functions. Found {} findings", findings.len());
    }

    #[test]
    fn test_no_finding_for_local_variables() {
        // No `global` statement, no module-level mutable var
        let store = GraphStore::in_memory();
        let detector = GlobalVariablesDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("clean.py", "def compute(x):\n    result = x * 2\n    return result\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag local variables in functions, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
