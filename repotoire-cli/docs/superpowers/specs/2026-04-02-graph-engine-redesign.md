# Graph Engine Redesign: CSR + Arena + Hand-Rolled Interner

## Goal

Replace petgraph's `StableGraph` and the lasso string interner with purpose-built data structures optimized for repotoire's exact access patterns. The frozen graph becomes three `Vec`s — nodes, offsets, neighbors — with O(1) slicing for all adjacency queries. Zero external graph/interner dependencies.

## Motivation

- **petgraph** (12 transitive deps including indexmap, fixedbitset) provides a general-purpose `StableGraph` but repotoire only uses: `add_node`, `add_edge`, `node_weight`, `node_indices`, `edges_directed`, `tarjan_scc`, `dominators::simple_fast`. Post-freeze, the graph is immutable — a CSR layout is a strict upgrade.
- **lasso** (22 transitive deps) provides `ThreadedRodeo` for string interning. Repotoire uses: `get_or_intern`, `resolve`, `get`, `len`. A hand-rolled interner backed by a chunk-based arena + `HashMap` covers this in ~120 lines.
- **GraphIndexes** currently stores 12 `HashMap<NodeIndex, Vec<NodeIndex>>` adjacency maps + 4 spatial maps + 3 bulk edge lists. These are all precomputed views of the same edge set. A unified CSR replaces all of them.
- **Cache locality**: petgraph's adjacency list scatters neighbor lists across the heap. CSR stores all neighbors contiguously — measurably faster for iterative algorithms (PageRank, betweenness, Louvain).

### Dep reduction impact

- Removes: petgraph (+ fixedbitset, indexmap, hashbrown, equivalent), lasso (+ hashbrown, memchr, etc.)
- Net: ~25 unique transitive deps eliminated

### Research backing

- CSR + CSC is the standard for frozen directed graphs (GBBS/Ligra, neo4j-labs/graph, GAP Benchmark Suite)
- BFS vertex reordering at freeze time gives 15-20% traversal speedup (ACM 2025, IPDPS 2016)
- Struct-of-arrays for algorithm scratch data improves cache utilization (Beamer 2016)

## Architecture

### Core insight: unified edge storage

An edge `(A → B, Calls)` is one fact visible from two perspectives:
- From A's side: outgoing Call to B
- From B's side: incoming Call from A

Store each edge under **both endpoints** in a single sorted array. The "direction" is just which offset slot you read.

### Data layout

```
STRIDE = 11 (5 bidirectional kinds × 2 + 1 unidirectional)

offsets: Vec<u32>    // length = node_count × STRIDE + 1
neighbors: Vec<u32>  // length = 2 × edge_count (each edge stored under both endpoints)
nodes: Vec<CodeNode> // length = node_count
```

Slot numbering per node (11 slots — ModifiedIn is one-directional):
```
slot 0:  Calls-Out
slot 1:  Calls-In
slot 2:  Imports-Out
slot 3:  Imports-In
slot 4:  Contains-Out
slot 5:  Contains-In
slot 6:  Inherits-Out
slot 7:  Inherits-In
slot 8:  Uses-Out
slot 9:  Uses-In
slot 10: ModifiedIn-Out  (no In slot — only entity→commit direction is queried)
```

**Note on STRIDE**: `STRIDE = 11`. Empty slots get consecutive equal offsets (i.e., `offsets[slot] == offsets[slot+1]` means zero neighbors). This is standard CSR behavior.

**Non-trait adjacency methods**: The current `CodeGraph` also exposes `contains_children()`, `contains_parent()`, `uses_targets()`, `uses_sources()`, and `modified_in()` as inherent methods (not via `GraphQuery`). These are all covered by the CSR slots above and become simple slicing operations, same as the trait methods.

