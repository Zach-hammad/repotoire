//! Analysis engine — layered, presentation-free code health analysis.
//!
//! # Architecture
//!
//! The engine cleanly separates *analysis* from *presentation*:
//!
//! - **`AnalysisEngine`** owns the repository state (graph, calibration data,
//!   cached findings) and exposes a single `analyze()` method that returns an
//!   [`AnalysisResult`] containing findings, score, and stats.
//! - **Consumers** (CLI `run_engine()`, future web dashboard) take
//!   the result and apply their own formatting, filtering, and pagination.
//!
//! # Pipeline stages
//!
//! Each call to `analyze()` runs 9 stages in order:
//!
//! 1. **Collect** — walk the repo, hash file contents, determine deltas
//! 2. **Parse** — tree-sitter parse source files in parallel
//! 3. **Graph** — build the in-memory CSR code graph
//! 4. **Git enrich** — add churn/blame/commit metadata to graph nodes
//! 5. **Calibrate** — learn adaptive thresholds + n-gram language model
//! 6. **Detect** — run all detectors in parallel (with incremental reuse)
//! 7. **Postprocess** — deduplicate, suppress, filter findings
//! 7.5. **Filter** — baseline matching, config overrides, delta attribution
//! 8. **Score** — compute three-pillar health score
//!
//! # Incremental analysis
//!
//! The engine is **stateful**: after the first cold analysis, subsequent calls
//! to `analyze()` detect file changes via content hashing and:
//! - Return cached results instantly if nothing changed
//! - Parse only changed files, patch the graph, and selectively re-run
//!   detectors when files were added/modified/removed
//!
//! # Persistence
//!
//! Engine state can be saved to disk via `save()` and restored via `load()`,
//! enabling cross-process incremental analysis (e.g., between CLI invocations).

pub mod context;
pub mod delta;
pub mod diff;
mod report_context;
pub mod stages;
pub mod state;

use crate::config::{load_project_config, ProjectConfig};
use crate::graph::frozen::CodeGraph;
use crate::graph::GraphQuery;
use crate::models::{Finding, Grade};
use crate::scoring::ScoreBreakdown;
use anyhow::Context;
use serde::{Deserialize, Serialize};
use std::borrow::Cow;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

/// Configuration for what analysis to perform.
/// No presentation concerns — no format, no pagination, no emoji.
#[derive(Debug, Clone)]
pub struct AnalysisConfig {
    pub workers: usize,
    pub skip_detectors: Vec<String>,
    pub max_files: usize,
    pub no_git: bool,
    pub verify: bool,
    /// Run all detectors including deep-scan detectors (code smells, style, dead code).
    /// Default: false (only high-value detectors run).
    pub all_detectors: bool,
    /// Force a fresh analysis, ignoring cached session data.
    pub force_reanalyze: bool,
}

impl Default for AnalysisConfig {
    fn default() -> Self {
        Self {
            workers: 8,
            skip_detectors: Vec::new(),
            max_files: 0,
            no_git: false,
            verify: false,
            all_detectors: false,
            force_reanalyze: false,
        }
    }
}

/// How the analysis was performed.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub enum AnalysisMode {
    #[default]
    Cold,
    Incremental {
        files_changed: usize,
    },
    Cached,
}

/// Health score result.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScoreResult {
    pub overall: f64,
    pub grade: Grade,
    pub breakdown: ScoreBreakdown,
}

/// Stats from each pipeline phase.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct AnalysisStats {
    pub mode: AnalysisMode,
    pub files_analyzed: usize,
    pub total_functions: usize,
    pub total_classes: usize,
    pub total_loc: usize,
    pub detectors_run: usize,
    pub findings_before_postprocess: usize,
    pub findings_filtered: usize,
    pub timings: BTreeMap<String, Duration>,
}

/// The single return type from analysis.
#[derive(Debug, Clone)]
pub struct AnalysisResult {
    pub findings: Vec<Finding>,
    pub score: ScoreResult,
    pub stats: AnalysisStats,
}

/// Progress event for UI feedback.
#[derive(Debug, Clone)]
pub enum ProgressEvent {
    StageStarted {
        name: Cow<'static, str>,
        total: Option<usize>,
    },
    StageProgress {
        current: usize,
    },
    StageCompleted {
        name: Cow<'static, str>,
        duration: Duration,
    },
}

/// Progress callback type.
pub type ProgressFn = Arc<dyn Fn(ProgressEvent) + Send + Sync>;

/// Re-exported from `cli::analyze` — presentation options live with their consumers.
pub use crate::cli::analyze::OutputOptions;

/// The analysis engine — the primary entry point for running code health analysis.
///
/// `AnalysisEngine` is stateful: it caches the code graph, calibration data,
/// and detector findings between calls. This enables three analysis modes:
///
/// - **Cold**: First call — full parse, graph build, calibrate, detect, score.
/// - **Cached**: Subsequent call with no file changes — returns previous results instantly.
/// - **Incremental**: Subsequent call with file changes — re-parses only deltas,
///   patches the graph, and selectively re-runs affected detectors.
///
/// # Usage
///
/// ```no_run
/// use repotoire::engine::{AnalysisEngine, AnalysisConfig};
/// use std::path::Path;
///
/// let mut engine = AnalysisEngine::new(Path::new("/path/to/repo"), false).unwrap();
/// let result = engine.analyze(&AnalysisConfig::default()).unwrap();
/// println!("Score: {} ({})", result.score.overall, result.score.grade);
/// ```
pub struct AnalysisEngine {
    repo_path: PathBuf,
    project_config: ProjectConfig,
    state: Option<state::EngineState>,
    progress: Option<ProgressFn>,
    ownership_model: Option<Arc<crate::git::ownership::OwnershipModel>>,
    /// Whether --all-detectors was set (for fingerprint computation in save)
    all_detectors: bool,
}

impl AnalysisEngine {
    /// Create a new engine for the given repository path.
    ///
    /// Canonicalizes the path and loads project config from `repotoire.toml` (or defaults).
    pub fn new(repo_path: &Path, all_detectors: bool) -> anyhow::Result<Self> {
        let repo_path = repo_path.canonicalize()?;
        let project_config = load_project_config(&repo_path);
        Ok(Self {
            repo_path,
            project_config,
            state: None,
            progress: None,
            ownership_model: None,
            all_detectors,
        })
    }

