# DetectorEngine Elimination

**Date:** 2026-03-17
**Status:** Design approved, pending implementation plan
**Scope:** Replace the 2,274-line DetectorEngine with a ~20-line `run_detectors()` free function and a `PrecomputedAnalysis` struct with a `to_context()` method.

## Problem Statement

Three structs carry the same data through the detection pipeline:

```
precompute_gd_startup() → GdPrecomputed (11 Arc fields)
    ↓ inject_gd_precomputed()
DetectorEngine (15 Option<Arc> fields storing GdPrecomputed data)
    ↓ run_graph_dependent() builds AnalysisContext
AnalysisContext (12 fields — 10 from GdPrecomputed + graph + resolver)
```

`DetectorEngine` is a 2,274-line struct whose actual job is ~60 lines of rayon parallel execution. The other ~2,200 lines are:

- **15 `Option<Arc<...>>` fields** (lines 343-374) that receive GdPrecomputed data via `inject_gd_precomputed()`, store it, then reassemble it into `AnalysisContext`
- **4 inject methods** (`inject_gd_precomputed`, `inject_cached_precomputed`, `inject_for_incremental`, `inject_minimal_for_file_local`) — each copies Arc fields into Option fields
- **1 extract method** (`extract_precomputed`) — copies Option fields back into GdPrecomputed
- **HMM context building** (~200 lines) duplicated from `precompute_gd_startup`
- **Builder pattern** (`DetectorEngineBuilder`, ~80 lines) for configuration
- **`run()`, `run_detailed()`, `run_graph_independent()`, `run_graph_dependent()`** — four execution methods with overlapping logic

The engine is a state-holding pass-through. It receives data it didn't create, stores it in fields it doesn't use directly, and passes it to `AnalysisContext` which detectors actually read.

### Why now

The recent refactors created the conditions to eliminate it:
- **AnalysisEngine** (`engine/mod.rs`) owns orchestration — detect_stage is just a function
- **`create_all_detectors()`** owns detector construction — the registry replaces the engine's role as detector holder
- **`precompute_gd_startup()`** owns the expensive computation — always has
- **`AnalysisContext`** owns the detector interface — detectors read this, not the engine

DetectorEngine is the layer that no longer has a reason to exist.

## Design

### PrecomputedAnalysis (renamed from GdPrecomputed)

Same 11 Arc fields (minus the unused `hmm_contexts`), plus a `to_context()` method that produces `AnalysisContext<'g>`:

```rust
/// Pre-computed analysis data built by `precompute_gd_startup()`.
///
/// All fields are Arc-wrapped for cheap cloning (~ns per clone).
/// Cached by AnalysisEngine across incremental runs to avoid
/// the ~3.9s precomputation overhead.
pub struct PrecomputedAnalysis {
    pub contexts: Arc<FunctionContextMap>,
    pub hmm_with_confidence: Arc<HashMap<String, (FunctionContext, f64)>>,
    pub taint_results: Arc<CentralizedTaintResults>,
    pub detector_context: Arc<DetectorContext>,
    pub file_index: Arc<FileIndex>,
    pub reachability: Arc<ReachabilityIndex>,
    pub public_api: Arc<HashSet<String>>,
    pub module_metrics: Arc<HashMap<String, ModuleMetrics>>,
    pub class_cohesion: Arc<HashMap<String, f64>>,
    pub decorator_index: Arc<HashMap<String, Vec<String>>>,
}

impl PrecomputedAnalysis {
    /// Combine precomputed data with a graph reference and resolver
    /// to produce the AnalysisContext that detectors receive.
    pub fn to_context<'g>(
        &self,
        graph: &'g dyn GraphQuery,
        resolver: &ThresholdResolver,
    ) -> AnalysisContext<'g> {
        AnalysisContext {
            graph,
            files: Arc::clone(&self.file_index),
            functions: Arc::clone(&self.contexts),
            taint: Arc::clone(&self.taint_results),
            detector_ctx: Arc::clone(&self.detector_context),
            hmm_classifications: Arc::clone(&self.hmm_with_confidence),
            resolver: Arc::new(resolver.clone()),
            reachability: Arc::clone(&self.reachability),
            public_api: Arc::clone(&self.public_api),
            module_metrics: Arc::clone(&self.module_metrics),
            class_cohesion: Arc::clone(&self.class_cohesion),
            decorator_index: Arc::clone(&self.decorator_index),
        }
    }
}
```

