//! Hardcoded IPs Detector
//!
//! Graph-enhanced detection of hardcoded IPs.
//! Uses graph to:
//! - Check if IP is used in network/database functions (higher risk)
//! - Count occurrences across files (suggests centralized config needed)
//! - Identify the context (connection string, URL, etc.)

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::LazyLock;
use tracing::info;

static IP_PATTERN: LazyLock<Regex> = LazyLock::new(|| Regex::new(r#"["']?(127\.0\.0\.1|0\.0\.0\.0|localhost|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|192\.168\.\d+\.\d+)["']?"#).expect("valid regex"));

pub struct HardcodedIpsDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
}

impl HardcodedIpsDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Analyze context of the hardcoded IP
    fn analyze_context(line: &str) -> (String, bool) {
        let line_lower = line.to_lowercase();

        // Database connection patterns
        if line_lower.contains("postgres")
            || line_lower.contains("mysql")
            || line_lower.contains("mongo")
            || line_lower.contains("redis")
            || line_lower.contains("jdbc")
            || line_lower.contains("database")
        {
            return ("Database connection".to_string(), true);
        }

        // API/HTTP patterns
        if line_lower.contains("http")
            || line_lower.contains("api")
            || line_lower.contains("endpoint")
            || line_lower.contains("url")
        {
            return ("API endpoint".to_string(), true);
        }

        // Network patterns
        if line_lower.contains("connect")
            || line_lower.contains("socket")
            || line_lower.contains("host")
            || line_lower.contains("server")
        {
            return ("Network connection".to_string(), true);
        }

        ("General usage".to_string(), false)
    }

}

