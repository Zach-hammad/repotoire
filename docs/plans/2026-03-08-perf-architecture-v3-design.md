# Performance Architecture V3: Persistent Graph + Hybrid Optimization

**Date**: 2026-03-08
**Branch**: `perf/optimization-v2` (continuation)
**Target**: CPython (3,415 files, 71K functions, 13K classes, 296K edges)

## Context

After 30+ commits of optimization work, CPython analysis is at **5.7s** (down from 14.6s baseline, -61%). The remaining time splits:

| Phase | Time | % |
|-------|------|---|
| init+parse | 2.6s | 46% |
| detect | 2.4s | 42% |
| postprocess | 0.25s | 4% |
| scoring | 0.15s | 3% |
| output | 0.17s | 3% |

Individual detector optimizations have hit diminishing returns. The remaining bottlenecks are **architectural**: the graph is rebuilt from scratch every run, phases are over-serialized, and detectors over-fetch shared data.

## Goals

1. **Faster wall-clock time**: Full cold analysis 5.7s → ~5.0s
2. **Fast incremental analysis**: Re-analysis with 100 changed files: 5.7s → ~1.5-2.0s
3. **Better scalability**: Unified pipeline for all repo sizes (eliminate streaming branch)

## Design Overview

Five independent changes, each valuable alone:

1. **Persistent Graph Cache** — serialize petgraph to bincode, reload on incremental runs
2. **Phase Overlap** — start calibration during late parse
3. **DetectorContext + Shared Pre-compute** — pre-build caller/callee maps, file contents, class hierarchy
4. **Unified Detection Pipeline** — remove streaming path, all repos use speculative
5. **Shared File Content Cache** — preload file contents once, share across all detectors

## 1. Persistent Graph Cache

### Problem

`GraphStore::new()` creates a fresh petgraph every run. Even in incremental mode (10 files changed out of 10K), we rebuild all 71K nodes + 296K edges from scratch (2.4s).

### Solution

Serialize the full graph state to a bincode cache file. On incremental runs, load the cached graph and patch only changed files.

### Cache Format

```rust
#[derive(Serialize, Deserialize)]
struct GraphCache {
    version: u32,                               // Schema version for invalidation
    binary_version: String,                     // Repotoire binary version
    config_hash: u64,                           // Hash of exclude patterns + config
    digraph: StableGraph<CodeNode, CodeEdge>,   // petgraph (StableGraph for safe removal)
    node_index: HashMap<String, NodeIndex>,     // QN → NodeIndex
    file_nodes: HashMap<String, Vec<NodeIndex>>,// file_path → all nodes in that file
    file_edges: HashMap<String, Vec<EdgeIndex>>,// file_path → all edges from/to that file's nodes
}
```

**Cache location**: `~/.cache/repotoire/<repo-hash>/graph_cache.bin`
**Estimated size (CPython)**: ~20-30MB (bincode-compressed)
**Load time**: ~150-300ms (bincode deserialize ~1GB/s)

### StableGraph Migration

Switch from `DiGraph<CodeNode, CodeEdge>` to `StableGraph<CodeNode, CodeEdge>`:

- `StableGraph` supports node/edge removal without index invalidation
- Same API surface as `DiGraph` for all algorithms (SCC, BFS, DFS via petgraph traits)
- ~5% iteration overhead (skips tombstoned slots)
- One-line type change in `store/mod.rs:28`

### Delta Patching Flow

```
On incremental run (100 files changed):

1. Load graph_cache.bin → GraphCache (~200ms)
2. Rebuild DashMap indexes from HashMap data (~50ms)
3. For each changed/deleted file:
   a) Look up file_nodes[file_path] → Vec<NodeIndex>
   b) Remove all edges to/from these nodes via file_edges index
   c) Remove the nodes themselves (StableGraph handles this safely)
   d) Clean up node_index, file_functions_index, file_classes_index entries
4. Parse 100 changed files → new nodes + edges (~100ms)
5. Insert new nodes + edges into graph (~50ms)
6. Update file_nodes/file_edges indexes for changed files
7. Invalidate call_maps_cache (OnceLock, rebuilt lazily by CachedGraphQuery)
```

### Cache Invalidation

Cache is invalid (fall through to cold build) when:
- Binary version changed (new detectors, parser changes)
- Schema version changed (GraphCache struct changed)
- Config hash changed (exclude patterns, detector config)
- Cache file missing, corrupt, or older than 7 days
- Manual `repotoire clean` command

### Key Files Modified

