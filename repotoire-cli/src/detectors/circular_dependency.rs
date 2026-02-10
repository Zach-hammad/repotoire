//! Circular dependency detector using Tarjan's SCC algorithm
//!
//! Detects circular dependencies in the import graph by finding
//! Strongly Connected Components (SCCs) with size > 1.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use tracing::{debug, info};
use uuid::Uuid;

/// Detects circular dependencies in the import graph
pub struct CircularDependencyDetector {
    config: DetectorConfig,
}

impl CircularDependencyDetector {
    /// Create a new detector with default config
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        Self { config }
    }

    /// Calculate severity based on cycle length
    fn calculate_severity(cycle_length: usize) -> Severity {
        match cycle_length {
            n if n >= 10 => Severity::Critical,
            n if n >= 5 => Severity::High,
            n if n >= 3 => Severity::Medium,
            _ => Severity::Low,
        }
    }

    /// Generate fix suggestion based on cycle size
    fn suggest_fix(cycle_length: usize) -> String {
        if cycle_length >= 5 {
            "Large circular dependency detected. Consider:\n\
             1. Extract shared interfaces/types into a separate module\n\
             2. Use dependency injection to break tight coupling\n\
             3. Refactor into layers with clear dependency direction\n\
             4. Apply the Dependency Inversion Principle"
                .to_string()
        } else {
            "Small circular dependency. Consider:\n\
             1. Merge the circular modules if they're tightly coupled\n\
             2. Extract common dependencies to a third module\n\
             3. Use forward references (TYPE_CHECKING) for type hints"
                .to_string()
        }
    }

    /// Estimate effort to fix based on cycle size
    fn estimate_effort(cycle_length: usize) -> String {
        match cycle_length {
            n if n >= 10 => "Large (2-4 days)".to_string(),
            n if n >= 5 => "Medium (1-2 days)".to_string(),
            _ => "Small (2-4 hours)".to_string(),
        }
    }

    /// Create a finding from a cycle
    fn create_finding(&self, cycle_files: Vec<String>, cycle_length: usize) -> Finding {
        let finding_id = Uuid::new_v4().to_string();
        let severity = Self::calculate_severity(cycle_length);

        // Format cycle for display
        let display_files: Vec<&str> = cycle_files
            .iter()
            .take(5)
            .map(|f| f.rsplit('/').next().unwrap_or(f))
            .collect();

        let mut cycle_display = display_files.join(" → ");
        if cycle_length > 5 {
            cycle_display.push_str(&format!(" ... ({} files total)", cycle_length));
        }

        let description = format!("Found circular import chain: {}", cycle_display);

        Finding {
            id: finding_id,
            detector: "CircularDependencyDetector".to_string(),
            severity,
            title: format!("Circular dependency involving {} files", cycle_length),
            description,
            affected_files: cycle_files.iter().map(PathBuf::from).collect(),
            line_start: None,
            line_end: None,
            suggested_fix: Some(Self::suggest_fix(cycle_length)),
            estimated_effort: Some(Self::estimate_effort(cycle_length)),
            category: Some("architecture".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Circular dependencies make code harder to understand, test, and maintain. \
                 They can cause import errors at runtime and make it difficult to refactor \
                 individual modules."
                    .to_string(),
            ),
        }
    }
}

impl Default for CircularDependencyDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for CircularDependencyDetector {
    fn name(&self) -> &'static str {
        "CircularDependencyDetector"
    }

    fn description(&self) -> &'static str {
        "Detects circular dependencies between modules using SCC analysis"
    }

    fn category(&self) -> &'static str {
        "architecture"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        debug!("Starting circular dependency detection");

        // Use GraphStore's built-in cycle detection
        let cycles = graph.find_import_cycles();

        debug!("Found {} cycles", cycles.len());

        if cycles.is_empty() {
            return Ok(vec![]);
        }

        // Create findings from cycles
        let mut findings: Vec<Finding> = Vec::new();
        let mut seen_cycles: std::collections::HashSet<Vec<String>> =
            std::collections::HashSet::new();

        for cycle in cycles {
            // Normalize for deduplication
            let mut normalized = cycle.clone();
            normalized.sort();
            
            if seen_cycles.contains(&normalized) {
                continue;
            }
            seen_cycles.insert(normalized);

            let cycle_length = cycle.len();
            findings.push(self.create_finding(cycle, cycle_length));
        }

        // Sort by severity (highest first)
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        info!(
            "CircularDependencyDetector found {} circular dependencies",
            findings.len()
        );

        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeEdge, CodeNode, NodeKind};

    #[test]
    fn test_severity_calculation() {
        assert_eq!(
            CircularDependencyDetector::calculate_severity(2),
            Severity::Low
        );
        assert_eq!(
            CircularDependencyDetector::calculate_severity(3),
            Severity::Medium
        );
        assert_eq!(
            CircularDependencyDetector::calculate_severity(5),
            Severity::High
        );
        assert_eq!(
            CircularDependencyDetector::calculate_severity(10),
            Severity::Critical
        );
    }

    #[test]
    fn test_detect_cycle() {
        let store = GraphStore::in_memory();

        // Create files
        store.add_node(CodeNode::file("a.py"));
        store.add_node(CodeNode::file("b.py"));
        store.add_node(CodeNode::file("c.py"));

        // Create cycle: a -> b -> c -> a
        store.add_edge_by_name("a.py", "b.py", CodeEdge::imports());
        store.add_edge_by_name("b.py", "c.py", CodeEdge::imports());
        store.add_edge_by_name("c.py", "a.py", CodeEdge::imports());

        let detector = CircularDependencyDetector::new();
        let findings = detector.detect(&store).unwrap();

        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].severity, Severity::Medium); // 3 files
    }

    #[test]
    fn test_no_cycle() {
        let store = GraphStore::in_memory();

        // Create files
        store.add_node(CodeNode::file("a.py"));
        store.add_node(CodeNode::file("b.py"));
        store.add_node(CodeNode::file("c.py"));

        // Linear chain: a -> b -> c (no cycle)
        store.add_edge_by_name("a.py", "b.py", CodeEdge::imports());
        store.add_edge_by_name("b.py", "c.py", CodeEdge::imports());

        let detector = CircularDependencyDetector::new();
        let findings = detector.detect(&store).unwrap();

        assert!(findings.is_empty());
    }
}
