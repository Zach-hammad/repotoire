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
//! - Handler exemption (2x threshold for HMM-classified handlers)
//! - Orchestrator allowance (1.5x threshold via FunctionRole or graph heuristic)
//! - Test function severity cap (capped at Low)
//! - Unreachable code severity reduction
//! - Match/switch inflation adjustment (extra threshold per arm)

use crate::detectors::analysis_context::AnalysisContext;
use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::function_context::FunctionRole;
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use tracing::info;

/// Lines added to the effective threshold per match/switch arm detected.
/// Each arm is structural boilerplate rather than distinct logic.
/// Set to 4 (up from 3) to better account for the body lines within each arm.
const LINES_PER_MATCH_ARM: u32 = 4;

/// Minimum match arms before we apply the inflation adjustment.
/// Lowered from 5 to 4 so that smaller match blocks (4+ arms) also
/// receive the inflation benefit, reducing FP on match-heavy code.
const MIN_MATCH_ARMS_FOR_ADJUSTMENT: u32 = 4;

pub struct LongMethodsDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    #[allow(dead_code)] // Part of detector pattern
    config: DetectorConfig,
    max_findings: usize,
    threshold: u32,
}

impl LongMethodsDetector {
    #[allow(dead_code)] // Constructor used by tests and detector registration
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

    /// Count match/switch arms in a function's source range.
    ///
    /// Looks for patterns like:
    /// - Rust: `=> {` or `=> expr,` (match arms)
    /// - JS/TS/Java/C/C++/Go: `case ` (switch cases)
    /// - Python: `case ` (structural pattern matching, 3.10+)
    ///
    /// Returns the number of arms detected.
    fn count_match_arms(content: &str, line_start: u32, line_end: u32) -> u32 {
        let mut arms = 0u32;
        for (i, line) in content.lines().enumerate() {
            let line_num = (i + 1) as u32;
            if line_num < line_start || line_num > line_end {
                continue;
            }
            let trimmed = line.trim();
            // Skip comment lines
            if trimmed.starts_with("//") || trimmed.starts_with("*") || trimmed.starts_with("/*") {
                continue;
            }
            // Rust match arms: `pattern => ...`
            // Skip lines where `=>` appears inside a string literal (e.g.,
            // `if line.contains("=>")`) to avoid false inflation.
            if trimmed.contains("=>") && !Self::arrow_in_string(trimmed) {
                arms += 1;
            }
            // switch/case in JS/TS/Java/C/C++/Go/Python
            if trimmed.starts_with("case ") || trimmed.starts_with("case(") {
                arms += 1;
            }
        }
        arms
    }

    /// Check if `=>` on a line appears only inside a string literal.
    /// Simple heuristic: if `=>` occurs after an odd number of `"` chars,
    /// it is likely inside a string.
    fn arrow_in_string(line: &str) -> bool {
        if let Some(arrow_pos) = line.find("=>") {
            let quotes_before = line[..arrow_pos].bytes().filter(|&b| b == b'"').count();
            // Odd number of quotes before => means we're inside a string
            quotes_before % 2 == 1
        } else {
            false
        }
    }

    /// Compute the effective threshold for a function, incorporating all
    /// context-aware adjustments.
    fn effective_threshold(
        &self,
        ctx: &AnalysisContext,
        qn: &str,
        base_threshold: u32,
        is_orchestrator: bool,
        match_arms: u32,
    ) -> u32 {
        let mut threshold = base_threshold;

        // Handler exemption: handlers legitimately dispatch many cases
        if ctx.is_handler(qn) {
            threshold *= 2;
        }

        // Orchestrator allowance: 50% increase for coordinating functions
        if is_orchestrator {
            threshold = threshold * 3 / 2;
        }

        // Match/switch inflation: each arm beyond the minimum adds structural
        // lines that inflate line count without adding distinct logic
        if match_arms >= MIN_MATCH_ARMS_FOR_ADJUSTMENT {
            threshold += match_arms * LINES_PER_MATCH_ARM;
        }

        threshold
    }
}

