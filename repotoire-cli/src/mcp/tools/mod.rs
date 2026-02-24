//! MCP Tool definitions and handler functions
//!
//! Each sub-module contains the handler logic for a group of tools.
//! Functions accept HandlerState and return Result<Value, anyhow::Error>.

pub mod ai;
pub mod analysis;
pub mod evolution;
pub mod files;
pub mod graph;
