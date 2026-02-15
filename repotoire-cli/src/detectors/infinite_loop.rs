//! Infinite loop detector
//!
//! Graph-enhanced detection of potential infinite loops:
//! - Detects while True/while(true) without break
//! - Detects for loops with no exit condition
//! - Uses graph to check if loop calls functions that might break
//! - Identifies intentional infinite loops (servers, event loops)

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;
use uuid::Uuid;

static INFINITE_WHILE: OnceLock<Regex> = OnceLock::new();
static BREAK_RETURN: OnceLock<Regex> = OnceLock::new();

fn infinite_while() -> &'static Regex {
    INFINITE_WHILE.get_or_init(|| {
        Regex::new(r"(?i)(while\s*\(\s*true\s*\)|while\s+True\s*:|while\s*\(\s*1\s*\)|for\s*\(\s*;\s*;\s*\)|loop\s*\{)").unwrap()
    })
}

fn break_return() -> &'static Regex {
    BREAK_RETURN.get_or_init(|| {
        Regex::new(r"\b(break|return|raise|throw|exit|panic!|std::process::exit)\b").unwrap()
    })
}

/// Detects potential infinite loops
pub struct InfiniteLoopDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
}

impl InfiniteLoopDetector {
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

    /// Check if the loop body contains break/return
    fn has_exit_in_body(lines: &[&str], loop_start: usize, indent: usize) -> bool {
        for line in lines.iter().skip(loop_start + 1) {
            let current_indent = line.chars().take_while(|c| c.is_whitespace()).count();

            // Stop if we've exited the loop (dedented)
            if !line.trim().is_empty() && current_indent <= indent {
                break;
            }

            if break_return().is_match(line) {
                return true;
            }
        }
        false
    }

    /// Check if loop appears to be intentional server/event loop
    fn is_intentional_loop(lines: &[&str], loop_start: usize, path: &str) -> bool {
        // Common intentional infinite loop patterns in path
        let path_lower = path.to_lowercase();
        if path_lower.contains("server")
            || path_lower.contains("main")
            || path_lower.contains("daemon")
            || path_lower.contains("worker")
            || path_lower.contains("event")
            || path_lower.contains("run")
            || path_lower.contains("loop")
            || path_lower.contains("poll")
            || path_lower.contains("listen")
            || path_lower.contains("serve")
            || path_lower.contains("dispatch")
            || path_lower.contains("scheduler")
            || path_lower.contains("executor")
            || path_lower.contains("runtime")
            // Urbit-specific
            || path_lower.contains("pier")
            || path_lower.contains("king")
            || path_lower.contains("lord")
            || path_lower.contains("serf")
            || path_lower.contains("vere")
        {
            return true;
        }

        // Check surrounding context
        let start = loop_start.saturating_sub(5);
        for line in lines.get(start..loop_start).unwrap_or(&[]) {
            let lower = line.to_lowercase();
            if lower.contains("server")
                || lower.contains("main loop")
                || lower.contains("event loop")
                || lower.contains("forever")
                || lower.contains("daemon")
            {
                return true;
            }
        }

        // Check loop body for server-like/event-loop operations
        for line in lines.iter().skip(loop_start).take(30) {
            let lower = line.to_lowercase();
            // Network/IO blocking calls
            if lower.contains("accept(")
                || lower.contains("recv(")
                || lower.contains("listen")
                || lower.contains("await")
                || lower.contains("poll(")
                || lower.contains("select(")
                || lower.contains("epoll")
                || lower.contains("kqueue")
                || lower.contains("read(")
                || lower.contains("write(")
                || lower.contains("getchar")
                || lower.contains("fgets(")
                || lower.contains("scanf")
            {
                return true;
            }
            // Synchronization/waiting
            if lower.contains("sleep(")
                || lower.contains("usleep")
                || lower.contains("nanosleep")
                || lower.contains("wait(")
                || lower.contains("waitpid")
                || lower.contains("pthread_cond_wait")
                || lower.contains("sem_wait")
                || lower.contains("mutex_lock")
                || lower.contains("condition_variable")
            {
                return true;
            }
            // Event loop keywords
            if lower.contains("event")
                || lower.contains("message")
                || lower.contains("signal")
                || lower.contains("dispatch")
                || lower.contains("handler")
                || lower.contains("callback")
                || lower.contains("queue")
            {
                return true;
            }
            // Urbit-specific event loop patterns
            if lower.contains("u3_pier")
                || lower.contains("_pier_work")
                || lower.contains("_king_")
                || lower.contains("_lord_")
                || lower.contains("_serf_")
            {
                return true;
            }
        }

        false
    }

