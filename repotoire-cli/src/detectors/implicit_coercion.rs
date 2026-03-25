//! Implicit Coercion Detector (JavaScript)
//!
//! Graph-enhanced detection of == instead of ===.
//! Uses graph to:
//! - Prioritize issues in heavily-called functions
//! - Reduce severity for dead code
//! - Identify route handlers (higher risk)

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphQueryExt;
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::LazyLock;
use tracing::info;

static LOOSE_EQUALITY: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^!=<>]==[^=]|[^!]==[^=]").expect("valid regex"));

pub struct ImplicitCoercionDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
}

impl ImplicitCoercionDetector {
    crate::detectors::detector_new!(100);

    /// Find containing function and get its context
    fn find_function_context(
        graph: &dyn crate::graph::GraphQuery,
        file_path: &str,
        line: u32,
    ) -> Option<(String, usize, bool)> {
        let i = graph.interner();
        graph
            .find_function_at(file_path, line)
            .map(|f| {
                let callers = graph.get_callers(f.qn(i));
                let caller_count = callers.len();

                // Check if this is a route handler
                let name_lower = f.node_name(i).to_lowercase();
                let is_handler = name_lower.contains("handler")
                    || name_lower.contains("route")
                    || name_lower.contains("controller")
                    || name_lower.starts_with("get")
                    || name_lower.starts_with("post")
                    || name_lower.starts_with("put")
                    || name_lower.starts_with("delete")
                    || name_lower.starts_with("handle");

                (f.node_name(i).to_string(), caller_count, is_handler)
            })
    }

    /// Check if function is dead code (no callers, not an entry point)
    fn is_dead_code(graph: &dyn crate::graph::GraphQuery, file_path: &str, line: u32) -> bool {
        let i = graph.interner();
        if let Some(func) = graph.find_function_at(file_path, line) {
            let callers = graph.get_callers(func.qn(i));
            let name_lower = func.node_name(i).to_lowercase();
            let is_entry = name_lower == "main"
                || name_lower.starts_with("test")
                || name_lower.contains("handler")
                || name_lower.contains("route")
                || func.get_bool("is_exported").unwrap_or(false);
            callers.is_empty() && !is_entry
        } else {
            false
        }
    }
}

impl Detector for ImplicitCoercionDetector {
    fn name(&self) -> &'static str {
        "implicit-coercion"
    }
    fn description(&self) -> &'static str {
        "Detects == instead of ==="
    }

    fn file_extensions(&self) -> &'static [&'static str] {
        &["js", "ts", "jsx", "tsx"]
    }

    fn detect(&self, ctx: &crate::detectors::analysis_context::AnalysisContext) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let files = &ctx.as_file_provider();
        let mut findings = vec![];

        for path in files.files_with_extensions(&["js", "ts", "jsx", "tsx"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            let path_str = path.to_string_lossy().to_string();

            if let Some(content) = files.content(path) {
                let lines: Vec<&str> = content.lines().collect();
                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    let trimmed = line.trim();
                    if trimmed.starts_with("//") {
                        continue;
                    }

                    // Check for == but not === or !==
                    if LOOSE_EQUALITY.is_match(line)
                        && !line.contains("===")
                        && !line.contains("!==")
                    {
                        // Skip null checks which are sometimes intentional
                        if line.contains("== null") || line.contains("null ==") {
                            continue;
                        }
                        // Skip undefined checks
                        if line.contains("== undefined") || line.contains("undefined ==") {
                            continue;
                        }

                        let line_num = (i + 1) as u32;

                        // Graph-enhanced analysis
                        let func_context = Self::find_function_context(graph, &path_str, line_num);
                        let is_dead = Self::is_dead_code(graph, &path_str, line_num);

                        // Calculate severity with graph context
                        let mut severity = Severity::Low;

                        // Reduce severity for dead code
                        if is_dead {
                            severity = Severity::Low;
                        } else if let Some((_, callers, is_handler)) = &func_context {
                            // Boost for route handlers (user input)
                            if *is_handler || *callers >= 10 {
                                severity = Severity::Medium;
                            }
                        }

                        // Build notes
                        let mut notes = Vec::new();
                        if let Some((func_name, callers, is_handler)) = &func_context {
                            notes.push(format!(
                                "📦 In function: `{}` ({} callers)",
                                func_name, callers
                            ));
                            if *is_handler {
                                notes.push("🌐 Route handler (processes user input)".to_string());
                            }
                        }
                        if is_dead {
                            notes.push("💀 In unused code".to_string());
                        }

                        let context_notes = if notes.is_empty() {
                            String::new()
                        } else {
                            format!("\n\n**Analysis:**\n{}", notes.join("\n"))
                        };

                        findings.push(Finding {
                            id: String::new(),
                            detector: "ImplicitCoercionDetector".to_string(),
                            severity,
                            title: "Loose equality (==) used".to_string(),
                            description: format!(
                                "== performs type coercion which can cause subtle bugs.{}",
                                context_notes
                            ),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(
                                "Use === for strict equality:\n\
                                 ```javascript\n\
                                 // Instead of:\n\
                                 if (value == 'string') { ... }\n\
                                 \n\
                                 // Use:\n\
                                 if (value === 'string') { ... }\n\
                                 ```"
                                .to_string(),
                            ),
                            estimated_effort: Some("2 minutes".to_string()),
                            category: Some("code-quality".to_string()),
                            cwe_id: None,
                            why_it_matters: Some(
                                "Type coercion in == can cause unexpected behavior:\n\
                                 • '1' == 1 is true\n\
                                 • '' == false is true\n\
                                 • [] == false is true\n\
                                 Use === to compare both value AND type."
                                    .to_string(),
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!(
            "ImplicitCoercionDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}


impl super::RegisteredDetector for ImplicitCoercionDetector {
    fn create(init: &super::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::new(init.repo_path))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_loose_equality() {
        let store = GraphStore::in_memory();
        let detector = ImplicitCoercionDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("check.js", "function check(value) {\n    if (value == 'hello') {\n        return true;\n    }\n}\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect == instead of ==="
        );
        assert!(
            findings[0].title.contains("Loose equality"),
            "Title should mention loose equality, got: {}",
            findings[0].title
        );
    }

    #[test]
    fn test_no_finding_for_strict_equality() {
        let store = GraphStore::in_memory();
        let detector = ImplicitCoercionDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("check.js", "function check(value) {\n    if (value === 'hello') {\n        return true;\n    }\n}\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag strict equality ===, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
