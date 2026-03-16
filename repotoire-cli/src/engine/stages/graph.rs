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
///
/// Steps:
/// 1. Remove entities for changed + removed files from the existing graph
/// 2. Re-insert new nodes/edges from the fresh parse results
/// 3. Compute a new edge fingerprint for topology change detection
pub fn graph_patch_stage(input: &GraphPatchInput) -> Result<GraphOutput> {
    let graph = input.graph.clone();

    // Step 1: Remove old entities for changed + removed files.
    // remove_file_entities expects relative paths (matching how build_graph stores them).
    let files_to_remove: Vec<PathBuf> = input
        .changed_files
        .iter()
        .chain(input.removed_files.iter())
        .filter_map(|p| {
            p.strip_prefix(input.repo_path)
                .ok()
                .map(|r| r.to_path_buf())
        })
        .collect();

    graph.remove_file_entities(&files_to_remove);

    // Step 2: Re-insert new parse results into the graph.
    // build_graph is additive — it adds nodes/edges to the existing graph.
    if !input.new_parse_results.is_empty() {
        let multi = indicatif::MultiProgress::with_draw_target(
            indicatif::ProgressDrawTarget::hidden(),
        );
        let bar_style = indicatif::ProgressStyle::default_bar();

        let value_store = crate::cli::analyze::graph::build_graph(
            &graph,
            input.repo_path,
            input.new_parse_results,
            &multi,
            &bar_style,
        )?;

        // Step 3: Compute new edge fingerprint
        let edge_fingerprint = graph.compute_edge_fingerprint();

        Ok(GraphOutput {
            graph,
            value_store: Some(Arc::new(value_store)),
            edge_fingerprint,
        })
    } else {
        // No new files to add — just compute fingerprint after removals
        let edge_fingerprint = graph.compute_edge_fingerprint();

        Ok(GraphOutput {
            graph,
            value_store: None,
            edge_fingerprint,
        })
    }
}
