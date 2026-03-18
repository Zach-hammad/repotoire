# Graph Primitives Engine

**Date:** 2026-03-17
**Status:** Draft
**Goal:** Transform Repotoire's graph from a lookup structure into a genuine competitive moat by pre-computing graph-theoretic primitives during `freeze()` and building detectors that are impossible without graph topology.

## Problem

Repotoire claims to be "graph-powered" but ~80% of graph usage is O(1) adjacency lookups — functionally equivalent to HashMaps. Of 100 detectors, ~75 are file-local AST-only, ~15 use single-hop lookups, and ~10 use actual graph algorithms. The graph is under-exploited.

Meanwhile, no competitor (linters, SonarQube, AI-native tools) has a code knowledge graph. The opportunity: build analyses that are structurally impossible without graph topology — dominator trees, articulation points, PageRank — creating differentiation that can't be replicated by adding an LLM to a linter.

## Design

### Architecture: Pre-Computed Primitives Layer

A new `GraphPrimitives` struct, owned by `GraphIndexes`, computed once during `GraphIndexes::build()`. This method is called by both freeze paths: `GraphBuilder::freeze()` (used in tests) and `GraphStore::to_code_graph()` (used by the engine). All primitives are immutable after construction. Detectors read them at O(1) via `ctx.graph` (`&dyn GraphQuery`) — zero graph traversal at detection time.

**Freeze paths (both call `GraphIndexes::build()`):**
- Engine pipeline: `GraphStore::to_code_graph()` → clones StableGraph → `GraphIndexes::build()` → `CodeGraph::from_parts()`
- Tests/builder: `GraphBuilder::freeze()` → `GraphIndexes::build()` → `CodeGraph::from_parts()`

**Pipeline position:** Parse → Graph build (mutable `GraphStore`) → Git enrich (mutates `GraphStore`) → **FREEZE** (primitives computed here) → Calibrate → Precompute (`FunctionContextMap`) → Detect

```
GraphIndexes::build() [called by both freeze paths]
        ├── Steps 1-6: existing (kind indexes, adjacency, spatial, SCC, fingerprint)
        ├── Step 7: Build temporary filtered subgraphs
        │     ├── call_subgraph: directed, Functions + Calls edges
        │     └── structural_subgraph: undirected, Functions/Files + Calls/Imports edges
        ├── Step 8: Compute primitives (rayon parallel)
        │     ├── Thread A: Tarjan SCC on call_subgraph          [O(V+E)]
        │     ├── Thread B: Sparse PageRank (custom, using adjacency indexes) [O((V+E) × 20)]
        │     ├── Thread C: Sampled Brandes betweenness           [O(200·(V+E))]
        │     ├── Thread D: Articulation points + bridges         [O(V+E)]
        │     └── Sequential:
        │           ├── Entry point detection + virtual root
        │           ├── Dominator tree (Cooper et al.)            [O(V+E)]
        │           ├── Dominated sets (reverse idom traversal)   [O(V)]
        │           └── Domination frontiers                      [O(V+E)]
        └── Step 9: Drop filtered subgraphs (temporary allocations freed)
```

**Total compute cost:** ~100ms for 50k-node graph. Bounded by sampled betweenness, which is moved cost (currently computed ad-hoc in `function_context.rs`). Net new cost: ~30ms.

**Memory overhead:** ~3.8MB for 50k-node graph. Negligible vs graph itself.

### Primitives

#### 1. Dominator Tree + Frontiers

**Algorithm:** `petgraph::algo::dominators::simple_fast` (Cooper et al. "Simple, Fast Dominance Algorithm")

**How it works:** In a directed graph rooted at R, node A dominates node B iff every path from R to B passes through A. The immediate dominator of B is the closest strict dominator. The domination frontier of A is the set of nodes just beyond A's dominance — where A's control ends.

**Entry point detection:** Functions with in-degree 0 on the call graph, filtered to exclude isolated nodes (must have outgoing calls). A virtual root node connects to all detected entry points. Additionally, functions matching known entry patterns (main, handle_*, test_*) are included even if they have callers (framework dispatch).

