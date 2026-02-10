//! SQL Injection detector
//!
//! Detects dangerous SQL patterns that can lead to SQL injection:
//!
//! - f-strings with SQL keywords and variable interpolation
//! - String concatenation in SQL queries
//! - .format() string interpolation in SQL
//! - % formatting in SQL queries
//!
//! CWE-89: Improper Neutralization of Special Elements used in an SQL Command

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use tracing::{debug, info};
use uuid::Uuid;

/// SQL-related function patterns to look for
const SQL_SINK_FUNCTIONS: &[&str] = &[
    "execute",
    "executemany",
    "executescript",
    "mogrify",
    "raw",
    "extra",
    "text",
    "from_statement",
    "run_sql",
    "execute_sql",
    "query",
];

/// SQL object patterns
const SQL_OBJECT_PATTERNS: &[&str] = &[
    "cursor",
    "connection",
    "conn",
    "db",
    "database",
    "engine",
    "session",
];

/// Default file patterns to exclude
const DEFAULT_EXCLUDE_PATTERNS: &[&str] = &[
    "tests/",
    "test_",
    "_test.py",
    "migrations/",
    "__pycache__/",
    ".git/",
    "node_modules/",
    "venv/",
    ".venv/",
];

/// Detects potential SQL injection vulnerabilities
pub struct SQLInjectionDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
    exclude_patterns: Vec<String>,
    // Compiled regex patterns
    fstring_sql_pattern: Regex,
    concat_sql_pattern: Regex,
    format_sql_pattern: Regex,
    percent_sql_pattern: Regex,
}

impl SQLInjectionDetector {
    /// Create a new detector with default settings
    pub fn new() -> Self {
        Self::with_config(DetectorConfig::new(), PathBuf::from("."))
    }

    /// Create with custom repository path
    pub fn with_repository_path(repository_path: PathBuf) -> Self {
        Self::with_config(DetectorConfig::new(), repository_path)
    }

    /// Create with custom config and repository path
    pub fn with_config(config: DetectorConfig, repository_path: PathBuf) -> Self {
        let max_findings = config.get_option_or("max_findings", 100);
        let exclude_patterns = config
            .get_option::<Vec<String>>("exclude_patterns")
            .unwrap_or_else(|| {
                DEFAULT_EXCLUDE_PATTERNS
                    .iter()
                    .map(|s| s.to_string())
                    .collect()
            });

        // Compile regex patterns
        // Pattern 1: f-string with SQL keywords (allow internal quotes)
        let fstring_sql_pattern = Regex::new(
            r#"(?i)f["'].*?\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|EXEC|EXECUTE)\b.*?\{[^}]+\}"#
        ).unwrap();

        // Pattern 2: String concatenation with SQL keywords (allow internal quotes)
        let concat_sql_pattern = Regex::new(
            r#"(?i)\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|EXEC|EXECUTE)\b.*["']\s*\+"#
        ).unwrap();

        // Pattern 3: .format() with SQL keywords (allow internal quotes)
        let format_sql_pattern = Regex::new(
            r#"(?i)\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|EXEC|EXECUTE)\b.*["']\.format\s*\("#
        ).unwrap();

        // Pattern 4: % formatting with SQL keywords (allow internal quotes)
        let percent_sql_pattern = Regex::new(
            r#"(?i)\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|EXEC|EXECUTE)\b.*%[sdr].*["']\s*%"#
        ).unwrap();

        Self {
            config,
            repository_path,
            max_findings,
            exclude_patterns,
            fstring_sql_pattern,
            concat_sql_pattern,
            format_sql_pattern,
            percent_sql_pattern,
        }
    }

    /// Check if path should be excluded
    fn should_exclude(&self, path: &str) -> bool {
        for pattern in &self.exclude_patterns {
            if pattern.ends_with('/') {
                let dir = pattern.trim_end_matches('/');
                if path.split('/').any(|p| p == dir) {
                    return true;
                }
            } else if pattern.contains('*') {
                let pattern = pattern.replace('*', ".*");
                if let Ok(re) = Regex::new(&format!("^{}$", pattern)) {
                    let filename = Path::new(path)
                        .file_name()
                        .and_then(|s| s.to_str())
                        .unwrap_or("");
                    if re.is_match(path) || re.is_match(filename) {
                        return true;
                    }
                }
            } else if path.contains(pattern) {
                return true;
            }
        }
        false
    }

    /// Check a line for dangerous SQL patterns
    fn check_line_for_patterns(&self, line: &str) -> Option<&'static str> {
        let stripped = line.trim();
        if stripped.starts_with('#') {
            return None;
        }

