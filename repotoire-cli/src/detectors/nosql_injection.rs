//! NoSQL Injection Detector
//!
//! Graph-enhanced detection of NoSQL injection:
//! - Trace user input to MongoDB queries
//! - Detect dangerous operators ($where, $regex, etc.)
//! - Check for sanitization/validation in call chain

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static NOSQL_PATTERN: OnceLock<Regex> = OnceLock::new();
static DANGEROUS_OPS: OnceLock<Regex> = OnceLock::new();
static USER_INPUT: OnceLock<Regex> = OnceLock::new();

fn nosql_pattern() -> &'static Regex {
    NOSQL_PATTERN.get_or_init(|| {
        Regex::new(r"(?i)(\.find\(|\.findOne\(|\.findById\(|\.updateOne\(|\.updateMany\(|\.deleteOne\(|\.deleteMany\(|\.aggregate\(|\.countDocuments\(|db\.\w+\.)").expect("valid regex")
    })
}

fn dangerous_ops() -> &'static Regex {
    DANGEROUS_OPS.get_or_init(|| {
        Regex::new(r"(\$where|\$regex|\$expr|\$function|\$accumulator)").expect("valid regex")
    })
}

fn user_input() -> &'static Regex {
    USER_INPUT.get_or_init(|| {
        Regex::new(r"(req\.(body|query|params|headers)|request\.(body|query)|ctx\.(request|body)|input|JSON\.parse)").expect("valid regex")
    })
}

/// Categorize the type of NoSQL injection risk
fn categorize_risk(line: &str) -> (&'static str, &'static str) {
    if line.contains("$where") {
        return ("where", "$where allows JavaScript execution");
    }
    if line.contains("$regex") {
        return ("regex", "$regex with user input enables ReDoS");
    }
    if line.contains("$ne") || line.contains("$gt") || line.contains("$lt") {
        return ("operator", "Operator injection can bypass authentication");
    }
    if line.contains("$expr") || line.contains("$function") {
        return ("eval", "Expression evaluation can execute arbitrary code");
    }
    ("query", "Query injection")
}

pub struct NosqlInjectionDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl NosqlInjectionDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Check if this is actually an Array method, not MongoDB
    fn is_array_method(line: &str) -> bool {
        // Common array variable patterns
        let array_vars = [
            "items.find(",
            "list.find(",
            "array.find(",
            "results.find(",
            "data.find(",
            "options.find(",
            "elements.find(",
            "entries.find(",
            "records.find(",
            "rows.find(",
            "values.find(",
            "keys.find(",
        ];

        if array_vars.iter().any(|v| line.contains(v)) {
            return true;
        }

        // Check for array method chains
        if line.contains(".filter(")
            || line.contains(".map(")
            || line.contains(".some(")
            || line.contains(".every(")
            || line.contains("Array.")
            || line.contains("[].")
        {
            return true;
        }

        false
    }

    /// Check for sanitization in surrounding context
    fn has_sanitization(lines: &[&str], current_line: usize) -> bool {
        let start = current_line.saturating_sub(10);
        let context = lines[start..current_line].join(" ").to_lowercase();

        context.contains("sanitize")
            || context.contains("validate")
            || context.contains("escape")
            || context.contains("clean")
            || context.contains("tostring()")
            || context.contains("parseint")
            || context.contains("number(")
            || context.contains("boolean(")
            || context.contains("mongo-sanitize")
            || context.contains("express-mongo-sanitize")
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

    /// Check if function is a route handler (directly receives user input)
    fn is_route_handler(func_name: &str, file_path: &str) -> bool {
        let name_lower = func_name.to_lowercase();
        let path_lower = file_path.to_lowercase();

        name_lower.contains("handler")
            || name_lower.contains("controller")
            || name_lower.contains("route")
            || name_lower.contains("api")
            || name_lower.starts_with("get")
            || name_lower.starts_with("post")
            || name_lower.starts_with("put")
            || name_lower.starts_with("delete")
            || path_lower.contains("route")
            || path_lower.contains("controller")
            || path_lower.contains("handler")
    }
}

