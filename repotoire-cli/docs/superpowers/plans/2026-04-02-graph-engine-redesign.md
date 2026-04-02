# Graph Engine Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace petgraph + lasso with a purpose-built CSR graph engine, hand-rolled string interner, and hand-rolled graph persistence. Eliminate 11 transitive deps (125 → 114). `bitcode` stays (used by incremental_cache, models, embedding_scorer — separate concern).

**Architecture:** Bottom-up: NodeIndex newtype → interner → algorithms (Tarjan, dominators) → CSR + builder + CodeGraph (atomic batch) → persistence → migrate consumers → remove deps.

**Tech Stack:** Pure Rust, zero new dependencies. `#[repr(C)]` for deterministic CodeNode layout. `std::fs::read`/`write` for persistence (no mmap — graph is ~4MB, fits in memory trivially).

**Spec:** `docs/superpowers/specs/2026-04-02-graph-engine-redesign.md`

---

## File Structure

### New files (all under `src/graph/`)

| File | Responsibility | Approx Lines |
|------|---------------|-------------|
| `node_index.rs` | `NodeIndex(u32)` newtype, replaces `petgraph::stable_graph::NodeIndex` | ~40 |
| `csr.rs` | CSR data structure, freeze logic, BFS reordering | ~350 |
| `algo.rs` | Hand-rolled Tarjan SCC + Lengauer-Tarjan dominators | ~200 |
| `overlay.rs` | Lightweight `WeightedOverlay` adjacency list for Phase B | ~60 |

### Modified files

| File | Change |
|------|--------|
| `src/graph/interner.rs` | Replace lasso with chunk-based arena interner |
| `src/graph/store_models.rs` | Add `#[repr(C)]` to `CodeNode`, import new NodeIndex |
| `src/graph/builder.rs` | Replace StableGraph with `Vec<Option<CodeNode>>` + edge Vec |
| `src/graph/frozen.rs` | Replace StableGraph+GraphIndexes with CSR-backed queries |
| `src/graph/indexes.rs` | Remove 12 adjacency HashMaps, keep spatial/bulk/cycle indexes |
| `src/graph/traits.rs` | Change NodeIndex import path |
| `src/graph/primitives/mod.rs` | Use CSR accessors, change NodeIndex path |
| `src/graph/primitives/phase_a.rs` | Replace petgraph::algo with hand-rolled, use CSR |
| `src/graph/primitives/phase_b.rs` | Replace StableGraph overlay with WeightedOverlay |
| `src/graph/persistence.rs` | Replace bitcode with zero-copy mmap persistence |
| `src/graph/compat.rs` | Change NodeIndex import path |
| `src/graph/metrics_cache.rs` | No petgraph changes (uses DashMap, separate concern) |
| `src/graph/mod.rs` | Add new modules, update re-exports |

### External consumer files (NodeIndex import path change only)

| File | Change |
|------|--------|
| `src/detectors/analysis_context.rs` | `petgraph::graph::NodeIndex` → `crate::graph::NodeIndex` |
| `src/detectors/engine.rs` | Same |
| `src/detectors/function_context.rs` | Same |
| `src/detectors/reachability.rs` | Same |
| `src/detectors/architecture/community_misplacement.rs` | Same |
| `src/detectors/architecture/pagerank_drift.rs` | Same |
| `src/detectors/architecture/temporal_bottleneck.rs` | Same |
| `src/scoring/graph_scorer.rs` | Same |
| `src/parsers/bounded_pipeline.rs` | Same |

---

## Task 1: NodeIndex newtype

**Files:**
- Create: `src/graph/node_index.rs`
- Modify: `src/graph/mod.rs`

- [ ] **Step 1: Write failing tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_node_index_basics() {
        let idx = NodeIndex::new(42);
        assert_eq!(idx.index(), 42);
        assert_eq!(idx, NodeIndex::new(42));
        assert_ne!(idx, NodeIndex::new(43));
    }

    #[test]
    fn test_node_index_invalid() {
        assert_eq!(NodeIndex::INVALID.index(), u32::MAX as usize);
    }

    #[test]
    fn test_node_index_ord() {
        let a = NodeIndex::new(1);
        let b = NodeIndex::new(2);
        assert!(a < b);
    }

    #[test]
    fn test_node_index_hash() {
        use std::collections::HashSet;
        let mut set = HashSet::new();
        set.insert(NodeIndex::new(1));
        set.insert(NodeIndex::new(1));
        assert_eq!(set.len(), 1);
    }
}
```

- [ ] **Step 2: Implement NodeIndex**

Create `src/graph/node_index.rs`:
```rust
use serde::{Deserialize, Serialize};

