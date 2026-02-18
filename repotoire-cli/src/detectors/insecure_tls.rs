//! Insecure TLS/Certificate Validation Detector (CWE-295)
//!
//! Detects disabled certificate verification across languages:
//! - Python: `verify=False`, disabled SSL context, urllib3 warning suppression
//! - JavaScript/Node: `rejectUnauthorized: false`, `NODE_TLS_REJECT_UNAUTHORIZED=0`
//! - Go: `InsecureSkipVerify: true`
//! - Java: Trust-all TrustManager, permissive HostnameVerifier
//! - Rust: danger_accept_invalid_certs(true)

use crate::detectors::base::{is_test_file, Detector, DetectorConfig};
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use tracing::{debug, info};

/// Patterns that indicate insecure TLS/certificate validation
const PYTHON_PATTERNS: &[(&str, &str, Severity)] = &[
    (
        "verify=False",
        "requests/urllib call with certificate verification disabled",
        Severity::High,
    ),
    (
        "verify = False",
        "requests/urllib call with certificate verification disabled",
        Severity::High,
    ),
    (
        "CERT_NONE",
        "SSL context with no certificate verification",
        Severity::Critical,
    ),
    (
        "check_hostname = False",
        "SSL hostname verification disabled",
        Severity::High,
    ),
    (
        "check_hostname=False",
        "SSL hostname verification disabled",
        Severity::High,
    ),
    (
        "InsecureRequestWarning",
        "urllib3 insecure request warning suppressed",
        Severity::Medium,
    ),
    (
        "create_default_context",
        "Custom SSL context (verify usage)",
        Severity::Low,
    ), // only if combined with CERT_NONE
];

const JS_PATTERNS: &[(&str, &str, Severity)] = &[
    (
        "rejectUnauthorized: false",
        "TLS certificate verification disabled",
        Severity::High,
    ),
    (
        "rejectUnauthorized:false",
        "TLS certificate verification disabled",
        Severity::High,
    ),
    (
        "rejectUnauthorized : false",
        "TLS certificate verification disabled",
        Severity::High,
    ),
    (
        "NODE_TLS_REJECT_UNAUTHORIZED",
        "Environment variable to disable TLS verification",
        Severity::Critical,
    ),
    (
        "process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0'",
        "TLS verification disabled via environment",
        Severity::Critical,
    ),
    (
        "process.env.NODE_TLS_REJECT_UNAUTHORIZED = \"0\"",
        "TLS verification disabled via environment",
        Severity::Critical,
    ),
    (
        "agent: new https.Agent",
        "Custom HTTPS agent (check rejectUnauthorized)",
        Severity::Low,
    ),
];

const GO_PATTERNS: &[(&str, &str, Severity)] = &[
    (
        "InsecureSkipVerify: true",
        "TLS certificate verification skipped",
        Severity::High,
    ),
    (
        "InsecureSkipVerify:true",
        "TLS certificate verification skipped",
        Severity::High,
    ),
];

const JAVA_PATTERNS: &[(&str, &str, Severity)] = &[
    (
        "TrustAllCerts",
        "Trust-all certificate manager (no validation)",
        Severity::Critical,
    ),
    (
        "X509TrustManager",
        "Custom trust manager (may bypass validation)",
        Severity::Medium,
    ),
    (
        "ALLOW_ALL_HOSTNAME_VERIFIER",
        "Hostname verification disabled",
        Severity::High,
    ),
    (
        "NoopHostnameVerifier",
        "Hostname verification disabled",
        Severity::High,
    ),
    (
        "setHostnameVerifier(allHostsValid)",
        "Hostname verification disabled",
        Severity::High,
    ),
    (
        "SSLContext.getInstance(\"SSL\")",
        "Outdated SSL protocol (use TLS)",
        Severity::Medium,
    ),
];

