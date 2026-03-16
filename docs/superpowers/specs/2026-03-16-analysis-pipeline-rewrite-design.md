# Analysis Pipeline Architectural Rewrite

**Date:** 2026-03-16
**Status:** Design approved, pending implementation plan
**Scope:** Full rewrite of the analyze pipeline into a layered engine architecture

## Problem Statement

The current analyze pipeline (`cli/analyze/mod.rs`) is a 1,316-line monolithic function with 22 parameters that tangles three concerns:

1. **Analysis** (produce findings + score)
2. **Presentation** (format, paginate, emoji, explain)
3. **Caching/persistence** (session save, incremental cache, background threads)

This causes concrete problems:

- **Divergent results across consumers.** The MCP server's `handle_analyze_oneshot()` builds its own `DetectorEngine`, skips postprocessing, scoring, and calibration entirely. The CLI and MCP server produce different results for the same repo.
- **Untestable orchestration.** The 22-parameter `run()` function cannot be unit tested. Adding a parameter requires threading it through every helper.
- **Blocked extensibility.** Adding a new consumer (web dashboard, IDE plugin) requires either calling the CLI function (inheriting its presentation concerns) or reimplementing the pipeline (creating another divergent codepath).
- **Fragile incremental path.** Session-based incremental analysis is a forked codepath inlined at the top of `run()`, not a transparent optimization layer. Adding pipeline stages means modifying both the cold and incremental paths.

### Current Consumers and Their Codepaths

| Consumer | Codepath | Postprocess? | Score? | Calibrate? |
|----------|----------|:---:|:---:|:---:|
| CLI `analyze` | `analyze::run()` (22 params) | Yes | Yes | Yes |
| MCP `analyze` (session) | `HandlerState::analyze_with_session()` | Partial | Yes | No |
| MCP `analyze` (oneshot) | `handle_analyze_oneshot()` | No | No | No |
| CLI `watch` | Calls `analyze::run()` | Yes | Yes | Yes |
| CLI `diff` | Reads cached findings | N/A | N/A | N/A |

The MCP oneshot path produces fundamentally different results from the CLI — no compound smell escalation, no confidence enrichment, no FP filtering, no adaptive thresholds.

## Architecture

### Layered Design

```
+--------------------------------------------------+
|  Consumers (CLI, MCP, Watch, IDE, Dashboard)      |  <-- Presentation
|  - Format findings (text, JSON, HTML, SARIF, MD)  |
|  - Filter by severity, confidence, pagination     |
|  - Apply ranking, emoji, explain-score             |
+--------------------------------------------------+
|  AnalysisEngine                                    |  <-- Orchestration
|  - Stateful (holds graph, precomputed data)        |
|  - analyze(config) -> AnalysisResult               |
|  - Cold / Incremental / Cached automatically       |
|  - Explicit persistence: save() / load()           |
+--------------------------------------------------+
|  Stages (pure functions)                           |  <-- Computation
|  - Collect -> Parse -> Graph -> Detect ->          |
|    Postprocess -> Score                             |
|  - Typed inputs/outputs, independently testable    |
|  - No engine state, no I/O side effects            |
+--------------------------------------------------+
```

### Design Principles

1. **Engine produces truth, consumers format it.** The engine returns ALL findings scored on ALL findings. Confidence filtering, severity filtering, pagination, ranking — all consumer-side. The health score is deterministic for a given codebase state, independent of display preferences.

2. **Stages are pure functions.** `fn(Input) -> Result<Output>`. No `&mut self`, no progress bars, no file I/O beyond reading source files. Each stage can be tested with synthetic inputs, no engine required.

3. **Incremental is transparent.** The engine decides whether to run cold, incremental, or cached based on internal state. The caller gets the same `AnalysisResult` regardless. There is no "incremental mode flag" — just state.

4. **Persistence is explicit.** `save()` writes state to disk. `load()` restores it. No hidden I/O in `analyze()`. The CLI saves after every run. The MCP server holds state in memory only. The watch command saves periodically. Each consumer chooses.

5. **One impure exception.** Git enrichment mutates graph nodes in place (additive metadata only, no topology changes). This is a documented pragmatic exception to avoid cloning the entire petgraph.

## Public API

### AnalysisConfig

Configuration for what analysis to perform. No presentation concerns.

```rust
pub struct AnalysisConfig {
    pub workers: usize,            // rayon parallelism (1-64)
    pub skip_detectors: Vec<String>, // detector names to exclude
    pub max_files: usize,          // 0 = unlimited
    pub no_git: bool,              // skip git enrichment
    pub verify: bool,              // LLM verification of findings
}
```

**What is NOT here:** `format`, `output_path`, `severity`, `top`, `page`, `per_page`, `no_emoji`, `explain_score`, `rank`, `export_training`, `timings`, `fail_on`, `min_confidence`, `show_all`, `since`. These are all presentation or consumer-specific concerns.

### AnalysisResult

The single return type. Contains everything a consumer needs.

```rust
pub struct AnalysisResult {
    pub findings: Vec<Finding>,    // ALL postprocessed findings, unfiltered
    pub score: ScoreResult,        // scored on ALL findings
    pub stats: AnalysisStats,      // mode, counts, timings
}

pub struct ScoreResult {
    pub overall: f64,              // 0-100, three-pillar weighted
    pub grade: String,             // A+ through F
    pub breakdown: ScoreBreakdown, // contains per-pillar PillarBreakdown with scores + details
}

pub enum AnalysisMode {
    Cold,
    Incremental { files_changed: usize },
    Cached,
}

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
```

### AnalysisEngine

Stateful engine. Holds graph and cached computations between calls.

