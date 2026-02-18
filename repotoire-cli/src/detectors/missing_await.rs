//! Missing Await Detector
//!
//! Graph-enhanced detection of async calls without await:
//! - Uses graph to identify async functions defined in the codebase
//! - Traces calls to known async functions across file boundaries
//! - Checks for Promise chain patterns (.then, .catch)

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static ASYNC_CALL: OnceLock<Regex> = OnceLock::new();
static ASYNC_DEF: OnceLock<Regex> = OnceLock::new();

fn async_call() -> &'static Regex {
    ASYNC_CALL.get_or_init(|| {
        Regex::new(r"(?i)(fetch\(|axios\.|\.json\(\)|\.text\(\)|async_\w+\(|aio\w+\.|\.read\(\)|\.write\(\)|\.send\(\)|\.get\(|\.post\(|\.put\(|\.delete\()").unwrap()
    })
}

fn async_def() -> &'static Regex {
    ASYNC_DEF.get_or_init(|| {
        Regex::new(r"(?:async\s+(?:def|function)|async\s+\w+\s*\(|async\s+\w+\s*=)").unwrap()
    })
}

pub struct MissingAwaitDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl MissingAwaitDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Identify async functions from the graph
    fn find_async_functions(graph: &dyn crate::graph::GraphQuery) -> HashSet<String> {
        let mut async_funcs = HashSet::new();

        for func in graph.get_functions() {
            // Check if function is marked as async in properties
            if let Some(is_async) = func.properties.get("is_async") {
                if is_async.as_bool().unwrap_or(false) {
                    async_funcs.insert(func.name.clone());
                }
            }

            // Check function name patterns that are typically async
            let name_lower = func.name.to_lowercase();
            if name_lower.starts_with("async_")
                || name_lower.starts_with("fetch_")
                || name_lower.starts_with("get_")
                || name_lower.starts_with("load_")
                || name_lower.ends_with("_async")
            {
                // Check file content for async keyword
                if let Ok(content) = std::fs::read_to_string(&func.file_path) {
                    let lines: Vec<&str> = content.lines().collect();
                    if let Some(line) = lines.get(func.line_start.saturating_sub(1) as usize) {
                        if line.contains("async ") {
                            async_funcs.insert(func.name.clone());
                        }
                    }
                }
            }
        }

        async_funcs
    }

    /// Find containing function name
    fn find_containing_function(
        graph: &dyn crate::graph::GraphQuery,
        file_path: &str,
        line: u32,
    ) -> Option<String> {
        graph
            .get_functions()
            .into_iter()
            .find(|f| f.file_path == file_path && f.line_start <= line && f.line_end >= line)
            .map(|f| f.name)
    }
}

