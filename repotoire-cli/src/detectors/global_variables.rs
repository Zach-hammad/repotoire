//! Global Variables Detector
//!
//! Graph-enhanced detection of mutable global variables.
//! Uses graph to:
//! - Count how many functions read/write the global (impact analysis)
//! - Detect cross-module usage (higher severity)
//! - Find potential encapsulation points

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

static GLOBAL_PATTERN: OnceLock<Regex> = OnceLock::new();
static VAR_NAME: OnceLock<Regex> = OnceLock::new();

fn global_pattern() -> &'static Regex {
    GLOBAL_PATTERN.get_or_init(|| Regex::new(r"^(var\s+\w+\s*=|let\s+\w+\s*=|global\s+\w+|\w+\s*=\s*[^=])").unwrap())
}

fn var_name_pattern() -> &'static Regex {
    VAR_NAME.get_or_init(|| Regex::new(r"^(?:var|let|global)\s+(\w+)").unwrap())
}

pub struct GlobalVariablesDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl GlobalVariablesDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }

    /// Extract variable name from declaration
    fn extract_var_name(line: &str) -> Option<String> {
        if let Some(caps) = var_name_pattern().captures(line.trim()) {
            return caps.get(1).map(|m| m.as_str().to_string());
        }
        // Handle Python global statement
        if line.trim().starts_with("global ") {
            return line.trim()
                .strip_prefix("global ")
                .and_then(|s| s.split_whitespace().next())
                .map(|s| s.to_string());
        }
        None
    }

    /// Count how many functions in the file reference this variable
    fn count_usages(&self, content: &str, var_name: &str, declaration_line: usize) -> usize {
        let mut count = 0;
        let var_pattern = format!(r"\b{}\b", regex::escape(var_name));
        if let Ok(re) = Regex::new(&var_pattern) {
            for (i, line) in content.lines().enumerate() {
                if i == declaration_line - 1 { continue; } // Skip declaration
                if re.is_match(line) {
                    count += 1;
                }
            }
        }
        count
    }

    /// Check if variable is used by functions in other files
    fn check_cross_module_usage(&self, graph: &GraphStore, file_path: &str, _var_name: &str) -> bool {
        // Check if any function in other files might reference this
        // This is heuristic - we check if the file is imported by others
        let file_name = file_path.rsplit('/').next().unwrap_or(file_path);
        let module_name = file_name.split('.').next().unwrap_or("");
        
        // Look for imports of this module
        for (_, import_target) in graph.get_imports() {
            if import_target.contains(module_name) {
                return true;
            }
        }
        false
    }

    fn create_finding(
        &self, 
        path: &std::path::Path, 
        line: usize, 
        var_name: &str,
        usage_count: usize,
        is_cross_module: bool
    ) -> Finding {
        // Calculate severity based on impact
        let severity = if is_cross_module && usage_count > 5 {
            Severity::High  // Cross-module globals with many usages are dangerous
        } else if is_cross_module || usage_count > 10 {
            Severity::Medium
        } else {
            Severity::Low
        };
        
        let mut notes = Vec::new();
        if usage_count > 0 {
            notes.push(format!("📊 Used {} times in this file", usage_count));
        }
        if is_cross_module {
            notes.push("⚠️ Module is imported by others - global may leak".to_string());
        }
        
        let context_notes = if notes.is_empty() {
            String::new()
        } else {
            format!("\n\n**Impact Analysis:**\n{}", notes.join("\n"))
        };
        
        let suggestion = if is_cross_module {
            let capitalized = format!("{}{}",
                var_name.chars().next().map(|c| c.to_uppercase().to_string()).unwrap_or_default(),
                var_name.chars().skip(1).collect::<String>()
            );
            format!(
                "Cross-module globals are especially dangerous. Consider:\n\
                 1. Export a getter/setter function instead: `get{0}()`, `set{0}()`\n\
                 2. Encapsulate in a class with controlled access\n\
                 3. Use dependency injection",
                capitalized
            )
        } else if usage_count > 5 {
            "Many usages - consider:\n\
             1. Encapsulate in a module with getter/setter\n\
             2. Convert to a class instance\n\
             3. Pass as parameter instead".to_string()
        } else {
            "Use const if immutable, or encapsulate in a module/class.".to_string()
        };

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "GlobalVariablesDetector".to_string(),
            severity,
            title: format!("Global mutable variable: {}", var_name),
            description: format!(
                "Global mutable state '{}' makes code hard to reason about.{}",
                var_name, context_notes
            ),
            affected_files: vec![path.to_path_buf()],
            line_start: Some(line as u32),
            line_end: Some(line as u32),
            suggested_fix: Some(suggestion),
            estimated_effort: Some(if is_cross_module { "30 minutes".to_string() } else { "15 minutes".to_string() }),
            category: Some("code-quality".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Global state causes hidden dependencies between functions. \
                 Changes to globals can have unexpected effects throughout the codebase.".to_string()
            ),
            ..Default::default()
        }
    }
}

impl Detector for GlobalVariablesDetector {
    fn name(&self) -> &'static str { "global-variables" }
    fn description(&self) -> &'static str { "Detects mutable global variables" }

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let path_str = path.to_string_lossy().to_string();
                let mut in_function = false;
                let mut brace_depth = 0;
                
                for (i, line) in content.lines().enumerate() {
                    let trimmed = line.trim();
                    
                    // Track function scope
                    if trimmed.starts_with("def ") || trimmed.starts_with("function ") || 
                       trimmed.contains("=> {") || trimmed.starts_with("async ") {
                        in_function = true;
                    }
                    brace_depth += line.matches('{').count() as i32;
                    brace_depth -= line.matches('}').count() as i32;
                    if brace_depth == 0 && in_function { in_function = false; }
                    
                    // Skip if inside function
                    if in_function { continue; }
                    
                    // Skip constants, imports, classes
                    if trimmed.starts_with("const ") || trimmed.starts_with("import ") ||
                       trimmed.starts_with("from ") || trimmed.starts_with("class ") ||
                       trimmed.starts_with("#") || trimmed.starts_with("//") ||
                       trimmed.is_empty() || trimmed.starts_with("export ") {
                        continue;
                    }
                    
                    // Check for global assignment
                    let is_global = if ext == "py" {
                        trimmed.contains("global ")
                    } else {
                        trimmed.starts_with("var ") || trimmed.starts_with("let ")
                    };
                    
                    if is_global {
                        if let Some(var_name) = Self::extract_var_name(trimmed) {
                            let usage_count = self.count_usages(&content, &var_name, i + 1);
                            let is_cross_module = self.check_cross_module_usage(graph, &path_str, &var_name);
                            
                            findings.push(self.create_finding(
                                path, 
                                i + 1, 
                                &var_name,
                                usage_count,
                                is_cross_module
                            ));
                        }
                    }
                }
            }
        }
        
        info!("GlobalVariablesDetector found {} findings (graph-aware)", findings.len());
        Ok(findings)
    }
}
