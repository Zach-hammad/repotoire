//! AI Missing Tests detector — identifies functions without test coverage.
//!
//! Uses **graph-based test reachability** instead of name-matching:
//! BFS forward from test functions through the call graph up to 5 hops.
//! Any function reachable from a test function is considered "tested."
//!
//! This eliminates the false positives caused by:
//! - Functions tested indirectly (e.g. `test_app_factory()` calls `create_app()`)
//! - Integration tests that exercise multiple functions
//! - Functions called by test fixtures

use crate::calibrate::MetricKind;
use crate::graph::GraphQueryExt;
use crate::detectors::analysis_context::AnalysisContext;
use crate::detectors::base::{Detector, DetectorConfig, DetectorScope};
use crate::detectors::function_context::FunctionRole;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::{HashSet, VecDeque};
use std::path::PathBuf;
use tracing::debug;

/// Maximum BFS depth from test functions through the call graph.
const MAX_BFS_DEPTH: usize = 5;

/// Default cap on reported findings.
const DEFAULT_MAX_FINDINGS: usize = 50;

/// Check if a file path is a test file (by convention).
fn is_test_path(path: &str) -> bool {
    let p = path.to_lowercase();
    p.contains("/test/")
        || p.contains("/tests/")
        || p.contains("/__tests__/")
        || p.contains("_test.")
        || p.contains(".test.")
        || p.contains(".spec.")
        || p.contains("/conftest")
        || p.contains("/fixtures/")
        || p.starts_with("test/")
        || p.starts_with("tests/")
        || p.starts_with("__tests__/")
}

/// Build the set of qualified names reachable from any test function
/// within `MAX_BFS_DEPTH` hops through the call graph.
fn build_tested_functions(ctx: &AnalysisContext<'_>) -> HashSet<String> {
    let graph = ctx.graph;
    let i = graph.interner();
    let mut tested = HashSet::new();
    let mut queue: VecDeque<String> = VecDeque::new();

    // Seed: all test functions (from FunctionContextMap roles + name patterns)
    for func in graph.get_functions_shared().iter() {
        let qn = func.qn(i);
        let name = func.node_name(i);
        if ctx.is_test_function(qn)
            || name.starts_with("test_")
            || name.starts_with("test")
            || is_test_path(func.path(i))
        {
            let qn_owned = qn.to_string();
            queue.push_back(qn_owned.clone());
            tested.insert(qn_owned);
        }
    }

    debug!(
        "AIMissingTestsDetector: seeded {} test functions for BFS",
        tested.len()
    );

    // BFS forward through callees (depth limit)
    let mut depth = 0;
    while !queue.is_empty() && depth < MAX_BFS_DEPTH {
        let level_size = queue.len();
        for _ in 0..level_size {
            let func_qn = queue
                .pop_front()
                .expect("queue checked non-empty in while condition");
            for callee in graph.get_callees(&func_qn) {
                let callee_qn = callee.qn(i).to_string();
                if tested.insert(callee_qn.clone()) {
                    queue.push_back(callee_qn);
                }
            }
        }
        depth += 1;
    }

    debug!(
        "AIMissingTestsDetector: {} functions reachable from tests (depth {})",
        tested.len(),
        depth
    );

    tested
}

/// Detects functions/methods that lack test coverage via graph reachability.
pub struct AIMissingTestsDetector {
    config: DetectorConfig,
    max_findings: usize,
}

impl AIMissingTestsDetector {
    /// Create a new detector with default settings.
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            max_findings: DEFAULT_MAX_FINDINGS,
        }
    }

    /// Create with custom config.
    pub fn with_config(config: DetectorConfig) -> Self {
        Self {
            max_findings: config.get_option_or("max_findings", DEFAULT_MAX_FINDINGS),
            config,
        }
    }

    /// Generate a brief test suggestion for a function.
    fn generate_test_suggestion(func_name: &str) -> String {
        format!(
            "Add test coverage for '{}'. Consider testing normal operation, \
             edge cases, and error handling.",
            func_name
        )
    }
}