const RUST_PATTERNS: &[(&str, &str, Severity)] = &[
    (
        "danger_accept_invalid_certs(true)",
        "Certificate validation disabled (reqwest)",
        Severity::High,
    ),
    (
        "danger_accept_invalid_hostnames(true)",
        "Hostname validation disabled (reqwest)",
        Severity::High,
    ),
    (
        "set_verify(SslVerifyMode::NONE)",
        "OpenSSL verification disabled",
        Severity::High,
    ),
];

pub struct InsecureTlsDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl InsecureTlsDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    fn get_patterns_for_ext(&self, ext: &str) -> Vec<(&'static str, &'static str, Severity)> {
        match ext {
            "py" | "pyi" => PYTHON_PATTERNS.to_vec(),
            "js" | "jsx" | "mjs" | "cjs" | "ts" | "tsx" => JS_PATTERNS.to_vec(),
            "go" => GO_PATTERNS.to_vec(),
            "java" | "kt" | "kts" => JAVA_PATTERNS.to_vec(),
            "rs" => RUST_PATTERNS.to_vec(),
            _ => vec![],
        }
    }

    fn scan_files(&self) -> Vec<Finding> {
        use crate::detectors::walk_source_files;

        let mut findings = Vec::new();
        let mut seen: HashSet<(String, u32)> = HashSet::new();

        if !self.repository_path.exists() {
            return findings;
        }

        let extensions = &[
            "py", "pyi", "js", "jsx", "ts", "tsx", "mjs", "cjs", "go", "java", "kt", "kts", "rs",
        ];

        for path in walk_source_files(&self.repository_path, Some(extensions)) {
            if findings.len() >= self.max_findings {
                break;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            let patterns = self.get_patterns_for_ext(ext);
            if patterns.is_empty() {
                continue;
            }

            let rel_path = path
                .strip_prefix(&self.repository_path)
                .unwrap_or(&path)
                .to_string_lossy()
                .to_string();

            // Downgrade severity in test files
            let is_test = is_test_file(Path::new(&rel_path));

            let content = match std::fs::read_to_string(&path) {
                Ok(c) => c,
                Err(_) => continue,
            };

            if content.len() > 500_000 {
                continue;
            }

            let lines: Vec<&str> = content.lines().collect();
            for (line_no, line) in lines.iter().enumerate() {
                let line_num = (line_no + 1) as u32;
                let trimmed = line.trim();

                // Skip comments
                if trimmed.starts_with('#') || trimmed.starts_with("//") || trimmed.starts_with('*')
                {
                    continue;
                }

                // Check suppression
                let prev_line = if line_no > 0 {
                    Some(lines[line_no - 1])
                } else {
                    None
                };
                if crate::detectors::is_line_suppressed(line, prev_line) {
                    continue;
                }

                for (pattern, description, severity) in &patterns {
                    if !line.contains(pattern) {
                        continue;
                    }

                    // Skip matches inside string literals (e.g., pattern definitions in detector source)
                    if ext == "rs" && (trimmed.starts_with('"') || trimmed.starts_with("&\"") || trimmed.starts_with("r#\"") || trimmed.starts_with("r\"")) {
                        continue;
                    }

                    // Skip low-confidence patterns unless combined with other signals
                    if *severity == Severity::Low {
                        continue; // Only flag direct insecure patterns
                    }

                    // For "InsecureRequestWarning", only flag if it's being suppressed
                    if *pattern == "InsecureRequestWarning"
                        && !line.contains("disable")
                        && !line.contains("filter")
                        && !line.contains("suppress")
                    {
                        continue;
                    }

                    // For X509TrustManager, only flag if it's implementing a custom one
                    if *pattern == "X509TrustManager"
                        && !line.contains("implements")
                        && !line.contains("new")
                        && !line.contains("class")
                    {
                        continue;
                    }

                    // For create_default_context, skip (low confidence alone)
                    if *pattern == "create_default_context" {
                        continue;
                    }

                    let loc = (rel_path.clone(), line_num);
                    if seen.contains(&loc) {
                        continue;
                    }
                    seen.insert(loc);

                    let effective_severity = if is_test {
                        // Downgrade in tests — might be intentional for testing
                        match severity {
                            Severity::Critical => Severity::Medium,
                            Severity::High => Severity::Low,
                            _ => Severity::Low,
                        }
                    } else {
                        *severity
                    };

                    let language = match ext {
                        "py" | "pyi" => "python",
                        "js" | "jsx" | "mjs" | "cjs" => "javascript",
                        "ts" | "tsx" => "typescript",
                        "go" => "go",
                        "java" => "java",
                        "kt" | "kts" => "kotlin",
                        "rs" => "rust",
                        _ => "unknown",
                    };

                    findings.push(Finding {
                        id: deterministic_finding_id(
                            "InsecureTlsDetector",
                            &rel_path,
                            line_num,
                            description,
                        ),
                        detector: "InsecureTlsDetector".to_string(),
                        title: format!("Insecure TLS/Certificate Validation ({})", language),
                        description: format!(
                            "**{}** (CWE-295)\n\n\
                             Disabled certificate or hostname verification detected.\n\n\
                             **Pattern**: `{}`\n\
                             **File**: {}:{}\n\
                             **Code**: `{}`\n\n\
                             This allows man-in-the-middle attacks. An attacker on the network \
                             can intercept and modify traffic without detection.{}",
                            description,
                            pattern,
                            rel_path,
                            line_num,
                            trimmed.chars().take(120).collect::<String>(),
                            if is_test {
                                "\n\n*Note: Found in test file — severity reduced.*"
                            } else {
                                ""
                            },
                        ),
                        severity: effective_severity,
                        affected_files: vec![PathBuf::from(&rel_path)],
                        line_start: Some(line_num),
                        line_end: None,
                        suggested_fix: Some(self.get_fix_suggestion(pattern, language)),
                        cwe_id: Some("CWE-295".to_string()),
                        confidence: Some(if is_test { 0.7 } else { 0.95 }),
                        category: Some("security".to_string()),
                        ..Default::default()
                    });

                    if findings.len() >= self.max_findings {
                        return findings;
                    }
                }
            }
        }

        findings
    }

    fn get_fix_suggestion(&self, pattern: &str, language: &str) -> String {
        match language {
            "python" => {
                if pattern.contains("verify") {
                    "Remove `verify=False` or set `verify=True` (default). For self-signed certs in dev, \
                     use `verify='/path/to/ca-bundle.crt'` instead.".to_string()
                } else if pattern.contains("CERT_NONE") {
                    "Use `ssl.CERT_REQUIRED` instead of `ssl.CERT_NONE`.".to_string()
                } else if pattern.contains("check_hostname") {
                    "Set `check_hostname = True` (default in Python 3.4+).".to_string()
                } else {
                    "Enable certificate verification. Never disable TLS validation in production."
                        .to_string()
                }
            }
            "javascript" | "typescript" => {
                if pattern.contains("rejectUnauthorized") {
                    "Remove `rejectUnauthorized: false`. For self-signed certs, provide the CA cert \
                     via `ca` option instead.".to_string()
                } else if pattern.contains("NODE_TLS") {
                    "Remove `NODE_TLS_REJECT_UNAUTHORIZED=0`. This disables TLS for the entire process.".to_string()
                } else {
                    "Enable certificate verification on HTTPS connections.".to_string()
                }
            }
            "go" => "Remove `InsecureSkipVerify: true` from tls.Config. For self-signed certs, \
                     provide a custom CA pool via `RootCAs`."
                .to_string(),
            "java" | "kotlin" => {
                "Use the default TrustManager and HostnameVerifier. For self-signed certs, \
                     add the CA to your trust store."
                    .to_string()
            }
            "rust" => {
                "Remove `danger_accept_invalid_certs(true)`. For self-signed certs, add the CA \
                     to the client builder via `add_root_certificate()`."
                    .to_string()
            }
            _ => "Enable certificate verification. Never disable TLS validation in production."
                .to_string(),
        }
    }
}

