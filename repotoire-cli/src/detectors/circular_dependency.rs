//! Circular dependency detector using Tarjan's SCC algorithm
//!
//! Graph-enhanced detection of circular dependencies:
//! - Find SCCs using Tarjan's algorithm
//! - Analyze coupling strength to suggest where to break the cycle
//! - Identify the "weakest link" (least coupled edge)
//! - Suggest specific refactoring based on what's being imported

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashMap;
use std::path::PathBuf;
use tracing::{debug, info};
use uuid::Uuid;

/// Analysis of coupling strength in a cycle
struct CouplingAnalysis {
    edge_strengths: HashMap<(String, String), usize>,
    weakest_link: Option<(String, String, usize)>,
    strongest_link: Option<(String, String, usize)>,
}

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

    /// Calculate severity based on cycle length and coupling strength
    fn calculate_severity(cycle_length: usize, max_coupling: usize) -> Severity {
        // High coupling in cycle = harder to break
        let coupling_factor = if max_coupling > 10 { 1 } else { 0 };
        
        match cycle_length + coupling_factor {
            n if n >= 10 => Severity::Critical,
            n if n >= 5 => Severity::High,
            n if n >= 3 => Severity::Medium,
            _ => Severity::Low,
        }
    }

    /// Analyze coupling strength between files in a cycle
    fn analyze_coupling(&self, cycle: &[String], graph: &GraphStore) -> CouplingAnalysis {
        let mut edge_strengths: HashMap<(String, String), usize> = HashMap::new();
        let mut weakest_link: Option<(String, String, usize)> = None;
        let mut strongest_link: Option<(String, String, usize)> = None;
        
        // For each edge in the cycle, count how many symbols are imported
        for i in 0..cycle.len() {
            let from = &cycle[i];
            let to = &cycle[(i + 1) % cycle.len()];
            
            // Count imports between these files
            let imports = graph.get_imports();
            let strength = imports.iter()
                .filter(|(src, dst)| {
                    src.contains(from) && dst.contains(to)
                })
                .count()
                .max(1); // At least 1 if there's an edge
            
            edge_strengths.insert((from.clone(), to.clone()), strength);
            
            match &weakest_link {
                None => weakest_link = Some((from.clone(), to.clone(), strength)),
                Some((_, _, s)) if strength < *s => {
                    weakest_link = Some((from.clone(), to.clone(), strength));
                }
                _ => {}
            }
            
            match &strongest_link {
                None => strongest_link = Some((from.clone(), to.clone(), strength)),
                Some((_, _, s)) if strength > *s => {
                    strongest_link = Some((from.clone(), to.clone(), strength));
                }
                _ => {}
            }
        }
        
        CouplingAnalysis {
            edge_strengths,
            weakest_link,
            strongest_link,
        }
    }

    /// Generate fix suggestion based on cycle analysis
    fn suggest_fix(cycle_length: usize, coupling: &CouplingAnalysis) -> String {
        let mut suggestion = String::new();
        
        // Specific suggestion based on weakest link
        if let Some((from, to, strength)) = &coupling.weakest_link {
            let from_name = from.rsplit('/').next().unwrap_or(from);
            let to_name = to.rsplit('/').next().unwrap_or(to);
            
            suggestion.push_str(&format!(
                "🔗 **Best place to break:** `{}` → `{}` (weakest coupling: {} imports)\n\n",
                from_name, to_name, strength
            ));
            
            if *strength <= 2 {
                suggestion.push_str(&format!(
                    "Since `{}` only imports {} symbol(s) from `{}`, consider:\n\
                     - Move those symbols to a shared module\n\
                     - Pass them as parameters instead of importing\n\
                     - Use dependency injection\n\n",
                    from_name, strength, to_name
                ));
            }
        }
        
        if cycle_length >= 5 {
            suggestion.push_str(
                "**For large cycles:**\n\
                 1. Extract shared interfaces/types into a new `types.py` or `interfaces.ts`\n\
                 2. Apply the Dependency Inversion Principle\n\
                 3. Consider restructuring into layers"
            );
        } else {
            suggestion.push_str(
                "**For small cycles:**\n\
                 1. Merge tightly coupled modules if they're always used together\n\
                 2. Use TYPE_CHECKING (Python) or type-only imports (TS) for type hints\n\
                 3. Extract common code to a third module"
            );
        }
        
        suggestion
    }

    /// Estimate effort to fix based on cycle size
    fn estimate_effort(cycle_length: usize) -> String {
        match cycle_length {
            n if n >= 10 => "Large (2-4 days)".to_string(),
            n if n >= 5 => "Medium (1-2 days)".to_string(),
            _ => "Small (2-4 hours)".to_string(),
        }
    }

    /// Create a finding from a cycle with coupling analysis
    fn create_finding(&self, cycle_files: Vec<String>, cycle_length: usize, coupling: CouplingAnalysis) -> Finding {
        let finding_id = Uuid::new_v4().to_string();
        let max_coupling = coupling.edge_strengths.values().max().copied().unwrap_or(1);
        let severity = Self::calculate_severity(cycle_length, max_coupling);

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
        
        // Add coupling info to description
        let mut description = format!("Found circular import chain: {}", cycle_display);
        
        if let Some((from, to, strength)) = &coupling.weakest_link {
            let from_name = from.rsplit('/').next().unwrap_or(from);
            let to_name = to.rsplit('/').next().unwrap_or(to);
            description.push_str(&format!(
                "\n\n**Weakest link:** `{}` → `{}` ({} imports)",
                from_name, to_name, strength
            ));
        }
        
        if let Some((from, to, strength)) = &coupling.strongest_link {
            if coupling.weakest_link.as_ref().map(|(f, t, _)| (f, t)) != Some((&from, &to)) {
                let from_name = from.rsplit('/').next().unwrap_or(from);
                let to_name = to.rsplit('/').next().unwrap_or(to);
                description.push_str(&format!(
                    "\n**Tightest coupling:** `{}` → `{}` ({} imports)",
                    from_name, to_name, strength
                ));
            }
        }

        Finding {
            id: finding_id,
            detector: "CircularDependencyDetector".to_string(),
            severity,
            title: format!("Circular dependency involving {} files", cycle_length),
            description,
            affected_files: cycle_files.iter().map(PathBuf::from).collect(),
            line_start: None,
            line_end: None,
            suggested_fix: Some(Self::suggest_fix(cycle_length, &coupling)),
            estimated_effort: Some(Self::estimate_effort(cycle_length)),
            category: Some("architecture".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Circular dependencies make code harder to understand, test, and maintain. \
                 They can cause import errors at runtime and make it difficult to refactor \
                 individual modules."
                    .to_string(),
            ),
            ..Default::default()
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

        // Create findings from cycles with coupling analysis
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
            
            // Analyze coupling strength to find the best place to break
            let coupling = self.analyze_coupling(&cycle, graph);
            
            findings.push(self.create_finding(cycle, cycle_length, coupling));
        }

        // Sort by severity (highest first)
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        info!(
            "CircularDependencyDetector found {} circular dependencies (graph-aware)",
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
        // With minimal coupling (1)
        assert_eq!(
            CircularDependencyDetector::calculate_severity(2, 1),
            Severity::Low
        );
        assert_eq!(
            CircularDependencyDetector::calculate_severity(3, 1),
            Severity::Medium
        );
        assert_eq!(
            CircularDependencyDetector::calculate_severity(5, 1),
            Severity::High
        );
        assert_eq!(
            CircularDependencyDetector::calculate_severity(10, 1),
            Severity::Critical
        );
        
        // High coupling bumps severity
        assert_eq!(
            CircularDependencyDetector::calculate_severity(4, 15),
            Severity::High  // 4 + 1 (coupling factor) = 5
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
