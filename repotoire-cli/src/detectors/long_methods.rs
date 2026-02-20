//! Long Methods Detector
//!
//! Graph-enhanced detection of overly long methods/functions.
//! Uses graph to:
//! - Check if function is an orchestrator (high out-degree - acceptable)
//! - Calculate complexity/lines ratio (high complexity in long func = worse)
//! - Identify natural split points based on callee clusters
//! - Check if function has many distinct responsibilities

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::PathBuf;
use tracing::info;

pub struct LongMethodsDetector {
    repository_path: PathBuf,
    config: DetectorConfig,
    max_findings: usize,
    threshold: u32,
}

impl LongMethodsDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            config: DetectorConfig::new(),
            max_findings: 100,
            threshold: 50,
        }
    }

    /// Create with custom config (reads max_lines threshold from project config,
    /// falling back to adaptive calibration, then hardcoded default)
    pub fn with_config(repository_path: impl Into<PathBuf>, config: DetectorConfig) -> Self {
        use crate::calibrate::MetricKind;
        let default_threshold = 50usize;
        let adaptive_threshold =
            config.adaptive.warn_usize(MetricKind::FunctionLength, default_threshold);
        let threshold = config.get_option_or("max_lines", adaptive_threshold) as u32;
        Self {
            repository_path: repository_path.into(),
            max_findings: 100,
            threshold,
            config,
        }
    }

    /// Check if function is an orchestrator (high out-degree, low complexity per callee)
    fn is_orchestrator(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        qualified_name: &str,
        lines: u32,
        complexity: i64,
    ) -> bool {
        let callees = graph.get_callees(qualified_name);
        let out_degree = callees.len();

        // Orchestrators: many callees, low complexity relative to size
        // They mostly coordinate/dispatch, not implement logic
        if out_degree >= 10 {
            let complexity_per_line = complexity as f64 / lines as f64;
            // Low complexity per line = mostly calling other functions
            complexity_per_line < 0.2
        } else {
            false
        }
    }

    /// Find distinct callee clusters (suggests natural split points)
    fn find_callee_clusters(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        qualified_name: &str,
    ) -> Vec<String> {
        let callees = graph.get_callees(qualified_name);

        // Group callees by their module/file
        let mut modules: HashSet<String> = HashSet::new();
        for callee in &callees {
            if let Some(module) = callee.file_path.rsplit('/').nth(1) {
                modules.insert(module.to_string());
            }
        }

        // If calling many different modules, each could be a separate function
        modules.into_iter().take(5).collect()
    }

    /// Calculate complexity density (complexity / lines)
    fn complexity_density(complexity: i64, lines: u32) -> f64 {
        if lines == 0 {
            return 0.0;
        }
        complexity as f64 / lines as f64
    }
}

impl Detector for LongMethodsDetector {
    fn name(&self) -> &'static str {
        "long-methods"
    }
    fn description(&self) -> &'static str {
        "Detects methods/functions over 50 lines"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        for func in graph.get_functions() {
            if findings.len() >= self.max_findings {
                break;
            }

            // Skip detector files (they have inherently complex parsing logic)
            if func.file_path.contains("/detectors/") {
                continue;
            }

            let lines = func.line_end.saturating_sub(func.line_start);
            if lines <= self.threshold {
                continue;
            }

            // Get complexity for analysis
            let complexity = func.complexity().unwrap_or(1);

            // === Graph-aware analysis ===
            let is_orchestrator =
                self.is_orchestrator(graph, &func.qualified_name, lines, complexity);
            let callee_clusters = self.find_callee_clusters(graph, &func.qualified_name);
            let density = Self::complexity_density(complexity, lines);
            let callees = graph.get_callees(&func.qualified_name);
            let out_degree = callees.len();

            // Calculate severity with graph awareness
            let mut severity = if lines > 200 {
                Severity::High
            } else if lines > 100 {
                Severity::Medium
            } else {
                Severity::Low
            };

            // Orchestrators get reduced severity (they're supposed to coordinate)
            if is_orchestrator {
                severity = match severity {
                    Severity::High => Severity::Medium,
                    _ => Severity::Low,
                };
            }

            // High complexity density = worse (lots of logic, not just coordination)
            if density > 0.5 && lines > 100 {
                severity = match severity {
                    Severity::Low => Severity::Medium,
                    Severity::Medium => Severity::High,
                    _ => severity,
                };
            }

            // Build analysis notes
            let mut notes = Vec::new();

            if is_orchestrator {
                notes.push(format!(
                    "📤 Orchestrator pattern: calls {} functions (reduced severity)",
                    out_degree
                ));
            }

            if density > 0.3 {
                notes.push(format!(
                    "⚠️ High complexity density: {:.2} (complexity {} / {} lines)",
                    density, complexity, lines
                ));
            }

            if callee_clusters.len() >= 3 {
                notes.push(format!(
                    "🔀 Calls {} different modules - possible split points",
                    callee_clusters.len()
                ));
            }

            let context_notes = if notes.is_empty() {
                String::new()
            } else {
                format!("\n\n**Graph Analysis:**\n{}", notes.join("\n"))
            };

            // Build smart suggestion based on analysis
            let suggestion = if is_orchestrator {
                "This appears to be an orchestrator function (coordinates many calls).\n\
                 If it must remain long, ensure it:\n\
                 1. Has clear section comments\n\
                 2. Handles errors at each step\n\
                 3. Has a clear flow (consider a state machine for complex flows)"
                    .to_string()
            } else if callee_clusters.len() >= 3 {
                format!(
                    "This function calls {} different modules. Consider extracting:\n{}",
                    callee_clusters.len(),
                    callee_clusters
                        .iter()
                        .take(3)
                        .map(|m| format!("  - `handle_{}()` for {} operations", m, m))
                        .collect::<Vec<_>>()
                        .join("\n")
                )
            } else if density > 0.4 {
                "High complexity density - this function does too much logic.\n\
                 1. Extract conditional branches into helper functions\n\
                 2. Use early returns to reduce nesting\n\
                 3. Consider the Strategy pattern for varying behaviors"
                    .to_string()
            } else {
                "Break into smaller, focused functions.".to_string()
            };

            findings.push(Finding {
                id: String::new(),
                detector: "LongMethodsDetector".to_string(),
                severity,
                title: format!("Long method: {} ({} lines)", func.name, lines),
                description: format!(
                    "Function '{}' has {} lines (threshold: {}).{}",
                    func.name, lines, self.threshold, context_notes
                ),
                affected_files: vec![PathBuf::from(&func.file_path)],
                line_start: Some(func.line_start),
                line_end: Some(func.line_end),
                suggested_fix: Some(suggestion),
                estimated_effort: Some(if lines > 200 {
                    "1-2 hours".to_string()
                } else {
                    "30 minutes".to_string()
                }),
                category: Some("maintainability".to_string()),
                cwe_id: None,
                why_it_matters: Some(
                    "Long methods are hard to understand, test, and maintain. \
                     Each function should do one thing well."
                        .to_string(),
                ),
                ..Default::default()
            });
        }

        info!(
            "LongMethodsDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
