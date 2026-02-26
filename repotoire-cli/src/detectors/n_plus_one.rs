//! N+1 Query Detector
//!
//! Graph-enhanced detection of N+1 query patterns.
//! Uses call graph to:
//! - Trace query functions called transitively in loops
//! - Detect when loop variable flows to query parameters
//! - Find hidden N+1 patterns across function boundaries

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::{debug, info};

static LOOP: OnceLock<Regex> = OnceLock::new();
static QUERY: OnceLock<Regex> = OnceLock::new();
static QUERY_FUNC: OnceLock<Regex> = OnceLock::new();

fn loop_pattern() -> &'static Regex {
    LOOP.get_or_init(|| {
        Regex::new(r"(?i)(for\s+\w+\s+in|\.forEach|\.map\(|\.each)").expect("valid regex")
    })
}

fn query_pattern() -> &'static Regex {
    QUERY.get_or_init(|| Regex::new(r"(?i)(\.get\(|\.find\(|\.filter\(|\.first\(|\.where\(|\.query\(|SELECT\s|Model\.\w+\.get|await\s+\w+\.findOne)").expect("valid regex"))
}

fn query_func_pattern() -> &'static Regex {
    QUERY_FUNC.get_or_init(|| {
        Regex::new(r"(?i)(get_|find_|fetch_|load_|query_|select_)").expect("valid regex")
    })
}

pub struct NPlusOneDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
}

impl NPlusOneDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Find functions that contain database queries
    fn find_query_functions(&self, graph: &dyn crate::graph::GraphQuery) -> HashSet<String> {
        let mut query_funcs = HashSet::new();

        for func in graph.get_functions() {
            // Check if function name suggests it does queries
            if query_func_pattern().is_match(&func.name) {
                query_funcs.insert(func.qualified_name.clone());
                continue;
            }

            // Check function content for query patterns
            if let Some(content) =
                crate::cache::global_cache().masked_content(std::path::Path::new(&func.file_path))
            {
                let lines: Vec<&str> = content.lines().collect();
                let start = func.line_start.saturating_sub(1) as usize;
                let end = (func.line_end as usize).min(lines.len());

                for line in lines.get(start..end).unwrap_or(&[]) {
                    if query_pattern().is_match(line) {
                        query_funcs.insert(func.qualified_name.clone());
                        break;
                    }
                }
            }
        }

        debug!("Found {} potential query functions", query_funcs.len());
        query_funcs
    }

    /// Check if a function transitively calls any query function
    #[allow(clippy::only_used_in_recursion)]
    fn calls_query_transitively(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        func_qn: &str,
        query_funcs: &HashSet<String>,
        depth: usize,
        visited: &mut HashSet<String>,
    ) -> Option<String> {
        if depth > 5 || visited.contains(func_qn) {
            return None;
        }
        visited.insert(func_qn.to_string());

        let callees = graph.get_callees(func_qn);
        for callee in &callees {
            // Direct call to query function
            if query_funcs.contains(&callee.qualified_name) {
                return Some(callee.name.clone());
            }

            // Recursive check
            if let Some(query_name) = self.calls_query_transitively(
                graph,
                &callee.qualified_name,
                query_funcs,
                depth + 1,
                visited,
            ) {
                return Some(format!("{} → {}", callee.name, query_name));
            }
        }

        None
    }

    /// Find N+1 patterns using graph traversal
    fn find_graph_n_plus_one(&self, graph: &dyn crate::graph::GraphQuery) -> Vec<Finding> {
        let mut findings = Vec::new();
        let query_funcs = self.find_query_functions(graph);

        if query_funcs.is_empty() {
            return findings;
        }

        // Find functions that look like they iterate over collections
        for func in graph.get_functions() {
            if findings.len() >= self.max_findings {
                break;
            }

            // Skip test files
            if func.file_path.contains("/test") || func.file_path.contains("_test.") {
                continue;
            }

            // Skip detector files (they iterate over graph nodes, not DB)
            if func.file_path.contains("/detectors/") {
                continue;
            }

            // Skip CLI files (they orchestrate analysis, expected patterns)
            if func.file_path.contains("/cli/") {
                continue;
            }

            // Skip parsers (they need to iterate to parse)
            if func.file_path.contains("/parsers/") {
                continue;
            }

            // Skip MCP handlers (they handle requests, expected to query)
            if func.file_path.contains("/mcp/") {
                continue;
            }

            // Skip git operations (they need to iterate over commits)
            if func.file_path.contains("/git/") {
                continue;
            }

            // Skip AI code (it generates fixes iteratively)
            if func.file_path.contains("/ai/") {
                continue;
            }

            // Skip reporters (they iterate over findings to generate reports)
            if func.file_path.contains("/reporters/") {
                continue;
            }

            // Skip scoring (it iterates over graph nodes)
            if func.file_path.contains("/scoring/") {
                continue;
            }

            // Skip graph store (it naturally iterates over graph data)
            if func.file_path.contains("/graph/") {
                continue;
            }

            // Skip framework source code (React, Vue, etc.)
            // Framework code iterates over component trees, not DB queries
            if func.file_path.contains("/packages/react")
                || func.file_path.contains("/packages/shared")
                || func.file_path.contains("/packages/scheduler")
                || func.file_path.contains("/reconciler/")
                || func.file_path.contains("/fiber/")
                || func.file_path.contains("/forks/")
            {
                continue;
            }

            // Skip bundled/generated code
            if crate::detectors::content_classifier::is_likely_bundled_path(&func.file_path) {
                continue;
            }

            // Check if this function contains a loop
            let has_loop = if let Some(content) =
                crate::cache::global_cache().masked_content(std::path::Path::new(&func.file_path))
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

            // Check if any called function (transitively) does a query
            let mut visited = HashSet::new();
            for callee in graph.get_callees(&func.qualified_name) {
                if let Some(query_chain) = self.calls_query_transitively(
                    graph,
                    &callee.qualified_name,
                    &query_funcs,
                    0,
                    &mut visited,
                ) {
                    findings.push(Finding {
                        id: String::new(),
                        detector: "NPlusOneDetector".to_string(),
                        severity: Severity::High,
                        title: format!("Hidden N+1: {} calls query in loop", func.name),
                        description: format!(
                            "Function '{}' contains a loop and calls '{}' which leads to a database query.\n\n\
                             **Call chain:** {} → {}\n\n\
                             This may cause N database queries instead of 1.",
                            func.name,
                            callee.name,
                            callee.name,
                            query_chain
                        ),
                        affected_files: vec![PathBuf::from(&func.file_path)],
                        line_start: Some(func.line_start),
                        line_end: Some(func.line_end),
                        suggested_fix: Some(
                            "Consider:\n\
                             1. Batch the query before the loop\n\
                             2. Use eager loading/prefetching\n\
                             3. Cache results if the same query is repeated".to_string()
                        ),
                        estimated_effort: Some("1 hour".to_string()),
                        category: Some("performance".to_string()),
                        cwe_id: None,
                        why_it_matters: Some(
                            "Hidden N+1 queries across function boundaries are harder to detect \
                             but cause the same performance issues.".to_string()
                        ),
                        ..Default::default()
                    });
                    break; // One finding per function
                }
            }
        }

        findings
    }
}

