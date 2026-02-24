//! String Concatenation in Loop Detector
//!
//! Graph-enhanced detection of string concatenation in loops.
//! Uses graph to:
//! - Find hidden patterns (loop calls function that concatenates)
//! - Estimate loop iteration count from context
//! - Provide language-specific fixes

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static LOOP_PATTERN: OnceLock<Regex> = OnceLock::new();
static STRING_CONCAT: OnceLock<Regex> = OnceLock::new();
static FOR_VAR_PATTERN: OnceLock<Regex> = OnceLock::new();
static CONCAT_VAR_PATTERN: OnceLock<Regex> = OnceLock::new();

fn loop_pattern() -> &'static Regex {
    LOOP_PATTERN.get_or_init(|| {
        Regex::new(r"(?i)(for\s+\w+\s+in|\.forEach|\.map\(|\.each|for\s*\(|while\s*\()")
            .expect("valid regex")
    })
}

fn for_var_pattern() -> &'static Regex {
    FOR_VAR_PATTERN.get_or_init(|| Regex::new(r"for\s+(\w+)\s+in").expect("valid regex"))
}

fn string_concat() -> &'static Regex {
    STRING_CONCAT.get_or_init(|| {
        // Only match += with string literal or f-string
        // Fix: 'f' must be followed by a quote to be an f-string prefix
        Regex::new(r#"\w+\s*\+=\s*(?:["'`]|f["'])"#)
            .expect("valid regex")
    })
}

/// Extract the variable name from a `+=` line (the identifier before `+=`)
fn concat_var_pattern() -> &'static Regex {
    CONCAT_VAR_PATTERN.get_or_init(|| Regex::new(r"(\w+)\s*\+=").expect("valid regex"))
}

pub struct StringConcatLoopDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
}

impl StringConcatLoopDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Find functions that do string concatenation
    fn find_concat_functions(&self, graph: &dyn crate::graph::GraphQuery) -> HashSet<String> {
        let mut concat_funcs = HashSet::new();

        for func in graph.get_functions() {
            if let Some(content) =
                crate::cache::global_cache().content(std::path::Path::new(&func.file_path))
            {
                let lines: Vec<&str> = content.lines().collect();
                let start = func.line_start.saturating_sub(1) as usize;
                let end = (func.line_end as usize).min(lines.len());

                for line in lines.get(start..end).unwrap_or(&[]) {
                    if string_concat().is_match(line) {
                        concat_funcs.insert(func.qualified_name.clone());
                        break;
                    }
                }
            }
        }
        concat_funcs
    }

    /// Get language-specific suggestion
    fn get_suggestion(ext: &str) -> String {
        match ext {
            "py" => "Use list and join:\n\
                     ```python\n\
                     parts = []\n\
                     for item in items:\n\
                         parts.append(str(item))\n\
                     result = ''.join(parts)\n\
                     ```\n\
                     Or use a list comprehension:\n\
                     ```python\n\
                     result = ''.join(str(item) for item in items)\n\
                     ```"
            .to_string(),
            "java" => "Use StringBuilder:\n\
                      ```java\n\
                      StringBuilder sb = new StringBuilder();\n\
                      for (String item : items) {\n\
                          sb.append(item);\n\
                      }\n\
                      String result = sb.toString();\n\
                      ```"
            .to_string(),
            "js" | "ts" => "Use array and join:\n\
                           ```javascript\n\
                           const parts = items.map(item => String(item));\n\
                           const result = parts.join('');\n\
                           ```\n\
                           Or use template literals with reduce:\n\
                           ```javascript\n\
                           const result = items.reduce((acc, item) => `${acc}${item}`, '');\n\
                           ```"
            .to_string(),
            "go" => "Use strings.Builder:\n\
                    ```go\n\
                    var sb strings.Builder\n\
                    for _, item := range items {\n\
                        sb.WriteString(item)\n\
                    }\n\
                    result := sb.String()\n\
                    ```"
            .to_string(),
            _ => "Use a StringBuilder or list.join() approach.".to_string(),
        }
    }
}

