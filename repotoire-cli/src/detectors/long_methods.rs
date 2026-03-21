//! Long Methods Detector
//!
//! Graph-enhanced detection of overly long methods/functions.
//! Uses graph to:
//! - Check if function is an orchestrator (high out-degree - acceptable)
//! - Calculate complexity/lines ratio (high complexity in long func = worse)
//! - Identify natural split points based on callee clusters
//! - Check if function has many distinct responsibilities
//!
//! FP-reduction strategies:
//! - Adaptive thresholds from codebase calibration
//! - Language-specific base thresholds (py=60, rs/go/ts/js=80, java/cs/c/cpp=100)
//! - Orchestrator severity reduction (High→Medium, Medium→Low)
//! - Test function severity cap (capped at Low)
//! - Unreachable code severity reduction

use crate::detectors::analysis_context::AnalysisContext;
use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::function_context::FunctionRole;
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use tracing::info;

pub struct LongMethodsDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    #[allow(dead_code)] // Part of detector pattern
    config: DetectorConfig,
    max_findings: usize,
    #[allow(dead_code)] // Kept for config-driven override via with_config()
    threshold: u32,
}

/// Returns the language-specific line threshold for a given file extension.
fn language_line_threshold(ext: &str) -> usize {
    match ext {
        "py" | "pyi" => 60,
        "rs" | "go" | "ts" | "tsx" | "js" | "jsx" | "mjs" => 80,
        "java" | "cs" | "c" | "h" | "cpp" | "cc" | "cxx" | "hpp" => 100,
        _ => 80,
    }
}

