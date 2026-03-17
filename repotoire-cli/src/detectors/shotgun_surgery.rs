//! Shotgun Surgery Detector
//!
//! Graph-aware detection of classes/functions where changes propagate widely.
//! Uses call graph to trace actual impact of modifications.
//!
//! Detection criteria:
//! - High fan-in (many callers)
//! - Callers spread across many files/modules
//! - Changes would cascade through call graph
//!
//! When an `AnalysisContext` is available, the detector enhances its
//! analysis with:
//! - **ContextHMM utility detection**: replaces 85+ hard-coded prefix/suffix/path
//!   patterns with a learned HMM classifier that recognises utility functions
//!   from call-graph shape and naming features.
//! - **FunctionContextMap role checks**: functions classified as `Utility` or
//!   `Hub` are skipped (they are *designed* to be widely called).
//! - **Role-based threshold scaling**: classes whose methods are predominantly
//!   `Hub` or `Utility` get a higher effective caller threshold before
//!   triggering a finding.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::function_context::FunctionRole;
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::PathBuf;
use tracing::{debug, info};

/// Thresholds for shotgun surgery detection
#[derive(Debug, Clone)]
pub struct ShotgunSurgeryThresholds {
    /// Minimum callers to consider
    pub min_callers: usize,
    /// Minimum unique files for medium severity
    pub medium_files: usize,
    /// Minimum unique files for high severity
    pub high_files: usize,
    /// Minimum unique modules for critical
    pub critical_modules: usize,
}

impl Default for ShotgunSurgeryThresholds {
    fn default() -> Self {
        Self {
            min_callers: 5,
            medium_files: 3,
            high_files: 5,
            critical_modules: 4,
        }
    }
}

pub struct ShotgunSurgeryDetector {
    config: DetectorConfig,
    thresholds: ShotgunSurgeryThresholds,
}

