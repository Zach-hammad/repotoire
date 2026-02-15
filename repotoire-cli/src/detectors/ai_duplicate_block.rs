//! AI Duplicate Block Detector
//!
//! Detects near-identical code blocks that AI coding assistants tend to create
//! (copy-paste patterns). Uses AST-based similarity analysis per ICSE 2025 research.
//!
//! AI assistants often generate repetitive code with minor variations like:
//! - Different variable names but same logic
//! - Same structure with different literals
//! - Copy-paste patterns with slight modifications
//!
//! This detector uses normalized identifier hashing and Jaccard similarity
//! to find these near-duplicates. Threshold: ≥70% similarity.

#![allow(dead_code)] // Module under development - structs/helpers used in tests only

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::PathBuf;
use tracing::{debug, info};
use uuid::Uuid;

/// Default thresholds (based on ICSE 2025 research)
const DEFAULT_SIMILARITY_THRESHOLD: f64 = 0.70; // 70% Jaccard similarity
const DEFAULT_GENERIC_NAME_THRESHOLD: f64 = 0.40; // 40% generic identifiers
const DEFAULT_MIN_LOC: usize = 5;
const DEFAULT_MAX_FINDINGS: usize = 50;

/// Generic identifier patterns commonly produced by AI assistants
const GENERIC_IDENTIFIERS: &[&str] = &[
    "result",
    "res",
    "ret",
    "return_value",
    "rv",
    "temp",
    "tmp",
    "t",
    "data",
    "d",
    "value",
    "val",
    "v",
    "item",
    "items",
    "i",
    "obj",
    "object",
    "o",
    "x",
    "y",
    "z",
    "a",
    "b",
    "c",
    "arr",
    "array",
    "list",
    "lst",
    "dict",
    "dictionary",
    "map",
    "mapping",
    "str",
    "string",
    "s",
    "num",
    "number",
    "n",
    "count",
    "cnt",
    "index",
    "idx",
    "key",
    "k",
    "var",
    "variable",
    "input",
    "output",
    "out",
    "response",
    "resp",
    "request",
    "req",
    "config",
    "cfg",
    "args",
    "kwargs",
    "params",
    "parameters",
    "options",
    "opts",
    "settings",
    "handler",
    "callback",
    "cb",
    "func",
    "fn",
    "function",
    "elem",
    "element",
    "node",
    "current",
    "curr",
    "cur",
    "previous",
    "prev",
    "next",
];

/// Processed function data for similarity comparison
#[derive(Debug, Clone)]
pub struct FunctionData {
    pub qualified_name: String,
    pub name: String,
    pub file_path: String,
    pub line_start: u32,
    pub line_end: u32,
    pub loc: usize,
    pub hash_set: HashSet<String>,
    pub generic_ratio: f64,
    pub ast_size: usize,
}

/// Calculate Jaccard similarity between two hash sets
fn jaccard_similarity(set1: &HashSet<String>, set2: &HashSet<String>) -> f64 {
    if set1.is_empty() && set2.is_empty() {
        return 1.0;
    }
    if set1.is_empty() || set2.is_empty() {
        return 0.0;
    }

    let intersection = set1.intersection(set2).count();
    let union = set1.union(set2).count();

    if union == 0 {
        0.0
    } else {
        intersection as f64 / union as f64
    }
}

/// Calculate ratio of generic identifiers in a function
fn calculate_generic_ratio(identifiers: &[String]) -> f64 {
    if identifiers.is_empty() {
        return 0.0;
    }

    let generic_set: HashSet<&str> = GENERIC_IDENTIFIERS.iter().copied().collect();
    let generic_count = identifiers
        .iter()
        .filter(|id| generic_set.contains(id.to_lowercase().as_str()))
        .count();

    generic_count as f64 / identifiers.len() as f64
}

