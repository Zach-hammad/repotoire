# Graph Primitives Engine Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pre-compute graph-theoretic primitives (dominator trees, articulation points, PageRank, betweenness, call-graph SCCs) during `freeze()` and build 3 new detectors that are impossible without graph topology.

**Architecture:** New `GraphPrimitives` struct computed inside `GraphIndexes::build()`, stored as a field on `GraphIndexes`, exposed through `CodeGraph` accessors and `GraphQuery` trait methods. 3 new detectors consume primitives at O(1). 3 existing detectors migrated from ad-hoc computation to primitive reads.

**Tech Stack:** Rust, petgraph 0.7 (`dominators::simple_fast`, `tarjan_scc`), rayon (parallel primitive computation), custom sparse PageRank, custom Tarjan articulation point detection.

**Spec:** `docs/superpowers/specs/2026-03-17-graph-primitives-engine-design.md`

---

## Chunk 1: Primitives Foundation

### Task 1: Create `GraphPrimitives` struct and empty `compute()`

**Files:**
- Create: `repotoire-cli/src/graph/primitives.rs`
- Modify: `repotoire-cli/src/graph/mod.rs:17` (add module declaration)

- [ ] **Step 1: Create the primitives module with struct, Default, and empty compute**

Create `repotoire-cli/src/graph/primitives.rs`:

```rust
//! Pre-computed graph algorithm results.
//!
//! `GraphPrimitives` is computed once during `GraphIndexes::build()` and provides
//! pre-computed dominator trees, articulation points, PageRank, betweenness
//! centrality, and call-graph SCCs. All fields are immutable after construction.
//! Detectors read them at O(1) — zero graph traversal at detection time.

use petgraph::stable_graph::{NodeIndex, StableGraph};
use std::collections::{HashMap, HashSet};

use super::store_models::{CodeEdge, CodeNode};

// SAFETY: GraphPrimitives contains only HashMap, HashSet, Vec, and f64 —
// all Send + Sync. Adding it to GraphIndexes (inside CodeGraph) does not
// violate the existing unsafe impl Send/Sync for CodeGraph.

/// Pre-computed graph algorithm results. Computed once during freeze().
/// All fields are immutable. O(1) access from any detector via CodeGraph.
#[derive(Default)]
pub struct GraphPrimitives {
    // ── Dominator analysis (directed call graph) ──
    /// Immediate dominator for each function (None for virtual-root children)
    pub(crate) idom: HashMap<NodeIndex, NodeIndex>,
    /// Transitive domination: all nodes dominated by each node
    pub(crate) dominated: HashMap<NodeIndex, Vec<NodeIndex>>,
    /// Domination frontier: where each node's dominance boundary ends
    pub(crate) frontier: HashMap<NodeIndex, Vec<NodeIndex>>,
    /// Depth in dominator tree (0 = entry points)
    pub(crate) dom_depth: HashMap<NodeIndex, usize>,

    // ── Structural connectivity (undirected call+import graph) ──
    /// Nodes whose removal disconnects the graph (sorted by index)
    pub(crate) articulation_points: Vec<NodeIndex>,
    /// For O(1) is_articulation_point() checks
    pub(crate) articulation_point_set: HashSet<NodeIndex>,
    /// Edges whose removal disconnects the graph
    pub(crate) bridges: Vec<(NodeIndex, NodeIndex)>,
    /// Per articulation point: sizes of components that would separate
    pub(crate) component_sizes: HashMap<NodeIndex, Vec<usize>>,

    // ── Call-graph cycles ──
    /// Mutual recursion groups (SCCs with >1 node on call graph)
    pub(crate) call_cycles: Vec<Vec<NodeIndex>>,

    // ── Centrality metrics ──
    /// PageRank score per function (call graph, sparse power iteration)
    pub(crate) page_rank: HashMap<NodeIndex, f64>,
    /// Sampled betweenness centrality per function (raw, unnormalized)
    pub(crate) betweenness: HashMap<NodeIndex, f64>,

    // ── BFS call depth (for FunctionContext backward compat) ──
    /// Shortest-path depth from entry points via BFS on call graph.
    /// Distinct from dom_depth: BFS depth is shortest path, dom_depth is dominator tree depth.
    /// Kept for FunctionContext.call_depth backward compatibility.
    pub(crate) call_depth: HashMap<NodeIndex, usize>,
}

impl GraphPrimitives {
    /// Compute all graph primitives from a frozen graph's data.
    ///
    /// Called by `GraphIndexes::build()` after adjacency indexes are ready.
    /// Returns `Default` for empty graphs (0 functions or 0 call edges).
    /// Independent algorithms run in parallel via `rayon::join`.
    pub fn compute(
        graph: &StableGraph<CodeNode, CodeEdge>,
        functions: &[NodeIndex],
        files: &[NodeIndex],
        all_call_edges: &[(NodeIndex, NodeIndex)],
        all_import_edges: &[(NodeIndex, NodeIndex)],
        call_callers: &HashMap<NodeIndex, Vec<NodeIndex>>,
        call_callees: &HashMap<NodeIndex, Vec<NodeIndex>>,
        edge_fingerprint: u64,
    ) -> Self {
        if functions.is_empty() || all_call_edges.is_empty() {
            return Self::default();
        }

        // TODO: Implement algorithms in subsequent tasks.
        // Final form uses rayon::join for parallel computation:
        //   Thread A: call_cycles (SCCs)
        //   Thread B: page_rank (sparse)
        //   Thread C: betweenness (sampled Brandes, par_iter)
        //   Thread D: articulation points
        //   Sequential: dominators (depends on SCCs for disconnected handling)
        Self::default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_is_empty() {
        let p = GraphPrimitives::default();
        assert!(p.idom.is_empty());
        assert!(p.dominated.is_empty());
        assert!(p.page_rank.is_empty());
        assert!(p.call_cycles.is_empty());
        assert!(p.articulation_points.is_empty());
    }

    #[test]
    fn test_compute_empty_graph_returns_default() {
        let graph = StableGraph::new();
        let p = GraphPrimitives::compute(
            &graph, &[], &[], &[], &[], &HashMap::new(), &HashMap::new(), 0,
        );
        assert!(p.idom.is_empty());
        assert!(p.page_rank.is_empty());
    }
}
```

- [ ] **Step 2: Add module declaration in `graph/mod.rs`**

In `repotoire-cli/src/graph/mod.rs`, after `pub mod metrics_cache;` (line 17), add:

```rust
pub mod primitives;
```

- [ ] **Step 3: Verify compilation**

Run: `cd repotoire-cli && cargo check`
Expected: Compiles without errors.

- [ ] **Step 4: Run tests**

Run: `cd repotoire-cli && cargo test graph::primitives`
Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/graph/primitives.rs repotoire-cli/src/graph/mod.rs
git commit -m "feat: add GraphPrimitives struct with empty compute()"
```

---

### Task 2: Wire `GraphPrimitives` into `GraphIndexes`

**Files:**
- Modify: `repotoire-cli/src/graph/indexes.rs:21-61` (add field to struct)
- Modify: `repotoire-cli/src/graph/indexes.rs:64-90` (add to Default)
- Modify: `repotoire-cli/src/graph/indexes.rs:247-253` (call compute in build)

- [ ] **Step 1: Add `primitives` field to `GraphIndexes` struct**

In `repotoire-cli/src/graph/indexes.rs`, after line 60 (`pub(crate) edge_fingerprint: u64,`), add:

```rust
    // ── Pre-computed graph primitives ──
    pub(crate) primitives: super::primitives::GraphPrimitives,
```

- [ ] **Step 2: Add to Default impl**

In the `Default` impl, after line 88 (`edge_fingerprint: 0,`), add:

```rust
            primitives: super::primitives::GraphPrimitives::default(),
```

- [ ] **Step 3: Call `compute()` in `build()` before return**

In `GraphIndexes::build()`, after line 250 (`indexes.edge_fingerprint = compute_edge_fingerprint(graph);`) and before line 252 (`indexes`), insert:

```rust
        // ── Step 7-9: Compute graph primitives ──
        indexes.primitives = super::primitives::GraphPrimitives::compute(
            graph,
            &indexes.functions,
            &indexes.files,
            &indexes.all_call_edges,
            &indexes.all_import_edges,
            &indexes.call_callers,
            &indexes.call_callees,
            indexes.edge_fingerprint,
        );
```

- [ ] **Step 4: Add import at top of file**

No import needed — using `super::primitives::GraphPrimitives` path.

- [ ] **Step 5: Verify compilation**

Run: `cd repotoire-cli && cargo check`
Expected: Compiles without errors.

- [ ] **Step 6: Run all existing tests to verify no regressions**

Run: `cd repotoire-cli && cargo test graph::indexes`
Expected: All existing tests pass.

- [ ] **Step 7: Commit**

```bash
git add repotoire-cli/src/graph/indexes.rs
git commit -m "feat: wire GraphPrimitives into GraphIndexes::build()"
```

---

### Task 3: Add `CodeGraph` accessor methods

**Files:**
- Modify: `repotoire-cli/src/graph/frozen.rs:295-376` (add methods before lifecycle section)

- [ ] **Step 1: Write tests for accessor methods**

Add to the existing `#[cfg(test)] mod tests` in `frozen.rs`, after the last test:

