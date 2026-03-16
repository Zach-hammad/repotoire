//! Stage 3: Graph construction and patching.

use crate::graph::GraphStore;
use crate::parsers::ParseResult;
use crate::values::store::ValueStore;
use anyhow::Result;
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Input for full graph construction (cold path).
pub struct GraphInput<'a> {
    pub parse_results: &'a [(PathBuf, Arc<ParseResult>)],
    pub repo_path: &'a Path,
}

/// Output from graph construction or patching.
pub struct GraphOutput {
    pub graph: Arc<GraphStore>,
    pub value_store: Option<Arc<ValueStore>>,
    /// Edge fingerprint (hash of all cross-file edges) for topology change detection.
    pub edge_fingerprint: u64,
}

/// Input for incremental graph patching.
pub struct GraphPatchInput<'a> {
    pub graph: Arc<GraphStore>,
    pub changed_files: &'a [PathBuf],
    pub removed_files: &'a [PathBuf],
    pub new_parse_results: &'a [(PathBuf, Arc<ParseResult>)],
    pub repo_path: &'a Path,
}

/// Build a graph from scratch (cold path).
pub fn graph_stage(input: &GraphInput) -> Result<GraphOutput> {
    let graph = Arc::new(GraphStore::in_memory());

    // Create hidden progress bars (no terminal output) to satisfy the existing API
    let multi = indicatif::MultiProgress::with_draw_target(
        indicatif::ProgressDrawTarget::hidden(),
    );
    let bar_style = indicatif::ProgressStyle::default_bar();

    // Delegate to the existing build_graph function
    let value_store = crate::cli::analyze::graph::build_graph(
        &graph,
        input.repo_path,
        input.parse_results,
        &multi,
        &bar_style,
    )?;

    // Compute edge fingerprint for topology change detection
    let edge_fingerprint = graph.compute_edge_fingerprint();

    Ok(GraphOutput {
        graph,
        value_store: Some(Arc::new(value_store)),
        edge_fingerprint,
    })
}

/// Patch an existing graph with delta changes (incremental path).
pub fn graph_patch_stage(_input: &GraphPatchInput) -> Result<GraphOutput> {
    todo!("Implement in Task 10")
}
