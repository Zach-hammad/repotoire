# GraphStore Rearchitecture — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the 1,736-line GraphStore monolith into a Builder/Frozen architecture with pre-built indexes, redesign GraphQuery for index-based O(1) access, and eliminate CachedGraphQuery.

**Architecture:** `GraphBuilder` (mutable, `&mut self`) accumulates nodes/edges during parse + git enrichment. `freeze()` builds all indexes and produces `CodeGraph` (immutable, `&self`, no locks). `GraphQuery` trait redesigned for `NodeIndex`-based returns. `CachedGraphQuery` eliminated — indexes are built-in.

**Tech Stack:** Rust, petgraph, no new dependencies

**Spec:** `docs/superpowers/specs/2026-03-17-graphstore-rearchitecture-design.md`

---

## File Structure

### Files to create

| File | Purpose |
|------|---------|
| `repotoire-cli/src/graph/builder.rs` | `GraphBuilder` — mutable accumulator (~400 lines) |
| `repotoire-cli/src/graph/frozen.rs` | `CodeGraph` — immutable indexed graph + GraphQuery impl (~300 lines) |
| `repotoire-cli/src/graph/indexes.rs` | `GraphIndexes` struct + `freeze()` index-building logic (~200 lines) |
| `repotoire-cli/src/graph/persistence.rs` | redb save/load + bincode cache for CodeGraph (~300 lines) |
| `repotoire-cli/src/graph/compat.rs` | Deprecated bridge methods for gradual migration (~200 lines) |
| `repotoire-cli/src/graph/metrics_cache.rs` | `MetricsCache` extracted from GraphStore (~40 lines) |

### Files to modify

| File | Change |
|------|--------|
| `repotoire-cli/src/graph/mod.rs` | Re-exports for new types |
| `repotoire-cli/src/graph/traits.rs` | Redesigned GraphQuery trait |
| `repotoire-cli/src/graph/store_query.rs` | GraphQuery impl for Arc<CodeGraph> (was Arc<GraphStore>) |
| `repotoire-cli/src/engine/stages/graph.rs` | Use GraphBuilder → freeze() |
| `repotoire-cli/src/engine/stages/detect.rs` | Remove CachedGraphQuery wrapper |
| `repotoire-cli/src/engine/stages/git_enrich.rs` | Take &mut GraphBuilder instead of &GraphStore |
| `repotoire-cli/src/engine/mod.rs` | Store Arc<CodeGraph>, pipeline order change |
| `repotoire-cli/src/engine/state.rs` | Arc<CodeGraph> instead of Arc<GraphStore> |
| `repotoire-cli/src/scoring/graph_scorer.rs` | Take (&CodeGraph, &MetricsCache) |
| `repotoire-cli/src/detectors/analysis_context.rs` | Add MetricsCache field |
| ~80 detector files | Migrate from old GraphQuery methods to new (gradual, via bridges) |
| `repotoire-cli/src/session.rs` | Arc<CodeGraph> |
| `repotoire-cli/src/mcp/state.rs` | Arc<CodeGraph> |

### Files to delete

| File | Reason |
|------|--------|
| `repotoire-cli/src/graph/store/mod.rs` | Replaced by builder.rs + frozen.rs + indexes.rs + persistence.rs |
| `repotoire-cli/src/graph/store/tests.rs` | Tests moved to new files |
| `repotoire-cli/src/graph/cached.rs` | Eliminated — indexes are built into CodeGraph |

---

## Chunk 1: Phase A — Create GraphBuilder + CodeGraph + Indexes (internal, no behavior change)

### Task 1: Create GraphIndexes and freeze() logic

**Files:**
- Create: `repotoire-cli/src/graph/indexes.rs`

- [ ] **Step 1: Create indexes.rs with GraphIndexes struct**

Define the struct with all index fields (kind indexes, adjacency indexes per edge kind, spatial indexes, pre-computed edge lists, import cycles, edge fingerprint). Add a `build()` method that takes a `&StableGraph` + `&HashMap<StrKey, NodeIndex>` and populates all indexes in one pass. Include the sorting of adjacency vectors by qualified name for determinism.

- [ ] **Step 2: Write tests for index building**

