//! Data Clumps detector - identifies parameter groups for extraction
//!
//! Data clumps are groups of parameters that frequently appear together across
//! multiple functions. This indicates a missing abstraction that should be
//! extracted into a struct or named type.
//!
//! Example:
//! ```text
//! fn process_user(first_name: &str, last_name: &str, email: &str) { ... }
//! fn validate_user(first_name: &str, last_name: &str, email: &str) { ... }
//! fn save_user(first_name: &str, last_name: &str, email: &str) { ... }
//! ```
//!
//! Should become:
//! ```text
//! struct UserInfo { first_name: String, last_name: String, email: String }
//! fn process_user(user: &UserInfo) { ... }
//! ```

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use tracing::{debug, info};
use uuid::Uuid;

/// Thresholds for data clumps detection
#[derive(Debug, Clone)]
pub struct DataClumpsThresholds {
    /// Minimum parameters to form a clump
    pub min_params: usize,
    /// Minimum functions sharing the clump
    pub min_occurrences: usize,
}

impl Default for DataClumpsThresholds {
    fn default() -> Self {
        Self {
            min_params: 3,
            min_occurrences: 4,
        }
    }
}

/// Common parameter patterns mapped to suggested class names
fn get_name_patterns() -> HashMap<Vec<&'static str>, &'static str> {
    let mut patterns = HashMap::new();
    patterns.insert(vec!["x", "y"], "Point");
    patterns.insert(vec!["x", "y", "z"], "Point3D");
    patterns.insert(vec!["width", "height"], "Size");
    patterns.insert(vec!["start", "end"], "Range");
    patterns.insert(vec!["host", "port"], "Address");
    patterns.insert(vec!["first_name", "last_name"], "Name");
    patterns.insert(vec!["first_name", "last_name", "email"], "PersonInfo");
    patterns.insert(vec!["latitude", "longitude"], "Coordinates");
    patterns.insert(vec!["lat", "lng"], "Coordinates");
    patterns.insert(vec!["lat", "lon"], "Coordinates");
    patterns.insert(vec!["red", "green", "blue"], "RGB");
    patterns.insert(vec!["r", "g", "b"], "RGB");
    patterns.insert(vec!["min", "max"], "Range");
    patterns.insert(vec!["start_date", "end_date"], "DateRange");
    patterns.insert(vec!["username", "password"], "Credentials");
    patterns.insert(vec!["user", "password"], "Credentials");
    patterns.insert(vec!["path", "filename"], "FilePath");
    patterns.insert(vec!["name", "email"], "Contact");
    patterns.insert(vec!["name", "email", "phone"], "Contact");
    patterns
}

/// Detects data clumps (parameter groups that appear together)
pub struct DataClumpsDetector {
    config: DetectorConfig,
    thresholds: DataClumpsThresholds,
}

impl DataClumpsDetector {
    /// Create a new detector with default thresholds
    pub fn new() -> Self {
        Self::with_thresholds(DataClumpsThresholds::default())
    }