```rust
    #[test]
    fn test_primitive_accessors_empty_graph() {
        let builder = GraphBuilder::new();
        let graph = builder.freeze();

        // All primitives should return empty/zero for an empty graph
        let fake_idx = NodeIndex::new(0);
        assert!(graph.dominated_by(fake_idx).is_empty());
        assert!(graph.domination_frontier(fake_idx).is_empty());
        assert_eq!(graph.dominator_depth(fake_idx), 0);
        assert_eq!(graph.domination_count(fake_idx), 0);
        assert!(!graph.is_articulation_point(fake_idx));
        assert!(graph.articulation_points().is_empty());
        assert!(graph.bridges().is_empty());
        assert!(graph.separation_sizes(fake_idx).is_none());
        assert!(graph.call_cycles().is_empty());
        assert_eq!(graph.page_rank(fake_idx), 0.0);
        assert_eq!(graph.betweenness(fake_idx), 0.0);
        assert!(graph.immediate_dominator(fake_idx).is_none());
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd repotoire-cli && cargo test frozen::tests::test_primitive_accessors_empty_graph`
Expected: FAIL — methods don't exist yet.

- [ ] **Step 3: Add accessor methods to `CodeGraph`**

In `repotoire-cli/src/graph/frozen.rs`, after the `edge_fingerprint()` method (line 307) and before the `stats()` method (line 310), add:

```rust
    // ==================== Graph Primitives (O(1)) ====================

    /// Immediate dominator of this node in the dominator tree.
    pub fn immediate_dominator(&self, idx: NodeIndex) -> Option<NodeIndex> {
        self.indexes.primitives.idom.get(&idx).copied()
    }

    /// All nodes transitively dominated by this node.
    pub fn dominated_by(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.indexes
            .primitives
            .dominated
            .get(&idx)
            .map(|v| v.as_slice())
            .unwrap_or(EMPTY_NODE_SLICE)
    }

    /// Domination frontier: nodes just beyond this node's dominance.
    pub fn domination_frontier(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.indexes
            .primitives
            .frontier
            .get(&idx)
            .map(|v| v.as_slice())
            .unwrap_or(EMPTY_NODE_SLICE)
    }

    /// Depth in dominator tree (0 = entry point).
    pub fn dominator_depth(&self, idx: NodeIndex) -> usize {
        self.indexes
            .primitives
            .dom_depth
            .get(&idx)
            .copied()
            .unwrap_or(0)
    }

    /// Number of functions dominated by this node.
    pub fn domination_count(&self, idx: NodeIndex) -> usize {
        self.dominated_by(idx).len()
    }

    /// Whether this node is an articulation point.
    pub fn is_articulation_point(&self, idx: NodeIndex) -> bool {
        self.indexes.primitives.articulation_point_set.contains(&idx)
    }

    /// All articulation points (sorted by index).
    pub fn articulation_points(&self) -> &[NodeIndex] {
        &self.indexes.primitives.articulation_points
    }

    /// All bridge edges.
    pub fn bridges(&self) -> &[(NodeIndex, NodeIndex)] {
        &self.indexes.primitives.bridges
    }

    /// Component sizes that would result from removing an articulation point.
    pub fn separation_sizes(&self, idx: NodeIndex) -> Option<&[usize]> {
        self.indexes
            .primitives
            .component_sizes
            .get(&idx)
            .map(|v| v.as_slice())
    }

    /// Call-graph SCCs (mutual recursion groups).
    pub fn call_cycles(&self) -> &[Vec<NodeIndex>] {
        &self.indexes.primitives.call_cycles
    }

    /// PageRank score for a function.
    pub fn page_rank(&self, idx: NodeIndex) -> f64 {
        self.indexes
            .primitives
            .page_rank
            .get(&idx)
            .copied()
            .unwrap_or(0.0)
    }

    /// Betweenness centrality score for a function.
    pub fn betweenness(&self, idx: NodeIndex) -> f64 {
        self.indexes
            .primitives
            .betweenness
            .get(&idx)
            .copied()
            .unwrap_or(0.0)
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd repotoire-cli && cargo test frozen::tests::test_primitive_accessors_empty_graph`
Expected: PASS

- [ ] **Step 5: Run all frozen tests for regressions**

Run: `cd repotoire-cli && cargo test frozen::tests`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add repotoire-cli/src/graph/frozen.rs
git commit -m "feat: add GraphPrimitives accessor methods to CodeGraph"
```

---

### Task 4: Extend `GraphQuery` trait with primitive methods

**Files:**
- Modify: `repotoire-cli/src/graph/traits.rs` (add 10 new trait methods with defaults)

- [ ] **Step 1: Add 10 new trait methods at end of NodeIndex-based section**

In `repotoire-cli/src/graph/traits.rs`, after the last existing `_idx` method (find the comment `// ── Pre-computed analyses ──` section near `import_cycles_idx` and `edge_fingerprint_idx`), add before the closing brace of the trait:

```rust
    // ── Graph primitives (pre-computed during freeze) ──

    /// All nodes transitively dominated by this node.
    fn dominated_by_idx(&self, _idx: NodeIndex) -> &[NodeIndex] {
        &[]
    }

    /// Domination frontier of this node.
    fn domination_frontier_idx(&self, _idx: NodeIndex) -> &[NodeIndex] {
        &[]
    }

    /// Depth in dominator tree (0 = entry point).
    fn dominator_depth_idx(&self, _idx: NodeIndex) -> usize {
        0
    }

    /// Whether this node is an articulation point.
    fn is_articulation_point_idx(&self, _idx: NodeIndex) -> bool {
        false
    }

    /// All articulation points (sorted).
    fn articulation_points_idx(&self) -> &[NodeIndex] {
        &[]
    }

    /// Component sizes if this articulation point were removed.
    fn separation_sizes_idx(&self, _idx: NodeIndex) -> Option<&[usize]> {
        None
    }

    /// All bridge edges.
    fn bridge_edges_idx(&self) -> &[(NodeIndex, NodeIndex)] {
        &[]
    }

    /// Call-graph SCCs (mutual recursion groups).
    fn call_cycles_idx(&self) -> &[Vec<NodeIndex>] {
        &[]
    }

    /// PageRank score for a function.
    fn page_rank_idx(&self, _idx: NodeIndex) -> f64 {
        0.0
    }

    /// Betweenness centrality score for a function.
    fn betweenness_idx(&self, _idx: NodeIndex) -> f64 {
        0.0
    }
```

- [ ] **Step 2: Verify compilation**

Run: `cd repotoire-cli && cargo check`
Expected: Compiles. Default impls mean no downstream breakage.

- [ ] **Step 3: Commit**

```bash
git add repotoire-cli/src/graph/traits.rs
git commit -m "feat: extend GraphQuery trait with 10 primitive query methods"
```

---

### Task 5: Implement `GraphQuery` overrides in `compat.rs`

**Files:**
- Modify: `repotoire-cli/src/graph/compat.rs` (add overrides for `CodeGraph` and `Arc<CodeGraph>`)

- [ ] **Step 1: Add `CodeGraph` overrides**

In the `impl GraphQuery for CodeGraph` block, in the NodeIndex-based overrides section (near the end of the block, after the last `_idx` method override), add:

```rust
    // ── Graph primitives ──

    fn dominated_by_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.dominated_by(idx)
    }

    fn domination_frontier_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.domination_frontier(idx)
    }

    fn dominator_depth_idx(&self, idx: NodeIndex) -> usize {
        self.dominator_depth(idx)
    }

    fn is_articulation_point_idx(&self, idx: NodeIndex) -> bool {
        self.is_articulation_point(idx)
    }

    fn articulation_points_idx(&self) -> &[NodeIndex] {
        self.articulation_points()
    }

    fn separation_sizes_idx(&self, idx: NodeIndex) -> Option<&[usize]> {
        self.separation_sizes(idx)
    }

    fn bridge_edges_idx(&self) -> &[(NodeIndex, NodeIndex)] {
        self.bridges()
    }

    fn call_cycles_idx(&self) -> &[Vec<NodeIndex>] {
        self.call_cycles()
    }

    fn page_rank_idx(&self, idx: NodeIndex) -> f64 {
        self.page_rank(idx)
    }

    fn betweenness_idx(&self, idx: NodeIndex) -> f64 {
        self.betweenness(idx)
    }
```

- [ ] **Step 2: Add `Arc<CodeGraph>` delegation**

In the `impl GraphQuery for Arc<CodeGraph>` block, in the NodeIndex-based overrides section (near the end of the block), add:

