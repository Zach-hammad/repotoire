//! Insecure Deserialization Detector
//!
//! Graph-enhanced detection of insecure deserialization.
//! Uses graph to:
//! - Trace data flow from user input to deserialization
//! - Identify route handlers with direct deserialization
//! - Check for sanitization in call chain

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static DESERIALIZE_PATTERN: OnceLock<Regex> = OnceLock::new();

fn deserialize_pattern() -> &'static Regex {
    DESERIALIZE_PATTERN.get_or_init(|| Regex::new(r"(?i)(JSON\.parse|yaml\.load|yaml\.safe_load|unserialize|ObjectInputStream|Marshal\.load|eval\s*\()").expect("valid regex"))
}

/// Categorize the deserialization method
fn categorize_deserialize(line: &str) -> (&'static str, &'static str, Severity) {
    let lower = line.to_lowercase();

    if lower.contains("objectinputstream") {
        return (
            "Java ObjectInputStream",
            "Can execute arbitrary code",
            Severity::Critical,
        );
    }
    if lower.contains("marshal.load") {
        return (
            "Ruby Marshal.load",
            "Can execute arbitrary code",
            Severity::Critical,
        );
    }
    if lower.contains("unserialize") && !lower.contains("safe_unserialize") {
        return (
            "PHP unserialize",
            "Can execute arbitrary code via magic methods",
            Severity::Critical,
        );
    }
    if lower.contains("yaml.load") && !lower.contains("safe_load") && !lower.contains("safeloader")
    {
        return (
            "YAML unsafe load",
            "Can execute arbitrary code via YAML tags",
            Severity::High,
        );
    }
    if lower.contains("eval") {
        return ("eval()", "Direct code execution", Severity::Critical);
    }
    if lower.contains("json.parse") {
        return (
            "JSON.parse",
            "Generally safe but may cause issues with __proto__",
            Severity::Low,
        );
    }

    ("Deserialization", "May be unsafe", Severity::Medium)
}

pub struct InsecureDeserializeDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl InsecureDeserializeDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Find containing function and context
    fn find_function_context(
        graph: &dyn crate::graph::GraphQuery,
        file_path: &str,
        line: u32,
    ) -> Option<(String, usize, bool)> {
        graph
            .get_functions()
            .into_iter()
            .find(|f| f.file_path == file_path && f.line_start <= line && f.line_end >= line)
            .map(|f| {
                let callers = graph.get_callers(&f.qualified_name);
                let name_lower = f.name.to_lowercase();

                // Check if this is a route handler
                let is_handler = name_lower.contains("handler")
                    || name_lower.contains("route")
                    || name_lower.contains("api")
                    || name_lower.contains("endpoint")
                    || name_lower.starts_with("get")
                    || name_lower.starts_with("post")
                    || name_lower.starts_with("put")
                    || name_lower.starts_with("delete");

                (f.name, callers.len(), is_handler)
            })
    }

    /// Check for validation/sanitization in surrounding code
    fn has_validation(lines: &[&str], current_line: usize) -> bool {
        let start = current_line.saturating_sub(10);
        let end = (current_line + 5).min(lines.len());
        let context = lines[start..end].join(" ").to_lowercase();

        context.contains("validate")
            || context.contains("sanitize")
            || context.contains("schema")
            || context.contains("allowlist")
            || context.contains("whitelist")
            || context.contains("instanceof")
            || context.contains("typeof ")
            || context.contains("zod.")
            || context.contains("joi.")
            || context.contains("yup.")
    }
}