    /// Create with custom thresholds
    pub fn with_thresholds(thresholds: DataClumpsThresholds) -> Self {
        Self {
            config: DetectorConfig::new(),
            thresholds,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        let thresholds = DataClumpsThresholds {
            min_params: config.get_option_or("min_params", 3),
            min_occurrences: config.get_option_or("min_occurrences", 4),
        };

        Self { config, thresholds }
    }

    /// Suggest a class name for a parameter set
    fn suggest_class_name(&self, params: &[String]) -> String {
        let param_set: HashSet<&str> = params.iter().map(|s| s.as_str()).collect();
        let patterns = get_name_patterns();

        // Check known patterns
        for (pattern_params, name) in &patterns {
            let pattern_set: HashSet<&str> = pattern_params.iter().copied().collect();
            if pattern_set.is_subset(&param_set) {
                return name.to_string();
            }
        }

        // Generate name from first parameter
        if let Some(first) = params.first() {
            let base = first
                .split('_')
                .map(|w| {
                    let mut chars = w.chars();
                    match chars.next() {
                        None => String::new(),
                        Some(c) => c.to_uppercase().chain(chars).collect(),
                    }
                })
                .collect::<String>();
            return format!("{}Info", base);
        }

        "ParamGroup".to_string()
    }

    /// Generate dataclass suggestion
    fn generate_suggestion(&self, params: &[String], class_name: &str) -> String {
        let fields: String = params
            .iter()
            .map(|p| format!("    {}: Any,  // TODO: add correct type", p))
            .collect::<Vec<_>>()
            .join("\n");

        format!(
            "Extract into a struct:\n\n\
             ```rust\n\
             struct {} {{\n\
             {}\n\
             }}\n\
             ```\n\n\
             Then refactor functions to accept a single `{}` parameter \
             instead of {} separate parameters.",
            class_name, fields, class_name, params.len()
        )
    }

    /// Calculate severity based on function count
    fn calculate_severity(&self, function_count: usize) -> Severity {
        if function_count >= 7 {
            Severity::High
        } else {
            Severity::Medium
        }
    }

    /// Estimate effort based on function count
    fn estimate_effort(&self, function_count: usize) -> String {
        if function_count >= 10 {
            "Large (1-2 days)".to_string()
        } else if function_count >= 6 {
            "Medium (4-8 hours)".to_string()
        } else {
            "Small (1-4 hours)".to_string()
        }
    }

    /// Create a finding for a data clump
    fn create_finding(
        &self,
        params: Vec<String>,
        functions: Vec<String>,
        file_paths: Vec<String>,
    ) -> Finding {
        let function_count = functions.len();
        let severity = self.calculate_severity(function_count);
        let class_name = self.suggest_class_name(&params);

        let params_display = params.join(", ");

        let mut func_display = functions.iter().take(5).cloned().collect::<Vec<_>>().join(", ");
        if functions.len() > 5 {
            func_display.push_str(&format!(" ... and {} more", functions.len() - 5));
        }

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "DataClumpsDetector".to_string(),
            severity,
            title: format!("Data clump: ({})", params_display),
            description: format!(
                "Parameters ({}) appear together in {} functions. \
                 This data clump suggests a missing abstraction that should be extracted \
                 into a struct to reduce parameter passing and improve code maintainability.\n\n\
                 **Affected functions:** {}",
                params_display, function_count, func_display
            ),
            affected_files: file_paths.into_iter().map(PathBuf::from).collect(),
            line_start: None,
            line_end: None,
            suggested_fix: Some(self.generate_suggestion(&params, &class_name)),
            estimated_effort: Some(self.estimate_effort(function_count)),
            category: Some("code_smell".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Data clumps indicate missing abstractions. When the same parameters \
                 travel together across multiple functions, they should be encapsulated \
                 in a dedicated type. This improves code readability, reduces parameter \
                 counts, and makes changes easier."
                    .to_string(),
            ),
            ..Default::default()
        }
    }

    /// Find parameter clumps from function data
    fn find_clumps(
        &self,
        functions_params: Vec<(String, HashSet<String>, Option<String>)>,
    ) -> Vec<(Vec<String>, Vec<String>, Vec<String>)> {
        // Count occurrences of each parameter combination
        let mut param_to_functions: HashMap<Vec<String>, HashSet<String>> = HashMap::new();
        let mut param_to_files: HashMap<Vec<String>, HashSet<String>> = HashMap::new();

        for (func_name, params, file_path) in &functions_params {
            let mut param_list: Vec<String> = params.iter().cloned().collect();
            param_list.sort();

            // Check all subsets of size >= min_params
            for size in self.thresholds.min_params..=param_list.len() {
                for combo in Self::combinations(&param_list, size) {
                    let mut key: Vec<String> = combo;
                    key.sort();

                    param_to_functions
                        .entry(key.clone())
                        .or_default()
                        .insert(func_name.clone());

                    if let Some(fp) = file_path {
                        param_to_files.entry(key).or_default().insert(fp.clone());
                    }
                }
            }
        }

        // Filter to clumps meeting threshold
        let mut clumps: Vec<(Vec<String>, HashSet<String>, HashSet<String>)> = param_to_functions
            .into_iter()
            .filter(|(_, funcs)| funcs.len() >= self.thresholds.min_occurrences)
            .map(|(params, funcs)| {
                let files = param_to_files.get(&params).cloned().unwrap_or_default();
                (params, funcs, files)
            })
            .collect();

        // Remove subsets
        clumps = self.remove_subsets(clumps);

        // Sort by function count descending
        clumps.sort_by(|a, b| b.1.len().cmp(&a.1.len()).then(b.0.len().cmp(&a.0.len())));

        // Convert to output format
        clumps
            .into_iter()
            .map(|(params, funcs, files)| {
                (
                    params,
                    funcs.into_iter().collect(),
                    files.into_iter().collect(),
                )
            })
            .collect()
    }

