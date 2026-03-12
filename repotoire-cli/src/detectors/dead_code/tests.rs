use super::*;

// ── Static helper tests ─────────────────────────────────────────────

#[test]
fn test_entry_points() {
    assert!(DeadCodeDetector::is_entry_point("main"));
    assert!(DeadCodeDetector::is_entry_point("__init__"));
    assert!(DeadCodeDetector::is_entry_point("__main__"));
    assert!(DeadCodeDetector::is_entry_point("setUp"));
    assert!(DeadCodeDetector::is_entry_point("tearDown"));
    assert!(DeadCodeDetector::is_entry_point("test_something"));
    assert!(!DeadCodeDetector::is_entry_point("my_function"));
    assert!(!DeadCodeDetector::is_entry_point("helper"));
}

#[test]
fn test_dunder_methods_by_pattern() {
    // Dunder methods are now checked via starts_with("__") && ends_with("__")
    // instead of a static list
    let dunder = |name: &str| name.starts_with("__") && name.ends_with("__");

    assert!(dunder("__str__"));
    assert!(dunder("__repr__"));
    assert!(dunder("__enter__"));
    assert!(dunder("__exit__"));
    assert!(dunder("__call__"));
    assert!(dunder("__post_init__"));
    assert!(dunder("__init_subclass__"));
    assert!(!dunder("my_method"));
    assert!(!dunder("__private")); // Single underscore prefix, not dunder
    assert!(!dunder("regular"));
}

#[test]
fn test_common_trait_methods() {
    assert!(DeadCodeDetector::is_common_trait_method("new"));
    assert!(DeadCodeDetector::is_common_trait_method("default"));
    assert!(DeadCodeDetector::is_common_trait_method("from"));
    assert!(DeadCodeDetector::is_common_trait_method("serialize"));
    assert!(DeadCodeDetector::is_common_trait_method("build"));
    assert!(!DeadCodeDetector::is_common_trait_method("my_function"));
    assert!(!DeadCodeDetector::is_common_trait_method("process"));
}

#[test]
fn test_severity() {
    let detector = DeadCodeDetector::new();

    assert_eq!(detector.calculate_function_severity(5), Severity::Low);
    assert_eq!(detector.calculate_function_severity(10), Severity::Medium);
    assert_eq!(detector.calculate_function_severity(25), Severity::High);

    assert_eq!(detector.calculate_class_severity(3, 10), Severity::Low);
    assert_eq!(detector.calculate_class_severity(5, 10), Severity::Medium);
    assert_eq!(detector.calculate_class_severity(10, 10), Severity::High);
}

#[test]
fn test_is_test_path() {
    // Rust test module files (tests.rs, test.rs)
    assert!(DeadCodeDetector::is_test_path(
        "src/detectors/dead_code/tests.rs"
    ));
    assert!(DeadCodeDetector::is_test_path("src/some_module/test.rs"));

    // Test directories
    assert!(DeadCodeDetector::is_test_path(
        "tests/integration/test_api.rs"
    ));
    assert!(DeadCodeDetector::is_test_path("src/tests/helpers.rs"));
    assert!(DeadCodeDetector::is_test_path(
        "src/__tests__/utils.test.ts"
    ));
    assert!(DeadCodeDetector::is_test_path("src/spec/helpers.js"));

    // Should NOT match regular files
    assert!(!DeadCodeDetector::is_test_path(
        "src/detectors/dead_code/mod.rs"
    ));
    assert!(!DeadCodeDetector::is_test_path(
        "src/utils/testing_utils.rs"
    ));
    // "test" as substring in filename shouldn't match
    assert!(!DeadCodeDetector::is_test_path("src/contest.rs"));
}

#[test]
fn test_is_benchmark_path() {
    // Rust benches directory
    assert!(DeadCodeDetector::is_benchmark_path(
        "benches/parser_bench.rs"
    ));
    assert!(DeadCodeDetector::is_benchmark_path(
        "repotoire-cli/benches/graph.rs"
    ));

    // Benchmark directories
    assert!(DeadCodeDetector::is_benchmark_path(
        "benchmark/perf_test.py"
    ));
    assert!(DeadCodeDetector::is_benchmark_path(
        "src/benchmarks/throughput.rs"
    ));

    // Should NOT match regular files
    assert!(!DeadCodeDetector::is_benchmark_path("src/detectors/mod.rs"));
    assert!(!DeadCodeDetector::is_benchmark_path("src/bench_utils.rs"));
}