impl Default for AIMissingTestsDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for AIMissingTestsDetector {
    fn name(&self) -> &'static str {
        "AIMissingTestsDetector"
    }

    fn description(&self) -> &'static str {
        "Detects functions/methods that lack test coverage (graph-based reachability)"
    }

    fn category(&self) -> &'static str {
        "ai_generated"
    }

    fn requires_graph(&self) -> bool {
        true
    }

    fn scope(&self) -> DetectorScope {
        DetectorScope::GraphWide
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn file_extensions(&self) -> &'static [&'static str] {
        &["py", "js", "ts", "jsx", "tsx", "java", "go", "rs", "c", "cpp", "cs"]
    }

    fn detect(
        &self,
        ctx: &crate::detectors::analysis_context::AnalysisContext,
    ) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let i = graph.interner();
        let tested = build_tested_functions(ctx);

        // Adaptive complexity threshold
        let complexity_threshold = ctx.resolver.warn(MetricKind::Complexity, 5.0);

        let mut findings = Vec::new();

        for func in graph.get_functions_shared().iter() {
            let qn = func.qn(i);
            let name = func.node_name(i);
            let file_path = func.path(i);

            // Skip if already tested (via graph reachability)
            if tested.contains(qn) {
                continue;
            }

            // Skip test functions themselves
            if ctx.is_test_function(qn) {
                continue;
            }
            if name.starts_with("test_") || name.starts_with("test") {
                continue;
            }

            // Skip test files
            if is_test_path(file_path) {
                continue;
            }

            // Skip framework boilerplate
            let path_lower = file_path.to_lowercase();
            if path_lower.contains("/migrations/")
                || path_lower.contains("/admin.py")
                || path_lower.contains("/apps.py")
                || path_lower.contains("/manage.py")
                || path_lower.contains("/settings")
                || path_lower.contains("/setup.py")
                || path_lower.contains("/conf.py")
            {
                continue;
            }

            // Skip simple functions (adaptive threshold)
            let complexity = func.complexity_opt().unwrap_or(1) as f64;
            if complexity < complexity_threshold {
                continue;
            }
            let loc = func.loc();
            if loc < 15 {
                continue;
            }

            // Skip utility/leaf roles (simple functions don't need dedicated tests)
            if matches!(
                ctx.function_role(qn),
                Some(FunctionRole::Utility | FunctionRole::Leaf)
            ) {
                continue;
            }

            // Skip private functions (underscore prefix in Python)
            if name.starts_with('_') && !name.starts_with("__") {
                continue;
            }

            // Skip dunder methods
            if name.starts_with("__") && name.ends_with("__") {
                continue;
            }

            // Skip non-important: not exported AND called by fewer than 2 modules
            if !func.is_exported() {
                let caller_modules = ctx.functions.get(qn).map_or(0, |fc| fc.caller_modules);
                if caller_modules < 2 {
                    continue;
                }
            }

            // Create finding with severity based on complexity
            let severity = if complexity > 15.0 {
                Severity::High
            } else if complexity > 10.0 {
                Severity::Medium
            } else {
                Severity::Low
            };

            let func_type = if func.is_method() { "method" } else { "function" };

            findings.push(Finding {
                id: String::new(),
                detector: "AIMissingTestsDetector".to_string(),
                severity,
                title: format!("Missing tests for {}: {}", func_type, name),
                description: format!(
                    "The {} '{}' (complexity: {}, {} LOC) has no test coverage. \
                     No test function reaches it within {} call-graph hops.",
                    func_type,
                    name,
                    complexity as i64,
                    loc,
                    MAX_BFS_DEPTH
                ),
                affected_files: vec![PathBuf::from(file_path)],
                line_start: Some(func.line_start),
                line_end: Some(func.line_end),
                suggested_fix: Some(Self::generate_test_suggestion(name)),
                estimated_effort: Some("Small (15-45 minutes)".to_string()),
                category: Some("test_coverage".to_string()),
                cwe_id: None,
                why_it_matters: Some(
                    "Untested code is a maintenance risk. Tests catch bugs early, document \
                     expected behavior, and make refactoring safer."
                        .to_string(),
                ),
                ..Default::default()
            });
        }

        findings.truncate(self.max_findings);
        debug!(
            "AIMissingTestsDetector: found {} missing test findings",
            findings.len()
        );
        Ok(findings)
    }
}

impl super::RegisteredDetector for AIMissingTestsDetector {
    fn create(_init: &super::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::new())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detectors::analysis_context::AnalysisContext;
    use crate::detectors::detector_context::{ContentFlags, DetectorContext};
    use crate::detectors::file_index::FileIndex;
    use crate::detectors::taint::centralized::CentralizedTaintResults;
    use crate::graph::{CodeEdge, CodeNode, GraphStore};
    use std::collections::HashMap;
    use std::path::Path;
    use std::sync::Arc;

