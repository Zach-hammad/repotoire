//! Middle Man Detector
//!
//! Graph-aware detection of classes that mostly delegate to other classes,
//! identifying unnecessary indirection in the codebase.
//!
//! Uses call graph to:
//! - Identify which class methods delegate to
//! - Calculate delegation ratio per target
//! - Detect concentrated delegation (all to one target = pure middle man)

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashMap;
use std::path::PathBuf;
use tracing::{debug, info};
use uuid::Uuid;

/// Thresholds for middle man detection
#[derive(Debug, Clone)]
pub struct MiddleManThresholds {
    /// Minimum methods to consider
    pub min_methods: usize,
    /// Percentage of methods that delegate (0.0 - 1.0)
    pub delegation_threshold: f64,
    /// Maximum complexity for a method to be considered pure delegation
    pub max_delegation_complexity: i64,
}

impl Default for MiddleManThresholds {
    fn default() -> Self {
        Self {
            min_methods: 3,
            delegation_threshold: 0.7,
            max_delegation_complexity: 2,
        }
    }
}

/// Patterns to exclude (legitimate delegation patterns)
const EXCLUDE_PATTERNS: &[&str] = &[
    "Adapter", "Wrapper", "Proxy", "Decorator", "Facade", "Bridge",
    "Controller", "Handler", "Router", "Dispatcher",
    "Test", "Mock", "Stub",
];

/// Detects classes that mostly delegate to other classes
pub struct MiddleManDetector {
    #[allow(dead_code)] // Stored for future config access
    config: DetectorConfig,
    thresholds: MiddleManThresholds,
}

impl MiddleManDetector {
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            thresholds: MiddleManThresholds::default(),
        }
    }

    #[allow(dead_code)] // Builder pattern method
    pub fn with_config(config: DetectorConfig) -> Self {
        let thresholds = MiddleManThresholds {
            min_methods: config.get_option_or("min_methods", 3),
            delegation_threshold: config.get_option_or("delegation_threshold", 0.7),
            max_delegation_complexity: config.get_option_or("max_delegation_complexity", 2),
        };
        Self { config, thresholds }
    }

    fn should_exclude(&self, class_name: &str) -> bool {
        let lower = class_name.to_lowercase();
        EXCLUDE_PATTERNS.iter().any(|p| lower.contains(&p.to_lowercase()))
    }

    /// Analyze delegation pattern for a class
    fn analyze_delegation(&self, graph: &GraphStore, class: &crate::graph::CodeNode) -> Option<DelegationAnalysis> {
        let functions = graph.get_functions();
        
        // Find methods belonging to this class
        let methods: Vec<_> = functions.iter()
            .filter(|f| {
                f.file_path == class.file_path &&
                f.line_start >= class.line_start &&
                f.line_end <= class.line_end
            })
            .collect();

        if methods.len() < self.thresholds.min_methods {
            return None;
        }

        let mut delegation_count = 0;
        let mut delegation_targets: HashMap<String, usize> = HashMap::new();
        
        for method in &methods {
            let callees = graph.get_callees(&method.qualified_name);
            let complexity = method.complexity().unwrap_or(1);
            
            // Pure delegation: single callee, low complexity
            if callees.len() == 1 && complexity <= self.thresholds.max_delegation_complexity {
                delegation_count += 1;
                
                // Track which class/module we're delegating to
                let target = &callees[0];
                let target_module = Self::extract_module(&target.file_path);
                *delegation_targets.entry(target_module).or_insert(0) += 1;
            }
        }

        let delegation_ratio = delegation_count as f64 / methods.len() as f64;
        
        if delegation_ratio < self.thresholds.delegation_threshold {
            return None;
        }

        // Find the primary delegation target
        let primary_target = delegation_targets
            .iter()
            .max_by_key(|(_, count)| *count)
            .map(|(target, count)| (target.clone(), *count));

        Some(DelegationAnalysis {
            method_count: methods.len(),
            delegation_count,
            delegation_ratio,
            primary_target,
            target_count: delegation_targets.len(),
        })
    }

    fn extract_module(file_path: &str) -> String {
        std::path::Path::new(file_path)
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("unknown")
            .to_string()
    }

    fn calculate_severity(&self, analysis: &DelegationAnalysis) -> Severity {
        // Concentrated delegation (all to one target) is worse
        let concentration = if analysis.target_count == 1 { 1.5 } else { 1.0 };
        let effective_ratio = analysis.delegation_ratio * concentration;
        
        if effective_ratio >= 0.95 {
            Severity::High
        } else if effective_ratio >= 0.8 {
            Severity::Medium
        } else {
            Severity::Low
        }
    }
}

