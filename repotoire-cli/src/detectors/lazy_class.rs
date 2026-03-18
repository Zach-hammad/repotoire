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
use crate::detectors::function_context::FunctionRole;
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

/// Patterns to exclude from lazy class detection (pre-lowercased for fast matching).
///
/// Trimmed from 76 entries to ~30: patterns now covered by role analysis
/// (betweenness check, FunctionRole::Hub, HMM Handler classification) have
/// been removed. Remaining patterns are semantic/structural and not
/// reliably detectable via graph metrics.
const EXCLUDE_PATTERNS: &[&str] = &[
    // Data containers (supposed to be simple)
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
    // DB migrations
    "migration",
    // Rust-specific (idiomatic small types)
    "phantom",  // PhantomData marker types
    "marker",   // Marker types (zero-sized, trait-only)
    // Python data containers
    "namedtuple",   // collections.namedtuple / typing.NamedTuple
    "dataclass",    // @dataclass classes (backup for QN-based check)
    // Java/C# patterns
    "record",       // Java 16+ records, C# records (data carriers)
    "enum",         // Java/C# enums with few methods are idiomatic
    // C# patterns
    "extension",    // C# extension method classes (helpers by design)
    "partial",      // C# partial classes (methods split across files)
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

    /// Check if a file path is a Go source file.
    fn is_go_file(path: &str) -> bool {
        path.ends_with(".go")
    }

    /// Check if a file path is a Java source file.
    #[allow(dead_code)] // Available for future Java-specific logic
    fn is_java_file(path: &str) -> bool {
        path.ends_with(".java")
    }

    /// Check if a file path is a C# source file.
    fn is_cs_file(path: &str) -> bool {
        path.ends_with(".cs")
    }

    /// Check if a file path is a Python source file.
    fn is_python_file(path: &str) -> bool {
        path.ends_with(".py")
    }

    /// Check if a qualified name indicates a Java/C# record (data carrier).
    /// Parser emits `path::record::Name:line` for records.
    fn is_record_qn(qn: &str) -> bool {
        qn.contains("::record::")
    }

    /// Check if a qualified name indicates a Java/C# enum.
    /// Parser emits `path::enum::Name:line` for enums.
    fn is_enum_qn(qn: &str) -> bool {
        qn.contains("::enum::")
    }

    /// Check if a qualified name indicates a C# struct (value type).
    /// Parser emits `path::struct::Name:line` for C# structs.
    fn is_cs_struct_qn(qn: &str) -> bool {
        qn.contains("::struct::")
    }

    /// Check if a C# class name follows the interface naming convention (IFoo).
    /// C# interfaces conventionally start with "I" followed by an uppercase letter.
    /// The parser already emits `::interface::` for explicit interface declarations,
    /// but some codebases define interface-like abstract classes with I-prefix names.
    fn is_cs_interface_name(name: &str) -> bool {
        let bytes = name.as_bytes();
        bytes.len() >= 2
            && bytes[0] == b'I'
            && bytes[1].is_ascii_uppercase()
    }

    /// Count methods belonging to a Rust struct/enum via `impl` blocks.
    ///
    /// Rust idiomatically spreads methods across multiple `impl` blocks (direct
    /// impls, trait impls like Display, Debug, Default, Serialize, etc.). These
    /// are separate Function nodes in the graph whose qualified names contain
    /// `impl<TypeName>` or `impl<Trait for TypeName>`. This method counts them
    /// so the detector can make an informed decision about whether the type is
    /// truly "lazy" or just idiomatic Rust.
    ///
    /// Handles generic types correctly: `impl<Foo<'g>>` matches type name `Foo`
    /// by checking that the character after the type name is `>` (end of impl)
    /// or `<` (start of generic parameters).
    fn count_rust_impl_methods(
        file_funcs: &[&crate::graph::store_models::CodeNode],
        type_name: &str,
        interner: &crate::graph::interner::StringInterner,
    ) -> usize {
        let impl_prefix = format!("impl<{}", type_name);
        let trait_infix = format!(" for {}", type_name);

        file_funcs
            .iter()
            .filter(|f| {
                let qn = f.qn(interner);
                Self::qn_matches_type(qn, &impl_prefix, type_name.len())
                    || Self::qn_matches_type(qn, &trait_infix, type_name.len())
            })
            .count()
    }

    /// Count distinct trait implementations for a Rust type.
    ///
    /// Counts unique `impl<Trait for TypeName>` patterns in qualified names.
    /// This measures the type's "contract fulfillment" — how many traits it
    /// manually implements.
    fn count_rust_trait_impls(
        file_funcs: &[&crate::graph::store_models::CodeNode],
        type_name: &str,
        interner: &crate::graph::interner::StringInterner,
    ) -> usize {
        let trait_infix = format!(" for {}", type_name);

        let mut trait_names: HashSet<&str> = HashSet::new();

        for f in file_funcs {
            let qn = f.qn(interner);
            if let Some(pos) = qn.find(&trait_infix) {
                let after = pos + trait_infix.len();
                if let Some(&ch) = qn.as_bytes().get(after) {
                    if ch == b'>' || ch == b'<' {
                        // Extract trait name: "impl<TraitName for TypeName>"
                        if let Some(impl_start) = qn[..pos].rfind("impl<") {
                            let trait_start = impl_start + 5; // len("impl<")
                            let trait_name = &qn[trait_start..pos];
                            trait_names.insert(trait_name);
                        }
                    }
                }
            }
        }

        trait_names.len()
    }

    /// Check if a QN contains a pattern followed by `>` or `<` at the right position.
    /// This correctly handles generic types like `Foo<'g>` where the type name
    /// is followed by `<` (generic params) instead of `>` (end of impl block).
    fn qn_matches_type(qn: &str, pattern: &str, _type_name_len: usize) -> bool {
        if let Some(pos) = qn.find(pattern) {
            let after = pos + pattern.len();
            if let Some(&ch) = qn.as_bytes().get(after) {
                return ch == b'>' || ch == b'<';
            }
        }
        false
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

    /// Core detection logic.
    ///
    /// When `analysis_ctx` is `Some`, enables enhanced checks:
    /// - Role-based exemptions (FunctionRole::Hub, high betweenness)
    /// - HMM-based handler classification gating
    /// - Adaptive thresholds from ThresholdResolver
    fn detect_inner(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        analysis_ctx: Option<&crate::detectors::analysis_context::AnalysisContext<'_>>,
    ) -> Result<Vec<Finding>> {
        let i = graph.interner();
        let mut findings = Vec::new();
        let classes = graph.get_classes_shared();

        // Adaptive threshold: adapts to codebase's class size distribution
        let adaptive_max_methods = analysis_ctx
            .map(|ctx| {
                ctx.resolver.warn_usize(
                    crate::calibrate::MetricKind::ClassMethodCount,
                    self.thresholds.max_methods,
                )
            })
            .unwrap_or(self.thresholds.max_methods);

        // Pre-filter: collect candidate classes that pass cheap checks.
        // Order: cheapest checks first (numeric/string), expensive checks last (content cache).
        // Per-file cache for content classifier results (avoids re-checking the same file).
        let mut file_excluded: rustc_hash::FxHashMap<&str, bool> = rustc_hash::FxHashMap::default();
        let mut candidates: Vec<&crate::graph::store_models::CodeNode> = Vec::new();
        for class in classes.iter() {
            // --- Cheapest checks first: numeric fields ---
            let method_count = class.get_i64("methodCount").unwrap_or(0) as usize;
            let loc = class.loc() as usize;

            if method_count > adaptive_max_methods || loc > self.thresholds.max_loc {
                continue;
            }
            if loc < 5 {
                continue;
            }

            // --- String checks (no allocation, no I/O) ---
            let qn = class.qn(i);
            let file_path = class.path(i);

            // Skip interfaces (Go, Java, C#), Go type aliases — all languages
            if qn.contains("::interface::") || qn.contains("::type::") {
                continue;
            }

            // Skip Java/C# records — data carriers by design (Java 16+, C# 9+)
            if Self::is_record_qn(qn) {
                continue;
            }

            // Skip Java/C# enums — enums with few methods are idiomatic
            if Self::is_enum_qn(qn) {
                continue;
            }

            // Skip C# structs — value types with few methods are idiomatic
            if Self::is_cs_file(file_path) && Self::is_cs_struct_qn(qn) {
                continue;
            }

            // C#: skip I-prefixed names (interface naming convention)
            // The parser marks explicit `interface` declarations with ::interface::
            // but abstract classes following the IFoo convention are interface-like.
            if Self::is_cs_file(file_path) && Self::is_cs_interface_name(class.node_name(i)) {
                continue;
            }

            // Go: types with 1-2 methods are idiomatic (small, focused interfaces
            // and structs following SRP). Only skip when LOC is also small.
            if Self::is_go_file(file_path) {
                let method_count = class.get_i64("methodCount").unwrap_or(0) as usize;
                if method_count <= 2 {
                    debug!(
                        "Skipping Go type {} - has {} methods (idiomatic small type)",
                        class.node_name(i), method_count
                    );
                    continue;
                }
            }

            // Python: classes with decorators and 0-1 methods are likely @dataclass,
            // @attrs, or similar data containers. The `has_decorators` flag is set
            // by the parser for any decorated class.
            if Self::is_python_file(file_path) && class.has_decorators() {
                let method_count = class.get_i64("methodCount").unwrap_or(0) as usize;
                if method_count <= 1 {
                    debug!(
                        "Skipping Python decorated class {} - likely @dataclass/data container ({} methods)",
                        class.node_name(i), method_count
                    );
                    continue;
                }
            }

            // Rust: traits are already excluded by "trait" in EXCLUDE_PATTERNS via
            // the class name, but also skip by qualified name pattern (::trait::)
            // which the Rust parser uses for trait definitions.
            if Self::is_rust_file(file_path) {
                // Qualified name pattern: "path::trait::TraitName:line"
                if qn.contains("::trait::") {
                    continue;
                }
            }

            if self.should_exclude(class.node_name(i)) {
                continue;
            }

            // Skip test fixture/model classes (path-based, cheap)
            {
                let lower_path = file_path.to_lowercase();
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
                .entry(file_path)
                .or_insert_with(|| {
                    if crate::detectors::content_classifier::is_likely_bundled_path(file_path) {
                        return true;
                    }
                    if let Some(content) =
                        crate::cache::global_cache().content(std::path::Path::new(file_path))
                    {
                        if crate::detectors::content_classifier::is_bundled_code(&content)
                            || crate::detectors::content_classifier::is_minified_code(&content)
                            || crate::detectors::content_classifier::is_fixture_code(
                                file_path,
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
                // --- Rust-specific: multi-dimensional type evaluation ---
                // Rust structs/enums provide value through multiple dimensions:
                // 1. Data structuring (fields) — what the type IS
                // 2. Behavior (impl methods) — what the type DOES
                // 3. Type contracts (trait impls) — what obligations it FULFILLS
                // A type is only lazy if ALL dimensions show no substance.
                if let Some(ref func_refs) = file_func_refs {
                    let type_name = class.node_name(i);
                    let field_count = class.field_count as usize;
                    let impl_methods =
                        Self::count_rust_impl_methods(func_refs, type_name, i);
                    let trait_impls =
                        Self::count_rust_trait_impls(func_refs, type_name, i);

                    if field_count >= 2 || impl_methods >= 2 || trait_impls >= 2 {
                        debug!(
                            "Skipping Rust type {} - fields={}, impl_methods={}, trait_impls={} (provides value)",
                            type_name, field_count, impl_methods, trait_impls
                        );
                        continue;
                    }
                }

                // Find methods belonging to this class from the shared file functions
                let methods: Vec<_> = file_funcs
                    .iter()
                    .filter(|f| f.line_start >= class.line_start && f.line_end <= class.line_end)
                    .collect();

                // --- Enhanced checks when AnalysisContext is available ---
                if let Some(ctx) = analysis_ctx {
                    // Role-based: skip if any method has high betweenness or is a Hub.
                    // This covers design patterns (adapter, wrapper, proxy, facade, etc.)
                    // that were previously matched by name patterns.
                    let has_important_method = methods.iter().any(|m| {
                        let mq = m.qn(i);
                        ctx.functions.get(mq).is_some_and(|fc| {
                            fc.betweenness > 0.05 || fc.role == FunctionRole::Hub
                        })
                    });
                    if has_important_method {
                        debug!(
                            "Skipping {} - has method with high betweenness or Hub role",
                            class.node_name(i)
                        );
                        continue;
                    }

                    // HMM: skip classes whose methods are primarily handlers.
                    // This covers framework patterns (Flask blueprints, Express routes,
                    // event listeners) that were previously matched by "handler",
                    // "listener", "observer" name patterns.
                    let handler_methods = methods
                        .iter()
                        .filter(|m| {
                            ctx.hmm_role(m.qn(i)).is_some_and(|(role, conf)| {
                                role == crate::detectors::context_hmm::FunctionContext::Handler
                                    && conf > 0.5
                            })
                        })
                        .count();
                    if handler_methods > 0 && handler_methods >= methods.len() / 2 {
                        debug!(
                            "Skipping {} - {}/{} methods are HMM-classified handlers",
                            class.node_name(i),
                            handler_methods,
                            methods.len()
                        );
                        continue;
                    }
                }

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

    fn detect(
        &self,
        ctx: &crate::detectors::analysis_context::AnalysisContext,
    ) -> Result<Vec<Finding>> {
        self.detect_inner(ctx.graph, Some(ctx))
    }
}

impl super::RegisteredDetector for LazyClassDetector {
    fn create(_init: &super::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::new())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeEdge, CodeNode, GraphStore};

    #[test]
    fn test_should_exclude() {
        let detector = LazyClassDetector::new();

        // Patterns still in EXCLUDE_PATTERNS (semantic, not role-detectable)
        assert!(detector.should_exclude("DatabaseConfig"));
        assert!(detector.should_exclude("CustomException"));
        assert!(detector.should_exclude("BaseClass"));
        assert!(detector.should_exclude("TestHelper"));

        // Patterns removed from EXCLUDE_PATTERNS (now handled by role analysis)
        assert!(!detector.should_exclude("UserAdapter"));
        assert!(!detector.should_exclude("FooHandler"));
        assert!(!detector.should_exclude("BarFactory"));

        // Never excluded
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
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

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
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

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
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

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
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

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
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

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
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

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
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

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

    #[test]
    fn test_count_rust_impl_methods_generic() {
        let graph = GraphStore::in_memory();
        let i = graph.interner();

        // Generic type: AnalysisContext<'g> — the QN contains the lifetime param
        let funcs = vec![
            CodeNode::function("new", "src/lib.rs")
                .with_qualified_name("src/lib.rs::impl<AnalysisContext<'g>>::new:10")
                .with_lines(10, 15),
            CodeNode::function("graph", "src/lib.rs")
                .with_qualified_name("src/lib.rs::impl<AnalysisContext<'g>>::graph:20")
                .with_lines(20, 22),
            CodeNode::function("fmt", "src/lib.rs")
                .with_qualified_name("src/lib.rs::impl<Debug for AnalysisContext<'g>>::fmt:30")
                .with_lines(30, 35),
        ];
        for f in &funcs {
            graph.add_node(f.clone());
        }

        let file_funcs = graph.get_functions_in_file("src/lib.rs");
        let refs: Vec<&crate::graph::store_models::CodeNode> = file_funcs.iter().collect();

        let count = LazyClassDetector::count_rust_impl_methods(&refs, "AnalysisContext", i);
        assert_eq!(count, 3, "Should count all 3 methods for generic AnalysisContext<'g>");
    }

    #[test]
    fn test_count_rust_trait_impls() {
        let graph = GraphStore::in_memory();
        let i = graph.interner();

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
            CodeNode::function("from", "src/lib.rs")
                .with_qualified_name("src/lib.rs::impl<From<i32> for Foo>::from:40")
                .with_lines(40, 45),
        ];
        for f in &funcs {
            graph.add_node(f.clone());
        }

        let file_funcs = graph.get_functions_in_file("src/lib.rs");
        let refs: Vec<&crate::graph::store_models::CodeNode> = file_funcs.iter().collect();

        let count = LazyClassDetector::count_rust_trait_impls(&refs, "Foo", i);
        assert_eq!(count, 3, "Should count 3 distinct trait impls: Display, Default, From<i32>");
    }

    // --- Go-specific tests ---

    #[test]
    fn test_go_small_type_not_flagged() {
        let graph = GraphStore::in_memory();

        // Go struct with 2 methods — idiomatic, should NOT be flagged
        graph.add_node(
            CodeNode::class("Reader", "pkg/io/reader.go")
                .with_qualified_name("pkg/io/reader.go::Reader:5")
                .with_lines(5, 15)
                .with_property("methodCount", 2i64),
        );

        graph.add_node(
            CodeNode::function("Read", "pkg/io/reader.go")
                .with_qualified_name("pkg/io/reader.go::Reader::Read:7")
                .with_lines(7, 10),
        );

        graph.add_node(
            CodeNode::function("Close", "pkg/io/reader.go")
                .with_qualified_name("pkg/io/reader.go::Reader::Close:12")
                .with_lines(12, 14),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Go type with 2 methods should NOT be flagged as lazy (idiomatic), got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_go_zero_method_type_not_flagged() {
        let graph = GraphStore::in_memory();

        // Go struct with 0 methods — still idiomatic in Go (data struct)
        graph.add_node(
            CodeNode::class("Point", "pkg/geo/point.go")
                .with_qualified_name("pkg/geo/point.go::Point:1")
                .with_lines(1, 8)
                .with_property("methodCount", 0i64),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Go type with 0 methods should NOT be flagged (idiomatic Go data struct)"
        );
    }

    #[test]
    fn test_go_interface_skipped_by_qn() {
        let graph = GraphStore::in_memory();

        // Go interface: parser emits ::interface:: in qualified name
        graph.add_node(
            CodeNode::class("Stringer", "pkg/fmt/stringer.go")
                .with_qualified_name("pkg/fmt/stringer.go::interface::Stringer:3")
                .with_lines(3, 8)
                .with_property("methodCount", 1i64),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Go interface should NOT be flagged as lazy class"
        );
    }

    // --- Java-specific tests ---

    #[test]
    fn test_java_interface_skipped_by_qn() {
        let graph = GraphStore::in_memory();

        // Java interface: parser emits ::interface:: in qualified name
        graph.add_node(
            CodeNode::class("Comparable", "src/main/java/Comparable.java")
                .with_qualified_name("src/main/java/Comparable.java::interface::Comparable:1")
                .with_lines(1, 10)
                .with_property("methodCount", 1i64),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Java interface should NOT be flagged as lazy class"
        );
    }

    #[test]
    fn test_java_record_skipped_by_qn() {
        let graph = GraphStore::in_memory();

        // Java 16+ record: parser emits ::record:: in qualified name
        graph.add_node(
            CodeNode::class("UserRecord", "src/main/java/UserRecord.java")
                .with_qualified_name("src/main/java/UserRecord.java::record::UserRecord:1")
                .with_lines(1, 8)
                .with_property("methodCount", 0i64),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Java record should NOT be flagged as lazy class (data carrier)"
        );
    }

    #[test]
    fn test_java_enum_skipped_by_qn() {
        let graph = GraphStore::in_memory();

        // Java enum: parser emits ::enum:: in qualified name
        graph.add_node(
            CodeNode::class("Color", "src/main/java/Color.java")
                .with_qualified_name("src/main/java/Color.java::enum::Color:1")
                .with_lines(1, 12)
                .with_property("methodCount", 1i64),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Java enum should NOT be flagged as lazy class"
        );
    }

    #[test]
    fn test_java_plain_class_still_flagged() {
        let graph = GraphStore::in_memory();

        // Regular Java class that is truly lazy — should still be flagged
        graph.add_node(
            CodeNode::class("TinyProcessor", "src/main/java/TinyProcessor.java")
                .with_qualified_name("src/main/java/TinyProcessor.java::TinyProcessor:1")
                .with_lines(1, 15)
                .with_property("methodCount", 1i64),
        );

        graph.add_node(
            CodeNode::function("process", "src/main/java/TinyProcessor.java")
                .with_qualified_name("src/main/java/TinyProcessor.java::TinyProcessor.process:3")
                .with_lines(3, 10),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert_eq!(
            findings.len(),
            1,
            "Plain Java class with 1 method and no callers should still be flagged"
        );
        assert!(findings[0].title.contains("TinyProcessor"));
    }

    // --- C#-specific tests ---

    #[test]
    fn test_cs_interface_skipped_by_qn() {
        let graph = GraphStore::in_memory();

        // C# interface: parser emits ::interface:: in qualified name
        graph.add_node(
            CodeNode::class("IDisposable", "src/IDisposable.cs")
                .with_qualified_name("src/IDisposable.cs::interface::IDisposable:1")
                .with_lines(1, 8)
                .with_property("methodCount", 1i64),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "C# interface should NOT be flagged as lazy class"
        );
    }

    #[test]
    fn test_cs_record_skipped_by_qn() {
        let graph = GraphStore::in_memory();

        // C# record: parser emits ::record:: in qualified name
        graph.add_node(
            CodeNode::class("Person", "src/Models/Person.cs")
                .with_qualified_name("src/Models/Person.cs::record::Person:1")
                .with_lines(1, 5)
                .with_property("methodCount", 0i64),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "C# record should NOT be flagged as lazy class (data carrier)"
        );
    }

    #[test]
    fn test_cs_enum_skipped_by_qn() {
        let graph = GraphStore::in_memory();

        // C# enum: parser emits ::enum:: in qualified name
        graph.add_node(
            CodeNode::class("Status", "src/Models/Status.cs")
                .with_qualified_name("src/Models/Status.cs::enum::Status:1")
                .with_lines(1, 10)
                .with_property("methodCount", 0i64),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "C# enum should NOT be flagged as lazy class"
        );
    }

    #[test]
    fn test_cs_struct_skipped_by_qn() {
        let graph = GraphStore::in_memory();

        // C# struct: parser emits ::struct:: in qualified name
        graph.add_node(
            CodeNode::class("Vector2", "src/Math/Vector2.cs")
                .with_qualified_name("src/Math/Vector2.cs::struct::Vector2:1")
                .with_lines(1, 15)
                .with_property("methodCount", 2i64),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "C# struct should NOT be flagged as lazy class (value type)"
        );
    }

    #[test]
    fn test_cs_i_prefixed_name_skipped() {
        let graph = GraphStore::in_memory();

        // C# class with I-prefix convention (interface-like abstract class)
        // Note: this class does NOT have ::interface:: in QN — it's a class
        // that follows the interface naming convention.
        graph.add_node(
            CodeNode::class("ILogger", "src/Logging/ILogger.cs")
                .with_qualified_name("src/Logging/ILogger.cs::ILogger:1")
                .with_lines(1, 10)
                .with_property("methodCount", 2i64),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "C# class with I-prefix naming convention should NOT be flagged"
        );
    }

    #[test]
    fn test_cs_i_prefix_not_applied_to_non_cs_files() {
        let graph = GraphStore::in_memory();

        // A Python class named "ILogger" should NOT get the C# I-prefix exclusion
        graph.add_node(
            CodeNode::class("ILogger", "src/logging.py")
                .with_qualified_name("src/logging.py::ILogger:1")
                .with_lines(1, 12)
                .with_property("methodCount", 2i64),
        );

        graph.add_node(
            CodeNode::function("log", "src/logging.py")
                .with_qualified_name("src/logging.py::ILogger::log:3")
                .with_lines(3, 8),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert_eq!(
            findings.len(),
            1,
            "Python class named ILogger should still be flagged (C# I-prefix rule is .cs only)"
        );
    }

    #[test]
    fn test_cs_plain_class_still_flagged() {
        let graph = GraphStore::in_memory();

        // Regular C# class with just a regular QN — should still be flagged
        graph.add_node(
            CodeNode::class("TinyWorker", "src/TinyWorker.cs")
                .with_qualified_name("src/TinyWorker.cs::TinyWorker:1")
                .with_lines(1, 12)
                .with_property("methodCount", 1i64),
        );

        graph.add_node(
            CodeNode::function("DoWork", "src/TinyWorker.cs")
                .with_qualified_name("src/TinyWorker.cs::TinyWorker.DoWork:3")
                .with_lines(3, 8),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert_eq!(
            findings.len(),
            1,
            "Plain C# class should still be flagged as lazy"
        );
        assert!(findings[0].title.contains("TinyWorker"));
    }

    // --- Python-specific tests ---

    #[test]
    fn test_python_decorated_class_with_few_methods_not_flagged() {
        let graph = GraphStore::in_memory();

        // Python @dataclass with 0 methods — should NOT be flagged
        let mut node = CodeNode::class("UserData", "src/models.py")
            .with_qualified_name("src/models.py::UserData:1")
            .with_lines(1, 10)
            .with_property("methodCount", 0i64);
        // Set the has_decorators flag (as the parser would for @dataclass)
        node.set_flag(crate::graph::store_models::FLAG_HAS_DECORATORS);
        graph.add_node(node);

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Python decorated class (likely @dataclass) with 0 methods should NOT be flagged"
        );
    }

    #[test]
    fn test_python_decorated_class_with_1_method_not_flagged() {
        let graph = GraphStore::in_memory();

        // Python @dataclass with 1 method (e.g., __post_init__) — should NOT be flagged
        let mut node = CodeNode::class("ConfigData", "src/config.py")
            .with_qualified_name("src/config.py::ConfigData:1")
            .with_lines(1, 15)
            .with_property("methodCount", 1i64);
        node.set_flag(crate::graph::store_models::FLAG_HAS_DECORATORS);
        graph.add_node(node);

        graph.add_node(
            CodeNode::function("__post_init__", "src/config.py")
                .with_qualified_name("src/config.py::ConfigData::__post_init__:5")
                .with_lines(5, 10),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Python decorated class with 1 method should NOT be flagged (likely @dataclass)"
        );
    }

    #[test]
    fn test_python_undecorated_class_still_flagged() {
        let graph = GraphStore::in_memory();

        // Python class WITHOUT decorators, few methods — should be flagged
        graph.add_node(
            CodeNode::class("TinyUtil", "src/utils.py")
                .with_qualified_name("src/utils.py::TinyUtil:1")
                .with_lines(1, 12)
                .with_property("methodCount", 1i64),
        );

        graph.add_node(
            CodeNode::function("run", "src/utils.py")
                .with_qualified_name("src/utils.py::TinyUtil::run:3")
                .with_lines(3, 8),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert_eq!(
            findings.len(),
            1,
            "Python undecorated class should still be flagged as lazy"
        );
        assert!(findings[0].title.contains("TinyUtil"));
    }

    #[test]
    fn test_python_decorated_class_with_many_methods_still_flagged() {
        let graph = GraphStore::in_memory();

        // Python decorated class with 2+ methods — still a candidate
        // (threshold is <= 1 for the decorator exemption)
        let mut node = CodeNode::class("SmallThing", "src/things.py")
            .with_qualified_name("src/things.py::SmallThing:1")
            .with_lines(1, 20)
            .with_property("methodCount", 2i64);
        node.set_flag(crate::graph::store_models::FLAG_HAS_DECORATORS);
        graph.add_node(node);

        graph.add_node(
            CodeNode::function("do_a", "src/things.py")
                .with_qualified_name("src/things.py::SmallThing::do_a:3")
                .with_lines(3, 8),
        );

        graph.add_node(
            CodeNode::function("do_b", "src/things.py")
                .with_qualified_name("src/things.py::SmallThing::do_b:10")
                .with_lines(10, 15),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![]);
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert_eq!(
            findings.len(),
            1,
            "Python decorated class with 2 methods should still be flagged (decorator exemption is <= 1)"
        );
    }

    #[test]
    fn test_python_namedtuple_excluded_by_pattern() {
        let detector = LazyClassDetector::new();

        // "namedtuple" should be in EXCLUDE_PATTERNS
        assert!(
            detector.should_exclude("PointNamedTuple"),
            "Class name containing 'namedtuple' should be excluded"
        );
        assert!(
            detector.should_exclude("MyNamedtuple"),
            "Class name containing 'namedtuple' (lowercase) should be excluded"
        );
    }

    #[test]
    fn test_python_dataclass_excluded_by_pattern() {
        let detector = LazyClassDetector::new();

        // "dataclass" should be in EXCLUDE_PATTERNS
        assert!(
            detector.should_exclude("UserDataclass"),
            "Class name containing 'dataclass' should be excluded"
        );
    }

    // --- Helper function tests ---

    #[test]
    fn test_file_type_detection() {
        // Go
        assert!(LazyClassDetector::is_go_file("pkg/io/reader.go"));
        assert!(!LazyClassDetector::is_go_file("src/reader.py"));

        // Java
        assert!(LazyClassDetector::is_java_file("src/Main.java"));
        assert!(!LazyClassDetector::is_java_file("src/main.go"));

        // C#
        assert!(LazyClassDetector::is_cs_file("src/Program.cs"));
        assert!(!LazyClassDetector::is_cs_file("src/style.css")); // .css != .cs

        // Python
        assert!(LazyClassDetector::is_python_file("src/app.py"));
        assert!(!LazyClassDetector::is_python_file("src/app.pyi")); // stub files
    }

    #[test]
    fn test_cs_interface_name_detection() {
        // Valid C# interface names
        assert!(LazyClassDetector::is_cs_interface_name("IDisposable"));
        assert!(LazyClassDetector::is_cs_interface_name("ILogger"));
        assert!(LazyClassDetector::is_cs_interface_name("IRepository"));

        // NOT interface names
        assert!(!LazyClassDetector::is_cs_interface_name("Integer")); // I + lowercase
        assert!(!LazyClassDetector::is_cs_interface_name("Item")); // I + lowercase 't'
        assert!(!LazyClassDetector::is_cs_interface_name("I")); // Too short
        assert!(!LazyClassDetector::is_cs_interface_name("")); // Empty
        assert!(!LazyClassDetector::is_cs_interface_name("iLogger")); // lowercase i
    }

    #[test]
    fn test_record_enum_qn_detection() {
        assert!(LazyClassDetector::is_record_qn("src/User.java::record::User:1"));
        assert!(LazyClassDetector::is_record_qn("src/Person.cs::record::Person:5"));
        assert!(!LazyClassDetector::is_record_qn("src/User.java::User:1"));

        assert!(LazyClassDetector::is_enum_qn("src/Color.java::enum::Color:1"));
        assert!(LazyClassDetector::is_enum_qn("src/Status.cs::enum::Status:1"));
        assert!(!LazyClassDetector::is_enum_qn("src/Color.java::Color:1"));

        assert!(LazyClassDetector::is_cs_struct_qn("src/Vec2.cs::struct::Vec2:1"));
        assert!(!LazyClassDetector::is_cs_struct_qn("src/Vec2.cs::Vec2:1"));
    }

    // --- New exclusion pattern tests ---

    #[test]
    fn test_new_exclusion_patterns() {
        let detector = LazyClassDetector::new();

        // Python data containers
        assert!(detector.should_exclude("UserNamedtuple"));
        assert!(detector.should_exclude("PointDataclass"));

        // Java/C# patterns
        assert!(detector.should_exclude("UserRecord"));
        assert!(detector.should_exclude("StatusEnum"));

        // C# extension methods
        assert!(detector.should_exclude("StringExtension"));
        assert!(detector.should_exclude("ListExtensions"));

        // C# partial classes
        assert!(detector.should_exclude("UserPartial"));
        assert!(detector.should_exclude("PartialController"));

        // Java abstract classes
        assert!(detector.should_exclude("AbstractProcessor"));
        assert!(detector.should_exclude("AbstractValidator"));
    }

    // --- Role analysis tests ---

    #[test]
    fn test_trimmed_exclude_patterns() {
        let detector = LazyClassDetector::new();

        // Kept patterns still work
        assert!(detector.should_exclude("UserConfig"));
        assert!(detector.should_exclude("CustomException"));
        assert!(detector.should_exclude("TestHelper"));
        assert!(detector.should_exclude("BaseClass"));

        // Removed patterns no longer excluded (handled by role analysis)
        assert!(!detector.should_exclude("FooAdapter"));
        assert!(!detector.should_exclude("BarHandler"));
        assert!(!detector.should_exclude("BazFactory"));
    }

    #[test]
    fn test_class_with_hub_method_not_flagged() {
        use crate::detectors::analysis_context::AnalysisContext;
        use crate::detectors::detector_context::{ContentFlags, DetectorContext};
        use crate::detectors::file_index::FileIndex;
        use crate::detectors::function_context::FunctionContext as FuncCtx;
        use crate::detectors::taint::centralized::CentralizedTaintResults;
        use std::sync::Arc;

        let graph = GraphStore::in_memory();

        // Create a small class with 1 method
        graph.add_node(
            CodeNode::class("MyAdapter", "src/adapters.py")
                .with_qualified_name("adapters::MyAdapter")
                .with_lines(1, 20)
                .with_property("methodCount", 1i64),
        );
        graph.add_node(
            CodeNode::function("adapt", "src/adapters.py")
                .with_qualified_name("adapters::MyAdapter::adapt")
                .with_lines(3, 15),
        );

        // Build FunctionContextMap with Hub role for the method
        let mut functions = std::collections::HashMap::new();
        functions.insert(
            "adapters::MyAdapter::adapt".to_string(),
            FuncCtx {
                qualified_name: "adapters::MyAdapter::adapt".to_string(),
                name: "adapt".to_string(),
                file_path: "src/adapters.py".to_string(),
                module: "adapters".to_string(),
                in_degree: 10,
                out_degree: 5,
                betweenness: 0.15,
                caller_modules: 4,
                callee_modules: 2,
                call_depth: 1,
                role: FunctionRole::Hub,
                is_exported: true,
                is_test: false,
                is_in_utility_module: false,
                complexity: None,
                loc: 12,
            },
        );

        let (det_ctx, _) =
            DetectorContext::build(&graph, &[], None, std::path::Path::new("/repo"));

        let ctx = AnalysisContext {
            graph: &graph,
            files: Arc::new(FileIndex::new(vec![])),
            functions: Arc::new(functions),
            taint: Arc::new(CentralizedTaintResults {
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

        let detector = LazyClassDetector::new();
        let findings = detector.detect_inner(&graph, Some(&ctx)).expect("should succeed");

        assert!(
            findings.is_empty(),
            "Class with a Hub method should NOT be flagged as lazy, got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_handler_methods_skip_class() {
        use crate::detectors::analysis_context::AnalysisContext;
        use crate::detectors::context_hmm::FunctionContext as HmmCtx;
        use crate::detectors::detector_context::{ContentFlags, DetectorContext};
        use crate::detectors::file_index::FileIndex;
        use crate::detectors::taint::centralized::CentralizedTaintResults;
        use std::sync::Arc;

        let graph = GraphStore::in_memory();

        // Create a class with 2 handler methods
        graph.add_node(
            CodeNode::class("LoginView", "src/views.py")
                .with_qualified_name("views::LoginView")
                .with_lines(1, 30)
                .with_property("methodCount", 2i64),
        );
        graph.add_node(
            CodeNode::function("get", "src/views.py")
                .with_qualified_name("views::LoginView::get")
                .with_lines(3, 15),
        );
        graph.add_node(
            CodeNode::function("post", "src/views.py")
                .with_qualified_name("views::LoginView::post")
                .with_lines(17, 28),
        );

        // HMM classifies both methods as Handler with high confidence
        let mut hmm = std::collections::HashMap::new();
        hmm.insert(
            "views::LoginView::get".to_string(),
            (HmmCtx::Handler, 0.85),
        );
        hmm.insert(
            "views::LoginView::post".to_string(),
            (HmmCtx::Handler, 0.90),
        );

        let (det_ctx, _) =
            DetectorContext::build(&graph, &[], None, std::path::Path::new("/repo"));

        let ctx = AnalysisContext {
            graph: &graph,
            files: Arc::new(FileIndex::new(vec![])),
            functions: Arc::new(std::collections::HashMap::new()),
            taint: Arc::new(CentralizedTaintResults {
                cross_function: std::collections::HashMap::new(),
                intra_function: std::collections::HashMap::new(),
            }),
            detector_ctx: Arc::new(det_ctx),
            hmm_classifications: Arc::new(hmm),
            resolver: Arc::new(crate::calibrate::ThresholdResolver::default()),
            reachability: Arc::new(crate::detectors::reachability::ReachabilityIndex::empty()),
            public_api: Arc::new(std::collections::HashSet::new()),
            module_metrics: Arc::new(std::collections::HashMap::new()),
            class_cohesion: Arc::new(std::collections::HashMap::new()),
            decorator_index: Arc::new(std::collections::HashMap::new()),
        };

        let detector = LazyClassDetector::new();
        let findings = detector.detect_inner(&graph, Some(&ctx)).expect("should succeed");

        assert!(
            findings.is_empty(),
            "Class with all handler methods should NOT be flagged as lazy, got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
