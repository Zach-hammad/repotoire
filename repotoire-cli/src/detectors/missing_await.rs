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

fn async_call() -> &'static Regex {
    ASYNC_CALL.get_or_init(|| {
        // Only match clearly async I/O patterns — NOT generic method calls
        Regex::new(r"(?i)\b(fetch\(|axios\.\w+\(|\.\bjson\(\)|\.\btext\(\)|async_\w+\(|aio\w+\.)")
            .expect("valid regex")
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

    /// Identify async functions from the graph — only trust the is_async property
    fn find_async_functions(graph: &dyn crate::graph::GraphQuery) -> HashSet<String> {
        let mut async_funcs = HashSet::new();
        for func in graph.get_functions() {
            if let Some(is_async) = func.properties.get("is_async") {
                if is_async.as_bool().unwrap_or(false) {
                    async_funcs.insert(func.name.clone());
                }
            }
        }
        async_funcs
    }

    /// Check if a line is an async function/method DECLARATION (not a call)
    fn is_async_declaration(line: &str) -> bool {
        let trimmed = line.trim();
        // async function foo() {
        // async foo() {
        // const foo = async () => {
        // const foo = async function() {
        // export async function foo() {
        // async def foo():  (Python)
        trimmed.contains("async function ")
            || trimmed.contains("async def ")
            || trimmed.contains("= async (")
            || trimmed.contains("= async function")
            || (trimmed.starts_with("async ") && trimmed.contains('(') && trimmed.contains('{'))
            || (trimmed.starts_with("export async "))
    }

    /// Check if the function body actually contains await (it's a real async function)
    fn function_body_has_await(lines: &[&str], start: usize, ext: &str) -> bool {
        let mut brace_depth = 0i32;
        let mut found_open = false;
        for line in &lines[start..] {
            if !found_open {
                if line.contains('{') || (ext == "py" && line.contains(':')) {
                    found_open = true;
                    // Count all braces on the opening line (e.g. `async function foo() {`)
                    brace_depth =
                        line.matches('{').count() as i32 - line.matches('}').count() as i32;
                    if line.contains("await ") {
                        return true;
                    }
                    continue;
                }
                continue;
            }
            brace_depth += line.matches('{').count() as i32;
            brace_depth -= line.matches('}').count() as i32;
            if line.contains("await ") {
                return true;
            }
            if ext != "py" && brace_depth <= 0 {
                break;
            }
            // Python: stop at dedent
            if ext == "py" {
                let indent = line.len() - line.trim_start().len();
                let start_indent = lines[start].len() - lines[start].trim_start().len();
                if !line.trim().is_empty()
                    && indent <= start_indent
                    && !line.trim().starts_with('#')
                {
                    break;
                }
            }
        }
        false
    }
}

impl Detector for MissingAwaitDetector {
    fn name(&self) -> &'static str {
        "missing-await"
    }
    fn description(&self) -> &'static str {
        "Detects async calls without await"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, _files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];
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

            if crate::detectors::content_classifier::is_non_production_path(&path_str) {
                continue;
            }

            let Some(content) = crate::cache::global_cache().content(path) else {
                continue;
            };
            let lines: Vec<&str> = content.lines().collect();

            // Find async function boundaries using brace counting
            // We need to know: (a) are we inside an async function? (b) which one?
            let mut async_ranges: Vec<(usize, usize, String)> = Vec::new(); // (start, end, name)
                                                                            // Pre-fetch functions for this file to avoid O(n²) graph lookups
            let file_funcs: Vec<_> = graph
                .get_functions()
                .into_iter()
                .filter(|f| f.file_path == path_str || path_str.ends_with(&f.file_path))
                .collect();

            for (i, line) in lines.iter().enumerate() {
                let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                if crate::detectors::is_line_suppressed(line, prev_line) {
                    continue;
                }

                if !Self::is_async_declaration(line) {
                    continue;
                }

                // Find the function name from pre-fetched list
                let func_name = file_funcs
                    .iter()
                    .find(|f| f.line_start <= (i + 1) as u32 && f.line_end >= (i + 1) as u32)
                    .map(|f| f.name.clone())
                    .unwrap_or_default();

                // Only flag if the function body actually uses await
                // (an async function that never awaits is fine — it just returns a Promise)
                if !Self::function_body_has_await(&lines, i, ext) {
                    continue;
                }

                // Find the function end via brace counting
                let mut depth = 0i32;
                let mut end = i;
                for (j, l) in lines[i..].iter().enumerate() {
                    depth += l.matches('{').count() as i32;
                    depth -= l.matches('}').count() as i32;
                    if depth <= 0 && j > 0 {
                        end = i + j;
                        break;
                    }
                }
                if end == i {
                    end = (i + 50).min(lines.len() - 1);
                } // fallback

                async_ranges.push((i, end, func_name));
            }

            // Now scan for un-awaited async calls within async function bodies
            for (start, end, func_name) in &async_ranges {
                for i in (*start + 1)..=*end {
                    let Some(line) = lines.get(i) else { continue };
                    let trimmed = line.trim();

                    // Skip blank lines, comments, declarations
                    if trimmed.is_empty()
                        || trimmed.starts_with("//")
                        || trimmed.starts_with("/*")
                        || trimmed.starts_with('*')
                        || Self::is_async_declaration(line)
                    {
                        continue;
                    }

                    // Skip React event handler assignments
                    {
                        let ll = trimmed.to_lowercase();
                        if ll.contains("onsubmit=")
                            || ll.contains("onclick=")
                            || ll.contains("onchange=")
                            || ll.contains("onpress=")
                            || ll.contains("onblur=")
                            || ll.contains("onfocus=")
                        {
                            continue;
                        }
                    }

                    // Skip React Query / hook options
                    if trimmed.contains("useMutation(")
                        || trimmed.contains("useQuery(")
                        || trimmed.contains("queryFn")
                        || trimmed.contains("mutationFn")
                    {
                        continue;
                    }

                    let has_async_call = async_call().is_match(line);
                    let calls_known_async = known_async_funcs.iter().any(|func| {
                        line.contains(&format!("{}(", func))
                                && !line.contains(&format!("async {}", func)) // skip declarations
                                && !line.contains(&format!("function {}", func))
                        // skip declarations
                    });

                    if !has_async_call && !calls_known_async {
                        continue;
                    }

                    // Check if properly awaited
                    let next_line = lines.get(i + 1).copied().unwrap_or("");
                    let prev_line = if i > 0 {
                        lines.get(i - 1).copied().unwrap_or("")
                    } else {
                        ""
                    };

                    let is_awaited = line.contains("await ")
                        || line.contains(".then(")
                        || line.contains("Promise.")
                        || (line.contains("return ") && (has_async_call || calls_known_async))
                        // Multi-line: await on next line
                        || next_line.trim().starts_with("await ")
                        || next_line.trim().starts_with(".then(")
                        // Previous line started a chain: const x = await \n  fetch(...)
                        || prev_line.contains("await");

                    // Fire-and-forget patterns
                    let is_fire_and_forget = trimmed.starts_with("void ")
                        || line.contains(".catch(")
                        || line.contains("// fire-and-forget")
                        || line.contains("// fire and forget")
                        || line.contains("// best-effort")
                        || line.contains("// non-blocking");

                    // Telemetry — inherently fire-and-forget
                    let is_telemetry = {
                        let ll = line.to_lowercase();
                        ll.contains("track(")
                            || ll.contains("telemetry")
                            || ll.contains("analytics")
                            || ll.contains("log_event")
                            || ll.contains("send_event")
                            || ll.contains("metric")
                    };

                    if is_awaited || is_fire_and_forget || is_telemetry {
                        continue;
                    }

                    let severity = if calls_known_async {
                        Severity::High
                    } else {
                        Severity::Medium
                    };

                    let mut notes = Vec::new();
                    if !func_name.is_empty() {
                        notes.push(format!("📦 In async function: `{}`", func_name));
                    }
                    if calls_known_async {
                        notes.push(
                            "🔍 Calls a function defined as async in this codebase".to_string(),
                        );
                    }
                    let context_notes = if notes.is_empty() {
                        String::new()
                    } else {
                        format!("\n\n**Analysis:**\n{}", notes.join("\n"))
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
                        suggested_fix: Some("Add `await` before the async call.".to_string()),
                        estimated_effort: Some("2 minutes".to_string()),
                        category: Some("bug-risk".to_string()),
                        cwe_id: None,
                        why_it_matters: Some(
                            "Without await, you get a Promise object instead of the actual result.".to_string()
                        ),
                        ..Default::default()
                    });
                }
            }
        }

        info!("MissingAwaitDetector found {} findings", findings.len());
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_fetch_without_await() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("api.js");
        std::fs::write(
            &file,
            r#"async function loadData() {
  const config = "default";
  fetch("/api/data");
  const result = await process(config);
  return result;
}
"#,
        )
        .unwrap();

        let store = GraphStore::in_memory();
        let detector = MissingAwaitDetector::new(dir.path());
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&store, &empty_files).unwrap();
        assert!(
            !findings.is_empty(),
            "Should detect fetch() without await in async function"
        );
    }

    #[test]
    fn test_no_finding_when_awaited() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("api_good.js");
        std::fs::write(
            &file,
            r#"async function loadData() {
  const res = await fetch("/api/data");
  const data = await res.json();
  return data;
}
"#,
        )
        .unwrap();

        let store = GraphStore::in_memory();
        let detector = MissingAwaitDetector::new(dir.path());
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&store, &empty_files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag properly awaited calls, got: {:?}",
            findings
        );
    }
}
