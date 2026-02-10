//! Eval/exec code execution detector
//!
//! Detects dangerous code execution patterns that can lead to Remote Code Execution:
//!
//! - eval() with non-literal argument
//! - exec() with non-literal argument
//! - compile() with user input
//! - __import__() with variable
//! - os.system(), subprocess.call() with shell=True and variables
//!
//! CWE-94: Code Injection
//! CWE-78: OS Command Injection

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphClient;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use tracing::{debug, info};
use uuid::Uuid;

/// Dangerous code execution functions
const CODE_EXEC_FUNCTIONS: &[&str] = &[
    "eval",
    "exec",
    "compile",
    "__import__",
    "import_module",
    "system",
    "popen",
    "call",
    "run",
    "Popen",
    "check_output",
    "check_call",
    "getoutput",
    "getstatusoutput",
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

/// Detects dangerous code execution patterns (eval, exec, etc.)
pub struct EvalDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
    exclude_patterns: Vec<String>,
    // Compiled regex patterns
    variable_arg_pattern: Regex,
    fstring_arg_pattern: Regex,
    concat_arg_pattern: Regex,
    format_arg_pattern: Regex,
    percent_arg_pattern: Regex,
    shell_true_pattern: Regex,
    literal_string_pattern: Regex,
}

impl EvalDetector {
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
        let func_names = CODE_EXEC_FUNCTIONS.join("|");

        let variable_arg_pattern = Regex::new(&format!(
            r"\b({func_names})\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*[,)]"
        ))
        .expect("Invalid regex");

        let fstring_arg_pattern = Regex::new(&format!(
            r#"\b({func_names})\s*\(\s*f["']"#
        ))
        .expect("Invalid regex");

        let concat_arg_pattern = Regex::new(&format!(
            r"\b({func_names})\s*\([^)]*\+"
        ))
        .expect("Invalid regex");

        let format_arg_pattern = Regex::new(&format!(
            r"\b({func_names})\s*\([^)]*\.format\s*\("
        ))
        .expect("Invalid regex");

        let percent_arg_pattern = Regex::new(&format!(
            r"\b({func_names})\s*\([^)]*%\s*"
        ))
        .expect("Invalid regex");

        let shell_true_pattern = Regex::new(
            r"(?i)\b(call|run|Popen|check_output|check_call)\s*\([^)]*shell\s*=\s*True"
        )
        .expect("Invalid regex");

        let literal_string_pattern = Regex::new(&format!(
            r#"\b({func_names})\s*\(\s*["'][^"']*["']\s*[,)]"#
        ))
        .expect("Invalid regex");