**Changes from GdPrecomputed:**
- Renamed to `PrecomputedAnalysis`
- Removed `hmm_contexts` field (only `hmm_with_confidence` is used by AnalysisContext)
- Added `to_context()` method (eliminates the field-by-field duplication)
- `Clone` impl unchanged (all Arc::clone)

### run_detectors() — The Core Execution Function

Replaces DetectorEngine's `run()`, `run_graph_independent()`, `run_graph_dependent()`, and `run_detailed()`.

```rust
/// Run all detectors in parallel against the given context.
///
/// Detectors are executed via rayon. Panics in individual detectors
/// are caught and logged — they don't crash the analysis.
/// Findings are collected and returned as a flat Vec.
pub fn run_detectors(
    detectors: &[Arc<dyn Detector>],
    ctx: &AnalysisContext,
    workers: usize,
) -> Vec<Finding> {
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(workers.max(1))
        .stack_size(8 * 1024 * 1024)  // 8MB — required for deeply nested C/C++ ASTs
        .build()
        .unwrap_or_else(|_| rayon::ThreadPoolBuilder::new().build().unwrap());

    pool.install(|| {
        detectors.par_iter()
            .flat_map(|detector| {
                let start = std::time::Instant::now();
                // detect() returns Result<Vec<Finding>> — catch_unwind adds another Result layer
                let result = std::panic::catch_unwind(
                    std::panic::AssertUnwindSafe(|| detector.detect(ctx))
                );
                let elapsed = start.elapsed();
                match result {
                    Ok(Ok(mut findings)) => {
                        // Apply per-detector max_findings limit
                        if let Some(config) = detector.config() {
                            if let Some(max) = config.max_findings {
                                if findings.len() > max {
                                    findings.truncate(max);
                                }
                            }
                        }
                        tracing::debug!(
                            "{}: {} findings ({:.1}ms)",
                            detector.name(),
                            findings.len(),
                            elapsed.as_secs_f64() * 1000.0
                        );
                        findings
                    }
                    Ok(Err(e)) => {
                        tracing::debug!(
                            "{} returned error after {:.1}ms: {}",
                            detector.name(),
                            elapsed.as_secs_f64() * 1000.0,
                            e
                        );
                        vec![]
                    }
                    Err(_) => {
                        tracing::error!(
                            "{} panicked after {:.1}ms",
                            detector.name(),
                            elapsed.as_secs_f64() * 1000.0
                        );
                        vec![]
                    }
                }
            })
            .collect()
    })
}
```

~30 lines. This is the actual work that DetectorEngine's 2,274 lines exist to perform.

### inject_taint_precomputed() — Preserved Helper

Security detectors that implement `set_precomputed_taint()` need taint paths injected before `detect()` is called. This is currently done inside `DetectorEngine::run_graph_dependent()`. Extract it as a free function:

```rust
/// Inject precomputed taint paths into security detectors.
///
/// Security detectors that implement `taint_category()` receive
/// pre-filtered taint paths matching their category (sql, xss, etc.).
pub fn inject_taint_precomputed(
    detectors: &[Arc<dyn Detector>],
    precomputed: &PrecomputedAnalysis,
) {
    for detector in detectors {
        if let Some(category) = detector.taint_category() {
            let cross = precomputed.taint_results.cross_function
                .get(category).cloned().unwrap_or_default();
            let intra = precomputed.taint_results.intra_function
                .get(category).cloned().unwrap_or_default();
            detector.set_precomputed_taint(cross, intra);
        }
    }
}
```

