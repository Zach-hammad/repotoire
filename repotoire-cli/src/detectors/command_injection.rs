//! Command Injection Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
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
                for (i, line) in content.lines().enumerate() {
                    if shell_exec().is_match(line) {
                        // Check for user input sources
                        let has_user_input = line.contains("req.") || line.contains("request.") ||
                            line.contains("params.") || line.contains("params[") ||
                            line.contains("query.") || line.contains("body.") ||
                            line.contains("input") || line.contains("argv") || line.contains("args");
                        
                        // Check for string interpolation/concatenation (command building)
                        let has_interpolation = line.contains("f\"") || line.contains("${") || 
                            line.contains("`") || line.contains("+ ") || line.contains(".format(");
                        
                        // Python subprocess shell=True is always dangerous
                        let has_shell_true = line.contains("shell=True") || line.contains("shell: true");
                        
                        // Combination of shell exec + interpolation is high risk even without explicit user input
                        // (the interpolated value might come from user input elsewhere)
                        let is_risky = has_shell_true || (has_user_input && has_interpolation) || 
                            (shell_exec().is_match(line) && line.contains("`") && line.contains("${"));
                        
                        if is_risky {
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "CommandInjectionDetector".to_string(),
                                severity: Severity::Critical,
                                title: "Potential command injection".to_string(),
                                description: "Shell command execution with potential user input.".to_string(),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some("Use subprocess with list args, avoid shell=True, sanitize input.".to_string()),
                                estimated_effort: Some("45 minutes".to_string()),
                                category: Some("security".to_string()),
                                cwe_id: Some("CWE-78".to_string()),
                                why_it_matters: Some("Attackers could execute arbitrary commands.".to_string()),
                            });
                        }
                    }
                }
            }
        }
        Ok(findings)
    }
}