/// A node index into the graph's node array.
///
/// Transparent wrapper around `u32`. Drop-in replacement for
/// `petgraph::stable_graph::NodeIndex` with identical size and Copy semantics.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[repr(transparent)]
pub struct NodeIndex(u32);

impl NodeIndex {
    pub const INVALID: NodeIndex = NodeIndex(u32::MAX);

    #[inline]
    pub fn new(idx: u32) -> Self {
        Self(idx)
    }

    #[inline]
    pub fn index(self) -> usize {
        self.0 as usize
    }

    #[inline]
    pub fn as_u32(self) -> u32 {
        self.0
    }
}

impl From<usize> for NodeIndex {
    fn from(idx: usize) -> Self {
        Self(idx as u32)
    }
}
```

Add to `src/graph/mod.rs`:
```rust
pub mod node_index;
pub use node_index::NodeIndex;
```

- [ ] **Step 3: Run tests**

Run: `cargo test graph::node_index -- --nocapture`
Expected: all 4 tests pass

- [ ] **Step 4: Run clippy**

Run: `cargo clippy --all-features -- -D warnings 2>&1 | tail -5`
Expected: no warnings (NodeIndex is not yet used, so no conflicts with petgraph's NodeIndex)

- [ ] **Step 5: Commit**

```bash
git add src/graph/node_index.rs src/graph/mod.rs
git commit -m "feat(graph): add NodeIndex newtype (replaces petgraph NodeIndex)"
```

---

## Task 2: Hand-rolled string interner (replace lasso)

**Files:**
- Modify: `src/graph/interner.rs`

- [ ] **Step 1: Write failing tests**

Add to `src/graph/interner.rs` test module:
```rust
#[test]
fn test_interner_thread_safety() {
    let interner = StringInterner::new();
    let key = interner.intern("hello");

    std::thread::scope(|s| {
        for _ in 0..4 {
            s.spawn(|| {
                let k = interner.intern("hello");
                assert_eq!(k, key);
                assert_eq!(interner.resolve(k), "hello");
            });
        }
    });
}

#[test]
fn test_interner_many_strings() {
    let interner = StringInterner::new();
    let mut keys = Vec::new();
    for i in 0..10_000 {
        keys.push(interner.intern(&format!("string_{i}")));
    }
    assert_eq!(interner.len(), 10_000);
    for (i, key) in keys.iter().enumerate() {
        assert_eq!(interner.resolve(*key), format!("string_{i}"));
    }
}

#[test]
fn test_interner_resolve_after_growth() {
    let interner = StringInterner::new();
    // Intern enough strings to force multiple chunks
    let first_key = interner.intern("first");
    for i in 0..10_000 {
        interner.intern(&format!("padding_{i}"));
    }
    // Original key must still resolve correctly
    assert_eq!(interner.resolve(first_key), "first");
}
```

- [ ] **Step 2: Run tests to verify existing tests still pass (baseline)**

Run: `cargo test graph::interner -- --nocapture`
Expected: existing 2 tests pass, new 3 fail to compile (functions not changed yet)

- [ ] **Step 3: Rewrite StringInterner**

Replace the contents of `src/graph/interner.rs` with the chunk-based arena interner. Keep the public API identical (`intern`, `resolve`, `get`, `len`, `empty_key`, `memory_usage`). Key implementation details:

- `StrKey(u32)` newtype (replacing `lasso::Spur`)
- `InternerInner` with `chunks: Vec<String>`, `spans: Vec<(u16, u32, u32)>`, `map: HashMap<u64, Vec<StrKey>>`
- `RwLock<InternerInner>` for thread safety
- `resolve()` returns `&str` borrowing from `&self` (safe because chunks are append-only)
- Chunk capacity starts at 64KB, doubles on growth
- Hash function: use `std::hash::DefaultHasher` (SipHash, already in std)
- Remove `ReadOnlyInterner` (unused, was marked `#[allow(dead_code)]`)
- Remove `use lasso::{Spur, ThreadedRodeo}`
- Keep `GLOBAL_INTERNER` as `LazyLock<StringInterner>`

- [ ] **Step 4: Run tests**

Run: `cargo test graph::interner -- --nocapture`
Expected: all 5 tests pass (2 existing + 3 new)

- [ ] **Step 5: Fix downstream compilation — `into_inner().get()` sweep**