Query pattern:
```rust
fn callees(&self, v: u32) -> &[u32] {
    let slot = v as usize * STRIDE + CALLS_OUT;
    &self.neighbors[self.offsets[slot] as usize..self.offsets[slot + 1] as usize]
}

fn callers(&self, v: u32) -> &[u32] {
    let slot = v as usize * STRIDE + CALLS_IN;
    &self.neighbors[self.offsets[slot] as usize..self.offsets[slot + 1] as usize]
}
```

One multiply, one add, two array reads, one slice. No HashMap, no Vec-of-Vec, no filtering.

### Memory budget (50k nodes, 200k edges)

| Component | Current (petgraph + GraphIndexes) | New (CSR) |
|-----------|----------------------------------|-----------|
| Node storage | 50k × ~48 bytes = 2.4MB | Same |
| Edge storage | StableGraph internal: ~32 bytes/edge = 6.4MB | neighbors: 400k × 4 = 1.6MB |
| Adjacency indexes | 12 HashMaps with Vec values: ~8MB estimated | offsets: 50k × 11 × 4 = 2.2MB |
| **Total graph overhead** | **~17MB** | **~4MB** |

### NodeIndex type

Replace `petgraph::stable_graph::NodeIndex` with:

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
#[repr(transparent)]
pub struct NodeIndex(u32);

impl NodeIndex {
    pub const INVALID: NodeIndex = NodeIndex(u32::MAX);
    pub fn new(idx: u32) -> Self { Self(idx) }
    pub fn index(self) -> usize { self.0 as usize }
}
```

This is a drop-in replacement — same size, same Copy semantics. The `_idx` suffix methods on `GraphQuery` keep their signatures, just with our `NodeIndex` instead of petgraph's.

## Two-phase architecture

### Phase 1: Mutable build (GraphBuilder)

```rust
pub struct GraphBuilder {
    nodes: Vec<CodeNode>,
    node_index: HashMap<StrKey, NodeIndex>,  // qualified_name → index
    edges: Vec<(NodeIndex, NodeIndex, EdgeKind)>,
    edge_set: HashSet<(NodeIndex, NodeIndex, EdgeKind)>,  // dedup
    extra_props: HashMap<StrKey, ExtraProps>,
}
```

- `add_node(node)` → push to `nodes` Vec, insert into `node_index` HashMap. If duplicate qualified_name, overwrite in place.
- `add_edge(from, to, kind)` → check `edge_set`, push to `edges` Vec.
- No petgraph. Just Vecs and HashMaps.

### Phase 2: Freeze (GraphBuilder → CodeGraph)

The freeze step:

1. **BFS vertex reordering** (optional, enabled by default):
   - Find highest-degree node as BFS seed. **Tie-breaking**: among nodes with equal degree, pick the one with the lexicographically smallest qualified name (deterministic across runs).
   - BFS traversal produces a permutation `old_idx → new_idx`
   - Renumber all node indices in edges, node_index map, extra_props
   - Reorder `nodes` Vec to match new numbering
   - Neighbor lists within each CSR slot are sorted by target NodeIndex after reordering (preserves determinism for detectors that depend on neighbor ordering)
   - Result: nodes accessed together during traversals are contiguous in memory

2. **Build CSR**:
   - Expand each edge `(A, B, kind)` into two entries:
     - `(A, kind, Out, B)` — stored under A
     - `(B, kind, In, A)` — stored under B
   - Sort by `(node, slot)` where `slot = kind * 2 + direction`
   - Scan sorted entries to build `offsets` array
   - Extract neighbor column into `neighbors` array

3. **Build auxiliary indexes** (from the same sorted edge data):
   - `functions: Vec<NodeIndex>` — nodes where `kind == Function`
   - `classes: Vec<NodeIndex>` — nodes where `kind == Class`
   - `files: Vec<NodeIndex>` — nodes where `kind == File`
   - `functions_by_file: HashMap<StrKey, Vec<NodeIndex>>` — grouped by file_path
   - `classes_by_file: HashMap<StrKey, Vec<NodeIndex>>`
   - `function_spatial: HashMap<StrKey, Vec<(u32, u32, NodeIndex)>>` — sorted by line_start for binary search
   - `import_cycles: Vec<Vec<NodeIndex>>` — from hand-rolled Tarjan SCC
   - `edge_fingerprint: u64` — SipHash of sorted edge triples
   - `all_call_edges: Vec<(NodeIndex, NodeIndex)>` — flat list for algorithms
   - `all_import_edges: Vec<(NodeIndex, NodeIndex)>`
   - `all_inheritance_edges: Vec<(NodeIndex, NodeIndex)>`

4. **Compute GraphPrimitives** (unchanged — already hand-rolled):
   - Phase A: dominator trees, articulation points, PageRank, betweenness, SCCs, call depths
   - Phase B: weighted overlay, weighted PageRank, weighted betweenness, Louvain communities

### Frozen CodeGraph struct

```rust
pub struct CodeGraph {
    // Core CSR storage
    nodes: Vec<CodeNode>,
    offsets: Vec<u32>,      // node_count × 11 + 1
    neighbors: Vec<u32>,    // 2 × edge_count

