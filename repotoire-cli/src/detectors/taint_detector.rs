//! Taint tracking detector for security vulnerability detection
//!
//! Uses data flow analysis to trace potentially malicious data from
//! untrusted sources (user input, network, files) to dangerous sinks.
//!
//! Detects:
//! - SQL injection (user input in database queries)
//! - Command injection (user input in shell commands)
//! - Code injection (user input in eval/exec)
//! - Path traversal (user input in file operations)
//! - XSS (user input in rendered templates)
//! - SSRF (user input in HTTP requests)
//! - Log injection (user input in log messages)
//!
//! This detector uses pattern-based detection when the Rust taint analyzer

#![allow(dead_code)] // Module under development - structs/helpers used in tests only
//! is not available, providing similar coverage through regex matching.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use tracing::{debug, info};
use uuid::Uuid;

/// Taint source patterns (user input, network, etc.)
/// NOTE: These should only be actual INPUT sources, not operations that use input
const TAINT_SOURCES: &[(&str, &str)] = &[
    // Web request input (user-controlled)
    ("request.args", "user_input"),
    ("request.form", "user_input"),
    ("request.data", "user_input"),
    ("request.json", "user_input"),
    ("request.files", "user_input"),
    ("request.cookies", "user_input"),
    ("request.headers", "user_input"),
    ("request.GET", "user_input"),
    ("request.POST", "user_input"),
    ("request.body", "user_input"),
    ("req.params", "user_input"),
    ("req.query", "user_input"),
    ("req.body", "user_input"),
    // CLI/stdin input
    ("input(", "user_input"),
    ("raw_input(", "user_input"),
    ("sys.stdin", "user_input"),
    ("sys.argv", "user_input"),
    ("argv[", "user_input"),
    // Environment (may be attacker-controlled in some contexts)
    ("os.environ", "environment"),
    ("getenv(", "environment"),
    ("process.env", "environment"),
    // Network input (reading FROM network, not making requests)
    ("socket.recv(", "network"),
    (".read()", "file"), // Reading file content, not opening
                         // NOTE: open() is NOT a source - it's a sink for path traversal
                         // NOTE: requests.get() is a sink for SSRF, not a source
];

/// Taint sink patterns (dangerous operations)
/// A sink is where tainted data becomes dangerous if unsanitized
const TAINT_SINKS: &[(&str, &str)] = &[
    // SQL injection sinks
    ("cursor.execute(", "sql_injection"),
    (".execute(", "sql_injection"),
    ("executemany(", "sql_injection"),
    (".raw(", "sql_injection"),
    ("rawQuery(", "sql_injection"),
    ("$query(", "sql_injection"),
    // Command injection sinks
    ("os.system(", "command_injection"),
    ("os.popen(", "command_injection"),
    ("subprocess.call(", "command_injection"),
    ("subprocess.run(", "command_injection"),
    ("subprocess.Popen(", "command_injection"),
    ("child_process.exec(", "command_injection"),
    ("execSync(", "command_injection"),
    // Code injection sinks
    ("eval(", "code_injection"),
    ("exec(", "code_injection"),
    ("Function(", "code_injection"),
    // Path traversal sinks (path argument from user input)
    ("send_file(", "path_traversal"),
    ("send_from_directory(", "path_traversal"),
    ("res.sendFile(", "path_traversal"),
    ("res.download(", "path_traversal"),
    // NOTE: open() removed - too noisy, most opens are safe internal operations
    // XSS sinks
    ("render_template_string(", "xss"),
    ("Markup(", "xss"),
    ("innerHTML", "xss"),
    ("document.write(", "xss"),
    ("dangerouslySetInnerHTML", "xss"),
    // SSRF sinks (URL from user input)
    ("urlopen(", "ssrf"),
    ("requests.get(", "ssrf"),
    ("requests.post(", "ssrf"),
    ("httpx.get(", "ssrf"),
    ("fetch(", "ssrf"),
    ("axios.get(", "ssrf"),
    // Log injection sinks
    ("logging.info(", "log_injection"),
    ("logging.debug(", "log_injection"),
    ("logging.warning(", "log_injection"),
    ("logging.error(", "log_injection"),
    ("logger.info(", "log_injection"),
    ("console.log(", "log_injection"),
];