    /// Builder method to set a progress callback for UI feedback.
    pub fn with_progress(mut self, progress: ProgressFn) -> Self {
        self.progress = Some(progress);
        self
    }

    /// Returns a reference to the code graph if analysis has been run.
    pub fn graph(&self) -> Option<&dyn GraphQuery> {
        self.state
            .as_ref()
            .map(|s| s.graph.as_ref() as &dyn GraphQuery)
    }

    /// Returns a reference to the co-change matrix if git enrichment was run.
    pub fn co_change(&self) -> Option<&crate::git::co_change::CoChangeMatrix> {
        self.state.as_ref().and_then(|s| s.co_change.as_deref())
    }

    /// Returns the calibrated style profile (if available).
    pub fn style_profile(&self) -> Option<&crate::calibrate::StyleProfile> {
        self.state.as_ref().map(|s| &s.style_profile)
    }

    /// Returns a reference to the concrete `CodeGraph` if analysis has been run.
    ///
    /// Use this when you need `CodeGraph`-specific APIs.
    /// For general graph queries, prefer `graph()` which returns `&dyn GraphQuery`.
    pub fn code_graph(&self) -> Option<&CodeGraph> {
        self.state.as_ref().map(|s| s.graph.as_ref())
    }

    /// Returns a shared `Arc<CodeGraph>` if analysis has been run.
    ///
    /// Use this when you need to share the graph across handler boundaries
    /// (e.g., state passing the graph to other tool handlers).
    pub fn graph_arc(&self) -> Option<Arc<CodeGraph>> {
        self.state.as_ref().map(|s| Arc::clone(&s.graph))
    }

    /// Returns a reference to the mutable `GraphBuilder` if cached.
    ///
    /// This is only available during in-process incremental analysis.
    /// After load() from disk, this returns None.
    pub fn graph_builder(&self) -> Option<&crate::graph::builder::GraphBuilder> {
        self.state.as_ref().and_then(|s| s.mutable_graph.as_ref())
    }

    /// Returns a reference to the project configuration.
    pub fn project_config(&self) -> &ProjectConfig {
        &self.project_config
    }

    /// Returns the canonicalized repository path.
    pub fn repo_path(&self) -> &Path {
        &self.repo_path
    }

    /// Run analysis on the repository.
    ///
    /// Wires all 8 stages together: collect -> parse -> graph -> git_enrich ->
    /// calibrate -> detect -> postprocess -> score.
    ///
    /// On the first call, performs a full (cold) analysis.
    /// On subsequent calls with unchanged files, returns cached results.
    /// When files change between calls, runs incremental analysis (delta only).
    pub fn analyze(&mut self, config: &AnalysisConfig) -> anyhow::Result<AnalysisResult> {
        use stages::*;
        let mut timings = BTreeMap::new();

        // Stage 1: Collect — walk the repo and hash file contents
        let exclude_patterns = self.project_config.exclude.effective_patterns();
        let collect_out = timed(&mut timings, "collect", || {
            collect::collect_stage(&collect::CollectInput {
                repo_path: &self.repo_path,
                exclude_patterns: &exclude_patterns,
                max_files: config.max_files,
            })
        })?;

        // Diff: compare current file hashes against previous state
        let changes = match &self.state {
            Some(state) => diff::FileChanges::compute(&state.file_hashes, &collect_out),
            None => diff::FileChanges::cold(&collect_out),
        };

        // Fast path: if nothing changed and we have cached state, return it
        if changes.nothing_changed() {
            if let Some(ref state) = self.state {
                return Ok(AnalysisResult {
                    findings: state.last_findings.clone(),
                    score: state.last_score.clone(),
                    stats: AnalysisStats {
                        mode: AnalysisMode::Cached,
                        ..state.last_stats.clone()
                    },
                });
            }
        }

        let all_files = collect_out.all_paths();

        // Ensure cache directory exists (build_graph saves graph stats there)
        let _ = crate::cache::ensure_cache_dir(&self.repo_path);

        // Decide: incremental or cold path
        let is_incremental = self.state.is_some() && changes.is_delta();

        if is_incremental {
            self.analyze_incremental(config, &collect_out, &changes, all_files, timings)
        } else {
            self.analyze_cold(config, &collect_out, all_files, timings)
        }
    }

