//! MCP JSON-RPC Server over stdio
//!
//! Implements the Model Context Protocol using JSON-RPC 2.0 over stdin/stdout.

use anyhow::{Context, Result};
use serde::Deserialize;
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
// No async runtime needed — ureq is sync
use tracing::{debug, error, info};

use super::state::HandlerState;
use super::tools::available_tools_full;

/// MCP Server implementation
pub struct McpServer {
    state: HandlerState,
    force_local: bool,
}

impl McpServer {
    pub fn new(repo_path: PathBuf, force_local: bool) -> Self {
        let state = HandlerState::new(repo_path, force_local);
        Self { state, force_local }
    }

    /// Run the server, reading JSON-RPC messages from stdin
    pub fn run(&mut self) -> Result<()> {
        let stdin = std::io::stdin();
        let mut stdout = std::io::stdout();
        let reader = BufReader::new(stdin.lock());

        // Print startup message to stderr (visible to users)
        eprintln!(
            "🎼 Repotoire MCP server started ({})",
            self.state.mode_description()
        );
        eprintln!("   Transport: stdio (JSON-RPC 2.0)");
        eprintln!("   Repository: {}", self.state.repo_path.display());
        if !self.state.is_pro() && !self.state.has_ai() {
            eprintln!(
                "   AI features disabled. Set ANTHROPIC_API_KEY or OPENAI_API_KEY to enable."
            );
        }
        eprintln!();
        eprintln!("   Ready. Waiting for JSON-RPC messages on stdin...");

        info!(
            "Repotoire MCP server started ({} mode)",
            if self.state.is_pro() { "PRO" } else { "FREE" }
        );

        for line in reader.lines() {
            let line = line.context("Failed to read from stdin")?;
            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }
            debug!("Received: {}", trimmed);
            let response = match self.handle_message(trimmed) {
                Ok(Some(resp)) => resp,
                Ok(None) => continue,
                Err(e) => {
                    error!("Error handling message: {}", e);
                    error_response(e)
                }
            };
            let response_str = serde_json::to_string(&response)?;
            debug!("Sending: {}", response_str);
            writeln!(stdout, "{}", response_str)?;
            stdout.flush()?;
        }

