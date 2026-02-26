//! Secret Detection
//!
//! Detects hardcoded secrets, API keys, passwords, and tokens in source code.
//! CWE-798: Use of Hard-coded Credentials

use crate::detectors::base::{is_test_file, Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use tracing::debug;

/// Secret patterns with their names and severity
static SECRET_PATTERNS: OnceLock<Vec<SecretPattern>> = OnceLock::new();

struct SecretPattern {
    name: &'static str,
    pattern: Regex,
    severity: Severity,
}

fn get_patterns() -> &'static Vec<SecretPattern> {
    SECRET_PATTERNS.get_or_init(|| {
        vec![
            // AWS
            SecretPattern {
                name: "AWS Access Key ID",
                pattern: Regex::new(r"AKIA[0-9A-Z]{16}").expect("valid regex"),
                severity: Severity::Critical,
            },
            SecretPattern {
                name: "AWS Secret Access Key",
                pattern: Regex::new(r"(?i)aws_secret_access_key\s*[=:]\s*[A-Za-z0-9/+=]{40}")
                    .expect("valid regex"),
                severity: Severity::Critical,
            },
            // GitHub
            SecretPattern {
                name: "GitHub Token",
                pattern: Regex::new(r"ghp_[a-zA-Z0-9]{36}").expect("valid regex"),
                severity: Severity::Critical,
            },
            // Generic API keys
            SecretPattern {
                name: "Generic API Key",
                pattern: Regex::new(r"(?i)api[_-]?key\s*[=:]\s*[a-zA-Z0-9_\-]{20,}")
                    .expect("valid regex"),
                severity: Severity::High,
            },
            SecretPattern {
                name: "Generic Secret",
                pattern: Regex::new(r"(?i)(secret|password|passwd|pwd)\s*[=:]\s*[^\s]{8,}")
                    .expect("valid regex"),
                severity: Severity::High,
            },
            // Private keys
            SecretPattern {
                name: "Private Key",
                pattern: Regex::new(r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")
                    .expect("valid regex"),
                severity: Severity::Critical,
            },
            // Slack
            SecretPattern {
                name: "Slack Token",
                pattern: Regex::new(r"xox[baprs]-[0-9]{10,13}-[0-9]{10,13}[a-zA-Z0-9-]*")
                    .expect("valid regex"),
                severity: Severity::Critical,
            },
            // Stripe
            SecretPattern {
                name: "Stripe API Key",
                pattern: Regex::new(r"sk_live_[a-zA-Z0-9]{24,}").expect("valid regex"),
                severity: Severity::Critical,
            },
            // Database URLs
            SecretPattern {
                name: "Database URL with Password",
                pattern: Regex::new(r"(?i)(postgres|mysql|mongodb|redis)://[^:]+:[^@]+@")
                    .expect("valid regex"),
                severity: Severity::Critical,
            },
            // SendGrid
            SecretPattern {
                name: "SendGrid API Key",
                pattern: Regex::new(r"SG\.[a-zA-Z0-9_-]{22}\.[a-zA-Z0-9_-]{43}")
                    .expect("valid regex"),
                severity: Severity::High,
            },
        ]
    })
}

pub struct SecretDetector {
    #[allow(dead_code)] // Part of detector pattern
    config: DetectorConfig,
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
}

impl SecretDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            config: DetectorConfig::default(),
            repository_path: repository_path.into(),
            max_findings: 100,
        }
    }

    /// Convert absolute path to relative path for consistent output
    fn relative_path(&self, path: &Path) -> PathBuf {
        path.strip_prefix(&self.repository_path)
            .unwrap_or(path)
            .to_path_buf()
    }

    /// Check if a Python os.environ.get() or os.getenv() call has a fallback (second argument)
    /// Pattern: os.environ.get("KEY", "fallback") or os.getenv("KEY", "fallback")
    fn has_python_env_fallback(line: &str) -> bool {
        // Look for the pattern: os.environ.get( or os.getenv( followed by args with a comma
        // This indicates a default value is provided
        let line_lower = line.to_lowercase();

        for pattern in ["os.environ.get(", "os.getenv("] {
            if let Some(start) = line_lower.find(pattern) {
                let after_pattern = &line[start + pattern.len()..];
                // Count parentheses to find the matching close
                let mut depth = 1;
                let mut found_comma_at_depth_1 = false;

                for ch in after_pattern.chars() {
                    match ch {
                        '(' => depth += 1,
                        ')' => {
                            depth -= 1;
                            if depth == 0 {
                                break;
                            }
                        }
                        ',' if depth == 1 => {
                            found_comma_at_depth_1 = true;
                            break;
                        }
                        _ => {}
                    }
                }

                if found_comma_at_depth_1 {
                    return true;
                }
            }
        }

        false
    }

    /// Check if a Go os.Getenv() call has fallback handling on the same line
    /// Common patterns:
    /// - `if val := os.Getenv("X"); val == "" { ... }` (short variable declaration with check)
    /// - `val := os.Getenv("X"); if val == "" { val = "default" }`
    /// - Using with a helper: `getEnvOr(os.Getenv("X"), "default")`
    /// - Ternary-style: `func() string { if v := os.Getenv("X"); v != "" { return v }; return "default" }()`
    fn has_go_env_fallback(line: &str) -> bool {
        // Check for common fallback indicators on the same line
        let has_empty_check = line.contains(r#"== """#) || line.contains(r#"!= """#);
        let has_if_statement = line.contains("if ");
        let has_fallback_helper = line.to_lowercase().contains("getenvdefault")
            || line.to_lowercase().contains("getenvor")
            || line.to_lowercase().contains("envdefault");

        has_fallback_helper || (has_empty_check && has_if_statement)
    }

    fn scan_file(&self, path: &Path, content: &str) -> Vec<Finding> {
        let mut findings = vec![];

        // Skip test files - they often contain test certificates/keys
        if is_test_file(path) {
            return findings;
        }

        // Skip binary files
        if content.contains('\0') {
            return findings;
        }

        let lines: Vec<&str> = content.lines().collect();
        for (line_num, line) in lines.iter().enumerate() {
            let prev_line = if line_num > 0 { Some(lines[line_num - 1]) } else { None };
            if crate::detectors::is_line_suppressed(line, prev_line) {
                continue;
            }

            // Skip comments that look like documentation
            let trimmed = line.trim();
            if trimmed.starts_with("//") && trimmed.contains("example") {
                continue;
            }

            for pattern in get_patterns() {
                if let Some(m) = pattern.pattern.find(line) {
                    let matched = m.as_str();

                    // Skip obvious false positives
                    if matched.len() < 10 {
                        continue;
                    }
                    if matched.contains("example") || matched.contains("EXAMPLE") {
                        continue;
                    }
                    if matched.contains("placeholder") || matched.contains("xxxx") {
                        continue;
                    }

                    // Skip placeholder patterns (setup templates, documentation)
                    let matched_lower = matched.to_lowercase();
                    if matched_lower.contains("your-")
                        || matched_lower.contains("-here")
                        || matched_lower.contains("changeme")
                        || matched_lower.contains("replace")
                        || matched_lower.contains("todo")
                        || matched_lower.contains("fixme")
                        || matched == "sk-your-openai-key"
                        || matched_lower.starts_with("xxx")
                        || matched_lower.ends_with("xxx")
                    {
                        continue;
                    }

                    // Skip shell variable substitutions: ${VAR_NAME}
                    // Docker Compose, shell scripts use ${SECRET} as variable reference, not hardcoded
                    if line.contains(&format!("${{{}", &matched.split('=').next().unwrap_or(""))) {
                        continue;
                    }

                    // Skip when value is reading from environment variables or headers
                    // (not hardcoding — the value is fetched at runtime, not embedded in source)
                    // Pattern: const secret = process.env.SECRET
                    if line.contains("= process.env.") || line.contains("=process.env.") {
                        continue;
                    }
                    // Node/Deno: process.env["KEY"] or process.env.KEY
                    if line.contains("process.env") {
                        continue;
                    }
                    // Rust: std::env::var("KEY") or env::var("KEY")
                    if line.contains("env::var(") || line.contains("std::env::var") {
                        continue;
                    }
                    // HTTP headers: req.headers.get(), headers.get(), request.headers
                    if line.contains("headers.get(")
                        || line.contains("req.headers.")
                        || line.contains("request.headers.")
                        || line.contains("headers[")
                    {
                        continue;
                    }
                    // Python: os.environ["KEY"] or os.environ.get()
                    if line.contains("os.environ[")
                        || line.contains("os.environ.get(")
                        || line.contains("os.getenv(")
                    {
                        continue;
                    }
                    // Go: os.Getenv("KEY")
                    if line.contains("os.Getenv(") || line.contains("os.LookupEnv(") {
                        continue;
                    }

                    // Value-type filtering for Generic Secret pattern:
                    // Skip when the value is clearly not a secret (function call or collection literal)
                    if pattern.name == "Generic Secret" {
                        // Extract the value part after = or :
                        let value_part = if let Some(eq_pos) = line.find('=') {
                            line[eq_pos + 1..].trim()
                        } else if let Some(colon_pos) = line.find(':') {
                            line[colon_pos + 1..].trim()
                        } else {
                            ""
                        };

                        if !value_part.is_empty() {
                            // Skip function/class calls: CharField(...), Signal(), SecretManager.from_config()
                            if value_part.contains('(') {
                                continue;
                            }
                            // Skip collection literals: [...], {...}
                            let first_char = value_part.chars().next().unwrap_or(' ');
                            if matches!(first_char, '[' | '{') {
                                continue;
                            }
                            // Skip variable references — a hardcoded secret MUST be a string literal
                            // Variables, attribute accesses, settings reads are NOT hardcoded
                            if !matches!(first_char, '"' | '\'' | '`' | 'b') {
                                // Not a string literal (b"..." for bytes is also a literal)
                                continue;
                            }
                            // If starts with b, check it's b"..." not a variable like `base64...`
                            if first_char == 'b' {
                                let second_char = value_part.chars().nth(1).unwrap_or(' ');
                                if !matches!(second_char, '"' | '\'') {
                                    continue;
                                }
                            }
                        }
                    }

                    // Determine effective severity based on context
                    let line_lower = line.to_lowercase();
                    let mut effective_severity = pattern.severity;

                    // Dev fallback pattern: process.env.X || 'fallback' or process.env.X ?? 'fallback'
                    // These are typically local dev defaults, not production credentials
                    if (line_lower.contains("process.env")
                        && (line.contains("||") || line.contains("??")))
                        // Python fallback patterns: os.environ.get("KEY", "fallback") or os.getenv("KEY", "fallback")
                        // The second argument is the default value, indicating a fallback
                        || ((line_lower.contains("os.environ.get(")
                            || line_lower.contains("os.getenv("))
                            && Self::has_python_env_fallback(line))
                        // Go fallback patterns: os.Getenv with fallback handling
                        // os.LookupEnv returns (value, found) - implies fallback handling
                        // Also check for common inline fallback patterns
                        || line.contains("os.LookupEnv(")
                        || (line.contains("os.Getenv(") && Self::has_go_env_fallback(line))
                        // Localhost URLs are lower risk - typically dev/test environments
                        || matched.contains("localhost")
                        || matched.contains("127.0.0.1")
                    {
                        effective_severity = Severity::Low;
                    }
                    // Check file path for seed/script/test/example patterns
                    else if let Some(rel_path) = path.to_str() {
                        let rel_lower = rel_path.to_lowercase();
                        if rel_lower.contains("/seed")
                            || rel_lower.contains("/script")
                            || rel_lower.contains("/fixture")
                            || rel_lower.contains("/examples/")
                            || rel_lower.contains("/example/")
                            || rel_lower.contains("/demo/")
                            || rel_lower.contains("/samples/")
                            || rel_lower.contains("/sample/")
                            || rel_lower.contains(".seed.")
                            || rel_lower.contains(".script.")
                            || rel_lower.contains(".example.")
                            || rel_lower.contains(".sample.")
                        {
                            effective_severity = Severity::Low;
                        }
                    }

                    let line_start = line_num as u32 + 1;
                    findings.push(Finding {
                        id: String::new(),
                        detector: "SecretDetector".to_string(),
                        severity: effective_severity,
                        title: format!("Hardcoded {}", pattern.name),
                        description: format!(
                            "Potential {} found in source code at line {}. \
                            Secrets should be stored in environment variables or secret management systems.",
                            pattern.name, line_start
                        ),
                        affected_files: vec![self.relative_path(path)],
                        line_start: Some(line_start),
                        line_end: Some(line_start),
                        suggested_fix: Some("Move this secret to an environment variable or secrets manager".to_string()),
                        estimated_effort: Some("15 minutes".to_string()),
                        category: Some("security".to_string()),
                        cwe_id: Some("CWE-798".to_string()),
                        why_it_matters: Some("Hardcoded secrets can be extracted from source code, leading to credential theft".to_string()),
                        ..Default::default()
                    });
                }
            }
        }

        findings
    }
}

