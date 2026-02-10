//! Graph database for code analysis
//!
//! Pure Rust implementation using petgraph + sled.
//! No C++ dependencies - builds everywhere!

pub mod store;

pub use store::{CodeEdge, CodeNode, EdgeKind, GraphStore, NodeKind};

// Legacy Kuzu modules (kept for reference but not used)
// mod client;
// pub mod queries;
// pub mod schema;
