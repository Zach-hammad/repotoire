//! MCP (Model Context Protocol) Server
//!
//! This module implements an MCP server for Repotoire, allowing AI assistants
//! like Claude to interact with code analysis capabilities via JSON-RPC over stdio.
//!
//! # Usage
//!
//! ```bash
//! # Start MCP server
//! repotoire serve
//!
//! # Force local-only mode (no API calls)
//! repotoire serve --local
//! ```
//!
//! # Claude Desktop Configuration
//!
//! ```json
//! {
//!   "mcpServers": {
//!     "repotoire": {
//!       "command": "repotoire",
//!       "args": ["serve"]
//!     }
//!   }
//! }
//! ```
//!
//! # Tool Tiers
//!
//! ## FREE (Local CLI)
//! - `analyze` - Run code analysis with detectors
//! - `query_graph` - Execute Cypher queries on local graph
//! - `get_findings` - List findings from analysis
//! - `get_file` - Read file content
//! - `get_architecture` - Get codebase structure overview
//! - `list_detectors` - List available detectors
//! - `get_hotspots` - Get files with most issues
//!
//! ## PRO (Cloud API - requires REPOTOIRE_API_KEY)
//! - `search_code` - Semantic code search with embeddings
//! - `ask` - RAG-powered Q&A about the codebase
//! - `generate_fix` - Generate AI-powered fix for a finding

mod handlers;
pub mod params;
mod server;
mod tools;

pub use server::run_server;
