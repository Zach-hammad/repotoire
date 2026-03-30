//! Stage 3: Graph construction, patching, and freeze.

use crate::git::co_change::CoChangeMatrix;
use crate::graph::builder::GraphBuilder;
use crate::graph::frozen::CodeGraph;
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

/// Output from graph construction (mutable phase — before git enrichment).
///
/// The `mutable_graph` field holds the `GraphBuilder` which supports mutation
/// (needed by git enrichment). After all mutations complete, call `freeze_graph()`
/// to produce the immutable `FrozenGraphOutput`.
pub struct GraphOutput {
    /// Mutable graph for git enrichment and other mutations.
    pub mutable_graph: GraphBuilder,
    pub value_store: Option<Arc<ValueStore>>,
}

/// Output from the frozen graph phase (immutable, indexed).
///
/// Produced by calling `freeze_graph()` after all mutations (git enrichment, etc.)
/// are complete. The `graph` field is an immutable `CodeGraph` with pre-built indexes.
pub struct FrozenGraphOutput {
    /// Immutable, indexed code graph for detection and scoring.
    pub graph: Arc<CodeGraph>,
    pub value_store: Option<Arc<ValueStore>>,
    /// Edge fingerprint (hash of all cross-file edges) for topology change detection.
    pub edge_fingerprint: u64,
}

/// Input for incremental graph patching.
pub struct GraphPatchInput {
    pub mutable_graph: GraphBuilder,
    pub changed_files: Vec<PathBuf>,
    pub removed_files: Vec<PathBuf>,
    pub new_parse_results: Vec<(PathBuf, Arc<ParseResult>)>,
    pub repo_path: PathBuf,
}

/// Build a graph from scratch (cold path).
///
/// Returns a mutable `GraphOutput` suitable for git enrichment. After all
/// mutations complete, call `freeze_graph()` to produce the immutable `FrozenGraphOutput`.
pub fn graph_stage(input: &GraphInput) -> Result<GraphOutput> {
    let mut graph = GraphBuilder::new();

    // Create hidden progress bars (no terminal output) to satisfy the existing API
    let multi = indicatif::MultiProgress::with_draw_target(indicatif::ProgressDrawTarget::hidden());
    let bar_style = indicatif::ProgressStyle::default_bar();

    // Delegate to the existing build_graph function
    let value_store = crate::cli::analyze::graph::build_graph(
        &mut graph,
        input.repo_path,
        input.parse_results,
        &multi,
        &bar_style,
    )?;

    Ok(GraphOutput {
        mutable_graph: graph,
        value_store: Some(Arc::new(value_store)),
    })
}

/// Freeze a mutable GraphBuilder into an immutable `CodeGraph` with pre-built indexes.
///
/// Call this AFTER git enrichment and all other mutations are complete.
/// Converts the `GraphBuilder` to a `CodeGraph` and computes the edge fingerprint.
pub fn freeze_graph(
    builder: GraphBuilder,
    value_store: Option<Arc<ValueStore>>,
    co_change: Option<&CoChangeMatrix>,
) -> FrozenGraphOutput {
    let code_graph = if let Some(cc) = co_change {
        builder.freeze_with_co_change(cc)
    } else {
        builder.freeze()
    };
    let edge_fingerprint = code_graph.edge_fingerprint();

    FrozenGraphOutput {
        graph: Arc::new(code_graph),
        value_store,
        edge_fingerprint,
    }
}

/// Patch an existing graph with delta changes (incremental path).
///
/// Steps:
/// 1. Remove entities for changed + removed files from the existing graph
/// 2. Re-insert new nodes/edges from the fresh parse results
///
/// Returns a mutable GraphOutput for further enrichment.
pub fn graph_patch_stage(input: GraphPatchInput) -> Result<GraphOutput> {
    let mut graph = input.mutable_graph;

    // Step 1: Remove old entities for changed + removed files.
    // remove_file_entities expects relative paths (matching how build_graph stores them).
    let files_to_remove: Vec<PathBuf> = input
        .changed_files
        .iter()
        .chain(input.removed_files.iter())
        .filter_map(|p| {
            p.strip_prefix(&input.repo_path)
                .ok()
                .map(|r| r.to_path_buf())
        })
        .collect();

    graph.remove_file_entities(&files_to_remove);

    // Step 2: Re-insert new parse results into the graph.
    // build_graph is additive — it adds nodes/edges to the existing graph.
    if !input.new_parse_results.is_empty() {
        let multi =
            indicatif::MultiProgress::with_draw_target(indicatif::ProgressDrawTarget::hidden());
        let bar_style = indicatif::ProgressStyle::default_bar();

        let value_store = crate::cli::analyze::graph::build_graph(
            &mut graph,
            &input.repo_path,
            &input.new_parse_results,
            &multi,
            &bar_style,
        )?;

        Ok(GraphOutput {
            mutable_graph: graph,
            value_store: Some(Arc::new(value_store)),
        })
    } else {
        // No new files to add
        Ok(GraphOutput {
            mutable_graph: graph,
            value_store: None,
        })
    }
}