/// Vulnerability severity mapping
fn get_severity(vulnerability: &str) -> Severity {
    match vulnerability {
        "sql_injection" | "command_injection" | "code_injection" => Severity::Critical,
        "path_traversal" | "xss" | "ssrf" => Severity::High,
        "log_injection" => Severity::Medium,
        _ => Severity::High,
    }
}

/// CWE ID mapping
fn get_cwe(vulnerability: &str) -> &'static str {
    match vulnerability {
        "sql_injection" => "CWE-89",
        "command_injection" => "CWE-78",
        "code_injection" => "CWE-94",
        "path_traversal" => "CWE-22",
        "xss" => "CWE-79",
        "ssrf" => "CWE-918",
        "log_injection" => "CWE-117",
        _ => "CWE-20",
    }
}

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

/// Taint tracking detector using pattern-based analysis
pub struct TaintDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
    exclude_patterns: Vec<String>,
    source_patterns: Vec<(Regex, String)>,
    sink_patterns: Vec<(Regex, String)>,
}

impl TaintDetector {
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

        // Compile source patterns
        let source_patterns: Vec<(Regex, String)> = TAINT_SOURCES
            .iter()
            .filter_map(|(pattern, category)| {
                Regex::new(&regex::escape(pattern))
                    .ok()
                    .map(|re| (re, category.to_string()))
            })
            .collect();

        // Compile sink patterns
        let sink_patterns: Vec<(Regex, String)> = TAINT_SINKS
            .iter()
            .filter_map(|(pattern, vuln)| {
                Regex::new(&regex::escape(pattern))
                    .ok()
                    .map(|re| (re, vuln.to_string()))
            })
            .collect();

