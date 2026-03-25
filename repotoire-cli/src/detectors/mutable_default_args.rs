//! Mutable Default Arguments Detector (Python)
//!
//! Graph-enhanced detection of mutable default arguments:
//! - Uses graph to check how many times the function is called
//! - Higher severity for frequently-called functions (more likely to trigger bug)
//! - Detects the specific mutable type for better suggestions

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphQueryExt;
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::LazyLock;
use tracing::info;

static MUTABLE_DEFAULT: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"def\s+(\w+)\s*\([^)]*(\w+)\s*=\s*(\[\]|\{\}|set\(\)|list\(\)|dict\(\)|defaultdict\(\)|Counter\(\)|deque\(\))").expect("valid regex")
    });
#[allow(dead_code)]
static FUNC_NAME: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"def\s+(\w+)").expect("valid regex"));

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
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
}

impl MutableDefaultArgsDetector {
    crate::detectors::detector_new!(50);

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

    fn file_extensions(&self) -> &'static [&'static str] {
        &["py"]
    }

    fn detect(&self, ctx: &crate::detectors::analysis_context::AnalysisContext) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let files = &ctx.as_file_provider();
        let i = graph.interner();
        let mut findings = vec![];

        // Pre-build lookup: (file_path, func_name) → CodeNode — O(N) once instead of O(N) per match
        let func_lookup: std::collections::HashMap<(String, String), crate::graph::store_models::CodeNode> = graph
            .get_functions()
            .into_iter()
            .map(|f| ((f.path(i).to_string(), f.node_name(i).to_string()), f))
            .collect();

        for path in files.files_with_extension("py") {
            if findings.len() >= self.max_findings {
                break;
            }

            let path_str = path.to_string_lossy().to_string();

            if let Some(content) = files.content(path) {
                let lines: Vec<&str> = content.lines().collect();

                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    if let Some(caps) = MUTABLE_DEFAULT.captures(line) {
                        let func_name = caps.get(1).map(|m| m.as_str()).unwrap_or("unknown");
                        let param_name = caps.get(2).map(|m| m.as_str()).unwrap_or("arg");
                        let mutable_type = caps.get(3).map(|m| m.as_str()).unwrap_or("[]");

                        // Get function info from pre-built lookup — O(1)
                        let key = (path_str.clone(), func_name.to_string());
                        let (caller_count, is_public, func_end) = if let Some(func) = func_lookup.get(&key) {
                            let callers = graph.get_callers(func.qn(crate::graph::interner::global_interner()));
                            let is_pub = !func_name.starts_with('_');
                            (callers.len(), is_pub, func.line_end as usize)
                        } else {
                            (0, !func_name.starts_with('_'), i + 20)
                        };

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


impl super::RegisteredDetector for MutableDefaultArgsDetector {
    fn create(init: &super::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::new(init.repo_path))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_mutable_default_list() {
        let store = GraphStore::in_memory();
        let detector = MutableDefaultArgsDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("service.py", "def collect_items(items=[]):\n    items.append(\"new\")\n    return items\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect mutable default argument []. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
        assert!(
            findings[0].title.contains("[]"),
            "Title should mention the mutable default type"
        );
    }

    #[test]
    fn test_no_finding_for_immutable_default() {
        let store = GraphStore::in_memory();
        let detector = MutableDefaultArgsDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("service.py", "def process(count=0, name=\"default\", flag=True):\n    return count + len(name)\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag immutable defaults. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