The `StrKey` type changes from `lasso::Spur` to our `StrKey(u32)`. `Spur::into_inner()` returns `NonZeroU32`, `.get()` converts to `u32`. Replace all 7 call sites with `StrKey::as_u32()`:

- `indexes.rs` line 391: `qualified_name.into_inner().get()` → `qualified_name.as_u32()`
- `indexes.rs` line 392: same pattern
- `persistence.rs` line 67: `key.into_inner().get()` → `key.as_u32()`
- `persistence.rs` line 150: same pattern
- `persistence.rs` line 153: same pattern
- `persistence.rs` line 156: same pattern
- `persistence.rs` line 159: same pattern

Also remove `ReadOnlyInterner` (unused, was `#[allow(dead_code)]`). Verify no tests reference it.

Run: `cargo check`
Expected: compiles clean

- [ ] **Step 6: Run full test suite**

Run: `cargo test --lib -- --quiet`
Expected: all ~1785 tests pass

- [ ] **Step 7: Commit**

```bash
git add src/graph/interner.rs src/graph/persistence.rs
git commit -m "feat(graph): hand-roll string interner (replace lasso)"
```

---

## Task 3: Hand-rolled Tarjan SCC + dominators

**Files:**
- Create: `src/graph/algo.rs`
- Modify: `src/graph/mod.rs`

- [ ] **Step 1: Write failing tests for Tarjan SCC**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tarjan_no_cycles() {
        // 0 → 1 → 2 (DAG)
        let adj = vec![vec![1u32], vec![2], vec![]];
        let sccs = tarjan_scc(3, |v| &adj[v as usize]);
        // Each node is its own SCC
        assert_eq!(sccs.len(), 3);
        assert!(sccs.iter().all(|scc| scc.len() == 1));
    }

    #[test]
    fn test_tarjan_single_cycle() {
        // 0 → 1 → 2 → 0
        let adj = vec![vec![1u32], vec![2], vec![0]];
        let sccs = tarjan_scc(3, |v| &adj[v as usize]);
        assert_eq!(sccs.len(), 1);
        assert_eq!(sccs[0].len(), 3);
    }

    #[test]
    fn test_tarjan_mixed() {
        // 0 → 1 → 2 → 1 (cycle 1-2), 0 → 3 (no cycle)
        let adj = vec![vec![1u32, 3], vec![2], vec![1], vec![]];
        let sccs = tarjan_scc(4, |v| &adj[v as usize]);
        let big: Vec<_> = sccs.iter().filter(|s| s.len() > 1).collect();
        assert_eq!(big.len(), 1);
        assert_eq!(big[0].len(), 2);
    }

    #[test]
    fn test_tarjan_empty() {
        let adj: Vec<Vec<u32>> = vec![];
        let sccs = tarjan_scc(0, |_v| &[][..]);
        assert!(sccs.is_empty());
    }
}
```

- [ ] **Step 2: Write failing tests for dominators**

```rust
    #[test]
    fn test_dominators_linear() {
        // 0 → 1 → 2 → 3
        let adj = vec![vec![1u32], vec![2], vec![3], vec![]];
        let idom = compute_dominators(4, 0, |v| &adj[v as usize]);
        assert_eq!(idom[1], Some(0));
        assert_eq!(idom[2], Some(1));
        assert_eq!(idom[3], Some(2));
    }

    #[test]
    fn test_dominators_diamond() {
        // 0 → 1, 0 → 2, 1 → 3, 2 → 3
        let adj = vec![vec![1u32, 2], vec![3], vec![3], vec![]];
        let idom = compute_dominators(4, 0, |v| &adj[v as usize]);
        assert_eq!(idom[1], Some(0));
        assert_eq!(idom[2], Some(0));
        assert_eq!(idom[3], Some(0)); // 0 dominates 3 (both paths go through 0)
    }