        Self {
            config,
            repository_path,
            max_findings,
            exclude_patterns,
            source_patterns,
            sink_patterns,
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

    /// Analyze a file for taint flows using pattern matching
    fn analyze_file(&self, content: &str, rel_path: &str) -> Vec<TaintFlow> {
        let mut flows = Vec::new();
        let lines: Vec<&str> = content.lines().collect();

        // Track variables that come from taint sources
        let mut tainted_vars: HashMap<String, (u32, String)> = HashMap::new();

        // Variable assignment pattern
        let assign_pattern = Regex::new(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=").unwrap();

        for (line_no, line) in lines.iter().enumerate() {
            let line_num = (line_no + 1) as u32;

            // Skip comments
            if line.trim().starts_with('#') {
                continue;
            }

            // Check for suppression comments
            let prev_line = if line_no > 0 {
                Some(lines[line_no - 1])
            } else {
                None
            };
            if crate::detectors::is_line_suppressed(line, prev_line) {
                continue;
            }

            // Check for taint sources
            for (pattern, category) in &self.source_patterns {
                if pattern.is_match(line) {
                    // Try to extract variable name
                    if let Some(caps) = assign_pattern.captures(line) {
                        if let Some(var) = caps.get(1) {
                            tainted_vars
                                .insert(var.as_str().to_string(), (line_num, category.clone()));
                        }
                    }
                }
            }

            // Check for taint sinks
            for (pattern, vulnerability) in &self.sink_patterns {
                if pattern.is_match(line) {
                    // Check if any tainted variable is used in this line
                    for (var, (source_line, source_category)) in &tainted_vars {
                        if line.contains(var.as_str()) {
                            flows.push(TaintFlow {
                                file_path: rel_path.to_string(),
                                source: var.clone(),
                                source_line: *source_line,
                                source_category: source_category.clone(),
                                sink: pattern.as_str().to_string(),
                                sink_line: line_num,
                                vulnerability: vulnerability.clone(),
                                snippet: line.trim().to_string(),
                            });
                        }
                    }

                    // Also check for direct flows (source in same line as sink)
                    for (src_pattern, src_category) in &self.source_patterns {
                        if src_pattern.is_match(line) {
                            flows.push(TaintFlow {
                                file_path: rel_path.to_string(),
                                source: src_pattern.as_str().to_string(),
                                source_line: line_num,
                                source_category: src_category.clone(),
                                sink: pattern.as_str().to_string(),
                                sink_line: line_num,
                                vulnerability: vulnerability.clone(),
                                snippet: line.trim().to_string(),
                            });
                        }
                    }
                }
            }
        }

        flows
    }

    /// Scan source files for taint flows
    fn scan_source_files(&self) -> Vec<Finding> {
        use crate::detectors::walk_source_files;

        let mut findings = Vec::new();
        let mut seen_locations: HashSet<(String, u32)> = HashSet::new();

        if !self.repository_path.exists() {
            return findings;
        }

        // Walk through Python files (respects .gitignore and .repotoireignore)
        for path in walk_source_files(&self.repository_path, Some(&["py"])) {
            let rel_path = path
                .strip_prefix(&self.repository_path)
                .unwrap_or(&path)
                .to_string_lossy()
                .to_string();

            if self.should_exclude(&rel_path) {
                continue;
            }

            let content = match std::fs::read_to_string(&path) {
                Ok(c) => c,
                Err(_) => continue,
            };

            // Skip very large files
            if content.len() > 1_000_000 {
                continue;
            }

            // Analyze file for taint flows
            let flows = self.analyze_file(&content, &rel_path);

            for flow in flows {
                let loc = (flow.file_path.clone(), flow.sink_line);
                if seen_locations.contains(&loc) {
                    continue;
                }
                seen_locations.insert(loc);

                findings.push(self.create_finding(&flow));

                if findings.len() >= self.max_findings {
                    return findings;
                }
            }
        }

        // Sort by severity
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        findings
    }

    /// Create a finding from a taint flow
    fn create_finding(&self, flow: &TaintFlow) -> Finding {
        let severity = get_severity(&flow.vulnerability);
        let cwe = get_cwe(&flow.vulnerability);
        let vuln_title = flow.vulnerability.replace('_', " ");

        let title = format!("Potential {} ({})", vuln_title, cwe);

        let description = format!(
            "**Potential {} Vulnerability**\n\n\
             **Location**: {}:{}-{}\n\n\
             **Taint Source**: `{}` (line {})\n\
             - Category: {}\n\n\
             **Dangerous Sink**: `{}` (line {})\n\n\
             **Code snippet**:\n```python\n{}\n```\n\n\
             {}",
            vuln_title,
            flow.file_path,
            flow.source_line,
            flow.sink_line,
            flow.source,
            flow.source_line,
            flow.source_category,
            flow.sink,
            flow.sink_line,
            flow.snippet,
            self.get_vulnerability_description(&flow.vulnerability)
        );

        let suggested_fix = self.get_recommendation(&flow.vulnerability);

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "TaintDetector".to_string(),
            severity,
            title,
            description,
            affected_files: vec![PathBuf::from(&flow.file_path)],
            line_start: Some(flow.source_line),
            line_end: Some(flow.sink_line),
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some(self.estimate_effort(&flow.vulnerability)),
            category: Some("security".to_string()),
            cwe_id: Some(cwe.to_string()),
            why_it_matters: Some(format!(
                "This {} vulnerability could allow attackers to {}",
                vuln_title,
                self.get_impact(&flow.vulnerability)
            )),
            ..Default::default()
        }
    }

    /// Get vulnerability description
    fn get_vulnerability_description(&self, vulnerability: &str) -> &'static str {
        match vulnerability {
            "sql_injection" => {
                "User input flows to a SQL query without proper parameterization. \
                 An attacker could manipulate the query to access or modify data."
            }
            "command_injection" => {
                "User input flows to a shell command. \
                 An attacker could execute arbitrary system commands."
            }
            "code_injection" => {
                "User input flows to dynamic code execution (eval/exec). \
                 An attacker could execute arbitrary Python code."
            }
            "path_traversal" => {
                "User input flows to a file path. \
                 An attacker could access files outside the intended directory."
            }
            "xss" => {
                "User input flows to rendered output. \
                 An attacker could inject malicious scripts."
            }
            "ssrf" => {
                "User input flows to an HTTP request URL. \
                 An attacker could make requests to internal services."
            }
            "log_injection" => {
                "User input flows to log output. \
                 An attacker could forge log entries or inject malicious data."
            }
            _ => "Potential security vulnerability detected.",
        }
    }

