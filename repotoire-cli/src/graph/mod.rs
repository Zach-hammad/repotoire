//! Graph database for code analysis
//!
//! Pure Rust implementation using petgraph + redb.
//! No C++ dependencies - builds everywhere!

pub mod interner;
pub mod store;
pub mod store_models;
pub mod store_query;
pub mod traits;

pub use store::GraphStore;
pub use store_models::{CodeEdge, CodeNode, EdgeKind, NodeKind};

pub use traits::GraphQuery;