```rust
pub struct AnalysisEngine { /* private fields */ }

impl AnalysisEngine {
    /// Create a fresh engine bound to a repository path.
    /// First analyze() call will be a cold run.
    pub fn new(repo_path: &Path) -> Result<Self>;

    /// Restore engine state from a previous save.
    /// First analyze() call will be incremental (or cached if nothing changed).
    pub fn load(session_path: &Path, repo_path: &Path) -> Result<Self>;

    /// Run analysis. Automatically cold, incremental, or cached based on state.
    /// Each call can use a different config (e.g., different skip_detectors).
    pub fn analyze(&mut self, config: &AnalysisConfig) -> Result<AnalysisResult>;

    /// Persist engine state for future restore via load().
    /// Does not modify the engine — pure I/O write.
    pub fn save(&self, path: &Path) -> Result<()>;

    /// Access the code graph (for consumers that need post-analysis graph queries).
    /// Returns None if analyze() has not been called yet.
    pub fn graph(&self) -> Option<&dyn GraphQuery>;

    /// Set a progress observer for UI feedback during analysis.
    /// CLI provides indicatif-based callbacks; MCP provides None.
    pub fn with_progress(self, progress: ProgressFn) -> Self;
}
```

**Key:** `repo_path` is a constructor argument, not config. It's immutable — it defines the engine's identity. `AnalysisConfig` is passed to `analyze()`, so each call can have different options. The engine internally diffs configs between calls to decide what needs re-running.

### OutputOptions (consumer-side)

Defined by consumers, not the engine. This is not part of the engine crate.

```rust
pub struct OutputOptions {
    pub format: OutputFormat,          // Text, Json, Html, Sarif, Markdown
    pub output_path: Option<PathBuf>,
    pub severity_filter: Option<Severity>,
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
    pub fail_on: Option<Severity>,
}
```

### Progress Reporting

Optional side-channel for UI feedback. Does not affect stage outputs.

```rust
pub type ProgressFn = Arc<dyn Fn(ProgressEvent) + Send + Sync>;

pub enum ProgressEvent {
    StageStarted { name: Cow<'static, str>, total: Option<usize> },
    StageProgress { current: usize },
    StageCompleted { name: Cow<'static, str>, duration: Duration },
}
```

## Stage Types

Each stage is a standalone public function with typed input and output structs. Stages have no knowledge of the engine, other stages, or persistence.

### Stage 1: Collect

Walks the repository, hashes files, returns the complete file manifest.

```rust
// src/engine/stages/collect.rs

pub struct CollectInput<'a> {
    pub repo_path: &'a Path,
    pub exclude_patterns: &'a [String],
    pub max_files: usize,
}

pub struct SourceFile {
    pub path: PathBuf,
    pub content_hash: u64,
}

pub struct CollectOutput {
    pub files: Vec<SourceFile>,
}

impl CollectOutput {
    pub fn all_paths(&self) -> Vec<PathBuf>;
}

pub fn collect_stage(input: &CollectInput) -> Result<CollectOutput>;
```

No tree-sitter, no graph. File walking uses the existing `ignore` crate for `.gitignore`-aware traversal, plus `.repotoireignore` and built-in exclusions (vendor, node_modules, dist). Content hashing uses SipHash (`DefaultHasher`) for consistency with the existing incremental cache.

### Stage 2: Parse

Runs tree-sitter parsers on source files in parallel via rayon.

```rust
// src/engine/stages/parse.rs

pub struct ParseInput {
    pub files: Vec<PathBuf>,
    pub workers: usize,
    pub progress: Option<ProgressFn>,
}

pub struct ParseOutput {
    pub results: Vec<(PathBuf, Arc<ParseResult>)>,
    pub stats: ParseStats,
}

pub struct ParseStats {
    pub files_parsed: usize,
    pub files_skipped: usize,   // >2MB guardrail
    pub total_functions: usize,
    pub total_classes: usize,
    pub total_loc: usize,
}

pub fn parse_stage(input: &ParseInput) -> Result<ParseOutput>;
```

Returns `Arc<ParseResult>` because graph building and calibration both consume parse results without cloning. On incremental runs, the engine calls this with only changed files. **Note:** `ParseStats` from incremental runs reflects only re-parsed files. The engine merges with cached `last_stats` from `EngineState` to produce correct `AnalysisStats` totals (total_functions, total_classes, total_loc reflect the full codebase, not just the delta).

### Stage 3: Graph

Builds the petgraph from parse results. Two functions, same output type.

```rust
// src/engine/stages/graph.rs

pub struct GraphInput<'a> {
    pub parse_results: &'a [(PathBuf, Arc<ParseResult>)],
    pub repo_path: &'a Path,
}

pub struct GraphOutput {
    pub graph: Arc<GraphStore>,
    pub value_store: Option<Arc<ValueStore>>,
    /// Edge fingerprint (hash of all cross-file edges) for topology change detection.
    /// Computed after graph construction/patching completes.
    pub edge_fingerprint: u64,
}

/// Build a graph from scratch (cold path).
pub fn graph_stage(input: &GraphInput) -> Result<GraphOutput>;

/// Patch an existing graph with delta changes (incremental path).
pub struct GraphPatchInput<'a> {
    pub graph: Arc<GraphStore>,
    pub changed_files: &'a [PathBuf],
    pub removed_files: &'a [PathBuf],
    pub new_parse_results: &'a [(PathBuf, Arc<ParseResult>)],
    pub repo_path: &'a Path,
}

pub fn graph_patch_stage(input: &GraphPatchInput) -> Result<GraphOutput>;
```

`graph_patch_stage` removes nodes/edges for changed and removed files via `remove_file_entities()`, inserts new nodes from re-parsed results, and re-resolves cross-file edges (calls, imports, inheritance). This mirrors the existing `AnalysisSession::update()` approach (session.rs lines 541-791). The engine decides which function to call — the consumer of `GraphOutput` doesn't know or care.

Both functions compute `edge_fingerprint` before returning, so the engine can detect topology changes.

`graph_stage` (full rebuild) is only used on cold runs (no prior state). All incremental runs use `graph_patch_stage`.

**Implementation prerequisites** (changes required to `GraphStore` before this stage works correctly):