### detect_stage — The New Orchestrator

```rust
pub fn detect_stage(input: &DetectInput) -> Result<DetectOutput> {
    let skip_set: HashSet<&str> = input.skip_detectors.iter().map(|s| s.as_str()).collect();

    // 1. Build detectors via registry
    let resolver = build_threshold_resolver(input.style_profile);
    let init = DetectorInit {
        repo_path: input.repo_path,
        project_config: input.project_config,
        resolver: resolver.clone(),
        ngram_model: input.ngram_model,
    };
    let detectors: Vec<_> = create_all_detectors(&init)
        .into_iter()
        .filter(|d| !skip_set.contains(d.name()))
        .collect();

    // 2. Wrap graph in CachedGraphQuery (memoizes get_functions, call maps, etc.)
    let cached_graph = CachedGraphQuery::new(input.graph);

    // 3. Precompute (or reuse cached)
    let precompute_start = Instant::now();
    let precomputed = if let Some(cached) = input.cached_precomputed {
        if !input.topology_changed {
            cached.clone()
        } else {
            precompute_analysis(&cached_graph, input.repo_path, input.source_files, &detectors)
        }
    } else {
        precompute_analysis(&cached_graph, input.repo_path, input.source_files, &detectors)
    };
    let precompute_duration = precompute_start.elapsed();

    // 4. Inject taint into security detectors
    inject_taint_precomputed(&detectors, &precomputed);

    // 5. Build context and run all detectors
    let ctx = precomputed.to_context(&cached_graph, &resolver);
    let mut findings = run_detectors(&detectors, &ctx, input.workers);

    // 6. Post-detection filters (moved from old DetectorEngine)
    // a. HMM context filter — reduces FPs for coupling/dead-code detectors
    apply_hmm_context_filter(&mut findings, &ctx);
    // b. Test file filter — remove findings where all affected files are tests
    findings.retain(|f| !f.affected_files.iter().all(|p| is_test_file(p)));
    // c. Deterministic sort for reproducible output
    findings.sort_by(|a, b| {
        a.severity.cmp(&b.severity)
            .then_with(|| a.affected_files.cmp(&b.affected_files))
            .then_with(|| a.line_start.cmp(&b.line_start))
            .then_with(|| a.detector.cmp(&b.detector))
    });

    // 7. Partition findings
    let mut findings_by_file = HashMap::new();
    let mut graph_wide_findings = HashMap::new();
    for finding in &findings {
        if finding.affected_files.is_empty() {
            graph_wide_findings.entry(finding.detector.clone())
                .or_insert_with(Vec::new).push(finding.clone());
        } else {
            for file in &finding.affected_files {
                findings_by_file.entry(file.clone())
                    .or_insert_with(Vec::new).push(finding.clone());
            }
        }
    }

    Ok(DetectOutput {
        findings,
        precomputed,
        findings_by_file,
        graph_wide_findings,
        stats: DetectStats {
            detectors_run: detectors.len(),
            detectors_skipped: skip_set.len(),
            gi_findings: 0,
            gd_findings: 0,
            precompute_duration,
        },
    })
}
```

**Post-detection filters (extracted from DetectorEngine):**
- `apply_hmm_context_filter()` — reduces FPs for coupling and dead-code detectors based on HMM function context classification. Currently lives in engine.rs (~60 lines). Extracted as a free function taking `(&mut Vec<Finding>, &AnalysisContext)`.
- Test file filtering — `is_test_file()` already exists in `detectors/base.rs`. Applied as a simple `retain()`.
- Deterministic sort — ensures reproducible output across runs (matches existing canonical sort in the engine).

### DetectOutput Change

