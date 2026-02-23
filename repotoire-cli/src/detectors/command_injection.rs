//! Command Injection Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::taint::{TaintAnalysisResult, TaintAnalyzer, TaintCategory};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

static SHELL_EXEC: OnceLock<Regex> = OnceLock::new();
static GO_EXEC: OnceLock<Regex> = OnceLock::new();
static JS_EXEC_DIRECT: OnceLock<Regex> = OnceLock::new();

fn shell_exec() -> &'static Regex {
    // Be specific about shell execution patterns - avoid matching RegExp.exec(), String.prototype.exec(), etc.
    // Pattern must match actual shell execution APIs:
    // - Python: os.system, os.popen, subprocess.*
    // - Node.js: child_process.exec, child_process.spawn, execSync, execAsync (promisified), require('child_process')
    // - PHP: shell_exec, system, popen, exec (standalone function)
    // - Ruby: system, exec, backticks
    // Note: execAsync is a common promisified wrapper for child_process.exec
    SHELL_EXEC.get_or_init(|| Regex::new(r#"(?i)(os\.system|os\.popen|subprocess\.(call|run|Popen)|child_process\.(exec|spawn|fork)|execSync|execAsync|spawnSync|require\(['"]child_process['"]\)|shell_exec|proc_open)"#).expect("valid regex"))
}

fn go_exec() -> &'static Regex {
    // Go exec patterns: exec.Command, exec.CommandContext
    GO_EXEC
        .get_or_init(|| Regex::new(r#"exec\.(Command|CommandContext)\s*\("#).expect("valid regex"))
}

fn js_exec_direct() -> &'static Regex {
    // Direct exec() call pattern for JavaScript - matches exec( but not .exec( to avoid RegExp.exec
    // This catches: exec(something), execSync(something), execAsync(something)
    JS_EXEC_DIRECT.get_or_init(|| {
        Regex::new(r#"(?:^|[^.\w])(exec|execSync|execAsync)\s*\("#).expect("valid regex")
    })
}

pub struct CommandInjectionDetector {
    repository_path: PathBuf,
    max_findings: usize,
    taint_analyzer: TaintAnalyzer,
}

impl CommandInjectionDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
            taint_analyzer: TaintAnalyzer::new(),
        }
    }

    /// Convert absolute path to relative path for consistent output
    fn relative_path(&self, path: &Path) -> PathBuf {
        path.strip_prefix(&self.repository_path)
            .unwrap_or(path)
            .to_path_buf()
    }
}

