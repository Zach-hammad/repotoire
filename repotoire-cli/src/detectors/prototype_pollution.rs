//! Prototype Pollution Detector (JavaScript)
//!
//! Graph-enhanced detection of prototype pollution:
//! - Trace user input flow to merge/extend operations
//! - Identify vulnerable patterns (lodash.merge, deepmerge)
//! - Check for sanitization in the call chain

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static POLLUTION_PATTERN: OnceLock<Regex> = OnceLock::new();
static USER_INPUT: OnceLock<Regex> = OnceLock::new();
static SANITIZATION: OnceLock<Regex> = OnceLock::new();

fn pollution_pattern() -> &'static Regex {
    POLLUTION_PATTERN.get_or_init(|| {
        Regex::new(r"(__proto__|prototype\s*\[|Object\.assign\(|\.extend\(|lodash\.merge|_\.merge|deepmerge|Object\.setPrototypeOf|Reflect\.set)").expect("valid regex")
    })
}

fn user_input() -> &'static Regex {
    USER_INPUT.get_or_init(|| {
        Regex::new(r"(req\.(body|query|params|headers)|request\.(body|query)|ctx\.(request|body)|input|JSON\.parse)").expect("valid regex")
    })
}

fn sanitization() -> &'static Regex {
    SANITIZATION.get_or_init(|| {
        Regex::new(r"(hasOwnProperty|Object\.keys|Object\.create\(null\)|delete.*__proto__|filter|sanitize|validate|clean)").expect("valid regex")
    })
}

/// Categorize the pollution pattern
fn categorize_pattern(line: &str) -> (&'static str, &'static str) {
    if line.contains("__proto__") {
        return ("direct", "Direct __proto__ access");
    }
    if line.contains("prototype[") {
        return ("bracket", "Dynamic prototype access");
    }
    if line.contains("Object.assign") {
        return ("assign", "Object.assign merge");
    }
    if line.contains("lodash") || line.contains("_.merge") {
        return ("lodash", "Lodash deep merge (CVE-2019-10744)");
    }
    if line.contains("deepmerge") {
        return ("deepmerge", "deepmerge library");
    }
    if line.contains("extend") {
        return ("extend", "jQuery-style extend");
    }
    if line.contains("setPrototypeOf") {
        return ("setproto", "setPrototypeOf manipulation");
    }
    ("other", "Object manipulation")
}

pub struct PrototypePollutionDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl PrototypePollutionDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Check if user input flows into this line
    fn has_user_input_flow(lines: &[&str], current_line: usize) -> (bool, Option<String>) {
        // Check current line
        if user_input().is_match(lines[current_line]) {
            if let Some(m) = user_input().find(lines[current_line]) {
                return (true, Some(m.as_str().to_string()));
            }
        }

        // Check previous lines for variable assignments from user input
        let start = current_line.saturating_sub(15);
        for line in &lines[start..current_line] {
            if user_input().is_match(line) {
                // Look for variable assignment
                if let Some(m) = user_input().find(line) {
                    return (true, Some(m.as_str().to_string()));
                }
            }
        }

        (false, None)
    }

    /// Check if there's sanitization before this line
    fn has_sanitization(lines: &[&str], current_line: usize) -> bool {
        let start = current_line.saturating_sub(10);
        for line in &lines[start..current_line] {
            if sanitization().is_match(line) {
                return true;
            }
        }
        false
    }

    /// Find containing function
    fn find_containing_function(
        graph: &dyn crate::graph::GraphQuery,
        file_path: &str,
        line: u32,
    ) -> Option<(String, usize)> {
        graph
            .get_functions()
            .into_iter()
            .find(|f| f.file_path == file_path && f.line_start <= line && f.line_end >= line)
            .map(|f| {
                let callers = graph.get_callers(&f.qualified_name).len();
                (f.name, callers)
            })
    }

    /// Check if function receives external data
    fn receives_external_data(
        graph: &dyn crate::graph::GraphQuery,
        func_name: &str,
        file_path: &str,
    ) -> bool {
        // Check if function is called from route handlers
        if let Some(func) = graph
            .get_functions()
            .into_iter()
            .find(|f| f.file_path == file_path && f.name == func_name)
        {
            let callers = graph.get_callers(&func.qualified_name);
            for caller in callers {
                let caller_lower = caller.name.to_lowercase();
                if caller_lower.contains("route")
                    || caller_lower.contains("handle")
                    || caller_lower.contains("api")
                    || caller_lower.contains("controller")
                    || caller_lower.contains("endpoint")
                {
                    return true;
                }
            }
        }
        false
    }
}

