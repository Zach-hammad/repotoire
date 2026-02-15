//! Graph database for code analysis
//!
//! Pure Rust implementation using petgraph + sled.
//! No C++ dependencies - builds everywhere!

pub mod store;
pub mod streaming_builder;

pub use store::{CodeEdge, CodeNode, EdgeKind, GraphStore, NodeKind};
pub use streaming_builder::{
    build_graph_streaming, parse_and_build_streaming_true, parse_and_build_pipeline,
    FunctionLookup, ModuleLookup, StreamingGraphBuilder, StreamingGraphStats,
};

// Legacy Kuzu modules (kept for reference but not used)
// mod client;
// pub mod queries;
// pub mod schema;
