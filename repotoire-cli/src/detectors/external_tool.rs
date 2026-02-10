//! Base utilities for external tool-based detectors
//!
//! This module provides common functionality for detectors that wrap external tools
//! (bandit, ruff, mypy, pylint, etc.) to reduce code duplication.
//!
//! # Architecture
//!
//! External tool detectors follow a common pattern:
//! 1. Run external tool as subprocess with `std::process::Command`
//! 2. Parse JSON/text output
//! 3. Enrich findings with graph context
//! 4. Return standardized `Finding` objects

use anyhow::{Context, Result};
use serde_json::Value as JsonValue;
use std::collections::HashMap;
use std::io::{BufRead, BufReader};
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::OnceLock;
use std::time::Duration;
use tracing::{debug, warn};

/// Result from running an external tool
#[derive(Debug, Clone)]
pub struct ExternalToolResult {
    /// Whether the tool completed (may still have findings)
    pub success: bool,
    /// Standard output
    pub stdout: String,
    /// Standard error
    pub stderr: String,
    /// Process exit code
    pub return_code: Option<i32>,
    /// Whether the tool timed out
    pub timed_out: bool,
    /// Error message if failed
    pub error: Option<String>,
}

impl ExternalToolResult {
    /// Create a successful result
    pub fn success(stdout: String, stderr: String, return_code: i32) -> Self {
        Self {
            success: true,
            stdout,
            stderr,
            return_code: Some(return_code),
            timed_out: false,
            error: None,
        }
    }

    /// Create a failed result
    pub fn failure(error: String) -> Self {
        Self {
            success: false,
            stdout: String::new(),
            stderr: String::new(),
            return_code: None,
            timed_out: false,
            error: Some(error),
        }
    }

    /// Create a timeout result
    pub fn timeout(tool_name: &str, timeout_secs: u64) -> Self {
        Self {
            success: false,
            stdout: String::new(),
            stderr: String::new(),
            return_code: None,
            timed_out: true,
            error: Some(format!("{} timed out after {}s", tool_name, timeout_secs)),
        }
    }

    /// Parse stdout as JSON
    pub fn json_output(&self) -> Option<JsonValue> {
        if self.stdout.is_empty() {
            return None;
        }
        serde_json::from_str(&self.stdout).ok()
    }

    /// Parse stdout as JSON array
    pub fn json_array(&self) -> Option<Vec<JsonValue>> {
        self.json_output().and_then(|v| v.as_array().cloned())
    }
}

/// Cached JavaScript runtime detection
static JS_RUNTIME: OnceLock<JsRuntime> = OnceLock::new();

/// JavaScript runtime type
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum JsRuntime {
    Bun,
    Npm,
    None,
}

impl JsRuntime {
    /// Get the package executor command (bunx or npx)
    pub fn exec_cmd(&self) -> &'static str {
        match self {
            JsRuntime::Bun => "bunx",
            JsRuntime::Npm => "npx",
            JsRuntime::None => "npx", // Fallback, will fail if not installed
        }
    }
}

/// Detect available JavaScript runtime (bun preferred for performance)
pub fn get_js_runtime() -> JsRuntime {
    *JS_RUNTIME.get_or_init(|| {
        // Check for bun first (faster)
        if Command::new("bun")
            .arg("--version")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
        {
            debug!("Using Bun runtime for JavaScript tools");
            return JsRuntime::Bun;
        }

        // Check for npm
        if Command::new("npm")
            .arg("--version")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
        {
            debug!("Using npm runtime for JavaScript tools");
            return JsRuntime::Npm;
        }

        warn!("No JavaScript runtime (bun or npm) found. JS tool commands may fail.");
        JsRuntime::None
    })
}

/// Get command to execute a JS package binary
pub fn get_js_exec_command(package: &str) -> Vec<String> {
    let runtime = get_js_runtime();
    vec![runtime.exec_cmd().to_string(), package.to_string()]
}