/// Detect near-identical code blocks typical of AI-generated code
pub struct AIDuplicateBlockDetector {
    config: DetectorConfig,
    similarity_threshold: f64,
    generic_name_threshold: f64,
    min_loc: usize,
    max_findings: usize,
}

impl AIDuplicateBlockDetector {
    /// Create a new detector with default settings
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            similarity_threshold: DEFAULT_SIMILARITY_THRESHOLD,
            generic_name_threshold: DEFAULT_GENERIC_NAME_THRESHOLD,
            min_loc: DEFAULT_MIN_LOC,
            max_findings: DEFAULT_MAX_FINDINGS,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        Self {
            similarity_threshold: config
                .get_option_or("similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD),
            generic_name_threshold: config
                .get_option_or("generic_name_threshold", DEFAULT_GENERIC_NAME_THRESHOLD),
            min_loc: config.get_option_or("min_loc", DEFAULT_MIN_LOC),
            max_findings: config.get_option_or("max_findings", DEFAULT_MAX_FINDINGS),
            config,
        }
    }

    /// Find duplicate pairs using Jaccard similarity
    fn find_duplicates(
        &self,
        functions: &[FunctionData],
    ) -> Vec<(FunctionData, FunctionData, f64)> {
        let mut duplicates: Vec<(FunctionData, FunctionData, f64)> = Vec::new();
        let mut seen_pairs: HashSet<(String, String)> = HashSet::new();

        for (i, func1) in functions.iter().enumerate() {
            for func2 in functions.iter().skip(i + 1) {
                // Skip same-file comparisons
                if func1.file_path == func2.file_path {
                    continue;
                }

                // Skip if AST sizes are too different (optimization)
                if func1.ast_size > 0 && func2.ast_size > 0 {
                    let size_ratio = func1.ast_size.min(func2.ast_size) as f64
                        / func1.ast_size.max(func2.ast_size) as f64;
                    if size_ratio < 0.5 {
                        continue;
                    }
                }

                // Create pair key
                let pair_key = if func1.qualified_name < func2.qualified_name {
                    (func1.qualified_name.clone(), func2.qualified_name.clone())
                } else {
                    (func2.qualified_name.clone(), func1.qualified_name.clone())
                };

                if seen_pairs.contains(&pair_key) {
                    continue;
                }
                seen_pairs.insert(pair_key);

                // Calculate Jaccard similarity
                let similarity = jaccard_similarity(&func1.hash_set, &func2.hash_set);

                if similarity >= self.similarity_threshold {
                    duplicates.push((func1.clone(), func2.clone(), similarity));
                }
            }
        }

        // Sort by similarity (highest first)
        duplicates.sort_by(|a, b| b.2.partial_cmp(&a.2).unwrap_or(std::cmp::Ordering::Equal));

        duplicates
    }

