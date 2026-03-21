//! Change Coupling Detector (formerly Shotgun Surgery)
//!
//! Detects classes and functions that are BOTH widely depended on AND
//! frequently changing. Stable infrastructure (high fan-in, low churn)
//! is NOT flagged — only volatile, widely-coupled nodes are risks.
//!
//! Detection formula:
//!   risk = spread_ratio × churn_rate
//!
//! Where:
//!   churn_rate   = min(commits_90d / 9.0, 1.0)
//!   spread_ratio = (caller_module_spread / fan_in).min(1.0)
//!
//! Requires git churn data — returns empty findings when unavailable.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::models::{Finding, Severity};
use anyhow::Result;
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

impl Detector for ShotgunSurgeryDetector {
    fn name(&self) -> &'static str {
        "ShotgunSurgeryDetector"
    }

    fn description(&self) -> &'static str {
        "Detects code with high change coupling: widely-depended-on nodes that change frequently"
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
        // If no git churn data available, skip entirely — we can't distinguish
        // stable infrastructure from volatile coupling risks without churn data.
        if ctx.git_churn.is_empty() {
            return Ok(Vec::new());
        }

        let graph = ctx.graph;
        let i = graph.interner();
        let mut findings = Vec::new();

        // ── Class analysis ──────────────────────────────────────────────────────
        for class in graph.get_classes_shared().iter() {
            // Skip interface nodes
            if class.qn(i).contains("::interface::") {
                continue;
            }

            let qn = class.qn(i);
            let fan_in = graph.call_fan_in(qn);
            if fan_in < 10 {
                continue;
            }

            let file_path = class.path(i);

            // Churn rate: 9 commits/90d → rate 1.0; fewer commits → proportionally lower
            let churn_rate = ctx
                .file_churn(file_path)
                .map(|c| (c.commits_90d as f64 / 9.0).min(1.0))
                .unwrap_or(0.0);

            if churn_rate < 0.01 {
                // Stable code — not a change coupling risk regardless of fan-in
                continue;
            }

            // Caller spread as a proxy for co-change impact
            let module_spread = graph.caller_module_spread(qn);
            let spread_ratio = (module_spread as f64 / fan_in as f64).min(1.0);

            let risk = spread_ratio * churn_rate;
            if risk < 0.1 {
                continue;
            }

            let commits_90d = ctx
                .file_churn(file_path)
                .map(|c| c.commits_90d)
                .unwrap_or(0);

            let severity = if risk > 0.5 && fan_in >= 30 {
                Severity::Critical
            } else if risk > 0.3 && fan_in >= 15 {
                Severity::High
            } else {
                Severity::Medium
            };

            findings.push(Finding {
                id: String::new(),
                detector: "ChangeCouplingDetector".to_string(),
                severity,
                title: format!(
                    "Change Coupling Risk: {}",
                    extract_short_name(qn)
                ),
                description: format!(
                    "Class '{}' has {} callers across {} modules and changed {} times in the last 90 days.\n\
                     Risk score: {:.2}. Changes here have wide-reaching effects.",
                    qn, fan_in, module_spread, commits_90d, risk
                ),
                affected_files: vec![file_path.to_string().into()],
                line_start: Some(class.line_start),
                line_end: Some(class.line_end),
                suggested_fix: Some(
                    "Options to reduce change coupling:\n\
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
                    "Change coupling means modifying this code requires updating callers \
                     across many modules, increasing the chance of missing something and \
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
            if fan_in < 15 {
                continue;
            }

            let file_path = func.path(i);

            let churn_rate = ctx
                .file_churn(file_path)
                .map(|c| (c.commits_90d as f64 / 9.0).min(1.0))
                .unwrap_or(0.0);

            if churn_rate < 0.01 {
                continue;
            }

            let module_spread = graph.caller_module_spread(qn);
            let spread_ratio = (module_spread as f64 / fan_in as f64).min(1.0);

            let risk = spread_ratio * churn_rate;
            if risk < 0.1 {
                continue;
            }

            let commits_90d = ctx
                .file_churn(file_path)
                .map(|c| c.commits_90d)
                .unwrap_or(0);

            let severity = if risk > 0.5 && fan_in >= 30 {
                Severity::Critical
            } else if risk > 0.3 && fan_in >= 15 {
                Severity::High
            } else {
                Severity::Medium
            };

            findings.push(Finding {
                id: String::new(),
                detector: "ChangeCouplingDetector".to_string(),
                severity,
                title: format!(
                    "Change Coupling Risk: {}",
                    extract_short_name(qn)
                ),
                description: format!(
                    "Function '{}' has {} callers across {} modules and changed {} times in the last 90 days.\n\
                     Risk score: {:.2}. Changes here have wide-reaching effects.",
                    qn, fan_in, module_spread, commits_90d, risk
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
                    "High change coupling in a frequently-modified function requires \
                     careful change management across many call sites."
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

impl super::RegisteredDetector for ShotgunSurgeryDetector {
    fn create(init: &super::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::with_config(init.config_for("ShotgunSurgeryDetector")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detectors::analysis_context::AnalysisContext;
    use crate::detectors::analysis_context::FileChurnInfo;
    use crate::graph::{CodeEdge, CodeNode, GraphStore};
    use std::collections::HashMap;
    use std::sync::Arc;

    /// Build a minimal AnalysisContext with custom git_churn for testing.
    fn ctx_with_churn<'g>(
        graph: &'g dyn crate::graph::GraphQuery,
        churn: HashMap<String, FileChurnInfo>,
    ) -> AnalysisContext<'g> {
        let mut ctx = AnalysisContext::test_with_mock_files(graph, vec![]);
        // Replace git_churn with our test data
        ctx.git_churn = Arc::new(churn);
        ctx
    }

    #[test]
    fn test_no_git_data_returns_empty() {
        // When git_churn is empty, detector must return zero findings
        // even if the graph has highly-connected nodes.
        let graph = GraphStore::in_memory();

        graph.add_node(
            CodeNode::class("BigService", "src/big.py")
                .with_qualified_name("big::BigService")
                .with_lines(1, 100),
        );
        graph.add_node(
            CodeNode::function("do_work", "src/big.py")
                .with_qualified_name("big::BigService::do_work")
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
            graph.add_edge_by_name(&qn, "big::BigService::do_work", CodeEdge::calls());
        }

        let ctx = AnalysisContext::test_with_mock_files(&graph, vec![]);
        // git_churn is empty by default in test_with_mock_files
        let detector = ShotgunSurgeryDetector::new();
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "No git data → must return zero findings, got {}",
            findings.len()
        );
    }

    #[test]
    fn test_stable_high_fanin_not_flagged() {
        // A class with fan-in 50 but zero churn should NOT be flagged.
        let graph = GraphStore::in_memory();

        graph.add_node(
            CodeNode::class("StableService", "src/stable.py")
                .with_qualified_name("stable::StableService")
                .with_lines(1, 100),
        );
        graph.add_node(
            CodeNode::function("do_work", "src/stable.py")
                .with_qualified_name("stable::StableService::do_work")
                .with_lines(10, 20),
        );
        for i in 0..50 {
            let file = format!("src/mod_{}.py", i);
            let qn = format!("mod_{}::caller_{}", i, i);
            graph.add_node(
                CodeNode::function(&format!("caller_{}", i), &file)
                    .with_qualified_name(&qn)
                    .with_lines(1, 5),
            );
            graph.add_edge_by_name(&qn, "stable::StableService::do_work", CodeEdge::calls());
        }

        // Zero churn for this file
        let churn: HashMap<String, FileChurnInfo> = HashMap::new();
        // git_churn is non-empty (we add an entry for a different file)
        let mut churn_map = HashMap::new();
        churn_map.insert(
            "src/other.py".to_string(),
            FileChurnInfo {
                commits_90d: 5,
                is_high_churn: true,
            },
        );
        let _ = churn;

        let ctx = ctx_with_churn(&graph, churn_map);
        let detector = ShotgunSurgeryDetector::new();
        let findings = detector.detect(&ctx).expect("detection should succeed");

        let stable_findings: Vec<_> = findings
            .iter()
            .filter(|f| f.title.contains("StableService"))
            .collect();
        assert!(
            stable_findings.is_empty(),
            "Stable class (zero churn) should not be flagged, got {:?}",
            stable_findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_volatile_high_fanin_flagged() {
        // A class with fan-in 20, callers in 10 modules, 9 commits in 90 days → flagged.
        // churn_rate = 9/9 = 1.0, spread_ratio = 10/20 = 0.5, risk = 0.5 — above 0.1 threshold.
        let graph = GraphStore::in_memory();

        graph.add_node(
            CodeNode::class("VolatileService", "src/volatile.py")
                .with_qualified_name("volatile::VolatileService")
                .with_lines(1, 100),
        );
        graph.add_node(
            CodeNode::function("do_work", "src/volatile.py")
                .with_qualified_name("volatile::VolatileService::do_work")
                .with_lines(10, 20),
        );
        // 20 callers from 10 distinct modules (2 callers per module)
        for i in 0..20usize {
            let module = i / 2; // modules 0..9
            let file = format!("src/module_{}/handler.py", module);
            let qn = format!("module_{}::caller_{}", module, i);
            graph.add_node(
                CodeNode::function(&format!("caller_{}", i), &file)
                    .with_qualified_name(&qn)
                    .with_lines(1, 5),
            );
            graph.add_edge_by_name(
                &qn,
                "volatile::VolatileService::do_work",
                CodeEdge::calls(),
            );
        }

        let mut churn_map = HashMap::new();
        churn_map.insert(
            "src/volatile.py".to_string(),
            FileChurnInfo {
                commits_90d: 9, // churn_rate = 1.0
                is_high_churn: true,
            },
        );

        let ctx = ctx_with_churn(&graph, churn_map);
        let detector = ShotgunSurgeryDetector::new();
        let findings = detector.detect(&ctx).expect("detection should succeed");

        // The method do_work has fan-in=20, callers across 10 modules, churn_rate=1.0
        // → risk = 0.5 → should be flagged via function analysis (fan_in >= 15)
        let volatile_findings: Vec<_> = findings
            .iter()
            .filter(|f| f.title.contains("do_work"))
            .collect();
        assert!(
            !volatile_findings.is_empty(),
            "Volatile method (fan-in=20, 10 modules, 9 commits) should be flagged, got findings: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
        assert!(
            volatile_findings[0].title.contains("Change Coupling Risk"),
            "Title should contain 'Change Coupling Risk', got: {}",
            volatile_findings[0].title
        );
    }
}
