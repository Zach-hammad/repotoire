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
use std::sync::OnceLock;
use tracing::info;

static IP_PATTERN: OnceLock<Regex> = OnceLock::new();

fn ip_pattern() -> &'static Regex {
    IP_PATTERN.get_or_init(|| Regex::new(r#"["']?(127\.0\.0\.1|0\.0\.0\.0|localhost|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|192\.168\.\d+\.\d+)["']?"#).expect("valid regex"))
}

pub struct HardcodedIpsDetector {
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

    /// Find containing function for context
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

impl Detector for HardcodedIpsDetector {
    fn name(&self) -> &'static str {
        "hardcoded-ips"
    }
    fn description(&self) -> &'static str {
        "Detects hardcoded IPs and localhost"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let mut ip_occurrences: HashMap<String, usize> = HashMap::new();
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        // First pass: count IP occurrences
        for entry in ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build()
            .filter_map(|e| e.ok())
        {
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().content(path) {
                for line in content.lines() {
                    if let Some(m) = ip_pattern().find(line) {
                        *ip_occurrences.entry(m.as_str().to_string()).or_default() += 1;
                    }
                }
            }
        }

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings {
                break;
            }
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

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

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(
                ext,
                "py" | "js" | "ts" | "java" | "go" | "rs" | "rb" | "php" | "cs"
            ) {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().content(path) {
                let path_str = path.to_string_lossy().to_string();
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

                    if let Some(m) = ip_pattern().find(line) {
                        let ip = m.as_str().to_string();
                        let occurrences = ip_occurrences.get(&ip).copied().unwrap_or(1);
                        let (context, is_risky) = Self::analyze_context(line);
                        let containing_func =
                            Self::find_containing_function(graph, &path_str, (i + 1) as u32);

                        // Calculate severity based on context
                        let severity = if is_risky {
                            Severity::High // Database/API connections with hardcoded IPs
                        } else if occurrences > 3 {
                            Severity::Medium // Used in multiple places
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
                            title: format!("Hardcoded IP: {}", ip),
                            description: format!(
                                "Hardcoded IPs make deployment inflexible and can expose internal network structure.{}",
                                context_notes
                            ),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
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
                }
            }
        }

        info!(
            "HardcodedIpsDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