impl Detector for MissingAwaitDetector {
    fn name(&self) -> &'static str {
        "missing-await"
    }
    fn description(&self) -> &'static str {
        "Detects async calls without await"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        // Find all async functions in the codebase
        let known_async_funcs = Self::find_async_functions(graph);

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

            let path_str = path.to_string_lossy().to_string();
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "js" | "ts" | "jsx" | "tsx" | "py") {
                continue;
            }

            // Skip non-production paths
            if crate::detectors::content_classifier::is_non_production_path(&path_str) {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();
                let mut in_async = false;
                let mut async_depth = 0;
                let mut current_async_func = String::new();

                for (i, line) in lines.iter().enumerate() {
                    // Track async function scope
                    if async_def().is_match(line) {
                        in_async = true;
                        async_depth = line.chars().take_while(|c| c.is_whitespace()).count();

                        // Extract function name
                        if let Some(func) =
                            Self::find_containing_function(graph, &path_str, (i + 1) as u32)
                        {
                            current_async_func = func;
                        }
                    }

                    // Check if we've left the async function (Python indentation)
                    if in_async && ext == "py" {
                        let current_indent = line.chars().take_while(|c| c.is_whitespace()).count();
                        if !line.trim().is_empty() && current_indent <= async_depth && i > 0 {
                            in_async = false;
                        }
                    }

                    // JS/TS: track braces
                    if in_async
                        && matches!(ext, "js" | "ts" | "jsx" | "tsx")
                        && line.contains("}")
                        && !line.contains("{")
                    {
                        // Simplified scope tracking
                        if line.trim() == "}" || line.trim() == "};" {
                            in_async = false;
                        }
                    }

                    if !in_async {
                        continue;
                    }

                    // Check for missing await on known async patterns
                    let has_async_call = async_call().is_match(line);

                    // Also check calls to known async functions
                    let calls_known_async = known_async_funcs
                        .iter()
                        .any(|func| line.contains(&format!("{}(", func)));

                    if has_async_call || calls_known_async {
                        // Check if properly awaited or intentionally fire-and-forget
                        let trimmed_line = line.trim();
                        let is_awaited = line.contains("await ")
                            || line.contains(".then(")
                            || line.contains("Promise.")
                            || line.contains("return ") && line.contains("(");

                        // Detect intentional fire-and-forget patterns
                        let is_fire_and_forget =
                            // void operator is the explicit TS/JS fire-and-forget idiom
                            trimmed_line.starts_with("void ")
                            // .catch() means errors are handled, just not awaited
                            || line.contains(".catch(")
                            // Common fire-and-forget variable assignment patterns
                            || line.contains("// fire-and-forget")
                            || line.contains("// fire and forget")
                            || line.contains("// best-effort")
                            || line.contains("// non-blocking")
                            || line.contains("// async, don't wait");

                        // Skip telemetry/tracking/analytics functions (inherently fire-and-forget)
                        let is_telemetry = {
                            let ll = line.to_lowercase();
                            ll.contains("track(")
                                || ll.contains("track_")
                                || ll.contains("telemetry")
                                || ll.contains("analytics")
                                || ll.contains("log_event")
                                || ll.contains("logevent")
                                || ll.contains("send_event")
                                || ll.contains("sendevent")
                                || ll.contains("report_")
                                || ll.contains("metric")
                        };

                        if !is_awaited && !is_fire_and_forget && !is_telemetry {
                            // Build context
                            let mut notes = Vec::new();
                            if !current_async_func.is_empty() {
                                notes.push(format!(
                                    "📦 In async function: `{}`",
                                    current_async_func
                                ));
                            }
                            if calls_known_async {
                                notes.push(
                                    "🔍 Calls a function defined as async in this codebase"
                                        .to_string(),
                                );
                            }

                            let context_notes = if notes.is_empty() {
                                String::new()
                            } else {
                                format!("\n\n**Analysis:**\n{}", notes.join("\n"))
                            };

                            let severity = if calls_known_async {
                                Severity::High // Calling a known async without await = definite bug
                            } else {
                                Severity::Medium
                            };

                            findings.push(Finding {
                                id: String::new(),
                                detector: "MissingAwaitDetector".to_string(),
                                severity,
                                title: "Async call without await".to_string(),
                                description: format!(
                                    "Async function called without await - returns Promise/coroutine, not the actual value.{}",
                                    context_notes
                                ),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some(
                                    "Add await before the async call:\n\
                                     ```javascript\n\
                                     const result = await fetchData();  // JS/TS\n\
                                     ```\n\
                                     ```python\n\
                                     result = await fetch_data()  # Python\n\
                                     ```".to_string()
                                ),
                                estimated_effort: Some("2 minutes".to_string()),
                                category: Some("bug-risk".to_string()),
                                cwe_id: None,
                                why_it_matters: Some(
                                    "Without await, you get a Promise/coroutine object instead of the actual result. \
                                     This can cause subtle bugs where code appears to work but operates on the wrong type.".to_string()
                                ),
                                ..Default::default()
                            });
                        }
                    }
                }
            }
        }

        info!(
            "MissingAwaitDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