        Ok(())
    }

    fn handle_message(&mut self, message: &str) -> Result<Option<Value>> {
        // Input size limit to prevent DoS (#22)
        const MAX_MESSAGE_SIZE: usize = 10 * 1024 * 1024; // 10MB
        if message.len() > MAX_MESSAGE_SIZE {
            anyhow::bail!("Message exceeds maximum size of {} bytes", MAX_MESSAGE_SIZE);
        }

        let request: JsonRpcRequest =
            serde_json::from_str(message).context("Invalid JSON-RPC request")?;

        // Handle based on method
        let result = match request.method.as_str() {
            "initialize" => self.handle_initialize(&request.params),
            "initialized" => return Ok(None), // Notification, no response
            "tools/list" => self.handle_list_tools(&request.params),
            "tools/call" => self.handle_call_tool(&request.params),
            "shutdown" => {
                info!("Shutdown requested");
                Ok(json!(null))
            }
            _ => Err(anyhow::anyhow!("Unknown method: {}", request.method)),
        };

        match result {
            Ok(value) => Ok(Some(json!({
                "jsonrpc": "2.0",
                "id": request.id,
                "result": value
            }))),
            Err(e) => Ok(Some(json!({
                "jsonrpc": "2.0",
                "id": request.id,
                "error": {
                    "code": -32603,
                    "message": e.to_string()
                }
            }))),
        }
    }

    fn handle_initialize(&self, _params: &Option<Value>) -> Result<Value> {
        Ok(json!({
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {}
            },
            "serverInfo": {
                "name": "repotoire",
                "version": env!("CARGO_PKG_VERSION")
            }
        }))
    }

    fn handle_list_tools(&self, _params: &Option<Value>) -> Result<Value> {
        let is_pro = self.state.is_pro() && !self.force_local;
        let has_ai = self.state.has_ai();
        let tools = available_tools_full(is_pro, has_ai);
        Ok(json!({
            "tools": tools
        }))
    }

    fn handle_call_tool(&mut self, params: &Option<Value>) -> Result<Value> {
        let params = params.as_ref().context("Missing params for tools/call")?;

        let name = params
            .get("name")
            .and_then(|v| v.as_str())
            .context("Missing tool name")?;

        let arguments = params.get("arguments").cloned().unwrap_or(json!({}));

        // Limit argument size (#22)
        let args_str = arguments.to_string();
        if args_str.len() > 1_000_000 {
            anyhow::bail!("Tool arguments exceed 1MB limit");
        }

        debug!("Calling tool: {} with args: {}", name, arguments);

        let result = match name {
            // FREE tools
            "analyze" => super::handlers::handle_analyze(&mut self.state, &arguments),
            "query_graph" => super::handlers::handle_query_graph(&mut self.state, &arguments),
            "get_findings" => super::handlers::handle_get_findings(&mut self.state, &arguments),
            "get_file" => super::handlers::handle_get_file(&self.state, &arguments),
            "get_architecture" => {
                super::handlers::handle_get_architecture(&mut self.state, &arguments)
            }
            "list_detectors" => super::handlers::handle_list_detectors(&self.state, &arguments),
            "get_hotspots" => super::handlers::handle_get_hotspots(&mut self.state, &arguments),

            // PRO tools (async)
            "search_code" | "ask" | "generate_fix" => Ok(self.handle_async_tool(name, &arguments)?),

            _ => return Err(anyhow::anyhow!("Unknown tool: {}", name)),
        };

        match result {
            Ok(value) => Ok(json!({
                "content": [{
                    "type": "text",
                    "text": serde_json::to_string_pretty(&value)?
                }]
            })),
            Err(e) => Ok(json!({
                "content": [{
                    "type": "text",
                    "text": json!({"error": e.to_string()}).to_string()
                }],
                "isError": true
            })),
        }
    }

    fn handle_async_tool(&self, name: &str, arguments: &Value) -> Result<Value> {
        // All handlers are now sync (ureq)
        match name {
            "search_code" => super::handlers::handle_search_code(&self.state, arguments),
            "ask" => super::handlers::handle_ask(&self.state, arguments),
            "generate_fix" => super::handlers::handle_generate_fix(&self.state, arguments),
            _ => Err(anyhow::anyhow!("Unknown tool: {}", name)),
        }
    }
}

/// JSON-RPC 2.0 Request
#[derive(Debug, Deserialize)]
struct JsonRpcRequest {
    #[allow(dead_code)] // Part of JSON-RPC spec
    jsonrpc: String,
    id: Option<Value>,
    method: String,
    params: Option<Value>,
}

/// Run the MCP server
fn error_response(e: anyhow::Error) -> serde_json::Value {
    serde_json::json!({
        "jsonrpc": "2.0",
        "id": null,
        "error": { "code": -32603, "message": e.to_string() }
    })
}

pub fn run_server(repo_path: PathBuf, force_local: bool) -> Result<()> {
    let mut server = McpServer::new(repo_path, force_local);
    server.run()
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_initialize() {
        let dir = tempdir().unwrap();
        let server = McpServer::new(dir.path().to_path_buf(), false);
        let result = server.handle_initialize(&None).unwrap();

        assert!(result.get("protocolVersion").is_some());
        assert!(result.get("serverInfo").is_some());
    }

    #[test]
    fn test_list_tools() {
        let dir = tempdir().unwrap();
        let server = McpServer::new(dir.path().to_path_buf(), false);
        let result = server.handle_list_tools(&None).unwrap();

        let tools = result.get("tools").unwrap().as_array().unwrap();
        assert!(!tools.is_empty());
    }
}