```

- [ ] **Step 3: Implement Tarjan SCC**

Create `src/graph/algo.rs` with standard Tarjan's algorithm:
- `pub fn tarjan_scc(node_count: usize, successors: impl Fn(u32) -> &[u32]) -> Vec<Vec<u32>>`
- Struct-of-arrays layout: separate `index: Vec<u32>`, `lowlink: Vec<u32>`, `on_stack: Vec<bool>` for cache efficiency
- Returns SCCs in reverse topological order (matches petgraph's behavior)

- [ ] **Step 4: Implement Lengauer-Tarjan dominators**

- `pub fn compute_dominators(node_count: usize, root: u32, successors: impl Fn(u32) -> &[u32]) -> Vec<Option<u32>>`
- Returns `idom[v] = Some(immediate_dominator)` for each reachable vertex, `None` for unreachable/root
- Use the simple iterative algorithm (Cooper, Harvey, Kennedy 2001) which is simpler than Lengauer-Tarjan and sufficient for our graph sizes

- [ ] **Step 5: Run tests**

Run: `cargo test graph::algo -- --nocapture`
Expected: all 6 tests pass

- [ ] **Step 6: Commit**

```bash
git add src/graph/algo.rs src/graph/mod.rs
git commit -m "feat(graph): hand-roll Tarjan SCC and dominator tree algorithms"
```

---

## Task 4: WeightedOverlay for Phase B

**Files:**
- Create: `src/graph/overlay.rs`
- Modify: `src/graph/mod.rs`

- [ ] **Step 1: Write failing tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_overlay_add_edge() {
        let mut g = WeightedOverlay::new(3);
        g.add_edge(0, 1, 0.5);
        g.add_edge(0, 2, 1.0);
        let neighbors: Vec<_> = g.neighbors(0).collect();
        assert_eq!(neighbors.len(), 2);
    }

    #[test]
    fn test_overlay_empty_node() {
        let g = WeightedOverlay::new(3);
        assert_eq!(g.neighbors(1).count(), 0);
    }

    #[test]
    fn test_overlay_node_count() {
        let g = WeightedOverlay::new(5);
        assert_eq!(g.node_count(), 5);
    }
}
```

- [ ] **Step 2: Implement WeightedOverlay**

```rust
/// Lightweight mutable weighted adjacency list for Phase B graph algorithms.
///
/// Used during GraphPrimitives computation for the co-change weighted overlay.
/// Not used at query time — only lives during freeze().
pub struct WeightedOverlay {
    adj: Vec<Vec<(u32, f32)>>,
}

impl WeightedOverlay {
    pub fn new(node_count: usize) -> Self {
        Self {
            adj: vec![Vec::new(); node_count],
        }
    }

    pub fn add_edge(&mut self, from: u32, to: u32, weight: f32) {
        self.adj[from as usize].push((to, weight));
    }

    pub fn neighbors(&self, v: u32) -> impl Iterator<Item = (u32, f32)> + '_ {
        self.adj[v as usize].iter().copied()
    }

    pub fn node_count(&self) -> usize {
        self.adj.len()
    }

    pub fn node_indices(&self) -> impl Iterator<Item = u32> {
        0..self.adj.len() as u32
    }
}
```

- [ ] **Step 3: Run tests**

Run: `cargo test graph::overlay -- --nocapture`
Expected: all 3 tests pass

- [ ] **Step 4: Commit**

```bash
git add src/graph/overlay.rs src/graph/mod.rs
git commit -m "feat(graph): add WeightedOverlay for Phase B algorithms"
```

---

## Task 5: CSR + GraphBuilder + CodeGraph + traits (atomic batch)

**Files:**
- Create: `src/graph/csr.rs`
- Modify: `src/graph/builder.rs`
- Modify: `src/graph/frozen.rs`
- Modify: `src/graph/traits.rs`
- Modify: `src/graph/compat.rs`
- Modify: `src/graph/mod.rs`

These files are interdependent: builder.freeze() calls CodeGraph::build() which uses CsrStorage, and traits.rs defines the NodeIndex type used everywhere. They must be changed atomically. **The codebase will not compile between Steps 1-7 — this is expected.**

### Part A: CsrStorage

- [ ] **Step 1: Create `src/graph/csr.rs` with tests**