impl Detector for CommandInjectionDetector {
    fn name(&self) -> &'static str {
        "command-injection"
    }
    fn description(&self) -> &'static str {
        "Detects command injection vulnerabilities"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        // Run taint analysis for command injection
        let mut taint_paths = self
            .taint_analyzer
            .trace_taint(graph, TaintCategory::CommandInjection);
        let intra_paths = crate::detectors::data_flow::run_intra_function_taint(
            &self.taint_analyzer,
            graph,
            TaintCategory::CommandInjection,
            &self.repository_path,
        );
        taint_paths.extend(intra_paths);
        let taint_result = TaintAnalysisResult::from_paths(taint_paths);

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings {
                break;
            }
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(
                ext,
                "py" | "js" | "ts" | "rb" | "php" | "java" | "go" | "sh"
            ) {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().masked_content(path) {
                let lines: Vec<&str> = content.lines().collect();
                let file_str = path.to_string_lossy();

                // Check if this is a build/script file (developer-controlled, not user-facing)
                let is_build_script = file_str.contains("/scripts/")
                    || file_str.contains("/build/")
                    || file_str.contains("/tools/")
                    || file_str.contains("/ci/")
                    || file_str.contains("/.github/")
                    || file_str.contains("/gulp")
                    || file_str.contains("/grunt")
                    || file_str.contains("webpack")
                    || file_str.contains("rollup")
                    || file_str.contains("vite.config")
                    || file_str.ends_with(".config.js")
                    || file_str.ends_with(".config.ts");

                // First pass: find template literals with RISKY interpolation stored in variables
                // e.g., const cmd = `echo ${userId}`;  // userId could be user input
                // But NOT: const cmd = `echo ${CONSTANT}`;  // All-caps likely safe
                let mut dangerous_vars: Vec<String> = vec![];
                for line in &lines {
                    // Match: const/let/var VARNAME = `...${...}...`
                    if (line.contains("const ") || line.contains("let ") || line.contains("var "))
                        && line.contains("`")
                        && line.contains("${")
                    {
                        // Check if the interpolated content looks like user input
                        // Look for: params, req, request, body, query, input, userId, id, args, etc.
                        let lower = line.to_lowercase();
                        let has_risky_interpolation = lower.contains("${")
                            && (lower.contains("id}")
                                || lower.contains("id,")
                                || lower.contains("param")
                                || lower.contains("input")
                                || lower.contains("user")
                                || lower.contains("name}")
                                || lower.contains("args")
                                || lower.contains("arg}")
                                || lower.contains("req.")
                                || lower.contains("body")
                                || lower.contains("query"));

                        if has_risky_interpolation {
                            // Extract variable name
                            if let Some(eq_pos) = line.find('=') {
                                let before_eq = &line[..eq_pos];
                                let var_name = before_eq.split_whitespace().last().unwrap_or("");
                                if !var_name.is_empty() {
                                    dangerous_vars.push(var_name.to_string());
                                }
                            }
                        }
                    }
                }

                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    let line_num = (i + 1) as u32;

                    // Helper to check taint and adjust severity
                    let check_taint = |base_desc: &str| -> (Severity, String) {
                        let matching_taint = taint_result.paths.iter().find(|p| {
                            (p.sink_file == file_str || p.source_file == file_str)
                                && (p.sink_line == line_num || p.source_line == line_num)
                        });

                        match matching_taint {
                            Some(taint_path) if taint_path.is_sanitized => {
                                (Severity::Low, format!(
                                    "{}\n\n**Taint Analysis Note**: A sanitizer function (`{}`) was found \
                                     in the data flow path, which may mitigate this vulnerability.",
                                    base_desc,
                                    taint_path.sanitizer.as_deref().unwrap_or("unknown")
                                ))
                            }
                            Some(taint_path) => {
                                (Severity::Critical, format!(
                                    "{}\n\n**Taint Analysis Confirmed**: Data flow analysis traced a path \
                                     from user input to this command execution sink without sanitization:\n\n`{}`",
                                    base_desc,
                                    taint_path.path_string()
                                ))
                            }
                            None => (Severity::Critical, base_desc.to_string())
                        }
                    };

                    // Check for direct shell execution with template literal
                    if shell_exec().is_match(line) {
                        // Check for user input sources
                        let has_user_input = line.contains("req.")
                            || line.contains("request.")
                            || line.contains("params.")
                            || line.contains("params[")
                            || line.contains("query.")
                            || line.contains("body.")
                            || line.contains("input")
                            || line.contains("argv")
                            || line.contains("args");

                        // Check for string interpolation ON THIS LINE
                        let has_interpolation = line.contains("f\"")
                            || line.contains("${")
                            || line.contains("+ ")
                            || line.contains(".format(");

                        // Check for template literal with interpolation ON THIS LINE
                        let has_template_interpolation = line.contains("`") && line.contains("${");

                        // Check if exec is using a dangerous variable we identified earlier
                        let uses_dangerous_var = dangerous_vars.iter().any(|v| line.contains(v));

                        // Python subprocess shell=True is always dangerous
                        let has_shell_true =
                            line.contains("shell=True") || line.contains("shell: true");

                        // Check for SAFE patterns that reduce risk:
                        // 1. process.env.* - environment variables are developer-controlled
                        // 2. __dirname, __filename - Node.js path constants
                        // 3. path.join, path.resolve - safe path construction
                        // 4. UPPER_CASE variables - likely constants
                        let has_safe_source = line.contains("process.env")
                            || line.contains("__dirname")
                            || line.contains("__filename")
                            || line.contains("path.join")
                            || line.contains("path.resolve")
                            || line.contains("cwd()")
                            || line.contains("${ROOT")
                            || line.contains("${DIR")
                            || line.contains("${PATH");

                        // Check if ONLY safe sources are interpolated (no user input)
                        let only_safe_interpolation =
                            has_template_interpolation && has_safe_source && !has_user_input;

                        // HIGH RISK conditions:
                        // 1. shell=True (Python) - always dangerous
                        // 2. exec with user input AND interpolation on same line
                        // 3. exec with template literal + ${} on same line (unless safe source)
                        // 4. exec using a variable that was built from template with ${}
                        let is_risky = has_shell_true
                            || (has_user_input && has_interpolation)
                            || (has_template_interpolation && !only_safe_interpolation)
                            || uses_dangerous_var;

                        if is_risky {
                            let base_desc = if has_template_interpolation {
                                "Template literal with interpolation passed directly to shell execution. Variables are inserted unsanitized."
                            } else if uses_dangerous_var {
                                "Shell execution using a command string built from template literal. User input may flow into the command."
                            } else if has_shell_true {
                                "subprocess with shell=True allows shell injection through any unsanitized input."
                            } else {
                                "Shell command execution with potential user input."
                            };

                            let (mut severity, description) = check_taint(base_desc);

                            // Reduce severity for build scripts (developer-controlled)
                            if is_build_script && severity == Severity::Critical {
                                severity = Severity::Low; // Build scripts are not user-facing
                            } else if has_safe_source
                                && !has_user_input
                                && severity == Severity::Critical
                            {
                                // Safe sources (env vars, path constants) without user input
                                severity = Severity::Medium;
                            }

                            findings.push(Finding {
                                id: String::new(),
                                detector: "CommandInjectionDetector".to_string(),
                                severity,
                                title: "Potential command injection".to_string(),
                                description,
                                affected_files: vec![self.relative_path(path)],
                                line_start: Some(line_num),
                                line_end: Some(line_num),
                                suggested_fix: Some("Use subprocess/spawn with array arguments instead of shell string. Never interpolate user input into commands.".to_string()),
                                estimated_effort: Some("45 minutes".to_string()),
                                category: Some("security".to_string()),
                                cwe_id: Some("CWE-78".to_string()),
                                why_it_matters: Some("Attackers could execute arbitrary system commands by injecting shell metacharacters.".to_string()),
                                ..Default::default()
                            });
                        }
                    }
                    // Fallback: Also flag template literals with ${} passed directly to exec-like functions
                    // but ONLY if shell_exec() didn't already match (avoid duplicates)
                    else if line.contains("exec(")
                        || line.contains("execSync(")
                        || line.contains("execAsync(")
                    {
                        if line.contains("`") && line.contains("${") {
                            let (severity, description) = check_taint(
                                "Template literal with variable interpolation passed to exec(). This is a classic command injection pattern."
                            );

                            findings.push(Finding {
                                id: String::new(),
                                detector: "CommandInjectionDetector".to_string(),
                                severity,
                                title: "Command injection via template literal".to_string(),
                                description,
                                affected_files: vec![self.relative_path(path)],
                                line_start: Some(line_num),
                                line_end: Some(line_num),
                                suggested_fix: Some("Use spawn() with array arguments: spawn('cmd', [arg1, arg2]) instead of exec(`cmd ${arg}`)".to_string()),
                                estimated_effort: Some("30 minutes".to_string()),
                                category: Some("security".to_string()),
                                cwe_id: Some("CWE-78".to_string()),
                                why_it_matters: Some("An attacker can inject shell commands by providing input like '; rm -rf /' or '$(malicious_command)'".to_string()),
                                ..Default::default()
                            });
                        }
                        // Also check if it's using a variable we identified as dangerous (built from template literal)
                        else if dangerous_vars.iter().any(|v| {
                            line.contains(&format!("({})", v)) || line.contains(&format!("({},", v))
                        }) {
                            let (severity, description) = check_taint(
                                "Shell execution using a command string that was built with template literal interpolation. User input may flow into the shell command."
                            );

                            findings.push(Finding {
                                id: String::new(),
                                detector: "CommandInjectionDetector".to_string(),
                                severity,
                                title: "Command injection via interpolated variable".to_string(),
                                description,
                                affected_files: vec![self.relative_path(path)],
                                line_start: Some(line_num),
                                line_end: Some(line_num),
                                suggested_fix: Some("Use spawn() with array arguments instead of building command strings. Never interpolate user input.".to_string()),
                                estimated_effort: Some("45 minutes".to_string()),
                                category: Some("security".to_string()),
                                cwe_id: Some("CWE-78".to_string()),
                                why_it_matters: Some("The command variable was built using ${} interpolation, allowing shell injection.".to_string()),
                                ..Default::default()
                            });
                        }
                    }

                    // Check for direct exec(req.body.command) pattern in JavaScript
                    // This catches exec(userInput) without template literals
                    if js_exec_direct().is_match(line) {
                        let has_direct_user_input = line.contains("req.body")
                            || line.contains("req.query")
                            || line.contains("req.params")
                            || line.contains("request.body")
                            || line.contains("request.query")
                            || line.contains("request.params");

                        if has_direct_user_input {
                            let (severity, description) = check_taint(
                                "User-controlled input (req.body/query/params) passed directly to shell execution function. This allows arbitrary command execution."
                            );

                            findings.push(Finding {
                                id: String::new(),
                                detector: "CommandInjectionDetector".to_string(),
                                severity,
                                title: "Command injection via direct user input".to_string(),
                                description,
                                affected_files: vec![self.relative_path(path)],
                                line_start: Some(line_num),
                                line_end: Some(line_num),
                                suggested_fix: Some("Never pass user input directly to exec(). Use a whitelist of allowed commands, or use spawn() with a fixed command and user input only as arguments.".to_string()),
                                estimated_effort: Some("1 hour".to_string()),
                                category: Some("security".to_string()),
                                cwe_id: Some("CWE-78".to_string()),
                                why_it_matters: Some("An attacker can execute ANY system command by sending malicious input like 'rm -rf /' or 'cat /etc/passwd'.".to_string()),
                                ..Default::default()
                            });
                        }
                    }

                    // Check for Go exec.Command with user input
                    if go_exec().is_match(line) {
                        let has_user_input = line.contains("r.")
                            || line.contains("req.")
                            || line.contains("request.")
                            || line.contains("c.")
                            || line.contains("ctx.")
                            || line.contains("Param")
                            || line.contains("Query")
                            || line.contains("FormValue")
                            || line.contains("PostForm")
                            || line.contains("userInput")
                            || line.contains("input")
                            || line.contains("cmd")
                            || line.contains("command");

                        // Also flag if variable names suggest user input
                        let has_risky_var = line.to_lowercase().contains("userinput")
                            || line.to_lowercase().contains("user_input")
                            || line.to_lowercase().contains("usercmd")
                            || line.to_lowercase().contains("user_cmd");

                        if has_user_input || has_risky_var {
                            let (severity, description) = check_taint(
                                "exec.Command called with potentially user-controlled input. If the command or arguments come from user input, this allows arbitrary command execution."
                            );

                            findings.push(Finding {
                                id: String::new(),
                                detector: "CommandInjectionDetector".to_string(),
                                severity,
                                title: "Potential command injection in Go exec.Command".to_string(),
                                description,
                                affected_files: vec![self.relative_path(path)],
                                line_start: Some(line_num),
                                line_end: Some(line_num),
                                suggested_fix: Some("Validate user input against a whitelist of allowed commands. Never pass raw user input to exec.Command. Use filepath.Clean for paths.".to_string()),
                                estimated_effort: Some("1 hour".to_string()),
                                category: Some("security".to_string()),
                                cwe_id: Some("CWE-78".to_string()),
                                why_it_matters: Some("Go's exec.Command runs system commands. If user input controls the command or arguments, attackers can execute arbitrary commands.".to_string()),
                                ..Default::default()
                            });
                        }
                    }
                }
            }
        }

        // Filter out Low severity (sanitized) findings
        findings.retain(|f| f.severity != Severity::Low);

        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detectors::base::Detector;
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_os_system_with_user_input() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("vuln.py");
        std::fs::write(
            &file,
            r#"import os

def run_command(user_input):
    os.system("ls " + user_input)
"#,
        )
        .unwrap();

        let store = GraphStore::in_memory();
        let detector = CommandInjectionDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(
            !findings.is_empty(),
            "Should detect os.system with user input concatenation"
        );
        assert!(
            findings.iter().any(|f| f.title.contains("command injection")),
            "Finding should mention command injection. Titles: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
        assert!(
            findings.iter().any(|f| f.cwe_id.as_deref() == Some("CWE-78")),
            "Finding should have CWE-78"
        );
    }

    #[test]
    fn test_no_findings_for_safe_subprocess() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("safe.py");
        std::fs::write(
            &file,
            r#"import subprocess

def list_files():
    result = subprocess.run(["ls", "-la"], capture_output=True)
    return result.stdout
"#,
        )
        .unwrap();

        let store = GraphStore::in_memory();
        let detector = CommandInjectionDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(
            findings.is_empty(),
            "Safe subprocess usage with list args should have no findings, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
