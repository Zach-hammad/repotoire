//! Regex Compilation in Loop Detector
//!
//! Graph-enhanced detection of regex compilation inside loops.
//! Uses graph to:
//! - Find hidden patterns (loop calls function that compiles regex)
//! - Track regex functions that might be called in loops

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

static LOOP: OnceLock<Regex> = OnceLock::new();
static REGEX_NEW: OnceLock<Regex> = OnceLock::new();

fn loop_pattern() -> &'static Regex {
    LOOP.get_or_init(|| Regex::new(r"(?i)(for\s+\w+\s+in|\.forEach|for\s*\(|while\s*\()").unwrap())
}

fn regex_new() -> &'static Regex {
    REGEX_NEW.get_or_init(|| {
        Regex::new(r"(?i)(Regex::new|re\.compile|new RegExp|Pattern\.compile)").unwrap()
    })
}

pub struct RegexInLoopDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl RegexInLoopDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Find functions that compile regexes
    fn find_regex_functions(&self, graph: &dyn crate::graph::GraphQuery) -> HashSet<String> {
        let mut regex_funcs = HashSet::new();

        for func in graph.get_functions() {
            // Check if function compiles regex
            if let Some(content) =
                crate::cache::global_cache().get_content(std::path::Path::new(&func.file_path))
            {
                let lines: Vec<&str> = content.lines().collect();
                let start = func.line_start.saturating_sub(1) as usize;
                let end = (func.line_end as usize).min(lines.len());

                for line in lines.get(start..end).unwrap_or(&[]) {
                    if regex_new().is_match(line) {
                        regex_funcs.insert(func.qualified_name.clone());
                        break;
                    }
                }
            }
        }

        regex_funcs
    }

    /// Check if function transitively compiles regex
    #[allow(clippy::only_used_in_recursion)]
    fn calls_regex_transitively(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        func_qn: &str,
        regex_funcs: &HashSet<String>,
        visited: &mut HashSet<String>,
        depth: usize,
    ) -> Option<String> {
        if depth > 3 || visited.contains(func_qn) {
            return None;
        }
        visited.insert(func_qn.to_string());

        for callee in graph.get_callees(func_qn) {
            if regex_funcs.contains(&callee.qualified_name) {
                return Some(callee.name.clone());
            }
            if let Some(chain) = self.calls_regex_transitively(
                graph,
                &callee.qualified_name,
                regex_funcs,
                visited,
                depth + 1,
            ) {
                return Some(format!("{} → {}", callee.name, chain));
            }
        }
        None
    }
}

impl Detector for RegexInLoopDetector {
    fn name(&self) -> &'static str {
        "regex-in-loop"
    }
    fn description(&self) -> &'static str {
        "Detects regex compilation inside loops"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        // Find all functions that compile regex
        let regex_funcs = self.find_regex_functions(graph);

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings {
                break;
            }
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py" | "js" | "ts" | "java" | "rs" | "go") {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let _path_str = path.to_string_lossy().to_string();
                let mut in_loop = false;
                let mut loop_line = 0;
                let mut brace_depth = 0;

                for (i, line) in content.lines().enumerate() {
                    if loop_pattern().is_match(line) {
                        in_loop = true;
                        loop_line = i + 1;
                        brace_depth = 0;
                    }

                    if in_loop {
                        brace_depth += line.matches('{').count() as i32;
                        brace_depth -= line.matches('}').count() as i32;
                        if brace_depth < 0 {
                            in_loop = false;
                            continue;
                        }

                        // Direct regex compilation in loop
                        if regex_new().is_match(line) {
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "RegexInLoopDetector".to_string(),
                                severity: Severity::Medium,
                                title: "Regex compiled inside loop".to_string(),
                                description: format!(
                                    "Regex compiled in loop (loop starts line {}).\n\n\
                                     **Performance impact:** Regex compilation is expensive. \
                                     In a loop of N iterations, this costs N compilations instead of 1.",
                                    loop_line
                                ),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some(
                                    "Move regex compilation outside the loop:\n\
                                     ```rust\n\
                                     let re = Regex::new(pattern)?;  // Before loop\n\
                                     for item in items {\n\
                                         re.is_match(item);  // Reuse\n\
                                     }\n\
                                     ```".to_string()
                                ),
                                estimated_effort: Some("10 minutes".to_string()),
                                category: Some("performance".to_string()),
                                cwe_id: None,
                                why_it_matters: Some(
                                    "Regex compilation involves parsing and optimization. \
                                     Doing it once instead of N times can dramatically improve performance.".to_string()
                                ),
                                ..Default::default()
                            });
                            in_loop = false;
                        }
                    }
                }
            }
        }

        // Graph-based: find loops that call regex-compiling functions
        if !regex_funcs.is_empty() {
            for func in graph.get_functions() {
                if findings.len() >= self.max_findings {
                    break;
                }

                // Check if function contains a loop
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

                // Check callees for regex compilation
                for callee in graph.get_callees(&func.qualified_name) {
                    let mut visited = HashSet::new();
                    if let Some(chain) = self.calls_regex_transitively(
                        graph,
                        &callee.qualified_name,
                        &regex_funcs,
                        &mut visited,
                        0,
                    ) {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "RegexInLoopDetector".to_string(),
                            severity: Severity::Medium,
                            title: format!("Hidden regex in loop: {}", func.name),
                            description: format!(
                                "Function '{}' contains a loop and calls '{}' which compiles a regex.\n\n\
                                 **Call chain:** {} → {}\n\n\
                                 This may cause regex compilation on every iteration.",
                                func.name, callee.name, callee.name, chain
                            ),
                            affected_files: vec![PathBuf::from(&func.file_path)],
                            line_start: Some(func.line_start),
                            line_end: Some(func.line_end),
                            suggested_fix: Some(
                                "Options:\n\
                                 1. Cache the compiled regex (lazy_static, OnceLock)\n\
                                 2. Pass pre-compiled regex as parameter\n\
                                 3. Restructure to compile once before loop".to_string()
                            ),
                            estimated_effort: Some("20 minutes".to_string()),
                            category: Some("performance".to_string()),
                            cwe_id: None,
                            why_it_matters: Some(
                                "Hidden regex compilation across function boundaries \
                                 is harder to spot but equally impactful.".to_string()
                            ),
                            ..Default::default()
                        });
                        break;
                    }
                }
            }
        }

        info!(
            "RegexInLoopDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
