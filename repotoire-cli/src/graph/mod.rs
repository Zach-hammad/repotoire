//! Graph database for code analysis
//!
//! Pure Rust implementation using petgraph + redb.
//! No C++ dependencies - builds everywhere!

pub mod compact_builder;
pub mod compact_store;
pub mod interner;
pub mod mmap_store;
pub mod store;
pub mod store_models;
pub mod store_query;
pub mod streaming_builder;
pub mod traits;

pub use store::GraphStore;
pub use store_models::{CodeEdge, CodeNode, EdgeKind, NodeKind};

pub use traits::GraphQuery;

// Legacy Kuzu modules (kept for reference but not used)
// mod client;
// pub mod queries;
// pub mod schema;
