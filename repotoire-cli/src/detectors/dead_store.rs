//! Dead Store Detector
//!
//! Graph-aware detection of variables that are assigned but never read:
//! 1. Local dead stores (assigned, never read in same function)
//! 2. Cross-function analysis (variable passed to function that doesn't use it)

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::{debug, info};
use uuid::Uuid;

static ASSIGNMENT: OnceLock<Regex> = OnceLock::new();
static VAR_READ: OnceLock<Regex> = OnceLock::new();

fn assignment() -> &'static Regex {
    ASSIGNMENT.get_or_init(|| {
        Regex::new(r"^\s*(let|var|const|int|float|string|auto|mut)?\s*(\w+)\s*[:=]").unwrap()
    })
}

fn var_read() -> &'static Regex {
    VAR_READ.get_or_init(|| Regex::new(r"\b(\w+)\b").unwrap())
}

/// Skip patterns for common false positives
const SKIP_VARS: &[&str] = &[
    "_", "self", "this", "cls", "ctx", "err", "ok", "result",
    "i", "j", "k", "n", "x", "y", "z", // loop/math vars
];

pub struct DeadStoreDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl DeadStoreDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Check if a variable is used after a given line
    fn is_used_after(&self, var: &str, lines: &[&str], start_line: usize) -> bool {
        for line in lines.iter().skip(start_line + 1) {
            let trimmed = line.trim();
            
            // Skip comments
            if trimmed.starts_with("//") || trimmed.starts_with("#") || trimmed.starts_with("*") {
                continue;
            }
            
            // Check if var is read (not just assigned again)
            if let Some(assign_match) = assignment().captures(line) {
                if let Some(assigned_var) = assign_match.get(2) {
                    if assigned_var.as_str() == var {
                        // It's being reassigned - check if it's using itself (e.g., x = x + 1)
                        let rhs = line.split('=').nth(1).unwrap_or("");
                        if !rhs.contains(var) {
                            continue; // Pure reassignment, doesn't count as read
                        }
                    }
                }
            }
            
            // Check for any reference to the variable
            if line.contains(var) {
                // Make sure it's a word boundary match
                for word in var_read().find_iter(line) {
                    if word.as_str() == var {
                        return true;
                    }
                }
            }
        }
        
        false
    }

    /// Find dead stores using source analysis
    fn find_local_dead_stores(&self) -> Vec<Finding> {
        let mut findings = Vec::new();
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings {
                break;
            }
            
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py" | "js" | "ts" | "go" | "rs" | "java") {
                continue;
            }

            // Skip test files
            let path_str = path.to_string_lossy();
            if path_str.contains("/test") || path_str.contains("_test.") {
                continue;
            }

            let rel_path = path.strip_prefix(&self.repository_path).unwrap_or(path);

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();
                let mut seen_assignments: HashSet<(String, usize)> = HashSet::new();

                for (i, line) in lines.iter().enumerate() {
                    if let Some(caps) = assignment().captures(line) {
                        if let Some(var_match) = caps.get(2) {
                            let var = var_match.as_str();
                            
                            // Skip common patterns
                            if SKIP_VARS.contains(&var) || var.starts_with('_') {
                                continue;
                            }
                            
                            // Skip if we've already flagged this var at this line
                            if seen_assignments.contains(&(var.to_string(), i)) {
                                continue;
                            }

                            // Check if variable is used after this line
                            if !self.is_used_after(var, &lines, i) {
                                seen_assignments.insert((var.to_string(), i));
                                
                                findings.push(Finding {
                                    id: Uuid::new_v4().to_string(),
                                    detector: "DeadStoreDetector".to_string(),
                                    severity: Severity::Low,
                                    title: format!("Dead store: {}", var),
                                    description: format!(
                                        "Variable '{}' is assigned but never read afterward.\n\n\
                                         ```\n{}\n```",
                                        var, line.trim()
                                    ),
                                    affected_files: vec![rel_path.to_path_buf()],
                                    line_start: Some((i + 1) as u32),
                                    line_end: Some((i + 1) as u32),
                                    suggested_fix: Some(format!(
                                        "Options:\n\
                                         1. Remove the unused assignment\n\
                                         2. Use the variable '{}' in subsequent code\n\
                                         3. If intentional, prefix with underscore: _{}", 
                                        var, var
                                    )),
                                    estimated_effort: Some("5 minutes".to_string()),
                                    category: Some("dead-code".to_string()),
                                    cwe_id: Some("CWE-563".to_string()),
                                    why_it_matters: Some(
                                        "Dead stores indicate logic errors or leftover code. \
                                         They add confusion and may hide bugs."
                                            .to_string()
                                    ),
                                    ..Default::default()
                                });
                            }
                        }
                    }
                }
            }
        }

        findings
    }

    /// Use graph to find functions with unused parameters
    fn find_unused_params(&self, graph: &GraphStore) -> Vec<Finding> {
        let mut findings = Vec::new();
        
        // This would require parameter tracking which isn't fully in graph yet
        // Leaving as placeholder for future enhancement
        
        // For now, check functions with high param count that might have unused ones
        for func in graph.get_functions() {
            if let Some(param_count) = func.param_count() {
                if param_count >= 5 {
                    // Flag as potential smell - many params often means some unused
                    debug!(
                        "Function {} has {} parameters - potential for unused params",
                        func.name, param_count
                    );
                }
            }
        }
        
        findings
    }
}

impl Detector for DeadStoreDetector {
    fn name(&self) -> &'static str {
        "DeadStoreDetector"
    }

    fn description(&self) -> &'static str {
        "Detects variables assigned but never read"
    }

    fn category(&self) -> &'static str {
        "dead-code"
    }

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();

        // Source-based local dead store detection
        findings.extend(self.find_local_dead_stores());

        // Graph-based unused parameter detection
        findings.extend(self.find_unused_params(graph));

        info!("DeadStoreDetector found {} findings", findings.len());
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_used_after() {
        let detector = DeadStoreDetector::new(".");
        
        let lines = vec![
            "let x = 5",
            "let y = x + 1",
            "print(y)",
        ];
        
        assert!(detector.is_used_after("x", &lines, 0)); // x is used on line 1
        assert!(detector.is_used_after("y", &lines, 1)); // y is used on line 2
        assert!(!detector.is_used_after("z", &lines, 0)); // z never used
    }

    #[test]
    fn test_skip_patterns() {
        assert!(SKIP_VARS.contains(&"self"));
        assert!(SKIP_VARS.contains(&"_"));
        assert!(SKIP_VARS.contains(&"err"));
    }
}
