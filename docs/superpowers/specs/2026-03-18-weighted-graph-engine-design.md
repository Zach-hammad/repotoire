# Weighted Graph Engine (Phase B)

**Date:** 2026-03-18
**Status:** Draft
**Builds on:** Phase A — Graph Primitives Engine (2026-03-17)
**Goal:** Enrich Repotoire's code graph with git co-change temporal weights, enabling weighted algorithms (PageRank, betweenness, community detection) and four new detectors that are impossible with static analysis alone.

## Problem

Phase A pre-computes graph-theoretic primitives (dominators, articulation points, PageRank, betweenness, SCCs) on an **unweighted** graph. All edges are binary — present or absent. This misses three categories of insight:

1. **Hidden coupling:** Files that always change together but have no import/call edge. These are "change A, forget to change B" bugs invisible to any static tool.
2. **Edge quality:** A function called once in a test and a function called in every request handler both produce the same `Calls` edge. Weighting by co-change frequency reflects *how tightly* things are coupled, not just *whether* they're connected.
3. **Natural boundaries:** Which clusters of code "belong together" based on how they actually evolve? Static module boundaries (directories) may not match operational reality.

No competitor (SonarQube, linters, AI-native tools) combines a code knowledge graph with temporal evolution data. This is Repotoire's moat.

## Design

### Architecture: Weighted Overlay Pattern

Phase B follows the same pattern as Phase A: compute once during `GraphPrimitives::compute()`, store results immutably, detectors read at O(1).

The key addition is a **weighted overlay graph** — a temporary `StableGraph<NodeIndex, f32>` built during primitives computation that merges structural edges (Calls/Imports) with co-change weights from git history. Weighted algorithms run on this overlay. The overlay is dropped after computation; only algorithm results survive into `GraphPrimitives`.

This matches Phase A's existing pattern where `compute_call_cycles` and `compute_articulation_points` build temporary filtered subgraphs and drop them.

### Pipeline Changes

```
Git Enrich (Stage 4)
  ├── Existing: blame, churn, commit_count per function/class
  └── NEW: compute_co_change_matrix() → CoChangeMatrix

Freeze → GraphIndexes::build() → GraphPrimitives::compute(... co_change)
  ├── Phase A (unchanged): SCC, PageRank, betweenness, dominators, articulation points
  └── Phase B (new):
      ├── Build weighted overlay (structural edges + co-change weights)
      ├── rayon parallel:
      │   ├── weighted_page_rank(&overlay)
      │   ├── weighted_betweenness(&overlay)  [Dijkstra-based Brandes]
      │   └── community_detection(&overlay)   [Louvain]
      └── Extract hidden_coupling pairs from CoChangeMatrix

Detect (Stage 7)
  ├── Phase A detectors (unchanged): SPOF, mutual recursion, bridge risk
  └── Phase B detectors (new):
      ├── HiddenCouplingDetector
      ├── CommunityMisplacementDetector
      ├── PageRankDriftDetector
      └── TemporalBottleneckDetector
```

### Component 1: Co-Change Matrix (Git Enrich Stage)

**File:** `git/co_change.rs` (new)
**Called from:** `git/enrichment.rs` at end of `enrich_all()`

Iterates commits from `GitHistory` and builds a file-pair co-change frequency map with exponential decay.

**Algorithm:**
```
for each commit in git history:
    files_changed = commit.files_changed()
    if files_changed.len() > max_files_per_commit: skip  // merge commits, bulk renames
    decay = exp(-age_days / half_life)
    for each pair (file_a, file_b) in files_changed where a < b:
        co_change[(file_a, file_b)] += decay
```

**Data structure:**
```rust
pub struct CoChangeMatrix {
    /// Decay-weighted co-change counts. Keys are interned file path StrKeys.
    /// Symmetric: (a, b) stored once with a < b.
    entries: HashMap<(StrKey, StrKey), f32>,
    half_life_days: f64,
    commits_analyzed: usize,
}
```

Uses `StrKey` (lasso interned strings) — same interner as graph node paths. Zero new string allocations.

**Configuration** (`repotoire.toml`):
```toml
[co_change]
half_life_days = 90       # Exponential decay half-life (default: 90)
min_weight = 0.1          # Drop pairs below this threshold
max_files_per_commit = 30 # Skip large commits (merges, bulk renames)
max_commits = 5000        # Cap iteration depth for very long histories
```

