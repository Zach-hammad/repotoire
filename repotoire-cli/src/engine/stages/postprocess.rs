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
pub fn postprocess_stage(_input: PostprocessInput) -> Result<PostprocessOutput> {
    todo!("Implement in Task 9")
}