        // Check f-string pattern
        if self.fstring_sql_pattern.is_match(line) {
            return Some("f-string");
        }

        // Check concatenation pattern
        if self.concat_sql_pattern.is_match(line) {
            return Some("concatenation");
        }

        // Check .format() pattern
        if self.format_sql_pattern.is_match(line) {
            return Some("format");
        }

        // Check % formatting pattern
        if self.percent_sql_pattern.is_match(line) {
            return Some("percent_format");
        }

        None
    }

    /// Check if line appears to be in SQL execution context
    fn is_sql_context(&self, line: &str) -> bool {
        let line_lower = line.to_lowercase();

        // Check for SQL function calls
        for func in SQL_SINK_FUNCTIONS {
            if line_lower.contains(&format!(".{}(", func)) {
                return true;
            }
        }

        // Check for SQL object patterns
        for obj in SQL_OBJECT_PATTERNS {
            if line_lower.contains(&format!("{}.", obj)) {
                return true;
            }
        }

        // Check for Django/SQLAlchemy patterns
        if line_lower.contains(".objects.raw(") {
            return true;
        }
        if line_lower.contains("text(")
            && ["select", "insert", "update", "delete"]
                .iter()
                .any(|kw| line_lower.contains(kw))
        {
            return true;
        }

        false
    }

    /// Scan source files for dangerous SQL patterns
    fn scan_source_files(&self) -> Vec<Finding> {
        use crate::detectors::walk_source_files;
        
        let mut findings = Vec::new();
        let mut seen_locations: HashSet<(String, u32)> = HashSet::new();

        if !self.repository_path.exists() {
            debug!("Repository path does not exist: {:?}", self.repository_path);
            return findings;
        }
        
        debug!("Scanning for SQL injection in: {:?}", self.repository_path);

        // Walk through Python files (respects .gitignore and .repotoireignore)
        for path in walk_source_files(&self.repository_path, Some(&["py"])) {
            let rel_path = path
                .strip_prefix(&self.repository_path)
                .unwrap_or(&path)
                .to_string_lossy()
                .to_string();

            if self.should_exclude(&rel_path) {
                debug!("Excluding file: {}", rel_path);
                continue;
            }

            let content = match std::fs::read_to_string(path) {
                Ok(c) => c,
                Err(_) => continue,
            };

            // Skip very large files
            if content.len() > 500_000 {
                continue;
            }

            let lines: Vec<&str> = content.lines().collect();
            for (line_no, line) in lines.iter().enumerate() {
                let line_num = (line_no + 1) as u32;
                
                // Check for suppression comments
                let prev_line = if line_no > 0 { Some(lines[line_no - 1]) } else { None };
                if crate::detectors::is_line_suppressed(line, prev_line) {
                    continue;
                }

                if let Some(pattern_type) = self.check_line_for_patterns(line) {
                    // Report all SQL patterns - even if not in obvious SQL context
                    // Building a SQL string is dangerous regardless of where it's called
                    let loc = (rel_path.clone(), line_num);
                    if seen_locations.contains(&loc) {
                        continue;
                    }
                    seen_locations.insert(loc);

                    findings.push(self.create_finding(
                        &rel_path,
                        line_num,
                        pattern_type,
                        line.trim(),
                    ));

                    if findings.len() >= self.max_findings {
                        return findings;
                    }
                }
            }
        }

        findings
    }

    /// Create a finding for detected SQL injection vulnerability
    fn create_finding(
        &self,
        file_path: &str,
        line_start: u32,
        pattern_type: &str,
        snippet: &str,
    ) -> Finding {
        let pattern_descriptions = [
            ("f-string", "f-string with variable interpolation in SQL query"),
            ("concatenation", "string concatenation in SQL query"),
            ("format", ".format() string interpolation in SQL query"),
            ("percent_format", "% string formatting in SQL query"),
        ];

        let pattern_desc = pattern_descriptions
            .iter()
            .find(|(t, _)| *t == pattern_type)
            .map(|(_, d)| *d)
            .unwrap_or("dynamic SQL construction");

        let title = "Potential SQL Injection (CWE-89)".to_string();

        let description = format!(
            "**Potential SQL Injection Vulnerability**\n\n\
             **Pattern detected**: {}\n\n\
             **Location**: {}:{}\n\n\
             **Code snippet**:\n```python\n{}\n```\n\n\
             SQL injection occurs when untrusted input is incorporated into SQL queries without\n\
             proper sanitization. An attacker could manipulate the query to:\n\
             - Access unauthorized data\n\
             - Modify or delete database records\n\
             - Execute administrative operations\n\
             - In some cases, execute operating system commands\n\n\
             This vulnerability is classified as **CWE-89: Improper Neutralization of Special\n\
             Elements used in an SQL Command ('SQL Injection')**.",
            pattern_desc, file_path, line_start, snippet
        );

        let suggested_fix = "**Recommended fixes**:\n\n\
             1. **Use parameterized queries** (preferred):\n\
                ```python\n\
                # Instead of:\n\
                cursor.execute(f\"SELECT * FROM users WHERE id={user_id}\")\n\n\
                # Use:\n\
                cursor.execute(\"SELECT * FROM users WHERE id = ?\", (user_id,))\n\
                ```\n\n\
             2. **Use ORM methods properly**:\n\
                ```python\n\
                # Instead of:\n\
                User.objects.raw(f\"SELECT * FROM users WHERE id={user_id}\")\n\n\
                # Use:\n\
                User.objects.filter(id=user_id)\n\
                ```\n\n\
             3. **Use SQLAlchemy's bindparams**:\n\
                ```python\n\
                # Instead of:\n\
                engine.execute(text(f\"SELECT * FROM users WHERE id={user_id}\"))\n\n\
                # Use:\n\
                engine.execute(text(\"SELECT * FROM users WHERE id = :id\"), {\"id\": user_id})\n\
                ```\n\n\
             4. **Validate and sanitize input** when parameterization is not possible.";

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "SQLInjectionDetector".to_string(),
            severity: Severity::Critical,
            title,
            description,
            affected_files: vec![PathBuf::from(file_path)],
            line_start: Some(line_start),
            line_end: Some(line_start),
            suggested_fix: Some(suggested_fix.to_string()),
            estimated_effort: Some("Medium (1-4 hours)".to_string()),
            category: Some("security".to_string()),
            cwe_id: Some("CWE-89".to_string()),
            why_it_matters: Some(
                "SQL injection is one of the most dangerous vulnerabilities, allowing attackers \
                 to access, modify, or delete sensitive data in the database."
                    .to_string(),
            ),
        }
    }
}