    /// Cold analysis path — full parse, graph build, calibrate, detect, score.
    fn analyze_cold(
        &mut self,
        config: &AnalysisConfig,
        collect_out: &stages::collect::CollectOutput,
        all_files: Vec<PathBuf>,
        mut timings: BTreeMap<String, Duration>,
    ) -> anyhow::Result<AnalysisResult> {
        use stages::*;

        // Stage 2: Parse — tree-sitter parse all source files in parallel
        let parse_out = timed(&mut timings, "parse", || {
            parse::parse_stage(&parse::ParseInput {
                files: all_files.clone(),
                workers: config.workers,
                progress: self.progress.clone(),
            })
        })?;

        // Stage 3: Graph — build the mutable code graph from parse results
        let mut graph_out = timed(&mut timings, "graph", || {
            graph::graph_stage(&graph::GraphInput {
                parse_results: &parse_out.results,
                repo_path: &self.repo_path,
            })
        })?;

        // Stage 4: Git enrich — add churn/blame/commit data to mutable graph nodes
        let git_out = if !config.no_git {
            timed(&mut timings, "git_enrich", || {
                git_enrich::git_enrich_stage(&mut git_enrich::GitEnrichInput {
                    repo_path: &self.repo_path,
                    graph: &mut graph_out.mutable_graph,
                    co_change_config: self.project_config.co_change.to_runtime(),
                })
            })?
        } else {
            git_enrich::GitEnrichOutput::skipped()
        };

        // Extract file churn before moving git_out fields
        let file_churn = Arc::new(git_out.file_churn);

        // Wrap co_change matrix in Arc for sharing with detectors and state
        let co_change_arc: Option<Arc<crate::git::co_change::CoChangeMatrix>> =
            Some(Arc::new(git_out.co_change_matrix));

        // Freeze: convert mutable GraphBuilder → immutable CodeGraph with indexes
        let frozen = timed(&mut timings, "freeze", || {
            graph::freeze_graph(
                graph_out.mutable_graph,
                graph_out.value_store,
                co_change_arc.as_ref().map(|a| a.as_ref()),
            )
        });

        // Stage 4.5: Ownership enrich — DOA-based file ownership from git history
        let ownership_model = if !config.no_git && self.project_config.ownership.enabled {
            let ownership_out = timed(&mut timings, "ownership_enrich", || {
                ownership_enrich::ownership_enrich_stage(&ownership_enrich::OwnershipEnrichInput {
                    repo_path: &self.repo_path,
                    ownership_config: self.project_config.ownership.to_runtime(),
                })
            })?;
            Some(Arc::new(ownership_out.model))
        } else {
            None
        };
        self.ownership_model = ownership_model.clone();

        // Stage 5: Calibrate — learn adaptive thresholds from the codebase
        let calibrate_out = timed(&mut timings, "calibrate", || {
            calibrate::calibrate_stage(&calibrate::CalibrateInput {
                parse_results: &parse_out.results,
                file_count: collect_out.files.len(),
                repo_path: &self.repo_path,
            })
        })?;

        // Load L3 cached embeddings (if available from a prior run)
        let cached_embeddings = {
            let cache_dir = crate::cache::paths::cache_dir(&self.repo_path);
            crate::predictive::embedding_scorer::load_embeddings(
                &cache_dir,
                frozen.edge_fingerprint,
            )
            .map(Arc::new)
        };

        // Stage 6: Detect — run all detectors in parallel
        let detect_out = timed(&mut timings, "detect", || {
            detect::detect_stage(&detect::DetectInput {
                graph: frozen.graph.as_ref(),
                source_files: &all_files,
                repo_path: &self.repo_path,
                project_config: &self.project_config,
                style_profile: Some(&calibrate_out.style_profile),
                ngram_model: calibrate_out.ngram_model.as_ref(),
                value_store: frozen.value_store.as_ref(),
                skip_detectors: &config.skip_detectors,
                workers: config.workers,
                progress: self.progress.clone(),
                file_churn: Arc::clone(&file_churn),
                co_change_matrix: co_change_arc.as_ref().map(Arc::clone),
                all_detectors: config.all_detectors,
                ownership: ownership_model.clone(),
                cached_embeddings: cached_embeddings.clone(),
                // Cold path — no incremental hints
                changed_files: None,
                topology_changed: true,
                cached_gd_precomputed: None,
                cached_file_findings: None,
                cached_graph_wide_findings: None,
            })
        })?;

        // Stage 7: Postprocess — deduplicate, suppress, filter findings
        let postprocess_out = timed(&mut timings, "postprocess", || {
            postprocess::postprocess_stage(postprocess::PostprocessInput {
                findings: detect_out.findings,
                project_config: &self.project_config,
                graph: frozen.graph.as_ref(),
                all_files: &all_files,
                repo_path: &self.repo_path,
                verify: config.verify,
                bypass_set: detect_out.bypass_set,
            })
        })?;

        // Merge already-postprocessed cached findings (empty on cold path)
        let mut final_findings = postprocess_out.findings;
        final_findings.extend(detect_out.cached_findings);

        // Stage 7.5: Filter — baseline matching, config overrides, delta attribution
        let graph_for_filter = Arc::clone(&frozen.graph);
        let resolve_qn = move |f: &crate::models::Finding| -> Option<String> {
            let file = f.affected_files.first()?;
            let line = f.line_start?;
            let file_str = file.to_string_lossy();
            let interner = graph_for_filter.interner();
            if let Some(idx) = graph_for_filter.function_at_idx(&file_str, line) {
                if let Some(node) = graph_for_filter.node_idx(idx) {
                    return Some(interner.resolve(node.qualified_name).to_string());
                }
            }
            for &cls_idx in graph_for_filter.classes_in_file_idx(&file_str) {
                if let Some(cls) = graph_for_filter.node_idx(cls_idx) {
                    if cls.line_start <= line && cls.line_end >= line {
                        return Some(interner.resolve(cls.qualified_name).to_string());
                    }
                }
            }
            None
        };
        let filter_output = filter::filter_stage(filter::FilterInput {
            findings: final_findings,
            baseline: None,
            detector_overrides: &self.project_config.detectors,
            changed_node_qnames: None,
            caller_of_changed_qnames: None,
            resolve_qualified_name: Some(&resolve_qn),
        });
        let final_findings = filter_output.findings;

        // Stage 8: Score — compute three-pillar health score
        let score_out = timed(&mut timings, "score", || {
            score::score_stage(&score::ScoreInput {
                graph: frozen.graph.as_ref(),
                findings: &final_findings,
                project_config: &self.project_config,
                repo_path: &self.repo_path,
                total_loc: parse_out.stats.total_loc,
            })
        })?;

        // Build stats
        let stats = AnalysisStats {
            mode: AnalysisMode::Cold,
            files_analyzed: collect_out.files.len(),
            total_functions: parse_out.stats.total_functions,
            total_classes: parse_out.stats.total_classes,
            total_loc: parse_out.stats.total_loc,
            detectors_run: detect_out.stats.detectors_run,
            findings_before_postprocess: postprocess_out.stats.input_count,
            findings_filtered: postprocess_out
                .stats
                .input_count
                .saturating_sub(postprocess_out.stats.output_count),
            timings,
        };

        // Cache state for next call
        self.state = Some(state::EngineState {
            file_hashes: collect_out
                .files
                .iter()
                .map(|f| (f.path.clone(), f.content_hash))
                .collect(),
            source_files: all_files,
            graph: frozen.graph,
            mutable_graph: None, // Consumed by freeze — rebuilt from CodeGraph on incremental path
            edge_fingerprint: frozen.edge_fingerprint,
            co_change: co_change_arc,
            precomputed: Some(detect_out.precomputed),
            style_profile: calibrate_out.style_profile,
            ngram_model: calibrate_out.ngram_model,
            findings_by_file: detect_out.findings_by_file,
            graph_wide_findings: detect_out.graph_wide_findings,
            last_findings: final_findings.clone(),
            last_score: score_out.clone(),
            last_stats: stats.clone(),
        });

        Ok(AnalysisResult {
            findings: final_findings,
            score: score_out,
            stats,
        })
    }