#[test]
fn test_is_pub_api_surface() {
    // Public functions in lib.rs
    assert!(DeadCodeDetector::is_pub_api_surface("src/lib.rs", true));
    assert!(DeadCodeDetector::is_pub_api_surface(
        "repotoire-cli/src/lib.rs",
        true
    ));

    // Public functions in mod.rs
    assert!(DeadCodeDetector::is_pub_api_surface(
        "src/detectors/mod.rs",
        true
    ));
    assert!(DeadCodeDetector::is_pub_api_surface(
        "src/graph/mod.rs",
        true
    ));

    // Non-exported functions in lib.rs should NOT be exempt
    assert!(!DeadCodeDetector::is_pub_api_surface("src/lib.rs", false));
    assert!(!DeadCodeDetector::is_pub_api_surface(
        "src/detectors/mod.rs",
        false
    ));

    // Regular files should NOT be exempt even if exported
    assert!(!DeadCodeDetector::is_pub_api_surface(
        "src/detectors/dead_code.rs",
        true
    ));
    assert!(!DeadCodeDetector::is_pub_api_surface("src/utils.rs", true));
}

// ── Graph flag exemption tests ──────────────────────────────────────

#[test]
fn test_exported_functions_are_skipped() {
    use crate::graph::store_models::{CodeNode, FLAG_IS_EXPORTED};
    use crate::graph::GraphStore;

    let store = GraphStore::in_memory();

    // Add an exported function with no callers
    let mut func = CodeNode::function("my_api", "src/lib.rs")
        .with_qualified_name("src/lib.rs::my_api");
    func.flags |= FLAG_IS_EXPORTED;
    store.add_node(func);

    // Add a non-exported function with no callers (should be flagged)
    let internal_func = CodeNode::function("internal_helper", "src/core.rs")
        .with_qualified_name("src/core.rs::internal_helper");
    store.add_node(internal_func);

    let ctx = make_test_analysis_ctx(&store);
    let detector = DeadCodeDetector::new();
    let findings = detector.find_dead_functions(&ctx);

    // Exported function should NOT appear in findings
    assert!(
        !findings.iter().any(|f| f.title.contains("my_api")),
        "Exported function should be skipped"
    );

    // Internal function should appear
    assert!(
        findings.iter().any(|f| f.title.contains("internal_helper")),
        "Non-exported function should be flagged"
    );
}

#[test]
fn test_decorated_functions_are_skipped() {
    use crate::graph::store_models::{CodeNode, FLAG_HAS_DECORATORS};
    use crate::graph::GraphStore;

    let store = GraphStore::in_memory();

    // Add a decorated function with no callers
    let mut func = CodeNode::function("route_handler", "src/routes.py")
        .with_qualified_name("src/routes.py::route_handler");
    func.flags |= FLAG_HAS_DECORATORS;
    store.add_node(func);

    let ctx = make_test_analysis_ctx(&store);
    let detector = DeadCodeDetector::new();
    let findings = detector.find_dead_functions(&ctx);

    assert!(
        !findings.iter().any(|f| f.title.contains("route_handler")),
        "Decorated function should be skipped"
    );
}

#[test]
fn test_address_taken_functions_are_skipped() {
    use crate::graph::store_models::{CodeNode, FLAG_ADDRESS_TAKEN};
    use crate::graph::GraphStore;

    let store = GraphStore::in_memory();

    // Add a function whose address is taken (used as callback)
    let mut func = CodeNode::function("my_callback", "src/events.rs")
        .with_qualified_name("src/events.rs::my_callback");
    func.flags |= FLAG_ADDRESS_TAKEN;
    store.add_node(func);

    let ctx = make_test_analysis_ctx(&store);
    let detector = DeadCodeDetector::new();
    let findings = detector.find_dead_functions(&ctx);

    assert!(
        !findings.iter().any(|f| f.title.contains("my_callback")),
        "Address-taken function should be skipped"
    );
}