Test that freeze produces correct:
- Kind indexes (function count matches node scan)
- Adjacency (callers/callees match edge scan)
- Spatial (functions_in_file matches filter)
- Edge fingerprint (deterministic for same graph)

- [ ] **Step 3: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 4: Commit**

```bash
git commit -am "feat: add GraphIndexes with freeze() index-building logic"
```

### Task 2: Create GraphBuilder

**Files:**
- Create: `repotoire-cli/src/graph/builder.rs`

- [ ] **Step 1: Create builder.rs with GraphBuilder struct**

Extract mutation methods from `store/mod.rs` into `GraphBuilder`:
- `new()`, `add_node()`, `add_nodes_batch()`, `add_nodes_batch_with_contains()`
- `add_edge()`, `add_edge_by_name()`, `add_edges_batch()`
- `get_node_index()`, `update_node_property()`, `update_node_properties()`
- `set_extra_props()`, `get_extra_props()`, `remove_file_entities()`
- `node_count()`, `edge_count()`, `interner()`

Read methods needed by git enrichment:
- `get_functions()`, `get_classes()`, `get_node()`

All methods take `&mut self` (mutation) or `&self` (reads). No RwLock, no DashMap, no Mutex — plain HashMap, HashSet.

Add `freeze(self) -> CodeGraph` that calls `GraphIndexes::build()`.

Add `from_frozen(CodeGraph) -> Self` (O(E) for edge_set rebuild).

- [ ] **Step 2: Write tests**

- Test add_node + add_edge + freeze produces valid CodeGraph
- Test remove_file_entities clears nodes and edges
- Test from_frozen roundtrip (freeze → from_frozen → re-freeze produces same graph)

- [ ] **Step 3: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 4: Commit**

```bash
git commit -am "feat: add GraphBuilder with mutation methods extracted from GraphStore"
```

### Task 3: Create CodeGraph (frozen, implements queries)

**Files:**
- Create: `repotoire-cli/src/graph/frozen.rs`

- [ ] **Step 1: Create frozen.rs with CodeGraph struct**

The struct holds: `StableGraph`, `HashMap<StrKey, NodeIndex>` (node_index), `HashMap<StrKey, ExtraProps>`, `GraphIndexes`.

Implement query methods that read from pre-built indexes:
- `node(idx) -> Option<&CodeNode>` — direct graph access
- `node_by_name(qn) -> Option<(NodeIndex, &CodeNode)>` — HashMap lookup
- `functions() -> &[NodeIndex]` — slice from kind index
- `callers(idx) -> &[NodeIndex]` — slice from adjacency index (empty slice if not found)
- `callees(idx)`, `importers(idx)`, etc.
- `functions_in_file(path) -> &[NodeIndex]` — intern path, lookup spatial
- `function_at(path, line) -> Option<NodeIndex>` — binary search on spatial
- `import_cycles() -> &[Vec<NodeIndex>]`
- `edge_fingerprint() -> u64`
- `stats() -> BTreeMap<String, i64>`
- `extra_props(qn) -> Option<&ExtraProps>`
- `raw_graph() -> &StableGraph<CodeNode, CodeEdge>`
- `into_builder(self) -> GraphBuilder`
- `clone_into_builder(&self) -> GraphBuilder`

- [ ] **Step 2: Write tests**

- Test node() returns correct CodeNode
- Test callers() returns correct caller indexes
- Test functions_in_file() matches expected
- Test function_at() binary search finds correct function
- Test empty adjacency returns empty slice (not panic)

- [ ] **Step 3: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 4: Commit**

```bash
git commit -am "feat: add CodeGraph — immutable indexed graph with O(1) queries"
```

### Task 4: Create compat bridges and MetricsCache

**Files:**
- Create: `repotoire-cli/src/graph/compat.rs`
- Create: `repotoire-cli/src/graph/metrics_cache.rs`

- [ ] **Step 1: Create compat.rs with deprecated bridge methods on CodeGraph**

Implement ALL bridge methods from the spec's inventory table. Each bridge converts from old API (Vec<CodeNode>, &str params) to new API (NodeIndex, slices). Mark each `#[deprecated]`.

