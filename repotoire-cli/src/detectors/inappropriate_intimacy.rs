//! Inappropriate Intimacy Detector
//!
//! Graph-enhanced detection of classes that are too tightly coupled.
//!
//! Uses graph analysis to:
//! - Distinguish layered architecture (CLI→Core) from actual intimacy
//! - Detect only bidirectional coupling (true intimacy)
//! - Check if coupled files share domain concepts (potentially legitimate)
//! - Analyze interface vs implementation dependencies
//!
//! Example:
//! ```text
//! class Order {
//!     fn process(&self, customer: &Customer) {
//!         customer.internal_field = ...;  // 10 times
//!     }
//! }
//! class Customer {
//!     fn validate(&self, order: &Order) {
//!         order.internal_field = ...;  // 8 times
//!     }
//! }
//! ```
//! These classes know too much about each other's internals.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::PathBuf;
use tracing::{debug, info};
use uuid::Uuid;

/// Thresholds for inappropriate intimacy detection
#[derive(Debug, Clone)]
pub struct InappropriateIntimacyThresholds {
    /// Total coupling for high severity
    pub threshold_high: usize,
    /// Total coupling for medium severity
    pub threshold_medium: usize,
    /// Minimum mutual access to consider
    pub min_mutual_access: usize,
}

impl Default for InappropriateIntimacyThresholds {
    fn default() -> Self {
        Self {
            threshold_high: 40,   // Increased from 20
            threshold_medium: 20, // Increased from 10
            min_mutual_access: 8, // Increased from 5
        }
    }
}

/// Detects classes that are too tightly coupled
pub struct InappropriateIntimacyDetector {
    #[allow(dead_code)] // Stored for future config access
    config: DetectorConfig,
    thresholds: InappropriateIntimacyThresholds,
}

impl InappropriateIntimacyDetector {
    /// Create a new detector with default thresholds
    pub fn new() -> Self {
        Self::with_thresholds(InappropriateIntimacyThresholds::default())
    }

    /// Create with custom thresholds
    pub fn with_thresholds(thresholds: InappropriateIntimacyThresholds) -> Self {
        Self {
            config: DetectorConfig::new(),
            thresholds,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        let thresholds = InappropriateIntimacyThresholds {
            threshold_high: config.get_option_or("threshold_high", 20),
            threshold_medium: config.get_option_or("threshold_medium", 10),
            min_mutual_access: config.get_option_or("min_mutual_access", 5),
        };

        Self { config, thresholds }
    }

    /// Calculate severity based on total coupling
    fn calculate_severity(&self, total_coupling: usize) -> Severity {
        if total_coupling >= self.thresholds.threshold_high {
            Severity::High
        } else if total_coupling >= self.thresholds.threshold_medium {
            Severity::Medium
        } else {
            Severity::Low
        }
    }

    /// Estimate effort based on severity
    fn estimate_effort(&self, severity: Severity) -> String {
        match severity {
            Severity::Critical => "Large (8+ hours)".to_string(),
            Severity::High => "Large (4-8 hours)".to_string(),
            Severity::Medium => "Medium (2-4 hours)".to_string(),
            Severity::Low | Severity::Info => "Medium (1-2 hours)".to_string(),
        }
    }

    /// Create a finding for inappropriate intimacy
    fn create_finding(
        &self,
        _class1: String,
        class1_name: String,
        _class2: String,
        class2_name: String,
        file1: String,
        file2: String,
        c1_to_c2: usize,
        c2_to_c1: usize,
    ) -> Finding {
        let total_coupling = c1_to_c2 + c2_to_c1;
        let severity = self.calculate_severity(total_coupling);
        let same_file = file1 == file2;
        let same_file_note = if same_file {
            " (same file)"
        } else {
            " (different files)"
        };

        let suggestion = if severity == Severity::High {
            format!(
                "Classes '{}' and '{}' have excessive mutual access ({} total accesses: \
                 {} and {} respectively).\n\n\
                 This tight coupling violates encapsulation. Consider:\n\
                 1. Merge the classes if they truly belong together\n\
                 2. Extract common data into a shared class\n\
                 3. Apply the Law of Demeter - don't access internals directly\n\
                 4. Introduce interfaces or abstract base classes to reduce coupling",
                class1_name, class2_name, total_coupling, c1_to_c2, c2_to_c1
            )
        } else {
            format!(
                "Classes '{}' and '{}' show inappropriate intimacy ({} mutual accesses). \
                 Consider refactoring to reduce coupling.",
                class1_name, class2_name, total_coupling
            )
        };

        let mut affected_files = vec![PathBuf::from(&file1)];
        if file1 != file2 {
            affected_files.push(PathBuf::from(&file2));
        }

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "InappropriateIntimacyDetector".to_string(),
            severity,
            title: format!("Inappropriate Intimacy: {} ↔ {}", class1_name, class2_name),
            description: format!(
                "Classes '{}' and '{}' are too tightly coupled{}:\n\
                 • {} → {}: {} accesses\n\
                 • {} → {}: {} accesses\n\
                 • Total coupling: {} mutual accesses\n\n\
                 This bidirectional coupling makes both classes difficult to change independently \
                 and violates encapsulation principles.",
                class1_name,
                class2_name,
                same_file_note,
                class1_name,
                class2_name,
                c1_to_c2,
                class2_name,
                class1_name,
                c2_to_c1,
                total_coupling
            ),
            affected_files,
            line_start: None,
            line_end: None,
            suggested_fix: Some(suggestion),
            estimated_effort: Some(self.estimate_effort(severity)),
            category: Some("coupling".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Inappropriate intimacy makes classes hard to change independently. \
                 When two classes know too much about each other's internals, changes \
                 to one often require changes to the other, leading to ripple effects \
                 and increased maintenance costs."
                    .to_string(),
            ),
            ..Default::default()
        }
    }
}

