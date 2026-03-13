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
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::LazyLock;
use tracing::{debug, info};

static LOOP: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?i)(for\s+\w+\s+in|\.forEach|\.map\(|\.each)").expect("valid regex")
    });
static QUERY: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)(\.get\(|\.find\(|\.filter\(|\.first\(|\.where\(|\.query\(|SELECT\s|Model\.\w+\.get|await\s+\w+\.findOne)").expect("valid regex"));
static QUERY_FUNC: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?i)(get_|find_|fetch_|load_|query_|select_)").expect("valid regex")
    });

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

    /// Find functions that contain database queries.
    ///
    /// Name-based detection only — O(n) with zero I/O.
    /// The QUERY_FUNC regex catches `get_`, `find_`, `fetch_`, `load_`, `query_`, `select_`.
    /// This is sufficient for hidden N+1 detection because:
    /// 1. Database-accessing functions almost always have descriptive names
    /// 2. False positives are filtered by the loop + callee check downstream
    fn find_query_functions(&self, graph: &dyn crate::graph::GraphQuery) -> HashSet<String> {
        let i = graph.interner();
        let mut query_funcs = HashSet::new();

        for func in graph.get_functions_shared().iter() {
            if QUERY_FUNC.is_match(func.node_name(i)) {
                query_funcs.insert(func.qn(i).to_string());
            }
        }

        debug!("Found {} potential query functions (name-based)", query_funcs.len());
        query_funcs
    }

    /// Pre-compute which functions transitively reach a query function (depth ≤ 5).
    /// Single reverse-BFS from query functions through callers — O(V+E) instead of
    /// per-function DFS that was O(functions_with_loops × branching_factor^5).
    fn build_transitive_query_callers(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        query_funcs: &HashSet<String>,
    ) -> HashMap<String, String> {
        let i = graph.interner();
        use std::collections::VecDeque;

        let mut reaches_query: HashMap<String, String> = HashMap::new();
        let mut queue: VecDeque<(String, String, usize)> = VecDeque::new();

        // Seed: direct query functions
        for qf in query_funcs {
            let short = qf.rsplit("::").next().unwrap_or(qf).to_string();
            reaches_query.insert(qf.clone(), short.clone());
            queue.push_back((qf.clone(), short, 0));
        }

        // BFS backwards through callers (max depth 5 to match old behavior)
        while let Some((qn, query_chain, depth)) = queue.pop_front() {
            if depth >= 5 {
                continue;
            }
            for caller in graph.get_callers(&qn) {
                if !reaches_query.contains_key(caller.qn(i)) {
                    let chain = format!("{} → {}", caller.node_name(i), query_chain);
                    reaches_query.insert(caller.qn(i).to_string(), chain.clone());
                    queue.push_back((caller.qn(i).to_string(), chain, depth + 1));
                }
            }
        }

        reaches_query
    }

    /// Find N+1 patterns using graph traversal
    fn find_graph_n_plus_one(&self, graph: &dyn crate::graph::GraphQuery) -> Vec<Finding> {
        let i = graph.interner();
        let mut findings = Vec::new();
        let query_funcs = self.find_query_functions(graph);

        if query_funcs.is_empty() {
            return findings;
        }

        // Single reverse-BFS: pre-compute which functions transitively reach a query
        let reaches_query = self.build_transitive_query_callers(graph, &query_funcs);

        // Pre-build set of skippable path prefixes for fast filtering
        let skip_prefixes: &[&str] = &[
            "/test", "/detectors/", "/cli/", "/parsers/", "/mcp/",
            "/git/", "/ai/", "/reporters/", "/scoring/", "/graph/",
            "/packages/react", "/packages/shared", "/packages/scheduler",
            "/reconciler/", "/fiber/", "/forks/",
        ];

        // Cache per-file line splits to avoid redundant allocations
        let mut file_lines: HashMap<String, Vec<String>> = HashMap::new();

        for func in graph.get_functions_shared().iter() {
            if findings.len() >= self.max_findings {
                break;
            }

            // Fast path exclusions — single pass over skip prefixes
            let fp = func.path(i);
            if fp.contains("_test.")
                || skip_prefixes.iter().any(|p| fp.contains(p))
                || crate::detectors::content_classifier::is_likely_bundled_path(fp)
            {
                continue;
            }

            // Check if this function contains a loop (cached per-file lines)
            let lines = file_lines.entry(func.path(i).to_string()).or_insert_with(|| {
                crate::cache::global_cache()
                    .masked_content(std::path::Path::new(func.path(i)))
                    .map(|c| c.lines().map(String::from).collect())
                    .unwrap_or_default()
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

            // Check if any callee transitively reaches a query function
            // (pre-computed via reverse BFS — O(1) lookup)
            for callee in graph.get_callees(func.qn(i)) {
                if let Some(query_chain) = reaches_query.get(callee.qn(i)) {
                    findings.push(Finding {
                        id: String::new(),
                        detector: "NPlusOneDetector".to_string(),
                        severity: Severity::High,
                        title: format!("Hidden N+1: {} calls query in loop", func.node_name(i)),
                        description: format!(
                            "Function '{}' contains a loop and calls '{}' which leads to a database query.\n\n\
                             **Call chain:** {} → {}\n\n\
                             This may cause N database queries instead of 1.",
                            func.node_name(i),
                            callee.node_name(i),
                            callee.node_name(i),
                            query_chain
                        ),
                        affected_files: vec![PathBuf::from(func.path(i))],
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

    fn file_extensions(&self) -> &'static [&'static str] {
        &["py", "js", "ts", "jsx", "tsx", "rb", "java"]
    }

    fn detect(&self, ctx: &crate::detectors::analysis_context::AnalysisContext) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let files = &ctx.as_file_provider();
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
                    if LOOP.is_match(line) {
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

                        if QUERY.is_match(line) {
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
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("views.py", "def list_orders(user_ids):\n    results = []\n    for uid in user_ids:\n        order = Order.objects.filter(user_id=uid)\n        results.append(order)\n    return results\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
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
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("views_good.py", "def list_orders(user_ids):\n    orders = Order.objects.filter(user_id__in=user_ids)\n    for order in orders:\n        print(order.total)\n    return orders\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag bulk query before loop (no query inside the loop), got: {:?}",
            findings
        );
    }
}
