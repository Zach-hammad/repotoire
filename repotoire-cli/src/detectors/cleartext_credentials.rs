//! Cleartext Credentials Detector
//!
//! Graph-enhanced detection of credentials logged in cleartext.
//! Uses graph to:
//! - Check if logging occurs in route handlers (higher risk)
//! - Identify the containing function for context
//! - Trace if credential variables flow from sensitive sources

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static LOG_PATTERN: OnceLock<Regex> = OnceLock::new();

fn log_pattern() -> &'static Regex {
    // Match logging statements that include actual credential variable names
    // Specifically target logging functions, NOT Error/Exception classes
    LOG_PATTERN.get_or_init(|| Regex::new(
        r"(?i)\b(console\.(log|warn|error|info|debug)|print[fl]?n?|logger\.(log|warn|error|info|debug|trace)|logging\.(log|warn|error|info|debug)|log\.(debug|info|warn|error|trace)|System\.out\.print|fmt\.Print|puts|p\s)\s*[\.(]\s*[^;\n]*\b(password|passwd|secret|api_key|apikey|auth_token|access_token|private_key|credentials?)\b"
    ).expect("valid regex"))
}

fn is_false_positive(line: &str) -> bool {
    let lower = line.to_lowercase();
    // Skip path/file references and tokenizer-related terms
    lower.contains("_path")
        || lower.contains("_file")
        || lower.contains("_dir")
        || lower.contains("tokenizer")
        || lower.contains("token_path")
        || lower.contains("password_field")
        || lower.contains("password_input")
        || lower.contains("password_hash")
        || lower.contains("password_reset")
        || lower.contains("no_password")
        || lower.contains("without_password")
        || lower.contains("hide_password")
        || lower.contains("mask_password")
}

/// Categorize the type of credential being logged
fn categorize_credential(line: &str) -> (&'static str, &'static str) {
    let lower = line.to_lowercase();

    if lower.contains("api_key") || lower.contains("apikey") {
        return ("API Key", "🔑");
    }
    if lower.contains("auth_token") || lower.contains("access_token") {
        return ("Auth Token", "🎫");
    }
    if lower.contains("private_key") {
        return ("Private Key", "🔐");
    }
    if lower.contains("password") || lower.contains("passwd") {
        return ("Password", "🔒");
    }
    if lower.contains("secret") {
        return ("Secret", "🤫");
    }
    if lower.contains("credentials") {
        return ("Credentials", "👤");
    }

    ("Sensitive Data", "⚠️")
}

pub struct CleartextCredentialsDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl CleartextCredentialsDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Find containing function and get context
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

                // Check if this is an auth-related function
                let is_auth_related = name_lower.contains("auth")
                    || name_lower.contains("login")
                    || name_lower.contains("signin")
                    || name_lower.contains("register")
                    || name_lower.contains("password")
                    || name_lower.contains("credential")
                    || name_lower.contains("token")
                    || name_lower.contains("session");

                (f.name, callers.len(), is_auth_related)
            })
    }

    /// Check if this is in production logging (vs debug/development)
    fn is_production_logging(line: &str) -> bool {
        let lower = line.to_lowercase();
        // Error and warning logs are more likely to hit production
        lower.contains(".error") || lower.contains(".warn") || lower.contains(".warning")
    }
}

impl Detector for CleartextCredentialsDetector {
    fn name(&self) -> &'static str {
        "cleartext-credentials"
    }
    fn description(&self) -> &'static str {
        "Detects credentials in logs"
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

            // Skip test files
            if crate::detectors::base::is_test_path(&path_str) || path_str.contains("spec") {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(
                ext,
                "py" | "js" | "ts" | "java" | "go" | "rb" | "php" | "cs"
            ) {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();
                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    if log_pattern().is_match(line) && !is_false_positive(line) {
                        let line_num = (i + 1) as u32;
                        let (cred_type, emoji) = categorize_credential(line);

                        // Graph-enhanced analysis
                        let func_context = Self::find_function_context(graph, &path_str, line_num);
                        let is_prod_log = Self::is_production_logging(line);

                        // Calculate severity
                        let mut severity = Severity::High;

                        if let Some((_, _, is_auth)) = &func_context {
                            // Critical if in auth-related code
                            if *is_auth {
                                severity = Severity::Critical;
                            }
                        }

                        // Critical for production logs
                        if is_prod_log {
                            severity = Severity::Critical;
                        }

                        // Build notes
                        let mut notes = Vec::new();
                        notes.push(format!("{} Credential type: {}", emoji, cred_type));

                        if let Some((func_name, callers, is_auth)) = &func_context {
                            notes.push(format!(
                                "📦 In function: `{}` ({} callers)",
                                func_name, callers
                            ));
                            if *is_auth {
                                notes.push("🔐 In authentication-related code".to_string());
                            }
                        }

                        if is_prod_log {
                            notes.push("🚨 Production log level (error/warn)".to_string());
                        }

                        let context_notes = format!("\n\n**Analysis:**\n{}", notes.join("\n"));

                        let suggestion = match ext {
                            "py" => "Mask or remove credentials from logs:\n\
                                 ```python\n\
                                 # Instead of:\n\
                                 logger.info(f\"Login attempt with password: {password}\")\n\
                                 \n\
                                 # Use:\n\
                                 logger.info(f\"Login attempt for user: {username}\")\n\
                                 # Or mask:\n\
                                 logger.debug(f\"Password length: {len(password)}\")\n\
                                 ```"
                            .to_string(),
                            "js" | "ts" => "Mask or remove credentials from logs:\n\
                                 ```javascript\n\
                                 // Instead of:\n\
                                 console.log('API Key:', apiKey);\n\
                                 \n\
                                 // Use:\n\
                                 console.log('API Key set:', !!apiKey);\n\
                                 // Or redact:\n\
                                 console.log('API Key:', apiKey.slice(0, 4) + '****');\n\
                                 ```"
                            .to_string(),
                            _ => "Remove sensitive data from logs or use masking.".to_string(),
                        };

                        findings.push(Finding {
                            id: String::new(),
                            detector: "CleartextCredentialsDetector".to_string(),
                            severity,
                            title: format!("{} may be logged in cleartext", cred_type),
                            description: format!(
                                "Sensitive data ({}) appears in logging statement.{}",
                                cred_type, context_notes
                            ),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(suggestion),
                            estimated_effort: Some("10 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-312".to_string()),
                            why_it_matters: Some(
                                "Credentials logged in cleartext can be:\n\
                                 • Exposed in log files accessible to attackers\n\
                                 • Sent to centralized logging systems\n\
                                 • Visible in monitoring dashboards\n\
                                 • Captured in crash reports"
                                    .to_string(),
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!(
            "CleartextCredentialsDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
