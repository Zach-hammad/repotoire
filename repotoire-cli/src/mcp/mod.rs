//! MCP (Model Context Protocol) Server
//!
//! Implements MCP 2025-06-18 via rmcp SDK with stdio and Streamable HTTP transports.
//!
//! # Usage
//!
//! ```bash
//! # Start MCP server (stdio, default)
//! repotoire serve
//!
//! # Start MCP server with Streamable HTTP transport
//! repotoire serve --http-port 8080
//!
//! # Force local-only mode (no API calls)
//! repotoire serve --local
//! ```
//!
//! # Tool Tiers
//!
//! ## FREE (Local CLI)
//! - `repotoire_analyze` - Run code analysis with detectors
//! - `repotoire_query_graph` - Query code knowledge graph
//! - `repotoire_trace_dependencies` - Multi-hop graph traversal
//! - `repotoire_analyze_impact` - Change impact analysis
//! - `repotoire_get_findings` - List findings from analysis
//! - `repotoire_get_file` - Read file content
//! - `repotoire_get_architecture` - Get codebase structure overview
//! - `repotoire_list_detectors` - List available detectors
//! - `repotoire_get_hotspots` - Get files with most issues
//! - `repotoire_query_evolution` - Query code evolution and git history
//!
//! ## PRO / BYOK (requires API key)
//! - `repotoire_search_code` - Semantic code search with embeddings
//! - `repotoire_ask` - RAG-powered Q&A about the codebase
//! - `repotoire_generate_fix` - Generate AI-powered fix for a finding

pub mod params;
pub mod rmcp_server;
pub mod state;
pub mod tools;
pub mod transport;

pub use rmcp_server::RepotoireServer;
pub use state::HandlerState;

use std::path::PathBuf;

use anyhow::Result;
use rmcp::ServiceExt;

/// Run MCP server on stdio (default) or HTTP
pub async fn run_mcp_server(
    repo_path: PathBuf,
    force_local: bool,
    http_port: Option<u16>,
) -> Result<()> {
    eprintln!("repotoire MCP server starting...");
    eprintln!("   Repository: {}", repo_path.display());

    if let Some(port) = http_port {
        eprintln!("   Transport: Streamable HTTP on port {}", port);
        transport::serve_http(repo_path, force_local, port).await?;
    } else {
        eprintln!("   Transport: stdio (JSON-RPC 2.0)");
        let state = HandlerState::new(repo_path, force_local);
        if !state.is_pro() && !state.has_ai() {
            eprintln!(
                "   AI features disabled. Set ANTHROPIC_API_KEY or OPENAI_API_KEY to enable."
            );
        }
        eprintln!("   Ready. Waiting for MCP messages on stdin...");
        let service = RepotoireServer::new(state)
            .serve(rmcp::transport::stdio())
            .await
            .inspect_err(|e| tracing::error!("MCP serve error: {:?}", e))?;
        service.waiting().await?;
    }
    Ok(())
}