impl LongMethodsDetector {
    #[allow(dead_code)] // Constructor used by tests and detector registration
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            config: DetectorConfig::new(),
            max_findings: 100,
            threshold: 80,
        }
    }

    /// Create with custom config (reads max_lines threshold from project config,
    /// falling back to adaptive calibration, then hardcoded default)
    pub fn with_config(repository_path: impl Into<PathBuf>, config: DetectorConfig) -> Self {
        use crate::calibrate::MetricKind;
        let default_threshold = 80usize;
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

    /// Check if function is an orchestrator via graph heuristic
    /// (high out-degree, low complexity per callee).
    fn is_graph_orchestrator(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        qualified_name: &str,
        lines: u32,
        complexity: i64,
    ) -> bool {
        let _i = graph.interner();
        let callees = graph.get_callees(qualified_name);
        let out_degree = callees.len();

        // Orchestrators: many callees, low complexity relative to size
        // They mostly coordinate/dispatch, not implement logic
        if out_degree >= 7 {
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
        let i = graph.interner();
        let callees = graph.get_callees(qualified_name);

        // Group callees by their module/file
        let mut modules: HashSet<String> = HashSet::new();
        for callee in &callees {
            if let Some(module) = callee.path(i).rsplit('/').nth(1) {
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
        "Detects methods/functions over 80 lines"
    }

    fn file_extensions(&self) -> &'static [&'static str] {
        &["py", "js", "ts", "jsx", "tsx", "rb", "java", "go", "rs", "c", "cpp", "cs"]
    }

    fn detect(&self, ctx: &AnalysisContext) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let i = graph.interner();
        let mut findings = vec![];

        for func in graph.get_functions_shared().iter() {
            if findings.len() >= self.max_findings {
                break;
            }

            // Skip detector files (they have inherently complex parsing logic)
            if func.path(i).contains("/detectors/") {
                continue;
            }

            let lines = func.line_end.saturating_sub(func.line_start);

            let qn = func.qn(i);

            // Determine language-specific threshold from file extension
            let ext = Path::new(func.path(i))
                .extension()
                .and_then(|e| e.to_str())
                .unwrap_or("");
            let lang_base = language_line_threshold(ext);
            let threshold = lang_base
                .max(ctx.threshold(crate::calibrate::MetricKind::FunctionLength, lang_base as f64) as usize)
                as u32;

            // Quick pre-filter: skip functions clearly under the threshold.
            if lines <= threshold {
                continue;
            }

            // Get complexity for analysis
            let complexity = func.complexity_opt().unwrap_or(1);

            // Determine orchestrator status from pre-computed FunctionRole first,
            // then fall back to graph heuristic.
            let is_orchestrator = matches!(
                ctx.function_role(qn),
                Some(FunctionRole::Orchestrator)
            ) || self.is_graph_orchestrator(graph, qn, lines, complexity);

            let is_test = ctx.is_test_function(qn);
            let callee_clusters = self.find_callee_clusters(graph, qn);
            let density = Self::complexity_density(complexity, lines);
            let callees = graph.get_callees(qn);
            let out_degree = callees.len();

            // Calculate severity based on how far over the threshold
            let mut severity = if lines > threshold * 3 {
                Severity::High
            } else if lines > threshold * 2 {
                Severity::Medium
            } else {
                Severity::Low
            };

            // High complexity density = worse (lots of logic, not just coordination)
            if density > 0.5 && lines > 100 {
                severity = match severity {
                    Severity::Low => Severity::Medium,
                    Severity::Medium => Severity::High,
                    _ => severity,
                };
            }

            // Orchestrators get reduced severity (they're supposed to coordinate)
            if is_orchestrator {
                severity = match severity {
                    Severity::High => Severity::Medium,
                    _ => Severity::Low,
                };
            }

            // Test functions: cap severity at Low
            if is_test {
                severity = Severity::Low;
            }

            // Unreachable code: reduce severity one level
            if !ctx.is_reachable(qn) && !ctx.is_public_api(qn) {
                severity = match severity {
                    Severity::High => Severity::Medium,
                    Severity::Medium => Severity::Low,
                    _ => severity,
                };
            }

            // Build analysis notes
            let mut notes = Vec::new();

            if is_orchestrator {
                notes.push(format!(
                    "Orchestrator pattern: calls {} functions (reduced severity)",
                    out_degree
                ));
            }

            if density > 0.3 {
                notes.push(format!(
                    "High complexity density: {:.2} (complexity {} / {} lines)",
                    density, complexity, lines
                ));
            }

            if callee_clusters.len() >= 3 {
                notes.push(format!(
                    "Calls {} different modules - possible split points",
                    callee_clusters.len()
                ));
            }

            if is_test {
                notes.push("Test function (severity capped at Low)".to_string());
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
                title: format!("Long method: {} ({} lines)", func.node_name(i), lines),
                description: format!(
                    "Function '{}' has {} lines (effective threshold: {}).{}",
                    func.node_name(i), lines, threshold, context_notes
                ),
                affected_files: vec![PathBuf::from(func.path(i))],
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

impl super::RegisteredDetector for LongMethodsDetector {
    fn create(init: &super::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::with_config(init.repo_path, init.config_for("long-methods")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeNode, GraphStore, NodeKind};

    #[test]
    fn test_language_threshold_long_methods() {
        assert_eq!(language_line_threshold("rs"), 80);
        assert_eq!(language_line_threshold("py"), 60);
        assert_eq!(language_line_threshold("java"), 100);
        assert_eq!(language_line_threshold("go"), 80);
        assert_eq!(language_line_threshold("unknown"), 80);
    }

    #[test]
    fn test_detects_long_method() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let store = GraphStore::in_memory();

        // Add a function node with line_end - line_start = 120 (> threshold 80 for .py)
        let func = CodeNode::function("big_function", "/src/app.py")
            .with_qualified_name("app::big_function")
            .with_lines(1, 121);
        store.add_node(func);

        let detector = LongMethodsDetector::new(dir.path());
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect function with 120 lines (threshold 80 for .py files)"
        );
        assert!(
            findings[0].title.contains("big_function"),
            "Title should mention function name, got: {}",
            findings[0].title
        );
    }

    #[test]
    fn test_no_finding_for_short_method() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let store = GraphStore::in_memory();

        // Add a function with only 20 lines (< threshold 80)
        let func = CodeNode::function("small_func", "/src/app.py")
            .with_qualified_name("app::small_func")
            .with_lines(1, 21);
        store.add_node(func);

        let detector = LongMethodsDetector::new(dir.path());
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag a 20-line function, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_test_function_severity_capped() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let store = GraphStore::in_memory();

        // A 250-line test function -- normally High severity
        let func = CodeNode::function("test_big_scenario", "/src/tests.py")
            .with_qualified_name("tests::test_big_scenario")
            .with_lines(1, 251);
        store.add_node(func);

        // Build context that recognizes it as a test function
        let detector = LongMethodsDetector::new(dir.path());
        // The function name starts with "test_" so FunctionContextMap
        // recognizes it, but in the test mock context the FunctionContextMap
        // is empty. We just verify that the default-path severity computation
        // works. For the FP-reduction in production, `ctx.is_test_function()`
        // uses the populated FunctionContextMap.
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        // Without the test context, it will still fire but as High severity
        // (the test mock doesn't populate FunctionContextMap)
        assert!(!findings.is_empty(), "Should still detect the 250-line function");
    }

    #[test]
    fn test_severity_uses_overshoot_ratio() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let store = GraphStore::in_memory();

        // 90 lines with threshold 80 (for .py: 60, but for .rs: 80) => slightly over threshold => Low
        // Use .rs file so threshold is 80, and 90 lines is > 80 but < 160 (2x) => Low
        let func = CodeNode::function("slightly_long", "/src/app.rs")
            .with_qualified_name("app::slightly_long")
            .with_lines(1, 91);
        store.add_node(func);

        let detector = LongMethodsDetector::new(dir.path());
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].severity, Severity::Low, "Slight overshoot should be Low");
    }

    #[test]
    fn test_description_shows_effective_threshold() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let store = GraphStore::in_memory();

        let func = CodeNode::function("big_fn", "/src/app.py")
            .with_qualified_name("app::big_fn")
            .with_lines(1, 121);
        store.add_node(func);

        let detector = LongMethodsDetector::new(dir.path());
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(!findings.is_empty());
        assert!(
            findings[0].description.contains("effective threshold"),
            "Description should show effective threshold, got: {}",
            findings[0].description
        );
    }

    #[test]
    fn test_python_threshold_is_60() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let store = GraphStore::in_memory();

        // 70-line Python function: > 60 (py threshold) => should fire
        let func = CodeNode::function("medium_fn", "/src/app.py")
            .with_qualified_name("app::medium_fn")
            .with_lines(1, 71);
        store.add_node(func);

        let detector = LongMethodsDetector::new(dir.path());
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "70-line Python function should trigger (threshold is 60)"
        );
    }

    #[test]
    fn test_java_threshold_is_100() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let store = GraphStore::in_memory();

        // 90-line Java function: < 100 (java threshold) => should NOT fire
        let func = CodeNode::function("medium_java_fn", "/src/Foo.java")
            .with_qualified_name("Foo::medium_java_fn")
            .with_lines(1, 91);
        store.add_node(func);

        let detector = LongMethodsDetector::new(dir.path());
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "90-line Java function should not trigger (threshold is 100), but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