    /// Build a minimal AnalysisContext for testing.
    fn make_ctx(graph: &dyn crate::graph::GraphQuery) -> AnalysisContext<'_> {
        let files = Arc::new(FileIndex::new(vec![]));
        let functions = Arc::new(HashMap::new());
        let taint = Arc::new(CentralizedTaintResults {
            cross_function: HashMap::new(),
            intra_function: HashMap::new(),
        });
        let (det_ctx, _) = DetectorContext::build(graph, &[], None, Path::new("/repo"));
        AnalysisContext {
            graph,
            files,
            functions,
            taint,
            detector_ctx: Arc::new(det_ctx),
            hmm_classifications: Arc::new(HashMap::new()),
            resolver: Arc::new(crate::calibrate::ThresholdResolver::default()),
            reachability: Arc::new(crate::detectors::reachability::ReachabilityIndex::empty()),
            public_api: Arc::new(std::collections::HashSet::new()),
            module_metrics: Arc::new(HashMap::new()),
            class_cohesion: Arc::new(HashMap::new()),
            decorator_index: Arc::new(HashMap::new()),
            git_churn: Arc::new(HashMap::new()),
            co_change_summary: Arc::new(HashMap::new()),
            co_change_matrix: None,
        }
    }

    #[test]
    fn test_is_test_path() {
        assert!(is_test_path("tests/test_module.py"));
        assert!(is_test_path("src/tests/util.py"));
        assert!(is_test_path("app_test.py"));
        assert!(is_test_path("app.test.js"));
        assert!(is_test_path("app.spec.ts"));
        assert!(is_test_path("__tests__/app.js"));
        assert!(is_test_path("test/integration.py"));
        assert!(is_test_path("src/conftest.py"));
        assert!(is_test_path("src/fixtures/data.py"));

        assert!(!is_test_path("src/module.py"));
        assert!(!is_test_path("app.js"));
        assert!(!is_test_path("src/analytics.py"));
    }

    #[test]
    fn test_function_reachable_from_test_not_flagged() {
        let store = GraphStore::in_memory();

        // Add a complex function
        let func = CodeNode::function("create_app", "src/app.py")
            .with_qualified_name("app.create_app")
            .with_lines(1, 40)
            .with_property("complexity", 10_i64)
            .with_property("exported", true);
        store.add_node(func);

        // Add a test function that calls create_app
        let test_fn = CodeNode::function("test_app_factory", "tests/test_app.py")
            .with_qualified_name("tests.test_app.test_app_factory")
            .with_lines(1, 15)
            .with_property("complexity", 2_i64);
        store.add_node(test_fn);

        // Add the call edge: test_app_factory -> create_app
        store.add_edge_by_name(
            "tests.test_app.test_app_factory",
            "app.create_app",
            CodeEdge::calls(),
        );

        let ctx = make_ctx(&store);
        let detector = AIMissingTestsDetector::new();
        let findings = detector.detect(&ctx).expect("detect should succeed");

        assert!(
            findings.is_empty(),
            "Function reachable from test should NOT be flagged. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_indirectly_tested_function_not_flagged() {
        let store = GraphStore::in_memory();

        // Add a helper function called by create_app
        let helper = CodeNode::function("init_db", "src/db.py")
            .with_qualified_name("db.init_db")
            .with_lines(1, 30)
            .with_property("complexity", 8_i64)
            .with_property("exported", true);
        store.add_node(helper);

        // Add create_app which calls init_db
        let func = CodeNode::function("create_app", "src/app.py")
            .with_qualified_name("app.create_app")
            .with_lines(1, 40)
            .with_property("complexity", 10_i64)
            .with_property("exported", true);
        store.add_node(func);

        // Add test function
        let test_fn = CodeNode::function("test_app_factory", "tests/test_app.py")
            .with_qualified_name("tests.test_app.test_app_factory")
            .with_lines(1, 15)
            .with_property("complexity", 2_i64);
        store.add_node(test_fn);

        // test -> create_app -> init_db (2 hops)
        store.add_edge_by_name(
            "tests.test_app.test_app_factory",
            "app.create_app",
            CodeEdge::calls(),
        );
        store.add_edge_by_name("app.create_app", "db.init_db", CodeEdge::calls());

        let ctx = make_ctx(&store);
        let detector = AIMissingTestsDetector::new();
        let findings = detector.detect(&ctx).expect("detect should succeed");

        assert!(
            findings.is_empty(),
            "Indirectly tested function (2 hops) should NOT be flagged. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_unreachable_complex_function_flagged() {
        let store = GraphStore::in_memory();

        // A complex exported function with no test reaching it
        let func = CodeNode::function("calculate_risk", "src/analytics.py")
            .with_qualified_name("analytics.calculate_risk")
            .with_lines(1, 40)
            .with_property("complexity", 12_i64)
            .with_property("exported", true);
        store.add_node(func);

        let ctx = make_ctx(&store);
        let detector = AIMissingTestsDetector::new();
        let findings = detector.detect(&ctx).expect("detect should succeed");

        assert!(
            !findings.is_empty(),
            "Unreachable complex exported function should be flagged"
        );
        assert_eq!(findings[0].severity, Severity::Medium);
    }

    #[test]
    fn test_simple_function_not_flagged() {
        let store = GraphStore::in_memory();

        // Simple function: low complexity, short
        let func = CodeNode::function("get_name", "src/models.py")
            .with_qualified_name("models.get_name")
            .with_lines(1, 5)
            .with_property("complexity", 1_i64)
            .with_property("exported", true);
        store.add_node(func);

        let ctx = make_ctx(&store);
        let detector = AIMissingTestsDetector::new();
        let findings = detector.detect(&ctx).expect("detect should succeed");

        assert!(
            findings.is_empty(),
            "Simple function should NOT be flagged. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_private_function_not_flagged() {
        let store = GraphStore::in_memory();

        let func = CodeNode::function("_internal_helper", "src/utils.py")
            .with_qualified_name("utils._internal_helper")
            .with_lines(1, 30)
            .with_property("complexity", 10_i64)
            .with_property("exported", true);
        store.add_node(func);

        let ctx = make_ctx(&store);
        let detector = AIMissingTestsDetector::new();
        let findings = detector.detect(&ctx).expect("detect should succeed");

        assert!(
            findings.is_empty(),
            "Private functions should NOT be flagged. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_dunder_method_not_flagged() {
        let store = GraphStore::in_memory();

        let func = CodeNode::function("__repr__", "src/models.py")
            .with_qualified_name("models.MyClass.__repr__")
            .with_lines(1, 20)
            .with_property("complexity", 6_i64)
            .with_property("exported", true);
        store.add_node(func);

        let ctx = make_ctx(&store);
        let detector = AIMissingTestsDetector::new();
        let findings = detector.detect(&ctx).expect("detect should succeed");

        assert!(
            findings.is_empty(),
            "Dunder methods should NOT be flagged. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_detect_returns_empty() {
        // detect() with empty graph returns no findings
        let store = GraphStore::in_memory();
        let func = CodeNode::function("complex_fn", "src/core.py")
            .with_qualified_name("core.complex_fn")
            .with_lines(1, 50)
            .with_property("complexity", 20_i64);
        store.add_node(func);

        let detector = AIMissingTestsDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test(&store);
        let findings = detector
            .detect(&ctx)
            .expect("detect should succeed");
        assert!(findings.is_empty(), "Legacy detect() should return empty");
    }

    #[test]
    fn test_severity_based_on_complexity() {
        let store = GraphStore::in_memory();

        // High complexity (>15) -> High severity
        let func = CodeNode::function("very_complex", "src/core.py")
            .with_qualified_name("core.very_complex")
            .with_lines(1, 60)
            .with_property("complexity", 20_i64)
            .with_property("exported", true);
        store.add_node(func);

        let ctx = make_ctx(&store);
        let detector = AIMissingTestsDetector::new();
        let findings = detector.detect(&ctx).expect("detect should succeed");

        assert!(!findings.is_empty(), "Should flag very complex function");
        assert_eq!(findings[0].severity, Severity::High);
    }

    #[test]
    fn test_test_function_itself_not_flagged() {
        let store = GraphStore::in_memory();

        // A test function with high complexity should not be flagged
        let func = CodeNode::function("test_complex_scenario", "tests/test_core.py")
            .with_qualified_name("tests.test_core.test_complex_scenario")
            .with_lines(1, 50)
            .with_property("complexity", 15_i64)
            .with_property("exported", true);
        store.add_node(func);

        let ctx = make_ctx(&store);
        let detector = AIMissingTestsDetector::new();
        let findings = detector.detect(&ctx).expect("detect should succeed");

        assert!(
            findings.is_empty(),
            "Test functions themselves should NOT be flagged. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
