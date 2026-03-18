//! Stage 4: Git history enrichment (impure — mutates graph nodes).

use crate::git::co_change::{CoChangeConfig, CoChangeMatrix};
use crate::graph::GraphStore;
use anyhow::Result;
use std::path::Path;

/// Input for the git enrichment stage.
pub struct GitEnrichInput<'a> {
    pub repo_path: &'a Path,
    pub graph: &'a GraphStore,
    pub co_change_config: CoChangeConfig,
}

/// Output from the git enrichment stage.
pub struct GitEnrichOutput {
    pub functions_enriched: usize,
    pub classes_enriched: usize,
    pub cache_hits: usize,
    pub co_change_matrix: CoChangeMatrix,
}

impl GitEnrichOutput {
    /// Create output representing a skipped git enrichment.
    pub fn skipped() -> Self {
        Self {
            functions_enriched: 0,
            classes_enriched: 0,
            cache_hits: 0,
            co_change_matrix: CoChangeMatrix::empty(),
        }
    }
}

/// Enriches graph nodes with churn, blame, last-modified data.
///
/// IMPURE: Mutates graph nodes in place (additive metadata only).
/// Must complete before detect_stage reads the graph.
pub fn git_enrich_stage(input: &GitEnrichInput) -> Result<GitEnrichOutput> {
    let stats = crate::git::enrichment::enrich_graph_with_git(
        input.repo_path,
        input.graph,
        None, // repo_id — not needed for local analysis
    )?;

    let co_change_matrix = crate::git::co_change::compute_from_repo(
        input.repo_path,
        &input.co_change_config,
    )
    .unwrap_or_else(|e| {
        tracing::warn!("Co-change analysis failed: {e}");
        CoChangeMatrix::empty()
    });

    Ok(GitEnrichOutput {
        functions_enriched: stats.functions_enriched,
        classes_enriched: stats.classes_enriched,
        cache_hits: stats.cache_hits,
        co_change_matrix,
    })
}