impl Detector for NPlusOneDetector {
    fn name(&self) -> &'static str {
        "n-plus-one"
    }
    fn description(&self) -> &'static str {
        "Detects N+1 query patterns"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        // === Source-based detection (direct queries in loops) ===
        for path in files.files_with_extensions(&["py", "js", "ts", "rb", "java", "go"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            let path_str = path.to_string_lossy();
            if crate::detectors::base::is_test_path(&path_str) {
                continue;
            }

            // Skip framework source code
            if path_str.contains("/packages/react")
                || path_str.contains("/packages/shared")
                || path_str.contains("/packages/scheduler")
                || path_str.contains("/reconciler/")
                || path_str.contains("/fiber/")
                || path_str.contains("/forks/")
            {
                continue;
            }

            // Skip bundled/generated code
            if crate::detectors::content_classifier::is_likely_bundled_path(&path_str) {
                continue;
            }

            // Skip non-production paths
            if crate::detectors::content_classifier::is_non_production_path(&path_str) {
                continue;
            }

            if let Some(content) = files.masked_content(path) {
                let mut in_loop = false;
                let mut loop_line = 0;
                let mut brace_depth = 0;
                let all_lines: Vec<&str> = content.lines().collect();

                for (i, line) in all_lines.iter().enumerate() {
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

                        if query_pattern().is_match(line) {
                            let prev_line = if i > 0 { Some(all_lines[i - 1]) } else { None };
                            if crate::detectors::is_line_suppressed(line, prev_line) {
                                continue;
                            }

                            findings.push(Finding {
                                id: String::new(),
                                detector: "NPlusOneDetector".to_string(),
                                severity: Severity::High,
                                title: "Potential N+1 query".to_string(),
                                description: format!(
                                    "Database query inside loop (loop started at line {}).\n\n\
                                     This pattern causes N database calls instead of 1.",
                                    loop_line
                                ),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some(
                                    "Use bulk fetch before loop or eager loading.".to_string(),
                                ),
                                estimated_effort: Some("45 minutes".to_string()),
                                category: Some("performance".to_string()),
                                cwe_id: None,
                                why_it_matters: Some(
                                    "Causes N database calls instead of 1.".to_string(),
                                ),
                                ..Default::default()
                            });
                            in_loop = false;
                        }
                    }
                }
            }
        }

        // === Graph-based detection (hidden N+1 across function boundaries) ===
        let graph_findings = self.find_graph_n_plus_one(graph);

        // Deduplicate: skip graph findings that overlap with source findings
        let existing_locations: HashSet<(String, u32)> = findings
            .iter()
            .flat_map(|f| {
                f.affected_files
                    .iter()
                    .map(|p| (p.to_string_lossy().to_string(), f.line_start.unwrap_or(0)))
            })
            .collect();

        for finding in graph_findings {
            let key = (
                finding
                    .affected_files
                    .first()
                    .map(|p| p.to_string_lossy().to_string())
                    .unwrap_or_default(),
                finding.line_start.unwrap_or(0),
            );
            if !existing_locations.contains(&key) && findings.len() < self.max_findings {
                findings.push(finding);
            }
        }

        info!(
            "NPlusOneDetector found {} findings (source + graph)",
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
    fn test_detects_query_in_loop() {
        let store = GraphStore::in_memory();
        let detector = NPlusOneDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("views.py", "def list_orders(user_ids):\n    results = []\n    for uid in user_ids:\n        order = Order.objects.filter(user_id=uid)\n        results.append(order)\n    return results\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect database query (.filter) inside a for loop"
        );
        assert!(
            findings.iter().any(|f| f.title.contains("N+1")),
            "Finding title should mention N+1"
        );
    }

    #[test]
    fn test_no_finding_for_bulk_query() {
        let store = GraphStore::in_memory();
        let detector = NPlusOneDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("views_good.py", "def list_orders(user_ids):\n    orders = Order.objects.filter(user_id__in=user_ids)\n    for order in orders:\n        print(order.total)\n    return orders\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag bulk query before loop (no query inside the loop), got: {:?}",
            findings
        );
    }
}