1. **Resettable call maps cache.** `GraphStore.call_maps_cache` is currently `OnceLock<CallMapsRaw>` — once set, it can never be cleared. After `remove_file_entities()`, cached call maps reference removed `NodeIndex` values and miss newly added functions. **Fix:** Replace `OnceLock` with `RwLock<Option<CallMapsRaw>>` and clear it in `remove_file_entities()`. The same applies to any other `OnceLock`-cached derived data in `GraphStore` (`CachedGraphQuery` creates its own per-instance caches via `OnceLock` which is fine — those are ephemeral wrappers, not stored on `GraphStore`).

2. **Full-graph name resolution during patching.** The current `build_graph_from_parse_results()` builds `global_func_map` only from the provided parse results. When patching, this means short-name-to-qualified-name resolution fails for functions in unchanged files (e.g., `foo()` can't resolve to `module.Class.foo` if that file wasn't re-parsed). **Fix:** `graph_patch_stage` must build `global_func_map` from the existing graph's function nodes (`graph.get_functions()`) PLUS the new parse results' entities. This ensures call edges from changed files to unchanged files resolve correctly. The same applies to `module_lookup` for import resolution.

3. **Metrics cache clearing.** `GraphStore.metrics_cache` (DashMap) stores computed metrics (degree centrality, etc.) keyed by entity QN. After `remove_file_entities()`, stale entries remain for removed nodes. **Fix:** Clear entries for removed QNs in `remove_file_entities()`.

### Stage 4: Git Enrich (optional)

Enriches graph nodes with git history data. The one impure stage.

```rust
// src/engine/stages/git_enrich.rs

pub struct GitEnrichInput<'a> {
    pub repo_path: &'a Path,
    pub graph: &'a GraphStore,
}

pub struct GitEnrichOutput {
    pub functions_enriched: usize,
    pub classes_enriched: usize,
    pub cache_hits: usize,
}

impl GitEnrichOutput {
    pub fn skipped() -> Self;
}

/// Enriches graph nodes with churn, blame, last-modified data.
/// IMPURE: Mutates graph nodes in place (additive metadata only).
/// Must complete before detect_stage reads the graph.
pub fn git_enrich_stage(input: &GitEnrichInput) -> Result<GitEnrichOutput>;
```

Documented exception to stage purity. The mutation is additive-only (writes churn/blame metadata to existing nodes, never changes graph topology). The engine ensures this stage completes before detection starts.

### Stage 5: Calibrate

Learns the codebase's coding patterns. Produces adaptive thresholds and an n-gram language model.

```rust
// src/engine/stages/calibrate.rs

pub struct CalibrateInput<'a> {
    pub parse_results: &'a [(PathBuf, Arc<ParseResult>)],
    pub file_count: usize,
    pub repo_path: &'a Path,
}

pub struct CalibrateOutput {
    pub style_profile: StyleProfile,
    pub ngram_model: Option<NgramModel>,
}

pub fn calibrate_stage(input: &CalibrateInput) -> Result<CalibrateOutput>;
```

