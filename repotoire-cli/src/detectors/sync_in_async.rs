//! Sync in Async Detector
//!
//! Graph-enhanced detection of blocking calls in async contexts:
//! - Traces calls to sync functions from async functions
//! - Identifies hidden blocking through call chains
//! - Suggests specific async alternatives

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static ASYNC_FUNC: OnceLock<Regex> = OnceLock::new();
static BLOCKING: OnceLock<Regex> = OnceLock::new();

fn async_func() -> &'static Regex {
    ASYNC_FUNC.get_or_init(|| {
        Regex::new(r"(?i)(async\s+def|async\s+function|async\s+fn)").expect("valid regex")
    })
}

fn blocking() -> &'static Regex {
    BLOCKING.get_or_init(|| {
        Regex::new(r"(?i)(time\.sleep|Thread\.sleep|readFileSync|writeFileSync|execSync|spawnSync|requests\.(get|post|put|delete|head|patch)|urllib\.request|urlopen|subprocess\.(run|call|check_output)|os\.system|std::thread::sleep|std::fs::(read|write)|open\([^)]+\)\.read)").expect("valid regex")
    })
}

/// Get async alternative for a blocking call
fn get_async_alternative(blocking_call: &str) -> &'static str {
    let call_lower = blocking_call.to_lowercase();

    if call_lower.contains("time.sleep") {
        return "asyncio.sleep()";
    }
    if call_lower.contains("thread.sleep") {
        return "await new Promise(r => setTimeout(r, ms))";
    }
    if call_lower.contains("readfilesync") {
        return "await fs.promises.readFile()";
    }
    if call_lower.contains("writefilesync") {
        return "await fs.promises.writeFile()";
    }
    if call_lower.contains("execsync") || call_lower.contains("spawnsync") {
        return "await exec() from child_process/promises or execa";
    }
    if call_lower.contains("requests.") {
        return "aiohttp, httpx, or aiofiles";
    }
    if call_lower.contains("urllib") || call_lower.contains("urlopen") {
        return "aiohttp.ClientSession()";
    }
    if call_lower.contains("subprocess") || call_lower.contains("os.system") {
        return "asyncio.create_subprocess_exec()";
    }
    if call_lower.contains("std::thread::sleep") {
        return "tokio::time::sleep() or async-std equivalent";
    }
    if call_lower.contains("std::fs") {
        return "tokio::fs or async-std::fs";
    }
    if call_lower.contains("open(") {
        return "aiofiles.open() for Python";
    }

    "Use async equivalent"
}

pub struct SyncInAsyncDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
}