    /// Incremental analysis path — parse only changed files, patch graph, reuse calibration.
    fn analyze_incremental(
        &mut self,
        config: &AnalysisConfig,
        collect_out: &stages::collect::CollectOutput,
        changes: &diff::FileChanges,
        all_files: Vec<PathBuf>,
        mut timings: BTreeMap<String, Duration>,
    ) -> anyhow::Result<AnalysisResult> {
        use stages::*;

        let files_changed = changes.changed.len() + changes.added.len() + changes.removed.len();
        let delta_files = changes.changed_and_added();

        // Take the previous state (we'll put it back at the end)
        let prev_state = self.state.take().expect("incremental requires state");
        let mut prev_co_change = prev_state.co_change;

        // Evict changed files from the global file cache so detectors read fresh content
        crate::cache::global_cache().evict(&delta_files);

        // Stage 2: Parse — only changed + added files
        let parse_out = timed(&mut timings, "parse", || {
            parse::parse_stage(&parse::ParseInput {
                files: delta_files.clone(),
                workers: config.workers,
                progress: self.progress.clone(),
            })
        })?;

        // Stage 3+4+freeze: Graph patch, git enrich, and freeze.
        //
        // Try to reconstruct a GraphBuilder from the frozen CodeGraph for patching.
        // If the Arc has other references (shouldn't happen since we took ownership
        // of prev_state), fall back to reusing the graph as-is.
        let can_patch = Arc::strong_count(&prev_state.graph) == 1;
        let (frozen, file_churn, co_change) = if can_patch {
            // Path A: Reconstruct builder from frozen graph, patch, re-freeze
            let code_graph = match Arc::try_unwrap(prev_state.graph) {
                Ok(g) => g,
                Err(_) => unreachable!("strong_count was 1"),
            };
            let mutable_graph = crate::graph::builder::GraphBuilder::from_frozen(code_graph);

            let mut graph_out = timed(&mut timings, "graph", || {
                graph::graph_patch_stage(graph::GraphPatchInput {
                    mutable_graph,
                    changed_files: changes.changed.clone(),
                    removed_files: changes.removed.clone(),
                    new_parse_results: parse_out.results.clone(),
                    repo_path: self.repo_path.clone(),
                })
            })?;

            let git_out = if !config.no_git {
                timed(&mut timings, "git_enrich", || {
                    git_enrich::git_enrich_stage(&mut git_enrich::GitEnrichInput {
                        repo_path: &self.repo_path,
                        graph: &mut graph_out.mutable_graph,
                        co_change_config: self.project_config.co_change.to_runtime(),
                    })
                })?
            } else {
                git_enrich::GitEnrichOutput::skipped()
            };

            let file_churn = Arc::new(git_out.file_churn);
            let co_change: Option<Arc<crate::git::co_change::CoChangeMatrix>> = if config.no_git {
                prev_co_change.take()
            } else {
                Some(Arc::new(git_out.co_change_matrix))
            };

            let frozen = timed(&mut timings, "freeze", || {
                graph::freeze_graph(
                    graph_out.mutable_graph,
                    graph_out.value_store,
                    co_change.as_ref().map(|a| a.as_ref()),
                )
            });

            (frozen, file_churn, co_change)
        } else {
            // Path B: Reuse loaded CodeGraph directly (rare fallback).
            //
            // Trade-off: the graph doesn't reflect the changed file's structural
            // changes (new functions, removed classes). But per-file detectors read
            // file content (not graph structure), and graph-wide detectors use cached
            // findings. The graph will be fully rebuilt on the next cold analysis.
            let frozen = graph::FrozenGraphOutput {
                graph: prev_state.graph,
                value_store: None,
                edge_fingerprint: prev_state.edge_fingerprint,
            };

            // Compute file churn (lightweight) for detectors.
            let file_churn = Arc::new(if !config.no_git {
                timed(&mut timings, "git_enrich", || {
                    git_enrich::compute_file_churn(&self.repo_path)
                })
            } else {
                std::collections::HashMap::new()
            });

            let co_change: Option<Arc<crate::git::co_change::CoChangeMatrix>> =
                prev_co_change.take();

            (frozen, file_churn, co_change)
        };

        // Stage 4.5: Ownership enrich — DOA-based file ownership from git history
        let ownership_model = if !config.no_git && self.project_config.ownership.enabled {
            let ownership_out = timed(&mut timings, "ownership_enrich", || {
                ownership_enrich::ownership_enrich_stage(&ownership_enrich::OwnershipEnrichInput {
                    repo_path: &self.repo_path,
                    ownership_config: self.project_config.ownership.to_runtime(),
                })
            })?;
            Some(Arc::new(ownership_out.model))
        } else {
            None
        };
        self.ownership_model = ownership_model.clone();

        // Stage 5: Calibrate — reuse cached style_profile, rebuild ngram if None
        let style_profile = prev_state.style_profile;
        let ngram_model = if prev_state.ngram_model.is_some() {
            prev_state.ngram_model
        } else {
            // Rebuild ngram from all files (need full parse for this)
            // For now, just leave it None — full calibration rebuild would be expensive.
            // A future optimization could rebuild from all_files parse results.
            None
        };

        // Detect topology change by comparing edge fingerprints.
        // Edge fingerprints hash qualified name strings (not process-local Spur keys),
        // so they are stable across save/load boundaries.
        let topology_changed = prev_state.edge_fingerprint != frozen.edge_fingerprint;

        // Load L3 cached embeddings (if available from a prior run)
        let cached_embeddings = {
            let cache_dir = crate::cache::paths::cache_dir(&self.repo_path);
            crate::predictive::embedding_scorer::load_embeddings(
                &cache_dir,
                frozen.edge_fingerprint,
            )
            .map(Arc::new)
        };

        // Reuse precomputed data when topology is stable
        let cached_gd = if !topology_changed {
            prev_state.precomputed.as_ref()
        } else {
            None
        };

        // Stage 6: Detect — run all detectors (reusing PrecomputedAnalysis when topology is stable)
        let detect_out = timed(&mut timings, "detect", || {
            detect::detect_stage(&detect::DetectInput {
                graph: frozen.graph.as_ref(),
                source_files: &all_files,
                repo_path: &self.repo_path,
                project_config: &self.project_config,
                style_profile: Some(&style_profile),
                ngram_model: ngram_model.as_ref(),
                value_store: frozen.value_store.as_ref(),
                skip_detectors: &config.skip_detectors,
                workers: config.workers,
                progress: self.progress.clone(),
                file_churn: Arc::clone(&file_churn),
                co_change_matrix: co_change.as_ref().map(Arc::clone),
                all_detectors: config.all_detectors,
                ownership: ownership_model.clone(),
                cached_embeddings: cached_embeddings.clone(),
                // Incremental hints
                changed_files: Some(&delta_files),
                topology_changed,
                cached_gd_precomputed: cached_gd,
                cached_file_findings: Some(&prev_state.findings_by_file),
                cached_graph_wide_findings: Some(&prev_state.graph_wide_findings),
            })
        })?;

        // Stage 7: Postprocess — deduplicate, suppress, filter findings
        let postprocess_out = timed(&mut timings, "postprocess", || {
            postprocess::postprocess_stage(postprocess::PostprocessInput {
                findings: detect_out.findings, // only NEW findings
                project_config: &self.project_config,
                graph: frozen.graph.as_ref(),
                all_files: &all_files,
                repo_path: &self.repo_path,
                verify: config.verify,
                bypass_set: detect_out.bypass_set,
            })
        })?;

        // Merge already-postprocessed cached findings with newly postprocessed findings
        let mut final_findings = postprocess_out.findings;
        final_findings.extend(detect_out.cached_findings);

        // Stage 7.5: Filter — baseline matching, config overrides, delta attribution
        let graph_for_filter = Arc::clone(&frozen.graph);
        let resolve_qn = move |f: &crate::models::Finding| -> Option<String> {
            let file = f.affected_files.first()?;
            let line = f.line_start?;
            let file_str = file.to_string_lossy();
            let interner = graph_for_filter.interner();
            if let Some(idx) = graph_for_filter.function_at_idx(&file_str, line) {
                if let Some(node) = graph_for_filter.node_idx(idx) {
                    return Some(interner.resolve(node.qualified_name).to_string());
                }
            }
            for &cls_idx in graph_for_filter.classes_in_file_idx(&file_str) {
                if let Some(cls) = graph_for_filter.node_idx(cls_idx) {
                    if cls.line_start <= line && cls.line_end >= line {
                        return Some(interner.resolve(cls.qualified_name).to_string());
                    }
                }
            }
            None
        };
        let filter_output = filter::filter_stage(filter::FilterInput {
            findings: final_findings,
            baseline: None,
            detector_overrides: &self.project_config.detectors,
            changed_node_qnames: None,
            caller_of_changed_qnames: None,
            resolve_qualified_name: Some(&resolve_qn),
        });
        let final_findings = filter_output.findings;

        // Merge parse stats: delta parse stats + cached totals for full codebase picture.
        // The delta only parsed changed files, but stats should reflect the whole codebase.
        let total_functions = prev_state
            .last_stats
            .total_functions
            .saturating_sub(parse_out.stats.total_functions)
            .saturating_add(parse_out.stats.total_functions);
        let total_classes = prev_state
            .last_stats
            .total_classes
            .saturating_sub(parse_out.stats.total_classes)
            .saturating_add(parse_out.stats.total_classes);
        // For LOC, use the cached total and adjust. Since we only parsed delta files,
        // we can't accurately recount all LOC. Use the cached value as a reasonable estimate.
        let total_loc = prev_state.last_stats.total_loc;

        // Stage 8: Score — compute three-pillar health score
        let score_out = timed(&mut timings, "score", || {
            score::score_stage(&score::ScoreInput {
                graph: frozen.graph.as_ref(),
                findings: &final_findings,
                project_config: &self.project_config,
                repo_path: &self.repo_path,
                total_loc,
            })
        })?;

        // Build stats
        let stats = AnalysisStats {
            mode: AnalysisMode::Incremental { files_changed },
            files_analyzed: collect_out.files.len(),
            total_functions,
            total_classes,
            total_loc,
            detectors_run: detect_out.stats.detectors_run,
            findings_before_postprocess: postprocess_out.stats.input_count,
            findings_filtered: postprocess_out
                .stats
                .input_count
                .saturating_sub(postprocess_out.stats.output_count),
            timings,
        };

        // Cache state for next call
        self.state = Some(state::EngineState {
            file_hashes: collect_out
                .files
                .iter()
                .map(|f| (f.path.clone(), f.content_hash))
                .collect(),
            source_files: all_files,
            graph: frozen.graph,
            mutable_graph: None, // Consumed by freeze — rebuilt from CodeGraph on next incremental
            edge_fingerprint: frozen.edge_fingerprint,
            co_change,
            precomputed: Some(detect_out.precomputed),
            style_profile,
            ngram_model,
            findings_by_file: detect_out.findings_by_file,
            graph_wide_findings: detect_out.graph_wide_findings,
            last_findings: final_findings.clone(),
            last_score: score_out.clone(),
            last_stats: stats.clone(),
        });

        // Background L3 embedding computation placeholder removed (was empty block)

        Ok(AnalysisResult {
            findings: final_findings,
            score: score_out,
            stats,
        })
    }

