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
use uuid::Uuid;

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
                pattern: Regex::new(r"AKIA[0-9A-Z]{16}").unwrap(),
                severity: Severity::Critical,
            },
            SecretPattern {
                name: "AWS Secret Access Key",
                pattern: Regex::new(r"(?i)aws_secret_access_key\s*[=:]\s*[A-Za-z0-9/+=]{40}").unwrap(),
                severity: Severity::Critical,
            },
            // GitHub
            SecretPattern {
                name: "GitHub Token",
                pattern: Regex::new(r"ghp_[a-zA-Z0-9]{36}").unwrap(),
                severity: Severity::Critical,
            },
            // Generic API keys
            SecretPattern {
                name: "Generic API Key",
                pattern: Regex::new(r"(?i)api[_-]?key\s*[=:]\s*[a-zA-Z0-9_\-]{20,}").unwrap(),
                severity: Severity::High,
            },
            SecretPattern {
                name: "Generic Secret",
                pattern: Regex::new(r"(?i)(secret|password|passwd|pwd)\s*[=:]\s*[^\s]{8,}").unwrap(),
                severity: Severity::High,
            },
            // Private keys
            SecretPattern {
                name: "Private Key",
                pattern: Regex::new(r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----").unwrap(),
                severity: Severity::Critical,
            },
            // Slack
            SecretPattern {
                name: "Slack Token",
                pattern: Regex::new(r"xox[baprs]-[0-9]{10,13}-[0-9]{10,13}[a-zA-Z0-9-]*").unwrap(),
                severity: Severity::Critical,
            },
            // Stripe
            SecretPattern {
                name: "Stripe API Key",
                pattern: Regex::new(r"sk_live_[a-zA-Z0-9]{24,}").unwrap(),
                severity: Severity::Critical,
            },
            // Database URLs
            SecretPattern {
                name: "Database URL with Password",
                pattern: Regex::new(r"(?i)(postgres|mysql|mongodb|redis)://[^:]+:[^@]+@").unwrap(),
                severity: Severity::Critical,
            },
            // SendGrid
            SecretPattern {
                name: "SendGrid API Key",
                pattern: Regex::new(r"SG\.[a-zA-Z0-9_-]{22}\.[a-zA-Z0-9_-]{43}").unwrap(),
                severity: Severity::High,
            },
        ]
    })
}

pub struct SecretDetector {
    config: DetectorConfig,
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
    
    fn scan_file(&self, path: &Path) -> Vec<Finding> {
        let mut findings = vec![];
        
        // Skip test files - they often contain test certificates/keys
        if is_test_file(path) {
            return findings;
        }
        
        // Use global cache for file content
        let content = match crate::cache::global_cache().get_content(path) {
            Some(c) => c,
            None => return findings,
        };

        // Skip binary files
        if content.contains('\0') {
            return findings;
        }

        for (line_num, line) in content.lines().enumerate() {
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
                    
                    // Determine effective severity based on context
                    let line_lower = line.to_lowercase();
                    let mut effective_severity = pattern.severity.clone();
                    
                    // Dev fallback pattern: process.env.X || 'fallback' or process.env.X ?? 'fallback'
                    // These are typically local dev defaults, not production credentials
                    if line_lower.contains("process.env") && (line.contains("||") || line.contains("??")) {
                        effective_severity = Severity::Low;
                    }
                    // Localhost URLs are lower risk - typically dev/test environments
                    else if matched.contains("localhost") || matched.contains("127.0.0.1") {
                        effective_severity = Severity::Low;
                    }
                    // Check file path for seed/script/test patterns
                    else if let Some(rel_path) = path.to_str() {
                        let rel_lower = rel_path.to_lowercase();
                        if rel_lower.contains("/seed") 
                            || rel_lower.contains("/script")
                            || rel_lower.contains("/fixture")
                            || rel_lower.contains(".seed.")
                            || rel_lower.contains(".script.")
                        {
                            effective_severity = Severity::Low;
                        }
                    }
                    
                    let line_start = line_num as u32 + 1;
                    findings.push(Finding {
                        id: Uuid::new_v4().to_string(),
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
                    });
                }
            }
        }

        findings
    }
}

impl Detector for SecretDetector {
    fn name(&self) -> &'static str {
        "secret-detection"
    }

    fn description(&self) -> &'static str {
        "Detects hardcoded secrets, API keys, and passwords"
    }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
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

            // Skip certain directories
            let path_str = path.to_string_lossy();
            if path_str.contains("node_modules") 
                || path_str.contains(".git")
                || path_str.contains("vendor")
                || path_str.contains("target")
            {
                continue;
            }

            // Only scan text files
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            let scannable = matches!(ext, 
                "py" | "js" | "ts" | "jsx" | "tsx" | "rs" | "go" | "java" | 
                "rb" | "php" | "cs" | "cpp" | "c" | "h" | "hpp" |
                "yaml" | "yml" | "json" | "toml" | "env" | "conf" | "config" |
                "sh" | "bash" | "zsh" | "properties" | "xml"
            );
            
            if !scannable {
                continue;
            }

            debug!("Scanning for secrets: {}", path.display());
            findings.extend(self.scan_file(path));
        }

        Ok(findings)
    }
}
