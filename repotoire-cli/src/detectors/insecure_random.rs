//! Insecure Random Detector
//!
//! Graph-enhanced detection of insecure random:
//! - Trace random values through function calls to security contexts
//! - Check if random is used for IDs, tokens, or passwords
//! - Language-specific secure alternatives

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static INSECURE_RANDOM: OnceLock<Regex> = OnceLock::new();

fn insecure_random() -> &'static Regex {
    INSECURE_RANDOM.get_or_init(|| {
        Regex::new(r"(?i)(Math\.random\(\)|random\.random\(\)|random\.randint|rand\(\)|srand\(|mt_rand|lcg_value|uniqid)").expect("valid regex")
    })
}

/// Get secure alternative for each language
fn get_secure_alternative(ext: &str) -> &'static str {
    match ext {
        "py" => {
            "```python\n\
                 import secrets\n\
                 \n\
                 # For tokens/passwords\n\
                 token = secrets.token_urlsafe(32)\n\
                 \n\
                 # For random integers\n\
                 num = secrets.randbelow(100)\n\
                 \n\
                 # For random bytes\n\
                 data = secrets.token_bytes(16)\n\
                 ```"
        }
        "js" | "ts" => {
            "```javascript\n\
                        // Node.js\n\
                        const crypto = require('crypto');\n\
                        const token = crypto.randomBytes(32).toString('hex');\n\
                        \n\
                        // Browser\n\
                        const array = new Uint8Array(32);\n\
                        crypto.getRandomValues(array);\n\
                        ```"
        }
        "java" => {
            "```java\n\
                   import java.security.SecureRandom;\n\
                   \n\
                   SecureRandom random = new SecureRandom();\n\
                   byte[] bytes = new byte[32];\n\
                   random.nextBytes(bytes);\n\
                   ```"
        }
        "go" => {
            "```go\n\
                 import \"crypto/rand\"\n\
                 \n\
                 bytes := make([]byte, 32)\n\
                 rand.Read(bytes)\n\
                 ```"
        }
        "php" => {
            "```php\n\
                  // PHP 7+\n\
                  $bytes = random_bytes(32);\n\
                  $token = bin2hex($bytes);\n\
                  ```"
        }
        "rb" => {
            "```ruby\n\
                 require 'securerandom'\n\
                 \n\
                 token = SecureRandom.hex(32)\n\
                 ```"
        }
        "c" | "cpp" => {
            "```c\n\
                        // Linux\n\
                        #include <sys/random.h>\n\
                        getrandom(buffer, size, 0);\n\
                        \n\
                        // Or read from /dev/urandom\n\
                        ```"
        }
        _ => "Use your platform's cryptographic random number generator.",
    }
}

pub struct InsecureRandomDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
}

