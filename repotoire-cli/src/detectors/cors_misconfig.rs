//! CORS Misconfiguration Detector
//!
//! Graph-enhanced detection of overly permissive CORS:
//! - Checks if CORS config is in development-only code paths
//! - Identifies if authenticated endpoints have wildcard CORS
//! - Reduces severity for public/read-only endpoints

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static CORS_PATTERN: OnceLock<Regex> = OnceLock::new();
static CREDENTIALS_PATTERN: OnceLock<Regex> = OnceLock::new();

fn cors_pattern() -> &'static Regex {
    CORS_PATTERN.get_or_init(|| {
        Regex::new(
            r#"(?i)(Access-Control-Allow-Origin|cors.*origin|allowedOrigins?)\s*[:=]\s*["'*]?\*"#,
        )
        .expect("valid regex")
    })
}

fn credentials_pattern() -> &'static Regex {
    CREDENTIALS_PATTERN.get_or_init(|| {
        Regex::new(r#"(?i)(credentials|allow.?credentials|with.?credentials)\s*[:=]\s*(true|["']include["'])"#).expect("valid regex")
    })
}

pub struct CorsMisconfigDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl CorsMisconfigDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Check if path is development-only
    fn is_dev_only_path(path: &str) -> bool {
        let dev_patterns = [
            "/dev/",
            "/development/",
            "/local/",
            "/test/",
            "/debug/",
            "dev.config",
            "development.config",
            "local.config",
            ".dev.",
            ".local.",
            ".development.",
            "/fixtures/",
            "/mocks/",
        ];
        let path_lower = path.to_lowercase();
        dev_patterns.iter().any(|p| path_lower.contains(p))
    }

    /// Check if the file/area handles sensitive operations
    fn involves_sensitive_data(content: &str, surrounding_lines: &[&str]) -> bool {
        let sensitive_patterns = [
            "auth", "login", "password", "token", "session", "cookie", "payment", "credit",
            "billing", "order", "user", "profile", "account", "private", "admin", "delete",
            "update", "create", "write",
        ];

        let combined = format!("{}\n{}", content, surrounding_lines.join("\n")).to_lowercase();
        sensitive_patterns.iter().any(|p| combined.contains(p))
    }

    /// Check if credentials are also allowed (very dangerous with *)
    fn allows_credentials(lines: &[&str], cors_line: usize) -> bool {
        // Check surrounding 10 lines for credentials setting
        let start = cors_line.saturating_sub(5);
        let end = (cors_line + 5).min(lines.len());

        for line in lines.get(start..end).unwrap_or(&[]) {
            if credentials_pattern().is_match(line) {
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
    ) -> Option<String> {
        graph
            .get_functions()
            .into_iter()
            .find(|f| f.file_path == file_path && f.line_start <= line && f.line_end >= line)
            .map(|f| f.name)
    }
}

impl Detector for CorsMisconfigDetector {
    fn name(&self) -> &'static str {
        "cors-misconfig"
    }
    fn description(&self) -> &'static str {
        "Detects overly permissive CORS configuration"
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

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(
                ext,
                "py" | "js"
                    | "ts"
                    | "java"
                    | "go"
                    | "rb"
                    | "php"
                    | "yaml"
                    | "yml"
                    | "json"
                    | "conf"
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

                    if cors_pattern().is_match(line) {
                        let line_num = (i + 1) as u32;

                        // Get surrounding context
                        let start = i.saturating_sub(10);
                        let end = (i + 10).min(lines.len());
                        let surrounding = &lines[start..end];

                        // Check various risk factors
                        let is_dev_only = Self::is_dev_only_path(&path_str);
                        let has_credentials = Self::allows_credentials(&lines, i);
                        let is_sensitive = Self::involves_sensitive_data(line, surrounding);
                        let containing_func =
                            Self::find_containing_function(graph, &path_str, line_num);

                        // Skip if clearly dev-only
                        if is_dev_only {
                            continue;
                        }

                        // Calculate severity
                        let severity = if has_credentials {
                            Severity::Critical // CORS: * with credentials = disaster
                        } else if is_sensitive {
                            Severity::High // Sensitive data with wildcard = bad
                        } else {
                            Severity::Medium // General wildcard CORS
                        };

                        // Build context notes
                        let mut notes = Vec::new();
                        if has_credentials {
                            notes.push("🚨 **CRITICAL**: Also allows credentials! This is a serious vulnerability.".to_string());
                        }
                        if is_sensitive {
                            notes.push(
                                "⚠️ Appears to handle sensitive data (auth/user/payment)"
                                    .to_string(),
                            );
                        }
                        if let Some(func) = &containing_func {
                            notes.push(format!("📦 In function: `{}`", func));
                        }

                        let context_notes = if notes.is_empty() {
                            String::new()
                        } else {
                            format!("\n\n**Analysis:**\n{}", notes.join("\n"))
                        };

                        let suggestion = if has_credentials {
                            "**CRITICAL FIX REQUIRED:**\n\
                             CORS: * with credentials is extremely dangerous and allows any website to:\n\
                             - Read authenticated responses\n\
                             - Perform actions as the user\n\n\
                             Options:\n\
                             1. Specify exact allowed origins\n\
                             2. Validate Origin header against allowlist\n\
                             3. Use dynamic origin reflection (carefully validated)".to_string()
                        } else {
                            "Specify allowed origins explicitly:\n\
                             ```javascript\n\
                             // Instead of: Access-Control-Allow-Origin: *\n\
                             // Use:\n\
                             const allowedOrigins = ['https://app.example.com', 'https://admin.example.com'];\n\
                             if (allowedOrigins.includes(request.origin)) {\n\
                               response.setHeader('Access-Control-Allow-Origin', request.origin);\n\
                             }\n\
                             ```".to_string()
                        };

                        findings.push(Finding {
                            id: String::new(),
                            detector: "CorsMisconfigDetector".to_string(),
                            severity,
                            title: if has_credentials {
                                "CRITICAL: Wildcard CORS with credentials".to_string()
                            } else {
                                "Overly permissive CORS (*)".to_string()
                            },
                            description: format!(
                                "Access-Control-Allow-Origin: * allows any website to make cross-origin requests.{}",
                                context_notes
                            ),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(suggestion),
                            estimated_effort: Some("15 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-942".to_string()),
                            why_it_matters: Some(
                                "Wildcard CORS allows any website to make requests to your API. \
                                 Combined with credentials, attackers can steal user data or perform \
                                 actions on behalf of logged-in users.".to_string()
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!(
            "CorsMisconfigDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
