//! Command Injection Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use uuid::Uuid;

static SHELL_EXEC: OnceLock<Regex> = OnceLock::new();

fn shell_exec() -> &'static Regex {
    // Be specific about shell execution patterns - avoid matching RegExp.exec(), String.prototype.exec(), etc.
    // Pattern must match actual shell execution APIs:
    // - Python: os.system, os.popen, subprocess.*
    // - Node.js: child_process.exec, child_process.spawn, execSync, require('child_process')
    // - PHP: shell_exec, system, popen, exec (standalone function)
    // - Ruby: system, exec, backticks
    SHELL_EXEC.get_or_init(|| Regex::new(r#"(?i)(os\.system|os\.popen|subprocess\.(call|run|Popen)|child_process\.(exec|spawn|fork)|execSync|spawnSync|require\(['"]child_process['"]\)|shell_exec|proc_open)"#).unwrap())
}

pub struct CommandInjectionDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl CommandInjectionDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
    
    /// Convert absolute path to relative path for consistent output
    fn relative_path(&self, path: &Path) -> PathBuf {
        path.strip_prefix(&self.repository_path)
            .unwrap_or(path)
            .to_path_buf()
    }
}

impl Detector for CommandInjectionDetector {
    fn name(&self) -> &'static str { "command-injection" }
    fn description(&self) -> &'static str { "Detects command injection vulnerabilities" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"rb"|"php"|"java"|"go"|"sh") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();
                
                // First pass: find template literals with interpolation stored in variables
                // e.g., const cmd = `echo ${userInput}`;
                let mut dangerous_vars: Vec<String> = vec![];
                for line in &lines {
                    // Match: const/let/var VARNAME = `...${...}...`
                    if (line.contains("const ") || line.contains("let ") || line.contains("var "))
                        && line.contains("`") && line.contains("${") {
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
                
                for (i, line) in lines.iter().enumerate() {
                    // Check for direct shell execution with template literal
                    if shell_exec().is_match(line) {
                        // Check for user input sources
                        let has_user_input = line.contains("req.") || line.contains("request.") ||
                            line.contains("params.") || line.contains("params[") ||
                            line.contains("query.") || line.contains("body.") ||
                            line.contains("input") || line.contains("argv") || line.contains("args");
                        
                        // Check for string interpolation ON THIS LINE
                        let has_interpolation = line.contains("f\"") || line.contains("${") || 
                            line.contains("+ ") || line.contains(".format(");
                        
                        // Check for template literal with interpolation ON THIS LINE
                        let has_template_interpolation = line.contains("`") && line.contains("${");
                        
                        // Check if exec is using a dangerous variable we identified earlier
                        let uses_dangerous_var = dangerous_vars.iter().any(|v| line.contains(v));
                        
                        // Python subprocess shell=True is always dangerous
                        let has_shell_true = line.contains("shell=True") || line.contains("shell: true");
                        
                        // HIGH RISK conditions:
                        // 1. shell=True (Python) - always dangerous
                        // 2. exec with user input AND interpolation on same line
                        // 3. exec with template literal + ${} on same line (obvious injection)
                        // 4. exec using a variable that was built from template with ${}
                        let is_risky = has_shell_true 
                            || (has_user_input && has_interpolation)
                            || has_template_interpolation
                            || uses_dangerous_var;
                        
                        if is_risky {
                            let desc = if has_template_interpolation {
                                "Template literal with interpolation passed directly to shell execution. Variables are inserted unsanitized."
                            } else if uses_dangerous_var {
                                "Shell execution using a command string built from template literal. User input may flow into the command."
                            } else if has_shell_true {
                                "subprocess with shell=True allows shell injection through any unsanitized input."
                            } else {
                                "Shell command execution with potential user input."
                            };
                            
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "CommandInjectionDetector".to_string(),
                                severity: Severity::Critical,
                                title: "Potential command injection".to_string(),
                                description: desc.to_string(),
                                affected_files: vec![self.relative_path(path)],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some("Use subprocess/spawn with array arguments instead of shell string. Never interpolate user input into commands.".to_string()),
                                estimated_effort: Some("45 minutes".to_string()),
                                category: Some("security".to_string()),
                                cwe_id: Some("CWE-78".to_string()),
                                why_it_matters: Some("Attackers could execute arbitrary system commands by injecting shell metacharacters.".to_string()),
                            });
                        }
                    }
                    
                    // Also flag template literals with ${} passed directly to exec-like functions
                    // even if they're not in our strict shell_exec pattern
                    // e.g., exec(`command ${var}`)
                    if line.contains("exec(") || line.contains("execSync(") || line.contains("execAsync(") {
                        if line.contains("`") && line.contains("${") {
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "CommandInjectionDetector".to_string(),
                                severity: Severity::Critical,
                                title: "Command injection via template literal".to_string(),
                                description: "Template literal with variable interpolation passed to exec(). This is a classic command injection pattern.".to_string(),
                                affected_files: vec![self.relative_path(path)],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some("Use spawn() with array arguments: spawn('cmd', [arg1, arg2]) instead of exec(`cmd ${arg}`)".to_string()),
                                estimated_effort: Some("30 minutes".to_string()),
                                category: Some("security".to_string()),
                                cwe_id: Some("CWE-78".to_string()),
                                why_it_matters: Some("An attacker can inject shell commands by providing input like '; rm -rf /' or '$(malicious_command)'".to_string()),
                            });
                        }
                    }
                }
            }
        }
        Ok(findings)
    }
}