impl Detector for PrototypePollutionDetector {
    fn name(&self) -> &'static str {
        "prototype-pollution"
    }
    fn description(&self) -> &'static str {
        "Detects prototype pollution vulnerabilities"
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

            let path_str = path.to_string_lossy().to_string();

            // Skip test/vendor
            if crate::detectors::base::is_test_path(&path_str) || path_str.contains("node_modules")
            {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "js" | "ts" | "jsx" | "tsx") {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();

                for (i, line) in lines.iter().enumerate() {
                    // Skip comments
                    let trimmed = line.trim();
                    if trimmed.starts_with("//") || trimmed.starts_with("*") {
                        continue;
                    }

                    if !pollution_pattern().is_match(line) {
                        continue;
                    }

                    let (pattern_type, pattern_desc) = categorize_pattern(line);
                    let (has_input, input_source) = Self::has_user_input_flow(&lines, i);
                    let has_sanitization = Self::has_sanitization(&lines, i);
                    let containing_func =
                        Self::find_containing_function(graph, &path_str, (i + 1) as u32);

                    // Check if function receives external data via graph
                    let receives_external = containing_func
                        .as_ref()
                        .map(|(name, _)| Self::receives_external_data(graph, name, &path_str))
                        .unwrap_or(false);

                    // Skip if no user input and no external data
                    if !has_input && !receives_external {
                        continue;
                    }

                    // Skip if sanitized
                    if has_sanitization {
                        continue;
                    }

                    // Calculate severity
                    let severity = if has_input {
                        Severity::Critical // Direct user input flow
                    } else if receives_external {
                        Severity::High // Called from route handlers
                    } else {
                        Severity::Medium
                    };

                    // Build notes
                    let mut notes = Vec::new();
                    notes.push(format!("🔍 Pattern: {}", pattern_desc));
                    if let Some(source) = &input_source {
                        notes.push(format!("⚠️ User input from: `{}`", source));
                    }
                    if receives_external {
                        notes.push(
                            "🌐 Function receives external data via route handler".to_string(),
                        );
                    }
                    if let Some((func_name, callers)) = &containing_func {
                        notes.push(format!(
                            "📦 In function: `{}` ({} callers)",
                            func_name, callers
                        ));
                    }

                    let context_notes = format!("\n\n**Analysis:**\n{}", notes.join("\n"));

                    let suggestion = match pattern_type {
                        "lodash" => 
                            "Lodash <= 4.17.11 is vulnerable (CVE-2019-10744). Options:\n\n\
                             1. **Update lodash** to >= 4.17.12\n\
                             2. **Use safe merge:**\n\
                             ```javascript\n\
                             // Instead of _.merge(target, userInput)\n\
                             const sanitized = JSON.parse(JSON.stringify(userInput));\n\
                             delete sanitized.__proto__;\n\
                             delete sanitized.constructor;\n\
                             _.merge(target, sanitized);\n\
                             ```".to_string(),
                        "assign" =>
                            "Use a null-prototype object or sanitize keys:\n\n\
                             ```javascript\n\
                             // Create object without prototype\n\
                             const safe = Object.create(null);\n\
                             Object.assign(safe, sanitizedInput);\n\
                             \n\
                             // Or filter dangerous keys\n\
                             const sanitize = (obj) => {\n\
                               const clean = {};\n\
                               for (const key of Object.keys(obj)) {\n\
                                 if (key !== '__proto__' && key !== 'constructor') {\n\
                                   clean[key] = obj[key];\n\
                                 }\n\
                               }\n\
                               return clean;\n\
                             };\n\
                             ```".to_string(),
                        _ =>
                            "Prevent prototype pollution:\n\n\
                             ```javascript\n\
                             // 1. Create null-prototype objects\n\
                             const obj = Object.create(null);\n\
                             \n\
                             // 2. Freeze the prototype\n\
                             Object.freeze(Object.prototype);\n\
                             \n\
                             // 3. Validate keys before setting\n\
                             if (key !== '__proto__' && key !== 'constructor' && key !== 'prototype') {\n\
                               obj[key] = value;\n\
                             }\n\
                             ```".to_string(),
                    };

                    findings.push(Finding {
                        id: String::new(),
                        detector: "PrototypePollutionDetector".to_string(),
                        severity,
                        title: format!("Prototype pollution: {}", pattern_desc),
                        description: format!(
                            "Object merge/extend operation with user-controlled input can pollute Object.prototype.{}",
                            context_notes
                        ),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some((i + 1) as u32),
                        line_end: Some((i + 1) as u32),
                        suggested_fix: Some(suggestion),
                        estimated_effort: Some("20 minutes".to_string()),
                        category: Some("security".to_string()),
                        cwe_id: Some("CWE-1321".to_string()),
                        why_it_matters: Some(
                            "Prototype pollution allows attackers to inject properties into Object.prototype, \
                             which affects ALL objects in the application. This can lead to:\n\
                             • Remote Code Execution (via gadget chains)\n\
                             • Denial of Service\n\
                             • Authentication bypass\n\
                             • Property injection".to_string()
                        ),
                        ..Default::default()
                    });
                }
            }
        }

        // Supplement with intra-function taint analysis
        let taint_analyzer = crate::detectors::taint::TaintAnalyzer::new();
        let intra_paths = crate::detectors::data_flow::run_intra_function_taint(
            &taint_analyzer,
            graph,
            crate::detectors::taint::TaintCategory::CodeInjection,
            &self.repository_path,
        );
        let mut seen: std::collections::HashSet<(String, u32)> = findings
            .iter()
            .filter_map(|f| {
                f.affected_files
                    .first()
                    .map(|p| (p.to_string_lossy().to_string(), f.line_start.unwrap_or(0)))
            })
            .collect();
        for path in intra_paths.iter().filter(|p| !p.is_sanitized) {
            let loc = (path.sink_file.clone(), path.sink_line);
            if !seen.insert(loc) {
                continue;
            }
            findings.push(crate::detectors::data_flow::taint_path_to_finding(
                path,
                "PrototypePollutionDetector",
                "Prototype Pollution",
            ));
            if findings.len() >= self.max_findings {
                break;
            }
        }

        info!(
            "PrototypePollutionDetector found {} findings (graph-aware + taint)",
            findings.len()
        );
        Ok(findings)
    }
}
