//! Missing Docstrings Detector
//!
//! Graph-enhanced detection of missing documentation:
//! - Prioritize public functions and those with many callers
//! - Higher severity for entry points and API endpoints
//! - Suggest docstring format based on language

use crate::detectors::base::{Detector, DetectorConfig};
use uuid::Uuid;
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use tracing::info;

pub struct MissingDocstringsDetector {
    repository_path: PathBuf,
    max_findings: usize,
    min_lines: u32,
}

impl MissingDocstringsDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 100, min_lines: 5 }
    }

    /// Check if function is an API endpoint or entry point
    fn is_entry_point(func_name: &str, file_path: &str) -> bool {
        let name_lower = func_name.to_lowercase();
        let path_lower = file_path.to_lowercase();
        
        // API endpoints
        name_lower.starts_with("get_") || name_lower.starts_with("post_") ||
        name_lower.starts_with("put_") || name_lower.starts_with("delete_") ||
        name_lower.starts_with("handle_") || name_lower.starts_with("api_") ||
        name_lower.ends_with("_handler") || name_lower.ends_with("_endpoint") ||
        name_lower.ends_with("_view") || name_lower.ends_with("_route") ||
        // Entry points
        name_lower == "main" || name_lower == "run" || name_lower == "start" ||
        name_lower == "execute" || name_lower == "init" || name_lower == "setup" ||
        // Route files
        path_lower.contains("route") || path_lower.contains("view") ||
        path_lower.contains("controller") || path_lower.contains("handler")
    }

    /// Generate docstring template based on function
    fn generate_template(func_name: &str, param_count: Option<i64>, ext: &str) -> String {
        let params = param_count.unwrap_or(0) as usize;
        
        match ext {
            "py" => {
                let mut template = format!(
                    "```python\n\
                     def {}(...):\n\
                     \"\"\"\n\
                     Brief description of what the function does.\n",
                    func_name
                );
                if params > 0 {
                    template.push_str("\n    Args:\n");
                    for i in 0..params.min(3) {
                        template.push_str(&format!("        param{}: Description.\n", i + 1));
                    }
                }
                template.push_str("\n    Returns:\n        Description of return value.\n");
                template.push_str("\"\"\"\n```");
                template
            },
            "js" | "ts" => {
                let mut template = "```javascript\n\
                     /**\n\
                     * Brief description of what the function does.\n\
                     *\n".to_string();
                if params > 0 {
                    for i in 0..params.min(3) {
                        template.push_str(&format!(" * @param {{type}} param{} - Description.\n", i + 1));
                    }
                }
                template.push_str(" * @returns {{type}} Description of return value.\n */\n```");
                template
            },
            "rs" => {
                "```rust\n\
                     /// Brief description of what the function does.\n\
                     ///\n\
                     /// # Arguments\n\
                     ///\n\
                     /// * `param` - Description.\n\
                     ///\n\
                     /// # Returns\n\
                     ///\n\
                     /// Description of return value.\n\
                     ```".to_string()
            },
            "go" => {
                format!(
                    "```go\n\
                     // {} does something.\n\
                     //\n\
                     // Parameters:\n\
                     //   - param: description\n\
                     //\n\
                     // Returns description.\n\
                     ```",
                    func_name
                )
            },
            _ => "Add a docstring describing the function's purpose, parameters, and return value.".to_string()
        }
    }
}

impl Detector for MissingDocstringsDetector {
    fn name(&self) -> &'static str { "missing-docstrings" }
    fn description(&self) -> &'static str { "Detects functions without documentation" }

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        for func in graph.get_functions() {
            if findings.len() >= self.max_findings { break; }
            
            let lines = func.line_end.saturating_sub(func.line_start);
            if lines < self.min_lines { continue; }
            
            // Skip private functions (single underscore prefix)
            if func.name.starts_with('_') && !func.name.starts_with("__") { continue; }
            // Skip test functions
            if func.name.starts_with("test_") || func.file_path.contains("test") { continue; }
            // Skip generated/vendor code
            if func.file_path.contains("vendor") || func.file_path.contains("node_modules") { continue; }

            // Get caller count for prioritization
            let callers = graph.get_callers(&func.qualified_name);
            let caller_count = callers.len();
            
            // Check if entry point
            let is_entry = Self::is_entry_point(&func.name, &func.file_path);
            
            // Determine file extension
            let ext = func.file_path.rsplit('.').next().unwrap_or("");

            // Check for docstring
            let file_path = PathBuf::from(&func.file_path);
            if let Ok(content) = std::fs::read_to_string(&file_path) {
                let file_lines: Vec<&str> = content.lines().collect();
                let start = (func.line_start as usize).saturating_sub(1);
                let end = (start + 5).min(file_lines.len());
                
                let has_doc = file_lines.get(start..end).map(|s| {
                    s.iter().any(|l| {
                        l.contains("\"\"\"") || l.contains("'''") ||
                        l.contains("///") || l.contains("/**") ||
                        l.trim().starts_with("//") && l.len() > 10  // Meaningful comment
                    })
                }).unwrap_or(false);

                if !has_doc {
                    // Calculate severity based on importance
                    let severity = if is_entry {
                        Severity::Medium  // Entry points/APIs should be documented
                    } else if caller_count >= 5 {
                        Severity::Medium  // Highly used functions
                    } else if caller_count >= 2 {
                        Severity::Low
                    } else {
                        Severity::Low
                    };
                    
                    // Build context notes
                    let mut notes = Vec::new();
                    notes.push(format!("📏 {} lines", lines));
                    if caller_count > 0 {
                        notes.push(format!("📞 {} callers", caller_count));
                    }
                    if is_entry {
                        notes.push("🚪 Entry point / API endpoint".to_string());
                    }
                    if let Some(pc) = func.param_count() {
                        notes.push(format!("📝 {} parameters", pc));
                    }
                    
                    let context_notes = format!("\n\n**Analysis:**\n{}", notes.join("\n"));
                    
                    let template = Self::generate_template(&func.name, func.param_count(), ext);
                    
                    findings.push(Finding {
                        id: Uuid::new_v4().to_string(),
                        detector: "MissingDocstringsDetector".to_string(),
                        severity,
                        title: format!("Missing documentation: `{}`", func.name),
                        description: format!(
                            "Function `{}` has no documentation.{}",
                            func.name, context_notes
                        ),
                        affected_files: vec![file_path.clone()],
                        line_start: Some(func.line_start),
                        line_end: Some(func.line_start),
                        suggested_fix: Some(format!(
                            "Add a docstring:\n\n{}",
                            template
                        )),
                        estimated_effort: Some("10 minutes".to_string()),
                        category: Some("documentation".to_string()),
                        cwe_id: None,
                        why_it_matters: Some(if is_entry {
                            "Entry points and API endpoints are the first thing developers encounter. \
                             Good documentation helps them understand how to use your code.".to_string()
                        } else if caller_count >= 5 {
                            "This function is used by many other parts of the codebase. \
                             Documentation prevents misuse and makes maintenance easier.".to_string()
                        } else {
                            "Documentation helps future maintainers (including yourself) understand \
                             the function's purpose without reading the implementation.".to_string()
                        }),
                        ..Default::default()
                    });
                }
            }
        }
        
        // Sort by severity (most important first)
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));
        
        info!("MissingDocstringsDetector found {} findings (graph-aware)", findings.len());
        Ok(findings)
    }
}