    // Node lookup
    node_index: HashMap<StrKey, NodeIndex>,

    // Cold storage
    extra_props: HashMap<StrKey, ExtraProps>,

    // Auxiliary indexes
    indexes: GraphIndexes,
}
```

Where `GraphIndexes` is simplified to only the things that AREN'T in the CSR:

```rust
pub struct GraphIndexes {
    // Node-kind indexes
    functions: Vec<NodeIndex>,
    classes: Vec<NodeIndex>,
    files: Vec<NodeIndex>,

    // Spatial indexes
    functions_by_file: HashMap<StrKey, Vec<NodeIndex>>,
    classes_by_file: HashMap<StrKey, Vec<NodeIndex>>,
    all_nodes_by_file: HashMap<StrKey, Vec<NodeIndex>>,
    function_spatial: HashMap<StrKey, Vec<(u32, u32, NodeIndex)>>,

    // Bulk edge lists (for algorithms that iterate all edges of a kind)
    all_call_edges: Vec<(NodeIndex, NodeIndex)>,
    all_import_edges: Vec<(NodeIndex, NodeIndex)>,
    all_inheritance_edges: Vec<(NodeIndex, NodeIndex)>,

    // Pre-computed analyses
    import_cycles: Vec<Vec<NodeIndex>>,
    edge_fingerprint: u64,
}
```

The 12 per-kind adjacency HashMaps are gone — replaced by the CSR.

## Edge flags

The current `CodeEdge` has a `flags: u8` field (bit 0 = `is_type_only`). This flag is actively used in `compute_import_cycles()` to filter out type-only imports before running Tarjan SCC.

The CSR stores only `(NodeIndex, NodeIndex, EdgeKind)` — no flags. To preserve this:
- Store a parallel `edge_flags: Vec<u8>` in `CodeGraph`, indexed the same as `neighbors`.
- Alternatively, pre-filter type-only import edges during CSR construction so they are excluded from the Imports slots. Since `is_type_only` is the only flag currently used at query time, and it's only used for import cycle detection, pre-filtering during freeze is cleaner.

**Decision**: Pre-filter during freeze. Import edges with `is_type_only` are excluded from the `Imports-Out`/`Imports-In` slots but included in a separate `all_import_edges_with_type_only` list in `GraphIndexes` if any consumer needs the full set.

## Phase B weighted overlay

Phase B (`phase_b.rs`) constructs a dynamic weighted overlay graph at analysis time using co-change data. This currently uses `StableGraph<NodeIndex, f32>`. After removing petgraph, this needs a lightweight mutable adjacency list:

```rust
struct WeightedOverlay {
    adj: Vec<Vec<(u32, f32)>>,  // adj[node] = [(neighbor, weight), ...]
}
```

This is simple, fast to build, and sufficient for Phase B's Dijkstra-based betweenness and Louvain community detection. No external deps needed.

## Hand-rolled Tarjan SCC

Currently `petgraph::algo::tarjan_scc` is used in one place (GraphIndexes::build). Replace with a ~60-line hand-rolled implementation:

```rust
fn tarjan_scc(node_count: usize, successors: impl Fn(u32) -> &[u32]) -> Vec<Vec<u32>> {
    // Standard Tarjan with index/lowlink/on_stack arrays
    // struct-of-arrays layout for cache efficiency
}
```

## Hand-rolled dominators

Currently `petgraph::algo::dominators::simple_fast` is used in phase_a.rs. Replace with a ~80-line hand-rolled implementation of the Lengauer-Tarjan dominator tree algorithm operating directly on the CSR.

## Hand-rolled string interner (replaces lasso)

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[repr(transparent)]
pub struct StrKey(u32);

impl StrKey {
    pub fn as_u32(self) -> u32 { self.0 }
}

pub struct StringInterner {
    inner: RwLock<InternerInner>,
}

struct InternerInner {
    /// Append-only chunk arena. Each chunk is a `String` that is never
    /// reallocated once created. This guarantees that `&str` references
    /// into existing chunks remain valid even when new chunks are added.
    chunks: Vec<String>,
    /// (chunk_index, start, len) for each interned string.
    spans: Vec<(u16, u32, u32)>,
    /// String hash → StrKey candidates for O(1) dedup.
    map: HashMap<u64, Vec<StrKey>>,
    /// Current chunk capacity target (grows as needed).
    chunk_capacity: usize,
}
```