        Self {
            config,
            repository_path,
            max_findings,
            exclude_patterns,
            variable_arg_pattern,
            fstring_arg_pattern,
            concat_arg_pattern,
            format_arg_pattern,
            percent_arg_pattern,
            shell_true_pattern,
            literal_string_pattern,
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
                // Simple glob matching
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

    /// Check a line for dangerous patterns
    fn check_line_for_patterns(&self, line: &str) -> Option<PatternMatch> {
        let stripped = line.trim();
        if stripped.starts_with('#') {
            return None;
        }

        // Check if line contains a code exec function
        let has_exec_func = CODE_EXEC_FUNCTIONS.iter().any(|f| line.contains(f));
        if !has_exec_func {
            return None;
        }

        // Skip if it's a safe literal-only pattern
        if self.literal_string_pattern.is_match(line) {
            if !self.variable_arg_pattern.is_match(line)
                && !self.fstring_arg_pattern.is_match(line)
                && !self.concat_arg_pattern.is_match(line)
            {
                return None;
            }
        }

        // Check for shell=True (high severity for subprocess calls)
        if let Some(caps) = self.shell_true_pattern.captures(line) {
            return Some(PatternMatch {
                pattern_type: "shell_true".to_string(),
                function: caps.get(1).map(|m| m.as_str().to_string()).unwrap_or_default(),
            });
        }

        // Check f-string pattern (high risk)
        if let Some(caps) = self.fstring_arg_pattern.captures(line) {
            return Some(PatternMatch {
                pattern_type: "f-string".to_string(),
                function: caps.get(1).map(|m| m.as_str().to_string()).unwrap_or_default(),
            });
        }

        // Check concatenation pattern (high risk)
        if let Some(caps) = self.concat_arg_pattern.captures(line) {
            return Some(PatternMatch {
                pattern_type: "concatenation".to_string(),
                function: caps.get(1).map(|m| m.as_str().to_string()).unwrap_or_default(),
            });
        }

        // Check .format() pattern (high risk)
        if let Some(caps) = self.format_arg_pattern.captures(line) {
            return Some(PatternMatch {
                pattern_type: "format".to_string(),
                function: caps.get(1).map(|m| m.as_str().to_string()).unwrap_or_default(),
            });
        }

        // Check % formatting pattern (high risk)
        if let Some(caps) = self.percent_arg_pattern.captures(line) {
            return Some(PatternMatch {
                pattern_type: "percent_format".to_string(),
                function: caps.get(1).map(|m| m.as_str().to_string()).unwrap_or_default(),
            });
        }

        // Check variable argument pattern (moderate risk)
        if let Some(caps) = self.variable_arg_pattern.captures(line) {
            let arg = caps.get(2).map(|m| m.as_str()).unwrap_or("");
            // Skip common safe patterns
            if ["None", "True", "False", "__name__", "__file__"].contains(&arg) {
                return None;
            }
            return Some(PatternMatch {
                pattern_type: "variable_arg".to_string(),
                function: caps.get(1).map(|m| m.as_str().to_string()).unwrap_or_default(),
            });
        }

        None
    }

    /// Scan source files for dangerous patterns
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

                if let Some(pattern_match) = self.check_line_for_patterns(line) {
                    let loc = (rel_path.clone(), line_num);
                    if seen_locations.contains(&loc) {
                        continue;
                    }
                    seen_locations.insert(loc);

                    findings.push(self.create_finding(
                        &rel_path,
                        line_num,
                        &pattern_match.pattern_type,
                        &pattern_match.function,
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

    /// Create a finding for detected code execution vulnerability
    fn create_finding(
        &self,
        file_path: &str,
        line_start: u32,
        pattern_type: &str,
        callee_name: &str,
        snippet: &str,
    ) -> Finding {
        let pattern_descriptions = [
            ("f-string", "f-string with variable interpolation"),
            ("concatenation", "string concatenation with variable"),
            ("format", ".format() string interpolation"),
            ("percent_format", "% string formatting"),
            ("variable_arg", "variable passed as argument"),
            ("shell_true", "shell=True with dynamic command"),
        ];

        let pattern_desc = pattern_descriptions
            .iter()
            .find(|(t, _)| *t == pattern_type)
            .map(|(_, d)| *d)
            .unwrap_or("dynamic code construction");

        // Determine CWE based on function type
        let (cwe, cwe_name) = if ["system", "popen", "call", "run", "Popen", "check_output", "check_call"]
            .contains(&callee_name)
        {
            ("CWE-78", "OS Command Injection")
        } else if ["__import__", "import_module"].contains(&callee_name) {
            ("CWE-502", "Unsafe Dynamic Import")
        } else {
            ("CWE-94", "Code Injection")
        };

        let title = format!("{} via {}", cwe_name, callee_name);

        let description = format!(
            "**Potential {} Vulnerability ({})**\n\n\
             **Pattern detected**: {} in {}()\n\n\
             **Location**: {}:{}\n\n\
             **Code snippet**:\n```python\n{}\n```\n\n\
             This vulnerability occurs when untrusted input is passed to code execution\n\
             functions without proper validation.",
            cwe_name, cwe, pattern_desc, callee_name, file_path, line_start, snippet
        );

        let suggested_fix = self.get_recommendation(cwe, callee_name);

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "EvalDetector".to_string(),
            severity: Severity::Critical,
            title,
            description,
            affected_files: vec![PathBuf::from(file_path)],
            line_start: Some(line_start),
            line_end: Some(line_start),
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some("Medium (1-4 hours)".to_string()),
            category: Some("security".to_string()),
            cwe_id: Some(cwe.to_string()),
            why_it_matters: Some(format!(
                "Code execution vulnerabilities allow attackers to run arbitrary code on the server, \
                 potentially leading to complete system compromise."
            )),
        }
    }

    /// Get recommendation based on vulnerability type
    fn get_recommendation(&self, cwe: &str, callee_name: &str) -> String {
        let mut recommendation = format!(
            "**Recommended fixes**:\n\n\
             1. **Avoid {}() with user input** (strongly preferred):\n\
             - Find alternative approaches that don't require dynamic code execution\n\
             - Use data structures instead of code generation\n\n\
             2. **Use allowlists for known-safe values**:\n\
             ```python\n\
             ALLOWED_VALUES = {{\"option1\", \"option2\", \"option3\"}}\n\
             if user_input in ALLOWED_VALUES:\n\
                 # Safe to use\n\
             ```\n",
            callee_name
        );

        if cwe == "CWE-78" {
            recommendation.push_str(
                "\n3. **Use subprocess with list arguments instead of shell=True**:\n\
                 ```python\n\
                 # Instead of:\n\
                 subprocess.call(f\"ls {user_dir}\", shell=True)\n\
                 \n\
                 # Use:\n\
                 subprocess.call([\"ls\", user_dir])  # No shell injection possible\n\
                 ```\n\n\
                 4. **Use shlex.quote() if shell is absolutely required**:\n\
                 ```python\n\
                 import shlex\n\
                 subprocess.call(f\"command {shlex.quote(user_input)}\", shell=True)\n\
                 ```\n"
            );
        } else if ["eval", "exec"].contains(&callee_name) {
            recommendation.push_str(
                "\n3. **Use ast.literal_eval() for parsing data**:\n\
                 ```python\n\
                 # Instead of:\n\
                 data = eval(user_string)\n\
                 \n\
                 # Use:\n\
                 import ast\n\
                 data = ast.literal_eval(user_string)  # Only parses literals\n\
                 ```\n\n\
                 4. **Use json.loads() for JSON data**:\n\
                 ```python\n\
                 import json\n\
                 data = json.loads(user_string)\n\
                 ```\n"
            );
        }

        recommendation
    }
}

impl Default for EvalDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for EvalDetector {
    fn name(&self) -> &'static str {
        "EvalDetector"
    }

    fn description(&self) -> &'static str {
        "Detects dangerous code execution patterns (eval, exec, shell=True, etc.)"
    }

    fn category(&self) -> &'static str {
        "security"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, _graph: &GraphClient) -> Result<Vec<Finding>> {
        debug!("Starting eval/exec detection");

        // Primary detection is via source scanning
        let findings = self.scan_source_files();

        info!("EvalDetector found {} potential vulnerabilities", findings.len());

        Ok(findings)
    }
}

/// Represents a pattern match in source code
struct PatternMatch {
    pattern_type: String,
    function: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pattern_detection() {
        let detector = EvalDetector::new();

        // Should detect f-string in eval
        assert!(detector.check_line_for_patterns(r#"eval(f"code {var}")"#).is_some());

        // Should detect variable in eval
        assert!(detector.check_line_for_patterns("eval(user_input)").is_some());

        // Should detect shell=True
        assert!(detector.check_line_for_patterns("subprocess.call(cmd, shell=True)").is_some());

        // Should NOT detect literal string
        assert!(detector.check_line_for_patterns(r#"eval("1 + 1")"#).is_none());

        // Should NOT detect comments
        assert!(detector.check_line_for_patterns("# eval(user_input)").is_none());
    }

    #[test]
    fn test_exclude_patterns() {
        let detector = EvalDetector::new();

        assert!(detector.should_exclude("tests/test_eval.py"));
        assert!(detector.should_exclude("src/test_module.py"));
        assert!(detector.should_exclude("venv/lib/python3.9/eval.py"));
        assert!(!detector.should_exclude("src/security.py"));
    }
}