```rust
pub struct DetectOutput {
    pub findings: Vec<Finding>,
    pub precomputed: PrecomputedAnalysis,  // was: gd_precomputed: GdPrecomputed
    pub findings_by_file: HashMap<PathBuf, Vec<Finding>>,
    pub graph_wide_findings: HashMap<String, Vec<Finding>>,
    pub stats: DetectStats,
}
```

### EngineState Change

```rust
// In engine/state.rs:
pub(crate) struct EngineState {
    // ...
    pub precomputed: Option<PrecomputedAnalysis>,  // was: gd_precomputed: Option<GdPrecomputed>
    // ...
}
```

## What Gets Deleted

| Code | Lines | Reason |
|------|-------|--------|
| `DetectorEngine` struct | 42 | Replaced by `run_detectors()` |
| `DetectorEngine` impl (all methods) | ~1,600 | 4 inject methods, 4 run methods, HMM building, context extraction, getters |
| `DetectorEngineBuilder` struct + impl | ~80 | No longer needed — detect_stage builds detectors directly |
| `hmm_contexts` field in GdPrecomputed | ~5 | Only `hmm_with_confidence` is used |
| `build_hmm_contexts_standalone()` | ~200 | Remains in `precompute_gd_startup` only |
| GI/GD split execution logic | ~200 | All detectors run in one pass |
| **Total deleted** | ~2,100 | |

## What Gets Added

| Code | Lines | Purpose |
|------|-------|---------|
| `PrecomputedAnalysis::to_context()` | ~15 | Builds AnalysisContext from precomputed data |
| `run_detectors()` | ~40 | Parallel detector execution with panic recovery + per-detector truncation |
| `inject_taint_precomputed()` | ~15 | Taint injection for security detectors |
| `apply_hmm_context_filter()` | ~60 | Extracted from engine (FP reduction for coupling/dead-code detectors) |
| Test file filter + deterministic sort in detect_stage | ~10 | Extracted from engine run methods |
| Rename GdPrecomputed → PrecomputedAnalysis | ~5 | Clarity |
| **Total added** | ~145 | |

**Net: ~2,100 deleted, ~65 added.** The largest single-file reduction in the refactor series.

## What Stays Unchanged

- `precompute_gd_startup()` (~180 lines) — the parallel precomputation of taint, HMM, contexts, etc. This is real work, not boilerplate. Renamed to `precompute_analysis()` for clarity.
- `AnalysisContext` — unchanged. Detectors still receive `&AnalysisContext`.
- `DetectorContext` — unchanged. It's a legitimate sub-component.
- `Detector` trait — unchanged. `detect(&self, ctx: &AnalysisContext) -> Vec<Finding>`.
- `RegisteredDetector` trait — unchanged.
- All 100 detectors — unchanged.

## Callers to Update

| Caller | File | Current | New |
|--------|------|---------|-----|
| `detect_stage` | `engine/stages/detect.rs` | Creates `DetectorEngine`, injects GdPrecomputed, runs | Calls `precompute_analysis()` + `to_context()` + `run_detectors()` |
| AnalysisEngine state | `engine/mod.rs`, `engine/state.rs` | Stores `GdPrecomputed` | Stores `PrecomputedAnalysis` |
| `session.rs` (3 sites) | `session.rs` | Creates `DetectorEngine`, builder, inject | Same pattern as detect_stage |
| `streaming_engine.rs` | `detectors/streaming_engine.rs` | Uses `DetectorEngine` | Same pattern |
| MCP oneshot | `mcp/tools/analysis.rs` | Uses `DetectorEngineBuilder` | Same pattern |
| `create_default_engine` | `detectors/mod.rs` | Returns `DetectorEngine` | Delete or return detectors Vec |
| Tests in `engine.rs` | `detectors/engine.rs` | Test `DetectorEngine` API | Test `run_detectors()` + `precompute_analysis()` |
| Legacy CLI detect | `cli/analyze/detect.rs` | 3 usages of DetectorEngine | Delete file (already `#[allow(dead_code)]` legacy) |

