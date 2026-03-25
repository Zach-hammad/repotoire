//! Graph database for code analysis
//!
//! Pure Rust implementation using petgraph + redb.
//! No C++ dependencies - builds everywhere!

pub mod interner;
pub mod store;
pub mod store_models;
pub mod store_query;
pub mod traits;

// ── Builder/Frozen architecture ──
pub mod indexes;
pub mod builder;
pub mod frozen;
pub mod compat;
pub mod metrics_cache;
pub mod primitives;
pub mod persistence;

#[allow(unused_imports)] // Public API for downstream use
pub use interner::{StrKey, StringInterner};
pub use store::GraphStore;
pub use store_models::{CodeEdge, CodeNode, EdgeKind, NodeKind};
pub use store_models::{ExtraProps, FLAG_IS_ASYNC, FLAG_IS_EXPORTED, FLAG_IS_PUBLIC, FLAG_IS_METHOD, FLAG_ADDRESS_TAKEN, FLAG_HAS_DECORATORS};

pub use traits::GraphQuery;
pub use traits::GraphQueryExt;

pub use builder::GraphBuilder;
pub use frozen::CodeGraph;
pub use indexes::GraphIndexes;
pub use metrics_cache::MetricsCache;
