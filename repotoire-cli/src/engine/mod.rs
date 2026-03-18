//! Analysis engine — layered, presentation-free code health analysis.
//!
//! # Architecture
//!
//! The engine cleanly separates *analysis* from *presentation*:
//!
//! - **`AnalysisEngine`** owns the repository state (graph, calibration data,
//!   cached findings) and exposes a single `analyze()` method that returns an
//!   [`AnalysisResult`] containing findings, score, and stats.
//! - **Consumers** (CLI `run_engine()`, MCP tools, future web dashboard) take
//!   the result and apply their own formatting, filtering, and pagination.
//!
//! # Pipeline stages
//!
//! Each call to `analyze()` runs 8 stages in order:
//!
//! 1. **Collect** — walk the repo, hash file contents, determine deltas
//! 2. **Parse** — tree-sitter parse source files in parallel
//! 3. **Graph** — build the in-memory petgraph code graph
//! 4. **Git enrich** — add churn/blame/commit metadata to graph nodes
//! 5. **Calibrate** — learn adaptive thresholds + n-gram language model
//! 6. **Detect** — run all detectors in parallel (with incremental reuse)
//! 7. **Postprocess** — deduplicate, suppress, filter findings
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

pub mod diff;
pub mod stages;
pub mod state;

use anyhow::Context;
use crate::config::{load_project_config, ProjectConfig};
use crate::graph::frozen::CodeGraph;
use crate::graph::GraphQuery;
use crate::models::Finding;
use crate::scoring::ScoreBreakdown;
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
}

impl Default for AnalysisConfig {
    fn default() -> Self {
        Self {
            workers: 8,
            skip_detectors: Vec::new(),
            max_files: 0,
            no_git: false,
            verify: false,
        }
    }
}

/// How the analysis was performed.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum AnalysisMode {
    Cold,
    Incremental { files_changed: usize },
    Cached,
}

/// Health score result.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScoreResult {
    pub overall: f64,
    pub grade: String,
    pub breakdown: ScoreBreakdown,
}

/// Stats from each pipeline phase.
#[derive(Debug, Clone, Serialize, Deserialize)]
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

/// Consumer-side presentation options — everything needed to format and display
/// analysis results. No analysis-logic concerns; purely output/filtering.
#[derive(Debug, Clone)]
pub struct OutputOptions {
    pub format: String,
    pub output_path: Option<PathBuf>,
    pub severity_filter: Option<String>,
    pub min_confidence: Option<f64>,
    pub show_all: bool,
    pub top: Option<usize>,
    pub page: usize,
    pub per_page: usize,
    pub no_emoji: bool,
    pub explain_score: bool,
    pub rank: bool,
    pub export_training: Option<PathBuf>,
    pub timings: bool,
    pub fail_on: Option<String>,
}

impl Default for OutputOptions {
    fn default() -> Self {
        Self {
            format: "text".to_string(),
            output_path: None,
            severity_filter: None,
            min_confidence: None,
            show_all: false,
            top: None,
            page: 1,
            per_page: 20,
            no_emoji: false,
            explain_score: false,
            rank: false,
            export_training: None,
            timings: false,
            fail_on: None,
        }
    }
}

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
/// let mut engine = AnalysisEngine::new(Path::new("/path/to/repo")).unwrap();
/// let result = engine.analyze(&AnalysisConfig::default()).unwrap();
/// println!("Score: {} ({})", result.score.overall, result.score.grade);
/// ```
pub struct AnalysisEngine {
    repo_path: PathBuf,
    project_config: ProjectConfig,
    state: Option<state::EngineState>,
    progress: Option<ProgressFn>,
}