impl SyncInAsyncDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Find all functions that contain blocking calls
    fn find_blocking_functions(&self, graph: &dyn crate::graph::GraphQuery) -> HashSet<String> {
        let mut blocking_funcs = HashSet::new();

        for func in graph.get_functions() {
            if let Ok(content) = std::fs::read_to_string(&func.file_path) {
                let lines: Vec<&str> = content.lines().collect();
                let start = func.line_start.saturating_sub(1) as usize;
                let end = (func.line_end as usize).min(lines.len());

                for line in lines.get(start..end).unwrap_or(&[]) {
                    if blocking().is_match(line) {
                        blocking_funcs.insert(func.qualified_name.clone());
                        break;
                    }
                }
            }
        }

        blocking_funcs
    }

    /// Check if an async function calls any known blocking functions
    fn check_transitive_blocking(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        func: &crate::graph::CodeNode,
        blocking_funcs: &HashSet<String>,
    ) -> Vec<String> {
        let mut blocked_by = Vec::new();
        let callees = graph.get_callees(&func.qualified_name);

        for callee in callees {
            if blocking_funcs.contains(&callee.qualified_name) {
                blocked_by.push(callee.name.clone());
            }
        }

        blocked_by
    }

    /// Find containing async function
    fn find_async_function(
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

impl Detector for SyncInAsyncDetector {
    fn name(&self) -> &'static str {
        "sync-in-async"
    }
    fn description(&self) -> &'static str {
        "Detects blocking calls in async functions"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        // First pass: identify all functions with blocking calls
        let blocking_funcs = self.find_blocking_functions(graph);

        for path in files.files_with_extensions(&["py", "js", "ts", "jsx", "tsx"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            let path_str = path.to_string_lossy().to_string();
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");

            // Skip detector files (contain regex patterns as strings)
            if path_str.contains("/detectors/") {
                continue;
            }

            // Skip non-production paths (scripts, tests, examples)
            if crate::detectors::content_classifier::is_non_production_path(&path_str) {
                continue;
            }

            if let Some(content) = files.content(path) {
                let lines: Vec<&str> = content.lines().collect();
                let mut in_async = false;
                let mut async_indent = 0;
                let mut current_async_name = String::new();

                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    let current_indent = line.chars().take_while(|c| c.is_whitespace()).count();

                    // Track async function scope
                    if async_func().is_match(line) {
                        in_async = true;
                        async_indent = current_indent;
                        if let Some(name) =
                            Self::find_async_function(graph, &path_str, (i + 1) as u32)
                        {
                            current_async_name = name;
                        }
                    }

                    // Check if we've left async scope (Python indentation)
                    if in_async
                        && ext == "py"
                        && !line.trim().is_empty()
                        && current_indent <= async_indent
                        && i > 0
                        && !async_func().is_match(line)
                    {
                        in_async = false;
                    }

                    if !in_async {
                        continue;
                    }

                    // Check for direct blocking calls
                    if let Some(m) = blocking().find(line) {
                        let blocking_call = m.as_str();
                        let alternative = get_async_alternative(blocking_call);

                        let mut notes = Vec::new();
                        if !current_async_name.is_empty() {
                            notes.push(format!("📦 In async function: `{}`", current_async_name));
                        }

                        // Check for transitive blocking
                        if let Some(func) = graph.get_functions().into_iter().find(|f| {
                            f.file_path == path_str
                                && f.line_start <= (i + 1) as u32
                                && f.line_end >= (i + 1) as u32
                        }) {
                            let transitive =
                                self.check_transitive_blocking(graph, &func, &blocking_funcs);
                            if !transitive.is_empty() {
                                notes.push(format!(
                                    "⚠️ Also calls blocking functions: {}",
                                    transitive.join(", ")
                                ));
                            }
                        }

                        let context_notes = if notes.is_empty() {
                            String::new()
                        } else {
                            format!("\n\n**Analysis:**\n{}", notes.join("\n"))
                        };

                        let severity = if blocking_call.contains("sleep")
                            || blocking_call.contains("Sync")
                            || blocking_call.contains("subprocess")
                        {
                            Severity::High // explicit blocking patterns in async context
                        } else {
                            Severity::Medium
                        };

                        findings.push(Finding {
                            id: String::new(),
                            detector: "SyncInAsyncDetector".to_string(),
                            severity,
                            title: format!("Blocking call `{}` in async function", blocking_call),
                            description: format!(
                                "Synchronous blocking call inside async function will block the event loop, \
                                 preventing other async tasks from running.{}",
                                context_notes
                            ),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some(format!(
                                "Replace with async alternative: `{}`\n\n\
                                 Example:\n\
                                 ```python\n\
                                 # Instead of: time.sleep(1)\n\
                                 await asyncio.sleep(1)\n\
                                 \n\
                                 # Instead of: requests.get(url)\n\
                                 async with aiohttp.ClientSession() as session:\n\
                                     async with session.get(url) as response:\n\
                                         data = await response.json()\n\
                                 ```",
                                alternative
                            )),
                            estimated_effort: Some("20 minutes".to_string()),
                            category: Some("performance".to_string()),
                            cwe_id: Some("CWE-400".to_string()),
                            why_it_matters: Some(
                                "Blocking calls in async code prevent the event loop from processing other tasks. \
                                 This defeats the purpose of async/await and can cause the entire application to hang.".to_string()
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!(
            "SyncInAsyncDetector found {} findings (graph-aware)",
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
    fn test_detects_time_sleep_in_async_def() {
        let store = GraphStore::in_memory();
        let detector = SyncInAsyncDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("server.py", "import asyncio\nimport time\n\nasync def handle_request():\n    data = await fetch_data()\n    time.sleep(1)\n    return data\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).unwrap();
        assert!(
            !findings.is_empty(),
            "Should detect time.sleep() inside async def"
        );
        assert!(
            findings
                .iter()
                .any(|f| f.title.contains("time.sleep")),
            "Finding should mention time.sleep"
        );
    }

    #[test]
    fn test_no_finding_for_sync_function() {
        let store = GraphStore::in_memory();
        let detector = SyncInAsyncDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("utils.py", "import time\n\ndef slow_function():\n    time.sleep(1)\n    return 42\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag time.sleep() in a regular (non-async) function, got: {:?}",
            findings
        );
    }
}