#[test]
fn test_dunder_methods_are_skipped() {
    use crate::graph::store_models::CodeNode;
    use crate::graph::GraphStore;

    let store = GraphStore::in_memory();

    // Add a dunder method with no callers
    let func = CodeNode::function("__repr__", "src/model.py")
        .with_qualified_name("src/model.py::__repr__");
    store.add_node(func);

    let ctx = make_test_analysis_ctx(&store);
    let detector = DeadCodeDetector::new();
    let findings = detector.find_dead_functions(&ctx);

    assert!(
        !findings.iter().any(|f| f.title.contains("__repr__")),
        "Dunder method should be skipped"
    );
}

#[test]
fn test_test_functions_skipped_via_role() {
    use crate::detectors::function_context::FunctionContext as FuncCtx;
    use crate::graph::store_models::CodeNode;
    use crate::graph::GraphStore;

    let store = GraphStore::in_memory();

    let func = CodeNode::function("verify_output", "tests/test_api.py")
        .with_qualified_name("tests/test_api.py::verify_output");
    store.add_node(func);

    // Build context with function marked as Test role
    let mut functions = std::collections::HashMap::new();
    functions.insert(
        "tests/test_api.py::verify_output".to_string(),
        FuncCtx {
            qualified_name: "tests/test_api.py::verify_output".to_string(),
            name: "verify_output".to_string(),
            file_path: "tests/test_api.py".to_string(),
            module: "tests".to_string(),
            in_degree: 0,
            out_degree: 0,
            betweenness: 0.0,
            caller_modules: 0,
            callee_modules: 0,
            call_depth: 0,
            role: FunctionRole::Test,
            is_exported: false,
            is_test: true,
            is_in_utility_module: false,
            complexity: None,
            loc: 5,
        },
    );

    let ctx = make_test_analysis_ctx_with_functions(&store, functions);
    let detector = DeadCodeDetector::new();
    let findings = detector.find_dead_functions(&ctx);

    assert!(
        !findings.iter().any(|f| f.title.contains("verify_output")),
        "Test function should be skipped via role"
    );
}

#[test]
fn test_entry_point_role_skipped() {
    use crate::detectors::function_context::FunctionContext as FuncCtx;
    use crate::graph::store_models::CodeNode;
    use crate::graph::GraphStore;

    let store = GraphStore::in_memory();

    let func = CodeNode::function("app_entry", "src/main.py")
        .with_qualified_name("src/main.py::app_entry");
    store.add_node(func);

    let mut functions = std::collections::HashMap::new();
    functions.insert(
        "src/main.py::app_entry".to_string(),
        FuncCtx {
            qualified_name: "src/main.py::app_entry".to_string(),
            name: "app_entry".to_string(),
            file_path: "src/main.py".to_string(),
            module: "main".to_string(),
            in_degree: 0,
            out_degree: 3,
            betweenness: 0.0,
            caller_modules: 0,
            callee_modules: 2,
            call_depth: 0,
            role: FunctionRole::EntryPoint,
            is_exported: true,
            is_test: false,
            is_in_utility_module: false,
            complexity: Some(5),
            loc: 20,
        },
    );

    let ctx = make_test_analysis_ctx_with_functions(&store, functions);
    let detector = DeadCodeDetector::new();
    let findings = detector.find_dead_functions(&ctx);

    assert!(
        !findings.iter().any(|f| f.title.contains("app_entry")),
        "EntryPoint role should be skipped"
    );
}

#[test]
fn test_hub_role_skipped() {
    use crate::detectors::function_context::FunctionContext as FuncCtx;
    use crate::graph::store_models::CodeNode;
    use crate::graph::GraphStore;

    let store = GraphStore::in_memory();

    let func = CodeNode::function("dispatch", "src/core.rs")
        .with_qualified_name("src/core.rs::dispatch");
    store.add_node(func);

    let mut functions = std::collections::HashMap::new();
    functions.insert(
        "src/core.rs::dispatch".to_string(),
        FuncCtx {
            qualified_name: "src/core.rs::dispatch".to_string(),
            name: "dispatch".to_string(),
            file_path: "src/core.rs".to_string(),
            module: "core".to_string(),
            in_degree: 0,
            out_degree: 10,
            betweenness: 0.8,
            caller_modules: 0,
            callee_modules: 5,
            call_depth: 1,
            role: FunctionRole::Hub,
            is_exported: false,
            is_test: false,
            is_in_utility_module: false,
            complexity: Some(15),
            loc: 40,
        },
    );

    let ctx = make_test_analysis_ctx_with_functions(&store, functions);
    let detector = DeadCodeDetector::new();
    let findings = detector.find_dead_functions(&ctx);

    assert!(
        !findings.iter().any(|f| f.title.contains("dispatch")),
        "Hub role should be skipped"
    );
}