Key bridges:
- `get_functions() -> Vec<CodeNode>` — `self.functions().iter().filter_map(|&i| self.node(i).copied()).collect()`
- `get_callers(qn: &str) -> Vec<CodeNode>` — lookup by name, iterate callers, copy nodes
- `get_calls() -> Vec<(StrKey, StrKey)>` — iterate all_call_edges, resolve QNs
- `get_imports() -> Vec<(StrKey, StrKey)>` — iterate all_import_edges
- `build_call_maps_raw()` — build from callers()/callees() per function
- `find_import_cycles() -> Vec<Vec<String>>` — resolve NodeIndex cycles to QN strings
- `compute_coupling_stats()` — iterate adjacency, check cross-file edges
- `find_minimal_cycle()` — BFS on raw_graph()

- [ ] **Step 2: Create metrics_cache.rs**

```rust
pub struct MetricsCache {
    cache: DashMap<String, f64>,
}
impl MetricsCache {
    pub fn new() -> Self { Self { cache: DashMap::new() } }
    pub fn set(&self, key: &str, value: f64) { self.cache.insert(key.to_string(), value); }
    pub fn get(&self, key: &str) -> Option<f64> { self.cache.get(key).map(|v| *v) }
    pub fn get_with_prefix(&self, prefix: &str) -> Vec<(String, f64)> { ... }
}
```

- [ ] **Step 3: Register all new modules in graph/mod.rs**

```rust
pub mod builder;
pub mod frozen;
pub mod indexes;
pub mod persistence;
pub mod compat;
pub mod metrics_cache;
```

Add re-exports for `GraphBuilder`, `CodeGraph`, `MetricsCache`.

- [ ] **Step 4: Add `pub type GraphStore = CodeGraph;` alias**

In `graph/mod.rs`, add the backward-compat type alias so all existing code continues to compile.

- [ ] **Step 5: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 6: Commit**

```bash
git commit -am "feat: add compat bridges, MetricsCache, and GraphStore type alias"
```

---

## Chunk 2: Phase B — Wire Pipeline to Builder/Frozen

### Task 5: Create persistence layer for CodeGraph

**Files:**
- Create: `repotoire-cli/src/graph/persistence.rs`

- [ ] **Step 1: Implement bincode save/load for CodeGraph**

Extract `save_graph_cache()` and `load_graph_cache()` from `store/mod.rs`. Adapt for CodeGraph:
- `save_cache()` serializes the StableGraph + node_index + extra_props via bincode
- `load_cache()` deserializes, then calls `GraphIndexes::build()` to reconstruct indexes

- [ ] **Step 2: Implement redb save/load for GraphBuilder**

Extract `save()` and `load()` from `store/mod.rs`. Adapt for GraphBuilder:
- `save_redb()` writes nodes/edges to redb tables
- `load_redb()` reads from redb into a builder

- [ ] **Step 3: Write roundtrip tests**

- [ ] **Step 4: Commit**

```bash
git commit -am "feat: add persistence layer for CodeGraph (bincode) and GraphBuilder (redb)"
```

### Task 6: Wire graph_stage to use GraphBuilder → freeze()

**Files:**
- Modify: `repotoire-cli/src/engine/stages/graph.rs`

- [ ] **Step 1: Rewrite graph_stage**

Currently creates a `GraphStore::in_memory()` and calls `build_graph()`. Change to:
1. Create `GraphBuilder::new()`
2. Call the existing graph building logic (from `cli/analyze/graph.rs`) which adds nodes/edges
3. Call `builder.freeze()` to produce `CodeGraph`
4. Read `edge_fingerprint` from the frozen graph

The `build_graph()` function in `cli/analyze/graph.rs` currently takes `&GraphStore`. It needs to take `&mut GraphBuilder` instead. Widen the function signature or create a wrapper.

- [ ] **Step 2: Rewrite graph_patch_stage**

1. `Arc::try_unwrap(graph).unwrap_or_else(|arc| arc.clone_into_builder())` → get builder
2. `builder.remove_file_entities(&changed_files)`
3. Add new parse results
4. `builder.freeze()` → new CodeGraph

- [ ] **Step 3: Update GraphOutput type**

Change `graph: Arc<GraphStore>` to `graph: Arc<CodeGraph>`.