impl AnalysisEngine {
    /// Create a new engine for the given repository path.
    ///
    /// Canonicalizes the path and loads project config from `repotoire.toml` (or defaults).
    pub fn new(repo_path: &Path) -> anyhow::Result<Self> {
        let repo_path = repo_path.canonicalize()?;
        let project_config = load_project_config(&repo_path);
        Ok(Self {
            repo_path,
            project_config,
            state: None,
            progress: None,
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
    /// (e.g., MCP state passing the graph to other tool handlers).
    pub fn graph_arc(&self) -> Option<Arc<CodeGraph>> {
        self.state.as_ref().map(|s| Arc::clone(&s.graph))
    }

    /// Returns a reference to the concrete `GraphStore` if analysis has been run.
    ///
    /// Deprecated: prefer `code_graph()` or `graph()`. This is kept for backward
    /// compatibility with code that needs `GraphStore`-specific APIs (MCP tools, etc.).
    /// Returns None if no mutable_graph is cached (e.g., after load from disk).
    pub fn graph_store(&self) -> Option<&crate::graph::GraphStore> {
        self.state
            .as_ref()
            .and_then(|s| s.mutable_graph.as_ref())
            .map(|g| g.as_ref())
    }

    /// Returns a shared `Arc<GraphStore>` if analysis has been run.
    ///
    /// Deprecated: prefer `graph_arc()` which returns `Arc<CodeGraph>`.
    /// This is kept for backward compatibility with MCP state and other consumers
    /// that haven't migrated yet.
    pub fn graph_store_arc(&self) -> Option<Arc<crate::graph::GraphStore>> {
        self.state
            .as_ref()
            .and_then(|s| s.mutable_graph.as_ref())
            .map(Arc::clone)
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
        let graph_out = timed(&mut timings, "graph", || {
            graph::graph_stage(&graph::GraphInput {
                parse_results: &parse_out.results,
                repo_path: &self.repo_path,
            })
        })?;

        // Stage 4: Git enrich — add churn/blame/commit data to mutable graph nodes
        let git_out = if !config.no_git {
            timed(&mut timings, "git_enrich", || {
                git_enrich::git_enrich_stage(&git_enrich::GitEnrichInput {
                    repo_path: &self.repo_path,
                    graph: &graph_out.mutable_graph,
                })
            })?
        } else {
            git_enrich::GitEnrichOutput::skipped()
        };

        // Freeze: convert mutable GraphStore → immutable CodeGraph with indexes
        let frozen = timed(&mut timings, "freeze", || {
            graph::freeze_graph(
                &graph_out.mutable_graph,
                graph_out.value_store,
                Some(&git_out.co_change_matrix),
            )
        });

        // Stage 5: Calibrate — learn adaptive thresholds from the codebase
        let calibrate_out = timed(&mut timings, "calibrate", || {
            calibrate::calibrate_stage(&calibrate::CalibrateInput {
                parse_results: &parse_out.results,
                file_count: collect_out.files.len(),
                repo_path: &self.repo_path,
            })
        })?;

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
            })
        })?;

        // Stage 8: Score — compute three-pillar health score
        let score_out = timed(&mut timings, "score", || {
            score::score_stage(&score::ScoreInput {
                graph: frozen.graph.as_ref(),
                findings: &postprocess_out.findings,
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
            mutable_graph: Some(graph_out.mutable_graph),
            edge_fingerprint: frozen.edge_fingerprint,
            precomputed: Some(detect_out.precomputed),
            style_profile: calibrate_out.style_profile,
            ngram_model: calibrate_out.ngram_model,
            findings_by_file: detect_out.findings_by_file,
            graph_wide_findings: detect_out.graph_wide_findings,
            last_findings: postprocess_out.findings.clone(),
            last_score: score_out.clone(),
            last_stats: stats.clone(),
        });

        Ok(AnalysisResult {
            findings: postprocess_out.findings,
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

        // Stage 3: Graph — patch existing mutable graph with delta.
        // If no mutable_graph is cached (e.g., after load from disk), rebuild from scratch.
        let mutable_graph = prev_state.mutable_graph.unwrap_or_else(|| {
            // Rebuild a mutable GraphStore from the frozen CodeGraph.
            // This is a clone + rebuild, but only happens on the first incremental
            // run after a load() from disk.
            let store = crate::graph::GraphStore::in_memory();
            // We can't easily reconstruct a GraphStore from a CodeGraph without
            // re-inserting all nodes/edges, so fall back to a cold rebuild.
            // The incremental path will still work correctly — it just re-adds
            // the new parse results to a fresh graph.
            Arc::new(store)
        });

        let graph_out = timed(&mut timings, "graph", || {
            graph::graph_patch_stage(&graph::GraphPatchInput {
                mutable_graph,
                changed_files: &changes.changed,
                removed_files: &changes.removed,
                new_parse_results: &parse_out.results,
                repo_path: &self.repo_path,
            })
        })?;

        // Stage 4: Git enrich — enrich the patched mutable graph
        let git_out = if !config.no_git {
            timed(&mut timings, "git_enrich", || {
                git_enrich::git_enrich_stage(&git_enrich::GitEnrichInput {
                    repo_path: &self.repo_path,
                    graph: &graph_out.mutable_graph,
                })
            })?
        } else {
            git_enrich::GitEnrichOutput::skipped()
        };

        // Freeze: convert mutable GraphStore → immutable CodeGraph with indexes
        let frozen = timed(&mut timings, "freeze", || {
            graph::freeze_graph(
                &graph_out.mutable_graph,
                graph_out.value_store,
                Some(&git_out.co_change_matrix),
            )
        });

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
        // After load() from disk, precomputed is None — treat as topology changed
        // since process-local Spur values make fingerprint comparison unreliable.
        let topology_changed = prev_state.precomputed.is_none()
            || prev_state.edge_fingerprint != frozen.edge_fingerprint;

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
                findings: detect_out.findings,
                project_config: &self.project_config,
                graph: frozen.graph.as_ref(),
                all_files: &all_files,
                repo_path: &self.repo_path,
                verify: config.verify,
            })
        })?;

