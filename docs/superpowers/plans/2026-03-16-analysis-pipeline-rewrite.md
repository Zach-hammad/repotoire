# Analysis Pipeline Architectural Rewrite — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the monolithic 22-parameter `analyze::run()` function with a layered `AnalysisEngine` backed by 8 pure-function stages, so CLI, MCP, watch, and future consumers all share one analysis codepath.

**Architecture:** Stateful `AnalysisEngine` holds graph + precomputed data between calls. Each `analyze()` call orchestrates 8 typed stages (collect → parse → graph → git_enrich → calibrate → detect → postprocess → score). The engine handles cold/incremental/cached paths transparently. Consumers receive `AnalysisResult` and apply their own filtering/formatting.

**Tech Stack:** Rust, petgraph, rayon, redb, tree-sitter, serde/serde_json, anyhow

**Spec:** `docs/superpowers/specs/2026-03-16-analysis-pipeline-rewrite-design.md`

---

## File Structure

### New files to create

```
repotoire-cli/src/engine/
├── mod.rs              # AnalysisEngine, AnalysisConfig, AnalysisResult, ScoreResult,
│                       # AnalysisStats, AnalysisMode, ProgressFn, ProgressEvent
├── state.rs            # EngineState (private), SessionMeta, save/load logic
├── diff.rs             # FileChanges, diff_files()
└── stages/
    ├── mod.rs          # Re-exports all stage types
    ├── collect.rs      # CollectInput, CollectOutput, SourceFile, collect_stage()
    ├── parse.rs        # ParseInput, ParseOutput, ParseStats, parse_stage()
    ├── graph.rs        # GraphInput, GraphOutput, GraphPatchInput, graph_stage(), graph_patch_stage()
    ├── git_enrich.rs   # GitEnrichInput, GitEnrichOutput, git_enrich_stage()
    ├── calibrate.rs    # CalibrateInput, CalibrateOutput, calibrate_stage()
    ├── detect.rs       # DetectInput, DetectOutput, DetectStats, detect_stage()
    ├── postprocess.rs  # PostprocessInput, PostprocessOutput, PostprocessStats, postprocess_stage()
    └── score.rs        # ScoreInput, score_stage() (returns ScoreResult from mod.rs)
```

### Files to modify

| File | Change |
|------|--------|
| `repotoire-cli/src/lib.rs` or `main.rs` | Add `pub mod engine;` |
| `repotoire-cli/src/graph/store/mod.rs` | Replace `OnceLock<CallMapsRaw>` with resettable `RwLock<Option<CallMapsRaw>>`; clear caches in `remove_file_entities()` |
| `repotoire-cli/src/cli/mod.rs:515-540` | Replace `analyze::run(22 args)` call with engine-based dispatch |
| `repotoire-cli/src/cli/analyze/mod.rs` | Rewrite `run()` to ~30 lines: create engine, call `analyze()`, format output |
| `repotoire-cli/src/cli/analyze/files.rs` | Change `pub(crate) fn collect_file_list` to `pub fn collect_file_list` |
| `repotoire-cli/src/cli/analyze/postprocess.rs` | Change `pub(super)` → `pub` on `postprocess_findings`; remove incremental cache, max_files, rank params |
| `repotoire-cli/src/cli/analyze/scoring.rs` | Change `pub(super)` → `pub` on `calculate_scores` |
| `repotoire-cli/src/cli/analyze/detect.rs` | Change `pub(super)` → `pub` on key functions |
| `repotoire-cli/src/mcp/state.rs` | Replace `session: Option<AnalysisSession>` with `engine: AnalysisEngine` |
| `repotoire-cli/src/mcp/tools/analysis.rs` | Rewrite `handle_analyze` to use engine; remove `handle_analyze_oneshot` |

### Files to remove (Phase 6, after full cutover)

- `repotoire-cli/src/session.rs` (replaced by `engine/state.rs`)
- `repotoire-cli/src/detectors/incremental_cache.rs` (replaced by `EngineState`)

---

## Chunk 1: GraphStore Prerequisites

These changes must land first — they make `remove_file_entities()` safe for incremental graph patching by clearing stale caches.

### Task 1: Replace OnceLock with resettable cache in GraphStore

**Files:**
- Modify: `repotoire-cli/src/graph/store/mod.rs`

- [ ] **Step 1: Write test for cache invalidation**

Add to the existing `#[cfg(test)]` module in `store/mod.rs`:

```rust
#[test]
fn test_call_maps_cache_invalidated_after_remove() {
    let store = GraphStore::in_memory();
    // Add two files with a cross-file call
    let file_a = store.intern("a.py");
    let file_b = store.intern("b.py");
    let fn_a = store.intern("a.foo");
    let fn_b = store.intern("b.bar");

    store.add_nodes_batch(vec![
        CodeNode::function(fn_a, file_a, 1, 5),
        CodeNode::function(fn_b, file_b, 1, 5),
    ]);
    store.add_edges_batch(vec![
        CodeEdge::new(fn_a, fn_b, EdgeKind::Calls),
    ]);

    // Warm the call maps cache
    let maps1 = store.build_call_maps_raw();
    assert!(maps1.0.contains_key(&fn_a)); // fn_a in qn_to_idx

    // Remove file_b entities
    store.remove_file_entities(&[std::path::PathBuf::from("b.py")]);

    // Cache must reflect the removal
    let maps2 = store.build_call_maps_raw();
    assert!(!maps2.0.contains_key(&fn_b)); // fn_b gone
}
```

- [ ] **Step 2: Run test — expect FAIL (stale OnceLock)**

```bash
cargo test test_call_maps_cache_invalidated_after_remove -- --nocapture
```

Expected: FAIL — `fn_b` still in `qn_to_idx` because `OnceLock` is never cleared.

- [ ] **Step 3: Replace OnceLock with RwLock\<Option\<...\>\>**

In `repotoire-cli/src/graph/store/mod.rs`, change the field declaration:

```rust
// Before:
call_maps_cache: OnceLock<CallMapsRaw>,

// After:
call_maps_cache: RwLock<Option<CallMapsRaw>>,
```

Update all constructors (`new()`, `in_memory()`, `load_graph_cache()`):

```rust
// Before:
call_maps_cache: OnceLock::new(),

// After:
call_maps_cache: RwLock::new(None),
```

Update `build_call_maps_raw()` (around line 772):

```rust
// Before:
if let Some(cached) = self.call_maps_cache.get() {
    return cached.clone();
}
// ... compute result ...
let _ = self.call_maps_cache.set(result.clone());
result

// After:
{
    let cache = self.call_maps_cache.read().unwrap();
    if let Some(cached) = cache.as_ref() {
        return cached.clone();
    }
}
// ... compute result (unchanged) ...
{
    let mut cache = self.call_maps_cache.write().unwrap();
    *cache = Some(result.clone());
}
result
```