impl SecretDetector {
    /// Find containing function
    fn find_containing_function(
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

                // Check if this is a config/init function
                let is_config = name_lower.contains("config")
                    || name_lower.contains("init")
                    || name_lower.contains("setup")
                    || name_lower.contains("settings");

                (f.name, callers.len(), is_config)
            })
    }
}

impl Detector for SecretDetector {
    fn name(&self) -> &'static str {
        "secret-detection"
    }

    fn description(&self) -> &'static str {
        "Detects hardcoded secrets, API keys, and passwords"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        for path in files.files_with_extensions(&[
            "py", "js", "ts", "jsx", "tsx", "rs", "go", "java", "rb", "php",
            "cs", "cpp", "c", "h", "hpp", "yaml", "yml", "json", "toml", "env",
            "conf", "config", "sh", "bash", "zsh", "properties", "xml",
        ]) {
            if findings.len() >= self.max_findings {
                break;
            }

            // Skip certain directories
            let path_str = path.to_string_lossy();
            if path_str.contains("node_modules")
                || path_str.contains(".git")
                || path_str.contains("vendor")
                || path_str.contains("target")
            {
                continue;
            }

            // Skip detector files (contain regex patterns that look like secrets)
            if path_str.contains("/detectors/") && path_str.ends_with(".rs") {
                continue;
            }

            debug!("Scanning for secrets: {}", path.display());
            if let Some(content) = files.masked_content(path) {
                findings.extend(self.scan_file(path, &content));
            }
        }

        // Enrich findings with graph context
        for finding in &mut findings {
            if let (Some(file_path), Some(line)) =
                (finding.affected_files.first(), finding.line_start)
            {
                let path_str = file_path.to_string_lossy().to_string();

                if let Some((func_name, callers, is_config)) =
                    Self::find_containing_function(graph, &path_str, line)
                {
                    let mut notes = Vec::new();
                    notes.push(format!(
                        "📦 In function: `{}` ({} callers)",
                        func_name, callers
                    ));

                    if is_config {
                        notes.push("⚙️ In config/setup function".to_string());
                        // Config functions with secrets are more expected but still bad
                        if finding.severity == Severity::Critical {
                            finding.severity = Severity::High;
                        }
                    }

                    // Boost severity if function has many callers (widely used)
                    if callers > 10 && finding.severity == Severity::High {
                        finding.severity = Severity::Critical;
                    }

                    finding.description = format!(
                        "{}\n\n**Context:**\n{}",
                        finding.description,
                        notes.join("\n")
                    );
                }
            }
        }

        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_hardcoded_aws_key() {
        let store = GraphStore::in_memory();
        let detector = SecretDetector::new("/mock/repo");
        // Use .rb extension: masking has no tree-sitter grammar for Ruby,
        // so the content is returned unchanged and the key stays visible.
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("config.rb", "\nAWS_ACCESS_KEY = \"AKIAIOSFODNN7ABCDEFG\"\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect hardcoded AWS access key"
        );
        assert!(findings.iter().any(|f| f.title.contains("AWS Access Key")));
    }

    #[test]
    fn test_no_finding_for_env_variable_usage() {
        let store = GraphStore::in_memory();
        let detector = SecretDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("config.py", "\nimport os\nAWS_KEY = os.environ.get(\"AWS_ACCESS_KEY_ID\")\nSECRET = os.getenv(\"AWS_SECRET_ACCESS_KEY\")\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag secrets read from environment variables, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_password_in_docstring() {
        let store = GraphStore::in_memory();
        let detector = SecretDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("auth.py", "def authenticate(username, password):\n    \"\"\"\n    Authenticate user with password.\n    password = hashlib.sha256(raw).hexdigest()\n    \"\"\"\n    return check_password(username, password)\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag 'password' references in docstrings. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_password_type_annotation() {
        let store = GraphStore::in_memory();
        let detector = SecretDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("models.py", "from pydantic import BaseModel\n\nclass LoginRequest(BaseModel):\n    username: str\n    password: str\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag password type annotations. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_password_field_definition() {
        let store = GraphStore::in_memory();
        let detector = SecretDetector::new("/mock/repo");
        // Use .rb -- no tree-sitter masking, content passes through
        // Use `password = CharField(...)` (keyword directly before =) so the regex matches
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("models.rb", "password = CharField(max_length=128)\nsecret = SecretManager.from_config(settings)\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag function/class calls as secrets. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_password_list_assignment() {
        let store = GraphStore::in_memory();
        let detector = SecretDetector::new("/mock/repo");
        // Use `password = [...]` (keyword directly before =) so the regex matches
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("config.rb", "password = [\"django.contrib.auth.hashers.PBKDF2PasswordHasher\"]\nsecret = {\"key\": \"value\", \"other\": \"data\"}\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag list/dict literal assignments as secrets. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_still_detects_real_hardcoded_password() {
        let store = GraphStore::in_memory();
        let detector = SecretDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("config.rb", "password = \"super_secret_password_123\"\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should still detect real hardcoded password"
        );
    }

    #[test]
    fn test_skips_uppercase_constant_reference() {
        let store = GraphStore::in_memory();
        let detector = SecretDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("config.rb", "password = HARDCODED_SECRET_VALUE\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag variable/constant references as secrets. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_password_variable_reference() {
        let store = GraphStore::in_memory();
        let detector = SecretDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("views.rb", "password=auth_password,\nsecret = settings.SECRET_KEY\nself._password = raw_password\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag variable references as secrets. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_settings_read() {
        let store = GraphStore::in_memory();
        let detector = SecretDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("config.rb", "self.password = settings.EMAIL_HOST_PASSWORD if password is None else password\npassword=self.settings_dict[\"PASSWORD\"],\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag settings reads as secrets. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_request_data_read() {
        let store = GraphStore::in_memory();
        let detector = SecretDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("views.rb", "csrf_secret = request.META[\"CSRF_COOKIE\"]\nold_password = self.cleaned_data[\"old_password\"]\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag request/form data reads as secrets. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