Write failing CSR tests:
```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::store_models::EdgeKind;

    #[test]
    fn test_csr_empty() {
        let csr = CsrStorage::build(0, &[]);
        // No nodes, no crashes
    }

    #[test]
    fn test_csr_basic_edges() {
        let edges = vec![
            (0u32, 1u32, EdgeKind::Calls),
            (0, 2, EdgeKind::Calls),
            (1, 2, EdgeKind::Imports),
        ];
        let csr = CsrStorage::build(3, &edges);

        let callees = csr.neighbors(0, Slot::CALLS_OUT);
        assert_eq!(callees.len(), 2);
        assert!(callees.contains(&1));
        assert!(callees.contains(&2));

        let callers = csr.neighbors(1, Slot::CALLS_IN);
        assert_eq!(callers, &[0]);

        let callers = csr.neighbors(2, Slot::CALLS_IN);
        assert_eq!(callers, &[0]);

        let imports = csr.neighbors(1, Slot::IMPORTS_OUT);
        assert_eq!(imports, &[2]);

        assert!(csr.neighbors(0, Slot::IMPORTS_OUT).is_empty());
    }

    #[test]
    fn test_csr_bidirectional_consistency() {
        let edges = vec![
            (0u32, 1u32, EdgeKind::Calls),
            (1, 2, EdgeKind::Calls),
            (2, 0, EdgeKind::Calls),
        ];
        let csr = CsrStorage::build(3, &edges);
        for &(src, tgt, _) in &edges {
            assert!(csr.neighbors(src, Slot::CALLS_OUT).contains(&tgt));
            assert!(csr.neighbors(tgt, Slot::CALLS_IN).contains(&src));
        }
    }

    #[test]
    fn test_csr_modified_in_unidirectional() {
        let edges = vec![(0u32, 1u32, EdgeKind::ModifiedIn)];
        let csr = CsrStorage::build(2, &edges);
        assert_eq!(csr.neighbors(0, Slot::MODIFIED_IN_OUT), &[1]);
    }

    #[test]
    fn test_csr_sorted_neighbors() {
        let edges = vec![
            (0u32, 5u32, EdgeKind::Calls),
            (0, 2, EdgeKind::Calls),
            (0, 8, EdgeKind::Calls),
            (0, 1, EdgeKind::Calls),
        ];
        let csr = CsrStorage::build(9, &edges);
        assert_eq!(csr.neighbors(0, Slot::CALLS_OUT), &[1, 2, 5, 8]);
    }
}
```

Implement `CsrStorage`:
- `STRIDE = 11`, slot constants
- `build(node_count, edges)`: expand each edge into two entries (skip ModifiedIn In), sort by `(node * STRIDE + slot, neighbor)`, build offsets + neighbors arrays
- `neighbors(&self, node: u32, slot: usize) -> &[u32]`: offset-based slice
- `neighbors_as_node_index(&self, node: usize, slot: usize) -> &[NodeIndex]`: transmute `&[u32]` to `&[NodeIndex]` (safe: `NodeIndex` is `#[repr(transparent)]`)
- `node_count(&self) -> usize`, `edge_count_per_direction(&self) -> usize`

### Part B: Update traits.rs + compat.rs

- [ ] **Step 2: Replace NodeIndex in traits.rs and compat.rs**

Replace `use petgraph::stable_graph::NodeIndex` with `use crate::graph::node_index::NodeIndex` in both files. No logic changes — just the import path.

### Part C: Rewrite GraphBuilder

- [ ] **Step 3: Rewrite builder.rs**

Replace `StableGraph<CodeNode, CodeEdge>` with:
```rust
pub struct GraphBuilder {
    nodes: Vec<Option<CodeNode>>,
    node_index: HashMap<StrKey, NodeIndex>,
    edges: Vec<(NodeIndex, NodeIndex, CodeEdge)>,
    edge_set: HashSet<(NodeIndex, NodeIndex, EdgeKind)>,
    extra_props: HashMap<StrKey, ExtraProps>,
    query_snapshot: OnceLock<CodeGraph>,
}
```

Rewrite all methods:
- `add_node`: push to Vec (or overwrite existing via index), return NodeIndex
- `add_edge`: check edge_set, push to edges Vec
- `remove_file_entities`: set nodes to `None` (tombstone), filter edges in place
- `node_count`: count non-None
- `freeze` / `freeze_with_co_change`: delegate to `CodeGraph::build()`
- `from_frozen`: reconstruct nodes + edges from CodeGraph's CSR

### Part D: Rewrite CodeGraph

- [ ] **Step 4: Rewrite frozen.rs**

New struct:
```rust
pub struct CodeGraph {
    nodes: Vec<CodeNode>,
    csr: CsrStorage,
    node_index: HashMap<StrKey, NodeIndex>,
    extra_props: HashMap<StrKey, ExtraProps>,
    indexes: GraphIndexes,
}
```

Implement `CodeGraph::build()` (the freeze path):
1. Compact nodes (remove None tombstones → build old→new remap)
2. Remap edge indices
3. Build CsrStorage from remapped edges
4. Build GraphIndexes (spatial, bulk lists, cycles, fingerprint)
5. Compute GraphPrimitives
6. Return CodeGraph

Rewrite all `GraphQuery` trait methods as CSR slices. Rewrite inherent methods (`contains_children`, `contains_parent`, `uses_targets`, `uses_sources`, `modified_in`). Remove `raw_graph()` method (returned `&StableGraph` — no longer exists).

Rewrite `from_parts` / `into_parts` for the new components.

