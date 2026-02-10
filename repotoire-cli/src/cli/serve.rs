//! MCP server command handler

use anyhow::Result;
use std::path::Path;

/// Run the MCP server
pub fn run(repo_path: &Path, force_local: bool) -> Result<()> {
    crate::mcp::run_server(repo_path.to_path_buf(), force_local)
}
