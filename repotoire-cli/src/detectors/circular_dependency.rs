//! Circular dependency detector using Tarjan's SCC algorithm
//!
//! Detects circular dependencies in the import graph by finding
//! Strongly Connected Components (SCCs) with size > 1.
//!
//! # Algorithm
//!
//! Uses Tarjan's algorithm via petgraph, which runs in O(V+E) time:
//! 1. Extract all File nodes and IMPORTS edges from the graph
//! 2. Build an edge list for the SCC algorithm
//! 3. Find all SCCs - each SCC with size > 1 is a circular dependency
//!
//! This is 10-100x faster than pairwise path queries.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphClient;
use crate::models::{Finding, Severity};
use anyhow::Result;
use petgraph::algo::tarjan_scc;
use petgraph::graph::DiGraph;
use std::collections::HashMap;
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

    /// Normalize cycle to canonical form
    ///
    /// Rotates the cycle to start with the lexicographically smallest element
    /// to ensure consistent deduplication.
    fn normalize_cycle(cycle: &[String]) -> Vec<String> {
        if cycle.is_empty() {
            return vec![];
        }

        // Find index of minimum element
        let min_idx = cycle
            .iter()
            .enumerate()
            .min_by_key(|(_, v)| *v)
            .map(|(i, _)| i)
            .unwrap_or(0);

        // Rotate to start with minimum
        let mut normalized = Vec::with_capacity(cycle.len());
        normalized.extend_from_slice(&cycle[min_idx..]);
        normalized.extend_from_slice(&cycle[..min_idx]);
        normalized
    }

    /// Create a finding from a cycle
    fn create_finding(&self, cycle_files: Vec<String>, cycle_length: usize) -> Finding {
        let finding_id = Uuid::new_v4().to_string();
        let severity = Self::calculate_severity(cycle_length);

        // Format cycle for display
        let display_files: Vec<&str> = cycle_files
            .iter()
            .take(5)
            .map(|f| {
                f.rsplit('/')
                    .next()
                    .unwrap_or(f)
            })
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

    /// Find SCCs using Tarjan's algorithm
    ///
    /// This is the core algorithm:
    /// 1. Build a directed graph from file imports
    /// 2. Run Tarjan's SCC algorithm (O(V+E))
    /// 3. Return SCCs with size > 1 (circular dependencies)
    fn find_sccs(
        &self,
        file_names: &[String],
        edges: &[(usize, usize)],
    ) -> Vec<Vec<String>> {
        // Build petgraph DiGraph
        let mut graph: DiGraph<(), ()> = DiGraph::new();
        let mut node_indices = Vec::with_capacity(file_names.len());

        // Add nodes
        for _ in 0..file_names.len() {
            node_indices.push(graph.add_node(()));
        }

        // Add edges
        for &(src, dst) in edges {
            if src < node_indices.len() && dst < node_indices.len() {
                graph.add_edge(node_indices[src], node_indices[dst], ());
            }
        }

        // Run Tarjan's SCC algorithm
        let sccs = tarjan_scc(&graph);

        // Convert to file names and filter to cycles (size > 1)
        sccs.into_iter()
            .filter(|scc| scc.len() > 1)
            .map(|scc| {
                scc.into_iter()
                    .map(|idx| file_names[idx.index()].clone())
                    .collect()
            })
            .collect()
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

    fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        debug!("Starting circular dependency detection");

        // Step 1: Get all files
        let files_query = "MATCH (f:File) RETURN f.filePath AS path ORDER BY path";
        let files_result = graph.execute(files_query)?;

        if files_result.is_empty() {
            debug!("No files found in graph");
            return Ok(vec![]);
        }

        // Build file path -> index mapping
        let mut file_to_idx: HashMap<String, usize> = HashMap::new();
        let mut file_names: Vec<String> = Vec::new();

        for (idx, row) in files_result.iter().enumerate() {
            if let Some(path) = row.get("path").and_then(|v| v.as_str()) {
                file_to_idx.insert(path.to_string(), idx);
                file_names.push(path.to_string());
            }
        }

        debug!("Found {} files", file_names.len());

        // Step 2: Get import edges
        let edges_query = r#"
            MATCH (f1:File)-[:IMPORTS]->(f2:File)
            RETURN f1.filePath AS src, f2.filePath AS dst
        "#;
        let edges_result = graph.execute(edges_query)?;

        let mut edges: Vec<(usize, usize)> = Vec::new();
        for row in edges_result {
            if let (Some(src), Some(dst)) = (
                row.get("src").and_then(|v| v.as_str()),
                row.get("dst").and_then(|v| v.as_str()),
            ) {
                if let (Some(&src_idx), Some(&dst_idx)) =
                    (file_to_idx.get(src), file_to_idx.get(dst))
                {
                    edges.push((src_idx, dst_idx));
                }
            }
        }

        debug!("Found {} import edges", edges.len());

        if edges.is_empty() {
            return Ok(vec![]);
        }

        // Step 3: Find SCCs (circular dependencies)
        let cycles = self.find_sccs(&file_names, &edges);
        debug!("Found {} cycles", cycles.len());

        // Step 4: Create findings
        let mut findings: Vec<Finding> = Vec::new();
        let mut seen_cycles: std::collections::HashSet<Vec<String>> =
            std::collections::HashSet::new();

        for cycle in cycles {
            let normalized = Self::normalize_cycle(&cycle);
            if seen_cycles.contains(&normalized) {
                continue;
            }
            seen_cycles.insert(normalized.clone());

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

    #[test]
    fn test_normalize_cycle() {
        let cycle = vec![
            "c.py".to_string(),
            "a.py".to_string(),
            "b.py".to_string(),
        ];
        let normalized = CircularDependencyDetector::normalize_cycle(&cycle);
        assert_eq!(normalized[0], "a.py");
    }

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
    fn test_find_sccs() {
        let detector = CircularDependencyDetector::new();
        
        // Create a simple cycle: a -> b -> c -> a
        let files = vec![
            "a.py".to_string(),
            "b.py".to_string(),
            "c.py".to_string(),
            "d.py".to_string(), // Not in cycle
        ];
        let edges = vec![(0, 1), (1, 2), (2, 0), (3, 0)]; // d -> a but not in cycle

        let cycles = detector.find_sccs(&files, &edges);
        
        assert_eq!(cycles.len(), 1);
        assert_eq!(cycles[0].len(), 3);
    }

    #[test]
    fn test_no_cycles() {
        let detector = CircularDependencyDetector::new();
        
        // Linear dependency chain: a -> b -> c
        let files = vec![
            "a.py".to_string(),
            "b.py".to_string(),
            "c.py".to_string(),
        ];
        let edges = vec![(0, 1), (1, 2)];

        let cycles = detector.find_sccs(&files, &edges);
        
        assert!(cycles.is_empty());
    }
}
