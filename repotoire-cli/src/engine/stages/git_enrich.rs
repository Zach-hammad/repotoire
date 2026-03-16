//! Stage 4: Git history enrichment (impure — mutates graph nodes).

use crate::graph::GraphStore;
use anyhow::Result;
use std::path::Path;

/// Input for the git enrichment stage.
pub struct GitEnrichInput<'a> {
    pub repo_path: &'a Path,
    pub graph: &'a GraphStore,
}

/// Output from the git enrichment stage.
pub struct GitEnrichOutput {
    pub functions_enriched: usize,
    pub classes_enriched: usize,
    pub cache_hits: usize,
}

impl GitEnrichOutput {
    /// Create output representing a skipped git enrichment.
    pub fn skipped() -> Self {
        Self {
            functions_enriched: 0,
            classes_enriched: 0,
            cache_hits: 0,
        }
    }
}

/// Enriches graph nodes with churn, blame, last-modified data.
///
/// IMPURE: Mutates graph nodes in place (additive metadata only).
/// Must complete before detect_stage reads the graph.
pub fn git_enrich_stage(_input: &GitEnrichInput) -> Result<GitEnrichOutput> {
    todo!("Implement in Task 6")
}