## GI/GD Split

**Eliminated.** All detectors run in one pass via `run_detectors()`. The `DetectorScope` enum is preserved (used by incremental analysis to decide what to re-run) but is no longer used for execution partitioning.

## Dependent Detector Sequencing

The current engine partitions detectors into independent (parallel) and dependent (sequential) groups via `Detector::is_dependent()`. No current detector overrides `is_dependent()` — it defaults to `false` for all 100 detectors. The new `run_detectors()` runs all detectors in parallel. If a detector ever needs sequential execution, `run_detectors()` can be extended with a two-phase approach, but this is YAGNI today.

Previously, GI detectors ran in parallel with GD precomputation to save ~1.5s. This optimization is dropped because:
- The detect_stage already runs everything sequentially (and has been stable)
- Incremental runs skip precompute entirely (zero benefit from the split)
- Cold runs are dominated by parsing + graph building (5-10s), not detection precompute
- One execution path is easier to reason about and test

If cold performance becomes a bottleneck, the optimization can be re-added inside `detect_stage` as an internal detail without changing any public API.

## Behavior Changes

### Intentional

1. **`hmm_contexts` (without confidence) dropped.** Only `hmm_with_confidence` was used by `AnalysisContext`. The plain `hmm_contexts` HashMap was a legacy field that `DetectorEngine` stored but never passed to detectors. No behavior change — detectors already only see `hmm_classifications` (which is `hmm_with_confidence`).

2. **GI/GD split removed.** All detectors run after precomputation completes. On cold runs, this may add ~1.5s (GI detectors wait for precompute instead of running in parallel). Offset by reduced complexity and consistent execution model.

### Preserved

1. All 100 detectors produce identical findings (same `detect()` method, same `AnalysisContext`).
2. Panic recovery — `run_detectors()` catches panics per-detector, same as `DetectorEngine`.
3. Parallel execution via rayon — same parallelism model.
4. Taint injection — security detectors receive pre-filtered taint paths before `detect()`.
5. Test file filtering — if needed, can be done at the finding level (post-detect) rather than pre-detect.

## Migration Path

### Phase 1: Add new types alongside old (no behavior change)

- Add `PrecomputedAnalysis` as a type alias or wrapper for `GdPrecomputed`
- Add `to_context()` method
- Add `run_detectors()` free function
- Add `inject_taint_precomputed()` free function
- Everything compiles, old engine still active

### Phase 2: Switch detect_stage to new path

- Rewrite `detect_stage` to use `precompute_analysis()` + `to_context()` + `run_detectors()`
- Remove dependency on `DetectorEngine` from detect_stage
- Update `DetectOutput` field name (`gd_precomputed` → `precomputed`)
- Update `EngineState` field name
- Verify CLI produces identical results

### Phase 3: Switch remaining callers

- Update `session.rs`, `streaming_engine.rs`, MCP oneshot, tests
- Delete `DetectorEngine`, `DetectorEngineBuilder`
- Delete `create_default_engine` if unused
- Rename `GdPrecomputed` → `PrecomputedAnalysis`
- Rename `precompute_gd_startup` → `precompute_analysis`

### Phase 4: Verification

- Run full test suite
- Compare findings on real repo before/after
- Verify incremental analysis still works (cached PrecomputedAnalysis)

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Missing taint injection causes security detector FPs | Security findings change | `inject_taint_precomputed()` extracted as explicit step; verified by existing security detector tests |
| GI/GD split removal adds ~1.5s to cold runs | Slower cold analysis | Marginal vs total cold time (5-10s); can re-add as internal optimization later |
| Test file filtering behavior change | More findings from test files | Current engine's `skip_test_files` flag can be replicated as post-filter if needed |
| Session.rs callers need significant rewrite | Merge complexity | Session.rs is deprecated (engine handles persistence); minimal maintenance |