    /// Generate all combinations of k elements from a slice
    fn combinations(items: &[String], k: usize) -> Vec<Vec<String>> {
        let n = items.len();
        if k > n {
            return vec![];
        }
        if k == 0 {
            return vec![vec![]];
        }
        if k == n {
            return vec![items.to_vec()];
        }

        let mut result = Vec::new();
        let mut indices: Vec<usize> = (0..k).collect();

        loop {
            result.push(indices.iter().map(|&i| items[i].clone()).collect());

            // Find rightmost index that can be incremented
            let mut i = k;
            while i > 0 {
                i -= 1;
                if indices[i] < n - k + i {
                    break;
                }
            }

            if indices[i] >= n - k + i {
                break;
            }

            indices[i] += 1;
            for j in (i + 1)..k {
                indices[j] = indices[j - 1] + 1;
            }
        }

        result
    }

    /// Remove clumps that are subsets of larger clumps with same functions
    fn remove_subsets(
        &self,
        mut clumps: Vec<(Vec<String>, HashSet<String>, HashSet<String>)>,
    ) -> Vec<(Vec<String>, HashSet<String>, HashSet<String>)> {
        // Sort by param set size descending
        clumps.sort_by(|a, b| b.0.len().cmp(&a.0.len()));

        let mut result = Vec::new();

        for (param_set, functions, files) in clumps {
            let param_set_hashset: HashSet<&String> = param_set.iter().collect();
            let is_subset = result.iter().any(|(existing_params, existing_funcs, _): &(Vec<String>, HashSet<String>, HashSet<String>)| {
                let existing_set: HashSet<&String> = existing_params.iter().collect();
                param_set_hashset.is_subset(&existing_set) && functions.is_subset(existing_funcs)
            });

            if !is_subset {
                result.push((param_set, functions, files));
            }
        }

        result
    }
}

impl Default for DataClumpsDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for DataClumpsDetector {
    fn name(&self) -> &'static str {
        "DataClumpsDetector"
    }

    fn description(&self) -> &'static str {
        "Detects groups of parameters that frequently appear together"
    }

    fn category(&self) -> &'static str {
        "code_smell"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        // Data clumps need parameter analysis which we don't have yet
        // Return empty for now - would need parser to extract parameter names
        let _ = graph;
        Ok(vec![])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_thresholds() {
        let detector = DataClumpsDetector::new();
        assert_eq!(detector.thresholds.min_params, 3);
        assert_eq!(detector.thresholds.min_occurrences, 4);
    }

    #[test]
    fn test_suggest_class_name() {
        let detector = DataClumpsDetector::new();

        assert_eq!(
            detector.suggest_class_name(&["x".to_string(), "y".to_string()]),
            "Point"
        );
        assert_eq!(
            detector.suggest_class_name(&["host".to_string(), "port".to_string()]),
            "Address"
        );
        assert_eq!(
            detector.suggest_class_name(&["foo".to_string(), "bar".to_string()]),
            "FooInfo"
        );
    }

    #[test]
    fn test_severity_calculation() {
        let detector = DataClumpsDetector::new();

        assert_eq!(detector.calculate_severity(4), Severity::Medium);
        assert_eq!(detector.calculate_severity(6), Severity::Medium);
        assert_eq!(detector.calculate_severity(7), Severity::High);
        assert_eq!(detector.calculate_severity(10), Severity::High);
    }
}
