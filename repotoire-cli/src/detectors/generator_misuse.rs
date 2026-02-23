//! Generator misuse detector
//!
//! Graph-enhanced detection of generator anti-patterns:
//! - Single-yield generators (should be simple functions)
//! - Generators that are immediately list()-ified
//! - Uses graph to find how generators are consumed

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static GENERATOR_DEF: OnceLock<Regex> = OnceLock::new();
static YIELD_STMT: OnceLock<Regex> = OnceLock::new();
static LIST_CALL: OnceLock<Regex> = OnceLock::new();

fn generator_def() -> &'static Regex {
    GENERATOR_DEF.get_or_init(|| Regex::new(r"def\s+(\w+)\s*\(").expect("valid regex"))
}

fn yield_stmt() -> &'static Regex {
    YIELD_STMT.get_or_init(|| Regex::new(r"\byield\b").expect("valid regex"))
}

fn list_call() -> &'static Regex {
    LIST_CALL.get_or_init(|| Regex::new(r"list\s*\(\s*(\w+)\s*\(").expect("valid regex"))
}

/// Detects generator functions with only one yield statement
pub struct GeneratorMisuseDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
}

impl GeneratorMisuseDetector {
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            repository_path: PathBuf::from("."),
            max_findings: 50,
        }
    }

    pub fn with_path(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            config: DetectorConfig::new(),
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Count yield statements in a function
    fn count_yields(lines: &[&str], func_start: usize, indent: usize) -> (usize, bool) {
        let mut count = 0;
        let mut in_loop = false;

        for line in lines.iter().skip(func_start + 1) {
            let current_indent = line.chars().take_while(|c| c.is_whitespace()).count();

            // Stop if we've left the function
            if !line.trim().is_empty() && current_indent <= indent {
                break;
            }

            // Track if yield is inside a loop
            if line.contains("for ") || line.contains("while ") {
                in_loop = true;
            }

            if yield_stmt().is_match(line) {
                count += 1;
            }
        }

        (count, in_loop)
    }

    /// Check if function body uses try/yield/finally (resource management pattern)
    fn is_resource_management_yield(lines: &[&str], func_start: usize, indent: usize) -> bool {
        let mut has_try = false;
        let mut has_finally = false;

        for line in lines.iter().skip(func_start + 1) {
            let current_indent = line.chars().take_while(|c| c.is_whitespace()).count();
            if !line.trim().is_empty() && current_indent <= indent {
                break;
            }
            let trimmed = line.trim();
            if trimmed.starts_with("try:") {
                has_try = true;
            }
            if trimmed.starts_with("finally:") {
                has_finally = true;
            }
        }
        has_try && has_finally
    }

    /// Check if file imports from frameworks that use yield for DI
    fn has_framework_yield_import(content: &str) -> bool {
        content.contains("from fastapi")
            || content.contains("from starlette")
            || content.contains("from contextlib import contextmanager")
            || content.contains("from contextlib import asynccontextmanager")
            || content.contains("import contextlib")
    }

    /// Check if function has @contextmanager or @asynccontextmanager decorator
    fn has_contextmanager_decorator(lines: &[&str], func_start: usize) -> bool {
        for i in (0..func_start).rev() {
            let trimmed = lines[i].trim();
            if trimmed.is_empty() { continue; }
            if trimmed.starts_with('@') {
                return trimmed.contains("contextmanager");
            }
            if !trimmed.starts_with('@') { break; }
        }
        false
    }

    /// Find all generators that are immediately converted to list
    fn find_list_wrapped_generators(
        &self,
        _graph: &dyn crate::graph::GraphQuery,
        files: &dyn crate::detectors::file_provider::FileProvider,
    ) -> HashSet<String> {
        let mut wrapped = HashSet::new();

        for path in files.files_with_extension("py") {
            if let Some(content) = files.content(path) {
                for cap in list_call().captures_iter(&content) {
                    if let Some(func_name) = cap.get(1) {
                        wrapped.insert(func_name.as_str().to_string());
                    }
                }
            }
        }

        wrapped
    }

    /// Check if generator is consumed lazily anywhere
    fn is_consumed_lazily(&self, func_name: &str, graph: &dyn crate::graph::GraphQuery) -> bool {
        // Check callers to see how the generator is consumed
        if let Some(func) = graph
            .get_functions()
            .into_iter()
            .find(|f| f.name == func_name)
        {
            let callers = graph.get_callers(&func.qualified_name);

            for caller in callers {
                if let Ok(content) = std::fs::read_to_string(&caller.file_path) {
                    // Check if caller iterates lazily (for loop) vs list()
                    let has_lazy = content.contains(&"for ".to_string())
                        && content.contains(&format!("{}(", func_name));
                    let has_list = content.contains(&format!("list({}(", func_name));

                    if has_lazy && !has_list {
                        return true;
                    }
                }
            }
        }

        false
    }
}