Independent of the graph. Runs in parallel with git enrichment. On incremental runs, the engine reuses cached calibration output (coding style doesn't change significantly from file-level edits).

### Stage 6: Detect

Builds detectors, precomputes shared data (taint, HMM, contexts), runs all detectors in parallel.

```rust
// src/engine/stages/detect.rs

pub struct DetectInput<'a> {
    pub graph: &'a dyn GraphQuery,
    pub source_files: &'a [PathBuf],
    pub repo_path: &'a Path,
    pub project_config: &'a ProjectConfig,
    pub style_profile: Option<&'a StyleProfile>,
    pub ngram_model: Option<&'a NgramModel>,
    pub value_store: Option<&'a Arc<ValueStore>>,
    pub skip_detectors: &'a [String],
    pub workers: usize,
    pub progress: Option<ProgressFn>,

    // Incremental optimization hints (engine provides these)
    pub changed_files: Option<&'a [PathBuf]>,
    pub topology_changed: bool,     // based on edge fingerprint comparison
    pub cached_gd_precomputed: Option<&'a GdPrecomputed>,
    pub cached_file_findings: Option<&'a HashMap<PathBuf, Vec<Finding>>>,
    pub cached_graph_wide_findings: Option<&'a HashMap<String, Vec<Finding>>>,
}

pub struct DetectOutput {
    pub findings: Vec<Finding>,
    pub gd_precomputed: GdPrecomputed,
    pub findings_by_file: HashMap<PathBuf, Vec<Finding>>,
    /// Keyed by detector name for selective invalidation on incremental runs.
    pub graph_wide_findings: HashMap<String, Vec<Finding>>,
    pub stats: DetectStats,
}

pub struct DetectStats {
    pub detectors_run: usize,
    pub detectors_skipped: usize,
    pub gi_findings: usize,
    pub gd_findings: usize,
    pub precompute_duration: Duration,
}

pub fn detect_stage(input: &DetectInput) -> Result<DetectOutput>;
```

**Incremental optimization logic (internal to stage):**

| Detector Scope | Files Unchanged | `topology_changed` | Action |
|---|:---:|:---:|---|
| `FileLocal` | Yes | - | Use `cached_file_findings` |
| `FileLocal` | No | - | Run on changed files only |
| `FileScopedGraph` | Yes | false | Use `cached_file_findings` |
| `FileScopedGraph` | - | true | Re-run all |
| `GraphWide` | - | true | Always re-run |
| `GraphWide` | - | false | Use `cached_graph_wide_findings` |

The detect stage decides — the engine just provides the hints. This keeps detector-scope intelligence inside the stage.

**Internal construction:** The detect stage internally builds `GdPrecomputed` via `precompute_gd_startup()` (or reuses `cached_gd_precomputed` if valid) and constructs an `AnalysisContext` from it. The `AnalysisContext` struct (12 fields: graph, files, functions, taint, detector_ctx, hmm_classifications, resolver, reachability, public_api, module_metrics, class_cohesion, decorator_index) is built inside the stage from the input fields + precomputed data. Detectors continue receiving `&AnalysisContext` via their existing `detect()` trait method — no changes to the Detector trait.

`findings_by_file` and `graph_wide_findings` (keyed by detector name) are returned separately so the engine can cache them for future incremental runs. When a graph-wide detector re-runs, its old findings are replaced by key.

### Stage 7: Postprocess

Pure finding transforms. No caching, no I/O, no presentation.

```rust
// src/engine/stages/postprocess.rs

pub struct PostprocessInput<'a> {
    pub findings: Vec<Finding>,
    pub project_config: &'a ProjectConfig,
    pub graph: &'a dyn GraphQuery,
    pub all_files: &'a [PathBuf],
    pub repo_path: &'a Path,
    pub verify: bool,
}

pub struct PostprocessOutput {
    pub findings: Vec<Finding>,
    pub stats: PostprocessStats,
}

pub struct PostprocessStats {
    pub input_count: usize,
    pub output_count: usize,
    pub suppressed: usize,
    pub excluded: usize,
    pub deduped: usize,
    pub fp_filtered: usize,
    pub security_downgraded: usize,
}

pub fn postprocess_stage(input: PostprocessInput) -> Result<PostprocessOutput>;
```

Takes ownership of `findings` (every sub-step mutates the Vec). The postprocess pipeline:

1. Assign deterministic IDs (SipHash of detector + file + line)
2. Assign default confidence by category
3. Confidence enrichment (contextual signals)
4. Detector overrides from project config
5. Path exclusion filtering
6. File-level suppression (`repotoire:ignore-file`)
7. Auto-suppress detector test fixtures
8. De-duplicate dead-code overlaps
9. Compound smell escalation
10. Security downgrading for non-production paths
11. FP classification filtering (GBDT or heuristic)
12. Confidence clamping to [0.0, 1.0]
13. LLM verification (optional, `--verify`)

**Removed from current postprocess (relocated):**
- Incremental cache update → engine's responsibility
- Max-files filtering → handled by collect stage
- Ranking → consumer-side presentation
- Min-confidence / show-all filtering → consumer-side presentation

### Stage 8: Score

Computes the three-pillar health score.

```rust
// src/engine/stages/score.rs

pub struct ScoreInput<'a> {
    pub graph: &'a dyn GraphQuery,
    pub findings: &'a [Finding],
    pub project_config: &'a ProjectConfig,
    pub repo_path: &'a Path,
    pub total_loc: usize,
}

/// Reuses ScoreResult from the public API — no separate internal type needed.
pub fn score_stage(input: &ScoreInput) -> Result<ScoreResult>;
```

Scored on ALL postprocessed findings. No confidence filtering — the score reflects the true codebase state. This is a behavior change from current code where `min_confidence` filtering happens before scoring.

## Engine Internals

### EngineState

Cached state from a previous analysis run. Everything needed for incremental analysis.

```rust
struct EngineState {
    // File tracking (for change detection)
    file_hashes: HashMap<PathBuf, u64>,
    source_files: Vec<PathBuf>,

    // Graph layer
    graph: Arc<GraphStore>,
    value_store: Option<Arc<ValueStore>>,

    // Graph topology fingerprint (hash of all cross-file edges).
    // Used to detect topology changes that require re-running
    // FileScopedGraph and GraphWide detectors, even when only
    // file content changed (e.g., adding an import statement).
    edge_fingerprint: u64,

    // Expensive precomputed data (~3.9s to rebuild).
    // Option because it's not persisted — rebuilt on first analyze() after load().
    gd_precomputed: Option<GdPrecomputed>,

    // Calibration (stable across incremental runs)
    style_profile: StyleProfile,
    ngram_model: Option<NgramModel>,

    // Previous findings (for incremental merge)
    findings_by_file: HashMap<PathBuf, Vec<Finding>>,
    /// Keyed by detector name for selective invalidation.
    graph_wide_findings: HashMap<String, Vec<Finding>>,

    // Previous results (for cached return)
    last_findings: Vec<Finding>,
    last_score: ScoreResult,
    last_stats: AnalysisStats,
}
```

**Note on parse results:** `EngineState` does NOT store parse results. On incremental runs, the engine re-parses only changed files and passes them to `graph_patch_stage`, which uses `remove_file_entities()` + re-insert (matching the existing `AnalysisSession` approach). The graph is self-sufficient for patching — it doesn't need parse results from unchanged files. After `load()`, the first `analyze()` call re-parses all files if needed (the graph is restored from redb, so graph patching works immediately for content-only changes).

### FileChanges

Internal diff between current and previous file state.

```rust
struct FileChanges {
    changed: Vec<PathBuf>,    // content hash differs
    added: Vec<PathBuf>,      // new file
    removed: Vec<PathBuf>,    // file gone
}

impl FileChanges {
    fn nothing_changed(&self) -> bool;
    fn is_delta(&self) -> bool;
    fn changed_and_added(&self) -> Vec<PathBuf>;
}
```

**Topology change detection** is NOT based on file adds/removes — it's based on the **edge fingerprint**. The engine computes the edge fingerprint AFTER graph patching (in the detect stage output) and compares it to `EngineState.edge_fingerprint`. A content-only change can alter topology (e.g., adding an import creates a new cross-file edge), and a file add may not (e.g., adding a standalone test file with no imports). The edge fingerprint captures actual topology changes precisely.

The engine passes `topology_changed: bool` to the detect stage as part of the incremental hints, computed by comparing fingerprints.

### analyze() Implementation

The engine's core method. Wires stages together, manages state transitions.

```rust
impl AnalysisEngine {
    pub fn analyze(&mut self, config: &AnalysisConfig) -> Result<AnalysisResult> {
        let mut timings = BTreeMap::new();

        // ── Stage 1: Collect ──────────────────────────────────
        let collect_out = timed(&mut timings, "collect", || {
            collect_stage(&CollectInput {
                repo_path: &self.repo_path,
                exclude_patterns: &self.project_config.exclude.effective_patterns(),
                max_files: config.max_files,
            })
        })?;

        // ── Diff against cached state ─────────────────────────
        let changes = self.diff_files(&collect_out);

        // Fast path: nothing changed, return cached result
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

        let is_incremental = self.state.is_some() && changes.is_delta();

        // ── Stage 2: Parse (only changed files if incremental) ─
        let files_to_parse = if is_incremental {
            changes.changed_and_added()
        } else {
            collect_out.all_paths()
        };

        let parse_out = timed(&mut timings, "parse", || {
            parse_stage(&ParseInput {
                files: files_to_parse,
                workers: config.workers,
                progress: self.progress.clone(),
            })
        })?;

        // ── Stage 3: Graph (build or patch) ────────────────────
        let graph_out = timed(&mut timings, "graph", || {
            if let Some(ref state) = self.state {
                // Incremental: patch existing graph (handles adds, removes, changes)
                graph_patch_stage(&GraphPatchInput {
                    graph: Arc::clone(&state.graph),
                    changed_files: &changes.changed,
                    removed_files: &changes.removed,
                    new_parse_results: &parse_out.results,
                    repo_path: &self.repo_path,
                })
            } else {
                // Cold: full build from all parse results
                graph_stage(&GraphInput {
                    parse_results: &parse_out.results,
                    repo_path: &self.repo_path,
                })
            }
        })?;

        // ── Stage 4: Git enrich (parallel with calibrate) ─────
        // Timed separately for accurate per-stage reporting.
        let git_handle = if config.no_git {
            None
        } else {
            let repo = self.repo_path.clone();
            let graph = Arc::clone(&graph_out.graph);
            Some(std::thread::spawn(move || {
                git_enrich_stage(&GitEnrichInput {
                    repo_path: &repo,
                    graph: &graph,
                })
            }))
        };

        // ── Stage 5: Calibrate ────────────────────────────────
        // Reuse cached style_profile if available. Always rebuild
        // ngram_model if missing (e.g., after load() from disk).
        let calibrate_out = timed(&mut timings, "calibrate", || {
            match &self.state {
                Some(state) if state.ngram_model.is_some() => {
                    // Both style_profile and ngram_model are cached — reuse
                    Ok(CalibrateOutput {
                        style_profile: state.style_profile.clone(),
                        ngram_model: state.ngram_model.clone(),
                    })
                }
                Some(state) => {
                    // style_profile cached but ngram_model missing (post-load)
                    // Rebuild ngram only; reuse style_profile
                    let full = calibrate_stage(&CalibrateInput {
                        parse_results: &parse_out.results,
                        file_count: collect_out.files.len(),
                        repo_path: &self.repo_path,
                    })?;
                    Ok(CalibrateOutput {
                        style_profile: state.style_profile.clone(),
                        ngram_model: full.ngram_model,
                    })
                }
                None => {
                    // Cold run — build everything
                    calibrate_stage(&CalibrateInput {
                        parse_results: &parse_out.results,
                        file_count: collect_out.files.len(),
                        repo_path: &self.repo_path,
                    })
                }
            }
        })?;

        // Wait for git enrichment to complete before detection
        if let Some(handle) = git_handle {
            let git_start = Instant::now();
            let _git_out = handle.join()
                .map_err(|_| anyhow!("git enrichment thread panicked"))??;
            timings.insert("git_enrich".into(), git_start.elapsed());
        }

        // ── Topology change detection ─────────────────────────
        // Compare edge fingerprint (computed by graph stage) to detect
        // topology changes from content edits (e.g., adding imports).
        let prev_fingerprint = self.state.as_ref().map(|s| s.edge_fingerprint);
        let topology_changed = prev_fingerprint
            .map(|prev| graph_out.edge_fingerprint != prev)
            .unwrap_or(true); // cold run = treat as changed

        // ── Stage 6: Detect ────────────────────────────────────
        // Bind changed_files to a local to avoid dangling temporary.
        let changed = changes.changed_and_added();
        let all_files = collect_out.all_paths();

        let detect_out = timed(&mut timings, "detect", || {
            detect_stage(&DetectInput {
                graph: graph_out.graph.as_ref(),
                source_files: &all_files,
                repo_path: &self.repo_path,
                project_config: &self.project_config,
                style_profile: Some(&calibrate_out.style_profile),
                ngram_model: calibrate_out.ngram_model.as_ref(),
                value_store: graph_out.value_store.as_ref(),
                skip_detectors: &config.skip_detectors,
                workers: config.workers,
                progress: self.progress.clone(),
                changed_files: if is_incremental { Some(&changed) } else { None },
                topology_changed,
                cached_gd_precomputed: self.state.as_ref()
                    .and_then(|s| s.gd_precomputed.as_ref()),
                cached_file_findings: self.state.as_ref()
                    .map(|s| &s.findings_by_file),
                cached_graph_wide_findings: self.state.as_ref()
                    .map(|s| &s.graph_wide_findings),
            })
        })?;

        // ── Stage 7: Postprocess ───────────────────────────────
        let postprocess_out = timed(&mut timings, "postprocess", || {
            postprocess_stage(PostprocessInput {
                findings: detect_out.findings,
                project_config: &self.project_config,
                graph: graph_out.graph.as_ref(),
                all_files: &all_files,
                repo_path: &self.repo_path,
                verify: config.verify,
            })
        })?;

        // ── Stage 8: Score ─────────────────────────────────────
        let score_out = timed(&mut timings, "score", || {
            score_stage(&ScoreInput {
                graph: graph_out.graph.as_ref(),
                findings: &postprocess_out.findings,
                project_config: &self.project_config,
                repo_path: &self.repo_path,
                total_loc: parse_out.stats.total_loc,
            })
        })?;

        // ── Update engine state ────────────────────────────────
        let stats = AnalysisStats {
            mode: if is_incremental {
                AnalysisMode::Incremental {
                    files_changed: changes.changed.len() + changes.added.len(),
                }
            } else {
                AnalysisMode::Cold
            },
            files_analyzed: collect_out.files.len(),
            total_functions: parse_out.stats.total_functions,
            total_classes: parse_out.stats.total_classes,
            total_loc: parse_out.stats.total_loc,
            detectors_run: detect_out.stats.detectors_run,
            findings_before_postprocess: postprocess_out.stats.input_count,
            findings_filtered: postprocess_out.stats.input_count
                - postprocess_out.stats.output_count,
            timings: timings.clone(),
        };

        self.state = Some(EngineState {
            file_hashes: collect_out.files.iter()
                .map(|f| (f.path.clone(), f.content_hash))
                .collect(),
            source_files: all_files,
            graph: graph_out.graph,
            value_store: graph_out.value_store,
            edge_fingerprint: graph_out.edge_fingerprint,
            gd_precomputed: Some(detect_out.gd_precomputed),
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
}
```

### Persistence (save/load)

```rust
impl AnalysisEngine {
    pub fn save(&self, path: &Path) -> Result<()> {
        let Some(ref state) = self.state else {
            return Ok(()); // nothing to save
        };

        // Serialize metadata (file hashes, findings, score, calibration)
        let meta = SessionMeta {
            version: SESSION_VERSION,
            binary_version: env!("CARGO_PKG_VERSION").to_string(),
            file_hashes: state.file_hashes.clone(),
            source_files: state.source_files.clone(),
            edge_fingerprint: state.edge_fingerprint,
            // Pre-postprocess findings (for incremental merge in detect stage)
            findings_by_file: state.findings_by_file.clone(),
            graph_wide_findings: state.graph_wide_findings.clone(),
            // Postprocessed findings (for cached return path — includes
            // confidence enrichment, FP filtering, compound escalation, etc.)
            last_findings: state.last_findings.clone(),
            last_score: state.last_score.clone(),
            last_stats: state.last_stats.clone(),
            style_profile: state.style_profile.clone(),
        };
        let meta_path = path.join("session.json");
        std::fs::write(&meta_path, serde_json::to_vec(&meta)?)?;

        // Persist graph via existing redb layer
        state.graph.save_graph_cache(path)?;

        Ok(())
    }

    pub fn load(session_path: &Path, repo_path: &Path) -> Result<Self> {
        let meta_path = session_path.join("session.json");
        let meta: SessionMeta = serde_json::from_slice(&std::fs::read(&meta_path)?)?;

        // Version check
        if meta.version != SESSION_VERSION
            || meta.binary_version != env!("CARGO_PKG_VERSION")
        {
            anyhow::bail!("Session cache version mismatch — cold analysis required");
        }

        // Restore graph (load_graph_cache returns Option<Self>)
        let graph = Arc::new(
            GraphStore::load_graph_cache(session_path)
                .ok_or_else(|| anyhow!("Graph cache missing or corrupt"))?
        );

        let project_config = load_project_config(repo_path);

        Ok(Self {
            repo_path: repo_path.to_path_buf(),
            project_config,
            state: Some(EngineState {
                file_hashes: meta.file_hashes,
                source_files: meta.source_files,
                graph,
                value_store: None,              // rebuilt on next analyze
                edge_fingerprint: meta.edge_fingerprint,
                gd_precomputed: None,           // rebuilt on next analyze
                style_profile: meta.style_profile,
                ngram_model: None,              // rebuilt on next analyze
                findings_by_file: meta.findings_by_file,
                graph_wide_findings: meta.graph_wide_findings,
                last_findings: meta.last_findings,
                last_score: meta.last_score,
                last_stats: meta.last_stats,
            }),
            progress: None,
        })
    }
}
```

**What is and isn't persisted:**

| Field | Persisted? | Rationale |
|-------|:---:|---------|
| file_hashes, source_files | Yes | Needed for change detection |
| graph | Yes | Via redb; expensive to rebuild |
| edge_fingerprint | Yes | Needed for topology change detection |
| findings_by_file, graph_wide_findings | Yes | Pre-postprocess; needed for incremental finding merge in detect stage |
| last_findings | Yes | Postprocessed findings; needed for cached return path (includes FP filtering, etc.) |
| last_score, last_stats | Yes | Needed for cached return path |
| style_profile | Yes | Stable across runs |
| gd_precomputed | No | ~3.9s to rebuild; complex Arc structures |
| ngram_model | No | Rebuilt from source files quickly |
| value_store | No | Rebuilt during graph stage |

After `load()`, the first `analyze()` call will be incremental (not cached) because `gd_precomputed` and `ngram_model` need rebuilding. If no files changed, the engine still re-runs calibration and detection precomputation, but reuses the cached graph and finding merge logic.

**Edge fingerprint after load:** The current `compute_edge_fingerprint()` hashes raw `lasso::Spur` u32 values which are process-local — the same string gets different u32 keys across process invocations. This means the persisted `edge_fingerprint` will never match post-load, causing the first run after load to always treat topology as "changed" and re-run all graph-dependent detectors. This is acceptable because `gd_precomputed` must be rebuilt anyway. **Future optimization:** Hash resolved string values instead of raw Spur keys for cross-process determinism.

## Consumer Examples

### CLI

```rust
// src/cli/analyze.rs — entire command handler (~30 lines)

pub fn run(path: &Path, config: AnalysisConfig, output: OutputOptions) -> Result<()> {
    let session_dir = cache_path(path).join("session");

    let mut engine = AnalysisEngine::load(&session_dir, path)
        .unwrap_or_else(|_| AnalysisEngine::new(path).unwrap())
        .with_progress(Arc::new(indicatif_progress()));

    let result = engine.analyze(&config)?;

    // Presentation — entirely the CLI's concern
    let filtered = apply_filters(&result.findings, &output);
    format_and_output(&filtered, &result.score, &result.stats, &output)?;

    // Persistence — explicit
    let _ = engine.save(&session_dir);

    // CI/CD threshold check
    check_fail_threshold(&output.fail_on, &result.score)?;

    Ok(())
}
```

### MCP Server

```rust
// src/mcp/state.rs

struct HandlerState {
    engine: AnalysisEngine,
    config: AnalysisConfig,
}

impl HandlerState {
    fn new(repo_path: &Path) -> Result<Self> {
        Ok(Self {
            engine: AnalysisEngine::new(repo_path)?,
            config: AnalysisConfig::default(),
        })
    }
}

// src/mcp/tools/analysis.rs

fn handle_analyze(state: &mut HandlerState) -> Result<Value> {
    let result = state.engine.analyze(&state.config)?;
    Ok(json!({
        "status": "completed",
        "total_findings": result.findings.len(),
        "health_score": result.score.overall,
        "grade": result.score.grade,
        "mode": format!("{:?}", result.stats.mode),
    }))
}

fn handle_get_findings(state: &HandlerState, params: &GetFindingsParams) -> Result<Value> {
    // Uses last analysis result — no re-analysis needed
    // ...
}

fn handle_query_graph(state: &HandlerState, params: &QueryGraphParams) -> Result<Value> {
    let graph = state.engine.graph()
        .ok_or_else(|| anyhow!("Run analyze first"))?;
    // Direct graph queries — no analysis needed
    // ...
}
```

### Watch Command

```rust
// src/cli/watch.rs

let session_dir = cache_path(path).join("session");
let mut engine = AnalysisEngine::load(&session_dir, path)
    .unwrap_or_else(|_| AnalysisEngine::new(path).unwrap())
    .with_progress(Arc::new(watch_progress()));

let mut last_save = Instant::now();

loop {
    wait_for_file_change(path)?;
    let result = engine.analyze(&config)?;  // automatically incremental
    print_delta(&result);

    // Persist periodically (every 5 minutes)
    if last_save.elapsed() > Duration::from_secs(300) {
        let _ = engine.save(&session_dir);
        last_save = Instant::now();
    }
}
```

## Behavior Changes

### Intentional

1. **Health score is now deterministic for a given codebase state.** Previously, `min_confidence` filtering happened before scoring, so the same repo could have different scores depending on a display preference. Now the score is computed on all findings; consumers filter at display time.

2. **MCP server produces identical results to CLI.** Previously the MCP oneshot path skipped postprocessing, scoring, and calibration. Now all consumers call the same engine.

3. **Incremental vs cold is invisible to consumers.** No `--incremental` flag, no "incremental mode." The engine decides based on state.

### Preserved

1. All 99 detectors, their behavior, and the `Detector` trait are unchanged.
2. Postprocessing pipeline (confidence enrichment, FP filtering, compound escalation, etc.) is preserved exactly, just relocated into a pure function stage.
3. Graph building, tree-sitter parsing, scoring algorithm — all unchanged.
4. Output formats (text, JSON, HTML, SARIF, Markdown) — unchanged, just called by consumers instead of the pipeline.
5. Inline suppression (`repotoire:ignore`) — preserved in postprocess stage.
6. Adaptive thresholds and n-gram calibration — preserved in calibrate stage.

## Module Layout

```
src/
├── engine/
│   ├── mod.rs              # AnalysisEngine, AnalysisConfig, AnalysisResult
│   ├── state.rs            # EngineState, SessionMeta, save/load
│   ├── diff.rs             # FileChanges, diff_files()
│   └── stages/
│       ├── mod.rs           # Stage module declarations
│       ├── collect.rs       # Stage 1: file walking + hashing
│       ├── parse.rs         # Stage 2: tree-sitter parsing
│       ├── graph.rs         # Stage 3: petgraph construction + patching
│       ├── git_enrich.rs    # Stage 4: git history enrichment (impure)
│       ├── calibrate.rs     # Stage 5: adaptive thresholds + n-gram
│       ├── detect.rs        # Stage 6: detector execution
│       ├── postprocess.rs   # Stage 7: finding transforms
│       └── score.rs         # Stage 8: health scoring
├── cli/
│   ├── analyze.rs           # ~30 lines: create engine, call analyze, format output
│   ├── output.rs            # OutputOptions, format_and_output, apply_filters
│   └── ...                  # other commands unchanged
├── mcp/
│   ├── state.rs             # HandlerState holds AnalysisEngine
│   └── tools/analysis.rs    # handle_analyze calls engine.analyze()
└── ...                      # detectors/, graph/, parsers/ — unchanged
```

## Testing Strategy

### Stage-Level Unit Tests

Each stage gets its own test module with synthetic inputs. No engine dependency required.

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_collect_respects_max_files() {
        let input = CollectInput {
            repo_path: &fixture_repo(),
            exclude_patterns: &[],
            max_files: 5,
        };
        let out = collect_stage(&input).unwrap();
        assert!(out.files.len() <= 5);
    }

    #[test]
    fn test_postprocess_assigns_deterministic_ids() {
        let findings = vec![/* synthetic findings */];
        let input1 = PostprocessInput { findings: findings.clone(), .. };
        let input2 = PostprocessInput { findings: findings.clone(), .. };
        let out1 = postprocess_stage(input1).unwrap();
        let out2 = postprocess_stage(input2).unwrap();
        // Two fresh runs on identical input produce identical IDs
        assert_eq!(out1.findings[0].id, out2.findings[0].id);
    }

    #[test]
    fn test_score_independent_of_finding_order() {
        let mut findings = vec![/* ... */];
        let s1 = score_stage(&ScoreInput { findings: &findings, .. }).unwrap();
        findings.reverse();
        let s2 = score_stage(&ScoreInput { findings: &findings, .. }).unwrap();
        assert_eq!(s1.overall, s2.overall);
    }
}
```

### Engine Integration Tests

Test the full pipeline with real repos.

```rust
#[test]
fn test_cold_then_incremental() {
    let repo = create_test_repo();
    let mut engine = AnalysisEngine::new(&repo).unwrap();

    let r1 = engine.analyze(&AnalysisConfig::default()).unwrap();
    assert!(matches!(r1.stats.mode, AnalysisMode::Cold));

    // No changes — cached
    let r2 = engine.analyze(&AnalysisConfig::default()).unwrap();
    assert!(matches!(r2.stats.mode, AnalysisMode::Cached));
    assert_eq!(r1.findings.len(), r2.findings.len());
    assert_eq!(r1.score.overall, r2.score.overall);

    // Modify a file — incremental
    modify_file(&repo, "main.py");
    let r3 = engine.analyze(&AnalysisConfig::default()).unwrap();
    assert!(matches!(r3.stats.mode, AnalysisMode::Incremental { .. }));
}