- [ ] **Step 4: Clear caches in remove_file_entities()**

In `remove_file_entities()` (line 1389), add cache invalidation at the end of the method:

```rust
// After existing node/edge removal logic, before method returns:

// Invalidate cached call maps — they reference removed NodeIndex values
{
    let mut cache = self.call_maps_cache.write().unwrap();
    *cache = None;
}

// Clear metrics for removed qualified names
for qn_key in &removed_qn_keys {
    self.metrics_cache.retain(|k, _| !k.contains(&interner.resolve(*qn_key)));
}
```

Where `removed_qn_keys` is collected during the removal loop (collect the `qualified_name` StrKey of each removed node before removing it).

- [ ] **Step 5: Run test — expect PASS**

```bash
cargo test test_call_maps_cache_invalidated_after_remove -- --nocapture
```

- [ ] **Step 6: Run full test suite**

```bash
cargo test
```

All existing tests must pass — the cache behavior change is transparent to callers.

- [ ] **Step 7: Commit**

```bash
git add repotoire-cli/src/graph/store/mod.rs
git commit -m "refactor: replace OnceLock with resettable RwLock for call maps cache

Prerequisite for incremental graph patching. OnceLock cannot be cleared
after remove_file_entities(), causing stale call maps on incremental runs.
RwLock<Option<...>> is cleared in remove_file_entities() alongside
metrics_cache entries for removed nodes."
```

---

## Chunk 2: Engine Types & Public API

Create the engine module with all type definitions. No logic yet — just types, constructors, and Default impls.

### Task 2: Create engine module structure

**Files:**
- Create: `repotoire-cli/src/engine/mod.rs`
- Create: `repotoire-cli/src/engine/stages/mod.rs`
- Create: `repotoire-cli/src/engine/state.rs`
- Create: `repotoire-cli/src/engine/diff.rs`
- Modify: `repotoire-cli/src/lib.rs` (or wherever top-level modules are declared)

- [ ] **Step 1: Create engine/mod.rs with public API types**

```rust
//! Analysis engine — layered architecture for code health analysis.
//!
//! The engine produces analysis results; consumers format them.
//! Stateful: holds graph + precomputed data between calls.

pub mod diff;
pub mod stages;
pub mod state;

use crate::models::Finding;
use crate::scoring::ScoreBreakdown;
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
#[derive(Debug, Clone)]
pub enum AnalysisMode {
    Cold,
    Incremental { files_changed: usize },
    Cached,
}

/// Health score result.
#[derive(Debug, Clone)]
pub struct ScoreResult {
    pub overall: f64,
    pub grade: String,
    pub breakdown: ScoreBreakdown,
}

/// Stats from each pipeline phase.
#[derive(Debug, Clone)]
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
    StageStarted { name: Cow<'static, str>, total: Option<usize> },
    StageProgress { current: usize },
    StageCompleted { name: Cow<'static, str>, duration: Duration },
}

/// Progress callback type.
pub type ProgressFn = Arc<dyn Fn(ProgressEvent) + Send + Sync>;
```

- [ ] **Step 2: Create engine/stages/mod.rs with re-exports**

```rust
//! Pure function stages for the analysis pipeline.
//!
//! Each stage: `fn(Input) -> Result<Output>`.
//! No engine state, no I/O side effects, independently testable.

pub mod calibrate;
pub mod collect;
pub mod detect;
pub mod git_enrich;
pub mod graph;
pub mod parse;
pub mod postprocess;
pub mod score;
```

- [ ] **Step 3: Create engine/state.rs (empty struct)**

```rust
//! Internal engine state — cached between analyze() calls.

use crate::calibrate::{NgramModel, StyleProfile};
use crate::detectors::GdPrecomputed;
use crate::graph::GraphStore;
use crate::models::Finding;
use crate::values::store::ValueStore;
use super::{AnalysisStats, ScoreResult};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

/// Cached state from a previous analysis run.
pub(crate) struct EngineState {
    pub file_hashes: HashMap<PathBuf, u64>,
    pub source_files: Vec<PathBuf>,
    pub graph: Arc<GraphStore>,
    pub value_store: Option<Arc<ValueStore>>,
    pub edge_fingerprint: u64,
    pub gd_precomputed: Option<GdPrecomputed>,
    pub style_profile: StyleProfile,
    pub ngram_model: Option<NgramModel>,
    pub findings_by_file: HashMap<PathBuf, Vec<Finding>>,
    pub graph_wide_findings: HashMap<String, Vec<Finding>>,
    pub last_findings: Vec<Finding>,
    pub last_score: ScoreResult,
    pub last_stats: AnalysisStats,
}
```

- [ ] **Step 4: Create engine/diff.rs**

```rust
//! File change detection between analysis runs.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use super::stages::collect::{CollectOutput, SourceFile};

/// Diff between current and previous file state.
pub(crate) struct FileChanges {
    pub changed: Vec<PathBuf>,
    pub added: Vec<PathBuf>,
    pub removed: Vec<PathBuf>,
}

impl FileChanges {
    pub fn nothing_changed(&self) -> bool {
        self.changed.is_empty() && self.added.is_empty() && self.removed.is_empty()
    }

    pub fn is_delta(&self) -> bool {
        !self.nothing_changed()
    }

    pub fn changed_and_added(&self) -> Vec<PathBuf> {
        self.changed.iter().chain(self.added.iter()).cloned().collect()
    }

    /// Compute diff from previous hashes and current collect output.
    pub fn compute(
        prev_hashes: &HashMap<PathBuf, u64>,
        current: &CollectOutput,
    ) -> Self {
        let mut changed = Vec::new();
        let mut added = Vec::new();
        let current_map: HashMap<&Path, u64> = current.files.iter()
            .map(|f| (f.path.as_path(), f.content_hash))
            .collect();

        for sf in &current.files {
            match prev_hashes.get(&sf.path) {
                Some(&old_hash) if old_hash != sf.content_hash => {
                    changed.push(sf.path.clone());
                }
                None => added.push(sf.path.clone()),
                _ => {}
            }
        }

        let removed: Vec<PathBuf> = prev_hashes.keys()
            .filter(|p| !current_map.contains_key(p.as_path()))
            .cloned()
            .collect();

        Self { changed, added, removed }
    }

    /// Compute for cold run (no previous state).
    pub fn cold(current: &CollectOutput) -> Self {
        Self {
            changed: Vec::new(),
            added: current.files.iter().map(|f| f.path.clone()).collect(),
            removed: Vec::new(),
        }
    }
}
```

