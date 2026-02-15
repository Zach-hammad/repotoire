//! Debug Code Detector
//!
//! Graph-enhanced detection of debug statements left in code.
//! Uses graph to:
//! - Check if function is a logging utility (acceptable)
//! - Count debug statements per function (suggests forgotten cleanup)
//! - Check if it's in a development-only module

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;
use uuid::Uuid;

static DEBUG_PATTERN: OnceLock<Regex> = OnceLock::new();

fn debug_pattern() -> &'static Regex {
    DEBUG_PATTERN.get_or_init(|| Regex::new(r"(?i)(console\.(log|debug|info|warn)|print\(|debugger;?|debug\s*=\s*True|DEBUG\s*=\s*true|binding\.pry|byebug|import\s+pdb|pdb\.set_trace)").unwrap())
}

pub struct DebugCodeDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl DebugCodeDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 100,
        }
    }

    /// Check if function is a logging/debug utility (acceptable)
    fn is_logging_utility(func_name: &str) -> bool {
        let logging_patterns = [
            "log", "debug", "trace", "print", "dump", "inspect", "show", "display",
        ];
        let name_lower = func_name.to_lowercase();
        logging_patterns.iter().any(|p| name_lower.contains(p))
    }

    /// Check if path is a development-only module
    fn is_dev_only_path(path: &str) -> bool {
        let dev_patterns = [
            "/dev/",
            "/debug/",
            "/utils/debug",
            "/helpers/debug",
            "debug_",
            "_debug.",
            "/logging/",
        ];
        dev_patterns.iter().any(|p| path.contains(p))
    }

    /// Find containing function
    fn find_containing_function(graph: &GraphStore, file_path: &str, line: u32) -> Option<String> {
        graph
            .get_functions()
            .into_iter()
            .find(|f| f.file_path == file_path && f.line_start <= line && f.line_end >= line)
            .map(|f| f.name)
    }
}

impl Detector for DebugCodeDetector {
    fn name(&self) -> &'static str {
        "debug-code"
    }
    fn description(&self) -> &'static str {
        "Detects debug statements left in code"
    }

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let mut debug_per_file: HashMap<String, usize> = HashMap::new();
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

            // Skip dev-only modules (acceptable to have debug code)
            if Self::is_dev_only_path(&path_str) {
                continue;
            }
            
            // Skip non-production paths (examples, docs, scripts)
            if crate::detectors::content_classifier::is_non_production_path(&path_str) {
                continue;
            }
            
            // Skip example files
            if path_str.contains("/examples/")
                || path_str.contains("/example/")
                || path_str.contains("/docs/")
                || path_str.contains("/documentation/")
            {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py" | "js" | "ts" | "jsx" | "tsx" | "rb" | "java") {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let mut file_debug_count = 0;

                for (i, line) in content.lines().enumerate() {
                    let trimmed = line.trim();
                    if trimmed.starts_with("//") || trimmed.starts_with("#") {
                        continue;
                    }

                    if debug_pattern().is_match(line) {
                        let line_num = (i + 1) as u32;
                        let containing_func =
                            Self::find_containing_function(graph, &path_str, line_num);

                        // Skip if in a logging utility function
                        if let Some(ref func) = containing_func {
                            if Self::is_logging_utility(func) {
                                continue;
                            }
                        }

                        file_debug_count += 1;

                        // Calculate severity
                        let severity = if line.contains("pdb")
                            || line.contains("debugger")
                            || line.contains("binding.pry")
                        {
                            Severity::High // Interactive debuggers are definitely leftover
                        } else if file_debug_count > 5 {
                            Severity::Medium // Many debug statements suggests forgotten cleanup
                        } else {
                            Severity::Low
                        };

                        let mut notes = Vec::new();
                        if let Some(func) = &containing_func {
                            notes.push(format!("📦 In function: `{}`", func));
                        }
                        if file_debug_count > 1 {
                            notes.push(format!(
                                "📊 {} debug statements in this file so far",
                                file_debug_count
                            ));
                        }

                        let context_notes = if notes.is_empty() {
                            String::new()
                        } else {
                            format!("\n\n**Analysis:**\n{}", notes.join("\n"))
                        };

                        let suggestion = if line.contains("print") {
                            "Replace with proper logging:\n\
                             ```python\n\
                             import logging\n\
                             logger = logging.getLogger(__name__)\n\
                             logger.debug('message')  # Only shows in debug mode\n\
                             ```"
                            .to_string()
                        } else if line.contains("console.log") {
                            "Remove or replace with a logging library that can be disabled:\n\
                             ```javascript\n\
                             import debug from 'debug';\n\
                             const log = debug('app:module');\n\
                             log('message');  // Only shows when DEBUG=app:*\n\
                             ```"
                            .to_string()
                        } else {
                            "Remove debug code or replace with proper logging.".to_string()
                        };

                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "DebugCodeDetector".to_string(),
                            severity,
                            title: if line.contains("debugger") || line.contains("pdb") {
                                "Interactive debugger left in code".to_string()
                            } else {
                                "Debug code left in".to_string()
                            },
                            description: format!(
                                "Debug statements should be removed before production.{}",
                                context_notes
                            ),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(suggestion),
                            estimated_effort: Some("5 minutes".to_string()),
                            category: Some("code-quality".to_string()),
                            cwe_id: Some("CWE-489".to_string()),
                            why_it_matters: Some(
                                "Debug code can leak sensitive information, clutter logs, \
                                 and interactive debuggers will hang the application."
                                    .to_string(),
                            ),
                            ..Default::default()
                        });
                    }
                }

                if file_debug_count > 0 {
                    debug_per_file.insert(path_str, file_debug_count);
                }
            }
        }

        info!(
            "DebugCodeDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