impl Detector for StringConcatLoopDetector {
    fn name(&self) -> &'static str {
        "string-concat-loop"
    }
    fn description(&self) -> &'static str {
        "Detects string concatenation in loops"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let concat_funcs = self.find_concat_functions(graph);

        for path in files.files_with_extensions(&["py", "js", "ts", "java", "go", "rb", "php"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");

            if let Some(content) = files.content(path) {
                let is_python = ext == "py";
                let mut in_loop = false;
                let mut loop_line: usize = 0;
                let mut brace_depth = 0;
                let mut loop_indent: usize = 0;
                let mut loop_line_idx: usize = 0;
                let mut _loop_var = String::new();
                let all_lines: Vec<&str> = content.lines().collect();

                // Track per-variable concat counts within the current loop.
                // Maps variable_name -> (first_line_number_1based, count)
                let mut loop_concats: HashMap<String, (usize, u32)> = HashMap::new();

                // Helper closure: flush accumulated concats, creating findings
                // only for variables with 2+ concatenations in the same loop.
                let flush_loop_concats = |concats: &mut HashMap<String, (usize, u32)>,
                                               findings: &mut Vec<Finding>,
                                               loop_start_line: usize,
                                               file_path: &std::path::Path,
                                               extension: &str| {
                    for (var_name, (first_line, count)) in concats.drain() {
                        if count >= 2 {
                            let suggestion = Self::get_suggestion(extension);
                            findings.push(Finding {
                                id: String::new(),
                                detector: "StringConcatLoopDetector".to_string(),
                                severity: Severity::Medium,
                                title: "String concatenation in loop".to_string(),
                                description: format!(
                                    "Variable '{}' concatenated {} times inside loop (started line {}).\n\n\
                                     **Performance:** O(n²) time complexity. Each concatenation \
                                     creates a new string and copies all previous characters.\n\n\
                                     For 1000 iterations, this copies ~500,000 characters instead of 1000.",
                                    var_name, count, loop_start_line
                                ),
                                affected_files: vec![file_path.to_path_buf()],
                                line_start: Some(first_line as u32),
                                line_end: Some(first_line as u32),
                                suggested_fix: Some(suggestion),
                                estimated_effort: Some("15 minutes".to_string()),
                                category: Some("performance".to_string()),
                                cwe_id: None,
                                why_it_matters: Some(
                                    "String concatenation in loops creates O(n²) time complexity \
                                     due to immutable string copying.".to_string()
                                ),
                                ..Default::default()
                            });
                        }
                    }
                };

                for (i, line) in all_lines.iter().enumerate() {
                    if loop_pattern().is_match(line) {
                        // If we were already in a loop, flush any accumulated concats
                        if in_loop {
                            flush_loop_concats(
                                &mut loop_concats,
                                &mut findings,
                                loop_line,
                                path,
                                ext,
                            );
                        }

                        in_loop = true;
                        loop_line = i + 1;
                        loop_line_idx = i;
                        loop_concats.clear();
                        if is_python {
                            loop_indent = line.len() - line.trim_start().len();
                        } else {
                            brace_depth = 0;
                        }

                        // Try to extract loop variable for context
                        if let Some(caps) = for_var_pattern().captures(line) {
                            _loop_var = caps
                                .get(1)
                                .map(|m| m.as_str().to_string())
                                .unwrap_or_default();
                        }
                    }

                    if in_loop {
                        if is_python {
                            let trimmed = line.trim();
                            if !trimmed.is_empty() && i > loop_line_idx {
                                let current_indent = line.len() - line.trim_start().len();
                                if current_indent <= loop_indent {
                                    // Loop ended — flush concats and check for accumulation
                                    flush_loop_concats(
                                        &mut loop_concats,
                                        &mut findings,
                                        loop_line,
                                        path,
                                        ext,
                                    );
                                    in_loop = false;
                                    continue;
                                }
                            }
                        } else {
                            brace_depth += line.matches('{').count() as i32;
                            brace_depth -= line.matches('}').count() as i32;
                            if brace_depth < 0 {
                                // Loop ended — flush concats and check for accumulation
                                flush_loop_concats(
                                    &mut loop_concats,
                                    &mut findings,
                                    loop_line,
                                    path,
                                    ext,
                                );
                                in_loop = false;
                                continue;
                            }
                        }

                        if string_concat().is_match(line) {
                            let prev_line = if i > 0 { Some(all_lines[i - 1]) } else { None };
                            if crate::detectors::is_line_suppressed(line, prev_line) {
                                continue;
                            }

                            // Extract variable name (text before +=)
                            if let Some(caps) = concat_var_pattern().captures(line) {
                                let var_name = caps
                                    .get(1)
                                    .map(|m| m.as_str().to_string())
                                    .unwrap_or_default();
                                let entry = loop_concats
                                    .entry(var_name)
                                    .or_insert((i + 1, 0));
                                entry.1 += 1;
                            }
                        }
                    }
                }

                // End of file — flush any remaining loop concats
                if in_loop {
                    flush_loop_concats(
                        &mut loop_concats,
                        &mut findings,
                        loop_line,
                        path,
                        ext,
                    );
                }
            }
        }

        // Graph-based: find loops that call concat functions
        // Skip Rust files — push_str mutates in place (not O(n²)),
        // and Path::join is not string concatenation
        if !concat_funcs.is_empty() {
            for func in graph.get_functions() {
                if findings.len() >= self.max_findings {
                    break;
                }

                if func.file_path.ends_with(".rs") {
                    continue;
                }

                let has_loop = if let Some(content) =
                    crate::cache::global_cache().content(std::path::Path::new(&func.file_path))
                {
                    let lines: Vec<&str> = content.lines().collect();
                    let start = func.line_start.saturating_sub(1) as usize;
                    let end = (func.line_end as usize).min(lines.len());

                    lines
                        .get(start..end)
                        .map(|slice| slice.iter().any(|line| loop_pattern().is_match(line)))
                        .unwrap_or(false)
                } else {
                    false
                };

                if !has_loop {
                    continue;
                }

                for callee in graph.get_callees(&func.qualified_name) {
                    if concat_funcs.contains(&callee.qualified_name) {
                        findings.push(Finding {
                            id: String::new(),
                            detector: "StringConcatLoopDetector".to_string(),
                            severity: Severity::Medium,
                            title: format!("Hidden string concat: {} → {}", func.name, callee.name),
                            description: format!(
                                "Function '{}' contains a loop and calls '{}' which does string concatenation.\n\n\
                                 This creates the same O(n²) performance issue across function boundaries.",
                                func.name, callee.name
                            ),
                            affected_files: vec![PathBuf::from(&func.file_path)],
                            line_start: Some(func.line_start),
                            line_end: Some(func.line_end),
                            suggested_fix: Some(
                                "Options:\n\
                                 1. Refactor the called function to accept a StringBuilder/list\n\
                                 2. Batch the operations before calling\n\
                                 3. Return parts instead of concatenating inside".to_string()
                            ),
                            estimated_effort: Some("30 minutes".to_string()),
                            category: Some("performance".to_string()),
                            cwe_id: None,
                            why_it_matters: Some(
                                "Hidden O(n²) patterns are harder to spot but equally impactful.".to_string()
                            ),
                            ..Default::default()
                        });
                        break;
                    }
                }
            }
        }

        info!(
            "StringConcatLoopDetector found {} findings (graph-aware)",
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
    fn test_detects_string_concat_in_loop() {
        let store = GraphStore::in_memory();
        let detector = StringConcatLoopDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("builder.py", "def build_output(items):\n    result = \"\"\n    for item in items:\n        result += \"key: \"\n        result += \"value\"\n    return result\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).unwrap();
        assert!(
            !findings.is_empty(),
            "Should detect string concatenation in loop (2+ concats to same var). Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_join() {
        let store = GraphStore::in_memory();
        let detector = StringConcatLoopDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("builder.py", "def build_output(items):\n    parts = []\n    for item in items:\n        parts.append(str(item))\n    return ''.join(parts)\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag list.append + join pattern. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_numeric_accumulation() {
        let store = GraphStore::in_memory();
        let detector = StringConcatLoopDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("calc.py", "def total_price(items):\n    total = 0\n    for item in items:\n        total += item.price\n    return total\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag numeric accumulation (total += item.price). Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_counter_increment() {
        let store = GraphStore::in_memory();
        let detector = StringConcatLoopDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("count.py", "def count_active(users):\n    count = 0\n    for user in users:\n        count += 1\n    return count\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag counter increment (count += 1). Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_still_detects_string_literal_concat_in_loop() {
        let store = GraphStore::in_memory();
        let detector = StringConcatLoopDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("build.py", "def build(items):\n    result = \"\"\n    for item in items:\n        result += \"prefix_\"\n        result += \"suffix_\"\n    return result\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).unwrap();
        assert!(
            !findings.is_empty(),
            "Should still detect string literal concat in loop (2+ concats)"
        );
    }

    #[test]
    fn test_still_detects_string_concat_with_plus() {
        let store = GraphStore::in_memory();
        let detector = StringConcatLoopDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("build.py", "def build(items):\n    result = \"\"\n    for item in items:\n        result = result + \"value\"\n    return result\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).unwrap();
        // Note: the = x + y pattern was removed, so this should no longer match
        // Only += with string literals is detected now
        assert!(
            findings.is_empty(),
            "Should not detect result = result + 'value' since = x + y pattern was removed"
        );
    }

    #[test]
    fn test_no_finding_for_media_iadd() {
        let store = GraphStore::in_memory();
        let detector = StringConcatLoopDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("forms.py", "for fs in formsets:\n    media += fs.media\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag media += fs.media (not string concat). Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_concat_after_loop() {
        let store = GraphStore::in_memory();
        let detector = StringConcatLoopDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("builder.py", "for item in items:\n    process(item)\n\nresult += \"_suffix\"\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            findings.is_empty(),
            "Concat after loop exits should not be flagged. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_still_detects_string_concat_in_loop() {
        let store = GraphStore::in_memory();
        let detector = StringConcatLoopDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("slow.py", "result = \"\"\nfor item in items:\n    result += \"item: \"\n    result += \"value\"\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            !findings.is_empty(),
            "Should still detect string concat inside loop (2+ concats)"
        );
    }

    #[test]
    fn test_no_finding_for_single_concat_per_iteration() {
        let store = GraphStore::in_memory();
        let detector = StringConcatLoopDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("views.py", "for item in items:\n    url += \"/\"\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag single concat per iteration. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_still_detects_multiple_concats_in_loop() {
        let store = GraphStore::in_memory();
        let detector = StringConcatLoopDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("builder.py", "for field in fields:\n    definition += \" \" + check\n    definition += \" \" + suffix\n    definition += \" \" + fk\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).unwrap();
        assert!(
            !findings.is_empty(),
            "Should detect multiple concats to same variable in loop"
        );
    }
}