- [ ] **Step 5: Create stub stage files (types only, no implementations)**

Create each of these files with just the input/output structs:

`repotoire-cli/src/engine/stages/collect.rs`:
```rust
//! Stage 1: File collection and hashing.

use anyhow::Result;
use std::path::{Path, PathBuf};

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
    pub fn all_paths(&self) -> Vec<PathBuf> {
        self.files.iter().map(|f| f.path.clone()).collect()
    }
}

pub fn collect_stage(_input: &CollectInput) -> Result<CollectOutput> {
    todo!("Implement in Task 3")
}
```

`repotoire-cli/src/engine/stages/parse.rs`:
```rust
//! Stage 2: Tree-sitter parsing.

use crate::engine::ProgressFn;
use crate::parsers::ParseResult;
use anyhow::Result;
use std::path::PathBuf;
use std::sync::Arc;

pub struct ParseInput {
    pub files: Vec<PathBuf>,
    pub workers: usize,
    pub progress: Option<ProgressFn>,
}

pub struct ParseStats {
    pub files_parsed: usize,
    pub files_skipped: usize,
    pub total_functions: usize,
    pub total_classes: usize,
    pub total_loc: usize,
}

pub struct ParseOutput {
    pub results: Vec<(PathBuf, Arc<ParseResult>)>,
    pub stats: ParseStats,
}

pub fn parse_stage(_input: &ParseInput) -> Result<ParseOutput> {
    todo!("Implement in Task 4")
}
```

`repotoire-cli/src/engine/stages/graph.rs`:
```rust
//! Stage 3: Graph construction and patching.

use crate::graph::GraphStore;
use crate::parsers::ParseResult;
use crate::values::store::ValueStore;
use anyhow::Result;
use std::path::{Path, PathBuf};
use std::sync::Arc;

pub struct GraphInput<'a> {
    pub parse_results: &'a [(PathBuf, Arc<ParseResult>)],
    pub repo_path: &'a Path,
}

pub struct GraphOutput {
    pub graph: Arc<GraphStore>,
    pub value_store: Option<Arc<ValueStore>>,
    pub edge_fingerprint: u64,
}

pub struct GraphPatchInput<'a> {
    pub graph: Arc<GraphStore>,
    pub changed_files: &'a [PathBuf],
    pub removed_files: &'a [PathBuf],
    pub new_parse_results: &'a [(PathBuf, Arc<ParseResult>)],
    pub repo_path: &'a Path,
}

pub fn graph_stage(_input: &GraphInput) -> Result<GraphOutput> {
    todo!("Implement in Task 5")
}

pub fn graph_patch_stage(_input: &GraphPatchInput) -> Result<GraphOutput> {
    todo!("Implement in Task 10")
}
```

`repotoire-cli/src/engine/stages/git_enrich.rs`:
```rust
//! Stage 4: Git history enrichment (impure — mutates graph nodes).

use crate::graph::GraphStore;
use anyhow::Result;
use std::path::Path;

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
    pub fn skipped() -> Self {
        Self { functions_enriched: 0, classes_enriched: 0, cache_hits: 0 }
    }
}

pub fn git_enrich_stage(_input: &GitEnrichInput) -> Result<GitEnrichOutput> {
    todo!("Implement in Task 6")
}
```

`repotoire-cli/src/engine/stages/calibrate.rs`:
```rust
//! Stage 5: Adaptive threshold calibration + n-gram model.

use crate::calibrate::{NgramModel, StyleProfile};
use crate::parsers::ParseResult;
use anyhow::Result;
use std::path::{Path, PathBuf};
use std::sync::Arc;

pub struct CalibrateInput<'a> {
    pub parse_results: &'a [(PathBuf, Arc<ParseResult>)],
    pub file_count: usize,
    pub repo_path: &'a Path,
}

pub struct CalibrateOutput {
    pub style_profile: StyleProfile,
    pub ngram_model: Option<NgramModel>,
}

pub fn calibrate_stage(_input: &CalibrateInput) -> Result<CalibrateOutput> {
    todo!("Implement in Task 7")
}
```

`repotoire-cli/src/engine/stages/detect.rs`:
```rust
//! Stage 6: Detector execution.

use crate::calibrate::{NgramModel, StyleProfile};
use crate::config::ProjectConfig;
use crate::detectors::GdPrecomputed;
use crate::engine::ProgressFn;
use crate::graph::GraphQuery;
use crate::models::Finding;
use crate::values::store::ValueStore;
use anyhow::Result;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

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
    pub changed_files: Option<&'a [PathBuf]>,
    pub topology_changed: bool,
    pub cached_gd_precomputed: Option<&'a GdPrecomputed>,
    pub cached_file_findings: Option<&'a HashMap<PathBuf, Vec<Finding>>>,
    pub cached_graph_wide_findings: Option<&'a HashMap<String, Vec<Finding>>>,
}

pub struct DetectStats {
    pub detectors_run: usize,
    pub detectors_skipped: usize,
    pub gi_findings: usize,
    pub gd_findings: usize,
    pub precompute_duration: Duration,
}

pub struct DetectOutput {
    pub findings: Vec<Finding>,
    pub gd_precomputed: GdPrecomputed,
    pub findings_by_file: HashMap<PathBuf, Vec<Finding>>,
    pub graph_wide_findings: HashMap<String, Vec<Finding>>,
    pub stats: DetectStats,
}

pub fn detect_stage(_input: &DetectInput) -> Result<DetectOutput> {
    todo!("Implement in Task 8")
}
```

`repotoire-cli/src/engine/stages/postprocess.rs`:
```rust
//! Stage 7: Finding transforms (pure — no caching, no I/O, no presentation).

use crate::config::ProjectConfig;
use crate::graph::GraphQuery;
use crate::models::Finding;
use anyhow::Result;
use std::path::{Path, PathBuf};

pub struct PostprocessInput<'a> {
    pub findings: Vec<Finding>,
    pub project_config: &'a ProjectConfig,
    pub graph: &'a dyn GraphQuery,
    pub all_files: &'a [PathBuf],
    pub repo_path: &'a Path,
    pub verify: bool,
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

pub struct PostprocessOutput {
    pub findings: Vec<Finding>,
    pub stats: PostprocessStats,
}

pub fn postprocess_stage(_input: PostprocessInput) -> Result<PostprocessOutput> {
    todo!("Implement in Task 9")
}
```