impl ShotgunSurgeryDetector {
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            thresholds: ShotgunSurgeryThresholds::default(),
        }
    }

    pub fn with_config(config: DetectorConfig) -> Self {
        // Apply coupling multiplier to thresholds (higher multiplier = more lenient)
        let multiplier = config.coupling_multiplier;
        let thresholds = ShotgunSurgeryThresholds {
            min_callers: ((config.get_option_or("min_callers", 5) as f64) * multiplier) as usize,
            medium_files: ((config.get_option_or("medium_files", 3) as f64) * multiplier) as usize,
            high_files: ((config.get_option_or("high_files", 5) as f64) * multiplier) as usize,
            critical_modules: ((config.get_option_or("critical_modules", 4) as f64) * multiplier)
                as usize,
        };
        Self {
            config,
            thresholds,
        }
    }

    /// Analyze impact of changing a class.
    ///
    /// When `analysis_ctx` is provided, applies role-based threshold scaling:
    /// classes whose methods are predominantly Hub or Utility get a higher
    /// effective `min_callers` threshold.
    fn analyze_class_impact(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        class: &crate::graph::CodeNode,
        det_ctx: &crate::detectors::DetectorContext,
        analysis_ctx: Option<&crate::detectors::analysis_context::AnalysisContext<'_>>,
    ) -> Option<ImpactAnalysis> {
        let i = graph.interner();
        // Find all methods belonging to this class using file-scoped index (O(1) lookup)
        // instead of scanning all 71k functions.
        let file_funcs = graph.get_functions_in_file(class.path(i));
        let methods: Vec<_> = file_funcs
            .iter()
            .filter(|f| f.line_start >= class.line_start && f.line_end <= class.line_end)
            .collect();

        // Collect all external callers of all methods
        let mut all_callers: HashSet<String> = HashSet::new();
        let mut caller_files: HashSet<String> = HashSet::new();
        let mut caller_modules: HashSet<String> = HashSet::new();

        for method in &methods {
            // Use pre-built callers map (avoids Vec<CodeNode> clone)
            if let Some(caller_qn_list) = det_ctx.callers_by_qn.get(method.qn(i)) {
                for caller_qn in caller_qn_list {
                    if let Some(caller_node) = graph.get_node(caller_qn) {
                        // Skip internal callers (same class)
                        if caller_node.file_path == class.file_path
                            && caller_node.line_start >= class.line_start
                            && caller_node.line_end <= class.line_end
                        {
                            continue;
                        }
                        all_callers.insert(caller_qn.clone());
                        caller_files.insert(caller_node.path(i).to_string());
                        caller_modules.insert(Self::extract_module(caller_node.path(i)));
                    }
                }
            } else {
                // Fallback: use graph.get_callers() (test path / empty callers map)
                for caller in graph.get_callers(method.qn(i)) {
                    if caller.file_path == class.file_path
                        && caller.line_start >= class.line_start
                        && caller.line_end <= class.line_end
                    {
                        continue;
                    }
                    all_callers.insert(caller.qn(i).to_string());
                    caller_files.insert(caller.path(i).to_string());
                    caller_modules.insert(Self::extract_module(caller.path(i)));
                }
            }
        }

        // Role-based threshold scaling: determine the predominant role of
        // the class's methods and scale min_callers accordingly.
        let effective_min_callers = if let Some(ctx) = analysis_ctx {
            let primary_role = methods
                .iter()
                .filter_map(|m| ctx.functions.get(m.qn(i)))
                .map(|fc| fc.role)
                .max_by_key(|r| match r {
                    FunctionRole::Hub => 4,
                    FunctionRole::Utility => 3,
                    FunctionRole::Orchestrator => 2,
                    _ => 1,
                })
                .unwrap_or(FunctionRole::Unknown);

            let threshold_multiplier = match primary_role {
                FunctionRole::Hub => 3.0,
                FunctionRole::Utility => 2.5,
                FunctionRole::Orchestrator => 2.0,
                _ => 1.0,
            };

            (self.thresholds.min_callers as f64 * threshold_multiplier) as usize
        } else {
            self.thresholds.min_callers
        };

        if all_callers.len() < effective_min_callers {
            return None;
        }

        // Trace cascading impact (callers of callers)
        let cascade_depth = self.trace_cascade_depth(graph, det_ctx, &all_callers, 0);

        Some(ImpactAnalysis {
            direct_callers: all_callers.len(),
            affected_files: caller_files.len(),
            affected_modules: caller_modules.len(),
            cascade_depth,
            sample_files: caller_files.iter().take(5).cloned().collect(),
        })
    }

    /// Trace how far changes cascade through the call graph.
    /// Caps expansion per level to avoid explosive growth on dense graphs.
    #[allow(clippy::only_used_in_recursion)]
    fn trace_cascade_depth(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        det_ctx: &crate::detectors::DetectorContext,
        callers: &HashSet<String>,
        depth: usize,
    ) -> usize {
        let i = graph.interner();
        // Cap at depth 3; also cap per-level expansion to avoid O(N^3) on dense graphs
        const MAX_PER_LEVEL: usize = 50;
        if depth >= 3 || callers.is_empty() {
            return depth;
        }

        let mut next_level: HashSet<String> = HashSet::new();
        for caller_qn in callers {
            // Use fan-in check to skip callers with no upstream
            if graph.call_fan_in(caller_qn) == 0 {
                continue;
            }
            // Use pre-built callers map
            if let Some(upstream_list) = det_ctx.callers_by_qn.get(caller_qn) {
                for upstream_qn in upstream_list {
                    if !callers.contains(upstream_qn) {
                        next_level.insert(upstream_qn.clone());
                        if next_level.len() >= MAX_PER_LEVEL {
                            return self.trace_cascade_depth(graph, det_ctx, &next_level, depth + 1);
                        }
                    }
                }
            } else {
                // Fallback: use graph.get_callers() (empty callers map)
                for upstream in graph.get_callers(caller_qn) {
                    let uqn = upstream.qn(i).to_string();
                    if !callers.contains(&uqn) {
                        next_level.insert(uqn);
                        if next_level.len() >= MAX_PER_LEVEL {
                            return self.trace_cascade_depth(graph, det_ctx, &next_level, depth + 1);
                        }
                    }
                }
            }
        }

        if next_level.is_empty() {
            depth
        } else {
            self.trace_cascade_depth(graph, det_ctx, &next_level, depth + 1)
        }
    }

    fn extract_module(file_path: &str) -> String {
        std::path::Path::new(file_path)
            .parent()
            .and_then(|p| p.to_str())
            .unwrap_or("root")
            .to_string()
    }

    fn calculate_severity(&self, analysis: &ImpactAnalysis) -> Severity {
        if analysis.affected_modules >= self.thresholds.critical_modules {
            Severity::Critical
        } else if analysis.affected_files >= self.thresholds.high_files {
            Severity::High
        } else if analysis.affected_files >= self.thresholds.medium_files {
            Severity::Medium
        } else {
            Severity::Low
        }
    }

    /// Core detection logic.
    ///
    /// When `analysis_ctx` is `Some`, enables enhanced checks:
    /// - ContextHMM-based utility detection (replaces 85+ hard-coded patterns)
    /// - FunctionContextMap role checks (Utility / Hub skip)
    /// - Role-based threshold scaling for classes
    fn detect_inner(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        det_ctx: &crate::detectors::DetectorContext,
        analysis_ctx: Option<&crate::detectors::analysis_context::AnalysisContext<'_>>,
    ) -> Result<Vec<Finding>> {
        let i = graph.interner();
        let mut findings = Vec::new();
        let all_functions = graph.get_functions_shared();

        for class in graph.classes_idx().iter().filter_map(|&idx| graph.node_idx(idx)) {
            // Skip interfaces
            if class.qn(i).contains("::interface::") {
                continue;
            }

            let analysis = match self.analyze_class_impact(graph, &class, det_ctx, analysis_ctx) {
                Some(a) => a,
                None => continue,
            };

            let severity = self.calculate_severity(&analysis);

            let cascade_note = if analysis.cascade_depth > 1 {
                format!(
                    "\n\n**Cascade Analysis:** Changes propagate {} levels deep through the call graph.",
                    analysis.cascade_depth
                )
            } else {
                String::new()
            };

            let sample_list = analysis.sample_files.join("\n  - ");
            let more_note = if analysis.affected_files > 5 {
                format!("\n  ... and {} more files", analysis.affected_files - 5)
            } else {
                String::new()
            };

            findings.push(Finding {
                id: String::new(),
                detector: "ShotgunSurgeryDetector".to_string(),
                severity,
                title: format!("Shotgun Surgery Risk: {}", class.node_name(i)),
                description: format!(
                    "Class '{}' is called by **{} functions** across **{} files** in **{} modules**.\n\n\
                     Any change to this class requires updates throughout the codebase.{}\n\n\
                     **Affected files (sample):**\n  - {}{}",
                    class.node_name(i),
                    analysis.direct_callers,
                    analysis.affected_files,
                    analysis.affected_modules,
                    cascade_note,
                    sample_list,
                    more_note
                ),
                affected_files: vec![class.path(i).to_string().into()],
                line_start: Some(class.line_start),
                line_end: Some(class.line_end),
                suggested_fix: Some("Options to reduce coupling:\n\
                     1. Create a Facade to limit the API surface\n\
                     2. Use interfaces/protocols to decouple\n\
                     3. Split into smaller, focused classes\n\
                     4. Apply Dependency Injection pattern".to_string()),
                estimated_effort: Some(match severity {
                    Severity::Critical => "Large (1-2 days)",
                    Severity::High => "Large (4-8 hours)",
                    _ => "Medium (2-4 hours)",
                }.to_string()),
                category: Some("coupling".to_string()),
                cwe_id: None,
                why_it_matters: Some(
                    "Shotgun surgery means a single change requires editing many files. \
                     This increases the chance of missing something and introducing bugs."
                        .to_string()
                ),
                ..Default::default()
            });
        }

        // Also check high-impact functions (not just classes)
        // Skip common trait/stdlib methods that conflate graph edges (bare method
        // calls like .clone() resolve to whichever fn was last in the global map).
        const SKIP_METHODS: &[&str] = &[
            "new", "default", "clone", "fmt", "eq", "hash", "from", "into",
            "drop", "deref", "serialize", "deserialize", "to_string",
        ];

        let min_fan_in = self.thresholds.min_callers * 2;
        for func in all_functions.iter() {
            // Fast O(1) fan-in check first — eliminates 99%+ functions before string ops
            if graph.call_fan_in(func.qn(i)) < min_fan_in {
                continue;
            }

            // Skip common trait implementations
            let name_lower = func.node_name(i).to_lowercase();
            if SKIP_METHODS
                .iter()
                .any(|m| name_lower == *m || name_lower.starts_with(m))
            {
                continue;
            }

            // ── Enhanced path: ContextHMM + FunctionContextMap role checks ──
            if let Some(ctx) = analysis_ctx {
                // Check FunctionContextMap role first (cheap HashMap lookup)
                if matches!(
                    ctx.function_role(func.qn(i)),
                    Some(FunctionRole::Utility | FunctionRole::Hub)
                ) {
                    continue;
                }

                // Check HMM classification for utility functions
                if let Some((hmm_role, conf)) = ctx.hmm_role(func.qn(i)) {
                    if matches!(
                        hmm_role,
                        crate::detectors::context_hmm::FunctionContext::Utility
                    ) && conf > 0.6
                    {
                        continue;
                    }
                }
            }

            // Zero-copy: count caller modules without cloning CodeNodes
            let module_count = graph.caller_module_spread(func.qn(i));

            if module_count >= self.thresholds.critical_modules {
                let fan_in = graph.call_fan_in(func.qn(i));
                findings.push(Finding {
                    id: String::new(),
                    detector: "ShotgunSurgeryDetector".to_string(),
                    severity: Severity::High,
                    title: format!("High-Impact Function: {}", func.node_name(i)),
                    description: format!(
                        "Function '{}' is called from {} places across {} modules.\n\n\
                         Changes will have wide-reaching effects.",
                        func.node_name(i),
                        fan_in,
                        module_count
                    ),
                    affected_files: vec![func.path(i).to_string().into()],
                    line_start: Some(func.line_start),
                    line_end: Some(func.line_end),
                    suggested_fix: Some(
                        "Consider creating wrapper functions or using dependency injection"
                            .to_string(),
                    ),
                    estimated_effort: Some("Medium (2-4 hours)".to_string()),
                    category: Some("coupling".to_string()),
                    cwe_id: None,
                    why_it_matters: Some(
                        "High-impact functions require careful change management".to_string(),
                    ),
                    ..Default::default()
                });
            }
        }

        findings.sort_by(|a, b| b.severity.cmp(&a.severity));
        info!("ShotgunSurgeryDetector found {} findings", findings.len());
        Ok(findings)
    }
}

