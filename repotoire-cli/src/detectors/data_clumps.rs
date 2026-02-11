//! Data Clumps Detector
//!
//! Graph-aware detection of parameter groups that appear together across functions.
//! Uses function parameter data from the graph to identify missing abstractions.

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
            min_occurrences: 3,
        }
    }
}

/// Known parameter patterns and suggested names
fn suggest_struct_name(params: &[String]) -> String {
    let param_set: HashSet<&str> = params.iter().map(|s| s.as_str()).collect();
    
    // Check known patterns
    let patterns: &[(&[&str], &str)] = &[
        (&["x", "y"], "Point"),
        (&["x", "y", "z"], "Point3D"),
        (&["width", "height"], "Size"),
        (&["start", "end"], "Range"),
        (&["min", "max"], "Range"),
        (&["host", "port"], "Address"),
        (&["first_name", "last_name"], "Name"),
        (&["first_name", "last_name", "email"], "PersonInfo"),
        (&["latitude", "longitude"], "Coordinates"),
        (&["lat", "lng"], "Coordinates"),
        (&["red", "green", "blue"], "RGB"),
        (&["r", "g", "b"], "RGB"),
        (&["username", "password"], "Credentials"),
        (&["user", "password"], "Credentials"),
        (&["path", "filename"], "FilePath"),
        (&["name", "email"], "Contact"),
        (&["start_date", "end_date"], "DateRange"),
        (&["created_at", "updated_at"], "Timestamps"),
    ];
    
    for (pattern_params, name) in patterns {
        let pattern_set: HashSet<&str> = pattern_params.iter().copied().collect();
        if pattern_set.is_subset(&param_set) {
            return name.to_string();
        }
    }
    
    // Generate from first param
    if let Some(first) = params.first() {
        let base: String = first
            .split('_')
            .map(|w| {
                let mut c = w.chars();
                match c.next() {
                    None => String::new(),
                    Some(f) => f.to_uppercase().chain(c).collect(),
                }
            })
            .collect();
        return format!("{}Params", base);
    }
    
    "ParamGroup".to_string()
}

pub struct DataClumpsDetector {
    config: DetectorConfig,
    thresholds: DataClumpsThresholds,
}

impl DataClumpsDetector {
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            thresholds: DataClumpsThresholds::default(),
        }
    }

    pub fn with_config(config: DetectorConfig) -> Self {
        let thresholds = DataClumpsThresholds {
            min_params: config.get_option_or("min_params", 3),
            min_occurrences: config.get_option_or("min_occurrences", 3),
        };
        Self { config, thresholds }
    }

    /// Extract parameter names from function's parameter property
    fn extract_params(&self, func: &crate::graph::CodeNode) -> Vec<String> {
        // Try to get params from properties
        if let Some(params_str) = func.get_str("params") {
            return params_str
                .split(',')
                .map(|p| p.trim().to_lowercase())
                .filter(|p| !p.is_empty() && !p.starts_with('_') && p != "self" && p != "this")
                .collect();
        }
        
        // Try param_count to see if function has parameters
        if let Some(count) = func.param_count() {
            if count >= self.thresholds.min_params as i64 {
                // We know it has params but can't extract names
                // Return empty - will need parser enhancement
            }
        }
        
        vec![]
    }

    /// Find parameter clumps across functions
    fn find_clumps(&self, graph: &GraphStore) -> Vec<DataClump> {
        let functions = graph.get_functions();
        
        // Build map of param sets to functions
        let mut param_to_funcs: HashMap<Vec<String>, Vec<FuncInfo>> = HashMap::new();
        
        for func in &functions {
            let params = self.extract_params(func);
            if params.len() < self.thresholds.min_params {
                continue;
            }
            
            // Generate all combinations of min_params or more
            for size in self.thresholds.min_params..=params.len().min(6) {
                for combo in combinations(&params, size) {
                    let mut key = combo;
                    key.sort();
                    
                    param_to_funcs
                        .entry(key)
                        .or_default()
                        .push(FuncInfo {
                            name: func.name.clone(),
                            file: func.file_path.clone(),
                            line: func.line_start,
                        });
                }
            }
        }
        
        // Filter to clumps meeting threshold
        let mut clumps: Vec<DataClump> = param_to_funcs
            .into_iter()
            .filter(|(_, funcs)| funcs.len() >= self.thresholds.min_occurrences)
            .map(|(params, funcs)| DataClump { params, funcs })
            .collect();
        
        // Remove subsets
        clumps = self.remove_subsets(clumps);
        
        // Sort by function count
        clumps.sort_by(|a, b| b.funcs.len().cmp(&a.funcs.len()));
        
        clumps
    }

    /// Remove clumps that are subsets of larger ones
    fn remove_subsets(&self, mut clumps: Vec<DataClump>) -> Vec<DataClump> {
        clumps.sort_by(|a, b| b.params.len().cmp(&a.params.len()));
        
        let mut result = Vec::new();
        
        for clump in clumps {
            let param_set: HashSet<&String> = clump.params.iter().collect();
            let func_set: HashSet<&str> = clump.funcs.iter().map(|f| f.name.as_str()).collect();
            
            let is_subset = result.iter().any(|existing: &DataClump| {
                let existing_params: HashSet<&String> = existing.params.iter().collect();
                let existing_funcs: HashSet<&str> = existing.funcs.iter().map(|f| f.name.as_str()).collect();
                param_set.is_subset(&existing_params) && func_set.is_subset(&existing_funcs)
            });
            
            if !is_subset {
                result.push(clump);
            }
        }
        
        result
    }

    fn calculate_severity(&self, func_count: usize) -> Severity {
        if func_count >= 6 {
            Severity::High
        } else if func_count >= 4 {
            Severity::Medium
        } else {
            Severity::Low
        }
    }
}