impl Detector for NosqlInjectionDetector {
    fn name(&self) -> &'static str {
        "nosql-injection"
    }
    fn description(&self) -> &'static str {
        "Detects NoSQL injection risks"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        for path in files.files_with_extensions(&["js", "ts", "py", "rb", "php"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            let path_str = path.to_string_lossy().to_string();

            // Skip test files
            if crate::detectors::base::is_test_path(&path_str) {
                continue;
            }

            if let Some(content) = files.content(path) {
                let lines: Vec<&str> = content.lines().collect();

                // Check if file has MongoDB context
                let has_mongo = content.contains("mongoose")
                    || content.contains("mongodb")
                    || content.contains("MongoClient")
                    || content.contains("pymongo")
                    || content.contains("Collection");

                if !has_mongo {
                    continue;
                }

                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    if !nosql_pattern().is_match(line) {
                        continue;
                    }
                    if Self::is_array_method(line) {
                        continue;
                    }

                    // Check for user input
                    let has_input = user_input().is_match(line);
                    let start = i.saturating_sub(5);
                    let context = lines[start..i].join(" ");
                    let has_input_nearby = user_input().is_match(&context);

                    if !has_input && !has_input_nearby {
                        continue;
                    }

                    // Check for sanitization
                    let is_sanitized = Self::has_sanitization(&lines, i);
                    if is_sanitized {
                        continue;
                    }

                    // Check for dangerous operators
                    let has_dangerous = dangerous_ops().is_match(line);
                    let (risk_type, risk_desc) = categorize_risk(line);

                    // Get function context
                    let containing_func =
                        Self::find_containing_function(graph, &path_str, (i + 1) as u32);
                    let is_handler = containing_func
                        .as_ref()
                        .map(|(name, _)| Self::is_route_handler(name, &path_str))
                        .unwrap_or(false);

                    // Calculate severity
                    let severity = if has_dangerous || (is_handler && has_input) {
                        Severity::Critical // dangerous operator or direct user input in route handler
                    } else if has_input {
                        Severity::High
                    } else {
                        Severity::Medium
                    };

                    // Build notes
                    let mut notes = Vec::new();
                    notes.push(format!("🔍 Risk type: {}", risk_desc));
                    if has_dangerous {
                        notes.push("⚠️ Uses dangerous operator".to_string());
                    }
                    if is_handler {
                        notes.push("🌐 In route handler (direct user input)".to_string());
                    }
                    if let Some((func_name, callers)) = &containing_func {
                        notes.push(format!(
                            "📦 In function: `{}` ({} callers)",
                            func_name, callers
                        ));
                    }

                    let context_notes = format!("\n\n**Analysis:**\n{}", notes.join("\n"));

                    let suggestion = match risk_type {
                        "where" =>
                            "**Never use $where with user input** - it executes JavaScript.\n\n\
                             ```javascript\n\
                             // Instead of:\n\
                             db.users.find({ $where: `this.name == '${userInput}'` });\n\
                             \n\
                             // Use:\n\
                             db.users.find({ name: userInput });  // Still sanitize!\n\
                             ```".to_string(),
                        "regex" =>
                            "Escape regex special characters or use literal match:\n\n\
                             ```javascript\n\
                             // Escape regex\n\
                             const escaped = userInput.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');\n\
                             db.users.find({ name: { $regex: escaped } });\n\
                             \n\
                             // Or use literal string match when possible\n\
                             db.users.find({ name: userInput });\n\
                             ```".to_string(),
                        "operator" =>
                            "Prevent operator injection by validating input types:\n\n\
                             ```javascript\n\
                             // User could send: { \"$ne\": \"\" } to bypass auth\n\
                             // Always validate/convert to expected type:\n\
                             const username = String(req.body.username);\n\
                             const password = String(req.body.password);\n\
                             \n\
                             // Or use mongo-sanitize\n\
                             const sanitize = require('mongo-sanitize');\n\
                             db.users.find({ username: sanitize(req.body.username) });\n\
                             ```".to_string(),
                        _ =>
                            "Sanitize all user input before using in queries:\n\n\
                             ```javascript\n\
                             const sanitize = require('mongo-sanitize');\n\
                             const cleanInput = sanitize(req.body);\n\
                             db.collection.find(cleanInput);\n\
                             ```".to_string(),
                    };

                    findings.push(Finding {
                        id: String::new(),
                        detector: "NosqlInjectionDetector".to_string(),
                        severity,
                        title: format!("NoSQL injection: {}", risk_desc),
                        description: format!(
                            "MongoDB query with user-controlled input can be exploited.{}",
                            context_notes
                        ),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some((i + 1) as u32),
                        line_end: Some((i + 1) as u32),
                        suggested_fix: Some(suggestion),
                        estimated_effort: Some("30 minutes".to_string()),
                        category: Some("security".to_string()),
                        cwe_id: Some("CWE-943".to_string()),
                        why_it_matters: Some(
                            "NoSQL injection can allow attackers to:\n\
                             • Bypass authentication ({ password: { $ne: '' } })\n\
                             • Extract data through $regex probing\n\
                             • Execute arbitrary JavaScript ($where)\n\
                             • Denial of service through ReDoS"
                                .to_string(),
                        ),
                        ..Default::default()
                    });
                }
            }
        }

        // Supplement with intra-function taint analysis (SSA-based)
        let taint_analyzer = crate::detectors::taint::TaintAnalyzer::new();
        let intra_paths = crate::detectors::data_flow::run_intra_function_taint(
            &taint_analyzer,
            graph,
            crate::detectors::taint::TaintCategory::SqlInjection,
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
                "NosqlInjectionDetector",
                "NoSQL Injection",
            ));
            if findings.len() >= self.max_findings {
                break;
            }
        }

        info!(
            "NosqlInjectionDetector found {} findings (graph-aware + taint)",
            findings.len()
        );
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_where_with_user_input() {
        let store = GraphStore::in_memory();
        let detector = NosqlInjectionDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("routes.js", "const mongoose = require('mongoose');\nconst User = mongoose.model('User');\n\nasync function findUser(req, res) {\n    const name = req.body.name;\n    const result = await User.find({ $where: `this.name == '${name}'` });\n    res.json(result);\n}\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).unwrap();
        assert!(
            !findings.is_empty(),
            "Should detect $where with user input from req.body"
        );
        assert!(
            findings.iter().any(|f| f.title.contains("$where")),
            "Finding should mention $where. Titles: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_safe_query() {
        let store = GraphStore::in_memory();
        let detector = NosqlInjectionDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("routes.js", "const mongoose = require('mongoose');\nconst User = mongoose.model('User');\n\nasync function findUser() {\n    const result = await User.find({ active: true });\n    return result;\n}\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).unwrap();
        assert!(
            findings.is_empty(),
            "Safe MongoDB query without user input should produce no findings, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