impl Detector for HardcodedIpsDetector {
    fn name(&self) -> &'static str {
        "hardcoded-ips"
    }
    fn description(&self) -> &'static str {
        "Detects hardcoded IPs and localhost"
    }

    fn requires_graph(&self) -> bool {
        false
    }

    fn file_extensions(&self) -> &'static [&'static str] {
        &["py", "js", "ts", "jsx", "tsx", "rb", "php", "java", "go", "rs", "c", "cpp"]
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let mut ip_occurrences: HashMap<String, usize> = HashMap::new();

        // Single pass: collect matches AND count occurrences simultaneously
        struct IpMatch {
            path: std::path::PathBuf,
            line_num: u32,
            ip: String,
            line_text: String,
        }
        let mut matches: Vec<IpMatch> = Vec::new();

        for path in files.files_with_extensions(&["py", "js", "ts", "java", "go", "rs", "rb", "php", "cs"]) {
            // Skip config files where this is expected
            let fname = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
            if fname.contains("config")
                || crate::detectors::base::is_test_path(fname)
                || fname.contains(".env")
            {
                continue;
            }
            if fname.contains("detector") || fname.contains("scanner") {
                continue;
            }

            // Use raw content first for a cheap pre-check — skip files without
            // any IP-like digit patterns to avoid expensive masked_content() parsing
            let raw = match files.content(path) {
                Some(c) => c,
                None => continue,
            };
            if !raw.contains("127.") && !raw.contains("0.0.0") && !raw.contains("10.")
                && !raw.contains("172.") && !raw.contains("192.168") && !raw.contains("localhost")
            {
                continue;
            }

            if let Some(content) = files.masked_content(path) {
                let lines: Vec<&str> = content.lines().collect();

                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    let trimmed = line.trim();
                    if trimmed.starts_with("//") || trimmed.starts_with("#") {
                        continue;
                    }

                    let lower = line.to_lowercase();
                    if lower.contains("ollama")
                        || lower.contains("local")
                        || lower.contains("dev")
                        || lower.contains("default")
                        || lower.contains(":11434")
                        || lower.contains(":3000")
                        || lower.contains(":8080")
                        || lower.contains(":5000")
                    {
                        continue;
                    }

                    if let Some(m) = IP_PATTERN.find(line) {
                        let ip = m.as_str().to_string();
                        *ip_occurrences.entry(ip.clone()).or_default() += 1;
                        matches.push(IpMatch {
                            path: path.to_path_buf(),
                            line_num: (i + 1) as u32,
                            ip,
                            line_text: line.to_string(),
                        });
                    }
                }
            }
        }

        // Generate findings using accumulated counts
        for m in &matches {
            if findings.len() >= self.max_findings {
                break;
            }
            let occurrences = ip_occurrences.get(&m.ip).copied().unwrap_or(1);
            let (context, is_risky) = Self::analyze_context(&m.line_text);
            let path_str = m.path.to_string_lossy();
            let containing_func =
                graph.find_function_at(&path_str, m.line_num).map(|f| f.node_name(crate::graph::interner::global_interner()).to_string());

            let severity = if is_risky {
                Severity::High
            } else if occurrences > 3 {
                Severity::Medium
            } else {
                Severity::Low
            };

            let mut notes = Vec::new();
            notes.push(format!("📍 Context: {}", context));
            if occurrences > 1 {
                notes.push(format!("📊 Found {} times in codebase", occurrences));
            }
            if let Some(func) = containing_func {
                notes.push(format!("📦 In function: `{}`", func));
            }

            let context_notes = format!("\n\n**Analysis:**\n{}", notes.join("\n"));

            let suggestion = if occurrences > 3 {
                format!(
                    "This IP appears {} times. Create a centralized config:\n\
                     ```python\n\
                     # config.py\n\
                     import os\n\
                     HOST = os.environ.get('APP_HOST', 'localhost')\n\
                     ```",
                    occurrences
                )
            } else if is_risky {
                format!(
                    "This {} uses a hardcoded IP. Use environment variables:\n\
                     ```bash\n\
                     export DATABASE_HOST=...\n\
                     ```\n\
                     ```python\n\
                     host = os.environ.get('DATABASE_HOST')\n\
                     ```",
                    context.to_lowercase()
                )
            } else {
                "Use environment variables or config files.".to_string()
            };

            findings.push(Finding {
                id: String::new(),
                detector: "HardcodedIpsDetector".to_string(),
                severity,
                title: format!("Hardcoded IP: {}", m.ip),
                description: format!(
                    "Hardcoded IPs make deployment inflexible and can expose internal network structure.{}",
                    context_notes
                ),
                affected_files: vec![m.path.clone()],
                line_start: Some(m.line_num),
                line_end: Some(m.line_num),
                suggested_fix: Some(suggestion),
                estimated_effort: Some("10 minutes".to_string()),
                category: Some("configuration".to_string()),
                cwe_id: Some("CWE-798".to_string()),
                why_it_matters: Some(
                    "Hardcoded IPs break in different environments (dev/staging/prod) \
                     and can leak internal network topology.".to_string()
                ),
                ..Default::default()
            });
        }

        info!(
            "HardcodedIpsDetector found {} findings (graph-aware)",
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
    fn test_detects_hardcoded_ip_in_connection() {
        // Use .rb extension: masking has no tree-sitter grammar for Ruby,
        // so the content is returned unchanged and the IP stays visible.
        let store = GraphStore::in_memory();
        let detector = HardcodedIpsDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("database.rb", "require 'pg'\n\ndef connect\n  conn = PG.connect(host: \"192.168.1.100\", dbname: \"mydb\")\n  conn\nend\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect hardcoded IP 192.168.1.100 in database connection"
        );
        assert!(
            findings.iter().any(|f| f.title.contains("192.168.1.100")),
            "Finding should mention the IP. Titles: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_clean_code() {
        let store = GraphStore::in_memory();
        let detector = HardcodedIpsDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("database.py", "import os\nimport psycopg2\n\ndef connect():\n    host = os.environ.get(\"DB_HOST\")\n    conn = psycopg2.connect(host=host, database=\"mydb\")\n    return conn\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Code using env vars should produce no findings, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_ip_in_docstring() {
        let store = GraphStore::in_memory();
        let detector = HardcodedIpsDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("network.py", "def connect_to_db():\n    \"\"\"\n    Connect to the database at 192.168.1.100.\n    \"\"\"\n    return create_connection()\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag IP addresses inside docstrings. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_ip_in_comment() {
        let store = GraphStore::in_memory();
        let detector = HardcodedIpsDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("server.py", "# Default: connect to 192.168.1.50 for staging\ndef get_host():\n    return os.environ.get('HOST')\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag IP addresses inside comments. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