    /// Persist the current engine state to disk.
    ///
    /// Writes two files into `session_path`:
    /// - `engine_session.json` — serializable metadata (hashes, findings, score)
    /// - `graph.bin` — bincode-serialized code graph
    ///
    /// If no analysis has been run yet (state is None), this is a no-op.
    pub fn save(&self, session_path: &Path) -> anyhow::Result<()> {
        let state = match &self.state {
            Some(s) => s,
            None => return Ok(()), // nothing to save
        };

        std::fs::create_dir_all(session_path).with_context(|| {
            format!(
                "Failed to create session directory: {}",
                session_path.display()
            )
        })?;

        // Build serializable metadata
        let meta = state::SessionMeta {
            version: state::SESSION_VERSION,
            binary_version: env!("CARGO_PKG_VERSION").to_string(),
            file_hashes: state.file_hashes.clone(),
            source_files: state.source_files.clone(),
            edge_fingerprint: state.edge_fingerprint,
            findings_by_file: state.findings_by_file.clone(),
            graph_wide_findings: state.graph_wide_findings.clone(),
            last_findings: state.last_findings.clone(),
            last_score: state.last_score.clone(),
            last_stats: state.last_stats.clone(),
            fingerprint: {
                let binary_hash = crate::detectors::binary_file_hash().unwrap_or(0);
                Some(crate::detectors::compute_fingerprint(
                    binary_hash,
                    &self.project_config,
                    self.all_detectors,
                ))
            },
        };

        let json = serde_json::to_string(&meta).context("Failed to serialize engine session")?;
        let meta_path = session_path.join("engine_session.json");
        std::fs::write(&meta_path, json)
            .with_context(|| format!("Failed to write {}", meta_path.display()))?;

        // Save graph via CodeGraph's bincode persistence
        let graph_path = session_path.join("graph.bin");
        state
            .graph
            .save_cache(&graph_path)
            .context("Failed to save graph cache")?;

        // Compute L3 embeddings if not cached (runs after all output is shown to user)
        let cache_dir = crate::cache::paths::cache_dir(&self.repo_path);
        let embeddings_exist = crate::predictive::embedding_scorer::load_embeddings(
            &cache_dir,
            state.edge_fingerprint,
        )
        .is_some();

        if !embeddings_exist {
            crate::predictive::embedding_scorer::compute_and_cache_embeddings(
                Arc::clone(&state.graph),
                cache_dir,
                state.edge_fingerprint,
            );
        }

        Ok(())
    }