    /// Find functions called in the loop body
    fn find_called_functions(lines: &[&str], loop_start: usize, indent: usize) -> Vec<String> {
        let call_re = Regex::new(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(").unwrap();
        let mut calls = Vec::new();

        for line in lines.iter().skip(loop_start + 1) {
            let current_indent = line.chars().take_while(|c| c.is_whitespace()).count();

            if !line.trim().is_empty() && current_indent <= indent {
                break;
            }

            for cap in call_re.captures_iter(line) {
                if let Some(m) = cap.get(1) {
                    let name = m.as_str();
                    if !["if", "while", "for", "print", "len"].contains(&name) {
                        calls.push(name.to_string());
                    }
                }
            }
        }

        calls
    }

    /// Check if any called function contains break/return/raise
    fn calls_exit_function(calls: &[String], graph: &dyn crate::graph::GraphQuery) -> Vec<String> {
        let mut exit_funcs = Vec::new();

        for call in calls {
            if let Some(func) = graph.get_functions().into_iter().find(|f| f.name == *call) {
                if let Ok(content) = std::fs::read_to_string(&func.file_path) {
                    let lines: Vec<&str> = content.lines().collect();
                    let start = func.line_start.saturating_sub(1) as usize;
                    let end = (func.line_end as usize).min(lines.len());

                    for line in lines.get(start..end).unwrap_or(&[]) {
                        if line.contains("raise")
                            || line.contains("return")
                            || line.contains("exit")
                            || line.contains("sys.exit")
                        {
                            exit_funcs.push(call.clone());
                            break;
                        }
                    }
                }
            }
        }

        exit_funcs
    }
}

impl Default for InfiniteLoopDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for InfiniteLoopDetector {
    fn name(&self) -> &'static str {
        "InfiniteLoopDetector"
    }

    fn description(&self) -> &'static str {
        "Detects potential infinite loops (while True without break)"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
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

            // Skip test files
            if path_str.contains("test") {
                continue;
            }

            // Skip detector files (contain analysis loops, not infinite loops)
            if path_str.contains("/detectors/") {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(
                ext,
                "py" | "js" | "ts" | "java" | "go" | "rs" | "rb" | "c" | "cpp"
            ) {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();

                for (i, line) in lines.iter().enumerate() {
                    if infinite_while().is_match(line) {
                        let indent = line.chars().take_while(|c| c.is_whitespace()).count();

                        // Check for direct break/return in body
                        let has_direct_exit = Self::has_exit_in_body(&lines, i, indent);

                        // Check if intentional (server, event loop)
                        let is_intentional = Self::is_intentional_loop(&lines, i, &path_str);

                        if is_intentional {
                            continue;
                        }

                        // Find called functions and check if they exit
                        let calls = Self::find_called_functions(&lines, i, indent);
                        let exit_funcs = Self::calls_exit_function(&calls, graph);

                        let has_exit = has_direct_exit || !exit_funcs.is_empty();

                        if has_exit {
                            continue;
                        } // Has an exit path, probably fine

                        // Build context
                        let mut notes = Vec::new();
                        if !calls.is_empty() {
                            let call_list: Vec<_> = calls.iter().take(5).cloned().collect();
                            notes.push(format!("📞 Calls: {}", call_list.join(", ")));
                        }
                        notes.push("⚠️ No break/return found in loop body".to_string());

                        let context_notes = format!("\n\n**Analysis:**\n{}", notes.join("\n"));

                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "InfiniteLoopDetector".to_string(),
                            severity: Severity::High,
                            title: "Potential infinite loop".to_string(),
                            description: format!(
                                "Loop with no apparent exit condition detected.{}",
                                context_notes
                            ),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some(
                                "Options:\n\
                                 1. Add a break condition\n\
                                 2. Add a return statement\n\
                                 3. If intentional, add a comment: # Intentional infinite loop\n\n\
                                 Example:\n\
                                 ```python\n\
                                 while True:\n\
                                     data = get_data()\n\
                                     if data is None:\n\
                                         break  # Exit condition\n\
                                     process(data)\n\
                                 ```"
                                .to_string(),
                            ),
                            estimated_effort: Some("10 minutes".to_string()),
                            category: Some("bug-risk".to_string()),
                            cwe_id: Some("CWE-835".to_string()),
                            why_it_matters: Some(
                                "Infinite loops without exit conditions will hang the program \
                                 and consume 100% CPU. Even intentional infinite loops (servers) \
                                 should have shutdown mechanisms."
                                    .to_string(),
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!(
            "InfiniteLoopDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
