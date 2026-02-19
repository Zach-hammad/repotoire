//! God class detector - finds classes that do too much
//!
//! A "god class" is a class that:
//! - Has too many methods
//! - Has too many lines of code
//! - Has too much complexity
//! - Has low cohesion (methods don't share data)
//!
//! Enhanced with graph-based role detection:
//! - Framework core classes (Flask, Express) are NOT flagged
//! - Facade pattern (thin wrappers) have raised thresholds
//! - Entry point classes (heavily used) have raised thresholds

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::class_context::{ClassContextBuilder, ClassContextMap, ClassRole};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use tracing::{debug, info};

/// Thresholds for god class detection
#[derive(Debug, Clone)]
pub struct GodClassThresholds {
    /// Method count above which a class is suspicious
    pub max_methods: usize,
    /// Method count for critical severity
    pub critical_methods: usize,
    /// Lines of code above which a class is suspicious
    pub max_lines: usize,
    /// Lines of code for critical severity
    pub critical_lines: usize,
    /// Complexity above which a class is suspicious
    pub max_complexity: usize,
    /// Complexity for critical severity
    pub critical_complexity: usize,
}

impl Default for GodClassThresholds {
    fn default() -> Self {
        Self {
            max_methods: 20,
            critical_methods: 30,
            max_lines: 500,
            critical_lines: 1000,
            max_complexity: 100,
            critical_complexity: 200,
        }
    }
}

/// Patterns for legitimate large classes (fallback when graph analysis unavailable)
const EXCLUDED_PATTERNS: &[&str] = &[
    r".*Client$",     // Database/API clients
    r".*Connection$", // Connection managers
    r".*Session$",    // Session handlers
    r".*Pipeline$",   // Data pipelines
    r".*Engine$",     // Workflow engines
    r".*Generator$",  // Code generators
    r".*Builder$",    // Builder pattern
    r".*Factory$",    // Factory pattern
    r".*Manager$",    // Resource managers
    r".*Controller$", // MVC controllers
    r".*Adapter$",    // Adapter pattern
    r".*Facade$",     // Facade pattern
];

/// Detects god classes (classes with too many responsibilities)
pub struct GodClassDetector {
    config: DetectorConfig,
    thresholds: GodClassThresholds,
    excluded_patterns: Vec<Regex>,
    use_pattern_exclusions: bool,
    /// Whether to use graph-based class context analysis
    use_graph_context: bool,
}

impl GodClassDetector {
    /// Create a new detector with default thresholds
    pub fn new() -> Self {
        Self::with_thresholds(GodClassThresholds::default())
    }

