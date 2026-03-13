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
use std::sync::LazyLock;
use tracing::info;

static LOOP: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?i)(for\s+\w+\s+in|\.forEach|for\s*\(|while\s*\()").expect("valid regex")
    });
static REGEX_NEW: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?i)(Regex::new|re\.compile|new RegExp|Pattern\.compile)")
            .expect("valid regex")
    });

/// Check if a regex compilation is cached (OnceLock, lazy_static, etc.)
fn is_cached_regex(line: &str) -> bool {
    let trimmed = line.trim();
    trimmed.contains("get_or_init")
        || trimmed.contains("lazy_static")
        || trimmed.contains("LazyLock")
        || trimmed.contains("Lazy::new")
        || trimmed.contains("OnceLock")
        || trimmed.contains("OnceCell")
        // Skip lines that are just string constants containing regex pattern names
        || trimmed.starts_with('"')
        || trimmed.starts_with("r#\"")
        || trimmed.starts_with("r\"")
        // Skip lines inside Rust multi-line string literals (end with \n\)
        || trimmed.ends_with("\\n\\")
        || trimmed.ends_with(".to_string(),")
}

/// Check if a Regex::new call is inside a cached context by looking at surrounding lines
fn is_cached_regex_context(content: &str, line_idx: usize) -> bool {
    let lines: Vec<&str> = content.lines().collect();
    // Check ±5 lines for caching patterns
    let start = line_idx.saturating_sub(5);
    let end = (line_idx + 3).min(lines.len());
    for j in start..end {
        if let Some(l) = lines.get(j) {
            if is_cached_regex(l) {
                return true;
            }
        }
    }
    false
}

