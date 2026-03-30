//! Change Coupling Detector (formerly Shotgun Surgery)
//!
//! Detects functions and classes where changes **propagate to callers**.
//! Uses the CoChangeMatrix to check: when function f's file changes,
//! do the files containing f's callers ALSO change?
//!
//! This replaces the old fan-in × churn heuristic which could not
//! distinguish "expected churn on a well-encapsulated interface" from
//! "problematic churn that forces callers to change too."
//!
//! Detection algorithm (DV8 Unstable Interface pattern):
//!   1. For each function/class with fan_in >= 10:
//!   2. Get caller file paths from the graph
//!   3. Query CoChangeMatrix: how many caller files co-change with this file?
//!   4. propagation_rate = co_changing_caller_files / total_caller_files
//!   5. Apply Martin's Instability filter: only flag functions that SHOULD be
//!      stable (I = fan_out / (fan_in + fan_out) < 0.5)
//!   6. Flag if propagation_rate >= 15%
//!
//! Requires co-change data — returns empty findings when unavailable.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::interner::global_interner;
use crate::graph::GraphQueryExt;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use tracing::info;

pub struct ShotgunSurgeryDetector {
    config: DetectorConfig,
}

impl ShotgunSurgeryDetector {
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
        }
    }

    pub fn with_config(config: DetectorConfig) -> Self {
        Self { config }
    }
}

impl Default for ShotgunSurgeryDetector {
    fn default() -> Self {
        Self::new()
    }
}

/// Extract the last component of a qualified name as the short display name.
fn extract_short_name(qn: &str) -> &str {
    qn.rsplit("::").next().unwrap_or(qn)
}

/// Check if a function name matches an expected-propagation pattern.
///
/// These patterns have inherently high fan-in AND co-change propagation
/// because their callers depend on their exact behavior. Changes to these
/// functions legitimately require caller updates — that's by design, not
/// a coupling problem.
///
/// Patterns:
/// - **Visitor/walker**: recursive AST/graph traversal (`walk`, `visit`, `traverse`, `accept`)
/// - **Config/factory**: configuration/initialization (`config`, `init`, `setup`, `create`, `build`, `new`)
/// - **Core accessors**: fundamental getters used everywhere (`get_`, `from_`, `into_`, `as_`)
fn is_expected_propagation_pattern(short_name: &str) -> bool {
    // Visitor/walker pattern
    if short_name.starts_with("walk")
        || short_name.starts_with("visit")
        || short_name.starts_with("traverse")
        || short_name == "accept"
    {
        return true;
    }

    // Config/factory pattern
    if short_name.starts_with("config_for")
        || short_name == "config"
        || short_name.starts_with("with_config")
        || short_name == "default"
    {
        return true;
    }

    false
}

/// Shared propagation analysis for a node (function or class).
/// Returns `Some((propagation_rate, co_changing_caller_files, total_caller_files))`
/// or `None` if there isn't enough data to analyze.
fn compute_propagation(
    graph: &dyn crate::graph::GraphQuery,
    co_change: &crate::git::co_change::CoChangeMatrix,
    qn: &str,
    file_path: &str,
) -> Option<(f32, u32, u32)> {
    let si = global_interner();
    let file_key = si.get(file_path)?;

    let callers = graph.get_callers(qn);
    let i = graph.interner();
    let mut total_caller_files = 0u32;
    let mut co_changing_caller_files = 0u32;
    let mut seen_files = HashSet::new();

    for caller in &callers {
        let caller_file = caller.path(i);
        if caller_file == file_path {
            continue; // same file, skip
        }
        if !seen_files.insert(caller_file) {
            continue; // dedup by file
        }

        total_caller_files += 1;

        if let Some(caller_key) = si.get(caller_file) {
            let pair_count = co_change.pair_commit_count(file_key, caller_key);
            if pair_count >= 3 {
                // minimum evidence threshold: at least 3 commits touching both
                co_changing_caller_files += 1;
            }
        }
    }

    if total_caller_files < 3 {
        return None; // need enough callers to measure
    }

    let propagation_rate = co_changing_caller_files as f32 / total_caller_files as f32;
    Some((
        propagation_rate,
        co_changing_caller_files,
        total_caller_files,
    ))
}