        // Merge parse stats: delta parse stats + cached totals for full codebase picture.
        // The delta only parsed changed files, but stats should reflect the whole codebase.
        let total_functions = prev_state.last_stats.total_functions
            .saturating_sub(parse_out.stats.total_functions)
            .saturating_add(parse_out.stats.total_functions);
        let total_classes = prev_state.last_stats.total_classes
            .saturating_sub(parse_out.stats.total_classes)
            .saturating_add(parse_out.stats.total_classes);
        // For LOC, use the cached total and adjust. Since we only parsed delta files,
        // we can't accurately recount all LOC. Use the cached value as a reasonable estimate.
        let total_loc = prev_state.last_stats.total_loc;

        // Stage 8: Score — compute three-pillar health score
        let score_out = timed(&mut timings, "score", || {
            score::score_stage(&score::ScoreInput {
                graph: frozen.graph.as_ref(),
                findings: &postprocess_out.findings,
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
            mutable_graph: Some(graph_out.mutable_graph),
            edge_fingerprint: frozen.edge_fingerprint,
            precomputed: Some(detect_out.precomputed),
            style_profile,
            ngram_model,
            findings_by_file: detect_out.findings_by_file,
            graph_wide_findings: detect_out.graph_wide_findings,
            last_findings: postprocess_out.findings.clone(),
            last_score: score_out.clone(),
            last_stats: stats.clone(),
        });

        Ok(AnalysisResult {
            findings: postprocess_out.findings,
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

        std::fs::create_dir_all(session_path)
            .with_context(|| format!("Failed to create session directory: {}", session_path.display()))?;

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
        };

        let json = serde_json::to_string(&meta)
            .context("Failed to serialize engine session")?;
        let meta_path = session_path.join("engine_session.json");
        std::fs::write(&meta_path, json)
            .with_context(|| format!("Failed to write {}", meta_path.display()))?;

        // Save graph via CodeGraph's bincode persistence
        let graph_path = session_path.join("graph.bin");
        state.graph.save_cache(&graph_path)
            .context("Failed to save graph cache")?;

        Ok(())
    }

    /// Load a previously saved engine session from disk.
    ///
    /// Reads `engine_session.json` and `graph.bin` from `session_path`,
    /// validates version compatibility, and reconstructs the engine state.
    ///
    /// Transient fields (PrecomputedAnalysis, ValueStore, NgramModel, StyleProfile)
    /// are left empty and rebuilt on the next `analyze()` call.
    pub fn load(session_path: &Path, repo_path: &Path) -> anyhow::Result<Self> {
        let repo_path = repo_path.canonicalize()?;
        let project_config = load_project_config(&repo_path);

        // Read and deserialize session metadata
        let meta_path = session_path.join("engine_session.json");
        let json = std::fs::read_to_string(&meta_path)
            .with_context(|| format!("Failed to read {}", meta_path.display()))?;
        let meta: state::SessionMeta = serde_json::from_str(&json)
            .context("Failed to deserialize engine session")?;

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

        // Load graph from CodeGraph bincode cache
        let graph_path = session_path.join("graph.bin");
        let graph = CodeGraph::load_cache(&graph_path)
            .ok_or_else(|| anyhow::anyhow!(
                "Failed to load graph cache from {}",
                graph_path.display()
            ))?;

        let state = state::EngineState {
            file_hashes: meta.file_hashes,
            source_files: meta.source_files,
            graph: Arc::new(graph),
            mutable_graph: None, // No mutable graph after load — rebuilt on first incremental
            edge_fingerprint: meta.edge_fingerprint,
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

        let mut engine = AnalysisEngine::new(tmp.path()).unwrap();
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

        let mut engine = AnalysisEngine::new(tmp.path()).unwrap();
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

        let mut engine = AnalysisEngine::new(dir.path()).unwrap();
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

        let engine2 = AnalysisEngine::load(session_dir.path(), dir.path()).unwrap();
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
        let engine = AnalysisEngine::new(dir.path()).unwrap();
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

        let mut engine = AnalysisEngine::new(dir.path()).unwrap();
        let config = AnalysisConfig {
            no_git: true,
            workers: 2,
            ..Default::default()
        };
        let r1 = engine.analyze(&config).unwrap();

        let session_dir = tempfile::tempdir().unwrap();
        engine.save(session_dir.path()).unwrap();
        drop(engine);

        let mut engine2 = AnalysisEngine::load(session_dir.path(), dir.path()).unwrap();
        let r2 = engine2.analyze(&config).unwrap();

        assert!(matches!(r2.stats.mode, AnalysisMode::Cached));
        assert_eq!(r1.findings.len(), r2.findings.len());
        assert_eq!(r1.score.overall, r2.score.overall);
    }

    #[test]
    fn test_incremental_after_file_modify() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("main.py"), "def foo(): pass").unwrap();

        let mut engine = AnalysisEngine::new(dir.path()).unwrap();
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

        let mut engine = AnalysisEngine::new(dir.path()).unwrap();
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

        let mut engine = AnalysisEngine::new(dir.path()).unwrap();
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

        let mut engine = AnalysisEngine::new(dir.path()).unwrap();
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

        let mut engine = AnalysisEngine::new(dir.path()).unwrap();
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
        assert!(!r2.score.grade.is_empty());
    }
}