### Part E: Verify

- [ ] **Step 5: Verify compilation**

Run: `cargo check`
Expected: compiles clean (this is the first time the build works after the atomic batch)

- [ ] **Step 6: Run CSR unit tests**

Run: `cargo test graph::csr -- --nocapture`
Expected: all 5 CSR tests pass

- [ ] **Step 7: Run builder tests**

Run: `cargo test graph::builder -- --nocapture`
Expected: all existing builder tests pass

- [ ] **Step 8: Run frozen tests**

Run: `cargo test graph::frozen -- --nocapture`
Expected: all existing frozen tests pass

- [ ] **Step 9: Run full test suite**

Run: `cargo test --lib -- --quiet`
Expected: all ~1785 tests pass

- [ ] **Step 10: Commit**

```bash
git add src/graph/csr.rs src/graph/builder.rs src/graph/frozen.rs src/graph/traits.rs src/graph/compat.rs src/graph/mod.rs
git commit -m "refactor(graph): replace petgraph with CSR engine — builder, frozen, traits"
```

---

## Task 6: Simplify GraphIndexes

**Files:**
- Modify: `src/graph/indexes.rs`

- [ ] **Step 1: Remove the 12 adjacency HashMaps**

Delete from `GraphIndexes`:
- `call_callers`, `call_callees`
- `import_sources`, `import_targets`
- `inherit_parents`, `inherit_children`
- `contains_children`, `contains_parent`
- `uses_targets`, `uses_sources`
- `modified_in`

These are all now served by the CSR.

- [ ] **Step 2: Rewrite `GraphIndexes::build()`**

The build method now only constructs:
- Node-kind indexes: `functions`, `classes`, `files`
- Spatial indexes: `functions_by_file`, `classes_by_file`, `all_nodes_by_file`, `function_spatial`
- Bulk edge lists: `all_call_edges`, `all_import_edges`, `all_inheritance_edges` (built from edge list, filtering by kind)
- Import cycles: use `algo::tarjan_scc` on the import subgraph (excluding `is_type_only` edges)
- Edge fingerprint: SipHash of sorted edge triples

Input changes: takes `&[CodeNode]`, `&[(NodeIndex, NodeIndex, CodeEdge)]`, `&HashMap<StrKey, NodeIndex>` instead of `&StableGraph`.

- [ ] **Step 3: Run tests**

Run: `cargo test graph::indexes -- --nocapture`
Expected: all existing tests pass

- [ ] **Step 4: Commit**

```bash
git add src/graph/indexes.rs
git commit -m "refactor(graph): remove 12 adjacency HashMaps from GraphIndexes (CSR replaces them)"
```

---

## Task 7: Adapt GraphPrimitives + Phase A

**Files:**
- Modify: `src/graph/primitives/phase_a.rs`
- Modify: `src/graph/primitives/mod.rs`

- [ ] **Step 1: Replace petgraph algorithm calls in phase_a.rs**

- `compute_dominators()`: Replace `petgraph::algo::dominators::simple_fast` with `crate::graph::algo::compute_dominators`. **Important**: The current code creates a virtual root node connected to all SCC representatives for disconnected graphs. The hand-rolled dominator function needs to handle this. Either: (a) accept a `virtual_root` parameter and virtual edges, or (b) reproduce the virtual-root construction in phase_a.rs before calling `compute_dominators`. Option (b) is simpler — build a temporary adjacency list with the virtual root, pass to `compute_dominators`.
- `compute_sccs()`: Replace `petgraph::algo::tarjan_scc` with `crate::graph::algo::tarjan_scc`.
- Update all `graph.edges_directed(node, Direction::Outgoing)` patterns to use CSR slot accessors.
- Replace `petgraph::stable_graph::NodeIndex` with `crate::graph::NodeIndex`.
- Update `GraphPrimitives::compute()` signature: replace `graph: &StableGraph<CodeNode, CodeEdge>` with `graph: &CodeGraph` (which provides node access via `graph.node(idx)` and edge access via CSR slots). Phase A functions that do `graph.node_weight(idx)` become `graph.node(idx)`, and `graph.edges_directed(idx, Outgoing)` becomes `graph.csr.neighbors(idx, slot)`.

- [ ] **Step 2: Replace petgraph in primitives/mod.rs**

- The `GraphPrimitives::compute()` method takes `&CodeGraph` which now has CSR. Update the plumbing that passes graph data to Phase A/B.
- Replace all `NodeIndex` imports.

- [ ] **Step 3: Run primitives tests**

