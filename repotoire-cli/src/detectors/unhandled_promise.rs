//! Unhandled Promise Rejection Detector
//!
//! Graph-enhanced detection of unhandled promises:
//! - Trace promise chains across function boundaries
//! - Check if async functions have try/catch at call site
//! - Higher severity for promises in critical paths

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

static PROMISE_PATTERN: OnceLock<Regex> = OnceLock::new();
static ASYNC_FUNC: OnceLock<Regex> = OnceLock::new();

fn promise_pattern() -> &'static Regex {
    PROMISE_PATTERN
        .get_or_init(|| Regex::new(r"(new Promise|\.then\(|fetch\(|axios\.|\.json\(\))").unwrap())
}

fn async_func() -> &'static Regex {
    ASYNC_FUNC.get_or_init(|| Regex::new(r"async\s+(function\s+)?(\w+)").unwrap())
}

pub struct UnhandledPromiseDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl UnhandledPromiseDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Find all async functions in the codebase
    fn find_async_functions(&self) -> HashSet<String> {
        let mut async_funcs = HashSet::new();
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "js" | "ts" | "jsx" | "tsx") {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for cap in async_func().captures_iter(&content) {
                    if let Some(name) = cap.get(2) {
                        async_funcs.insert(name.as_str().to_string());
                    }
                }
            }
        }

        async_funcs
    }

    /// Check if the promise is in a critical path (auth, payment, etc.)
    fn is_critical_context(line: &str, surrounding: &str) -> bool {
        let combined = format!("{} {}", line, surrounding).to_lowercase();
        combined.contains("auth")
            || combined.contains("login")
            || combined.contains("payment")
            || combined.contains("order")
            || combined.contains("user")
            || combined.contains("session")
            || combined.contains("token")
            || combined.contains("credential")
    }

    /// Find containing function
    fn find_containing_function(
        graph: &GraphStore,
        file_path: &str,
        line: u32,
    ) -> Option<(String, usize)> {
        graph
            .get_functions()
            .into_iter()
            .find(|f| f.file_path == file_path && f.line_start <= line && f.line_end >= line)
            .map(|f| {
                let callers = graph.get_callers(&f.qualified_name).len();
                (f.name, callers)
            })
    }
}

impl Detector for UnhandledPromiseDetector {
    fn name(&self) -> &'static str {
        "unhandled-promise"
    }
    fn description(&self) -> &'static str {
        "Detects promises without error handling"
    }

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let async_funcs = self.find_async_functions();

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
            if path_str.contains("test") || path_str.contains("spec") {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "js" | "ts" | "jsx" | "tsx") {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();

                for (i, line) in lines.iter().enumerate() {
                    // Skip comments
                    let trimmed = line.trim();
                    if trimmed.starts_with("//") || trimmed.starts_with("*") {
                        continue;
                    }

                    let has_promise = promise_pattern().is_match(line);

                    // Also check calls to known async functions without await
                    let calls_async = async_funcs
                        .iter()
                        .any(|f| line.contains(&format!("{}(", f)) && !line.contains("await"));

                    if has_promise || calls_async {
                        // Check surrounding context for error handling
                        let start = i.saturating_sub(3);
                        let end = (i + 10).min(lines.len());
                        let context = lines[start..end].join(" ");

                        let has_catch = context.contains(".catch")
                            || context.contains("catch (")
                            || context.contains("catch(");
                        let in_try = lines[start..i]
                            .iter()
                            .any(|l| l.contains("try {") || l.contains("try{"));
                        let has_finally = context.contains(".finally");

                        if has_catch || in_try {
                            continue;
                        }

                        // Analyze context
                        let is_critical = Self::is_critical_context(line, &context);
                        let containing_func =
                            Self::find_containing_function(graph, &path_str, (i + 1) as u32);

                        // Calculate severity
                        let severity = if is_critical {
                            Severity::High // Critical path without error handling
                        } else if calls_async {
                            Severity::Medium // Calling known async without handling
                        } else {
                            Severity::Medium
                        };

                        // Build notes
                        let mut notes = Vec::new();
                        if is_critical {
                            notes.push("⚠️ In critical path (auth/payment/user)".to_string());
                        }
                        if calls_async {
                            notes.push(
                                "🔍 Calls async function without await or .catch".to_string(),
                            );
                        }
                        if let Some((func_name, callers)) = containing_func {
                            notes.push(format!(
                                "📦 In function: `{}` ({} callers)",
                                func_name, callers
                            ));
                        }
                        if has_finally {
                            notes.push("✓ Has .finally() but no .catch()".to_string());
                        }

                        let context_notes = if notes.is_empty() {
                            String::new()
                        } else {
                            format!("\n\n**Analysis:**\n{}", notes.join("\n"))
                        };

                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "UnhandledPromiseDetector".to_string(),
                            severity,
                            title: if calls_async {
                                "Async function called without error handling".to_string()
                            } else {
                                "Promise without .catch()".to_string()
                            },
                            description: format!(
                                "Promise rejection may go unhandled, causing silent failures or crashes.{}",
                                context_notes
                            ),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some(
                                "Options:\n\n\
                                 **1. Add .catch():**\n\
                                 ```javascript\n\
                                 fetchData()\n\
                                   .then(data => process(data))\n\
                                   .catch(err => console.error('Failed:', err));\n\
                                 ```\n\n\
                                 **2. Use try/catch with await:**\n\
                                 ```javascript\n\
                                 try {\n\
                                   const data = await fetchData();\n\
                                   process(data);\n\
                                 } catch (err) {\n\
                                   console.error('Failed:', err);\n\
                                 }\n\
                                 ```".to_string()
                            ),
                            estimated_effort: Some("5 minutes".to_string()),
                            category: Some("error-handling".to_string()),
                            cwe_id: Some("CWE-755".to_string()),
                            why_it_matters: Some(
                                "Unhandled promise rejections can crash Node.js (--unhandled-rejections=strict) \
                                 or cause silent failures that are hard to debug.".to_string()
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!(
            "UnhandledPromiseDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