`repotoire-cli/src/engine/stages/score.rs`:
```rust
//! Stage 8: Health scoring.

use crate::config::ProjectConfig;
use crate::engine::ScoreResult;
use crate::graph::GraphQuery;
use crate::models::Finding;
use anyhow::Result;
use std::path::Path;

pub struct ScoreInput<'a> {
    pub graph: &'a dyn GraphQuery,
    pub findings: &'a [Finding],
    pub project_config: &'a ProjectConfig,
    pub repo_path: &'a Path,
    pub total_loc: usize,
}

pub fn score_stage(_input: &ScoreInput) -> Result<ScoreResult> {
    todo!("Implement in Task 9")
}
```

- [ ] **Step 6: Register engine module**

Add to the top-level module declarations (check whether `repotoire-cli/src/lib.rs` or `repotoire-cli/src/main.rs` is the crate root):

```rust
pub mod engine;
```

- [ ] **Step 7: Verify compilation**

```bash
cargo check
```

Everything should compile — all stage functions are `todo!()` stubs, types are defined.

- [ ] **Step 8: Commit**

```bash
git add repotoire-cli/src/engine/
git commit -m "feat: create engine module with public API types and stage stubs

Adds the engine/ module with:
- Public API: AnalysisConfig, AnalysisResult, ScoreResult, AnalysisStats
- Stage input/output types for all 8 stages
- EngineState for internal state management
- FileChanges for incremental diff detection

All stage functions are todo!() stubs — implementations follow."
```

---

## Chunk 3: Stage Implementations (Thin Wrappers)

Each stage wraps existing code. No new logic — just adapting existing functions to the new typed interfaces.

### Task 3: Implement collect_stage

**Files:**
- Modify: `repotoire-cli/src/engine/stages/collect.rs`
- Modify: `repotoire-cli/src/cli/analyze/files.rs` (widen visibility)

- [ ] **Step 1: Widen visibility of collect_file_list**

In `repotoire-cli/src/cli/analyze/files.rs`, change:

```rust
// Before:
pub(crate) fn collect_file_list(repo_path: &Path, exclude: &ExcludeConfig) -> Result<Vec<PathBuf>> {

// After:
pub fn collect_file_list(repo_path: &Path, exclude: &ExcludeConfig) -> Result<Vec<PathBuf>> {
```

- [ ] **Step 2: Implement collect_stage**

Replace the `todo!()` in `collect.rs`:

```rust
use crate::config::ExcludeConfig;
use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};

pub fn collect_stage(input: &CollectInput) -> Result<CollectOutput> {
    let exclude = ExcludeConfig::from_patterns(input.exclude_patterns);
    let mut paths = crate::cli::analyze::files::collect_file_list(input.repo_path, &exclude)?;

    if input.max_files > 0 && paths.len() > input.max_files {
        paths.truncate(input.max_files);
    }

    let files: Vec<SourceFile> = paths.into_iter().filter_map(|path| {
        let content = std::fs::read(&path).ok()?;
        let mut hasher = DefaultHasher::new();
        content.hash(&mut hasher);
        Some(SourceFile {
            path,
            content_hash: hasher.finish(),
        })
    }).collect();

    Ok(CollectOutput { files })
}
```

- [ ] **Step 3: Write test**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_collect_empty_dir() {
        let dir = tempfile::tempdir().unwrap();
        let out = collect_stage(&CollectInput {
            repo_path: dir.path(),
            exclude_patterns: &[],
            max_files: 0,
        }).unwrap();
        assert!(out.files.is_empty());
    }

    #[test]
    fn test_collect_hashes_are_deterministic() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("test.py"), "print('hello')").unwrap();
        let out1 = collect_stage(&CollectInput {
            repo_path: dir.path(),
            exclude_patterns: &[],
            max_files: 0,
        }).unwrap();
        let out2 = collect_stage(&CollectInput {
            repo_path: dir.path(),
            exclude_patterns: &[],
            max_files: 0,
        }).unwrap();
        assert_eq!(out1.files[0].content_hash, out2.files[0].content_hash);
    }
}
```

- [ ] **Step 4: Run tests**

```bash
cargo test engine::stages::collect -- --nocapture
```

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/engine/stages/collect.rs repotoire-cli/src/cli/analyze/files.rs
git commit -m "feat(engine): implement collect_stage wrapping existing file collection"
```

### Task 4: Implement parse_stage

**Files:**
- Modify: `repotoire-cli/src/engine/stages/parse.rs`

- [ ] **Step 1: Implement parse_stage**

Replace `todo!()`:

```rust
use rayon::prelude::*;

pub fn parse_stage(input: &ParseInput) -> Result<ParseOutput> {
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(input.workers)
        .build()?;

    let results: Vec<(PathBuf, Arc<ParseResult>)> = pool.install(|| {
        input.files.par_iter().filter_map(|path| {
            match crate::parsers::parse_file_with_values(path) {
                Ok(pr) => Some((path.clone(), Arc::new(pr))),
                Err(e) => {
                    tracing::debug!("Parse error for {}: {}", path.display(), e);
                    None
                }
            }
        }).collect()
    });

    let mut stats = ParseStats {
        files_parsed: results.len(),
        files_skipped: input.files.len() - results.len(),
        total_functions: 0,
        total_classes: 0,
        total_loc: 0,
    };

    for (_path, pr) in &results {
        stats.total_functions += pr.entities.iter()
            .filter(|e| e.kind == "function" || e.kind == "method")
            .count();
        stats.total_classes += pr.entities.iter()
            .filter(|e| e.kind == "class")
            .count();
        stats.total_loc += pr.loc;
    }

    Ok(ParseOutput { results, stats })
}
```

- [ ] **Step 2: Verify compilation**

```bash
cargo check
```

Adapt the implementation based on the actual `ParseResult` struct fields (entity kind field names may differ — check `repotoire-cli/src/parsers/mod.rs` for the actual field names).

- [ ] **Step 3: Commit**

```bash
git add repotoire-cli/src/engine/stages/parse.rs
git commit -m "feat(engine): implement parse_stage wrapping tree-sitter parsing"
```

### Task 5: Implement graph_stage (cold path only)

**Files:**
- Modify: `repotoire-cli/src/engine/stages/graph.rs`
- Modify: `repotoire-cli/src/cli/analyze/graph.rs` (widen visibility)

- [ ] **Step 1: Implement graph_stage**

This wraps the existing `build_graph()` from `cli/analyze/graph.rs`. Widen its visibility first, then delegate:

```rust
pub fn graph_stage(input: &GraphInput) -> Result<GraphOutput> {
    let graph = Arc::new(GraphStore::in_memory());

    // Delegate to existing graph building logic
    crate::cli::analyze::graph::build_graph_from_parse_results(
        &graph,
        input.parse_results,
        input.repo_path,
    )?;

    let edge_fingerprint = graph.compute_edge_fingerprint();
    Ok(GraphOutput {
        graph,
        value_store: None, // populated by enhanced pipeline later
        edge_fingerprint,
    })
}
```