impl Detector for ShotgunSurgeryDetector {
    fn name(&self) -> &'static str {
        "ShotgunSurgeryDetector"
    }

    fn description(&self) -> &'static str {
        "Detects unstable interfaces where changes propagate to callers (co-change analysis)"
    }

    fn category(&self) -> &'static str {
        "coupling"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(
        &self,
        ctx: &crate::detectors::analysis_context::AnalysisContext,
    ) -> Result<Vec<Finding>> {
        let co_change = match &ctx.co_change_matrix {
            Some(cm) => cm,
            None => return Ok(Vec::new()), // No co-change data available
        };

        let graph = ctx.graph;
        let i = graph.interner();
        let mut findings = Vec::new();

        // ── Class analysis ──────────────────────────────────────────────────────
        for class in graph.get_classes_shared().iter() {
            if class.qn(i).contains("::interface::") {
                continue;
            }

            let qn = class.qn(i);
            let fan_in = graph.call_fan_in(qn);
            if fan_in < 10 {
                continue;
            }

            let file_path = class.path(i);

            let (propagation_rate, co_changing, total) =
                match compute_propagation(graph, co_change, qn, file_path) {
                    Some(v) => v,
                    None => continue,
                };

            if propagation_rate < 0.15 {
                continue; // less than 15% propagation = not a problem
            }

            // Martin's Instability filter: I = fan_out / (fan_in + fan_out)
            // Only flag classes that SHOULD be stable (I < 0.5)
            let fan_out = graph.call_fan_out(qn);
            let instability = if fan_in + fan_out > 0 {
                fan_out as f32 / (fan_in + fan_out) as f32
            } else {
                0.5
            };
            if instability > 0.5 {
                continue; // high instability = expected to change
            }

            let severity = if propagation_rate > 0.5 {
                Severity::Medium
            } else {
                Severity::Low
            };

            findings.push(Finding {
                id: String::new(),
                detector: "ChangeCouplingDetector".to_string(),
                severity,
                title: format!(
                    "Change Coupling: {} ({:.0}% propagation, {} callers)",
                    extract_short_name(qn),
                    propagation_rate * 100.0,
                    fan_in
                ),
                description: format!(
                    "Class '{}' has {} callers across {} files. When it changes, {:.0}% of caller files \
                     also change ({} of {}). This indicates an unstable interface where changes propagate \
                     to dependents.",
                    qn, fan_in, total, propagation_rate * 100.0, co_changing, total
                ),
                affected_files: vec![file_path.to_string().into()],
                line_start: Some(class.line_start),
                line_end: Some(class.line_end),
                suggested_fix: Some(
                    "Options to reduce change propagation:\n\
                     1. Create a Facade to limit the API surface\n\
                     2. Use interfaces/protocols to decouple callers\n\
                     3. Split into smaller, focused classes\n\
                     4. Apply Dependency Injection to reduce direct dependencies"
                        .to_string(),
                ),
                estimated_effort: Some(
                    match severity {
                        Severity::Critical => "Large (1-2 days)",
                        Severity::High => "Large (4-8 hours)",
                        _ => "Medium (2-4 hours)",
                    }
                    .to_string(),
                ),
                category: Some("coupling".to_string()),
                cwe_id: None,
                why_it_matters: Some(
                    "Change propagation means modifying this code forces changes in caller files \
                     across the codebase, increasing the chance of missing something and \
                     introducing bugs."
                        .to_string(),
                ),
                ..Default::default()
            });
        }

        // ── Function analysis ───────────────────────────────────────────────────
        for func in graph.get_functions_shared().iter() {
            let qn = func.qn(i);
            let fan_in = graph.call_fan_in(qn);
            if fan_in < 10 {
                continue;
            }

            let file_path = func.path(i);

            // Skip expected-propagation patterns where high fan-in + co-change is
            // inherent to the pattern, not a design flaw:
            // - Visitor/walker: recursive AST traversal called from many sites
            // - Config/factory: configuration lookup called by all consumers
            // - Parser entry points: called by every test and consumer
            let short_name = extract_short_name(qn).to_lowercase();
            if is_expected_propagation_pattern(&short_name) {
                continue;
            }

            let (propagation_rate, co_changing, total) =
                match compute_propagation(graph, co_change, qn, file_path) {
                    Some(v) => v,
                    None => continue,
                };

            if propagation_rate < 0.15 {
                continue;
            }

            // Instability filter
            let fan_out = graph.call_fan_out(qn);
            let instability = if fan_in + fan_out > 0 {
                fan_out as f32 / (fan_in + fan_out) as f32
            } else {
                0.5
            };
            if instability > 0.5 {
                continue;
            }

            let severity = if propagation_rate > 0.5 {
                Severity::Medium
            } else {
                Severity::Low
            };

            findings.push(Finding {
                id: String::new(),
                detector: "ChangeCouplingDetector".to_string(),
                severity,
                title: format!(
                    "Change Coupling: {} ({:.0}% propagation, {} callers)",
                    extract_short_name(qn),
                    propagation_rate * 100.0,
                    fan_in
                ),
                description: format!(
                    "Function '{}' has {} callers across {} files. When it changes, {:.0}% of caller files \
                     also change ({} of {}). This indicates an unstable interface where changes propagate \
                     to dependents.",
                    qn, fan_in, total, propagation_rate * 100.0, co_changing, total
                ),
                affected_files: vec![file_path.to_string().into()],
                line_start: Some(func.line_start),
                line_end: Some(func.line_end),
                suggested_fix: Some(
                    "Consider creating wrapper functions or using dependency injection \
                     to reduce the blast radius of changes."
                        .to_string(),
                ),
                estimated_effort: Some(
                    match severity {
                        Severity::Critical => "Large (1-2 days)",
                        Severity::High => "Large (4-8 hours)",
                        _ => "Medium (2-4 hours)",
                    }
                    .to_string(),
                ),
                category: Some("coupling".to_string()),
                cwe_id: None,
                why_it_matters: Some(
                    "High change propagation in a function with many callers indicates \
                     an unstable interface that forces coordinated changes across the codebase."
                        .to_string(),
                ),
                ..Default::default()
            });
        }

        findings.sort_by(|a, b| b.severity.cmp(&a.severity));
        info!("ChangeCouplingDetector found {} findings", findings.len());
        Ok(findings)
    }
}