impl Default for InappropriateIntimacyDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for InappropriateIntimacyDetector {
    fn name(&self) -> &'static str {
        "InappropriateIntimacyDetector"
    }

    fn description(&self) -> &'static str {
        "Detects classes that are too tightly coupled"
    }

    fn category(&self) -> &'static str {
        "coupling"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }
    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();
        use std::collections::HashMap;

        // Expected architectural layers - these calling down is normal, not intimacy
        fn is_expected_layer_dependency(from: &str, _to: &str) -> bool {
            // CLI can call anything
            if from.contains("/cli/") {
                return true;
            }
            // Handlers can call core modules
            if from.contains("/handlers/") || from.contains("/mcp/") {
                return true;
            }
            // Tests can call anything
            if from.contains("/tests/") || from.contains("_test.rs") {
                return true;
            }
            // mod.rs files often re-export or orchestrate
            if from.ends_with("/mod.rs") {
                return true;
            }
            false
        }

        // Check if files are in the same domain/module (legitimate close coupling)
        fn same_domain(file_a: &str, file_b: &str) -> bool {
            // Same parent directory = same module
            let dir_a = file_a.rsplit('/').nth(1).unwrap_or("");
            let dir_b = file_b.rsplit('/').nth(1).unwrap_or("");
            dir_a == dir_b && !dir_a.is_empty()
        }

        // Check if one file is the interface and other is implementation
        fn is_interface_impl_pair(file_a: &str, file_b: &str) -> bool {
            let name_a = file_a.rsplit('/').next().unwrap_or("");
            let name_b = file_b.rsplit('/').next().unwrap_or("");

            // Common patterns: base.rs/impl.rs, interface.py/implementation.py
            let interface_patterns = ["base", "interface", "abstract", "types", "protocol"];
            let impl_patterns = ["impl", "concrete", "default", "standard"];

            let a_is_interface = interface_patterns.iter().any(|p| name_a.contains(p));
            let b_is_impl = impl_patterns.iter().any(|p| name_b.contains(p));
            let b_is_interface = interface_patterns.iter().any(|p| name_b.contains(p));
            let a_is_impl = impl_patterns.iter().any(|p| name_a.contains(p));

            (a_is_interface && b_is_impl) || (b_is_interface && a_is_impl)
        }

        // Count DIRECTIONAL calls between files
        let mut a_to_b: HashMap<(String, String), usize> = HashMap::new();
        let mut b_to_a: HashMap<(String, String), usize> = HashMap::new();

        // Track which functions are called (for interface analysis)
        let mut a_to_b_funcs: HashMap<(String, String), HashSet<String>> = HashMap::new();
        let mut b_to_a_funcs: HashMap<(String, String), HashSet<String>> = HashMap::new();

        for (caller, callee) in graph.get_calls() {
            if let (Some(caller_node), Some(callee_node)) =
                (graph.get_node(&caller), graph.get_node(&callee))
            {
                if caller_node.file_path != callee_node.file_path {
                    // Skip expected layered architecture dependencies
                    if is_expected_layer_dependency(&caller_node.file_path, &callee_node.file_path)
                    {
                        continue;
                    }

                    let key = if caller_node.file_path < callee_node.file_path {
                        (caller_node.file_path.clone(), callee_node.file_path.clone())
                    } else {
                        (callee_node.file_path.clone(), caller_node.file_path.clone())
                    };

                    // Track direction and functions called
                    if caller_node.file_path < callee_node.file_path {
                        *a_to_b.entry(key.clone()).or_insert(0) += 1;
                        a_to_b_funcs
                            .entry(key)
                            .or_default()
                            .insert(callee_node.name.clone());
                    } else {
                        *b_to_a.entry(key.clone()).or_insert(0) += 1;
                        b_to_a_funcs
                            .entry(key)
                            .or_default()
                            .insert(callee_node.name.clone());
                    }
                }
            }
        }

        // Flag only BIDIRECTIONAL coupling (true intimacy, not layered dependency)
        for ((file_a, file_b), count_a_to_b) in &a_to_b {
            let count_b_to_a = b_to_a
                .get(&(file_a.clone(), file_b.clone()))
                .copied()
                .unwrap_or(0);

            // Must have significant calls in BOTH directions to be "intimacy"
            if count_a_to_b >= &8 && count_b_to_a >= 8 {
                let total = count_a_to_b + count_b_to_a;

                // Check for legitimate patterns
                let is_same_domain = same_domain(file_a, file_b);
                let is_interface_pair = is_interface_impl_pair(file_a, file_b);

                // Get unique functions called in each direction
                let funcs_a_to_b = a_to_b_funcs
                    .get(&(file_a.clone(), file_b.clone()))
                    .map(|s| s.len())
                    .unwrap_or(0);
                let funcs_b_to_a = b_to_a_funcs
                    .get(&(file_a.clone(), file_b.clone()))
                    .map(|s| s.len())
                    .unwrap_or(0);

                // Concentrated coupling (few functions, many calls) is worse
                let is_concentrated = (funcs_a_to_b + funcs_b_to_a) < 6 && total >= 30;

                // Calculate severity with graph-aware adjustments
                let mut severity = if total >= 50 && count_b_to_a >= 15 && *count_a_to_b >= 15 {
                    Severity::High
                } else if total >= 30 {
                    Severity::Medium
                } else {
                    Severity::Low
                };

                // Reduce severity for legitimate patterns
                if is_same_domain || is_interface_pair {
                    severity = match severity {
                        Severity::High => Severity::Medium,
                        Severity::Medium => Severity::Low,
                        _ => Severity::Low,
                    };
                    debug!(
                        "Reduced severity for legitimate pattern: {} <-> {}",
                        file_a, file_b
                    );
                }

                // Increase severity for concentrated coupling
                if is_concentrated && !is_same_domain {
                    severity = match severity {
                        Severity::Low => Severity::Medium,
                        Severity::Medium => Severity::High,
                        _ => severity,
                    };
                }

                // Build notes about the coupling pattern
                let mut notes = Vec::new();
                if is_same_domain {
                    notes.push("📁 Same module (may be legitimate close coupling)".to_string());
                }
                if is_interface_pair {
                    notes.push("🔌 Interface/implementation pair detected".to_string());
                }
                if is_concentrated {
                    notes.push(format!(
                        "⚠️ Concentrated: {} functions called {} times",
                        funcs_a_to_b + funcs_b_to_a,
                        total
                    ));
                }

                let pattern_notes = if notes.is_empty() {
                    String::new()
                } else {
                    format!("\n\n**Analysis:**\n{}", notes.join("\n"))
                };

                // Skip very low severity findings for same-domain files
                if is_same_domain && severity == Severity::Low {
                    continue;
                }

                findings.push(Finding {
                    id: Uuid::new_v4().to_string(),
                    detector: "InappropriateIntimacyDetector".to_string(),
                    severity,
                    title: "Inappropriate Intimacy".to_string(),
                    description: format!(
                        "Bidirectional coupling:\n\
                         - {} → {} ({} calls to {} functions)\n\
                         - {} → {} ({} calls to {} functions){}",
                        file_a.rsplit('/').next().unwrap_or(file_a),
                        file_b.rsplit('/').next().unwrap_or(file_b),
                        count_a_to_b, funcs_a_to_b,
                        file_b.rsplit('/').next().unwrap_or(file_b),
                        file_a.rsplit('/').next().unwrap_or(file_a),
                        count_b_to_a, funcs_b_to_a,
                        pattern_notes
                    ),
                    affected_files: vec![file_a.clone().into(), file_b.clone().into()],
                    line_start: None,
                    line_end: None,
                    suggested_fix: Some(
                        if is_same_domain {
                            "These files are in the same module. Consider:\n\
                             1. Merging them if they represent one concept\n\
                             2. Extracting shared state/logic to a third file".to_string()
                        } else if is_concentrated {
                            "Concentrated coupling (few functions, many calls) suggests:\n\
                             1. Extract those functions to a shared utility\n\
                             2. Use dependency injection instead of direct calls".to_string()
                        } else {
                            "Extract shared functionality into a separate module, or \
                             merge these files if they're truly one concept".to_string()
                        }
                    ),
                    estimated_effort: Some(if severity == Severity::High {
                        "Large (4-8 hours)".to_string()
                    } else {
                        "Medium (2-4 hours)".to_string()
                    }),
                    category: Some("coupling".to_string()),
                    cwe_id: None,
                    why_it_matters: Some(
                        "Bidirectional coupling makes both files hard to change independently. \
                         A change in one often requires changes in the other, creating maintenance burden."
                            .to_string()
                    ),
                    ..Default::default()
                });
            }
        }

        info!(
            "InappropriateIntimacyDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_thresholds() {
        let detector = InappropriateIntimacyDetector::new();
        assert_eq!(detector.thresholds.threshold_high, 40);
        assert_eq!(detector.thresholds.threshold_medium, 20);
        assert_eq!(detector.thresholds.min_mutual_access, 8);
    }

    #[test]
    fn test_severity_calculation() {
        let detector = InappropriateIntimacyDetector::new();

        assert_eq!(detector.calculate_severity(10), Severity::Low);
        assert_eq!(detector.calculate_severity(20), Severity::Medium);
        assert_eq!(detector.calculate_severity(30), Severity::Medium);
        assert_eq!(detector.calculate_severity(40), Severity::High);
        assert_eq!(detector.calculate_severity(60), Severity::High);
    }
}
