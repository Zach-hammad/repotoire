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
use crate::detectors::taint::{TaintAnalyzer, TaintCategory};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use tracing::{debug, info};

/// Dangerous code execution functions (without parens - used in regex)
const CODE_EXEC_FUNCTIONS: &[&str] = &["eval", "exec", "__import__", "import_module"];

/// Patterns that require a module prefix to avoid false positives
/// e.g. subprocess.run() is dangerous, but plugins.run() is not
const SHELL_EXEC_PREFIXES: &[&str] = &[
    r"os\.system",
    r"os\.popen",
    r"subprocess\.call",
    r"subprocess\.run",
    r"subprocess\.Popen",
    r"subprocess\.check_output",
    r"subprocess\.check_call",
    r"subprocess\.getoutput",
    r"subprocess\.getstatusoutput",
    r"child_process\.exec",
    r"child_process\.spawn",
    "execSync",
    "spawnSync",
    "shell_exec",
    "proc_open",
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
    "management/commands/",
];

/// Detects dangerous code execution patterns (eval, exec, etc.)
pub struct EvalDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
    exclude_patterns: Vec<String>,
    compiled_globs: Vec<Regex>,
    taint_analyzer: TaintAnalyzer,
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
        // Combine both simple functions and prefixed shell functions
        let simple_funcs = CODE_EXEC_FUNCTIONS.join("|");
        let shell_funcs = SHELL_EXEC_PREFIXES.join("|");
        let func_names = format!("{}|{}", simple_funcs, shell_funcs);

        let variable_arg_pattern = Regex::new(&format!(
            r"({func_names})\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*[,)]"
        ))
        .expect("valid regex: pattern built from hardcoded constants");

        let fstring_arg_pattern =
            Regex::new(&format!(r#"({func_names})\s*\(\s*f["']"#)).expect("valid regex: pattern built from hardcoded constants");

        let concat_arg_pattern =
            Regex::new(&format!(r"({func_names})\s*\([^)]*\+")).expect("valid regex: pattern built from hardcoded constants");

        let format_arg_pattern =
            Regex::new(&format!(r"({func_names})\s*\([^)]*\.format\s*\(")).expect("valid regex: pattern built from hardcoded constants");

        let percent_arg_pattern =
            Regex::new(&format!(r"({func_names})\s*\([^)]*%\s*")).expect("valid regex: pattern built from hardcoded constants");

        let shell_true_pattern =
            Regex::new(r"(?i)\b(call|run|Popen|check_output|check_call)\s*\([^)]*shell\s*=\s*True")
                .expect("valid regex: pattern built from hardcoded constants");

        let literal_string_pattern =
            Regex::new(&format!(r#"\b({func_names})\s*\(\s*["'][^"']*["']\s*[,)]"#))
                .expect("valid regex: pattern built from hardcoded constants");

        let compiled_globs: Vec<Regex> = exclude_patterns
            .iter()
            .filter(|p| p.contains('*'))
            .filter_map(|p| {
                let re_str = format!("^{}$", p.replace('*', ".*"));
                Regex::new(&re_str).ok()
            })
            .collect();

        Self {
            config,
            repository_path,
            max_findings,
            exclude_patterns,
            compiled_globs,
            taint_analyzer: TaintAnalyzer::new(),
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
                // Handle multi-segment directory patterns (e.g. "management/commands")
                if dir.contains('/') {
                    if path.contains(pattern.as_str()) || path.contains(dir) {
                        return true;
                    }
                } else if path.split('/').any(|p| p == dir) {
                    return true;
                }
            } else if pattern.contains('*') {
                // Glob patterns are pre-compiled in self.compiled_globs
                continue;
            } else if path.contains(pattern) {
                return true;
            }
        }
        // Check pre-compiled glob patterns
        let filename = Path::new(path)
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("");
        for re in &self.compiled_globs {
            if re.is_match(path) || re.is_match(filename) {
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

        // Skip safe framework-specific patterns that use these function names safely
        let lower = line.to_lowercase();
        if lower.contains("torch.compile") ||           // PyTorch JIT compiler
           lower.contains("tf.function") ||             // TensorFlow decorator
           lower.contains("jax.jit") ||                 // JAX JIT
           lower.contains("numba.jit") ||               // Numba JIT
           lower.contains("re.compile") ||              // Regex compilation
           lower.contains("regex.compile") ||           // Regex compilation
           lower.contains("pattern.compile") ||         // Pattern compilation
           lower.contains("compiler.compile") ||        // Generic compilers
           lower.contains("model.compile") ||           // Keras model.compile
           lower.contains("literal_eval") ||            // ast.literal_eval is SAFE (only parses literals)
           lower.contains("importlib.import_module")
        {
            // Standard library import (safer than __import__)
            return None;
        }

        // Check if line contains a code exec function
        let has_simple_exec = CODE_EXEC_FUNCTIONS.iter().any(|f| line.contains(f));

        // Filter out method calls like node.eval(context) and method definitions like def eval(self)
        // — these are NOT Python's builtin eval()
        let has_simple_exec = if has_simple_exec && line.contains("eval(") {
            // Check if eval( is preceded by a dot (method call like obj.eval(...))
            let eval_preceded_by_dot = line.find("eval(").map(|pos| {
                pos > 0 && line[..pos].trim_end().ends_with('.')
            }).unwrap_or(false);
            // Check if eval( is preceded by 'def' (method definition like def eval(self))
            let eval_preceded_by_def = line.find("eval(").map(|pos| {
                pos > 0 && line[..pos].trim_end().ends_with("def")
            }).unwrap_or(false);
            if eval_preceded_by_dot || eval_preceded_by_def {
                // Remove "eval" from consideration, check if other exec functions remain
                CODE_EXEC_FUNCTIONS.iter().any(|f| *f != "eval" && line.contains(f))
            } else {
                true
            }
        } else {
            has_simple_exec
        };

        let has_shell_exec = SHELL_EXEC_PREFIXES.iter().any(|f| {
            // Remove regex escapes for simple contains check
            let plain = f.replace(r"\.", ".");
            line.contains(&plain)
        });
        if !has_simple_exec && !has_shell_exec {
            return None;
        }

        // Skip if it's a safe literal-only pattern
        if self.literal_string_pattern.is_match(line)
            && !self.variable_arg_pattern.is_match(line)
            && !self.fstring_arg_pattern.is_match(line)
            && !self.concat_arg_pattern.is_match(line)
        {
            return None;
        }

        // Check for shell=True (high severity for subprocess calls)
        if let Some(caps) = self.shell_true_pattern.captures(line) {
            return Some(PatternMatch {
                pattern_type: "shell_true".to_string(),
                function: caps
                    .get(1)
                    .map(|m| m.as_str().to_string())
                    .unwrap_or_default(),
            });
        }

        // Skip subprocess.run/Popen/call WITHOUT shell=True — list args are safe
        if has_shell_exec && !line.contains("shell=True") && !line.contains("shell = True") {
            let is_subprocess = lower.contains("subprocess.run") || lower.contains("subprocess.popen")
                || lower.contains("subprocess.call") || lower.contains("subprocess.check_call")
                || lower.contains("subprocess.check_output");
            if is_subprocess {
                return None;
            }
        }

        // Check f-string pattern (high risk)
        if let Some(caps) = self.fstring_arg_pattern.captures(line) {
            return Some(PatternMatch {
                pattern_type: "f-string".to_string(),
                function: caps
                    .get(1)
                    .map(|m| m.as_str().to_string())
                    .unwrap_or_default(),
            });
        }

        // Check concatenation pattern (high risk)
        if let Some(caps) = self.concat_arg_pattern.captures(line) {
            return Some(PatternMatch {
                pattern_type: "concatenation".to_string(),
                function: caps
                    .get(1)
                    .map(|m| m.as_str().to_string())
                    .unwrap_or_default(),
            });
        }

        // Check .format() pattern (high risk)
        if let Some(caps) = self.format_arg_pattern.captures(line) {
            return Some(PatternMatch {
                pattern_type: "format".to_string(),
                function: caps
                    .get(1)
                    .map(|m| m.as_str().to_string())
                    .unwrap_or_default(),
            });
        }

        // Check % formatting pattern (high risk)
        if let Some(caps) = self.percent_arg_pattern.captures(line) {
            return Some(PatternMatch {
                pattern_type: "percent_format".to_string(),
                function: caps
                    .get(1)
                    .map(|m| m.as_str().to_string())
                    .unwrap_or_default(),
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
                function: caps
                    .get(1)
                    .map(|m| m.as_str().to_string())
                    .unwrap_or_default(),
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

            let content = match crate::cache::global_cache().masked_content(&path) {
                Some(c) => c.to_string(),
                None => continue,
            };

            // Skip very large files
            if content.len() > 500_000 {
                continue;
            }

            let lines: Vec<&str> = content.lines().collect();
            for (line_no, line) in lines.iter().enumerate() {
                let line_num = (line_no + 1) as u32;

                // Check for suppression comments
                let prev_line = if line_no > 0 {
                    Some(lines[line_no - 1])
                } else {
                    None
                };
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
        let (cwe, cwe_name) = if [
            "system",
            "popen",
            "call",
            "run",
            "Popen",
            "check_output",
            "check_call",
        ]
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

        // Determine severity based on context
        let file_lower = file_path.to_lowercase();
        let severity = if ["__import__", "import_module"].contains(&callee_name) {
            // Dynamic imports in framework internals (Flask, Django, etc.) are expected
            // and typically used for extension loading, not user input
            if file_lower.contains("/flask/")
                || file_lower.contains("/django/")
                || file_lower.contains("/werkzeug/")
                || file_lower.contains("/celery/")
                || file_lower.contains("/fastapi/")
                || file_lower.starts_with("django/")  // relative paths
                || file_lower.starts_with("flask/")    // relative paths
                || file_lower.starts_with("fastapi/")  // relative paths
                || file_lower.starts_with("werkzeug/") // relative paths
                || file_lower.starts_with("celery/")   // relative paths
                || file_lower.contains("helpers.py")
                || file_lower.contains("loader")
                || file_lower.contains("importer")
                || file_lower.contains("plugin")
            {
                Severity::Low // Framework internal - expected usage
            } else {
                Severity::High // Still concerning but not critical for imports
            }
        } else {
            Severity::Critical // eval/exec are always critical
        };

        Finding {
            id: String::new(),
            detector: "EvalDetector".to_string(),
            severity,
            title,
            description,
            affected_files: vec![PathBuf::from(file_path)],
            line_start: Some(line_start),
            line_end: Some(line_start),
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some("Medium (1-4 hours)".to_string()),
            category: Some("security".to_string()),
            cwe_id: Some(cwe.to_string()),
            why_it_matters: Some("Code execution vulnerabilities allow attackers to run arbitrary code on the server, \
                 potentially leading to complete system compromise.".to_string()),
            ..Default::default()
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
                 ```\n",
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
                 ```\n",
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

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, _files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        debug!("Starting eval/exec detection");

        // Primary detection is via source scanning
        let mut findings = self.scan_source_files();

        // Run taint analysis to adjust severity based on data flow
        let mut taint_results = self
            .taint_analyzer
            .trace_taint(graph, TaintCategory::CodeInjection);
        let intra_paths = crate::detectors::data_flow::run_intra_function_taint(
            &self.taint_analyzer,
            graph,
            TaintCategory::CodeInjection,
            &self.repository_path,
        );
        taint_results.extend(intra_paths);

        // Adjust severity based on taint analysis
        for finding in &mut findings {
            let file_path = finding
                .affected_files
                .first()
                .map(|p| p.to_string_lossy().to_string())
                .unwrap_or_default();
            let line = finding.line_start.unwrap_or(0);

            // Check if this finding has a confirmed taint path
            for taint in &taint_results {
                if taint.sink_file == file_path && taint.sink_line == line {
                    if taint.is_sanitized {
                        // Sanitized path - downgrade to Low
                        finding.severity = Severity::Low;
                    } else {
                        // Confirmed unsanitized path from user input - Critical
                        finding.severity = Severity::Critical;
                        finding.description = format!(
                            "{}\n\n**Taint Analysis:** Unsanitized data flow from {} (line {}) to sink.",
                            finding.description,
                            taint.source_function,
                            taint.source_line
                        );
                    }
                    break;
                }
            }
        }

        // Filter out Low severity (sanitized) findings
        findings.retain(|f| f.severity != Severity::Low);

        info!(
            "EvalDetector found {} potential vulnerabilities",
            findings.len()
        );

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
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_eval_with_variable() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let file = dir.path().join("handler.py");
        std::fs::write(
            &file,
            r#"
def process(user_input):
    result = eval(user_input)
    return result
"#,
        )
        .expect("should write test file");

        let store = GraphStore::in_memory();
        let detector = EvalDetector::with_repository_path(dir.path().to_path_buf());
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&store, &empty_files).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect eval() with variable argument"
        );
        assert!(findings.iter().any(|f| f.detector == "EvalDetector"));
    }

    #[test]
    fn test_no_finding_for_management_command() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let mgmt_dir = dir.path().join("management").join("commands");
        std::fs::create_dir_all(&mgmt_dir).expect("should write test file");
        let file = mgmt_dir.join("shell.py");
        std::fs::write(
            &file,
            "def handle(self, **options):\n    code = compile(source, '<shell>', 'exec')\n    exec(code)\n",
        ).expect("should write test file");

        let store = GraphStore::in_memory();
        let detector = EvalDetector::with_repository_path(dir.path().to_path_buf());
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&store, &empty_files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag exec() in management/commands/. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_method_eval() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let file = dir.path().join("smartif.py");
        std::fs::write(
            &file,
            "class Operator:\n    def eval(self, context):\n        return self.value\n\nresult = op.eval(context)\n",
        )
        .expect("should write test file");

        let store = GraphStore::in_memory();
        let detector = EvalDetector::with_repository_path(dir.path().to_path_buf());
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&store, &empty_files).expect("detection should succeed");
        let eval_findings: Vec<_> = findings.iter().filter(|f| f.title.contains("eval")).collect();
        assert!(eval_findings.is_empty(), "Should not flag .eval() method call. Found: {:?}",
            eval_findings.iter().map(|f| &f.title).collect::<Vec<_>>());
    }

    #[test]
    fn test_no_finding_for_safe_subprocess() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let file = dir.path().join("runner.py");
        std::fs::write(
            &file,
            "import subprocess\n\ndef run_command(args):\n    result = subprocess.run(args, capture_output=True)\n    return result.stdout\n",
        )
        .expect("should write test file");

        let store = GraphStore::in_memory();
        let detector = EvalDetector::with_repository_path(dir.path().to_path_buf());
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&store, &empty_files).expect("detection should succeed");
        let subprocess_findings: Vec<_> = findings.iter().filter(|f| {
            f.title.contains("subprocess") || f.title.contains("command") || f.title.contains("Shell") || f.title.contains("shell")
        }).collect();
        assert!(subprocess_findings.is_empty(), "Should not flag subprocess.run without shell=True. Found: {:?}",
            subprocess_findings.iter().map(|f| &f.title).collect::<Vec<_>>());
    }

    #[test]
    fn test_no_finding_for_literal_eval() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let file = dir.path().join("safe.py");
        std::fs::write(
            &file,
            r#"
import ast
data = ast.literal_eval("[1, 2, 3]")
"#,
        )
        .expect("should write test file");

        let store = GraphStore::in_memory();
        let detector = EvalDetector::with_repository_path(dir.path().to_path_buf());
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&store, &empty_files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag ast.literal_eval (safe), but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