- [ ] **Step 4: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 5: Commit**

```bash
git commit -am "feat: wire graph_stage to use GraphBuilder -> freeze() -> CodeGraph"
```

### Task 7: Wire git enrichment to run on builder before freeze

**Files:**
- Modify: `repotoire-cli/src/engine/stages/git_enrich.rs`
- Modify: `repotoire-cli/src/engine/mod.rs`

- [ ] **Step 1: Change git_enrich_stage to take &mut GraphBuilder**

Update `GitEnrichInput.graph` from `&GraphStore` to `&mut GraphBuilder`. The enricher calls `get_functions()`, `get_classes()`, `update_node_properties()`, `add_node()`, `add_edge_by_name()`, `set_extra_props()` — all available on `GraphBuilder`.

- [ ] **Step 2: Update engine pipeline order**

In `engine/mod.rs`, change the analyze flow:
```
Current:  graph_stage → [git_enrich ∥ calibrate] → detect
New:      build_graph (builder) → git_enrich (on builder) → freeze → [calibrate ∥ detect]
```

Git enrichment runs sequentially on the builder before freeze. Calibration can still run in parallel with detection since it doesn't need the graph.

- [ ] **Step 3: Update EngineState to store Arc<CodeGraph>**

- [ ] **Step 4: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 5: Smoke test**

```bash
cargo run -- analyze . --format json --no-git --max-files 20 2>/dev/null | head -5
```

- [ ] **Step 6: Commit**

```bash
git commit -am "feat: wire git enrichment to builder, freeze before detection"
```

### Task 8: Update remaining Arc<GraphStore> references

**Files:**
- Modify: `engine/state.rs`, `engine/stages/score.rs`, `engine/stages/postprocess.rs`, `session.rs`, `mcp/state.rs`, `scoring/graph_scorer.rs`, `cli/analyze/scoring.rs`

- [ ] **Step 1: Replace Arc<GraphStore> with Arc<CodeGraph> everywhere**

The `GraphStore` type alias (`pub type GraphStore = CodeGraph`) handles most cases automatically. But files that import `GraphStore` by name need updating:

```bash
grep -rn "use.*GraphStore" repotoire-cli/src/ --include="*.rs"
```

Update imports to use `CodeGraph` directly. Leave the alias for any remaining edge cases.

- [ ] **Step 2: Update GraphScorer to take (&CodeGraph, &MetricsCache)**

`GraphScorer::new()` currently takes `&GraphStore`. Add `MetricsCache` parameter. The scorer reads metrics via `MetricsCache` instead of `graph.get_cached_metric()`.

- [ ] **Step 3: Update score_stage to pass MetricsCache**

Change `ScoreInput` to include `&MetricsCache` (or pass the concrete `&CodeGraph` instead of `&dyn GraphQuery`).

- [ ] **Step 4: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 5: Commit**

```bash
git commit -am "refactor: replace Arc<GraphStore> with Arc<CodeGraph> across codebase"
```

---

## Chunk 3: Phase C — GraphQuery Trait Redesign

### Task 9: Add new GraphQuery trait methods alongside old

**Files:**
- Modify: `repotoire-cli/src/graph/traits.rs`
- Modify: `repotoire-cli/src/graph/frozen.rs` (impl)
- Modify: `repotoire-cli/src/graph/store_query.rs` (impl for Arc<CodeGraph>)

- [ ] **Step 1: Add new methods to GraphQuery trait**

Add all new methods from the spec's trait design:
- `node()`, `node_by_name()`
- `functions()`, `classes()`, `files()` returning `&[NodeIndex]`
- `callers()`, `callees()`, `importers()`, `importees()`, `parent_classes()`, `child_classes()`
- `call_fan_in()`, `call_fan_out()` (default impls)
- `functions_in_file()`, `classes_in_file()`, `function_at()`
- `all_call_edges()`, `all_import_edges()`, `all_inheritance_edges()`
- `import_cycles()`, `edge_fingerprint()`