#[test]
fn test_save_load_roundtrip() {
    let repo = create_test_repo();
    let session_dir = tempdir();

    let mut engine = AnalysisEngine::new(&repo).unwrap();
    let r1 = engine.analyze(&AnalysisConfig::default()).unwrap();
    engine.save(&session_dir).unwrap();
    drop(engine);

    let mut engine = AnalysisEngine::load(&session_dir, &repo).unwrap();
    let r2 = engine.analyze(&AnalysisConfig::default()).unwrap();

    assert!(matches!(r2.stats.mode, AnalysisMode::Cached));
    assert_eq!(r1.score.overall, r2.score.overall);
}

#[test]
fn test_cli_and_mcp_produce_identical_results() {
    let repo = create_test_repo();
    let config = AnalysisConfig::default();

    // Simulate CLI
    let mut cli_engine = AnalysisEngine::new(&repo).unwrap();
    let cli_result = cli_engine.analyze(&config).unwrap();

    // Simulate MCP
    let mut mcp_engine = AnalysisEngine::new(&repo).unwrap();
    let mcp_result = mcp_engine.analyze(&config).unwrap();

    assert_eq!(cli_result.findings.len(), mcp_result.findings.len());
    assert_eq!(cli_result.score.overall, mcp_result.score.overall);
}
```

## Migration Path

### Phase 1: Create engine module with stage types (no behavior change)

- Create `src/engine/` with all stage input/output types
- Create stage functions that wrap existing code (delegate to current implementations)
- Create `AnalysisEngine` struct that calls stages
- All existing code continues working — engine is additive

### Phase 2: Wire CLI to use engine

- Replace `analyze::run()` 24-param function with engine-based implementation
- Create `OutputOptions` and move presentation logic to `cli/output.rs`
- Existing tests continue passing — same behavior, different wiring

### Phase 3: Wire MCP to use engine

- Replace `HandlerState::analyze_with_session()` with `AnalysisEngine`
- Remove `handle_analyze_oneshot()` — no longer needed
- MCP now produces identical results to CLI

### Phase 4: Extract stage implementations

- Move existing code from `cli/analyze/*.rs` into `engine/stages/*.rs`
- Make stages pure functions (remove progress bars, move caching out)
- Add per-stage unit tests

### Phase 5: Implement incremental optimizations

- File diffing in engine
- Edge fingerprint-based topology change detection
- Graph patching in graph stage (align with existing `AnalysisSession::update()` approach)
- Cached finding merge in detect stage
- Remove old `AnalysisSession` (replaced by engine state)
- Remove `IncrementalCache` (`detectors/incremental_cache.rs`) — superseded by `EngineState.findings_by_file` + `file_hashes`. Note: the first run after upgrading will be a cold run (old `IncrementalCache` JSON files are not migrated; they can be cleaned up via `repotoire clean`)

### Phase 6: Clean up

- Remove old `cli/analyze/` submodules (detect.rs, postprocess.rs, scoring.rs, etc.)
- Remove `session.rs` (replaced by engine state + persistence)
- Remove `skip_graph` CLI flag — no longer meaningful (the engine always builds a graph; consumers that want graph-less analysis can skip_detectors for graph-dependent ones)
- Update CLAUDE.md architecture documentation

## Thread Safety

`AnalysisEngine::analyze()` takes `&mut self`, ensuring single-writer access at the Rust type level. Internally, detection runs in parallel via rayon. For the MCP server (which uses tokio), the engine must be wrapped in a `Mutex` or `tokio::sync::Mutex` and accessed via `spawn_blocking` — matching the existing `HandlerState` pattern. This is documented as a constraint: **analysis is single-threaded at the engine level, internally parallel via rayon.**

## Scoring Behavior Change

Moving `min_confidence` filtering from pre-score to post-score is an intentional behavior change. Expected impact:

- Scores will generally decrease slightly (more findings counted in denominator)
- Estimated delta: 1-5 points for most repos (low-confidence findings are typically Low/Info severity with small penalty weights)
- No transition flag — the new behavior is strictly more correct (score reflects codebase state, not display preferences)
- Quantify actual delta during Phase 2 by running both old and new logic on Flask, FastAPI, Django benchmarks before merging

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Graph patching produces stale edges | Incorrect incremental results | Edge fingerprint comparison detects topology changes precisely; integration tests with add/remove/rename scenarios |
| Behavior change in scoring (no confidence pre-filter) | User-visible score changes (1-5 points) | Quantify on benchmarks before merging; document in changelog |
| Performance regression from stage boundary overhead | Slower cold analysis | Stage boundaries are just function calls — zero overhead; benchmark before/after |
| Large refactor scope causes regressions | Broken detectors or output | Phase-by-phase migration; each phase independently verifiable; existing test suite must pass at every phase |
| Serialization format change breaks cached sessions | Users must re-analyze | Bump SESSION_VERSION; clean error message on version mismatch |
| Memory pressure from EngineState in watch command | High RSS on large repos | EngineState does not hold parse results (unlike original design); graph + findings are the primary residents; monitor with 20k+ file repos |
| TOCTOU: file modified between collect (hash) and parse (read) | Stale hash, fresh content | Accepted risk — same as existing code; next analyze() will detect the hash change and re-process |