impl crate::detectors::RegisteredDetector for ShotgunSurgeryDetector {
    fn create(init: &crate::detectors::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::with_config(init.config_for("ShotgunSurgeryDetector")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detectors::analysis_context::AnalysisContext;
    use crate::git::co_change::CoChangeMatrix;
    use crate::graph::builder::GraphBuilder;
    use crate::graph::{CodeEdge, CodeNode};
    use std::sync::Arc;

    #[test]
    fn test_no_co_change_data_returns_empty() {
        // When co_change_matrix is None, detector must return zero findings.
        let mut graph = GraphBuilder::new();

        graph.add_node(
            CodeNode::function("do_work", "src/big.py")
                .with_qualified_name("big::do_work")
                .with_lines(10, 20),
        );
        for i in 0..20 {
            let file = format!("src/mod_{}.py", i);
            let qn = format!("mod_{}::caller_{}", i, i);
            graph.add_node(
                CodeNode::function(&format!("caller_{}", i), &file)
                    .with_qualified_name(&qn)
                    .with_lines(1, 5),
            );
            graph.add_edge_by_name(&qn, "big::do_work", CodeEdge::calls());
        }

        // co_change_matrix is None by default in test context
        let ctx = AnalysisContext::test_with_mock_files(&graph, vec![]);
        let detector = ShotgunSurgeryDetector::new();
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "No co-change data → must return zero findings, got {}",
            findings.len()
        );
    }

    #[test]
    fn test_no_propagation_not_flagged() {
        // A function with high fan-in but no co-change propagation should NOT be flagged.
        let mut graph = GraphBuilder::new();

        graph.add_node(
            CodeNode::function("stable_api", "src/stable.py")
                .with_qualified_name("stable::stable_api")
                .with_lines(10, 20),
        );
        for i in 0..20 {
            let file = format!("src/mod_{}.py", i);
            let qn = format!("mod_{}::caller_{}", i, i);
            graph.add_node(
                CodeNode::function(&format!("caller_{}", i), &file)
                    .with_qualified_name(&qn)
                    .with_lines(1, 5),
            );
            graph.add_edge_by_name(&qn, "stable::stable_api", CodeEdge::calls());
        }

        // Empty CoChangeMatrix — no file pairs co-change
        let mut ctx = AnalysisContext::test_with_mock_files(&graph, vec![]);
        ctx.co_change_matrix = Some(Arc::new(CoChangeMatrix::empty()));

        let detector = ShotgunSurgeryDetector::new();
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "No co-change propagation → should not be flagged, got {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_extract_short_name() {
        assert_eq!(extract_short_name("module::Class::method"), "method");
        assert_eq!(extract_short_name("standalone"), "standalone");
        assert_eq!(extract_short_name("a::b"), "b");
    }
}
