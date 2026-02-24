//! MCP server command handler

use anyhow::Result;
use std::path::Path;

/// Run the MCP server with stdio or HTTP transport
pub fn run(repo_path: &Path, force_local: bool, http_port: Option<u16>) -> Result<()> {
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?
        .block_on(crate::mcp::run_mcp_server(
            repo_path.to_path_buf(),
            force_local,
            http_port,
        ))
}