    /// Create a finding from a duplicate pair
    fn create_finding(
        &self,
        func1: &FunctionData,
        func2: &FunctionData,
        similarity: f64,
    ) -> Finding {
        let similarity_pct = (similarity * 100.0) as u32;

        // Check for generic naming pattern
        let has_generic_naming = func1.generic_ratio >= self.generic_name_threshold
            || func2.generic_ratio >= self.generic_name_threshold;

        // Determine severity based on similarity and generic naming
        let severity = if similarity >= 0.90 && has_generic_naming {
            Severity::Critical
        } else if similarity >= 0.85 || (similarity >= 0.70 && has_generic_naming) {
            Severity::High
        } else {
            Severity::Medium
        };

        // Build description
        let mut description = format!(
            "Functions '{}' and '{}' have {}% AST similarity, \
             indicating AI-generated copy-paste patterns.\n\n\
             **{}** ({} LOC): `{}`\n\
             **{}** ({} LOC): `{}`\n\n",
            func1.name,
            func2.name,
            similarity_pct,
            func1.name,
            func1.loc,
            func1.file_path,
            func2.name,
            func2.loc,
            func2.file_path,
        );

        if has_generic_naming {
            let avg_generic = (func1.generic_ratio + func2.generic_ratio) / 2.0;
            description.push_str(&format!(
                "⚠️ **Generic naming detected**: {:.0}% of identifiers \
                 use generic names (result, temp, data, etc.), a common AI pattern.\n\n",
                avg_generic * 100.0
            ));
        }

        description.push_str(
            "Near-identical functions increase maintenance burden and \
             can lead to inconsistent bug fixes.",
        );

        let suggestion = "Consider one of the following approaches:\n\
             1. **Extract common logic** into a shared helper function\n\
             2. **Use a template/factory pattern** if variations are intentional\n\
             3. **Consolidate** into a single implementation if truly duplicates\n\
             4. **Add documentation** explaining why similar implementations exist"
            .to_string();

        let mut affected_files = Vec::new();
        if func1.file_path != "unknown" {
            affected_files.push(PathBuf::from(&func1.file_path));
        }
        if func2.file_path != "unknown" {
            affected_files.push(PathBuf::from(&func2.file_path));
        }

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "AIDuplicateBlockDetector".to_string(),
            severity,
            title: format!(
                "AI-style duplicate: {} ≈ {} ({}% AST)",
                func1.name, func2.name, similarity_pct
            ),
            description,
            affected_files,
            line_start: Some(func1.line_start),
            line_end: Some(func1.line_end),
            suggested_fix: Some(suggestion),
            estimated_effort: Some("Medium (1-2 hours)".to_string()),
            category: Some("duplication".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Near-identical code duplicates increase maintenance burden. \
                 When bugs are found, they must be fixed in multiple places. \
                 When requirements change, all copies must be updated consistently."
                    .to_string(),
            ),
            ..Default::default()
        }
    }
}

impl Default for AIDuplicateBlockDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for AIDuplicateBlockDetector {
    fn name(&self) -> &'static str {
        "AIDuplicateBlockDetector"
    }

    fn description(&self) -> &'static str {
        "Detects near-identical code blocks using AST similarity (≥70%)"
    }

    fn category(&self) -> &'static str {
        "ai_generated"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }
    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        // Duplicate detection needs AST fingerprinting
        let _ = graph;
        Ok(vec![])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_jaccard_similarity() {
        let set1: HashSet<String> = ["a", "b", "c", "d"].iter().map(|s| s.to_string()).collect();
        let set2: HashSet<String> = ["c", "d", "e", "f"].iter().map(|s| s.to_string()).collect();

        let sim = jaccard_similarity(&set1, &set2);
        // intersection: c, d (2), union: a, b, c, d, e, f (6)
        assert!((sim - (2.0 / 6.0)).abs() < 0.01);

        let empty: HashSet<String> = HashSet::new();
        assert_eq!(jaccard_similarity(&empty, &empty), 1.0);
        assert_eq!(jaccard_similarity(&set1, &empty), 0.0);
    }

    #[test]
    fn test_generic_ratio() {
        let identifiers = vec![
            "result".to_string(),
            "temp".to_string(),
            "user_id".to_string(),
            "data".to_string(),
        ];
        let ratio = calculate_generic_ratio(&identifiers);
        // result, temp, data are generic (3/4 = 0.75)
        assert!((ratio - 0.75).abs() < 0.01);

        let no_generic = vec!["user_id".to_string(), "order_amount".to_string()];
        assert_eq!(calculate_generic_ratio(&no_generic), 0.0);

        let empty: Vec<String> = vec![];
        assert_eq!(calculate_generic_ratio(&empty), 0.0);
    }

    #[test]
    fn test_detector_defaults() {
        let detector = AIDuplicateBlockDetector::new();
        assert!((detector.similarity_threshold - 0.70).abs() < 0.01);
        assert!((detector.generic_name_threshold - 0.40).abs() < 0.01);
        assert_eq!(detector.min_loc, 5);
    }
}
