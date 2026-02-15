//! Duplicate Code Detector
//!
//! Graph-enhanced detection of copy-pasted code blocks.
//! Uses call graph to:
//! - Check if duplicates have similar callers (stronger refactor signal)
//! - Suggest optimal location for extracted function based on callers
//! - Skip duplicates in test code or generated files

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use tracing::{debug, info};
use uuid::Uuid;

pub struct DuplicateCodeDetector {
    repository_path: PathBuf,
    max_findings: usize,
    min_lines: usize,
}

impl DuplicateCodeDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
            min_lines: 6,
        }
    }

    fn normalize_line(line: &str) -> String {
        // Normalize whitespace and remove comments
        let trimmed = line.trim();
        if trimmed.starts_with("//") || trimmed.starts_with("#") || trimmed.starts_with("*") {
            return String::new();
        }
        trimmed.split_whitespace().collect::<Vec<_>>().join(" ")
    }

    /// Check if a file is a test file
    fn is_test_file(path: &std::path::Path) -> bool {
        let path_str = path.to_string_lossy();
        path_str.contains("/test")
            || path_str.contains("_test.")
            || path_str.contains(".test.")
            || path_str.contains("/tests/")
            || path_str.contains("/spec/")
            || path_str.contains(".spec.")
    }

    /// Find functions containing the duplicate at each location
    fn find_containing_functions(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        locations: &[(PathBuf, usize)],
    ) -> Vec<Option<String>> {
        locations
            .iter()
            .map(|(path, line)| {
                let path_str = path.to_string_lossy();
                graph
                    .get_functions()
                    .into_iter()
                    .find(|f| {
                        f.file_path == path_str
                            && f.line_start <= *line as u32
                            && f.line_end >= *line as u32
                    })
                    .map(|f| f.qualified_name)
            })
            .collect()
    }

    /// Analyze caller similarity for duplicated code
    fn analyze_caller_similarity(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        containing_funcs: &[Option<String>],
    ) -> (usize, String) {
        let valid_funcs: Vec<&String> =
            containing_funcs.iter().filter_map(|f| f.as_ref()).collect();

        if valid_funcs.len() < 2 {
            return (0, String::new());
        }

        // Collect all callers for each function
        let caller_sets: Vec<HashSet<String>> = valid_funcs
            .iter()
            .map(|qn| {
                graph
                    .get_callers(qn)
                    .into_iter()
                    .map(|c| c.qualified_name)
                    .collect()
            })
            .collect();

        // Find common callers across all duplicates
        if caller_sets.is_empty() {
            return (0, String::new());
        }

        let common_callers: HashSet<String> = caller_sets[0]
            .iter()
            .filter(|caller| caller_sets.iter().skip(1).all(|set| set.contains(*caller)))
            .cloned()
            .collect();

        // Suggest extraction location based on common callers
        let suggestion = if !common_callers.is_empty() {
            // Find the module that most common callers are in
            let mut module_counts: HashMap<String, usize> = HashMap::new();
            for caller in &common_callers {
                if let Some(func) = graph
                    .get_functions()
                    .into_iter()
                    .find(|f| &f.qualified_name == caller)
                {
                    let module = func
                        .file_path
                        .rsplit('/')
                        .nth(1)
                        .unwrap_or("utils")
                        .to_string();
                    *module_counts.entry(module).or_default() += 1;
                }
            }
            module_counts
                .into_iter()
                .max_by_key(|(_, count)| *count)
                .map(|(module, _)| module)
                .unwrap_or_else(|| "utils".to_string())
        } else {
            String::new()
        };

        (common_callers.len(), suggestion)
    }
}