Provide default implementations that delegate to old methods (so existing GraphQuery implementors don't break):

```rust
fn functions(&self) -> &[NodeIndex] {
    &[] // Default: empty. CodeGraph overrides with real index.
}
```

- [ ] **Step 2: Implement new methods on CodeGraph**

In `frozen.rs`, implement all new methods reading from `GraphIndexes`.

- [ ] **Step 3: Update store_query.rs**

Implement new methods for `Arc<CodeGraph>` by delegating to `(**self).method()`.

- [ ] **Step 4: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 5: Commit**

```bash
git commit -am "feat: add new GraphQuery trait methods (NodeIndex-based, O(1))"
```

### Task 10: Migrate consumer files to new GraphQuery API (batch 1: engine + infrastructure)

**Files:**
- Modify: `engine/stages/detect.rs`, `detectors/runner.rs`, `detectors/analysis_context.rs`, `detectors/detector_context.rs`, `detectors/function_context.rs`, `detectors/reachability.rs`, `detectors/module_metrics.rs`, `scoring/graph_scorer.rs`

- [ ] **Step 1: Remove CachedGraphQuery from detect_stage**

The detect_stage currently wraps the graph in `CachedGraphQuery::new(graph)`. Remove this — pass the `CodeGraph` directly. All queries are now O(1) from built-in indexes.

- [ ] **Step 2: Migrate infrastructure files**

Update `detector_context.rs`, `function_context.rs`, `reachability.rs`, `module_metrics.rs` to use new GraphQuery methods where possible. These files build derived data from the graph — they benefit most from index-based access.

Pattern: replace `graph.get_functions()` (returns Vec<CodeNode>) with `graph.functions()` (returns &[NodeIndex]) + `graph.node(idx)`.

- [ ] **Step 3: Update graph_scorer.rs**

Replace `graph.get_functions()` with `graph.functions()` + `graph.node()`. Use `MetricsCache` for metric reads/writes.

- [ ] **Step 4: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 5: Commit**

```bash
git commit -am "refactor: migrate engine + infrastructure to new GraphQuery API"
```

### Task 11: Migrate consumer files (batch 2: detectors)

**Files:**
- Modify: ~80 detector files that access graph through AnalysisContext

- [ ] **Step 1: Identify which detectors call old GraphQuery methods**

```bash
grep -rn "\.get_functions()\|\.get_callers(\|\.get_callees(\|\.get_imports()\|\.get_calls()" repotoire-cli/src/detectors/ --include="*.rs" | grep -v compat | grep -v "mod.rs"
```

Most detectors access the graph through `ctx.graph` (which is `&dyn GraphQuery`). They need to migrate from `ctx.graph.get_callers(qn)` to `ctx.graph.node_by_name(qn)` + `ctx.graph.callers(idx)`.

- [ ] **Step 2: Migrate detectors in batches**

Work through detectors alphabetically, ~20 per commit. The migration is mechanical:

```rust
// Before:
let callers = ctx.graph.get_callers(&func_qn);
for caller in &callers { ... }

// After:
if let Some((idx, _)) = ctx.graph.node_by_name(&func_qn) {
    for &caller_idx in ctx.graph.callers(idx) {
        let caller = ctx.graph.node(caller_idx).unwrap();
        ...
    }
}
```

- [ ] **Step 3: Verify after each batch — `cargo check && cargo test --lib`**

- [ ] **Step 4: Commit per batch**

```bash
git commit -am "refactor: migrate detectors A-D to new GraphQuery API"
git commit -am "refactor: migrate detectors E-L to new GraphQuery API"
git commit -am "refactor: migrate detectors M-S to new GraphQuery API"
git commit -am "refactor: migrate detectors T-Z to new GraphQuery API"
```

### Task 12: Migrate remaining consumers (MCP, CLI, session)

**Files:**
- Modify: `mcp/tools/graph_queries.rs`, `mcp/tools/evolution.rs`, `mcp/tools/files.rs`
- Modify: `cli/graph.rs`, `cli/analyze/graph.rs`, `cli/analyze/export.rs`
- Modify: `session.rs`, `detectors/streaming_engine.rs`
- Modify: `classifier/features_v2.rs`, `classifier/debt.rs`
- Modify: `predictive/mod.rs`

- [ ] **Step 1: Migrate MCP handlers**

MCP graph_queries.rs uses `get_functions()`, `get_callers()`, etc. for JSON serialization. Migrate to NodeIndex-based, resolving nodes for JSON output.

- [ ] **Step 2: Migrate CLI graph/export commands**

- [ ] **Step 3: Migrate remaining files**

- [ ] **Step 4: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 5: Commit**

```bash
git commit -am "refactor: migrate MCP, CLI, session to new GraphQuery API"
```

---

## Chunk 4: Phase D — Eliminate CachedGraphQuery + Cleanup

### Task 13: Remove old GraphQuery methods and CachedGraphQuery

**Files:**
- Modify: `repotoire-cli/src/graph/traits.rs` — remove old methods
- Delete: `repotoire-cli/src/graph/cached.rs` — 528 lines
- Delete: `repotoire-cli/src/graph/compat.rs` — bridges no longer needed
- Delete: `repotoire-cli/src/graph/store/mod.rs` — old GraphStore
- Delete: `repotoire-cli/src/graph/store/tests.rs` — old tests
- Modify: `repotoire-cli/src/graph/mod.rs` — remove old modules

- [ ] **Step 1: Verify zero uses of old GraphQuery methods**

```bash
grep -rn "get_functions()\|get_callers(\|get_callees(\|get_imports()\|get_calls()\|build_call_maps_raw\|get_call_adjacency" repotoire-cli/src/ --include="*.rs" | grep -v "compat.rs" | grep -v "store/"
```

Should return zero results (only compat.rs and old store/ which are being deleted).

- [ ] **Step 2: Verify zero uses of CachedGraphQuery**

```bash
grep -rn "CachedGraphQuery" repotoire-cli/src/ --include="*.rs" | grep -v "cached.rs"
```

- [ ] **Step 3: Remove old methods from GraphQuery trait**

In `traits.rs`, remove all deprecated methods. Only the new NodeIndex-based methods remain.

- [ ] **Step 4: Delete files**

```bash
rm repotoire-cli/src/graph/cached.rs
rm repotoire-cli/src/graph/compat.rs
rm -rf repotoire-cli/src/graph/store/
```

Remove `mod` declarations from `graph/mod.rs`.

- [ ] **Step 5: Remove GraphStore type alias**

Delete `pub type GraphStore = CodeGraph;` from `graph/mod.rs`.

Update any remaining `GraphStore` references to `CodeGraph`.

- [ ] **Step 6: Delete store_query.rs or merge into frozen.rs**

The `GraphQuery` impl for `Arc<CodeGraph>` may live in `frozen.rs` or a thin `query_impl.rs`.

- [ ] **Step 7: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 8: Commit**

```bash
git commit -am "refactor: delete CachedGraphQuery (528 lines), old GraphStore (1,736 lines), compat bridges

GraphQuery trait now has only NodeIndex-based methods. All queries
backed by pre-built indexes. No locks, no DashMap, no memoization layer."
```

### Task 14: End-to-end verification

- [ ] **Step 1: Full test suite**

```bash
cd repotoire-cli && cargo test
```

- [ ] **Step 2: Smoke test real analysis**

```bash
cargo run -- analyze . --format json --no-git --max-files 30 2>/dev/null | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f'Score: {d[\"overall_score\"]:.1f} ({d[\"grade\"]})')
print(f'Findings: {len(d[\"findings\"])}')
"
```

- [ ] **Step 3: Verify line counts**

```bash
echo "=== New files ===" && wc -l repotoire-cli/src/graph/builder.rs repotoire-cli/src/graph/frozen.rs repotoire-cli/src/graph/indexes.rs repotoire-cli/src/graph/persistence.rs repotoire-cli/src/graph/metrics_cache.rs
echo "=== Deleted ===" && echo "store/mod.rs: was 1,736 lines" && echo "cached.rs: was 528 lines"
```

- [ ] **Step 4: Verify no remaining locks in graph layer**

```bash
grep -rn "RwLock\|DashMap\|Mutex\|OnceLock" repotoire-cli/src/graph/ --include="*.rs"
```

Should return zero results (only MetricsCache uses DashMap, which is in a separate struct).

- [ ] **Step 5: Commit**

```bash
git commit -am "chore: verification complete — GraphStore rearchitecture"
```
