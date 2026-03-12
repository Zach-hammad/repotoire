//! Lazy Class detector - identifies underutilized classes
//!
//! Graph-aware detection: checks not just method count but whether those methods
//! are actually CALLED. A class with few methods that are heavily used is not lazy.
//!
//! Detection criteria:
//! - Few methods (≤3)
//! - Small total LOC
//! - Low usage (methods rarely called from outside)

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::PathBuf;
use tracing::{debug, info};

/// Thresholds for lazy class detection
#[derive(Debug, Clone)]
pub struct LazyClassThresholds {
    /// Maximum methods for a class to be considered potentially lazy
    pub max_methods: usize,
    /// Maximum LOC for lazy class
    pub max_loc: usize,
    /// Minimum callers to consider a class "actively used"
    pub min_callers_to_skip: usize,
}

impl Default for LazyClassThresholds {
    fn default() -> Self {
        Self {
            max_methods: 3,
            max_loc: 50,
            min_callers_to_skip: 5, // If 5+ external callers, not lazy
        }
    }
}

/// Patterns to exclude from lazy class detection (pre-lowercased for fast matching)
const EXCLUDE_PATTERNS: &[&str] = &[
    // Design patterns (intentionally small)
    "adapter",
    "wrapper",
    "proxy",
    "decorator",
    "facade",
    "bridge",
    // Data classes (supposed to be simple)
    "config",
    "settings",
    "options",
    "dto",
    "entity",
    "model",
    "schema",
    "request",
    "response",
    "params",
    "args",
    "event",
    "message",
    // Exceptions
    "exception",
    "error",
    // Base/abstract (extended elsewhere)
    "base",
    "abstract",
    "interface",
    "mixin",
    "protocol",
    "trait",
    // Test infrastructure
    "test",
    "mock",
    "stub",
    "fake",
    "fixture",
    // Framework conventions
    "serializer",
    "validator",
    "handler",
    "listener",
    "observer",
    "factory",
    "builder",
    "provider",
    "service",
    // ORM patterns (intentionally small - Strategy pattern)
    "lookup",
    "transform",
    "descriptor",
    "attribute",
    "field",
    "constraint",
    "index",
    "expression",
    "widget",
    "migration",
    "command",
    "middleware",
    // Rust-specific (idiomatic small types)
    "phantom",  // PhantomData marker types
    "marker",   // Marker types (zero-sized, trait-only)
];

/// Detects classes that do minimal work and aren't used much
pub struct LazyClassDetector {
    #[allow(dead_code)] // Stored for future config access
    config: DetectorConfig,
    thresholds: LazyClassThresholds,
}

