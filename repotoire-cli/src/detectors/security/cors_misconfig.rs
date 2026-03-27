//! CORS Misconfiguration Detector
//!
//! Graph-enhanced detection of overly permissive CORS:
//! - Checks if CORS config is in development-only code paths
//! - Identifies if authenticated endpoints have wildcard CORS
//! - Reduces severity for public/read-only endpoints

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphQueryExt;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::LazyLock;
use tracing::info;

static CORS_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(
            r#"(?i)(Access-Control-Allow-Origin|cors.*origin|allowedOrigins?)\s*[:=]\s*["'*]?\*"#,
        )
        .expect("valid regex")
    });
static CREDENTIALS_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?i)(credentials|allow.?credentials|with.?credentials)\s*[:=]\s*(true|["']include["'])"#).expect("valid regex")
    });

pub struct CorsMisconfigDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
}

impl CorsMisconfigDetector {
    crate::detectors::detector_new!(50);

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
        // Normalize: ensure path starts with '/' so patterns like "/dev/" match relative paths
        let normalized = if path_lower.starts_with('/') { path_lower } else { format!("/{}", path_lower) };
        dev_patterns.iter().any(|p| normalized.contains(p))
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
            if CREDENTIALS_PATTERN.is_match(line) {
                return true;
            }
        }
        false
    }

}

impl Detector for CorsMisconfigDetector {
    fn name(&self) -> &'static str {
        "cors-misconfig"
    }
    fn description(&self) -> &'static str {
        "Detects overly permissive CORS configuration"
    }

    fn requires_graph(&self) -> bool {
        false
    }

    fn bypass_postprocessor(&self) -> bool {
        true
    }

    fn file_extensions(&self) -> &'static [&'static str] {
        &["py", "js", "ts", "jsx", "tsx", "rb", "php", "java"]
    }

    fn detect(&self, ctx: &crate::detectors::analysis_context::AnalysisContext) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let files = &ctx.as_file_provider();
        let mut findings = vec![];

        for path in files.files_with_extensions(&["py", "js", "ts", "java", "go", "rb", "php", "yaml", "yml", "json", "conf"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            let path_str = path.to_string_lossy().to_string();

            // Cheap pre-filter: skip files without CORS-related keywords
            // to avoid expensive masked_content() tree-sitter parsing
            let raw = match files.content(path) {
                Some(c) => c,
                None => continue,
            };
            let raw_lower = raw.to_ascii_lowercase();
            if !raw_lower.contains("cors") && !raw_lower.contains("access-control")
                && !raw_lower.contains("allowedorigin")
            {
                continue;
            }

            // Use raw content for pattern matching (tree-sitter masking strips '*' from strings)
            // and masked content to filter out lines that are entirely comments/strings.
            let masked = match files.masked_content(path) {
                Some(c) => c,
                None => continue,
            };
            {
                let raw_lines: Vec<&str> = raw.lines().collect();
                let masked_lines: Vec<&str> = masked.lines().collect();

                for (i, line) in raw_lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(raw_lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    if CORS_PATTERN.is_match(line) {
                        // Verify the line contains real code (not entirely a comment/string)
                        let masked_line = masked_lines.get(i).copied().unwrap_or("");
                        if masked_line.trim().is_empty() {
                            continue;
                        }
                        let line_num = (i + 1) as u32;

                        // Get surrounding context
                        let start = i.saturating_sub(10);
                        let end = (i + 10).min(raw_lines.len());
                        let surrounding = &raw_lines[start..end];

                        // Check various risk factors
                        let is_dev_only = Self::is_dev_only_path(&path_str);
                        let has_credentials = Self::allows_credentials(&raw_lines, i);
                        let is_sensitive = Self::involves_sensitive_data(line, surrounding);
                        let containing_func =
                            graph.find_function_at(&path_str, line_num).map(|f| f.node_name(crate::graph::interner::global_interner()).to_string());

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


impl crate::detectors::RegisteredDetector for CorsMisconfigDetector {
    fn create(init: &crate::detectors::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::new(init.repo_path))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::builder::GraphBuilder;

    #[test]
    fn test_detects_wildcard_cors() {
        let store = GraphBuilder::new().freeze();
        let detector = CorsMisconfigDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("config.conf", "Access-Control-Allow-Origin: *\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(!findings.is_empty(), "Should detect wildcard CORS");
        assert!(
            findings.iter().any(|f| f.title.contains("CORS")),
            "Finding should mention CORS. Titles: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_specific_origin() {
        let store = GraphBuilder::new().freeze();
        let detector = CorsMisconfigDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("server.js", "const express = require('express');\nconst app = express();\napp.use((req, res, next) => {\n    res.setHeader('Access-Control-Allow-Origin', 'https://myapp.example.com');\n    next();\n});\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(findings.is_empty(), "Should not detect CORS with specific origin. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>());
    }
}