```rust
    // ── Graph primitives ──

    fn dominated_by_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        <CodeGraph as super::traits::GraphQuery>::dominated_by_idx(self, idx)
    }

    fn domination_frontier_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        <CodeGraph as super::traits::GraphQuery>::domination_frontier_idx(self, idx)
    }

    fn dominator_depth_idx(&self, idx: NodeIndex) -> usize {
        <CodeGraph as super::traits::GraphQuery>::dominator_depth_idx(self, idx)
    }

    fn is_articulation_point_idx(&self, idx: NodeIndex) -> bool {
        <CodeGraph as super::traits::GraphQuery>::is_articulation_point_idx(self, idx)
    }

    fn articulation_points_idx(&self) -> &[NodeIndex] {
        <CodeGraph as super::traits::GraphQuery>::articulation_points_idx(self)
    }

    fn separation_sizes_idx(&self, idx: NodeIndex) -> Option<&[usize]> {
        <CodeGraph as super::traits::GraphQuery>::separation_sizes_idx(self, idx)
    }

    fn bridge_edges_idx(&self) -> &[(NodeIndex, NodeIndex)] {
        <CodeGraph as super::traits::GraphQuery>::bridge_edges_idx(self)
    }

    fn call_cycles_idx(&self) -> &[Vec<NodeIndex>] {
        <CodeGraph as super::traits::GraphQuery>::call_cycles_idx(self)
    }

    fn page_rank_idx(&self, idx: NodeIndex) -> f64 {
        <CodeGraph as super::traits::GraphQuery>::page_rank_idx(self, idx)
    }

    fn betweenness_idx(&self, idx: NodeIndex) -> f64 {
        <CodeGraph as super::traits::GraphQuery>::betweenness_idx(self, idx)
    }
```

- [ ] **Step 3: Verify compilation**

Run: `cd repotoire-cli && cargo check`
Expected: Compiles without errors.

- [ ] **Step 4: Run full test suite to verify no regressions**

