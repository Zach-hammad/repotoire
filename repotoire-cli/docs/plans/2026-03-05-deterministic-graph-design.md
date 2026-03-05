# Two-Phase Pipeline for Deterministic Graph Construction

**Date**: 2026-03-05
**Status**: Approved
**Problem**: Non-deterministic graph construction in the overlapped pipeline

## Problem Statement

The overlapped pipeline (`FlushingGraphBuilder` in `bounded_pipeline.rs`) resolves cross-file edges *incrementally* as files stream in from the filesystem walker. Since file discovery order is non-deterministic (OS readdir order, rayon parallelism), the symbol tables (`function_lookup`, `module_lookup`) are incomplete at edge resolution time. This means:

- A call from `module_a.helper()` to `module_b.helper_from_b()` may or may not resolve depending on whether `module_b` was parsed *before* `module_a`
- Missing edges cascade: dead code detection, fan-in/fan-out metrics, circular dependency analysis all diverge
- Cold-start runs (overlapped pipeline) differ from warm-start runs (sequential pipeline with incremental cache)

## Root Cause

In `FlushingGraphBuilder::process()` (bounded_pipeline.rs:276-319), cross-file call edges and import edges are resolved against `self.function_lookup` and `self.module_lookup` — both of which only contain entries for files parsed *so far*. Files arriving later are invisible.

## Chosen Approach: Two-Phase Pipeline

Based on the classic **two-pass assembler** pattern from compiler theory and the PhaseSeed split-phase approach (arXiv 2511.06661):

### Phase 1: Collect (overlaps with file walking)
- Parse files as they stream in (keep rayon parallelism)
- Add all **nodes** to the graph immediately (file, function, class nodes)
- Build complete **symbol tables** (`function_lookup`, `module_lookup`)
- Buffer unresolved cross-file references as `DeferredEdge` structs
- Intra-file edges (Contains, same-file Calls) resolve immediately — these don't need cross-file state

### Phase 2: Resolve (after all files parsed)
- Sort deferred edges for deterministic processing order
- Resolve cross-file call edges against the now-complete `function_lookup`
- Resolve import edges against the now-complete `module_lookup`
- Flush all resolved edges to the graph in a single batch

### DeferredEdge Struct

```rust
struct DeferredEdge {
    kind: DeferredEdgeKind,
    source_qn: String,     // caller qualified name or file path
    target_hint: String,    // unresolved callee name or import path
    file_path: String,      // source file (for import edges)
    properties: Option<Vec<(String, String)>>, // e.g., is_type_only
}

enum DeferredEdgeKind {
    Call,
    Import,
}
```

### Key Invariant

After Phase 2, the graph is identical regardless of file discovery order. The same set of files always produces the same graph because:
1. Node insertion is sorted (already fixed in prior work)
2. Symbol tables are complete before any cross-file resolution
3. Deferred edges are sorted before resolution
4. Resolution is deterministic against complete state

## Scope of Changes

### Primary: `bounded_pipeline.rs`
- Add `DeferredEdge` struct and `deferred_edges: Vec<DeferredEdge>` to `FlushingGraphBuilder`
- Modify `process()`: buffer cross-file calls/imports as `DeferredEdge` instead of resolving immediately
- Modify `finalize()`: sort deferred edges, resolve with complete symbol tables, flush to graph

### Secondary: `cli/analyze/graph.rs`
- Extract shared resolution logic so both pipelines (sequential `build_graph`/`build_graph_chunked` and overlapped) use identical edge resolution code
- Ensures consistency between pipeline paths

### Verification
- 10 cold-start runs (each with `repotoire clean`) must all produce identical output
- Warm-start runs must match cold-start runs
- `cargo test` must pass
- Performance: Phase 2 adds one extra pass over deferred edges — expected <5% wall-clock overhead since edge resolution is cheap relative to tree-sitter parsing

## Non-Goals

- Changing the sequential pipeline's core logic (already deterministic after prior fixes)
- Changing petgraph internals or NodeIndex allocation strategy
- Adding a convergence/fix-up pass (rejected approach)

## Prior Work (This Branch)

Fixes already applied on `perf/optimization-v2` that this design builds on:
- Sorted `parse_results` after rayon collection
- Sorted `file_results` after rayon collection in `build_graph`/`build_graph_chunked`
- Changed `ModuleLookup` from `HashMap` to `BTreeMap`
- Sorted candidate vecs in `ModuleLookup`
- Changed `GraphStore::stats()` to `BTreeMap`
- Sorted all `GraphStore` query results (get_nodes_by_kind, get_callers, get_callees, etc.)
- Sorted detector results by `detector_name` in `engine.rs`
- Sorted taint paths in `centralized.rs`
- Fixed redb edge accumulation bug in `GraphStore::load()`
- Sorted voting engine results and changed group maps to `BTreeMap`
- Added tiebreaker sorts in dead code detector