pub struct RegexInLoopDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
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

    /// Find functions that compile regexes.
    /// Uses per-file line caching to avoid redundant content reads (71K functions → ~3.4K files).
    fn find_regex_functions(&self, graph: &dyn crate::graph::GraphQuery, det_ctx: &crate::detectors::DetectorContext) -> HashSet<String> {
        let i = graph.interner();
        let mut regex_funcs = HashSet::new();
        let mut file_lines: std::collections::HashMap<String, Vec<String>> = std::collections::HashMap::new();
        let ctx = Some(det_ctx);

        for func in graph.get_functions_shared().iter() {
            let lines = file_lines.entry(func.path(i).to_string()).or_insert_with(|| {
                let path = std::path::Path::new(func.path(i));
                // Try DetectorContext first, fall back to global_cache
                if let Some(content) = ctx.and_then(|c| c.file_contents.get(path)).map(|s| &**s) {
                    content.lines().map(String::from).collect()
                } else {
                    crate::cache::global_cache()
                        .content(path)
                        .map(|c| c.lines().map(String::from).collect())
                        .unwrap_or_default()
                }
            });

            let start = func.line_start.saturating_sub(1) as usize;
            let end = (func.line_end as usize).min(lines.len());

            // Check if the function itself uses caching patterns
            let func_is_cached = lines.get(start..end).map(|slice| {
                slice.iter().any(|line| {
                    line.contains("get_or_init")
                        || line.contains("OnceLock")
                        || line.contains("OnceCell")
                        || line.contains("lazy_static")
                        || line.contains("LazyLock")
                })
            }).unwrap_or(false);

            if !func_is_cached {
                for line in lines.get(start..end).unwrap_or(&[]) {
                    if REGEX_NEW.is_match(line) && !is_cached_regex(line) {
                        regex_funcs.insert(func.qn(i).to_string());
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
        det_ctx: &crate::detectors::DetectorContext,
        func_qn: &str,
        regex_funcs: &HashSet<String>,
        visited: &mut HashSet<String>,
        depth: usize,
    ) -> Option<String> {
        let i = graph.interner();
        if depth > 3 || visited.contains(func_qn) {
            return None;
        }
        visited.insert(func_qn.to_string());

        // Use pre-built callees map
        if let Some(callee_qn_list) = det_ctx.callees_by_qn.get(func_qn) {
            for callee_qn in callee_qn_list {
                let callee_name = graph.get_node(callee_qn)
                    .map(|n| n.node_name(i).to_string())
                    .unwrap_or_else(|| callee_qn.clone());
                if regex_funcs.contains(callee_qn) {
                    return Some(callee_name);
                }
                if let Some(chain) = self.calls_regex_transitively(
                    graph,
                    det_ctx,
                    callee_qn,
                    regex_funcs,
                    visited,
                    depth + 1,
                ) {
                    return Some(format!("{} \u{2192} {}", callee_name, chain));
                }
            }
        } else {
            // Fallback: use graph.get_callees() (empty callees map)
            for callee in graph.get_callees(func_qn) {
                if regex_funcs.contains(callee.qn(i)) {
                    return Some(callee.node_name(i).to_string());
                }
                if let Some(chain) = self.calls_regex_transitively(
                    graph,
                    det_ctx,
                    callee.qn(i),
                    regex_funcs,
                    visited,
                    depth + 1,
                ) {
                    return Some(format!("{} \u{2192} {}", callee.node_name(i), chain));
                }
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

    fn file_extensions(&self) -> &'static [&'static str] {
        &["py", "js", "ts", "jsx", "tsx", "rb", "java", "go"]
    }

    fn detect(&self, ctx: &crate::detectors::analysis_context::AnalysisContext) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let det_ctx = &ctx.detector_ctx;
        let files = &ctx.as_file_provider();
        let i = graph.interner();
        let mut findings = vec![];

        // Find all functions that compile regex
        let regex_funcs = self.find_regex_functions(graph, det_ctx);

        for path in files.files_with_extensions(&["py", "js", "ts", "java", "rs", "go"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            if let Some(content) = files.content(path) {
                let _path_str = path.to_string_lossy().to_string();
                let is_python = path.extension().and_then(|e| e.to_str()) == Some("py");
                let is_rust = path.extension().and_then(|e| e.to_str()) == Some("rs");
                let mut in_loop = false;
                let mut loop_line = 0;
                let mut brace_depth = 0;
                let mut loop_indent: usize = 0;
                let mut loop_line_idx: usize = 0;
                let all_lines: Vec<&str> = content.lines().collect();

                for (i, line) in all_lines.iter().enumerate() {
                    // Skip Python comments
                    if is_python && line.trim().starts_with('#') {
                        continue;
                    }

                    // Skip list comprehensions (one-shot constructs)
                    if is_python && line.contains('[') && line.contains(" for ") && line.contains(" in ") {
                        continue;
                    }

                    if LOOP.is_match(line) {
                        in_loop = true;
                        loop_line = i + 1;
                        loop_line_idx = i;
                        if is_python {
                            loop_indent = line.len() - line.trim_start().len();
                        } else {
                            brace_depth = 0;
                        }
                    }

                    if in_loop {
                        if is_python {
                            // Python: exit loop scope when indentation returns to/below loop level
                            let trimmed = line.trim();
                            if !trimmed.is_empty() && i > loop_line_idx {
                                let current_indent = line.len() - line.trim_start().len();
                                if current_indent <= loop_indent {
                                    in_loop = false;
                                    continue;
                                }
                            }
                        } else {
                            // Brace-based languages
                            brace_depth += line.matches('{').count() as i32;
                            brace_depth -= line.matches('}').count() as i32;
                            if brace_depth < 0 {
                                in_loop = false;
                                continue;
                            }
                        }

                        // Direct regex compilation in loop
                        // Skip cached patterns: OnceLock, lazy_static, static, get_or_init
                        if REGEX_NEW.is_match(line)
                            && !is_cached_regex(line)
                            && !is_cached_regex_context(&content, i)
                        {
                            let prev_line = if i > 0 { Some(all_lines[i - 1]) } else { None };
                            if crate::detectors::is_line_suppressed(line, prev_line) {
                                continue;
                            }
                            // Skip test code in Rust files (string literals containing regex patterns)
                            if is_rust && crate::detectors::rust_smells::is_test_context(line, &content, i) {
                                continue;
                            }

                            findings.push(Finding {
                                id: String::new(),
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
        // Skip Rust files — OnceLock/lazy_static caching is pervasive and
        // the call graph can't distinguish cached from uncached compilation
        if !regex_funcs.is_empty() {
            let mut graph_file_lines: std::collections::HashMap<String, Vec<String>> = std::collections::HashMap::new();

            for func in graph.get_functions_shared().iter() {
                if findings.len() >= self.max_findings {
                    break;
                }

                if func.path(i).ends_with(".rs") {
                    continue;
                }

                // Check if function contains a loop (per-file line caching)
                let lines = graph_file_lines.entry(func.path(i).to_string()).or_insert_with(|| {
                    let path = std::path::Path::new(func.path(i));
                    // Try DetectorContext first, fall back to global_cache
                    if let Some(content) = det_ctx.file_contents.get(path).map(|s| &**s) {
                        content.lines().map(String::from).collect()
                    } else {
                        crate::cache::global_cache()
                            .content(path)
                            .map(|c| c.lines().map(String::from).collect())
                            .unwrap_or_default()
                    }
                });

                let start = func.line_start.saturating_sub(1) as usize;
                let end = (func.line_end as usize).min(lines.len());

                let has_loop = lines
                    .get(start..end)
                    .map(|slice| slice.iter().any(|line| LOOP.is_match(line)))
                    .unwrap_or(false);

                if !has_loop {
                    continue;
                }

                // Check callees for regex compilation
                // Collect callee (qn, name) pairs from pre-built callees map or graph fallback
                let callee_pairs: Vec<(String, String)> = det_ctx.callees_by_qn.get(func.qn(i))
                    .map(|v| v.iter().map(|qn| {
                        let name = graph.get_node(qn)
                            .map(|n| n.node_name(i).to_string())
                            .unwrap_or_else(|| qn.clone());
                        (qn.clone(), name)
                    }).collect())
                    .unwrap_or_else(|| {
                        // Fallback: use graph.get_callees() (empty callees map)
                        graph.get_callees(func.qn(i))
                            .into_iter()
                            .map(|c| (c.qn(i).to_string(), c.node_name(i).to_string()))
                            .collect()
                    });

                for (callee_qn, callee_name) in &callee_pairs {
                    let mut visited = HashSet::new();
                    if let Some(chain) = self.calls_regex_transitively(
                        graph,
                        det_ctx,
                        callee_qn,
                        &regex_funcs,
                        &mut visited,
                        0,
                    ) {
                        findings.push(Finding {
                            id: String::new(),
                            detector: "RegexInLoopDetector".to_string(),
                            severity: Severity::Medium,
                            title: format!("Hidden regex in loop: {}", func.node_name(i)),
                            description: format!(
                                "Function '{}' contains a loop and calls '{}' which compiles a regex.\n\n\
                                 **Call chain:** {} \u{2192} {}\n\n\
                                 This may cause regex compilation on every iteration.",
                                func.node_name(i), callee_name, callee_name, chain
                            ),
                            affected_files: vec![PathBuf::from(func.path(i))],
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_re_compile_in_loop() {
        let store = GraphStore::in_memory();
        let detector = RegexInLoopDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("parser.py", "import re\n\ndef process_lines(lines):\n    for line in lines:\n        pattern = re.compile(r'\\d+')\n        match = pattern.match(line)\n        if match:\n            print(match.group())\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect re.compile() inside a for loop"
        );
        assert!(
            findings.iter().any(|f| f.title.contains("Regex compiled inside loop")),
            "Finding title should mention regex compiled inside loop"
        );
    }

    #[test]
    fn test_no_finding_when_regex_outside_loop() {
        let store = GraphStore::in_memory();
        let detector = RegexInLoopDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("parser_good.py", "import re\n\ndef process_lines(lines):\n    pattern = re.compile(r'\\d+')\n    for line in lines:\n        match = pattern.match(line)\n        if match:\n            print(match.group())\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag re.compile() outside a loop, got: {:?}",
            findings
        );
    }

    #[test]
    fn test_no_finding_for_python_regex_outside_loop() {
        let store = GraphStore::in_memory();
        let detector = RegexInLoopDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("parser.py", "import re\n\nfor item in items:\n    process(item)\n\npattern = re.compile(r'\\w+')\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(findings.is_empty(), "re.compile after loop exits should not be flagged. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>());
    }

    #[test]
    fn test_no_finding_for_list_comprehension() {
        let store = GraphStore::in_memory();
        let detector = RegexInLoopDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("security.py", "import re\n\nREDIRECT_HOSTS = [re.compile(r) for r in settings.ALLOWED_REDIRECTS]\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(findings.is_empty(), "List comprehension re.compile should not be flagged. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>());
    }

    #[test]
    fn test_no_finding_for_regex_in_comment() {
        let store = GraphStore::in_memory();
        let detector = RegexInLoopDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("settings.py", "LANGUAGES = [\n    ('en', 'English'),\n    ('fr', 'French'),\n]\n# LANGUAGES_BIDI = re.compile(r'...')\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(findings.is_empty(), "Commented-out re.compile should not be flagged. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>());
    }

    #[test]
    fn test_still_detects_regex_inside_python_loop() {
        let store = GraphStore::in_memory();
        let detector = RegexInLoopDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("slow.py", "import re\n\nfor pattern in patterns:\n    compiled = re.compile(pattern)\n    compiled.match(text)\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(!findings.is_empty(), "Should still detect re.compile inside a for loop");
    }
}
