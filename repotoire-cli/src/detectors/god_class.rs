//! God class detector - finds classes that do too much
//!
//! A "god class" is a class that:
//! - Has too many methods
//! - Has too many lines of code
//! - Has too much complexity
//! - Has low cohesion (methods don't share data)
//!
//! These classes violate the Single Responsibility Principle and
//! are difficult to understand, test, and maintain.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use tracing::{debug, info};
use uuid::Uuid;

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

/// Patterns for legitimate large classes
const EXCLUDED_PATTERNS: &[&str] = &[
    r".*Client$",       // Database/API clients
    r".*Connection$",   // Connection managers
    r".*Session$",      // Session handlers
    r".*Pipeline$",     // Data pipelines
    r".*Engine$",       // Workflow engines
    r".*Generator$",    // Code generators
    r".*Builder$",      // Builder pattern
    r".*Factory$",      // Factory pattern
    r".*Manager$",      // Resource managers
    r".*Controller$",   // MVC controllers
    r".*Adapter$",      // Adapter pattern
    r".*Facade$",       // Facade pattern
];

/// Detects god classes (classes with too many responsibilities)
pub struct GodClassDetector {
    config: DetectorConfig,
    thresholds: GodClassThresholds,
    excluded_patterns: Vec<Regex>,
    use_pattern_exclusions: bool,
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
        }
    }

    /// Create with custom config
    /// 
    /// Supports both naming conventions:
    /// - max_methods / method_count
    /// - max_lines / loc
    pub fn with_config(config: DetectorConfig) -> Self {
        let thresholds = GodClassThresholds {
            // Support both "max_methods" and "method_count" from config
            max_methods: config.get_option("max_methods")
                .or_else(|| config.get_option("method_count"))
                .unwrap_or(20),
            critical_methods: config.get_option_or("critical_methods", 30),
            // Support both "max_lines" and "loc" from config
            max_lines: config.get_option("max_lines")
                .or_else(|| config.get_option("loc"))
                .unwrap_or(500),
            critical_lines: config.get_option_or("critical_lines", 1000),
            max_complexity: config.get_option_or("max_complexity", 100),
            critical_complexity: config.get_option_or("critical_complexity", 200),
        };

        let use_pattern_exclusions = config.get_option_or("use_pattern_exclusions", true);

        let excluded_patterns = EXCLUDED_PATTERNS
            .iter()
            .filter_map(|p| Regex::new(p).ok())
            .collect();

        Self {
            config,
            thresholds,
            excluded_patterns,
            use_pattern_exclusions,
        }
    }

    /// Check if a class name matches excluded patterns
    fn is_excluded_pattern(&self, class_name: &str) -> bool {
        if !self.use_pattern_exclusions {
            return false;
        }
        self.excluded_patterns.iter().any(|p| p.is_match(class_name))
    }

    /// Determine if metrics indicate a god class
    fn is_god_class(
        &self,
        method_count: usize,
        complexity: usize,
        loc: usize,
    ) -> Option<String> {
        let mut reasons = Vec::new();

        // Check method count
        if method_count >= self.thresholds.critical_methods {
            reasons.push(format!("very high method count ({})", method_count));
        } else if method_count >= self.thresholds.max_methods {
            reasons.push(format!("high method count ({})", method_count));
        }

        // Check complexity
        if complexity >= self.thresholds.critical_complexity {
            reasons.push(format!("very high complexity ({})", complexity));
        } else if complexity >= self.thresholds.max_complexity {
            reasons.push(format!("high complexity ({})", complexity));
        }

        // Check lines of code
        if loc >= self.thresholds.critical_lines {
            reasons.push(format!("very large class ({} LOC)", loc));
        } else if loc >= self.thresholds.max_lines {
            reasons.push(format!("large class ({} LOC)", loc));
        }

        // Need at least one critical violation or two regular violations
        let critical_count = [
            method_count >= self.thresholds.critical_methods,
            complexity >= self.thresholds.critical_complexity,
            loc >= self.thresholds.critical_lines,
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

    /// Calculate severity based on metrics
    fn calculate_severity(&self, method_count: usize, complexity: usize, loc: usize) -> Severity {
        let critical_count = [
            method_count >= self.thresholds.critical_methods,
            complexity >= self.thresholds.critical_complexity,
            loc >= self.thresholds.critical_lines,
        ]
        .iter()
        .filter(|&&x| x)
        .count();

        if critical_count >= 2 {
            return Severity::Critical;
        }

        let high_count = [
            method_count >= self.thresholds.max_methods,
            complexity >= self.thresholds.max_complexity,
            loc >= self.thresholds.max_lines,
        ]
        .iter()
        .filter(|&&x| x)
        .count();

        match (critical_count, high_count) {
            (1, _) => Severity::High,
            (0, n) if n >= 2 => Severity::High,
            (0, 1) => Severity::Medium,
            _ => Severity::Low,
        }
    }

    /// Generate refactoring suggestions
    fn suggest_refactoring(
        &self,
        name: &str,
        method_count: usize,
        complexity: usize,
        loc: usize,
    ) -> String {
        let mut suggestions = vec![format!("Refactor '{}' to reduce its responsibilities:\n", name)];

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
        _qualified_name: String,
        name: String,
        file_path: String,
        method_count: usize,
        complexity: usize,
        loc: usize,
        line_start: Option<u32>,
        line_end: Option<u32>,
        reason: &str,
    ) -> Finding {
        let severity = self.calculate_severity(method_count, complexity, loc);

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "GodClassDetector".to_string(),
            severity,
            title: format!("God class detected: {}", name),
            description: format!(
                "Class '{}' shows signs of being a god class: {}.\n\n\
                 **Metrics:**\n\
                 - Methods: {}\n\
                 - Total complexity: {}\n\
                 - Lines of code: {}",
                name, reason, method_count, complexity, loc
            ),
            affected_files: vec![PathBuf::from(&file_path)],
            line_start,
            line_end,
            suggested_fix: Some(self.suggest_refactoring(&name, method_count, complexity, loc)),
            estimated_effort: Some(self.estimate_effort(method_count, complexity, loc)),
            category: Some("complexity".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "God classes violate the Single Responsibility Principle. They are difficult \
                 to understand, test, and maintain. Changes to one part may unexpectedly \
                 affect other parts, leading to bugs and technical debt."
                    .to_string(),
            ),
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
    }    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();

        for class in graph.get_classes() {
            // Skip TypeScript/Go interfaces - they have properties, not methods
            if class.qualified_name.contains("::interface::")
                || class.qualified_name.contains("::type::")
            {
                continue;
            }

            // Use is_excluded_pattern() to skip legitimate large classes
            if self.is_excluded_pattern(&class.name) {
                debug!("Skipping excluded pattern: {}", class.name);
                continue;
            }

            let method_count = class.get_i64("methodCount").unwrap_or(0) as usize;
            let complexity = class.complexity().unwrap_or(1) as usize;
            let loc = class.loc() as usize;

            // Use is_god_class() for multi-criteria evaluation
            if let Some(reason) = self.is_god_class(method_count, complexity, loc) {
                // Use create_finding() which calls calculate_severity(),
                // suggest_refactoring(), and estimate_effort()
                findings.push(self.create_finding(
                    class.qualified_name.clone(),
                    class.name.clone(),
                    class.file_path.clone(),
                    method_count,
                    complexity,
                    loc,
                    Some(class.line_start),
                    Some(class.line_end),
                    &reason,
                ));
            }
        }

        Ok(findings)
    }
}