Run: `cargo test graph::primitives -- --nocapture`
Expected: all existing tests pass (PageRank, SCC, dominator, betweenness, articulation points, call depths)

- [ ] **Step 4: Commit**

```bash
git add src/graph/primitives/
git commit -m "refactor(graph): adapt Phase A primitives to use CSR + hand-rolled algorithms"
```

---

## Task 8: Adapt Phase B + WeightedOverlay

**Files:**
- Modify: `src/graph/primitives/phase_b.rs`

- [ ] **Step 1: Replace StableGraph overlay with WeightedOverlay**

Phase B currently builds `StableGraph<NodeIndex, f32>` for the weighted overlay. Replace with `WeightedOverlay`:

```rust
// Before:
let mut overlay = StableGraph::<NodeIndex, f32>::new();
// ... add nodes and edges ...
for edge in overlay.edges_directed(node, Direction::Outgoing) { ... }

// After:
let mut overlay = WeightedOverlay::new(node_count);
// ... add edges ...
for (neighbor, weight) in overlay.neighbors(node) { ... }
```

- [ ] **Step 2: Replace petgraph NodeIndex**

Update all `petgraph::stable_graph::NodeIndex` references to `crate::graph::NodeIndex` or raw `u32` for the overlay (which uses `u32` internally).

- [ ] **Step 3: Run Phase B tests**

Run: `cargo test graph::primitives::phase_b -- --nocapture`
Expected: all existing tests pass (weighted PageRank, weighted betweenness, Louvain communities)

- [ ] **Step 4: Commit**

```bash
git add src/graph/primitives/phase_b.rs
git commit -m "refactor(graph): adapt Phase B to use WeightedOverlay (drop petgraph overlay)"
```

---

## Task 9: Hand-rolled persistence (replace bitcode for graph)

**Files:**
- Modify: `src/graph/persistence.rs`
- Modify: `src/graph/store_models.rs`

- [ ] **Step 1: Add `#[repr(C)]` to CodeNode**

In `store_models.rs`, add `#[repr(C)]` to `CodeNode` for deterministic memory layout:
```rust
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[repr(C)]
pub struct CodeNode { ... }
```

- [ ] **Step 2: Write failing persistence tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_save_load_roundtrip() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("graph.rptg");

        // Build a small graph
        let mut builder = GraphBuilder::new();
        let f = CodeNode::file("test.py");
        builder.add_node(f);
        let func = CodeNode::function("foo", "test.py");
        builder.add_node(func);
        builder.add_edge_by_name("test.py", "test.py::foo", CodeEdge::contains());
        let graph = builder.freeze();

        // Save
        save_graph(&graph, &path).unwrap();

        // Load
        let loaded = load_graph(&path).unwrap();

        // Verify topology matches
        assert_eq!(graph.node_count(), loaded.node_count());
    }

    #[test]
    fn test_file_format_magic() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("graph.rptg");

        let builder = GraphBuilder::new();
        let graph = builder.freeze();
        save_graph(&graph, &path).unwrap();

        let bytes = std::fs::read(&path).unwrap();
        assert_eq!(&bytes[..4], b"RPTG");
    }
}
```

- [ ] **Step 3: Implement save_graph()**

Write the file format:
- Magic `RPTG` + format version 1 + counts + string table + raw CodeNode bytes + raw offsets + raw neighbors + auxiliary indexes (serde_json for the complex structs)

- [ ] **Step 4: Implement load_graph()**

Read and validate header, re-intern strings, read offsets/neighbors into Vec (`std::fs::read` — no mmap, graph is ~4MB), remap StrKey fields in nodes, reconstruct CodeGraph.

- [ ] **Step 5: Run tests**

Run: `cargo test graph::persistence -- --nocapture`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/graph/persistence.rs src/graph/store_models.rs
git commit -m "refactor(graph): zero-copy persistence with hand-rolled format (drop bitcode for graph)"
```

---

## Task 10: Migrate external consumers

**Files:**
- Modify: 9 files (see file list above)

- [ ] **Step 1: Replace NodeIndex imports in all consumer files**

In each file, replace:
- `petgraph::stable_graph::NodeIndex` → `crate::graph::NodeIndex`
- `petgraph::graph::NodeIndex` → `crate::graph::NodeIndex`

Files:
1. `src/detectors/analysis_context.rs`
2. `src/detectors/engine.rs`
3. `src/detectors/function_context.rs`
4. `src/detectors/reachability.rs`
5. `src/detectors/architecture/community_misplacement.rs`
6. `src/detectors/architecture/pagerank_drift.rs`
7. `src/detectors/architecture/temporal_bottleneck.rs`
8. `src/scoring/graph_scorer.rs`
9. `src/parsers/bounded_pipeline.rs`

