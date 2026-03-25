//! Taint Analysis for Security Vulnerability Detection
//!
//! This module provides graph-based data flow analysis to trace potentially malicious
//! data from untrusted sources (user input) to dangerous sinks (SQL queries, shell commands, etc.).
//!
//! # Architecture
//!
//! ```text
//! ┌─────────────────────────────────────────────────────────────┐
//! │                     TaintAnalyzer                           │
//! │  - Defines sources (user input entry points)                │
//! │  - Defines sinks (dangerous operations)                     │
//! │  - Defines sanitizers (functions that neutralize taint)     │
//! └─────────────────────────────────────────────────────────────┘
//!                              │
//!                              ▼
//! ┌─────────────────────────────────────────────────────────────┐
//! │                    trace_taint()                            │
//! │  - BFS through call graph from source functions             │
//! │  - Track path through function calls                        │
//! │  - Identify when tainted data reaches a sink                │
//! └─────────────────────────────────────────────────────────────┘
//!                              │
//!                              ▼
//! ┌─────────────────────────────────────────────────────────────┐
//! │                     TaintPath                               │
//! │  - Source function (where taint originates)                 │
//! │  - Sink function (dangerous operation)                      │
//! │  - Call chain (functions between source and sink)           │
//! │  - Sanitized flag (whether sanitizer was in path)           │
//! └─────────────────────────────────────────────────────────────┘
//! ```
//!
//! # Usage
//!
//! ```ignore
//! use repotoire_cli::detectors::taint::{TaintAnalyzer, TaintCategory};
//!
//! let analyzer = TaintAnalyzer::new();
//! let paths = analyzer.trace_taint(&graph, TaintCategory::SqlInjection);
//!
//! for path in paths {
//!     if !path.is_sanitized {
//!         // Critical: unsanitized taint flow to SQL sink
//!     }
//! }
//! ```

mod types;
pub use types::*;

mod analysis;
pub use analysis::*;

pub mod centralized;
pub use centralized::CentralizedTaintResults;

mod heuristic;

#[cfg(test)]
mod tests;
