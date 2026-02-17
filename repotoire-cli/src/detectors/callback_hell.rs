//! Callback Hell Detector
//!
//! Graph-enhanced detection of deeply nested callbacks.
//! Uses graph to:
//! - Find async functions in the file that could be used
//! - Check if there are Promise-based alternatives available
//! - Identify natural extraction points for nested callbacks

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::PathBuf;
use tracing::info;
use uuid::Uuid;

pub struct CallbackHellDetector {
    repository_path: PathBuf,
    max_findings: usize,
    max_nesting: usize,
}

impl CallbackHellDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
            max_nesting: 3,
        }
    }

    /// Find async functions in the codebase that could be used instead
    fn find_async_alternatives(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        file_path: &str,
    ) -> Vec<String> {
        graph
            .get_functions()
            .into_iter()
            .filter(|f| {
                // Same file or imported module
                f.file_path == file_path
                    || f.file_path.rsplit('/').nth(1) == file_path.rsplit('/').nth(1)
            })
            .filter(|f| {
                // Look for async functions or promise-returning functions
                f.name.starts_with("async") || f.name.contains("Async") || f.name.ends_with("Async")
            })
            .map(|f| f.name)
            .take(5)
            .collect()
    }

    /// Check if file already uses async/await
    fn uses_async_await(content: &str) -> bool {
        content.contains("async ") && content.contains("await ")
    }

    /// Check if file uses Promise.all (good pattern)
    fn uses_promise_combinators(content: &str) -> bool {
        content.contains("Promise.all")
            || content.contains("Promise.race")
            || content.contains("Promise.allSettled")
    }
}

impl Detector for CallbackHellDetector {
    fn name(&self) -> &'static str {
        "callback-hell"
    }
    fn description(&self) -> &'static str {
        "Detects deeply nested callbacks"
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

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "js" | "ts" | "jsx" | "tsx") {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let mut callback_depth = 0;
                let mut max_depth = 0;
                let mut max_line = 0;
                let mut then_count = 0;
                let mut anonymous_count = 0;

                for (i, line) in content.lines().enumerate() {
                    // Count callback indicators
                    let anon_funcs = line.matches("function(").count();
                    let arrows = line.matches("=> {").count();
                    let thens = line.matches(".then(").count();

                    anonymous_count += anon_funcs + arrows;
                    then_count += thens;
                    callback_depth += anon_funcs + arrows + thens;

                    // Track closings
                    if line.contains("});") || line.contains("})") {
                        callback_depth = callback_depth.saturating_sub(1);
                    }

                    if callback_depth > max_depth {
                        max_depth = callback_depth;
                        max_line = i + 1;
                    }
                }

                if max_depth > self.max_nesting {
                    let path_str = path.to_string_lossy().to_string();

                    // === Graph-enhanced analysis ===
                    let async_alternatives = self.find_async_alternatives(graph, &path_str);
                    let already_uses_async = Self::uses_async_await(&content);
                    let uses_combinators = Self::uses_promise_combinators(&content);

                    // Calculate severity based on analysis
                    let severity = if max_depth > 5 {
                        Severity::High
                    } else if max_depth > 4 || (then_count > 5 && !already_uses_async) {
                        Severity::Medium
                    } else {
                        Severity::Low
                    };

                    // Build context notes
                    let mut notes = Vec::new();

                    if already_uses_async {
                        notes.push("✓ File already uses async/await in some places".to_string());
                    }
                    if uses_combinators {
                        notes.push("✓ Uses Promise combinators (good pattern)".to_string());
                    }
                    if then_count > 3 {
                        notes.push(format!("⚠️ {} .then() chains detected", then_count));
                    }
                    if anonymous_count > 5 {
                        notes.push(format!(
                            "⚠️ {} anonymous functions - consider naming them",
                            anonymous_count
                        ));
                    }

                    let context_notes = if notes.is_empty() {
                        String::new()
                    } else {
                        format!("\n\n**Analysis:**\n{}", notes.join("\n"))
                    };

                    // Build smart suggestion
                    let suggestion = if already_uses_async {
                        "This file already uses async/await. Convert remaining callbacks:\n\
                         1. Replace `.then()` chains with `await`\n\
                         2. Use `try/catch` instead of `.catch()`"
                            .to_string()
                    } else if !async_alternatives.is_empty() {
                        format!(
                            "Convert to async/await. Similar async functions exist:\n{}\n\n\
                             Or extract nested callbacks into named functions.",
                            async_alternatives
                                .iter()
                                .map(|n| format!("  - {}", n))
                                .collect::<Vec<_>>()
                                .join("\n")
                        )
                    } else {
                        "Refactor options:\n\
                         1. Convert to async/await (recommended)\n\
                         2. Extract nested callbacks into named functions\n\
                         3. Use Promise.all() for parallel operations"
                            .to_string()
                    };

                    findings.push(Finding {
                        id: Uuid::new_v4().to_string(),
                        detector: "CallbackHellDetector".to_string(),
                        severity,
                        title: format!("Callback hell ({} levels deep)", max_depth),
                        description: format!(
                            "Deeply nested callbacks ({} levels) make code hard to follow.{}",
                            max_depth, context_notes
                        ),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(max_line as u32),
                        line_end: Some(max_line as u32),
                        suggested_fix: Some(suggestion),
                        estimated_effort: Some(if max_depth > 5 { "1 hour".to_string() } else { "30 minutes".to_string() }),
                        category: Some("readability".to_string()),
                        cwe_id: None,
                        why_it_matters: Some(
                            "The 'pyramid of doom' hurts readability and makes error handling difficult. \
                             Each nesting level increases cognitive load.".to_string()
                        ),
                        ..Default::default()
                    });
                }
            }
        }

        info!(
            "CallbackHellDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