impl LazyClassDetector {
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            thresholds: LazyClassThresholds::default(),
        }
    }

    #[allow(dead_code)] // Builder pattern method
    pub fn with_config(config: DetectorConfig) -> Self {
        let thresholds = LazyClassThresholds {
            max_methods: config.get_option_or("max_methods", 3),
            max_loc: config.get_option_or("max_loc", 50),
            min_callers_to_skip: config.get_option_or("min_callers_to_skip", 5),
        };
        Self { config, thresholds }
    }

    /// Check if class name matches an exclusion pattern.
    /// Patterns are pre-lowercased; we only lowercase the class name once.
    fn should_exclude(&self, class_name: &str) -> bool {
        let lower = class_name.to_lowercase();
        EXCLUDE_PATTERNS.iter().any(|p| lower.contains(p))
    }

    /// Check if a file path is a Rust source file.
    fn is_rust_file(path: &str) -> bool {
        path.ends_with(".rs")
    }

    /// Count methods belonging to a Rust struct/enum via `impl` blocks.
    ///
    /// Rust idiomatically spreads methods across multiple `impl` blocks (direct
    /// impls, trait impls like Display, Debug, Default, Serialize, etc.). These
    /// are separate Function nodes in the graph whose qualified names contain
    /// `impl<TypeName>` or `impl<Trait for TypeName>`. This method counts them
    /// so the detector can make an informed decision about whether the type is
    /// truly "lazy" or just idiomatic Rust.
    fn count_rust_impl_methods(
        file_funcs: &[&crate::graph::store_models::CodeNode],
        type_name: &str,
        interner: &crate::graph::interner::StringInterner,
    ) -> usize {
        // Match patterns like:
        //   path::impl<TypeName>::method:line
        //   path::impl<Trait for TypeName>::method:line
        let impl_direct = format!("impl<{}>", type_name);
        let impl_trait_suffix = format!(" for {}>", type_name);

        file_funcs
            .iter()
            .filter(|f| {
                let qn = f.qn(interner);
                qn.contains(&impl_direct) || qn.contains(&impl_trait_suffix)
            })
            .count()
    }

    /// Count unique external callers of a class's methods.
    ///
    /// Uses zero-copy `count_external_callers_of()` on each method — avoids
    /// cloning CodeNodes from `get_callers()`.  Pre-check with `call_fan_in()`
    /// skips methods with 0 callers entirely (~60% of methods in CPython).
    fn count_external_callers(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        class: &crate::graph::CodeNode,
        methods: &[&crate::graph::store_models::CodeNode],
    ) -> usize {
        let i = graph.interner();
        let mut total = 0usize;
        for method in methods {
            // Quick check: skip methods with 0 callers (avoids index lookup)
            if graph.call_fan_in(method.qn(i)) == 0 {
                continue;
            }
            total += graph.count_external_callers_of(
                method.qn(i),
                class.path(i),
                class.line_start,
                class.line_end,
            );
        }
        total
    }

    /// Calculate usage ratio (callers per method)
    #[allow(dead_code)] // Helper for graph-based detection
    fn calculate_usage_ratio(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        class: &crate::graph::CodeNode,
        methods: &[&crate::graph::store_models::CodeNode],
        method_count: usize,
    ) -> f64 {
        let _i = graph.interner();
        if method_count == 0 {
            return 0.0;
        }

        let callers = self.count_external_callers(graph, class, methods);
        callers as f64 / method_count as f64
    }
}

