//! Graph database for code analysis
//!
//! Pure Rust implementation using petgraph.
//! No C++ dependencies - builds everywhere!

pub mod interner;
pub mod node_index;
pub mod store_models;
pub mod traits;

// ── Builder/Frozen architecture ──
pub mod builder;
pub mod compat;
pub mod frozen;
pub mod indexes;
pub mod metrics_cache;
pub mod persistence;
pub mod primitives;

#[allow(unused_imports)] // Public API for downstream use
pub use interner::{StrKey, StringInterner};
pub use store_models::{CodeEdge, CodeNode, EdgeKind, NodeKind};
pub use store_models::{
    ExtraProps, FLAG_ADDRESS_TAKEN, FLAG_HAS_DECORATORS, FLAG_IS_ASYNC, FLAG_IS_EXPORTED,
    FLAG_IS_METHOD, FLAG_IS_PUBLIC,
};

pub use traits::GraphQuery;
pub use traits::GraphQueryExt;

pub use builder::GraphBuilder;
pub use frozen::CodeGraph;
pub use indexes::GraphIndexes;
pub use metrics_cache::MetricsCache;