Adapt based on the actual function signature in `cli/analyze/graph.rs`. The exact function name and params may differ — check the file and delegate accordingly.

- [ ] **Step 2: Verify compilation**

```bash
cargo check
```

- [ ] **Step 3: Commit**

```bash
git add repotoire-cli/src/engine/stages/graph.rs repotoire-cli/src/cli/analyze/graph.rs
git commit -m "feat(engine): implement graph_stage wrapping petgraph construction"
```

### Task 6: Implement git_enrich_stage

**Files:**
- Modify: `repotoire-cli/src/engine/stages/git_enrich.rs`

- [ ] **Step 1: Implement git_enrich_stage**

```rust
pub fn git_enrich_stage(input: &GitEnrichInput) -> Result<GitEnrichOutput> {
    let stats = crate::git::enrichment::enrich_graph_with_git(
        input.repo_path,
        input.graph,
        None,
    )?;
    Ok(GitEnrichOutput {
        functions_enriched: stats.functions_enriched,
        classes_enriched: stats.classes_enriched,
        cache_hits: stats.cache_hits,
    })
}
```

- [ ] **Step 2: Commit**

```bash
git add repotoire-cli/src/engine/stages/git_enrich.rs
git commit -m "feat(engine): implement git_enrich_stage wrapping git enrichment"
```

### Task 7: Implement calibrate_stage

**Files:**
- Modify: `repotoire-cli/src/engine/stages/calibrate.rs`

- [ ] **Step 1: Implement calibrate_stage**

```rust
pub fn calibrate_stage(input: &CalibrateInput) -> Result<CalibrateOutput> {
    let parse_pairs: Vec<_> = input.parse_results.iter().map(|(path, pr)| {
        let loc = std::fs::read_to_string(path)
            .map(|c| c.lines().count())
            .unwrap_or(0);
        (crate::parsers::ParseResult::clone(pr), loc)
    }).collect();

    let commit_sha = std::process::Command::new("git")
        .args(["rev-parse", "HEAD"])
        .current_dir(input.repo_path)
        .output()
        .ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string());

    let style_profile = crate::calibrate::collect_metrics(
        &parse_pairs, input.file_count, commit_sha,
    );

    // Build n-gram model
    let mut model = crate::calibrate::NgramModel::new();
    for (path, _pr) in input.parse_results {
        let path_lower = path.to_string_lossy().to_lowercase();
        if path_lower.contains("/test") || path_lower.contains("/vendor")
            || path_lower.contains("/node_modules") || path_lower.contains("/generated")
        {
            continue;
        }
        let Ok(content) = std::fs::read_to_string(path) else { continue };
        let tokens = crate::calibrate::NgramModel::tokenize_file(&content);
        model.train_on_tokens(&tokens);
    }
    let ngram_model = model.is_confident().then_some(model);

    Ok(CalibrateOutput { style_profile, ngram_model })
}
```

- [ ] **Step 2: Commit**

```bash
git add repotoire-cli/src/engine/stages/calibrate.rs
git commit -m "feat(engine): implement calibrate_stage wrapping threshold calibration"
```

### Task 8: Implement detect_stage (cold path)

**Files:**
- Modify: `repotoire-cli/src/engine/stages/detect.rs`
- Modify: `repotoire-cli/src/cli/analyze/detect.rs` (widen visibility)

- [ ] **Step 1: Implement detect_stage (cold path — ignores incremental hints)**

This is the most complex stage. It wraps `DetectorEngineBuilder` + `precompute_gd_startup()` + `engine.run()`. The initial implementation handles the cold path only (no incremental optimization). Incremental hints are accepted but ignored until Chunk 5.

```rust
use crate::detectors::{
    default_detectors_with_ngram, DetectorEngineBuilder, SourceFiles,
    precompute_gd_startup,
};
use crate::graph::CachedGraphQuery;

pub fn detect_stage(input: &DetectInput) -> Result<DetectOutput> {
    let start = std::time::Instant::now();

    // Build detector list
    let detectors = default_detectors_with_ngram(
        input.repo_path,
        input.project_config,
        input.style_profile,
        input.ngram_model,
    );

    // Filter out skipped detectors
    let detectors: Vec<_> = detectors.into_iter()
        .filter(|d| !input.skip_detectors.iter().any(|s| {
            crate::cli::analyze::normalize_to_kebab(&d.name())
                == crate::cli::analyze::normalize_to_kebab(s)
        }))
        .collect();

    // Precompute GD data (or reuse cached)
    let cached = CachedGraphQuery::new(input.graph);
    let source_files: Vec<PathBuf> = input.source_files.to_vec();
    let gd = input.cached_gd_precomputed
        .filter(|_| !input.topology_changed)
        .cloned()
        .unwrap_or_else(|| {
            precompute_gd_startup(
                &cached,
                input.repo_path,
                None,
                &source_files,
                input.value_store.cloned(),
                &detectors.iter().map(|d| d.clone() as _).collect::<Vec<_>>(),
            )
        });

    let precompute_duration = start.elapsed();

    // Build and run engine
    let mut engine = DetectorEngineBuilder::new()
        .workers(input.workers)
        .detectors(detectors)
        .build();

    engine.inject_gd_precomputed(gd.clone());

    let source = SourceFiles::new(source_files, input.repo_path.to_path_buf());
    let findings = engine.run(input.graph, &source)?;

    // Partition findings by file vs graph-wide
    let mut findings_by_file: HashMap<PathBuf, Vec<Finding>> = HashMap::new();
    let mut graph_wide_findings: HashMap<String, Vec<Finding>> = HashMap::new();

    for finding in &findings {
        if finding.affected_files.is_empty() {
            graph_wide_findings.entry(finding.detector.clone())
                .or_default().push(finding.clone());
        } else {
            for file in &finding.affected_files {
                findings_by_file.entry(file.clone())
                    .or_default().push(finding.clone());
            }
        }
    }

    Ok(DetectOutput {
        findings,
        gd_precomputed: gd,
        findings_by_file,
        graph_wide_findings,
        stats: DetectStats {
            detectors_run: engine.detectors_run(),
            detectors_skipped: input.skip_detectors.len(),
            gi_findings: 0, // detailed stats available from engine
            gd_findings: 0,
            precompute_duration,
        },
    })
}
```

Adapt based on actual `DetectorEngine` API — method names, `inject_gd_precomputed`, etc. Check `engine.rs` for exact signatures.

