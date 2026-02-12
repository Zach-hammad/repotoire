//! Data Clumps Detector
//!
//! Graph-aware detection of parameter groups that appear together across functions.
//! Uses function parameter data from the graph to identify missing abstractions.
//!
//! Enhanced with call graph analysis:
//! - Higher severity if functions with same params CALL each other (strong coupling)
//! - Lower severity if functions are in different modules with no call relationship
//! - Identifies refactoring opportunities where params travel through call chains

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

    #[allow(dead_code)] // Builder pattern method
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
                            qualified_name: func.qualified_name.clone(),
                            file: func.file_path.clone(),
                            line: func.line_start,
                        });
                }
            }
        }
        
        // Filter to clumps meeting threshold and analyze call relationships
        let mut clumps: Vec<DataClump> = param_to_funcs
            .into_iter()
            .filter(|(_, funcs)| funcs.len() >= self.thresholds.min_occurrences)
            .map(|(params, funcs)| {
                // Analyze call relationships between functions in this clump
                let (call_count, is_chain) = self.analyze_call_relationships(graph, &funcs);
                DataClump { 
                    params, 
                    funcs,
                    call_relationships: call_count,
                    is_call_chain: is_chain,
                }
            })
            .collect();
        
        // Remove subsets
        clumps = self.remove_subsets(clumps);
        
        // Sort by call relationships first (stronger signal), then by function count
        clumps.sort_by(|a, b| {
            b.call_relationships.cmp(&a.call_relationships)
                .then(b.funcs.len().cmp(&a.funcs.len()))
        });
        
        clumps
    }

    /// Analyze call relationships between functions that share parameters
    fn analyze_call_relationships(&self, graph: &GraphStore, funcs: &[FuncInfo]) -> (usize, bool) {
        let func_qns: HashSet<&str> = funcs.iter().map(|f| f.qualified_name.as_str()).collect();
        let mut call_count = 0;
        let mut has_chain = false;
        
        for func in funcs {
            let callees = graph.get_callees(&func.qualified_name);
            for callee in &callees {
                if func_qns.contains(callee.qualified_name.as_str()) {
                    call_count += 1;
                    
                    // Check if callee also calls another function in the clump (chain)
                    let callee_callees = graph.get_callees(&callee.qualified_name);
                    for cc in &callee_callees {
                        if func_qns.contains(cc.qualified_name.as_str()) && cc.qualified_name != func.qualified_name {
                            has_chain = true;
                        }
                    }
                }
            }
        }
        
        (call_count, has_chain)
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

    /// Calculate severity based on function count and call relationships
    fn calculate_severity(&self, clump: &DataClump) -> Severity {
        let func_count = clump.funcs.len();
        let call_rels = clump.call_relationships;
        
        // Base severity from function count
        let base = if func_count >= 6 {
            Severity::High
        } else if func_count >= 4 {
            Severity::Medium
        } else {
            Severity::Low
        };
        
        // Upgrade severity if there are call relationships (stronger signal)
        // Functions that call each other with the same params = definite refactor target
        if clump.is_call_chain {
            // Params traveling through a call chain = HIGH priority
            return match base {
                Severity::Low => Severity::Medium,
                Severity::Medium => Severity::High,
                _ => Severity::High,
            };
        }
        
        if call_rels >= 3 {
            // Many mutual calls = boost severity
            return match base {
                Severity::Low => Severity::Medium,
                _ => base,
            };
        }
        
        base
    }
}

struct DataClump {
    params: Vec<String>,
    funcs: Vec<FuncInfo>,
    /// Number of call relationships between functions in this clump
    call_relationships: usize,
    /// Whether functions form a call chain (A->B->C all have same params)
    is_call_chain: bool,
}

struct FuncInfo {
    name: String,
    qualified_name: String,
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
            let severity = self.calculate_severity(&clump);
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
            
