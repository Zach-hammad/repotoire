//! Mutable Default Arguments Detector (Python)
//!
//! Graph-enhanced detection of mutable default arguments:
//! - Uses graph to check how many times the function is called
//! - Higher severity for frequently-called functions (more likely to trigger bug)
//! - Detects the specific mutable type for better suggestions

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static MUTABLE_DEFAULT: OnceLock<Regex> = OnceLock::new();
static FUNC_NAME: OnceLock<Regex> = OnceLock::new();

fn mutable_default() -> &'static Regex {
    MUTABLE_DEFAULT.get_or_init(|| {
        Regex::new(r"def\s+(\w+)\s*\([^)]*(\w+)\s*=\s*(\[\]|\{\}|set\(\)|list\(\)|dict\(\)|defaultdict\(\)|Counter\(\)|deque\(\))").expect("valid regex")
    })
}

fn func_name() -> &'static Regex {
    FUNC_NAME.get_or_init(|| Regex::new(r"def\s+(\w+)").expect("valid regex"))
}

/// Get the appropriate fix based on mutable type
fn get_fix_example(mutable_type: &str, param_name: &str) -> String {
    match mutable_type {
        "[]" | "list()" => format!(
            "```python\n\
             def func({param}: list = None):\n\
             {param} = {param} if {param} is not None else []\n\
             ```",
            param = param_name
        ),
        "{}" | "dict()" => format!(
            "```python\n\
             def func({param}: dict = None):\n\
             {param} = {param} if {param} is not None else {{}}\n\
             ```",
            param = param_name
        ),
        "set()" => format!(
            "```python\n\
             def func({param}: set = None):\n\
             {param} = {param} if {param} is not None else set()\n\
             ```",
            param = param_name
        ),
        _ => format!(
            "```python\n\
             def func({param}=None):\n\
             {param} = {param} if {param} is not None else <default>\n\
             ```",
            param = param_name
        ),
    }
}

pub struct MutableDefaultArgsDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl MutableDefaultArgsDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Find function info from graph
    fn get_function_info(
        graph: &dyn crate::graph::GraphQuery,
        file_path: &str,
        func_name: &str,
    ) -> (usize, bool) {
        if let Some(func) = graph
            .get_functions()
            .into_iter()
            .find(|f| f.file_path == file_path && f.name == func_name)
        {
            let callers = graph.get_callers(&func.qualified_name);
            let is_public = !func_name.starts_with('_');
            (callers.len(), is_public)
        } else {
            (0, !func_name.starts_with('_'))
        }
    }

    /// Check if function modifies the default arg (makes bug more likely)
    fn modifies_default(
        content: &str,
        func_start: usize,
        func_end: usize,
        param_name: &str,
    ) -> bool {
        let lines: Vec<&str> = content.lines().collect();

        for line in lines.get(func_start..func_end).unwrap_or(&[]) {
            let trimmed = line.trim();
            // Check for mutations: .append, .extend, []=, .update, .add, etc.
            if trimmed.contains(&format!("{}.append", param_name))
                || trimmed.contains(&format!("{}.extend", param_name))
                || trimmed.contains(&format!("{}.insert", param_name))
                || trimmed.contains(&format!("{}.update", param_name))
                || trimmed.contains(&format!("{}.add", param_name))
                || trimmed.contains(&format!("{}[", param_name)) && trimmed.contains("=")
            {
                return true;
            }
        }
        false
    }
}

impl Detector for MutableDefaultArgsDetector {
    fn name(&self) -> &'static str {
        "mutable-default-args"
    }
    fn description(&self) -> &'static str {
        "Detects mutable default arguments in Python"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
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
            if ext != "py" {
                continue;
            }

            let path_str = path.to_string_lossy().to_string();

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();

                for (i, line) in lines.iter().enumerate() {
                    if let Some(caps) = mutable_default().captures(line) {
                        let func_name = caps.get(1).map(|m| m.as_str()).unwrap_or("unknown");
                        let param_name = caps.get(2).map(|m| m.as_str()).unwrap_or("arg");
                        let mutable_type = caps.get(3).map(|m| m.as_str()).unwrap_or("[]");

                        // Get function info from graph
                        let (caller_count, is_public) =
                            Self::get_function_info(graph, &path_str, func_name);

                        // Find function end for mutation check
                        let func_end = graph
                            .get_functions()
                            .into_iter()
                            .find(|f| f.file_path == path_str && f.name == func_name)
                            .map(|f| f.line_end as usize)
                            .unwrap_or(i + 20);

                        let modifies = Self::modifies_default(&content, i, func_end, param_name);

                        // Calculate severity
                        let severity = if modifies && caller_count > 5 {
                            Severity::High // Mutates + called often = high risk
                        } else if modifies || caller_count > 3 || is_public {
                            Severity::Medium
                        } else {
                            Severity::Low // Private, rarely called
                        };

                        // Build context notes
                        let mut notes = Vec::new();
                        if caller_count > 0 {
                            notes.push(format!("📞 Called {} times in codebase", caller_count));
                        }
                        if modifies {
                            notes.push(format!(
                                "⚠️ Function modifies `{}` - bug will definitely manifest!",
                                param_name
                            ));
                        }
                        if is_public {
                            notes.push(
                                "🌐 Public function (could be called from external code)"
                                    .to_string(),
                            );
                        }

                        let context_notes = if notes.is_empty() {
                            String::new()
                        } else {
                            format!("\n\n**Analysis:**\n{}", notes.join("\n"))
                        };

                        let type_name = match mutable_type {
                            "[]" | "list()" => "list",
                            "{}" | "dict()" => "dict",
                            "set()" => "set",
                            _ => "mutable object",
                        };

                        findings.push(Finding {
                            id: String::new(),
                            detector: "MutableDefaultArgsDetector".to_string(),
                            severity,
                            title: format!("Mutable default {} `{}`", type_name, mutable_type),
                            description: format!(
                                "Mutable default `{} = {}` is shared between all calls to `{}`.\n\
                                 This is a classic Python gotcha that causes surprising bugs.{}",
                                param_name, mutable_type, func_name, context_notes
                            ),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some(format!(
                                "Use `None` as default and create the {} inside the function:\n\n{}",
                                type_name,
                                get_fix_example(mutable_type, param_name)
                            )),
                            estimated_effort: Some("5 minutes".to_string()),
                            category: Some("bug-risk".to_string()),
                            cwe_id: Some("CWE-1188".to_string()),
                            why_it_matters: Some(
                                "Python evaluates default arguments once at function definition time, not at each call. \
                                 If you mutate a mutable default (list, dict, set), the mutation persists across calls, \
                                 causing data to leak between invocations.".to_string()
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!(
            "MutableDefaultArgsDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