    /// Create with custom thresholds
    pub fn with_thresholds(thresholds: GodClassThresholds) -> Self {
        let excluded_patterns = EXCLUDED_PATTERNS
            .iter()
            .filter_map(|p| Regex::new(p).ok())
            .collect();

        Self {
            config: DetectorConfig::new(),
            thresholds,
            excluded_patterns,
            use_pattern_exclusions: true,
            use_graph_context: true, // Enable by default
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        use crate::calibrate::MetricKind;
        let thresholds = GodClassThresholds {
            max_methods: config
                .get_option("max_methods")
                .or_else(|| config.get_option("method_count"))
                .unwrap_or_else(|| config.adaptive.warn_usize(MetricKind::ClassMethodCount, 20)),
            critical_methods: config.get_option_or("critical_methods", 30),
            max_lines: config
                .get_option("max_lines")
                .or_else(|| config.get_option("loc"))
                .unwrap_or_else(|| config.adaptive.warn_usize(MetricKind::FileLength, 500)),
            critical_lines: config.get_option_or("critical_lines",
                config.adaptive.high_usize(MetricKind::FileLength, 1000)),
            max_complexity: config.get_option_or("max_complexity",
                config.adaptive.warn_usize(MetricKind::Complexity, 100)),
            critical_complexity: config.get_option_or("critical_complexity",
                config.adaptive.high_usize(MetricKind::Complexity, 200)),
        };

        let use_pattern_exclusions = config.get_option_or("use_pattern_exclusions", true);
        let use_graph_context = config.get_option_or("use_graph_context", true);

        let excluded_patterns = EXCLUDED_PATTERNS
            .iter()
            .filter_map(|p| Regex::new(p).ok())
            .collect();

        Self {
            config,
            thresholds,
            excluded_patterns,
            use_pattern_exclusions,
            use_graph_context,
        }
    }

    /// Check if a class name matches excluded patterns
    fn is_excluded_pattern(&self, class_name: &str) -> bool {
        if !self.use_pattern_exclusions {
            return false;
        }
        self.excluded_patterns
            .iter()
            .any(|p| p.is_match(class_name))
    }

    /// Determine if metrics indicate a god class, given adjusted thresholds
    fn is_god_class(
        &self,
        method_count: usize,
        complexity: usize,
        loc: usize,
        max_methods: usize,
        critical_methods: usize,
        max_lines: usize,
        critical_lines: usize,
    ) -> Option<String> {
        let mut reasons = Vec::new();

        // Check method count
        if method_count >= critical_methods {
            reasons.push(format!("very high method count ({})", method_count));
        } else if method_count >= max_methods {
            reasons.push(format!("high method count ({})", method_count));
        }

        // Check complexity
        if complexity >= self.thresholds.critical_complexity {
            reasons.push(format!("very high complexity ({})", complexity));
        } else if complexity >= self.thresholds.max_complexity {
            reasons.push(format!("high complexity ({})", complexity));
        }

        // Check lines of code
        if loc >= critical_lines {
            reasons.push(format!("very large class ({} LOC)", loc));
        } else if loc >= max_lines {
            reasons.push(format!("large class ({} LOC)", loc));
        }

        // Need at least one critical violation or two regular violations
        let critical_count = [
            method_count >= critical_methods,
            complexity >= self.thresholds.critical_complexity,
            loc >= critical_lines,
        ]
        .iter()
        .filter(|&&x| x)
        .count();

        if critical_count >= 1 || reasons.len() >= 2 {
            Some(reasons.join(", "))
        } else {
            None
        }
    }

    /// Calculate severity based on metrics and role
    fn calculate_severity(
        &self,
        method_count: usize,
        complexity: usize,
        loc: usize,
        severity_multiplier: f64,
    ) -> Severity {
        let critical_count = [
            method_count >= self.thresholds.critical_methods,
            complexity >= self.thresholds.critical_complexity,
            loc >= self.thresholds.critical_lines,
        ]
        .iter()
        .filter(|&&x| x)
        .count();

        let high_count = [
            method_count >= self.thresholds.max_methods,
            complexity >= self.thresholds.max_complexity,
            loc >= self.thresholds.max_lines,
        ]
        .iter()
        .filter(|&&x| x)
        .count();

        let base_severity = match (critical_count, high_count) {
            (n, _) if n >= 2 => Severity::Critical,
            (1, _) => Severity::High,
            (0, n) if n >= 2 => Severity::High,
            (0, 1) => Severity::Medium,
            _ => Severity::Low,
        };

        // Apply role-based severity reduction
        if severity_multiplier <= 0.0 {
            return Severity::Low; // Shouldn't happen, but safety
        }
        if severity_multiplier <= 0.3 {
            return Severity::Low;
        }
        if severity_multiplier <= 0.5 {
            return match base_severity {
                Severity::Critical => Severity::Medium,
                Severity::High => Severity::Low,
                _ => Severity::Low,
            };
        }
        if severity_multiplier <= 0.7 {
            return match base_severity {
                Severity::Critical => Severity::High,
                Severity::High => Severity::Medium,
                _ => base_severity,
            };
        }

        base_severity
    }

    /// Generate refactoring suggestions
    fn suggest_refactoring(
        &self,
        name: &str,
        method_count: usize,
        complexity: usize,
        loc: usize,
        role_note: Option<&str>,
    ) -> String {
        let mut suggestions = vec![format!(
            "Refactor '{}' to reduce its responsibilities:\n",
            name
        )];

        if let Some(note) = role_note {
            suggestions.push(format!("**Note:** {}\n\n", note));
        }

        if method_count >= self.thresholds.max_methods {
            suggestions.push(
                "1. **Extract related methods into separate classes**\n\
                    - Look for method groups that work with the same data\n\
                    - Create focused classes with single responsibilities\n"
                    .to_string(),
            );
        }

        if complexity >= self.thresholds.max_complexity {
            suggestions.push(
                "2. **Simplify complex methods**\n\
                    - Break down complex methods into smaller functions\n\
                    - Consider using the Strategy or Command pattern\n"
                    .to_string(),
            );
        }

        if loc >= self.thresholds.max_lines {
            suggestions.push(format!(
                "3. **Break down the large class ({} LOC)**\n\
                    - Split into smaller, focused classes\n\
                    - Consider using composition over inheritance\n\
                    - Extract data classes for complex state\n",
                loc
            ));
        }

        suggestions.push(
            "\n**Apply SOLID principles:**\n\
             - Single Responsibility: Each class should have one reason to change\n\
             - Open/Closed: Extend behavior without modifying existing code\n\
             - Interface Segregation: Create specific interfaces\n\
             - Dependency Inversion: Depend on abstractions"
                .to_string(),
        );

        suggestions.join("")
    }

    /// Estimate refactoring effort
    fn estimate_effort(&self, method_count: usize, complexity: usize, loc: usize) -> String {
        if method_count >= self.thresholds.critical_methods
            || complexity >= self.thresholds.critical_complexity
            || loc >= self.thresholds.critical_lines
        {
            "Large (1-2 weeks)".to_string()
        } else if method_count >= self.thresholds.max_methods
            || complexity >= self.thresholds.max_complexity
            || loc >= self.thresholds.max_lines
        {
            "Medium (3-5 days)".to_string()
        } else {
            "Small (1-2 days)".to_string()
        }
    }

    /// Create a finding for a god class
    fn create_finding(
        &self,
        name: String,
        file_path: String,
        method_count: usize,
        complexity: usize,
        loc: usize,
        line_start: Option<u32>,
        line_end: Option<u32>,
        reason: &str,
        role_info: Option<(&ClassRole, &str)>,
    ) -> Finding {
        let severity_multiplier = role_info
            .map(|(role, _)| role.severity_multiplier())
            .unwrap_or(1.0);

        let severity = self.calculate_severity(method_count, complexity, loc, severity_multiplier);

        let role_note = role_info.map(|(role, reason)| {
            format!(
                "This class was identified as {:?} ({}). Thresholds adjusted accordingly.",
                role, reason
            )
        });

        let description = if let Some((role, role_reason)) = role_info {
            format!(
                "Class '{}' shows signs of being a god class: {}.\n\n\
                 **Role Analysis:** {:?} — {}\n\n\
                 **Metrics:**\n\
                 - Methods: {}\n\
                 - Total complexity: {}\n\
                 - Lines of code: {}",
                name, reason, role, role_reason, method_count, complexity, loc
            )
        } else {
            format!(
                "Class '{}' shows signs of being a god class: {}.\n\n\
                 **Metrics:**\n\
                 - Methods: {}\n\
                 - Total complexity: {}\n\
                 - Lines of code: {}",
                name, reason, method_count, complexity, loc
            )
        };

        // Use method count as the primary metric for explainability
        let explanation = self.config.adaptive.explain(
            crate::calibrate::MetricKind::ClassMethodCount,
            method_count as f64,
            20.0, // default max_methods
        );
        let threshold_metadata = explanation.to_metadata().into_iter().collect();
        let description = format!("{}\n\n📊 {}", description, explanation.to_note());

        Finding {
            id: String::new(),
            detector: "GodClassDetector".to_string(),
            severity,
            title: format!("God class detected: {}", name),
            description,
            affected_files: vec![PathBuf::from(&file_path)],
            line_start,
            line_end,
            suggested_fix: Some(self.suggest_refactoring(
                &name,
                method_count,
                complexity,
                loc,
                role_note.as_deref(),
            )),
            estimated_effort: Some(self.estimate_effort(method_count, complexity, loc)),
            category: Some("complexity".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "God classes violate the Single Responsibility Principle. They are difficult \
                 to understand, test, and maintain. Changes to one part may unexpectedly \
                 affect other parts, leading to bugs and technical debt."
                    .to_string(),
            ),
            threshold_metadata,
            ..Default::default()
        }
    }
}

impl Default for GodClassDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for GodClassDetector {
    fn name(&self) -> &'static str {
        "GodClassDetector"
    }