- [ ] **Step 2: Verify compilation**

Run: `cargo check`
Expected: compiles clean

- [ ] **Step 3: Run full test suite**

Run: `cargo test --lib -- --quiet`
Expected: all ~1785 tests pass

- [ ] **Step 4: Commit**

```bash
git add src/detectors/ src/scoring/ src/parsers/
git commit -m "refactor: migrate all consumers from petgraph NodeIndex to graph::NodeIndex"
```

---

## Task 11: Remove petgraph + lasso from Cargo.toml

**Files:**
- Modify: `Cargo.toml`

- [ ] **Step 1: Remove deps**

Remove from `[dependencies]`:
```toml
petgraph = "0.7"
lasso = { version = "0.7.3", features = ["multi-threaded"] }
```

- [ ] **Step 2: Verify compilation**

Run: `cargo check`
Expected: compiles clean with zero petgraph/lasso references

- [ ] **Step 3: Verify no remaining references**

Run: `grep -r "petgraph\|lasso" src/ --include="*.rs"`
Expected: zero matches (comments mentioning "petgraph" in design docs are OK)

- [ ] **Step 4: Check dep count**

Run: `cargo tree --prefix=none | sed 's/ v.*//' | sort -u | wc -l`
Expected: ~114 (down from 125). Bitcode stays (used by incremental_cache, models, embedding_scorer).

- [ ] **Step 5: Run full test suite + clippy**

Run: `cargo test --lib -- --quiet && cargo clippy --all-features -- -D warnings`
Expected: all tests pass, no clippy warnings

- [ ] **Step 6: Commit**

```bash
git add Cargo.toml
git commit -m "chore(deps): remove petgraph + lasso — graph engine fully hand-rolled"
```

---

## Task 12: BFS vertex reordering

**Files:**
- Modify: `src/graph/csr.rs` (if not already integrated in Task 6)
- Modify: `src/graph/frozen.rs` (enable reordering in freeze path)

- [ ] **Step 1: Write BFS reordering test**

```rust
#[test]
fn test_bfs_reorder_highest_degree_first() {
    // Node 2 has highest degree (3 edges), should get index 0 after reorder
    let edges = vec![
        (0u32, 2, EdgeKind::Calls),
        (1, 2, EdgeKind::Calls),
        (2, 0, EdgeKind::Calls),
        (2, 1, EdgeKind::Calls),
        (2, 3, EdgeKind::Calls),
    ];
    let perm = bfs_reorder(4, &edges, ...);
    // Node 2 should map to new index 0 (BFS seed)
    assert_eq!(perm[2], 0);
}

#[test]
fn test_bfs_reorder_deterministic_tiebreak() {
    // Two nodes with same degree — must be deterministic
    let perm1 = bfs_reorder(...);
    let perm2 = bfs_reorder(...);
    assert_eq!(perm1, perm2);
}
```

- [ ] **Step 2: Enable in freeze path**

Wire `bfs_reorder()` into `CodeGraph::build()` between compaction and CSR construction. Apply the permutation to nodes, edges, and node_index map.

- [ ] **Step 3: Run full test suite**

Run: `cargo test --lib -- --quiet`
Expected: all tests pass (reordering is transparent — query results unchanged, just internal layout differs)

- [ ] **Step 4: Commit**

```bash
git add src/graph/csr.rs src/graph/frozen.rs
git commit -m "feat(graph): enable BFS vertex reordering at freeze time"
```

---

## Task 13: Final cleanup + graph/mod.rs

**Files:**
- Modify: `src/graph/mod.rs`

- [ ] **Step 1: Update module doc comment**

Replace "Pure Rust implementation using petgraph" with "Pure Rust graph engine — CSR-backed, zero external dependencies."

- [ ] **Step 2: Update re-exports**

Ensure all new public types are re-exported:
```rust
pub use node_index::NodeIndex;
pub use csr::{CsrStorage, Slot, STRIDE};
pub use algo::{tarjan_scc, compute_dominators};
```

- [ ] **Step 3: Final full test + clippy + fmt**

Run:
```bash
cargo test --lib -- --quiet
cargo clippy --all-features -- -D warnings
cargo fmt --all -- --check
```
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add src/graph/mod.rs
git commit -m "chore(graph): final cleanup — update module docs and re-exports"
```