/// Run an external tool with standard error handling
///
/// # Arguments
/// * `cmd` - Command and arguments to run
/// * `tool_name` - Human-readable tool name for error messages
/// * `timeout_secs` - Timeout in seconds (0 = no timeout)
/// * `cwd` - Working directory for the tool
/// * `env` - Additional environment variables
///
/// # Returns
/// `ExternalToolResult` with stdout, stderr, and status
pub fn run_external_tool(
    cmd: &[String],
    tool_name: &str,
    timeout_secs: u64,
    cwd: Option<&Path>,
    env: Option<&HashMap<String, String>>,
) -> ExternalToolResult {
    if cmd.is_empty() {
        return ExternalToolResult::failure("Empty command".to_string());
    }

    let program = &cmd[0];
    let args = &cmd[1..];

    debug!("Running {}: {} {:?}", tool_name, program, args);

    let mut command = Command::new(program);
    command.args(args);

    if let Some(dir) = cwd {
        command.current_dir(dir);
    }

    // Merge custom env with current environment
    if let Some(extra_env) = env {
        for (key, value) in extra_env {
            command.env(key, value);
        }
    }

    command.stdout(Stdio::piped());
    command.stderr(Stdio::piped());

    // Spawn the process
    let child = match command.spawn() {
        Ok(child) => child,
        Err(e) => {
            if e.kind() == std::io::ErrorKind::NotFound {
                return ExternalToolResult::failure(format!(
                    "{} not found. Please install it first.",
                    tool_name
                ));
            }
            return ExternalToolResult::failure(format!("Failed to run {}: {}", tool_name, e));
        }
    };

    // Wait with timeout
    if timeout_secs > 0 {
        run_with_timeout(child, tool_name, timeout_secs)
    } else {
        run_without_timeout(child, tool_name)
    }
}

/// Run a JavaScript tool using the best available runtime
pub fn run_js_tool(
    package: &str,
    args: &[String],
    tool_name: &str,
    timeout_secs: u64,
    cwd: Option<&Path>,
    env: Option<&HashMap<String, String>>,
) -> ExternalToolResult {
    let mut cmd = get_js_exec_command(package);
    cmd.extend(args.iter().cloned());
    run_external_tool(&cmd, tool_name, timeout_secs, cwd, env)
}

/// Run process without timeout
fn run_without_timeout(
    child: std::process::Child,
    tool_name: &str,
) -> ExternalToolResult {
    let output = match child.wait_with_output() {
        Ok(output) => output,
        Err(e) => {
            return ExternalToolResult::failure(format!("Failed to wait for {}: {}", tool_name, e));
        }
    };

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    let return_code = output.status.code().unwrap_or(-1);

    ExternalToolResult::success(stdout, stderr, return_code)
}

/// Run process with timeout (uses a separate thread for waiting)
fn run_with_timeout(
    mut child: std::process::Child,
    tool_name: &str,
    timeout_secs: u64,
) -> ExternalToolResult {
    use std::thread;
    use std::time::Instant;

    let start = Instant::now();
    let timeout = Duration::from_secs(timeout_secs);

    // Poll for completion with small sleep intervals
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                // Process completed
                let stdout = child
                    .stdout
                    .take()
                    .map(|s| {
                        let reader = BufReader::new(s);
                        reader.lines().filter_map(|l| l.ok()).collect::<Vec<_>>().join("\n")
                    })
                    .unwrap_or_default();

                let stderr = child
                    .stderr
                    .take()
                    .map(|s| {
                        let reader = BufReader::new(s);
                        reader.lines().filter_map(|l| l.ok()).collect::<Vec<_>>().join("\n")
                    })
                    .unwrap_or_default();

                return ExternalToolResult::success(stdout, stderr, status.code().unwrap_or(-1));
            }
            Ok(None) => {
                // Still running
                if start.elapsed() > timeout {
                    // Kill the process
                    let _ = child.kill();
                    warn!("{} timed out after {}s", tool_name, timeout_secs);
                    return ExternalToolResult::timeout(tool_name, timeout_secs);
                }
                thread::sleep(Duration::from_millis(100));
            }
            Err(e) => {
                return ExternalToolResult::failure(format!("Failed to wait for {}: {}", tool_name, e));
            }
        }
    }
}

