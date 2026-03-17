//! Stage 7: Finding transforms (pure — no caching, no I/O, no presentation).

use crate::config::ProjectConfig;
use crate::graph::GraphQuery;
use crate::models::Finding;
use anyhow::Result;
use std::path::{Path, PathBuf};

/// Input for the postprocess stage. Takes ownership of findings.
pub struct PostprocessInput<'a> {
    pub findings: Vec<Finding>,
    pub project_config: &'a ProjectConfig,
    pub graph: &'a dyn GraphQuery,
    pub all_files: &'a [PathBuf],
    pub repo_path: &'a Path,
    pub verify: bool,
}

/// Statistics from the postprocess stage.
pub struct PostprocessStats {
    pub input_count: usize,
    pub output_count: usize,
    pub suppressed: usize,
    pub excluded: usize,
    pub deduped: usize,
    pub fp_filtered: usize,
    pub security_downgraded: usize,
}

/// Output from the postprocess stage.
pub struct PostprocessOutput {
    pub findings: Vec<Finding>,
    pub stats: PostprocessStats,
}

/// Run the postprocessing pipeline on findings.
///
/// Takes ownership of findings (every sub-step mutates the Vec).
pub fn postprocess_stage(input: PostprocessInput) -> Result<PostprocessOutput> {
    let input_count = input.findings.len();
    let mut findings = input.findings;

    // Create a temporary no-op IncrementalCache (required by the existing function
    // but not used since we pass is_incremental_mode: false)
    let cache_dir = std::env::temp_dir().join("repotoire-stage-noop-cache");
    let mut dummy_cache = crate::detectors::IncrementalCache::new(&cache_dir);

    // Delegate to the existing postprocess_findings function with dummy/no-op
    // values for params that are being deprecated in the new engine
    crate::cli::analyze::postprocess::postprocess_findings(
        &mut findings,
        input.project_config,
        &mut dummy_cache,
        false,  // is_incremental_mode — no caching in the new engine's postprocess
        &[],    // files_to_parse — empty, not used in non-incremental mode
        input.all_files,
        0,      // max_files — 0 means no limit (already applied in collect stage)
        input.verify,
        input.graph,
        false,  // rank — ranking is a presentation concern, not a pipeline transform
        None,   // min_confidence — no threshold filtering in postprocess
        false,  // show_all — irrelevant when min_confidence is None
        input.repo_path,
    );

    let output_count = findings.len();
    let total_removed = input_count.saturating_sub(output_count);

    Ok(PostprocessOutput {
        findings,
        stats: PostprocessStats {
            input_count,
            output_count,
            suppressed: 0,       // Individual breakdowns not available from existing function
            excluded: 0,
            deduped: 0,
            fp_filtered: total_removed,
            security_downgraded: 0,
        },
    })
}
