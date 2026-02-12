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
use uuid::Uuid;

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

/// Patterns to exclude from lazy class detection
const EXCLUDE_PATTERNS: &[&str] = &[
    // Design patterns (intentionally small)
    "Adapter",
    "Wrapper",
    "Proxy",
    "Decorator",
    "Facade",
    "Bridge",
    // Data classes (supposed to be simple)
    "Config",
    "Settings",
    "Options",
    "DTO",
    "Entity",
    "Model",
    "Schema",
    "Request",
    "Response",
    "Params",
    "Args",
    "Event",
    "Message",
    // Exceptions
    "Exception",
    "Error",
    // Base/abstract (extended elsewhere)
    "Base",
    "Abstract",
    "Interface",
    "Mixin",
    "Protocol",
    "Trait",
    // Test infrastructure
    "Test",
    "Mock",
    "Stub",
    "Fake",
    "Fixture",
    // Framework conventions
    "Serializer",
    "Validator",
    "Handler",
    "Listener",
    "Observer",
    "Factory",
    "Builder",
    "Provider",
    "Service",
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

    /// Check if class name matches an exclusion pattern
    fn should_exclude(&self, class_name: &str) -> bool {
        let lower = class_name.to_lowercase();
        EXCLUDE_PATTERNS
            .iter()
            .any(|p| lower.contains(&p.to_lowercase()))
    }

    /// Count unique external callers of a class's methods
    fn count_external_callers(&self, graph: &GraphStore, class: &crate::graph::CodeNode) -> usize {
        let functions = graph.get_functions();

        // Find methods belonging to this class (by file + line range)
        let class_methods: Vec<&crate::graph::CodeNode> = functions
            .iter()
            .filter(|f| {
                f.file_path == class.file_path
                    && f.line_start >= class.line_start
                    && f.line_end <= class.line_end
            })
            .collect();

        if class_methods.is_empty() {
            return 0;
        }

        // Collect all unique external callers
        let mut external_callers: HashSet<String> = HashSet::new();

        for method in &class_methods {
            for caller in graph.get_callers(&method.qualified_name) {
                // External = not in same file or not in class line range
                let is_external = caller.file_path != class.file_path
                    || caller.line_start < class.line_start
                    || caller.line_end > class.line_end;

                if is_external {
                    external_callers.insert(caller.qualified_name.clone());
                }
            }
        }

        external_callers.len()
    }

    /// Calculate usage ratio (callers per method)
    fn calculate_usage_ratio(
        &self,
        graph: &GraphStore,
        class: &crate::graph::CodeNode,
        method_count: usize,
    ) -> f64 {
        if method_count == 0 {
            return 0.0;
        }

        let callers = self.count_external_callers(graph, class);
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

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();

        for class in graph.get_classes() {
            // Skip interfaces and type aliases
            if class.qualified_name.contains("::interface::")
                || class.qualified_name.contains("::type::")
            {
                continue;
            }

            // Skip excluded patterns
            if self.should_exclude(&class.name) {
                continue;
            }

            let method_count = class.get_i64("methodCount").unwrap_or(0) as usize;
            let loc = class.loc() as usize;

            // Must have few methods and be small
            if method_count > self.thresholds.max_methods || loc > self.thresholds.max_loc {
                continue;
            }

            // Skip tiny classes (likely incomplete or placeholders)
            if loc < 5 {
                continue;
            }

            // KEY GRAPH CHECK: Is this class actually used?
            let external_callers = self.count_external_callers(graph, &class);

            // If the class has many callers, it's not lazy - it's well-used!
            if external_callers >= self.thresholds.min_callers_to_skip {
                debug!(
                    "Skipping {} - has {} external callers (threshold: {})",
                    class.name, external_callers, self.thresholds.min_callers_to_skip
                );
                continue;
            }

            // Calculate severity based on usage
            let severity = if external_callers == 0 {
                Severity::Medium // Completely unused
            } else {
                Severity::Low // Used but not much
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
                id: Uuid::new_v4().to_string(),
                detector: "LazyClassDetector".to_string(),
                severity,
                title: format!("Lazy Class: {}", class.name),
                description: format!(
                    "Class '{}' has only {} method(s) and {} LOC. {}\n\n\
                     Consider inlining this class's functionality or expanding it.",
                    class.name, method_count, loc, usage_note
                ),
                affected_files: vec![class.file_path.clone().into()],
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
        let findings = detector.detect(&graph).unwrap();

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
        let findings = detector.detect(&graph).unwrap();

        // Should flag - unused class
        assert_eq!(findings.len(), 1);
        assert!(findings[0].title.contains("UnusedClass"));
    }
}