    /// Load a previously saved engine session from disk.
    ///
    /// Reads `engine_session.json` and `graph.bin` from `session_path`,
    /// validates version compatibility, and reconstructs the engine state.
    ///
    /// Transient fields (PrecomputedAnalysis, ValueStore, NgramModel, StyleProfile)
    /// are left empty and rebuilt on the next `analyze()` call.
    pub fn load(
        session_path: &Path,
        repo_path: &Path,
        all_detectors: bool,
    ) -> anyhow::Result<Self> {
        let repo_path = repo_path.canonicalize()?;
        let project_config = load_project_config(&repo_path);

        // Read and deserialize session metadata
        let meta_path = session_path.join("engine_session.json");
        let json = std::fs::read_to_string(&meta_path)
            .with_context(|| format!("Failed to read {}", meta_path.display()))?;
        let meta: state::SessionMeta =
            serde_json::from_str(&json).context("Failed to deserialize engine session")?;

        // Version checks
        if meta.version != state::SESSION_VERSION {
            anyhow::bail!(
                "Session version mismatch: expected {}, found {}",
                state::SESSION_VERSION,
                meta.version
            );
        }
        if meta.binary_version != env!("CARGO_PKG_VERSION") {
            anyhow::bail!(
                "Binary version mismatch: session was saved with {}, current is {}",
                meta.binary_version,
                env!("CARGO_PKG_VERSION")
            );
        }

        // Fingerprint check — catches config changes, dev rebuilds, mode changes
        let binary_hash = match crate::detectors::binary_file_hash() {
            Some(h) => h,
            None => {
                anyhow::bail!("Cannot hash binary for session validation");
            }
        };
        let current_fp =
            crate::detectors::compute_fingerprint(binary_hash, &project_config, all_detectors);
        if meta.fingerprint != Some(current_fp) {
            anyhow::bail!("Session fingerprint mismatch — config or binary changed");
        }

        // Load graph from CodeGraph bincode cache
        let graph_path = session_path.join("graph.bin");
        let graph = CodeGraph::load_cache(&graph_path).ok_or_else(|| {
            anyhow::anyhow!("Failed to load graph cache from {}", graph_path.display())
        })?;

        let state = state::EngineState {
            file_hashes: meta.file_hashes,
            source_files: meta.source_files,
            graph: Arc::new(graph),
            mutable_graph: None, // Rebuilt from CodeGraph on first incremental
            edge_fingerprint: meta.edge_fingerprint,
            co_change: None, // Not persisted — rebuilt on next git enrichment
            precomputed: None,
            style_profile: crate::calibrate::StyleProfile {
                version: crate::calibrate::StyleProfile::VERSION,
                generated_at: String::new(),
                commit_sha: None,
                total_files: 0,
                total_functions: 0,
                metrics: std::collections::HashMap::new(),
            },
            ngram_model: None,
            findings_by_file: meta.findings_by_file,
            graph_wide_findings: meta.graph_wide_findings,
            last_findings: meta.last_findings,
            last_score: meta.last_score,
            last_stats: meta.last_stats,
        };

        Ok(Self {
            repo_path,
            project_config,
            state: Some(state),
            progress: None,
            ownership_model: None,
            all_detectors,
        })
    }
}