impl Detector for InsecureDeserializeDetector {
    fn name(&self) -> &'static str {
        "insecure-deserialize"
    }
    fn description(&self) -> &'static str {
        "Detects insecure deserialization"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        for path in files.files_with_extensions(&["py", "js", "ts", "java", "php", "rb"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            let path_str = path.to_string_lossy().to_string();

            // Skip test files
            if crate::detectors::base::is_test_path(&path_str) {
                continue;
            }

            if let Some(content) = files.masked_content(path) {
                let lines: Vec<&str> = content.lines().collect();

                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    if deserialize_pattern().is_match(line) {
                        let has_user_input = line.contains("req.")
                            || line.contains("request")
                            || line.contains("body")
                            || line.contains("input")
                            || line.contains("params")
                            || line.contains("data")
                            || line.contains("payload");

                        // Skip if no user input indicator
                        if !has_user_input {
                            continue;
                        }

                        let line_num = (i + 1) as u32;
                        let (method, risk_desc, base_severity) = categorize_deserialize(line);

                        // Graph-enhanced analysis
                        let func_context = Self::find_function_context(graph, &path_str, line_num);
                        let has_validation = Self::has_validation(&lines, i);

                        // Calculate severity
                        let mut severity = base_severity;

                        // Reduce if validation found
                        if has_validation {
                            severity = match severity {
                                Severity::Critical => Severity::High,
                                Severity::High => Severity::Medium,
                                _ => Severity::Low,
                            };
                        }

                        // Boost if in route handler (direct user input)
                        if let Some((_, _, is_handler)) = &func_context {
                            if *is_handler && !has_validation {
                                severity = Severity::Critical;
                            }
                        }

                        // Skip JSON.parse unless in critical context
                        if method == "JSON.parse" && severity == Severity::Low {
                            continue;
                        }

                        // Build notes
                        let mut notes = Vec::new();
                        notes.push(format!("🔧 Method: {}", method));
                        notes.push(format!("⚠️ Risk: {}", risk_desc));

                        if let Some((func_name, callers, is_handler)) = &func_context {
                            notes.push(format!(
                                "📦 In function: `{}` ({} callers)",
                                func_name, callers
                            ));
                            if *is_handler {
                                notes.push(
                                    "🌐 In route handler (receives user input directly)"
                                        .to_string(),
                                );
                            }
                        }

                        if has_validation {
                            notes.push("✅ Validation/schema check found nearby".to_string());
                        }

                        let context_notes = format!("\n\n**Analysis:**\n{}", notes.join("\n"));

                        let suggestion = match method {
                            "Java ObjectInputStream" => 
                                "Use a safer alternative:\n\
                                 ```java\n\
                                 // Use JSON/Jackson with typed deserialization:\n\
                                 ObjectMapper mapper = new ObjectMapper();\n\
                                 mapper.configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, true);\n\
                                 MyClass obj = mapper.readValue(json, MyClass.class);\n\
                                 \n\
                                 // Or use OWASP's deserialization filter (Java 9+):\n\
                                 ObjectInputFilter.Config.setSerialFilter(filter);\n\
                                 ```".to_string(),
                            "YAML unsafe load" =>
                                "Use safe_load instead:\n\
                                 ```python\n\
                                 # Instead of:\n\
                                 data = yaml.load(user_input)  # DANGEROUS\n\
                                 \n\
                                 # Use:\n\
                                 data = yaml.safe_load(user_input)\n\
                                 # Or with SafeLoader:\n\
                                 data = yaml.load(user_input, Loader=yaml.SafeLoader)\n\
                                 ```".to_string(),
                            "PHP unserialize" =>
                                "Use JSON instead:\n\
                                 ```php\n\
                                 // Instead of:\n\
                                 $obj = unserialize($user_input);  // DANGEROUS\n\
                                 \n\
                                 // Use:\n\
                                 $data = json_decode($user_input, true);\n\
                                 \n\
                                 // If unserialize is required, use allowed_classes:\n\
                                 $obj = unserialize($data, ['allowed_classes' => ['SafeClass']]);\n\
                                 ```".to_string(),
                            _ => "Validate schema before deserializing user input.".to_string(),
                        };

                        findings.push(Finding {
                            id: String::new(),
                            detector: "InsecureDeserializeDetector".to_string(),
                            severity,
                            title: format!("Insecure deserialization: {}", method),
                            description: format!(
                                "Deserializing user-controlled data with {} can lead to Remote Code Execution.{}",
                                method, context_notes
                            ),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(suggestion),
                            estimated_effort: Some("30 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-502".to_string()),
                            why_it_matters: Some(
                                "Insecure deserialization can allow attackers to:\n\
                                 • Execute arbitrary code on the server\n\
                                 • Bypass authentication\n\
                                 • Perform denial of service\n\
                                 • Access sensitive data".to_string()
                            ),
                            ..Default::default()
                        });
                    }
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
                "InsecureDeserializeDetector",
                "Insecure Deserialization",
            ));
            if findings.len() >= self.max_findings {
                break;
            }
        }

        info!(
            "InsecureDeserializeDetector found {} findings (graph-aware + taint)",
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
    fn test_detects_unsafe_yaml_load() {
        let store = GraphStore::in_memory();
        let detector = InsecureDeserializeDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("config_loader.py", "import yaml\n\ndef load_config(data):\n    config = yaml.load(data)\n    return config\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(!findings.is_empty(), "Should detect yaml.load without SafeLoader");
        assert!(
            findings.iter().any(|f| f.title.contains("YAML") || f.title.contains("yaml")),
            "Finding should mention YAML. Titles: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_json_dumps() {
        let store = GraphStore::in_memory();
        let detector = InsecureDeserializeDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("config_loader.py", "import json\n\ndef save_config(config):\n    output = json.dumps(config, indent=2)\n    with open(\"config.json\", \"w\") as f:\n        f.write(output)\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(findings.is_empty(), "Should not detect json.dumps (serialization, not deserialization). Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>());
    }
}