/// Graph context for a file/line from the code graph
#[derive(Debug, Clone, Default)]
pub struct GraphContext {
    pub file_loc: Option<i64>,
    pub language: Option<String>,
    pub affected_nodes: Vec<String>,
    pub complexities: Vec<i64>,
}

impl GraphContext {
    /// Get max complexity from the list
    pub fn max_complexity(&self) -> i64 {
        self.complexities.iter().copied().max().unwrap_or(0)
    }
}

/// Get graph context for a file/line from the knowledge graph
///
/// # Arguments
/// * `graph` - Graph database client
/// * `file_path` - Relative file path
/// * `line` - Optional line number to find containing entity
pub fn get_graph_context(
    graph: &crate::graph::GraphStore,
    file_path: &str,
    _line: Option<u32>,
) -> GraphContext {
    // Get file info from GraphStore
    if let Some(file_node) = graph.get_node(file_path) {
        GraphContext {
            file_loc: file_node.get_i64("loc"),
            language: file_node.language.clone(),
            affected_nodes: Vec::new(),
            complexities: Vec::new(),
        }
    } else {
        GraphContext::default()
    }
}

/// Batch get graph context for multiple files in a single query
pub fn batch_get_graph_context(
    graph: &crate::graph::GraphStore,
    file_paths: &[String],
) -> HashMap<String, GraphContext> {
    let mut contexts = HashMap::new();
    
    for path in file_paths {
        if let Some(file_node) = graph.get_node(path) {
            contexts.insert(
                path.clone(),
                GraphContext {
                    file_loc: file_node.get_i64("loc"),
                    language: file_node.language.clone(),
                    affected_nodes: Vec::new(),
                    complexities: Vec::new(),
                },
            );
        }
    }
    
    contexts
}

/// Estimate fix effort based on severity
pub fn estimate_fix_effort(severity: &str) -> &'static str {
    match severity.to_lowercase().as_str() {
        "critical" => "30 minutes",
        "high" => "15 minutes",
        "medium" => "10 minutes",
        "low" => "5 minutes",
        _ => "10 minutes",
    }
}

/// Check if a tool is installed
pub fn is_tool_installed(tool: &str) -> bool {
    Command::new(tool)
        .arg("--version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

/// Check if a Python tool is installed (via pip)
pub fn is_python_tool_installed(tool: &str) -> bool {
    // Try running the tool directly first
    if is_tool_installed(tool) {
        return true;
    }

    // Try via python -m
    Command::new("python")
        .args(["-m", tool, "--version"])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_external_tool_result() {
        let result = ExternalToolResult::success("output".into(), "".into(), 0);
        assert!(result.success);
        assert_eq!(result.stdout, "output");

        let result = ExternalToolResult::failure("error".into());
        assert!(!result.success);
        assert_eq!(result.error, Some("error".into()));

        let result = ExternalToolResult::timeout("test", 60);
        assert!(result.timed_out);
    }

    #[test]
    fn test_json_parsing() {
        let result = ExternalToolResult::success(r#"{"key": "value"}"#.into(), "".into(), 0);
        let json = result.json_output().unwrap();
        assert_eq!(json["key"], "value");

        let result = ExternalToolResult::success(r#"[1, 2, 3]"#.into(), "".into(), 0);
        let arr = result.json_array().unwrap();
        assert_eq!(arr.len(), 3);
    }

    #[test]
    fn test_js_runtime_detection() {
        // Just ensure it doesn't panic
        let runtime = get_js_runtime();
        let _ = runtime.exec_cmd();
    }
}