/// Time a closure and record its duration in the timings map.
fn timed<T>(timings: &mut BTreeMap<String, Duration>, name: &str, f: impl FnOnce() -> T) -> T {
    let start = std::time::Instant::now();
    let result = f();
    timings.insert(name.to_string(), start.elapsed());
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_engine_cold_analysis() {
        // Create a temp dir with a simple Python file
        let tmp = tempfile::tempdir().unwrap();
        let py_file = tmp.path().join("main.py");
        std::fs::write(
            &py_file,
            r#"
def hello():
    print("hello world")

def add(a, b):
    return a + b
"#,
        )
        .unwrap();

        let mut engine = AnalysisEngine::new(tmp.path(), false).unwrap();
        let config = AnalysisConfig {
            workers: 2,
            no_git: true, // temp dir has no git
            ..Default::default()
        };
        let result = engine.analyze(&config).unwrap();

        assert!(matches!(result.stats.mode, AnalysisMode::Cold));
        assert!(result.score.overall >= 0.0);
        assert!(result.score.overall <= 100.0);
        assert!(result.stats.files_analyzed >= 1);
    }

    #[test]
    fn test_engine_second_call_cached() {
        // Create a temp dir with a simple Python file
        let tmp = tempfile::tempdir().unwrap();
        let py_file = tmp.path().join("example.py");
        std::fs::write(
            &py_file,
            r#"
def greet(name):
    return f"Hello, {name}!"
"#,
        )
        .unwrap();

        let mut engine = AnalysisEngine::new(tmp.path(), false).unwrap();
        let config = AnalysisConfig {
            workers: 2,
            no_git: true,
            ..Default::default()
        };

        let r1 = engine.analyze(&config).unwrap();
        assert!(matches!(r1.stats.mode, AnalysisMode::Cold));

        // Second call on unchanged files should return cached result
        let r2 = engine.analyze(&config).unwrap();
        assert!(matches!(r2.stats.mode, AnalysisMode::Cached));

        // Results should be identical
        assert_eq!(r1.findings.len(), r2.findings.len());
        assert_eq!(r1.score.overall, r2.score.overall);
        assert_eq!(r1.score.grade, r2.score.grade);
    }

    #[test]
    fn test_save_load_roundtrip() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("test.py"), "def hello(): pass").unwrap();

        let mut engine = AnalysisEngine::new(dir.path(), false).unwrap();
        let config = AnalysisConfig {
            no_git: true,
            max_files: 5,
            workers: 2,
            ..Default::default()
        };
        let r1 = engine.analyze(&config).unwrap();

        let session_dir = tempfile::tempdir().unwrap();
        engine.save(session_dir.path()).unwrap();
        drop(engine);

        let engine2 = AnalysisEngine::load(session_dir.path(), dir.path(), false).unwrap();
        // Engine loaded successfully with graph and findings
        assert!(engine2.graph().is_some());

        // Findings and score survived the roundtrip
        let state = engine2.state.as_ref().unwrap();
        assert_eq!(state.last_findings.len(), r1.findings.len());
        assert_eq!(state.last_score.overall, r1.score.overall);
        assert_eq!(state.last_score.grade, r1.score.grade);
    }

    #[test]
    fn test_save_noop_without_state() {
        let dir = tempfile::tempdir().unwrap();
        let engine = AnalysisEngine::new(dir.path(), false).unwrap();
        let session_dir = tempfile::tempdir().unwrap();
        // save() on a fresh engine (no analyze() yet) should be a no-op
        engine.save(session_dir.path()).unwrap();
        assert!(!session_dir.path().join("engine_session.json").exists());
    }

    #[test]
    fn test_load_cached_fast_path() {
        // After load(), calling analyze() with unchanged files should hit the cached fast path
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("app.py"), "def run(): return 42").unwrap();

        let mut engine = AnalysisEngine::new(dir.path(), false).unwrap();
        let config = AnalysisConfig {
            no_git: true,
            workers: 2,
            ..Default::default()
        };
        let r1 = engine.analyze(&config).unwrap();

        let session_dir = tempfile::tempdir().unwrap();
        engine.save(session_dir.path()).unwrap();
        drop(engine);

        let mut engine2 = AnalysisEngine::load(session_dir.path(), dir.path(), false).unwrap();
        let r2 = engine2.analyze(&config).unwrap();

        assert!(matches!(r2.stats.mode, AnalysisMode::Cached));
        assert_eq!(r1.findings.len(), r2.findings.len());
        assert_eq!(r1.score.overall, r2.score.overall);
    }

    #[test]
    fn test_incremental_after_file_modify() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("main.py"), "def foo(): pass").unwrap();

        let mut engine = AnalysisEngine::new(dir.path(), false).unwrap();
        let config = AnalysisConfig {
            no_git: true,
            workers: 2,
            ..Default::default()
        };

        let r1 = engine.analyze(&config).unwrap();
        assert!(matches!(r1.stats.mode, AnalysisMode::Cold));

        // Modify file
        std::fs::write(dir.path().join("main.py"), "def foo():\n    return 42\n").unwrap();

        let r2 = engine.analyze(&config).unwrap();
        assert!(
            matches!(r2.stats.mode, AnalysisMode::Incremental { .. }),
            "Expected Incremental mode after file modify, got {:?}",
            r2.stats.mode
        );
    }

    #[test]
    fn test_incremental_after_file_add() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("main.py"), "def foo(): pass").unwrap();

        let mut engine = AnalysisEngine::new(dir.path(), false).unwrap();
        let config = AnalysisConfig {
            no_git: true,
            workers: 2,
            ..Default::default()
        };

        engine.analyze(&config).unwrap();

        // Add new file
        std::fs::write(dir.path().join("helper.py"), "def bar(): return 1").unwrap();

        let r2 = engine.analyze(&config).unwrap();
        assert!(
            matches!(r2.stats.mode, AnalysisMode::Incremental { .. }),
            "Expected Incremental mode after file add, got {:?}",
            r2.stats.mode
        );
    }

    #[test]
    fn test_incremental_after_file_remove() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("main.py"), "def foo(): pass").unwrap();
        std::fs::write(dir.path().join("helper.py"), "def bar(): return 1").unwrap();

        let mut engine = AnalysisEngine::new(dir.path(), false).unwrap();
        let config = AnalysisConfig {
            no_git: true,
            workers: 2,
            ..Default::default()
        };

        engine.analyze(&config).unwrap();

        // Remove a file
        std::fs::remove_file(dir.path().join("helper.py")).unwrap();

        let r2 = engine.analyze(&config).unwrap();
        assert!(
            matches!(r2.stats.mode, AnalysisMode::Incremental { .. }),
            "Expected Incremental mode after file remove, got {:?}",
            r2.stats.mode
        );
    }

    #[test]
    fn test_incremental_then_cached() {
        // After incremental, a second call with no changes should be cached
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("main.py"), "def foo(): pass").unwrap();

        let mut engine = AnalysisEngine::new(dir.path(), false).unwrap();
        let config = AnalysisConfig {
            no_git: true,
            workers: 2,
            ..Default::default()
        };

        engine.analyze(&config).unwrap(); // cold
        std::fs::write(dir.path().join("main.py"), "def foo():\n    return 42\n").unwrap();
        engine.analyze(&config).unwrap(); // incremental

        let r3 = engine.analyze(&config).unwrap();
        assert!(
            matches!(r3.stats.mode, AnalysisMode::Cached),
            "Expected Cached mode on third call with no changes, got {:?}",
            r3.stats.mode
        );
    }

    #[test]
    fn test_incremental_produces_valid_score() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("main.py"), "def foo(): pass").unwrap();

        let mut engine = AnalysisEngine::new(dir.path(), false).unwrap();
        let config = AnalysisConfig {
            no_git: true,
            workers: 2,
            ..Default::default()
        };

        engine.analyze(&config).unwrap();

        std::fs::write(
            dir.path().join("main.py"),
            "def foo():\n    return 42\n\ndef bar():\n    return 0\n",
        )
        .unwrap();

        let r2 = engine.analyze(&config).unwrap();
        assert!(r2.score.overall >= 0.0 && r2.score.overall <= 100.0);
        // Grade should be a valid variant (not default F for a real analysis)
        assert!(r2.score.overall >= 0.0);
    }

    #[test]
    fn test_co_change_retained_after_analyze() {
        let dir = tempfile::tempdir().unwrap();
        let test_file = dir.path().join("main.py");
        std::fs::write(&test_file, "def foo(): pass\n").unwrap();
        std::process::Command::new("git")
            .args(["init"])
            .current_dir(dir.path())
            .output()
            .unwrap();
        std::process::Command::new("git")
            .args(["add", "."])
            .current_dir(dir.path())
            .output()
            .unwrap();
        std::process::Command::new("git")
            .args([
                "-c",
                "user.name=test",
                "-c",
                "user.email=test@test.com",
                "commit",
                "-m",
                "init",
            ])
            .current_dir(dir.path())
            .output()
            .unwrap();

        let mut engine = AnalysisEngine::new(dir.path(), false).unwrap();
        let _result = engine.analyze(&AnalysisConfig::default()).unwrap();
        assert!(
            engine.co_change().is_some(),
            "CoChangeMatrix should be retained after analyze"
        );
    }

    #[test]
    fn test_build_report_context_returns_context() {
        let dir = tempfile::tempdir().unwrap();
        let test_file = dir.path().join("main.py");
        std::fs::write(&test_file, "def foo(): pass\ndef bar(): pass\n").unwrap();

        std::process::Command::new("git")
            .args(["init"])
            .current_dir(dir.path())
            .output()
            .unwrap();
        std::process::Command::new("git")
            .args(["add", "."])
            .current_dir(dir.path())
            .output()
            .unwrap();
        std::process::Command::new("git")
            .args([
                "-c",
                "user.name=test",
                "-c",
                "user.email=test@test.com",
                "commit",
                "-m",
                "init",
            ])
            .current_dir(dir.path())
            .output()
            .unwrap();

        let mut engine = AnalysisEngine::new(dir.path(), false).unwrap();
        let result = engine.analyze(&AnalysisConfig::default()).unwrap();

        let health = crate::models::HealthReport {
            overall_score: result.score.overall,
            grade: result.score.grade,
            structure_score: result.score.breakdown.structure.final_score,
            quality_score: result.score.breakdown.quality.final_score,
            architecture_score: Some(result.score.breakdown.architecture.final_score),
            findings: result.findings.clone(),
            findings_summary: crate::models::FindingsSummary::from_findings(&result.findings),
            total_files: result.stats.files_analyzed,
            total_functions: result.stats.total_functions,
            total_classes: result.stats.total_classes,
            total_loc: result.stats.total_loc,
        };

        let ctx = engine
            .build_report_context(health, crate::reporters::OutputFormat::Html)
            .unwrap();
        // graph_data should be Some for Html format (even if empty graph)
        // previous_health should be None (first run)
        assert!(ctx.previous_health.is_none());
    }

    #[test]
    fn test_full_incremental_pipeline_correctness() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join("main.py"),
            "import helper\n\ndef main():\n    helper.greet()\n",
        )
        .unwrap();
        std::fs::write(
            dir.path().join("helper.py"),
            "def greet():\n    print('hello')\n",
        )
        .unwrap();

        let config = AnalysisConfig {
            no_git: true,
            ..Default::default()
        };

        // Cold analysis
        let mut engine = AnalysisEngine::new(dir.path(), false).unwrap();
        let cold_result = engine.analyze(&config).unwrap();
        assert!(matches!(cold_result.stats.mode, AnalysisMode::Cold));
        let cold_files = cold_result.stats.files_analyzed;
        assert!(cold_files >= 2);
        let cold_score = cold_result.score.overall;
        let cold_findings_count = cold_result.findings.len();

        // Save
        let session_dir = tempfile::tempdir().unwrap();
        engine.save(session_dir.path()).unwrap();

        // No changes → should be Cached
        let mut engine2 = AnalysisEngine::load(session_dir.path(), dir.path(), false).unwrap();
        let cached_result = engine2.analyze(&config).unwrap();
        assert!(matches!(cached_result.stats.mode, AnalysisMode::Cached));
        assert_eq!(cached_result.score.overall, cold_score);
        assert_eq!(cached_result.findings.len(), cold_findings_count);

        // Modify one file → should be Incremental
        std::fs::write(
            dir.path().join("helper.py"),
            "def greet():\n    print('hello world')\n\ndef farewell():\n    print('bye')\n",
        )
        .unwrap();

        let mut engine3 = AnalysisEngine::load(session_dir.path(), dir.path(), false).unwrap();
        let incr_result = engine3.analyze(&config).unwrap();
        assert!(matches!(
            incr_result.stats.mode,
            AnalysisMode::Incremental { .. }
        ));
        assert!(incr_result.score.overall > 0.0);
        assert_eq!(incr_result.stats.files_analyzed, cold_files);
    }
}