- `graph/store/mod.rs`: Add `load_cache()`, `save_cache()`, `remove_file_entities()`, change `DiGraph` → `StableGraph`
- `graph/store_models.rs`: No changes (already Serialize/Deserialize)
- `cli/analyze/mod.rs`: `init_graph_db()` tries cache load first
- `parsers/bounded_pipeline.rs`: Runs only on changed files when graph is pre-loaded
- `Cargo.toml`: Add `bincode` dependency

### Impact

- **Cold analysis**: No change (graph still built from scratch)
- **Incremental (100 files)**: init+parse drops from 2.6s to ~0.5s (load cache + parse 100 files + patch)
- **Git enrichment**: Can be scoped to changed files only (future optimization)

## 2. Phase Overlap: Calibration During Parse

### Problem

In the overlapped path (`initialize_graph_overlapped`), `parse_result.parse_results` is empty (line 657) because the streaming pipeline feeds entities directly into the graph. Calibration (`mod.rs:160-194`) needs parse-time metrics but must wait for the full parse to complete.

### Solution

Collect parse-time metrics incrementally during streaming parse via an `AtomicMetricsCollector`:

```rust
pub struct StreamingMetricsCollector {
    complexities: Mutex<Vec<i64>>,
    locs: Mutex<Vec<u32>>,
    param_counts: Mutex<Vec<i64>>,
    nesting_depths: Mutex<Vec<i64>>,
    function_count: AtomicUsize,
    class_count: AtomicUsize,
}
```

Shared with the bounded pipeline as `Arc<StreamingMetricsCollector>`. Each parsed file appends its metrics (~10ms overhead total for CPython).

After parse completes, `collect_metrics()` reads from the collector instead of re-iterating parse results.

### Key Files Modified

- `parsers/bounded_pipeline.rs`: Accept `Arc<StreamingMetricsCollector>`, populate during parse
- `calibrate/mod.rs`: Add `collect_metrics_from_collector()` alternative to `collect_metrics()`
- `cli/analyze/mod.rs`: Create collector before parse, pass to pipeline, use for calibration

### Impact

- First-run calibration overlaps with late parse: ~150ms saved
- Subsequent runs: no change (cached style profile)

## 3. DetectorContext + Shared Pre-compute

### Problem

Detectors independently query the graph for shared data patterns:
- `graph.get_callers(qn)` returns `Vec<CodeNode>` (cloned each call) — used by ~15 detectors
- `graph.get_callees(qn)` returns `Vec<CodeNode>` (cloned each call) — used by ~10 detectors
- File content read via `global_cache()` — used by ~20 detectors independently
- Class hierarchy computed per-detector — used by ~5 detectors

### Solution

Add a `DetectorContext` struct built during `precompute_gd_startup()` and passed to all detectors.

```rust
pub struct DetectorContext {
    /// QN → Vec<caller QN> (avoids Vec<CodeNode> cloning per query)
    pub callers_by_qn: Arc<HashMap<String, Vec<String>>>,
    /// QN → Vec<callee QN>
    pub callees_by_qn: Arc<HashMap<String, Vec<String>>>,
    /// Pre-loaded file content (raw)
    pub file_contents: Arc<HashMap<PathBuf, Arc<str>>>,
    /// Pre-loaded masked content (comments/strings removed)
    pub masked_contents: Arc<HashMap<PathBuf, Arc<str>>>,
    /// Parent QN → Vec<child QN>
    pub class_children: Arc<HashMap<String, Vec<String>>>,
}
```

### Injection via Detector Trait Extension

Add a default method to the existing `Detector` trait:

```rust
pub trait Detector: Send + Sync {
    fn name(&self) -> &str;
    fn detect(&self, graph: &dyn GraphQuery, source_files: &SourceFiles) -> Result<Vec<Finding>>;

    /// Optional: receive shared pre-computed context.
    /// Default: no-op. Override to use context data.
    fn set_detector_context(&mut self, _ctx: Arc<DetectorContext>) {}
}
```

Engine injects context before running detectors:
```rust
for detector in &mut self.detectors {
    Arc::get_mut(detector).map(|d| d.set_detector_context(Arc::clone(&ctx)));
}
```

### Pre-compute Cost (runs in parallel with taint)

| Data | Build Cost | Size (CPython) |
|------|-----------|----------------|
| callers_by_qn | ~50ms | 296K entries |
| callees_by_qn | ~50ms | 296K entries |
| file_contents | ~200ms (par_iter) | 3,415 files, ~50MB |
| masked_contents | ~400ms (par_iter) | 3,415 files, ~40MB |
| class_children | ~20ms | 13K entries |

