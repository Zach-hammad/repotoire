//! Stage 4: Git history enrichment (impure — mutates graph nodes).

use crate::detectors::analysis_context::FileChurnInfo;
use crate::git::co_change::{CoChangeConfig, CoChangeMatrix};
use crate::graph::builder::GraphBuilder;
use anyhow::Result;
use std::collections::HashMap;
use std::path::Path;

/// Input for the git enrichment stage.
pub struct GitEnrichInput<'a> {
    pub repo_path: &'a Path,
    pub graph: &'a mut GraphBuilder,
    pub co_change_config: CoChangeConfig,
}

/// Output from the git enrichment stage.
pub struct GitEnrichOutput {
    pub functions_enriched: usize,
    pub classes_enriched: usize,
    pub cache_hits: usize,
    pub co_change_matrix: CoChangeMatrix,
    /// Per-file churn data from last 90 days of git history.
    pub file_churn: HashMap<String, FileChurnInfo>,
}

impl GitEnrichOutput {
    /// Create output representing a skipped git enrichment.
    pub fn skipped() -> Self {
        Self {
            functions_enriched: 0,
            classes_enriched: 0,
            cache_hits: 0,
            co_change_matrix: CoChangeMatrix::empty(),
            file_churn: HashMap::new(),
        }
    }
}

/// Compute per-file churn (commit counts in last 90 days) from git history.
///
/// Uses the fast `get_recent_commits` path with a 90-day window and
/// 500-commit cap. Returns an empty map if the repo can't be opened.
pub fn compute_file_churn(repo_path: &Path) -> HashMap<String, FileChurnInfo> {
    let mut churn = HashMap::new();

    let history = match crate::git::history::GitHistory::open(repo_path) {
        Ok(h) => h,
        Err(_) => return churn,
    };

    // 90 days ago
    let since = chrono::Utc::now() - chrono::Duration::days(90);

    let commits = match history.get_recent_commits(500, Some(since)) {
        Ok(c) => c,
        Err(_) => return churn,
    };

    // Count commits per file
    for commit in &commits {
        for file in &commit.files_changed {
            let entry = churn
                .entry(file.clone())
                .or_insert_with(FileChurnInfo::default);
            entry.commits_90d += 1;
        }
    }

    // Set high_churn flag
    for info in churn.values_mut() {
        info.is_high_churn = info.commits_90d >= 5;
    }

    churn
}

/// Enriches graph nodes with churn, blame, last-modified data.
///
/// IMPURE: Mutates graph nodes in place (additive metadata only).
/// Must complete before detect_stage reads the graph.
///
/// The three sub-stages are independent:
/// - `enrich_graph_with_git` mutates the graph (needs &mut);
/// - `compute_from_repo` (co-change) only reads git history;
/// - `compute_file_churn` only reads git history.
///
/// We run them in parallel via `rayon::join` — the graph mutation runs on one
/// worker, the two read-only git walks on the other. This cut `git_enrich`
/// wall-time roughly in half on measured cold-starts.
pub fn git_enrich_stage(input: &mut GitEnrichInput) -> Result<GitEnrichOutput> {
    let t_total = std::time::Instant::now();
    let repo_path = input.repo_path;
    let co_change_config = &input.co_change_config;
    let graph: &mut GraphBuilder = &mut *input.graph;

    let (stats_result, (co_change_matrix, file_churn)) = rayon::join(
        || {
            let t = std::time::Instant::now();
            let r = crate::git::enrichment::enrich_graph_with_git(
                repo_path,
                graph,
                None, // repo_id — not needed for local analysis
            );
            tracing::debug!(
                "git_enrich::enrichment = {:.3}s",
                t.elapsed().as_secs_f32()
            );
            r
        },
        || {
            rayon::join(
                || {
                    let t = std::time::Instant::now();
                    let m = crate::git::co_change::compute_from_repo(
                        repo_path,
                        co_change_config,
                    )
                    .unwrap_or_else(|e| {
                        tracing::debug!("Co-change analysis skipped: {e}");
                        CoChangeMatrix::empty()
                    });
                    tracing::debug!(
                        "git_enrich::co_change = {:.3}s ({} pairs)",
                        t.elapsed().as_secs_f32(),
                        m.len()
                    );
                    m
                },
                || {
                    let t = std::time::Instant::now();
                    let c = compute_file_churn(repo_path);
                    tracing::debug!(
                        "git_enrich::file_churn = {:.3}s ({} files)",
                        t.elapsed().as_secs_f32(),
                        c.len()
                    );
                    c
                },
            )
        },
    );

    tracing::debug!(
        "git_enrich total = {:.3}s (parallel)",
        t_total.elapsed().as_secs_f32()
    );

    let stats = stats_result?;
    Ok(GitEnrichOutput {
        functions_enriched: stats.functions_enriched,
        classes_enriched: stats.classes_enriched,
        cache_hits: stats.cache_hits,
        co_change_matrix,
        file_churn,
    })
}
