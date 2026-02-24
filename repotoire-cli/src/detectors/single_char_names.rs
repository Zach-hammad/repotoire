//! Single Character Variable Names Detector
//!
//! Graph-enhanced detection of single-character variables:
//! - Uses graph to check function size (small functions = less severe)
//! - Checks how many times the variable is referenced
//! - Reduces severity for short-lived variables in small scopes

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static SINGLE_CHAR: OnceLock<Regex> = OnceLock::new();

fn single_char() -> &'static Regex {
    SINGLE_CHAR.get_or_init(|| {
        Regex::new(r"\b(let|var|const|def|int|string|float|double)\s+([a-zA-Z])\s*[=:]")
            .expect("valid regex")
    })
}

/// Context-aware suggestions based on variable name
fn suggest_name(var: &str, context_line: &str) -> String {
    let line_lower = context_line.to_lowercase();

    // Try to infer from context
    if line_lower.contains("count") || line_lower.contains("len") || line_lower.contains("size") {
        return format!(
            "Consider: `count`, `length`, or `size` instead of `{}`",
            var
        );
    }
    if line_lower.contains("sum") || line_lower.contains("total") {
        return format!(
            "Consider: `sum`, `total`, or `accumulator` instead of `{}`",
            var
        );
    }
    if line_lower.contains("index") || line_lower.contains("idx") {
        return format!("Consider: `index` or `position` instead of `{}`", var);
    }
    if line_lower.contains("error") || line_lower.contains("err") {
        return format!("Consider: `error` or `err` instead of `{}`", var);
    }
    if line_lower.contains("result") || line_lower.contains("ret") {
        return format!("Consider: `result` or `output` instead of `{}`", var);
    }
    if line_lower.contains("file") || line_lower.contains("path") {
        return format!(
            "Consider: `file`, `path`, or `filename` instead of `{}`",
            var
        );
    }
    if line_lower.contains("temp") || line_lower.contains("tmp") {
        return format!(
            "Consider: `temp` or a more descriptive name instead of `{}`",
            var
        );
    }

    format!(
        "Use a descriptive name that explains the variable's purpose instead of `{}`.\n\
         Good names answer: What does this represent?",
        var
    )
}

pub struct SingleCharNamesDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
}

impl SingleCharNamesDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Build a map of file -> function LOC ranges from graph
    fn build_function_map(
        &self,
        graph: &dyn crate::graph::GraphQuery,
    ) -> HashMap<String, Vec<(u32, u32, String)>> {
        let mut map: HashMap<String, Vec<(u32, u32, String)>> = HashMap::new();

        for func in graph.get_functions() {
            if func.line_start > 0 && func.line_end > 0 {
                let path = func.file_path.clone();
                map.entry(path).or_default().push((
                    func.line_start,
                    func.line_end,
                    func.name.clone(),
                ));
            }
        }

        map
    }

    /// Find function containing a line and return its size
    fn get_function_context(
        func_map: &HashMap<String, Vec<(u32, u32, String)>>,
        path: &str,
        line: u32,
    ) -> Option<(String, u32)> {
        if let Some(funcs) = func_map.get(path) {
            for (start, end, name) in funcs {
                if line >= *start && line <= *end {
                    return Some((name.clone(), end - start + 1));
                }
            }
        }
        None
    }

    /// Count references to a variable in a function
    fn count_references(content: &str, var: &str, func_start: usize, func_end: usize) -> usize {
        let word_re = Regex::new(&format!(r"\b{}\b", regex::escape(var))).expect("valid regex");
        let lines: Vec<&str> = content.lines().collect();
        let mut count = 0;

        for line in lines
            .iter()
            .skip(func_start)
            .take(func_end - func_start + 1)
        {
            count += word_re.find_iter(line).count();
        }

        count
    }
}