**Soundness note**: A single `String` arena is unsound because `push_str` can reallocate, invalidating all outstanding `&str` references. The chunk-based design avoids this: each chunk is a fixed `String` that never grows after creation. When a chunk fills, a new chunk is allocated. `resolve()` returns a `&str` pointing into a chunk that will never move. This matches the approach used by `lasso::ThreadedRodeo` internally.

**Thread safety**: `RwLock<InternerInner>`. `intern()` takes a write lock. `resolve()` takes a read lock — the `&str` returned borrows from the `StringInterner` (not from the lock guard), which is safe because chunks are append-only and never reallocated.

API surface (unchanged from current):
- `intern(&self, s: &str) -> StrKey`
- `resolve(&self, key: StrKey) -> &str`
- `get(&self, s: &str) -> Option<StrKey>`
- `len() -> usize`
- `empty_key() -> StrKey`

## GraphQuery trait changes

The trait signature stays the same except `NodeIndex` changes from `petgraph::stable_graph::NodeIndex` to our own `NodeIndex(u32)`. All `_idx` methods keep their signatures.

The `CodeGraph` implementation changes from HashMap lookups to CSR slicing:

```rust
// Before (HashMap lookup, potential cache miss):
fn callers_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
    self.indexes.call_callers.get(&idx).map_or(&[], |v| v)
}

// After (CSR slice, contiguous memory):
fn callers_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
    let slot = idx.index() * STRIDE + CALLS_IN;
    let start = self.offsets[slot] as usize;
    let end = self.offsets[slot + 1] as usize;
    // SAFETY: neighbors stores u32 values that are valid NodeIndex,
    // and NodeIndex is #[repr(transparent)] over u32
    unsafe { std::mem::transmute(&self.neighbors[start..end]) }
}
```

Note: the `transmute` from `&[u32]` to `&[NodeIndex]` is safe because `NodeIndex` is `#[repr(transparent)]` over `u32`. Alternatively, store `neighbors` as `Vec<NodeIndex>` directly to avoid the transmute.

## GraphPrimitives adaptation

The primitives (phase_a.rs, phase_b.rs) currently operate on `&StableGraph<CodeNode, CodeEdge>`. They need to be adapted to operate on CSR arrays instead. The change is mechanical:

```rust
// Before (petgraph):
for edge in graph.edges_directed(node, Direction::Outgoing) {
    let target = edge.target();
    ...
}

// After (CSR):
for &target in code_graph.callees_idx(node) {
    ...
}
```

Most primitives already use `GraphQuery` trait methods rather than raw petgraph access, so the change propagates naturally.

## Persistence (serialization)

Current: `graph/persistence.rs` serializes the `StableGraph` via bitcode + custom Spur→String table.

New: Serialize the CSR arrays directly. The format becomes:
- String table: `Vec<String>` (all interned strings, indexed by StrKey.0)
- Node array: `Vec<SerializedCodeNode>` (StrKey fields stored as u32 indices)
- Edge triples: `Vec<(u32, u32, u8)>` (source, target, edge_kind)

On load: re-intern strings into the global interner (producing new StrKeys), rebuild nodes with new keys, run freeze() to rebuild CSR. This is simpler than the current approach which deserializes a full StableGraph.

## Migration strategy

The migration is bottom-up:

1. **Hand-roll StringInterner** (replace lasso) — 2 files, self-contained
2. **Hand-roll NodeIndex** — newtype u32, update all imports
3. **Hand-roll Tarjan SCC + dominators** — replace petgraph::algo (needed by steps 5-6)
4. **Rewrite GraphBuilder** — Vec-based instead of StableGraph
5. **Build CSR freeze** — the core new code
6. **Rewrite CodeGraph** — CSR-backed queries (trait methods + inherent methods)
7. **Simplify GraphIndexes** — remove the 12 adjacency HashMaps (depends on steps 3, 5, 6)
8. **Add WeightedOverlay** — lightweight mutable adjacency list for Phase B
9. **Adapt GraphPrimitives** — use CSR accessors + WeightedOverlay
10. **Adapt persistence** — serialize CSR arrays
11. **Update external consumers** — detectors, scoring, CLI (NodeIndex import path change)
12. **Remove petgraph + lasso from Cargo.toml**
13. **BFS vertex reordering** — optimization pass in freeze()

**GraphBuilder QuerySnapshot**: The builder's lazy `QuerySnapshot` (used for test code and fallback trait access) will continue to use HashMap-based indexes during the mutable build phase. When `snapshot()` is called, it runs `freeze()` to build a CSR-backed `CodeGraph` snapshot — same mechanism, just using the new CSR instead of petgraph internals.

## Testing strategy

- All existing 1785 tests must pass after migration
- GraphQuery trait tests exercise the full API surface
- Primitives tests (PageRank, SCC, dominator, betweenness) validate algorithm correctness
- New unit tests for CSR construction, BFS reordering, interner
- Property test: for any graph built with the old code, the new code produces identical query results

## Files affected

### New files
- `src/graph/csr.rs` — CSR data structure + freeze logic (~300 lines)
- `src/graph/node_index.rs` — NodeIndex newtype (~30 lines)
- `src/graph/algo.rs` — tarjan_scc + dominators (~150 lines)
- `src/graph/overlay.rs` — WeightedOverlay for Phase B (~50 lines)

### Modified files
- `src/graph/interner.rs` — replace lasso with hand-rolled (~120 lines)
- `src/graph/builder.rs` — Vec-based instead of StableGraph
- `src/graph/frozen.rs` — CSR-backed queries
- `src/graph/indexes.rs` — remove adjacency HashMaps
- `src/graph/traits.rs` — NodeIndex import path
- `src/graph/primitives/phase_a.rs` — use CSR accessors
- `src/graph/primitives/phase_b.rs` — use CSR accessors
- `src/graph/persistence.rs` — CSR serialization
- `src/graph/compat.rs` — NodeIndex type
- All files importing `petgraph::stable_graph::NodeIndex` (~13 files)

### Removed deps
- `petgraph` (Cargo.toml)
- `lasso` (Cargo.toml)

## Estimated size

~2,000 lines new/rewritten code. ~500 lines removed (petgraph boilerplate, lasso wrapper).