impl Default for LazyClassDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for LazyClassDetector {
    fn name(&self) -> &'static str {
        "LazyClassDetector"
    }

    fn description(&self) -> &'static str {
        "Detects classes with few methods that aren't used much"
    }

    fn category(&self) -> &'static str {
        "design"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, _files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let i = graph.interner();
        let mut findings = Vec::new();
        let classes = graph.get_classes_shared();

        // Pre-filter: collect candidate classes that pass cheap checks.
        // Order: cheapest checks first (numeric/string), expensive checks last (content cache).
        // Per-file cache for content classifier results (avoids re-checking the same file).
        let mut file_excluded: rustc_hash::FxHashMap<&str, bool> = rustc_hash::FxHashMap::default();
        let mut candidates: Vec<&crate::graph::store_models::CodeNode> = Vec::new();
        for class in classes.iter() {
            // --- Cheapest checks first: numeric fields ---
            let method_count = class.get_i64("methodCount").unwrap_or(0) as usize;
            let loc = class.loc() as usize;

            if method_count > self.thresholds.max_methods || loc > self.thresholds.max_loc {
                continue;
            }
            if loc < 5 {
                continue;
            }

            // --- String checks (no allocation, no I/O) ---
            if class.qn(i).contains("::interface::")
                || class.qn(i).contains("::type::")
            {
                continue;
            }

            // Rust: traits are already excluded by "trait" in EXCLUDE_PATTERNS via
            // the class name, but also skip by qualified name pattern (::trait::)
            // which the Rust parser uses for trait definitions.
            // Rust enums with methods are idiomatic, not lazy — skip them.
            if Self::is_rust_file(class.path(i)) {
                // Qualified name pattern: "path::trait::TraitName:line"
                if class.qn(i).contains("::trait::") {
                    continue;
                }
            }

            if self.should_exclude(class.node_name(i)) {
                continue;
            }

            // Skip test fixture/model classes (path-based, cheap)
            {
                let lower_path = class.path(i).to_lowercase();
                if lower_path.contains("/test/") || lower_path.contains("/tests/")
                    || lower_path.contains("/__tests__/") || lower_path.contains("/spec/")
                    || lower_path.contains("/fixtures/")
                    || lower_path.contains("test_") || lower_path.contains("_test.")
                    || lower_path.starts_with("tests/")
                    || lower_path.starts_with("test/")
                    || lower_path.starts_with("__tests__/")
                {
                    continue;
                }
            }

            // --- Expensive: per-file bundled/minified/fixture check (cached) ---
            let excluded = *file_excluded
                .entry(class.path(i))
                .or_insert_with(|| {
                    if crate::detectors::content_classifier::is_likely_bundled_path(class.path(i)) {
                        return true;
                    }
                    if let Some(content) =
                        crate::cache::global_cache().content(std::path::Path::new(class.path(i)))
                    {
                        if crate::detectors::content_classifier::is_bundled_code(&content)
                            || crate::detectors::content_classifier::is_minified_code(&content)
                            || crate::detectors::content_classifier::is_fixture_code(
                                class.path(i),
                                &content,
                            )
                        {
                            return true;
                        }
                    }
                    false
                });
            if excluded {
                continue;
            }

            candidates.push(class);
        }

        // Group candidates by file to call get_functions_in_file() once per file
        // instead of per class (13K → ~3.4K calls for CPython).
        let mut by_file: std::collections::HashMap<&str, Vec<&crate::graph::store_models::CodeNode>> =
            std::collections::HashMap::new();
        for class in &candidates {
            by_file.entry(class.path(i)).or_default().push(class);
        }

        for (file_path, file_classes) in &by_file {
            let file_funcs = graph.get_functions_in_file(file_path);
            let is_rust = Self::is_rust_file(file_path);

            // For Rust files, pre-collect references to file_funcs once for
            // efficient impl-block method counting across all classes in the file.
            let file_func_refs: Option<Vec<&crate::graph::store_models::CodeNode>> =
                if is_rust { Some(file_funcs.iter().collect()) } else { None };

            for class in file_classes {
                // --- Rust-specific: count impl-block methods ---
                // Rust structs/enums have methods in separate `impl` blocks outside
                // the struct's line range. The parser sets methodCount=0 on the struct
                // node itself because no methods live inside `struct Foo { ... }`.
                // We must count methods from `impl Foo` and `impl Trait for Foo` blocks
                // to get the real method count.
                if let Some(ref func_refs) = file_func_refs {
                    let impl_method_count =
                        Self::count_rust_impl_methods(func_refs, class.node_name(i), i);

                    // Rust threshold: a struct/enum with 2+ impl-block methods is
                    // not lazy — it has real functionality spread across impl blocks.
                    // This is higher than the default max_methods=3 check because
                    // Rust idiomatically uses many small trait impls (Display, Debug,
                    // Default, From, etc.) that each add a method.
                    if impl_method_count >= 2 {
                        debug!(
                            "Skipping Rust type {} - has {} impl-block methods",
                            class.node_name(i), impl_method_count
                        );
                        continue;
                    }
                }

                // Find methods belonging to this class from the shared file functions
                let methods: Vec<_> = file_funcs
                    .iter()
                    .filter(|f| f.line_start >= class.line_start && f.line_end <= class.line_end)
                    .collect();

                // Zero-copy external caller count (no CodeNode cloning)
                let external_callers = self.count_external_callers(graph, class, &methods);

                if external_callers >= self.thresholds.min_callers_to_skip {
                    debug!(
                        "Skipping {} - has {} external callers (threshold: {})",
                        class.node_name(i), external_callers, self.thresholds.min_callers_to_skip
                    );
                    continue;
                }

                let method_count = class.get_i64("methodCount").unwrap_or(0) as usize;
                let loc = class.loc() as usize;

                let severity = if external_callers == 0 {
                    Severity::Medium
                } else {
                    Severity::Low
                };

                let usage_note = if external_callers == 0 {
                    "No external code calls this class's methods.".to_string()
                } else {
                    format!(
                        "Only {} external caller(s) use this class.",
                        external_callers
                    )
                };

                findings.push(Finding {
                    id: String::new(),
                    detector: "LazyClassDetector".to_string(),
                    severity,
                    title: format!("Lazy Class: {}", class.node_name(i)),
                    description: format!(
                        "Class '{}' has only {} method(s) and {} LOC. {}\n\n\
                         Consider inlining this class's functionality or expanding it.",
                        class.node_name(i), method_count, loc, usage_note
                    ),
                    affected_files: vec![class.path(i).to_string().into()],
                    line_start: Some(class.line_start),
                    line_end: Some(class.line_end),
                    suggested_fix: Some(
                        "Options:\n\
                         1. Inline functionality into callers\n\
                         2. Merge with a related class\n\
                         3. Convert to standalone functions\n\
                         4. If intentional, add documentation explaining the design choice"
                            .to_string(),
                    ),
                    estimated_effort: Some("Small (30 min)".to_string()),
                    category: Some("design".to_string()),
                    cwe_id: None,
                    why_it_matters: Some(
                        "Lazy classes add cognitive overhead without providing value. \
                         They increase indirection and make code harder to navigate."
                            .to_string(),
                    ),
                    ..Default::default()
                });
            }
        }

        info!("LazyClassDetector found {} findings", findings.len());
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeEdge, CodeNode, GraphStore};

    #[test]
    fn test_should_exclude() {
        let detector = LazyClassDetector::new();

        assert!(detector.should_exclude("UserAdapter"));
        assert!(detector.should_exclude("DatabaseConfig"));
        assert!(detector.should_exclude("CustomException"));
        assert!(detector.should_exclude("BaseClass"));
        assert!(detector.should_exclude("TestHelper"));

        assert!(!detector.should_exclude("OrderProcessor"));
        assert!(!detector.should_exclude("Calculator"));
    }

    #[test]
    fn test_skip_heavily_used_class() {
        let graph = GraphStore::in_memory();

        // Create a small class
        graph.add_node(
            CodeNode::class("SmallClass", "src/small.py")
                .with_qualified_name("small::SmallClass")
                .with_lines(1, 20)
                .with_property("methodCount", 2i64),
        );

        // Add a method
        graph.add_node(
            CodeNode::function("do_thing", "src/small.py")
                .with_qualified_name("small::SmallClass::do_thing")
                .with_lines(5, 10),
        );

        // Add many callers from outside
        for i in 0..10 {
            let caller_name = format!("caller_{}", i);
            graph.add_node(
                CodeNode::function(&caller_name, "src/callers.py")
                    .with_qualified_name(&format!("callers::{}", caller_name))
                    .with_lines(i * 10, i * 10 + 5),
            );
            graph.add_edge_by_name(
                &format!("callers::{}", caller_name),
                "small::SmallClass::do_thing",
                CodeEdge::calls(),
            );
        }

        let detector = LazyClassDetector::new();
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&graph, &empty_files).expect("detection should succeed");

        // Should NOT flag - class has many callers
        assert!(
            findings.is_empty(),
            "Heavily-used class should not be flagged as lazy"
        );
    }

    #[test]
    fn test_flag_unused_class() {
        let graph = GraphStore::in_memory();

        // Create a small class with no callers
        graph.add_node(
            CodeNode::class("UnusedClass", "src/unused.py")
                .with_qualified_name("unused::UnusedClass")
                .with_lines(1, 20)
                .with_property("methodCount", 1i64),
        );

        graph.add_node(
            CodeNode::function("lonely_method", "src/unused.py")
                .with_qualified_name("unused::UnusedClass::lonely_method")
                .with_lines(5, 15),
        );

        let detector = LazyClassDetector::new();
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&graph, &empty_files).expect("detection should succeed");

        // Should flag - unused class
        assert_eq!(findings.len(), 1);
        assert!(findings[0].title.contains("UnusedClass"));
    }

    // --- Rust-specific tests ---

    #[test]
    fn test_rust_struct_with_impl_methods_not_flagged() {
        let graph = GraphStore::in_memory();

        // Rust struct: small definition (just fields), no methods inside struct body
        graph.add_node(
            CodeNode::class("GraphStore", "src/graph/store.rs")
                .with_qualified_name("src/graph/store.rs::GraphStore:10")
                .with_lines(10, 18) // struct definition only
                .with_property("methodCount", 0i64),
        );

        // impl GraphStore { fn new(), fn add_node(), fn get_node() }
        // These are outside the struct's line range, in a separate impl block
        graph.add_node(
            CodeNode::function("new", "src/graph/store.rs")
                .with_qualified_name("src/graph/store.rs::impl<GraphStore>::new:25")
                .with_lines(25, 30),
        );
        graph.add_node(
            CodeNode::function("add_node", "src/graph/store.rs")
                .with_qualified_name("src/graph/store.rs::impl<GraphStore>::add_node:32")
                .with_lines(32, 45),
        );
        graph.add_node(
            CodeNode::function("get_node", "src/graph/store.rs")
                .with_qualified_name("src/graph/store.rs::impl<GraphStore>::get_node:47")
                .with_lines(47, 55),
        );

        let detector = LazyClassDetector::new();
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&graph, &empty_files).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Rust struct with 3 impl-block methods should NOT be flagged as lazy, got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_rust_struct_with_trait_impls_not_flagged() {
        let graph = GraphStore::in_memory();

        // Rust struct with trait implementations
        graph.add_node(
            CodeNode::class("Finding", "src/models.rs")
                .with_qualified_name("src/models.rs::Finding:5")
                .with_lines(5, 15)
                .with_property("methodCount", 0i64),
        );

        // impl Display for Finding
        graph.add_node(
            CodeNode::function("fmt", "src/models.rs")
                .with_qualified_name("src/models.rs::impl<Display for Finding>::fmt:20")
                .with_lines(20, 30),
        );

        // impl Default for Finding
        graph.add_node(
            CodeNode::function("default", "src/models.rs")
                .with_qualified_name("src/models.rs::impl<Default for Finding>::default:35")
                .with_lines(35, 45),
        );

        let detector = LazyClassDetector::new();
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&graph, &empty_files).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Rust struct with 2 trait impl methods should NOT be flagged as lazy, got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_rust_struct_truly_lazy_still_flagged() {
        let graph = GraphStore::in_memory();

        // A truly lazy Rust struct: small, only 1 impl method, no callers.
        // Name chosen to NOT match any EXCLUDE_PATTERNS (avoid "wrapper", "config", etc.)
        graph.add_node(
            CodeNode::class("TinyHolder", "src/holder.rs")
                .with_qualified_name("src/holder.rs::TinyHolder:1")
                .with_lines(1, 10)
                .with_property("methodCount", 0i64),
        );

        // Only 1 impl method — below the Rust threshold of 2
        graph.add_node(
            CodeNode::function("inner", "src/holder.rs")
                .with_qualified_name("src/holder.rs::impl<TinyHolder>::inner:15")
                .with_lines(15, 18),
        );

        let detector = LazyClassDetector::new();
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&graph, &empty_files).expect("detection should succeed");

        // Should still flag — only 1 impl method, truly underutilized
        assert_eq!(
            findings.len(),
            1,
            "Rust struct with only 1 impl method should be flagged"
        );
        assert!(findings[0].title.contains("TinyHolder"));
    }

    #[test]
    fn test_rust_exclusion_patterns() {
        let detector = LazyClassDetector::new();

        // Rust-specific exclusions
        assert!(detector.should_exclude("PhantomType"));
        assert!(detector.should_exclude("MarkerStruct"));

        // "error" was already excluded before Rust changes
        assert!(detector.should_exclude("ParseError"));
    }

    #[test]
    fn test_rust_trait_skipped_by_qn() {
        let graph = GraphStore::in_memory();

        // Rust trait: qualified name has ::trait:: pattern
        graph.add_node(
            CodeNode::class("GraphQuery", "src/graph/traits.rs")
                .with_qualified_name("src/graph/traits.rs::trait::GraphQuery:10")
                .with_lines(10, 20)
                .with_property("methodCount", 0i64),
        );

        let detector = LazyClassDetector::new();
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&graph, &empty_files).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Rust trait should NOT be flagged as lazy class"
        );
    }

    #[test]
    fn test_python_class_unaffected_by_rust_logic() {
        let graph = GraphStore::in_memory();

        // Python class with few methods — should still be flagged normally
        graph.add_node(
            CodeNode::class("TinyHelper", "src/helpers.py")
                .with_qualified_name("helpers::TinyHelper")
                .with_lines(1, 15)
                .with_property("methodCount", 1i64),
        );

        graph.add_node(
            CodeNode::function("do_thing", "src/helpers.py")
                .with_qualified_name("helpers::TinyHelper::do_thing")
                .with_lines(3, 10),
        );

        let detector = LazyClassDetector::new();
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&graph, &empty_files).expect("detection should succeed");

        assert_eq!(
            findings.len(),
            1,
            "Python class should still be flagged normally (Rust logic doesn't apply)"
        );
        assert!(findings[0].title.contains("TinyHelper"));
    }

    #[test]
    fn test_is_rust_file() {
        assert!(LazyClassDetector::is_rust_file("src/main.rs"));
        assert!(LazyClassDetector::is_rust_file("repotoire-cli/src/detectors/lazy_class.rs"));
        assert!(!LazyClassDetector::is_rust_file("src/helpers.py"));
        assert!(!LazyClassDetector::is_rust_file("src/utils.ts"));
        assert!(!LazyClassDetector::is_rust_file("src/app.rs.bak")); // not a .rs file
    }

    #[test]
    fn test_count_rust_impl_methods() {
        let graph = GraphStore::in_memory();
        let i = graph.interner();

        // Add functions with impl-block qualified names
        let funcs = vec![
            CodeNode::function("new", "src/lib.rs")
                .with_qualified_name("src/lib.rs::impl<Foo>::new:10")
                .with_lines(10, 15),
            CodeNode::function("fmt", "src/lib.rs")
                .with_qualified_name("src/lib.rs::impl<Display for Foo>::fmt:20")
                .with_lines(20, 25),
            CodeNode::function("default", "src/lib.rs")
                .with_qualified_name("src/lib.rs::impl<Default for Foo>::default:30")
                .with_lines(30, 35),
            // Method for a DIFFERENT type — should not be counted
            CodeNode::function("bar_method", "src/lib.rs")
                .with_qualified_name("src/lib.rs::impl<Bar>::bar_method:40")
                .with_lines(40, 45),
            // Standalone function — should not be counted
            CodeNode::function("standalone", "src/lib.rs")
                .with_qualified_name("src/lib.rs::standalone:50")
                .with_lines(50, 55),
        ];
        for f in &funcs {
            graph.add_node(f.clone());
        }

        // Retrieve them back so we have the compact CodeNode form
        let file_funcs = graph.get_functions_in_file("src/lib.rs");
        let file_func_refs: Vec<&crate::graph::store_models::CodeNode> =
            file_funcs.iter().collect();

        let count = LazyClassDetector::count_rust_impl_methods(&file_func_refs, "Foo", i);
        assert_eq!(count, 3, "Should count 3 methods for Foo (new, fmt, default)");

        let count_bar = LazyClassDetector::count_rust_impl_methods(&file_func_refs, "Bar", i);
        assert_eq!(count_bar, 1, "Should count 1 method for Bar");

        let count_none = LazyClassDetector::count_rust_impl_methods(&file_func_refs, "Baz", i);
        assert_eq!(count_none, 0, "Should count 0 methods for non-existent type");
    }
}