impl Detector for SingleCharNamesDetector {
    fn name(&self) -> &'static str {
        "single-char-names"
    }
    fn description(&self) -> &'static str {
        "Detects single-character variable names"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let func_map = self.build_function_map(graph);

        for path in files.files_with_extensions(&["py", "js", "ts", "java", "go", "rs", "cs"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            // Skip test files — short names are idiomatic in tests
            if crate::detectors::base::is_test_file(path) {
                continue;
            }

            let path_str = path.to_string_lossy().to_string();

            if let Some(content) = files.content(path) {
                let lines: Vec<&str> = content.lines().collect();
                let mut in_test_block = false;

                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    // Skip everything after #[cfg(test)] (Rust test modules)
                    if line.contains("#[cfg(test)]") {
                        in_test_block = true;
                    }
                    if in_test_block {
                        continue;
                    }
                    // Skip loop variables (for i in, for (int i, etc)
                    if line.contains("for ") || line.contains("for(") {
                        continue;
                    }
                    // Skip lambda parameters
                    if line.contains("=>") || line.contains("lambda ") {
                        continue;
                    }
                    // Skip list comprehensions
                    if line.contains(" in ") && (line.contains("[") || line.contains("(")) {
                        continue;
                    }

                    if let Some(caps) = single_char().captures(line) {
                        if let Some(var) = caps.get(2) {
                            let v = var.as_str();
                            // Allow common math/loop variables
                            if matches!(
                                v,
                                "x" | "y" | "z" | "i" | "j" | "k" | "n" | "m" | "t" | "e" | "f"
                            ) {
                                continue;
                            }

                            let line_num = (i + 1) as u32;

                            // Check function context
                            let (severity, context_note) = if let Some((func_name, func_size)) =
                                Self::get_function_context(&func_map, &path_str, line_num)
                            {
                                // Count references within function
                                let func_start = lines
                                    .iter()
                                    .position(|l| {
                                        l.contains(&format!("def {}", func_name))
                                            || l.contains(&format!("fn {}", func_name))
                                            || l.contains(&format!("function {}", func_name))
                                            || l.contains(&format!("func {}", func_name))
                                    })
                                    .unwrap_or(0);
                                let func_end = (func_start + func_size as usize).min(lines.len());
                                let ref_count =
                                    Self::count_references(&content, v, func_start, func_end);

                                // Small function + few references = less severe
                                if func_size <= 10 && ref_count <= 3 {
                                    (Severity::Low, format!(
                                        "\n\n📊 In `{}` ({} lines), used {} times — limited scope reduces impact.",
                                        func_name, func_size, ref_count
                                    ))
                                } else if func_size > 30 || ref_count > 5 {
                                    (Severity::Medium, format!(
                                        "\n\n⚠️ In `{}` ({} lines), used {} times — consider renaming for clarity.",
                                        func_name, func_size, ref_count
                                    ))
                                } else {
                                    (
                                        Severity::Low,
                                        format!(
                                            "\n\n📊 In `{}` ({} lines), used {} times.",
                                            func_name, func_size, ref_count
                                        ),
                                    )
                                }
                            } else {
                                // Module-level variable with single char = worse
                                (
                                    Severity::Medium,
                                    "\n\n⚠️ Module-level single-char variable.".to_string(),
                                )
                            };

                            findings.push(Finding {
                                id: String::new(),
                                detector: "SingleCharNamesDetector".to_string(),
                                severity,
                                title: format!("Single-character variable: {}", v),
                                description: format!(
                                    "Single-letter names reduce code readability.{}",
                                    context_note
                                ),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some(line_num),
                                line_end: Some(line_num),
                                suggested_fix: Some(suggest_name(v, line)),
                                estimated_effort: Some("2 minutes".to_string()),
                                category: Some("readability".to_string()),
                                cwe_id: None,
                                why_it_matters: Some(
                                    "Single-letter variables make code harder to understand, \
                                     especially when debugging or reviewing. They force readers \
                                     to track variable purpose through context."
                                        .to_string(),
                                ),
                                ..Default::default()
                            });
                        }
                    }
                }
            }
        }

        info!(
            "SingleCharNamesDetector found {} findings (graph-aware)",
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
    fn test_detects_single_char_variable() {
        let store = GraphStore::in_memory();
        let detector = SingleCharNamesDetector::new("/mock/repo");
        // Use JS syntax so the regex `\b(let|var|const|...)\s+([a-zA-Z])\s*[=:]` matches
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("utils.js", "function process() {\n    let q = getData();\n    return q;\n}\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            findings.iter().any(|f| f.title.to_lowercase().contains("q")),
            "Should detect single-char variable 'q'. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_loop_index() {
        let store = GraphStore::in_memory();
        let detector = SingleCharNamesDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("utils.py", "for i in range(10):\n    print(i)\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag loop index 'i'. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