**Pipeline threading:** `CoChangeMatrix` returned from `git_enrich_stage()` alongside enriched graph. Stored on `AnalysisEngine`, passed into `GraphIndexes::build()` → `GraphPrimitives::compute()` as new parameter.

**Shallow clone handling:** If git history has ≤ 1 commit, log a warning: `"Co-change analysis requires git history depth > 1. Weighted analyses will be empty."` and return an empty `CoChangeMatrix`. Weighted algorithms produce empty results. New detectors produce zero findings.

### Component 2: Weighted Overlay Graph (Primitives Stage)

**File:** `graph/primitives.rs` (extended)

**New function:**
```rust
fn build_weighted_overlay(
    functions: &[NodeIndex],
    all_call_edges: &[(NodeIndex, NodeIndex)],
    all_import_edges: &[(NodeIndex, NodeIndex)],
    co_change: &CoChangeMatrix,
    graph: &StableGraph<CodeNode, CodeEdge>,
) -> StableGraph<NodeIndex, f32>
```

**Edge weight formula:**
```
weight(A, B) = structural_base + co_change_boost

structural_base:
  Calls edge:           1.0
  Imports edge:         0.5
  Both Calls + Imports: 1.5
  Co-change only:       0.0   (pure temporal, no structural edge)

co_change_boost:
  co_change_weight(file(A), file(B)) from matrix
  Capped at 2.0 (prevents single hot pair from dominating)

Total weight range: [0.1, 3.5]
```

**Hidden coupling edges:** File pairs in `CoChangeMatrix` with weight ≥ `min_weight` but NO structural edge between any functions in those files become overlay-only edges with `structural_base = 0.0`. Weight is purely temporal.

**Granularity:** Co-change is computed at file level (function-level would require O(functions × commits) blame intersection). File-level weights propagate to function-level overlay edges: all function pairs spanning two co-changing files get the same co-change boost.

**Memory:** ~2.4MB temporary for 50k-node graph. Dropped after algorithm computation.

### Component 3: Weighted Algorithms

#### 3a. Weighted PageRank

Replaces uniform `1/out_degree` transition with `edge_weight / sum(out_weights)`.

```rust
fn compute_weighted_page_rank(
    overlay: &StableGraph<NodeIndex, f32>,
    iterations: usize,  // 20
    damping: f64,        // 0.85
    tolerance: f64,      // 1e-6
) -> HashMap<NodeIndex, f64>
```

Separate from Phase A's unweighted PageRank — both stored. Detector #3 compares them.

**Complexity:** O((V+E) × iterations). Same as Phase A.

#### 3b. Weighted Betweenness (Dijkstra-based Brandes)

Brandes algorithm with Dijkstra replacing BFS for weighted shortest paths.

```rust
fn compute_weighted_betweenness(
    overlay: &StableGraph<NodeIndex, f32>,
    sample_size: usize,     // 200
    edge_fingerprint: u64,  // deterministic sampling
) -> HashMap<NodeIndex, f64>
```

**Complexity:** O(sample_size × (V+E) log V). ~200ms worst case for 50k nodes. Tracing span for measurement.

#### 3c. Community Detection (Louvain)

Louvain modularity optimization on the weighted overlay.

```rust
fn compute_communities(
    overlay: &StableGraph<NodeIndex, f32>,
    resolution: f64,  // 1.0 default
) -> (HashMap<NodeIndex, usize>, f64)  // (node→community_id, modularity)
```

**Algorithm:** Iterative: (1) each node = own community, (2) for each node try moving to neighbor's community, accept if modularity gain > 0, (3) collapse communities into super-nodes, repeat until stable. Converges in 3-5 passes.

**Why Louvain over Label Propagation:** Deterministic with fixed node ordering (required by `is_deterministic() = true` contract). Higher quality partitions. Produces modularity score.

**Complexity:** O(E × passes). Typically 3-5 passes, so ~O(5E).

**Parallelism:** All three weighted algorithms run in parallel via rayon:
```rust
rayon::join(
    || compute_weighted_page_rank(&overlay, ...),
    || rayon::join(
        || compute_weighted_betweenness(&overlay, ...),
        || compute_communities(&overlay, ...),
    ),
)
```

### Component 4: New GraphPrimitives Fields + GraphQuery Methods