impl Default for SQLInjectionDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for SQLInjectionDetector {
    fn name(&self) -> &'static str {
        "SQLInjectionDetector"
    }

    fn description(&self) -> &'static str {
        "Detects potential SQL injection vulnerabilities from string interpolation in queries"
    }

    fn category(&self) -> &'static str {
        "security"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        debug!("Starting SQL injection detection");

        let findings = self.scan_source_files();

        info!("SQLInjectionDetector found {} potential vulnerabilities", findings.len());

        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_fstring_sql_detection() {
        let detector = SQLInjectionDetector::new();

        // Should detect f-string SQL injection
        assert_eq!(
            detector.check_line_for_patterns(r#"cursor.execute(f"SELECT * FROM users WHERE id={user_id}")"#),
            Some("f-string")
        );

        // Should NOT detect static SQL
        assert!(detector.check_line_for_patterns(r#"cursor.execute("SELECT * FROM users")"#).is_none());
    }

    #[test]
    fn test_concat_sql_detection() {
        let detector = SQLInjectionDetector::new();

        // Should detect concatenation SQL injection
        assert_eq!(
            detector.check_line_for_patterns(r#"cursor.execute("SELECT * FROM users WHERE id=" + user_id)"#),
            Some("concatenation")
        );
    }

    #[test]
    fn test_format_sql_detection() {
        let detector = SQLInjectionDetector::new();

        // Should detect .format() SQL injection
        assert_eq!(
            detector.check_line_for_patterns(r#"cursor.execute("SELECT * FROM users WHERE id={}".format(user_id))"#),
            Some("format")
        );
    }

    #[test]
    fn test_percent_sql_detection() {
        let detector = SQLInjectionDetector::new();

        // Should detect % formatting SQL injection
        assert_eq!(
            detector.check_line_for_patterns(r#"cursor.execute("SELECT * FROM users WHERE id=%s" % user_id)"#),
            Some("percent_format")
        );
    }

    #[test]
    fn test_sql_context_detection() {
        let detector = SQLInjectionDetector::new();

        assert!(detector.is_sql_context("cursor.execute(query)"));
        assert!(detector.is_sql_context("conn.execute(sql)"));
        assert!(detector.is_sql_context("db.query(statement)"));
        assert!(detector.is_sql_context("User.objects.raw(sql)"));
        assert!(!detector.is_sql_context("print(message)"));
    }
}