impl InsecureRandomDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Check what the random value is used for
    fn analyze_usage(line: &str, surrounding: &str) -> (SecurityContext, String) {
        let combined = format!("{} {}", line, surrounding).to_lowercase();

        // Token/secret generation
        if combined.contains("token") || combined.contains("secret") || combined.contains("api_key")
        {
            return (
                SecurityContext::Token,
                "token/secret generation".to_string(),
            );
        }

        // Password/salt
        if combined.contains("password") || combined.contains("salt") || combined.contains("hash") {
            return (
                SecurityContext::Password,
                "password/salt generation".to_string(),
            );
        }

        // Session/auth
        if combined.contains("session") || combined.contains("auth") || combined.contains("login") {
            return (
                SecurityContext::Session,
                "session/authentication".to_string(),
            );
        }

        // ID generation — only flag security-sensitive IDs, not trace/metric/display IDs
        if combined.contains("uuid") || combined.contains("identifier") {
            return (SecurityContext::ID, "ID generation".to_string());
        }
        // Security-sensitive ID patterns
        if (combined.contains("session_id")
            || combined.contains("sessionid")
            || combined.contains("user_id")
            || combined.contains("userid")
            || combined.contains("auth_id")
            || combined.contains("api_id"))
            && !combined.contains("trace")
            && !combined.contains("metric")
            && !combined.contains("display")
            && !combined.contains("record")
            && !combined.contains("internal")
            && !combined.contains("log")
        {
            return (SecurityContext::ID, "ID generation".to_string());
        }

        // Crypto
        if combined.contains("crypto")
            || combined.contains("encrypt")
            || combined.contains("key")
            || combined.contains("iv")
            || combined.contains("nonce")
        {
            return (
                SecurityContext::Crypto,
                "cryptographic operation".to_string(),
            );
        }

        // OTP/verification
        if combined.contains("otp")
            || combined.contains("code")
            || combined.contains("verification")
            || combined.contains("pin")
        {
            return (SecurityContext::OTP, "OTP/verification code".to_string());
        }

        (SecurityContext::Unknown, "unknown".to_string())
    }

    /// Find functions that use insecure random and are called by security-related code
    fn find_security_callers(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        func_name: &str,
    ) -> Vec<String> {
        let mut security_callers = Vec::new();

        if let Some(func) = graph
            .get_functions()
            .into_iter()
            .find(|f| f.name == func_name)
        {
            let callers = graph.get_callers(&func.qualified_name);

            for caller in callers {
                let caller_lower = caller.name.to_lowercase();
                if caller_lower.contains("auth")
                    || caller_lower.contains("login")
                    || caller_lower.contains("token")
                    || caller_lower.contains("session")
                    || caller_lower.contains("password")
                    || caller_lower.contains("secret")
                {
                    security_callers.push(caller.name.clone());
                }
            }
        }

        security_callers
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

#[derive(PartialEq)]
enum SecurityContext {
    Token,
    Password,
    Session,
    ID,
    Crypto,
    OTP,
    Unknown,
}

impl Detector for InsecureRandomDetector {
    fn name(&self) -> &'static str {
        "insecure-random"
    }
    fn description(&self) -> &'static str {
        "Detects insecure random for security purposes"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        for path in files.files_with_extensions(&["py", "js", "ts", "java", "go", "rb", "php", "c", "cpp"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            let path_str = path.to_string_lossy().to_string();

            // Skip test files
            if crate::detectors::base::is_test_path(&path_str) {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");

            if let Some(content) = files.masked_content(path) {
                let lines: Vec<&str> = content.lines().collect();

                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    if insecure_random().is_match(line) {
                        let start = i.saturating_sub(5);
                        let end = (i + 5).min(lines.len());
                        let surrounding = lines[start..end].join(" ");

                        let (context, usage) = Self::analyze_usage(line, &surrounding);
                        let containing_func =
                            Self::find_containing_function(graph, &path_str, (i + 1) as u32);

                        // Check if function is called by security code
                        let security_callers = if let Some(ref func) = containing_func {
                            self.find_security_callers(graph, func)
                        } else {
                            vec![]
                        };

                        // Only flag if in security context
                        if context == SecurityContext::Unknown && security_callers.is_empty() {
                            continue;
                        }

                        // For ID context: only flag if it looks like a *security-critical* ID
                        // (session ID, CSRF token, auth token). Skip trace IDs, metric IDs,
                        // display IDs, record IDs, game logic IDs — these don't need crypto-secure random.
                        if context == SecurityContext::ID && security_callers.is_empty() {
                            let line_lower = line.to_lowercase();
                            let is_safe_id = line_lower.contains("traceid")
                                || line_lower.contains("trace_id")
                                || line_lower.contains("metricid")
                                || line_lower.contains("metric_id")
                                || line_lower.contains("displayid")
                                || line_lower.contains("display_id")
                                || line_lower.contains("recordid")
                                || line_lower.contains("record_id")
                                || line_lower.contains("requestid")
                                || line_lower.contains("request_id")
                                || line_lower.contains("gameid")
                                || line_lower.contains("game_id")
                                || line_lower.contains("itemid")
                                || line_lower.contains("item_id")
                                // session and auth IDs are security-critical; keep flagging those
                                ;
                            // Also skip if it's clearly a non-security random use:
                            // e.g. Math.random() for game logic, UI jitter, test data
                            let is_game_or_ui = line_lower.contains("game")
                                || line_lower.contains("jitter")
                                || line_lower.contains("color")
                                || line_lower.contains("animation")
                                || line_lower.contains("position")
                                || line_lower.contains("offset")
                                || line_lower.contains("delay");
                            if is_safe_id || is_game_or_ui {
                                continue;
                            }
                        }

                        // Calculate severity
                        let severity = match context {
                            SecurityContext::Crypto | SecurityContext::Password => {
                                Severity::Critical
                            }
                            SecurityContext::Token
                            | SecurityContext::Session
                            | SecurityContext::OTP => Severity::High,
                            SecurityContext::ID => Severity::Medium,
                            SecurityContext::Unknown if !security_callers.is_empty() => {
                                Severity::High
                            }
                            _ => Severity::Medium,
                        };

                        // Build notes
                        let mut notes = Vec::new();
                        notes.push(format!("🎯 Used for: {}", usage));
                        if let Some(func) = &containing_func {
                            notes.push(format!("📦 In function: `{}`", func));
                        }
                        if !security_callers.is_empty() {
                            notes.push(format!(
                                "⚠️ Called by security functions: {}",
                                security_callers.join(", ")
                            ));
                        }

                        let context_notes = format!("\n\n**Analysis:**\n{}", notes.join("\n"));

                        let random_func = insecure_random()
                            .find(line)
                            .map(|m| m.as_str())
                            .unwrap_or("random");

                        findings.push(Finding {
                            id: String::new(),
                            detector: "InsecureRandomDetector".to_string(),
                            severity,
                            title: format!("Insecure `{}` used for {}", random_func, usage),
                            description: format!(
                                "`{}` is not cryptographically secure and can be predicted by attackers.{}",
                                random_func, context_notes
                            ),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some(format!(
                                "Use a cryptographically secure random number generator:\n\n{}",
                                get_secure_alternative(ext)
                            )),
                            estimated_effort: Some("15 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-330".to_string()),
                            why_it_matters: Some(
                                "Insecure random number generators (like Math.random or random.random) \
                                 use predictable algorithms. Attackers can often guess the output and \
                                 forge tokens, guess passwords, or bypass authentication.".to_string()
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!(
            "InsecureRandomDetector found {} findings (graph-aware)",
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
    fn test_detects_insecure_random_in_security_context() {
        let store = GraphStore::in_memory();
        let detector = InsecureRandomDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("auth.py", "import random\n\ndef generate_token():\n    token = random.random()\n    return str(token)\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect random.random() used for token generation"
        );
        assert!(
            findings.iter().any(|f| f.title.contains("random.random()")),
            "Finding should mention random.random(). Titles: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_non_security_random() {
        let store = GraphStore::in_memory();
        let detector = InsecureRandomDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("simulation.py", "import random\n\ndef roll_dice():\n    return random.randint(1, 6)\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag random used in non-security context, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