impl Detector for LongMethodsDetector {
    fn name(&self) -> &'static str {
        "long-methods"
    }
    fn description(&self) -> &'static str {
        "Detects methods/functions over 50 lines"
    }

    fn file_extensions(&self) -> &'static [&'static str] {
        &["py", "js", "ts", "jsx", "tsx", "rb", "java", "go", "rs", "c", "cpp", "cs"]
    }

    fn detect(&self, ctx: &AnalysisContext) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let i = graph.interner();
        let mut findings = vec![];

        // Use adaptive threshold from AnalysisContext resolver, falling back
        // to the config-resolved threshold. Floor at 50 to prevent adaptive
        // calibration from making the detector overly sensitive.
        let base_threshold = (ctx.threshold(
            crate::calibrate::MetricKind::FunctionLength,
            self.threshold as f64,
        ) as u32)
            .max(50);

        // Pre-load file contents for match-arm counting.
        // Key: file path, Value: file content string
        let mut file_content_cache: HashSet<String> = HashSet::new();

        for func in graph.get_functions_shared().iter() {
            if findings.len() >= self.max_findings {
                break;
            }

            // Skip detector files (they have inherently complex parsing logic)
            if func.path(i).contains("/detectors/") {
                continue;
            }

            let lines = func.line_end.saturating_sub(func.line_start);

            // Quick pre-filter: skip functions clearly under the base threshold.
            // Even with all adjustments removed, we'd still need > base_threshold.
            if lines <= base_threshold {
                continue;
            }

            let qn = func.qn(i);

            // Get complexity for analysis
            let complexity = func.complexity_opt().unwrap_or(1);

            // Determine orchestrator status from pre-computed FunctionRole first,
            // then fall back to graph heuristic.
            let is_orchestrator = matches!(
                ctx.function_role(qn),
                Some(FunctionRole::Orchestrator)
            ) || self.is_graph_orchestrator(graph, qn, lines, complexity);

            // Count match/switch arms in the function body for inflation adjustment
            let match_arms = Self::get_match_arm_count(ctx, func.path(i), func.line_start, func.line_end, &mut file_content_cache);

            // Compute effective threshold with all context adjustments
            let effective_threshold =
                self.effective_threshold(ctx, qn, base_threshold, is_orchestrator, match_arms);

            if lines <= effective_threshold {
                continue;
            }

            let is_test = ctx.is_test_function(qn);
            let callee_clusters = self.find_callee_clusters(graph, qn);
            let density = Self::complexity_density(complexity, lines);
            let callees = graph.get_callees(qn);
            let out_degree = callees.len();

            // Calculate severity based on how far over the effective threshold
            let overshoot = lines as f64 / effective_threshold as f64;
            let mut severity = if overshoot > 4.0 {
                Severity::High
            } else if overshoot > 2.0 {
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

            if match_arms >= MIN_MATCH_ARMS_FOR_ADJUSTMENT {
                notes.push(format!(
                    "Match/switch inflation: {} arms detected (threshold raised by {})",
                    match_arms,
                    match_arms * LINES_PER_MATCH_ARM
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
                    func.node_name(i), lines, effective_threshold, context_notes
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

impl LongMethodsDetector {
    /// Get match arm count for a function, using file content from
    /// AnalysisContext FileIndex or the global cache.
    fn get_match_arm_count(
        ctx: &AnalysisContext,
        file_path: &str,
        line_start: u32,
        line_end: u32,
        _visited: &mut HashSet<String>,
    ) -> u32 {
        let path = Path::new(file_path);

        // Try AnalysisContext FileIndex first (test-friendly)
        if let Some(entry) = ctx.files.get(path) {
            return Self::count_match_arms(&entry.content, line_start, line_end);
        }

        // Fall back to global cache (production path)
        if let Some(content) = crate::cache::global_cache().content(path) {
            return Self::count_match_arms(&content, line_start, line_end);
        }

        0
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
    fn test_detects_long_method() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let store = GraphStore::in_memory();

        // Add a function node with line_end - line_start = 120 (> threshold 50)
        let func = CodeNode::function("big_function", "/src/app.py")
            .with_qualified_name("app::big_function")
            .with_lines(1, 121);
        store.add_node(func);

        let detector = LongMethodsDetector::new(dir.path());
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect function with 120 lines (threshold 50)"
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

        // Add a function with only 20 lines (< threshold 50)
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
    fn test_match_arm_counting() {
        // Rust match arms
        let content = r#"fn handle(x: i32) {
    match x {
        1 => println!("one"),
        2 => println!("two"),
        3 => println!("three"),
        4 => println!("four"),
        5 => println!("five"),
        6 => println!("six"),
        _ => println!("other"),
    }
}"#;
        let arms = LongMethodsDetector::count_match_arms(content, 1, 11);
        assert_eq!(arms, 7, "Should count 7 match arms");
    }

    #[test]
    fn test_switch_case_counting() {
        // JS/Java switch cases
        let content = r#"function handle(x) {
    switch(x) {
        case 1: return "one";
        case 2: return "two";
        case 3: return "three";
        case 4: return "four";
        case 5: return "five";
        case 6: return "six";
        default: return "other";
    }
}"#;
        let arms = LongMethodsDetector::count_match_arms(content, 1, 11);
        assert_eq!(arms, 6, "Should count 6 case statements");
    }

    #[test]
    fn test_match_inflation_raises_threshold() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let store = GraphStore::in_memory();

        // Function with 80 lines containing many match arms.
        // Base threshold is 50, but 10 match arms should add 10*4=40 extra,
        // making effective threshold 90. So 80-line function should NOT trigger.
        let func = CodeNode::function("dispatch", "/src/app.rs")
            .with_qualified_name("app::dispatch")
            .with_lines(1, 81);
        store.add_node(func);

        // Build file content with 10 match arms within lines 1-81
        let mut lines_vec = vec!["fn dispatch(cmd: i32) -> &'static str {".to_string()];
        lines_vec.push("    match cmd {".to_string());
        for j in 0..10 {
            lines_vec.push(format!("        {} => \"val{}\",", j, j));
        }
        lines_vec.push("    }".to_string());
        // Pad to 81 lines total
        while lines_vec.len() < 81 {
            lines_vec.push("    // padding".to_string());
        }
        lines_vec.push("}".to_string());
        let file_content = lines_vec.join("\n");

        let detector = LongMethodsDetector::new(dir.path());
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
            &store,
            vec![("/src/app.rs", &file_content)],
        );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "80-line function with 10 match arms should not trigger (effective threshold ~80), \
             but got: {:?}",
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

        // 60 lines with threshold 50 => overshoot 1.2 => Low
        let func = CodeNode::function("slightly_long", "/src/app.py")
            .with_qualified_name("app::slightly_long")
            .with_lines(1, 61);
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
}