struct DelegationAnalysis {
    method_count: usize,
    delegation_count: usize,
    delegation_ratio: f64,
    primary_target: Option<(String, usize)>,
    target_count: usize,
}

impl Default for MiddleManDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for MiddleManDetector {
    fn name(&self) -> &'static str {
        "MiddleManDetector"
    }

    fn description(&self) -> &'static str {
        "Detects classes that mostly delegate to other classes"
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
            // Skip interfaces
            if class.qualified_name.contains("::interface::") 
                || class.qualified_name.contains("::type::") {
                continue;
            }
            
            // Skip excluded patterns
            if self.should_exclude(&class.name) {
                continue;
            }

            // Analyze delegation pattern
            let analysis = match self.analyze_delegation(graph, &class) {
                Some(a) => a,
                None => continue,
            };

            let severity = self.calculate_severity(&analysis);
            
            let target_info = match &analysis.primary_target {
                Some((target, count)) => format!(
                    "primarily to '{}' ({} of {} delegations)",
                    target, count, analysis.delegation_count
                ),
                None => "to various targets".to_string(),
            };

            let concentration_note = if analysis.target_count == 1 {
                "\n\n**Note:** All delegations go to a single target - this is a pure proxy class."
            } else {
                ""
            };

            findings.push(Finding {
                id: Uuid::new_v4().to_string(),
                detector: "MiddleManDetector".to_string(),
                severity,
                title: format!("Middle Man: {}", class.name),
                description: format!(
                    "Class '{}' delegates {:.0}% of its methods ({}/{}) {}.\n\n\
                     This class adds indirection without significant value.{}",
                    class.name,
                    analysis.delegation_ratio * 100.0,
                    analysis.delegation_count,
                    analysis.method_count,
                    target_info,
                    concentration_note
                ),
                affected_files: vec![class.file_path.clone().into()],
                line_start: Some(class.line_start),
                line_end: Some(class.line_end),
                suggested_fix: Some(format!(
                    "Options:\n\
                     1. Remove the middle man - have callers use {} directly\n\
                     2. Add meaningful logic to justify the class's existence\n\
                     3. If this is intentional (Facade/Adapter), document the reason",
                    analysis.primary_target
                        .as_ref()
                        .map(|(t, _)| t.as_str())
                        .unwrap_or("the delegate")
                )),
                estimated_effort: Some("Medium (1-2 hours)".to_string()),
                category: Some("design".to_string()),
                cwe_id: None,
                why_it_matters: Some(
                    "Middle man classes add unnecessary indirection. They increase \
                     call stack depth, make code harder to trace, and add maintenance \
                     overhead without providing value."
                        .to_string()
                ),
                ..Default::default()
            });
        }

        info!("MiddleManDetector found {} findings", findings.len());
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeNode, CodeEdge, GraphStore};

    #[test]
    fn test_should_exclude() {
        let detector = MiddleManDetector::new();
        
        assert!(detector.should_exclude("UserAdapter"));
        assert!(detector.should_exclude("OrderProxy"));
        assert!(detector.should_exclude("TestHelper"));
        
        assert!(!detector.should_exclude("OrderManager"));
        assert!(!detector.should_exclude("UserService"));
    }

    #[test]
    fn test_detect_middle_man() {
        let graph = GraphStore::in_memory();
        
        // Create a middle man class
        graph.add_node(CodeNode::class("MiddleClass", "src/middle.py")
            .with_qualified_name("middle::MiddleClass")
            .with_lines(1, 50)
            .with_property("methodCount", 4i64));
        
        // Add methods that delegate
        for i in 0..4 {
            let method = format!("method_{}", i);
            graph.add_node(CodeNode::function(&method, "src/middle.py")
                .with_qualified_name(&format!("middle::MiddleClass::{}", method))
                .with_lines(i * 10 + 5, i * 10 + 10)
                .with_property("complexity", 1i64));
            
            // Each method delegates to the same target
            graph.add_node(CodeNode::function(&format!("real_{}", i), "src/real.py")
                .with_qualified_name(&format!("real::RealClass::{}", format!("real_{}", i)))
                .with_lines(i * 10, i * 10 + 5));
            
            graph.add_edge_by_name(
                &format!("middle::MiddleClass::{}", method),
                &format!("real::RealClass::real_{}", i),
                CodeEdge::calls()
            );
        }
        
        let detector = MiddleManDetector::new();
        let findings = detector.detect(&graph).unwrap();
        
        assert_eq!(findings.len(), 1);
        assert!(findings[0].title.contains("MiddleClass"));
        assert!(findings[0].description.contains("100%")); // All methods delegate
    }
}
