//! Insecure Cookie Detector
//!
//! Graph-enhanced detection of insecure cookies:
//! - Identify session/auth cookies (higher severity)
//! - Check for SameSite attribute
//! - Use graph to find cookie-setting functions in auth flows

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static COOKIE_PATTERN: OnceLock<Regex> = OnceLock::new();

fn cookie_pattern() -> &'static Regex {
    COOKIE_PATTERN.get_or_init(|| {
        Regex::new(
            r"(?i)(set.cookie|cookie\s*=|res\.cookie|response\.set_cookie|setcookie|\.cookies\[)",
        )
        .expect("valid regex")
    })
}

pub struct InsecureCookieDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl InsecureCookieDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Check if this is a sensitive cookie (session, auth, etc.)
    fn is_sensitive_cookie(line: &str, surrounding: &str) -> (bool, String) {
        let combined = format!("{} {}", line, surrounding).to_lowercase();

        if combined.contains("session") {
            return (true, "session".to_string());
        }
        if combined.contains("auth") || combined.contains("token") {
            return (true, "authentication".to_string());
        }
        if combined.contains("jwt") || combined.contains("bearer") {
            return (true, "JWT/bearer token".to_string());
        }
        if combined.contains("csrf") || combined.contains("xsrf") {
            return (true, "CSRF token".to_string());
        }
        if combined.contains("remember") || combined.contains("login") {
            return (true, "remember-me/login".to_string());
        }
        if combined.contains("user") || combined.contains("account") {
            return (true, "user data".to_string());
        }

        (false, "general".to_string())
    }

    /// Check cookie flags in surrounding context
    fn check_cookie_flags(lines: &[&str], cookie_line: usize) -> CookieFlags {
        let start = cookie_line.saturating_sub(2);
        let end = (cookie_line + 5).min(lines.len());
        let context = lines[start..end].join(" ").to_lowercase();

        CookieFlags {
            has_httponly: context.contains("httponly"),
            has_secure: context.contains("secure") && !context.contains("insecure"),
            has_samesite: context.contains("samesite"),
            samesite_value: if context.contains("samesite=strict")
                || context.contains("samesite='strict'")
            {
                Some("Strict".to_string())
            } else if context.contains("samesite=lax") || context.contains("samesite='lax'") {
                Some("Lax".to_string())
            } else if context.contains("samesite=none") || context.contains("samesite='none'") {
                Some("None".to_string())
            } else {
                None
            },
        }
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

struct CookieFlags {
    has_httponly: bool,
    has_secure: bool,
    has_samesite: bool,
    samesite_value: Option<String>,
}

impl Detector for InsecureCookieDetector {
    fn name(&self) -> &'static str {
        "insecure-cookie"
    }
    fn description(&self) -> &'static str {
        "Detects cookies without security flags"
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
            if crate::detectors::base::is_test_path(&path_str) {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py" | "js" | "ts" | "php" | "rb" | "java" | "go") {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();

                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    if cookie_pattern().is_match(line) {
                        let start = i.saturating_sub(5);
                        let end = (i + 5).min(lines.len());
                        let surrounding = lines[start..end].join(" ");

                        let (is_sensitive, cookie_type) =
                            Self::is_sensitive_cookie(line, &surrounding);
                        let flags = Self::check_cookie_flags(&lines, i);
                        let containing_func =
                            Self::find_containing_function(graph, &path_str, (i + 1) as u32);

                        // Collect missing flags
                        let mut missing = Vec::new();
                        if !flags.has_httponly {
                            missing.push("HttpOnly");
                        }
                        if !flags.has_secure {
                            missing.push("Secure");
                        }
                        if !flags.has_samesite {
                            missing.push("SameSite");
                        }

                        if missing.is_empty() {
                            continue;
                        }

                        // Calculate severity
                        let severity = if is_sensitive && !flags.has_httponly {
                            Severity::Critical // Session cookie without HttpOnly = XSS can steal it
                        } else if is_sensitive {
                            Severity::High
                        } else if !flags.has_httponly {
                            Severity::Medium
                        } else {
                            Severity::Low
                        };

                        // Build notes
                        let mut notes = Vec::new();
                        if is_sensitive {
                            notes.push(format!("🔐 {} cookie - high value target", cookie_type));
                        }
                        notes.push(format!("❌ Missing: {}", missing.join(", ")));
                        if let Some(ss) = &flags.samesite_value {
                            notes.push(format!("✓ SameSite={}", ss));
                            if ss == "None" && !flags.has_secure {
                                notes.push("⚠️ SameSite=None requires Secure flag!".to_string());
                            }
                        }
                        if let Some(func) = containing_func {
                            notes.push(format!("📦 In function: `{}`", func));
                        }

                        let context_notes = format!("\n\n**Analysis:**\n{}", notes.join("\n"));

                        // Build language-specific fix
                        let ext_str = ext;
                        let suggestion = match ext_str {
                            "py" => "```python\n\
                                 response.set_cookie(\n\
                                     'cookie_name',\n\
                                     value,\n\
                                     httponly=True,   # Prevents JavaScript access\n\
                                     secure=True,     # HTTPS only\n\
                                     samesite='Lax'   # CSRF protection\n\
                                 )\n\
                                 ```"
                            .to_string(),
                            "js" | "ts" => "```javascript\n\
                                 res.cookie('cookie_name', value, {\n\
                                     httpOnly: true,  // Prevents JavaScript access\n\
                                     secure: true,    // HTTPS only\n\
                                     sameSite: 'lax'  // CSRF protection\n\
                                 });\n\
                                 ```"
                            .to_string(),
                            "php" => "```php\n\
                                 setcookie('cookie_name', $value, [\n\
                                     'httponly' => true,\n\
                                     'secure' => true,\n\
                                     'samesite' => 'Lax'\n\
                                 ]);\n\
                                 ```"
                            .to_string(),
                            _ => "Add httponly, secure, and samesite flags.".to_string(),
                        };

                        findings.push(Finding {
                            id: String::new(),
                            detector: "InsecureCookieDetector".to_string(),
                            severity,
                            title: format!("Cookie missing {} flag{}",
                                missing[0],
                                if missing.len() > 1 { "s" } else { "" }
                            ),
                            description: format!(
                                "Cookie is missing security flags that protect against common attacks.{}",
                                context_notes
                            ),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some(suggestion),
                            estimated_effort: Some("5 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some(if !flags.has_httponly {
                                "CWE-1004".to_string()  // Sensitive Cookie Without HttpOnly
                            } else {
                                "CWE-614".to_string()   // Sensitive Cookie Without Secure
                            }),
                            why_it_matters: Some(
                                "• **HttpOnly** prevents XSS attacks from stealing cookies via JavaScript\n\
                                 • **Secure** ensures cookies are only sent over HTTPS\n\
                                 • **SameSite** prevents CSRF attacks by controlling cross-site requests".to_string()
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!(
            "InsecureCookieDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
