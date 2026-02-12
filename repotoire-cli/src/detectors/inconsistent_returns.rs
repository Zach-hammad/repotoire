//! Inconsistent Returns Detector
//!
//! Graph-enhanced detection of functions with inconsistent return paths:
//! - Uses graph to check if callers use the return value (increases severity)
//! - Identifies functions that are awaited/assigned vs. called standalone
//! - Checks for None/null vs value return mismatches

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use tracing::info;
use uuid::Uuid;

pub struct InconsistentReturnsDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl InconsistentReturnsDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Check if any caller uses the return value
    fn return_value_is_used(graph: &GraphStore, func: &crate::graph::CodeNode) -> (bool, usize) {
        let callers = graph.get_callers(&func.qualified_name);
        let mut callers_using_value = 0;

        for caller in &callers {
            // Check caller's code for patterns that use return value
            if let Ok(content) = std::fs::read_to_string(&caller.file_path) {
                let lines: Vec<&str> = content.lines().collect();
                let start = caller.line_start.saturating_sub(1) as usize;
                let end = (caller.line_end as usize).min(lines.len());

                for line in lines.get(start..end).unwrap_or(&[]) {
                    // Look for patterns like: x = func(), if func(), return func()
                    if line.contains(&func.name) {
                        if line.contains("=") && line.contains(&format!("{}(", func.name)) {
                            callers_using_value += 1;
                            break;
                        }
                        if line.trim().starts_with("return")
                            && line.contains(&format!("{}(", func.name))
                        {
                            callers_using_value += 1;
                            break;
                        }
                        if line.contains("if") && line.contains(&format!("{}(", func.name)) {
                            callers_using_value += 1;
                            break;
                        }
                        if line.contains("await") && line.contains(&format!("{}(", func.name)) {
                            callers_using_value += 1;
                            break;
                        }
                    }
                }
            }
        }

        (callers_using_value > 0, callers.len())
    }

    /// Analyze return patterns in function
    fn analyze_returns(func_text: &str) -> ReturnAnalysis {
        let mut has_return_value = false;
        let mut has_return_none = false;
        let mut has_implicit_return = true;
        let mut return_count = 0;

        for line in func_text.lines() {
            let trimmed = line.trim();

            // Skip comments
            if trimmed.starts_with("//") || trimmed.starts_with("#") {
                continue;
            }

            if trimmed.starts_with("return") || trimmed.contains(" return ") {
                return_count += 1;
                has_implicit_return = false;

                // Check what kind of return
                if trimmed == "return"
                    || trimmed == "return;"
                    || trimmed.starts_with("return;")
                    || trimmed.starts_with("return\n")
                {
                    has_return_none = true;
                } else if trimmed.contains("return None")
                    || trimmed.contains("return null")
                    || trimmed.contains("return undefined")
                {
                    has_return_none = true;
                } else if trimmed.starts_with("return ") {
                    has_return_value = true;
                }
            }
        }

        ReturnAnalysis {
            has_return_value,
            has_return_none,
            has_implicit_return,
            return_count,
        }
    }
}

struct ReturnAnalysis {
    has_return_value: bool,
    has_return_none: bool,
    has_implicit_return: bool,
    return_count: usize,
}

impl Detector for InconsistentReturnsDetector {
    fn name(&self) -> &'static str {
        "inconsistent-returns"
    }
    fn description(&self) -> &'static str {
        "Detects functions with inconsistent return paths"
    }

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        for func in graph.get_functions() {
            if findings.len() >= self.max_findings {
                break;
            }

            // Skip test functions
            if func.name.starts_with("test_") || func.file_path.contains("/test") {
                continue;
            }

            // Skip very small functions
            let func_size = func.line_end.saturating_sub(func.line_start);
            if func_size < 3 {
                continue;
            }

            if let Ok(content) = std::fs::read_to_string(&func.file_path) {
                let start = func.line_start.saturating_sub(1) as usize;
                let end = func.line_end as usize;
                let func_lines: Vec<&str> = content.lines().skip(start).take(end - start).collect();
                let func_text = func_lines.join("\n");

                let analysis = Self::analyze_returns(&func_text);

                // Check for inconsistent return patterns
                let is_inconsistent = analysis.has_return_value
                    && (analysis.has_return_none
                        || (analysis.has_implicit_return && analysis.return_count > 0));

                if is_inconsistent {
                    // Check if callers use the return value
                    let (value_is_used, caller_count) = Self::return_value_is_used(graph, &func);

                    // Calculate severity
                    let severity = if value_is_used {
                        Severity::High // Callers expect a value!
                    } else if caller_count > 3 {
                        Severity::Medium // Many callers, potential bug
                    } else {
                        Severity::Medium
                    };

                    // Build context notes
                    let mut notes = Vec::new();
                    if analysis.return_count > 0 {
                        notes.push(format!(
                            "📊 {} return statements found",
                            analysis.return_count
                        ));
                    }
                    if analysis.has_return_value {
                        notes.push("✓ Some paths return a value".to_string());
                    }
                    if analysis.has_return_none {
                        notes.push("✗ Some paths return None/null".to_string());
                    }
                    if analysis.has_implicit_return {
                        notes.push("✗ Some paths have no return (implicit None)".to_string());
                    }
                    if caller_count > 0 {
                        if value_is_used {
                            notes.push(format!(
                                "⚠️ Called by {} functions - some USE the return value!",
                                caller_count
                            ));
                        } else {
                            notes.push(format!("📞 Called by {} functions", caller_count));
                        }
                    }

                    let context_notes = format!("\n\n**Analysis:**\n{}", notes.join("\n"));

                    let suggestion = if value_is_used {
                        "**CRITICAL**: Callers expect a return value! Options:\n\
                         1. Return a default value on all paths\n\
                         2. Raise an exception for error cases\n\
                         3. Return an Optional type and have callers handle None"
                            .to_string()
                    } else {
                        "Ensure all paths return consistently:\n\
                         1. Add explicit return None where implicit\n\
                         2. Return a value on all paths\n\
                         3. Use Optional type hint to document behavior"
                            .to_string()
                    };

                    findings.push(Finding {
                        id: Uuid::new_v4().to_string(),
                        detector: "InconsistentReturnsDetector".to_string(),
                        severity,
                        title: format!("Inconsistent returns in '{}'", func.name),
                        description: format!(
                            "Function has mixed return behavior - some paths return values, others don't.{}",
                            context_notes
                        ),
                        affected_files: vec![PathBuf::from(&func.file_path)],
                        line_start: Some(func.line_start),
                        line_end: Some(func.line_end),
                        suggested_fix: Some(suggestion),
                        estimated_effort: Some("15 minutes".to_string()),
                        category: Some("bug-risk".to_string()),
                        cwe_id: Some("CWE-394".to_string()),
                        why_it_matters: Some(
                            "Inconsistent returns can cause unexpected None/undefined values, \
                             leading to TypeErrors or NullPointerExceptions in callers.".to_string()
                        ),
                        ..Default::default()
                    });
                }
            }
        }

        info!(
            "InconsistentReturnsDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
