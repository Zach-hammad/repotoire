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
        let i = graph.interner();
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

            for class in file_classes {
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
}