impl Detector for DuplicateCodeDetector {
    fn name(&self) -> &'static str {
        "duplicate-code"
    }
    fn description(&self) -> &'static str {
        "Detects copy-pasted code blocks"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let mut blocks: HashMap<String, Vec<(PathBuf, usize)>> = HashMap::new();

        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            // Skip test files for duplicate detection
            if Self::is_test_file(path) {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(
                ext,
                "py" | "js"
                    | "ts"
                    | "jsx"
                    | "tsx"
                    | "java"
                    | "go"
                    | "rs"
                    | "rb"
                    | "php"
                    | "c"
                    | "cpp"
            ) {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();

                // Sliding window of min_lines
                for i in 0..lines.len().saturating_sub(self.min_lines) {
                    let block: String = lines[i..i + self.min_lines]
                        .iter()
                        .map(|l| Self::normalize_line(l))
                        .filter(|l| !l.is_empty())
                        .collect::<Vec<_>>()
                        .join("\n");

                    if block.len() > 50 {
                        // Ignore trivial blocks
                        blocks
                            .entry(block)
                            .or_default()
                            .push((path.to_path_buf(), i + 1));
                    }
                }
            }
        }

        // Find duplicates with graph-enhanced analysis
        for (_block, locations) in blocks {
            if locations.len() > 1 && findings.len() < self.max_findings {
                let files: Vec<_> = locations.iter().map(|(p, _)| p.clone()).collect();
                let first_line = locations[0].1;

                // === Graph-enhanced analysis ===
                let containing_funcs = self.find_containing_functions(graph, &locations);
                let (common_callers, suggested_module) =
                    self.analyze_caller_similarity(graph, &containing_funcs);

                // Boost severity if duplicates have common callers (stronger refactor signal)
                let severity = if common_callers >= 2 {
                    Severity::High // Same code called by same functions = definite refactor
                } else if locations.len() > 3 {
                    Severity::Medium
                } else {
                    Severity::Low
                };

                // Build graph-aware description
                let caller_note = if common_callers > 0 {
                    format!("\n\n📞 **{} common caller(s)** call all duplicate locations - strong refactor signal.", common_callers)
                } else {
                    String::new()
                };

                // Build smart suggestion
                let suggestion = if !suggested_module.is_empty() && common_callers > 0 {
                    format!(
                        "Extract into a shared function in the `{}` module (where most callers are).",
                        suggested_module
                    )
                } else {
                    "Extract into a shared function.".to_string()
                };

                // List containing functions if available
                let func_list: Vec<String> = containing_funcs
                    .iter()
                    .zip(locations.iter())
                    .filter_map(|(f, (path, line))| {
                        f.as_ref().map(|qn| {
                            let name = qn.rsplit("::").next().unwrap_or(qn);
                            format!(
                                "  - `{}` ({}:{})",
                                name,
                                path.file_name().unwrap_or_default().to_string_lossy(),
                                line
                            )
                        })
                    })
                    .take(5)
                    .collect();

                let func_note = if !func_list.is_empty() {
                    format!("\n\n**Found in functions:**\n{}", func_list.join("\n"))
                } else {
                    String::new()
                };

                findings.push(Finding {
                    id: Uuid::new_v4().to_string(),
                    detector: "DuplicateCodeDetector".to_string(),
                    severity,
                    title: format!("Duplicate code ({} occurrences)", locations.len()),
                    description: format!(
                        "Same code block found in **{} places**.{}{}",
                        locations.len(),
                        func_note,
                        caller_note
                    ),
                    affected_files: files,
                    line_start: Some(first_line as u32),
                    line_end: Some((first_line + self.min_lines) as u32),
                    suggested_fix: Some(suggestion),
                    estimated_effort: Some(if common_callers >= 2 { "30 minutes".to_string() } else { "20 minutes".to_string() }),
                    category: Some("maintainability".to_string()),
                    cwe_id: None,
                    why_it_matters: Some(
                        "Duplicate code means duplicate bugs. When you fix one, you must remember to fix all copies."
                            .to_string()
                    ),
                    ..Default::default()
                });
            }
        }

        info!(
            "DuplicateCodeDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
