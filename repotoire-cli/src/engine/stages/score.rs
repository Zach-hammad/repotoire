//! Stage 8: Health scoring.

use crate::config::ProjectConfig;
use crate::engine::ScoreResult;
use crate::graph::GraphQuery;
use crate::models::Finding;
use anyhow::Result;
use std::path::Path;

/// Input for the score stage.
pub struct ScoreInput<'a> {
    pub graph: &'a dyn GraphQuery,
    pub findings: &'a [Finding],
    pub project_config: &'a ProjectConfig,
    pub repo_path: &'a Path,
    pub total_loc: usize,
}

/// Compute the three-pillar health score.
///
/// Scored on ALL postprocessed findings — no confidence filtering.
pub fn score_stage(_input: &ScoreInput) -> Result<ScoreResult> {
    todo!("Implement in Task 9")
}