    fn description(&self) -> &'static str {
        "Detects classes with too many methods or lines of code"
    }

    fn category(&self) -> &'static str {
        "complexity"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();

        // Build class context for graph-based analysis
        let class_contexts: Option<ClassContextMap> = if self.use_graph_context {
            let builder = ClassContextBuilder::new(graph);
            let contexts = builder.build();
            debug!("ClassContext built {} entries", contexts.len());
            if contexts.is_empty() {
                debug!("ClassContext empty, falling back to pattern matching");
                None
            } else {
                // Debug: show framework classes found
                for (qn, ctx) in &contexts {
                    if ctx.role == ClassRole::FrameworkCore {
                        debug!("Framework class detected: {} ({:?})", qn, ctx.role_reason);
                    }
                }
                Some(contexts)
            }
        } else {
            None
        };

        for class in graph.get_classes() {
            // Skip TypeScript/Go interfaces - they have properties, not methods
            if class.qualified_name.contains("::interface::")
                || class.qualified_name.contains("::type::")
            {
                continue;
            }

            let method_count = class.get_i64("methodCount").unwrap_or(0) as usize;
            let complexity = class.complexity().unwrap_or(1) as usize;
            let loc = class.loc() as usize;

            // Get class context if available
            let ctx = class_contexts
                .as_ref()
                .and_then(|c| c.get(&class.qualified_name));

            // Check if we should skip this class entirely based on graph analysis
            if let Some(ctx) = ctx {
                if ctx.skip_god_class() {
                    debug!(
                        "Skipping {} ({:?}): {}",
                        class.name, ctx.role, ctx.role_reason
                    );
                    continue;
                }
            }

            // Fall back to pattern exclusion if no graph context
            if ctx.is_none() && self.is_excluded_pattern(&class.name) {
                debug!("Skipping excluded pattern: {}", class.name);
                continue;
            }

            // Get adjusted thresholds based on role
            let (max_methods, max_lines) = ctx
                .map(|c| {
                    c.adjusted_thresholds(self.thresholds.max_methods, self.thresholds.max_lines)
                })
                .unwrap_or((self.thresholds.max_methods, self.thresholds.max_lines));

            let (critical_methods, critical_lines) = ctx
                .map(|c| {
                    c.adjusted_thresholds(
                        self.thresholds.critical_methods,
                        self.thresholds.critical_lines,
                    )
                })
                .unwrap_or((
                    self.thresholds.critical_methods,
                    self.thresholds.critical_lines,
                ));

            // Check if it's a god class with adjusted thresholds
            if let Some(reason) = self.is_god_class(
                method_count,
                complexity,
                loc,
                max_methods,
                critical_methods,
                max_lines,
                critical_lines,
            ) {
                let role_info = ctx.map(|c| (&c.role, c.role_reason.as_str()));

                findings.push(self.create_finding(
                    class.name.clone(),
                    class.file_path.clone(),
                    method_count,
                    complexity,
                    loc,
                    Some(class.line_start),
                    Some(class.line_end),
                    &reason,
                    role_info,
                ));
            }
        }

        info!(
            "GodClassDetector: analyzed {} classes, found {} issues",
            graph.get_classes().len(),
            findings.len()
        );

        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeNode, GraphStore};

    fn create_test_class(name: &str, methods: usize, loc: u32, complexity: i64) -> CodeNode {
        CodeNode::class(name, "test.py")
            .with_qualified_name(&format!("test::{}", name))
            .with_lines(1, loc)
            .with_property("methodCount", methods as i64)
            .with_property("complexity", complexity)
    }

    #[test]
    fn test_skip_framework_class() {
        let store = GraphStore::in_memory();
        store.add_node(create_test_class("Flask", 50, 2000, 150));

        let detector = GodClassDetector::new();
        let findings = detector.detect(&store).unwrap();

        assert!(
            findings.is_empty(),
            "Framework core class Flask should not be flagged"
        );
    }

    #[test]
    fn test_skip_application_pattern() {
        let store = GraphStore::in_memory();
        store.add_node(create_test_class("MyApplication", 40, 1500, 120));

        let detector = GodClassDetector::new();
        let findings = detector.detect(&store).unwrap();

        assert!(
            findings.is_empty(),
            "Class matching Application pattern should not be flagged"
        );
    }

    #[test]
    fn test_flag_actual_god_class() {
        let store = GraphStore::in_memory();
        store.add_node(create_test_class("OrderProcessor", 35, 1200, 180));

        let detector = GodClassDetector::new();
        let findings = detector.detect(&store).unwrap();

        assert_eq!(findings.len(), 1, "Actual god class should be flagged");
        assert!(findings[0].title.contains("OrderProcessor"));
    }

    #[test]
    fn test_thresholds() {
        let detector = GodClassDetector::new();

        // Just under thresholds - no flag
        assert!(detector
            .is_god_class(19, 99, 499, 20, 30, 500, 1000)
            .is_none());

        // Single violation at regular threshold - NOT enough (need 2+ or 1 critical)
        assert!(detector
            .is_god_class(20, 50, 400, 20, 30, 500, 1000)
            .is_none());

        // Two regular violations - flag
        assert!(detector
            .is_god_class(25, 120, 400, 20, 30, 500, 1000)
            .is_some());

        // Single critical violation - flag
        assert!(detector
            .is_god_class(30, 50, 400, 20, 30, 500, 1000)
            .is_some());

        // Multiple violations - definitely flag
        assert!(detector
            .is_god_class(25, 120, 700, 20, 30, 500, 1000)
            .is_some());
    }

    #[test]
    fn test_excluded_patterns() {
        let detector = GodClassDetector::new();

        assert!(detector.is_excluded_pattern("DatabaseClient"));
        assert!(detector.is_excluded_pattern("UserManager"));
        assert!(detector.is_excluded_pattern("EventFacade"));
        assert!(!detector.is_excluded_pattern("OrderProcessor"));
    }
}
