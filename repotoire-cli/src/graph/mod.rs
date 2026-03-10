//! Graph database for code analysis
//!
//! Pure Rust implementation using petgraph + redb.
//! No C++ dependencies - builds everywhere!

pub mod interner;
pub mod store;
pub mod store_models;
pub mod store_query;
pub mod traits;
pub mod cached;

#[allow(unused_imports)] // Public API for downstream use — not consumed internally yet
pub use interner::{StrKey, StringInterner};
pub use store::GraphStore;
pub use store_models::{CodeEdge, CodeNode, EdgeKind, NodeKind};
pub use store_models::{ExtraProps, FLAG_IS_ASYNC, FLAG_IS_EXPORTED, FLAG_IS_PUBLIC, FLAG_IS_METHOD, FLAG_ADDRESS_TAKEN, FLAG_HAS_DECORATORS};

pub use cached::CachedGraphQuery;
pub use traits::GraphQuery;