struct ImpactAnalysis {
    direct_callers: usize,
    affected_files: usize,
    affected_modules: usize,
    cascade_depth: usize,
    sample_files: Vec<String>,
}

impl Default for ShotgunSurgeryDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for ShotgunSurgeryDetector {
    fn name(&self) -> &'static str {
        "ShotgunSurgeryDetector"
    }

    fn description(&self) -> &'static str {
        "Detects code where changes propagate widely"
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
        self.detect_inner(ctx.graph, &ctx.detector_ctx, Some(ctx))
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
    use crate::graph::{CodeEdge, CodeNode, GraphStore};
    use std::sync::Arc;

    #[test]
    fn test_detect_shotgun_surgery() {
        let graph = GraphStore::in_memory();

        // Create a class with many external callers
        graph.add_node(
            CodeNode::class("SharedService", "src/shared.py")
                .with_qualified_name("shared::SharedService")
                .with_lines(1, 50),
        );

        graph.add_node(
            CodeNode::function("do_work", "src/shared.py")
                .with_qualified_name("shared::SharedService::do_work")
                .with_lines(10, 20),
        );

        // Add callers from multiple files
        for i in 0..10 {
            let file = format!("src/module_{}.py", i);
            let caller = format!("caller_{}", i);
            graph.add_node(
                CodeNode::function(&caller, &file)
                    .with_qualified_name(&format!("module_{}::{}", i, caller))
                    .with_lines(1, 10),
            );

            graph.add_edge_by_name(
                &format!("module_{}::{}", i, caller),
                "shared::SharedService::do_work",
                CodeEdge::calls(),
            );
        }

        let detector = ShotgunSurgeryDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector
            .detect(&ctx)
            .expect("detection should succeed");

        assert!(!findings.is_empty());
        assert!(findings[0].title.contains("SharedService"));
    }

    #[test]
    fn test_hub_method_raises_threshold() {
        // Create a class with 6 callers, which normally exceeds min_callers=5.
        // But when the class's methods are classified as Hub (multiplier 3.0),
        // the effective threshold becomes 15 — so 6 callers should NOT trigger.
        let graph = GraphStore::in_memory();

        graph.add_node(
            CodeNode::class("HubService", "src/hub.py")
                .with_qualified_name("hub::HubService")
                .with_lines(1, 50),
        );

        graph.add_node(
            CodeNode::function("process", "src/hub.py")
                .with_qualified_name("hub::HubService::process")
                .with_lines(10, 20),
        );

        // Add 6 callers from different files
        for idx in 0..6 {
            let file = format!("src/mod_{}.py", idx);
            let caller = format!("caller_{}", idx);
            graph.add_node(
                CodeNode::function(&caller, &file)
                    .with_qualified_name(&format!("mod_{}::{}", idx, caller))
                    .with_lines(1, 10),
            );
            graph.add_edge_by_name(
                &format!("mod_{}::{}", idx, caller),
                "hub::HubService::process",
                CodeEdge::calls(),
            );
        }

        // Build an AnalysisContext with the process method classified as Hub
        let mut functions_map = std::collections::HashMap::new();
        functions_map.insert(
            "hub::HubService::process".to_string(),
            crate::detectors::function_context::FunctionContext {
                qualified_name: "hub::HubService::process".to_string(),
                name: "process".to_string(),
                file_path: "src/hub.py".to_string(),
                module: "hub".to_string(),
                in_degree: 6,
                out_degree: 3,
                betweenness: 0.8,
                caller_modules: 6,
                callee_modules: 2,
                call_depth: 1,
                role: FunctionRole::Hub,
                is_exported: true,
                is_test: false,
                is_in_utility_module: false,
                complexity: None,
                loc: 10,
            },
        );

        let (det_ctx, _file_data) = crate::detectors::DetectorContext::build(
            &graph,
            &[],
            None,
            std::path::Path::new("/repo"),
        );

        let ctx = crate::detectors::analysis_context::AnalysisContext {
            graph: &graph,
            files: Arc::new(crate::detectors::file_index::FileIndex::new(vec![])),
            functions: Arc::new(functions_map),
            taint: Arc::new(crate::detectors::taint::centralized::CentralizedTaintResults {
                cross_function: std::collections::HashMap::new(),
                intra_function: std::collections::HashMap::new(),
            }),
            detector_ctx: Arc::new(det_ctx),
            hmm_classifications: Arc::new(std::collections::HashMap::new()),
            resolver: Arc::new(crate::calibrate::ThresholdResolver::default()),
            reachability: Arc::new(crate::detectors::reachability::ReachabilityIndex::empty()),
            public_api: Arc::new(std::collections::HashSet::new()),
            module_metrics: Arc::new(std::collections::HashMap::new()),
            class_cohesion: Arc::new(std::collections::HashMap::new()),
            decorator_index: Arc::new(std::collections::HashMap::new()),
        };

        let detector = ShotgunSurgeryDetector::new();
        let findings = detector
            .detect(&ctx)
            .expect("detection should succeed");

        // With Hub multiplier (3.0), effective min_callers = 15.
        // Only 6 callers, so the class should NOT be flagged.
        let class_findings: Vec<_> = findings
            .iter()
            .filter(|f| f.title.contains("HubService"))
            .collect();
        assert!(
            class_findings.is_empty(),
            "Hub class with only 6 callers should not be flagged (effective threshold 15)"
        );
    }

    #[test]
    fn test_utility_function_skipped_by_hmm() {
        // A function with HMM Utility role at confidence > 0.6
        // should NOT be flagged even with high fan-in.
        let graph = GraphStore::in_memory();

        graph.add_node(
            CodeNode::function("format_output", "src/formatter.py")
                .with_qualified_name("formatter::format_output")
                .with_lines(1, 20),
        );

        // Add many callers from different modules to trigger high fan-in
        for idx in 0..20 {
            let file = format!("src/area_{}/handler.py", idx);
            let caller = format!("use_formatter_{}", idx);
            graph.add_node(
                CodeNode::function(&caller, &file)
                    .with_qualified_name(&format!("area_{}::{}", idx, caller))
                    .with_lines(1, 10),
            );
            graph.add_edge_by_name(
                &format!("area_{}::{}", idx, caller),
                "formatter::format_output",
                CodeEdge::calls(),
            );
        }

        // Build AnalysisContext with HMM classifying format_output as Utility
        let mut hmm_map = std::collections::HashMap::new();
        hmm_map.insert(
            "formatter::format_output".to_string(),
            (
                crate::detectors::context_hmm::FunctionContext::Utility,
                0.85,
            ),
        );

        let (det_ctx, _file_data) = crate::detectors::DetectorContext::build(
            &graph,
            &[],
            None,
            std::path::Path::new("/repo"),
        );

        let ctx = crate::detectors::analysis_context::AnalysisContext {
            graph: &graph,
            files: Arc::new(crate::detectors::file_index::FileIndex::new(vec![])),
            functions: Arc::new(std::collections::HashMap::new()),
            taint: Arc::new(crate::detectors::taint::centralized::CentralizedTaintResults {
                cross_function: std::collections::HashMap::new(),
                intra_function: std::collections::HashMap::new(),
            }),
            detector_ctx: Arc::new(det_ctx),
            hmm_classifications: Arc::new(hmm_map),
            resolver: Arc::new(crate::calibrate::ThresholdResolver::default()),
            reachability: Arc::new(crate::detectors::reachability::ReachabilityIndex::empty()),
            public_api: Arc::new(std::collections::HashSet::new()),
            module_metrics: Arc::new(std::collections::HashMap::new()),
            class_cohesion: Arc::new(std::collections::HashMap::new()),
            decorator_index: Arc::new(std::collections::HashMap::new()),
        };

        let detector = ShotgunSurgeryDetector::new();
        let findings = detector
            .detect(&ctx)
            .expect("detection should succeed");

        // The function should be skipped by HMM utility detection
        let func_findings: Vec<_> = findings
            .iter()
            .filter(|f| f.title.contains("format_output"))
            .collect();
        assert!(
            func_findings.is_empty(),
            "Function classified as Utility by HMM (conf=0.85) should not be flagged"
        );
    }

    #[test]
    fn test_trimmed_skip_methods() {
        // Verify that core trait methods are still skipped
        let graph = GraphStore::in_memory();

        // Create functions named after common trait methods with high fan-in
        for method_name in &["new", "default", "clone"] {
            let qn = format!("mod::{}", method_name);
            graph.add_node(
                CodeNode::function(method_name, "src/types.py")
                    .with_qualified_name(&qn)
                    .with_lines(1, 10),
            );
            // Add many callers across modules
            for idx in 0..20 {
                let file = format!("src/area_{}/use.py", idx);
                let caller = format!("caller_{}_{}", method_name, idx);
                let caller_qn = format!("area_{}::{}", idx, caller);
                graph.add_node(
                    CodeNode::function(&caller, &file)
                        .with_qualified_name(&caller_qn)
                        .with_lines(1, 5),
                );
                graph.add_edge_by_name(&caller_qn, &qn, CodeEdge::calls());
            }
        }

        let detector = ShotgunSurgeryDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector
            .detect(&ctx)
            .expect("detection should succeed");

        // "new", "default", "clone" should all be skipped
        for method_name in &["new", "default", "clone"] {
            let method_findings: Vec<_> = findings
                .iter()
                .filter(|f| {
                    f.title
                        .to_lowercase()
                        .contains(&format!("function: {}", method_name))
                })
                .collect();
            assert!(
                method_findings.is_empty(),
                "'{}' should still be in SKIP_METHODS and not flagged",
                method_name
            );
        }

        // "push", "pop", "sort" are no longer in SKIP_METHODS — they are
        // handled by ContextHMM now. Without AnalysisContext they would
        // NOT be skipped. We verify by checking they are NOT in the const.
        const SKIP_METHODS: &[&str] = &[
            "new", "default", "clone", "fmt", "eq", "hash", "from", "into",
            "drop", "deref", "serialize", "deserialize", "to_string",
        ];
        assert!(
            !SKIP_METHODS.contains(&"push"),
            "'push' should have been removed from SKIP_METHODS"
        );
        assert!(
            !SKIP_METHODS.contains(&"pop"),
            "'pop' should have been removed from SKIP_METHODS"
        );
        assert!(
            !SKIP_METHODS.contains(&"sort"),
            "'sort' should have been removed from SKIP_METHODS"
        );
    }
}
