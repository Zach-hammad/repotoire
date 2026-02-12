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
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;
use uuid::Uuid;

static LOOP_PATTERN: OnceLock<Regex> = OnceLock::new();
static STRING_CONCAT: OnceLock<Regex> = OnceLock::new();
static FOR_VAR_PATTERN: OnceLock<Regex> = OnceLock::new();

fn loop_pattern() -> &'static Regex {
    LOOP_PATTERN.get_or_init(|| {
        Regex::new(r"(?i)(for\s+\w+\s+in|\.forEach|\.map\(|\.each|for\s*\(|while\s*\()").unwrap()
    })
}

fn for_var_pattern() -> &'static Regex {
    FOR_VAR_PATTERN.get_or_init(|| Regex::new(r"for\s+(\w+)\s+in").unwrap())
}

fn string_concat() -> &'static Regex {
    STRING_CONCAT.get_or_init(|| {
        Regex::new(r#"\w+\s*\+=\s*["'`]|\w+\s*=\s*\w+\s*\+\s*["'`]|\w+\s*\+=\s*\w+"#).unwrap()
    })
}

pub struct StringConcatLoopDetector {
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
    fn find_concat_functions(&self, graph: &GraphStore) -> HashSet<String> {
        let mut concat_funcs = HashSet::new();

        for func in graph.get_functions() {
            if let Some(content) =
                crate::cache::global_cache().get_content(std::path::Path::new(&func.file_path))
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

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let concat_funcs = self.find_concat_functions(graph);
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
            if !matches!(ext, "py" | "js" | "ts" | "java" | "go" | "rb" | "php") {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let mut in_loop = false;
                let mut loop_line = 0;
                let mut brace_depth = 0;
                let mut _loop_var = String::new();

                for (i, line) in content.lines().enumerate() {
                    if loop_pattern().is_match(line) {
                        in_loop = true;
                        loop_line = i + 1;
                        brace_depth = 0;

                        // Try to extract loop variable for context
                        if let Some(caps) = for_var_pattern().captures(line) {
                            _loop_var = caps
                                .get(1)
                                .map(|m| m.as_str().to_string())
                                .unwrap_or_default();
                        }
                    }

                    if in_loop {
                        brace_depth += line.matches('{').count() as i32;
                        brace_depth -= line.matches('}').count() as i32;
                        if brace_depth < 0 {
                            in_loop = false;
                            continue;
                        }

                        if string_concat().is_match(line) {
                            let suggestion = Self::get_suggestion(ext);

                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "StringConcatLoopDetector".to_string(),
                                severity: Severity::Medium,
                                title: "String concatenation in loop".to_string(),
                                description: format!(
                                    "String concatenation inside loop (started line {}).\n\n\
                                     **Performance:** O(n²) time complexity. Each concatenation \
                                     creates a new string and copies all previous characters.\n\n\
                                     For 1000 iterations, this copies ~500,000 characters instead of 1000.",
                                    loop_line
                                ),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
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
                            in_loop = false;
                        }
                    }
                }
            }
        }

        // Graph-based: find loops that call concat functions
        if !concat_funcs.is_empty() {
            for func in graph.get_functions() {
                if findings.len() >= self.max_findings {
                    break;
                }

                let has_loop = if let Some(content) =
                    crate::cache::global_cache().get_content(std::path::Path::new(&func.file_path))
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
                            id: Uuid::new_v4().to_string(),
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