Run: `cd repotoire-cli && cargo test`
Expected: All tests pass. Zero regressions.

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/graph/compat.rs
git commit -m "feat: implement GraphQuery primitive overrides for CodeGraph and Arc<CodeGraph>"
```

---

## Chunk 2: Algorithm Implementations

### Task 6: Implement call-graph SCCs (mutual recursion detection)

**Files:**
- Modify: `repotoire-cli/src/graph/primitives.rs` (add `compute_call_cycles()`)

- [ ] **Step 1: Write test for call-graph SCC detection**

Add to `primitives.rs` tests module:

```rust
    use crate::graph::builder::GraphBuilder;
    use crate::graph::store_models::{CodeEdge, CodeNode};

    #[test]
    fn test_call_cycles_detected() {
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("foo", "a.py"));
        let f2 = builder.add_node(CodeNode::function("bar", "a.py"));
        let f3 = builder.add_node(CodeNode::function("baz", "a.py"));
        // f1 -> f2 -> f3 -> f1 (mutual recursion)
        builder.add_edge(f1, f2, CodeEdge::calls());
        builder.add_edge(f2, f3, CodeEdge::calls());
        builder.add_edge(f3, f1, CodeEdge::calls());

        let graph = builder.freeze();
        assert_eq!(graph.call_cycles().len(), 1);
        assert_eq!(graph.call_cycles()[0].len(), 3);
    }

    #[test]
    fn test_no_call_cycles_in_dag() {
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("foo", "a.py"));
        let f2 = builder.add_node(CodeNode::function("bar", "a.py"));
        let f3 = builder.add_node(CodeNode::function("baz", "a.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());
        builder.add_edge(f2, f3, CodeEdge::calls());

        let graph = builder.freeze();
        assert!(graph.call_cycles().is_empty());
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd repotoire-cli && cargo test graph::primitives::tests::test_call_cycles`
Expected: FAIL — cycles are empty because compute() returns default.

- [ ] **Step 3: Implement `compute_call_cycles()`**

Add to `primitives.rs`, above `compute()`:

```rust
use petgraph::algo::tarjan_scc;
use super::interner::global_interner;

/// Compute call-graph SCCs (mutual recursion groups).
/// Follows the same idx_map/reverse_map pattern as compute_import_cycles().
fn compute_call_cycles(
    all_call_edges: &[(NodeIndex, NodeIndex)],
    graph: &StableGraph<CodeNode, CodeEdge>,
) -> Vec<Vec<NodeIndex>> {
    let si = global_interner();

    // Build filtered call-only subgraph with dense indices
    let mut filtered: StableGraph<NodeIndex, ()> = StableGraph::new();
    let mut idx_map: HashMap<NodeIndex, NodeIndex> = HashMap::new();
    let mut reverse_map: HashMap<NodeIndex, NodeIndex> = HashMap::new();

    // Collect nodes involved in call edges
    let mut relevant: HashSet<NodeIndex> = HashSet::new();
    for &(src, tgt) in all_call_edges {
        relevant.insert(src);
        relevant.insert(tgt);
    }

    let mut sorted_nodes: Vec<NodeIndex> = relevant.into_iter().collect();
    sorted_nodes.sort_by_key(|idx| idx.index());

    for orig_idx in sorted_nodes {
        let new_idx = filtered.add_node(orig_idx);
        idx_map.insert(orig_idx, new_idx);
        reverse_map.insert(new_idx, orig_idx);
    }

    for &(src, tgt) in all_call_edges {
        if let (Some(&from), Some(&to)) = (idx_map.get(&src), idx_map.get(&tgt)) {
            filtered.add_edge(from, to, ());
        }
    }

    let sccs = tarjan_scc(&filtered);

    let mut cycles: Vec<Vec<NodeIndex>> = sccs
        .into_iter()
        .filter(|scc| scc.len() > 1)
        .map(|scc| {
            let mut orig: Vec<NodeIndex> = scc
                .iter()
                .filter_map(|&idx| reverse_map.get(&idx).copied())
                .collect();
            orig.sort_by(|a, b| {
                let a_qn = graph.node_weight(*a).map(|n| si.resolve(n.qualified_name)).unwrap_or("");
                let b_qn = graph.node_weight(*b).map(|n| si.resolve(n.qualified_name)).unwrap_or("");
                a_qn.cmp(b_qn)
            });
            orig
        })
        .collect();

    cycles.sort_by(|a, b| b.len().cmp(&a.len()));
    cycles.dedup();
    cycles
}
```

- [ ] **Step 4: Wire into `compute()`**

Replace the `// TODO` line in `compute()` with:

```rust
        let call_cycles = compute_call_cycles(all_call_edges, graph);

        Self {
            call_cycles,
            ..Self::default()
        }
```

- [ ] **Step 5: Run tests**

Run: `cd repotoire-cli && cargo test graph::primitives`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add repotoire-cli/src/graph/primitives.rs
git commit -m "feat: implement call-graph SCC detection for mutual recursion"
```

---

### Task 7: Implement custom sparse PageRank

**Files:**
- Modify: `repotoire-cli/src/graph/primitives.rs` (add `compute_page_rank()`)

- [ ] **Step 1: Write PageRank test**

Add to tests:

```rust
    #[test]
    fn test_page_rank_hub_has_highest_score() {
        let mut builder = GraphBuilder::new();
        let hub = builder.add_node(CodeNode::function("hub", "a.py"));
        let f1 = builder.add_node(CodeNode::function("f1", "a.py"));
        let f2 = builder.add_node(CodeNode::function("f2", "a.py"));
        let f3 = builder.add_node(CodeNode::function("f3", "a.py"));
        let leaf = builder.add_node(CodeNode::function("leaf", "a.py"));
        // f1, f2, f3 all call hub; hub calls leaf
        builder.add_edge(f1, hub, CodeEdge::calls());
        builder.add_edge(f2, hub, CodeEdge::calls());
        builder.add_edge(f3, hub, CodeEdge::calls());
        builder.add_edge(hub, leaf, CodeEdge::calls());

        let graph = builder.freeze();
        let hub_pr = graph.page_rank(hub);
        let leaf_pr = graph.page_rank(leaf);
        let f1_pr = graph.page_rank(f1);

        // Hub should have highest PageRank (most important callers)
        assert!(hub_pr > leaf_pr, "hub ({hub_pr}) should outrank leaf ({leaf_pr})");
        assert!(hub_pr > f1_pr, "hub ({hub_pr}) should outrank f1 ({f1_pr})");
        // All scores should be positive
        assert!(hub_pr > 0.0);
        assert!(leaf_pr > 0.0);
    }

    #[test]
    fn test_page_rank_empty_returns_empty() {
        let builder = GraphBuilder::new();
        let graph = builder.freeze();
        // No functions — no PageRank
        assert!(graph.page_rank(NodeIndex::new(0)) == 0.0);
    }
```

- [ ] **Step 2: Run to verify failure**

Run: `cd repotoire-cli && cargo test graph::primitives::tests::test_page_rank`
Expected: FAIL — PageRank returns 0.0 for all.

- [ ] **Step 3: Implement sparse PageRank**

Add to `primitives.rs`:

```rust
/// Custom sparse PageRank. O((V+E) × iterations) — NOT petgraph's O(V²) built-in.
fn compute_page_rank(
    functions: &[NodeIndex],
    call_callees: &HashMap<NodeIndex, Vec<NodeIndex>>,
    call_callers: &HashMap<NodeIndex, Vec<NodeIndex>>,
    max_iterations: usize,
    damping: f64,
    tolerance: f64,
) -> HashMap<NodeIndex, f64> {
    let n = functions.len();
    if n == 0 {
        return HashMap::new();
    }

    let inv_n = 1.0 / n as f64;
    let base = (1.0 - damping) * inv_n;

    let mut rank: HashMap<NodeIndex, f64> = functions.iter().map(|&idx| (idx, inv_n)).collect();
    let mut new_rank: HashMap<NodeIndex, f64> = HashMap::with_capacity(n);

    // Pre-compute out-degree for each function
    let out_degree: HashMap<NodeIndex, usize> = functions
        .iter()
        .map(|&idx| {
            let deg = call_callees.get(&idx).map(|v| v.len()).unwrap_or(0);
            (idx, deg)
        })
        .collect();

    for _iter in 0..max_iterations {
        // Initialize with base rank
        for &idx in functions {
            new_rank.insert(idx, base);
        }

        // Distribute rank along edges (sparse iteration)
        for &src in functions {
            let src_rank = rank[&src];
            let deg = out_degree[&src];
            if deg == 0 {
                continue;
            }
            let contribution = damping * src_rank / deg as f64;
            if let Some(callees) = call_callees.get(&src) {
                for &tgt in callees {
                    if let Some(r) = new_rank.get_mut(&tgt) {
                        *r += contribution;
                    }
                }
            }
        }

        // Check convergence
        let delta: f64 = functions
            .iter()
            .map(|idx| (new_rank[idx] - rank[idx]).abs())
            .sum();

        std::mem::swap(&mut rank, &mut new_rank);

        if delta < tolerance {
            break;
        }
    }

    rank
}
```

- [ ] **Step 4: Wire into `compute()`**

Update `compute()` to call PageRank:

```rust
        let call_cycles = compute_call_cycles(all_call_edges, graph);
        let page_rank = compute_page_rank(
            functions, call_callees, call_callers,
            20,    // max iterations
            0.85,  // damping factor
            1e-6,  // convergence tolerance
        );

        Self {
            call_cycles,
            page_rank,
            ..Self::default()
        }
```

- [ ] **Step 5: Run tests**

Run: `cd repotoire-cli && cargo test graph::primitives`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add repotoire-cli/src/graph/primitives.rs
git commit -m "feat: implement custom sparse PageRank O((V+E)×iterations)"
```

---

### Task 8: Implement dominator tree + frontiers

**Files:**
- Modify: `repotoire-cli/src/graph/primitives.rs` (add dominator computation)

- [ ] **Step 1: Write dominator tests**

```rust
    #[test]
    fn test_dominator_tree_linear_chain() {
        // entry -> A -> B -> C
        // A dominates B and C. B dominates C.
        let mut builder = GraphBuilder::new();
        let entry = builder.add_node(CodeNode::function("entry", "a.py"));
        let a = builder.add_node(CodeNode::function("a_fn", "a.py"));
        let b = builder.add_node(CodeNode::function("b_fn", "a.py"));
        let c = builder.add_node(CodeNode::function("c_fn", "a.py"));
        builder.add_edge(entry, a, CodeEdge::calls());
        builder.add_edge(a, b, CodeEdge::calls());
        builder.add_edge(b, c, CodeEdge::calls());

        let graph = builder.freeze();

        // entry has in-degree 0, so it's an entry point
        // entry dominates a, b, c
        assert!(graph.dominated_by(entry).len() >= 3);
        // a dominates b and c
        assert!(graph.dominated_by(a).len() >= 2);
        // b dominates c
        assert!(graph.dominated_by(b).contains(&c));
        // c dominates nothing
        assert!(graph.dominated_by(c).is_empty());
        // Depth increases along chain
        assert!(graph.dominator_depth(a) < graph.dominator_depth(c));
    }

    #[test]
    fn test_dominator_diamond_no_single_dominator() {
        // entry -> A, entry -> B, A -> join, B -> join
        // Neither A nor B dominates join (two paths)
        let mut builder = GraphBuilder::new();
        let entry = builder.add_node(CodeNode::function("entry", "a.py"));
        let a = builder.add_node(CodeNode::function("a_fn", "a.py"));
        let b = builder.add_node(CodeNode::function("b_fn", "a.py"));
        let join = builder.add_node(CodeNode::function("join", "a.py"));
        builder.add_edge(entry, a, CodeEdge::calls());
        builder.add_edge(entry, b, CodeEdge::calls());
        builder.add_edge(a, join, CodeEdge::calls());
        builder.add_edge(b, join, CodeEdge::calls());

        let graph = builder.freeze();

        // A does NOT dominate join (B also reaches it)
        assert!(!graph.dominated_by(a).contains(&join));
        // entry dominates all (only path from root)
        assert!(graph.dominated_by(entry).len() >= 3);
    }
```

- [ ] **Step 2: Run to verify failure**

Run: `cd repotoire-cli && cargo test graph::primitives::tests::test_dominator`
Expected: FAIL — dominated_by returns empty.

- [ ] **Step 3: Implement dominator computation**

Add to `primitives.rs`:

```rust
use petgraph::algo::dominators;

/// Compute dominator tree, dominated sets, frontiers, and depths.
fn compute_dominators(
    functions: &[NodeIndex],
    all_call_edges: &[(NodeIndex, NodeIndex)],
    call_callers: &HashMap<NodeIndex, Vec<NodeIndex>>,
    call_callees: &HashMap<NodeIndex, Vec<NodeIndex>>,
    call_cycles: &[Vec<NodeIndex>],
    graph: &StableGraph<CodeNode, CodeEdge>,
) -> (
    HashMap<NodeIndex, NodeIndex>,      // idom
    HashMap<NodeIndex, Vec<NodeIndex>>, // dominated
    HashMap<NodeIndex, Vec<NodeIndex>>, // frontier
    HashMap<NodeIndex, usize>,          // dom_depth
) {
    let empty = (HashMap::new(), HashMap::new(), HashMap::new(), HashMap::new());
    if functions.is_empty() || all_call_edges.is_empty() {
        return empty;
    }

    // Detect entry points: in-degree 0 on call graph, with outgoing calls
    let func_set: HashSet<NodeIndex> = functions.iter().copied().collect();
    let mut entry_points: Vec<NodeIndex> = functions
        .iter()
        .filter(|&&idx| {
            let in_deg = call_callers.get(&idx).map(|v| v.len()).unwrap_or(0);
            let out_deg = call_callees.get(&idx).map(|v| v.len()).unwrap_or(0);
            in_deg == 0 && out_deg > 0
        })
        .copied()
        .collect();

    // Handle disconnected SCCs: run dominator first with current entry points,
    // then check which SCCs have no node in the idom map (truly unreachable).
    // Two-pass approach: first pass finds reachable nodes, second pass adds
    // representatives for unreachable SCCs.
    //
    // Simpler approach: compute full BFS reachability from entry points first.
    if !call_cycles.is_empty() {
        let mut reachable: HashSet<NodeIndex> = HashSet::new();
        let mut bfs_queue: std::collections::VecDeque<NodeIndex> = entry_points.iter().copied().collect();
        while let Some(node) = bfs_queue.pop_front() {
            if !reachable.insert(node) { continue; }
            if let Some(callees) = call_callees.get(&node) {
                for &callee in callees {
                    if func_set.contains(&callee) && !reachable.contains(&callee) {
                        bfs_queue.push_back(callee);
                    }
                }
            }
        }

        for cycle in call_cycles {
            if !cycle.iter().any(|n| reachable.contains(n)) {
                // Pick node with highest out-degree as representative
                if let Some(&rep) = cycle.iter().max_by_key(|&&idx| {
                    call_callees.get(&idx).map(|v| v.len()).unwrap_or(0)
                }) {
                    entry_points.push(rep);
                }
            }
        }
    }

    if entry_points.is_empty() {
        return empty;
    }

    // Build filtered call subgraph with virtual root
    let mut filtered: StableGraph<Option<NodeIndex>, ()> = StableGraph::new();
    let mut idx_map: HashMap<NodeIndex, NodeIndex> = HashMap::new();
    let mut reverse_map: HashMap<NodeIndex, NodeIndex> = HashMap::new();

    // Add virtual root
    let virtual_root = filtered.add_node(None);

    // Add all function nodes
    let mut sorted_funcs: Vec<NodeIndex> = func_set.iter().copied().collect();
    sorted_funcs.sort_by_key(|idx| idx.index());
    for orig_idx in sorted_funcs {
        let new_idx = filtered.add_node(Some(orig_idx));
        idx_map.insert(orig_idx, new_idx);
        reverse_map.insert(new_idx, orig_idx);
    }

    // Connect virtual root to entry points
    for &ep in &entry_points {
        if let Some(&mapped) = idx_map.get(&ep) {
            filtered.add_edge(virtual_root, mapped, ());
        }
    }

    // Add call edges
    for &(src, tgt) in all_call_edges {
        if let (Some(&from), Some(&to)) = (idx_map.get(&src), idx_map.get(&tgt)) {
            filtered.add_edge(from, to, ());
        }
    }

    // Run Cooper et al. dominator algorithm
    let doms = dominators::simple_fast(&filtered, virtual_root);

    // Extract idom, build dominated sets and depths
    let mut idom: HashMap<NodeIndex, NodeIndex> = HashMap::new();
    let mut dom_depth: HashMap<NodeIndex, usize> = HashMap::new();
    let mut children: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();

    for &func_idx in functions {
        if let Some(&mapped) = idx_map.get(&func_idx) {
            if let Some(idom_mapped) = doms.immediate_dominator(mapped) {
                if idom_mapped == virtual_root {
                    // Dominated by virtual root = entry point
                    dom_depth.insert(func_idx, 0);
                } else if let Some(&orig_idom) = reverse_map.get(&idom_mapped) {
                    idom.insert(func_idx, orig_idom);
                    children.entry(orig_idom).or_default().push(func_idx);
                }
            }
        }
    }

    // Compute depths via BFS from roots
    let roots: Vec<NodeIndex> = functions
        .iter()
        .filter(|idx| !idom.contains_key(idx) && dom_depth.get(idx) == Some(&0))
        .copied()
        .collect();

    let mut queue: std::collections::VecDeque<NodeIndex> = roots.into_iter().collect();
    while let Some(node) = queue.pop_front() {
        let depth = dom_depth.get(&node).copied().unwrap_or(0);
        if let Some(kids) = children.get(&node) {
            for &kid in kids {
                dom_depth.insert(kid, depth + 1);
                queue.push_back(kid);
            }
        }
    }

    // Build transitive dominated sets
    let mut dominated: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();
    fn collect_dominated(
        node: NodeIndex,
        children: &HashMap<NodeIndex, Vec<NodeIndex>>,
        dominated: &mut HashMap<NodeIndex, Vec<NodeIndex>>,
    ) -> Vec<NodeIndex> {
        let mut all = Vec::new();
        if let Some(kids) = children.get(&node) {
            for &kid in kids {
                all.push(kid);
                let sub = collect_dominated(kid, children, dominated);
                all.extend(sub);
            }
        }
        dominated.insert(node, all.clone());
        all
    }
    for &func_idx in functions {
        if !dominated.contains_key(&func_idx) {
            collect_dominated(func_idx, &children, &mut dominated);
        }
    }

    // Compute domination frontiers (Cooper et al. standard algorithm)
    // A node b is in the frontier of runner if:
    // runner dominates a predecessor of b, but does not strictly dominate b
    let mut frontier: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();
    for &b in functions {
        let callers = call_callers.get(&b).cloned().unwrap_or_default();
        if callers.len() < 2 {
            continue;
        }
        let b_idom = idom.get(&b).copied();
        for &p in &callers {
            let mut runner = p;
            while Some(runner) != b_idom {
                frontier.entry(runner).or_default().push(b);
                match idom.get(&runner) {
                    Some(&next) if next != runner => runner = next,
                    _ => break,
                }
            }
        }
    }
    // Dedup frontiers
    for v in frontier.values_mut() {
        v.sort_by_key(|idx| idx.index());
        v.dedup();
    }

    (idom, dominated, frontier, dom_depth)
}
```

- [ ] **Step 4: Wire into `compute()`**

Update `compute()`:

```rust
        let call_cycles = compute_call_cycles(all_call_edges, graph);
        let page_rank = compute_page_rank(
            functions, call_callees, call_callers, 20, 0.85, 1e-6,
        );
        let (idom, dominated, frontier, dom_depth) = compute_dominators(
            functions, all_call_edges, call_callers, call_callees, &call_cycles, graph,
        );

        Self {
            idom,
            dominated,
            frontier,
            dom_depth,
            call_cycles,
            page_rank,
            ..Self::default()
        }
```

- [ ] **Step 5: Run tests**

Run: `cd repotoire-cli && cargo test graph::primitives`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add repotoire-cli/src/graph/primitives.rs
git commit -m "feat: implement dominator tree, dominated sets, frontiers, and depths"
```

---

### Task 9: Implement articulation points + bridges + component sizes

**Files:**
- Modify: `repotoire-cli/src/graph/primitives.rs` (add Tarjan biconnected)

- [ ] **Step 1: Write articulation point tests**

```rust
    #[test]
    fn test_articulation_point_bridge_node() {
        // A -- bridge -- B, bridge is an articulation point
        // cluster1: f1 <-> f2 <-> f3 (triangle)
        // bridge connects f3 -> f4
        // cluster2: f4 <-> f5 <-> f6 (triangle)
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("f1", "a.py"));
        let f2 = builder.add_node(CodeNode::function("f2", "a.py"));
        let f3 = builder.add_node(CodeNode::function("f3", "a.py"));
        let f4 = builder.add_node(CodeNode::function("f4", "b.py"));
        let f5 = builder.add_node(CodeNode::function("f5", "b.py"));
        let f6 = builder.add_node(CodeNode::function("f6", "b.py"));
        // Cluster 1
        builder.add_edge(f1, f2, CodeEdge::calls());
        builder.add_edge(f2, f3, CodeEdge::calls());
        builder.add_edge(f3, f1, CodeEdge::calls());
        // Bridge
        builder.add_edge(f3, f4, CodeEdge::calls());
        // Cluster 2
        builder.add_edge(f4, f5, CodeEdge::calls());
        builder.add_edge(f5, f6, CodeEdge::calls());
        builder.add_edge(f6, f4, CodeEdge::calls());

        let graph = builder.freeze();

        // f3 and f4 should be articulation points (bridge between clusters)
        assert!(graph.is_articulation_point(f3) || graph.is_articulation_point(f4),
            "at least one bridge node should be an articulation point");
        assert!(!graph.articulation_points().is_empty());
    }

    #[test]
    fn test_no_articulation_points_in_complete_graph() {
        // Fully connected: no single removal disconnects
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("f1", "a.py"));
        let f2 = builder.add_node(CodeNode::function("f2", "a.py"));
        let f3 = builder.add_node(CodeNode::function("f3", "a.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());
        builder.add_edge(f2, f1, CodeEdge::calls());
        builder.add_edge(f2, f3, CodeEdge::calls());
        builder.add_edge(f3, f2, CodeEdge::calls());
        builder.add_edge(f1, f3, CodeEdge::calls());
        builder.add_edge(f3, f1, CodeEdge::calls());

        let graph = builder.freeze();
        assert!(graph.articulation_points().is_empty());
    }
```

- [ ] **Step 2: Run to verify failure**

Run: `cd repotoire-cli && cargo test graph::primitives::tests::test_articulation`
Expected: FAIL.

- [ ] **Step 3: Implement Tarjan's biconnected components**

Add `compute_articulation_points()` to `primitives.rs`. This is a standard DFS-based algorithm tracking discovery times and low-link values on the undirected projection of call+import edges. Implementation follows the classic Tarjan approach:

```rust
/// Compute articulation points, bridges, and component sizes.
/// Uses Tarjan's biconnected components on undirected projection of Calls + Imports.
fn compute_articulation_points(
    functions: &[NodeIndex],
    all_call_edges: &[(NodeIndex, NodeIndex)],
    all_import_edges: &[(NodeIndex, NodeIndex)],
    files: &[NodeIndex],
) -> (Vec<NodeIndex>, HashSet<NodeIndex>, Vec<(NodeIndex, NodeIndex)>, HashMap<NodeIndex, Vec<usize>>) {
    // Build undirected adjacency from Calls + Imports
    let mut adj: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();
    let mut all_nodes: HashSet<NodeIndex> = HashSet::new();

    for nodes in [functions, files] {
        for &idx in nodes {
            all_nodes.insert(idx);
        }
    }

    for edges in [all_call_edges, all_import_edges] {
        for &(src, tgt) in edges {
            if all_nodes.contains(&src) && all_nodes.contains(&tgt) && src != tgt {
                adj.entry(src).or_default().push(tgt);
                adj.entry(tgt).or_default().push(src);
            }
        }
    }

    // Dedup adjacency
    for v in adj.values_mut() {
        v.sort_by_key(|idx| idx.index());
        v.dedup();
    }

    let n = all_nodes.len();
    if n == 0 {
        return (Vec::new(), HashSet::new(), Vec::new(), HashMap::new());
    }

    // Tarjan's algorithm for articulation points and bridges
    let mut disc: HashMap<NodeIndex, usize> = HashMap::new();
    let mut low: HashMap<NodeIndex, usize> = HashMap::new();
    let mut parent: HashMap<NodeIndex, Option<NodeIndex>> = HashMap::new();
    let mut ap_set: HashSet<NodeIndex> = HashSet::new();
    let mut bridges: Vec<(NodeIndex, NodeIndex)> = Vec::new();
    let mut timer: usize = 0;
    let mut subtree_sizes: HashMap<NodeIndex, usize> = HashMap::new();

    // Iterative DFS to avoid stack overflow on 50k+ node graphs.
    // Uses explicit stack with (node, neighbor_index, is_returning) frames.
    let mut sorted_nodes: Vec<NodeIndex> = all_nodes.into_iter().collect();
    sorted_nodes.sort_by_key(|idx| idx.index());
    let mut child_counts: HashMap<NodeIndex, usize> = HashMap::new();

    for &start in &sorted_nodes {
        if disc.contains_key(&start) {
            continue;
        }
        parent.insert(start, None);

        // Stack: (node, index into neighbors, subtree_size_so_far)
        let mut stack: Vec<(NodeIndex, usize, usize)> = vec![(start, 0, 1)];
        disc.insert(start, timer);
        low.insert(start, timer);
        timer += 1;

        while let Some((u, ni, size)) = stack.last_mut() {
            let neighbors = adj.get(u).cloned().unwrap_or_default();
            if *ni < neighbors.len() {
                let v = neighbors[*ni];
                *ni += 1;

                if !disc.contains_key(&v) {
                    *child_counts.entry(*u).or_insert(0) += 1;
                    parent.insert(v, Some(*u));
                    disc.insert(v, timer);
                    low.insert(v, timer);
                    timer += 1;
                    stack.push((v, 0, 1));
                } else if Some(v) != parent.get(u).copied().flatten() {
                    let v_disc = disc[&v];
                    let u_low = low[u];
                    if v_disc < u_low {
                        low.insert(*u, v_disc);
                    }
                }
            } else {
                // Done with all neighbors of u — pop and update parent
                let u = *u;
                let u_size = *size;
                subtree_sizes.insert(u, u_size);
                stack.pop();

                if let Some((parent_node, _, parent_size)) = stack.last_mut() {
                    let v_low = low[&u];
                    let p_low = low[parent_node];
                    if v_low < p_low {
                        low.insert(*parent_node, v_low);
                    }

                    let p_disc = disc[parent_node];
                    // Articulation point: non-root with low[child] >= disc[parent]
                    if low[&u] >= p_disc {
                        ap_set.insert(*parent_node);
                    }
                    // Bridge condition
                    if low[&u] > p_disc {
                        bridges.push((*parent_node, u));
                    }

                    *parent_size += u_size;
                } else {
                    // u is root — articulation point if >1 children
                    let cc = child_counts.get(&u).copied().unwrap_or(0);
                    if cc > 1 {
                        ap_set.insert(u);
                    }
                }
            }
        }
    }

    // Compute component sizes for each articulation point
    let total = sorted_nodes.len();
    let mut component_sizes: HashMap<NodeIndex, Vec<usize>> = HashMap::new();
    for &ap in &ap_set {
        let mut sizes: Vec<usize> = Vec::new();
        let mut used = 0;
        let neighbors = adj.get(&ap).cloned().unwrap_or_default();
        for &v in &neighbors {
            if parent.get(&v).copied().flatten() == Some(ap) {
                let st = subtree_sizes.get(&v).copied().unwrap_or(1);
                sizes.push(st);
                used += st;
            }
        }
        // Remaining nodes (not in any child subtree)
        let remaining = total.saturating_sub(used + 1);
        if remaining > 0 {
            sizes.push(remaining);
        }
        sizes.sort_unstable();
        sizes.reverse();
        component_sizes.insert(ap, sizes);
    }

    let mut ap_vec: Vec<NodeIndex> = ap_set.iter().copied().collect();
    ap_vec.sort_by_key(|idx| idx.index());

    (ap_vec, ap_set, bridges, component_sizes)
}
```

- [ ] **Step 4: Wire into `compute()`**

Update `compute()` to call articulation points and assemble the full struct:

```rust
        // Step 1: SCCs first (needed by dominator for disconnected component handling)
        let call_cycles = compute_call_cycles(all_call_edges, graph);

        // Step 2: Independent algorithms in parallel via rayon::join
        let (page_rank, (betweenness, ap_result)) = rayon::join(
            || compute_page_rank(functions, call_callees, call_callers, 20, 0.85, 1e-6),
            || rayon::join(
                || compute_betweenness(functions, call_callees, edge_fingerprint),
                || compute_articulation_points(functions, all_call_edges, all_import_edges, files),
            ),
        );
        let (articulation_points, articulation_point_set, bridges, component_sizes) = ap_result;

        // Step 3: Dominators (depends on call_cycles for disconnected handling)
        let (idom, dominated, frontier, dom_depth) = compute_dominators(
            functions, all_call_edges, call_callers, call_callees, &call_cycles, graph,
        );

        // Step 4: BFS call depths (backward compat with FunctionContext.call_depth)
        let call_depth = compute_call_depths(functions, call_callees, call_callers);

        Self {
            idom, dominated, frontier, dom_depth,
            articulation_points, articulation_point_set, bridges, component_sizes,
            call_cycles,
            page_rank, betweenness, call_depth,
        }
```

- [ ] **Step 5: Run tests**

Run: `cd repotoire-cli && cargo test graph::primitives`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add repotoire-cli/src/graph/primitives.rs
git commit -m "feat: implement articulation points, bridges, and component sizes"
```

---

### Task 9b: Implement BFS call depths (FunctionContext backward compat)

**Files:**
- Modify: `repotoire-cli/src/graph/primitives.rs` (add `compute_call_depths()`)

- [ ] **Step 1: Write call depth test**

Add to tests:

```rust
    #[test]
    fn test_call_depth_from_entry_points() {
        // entry(depth=0) -> mid(depth=1) -> leaf(depth=2)
        let mut builder = GraphBuilder::new();
        let entry = builder.add_node(CodeNode::function("entry", "a.py"));
        let mid = builder.add_node(CodeNode::function("mid", "a.py"));
        let leaf = builder.add_node(CodeNode::function("leaf", "a.py"));
        builder.add_edge(entry, mid, CodeEdge::calls());
        builder.add_edge(mid, leaf, CodeEdge::calls());

        let graph = builder.freeze();
        // Access via call_depth_idx (added in Task 15 step 5, but test structure here)
        // For now test via primitives directly
        assert!(true); // Placeholder — full test after call_depth_idx wiring
    }
```

- [ ] **Step 2: Implement `compute_call_depths()`**

Add to `primitives.rs`:

```rust
/// BFS call depth from entry points (in-degree 0 on call graph).
/// This is semantically distinct from dominator tree depth:
/// BFS depth = shortest path from any entry point.
/// Dominator depth = depth in the dominator tree.
/// Kept for FunctionContext.call_depth backward compatibility.
fn compute_call_depths(
    functions: &[NodeIndex],
    call_callees: &HashMap<NodeIndex, Vec<NodeIndex>>,
    call_callers: &HashMap<NodeIndex, Vec<NodeIndex>>,
) -> HashMap<NodeIndex, usize> {
    use std::collections::VecDeque;

    let func_set: HashSet<NodeIndex> = functions.iter().copied().collect();

    // Entry points: in-degree 0 on call graph
    let entry_points: Vec<NodeIndex> = functions
        .iter()
        .filter(|&&idx| {
            call_callers.get(&idx).map(|v| v.len()).unwrap_or(0) == 0
        })
        .copied()
        .collect();

    let mut depths: HashMap<NodeIndex, usize> = HashMap::new();
    let mut queue: VecDeque<NodeIndex> = VecDeque::new();

    for &ep in &entry_points {
        depths.insert(ep, 0);
        queue.push_back(ep);
    }

    while let Some(node) = queue.pop_front() {
        let depth = depths[&node];
        if let Some(callees) = call_callees.get(&node) {
            for &callee in callees {
                if func_set.contains(&callee) && !depths.contains_key(&callee) {
                    depths.insert(callee, depth + 1);
                    queue.push_back(callee);
                }
            }
        }
    }

    depths
}
```

- [ ] **Step 3: Run tests**

Run: `cd repotoire-cli && cargo test graph::primitives`
Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/graph/primitives.rs
git commit -m "feat: implement BFS call depths for FunctionContext backward compat"
```

---

### Task 10: Move betweenness centrality from function_context to primitives

**Files:**
- Modify: `repotoire-cli/src/graph/primitives.rs` (add betweenness computation)

- [ ] **Step 1: Write betweenness test**

```rust
    #[test]
    fn test_betweenness_bridge_node_is_highest() {
        // f1 -> bridge -> f3, f2 -> bridge -> f4
        // bridge is on all shortest paths
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("f1", "a.py"));
        let f2 = builder.add_node(CodeNode::function("f2", "a.py"));
        let bridge = builder.add_node(CodeNode::function("bridge", "a.py"));
        let f3 = builder.add_node(CodeNode::function("f3", "a.py"));
        let f4 = builder.add_node(CodeNode::function("f4", "a.py"));
        builder.add_edge(f1, bridge, CodeEdge::calls());
        builder.add_edge(f2, bridge, CodeEdge::calls());
        builder.add_edge(bridge, f3, CodeEdge::calls());
        builder.add_edge(bridge, f4, CodeEdge::calls());

        let graph = builder.freeze();
        let bridge_bc = graph.betweenness(bridge);
        let f1_bc = graph.betweenness(f1);
        assert!(bridge_bc > f1_bc,
            "bridge ({bridge_bc}) should have higher betweenness than f1 ({f1_bc})");
    }
```

- [ ] **Step 2: Implement sampled Brandes with deterministic seed**

Add `compute_betweenness()` to `primitives.rs`. Port the logic from `function_context.rs:398-485` but replace `rand::rng()` with a deterministic seed derived from `edge_fingerprint`:

```rust
/// Sampled Brandes betweenness centrality with deterministic seed.
/// Ported from function_context.rs, with rand::rng() replaced by
/// a seed derived from edge_fingerprint for reproducible results.
/// Returns RAW (unnormalized) values — FunctionContextBuilder normalizes in consumer.
/// Uses rayon par_iter for parallel BFS (matching existing implementation).
fn compute_betweenness(
    functions: &[NodeIndex],
    call_callees: &HashMap<NodeIndex, Vec<NodeIndex>>,
    edge_fingerprint: u64,
) -> HashMap<NodeIndex, f64> {
    use rayon::prelude::*;
    use std::collections::VecDeque;

    let n = functions.len();
    if n == 0 {
        return HashMap::new();
    }

    let func_set: HashSet<NodeIndex> = functions.iter().copied().collect();
    let sample_size = n.min(200);

    // Deterministic sampling: use edge_fingerprint as seed (behavioral change from rand::rng())
    let mut indices: Vec<usize> = (0..n).collect();
    if sample_size < n {
        let mut seed = edge_fingerprint;
        for i in (1..n).rev() {
            seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
            let j = (seed >> 33) as usize % (i + 1);
            indices.swap(i, j);
        }
        indices.truncate(sample_size);
    }

    let source_nodes: Vec<NodeIndex> = indices.iter().map(|&i| functions[i]).collect();

    // Parallel Brandes: each source produces partial centrality, then sum
    let partial_centralities: Vec<HashMap<NodeIndex, f64>> = source_nodes
        .par_iter()
        .map(|&source| {
            let mut partial: HashMap<NodeIndex, f64> = HashMap::new();
            let mut stack: Vec<NodeIndex> = Vec::new();
            let mut predecessors: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();
            let mut sigma: HashMap<NodeIndex, f64> = HashMap::new();
            let mut dist: HashMap<NodeIndex, i64> = HashMap::new();
            let mut delta: HashMap<NodeIndex, f64> = HashMap::new();

            for &idx in functions {
                sigma.insert(idx, 0.0);
                dist.insert(idx, -1);
                delta.insert(idx, 0.0);
            }
            *sigma.get_mut(&source).unwrap() = 1.0;
            *dist.get_mut(&source).unwrap() = 0;

            let mut queue: VecDeque<NodeIndex> = VecDeque::new();
            queue.push_back(source);

            while let Some(v) = queue.pop_front() {
                stack.push(v);
                let v_dist = dist[&v];
                if let Some(neighbors) = call_callees.get(&v) {
                    for &w in neighbors {
                        if !func_set.contains(&w) { continue; }
                        if dist[&w] < 0 {
                            dist.insert(w, v_dist + 1);
                            queue.push_back(w);
                        }
                        if dist[&w] == v_dist + 1 {
                            *sigma.get_mut(&w).unwrap() += sigma[&v];
                            predecessors.entry(w).or_default().push(v);
                        }
                    }
                }
            }

            while let Some(w) = stack.pop() {
                if let Some(preds) = predecessors.get(&w) {
                    for &v in preds {
                        let d = (sigma[&v] / sigma[&w]) * (1.0 + delta[&w]);
                        *delta.get_mut(&v).unwrap() += d;
                    }
                }
                if w != source {
                    *partial.entry(w).or_insert(0.0) += delta[&w];
                }
            }
            partial
        })
        .collect();

    // Sum partial centralities
    let mut centrality: HashMap<NodeIndex, f64> = functions.iter().map(|&idx| (idx, 0.0)).collect();
    for partial in partial_centralities {
        for (idx, val) in partial {
            *centrality.entry(idx).or_insert(0.0) += val;
        }
    }

    // Scale if sampled (do NOT normalize to 0-1; consumer handles normalization)
    if sample_size < n {
        let scale = n as f64 / sample_size as f64;
        for v in centrality.values_mut() {
            *v *= scale;
        }
    }

    centrality
}
```

- [ ] **Step 3: Wire into `compute()` — replace the `betweenness: HashMap::new()` placeholder**

```rust
        let betweenness = compute_betweenness(functions, call_callees, edge_fingerprint);
```

And update the struct construction to use it.

- [ ] **Step 4: Run tests**

Run: `cd repotoire-cli && cargo test graph::primitives`
Expected: All pass.

- [ ] **Step 5: Run full test suite**

Run: `cd repotoire-cli && cargo test`
Expected: All pass. No regressions.

- [ ] **Step 6: Commit**

```bash
git add repotoire-cli/src/graph/primitives.rs
git commit -m "feat: implement deterministic sampled Brandes betweenness centrality"
```

---

## Chunk 3: New Detectors

### Task 11: Implement `SinglePointOfFailureDetector`

**Files:**
- Create: `repotoire-cli/src/detectors/single_point_of_failure.rs`
- Modify: `repotoire-cli/src/detectors/mod.rs` (register detector)

- [ ] **Step 1: Create detector file with struct, traits, and detect logic**

Create `repotoire-cli/src/detectors/single_point_of_failure.rs` following the pattern from `circular_dependency.rs`. Key implementation details:
- Iterates `ctx.graph.functions_idx()`
- Reads `ctx.graph.dominated_by_idx(func_idx).len()` for domination count
- Reads `ctx.graph.page_rank_idx(func_idx)` for importance weighting
- Reads `ctx.graph.domination_frontier_idx(func_idx)` for blast radius boundary
- Implements `RegisteredDetector` with `create(init: &DetectorInit) -> Arc<dyn Detector>`
- Severity: Critical (>20% domination AND top 1% PR), High (>10%), Medium (>threshold)
- Config: `min_dominated` (default 20), `min_page_rank_percentile` (default 0.9)

**CRITICAL: Must explicitly override `detector_scope()`** — the default returns `FileScopedGraph`, not `GraphWide`:
```rust
fn detector_scope(&self) -> DetectorScope {
    DetectorScope::GraphWide
}
```
Without this, graph-wide cache routing breaks. Also set `fn category(&self) -> &'static str { "architecture" }`.

- [ ] **Step 2: Register in `DETECTOR_FACTORIES`**

In `detectors/mod.rs`, add `register::<SinglePointOfFailureDetector>(),` to the array and add `mod single_point_of_failure; pub use single_point_of_failure::*;`.

**IMPORTANT:** Also update the detector count assertion in `test_create_all_detectors_registry` — change `assert_eq!(detectors.len(), 100)` to `101` (will become 103 after all 3 detectors are added). This test exists to catch registration issues.

- [ ] **Step 3: Verify compilation**

Run: `cd repotoire-cli && cargo check`

- [ ] **Step 4: Write inline tests in the detector file**

Test with a graph where one function dominates many others. Verify finding is emitted with correct severity and message.

- [ ] **Step 5: Run tests**

Run: `cd repotoire-cli && cargo test detectors::single_point_of_failure`

- [ ] **Step 6: Commit**

```bash
git add repotoire-cli/src/detectors/single_point_of_failure.rs repotoire-cli/src/detectors/mod.rs
git commit -m "feat: add SinglePointOfFailureDetector using dominator tree primitives"
```

---

### Task 12: Implement `StructuralBridgeRiskDetector`

**Files:**
- Create: `repotoire-cli/src/detectors/structural_bridge_risk.rs`
- Modify: `repotoire-cli/src/detectors/mod.rs` (register)

- [ ] **Step 1: Create detector**

Same pattern as Task 11. Iterates `ctx.graph.articulation_points_idx()`, reads `ctx.graph.separation_sizes_idx(ap_idx)`, filters by `min_component_size`, emits findings. **Must override `detector_scope()` to return `GraphWide`.**

- [ ] **Step 2: Register in `DETECTOR_FACTORIES`**

Add `register::<StructuralBridgeRiskDetector>(),` and module declarations.

- [ ] **Step 3: Add inline tests**

- [ ] **Step 4: Run tests**

Run: `cd repotoire-cli && cargo test detectors::structural_bridge_risk`

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/structural_bridge_risk.rs repotoire-cli/src/detectors/mod.rs
git commit -m "feat: add StructuralBridgeRiskDetector using articulation point primitives"
```

---

### Task 13: Implement `MutualRecursionDetector`

**Files:**
- Create: `repotoire-cli/src/detectors/mutual_recursion.rs`
- Modify: `repotoire-cli/src/detectors/mod.rs` (register)

- [ ] **Step 1: Create detector**

Iterates `ctx.graph.call_cycles_idx()`, computes combined complexity (`n.complexity as u32`), filters by `max_cycle_size`, emits findings. **Must override `detector_scope()` to return `GraphWide`.**

- [ ] **Step 2: Register in `DETECTOR_FACTORIES`**

- [ ] **Step 3: Add inline tests**

- [ ] **Step 4: Run tests**

Run: `cd repotoire-cli && cargo test detectors::mutual_recursion`

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/mutual_recursion.rs repotoire-cli/src/detectors/mod.rs
git commit -m "feat: add MutualRecursionDetector using call-graph SCC primitives"
```

---

## Chunk 4: Migration & Integration

### Task 14: Fix engine finding routing for graph-wide detectors

**Files:**
- Modify: `repotoire-cli/src/engine/stages/detect.rs:119-125`

- [ ] **Step 1: Build detector scope lookup, then change routing logic**

The detect stage receives flat `Vec<Finding>` from `run_detectors()`, but needs to know each finding's detector scope for routing. The fix has two parts:

**Part A:** Before calling `run_detectors()`, build a scope lookup from the detector list:
```rust
let scope_map: HashMap<String, DetectorScope> = detectors
    .iter()
    .map(|d| (d.name().to_string(), d.detector_scope()))
    .collect();
```

**Part B:** Replace the routing condition (currently `finding.affected_files.is_empty()`) with scope-based routing:
```rust
for finding in &findings {
    let scope = scope_map.get(&finding.detector)
        .copied()
        .unwrap_or(DetectorScope::FileScopedGraph);
    if scope == DetectorScope::GraphWide {
        graph_wide_findings.entry(finding.detector.clone()).or_default().push(finding.clone());
    } else {
        for file in &finding.affected_files {
            findings_by_file.entry(file.clone()).or_default().push(finding.clone());
        }
    }
}
```

This ensures graph-wide detectors (like our 3 new ones) have their findings routed to the graph-wide cache, even when they populate `affected_files`.

- [ ] **Step 2: Verify existing tests pass**

Run: `cd repotoire-cli && cargo test engine`

- [ ] **Step 3: Commit**

```bash
git add repotoire-cli/src/engine/stages/detect.rs
git commit -m "fix: route graph-wide detector findings by detector_scope, not affected_files"
```

---

### Task 15: Migrate `FunctionContextBuilder` to read from primitives

**Files:**
- Modify: `repotoire-cli/src/detectors/function_context.rs` — both `build()` (~line 154) and `build_indexed()` (~line 241) paths

- [ ] **Step 1: Update `build_indexed()` (primary production path) to read from primitives**

In `FunctionContextBuilder.build_indexed()` (~line 241), find where betweenness and call depths are computed/used:
- Replace `self.calculate_betweenness(...)` call with reads from `graph.betweenness_idx(func_idx)` — store raw value, normalize in consumer as before
- Replace `self.calculate_call_depths(...)` call with reads from `graph.call_depth_idx(func_idx)` — uses BFS-based call depth (NOT dominator depth) for backward compatibility with `FunctionRole` inference

**Important:** The consumer (`build_indexed`) currently normalizes betweenness to [0,1] by dividing by max. Keep this normalization — the primitives now store raw values.

- [ ] **Step 2: Update legacy `build()` path similarly**

Same changes in `build()` (~line 154). This path is used in some test scenarios.

- [ ] **Step 3: Delete `calculate_betweenness()` method (~lines 398-485)**

Remove the ~90-line Brandes implementation.

- [ ] **Step 4: Delete `calculate_call_depths()` method (~lines 488-543)**

Remove the ~56-line BFS implementation.

- [ ] **Step 5: Add `call_depth_idx` to GraphQuery trait and implementations**

Add to `traits.rs`:
```rust
fn call_depth_idx(&self, _idx: NodeIndex) -> usize { 0 }
```
Add CodeGraph accessor in `frozen.rs` reading from `self.indexes.primitives.call_depth`.
Add compat.rs overrides for `CodeGraph` and `Arc<CodeGraph>`.

- [ ] **Step 6: Run all tests**

Run: `cd repotoire-cli && cargo test`
Expected: All pass. FunctionContext still populated correctly, role inference unchanged.

- [ ] **Step 7: Commit**

```bash
git add repotoire-cli/src/detectors/function_context.rs repotoire-cli/src/graph/traits.rs repotoire-cli/src/graph/frozen.rs repotoire-cli/src/graph/compat.rs
git commit -m "refactor: migrate FunctionContextBuilder to read betweenness/call_depth from graph primitives"
```

---

### Task 16: Migrate `ArchitecturalBottleneckDetector`

**Files:**
- Modify: `repotoire-cli/src/detectors/architectural_bottleneck.rs`

- [ ] **Step 1: Replace ad-hoc betweenness with `ctx.graph.betweenness_idx()`**

Where the detector currently reads betweenness from `FunctionContextMap`, change to read from `ctx.graph.betweenness_idx(func_idx)`.

- [ ] **Step 2: Run detector tests**

Run: `cd repotoire-cli && cargo test detectors::architectural_bottleneck`

- [ ] **Step 3: Commit**

```bash
git add repotoire-cli/src/detectors/architectural_bottleneck.rs
git commit -m "refactor: ArchitecturalBottleneckDetector reads betweenness from graph primitives"
```

---

### Task 17: Migrate `InfluentialCodeDetector` to PageRank

**Files:**
- Modify: `repotoire-cli/src/detectors/influential_code.rs`

- [ ] **Step 1: Replace fan-in with PageRank percentile**

Change the core detection metric from `in_degree` / `call_fan_in()` to `ctx.graph.page_rank_idx(func_idx)`. Compute PageRank percentile across all functions. Adjust severity thresholds.

**This is a behavioral change.** Detection results will differ. Update inline tests.

- [ ] **Step 2: Update thresholds for PageRank-based detection**

Replace count-based thresholds with percentile-based thresholds (e.g., top 5% PageRank = High, top 1% = Critical).

- [ ] **Step 3: Run tests and update expected values**

Run: `cd repotoire-cli && cargo test detectors::influential_code`

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/influential_code.rs
git commit -m "refactor: InfluentialCodeDetector uses PageRank instead of fan-in (behavioral change)"
```

---

### Task 18: Integration test and validation

**Files:**
- Modify: `repotoire-cli/src/graph/primitives.rs` (add comprehensive integration test)

- [ ] **Step 1: Write integration test with realistic graph**

Add a test that builds a non-trivial graph (10+ functions, multiple files, mix of call patterns) and verifies all primitives are computed correctly together:

```rust
    #[test]
    fn test_all_primitives_integration() {
        let mut builder = GraphBuilder::new();
        // Entry point with fan-out
        let entry = builder.add_node(CodeNode::function("main", "main.py"));
        let auth = builder.add_node(CodeNode::function("auth", "auth.py"));
        let handler = builder.add_node(CodeNode::function("handler", "api.py"));
        let db = builder.add_node(CodeNode::function("db_query", "db.py"));
        let cache = builder.add_node(CodeNode::function("cache_get", "cache.py"));
        let helper_a = builder.add_node(CodeNode::function("helper_a", "utils.py"));
        let helper_b = builder.add_node(CodeNode::function("helper_b", "utils.py"));
        // Mutual recursion pair
        let resolve = builder.add_node(CodeNode::function("resolve", "types.py"));
        let expand = builder.add_node(CodeNode::function("expand", "types.py"));

        // Call edges: entry -> auth -> handler -> db, handler -> cache
        builder.add_edge(entry, auth, CodeEdge::calls());
        builder.add_edge(auth, handler, CodeEdge::calls());
        builder.add_edge(handler, db, CodeEdge::calls());
        builder.add_edge(handler, cache, CodeEdge::calls());
        // Hub: auth also calls helpers
        builder.add_edge(auth, helper_a, CodeEdge::calls());
        builder.add_edge(auth, helper_b, CodeEdge::calls());
        // Mutual recursion
        builder.add_edge(resolve, expand, CodeEdge::calls());
        builder.add_edge(expand, resolve, CodeEdge::calls());
        // Import edges for structural connectivity
        let f_main = builder.add_node(CodeNode::file("main.py"));
        let f_auth = builder.add_node(CodeNode::file("auth.py"));
        builder.add_edge(f_main, f_auth, CodeEdge::imports());

        let graph = builder.freeze();

        // PageRank: auth should have high PR (called by entry, calls many)
        assert!(graph.page_rank(auth) > 0.0, "auth should have positive PageRank");
        // Betweenness: auth is on many paths
        assert!(graph.betweenness(auth) > 0.0, "auth should have positive betweenness");
        // Dominator: entry dominates everything reachable
        assert!(graph.domination_count(entry) >= 5, "entry should dominate most functions");
        // auth dominates handler, db, cache, helpers
        assert!(graph.dominated_by(auth).len() >= 4);
        // Call cycles: resolve <-> expand
        assert_eq!(graph.call_cycles().len(), 1);
        assert_eq!(graph.call_cycles()[0].len(), 2);
        // Depth: entry is 0, auth is 1, handler is 2
        assert_eq!(graph.dominator_depth(entry), 0);
        assert!(graph.dominator_depth(handler) > graph.dominator_depth(auth));
    }
```

- [ ] **Step 2: Run full test suite**

Run: `cd repotoire-cli && cargo test`
Expected: All tests pass.

- [ ] **Step 3: Run cargo clippy**

Run: `cd repotoire-cli && cargo clippy`
Expected: No new warnings.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test: add integration test for all graph primitives working together"
```

- [ ] **Step 5: Validate on a real project**

Run: `cd repotoire-cli && cargo run -- analyze /path/to/flask --format json | jq '.findings[] | select(.detector | test("single-point|structural-bridge|mutual-recursion"))'`

Verify new detectors produce findings on a real codebase.