impl Detector for InsecureTlsDetector {
    fn name(&self) -> &'static str {
        "InsecureTlsDetector"
    }
    fn description(&self) -> &'static str {
        "Detects disabled TLS/certificate verification (CWE-295)"
    }
    fn detect(&self, _graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        debug!("Starting insecure TLS detection");
        let findings = self.scan_files();
        info!("InsecureTlsDetector found {} findings", findings.len());
        Ok(findings)
    }
    fn category(&self) -> &'static str {
        "security"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_python_verify_false() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("client.py");
        std::fs::write(&file, "response = requests.get(url, verify=False)\n").unwrap();

        let detector = InsecureTlsDetector::new(dir.path());
        let findings = detector.scan_files();
        assert!(!findings.is_empty(), "Should detect verify=False");
        assert_eq!(findings[0].severity, Severity::High);
    }

    #[test]
    fn test_js_reject_unauthorized() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("client.js");
        std::fs::write(
            &file,
            "const agent = new https.Agent({ rejectUnauthorized: false });\n",
        )
        .unwrap();

        let detector = InsecureTlsDetector::new(dir.path());
        let findings = detector.scan_files();
        assert!(
            !findings.is_empty(),
            "Should detect rejectUnauthorized: false"
        );
    }

    #[test]
    fn test_go_insecure_skip_verify() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("client.go");
        std::fs::write(
            &file,
            "tlsConfig := &tls.Config{InsecureSkipVerify: true}\n",
        )
        .unwrap();

        let detector = InsecureTlsDetector::new(dir.path());
        let findings = detector.scan_files();
        assert!(!findings.is_empty(), "Should detect InsecureSkipVerify");
    }

    #[test]
    fn test_rust_danger_accept() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("client.rs");
        std::fs::write(
            &file,
            "let client = reqwest::Client::builder().danger_accept_invalid_certs(true).build()?;\n",
        )
        .unwrap();

        let detector = InsecureTlsDetector::new(dir.path());
        let findings = detector.scan_files();
        assert!(
            !findings.is_empty(),
            "Should detect danger_accept_invalid_certs"
        );
    }

    #[test]
    fn test_java_trust_all() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("Client.java");
        std::fs::write(
            &file,
            "TrustManager[] trustAllCerts = new TrustAllCerts();\n",
        )
        .unwrap();

        let detector = InsecureTlsDetector::new(dir.path());
        let findings = detector.scan_files();
        assert!(!findings.is_empty(), "Should detect TrustAllCerts");
    }

    #[test]
    fn test_test_file_downgraded() {
        let dir = tempfile::tempdir().unwrap();
        let test_dir = dir.path().join("tests");
        std::fs::create_dir_all(&test_dir).unwrap();
        let file = test_dir.join("test_client.py");
        std::fs::write(&file, "response = requests.get(url, verify=False)\n").unwrap();

        let detector = InsecureTlsDetector::new(dir.path());
        let findings = detector.scan_files();
        assert!(!findings.is_empty(), "Should still detect in test files");
        assert_eq!(
            findings[0].severity,
            Severity::Low,
            "Should be downgraded in tests"
        );
    }

    #[test]
    fn test_clean_code_no_findings() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("client.py");
        std::fs::write(
            &file,
            "response = requests.get(url)\nprint(response.status_code)\n",
        )
        .unwrap();

        let detector = InsecureTlsDetector::new(dir.path());
        let findings = detector.scan_files();
        assert!(findings.is_empty(), "Clean code should have no findings");
    }

    #[test]
    fn test_python_cert_none() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("server.py");
        std::fs::write(&file, "ctx = ssl.create_default_context()\nctx.check_hostname = False\nctx.verify_mode = ssl.CERT_NONE\n").unwrap();

        let detector = InsecureTlsDetector::new(dir.path());
        let findings = detector.scan_files();
        assert!(
            findings.len() >= 2,
            "Should detect both check_hostname and CERT_NONE. Found: {}",
            findings.len()
        );
    }
}