impl Default for GeneratorMisuseDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for GeneratorMisuseDetector {
    fn name(&self) -> &'static str {
        "GeneratorMisuseDetector"
    }

    fn description(&self) -> &'static str {
        "Detects single-yield generators that add unnecessary complexity"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        // Find generators that are always list()-wrapped
        let list_wrapped = self.find_list_wrapped_generators(graph, files);

        for path in files.files_with_extension("py") {
            if findings.len() >= self.max_findings {
                break;
            }

            let path_str = path.to_string_lossy().to_string();

            // Skip test files
            if crate::detectors::base::is_test_path(&path_str) {
                continue;
            }

            if let Some(content) = files.content(path) {
                let lines: Vec<&str> = content.lines().collect();

                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    if let Some(caps) = generator_def().captures(line) {
                        let func_name = caps.get(1).map(|m| m.as_str()).unwrap_or("");
                        let indent = line.chars().take_while(|c| c.is_whitespace()).count();

                        // Check if it's a generator (has yield)
                        let (yield_count, yield_in_loop) = Self::count_yields(&lines, i, indent);

                        if yield_count == 0 {
                            continue;
                        } // Not a generator

                        // Single yield outside loop = probably should be a simple return
                        if yield_count == 1 && !yield_in_loop {
                            // Skip resource management patterns (try/yield/finally)
                            if Self::is_resource_management_yield(&lines, i, indent)
                                && (Self::has_framework_yield_import(&content)
                                    || Self::has_contextmanager_decorator(&lines, i))
                            {
                                continue;
                            }

                            findings.push(Finding {
                                id: String::new(),
                                detector: "GeneratorMisuseDetector".to_string(),
                                severity: Severity::Low,
                                title: format!("Single-yield generator: `{}`", func_name),
                                description: format!(
                                    "Generator `{}` only yields once and not in a loop. \
                                     Consider using a simple function with return instead.\n\n\
                                     **Why it matters:** Single-yield generators add complexity \
                                     without the lazy evaluation benefits.",
                                    func_name
                                ),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: None,
                                suggested_fix: Some(format!(
                                    "Convert to a simple function:\n\n\
                                     ```python\n\
                                     # Instead of:\n\
                                     def {}(...):\n\
                                         yield some_value\n\
                                     \n\
                                     # Use:\n\
                                     def {}(...):\n\
                                         return some_value\n\
                                     ```",
                                    func_name, func_name
                                )),
                                estimated_effort: Some("10 minutes".to_string()),
                                category: Some("code-quality".to_string()),
                                cwe_id: None,
                                why_it_matters: Some(
                                    "Single-yield generators require callers to use next() or iterate, \
                                     adding complexity without benefits.".to_string()
                                ),
                                ..Default::default()
                            });
                        }

                        // Generator always wrapped in list() = defeats the purpose
                        if list_wrapped.contains(func_name)
                            && !self.is_consumed_lazily(func_name, graph)
                        {
                            findings.push(Finding {
                                id: String::new(),
                                detector: "GeneratorMisuseDetector".to_string(),
                                severity: Severity::Low,
                                title: format!("Generator always list()-wrapped: `{}`", func_name),
                                description: format!(
                                    "Generator `{}` is always wrapped in `list()`, defeating lazy evaluation.\n\n\
                                     **Analysis:** No callers consume this generator lazily.",
                                    func_name
                                ),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: None,
                                suggested_fix: Some(format!(
                                    "Consider returning a list directly:\n\n\
                                     ```python\n\
                                     # Instead of:\n\
                                     def {}(...):\n\
                                         for item in items:\n\
                                             yield transform(item)\n\
                                     \n\
                                     # result = list({}(...))  # Always converted\n\
                                     \n\
                                     # Use:\n\
                                     def {}(...):\n\
                                         return [transform(item) for item in items]\n\
                                     ```",
                                    func_name, func_name, func_name
                                )),
                                estimated_effort: Some("15 minutes".to_string()),
                                category: Some("performance".to_string()),
                                cwe_id: None,
                                why_it_matters: Some(
                                    "Generators wrapped in list() lose lazy evaluation benefits \
                                     and add unnecessary overhead.".to_string()
                                ),
                                ..Default::default()
                            });
                        }
                    }
                }
            }
        }

        info!(
            "GeneratorMisuseDetector found {} findings (graph-aware)",
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
    fn test_detects_single_yield_generator() {
        let store = GraphStore::in_memory();
        let detector = GeneratorMisuseDetector::with_path("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("utils.py", "\ndef single_value():\n    yield 42\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            !findings.is_empty(),
            "Should detect single-yield generator"
        );
        assert!(findings.iter().any(|f| f.title.contains("Single-yield generator")));
    }

    #[test]
    fn test_no_finding_for_generator_with_loop() {
        let store = GraphStore::in_memory();
        let detector = GeneratorMisuseDetector::with_path("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("utils.py", "\ndef multi_yield(items):\n    for item in items:\n        yield item * 2\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag generator with yield inside a loop, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_fastapi_dependency() {
        let store = GraphStore::in_memory();
        let detector = GeneratorMisuseDetector::with_path("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("deps.py", "from fastapi import Depends\n\ndef get_db():\n    db = SessionLocal()\n    try:\n        yield db\n    finally:\n        db.close()\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag FastAPI try/yield/finally dependency. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_contextmanager() {
        let store = GraphStore::in_memory();
        let detector = GeneratorMisuseDetector::with_path("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("utils.py", "from contextlib import contextmanager\n\n@contextmanager\ndef managed_resource():\n    resource = acquire()\n    try:\n        yield resource\n    finally:\n        release(resource)\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag contextmanager try/yield/finally. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