            // Add call relationship info if present
            let call_info = if clump.is_call_chain {
                "\n\n⚠️ **Call chain detected**: These parameters travel through a call chain, \
                 making refactoring especially valuable.".to_string()
            } else if clump.call_relationships > 0 {
                format!("\n\n📞 **{} call relationships** found between these functions.", 
                        clump.call_relationships)
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
                     **Affected functions:**\n{}{}{}",
                    params_str,
                    clump.funcs.len(),
                    struct_name,
                    func_list,
                    more_note,
                    call_info
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
                     Then refactor functions to accept `{}` instead of {} separate parameters.{}",
                    struct_name,
                    clump.params.iter().map(|p| format!("    {}: Type,", p)).collect::<Vec<_>>().join("\n"),
                    struct_name,
                    clump.params.len(),
                    if clump.is_call_chain { 
                        "\n\nSince these functions call each other, the refactoring can be done incrementally." 
                    } else { "" }
                )),
                estimated_effort: Some(if clump.funcs.len() >= 6 || clump.is_call_chain {
                    "Large (2-4 hours)".to_string()
                } else {
                    "Medium (1-2 hours)".to_string()
                }),
                category: Some("code_smell".to_string()),
                cwe_id: None,
                why_it_matters: Some(
                    "Data clumps indicate missing abstractions. Grouping related parameters \
                     into a struct improves readability, type safety, and makes changes easier."
                        .to_string()
                ),
                ..Default::default()
            });
        }

        info!("DataClumpsDetector found {} findings (graph-aware)", findings.len());
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
        
        // Test base severity from function count
        let clump_3 = DataClump {
            params: vec!["a".to_string(), "b".to_string(), "c".to_string()],
            funcs: vec![
                FuncInfo { name: "f1".to_string(), qualified_name: "mod::f1".to_string(), file: "a.rs".to_string(), line: 1 },
                FuncInfo { name: "f2".to_string(), qualified_name: "mod::f2".to_string(), file: "a.rs".to_string(), line: 10 },
                FuncInfo { name: "f3".to_string(), qualified_name: "mod::f3".to_string(), file: "a.rs".to_string(), line: 20 },
            ],
            call_relationships: 0,
            is_call_chain: false,
        };
        assert_eq!(detector.calculate_severity(&clump_3), Severity::Low);
        
        let clump_4 = DataClump {
            params: vec!["a".to_string(), "b".to_string(), "c".to_string()],
            funcs: vec![
                FuncInfo { name: "f1".to_string(), qualified_name: "mod::f1".to_string(), file: "a.rs".to_string(), line: 1 },
                FuncInfo { name: "f2".to_string(), qualified_name: "mod::f2".to_string(), file: "a.rs".to_string(), line: 10 },
                FuncInfo { name: "f3".to_string(), qualified_name: "mod::f3".to_string(), file: "a.rs".to_string(), line: 20 },
                FuncInfo { name: "f4".to_string(), qualified_name: "mod::f4".to_string(), file: "a.rs".to_string(), line: 30 },
            ],
            call_relationships: 0,
            is_call_chain: false,
        };
        assert_eq!(detector.calculate_severity(&clump_4), Severity::Medium);
        
        let clump_6 = DataClump {
            params: vec!["a".to_string(), "b".to_string(), "c".to_string()],
            funcs: vec![
                FuncInfo { name: "f1".to_string(), qualified_name: "mod::f1".to_string(), file: "a.rs".to_string(), line: 1 },
                FuncInfo { name: "f2".to_string(), qualified_name: "mod::f2".to_string(), file: "a.rs".to_string(), line: 10 },
                FuncInfo { name: "f3".to_string(), qualified_name: "mod::f3".to_string(), file: "a.rs".to_string(), line: 20 },
                FuncInfo { name: "f4".to_string(), qualified_name: "mod::f4".to_string(), file: "a.rs".to_string(), line: 30 },
                FuncInfo { name: "f5".to_string(), qualified_name: "mod::f5".to_string(), file: "a.rs".to_string(), line: 40 },
                FuncInfo { name: "f6".to_string(), qualified_name: "mod::f6".to_string(), file: "a.rs".to_string(), line: 50 },
            ],
            call_relationships: 0,
            is_call_chain: false,
        };
        assert_eq!(detector.calculate_severity(&clump_6), Severity::High);
        
        // Test call chain boost
        let clump_chain = DataClump {
            params: vec!["a".to_string(), "b".to_string(), "c".to_string()],
            funcs: vec![
                FuncInfo { name: "f1".to_string(), qualified_name: "mod::f1".to_string(), file: "a.rs".to_string(), line: 1 },
                FuncInfo { name: "f2".to_string(), qualified_name: "mod::f2".to_string(), file: "a.rs".to_string(), line: 10 },
                FuncInfo { name: "f3".to_string(), qualified_name: "mod::f3".to_string(), file: "a.rs".to_string(), line: 20 },
            ],
            call_relationships: 2,
            is_call_chain: true,  // Call chain boosts Low -> Medium
        };
        assert_eq!(detector.calculate_severity(&clump_chain), Severity::Medium);
    }
}