    /// Get recommendation for fixing the vulnerability
    fn get_recommendation(&self, vulnerability: &str) -> String {
        match vulnerability {
            "sql_injection" => "Use parameterized queries or prepared statements:\n\
                 ```python\n\
                 cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))\n\
                 ```"
            .to_string(),
            "command_injection" => {
                "Avoid shell commands with user input. Use subprocess with shell=False:\n\
                 ```python\n\
                 subprocess.run(['ls', '-l', path], shell=False)\n\
                 ```"
                .to_string()
            }
            "code_injection" => "Never use eval/exec with user input. Use safer alternatives:\n\
                 ```python\n\
                 import ast\n\
                 data = ast.literal_eval(user_string)\n\
                 ```"
            .to_string(),
            "path_traversal" => "Use os.path.basename() or validate paths:\n\
                 ```python\n\
                 safe_path = os.path.join(base_dir, os.path.basename(user_path))\n\
                 ```"
            .to_string(),
            "xss" => "Escape HTML output using framework-provided functions:\n\
                 ```python\n\
                 from markupsafe import escape\n\
                 safe_output = escape(user_input)\n\
                 ```"
            .to_string(),
            "ssrf" => "Validate URLs against an allowlist of permitted domains:\n\
                 ```python\n\
                 ALLOWED_HOSTS = {'api.example.com', 'cdn.example.com'}\n\
                 if urlparse(user_url).netloc in ALLOWED_HOSTS:\n\
                     # Safe to request\n\
                 ```"
            .to_string(),
            "log_injection" => "Sanitize user input before logging:\n\
                 ```python\n\
                 safe_input = user_input.replace('\\n', '').replace('\\r', '')\n\
                 logger.info('User action: %s', safe_input)\n\
                 ```"
            .to_string(),
            _ => "Review and sanitize user input before use.".to_string(),
        }
    }

    /// Estimate effort to fix
    fn estimate_effort(&self, vulnerability: &str) -> String {
        match vulnerability {
            "sql_injection" | "command_injection" | "code_injection" => {
                "Medium-High (2-8 hours)".to_string()
            }
            "path_traversal" | "ssrf" | "xss" => "Medium (1-4 hours)".to_string(),
            _ => "Low-Medium (30 min - 2 hours)".to_string(),
        }
    }

    /// Get impact description
    fn get_impact(&self, vulnerability: &str) -> &'static str {
        match vulnerability {
            "sql_injection" => "access, modify, or delete sensitive database records",
            "command_injection" => "execute arbitrary commands on the server",
            "code_injection" => "execute arbitrary Python code with application privileges",
            "path_traversal" => "read or write files outside intended directories",
            "xss" => "steal user sessions, inject malicious content, or redirect users",
            "ssrf" => "access internal services or scan internal networks",
            "log_injection" => "forge log entries or exploit log parsing vulnerabilities",
            _ => "compromise application security",
        }
    }
}

/// Represents a taint flow from source to sink
#[derive(Debug, Clone)]
struct TaintFlow {
    file_path: String,
    source: String,
    source_line: u32,
    source_category: String,
    sink: String,
    sink_line: u32,
    vulnerability: String,
    snippet: String,
}

impl Default for TaintDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for TaintDetector {
    fn name(&self) -> &'static str {
        "TaintDetector"
    }

    fn description(&self) -> &'static str {
        "Detects security vulnerabilities through taint tracking (data flow from sources to sinks)"
    }

    fn category(&self) -> &'static str {
        "security"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        debug!("Starting taint tracking detection");

        let findings = self.scan_source_files();

        info!(
            "TaintDetector found {} potential vulnerabilities",
            findings.len()
        );

        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_severity_mapping() {
        assert_eq!(get_severity("sql_injection"), Severity::Critical);
        assert_eq!(get_severity("command_injection"), Severity::Critical);
        assert_eq!(get_severity("xss"), Severity::High);
        assert_eq!(get_severity("log_injection"), Severity::Medium);
    }

    #[test]
    fn test_cwe_mapping() {
        assert_eq!(get_cwe("sql_injection"), "CWE-89");
        assert_eq!(get_cwe("command_injection"), "CWE-78");
        assert_eq!(get_cwe("path_traversal"), "CWE-22");
    }

    #[test]
    fn test_taint_flow_detection() {
        let detector = TaintDetector::new();

        let code = r#"
user_id = request.args.get('id')
cursor.execute(f"SELECT * FROM users WHERE id={user_id}")
"#;

        let flows = detector.analyze_file(code, "test.py");
        assert!(!flows.is_empty());
        assert!(flows.iter().any(|f| f.vulnerability == "sql_injection"));
    }

    #[test]
    fn test_direct_flow_detection() {
        let detector = TaintDetector::new();

        let code = r#"
os.system(request.args.get('cmd'))
"#;

        let flows = detector.analyze_file(code, "test.py");
        assert!(!flows.is_empty());
        assert!(flows.iter().any(|f| f.vulnerability == "command_injection"));
    }
}