#[test]
fn test_hmm_handler_skipped() {
    use crate::graph::store_models::CodeNode;
    use crate::graph::GraphStore;

    let store = GraphStore::in_memory();

    let func = CodeNode::function("on_message", "src/events.py")
        .with_qualified_name("src/events.py::on_message");
    store.add_node(func);

    let mut hmm = std::collections::HashMap::new();
    hmm.insert(
        "src/events.py::on_message".to_string(),
        (context_hmm::FunctionContext::Handler, 0.85),
    );

    let ctx = make_test_analysis_ctx_with_hmm(&store, hmm);
    let detector = DeadCodeDetector::new();
    let findings = detector.find_dead_functions(&ctx);

    assert!(
        !findings.iter().any(|f| f.title.contains("on_message")),
        "HMM Handler should be skipped"
    );
}

#[test]
fn test_uncalled_function_is_flagged() {
    use crate::graph::store_models::CodeNode;
    use crate::graph::GraphStore;

    let store = GraphStore::in_memory();

    // A plain function with no callers, no flags, not in test path
    let func = CodeNode::function("unused_helper", "src/core.rs")
        .with_qualified_name("src/core.rs::unused_helper");
    store.add_node(func);

    let ctx = make_test_analysis_ctx(&store);
    let detector = DeadCodeDetector::new();
    let findings = detector.find_dead_functions(&ctx);

    assert!(
        findings.iter().any(|f| f.title.contains("unused_helper")),
        "Uncalled function with no exemptions should be flagged"
    );
}

#[test]
fn test_detector_name() {
    let detector = DeadCodeDetector::new();
    assert_eq!(detector.name(), "DeadCodeDetector");
}

// ── Test helpers ────────────────────────────────────────────────────

fn make_test_analysis_ctx(graph: &dyn crate::graph::GraphQuery) -> AnalysisContext<'_> {
    make_test_analysis_ctx_with_functions(graph, std::collections::HashMap::new())
}

fn make_test_analysis_ctx_with_functions(
    graph: &dyn crate::graph::GraphQuery,
    functions: crate::detectors::function_context::FunctionContextMap,
) -> AnalysisContext<'_> {
    make_test_analysis_ctx_full(graph, functions, std::collections::HashMap::new())
}

fn make_test_analysis_ctx_with_hmm(
    graph: &dyn crate::graph::GraphQuery,
    hmm: std::collections::HashMap<String, (context_hmm::FunctionContext, f64)>,
) -> AnalysisContext<'_> {
    make_test_analysis_ctx_full(graph, std::collections::HashMap::new(), hmm)
}

fn make_test_analysis_ctx_full(
    graph: &dyn crate::graph::GraphQuery,
    functions: crate::detectors::function_context::FunctionContextMap,
    hmm: std::collections::HashMap<String, (context_hmm::FunctionContext, f64)>,
) -> AnalysisContext<'_> {
    use crate::detectors::detector_context::DetectorContext;
    use crate::detectors::file_index::FileIndex;
    use crate::detectors::taint::centralized::CentralizedTaintResults;
    use std::collections::HashMap;
    use std::path::Path;
    use std::sync::Arc;

    let files = Arc::new(FileIndex::new(vec![]));
    let taint = Arc::new(CentralizedTaintResults {
        cross_function: HashMap::new(),
        intra_function: HashMap::new(),
    });

    let (det_ctx, _) = DetectorContext::build(graph, &[], None, Path::new("/repo"));

    AnalysisContext {
        graph,
        files,
        functions: Arc::new(functions),
        taint,
        detector_ctx: Arc::new(det_ctx),
        hmm_classifications: Arc::new(hmm),
        resolver: Arc::new(crate::calibrate::ThresholdResolver::default()),
    }
}