- [ ] **Step 2: Verify compilation**

```bash
cargo check
```

- [ ] **Step 3: Commit**

```bash
git add repotoire-cli/src/engine/stages/detect.rs
git commit -m "feat(engine): implement detect_stage wrapping detector engine (cold path)"
```

### Task 9: Implement postprocess_stage and score_stage

**Files:**
- Modify: `repotoire-cli/src/engine/stages/postprocess.rs`
- Modify: `repotoire-cli/src/engine/stages/score.rs`
- Modify: `repotoire-cli/src/cli/analyze/postprocess.rs` (widen visibility)
- Modify: `repotoire-cli/src/cli/analyze/scoring.rs` (widen visibility)

- [ ] **Step 1: Implement postprocess_stage**

```rust
pub fn postprocess_stage(input: PostprocessInput) -> Result<PostprocessOutput> {
    let input_count = input.findings.len();
    let mut findings = input.findings;

    // Delegate to existing postprocess pipeline, minus cache/presentation steps.
    // We call the individual sub-functions directly rather than the monolithic
    // postprocess_findings() to avoid the incremental_cache and rank params.

    // Step 0: Deterministic IDs
    for finding in findings.iter_mut() {
        let file = finding.affected_files.first()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default();
        let line = finding.line_start.unwrap_or(0);
        finding.id = crate::detectors::base::finding_id(&finding.detector, &file, line);
    }

    // Steps 0.5-12: Delegate to existing postprocess functions
    // (These need to be extracted as pub functions or called via a wrapper)
    // For now, call the existing function with dummy cache params:
    let mut dummy_cache = crate::detectors::IncrementalCache::new(
        &std::path::PathBuf::from("/dev/null")
    );
    crate::cli::analyze::postprocess::postprocess_findings(
        &mut findings,
        input.project_config,
        &mut dummy_cache,
        false, // is_incremental_mode
        &[],   // files_to_parse (unused without incremental)
        input.all_files,
        0,     // max_files (0 = no filter — already handled by collect)
        input.verify,
        input.graph,
        false, // rank (consumer-side)
        None,  // min_confidence (consumer-side)
        false, // show_all (consumer-side)
        input.repo_path,
    );

    let output_count = findings.len();

    Ok(PostprocessOutput {
        findings,
        stats: PostprocessStats {
            input_count,
            output_count,
            suppressed: 0,     // TODO: wire detailed stats from sub-functions
            excluded: 0,
            deduped: 0,
            fp_filtered: input_count - output_count,
            security_downgraded: 0,
        },
    })
}
```

- [ ] **Step 2: Implement score_stage**

```rust
pub fn score_stage(input: &ScoreInput) -> Result<ScoreResult> {
    use crate::graph::GraphStore;
    use crate::scoring::GraphScorer;

    // The existing calculate_scores expects Arc<GraphStore>, but we have &dyn GraphQuery.
    // For now, delegate via the scorer directly.
    let scorer = GraphScorer::new_from_query(input.graph, input.project_config, input.repo_path);
    let breakdown = scorer.calculate(input.findings);

    Ok(ScoreResult {
        overall: breakdown.overall_score,
        grade: breakdown.grade.clone(),
        breakdown,
    })
}
```

Adapt based on actual `GraphScorer` API. It may require `&GraphStore` not `&dyn GraphQuery` — in that case, add a `graph: &GraphStore` variant to `ScoreInput` or use the engine's stored `Arc<GraphStore>`.

- [ ] **Step 3: Widen visibility of postprocess_findings**

In `repotoire-cli/src/cli/analyze/postprocess.rs`:
```rust
// Before:
pub(super) fn postprocess_findings(

// After:
pub fn postprocess_findings(
```

- [ ] **Step 4: Verify compilation**

```bash
cargo check
```

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/engine/stages/postprocess.rs \
       repotoire-cli/src/engine/stages/score.rs \
       repotoire-cli/src/cli/analyze/postprocess.rs \
       repotoire-cli/src/cli/analyze/scoring.rs
git commit -m "feat(engine): implement postprocess_stage and score_stage"
```

---

## Chunk 4: AnalysisEngine Core (Cold Path)

Wire the stages together in `AnalysisEngine::analyze()`. Cold path only — no incremental, no persistence.

### Task 10: Implement AnalysisEngine::new() and analyze() (cold path)

**Files:**
- Modify: `repotoire-cli/src/engine/mod.rs`

- [ ] **Step 1: Add AnalysisEngine struct and new()**

Add to `engine/mod.rs`:

```rust
use crate::config::{load_project_config, ProjectConfig};
use crate::graph::GraphQuery;
use anyhow::Result;

pub struct AnalysisEngine {
    repo_path: PathBuf,
    project_config: ProjectConfig,
    state: Option<state::EngineState>,
    progress: Option<ProgressFn>,
}

impl AnalysisEngine {
    pub fn new(repo_path: &Path) -> Result<Self> {
        let repo_path = repo_path.canonicalize()?;
        let project_config = load_project_config(&repo_path);
        Ok(Self {
            repo_path,
            project_config,
            state: None,
            progress: None,
        })
    }

    pub fn with_progress(mut self, progress: ProgressFn) -> Self {
        self.progress = Some(progress);
        self
    }