**New fields on `GraphPrimitives`:**
```rust
// ── Weighted centrality metrics ──
pub(crate) weighted_page_rank: HashMap<NodeIndex, f64>,
pub(crate) weighted_betweenness: HashMap<NodeIndex, f64>,

// ── Community structure ──
pub(crate) community: HashMap<NodeIndex, usize>,
pub(crate) modularity: f64,

// ── Hidden coupling (co-change without structural edge) ──
// NodeIndex values are File-level node indices (matching co-change granularity).
pub(crate) hidden_coupling: Vec<(NodeIndex, NodeIndex, f32)>,
```

**New GraphQuery trait methods (6):**
```rust
fn weighted_page_rank_idx(&self, idx: NodeIndex) -> Option<f64>;
fn weighted_betweenness_idx(&self, idx: NodeIndex) -> Option<f64>;
fn community_idx(&self, idx: NodeIndex) -> Option<usize>;
fn modularity(&self) -> f64;
fn co_change_weight(&self, file_a: &str, file_b: &str) -> Option<f32>;
fn hidden_coupling_pairs(&self) -> &[(NodeIndex, NodeIndex, f32)];
```

Follows Phase A's pattern exactly: trait definition in `traits.rs`, implementation on `CodeGraph` delegating to `self.indexes.primitives.*`, implementation on `Arc<CodeGraph>` delegating to inner.

**`GraphIndexes::build()` signature change:** Add `Option<&CoChangeMatrix>` parameter. Four call sites need updating:
- `graph/builder.rs` (`GraphBuilder::freeze()`) — pass `None` (builder path has no git context)
- `graph/store/mod.rs` (`GraphStore::to_code_graph()`) — pass the engine-provided `CoChangeMatrix`
- `graph/persistence.rs` (deserialization path) — pass `None` (loaded from cache, no git context)
- `graph/indexes.rs` (tests) — pass `None`

Using `Option` means only the engine pipeline path (via `GraphStore::to_code_graph()`) provides co-change data. All other callers pass `None` and get empty weighted primitives.

### Component 5: Four New Detectors

All four detectors follow the Phase A pattern: implement `Detector` trait, `RegisteredDetector` for factory registration, `DetectorScope::GraphWide`, `is_deterministic() = true`, registered in `DETECTOR_FACTORIES`.

#### Detector 1: HiddenCouplingDetector

**File:** `detectors/hidden_coupling.rs`

**What:** File pairs with high co-change frequency but zero structural edges.

**Input:** `hidden_coupling_pairs()` from `GraphQuery`.

**Severity:**
- High: co-change weight ≥ 1.5
- Medium: co-change weight ≥ 0.5
- Low: co-change weight ≥ min_weight (0.1)

**Config:** `min_weight` threshold.

#### Detector 2: CommunityMisplacementDetector

**File:** `detectors/community_misplacement.rs`

**What:** Files in directory X that cluster with community Y per Louvain.

**Input:** `community_idx()` from `GraphQuery` + file system directory.

**Algorithm:** For each community with ≥ 5 members, compute dominant directory (mode). Files whose directory differs from dominant AND that represent ≤ 20% of community membership are reported.

**Severity:**
- Medium: different top-level module than community peers
- Low: different sub-directory, same top-level module

**Config:** `min_community_size` (default: 5), `max_outlier_ratio` (default: 0.2).

#### Detector 3: PageRankDriftDetector

**File:** `detectors/pagerank_drift.rs`

**What:** Functions where static PageRank rank diverges from change-weighted PageRank rank.

**Input:** `page_rank_idx()` (Phase A) vs `weighted_page_rank_idx()` (Phase B).

**Two sub-patterns:**
- Operationally critical, structurally hidden: high weighted rank, low unweighted rank
- Structurally central, operationally dormant: high unweighted rank, low weighted rank

**Severity:** Medium for both patterns (insights, not bugs).

**Threshold:** Report when rank percentile difference > 30 points.

**Config:** `min_percentile_drift` (default: 30).

#### Detector 4: TemporalBottleneckDetector

**File:** `detectors/temporal_bottleneck.rs`

**What:** Functions with high weighted betweenness — on critical paths of change propagation.

**Input:** `weighted_betweenness_idx()` from `GraphQuery`.

**Distinct from Phase A's ArchitecturalBottleneckDetector:** Phase A = structural bottleneck (unweighted). Phase B = temporal bottleneck (change-weighted). A function can be temporal bottleneck without being structural — it's on the hot path of change propagation.

