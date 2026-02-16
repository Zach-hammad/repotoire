//! Graph database for code analysis
//!
//! Pure Rust implementation using petgraph + sled.
//! No C++ dependencies - builds everywhere!

pub mod store;
pub mod streaming_builder;
pub mod interner;
pub mod compact_store;
pub mod compact_builder;
pub mod traits;

pub use store::{CodeEdge, CodeNode, EdgeKind, GraphStore, NodeKind};


pub use traits::GraphQuery;

// Legacy Kuzu modules (kept for reference but not used)
// mod client;
// pub mod queries;
// pub mod schema;