struct DataClump {
    params: Vec<String>,
    funcs: Vec<FuncInfo>,
}

struct FuncInfo {
    name: String,
    file: String,
    line: u32,
}

/// Generate combinations of k items
fn combinations(items: &[String], k: usize) -> Vec<Vec<String>> {
    if k > items.len() {
        return vec![];
    }
    if k == items.len() {
        return vec![items.to_vec()];
    }
    if k == 0 {
        return vec![vec![]];
    }
    
    let mut result = Vec::new();
    let mut indices: Vec<usize> = (0..k).collect();
    let n = items.len();
    
    loop {
        result.push(indices.iter().map(|&i| items[i].clone()).collect());
        
        let mut i = k;
        while i > 0 {
            i -= 1;
            if indices[i] < n - k + i {
                break;
            }
        }
        
        if i == 0 && indices[0] >= n - k {
            break;
        }
        
        indices[i] += 1;
        for j in (i + 1)..k {
            indices[j] = indices[j - 1] + 1;
        }
    }
    
    result
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
        "Detects parameter groups that appear together across functions"
    }

    fn category(&self) -> &'static str {
        "code_smell"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();
        
        let clumps = self.find_clumps(graph);
        
        for clump in clumps {
            let severity = self.calculate_severity(clump.funcs.len());
            let struct_name = suggest_struct_name(&clump.params);
            let params_str = clump.params.join(", ");
            
            let func_list: String = clump.funcs
                .iter()
                .take(5)
                .map(|f| format!("  - {} ({}:{})", f.name, f.file, f.line))
                .collect::<Vec<_>>()
                .join("\n");
            
            let more_note = if clump.funcs.len() > 5 {
                format!("\n  ... and {} more functions", clump.funcs.len() - 5)
            } else {
                String::new()
            };
            
            let files: Vec<PathBuf> = clump.funcs
                .iter()
                .map(|f| PathBuf::from(&f.file))
                .collect::<HashSet<_>>()
                .into_iter()
                .collect();

            findings.push(Finding {
                id: Uuid::new_v4().to_string(),
                detector: "DataClumpsDetector".to_string(),
                severity,
                title: format!("Data clump: ({})", params_str),
                description: format!(
                    "Parameters **({})** appear together in **{} functions**.\n\n\
                     This suggests a missing abstraction - consider extracting a `{}` struct.\n\n\
                     **Affected functions:**\n{}{}",
                    params_str,
                    clump.funcs.len(),
                    struct_name,
                    func_list,
                    more_note
                ),
                affected_files: files,
                line_start: clump.funcs.first().map(|f| f.line),
                line_end: None,
                suggested_fix: Some(format!(
                    "Extract parameters into a struct:\n\n\
                     ```rust\n\
                     struct {} {{\n\
                     {}\n\
                     }}\n\
                     ```\n\n\
                     Then refactor functions to accept `{}` instead of {} separate parameters.",
                    struct_name,
                    clump.params.iter().map(|p| format!("    {}: Type,", p)).collect::<Vec<_>>().join("\n"),
                    struct_name,
                    clump.params.len()
                )),
                estimated_effort: Some(if clump.funcs.len() >= 6 {
                    "Large (2-4 hours)".to_string()
                } else {
                    "Medium (1-2 hours)".to_string()
                }),
                category: Some("code_smell".to_string()),
                cwe_id: None,
                why_it_matters: Some(
                    "Data clumps indicate missing abstractions. Grouping related parameters \
                     into a struct improves readability and makes changes easier."
                        .to_string()
                ),
                ..Default::default()
            });
        }

        info!("DataClumpsDetector found {} findings", findings.len());
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_suggest_struct_name() {
        assert_eq!(suggest_struct_name(&["x".to_string(), "y".to_string()]), "Point");
        assert_eq!(suggest_struct_name(&["host".to_string(), "port".to_string()]), "Address");
        assert_eq!(suggest_struct_name(&["foo".to_string(), "bar".to_string(), "baz".to_string()]), "FooParams");
    }

    #[test]
    fn test_combinations() {
        let items = vec!["a".to_string(), "b".to_string(), "c".to_string()];
        let combos = combinations(&items, 2);
        assert_eq!(combos.len(), 3); // ab, ac, bc
    }

    #[test]
    fn test_severity() {
        let detector = DataClumpsDetector::new();
        assert_eq!(detector.calculate_severity(3), Severity::Low);
        assert_eq!(detector.calculate_severity(4), Severity::Medium);
        assert_eq!(detector.calculate_severity(6), Severity::High);
    }
}
