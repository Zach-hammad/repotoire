use super::*;

#[test]
fn test_entry_points() {
    let detector = DeadCodeDetector::new();

    assert!(detector.is_entry_point("main"));
    assert!(detector.is_entry_point("__init__"));
    assert!(detector.is_entry_point("test_something"));
    assert!(!detector.is_entry_point("my_function"));
}

#[test]
fn test_magic_methods() {
    let detector = DeadCodeDetector::new();

    assert!(detector.is_magic_method("__str__"));
    assert!(detector.is_magic_method("__repr__"));
    assert!(!detector.is_magic_method("my_method"));
}

#[test]
fn test_should_filter() {
    let detector = DeadCodeDetector::new();

    // Magic methods
    assert!(detector.should_filter("__str__", false, false));

    // Entry points
    assert!(detector.should_filter("main", false, false));
    assert!(detector.should_filter("test_foo", false, false));

    // Public methods are no longer blanket-filtered (#15)
    assert!(!detector.should_filter("public_method", true, false));
    assert!(!detector.should_filter("_private_method", true, false));

    // Decorated methods (not standalone functions) are filtered
    assert!(detector.should_filter("any_func", true, true));
    assert!(!detector.should_filter("any_func", false, true)); // standalone decorated: not filtered

    // Patterns — trimmed list (#15)
    assert!(!detector.should_filter("load_config", false, false)); // removed from list
    assert!(detector.should_filter("to_dict", false, false));
    assert!(detector.should_filter("callback_handler", false, false));
    assert!(detector.should_filter("on_click", false, false));
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
fn test_callback_patterns() {
    let detector = DeadCodeDetector::new();

    // on* handlers (camelCase)
    assert!(detector.is_callback_pattern("onClick"));
    assert!(detector.is_callback_pattern("onSubmit"));
    assert!(detector.is_callback_pattern("onLoad"));
    assert!(detector.is_callback_pattern("onMouseOver"));

    // handle* functions (camelCase)
    assert!(detector.is_callback_pattern("handleClick"));
    assert!(detector.is_callback_pattern("handleSubmit"));
    assert!(detector.is_callback_pattern("handleChange"));

    // Should NOT match non-callback patterns
    assert!(!detector.is_callback_pattern("online")); // "on" but not camelCase callback
    assert!(!detector.is_callback_pattern("only"));
    assert!(!detector.is_callback_pattern("handler_setup")); // not camelCase handle*
    assert!(!detector.is_callback_pattern("regular_function"));

    // Should match explicit callback names
    assert!(detector.is_callback_pattern("my_callback"));
    assert!(detector.is_callback_pattern("event_handler"));
    assert!(detector.is_callback_pattern("click_listener"));
}

#[test]
fn test_framework_auto_load_patterns() {
    let detector = DeadCodeDetector::new();

    // Fastify autoload
    assert!(detector.is_framework_auto_load("src/plugins/auth.ts"));
    assert!(detector.is_framework_auto_load("plugins/db.js"));
    assert!(detector.is_framework_auto_load("/app/routes/api/users.ts"));

    // Event handlers directory
    assert!(detector.is_framework_auto_load("src/handlers/user-created.ts"));
    assert!(detector.is_framework_auto_load("handlers/order.js"));

    // CLI commands
    assert!(detector.is_framework_auto_load("src/commands/deploy.ts"));
    assert!(detector.is_framework_auto_load("commands/init.js"));

    // Migrations/seeds
    assert!(detector.is_framework_auto_load("db/migrations/001_create_users.ts"));
    assert!(detector.is_framework_auto_load("seeds/users.js"));

    // Should NOT match regular files
    assert!(!detector.is_framework_auto_load("src/utils/helpers.ts"));
    assert!(!detector.is_framework_auto_load("lib/core.js"));
}

#[test]
fn test_is_in_test_module() {
    let detector = DeadCodeDetector::new();

    // Rust test module files (tests.rs, test.rs)
    assert!(detector.is_in_test_module(
        "src/detectors/dead_code/tests.rs",
        "src/detectors/dead_code/tests.rs::test_entry_points:4"
    ));
    assert!(detector.is_in_test_module(
        "src/some_module/test.rs",
        "src/some_module/test.rs::helper:10"
    ));

    // Test directories
    assert!(detector.is_in_test_module(
        "tests/integration/test_api.rs",
        "tests/integration/test_api.rs::setup:5"
    ));
    assert!(detector.is_in_test_module(
        "src/tests/helpers.rs",
        "src/tests/helpers.rs::make_fixture:3"
    ));

    // Qualified name with ::tests:: segment (other languages / edge cases)
    assert!(detector.is_in_test_module(
        "src/lib.rs",
        "src/lib.rs::tests::helper_fn:100"
    ));
    assert!(detector.is_in_test_module(
        "src/lib.rs",
        "src/lib.rs::test::setup:50"
    ));

    // Should NOT match regular files
    assert!(!detector.is_in_test_module(
        "src/detectors/dead_code/mod.rs",
        "src/detectors/dead_code/mod.rs::detect:976"
    ));
    assert!(!detector.is_in_test_module(
        "src/utils/testing_utils.rs",
        "src/utils/testing_utils.rs::make_graph:10"
    ));
    // "test" as substring in filename shouldn't match (no trailing .rs boundary)
    assert!(!detector.is_in_test_module(
        "src/contest.rs",
        "src/contest.rs::solve:1"
    ));
}

#[test]
fn test_is_in_benchmark() {
    let detector = DeadCodeDetector::new();

    // Rust benches directory
    assert!(detector.is_in_benchmark("benches/parser_bench.rs"));
    assert!(detector.is_in_benchmark("repotoire-cli/benches/graph.rs"));

    // Benchmark directories
    assert!(detector.is_in_benchmark("benchmark/perf_test.py"));
    assert!(detector.is_in_benchmark("src/benchmarks/throughput.rs"));

    // Should NOT match regular files
    assert!(!detector.is_in_benchmark("src/detectors/mod.rs"));
    assert!(!detector.is_in_benchmark("src/bench_utils.rs"));
}

#[test]
fn test_is_pub_api_surface() {
    let detector = DeadCodeDetector::new();

    // Public functions in lib.rs
    assert!(detector.is_pub_api_surface("src/lib.rs", true));
    assert!(detector.is_pub_api_surface("repotoire-cli/src/lib.rs", true));

    // Public functions in mod.rs
    assert!(detector.is_pub_api_surface("src/detectors/mod.rs", true));
    assert!(detector.is_pub_api_surface("src/graph/mod.rs", true));

    // Non-exported functions in lib.rs should NOT be exempt
    assert!(!detector.is_pub_api_surface("src/lib.rs", false));
    assert!(!detector.is_pub_api_surface("src/detectors/mod.rs", false));

    // Regular files should NOT be exempt even if exported
    assert!(!detector.is_pub_api_surface("src/detectors/dead_code.rs", true));
    assert!(!detector.is_pub_api_surface("src/utils.rs", true));
}