**Total**: ~400ms, but runs in parallel with taint (1.5s) → **zero additional wall-clock cost**.

### Migration Path

1. Build DetectorContext in `precompute_gd_startup()` (add to `GdPrecomputed`)
2. Add `set_detector_context()` default method to `Detector` trait
3. Migrate top 6 slowest detectors first:
   - ShotgunSurgery: use `callers_by_qn` instead of `graph.get_callers()`
   - UnreachableCode: use `file_contents` for `is_exported_in_source()`, `class_children`
   - BooleanTrap: use `file_contents`, `masked_contents`
   - RegexInLoop: use `file_contents` instead of per-file line caching
   - PathTraversal: use `file_contents`
   - GodClass: use `class_children`
4. Remaining detectors work unchanged via default no-op

### Key Files Modified

- `detectors/engine.rs`: Build DetectorContext in precompute, inject into detectors
- `detectors/base.rs`: Add `set_detector_context()` default method to `Detector` trait
- 6 detector files: Override `set_detector_context()`, use context data
- `GdPrecomputed` struct: Add `detector_context: Arc<DetectorContext>` field

### Impact

- ShotgunSurgery: 546ms → ~300ms (eliminate Vec<CodeNode> cloning in cascade)
- UnreachableCode: 822ms → ~500ms (pre-loaded file content, pre-built class children)
- BooleanTrap: 472ms → ~300ms (pre-loaded content eliminates per-file cache probes)
- Others: 10-30% improvement each
- **Total detect phase**: 2.4s → ~2.0s

## 4. Unified Detection Pipeline

### Problem

Two code paths in `detect.rs`:
- Speculative (`all_files.len() <= 5000`): full GI/GD parallelism
- Streaming (`all_files.len() > 5000`): loses parallelism, writes findings to disk

### Solution

Remove `run_detectors_streaming()`. Use speculative path for all repo sizes.

The streaming path was for memory concerns, but:
- `MAX_FINDINGS_LIMIT = 10_000` already caps findings
- The graph is in memory regardless (petgraph holds all nodes/edges)
- Findings at ~500 bytes each × 10K = 5MB — negligible

### Key Files Modified

- `cli/analyze/detect.rs`: Remove `run_detectors_streaming()`, remove `use_streaming` branch
- `cli/analyze/mod.rs`: Remove `run_detectors_streaming` import

### Impact

- Repos >5K files get same parallelism benefits
- Simpler codebase, one code path to maintain
- No performance change for repos <5K files

## 5. Shared File Content Cache

### Problem

Multiple detectors read file content independently. Even with `global_cache()` memoization, each detector independently decides to read and the cache has per-file locking overhead.

### Solution

Subsumes into Section 3 (DetectorContext). The `file_contents` and `masked_contents` fields in DetectorContext provide the shared cache. Built once at precompute using `rayon::par_iter` for parallel I/O.

Already described in Section 3 — no separate implementation needed.

## Expected Impact Summary

| Change | Cold Analysis | Incremental (100 files) | Effort |
|--------|-------------|------------------------|--------|
| Persistent graph cache | No change | 2.4s → 0.5s | Medium |
| Phase overlap | -0.15s (first run only) | -0.1s | Low |
| DetectorContext | -0.4s | -0.4s | Medium |
| Unified pipeline | No change (already <5K) | Consistent | Low |
| Shared file content | (included in DetectorContext) | — | — |
| **Total** | **5.7s → ~5.1s** | **5.7s → ~1.5-2.0s** | |

## Implementation Order

1. **Unified pipeline** (lowest risk, simplest) — delete streaming path
2. **DetectorContext** (highest cold-analysis ROI) — pre-build shared data, migrate 6 detectors
3. **Persistent graph cache** (highest incremental ROI) — StableGraph + bincode + delta patching
4. **Phase overlap** (small win, low effort) — streaming metrics collector
5. **Validation** — benchmark cold + incremental on CPython, Flask, Django

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| StableGraph iteration overhead | Benchmark before/after; ~5% overhead is acceptable |
| bincode cache corruption | Graceful fallback to cold build on any deserialization error |
| StableGraph API differences | petgraph trait-based algorithms work on both DiGraph and StableGraph |
| DetectorContext memory overhead (50MB+ file contents) | Already in memory via global_cache(); this just formalizes it |
| Cache invalidation misses | Conservative invalidation (any version/config change → full rebuild) |