    pub fn graph(&self) -> Option<&dyn GraphQuery> {
        self.state.as_ref().map(|s| s.graph.as_ref() as &dyn GraphQuery)
    }
}
```

- [ ] **Step 2: Implement analyze() — cold path only**

```rust
impl AnalysisEngine {
    pub fn analyze(&mut self, config: &AnalysisConfig) -> Result<AnalysisResult> {
        use stages::*;
        let mut timings = BTreeMap::new();

        // Stage 1: Collect
        let collect_out = timed(&mut timings, "collect", || {
            collect::collect_stage(&collect::CollectInput {
                repo_path: &self.repo_path,
                exclude_patterns: &self.project_config.exclude.effective_patterns(),
                max_files: config.max_files,
            })
        })?;

        // Check for cached return
        let changes = match &self.state {
            Some(state) => diff::FileChanges::compute(&state.file_hashes, &collect_out),
            None => diff::FileChanges::cold(&collect_out),
        };

        if changes.nothing_changed() {
            if let Some(ref state) = self.state {
                return Ok(AnalysisResult {
                    findings: state.last_findings.clone(),
                    score: state.last_score.clone(),
                    stats: AnalysisStats { mode: AnalysisMode::Cached, ..state.last_stats.clone() },
                });
            }
        }

        let all_files = collect_out.all_paths();

        // Stage 2: Parse
        let parse_out = timed(&mut timings, "parse", || {
            parse::parse_stage(&parse::ParseInput {
                files: all_files.clone(),
                workers: config.workers,
                progress: self.progress.clone(),
            })
        })?;

        // Stage 3: Graph (cold only for now)
        let graph_out = timed(&mut timings, "graph", || {
            graph::graph_stage(&graph::GraphInput {
                parse_results: &parse_out.results,
                repo_path: &self.repo_path,
            })
        })?;

        // Stage 4: Git enrich
        if !config.no_git {
            timed(&mut timings, "git_enrich", || {
                git_enrich::git_enrich_stage(&git_enrich::GitEnrichInput {
                    repo_path: &self.repo_path,
                    graph: &graph_out.graph,
                })
            })?;
        }

        // Stage 5: Calibrate
        let calibrate_out = timed(&mut timings, "calibrate", || {
            calibrate::calibrate_stage(&calibrate::CalibrateInput {
                parse_results: &parse_out.results,
                file_count: collect_out.files.len(),
                repo_path: &self.repo_path,
            })
        })?;

        // Stage 6: Detect
        let detect_out = timed(&mut timings, "detect", || {
            detect::detect_stage(&detect::DetectInput {
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
                changed_files: None,
                topology_changed: true,
                cached_gd_precomputed: None,
                cached_file_findings: None,
                cached_graph_wide_findings: None,
            })
        })?;

        // Stage 7: Postprocess
        let postprocess_out = timed(&mut timings, "postprocess", || {
            postprocess::postprocess_stage(postprocess::PostprocessInput {
                findings: detect_out.findings,
                project_config: &self.project_config,
                graph: graph_out.graph.as_ref(),
                all_files: &all_files,
                repo_path: &self.repo_path,
                verify: config.verify,
            })
        })?;

        // Stage 8: Score
        let score_out = timed(&mut timings, "score", || {
            score::score_stage(&score::ScoreInput {
                graph: graph_out.graph.as_ref(),
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
            findings_filtered: postprocess_out.stats.input_count - postprocess_out.stats.output_count,
            timings,
        };

        // Cache state for next call
        self.state = Some(state::EngineState {
            file_hashes: collect_out.files.iter()
                .map(|f| (f.path.clone(), f.content_hash)).collect(),
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

fn timed<T>(
    timings: &mut BTreeMap<String, Duration>,
    name: &str,
    f: impl FnOnce() -> T,
) -> T {
    let start = std::time::Instant::now();
    let result = f();
    timings.insert(name.to_string(), start.elapsed());
    result
}
```

- [ ] **Step 3: Verify compilation**

```bash
cargo check
```

- [ ] **Step 4: Write integration test**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_engine_cold_analysis() {
        // Use the repotoire codebase itself as a test repo (it's always available)
        let repo = std::env::current_dir().unwrap();
        let mut engine = AnalysisEngine::new(&repo).unwrap();
        let config = AnalysisConfig {
            workers: 2,
            max_files: 10,  // limit for test speed
            no_git: true,   // skip git for test speed
            ..Default::default()
        };
        let result = engine.analyze(&config).unwrap();
        assert!(matches!(result.stats.mode, AnalysisMode::Cold));
        assert!(result.stats.files_analyzed <= 10);
        assert!(result.score.overall >= 0.0);
        assert!(result.score.overall <= 100.0);
    }

    #[test]
    fn test_engine_second_call_returns_cached() {
        let repo = std::env::current_dir().unwrap();
        let mut engine = AnalysisEngine::new(&repo).unwrap();
        let config = AnalysisConfig {
            workers: 2,
            max_files: 5,
            no_git: true,
            ..Default::default()
        };
        let _r1 = engine.analyze(&config).unwrap();
        let r2 = engine.analyze(&config).unwrap();
        assert!(matches!(r2.stats.mode, AnalysisMode::Cached));
    }
}
```

- [ ] **Step 5: Run tests**

```bash
cargo test engine::tests -- --nocapture
```

- [ ] **Step 6: Commit**

```bash
git add repotoire-cli/src/engine/mod.rs
git commit -m "feat(engine): implement AnalysisEngine with cold analysis path

AnalysisEngine::new() + analyze() wires all 8 stages together for
cold analysis. Second call on unchanged files returns cached result.
Incremental path and persistence follow in subsequent tasks."
```

---

## Chunk 5: CLI Rewiring

Replace the 22-parameter `analyze::run()` with engine-based dispatch. Create `OutputOptions` for consumer-side formatting.

### Task 11: Create OutputOptions and consumer-side formatting

**Files:**
- Create: `repotoire-cli/src/cli/output.rs` (or modify existing `cli/analyze/output.rs`)

- [ ] **Step 1: Define OutputOptions**

Create a struct that captures all presentation-only config. This is the CLI's concern, not the engine's:

```rust
use crate::models::Severity;
use std::path::PathBuf;

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
```

- [ ] **Step 2: Implement apply_filters**

```rust
use crate::models::Finding;

pub fn apply_filters(findings: &[Finding], opts: &OutputOptions) -> Vec<Finding> {
    let mut filtered: Vec<Finding> = findings.to_vec();

    // Confidence filter
    if !opts.show_all {
        if let Some(threshold) = opts.min_confidence {
            filtered.retain(|f| f.effective_confidence() >= threshold);
        }
    }

    // Severity filter
    if let Some(ref sev) = opts.severity_filter {
        // ... existing severity filter logic
    }

    filtered
}
```

- [ ] **Step 3: Commit**

```bash
git add repotoire-cli/src/cli/output.rs
git commit -m "feat(cli): add OutputOptions and apply_filters for consumer-side formatting"
```

### Task 12: Rewire CLI analyze command to use engine

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/mod.rs`
- Modify: `repotoire-cli/src/cli/mod.rs`

- [ ] **Step 1: Add engine-based run function**

Add a new function `run_with_engine()` alongside the existing `run()`:

```rust
pub fn run_with_engine(
    path: &Path,
    config: crate::engine::AnalysisConfig,
    output: super::output::OutputOptions,
) -> Result<()> {
    let session_dir = cache_path(path).join("session");

    let mut engine = crate::engine::AnalysisEngine::load(&session_dir, path)
        .unwrap_or_else(|_| crate::engine::AnalysisEngine::new(path).unwrap());

    let result = engine.analyze(&config)?;

    // Consumer-side filtering
    let filtered = super::output::apply_filters(&result.findings, &output);

    // Format and output (reuse existing format_and_output logic)
    format_and_output(&filtered, &result.score, &result.stats, &output)?;

    // Explicit persistence
    let _ = engine.save(&session_dir);

    Ok(())
}
```

- [ ] **Step 2: Update CLI dispatch to use engine path**

In `cli/mod.rs`, replace the `analyze::run(22 args)` call with construction of `AnalysisConfig` + `OutputOptions` and a call to `analyze::run_with_engine()`.

- [ ] **Step 3: Run full test suite**

```bash
cargo test
```

All existing tests must pass. The engine produces the same results as the old pipeline (it wraps the same code).

- [ ] **Step 4: Run manual smoke test**

```bash
cargo run -- analyze . --format json --max-files 20 --no-git
```

Compare output with a previous run to verify identical results.

- [ ] **Step 5: Remove old run() function**

Once satisfied, delete the old 22-param `run()` and rename `run_with_engine` to `run`.

- [ ] **Step 6: Commit**

```bash
git add repotoire-cli/src/cli/analyze/mod.rs repotoire-cli/src/cli/mod.rs repotoire-cli/src/cli/output.rs
git commit -m "feat(cli): rewire analyze command to use AnalysisEngine

Replaces the 22-parameter run() function with engine-based dispatch.
AnalysisConfig captures analysis concerns, OutputOptions captures
presentation concerns. Engine produces truth, CLI formats it."
```

---

## Chunk 6: Persistence & MCP Rewiring

### Task 13: Implement save() and load()

**Files:**
- Modify: `repotoire-cli/src/engine/state.rs`
- Modify: `repotoire-cli/src/engine/mod.rs`

- [ ] **Step 1: Define SessionMeta**

In `state.rs`:

```rust
use serde::{Deserialize, Serialize};

const SESSION_VERSION: u32 = 3;

#[derive(Serialize, Deserialize)]
pub(crate) struct SessionMeta {
    pub version: u32,
    pub binary_version: String,
    pub file_hashes: HashMap<PathBuf, u64>,
    pub source_files: Vec<PathBuf>,
    pub edge_fingerprint: u64,
    pub findings_by_file: HashMap<PathBuf, Vec<Finding>>,
    pub graph_wide_findings: HashMap<String, Vec<Finding>>,
    pub last_findings: Vec<Finding>,
    pub last_score: ScoreResult,
    pub last_stats: AnalysisStats,
    pub style_profile: StyleProfile,
}
```

Note: `ScoreResult` and `AnalysisStats` must derive `Serialize, Deserialize`. Add those derives to the types in `engine/mod.rs`.

- [ ] **Step 2: Implement save() and load()**

In `engine/mod.rs`:

```rust
impl AnalysisEngine {
    pub fn save(&self, path: &Path) -> Result<()> {
        let Some(ref state) = self.state else { return Ok(()); };
        std::fs::create_dir_all(path)?;
        let meta = state::SessionMeta { /* fill from state */ };
        let meta_path = path.join("engine_session.json");
        std::fs::write(&meta_path, serde_json::to_vec(&meta)?)?;
        state.graph.save_graph_cache(&path.join("graph.bin"))?;
        Ok(())
    }

    pub fn load(session_path: &Path, repo_path: &Path) -> Result<Self> {
        let meta_path = session_path.join("engine_session.json");
        let meta: state::SessionMeta = serde_json::from_slice(&std::fs::read(&meta_path)?)?;
        // Version check, graph restore, etc.
        // See spec for full implementation
        todo!("Full implementation per spec")
    }
}
```

- [ ] **Step 3: Write roundtrip test**

- [ ] **Step 4: Commit**

### Task 14: Rewire MCP server to use engine

**Files:**
- Modify: `repotoire-cli/src/mcp/state.rs`
- Modify: `repotoire-cli/src/mcp/tools/analysis.rs`

- [ ] **Step 1: Replace HandlerState session with engine**

- [ ] **Step 2: Rewrite handle_analyze to use engine.analyze()**

- [ ] **Step 3: Remove handle_analyze_oneshot**

- [ ] **Step 4: Run MCP tests**

- [ ] **Step 5: Commit**

---

## Chunk 7: Incremental Analysis

Add incremental path to analyze() — file diffing, graph patching, cached finding merge.

### Task 15: Implement incremental analyze() path

**Files:**
- Modify: `repotoire-cli/src/engine/mod.rs` (analyze method)
- Modify: `repotoire-cli/src/engine/stages/graph.rs` (graph_patch_stage)
- Modify: `repotoire-cli/src/engine/stages/detect.rs` (incremental hints)

- [ ] **Step 1: Implement graph_patch_stage**

Uses `remove_file_entities()` + re-insert from new parse results. Builds `global_func_map` from existing graph nodes + new parse results for correct cross-file edge resolution.

- [ ] **Step 2: Add incremental branching to analyze()**

Add the conditional logic per the spec: parse only changed files, patch vs rebuild graph, reuse calibration, pass incremental hints to detect stage.

- [ ] **Step 3: Add incremental finding merge to detect_stage**

When `cached_file_findings` is Some, skip FileLocal detectors for unchanged files and merge cached findings.

- [ ] **Step 4: Write incremental test**

```rust
#[test]
fn test_incremental_after_file_change() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("main.py"), "def foo(): pass").unwrap();

    let mut engine = AnalysisEngine::new(dir.path()).unwrap();
    let config = AnalysisConfig { no_git: true, ..Default::default() };

    let r1 = engine.analyze(&config).unwrap();
    assert!(matches!(r1.stats.mode, AnalysisMode::Cold));

    // Modify file
    std::fs::write(dir.path().join("main.py"), "def foo():\n    x = 1\n    return x").unwrap();

    let r2 = engine.analyze(&config).unwrap();
    assert!(matches!(r2.stats.mode, AnalysisMode::Incremental { .. }));
}
```

- [ ] **Step 5: Commit**

---

## Chunk 8: Cleanup

### Task 16: Remove old pipeline code

**Files:**
- Remove: `repotoire-cli/src/session.rs`
- Remove: `repotoire-cli/src/detectors/incremental_cache.rs` (if no longer referenced)
- Modify: `repotoire-cli/src/cli/analyze/` (remove unused submodules)
- Modify: `CLAUDE.md` (update architecture docs)

- [ ] **Step 1: Remove session.rs**
- [ ] **Step 2: Remove references to IncrementalCache**
- [ ] **Step 3: Clean up cli/analyze/ — remove detect.rs, postprocess.rs, scoring.rs, setup.rs if fully superseded**
- [ ] **Step 4: Update CLAUDE.md architecture section**
- [ ] **Step 5: Run full test suite**
- [ ] **Step 6: Commit**

```bash
git commit -m "refactor: remove old pipeline code superseded by AnalysisEngine

Removes session.rs (2,210 lines), IncrementalCache, and old pipeline
submodules. All analysis now flows through engine/."
```
