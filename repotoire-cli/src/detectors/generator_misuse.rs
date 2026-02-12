//! Generator misuse detector
//!
//! Graph-enhanced detection of generator anti-patterns:
//! - Single-yield generators (should be simple functions)
//! - Generators that are immediately list()-ified
//! - Uses graph to find how generators are consumed

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;
use uuid::Uuid;

static GENERATOR_DEF: OnceLock<Regex> = OnceLock::new();
static YIELD_STMT: OnceLock<Regex> = OnceLock::new();
static LIST_CALL: OnceLock<Regex> = OnceLock::new();

fn generator_def() -> &'static Regex {
    GENERATOR_DEF.get_or_init(|| Regex::new(r"def\s+(\w+)\s*\(").unwrap())
}

fn yield_stmt() -> &'static Regex {
    YIELD_STMT.get_or_init(|| Regex::new(r"\byield\b").unwrap())
}

fn list_call() -> &'static Regex {
    LIST_CALL.get_or_init(|| Regex::new(r"list\s*\(\s*(\w+)\s*\(").unwrap())
}

/// Detects generator functions with only one yield statement
pub struct GeneratorMisuseDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
}

impl GeneratorMisuseDetector {
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            repository_path: PathBuf::from("."),
            max_findings: 50,
        }
    }

    pub fn with_path(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            config: DetectorConfig::new(),
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Count yield statements in a function
    fn count_yields(lines: &[&str], func_start: usize, indent: usize) -> (usize, bool) {
        let mut count = 0;
        let mut in_loop = false;
        
        for line in lines.iter().skip(func_start + 1) {
            let current_indent = line.chars().take_while(|c| c.is_whitespace()).count();
            
            // Stop if we've left the function
            if !line.trim().is_empty() && current_indent <= indent {
                break;
            }
            
            // Track if yield is inside a loop
            if line.contains("for ") || line.contains("while ") {
                in_loop = true;
            }
            
            if yield_stmt().is_match(line) {
                count += 1;
            }
        }
        
        (count, in_loop)
    }

    /// Find all generators that are immediately converted to list
    fn find_list_wrapped_generators(&self, graph: &GraphStore) -> HashSet<String> {
        let mut wrapped = HashSet::new();
        
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if ext != "py" { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for cap in list_call().captures_iter(&content) {
                    if let Some(func_name) = cap.get(1) {
                        wrapped.insert(func_name.as_str().to_string());
                    }
                }
            }
        }
        
        wrapped
    }

    /// Check if generator is consumed lazily anywhere
    fn is_consumed_lazily(&self, func_name: &str, graph: &GraphStore) -> bool {
        // Check callers to see how the generator is consumed
        if let Some(func) = graph.get_functions().into_iter().find(|f| f.name == func_name) {
            let callers = graph.get_callers(&func.qualified_name);
            
            for caller in callers {
                if let Ok(content) = std::fs::read_to_string(&caller.file_path) {
                    // Check if caller iterates lazily (for loop) vs list()
                    let has_lazy = content.contains(&format!("for ")) && 
                                   content.contains(&format!("{}(", func_name));
                    let has_list = content.contains(&format!("list({}(", func_name));
                    
                    if has_lazy && !has_list {
                        return true;
                    }
                }
            }
        }
        
        false
    }
}

impl Default for GeneratorMisuseDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for GeneratorMisuseDetector {
    fn name(&self) -> &'static str {
        "GeneratorMisuseDetector"
    }

    fn description(&self) -> &'static str {
        "Detects single-yield generators that add unnecessary complexity"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        
        // Find generators that are always list()-wrapped
        let list_wrapped = self.find_list_wrapped_generators(graph);
        
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            // Only Python has generators with yield
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if ext != "py" { continue; }
            
            let path_str = path.to_string_lossy().to_string();
            
            // Skip test files
            if path_str.contains("test") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();
                
                for (i, line) in lines.iter().enumerate() {
                    if let Some(caps) = generator_def().captures(line) {
                        let func_name = caps.get(1).map(|m| m.as_str()).unwrap_or("");
                        let indent = line.chars().take_while(|c| c.is_whitespace()).count();
                        
                        // Check if it's a generator (has yield)
                        let (yield_count, yield_in_loop) = Self::count_yields(&lines, i, indent);
                        
                        if yield_count == 0 { continue; }  // Not a generator
                        
                        // Single yield outside loop = probably should be a simple return
                        if yield_count == 1 && !yield_in_loop {
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "GeneratorMisuseDetector".to_string(),
                                severity: Severity::Low,
                                title: format!("Single-yield generator: `{}`", func_name),
                                description: format!(
                                    "Generator `{}` only yields once and not in a loop. \
                                     Consider using a simple function with return instead.\n\n\
                                     **Why it matters:** Single-yield generators add complexity \
                                     without the lazy evaluation benefits.",
                                    func_name
                                ),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: None,
                                suggested_fix: Some(format!(
                                    "Convert to a simple function:\n\n\
                                     ```python\n\
                                     # Instead of:\n\
                                     def {}(...):\n\
                                         yield some_value\n\
                                     \n\
                                     # Use:\n\
                                     def {}(...):\n\
                                         return some_value\n\
                                     ```",
                                    func_name, func_name
                                )),
                                estimated_effort: Some("10 minutes".to_string()),
                                category: Some("code-quality".to_string()),
                                cwe_id: None,
                                why_it_matters: Some(
                                    "Single-yield generators require callers to use next() or iterate, \
                                     adding complexity without benefits.".to_string()
                                ),
                                ..Default::default()
                            });
                        }
                        
                        // Generator always wrapped in list() = defeats the purpose
                        if list_wrapped.contains(func_name) && !self.is_consumed_lazily(func_name, graph) {
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "GeneratorMisuseDetector".to_string(),
                                severity: Severity::Low,
                                title: format!("Generator always list()-wrapped: `{}`", func_name),
                                description: format!(
                                    "Generator `{}` is always wrapped in `list()`, defeating lazy evaluation.\n\n\
                                     **Analysis:** No callers consume this generator lazily.",
                                    func_name
                                ),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: None,
                                suggested_fix: Some(format!(
                                    "Consider returning a list directly:\n\n\
                                     ```python\n\
                                     # Instead of:\n\
                                     def {}(...):\n\
                                         for item in items:\n\
                                             yield transform(item)\n\
                                     \n\
                                     # result = list({}(...))  # Always converted\n\
                                     \n\
                                     # Use:\n\
                                     def {}(...):\n\
                                         return [transform(item) for item in items]\n\
                                     ```",
                                    func_name, func_name, func_name
                                )),
                                estimated_effort: Some("15 minutes".to_string()),
                                category: Some("performance".to_string()),
                                cwe_id: None,
                                why_it_matters: Some(
                                    "Generators wrapped in list() lose lazy evaluation benefits \
                                     and add unnecessary overhead.".to_string()
                                ),
                                ..Default::default()
                            });
                        }
                    }
                }
            }
        }
        
        info!("GeneratorMisuseDetector found {} findings (graph-aware)", findings.len());
        Ok(findings)
    }
}