**Severity:**
- High: weighted betweenness > p99 AND > 2× unweighted betweenness
- Medium: weighted betweenness > p95

**Config:** `percentile_threshold` (default: 95), `amplification_factor` (default: 2.0).

### Component 6: CLI Flag Cleanup

Remove three escape-hatch CLI flags that degrade Repotoire's competitive advantage:

- `--no-git` CLI flag — removed from clap derive struct
- `--skip-graph` CLI flag — removed from clap derive struct
- `--lite` CLI flag — removed (was alias for `--skip-graph --no-git --max-files=10000`)

`--max-files` stays (legitimate guard for huge repos).

**Internal `no_git` auto-detection replaces the flag:** The `AnalysisConfig.no_git` field stays as an internal-only boolean, but is no longer user-facing. The engine auto-detects "no git repo present" (e.g., temp directories in tests, non-git directories) and sets `no_git = true` internally. This preserves all existing engine test behavior (tests use temp dirs with no `.git`) without requiring test infrastructure changes.

**`skip_graph` removed entirely:** No internal equivalent. Graph building is always on. The `AnalysisConfig.skip_graph` field is removed along with all conditional paths that check it.

**Affected files:** `cli/analyze/mod.rs` (flag removal), `engine/mod.rs` (auto-detect logic, remove skip_graph paths), `engine/stages/*.rs` (remove skip_graph conditionals), `CLAUDE.md` (documentation).

## Performance Budget

| Operation | 50k-node graph | Notes |
|-----------|---------------|-------|
| Co-change matrix | ~200ms | O(commits × avg_files²), capped at 5000 commits max |
| Overlay construction | ~10ms | Single pass over edges + matrix |
| Weighted PageRank | ~30ms | Same as unweighted, different transition |
| Weighted betweenness | ~200ms | Dijkstra vs BFS, 200 samples |
| Louvain communities | ~50ms | O(5E), 3-5 passes |
| **Phase B total** | **~490ms** | Net new cost on top of Phase A's ~100ms |

Total analysis overhead: ~590ms for Phase A + B combined. Acceptable for a tool that runs once per analysis.

**Memory:** ~6MB temporary during primitives computation (overlay + algorithm working sets). Dropped post-computation. Persistent fields: ~4MB (weighted PR + betweenness + community maps + hidden coupling).

## Incremental Cache

Phase B detectors use `DetectorScope::GraphWide` + `is_deterministic() = true`, same as Phase A. Graph-wide detectors always re-run (not cached per-file). This is correct: co-change weights change with every new commit even if source files don't.

## Testing Strategy

**Unit tests** (inline `#[cfg(test)]` modules):
- `CoChangeMatrix`: empty history, single commit, decay weighting, max_files_per_commit guard, shallow clone
- `build_weighted_overlay`: structural-only edges, co-change-only edges, both, weight capping
- `compute_weighted_page_rank`: convergence, comparison to unweighted for uniform weights
- `compute_weighted_betweenness`: known graph with calculable centrality
- `compute_communities`: two disconnected cliques → two communities, single clique → one community
- Each detector: positive case, negative case, threshold boundary, empty graph

**Integration test:**
- Build graph with known structure + simulated co-change → verify all four detectors produce expected findings

## Configuration Summary

```toml
[co_change]
half_life_days = 90
min_weight = 0.1
max_files_per_commit = 30

[detectors.HiddenCouplingDetector]
min_weight = 0.1

[detectors.CommunityMisplacementDetector]
min_community_size = 5
max_outlier_ratio = 0.2

[detectors.PageRankDriftDetector]
min_percentile_drift = 30

[detectors.TemporalBottleneckDetector]
percentile_threshold = 95
amplification_factor = 2.0
```

## What We Leverage From Phase A

- `GraphPrimitives` struct and `compute()` entry point
- `GraphQuery` trait extension pattern (trait → CodeGraph impl → Arc impl)
- Temporary subgraph build/drop pattern (`idx_map`/`reverse_map`)
- `RegisteredDetector` factory pattern for detector registration
- `DetectorScope::GraphWide` + `is_deterministic()` for cache bypass
- `DetectorConfig::with_config()` / `config_for()` for per-detector settings
- Unweighted PageRank and betweenness (Phase A) as comparison baselines
- `rayon::join` parallelism pattern
- `StrKey` interner for zero-allocation path handling
- `all_call_edges` / `all_import_edges` pre-filtered edge lists