**Disconnected component handling:** After identifying entry points, check each call-graph SCC (from primitive #3). For any SCC where no node is reachable from the identified entry points, add one representative node (the one with highest out-degree) to the virtual root's children. This ensures mutual recursion islands that have no external callers are still covered by the dominator tree.

**Memory note on `dominated` map:** For a deep dominator tree (e.g., a linear chain), the `dominated` map is O(V^2) worst case. For real call graphs this is unlikely to be pathological, but for very large graphs (>100k nodes) we may need to switch to a lazy computation (walk the idom tree on demand instead of pre-materializing). For Phase A, pre-materialization is acceptable.

**Data stored:**
- `idom: HashMap<NodeIndex, NodeIndex>` — immediate dominator per node
- `dominated: HashMap<NodeIndex, Vec<NodeIndex>>` — all nodes transitively dominated by each node
- `frontier: HashMap<NodeIndex, Vec<NodeIndex>>` — domination frontier per node
- `dom_depth: HashMap<NodeIndex, usize>` — depth in dominator tree

**Complexity:** O(V+E) for dominator tree, O(V) for dominated sets, O(V+E) for frontiers.

#### 2. Articulation Points + Bridge Edges

**Algorithm:** Tarjan's biconnected components (custom implementation — not in petgraph).

**How it works:** A single DFS pass on the undirected projection of the code graph computes discovery times and low-link values. An articulation point is a node where removing it increases the number of connected components. A bridge is an edge with the same property.

**Subgraph:** Undirected projection of Calls + Imports edges. Excludes Contains (structural, not dependency), Uses (too noisy), ModifiedIn (git metadata), Inherits (considered separately in Phase B).

**Data stored:**
- `articulation_points: Vec<NodeIndex>` — nodes whose removal disconnects the graph (sorted by index for `&[NodeIndex]` return)
- `bridges: Vec<(NodeIndex, NodeIndex)>` — edges whose removal disconnects the graph
- `component_sizes: HashMap<NodeIndex, Vec<usize>>` — per articulation point, sizes of components that would separate

**Component size computation:** During the articulation point DFS, track subtree sizes. For each articulation point, the component sizes are derived from the biconnected component tree: the subtree sizes of each child in the DFS tree, plus the remaining nodes (V - sum of subtrees). This is computed in a second O(V+E) pass after the articulation point identification pass.

**Complexity:** O(V+E) for articulation points + O(V+E) for component sizes = O(V+E) total (two passes).

#### 3. Call-Graph SCCs (Mutual Recursion)

**Algorithm:** `petgraph::algo::tarjan_scc` on the call subgraph.

**How it works:** Identical pattern to existing import-cycle detection in `indexes.rs:256-360`. Build filtered call-only subgraph, run Tarjan SCC, keep SCCs with >1 node.

**Data stored:**
- `call_cycles: Vec<Vec<NodeIndex>>` — mutual recursion groups, sorted by size descending

**Complexity:** O(V+E).

#### 4. PageRank

**Algorithm:** Custom sparse PageRank implementation (NOT `petgraph::algo::page_rank`).

**Why not petgraph's built-in:** petgraph's `page_rank` uses a naive dense O(V^2) inner loop per iteration — it iterates all V nodes for each V node, checking edges. For a 50k-node call subgraph this is catastrophically slow (~50 billion operations for 20 iterations). Code call graphs are sparse (average out-degree 3-5), so a sparse implementation is O(V+E) per iteration.

**Custom implementation:** Standard sparse power iteration using pre-built adjacency indexes:
```
// Per iteration: iterate edges, not all pairs
for each node v:
    rank[v] = (1 - d) / N
for each edge (u, v) in call_edges:
    rank[v] += d * old_rank[u] / out_degree[u]
```
This is O(V + E) per iteration, O((V + E) × 20) total. Damping factor 0.85, 20 iterations, convergence check (early exit if delta < 1e-6).

**How it works:** Iterative algorithm that computes "importance" by distributing rank along edges. A function's PageRank is high when important functions call it. Unlike fan-in (which counts direct callers equally), PageRank captures transitive importance: 3 callers with high PageRank > 30 callers from dead code.

**Subgraph:** Uses pre-built `call_callees` and `call_callers` adjacency indexes directly — no filtered subgraph needed for this primitive. Results stored keyed by original NodeIndexes.

**Data stored:**
- `page_rank: HashMap<NodeIndex, f64>` — raw PageRank score per function

**Complexity:** O((V+E) × 20 iterations). For 50k nodes, 200k edges: ~4M operations × 20 = ~80M ops, ~20ms.

#### 5. Betweenness Centrality (Moved, Not New)

**Algorithm:** Sampled Brandes algorithm (200 source nodes, deterministic seed).

**How it works:** BFS from each sampled source, back-propagate centrality. Currently implemented ad-hoc in `function_context.rs:398-485`. Moved to freeze-time pre-computation. The function_context code is deleted.

**Deterministic sampling (behavioral change):** The current implementation in `function_context.rs` uses `rand::rng()` — a non-deterministic random source. This means betweenness values differ across runs of the same codebase. The moved implementation changes this: the random seed for source node selection is derived from `edge_fingerprint` (already computed in Step 6). This is a deliberate behavioral change — it ensures that two analyses of the same graph topology always produce identical betweenness values, which is necessary for reproducible findings. The tradeoff is that the specific sample selection is now fixed for a given topology rather than randomly varying, but with 200 samples out of V nodes, the approximation quality is equivalent.

**Data stored:**
- `betweenness: HashMap<NodeIndex, f64>` — sampled betweenness per function

**Complexity:** O(200·(V+E)). This is moved cost — already incurred today, just relocated.

### GraphPrimitives Struct

```rust
/// Pre-computed graph algorithm results. Computed once during freeze().
/// All fields are immutable. O(1) access from any detector via CodeGraph.
pub struct GraphPrimitives {
    // ── Dominator analysis (directed call graph) ──
    pub idom: HashMap<NodeIndex, NodeIndex>,
    pub dominated: HashMap<NodeIndex, Vec<NodeIndex>>,
    pub frontier: HashMap<NodeIndex, Vec<NodeIndex>>,
    pub dom_depth: HashMap<NodeIndex, usize>,

    // ── Structural connectivity (undirected call+import graph) ──
    pub articulation_points: Vec<NodeIndex>,          // sorted, for &[NodeIndex] return
    pub articulation_point_set: HashSet<NodeIndex>,   // for O(1) is_articulation_point()
    pub bridges: Vec<(NodeIndex, NodeIndex)>,
    pub component_sizes: HashMap<NodeIndex, Vec<usize>>,

    // ── Call-graph cycles ──
    pub call_cycles: Vec<Vec<NodeIndex>>,

    // ── Centrality metrics ──
    pub page_rank: HashMap<NodeIndex, f64>,
    pub betweenness: HashMap<NodeIndex, f64>,
}

impl Default for GraphPrimitives {
    fn default() -> Self {
        Self {
            idom: HashMap::new(),
            dominated: HashMap::new(),
            frontier: HashMap::new(),
            dom_depth: HashMap::new(),
            articulation_points: Vec::new(),
            articulation_point_set: HashSet::new(),
            bridges: Vec::new(),
            component_sizes: HashMap::new(),
            call_cycles: Vec::new(),
            page_rank: HashMap::new(),
            betweenness: HashMap::new(),
        }
    }
}
```

### CodeGraph API

New methods on `CodeGraph` (thin wrappers over `self.indexes.primitives`):

```rust
impl CodeGraph {
    // ── Dominator queries ──
    pub fn immediate_dominator(&self, idx: NodeIndex) -> Option<NodeIndex>
    pub fn dominated_by(&self, idx: NodeIndex) -> &[NodeIndex]
    pub fn domination_frontier(&self, idx: NodeIndex) -> &[NodeIndex]
    pub fn dominator_depth(&self, idx: NodeIndex) -> usize
    pub fn domination_count(&self, idx: NodeIndex) -> usize

    // ── Structural connectivity ──
    pub fn is_articulation_point(&self, idx: NodeIndex) -> bool  // convenience, delegates to _idx
    pub fn articulation_points(&self) -> &[NodeIndex]
    pub fn bridges(&self) -> &[(NodeIndex, NodeIndex)]
    pub fn separation_sizes(&self, idx: NodeIndex) -> Option<&[usize]>  // convenience, delegates to _idx

    // ── Call cycles ──
    pub fn call_cycles(&self) -> &[Vec<NodeIndex>]

    // ── Centrality ──
    pub fn page_rank(&self, idx: NodeIndex) -> f64
    pub fn betweenness(&self, idx: NodeIndex) -> f64
}
```

### GraphQuery Trait Extension

New methods added to `GraphQuery` trait in `traits.rs`:

```rust
pub trait GraphQuery {
    // ... existing methods ...

    fn dominated_by_idx(&self, idx: NodeIndex) -> &[NodeIndex];
    fn domination_frontier_idx(&self, idx: NodeIndex) -> &[NodeIndex];
    fn dominator_depth_idx(&self, idx: NodeIndex) -> usize;
    fn is_articulation_point_idx(&self, idx: NodeIndex) -> bool;
    fn articulation_points_idx(&self) -> &[NodeIndex];
    fn separation_sizes_idx(&self, idx: NodeIndex) -> Option<&[usize]>;
    fn bridge_edges_idx(&self) -> &[(NodeIndex, NodeIndex)];
    fn call_cycles_idx(&self) -> &[Vec<NodeIndex>];
    fn page_rank_idx(&self, idx: NodeIndex) -> f64;
    fn betweenness_idx(&self, idx: NodeIndex) -> f64;
}
```

All 10 new methods have default implementations on the trait itself (returning empty slices, `false`, `0.0`, `None`). `GraphStore` and `Arc<GraphStore>` inherit these defaults — no changes needed in `store_query.rs`. Only `CodeGraph` (via `compat.rs`) overrides them with real data.

### New Detectors

All three new detectors implement both the `Detector` trait (for detection logic) and the `RegisteredDetector` trait (for factory registration via `create(init: &DetectorInit) -> Arc<dyn Detector>`). They are registered in the `DETECTOR_FACTORIES` array in `detectors/mod.rs` via `register::<T>()`. Each accepts `DetectorConfig` from `init.config_for("DetectorName")`. Configuration is loaded via `normalize_detector_name()` (e.g., `SinglePointOfFailureDetector` → `single-point-of-failure`).

**Critical: `detector_scope()` override.** The default `detector_scope()` implementation returns `FileScopedGraph` (not `GraphWide`) when `requires_graph()` is true. Each new detector MUST explicitly override `detector_scope()` to return `DetectorScope::GraphWide`. Without this override, the engine's cache routing and the incremental cache will misclassify these detectors.

#### SinglePointOfFailureDetector

**File:** `detectors/single_point_of_failure.rs`

**Consumes:** `dominated_by_idx`, `domination_frontier_idx`, `page_rank_idx`

**Trait requirements:** Implements both `Detector` and `RegisteredDetector`. Returns `DetectorScope::GraphWide` from `detector_scope()`.

**Logic:**
```
fn detect(&self, ctx: &AnalysisContext) -> Result<Vec<Finding>> {
    let graph = ctx.graph;  // &dyn GraphQuery
    for &func_idx in graph.functions_idx() {
        let dom_count = graph.dominated_by_idx(func_idx).len();
        if dom_count < self.threshold { continue; }

        let pr = graph.page_rank_idx(func_idx);
        let frontier = graph.domination_frontier_idx(func_idx);
        let node = graph.node_idx(func_idx).unwrap();
        let severity = self.calculate_severity(dom_count, pr, total_functions);

        findings.push(Finding {
            detector: "single-point-of-failure",
            message: format!("{name} dominates {dom_count} of {total} functions ({pct}%).
                      Blast radius boundary: {frontier_names}.
                      PageRank: {pr:.4} (top {pr_pct}%)."),
            severity,
            file: node.file_path,
            line: node.line_start,
            affected_files: /* files of dominated nodes */,
        });
    }
}
```

**Detection time:** O(N) scan, O(1) per function. No graph traversal.

**Severity calculation:**
- Critical: dominates >20% of reachable functions AND top 1% PageRank
- High: dominates >10% OR top 5% PageRank with >5% domination
- Medium: dominates >threshold (default 20 functions)

**Configuration:**
```toml
[detectors.single-point-of-failure]
enabled = true
min_dominated = 20
min_page_rank_percentile = 0.9
```

#### StructuralBridgeRiskDetector

**File:** `detectors/structural_bridge_risk.rs`

**Consumes:** `articulation_points`, `bridges`, `component_sizes`

**Trait requirements:** Implements both `Detector` and `RegisteredDetector`. Returns `DetectorScope::GraphWide` from `detector_scope()`.

**Logic:**
```
fn detect(&self, ctx: &AnalysisContext) -> Result<Vec<Finding>> {
    let graph = ctx.graph;
    for &ap_idx in graph.articulation_points_idx() {
        let sizes = match graph.separation_sizes_idx(ap_idx) { Some(s) => s, None => continue };
        if sizes.iter().min() < self.min_component_size { continue; }

        let node = graph.node_idx(ap_idx).unwrap();
        let severity = self.calculate_severity(sizes);
        findings.push(Finding {
            detector: "structural-bridge-risk",
            message: format!("{name} is a structural bridge: removing it disconnects
                      components of size {sizes:?}. No alternative path exists."),
            severity,
            file: node.file_path,
            line: node.line_start,
        });
    }
}
```

**Severity calculation:**
- Critical: both components >100 nodes
- High: both components >30 nodes
- Medium: both components >min_component_size

**Configuration:**
```toml
[detectors.structural-bridge-risk]
enabled = true
min_component_size = 10
```

#### MutualRecursionDetector

**File:** `detectors/mutual_recursion.rs`

**Consumes:** `call_cycles`

**Trait requirements:** Implements both `Detector` and `RegisteredDetector`. Returns `DetectorScope::GraphWide` from `detector_scope()`.

**Logic:**
```
fn detect(&self, ctx: &AnalysisContext) -> Result<Vec<Finding>> {
    let graph = ctx.graph;
    for cycle in graph.call_cycles_idx() {
        if cycle.len() > self.max_cycle_size { continue; }

        let total_complexity: u32 = cycle.iter()
            .filter_map(|&idx| graph.node_idx(idx))
            .map(|n| n.complexity as u32)  // CodeNode.complexity is u16
            .sum();
        let first_node = graph.node_idx(cycle[0]).unwrap();
        let severity = self.calculate_severity(cycle.len(), total_complexity);
        findings.push(Finding {
            detector: "mutual-recursion",
            message: format!("Mutual recursion: {names} form a {len}-function call cycle.
                      Combined complexity: {complexity}.
                      These functions cannot be tested independently."),
            severity,
            file: first_node.file_path,
            line: first_node.line_start,
            affected_files: /* files of all functions in cycle */,
        });
    }
}
```

**Severity calculation:**
- High: cycle size >5 OR combined complexity >30
- Medium: cycle size >2
- Low: cycle size == 2 (simple mutual recursion, may be intentional)

**Configuration:**
```toml
[detectors.mutual-recursion]
enabled = true
max_cycle_size = 50
```

### Existing Detector Migration

#### ArchitecturalBottleneckDetector

**Current state:** Calls `function_context.rs::calculate_betweenness()` ad-hoc during detection. O(200·(V+E)) cost at detection time.

**After migration:** Reads `ctx.graph.betweenness_idx(idx)` from pre-computed primitives. Detection becomes O(N) scan with O(1) per function. Same results, same algorithm, zero detection-time graph traversal.

**Changes:**
- Remove dependency on `FunctionContextMap::calculate_betweenness()`
- Replace with `ctx.graph.betweenness_idx(idx)` call
- Delete betweenness-related code from `function_context.rs` (~90 lines)

#### InfluentialCodeDetector

**Current state:** Uses `call_fan_in()` as proxy for influence.

**After migration:** Uses `ctx.graph.page_rank_idx(idx)` for true recursive importance. A function called by 3 high-PageRank functions correctly outranks one called by 30 dead-code functions.

**This is a behavioral change:** Switching from fan-in to PageRank will change which functions get flagged. Functions with high fan-in from dead/test code will drop in severity; functions with moderate fan-in from critical paths will increase. Existing tests comparing detector output will need updating. Thresholds need re-tuning against validation projects (Flask, FastAPI, Django).

**Changes:**
- Replace fan-in threshold with PageRank percentile threshold
- Severity adjusted by PageRank percentile instead of raw fan-in count
- Update existing detector tests to reflect new ranking behavior
- Re-validate against benchmark projects after migration

#### FunctionContext Construction (Precomputation Phase)

**Current state:** `FunctionContextBuilder.build()` runs in the precomputation phase (after freeze, before detection) as part of `precompute_gd_startup`. It:
1. Calls `calculate_betweenness()` — 90-line Brandes implementation, O(200·(V+E))
2. Calls `calculate_call_depths()` — 60-line BFS from entry points, O(V+E)
3. Uses results to infer `FunctionRole` for each function (Hub, Utility, Leaf, etc.)
4. Produces `FunctionContextMap`, passed to detectors via `ctx.functions`

**After migration:** `FunctionContextBuilder.build()` still runs in precomputation (it still produces `FunctionContextMap` for 20+ detectors that use role-based severity). But instead of computing betweenness and call depth, it READS them from the frozen graph's primitives:

```rust
// Before: self.calculate_betweenness(&graph) → HashMap<String, f64>
// After:  graph.betweenness_idx(func_idx) → f64 (O(1) lookup)

// Before: self.calculate_call_depths(&graph) → HashMap<String, usize>
// After:  graph.dominator_depth_idx(func_idx) → usize (O(1) lookup)
```

The `FunctionContext` struct is unchanged. The `FunctionRole` inference logic is unchanged. Only the data SOURCE changes — from ad-hoc computation to pre-computed primitive reads.

**Deleted code:**
- `calculate_betweenness()` method in `function_context.rs` (~90 lines)
- `calculate_call_depths()` method in `function_context.rs` (~60 lines)
- Total: ~150 lines of ad-hoc graph algorithm code replaced by O(1) reads

### Scoring Integration

No changes to the scoring pipeline. New detectors produce standard `Finding` structs that flow through existing penalty calculation:

```
Finding(severity=High) → penalty per kLOC → density normalization → health score impact
```

The Architecture pillar (30% weight) naturally captures the new findings since they're architectural in nature.

### Incremental Cache Integration

New detectors are graph-level (`DetectorScope::GraphWide`). They follow the existing pattern for `CircularDependencyDetector`.

**How graph-level caching works (existing mechanism):**
- Cache key: `compute_all_files_hash(files)` — XXH3 hash of all file paths + content hashes
- Stored in: `cache.graph.detectors: HashMap<String, Vec<CachedFinding>>`, keyed by detector name
- Invalidation: any file added/removed/modified triggers re-run of all graph-wide detectors
- API: `cache_graph_findings(detector_name, findings)` / `cached_graph_findings(detector_name)`

**This is conservative but correct.** Ideally, topology-dependent detectors (like ours) would key on `edge_fingerprint` — a comment change doesn't affect the call graph. But the existing all-files-hash mechanism works, and changing it is a separate optimization. The new detectors plug into the existing mechanism with zero cache infrastructure changes.

**Engine routing fix required:** The current engine (`engine/stages/detect.rs` ~line 119) routes findings to `graph_wide_findings` vs `findings_by_file` based on `finding.affected_files.is_empty()`. Our new detectors populate `affected_files` (e.g., files of dominated nodes), which would incorrectly route them to the per-file cache instead of the graph-wide cache. **This must be fixed:** change the engine's routing logic to use `detector.detector_scope() == DetectorScope::GraphWide` instead of `affected_files.is_empty()`. This is a one-line change in `detect.rs` but is critical for correct caching behavior. Add `engine/stages/detect.rs` to the modified files list.

### Configuration

Each detector gets a section in `repotoire.toml` with adaptive calibration support:

```toml
[detectors.single-point-of-failure]
enabled = true
min_dominated = 20
min_page_rank_percentile = 0.9

[detectors.structural-bridge-risk]
enabled = true
min_component_size = 10

[detectors.mutual-recursion]
enabled = true
max_cycle_size = 50
```

Thresholds integrate with the existing `ThresholdResolver` and `StyleProfile` system — adaptive calibration can adjust `min_dominated` based on the codebase's dominator depth distribution.

### Performance Budget

| Operation | 10k nodes | 50k nodes | Notes |
|-----------|-----------|-----------|-------|
| Build call subgraph | <1ms | ~2ms | Freshly-constructed StableGraph (dense indices) |
| Build structural subgraph | <1ms | ~2ms | Freshly-constructed UnGraph (dense indices) |
| Call-graph SCCs | <1ms | ~3ms | petgraph tarjan_scc |
| PageRank (20 iter) | ~4ms | ~20ms | Custom sparse impl, O((V+E)×20) |
| Betweenness (200 samples) | ~20ms | ~100ms | Moved from detection time |
| Dominator tree | <1ms | ~3ms | petgraph dominators::simple_fast |
| Dominated sets + frontiers | <1ms | ~5ms | Reverse tree traversal |
| Articulation points + sizes | <1ms | ~5ms | Two DFS passes |
| **Total primitives** | **~28ms** | **~140ms** | Parallel: ~105ms (betweenness-bound) |
| **Net new cost** | **~8ms** | **~40ms** | Betweenness is moved, not new |

Detection time for all 3 new detectors combined: O(N) scan, <1ms for 50k nodes.

### Implementation Notes

**Filtered subgraph construction:** All filtered subgraphs (call_subgraph for SCCs/dominators, structural_subgraph for articulation points) MUST be freshly-constructed `StableGraph` instances with dense NodeIndex allocation (no holes). This is required because `petgraph::algo::dominators::simple_fast` and `tarjan_scc` use `NodeIndexable` traits that assume dense indices. Follow the same `idx_map`/`reverse_map` pattern used by `compute_import_cycles()` in `indexes.rs:265-293`. PageRank uses our custom sparse implementation with the adjacency indexes directly, so it does not need a filtered subgraph.

**Empty graph early return:** `GraphPrimitives::compute()` must early-return `Default::default()` when the graph has 0 functions or 0 call edges. `dominators::simple_fast` requires a root node and will panic on empty input. PageRank on an empty graph is trivially empty.

**Send + Sync safety:** `GraphPrimitives` contains only `HashMap`, `HashSet`, `Vec`, and `f64` — all `Send + Sync`. Adding it to `GraphIndexes` (which is inside `CodeGraph`) does not violate the existing `unsafe impl Send/Sync for CodeGraph` invariant. Document this in a SAFETY comment on `GraphPrimitives`.

**compat.rs delegation:** The `GraphQuery` trait extension adds 10 new methods (including `separation_sizes_idx`). The `Arc<CodeGraph>` impl in `compat.rs` must delegate all 10. To avoid further bloating the already-808-line file, implement the delegation via a macro: `impl_graph_query_delegation!(Arc<CodeGraph>)`. This is a one-time cleanup that pays for itself as we add more trait methods in Phase B/C.

### Files Changed

**New files:**
- `graph/primitives.rs` — `GraphPrimitives` struct + `compute()` method + all algorithms
- `detectors/single_point_of_failure.rs` — SinglePointOfFailureDetector
- `detectors/structural_bridge_risk.rs` — StructuralBridgeRiskDetector
- `detectors/mutual_recursion.rs` — MutualRecursionDetector

**Modified files:**
- `graph/indexes.rs` — add `primitives: GraphPrimitives` field to `GraphIndexes`, call `compute()` in `build()`
- `graph/frozen.rs` — add primitive accessor methods to `CodeGraph`
- `graph/traits.rs` — extend `GraphQuery` trait with 10 new primitive query methods (with default impls returning empty/zero)
- `graph/compat.rs` — implement 10 new `GraphQuery` methods for `CodeGraph` + `Arc<CodeGraph>` (introduce `impl_graph_query_delegation!` macro to replace manual forwarding)
- `engine/stages/detect.rs` — fix finding routing to use `detector_scope()` instead of `affected_files.is_empty()` for graph-wide cache classification
- `detectors/mod.rs` — register 3 new detectors in `DETECTOR_FACTORIES` via `register::<T>()`
- `detectors/function_context.rs` — delete `calculate_betweenness()` (~90 lines) and `calculate_call_depths()` (~60 lines), replace with `ctx.graph.betweenness_idx()` and `ctx.graph.dominator_depth_idx()` reads in `FunctionContextBuilder.build()`
- `detectors/architectural_bottleneck.rs` — replace ad-hoc betweenness with `ctx.graph.betweenness_idx()`
- `detectors/influential_code.rs` — replace fan-in with `ctx.graph.page_rank_idx()` (behavioral change, requires threshold re-tuning)

**Deleted code:** ~150 lines from `function_context.rs` (ad-hoc betweenness + call depth computation)

### Phase B / C Roadmap (Not In Scope)

**Phase B — Weighted Graph:**
- Edge weights from git co-change frequency
- Weighted PageRank, weighted community detection
- More accurate "true coupling" metrics

**Phase C — Structural Intelligence:**
- Spectral clustering for package-vs-reality mismatch detection
- Structural role embeddings (algebraic, not ML)
- Architecture conformance checking (declared layers vs actual graph)

These phases build on the primitives layer. The `GraphPrimitives` struct is extensible — Phase B/C add fields without changing the architecture.
