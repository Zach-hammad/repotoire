//! Stage 8: Health scoring.

use crate::config::ProjectConfig;
use crate::engine::ScoreResult;
use crate::graph::GraphQuery;
use crate::models::Finding;
use crate::scoring::GraphScorer;
use anyhow::Result;
use std::path::Path;

/// Input for the score stage.
pub struct ScoreInput<'a> {
    /// The graph — any type that implements GraphQuery (CodeGraph, etc.).
    pub graph: &'a dyn GraphQuery,
    pub findings: &'a [Finding],
    pub project_config: &'a ProjectConfig,
    pub repo_path: &'a Path,
    pub total_loc: usize,
}

/// Compute the three-pillar health score.
///
/// Scored on ALL postprocessed findings — no confidence filtering.
pub fn score_stage(input: &ScoreInput) -> Result<ScoreResult> {
    let scorer = GraphScorer::new(input.graph, input.project_config, input.repo_path);
    let breakdown = scorer.calculate(input.findings);

    Ok(ScoreResult {
        overall: breakdown.overall_score,
        grade: breakdown.grade.clone(),
        breakdown,
    })
}
