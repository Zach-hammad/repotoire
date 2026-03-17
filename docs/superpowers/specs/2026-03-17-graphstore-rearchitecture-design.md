# GraphStore Rearchitecture

**Date:** 2026-03-17
**Status:** Design approved, pending implementation plan
**Scope:** Split the 1,736-line GraphStore monolith into a Builder/Frozen architecture with pre-built indexes, redesign the GraphQuery trait for index-based access, and eliminate CachedGraphQuery.

## Problem Statement

`GraphStore` (`graph/store/mod.rs`) is a 1,736-line struct with 13 fields mixing four concerns:

1. **Graph data** — petgraph `StableGraph` behind `RwLock`, `DashMap` node index, `Mutex` edge dedup
2. **Spatial indexes** — 4 `DashMap`s for file-scoped lookups, maintained incrementally during add_node/add_edge
3. **Caches** — `DashMap` metrics cache, `RwLock<Option<...>>` call maps cache
4. **Persistence** — redb database, bincode cache, extra properties

This causes:

### Performance: Lock contention during queries

The petgraph is behind `RwLock`. Every query (`get_callers`, `get_functions`, `get_callees`) acquires a read lock, scans edges/nodes, clones `CodeNode` structs into a `Vec`, and releases the lock. During parallel detection with 100 detectors on rayon threads, this creates read-lock contention on every graph access.

`CachedGraphQuery` (528 lines, 14 `OnceLock` fields) exists solely to compensate: it memoizes query results so the lock is only acquired once per query type per analysis run. The cache layer exists because the base queries are too slow to call repeatedly.

### Performance: O(E) and O(N) base queries

- `get_callers(qn)` — O(E) edge scan per call (iterate all incoming edges, filter by `EdgeKind::Calls`, clone matching nodes)
- `get_functions()` — O(N) node scan (iterate all nodes, filter by `NodeKind::Function`, clone)
- `build_call_maps_raw()` — O(N+E) to build adjacency lists from scratch, cached via `RwLock<Option<...>>`
- `get_calls()` — O(E) edge scan, materializes all call edges into `Vec<(StrKey, StrKey)>`

With pre-built adjacency indexes, all of these become O(1) lookups.

### Architecture: Write and read paths are entangled

`GraphStore` supports both mutation (`add_node`, `add_edge`, `remove_file_entities`) and querying (`get_callers`, `find_import_cycles`, `stats`). But the graph is **write-once, read-many**: built during the graph stage (stage 3), enriched with git data (stage 4), then read by every subsequent stage (detect, postprocess, score, MCP queries).

`RwLock` and `DashMap` pay the cost of concurrent write support during the read phase, when no writes occur.

### Architecture: CachedGraphQuery is a workaround, not a solution

`CachedGraphQuery` wraps `&dyn GraphQuery` with 14 `OnceLock` fields that memoize every expensive method. It's created per-analysis-run, builds reverse call maps lazily, and caches node collections as `Arc<[CodeNode]>`. This is ~528 lines of infrastructure that exists because the base `GraphStore` is too slow for repeated queries.

The proper fix: make the base graph fast enough that caching is unnecessary.

## Design

### Architecture: Builder/Frozen Split

Two types replace `GraphStore`:

```
Parse → GraphBuilder (mutable) → Git Enrich → freeze() → CodeGraph (immutable, indexed)
                                                              ↓
                                                     Detect, Postprocess, Score, MCP
```

**`GraphBuilder`** — mutable accumulator for the build phase:

```rust
/// Mutable graph builder. Used during parse, graph build, and git enrichment.
/// No locks — all methods take &mut self.
pub struct GraphBuilder {
    graph: StableGraph<CodeNode, CodeEdge>,
    node_index: HashMap<StrKey, NodeIndex>,
    edge_set: HashSet<(NodeIndex, NodeIndex, EdgeKind)>,
    extra_props: HashMap<StrKey, ExtraProps>,
}

impl GraphBuilder {
    pub fn new() -> Self;
    pub fn add_node(&mut self, node: CodeNode) -> NodeIndex;
    pub fn add_nodes_batch(&mut self, nodes: Vec<CodeNode>) -> Vec<NodeIndex>;
    pub fn add_edge(&mut self, from: NodeIndex, to: NodeIndex, edge: CodeEdge);
    pub fn add_edge_by_name(&mut self, from_qn: &str, to_qn: &str, edge: CodeEdge) -> bool;
    pub fn add_edges_batch(&mut self, edges: Vec<(String, String, CodeEdge)>) -> usize;
    pub fn get_node_index(&self, qn: &str) -> Option<NodeIndex>;
    pub fn update_node_property(&mut self, qn: &str, key: &str, value: Value) -> bool;
    pub fn update_node_properties(&mut self, qn: &str, props: &[(&str, Value)]) -> bool;
    pub fn set_extra_props(&mut self, qn: StrKey, props: ExtraProps);
    pub fn remove_file_entities(&mut self, files: &[PathBuf]);
    pub fn node_count(&self) -> usize;
    pub fn edge_count(&self) -> usize;

    // ── Read methods (needed by git enrichment before freeze) ──

    /// Get all function nodes (O(N) scan — acceptable during build phase).
    pub fn get_functions(&self) -> Vec<CodeNode>;

    /// Get all class nodes (O(N) scan).
    pub fn get_classes(&self) -> Vec<CodeNode>;

    /// Get node by qualified name.
    pub fn get_node(&self, qn: &str) -> Option<&CodeNode>;

    /// Get extra props (for git enrichment to check existing data).
    pub fn get_extra_props(&self, qn: StrKey) -> Option<&ExtraProps>;

    /// Access the interner.
    pub fn interner(&self) -> &'static StringInterner;

    // ── Lifecycle ────────────────────────────────────────────

    /// Consume builder, build all indexes, produce immutable graph.
    pub fn freeze(self) -> CodeGraph;
}
```

No `RwLock`, no `DashMap`, no `Mutex`. Plain `&mut self` methods. The builder is single-threaded (graph construction already processes batch results sequentially).

**`CodeGraph`** — frozen, immutable, indexed:

```rust
/// Immutable code graph with pre-built indexes. All queries are O(1).
/// No locks — all methods take &self. Safe to share across rayon threads.
/// Produced by GraphBuilder::freeze().
pub struct CodeGraph {
    // Core data (immutable)
    graph: StableGraph<CodeNode, CodeEdge>,
    node_index: HashMap<StrKey, NodeIndex>,
    extra_props: HashMap<StrKey, ExtraProps>,

    // Pre-built indexes (constructed during freeze())
    indexes: GraphIndexes,
}

/// All pre-built indexes, computed once during freeze().
struct GraphIndexes {
    // Kind indexes — which nodes are functions/classes/files
    functions: Vec<NodeIndex>,
    classes: Vec<NodeIndex>,
    files: Vec<NodeIndex>,

    // Adjacency indexes — per-node incoming/outgoing by edge kind
    call_callers: HashMap<NodeIndex, Vec<NodeIndex>>,   // who calls me
    call_callees: HashMap<NodeIndex, Vec<NodeIndex>>,   // what I call
    import_sources: HashMap<NodeIndex, Vec<NodeIndex>>, // who imports me
    import_targets: HashMap<NodeIndex, Vec<NodeIndex>>, // what I import
    inherit_parents: HashMap<NodeIndex, Vec<NodeIndex>>,// my parent classes
    inherit_children: HashMap<NodeIndex, Vec<NodeIndex>>,// my child classes
    contains_children: HashMap<NodeIndex, Vec<NodeIndex>>,// entities I contain
    contains_parent: HashMap<NodeIndex, Vec<NodeIndex>>,  // who contains me
    uses_targets: HashMap<NodeIndex, Vec<NodeIndex>>,     // what I use
    uses_sources: HashMap<NodeIndex, Vec<NodeIndex>>,     // who uses me
    modified_in: HashMap<NodeIndex, Vec<NodeIndex>>,      // commits that modified me

    // Spatial indexes — per-file node lookups
    functions_by_file: HashMap<StrKey, Vec<NodeIndex>>,
    classes_by_file: HashMap<StrKey, Vec<NodeIndex>>,
    all_nodes_by_file: HashMap<StrKey, Vec<NodeIndex>>,
    function_spatial: HashMap<StrKey, Vec<(u32, u32, NodeIndex)>>,

    // Pre-computed bulk edge lists (for consumers that iterate all edges of a kind)
    all_call_edges: Vec<(NodeIndex, NodeIndex)>,
    all_import_edges: Vec<(NodeIndex, NodeIndex)>,
    all_inheritance_edges: Vec<(NodeIndex, NodeIndex)>,

    // Pre-computed expensive analyses
    import_cycles: Vec<Vec<NodeIndex>>,
    edge_fingerprint: u64,
}
```

**`freeze()` builds all indexes in one pass:**

```rust
impl GraphBuilder {
    pub fn freeze(self) -> CodeGraph {
        let mut indexes = GraphIndexes::default();

        // 1. Scan nodes → kind indexes + spatial indexes
        for idx in self.graph.node_indices() {
            let node = &self.graph[idx];
            match node.kind {
                NodeKind::Function => indexes.functions.push(idx),
                NodeKind::Class => indexes.classes.push(idx),
                NodeKind::File => indexes.files.push(idx),
                _ => {}
            }
            // Spatial: file → nodes
            indexes.all_nodes_by_file.entry(node.file_path).or_default().push(idx);
            if node.kind == NodeKind::Function {
                indexes.functions_by_file.entry(node.file_path).or_default().push(idx);
                indexes.function_spatial.entry(node.file_path).or_default()
                    .push((node.line_start, node.line_end, idx));
            }
            if node.kind == NodeKind::Class {
                indexes.classes_by_file.entry(node.file_path).or_default().push(idx);
            }
        }

        // Sort spatial indexes for binary search
        for spans in indexes.function_spatial.values_mut() {
            spans.sort_unstable_by_key(|(start, _, _)| *start);
        }

        // 2. Scan edges → adjacency indexes
        for edge_ref in self.graph.edge_references() {
            let (src, tgt) = (edge_ref.source(), edge_ref.target());
            match edge_ref.weight().kind {
                EdgeKind::Calls => {
                    indexes.call_callees.entry(src).or_default().push(tgt);
                    indexes.call_callers.entry(tgt).or_default().push(src);
                }
                EdgeKind::Imports => {
                    indexes.import_targets.entry(src).or_default().push(tgt);
                    indexes.import_sources.entry(tgt).or_default().push(src);
                }
                EdgeKind::Inherits => {
                    indexes.inherit_parents.entry(src).or_default().push(tgt);
                    indexes.inherit_children.entry(tgt).or_default().push(src);
                }
                EdgeKind::Contains => {
                    indexes.contains_children.entry(src).or_default().push(tgt);
                    indexes.contains_parent.entry(tgt).or_default().push(src);
                }
                EdgeKind::Uses => {
                    indexes.uses_targets.entry(src).or_default().push(tgt);
                    indexes.uses_sources.entry(tgt).or_default().push(src);
                }
                EdgeKind::ModifiedIn => {
                    // One-directional: entity → commit only.
                    // Commit → entity direction is not needed (no consumer queries it).
                    indexes.modified_in.entry(src).or_default().push(tgt);
                }
            }
        }

        // 3. Pre-compute expensive analyses
        indexes.import_cycles = compute_import_cycles(&self.graph, &self.node_index);
        indexes.edge_fingerprint = compute_edge_fingerprint(&self.graph);

        CodeGraph {
            graph: self.graph,
            node_index: self.node_index,
            extra_props: self.extra_props,
            indexes,
        }
    }
}
```

### GraphQuery Trait Redesign

The new trait uses `NodeIndex`-based access. All methods return references or slices — zero allocation.

```rust
pub trait GraphQuery: Send + Sync {
    /// Access the string interner.
    fn interner(&self) -> &StringInterner;

    // ── Node access (O(1), zero-copy) ────────────────────────

    /// Get a node by its graph index.
    fn node(&self, idx: NodeIndex) -> Option<&CodeNode>;

    /// Look up a node by qualified name. Returns both index and reference.
    fn node_by_name(&self, qn: &str) -> Option<(NodeIndex, &CodeNode)>;

    // ── Kind-indexed collections (O(1), pre-built) ───────────

    /// All function NodeIndexes.
    fn functions(&self) -> &[NodeIndex];

    /// All class NodeIndexes.
    fn classes(&self) -> &[NodeIndex];

    /// All file NodeIndexes.
    fn files(&self) -> &[NodeIndex];

    // ── Adjacency queries (O(1) per node, pre-built) ─────────

    /// Functions that call this node (incoming Calls edges).
    fn callers(&self, idx: NodeIndex) -> &[NodeIndex];

    /// Functions this node calls (outgoing Calls edges).
    fn callees(&self, idx: NodeIndex) -> &[NodeIndex];

    /// Modules/files that import this node (incoming Imports edges).
    fn importers(&self, idx: NodeIndex) -> &[NodeIndex];

    /// Modules/files this node imports (outgoing Imports edges).
    fn importees(&self, idx: NodeIndex) -> &[NodeIndex];

    /// Parent classes (outgoing Inherits edges).
    fn parent_classes(&self, idx: NodeIndex) -> &[NodeIndex];

    /// Child classes (incoming Inherits edges).
    fn child_classes(&self, idx: NodeIndex) -> &[NodeIndex];

    /// Number of callers (derived from adjacency, O(1)).
    fn call_fan_in(&self, idx: NodeIndex) -> usize {
        self.callers(idx).len()
    }

    /// Number of callees (derived from adjacency, O(1)).
    fn call_fan_out(&self, idx: NodeIndex) -> usize {
        self.callees(idx).len()
    }

    // ── File-scoped queries (O(1), pre-built spatial index) ──

    /// Functions in a file.
    fn functions_in_file(&self, file_path: &str) -> &[NodeIndex];

    /// Classes in a file.
    fn classes_in_file(&self, file_path: &str) -> &[NodeIndex];

    /// Find the function containing a line in a file (binary search, O(log N)).
    fn function_at(&self, file_path: &str, line: u32) -> Option<NodeIndex>;

    // ── Pre-computed graph analysis ──────────────────────────

    /// Import cycle groups (computed during freeze).
    fn import_cycles(&self) -> &[Vec<NodeIndex>];

    /// Edge fingerprint for topology change detection.
    fn edge_fingerprint(&self) -> u64;

    /// Graph statistics.
    fn stats(&self) -> BTreeMap<String, i64>;

    // ── Bulk edge access (pre-computed during freeze, O(1) slice) ────

    /// All call edges as (caller, callee) NodeIndex pairs.
    fn all_call_edges(&self) -> &[(NodeIndex, NodeIndex)];

    /// All import edges as (importer, importee) NodeIndex pairs.
    fn all_import_edges(&self) -> &[(NodeIndex, NodeIndex)];

    /// All inheritance edges as (child, parent) NodeIndex pairs.
    fn all_inheritance_edges(&self) -> &[(NodeIndex, NodeIndex)];

    // ── Cold properties ──────────────────────────────────────

    /// Extra properties for a node (churn, blame, etc.).
    /// Returns a reference (frozen graph) — callers needing owned data use .cloned().
    fn extra_props(&self, qn: StrKey) -> Option<&ExtraProps>;

}

// raw_graph() is on CodeGraph directly, NOT the trait — avoids coupling
// all trait implementors to petgraph's concrete StableGraph type.
impl CodeGraph {
    /// Direct access to the underlying petgraph for custom traversals
    /// (BFS, DFS, Dijkstra, etc.) that don't fit the indexed query API.
    /// Only available on the concrete type, not through &dyn GraphQuery.
    pub fn raw_graph(&self) -> &StableGraph<CodeNode, CodeEdge> {
        &self.graph
    }
}
```
```

**What's removed from the old trait:**

| Old method | Replacement | Reason |
|-----------|-------------|--------|
| `get_functions() -> Vec<CodeNode>` | `functions() -> &[NodeIndex]` | Zero-copy |
| `get_callers(qn: &str) -> Vec<CodeNode>` | `callers(idx: NodeIndex) -> &[NodeIndex]` | Index-based, O(1) |
| `get_calls() -> Vec<(StrKey, StrKey)>` | Iterate `callees()` per node | No edge materialization |
| `get_calls_shared() -> Arc<[...]>` | Not needed | Base queries are fast |
| `get_functions_shared() -> Arc<[CodeNode]>` | Not needed | Base queries are fast |
| `build_call_maps_raw()` | Not needed | Adjacency indexes replace this |
| `get_call_adjacency()` | Not needed | Adjacency indexes replace this |
| `caller_file_spread()` | Compute from `callers()` + `node()` | Not a core graph operation |
| `caller_module_spread()` | Same | Same |
| `count_external_callers_of()` | Same | Same |
| `get_complex_functions()` | Filter `functions()` by complexity | Not a core graph operation |
| `get_long_param_functions()` | Filter `functions()` by params | Same |
| `is_in_import_cycle()` | Check `import_cycles()` | Pre-computed |

**Backward-compatible bridge methods (deprecated, for gradual migration):**

```rust
// In compat.rs — deprecated bridges from old API to new
impl CodeGraph {
    #[deprecated(note = "Use functions() + node() instead")]
    pub fn get_functions(&self) -> Vec<CodeNode> {
        self.functions().iter()
            .filter_map(|&idx| self.node(idx).copied())
            .collect()
    }

    #[deprecated(note = "Use node_by_name() + callers() + node() instead")]
    pub fn get_callers(&self, qn: &str) -> Vec<CodeNode> {
        let Some((idx, _)) = self.node_by_name(qn) else { return vec![] };
        self.callers(idx).iter()
            .filter_map(|&ci| self.node(ci).copied())
            .collect()
    }

    // ... bridges for all old methods
}
```

These bridges enable gradual migration. Consumers are updated one file at a time. Once all consumers use the new API, bridges are deleted.

**Complete bridge method inventory** (every old method → bridge):

| Old method | Bridge delegates to | Notes |
|-----------|-------------------|-------|
| `get_functions() -> Vec<CodeNode>` | `functions()` + `node()` | Clones nodes |
| `get_classes() -> Vec<CodeNode>` | `classes()` + `node()` | Clones nodes |
| `get_files() -> Vec<CodeNode>` | `files()` + `node()` | Clones nodes |
| `get_functions_shared() -> Arc<[CodeNode]>` | `functions()` + `node()` | Builds Arc |
| `get_classes_shared() -> Arc<[CodeNode]>` | `classes()` + `node()` | Builds Arc |
| `get_files_shared() -> Arc<[CodeNode]>` | `files()` + `node()` | Builds Arc |
| `get_callers(qn) -> Vec<CodeNode>` | `node_by_name()` + `callers()` + `node()` | |
| `get_callees(qn) -> Vec<CodeNode>` | `node_by_name()` + `callees()` + `node()` | |
| `get_importers(qn) -> Vec<CodeNode>` | `node_by_name()` + `importers()` + `node()` | |
| `get_child_classes(qn) -> Vec<CodeNode>` | `node_by_name()` + `child_classes()` + `node()` | |
| `get_node(qn) -> Option<CodeNode>` | `node_by_name()` | Clones |
| `get_functions_in_file(path) -> Vec<CodeNode>` | `functions_in_file()` + `node()` | |
| `get_classes_in_file(path) -> Vec<CodeNode>` | `classes_in_file()` + `node()` | |
| `find_function_at(path, line) -> Option<CodeNode>` | `function_at()` + `node()` | |
| `call_fan_in(qn) -> usize` | `node_by_name()` + `call_fan_in(idx)` | |
| `call_fan_out(qn) -> usize` | `node_by_name()` + `call_fan_out(idx)` | |
| `get_calls() -> Vec<(StrKey, StrKey)>` | Iterate `callees()` per function, resolve QNs | O(E) |
| `get_calls_shared() -> Arc<[(StrKey, StrKey)]>` | Same, wrapped in Arc | O(E) |
| `get_imports() -> Vec<(StrKey, StrKey)>` | Iterate `importees()` per node, resolve QNs | O(E) |
| `get_inheritance() -> Vec<(StrKey, StrKey)>` | Iterate `parent_classes()` per class, resolve QNs | O(E) |
| `build_call_maps_raw()` | Build from `callers()`/`callees()` per function | O(N) |
| `get_call_adjacency()` | Build from `callers()`/`callees()` per function | O(N) |
| `find_import_cycles() -> Vec<Vec<String>>` | `import_cycles()` + resolve QNs | Pre-computed |
| `is_in_import_cycle(path)` | `import_cycles()` + resolve + contains check | |
| `stats() -> BTreeMap<String, i64>` | Direct — same signature | |
| `extra_props(qn) -> Option<ExtraProps>` | `extra_props(qn).cloned()` | Clones for owned return |
| `caller_file_spread(qn) -> usize` | `callers()` + `node()` → unique files | |
| `caller_module_spread(qn) -> usize` | `callers()` + `node()` → unique parent dirs | |
| `count_external_callers_of(qn, ...)` | `callers()` + `node()` → filter by file/line | |
| `get_complex_functions(min) -> Vec<CodeNode>` | `functions()` + filter by complexity | |
| `get_long_param_functions(min) -> Vec<CodeNode>` | `functions()` + filter by params | |
| `compute_coupling_stats()` | Iterate callers/callees, check cross-file edges | Method on `CodeGraph` |
| `compute_edge_fingerprint()` | `edge_fingerprint()` | Pre-computed during freeze |
| `find_minimal_cycle(qn, kind)` | Use `raw_graph()` for BFS | Method on `CodeGraph` |
| `get_edges_by_kind(kind)` | Iterate adjacency index for that kind | Method on `CodeGraph` |
| `get_all_edges()` | Iterate all adjacency indexes | Method on `CodeGraph` |

**Note on `extra_props()`:** Return type changes from `Option<ExtraProps>` (owned) to `Option<&ExtraProps>` (reference). Bridge clones for backward compat.

**Note on adjacency sort order:** Adjacency vectors are sorted by **resolved qualified name** during `freeze()`, matching the current behavior where `get_callers`/`get_callees` sort by QN. This ensures deterministic iteration order and identical results during migration.

**Note on `compute_coupling_stats()` and `find_minimal_cycle()`:** These are NOT on the `GraphQuery` trait (they weren't before either). They are methods on `CodeGraph` directly, computed from adjacency indexes + `raw_graph()`. `GraphScorer` receives `&CodeGraph` (concrete type, not trait object).

**Note on `MetricsCache` and `GraphScorer`:** `GraphScorer::new()` currently takes `&GraphStore`. After migration, it takes `(&CodeGraph, &MetricsCache)`. This constructor change is part of Phase C.

### Consumer Migration Pattern

**Before (clone-heavy, lock-contended):**
```rust
let functions = graph.get_functions();  // O(N) scan, N clones, lock acquired
for func in &functions {
    let callers = graph.get_callers(func.qn(i));  // O(E) scan per func
    if callers.len() > threshold {
        // ... create finding
    }
}
```

**After (zero-copy, lockless):**
```rust
let i = graph.interner();
for &func_idx in graph.functions() {          // O(1), slice reference
    let fan_in = graph.call_fan_in(func_idx); // O(1), adjacency lookup
    if fan_in > threshold {
        let func = graph.node(func_idx).unwrap(); // O(1), direct access
        // ... create finding using func.qn(i), func.line_start, etc.
    }
}
```

Zero allocations. No locks. The graph is accessed through pre-built indexes.

### Incremental Patching

`CodeGraph` supports conversion back to a builder for incremental graph patching:

```rust
impl CodeGraph {
    /// Convert back to a mutable builder for patching.
    /// Moves the StableGraph without copying nodes/edges.
    /// Rebuilds edge_set from graph edges — O(E) where E is edge count.
    /// Indexes are dropped (rebuilt on re-freeze).
    /// NodeIndex values remain stable across into_builder/re-freeze cycles
    /// (StableGraph guarantees this).
    pub fn into_builder(self) -> GraphBuilder {
        // Rebuild edge_set from graph edges — O(E)
        let edge_set: HashSet<(NodeIndex, NodeIndex, EdgeKind)> = self.graph
            .edge_references()
            .map(|e| (e.source(), e.target(), e.weight().kind))
            .collect();

        GraphBuilder {
            graph: self.graph,
            node_index: self.node_index,
            edge_set,
            extra_props: self.extra_props,
        }
    }

    /// Clone into a builder. Used when Arc::try_unwrap fails (multiple references).
    /// O(N+E) — copies all nodes, edges, and properties.
    pub fn clone_into_builder(&self) -> GraphBuilder {
        let graph = self.graph.clone();
        let node_index = self.node_index.clone();
        let edge_set = graph.edge_references()
            .map(|e| (e.source(), e.target(), e.weight().kind))
            .collect();
        GraphBuilder {
            graph,
            node_index,
            edge_set,
            extra_props: self.extra_props.clone(),
        }
    }
}
```

The incremental path:
1. `code_graph.into_builder()` — O(1) move, indexes dropped
2. `builder.remove_file_entities(&changed_files)` — remove old nodes/edges
3. `builder.add_nodes_batch(new_nodes)` + `builder.add_edges_batch(new_edges)` — add from re-parsed files
4. `builder.freeze()` — rebuild all indexes

`StableGraph` preserves `NodeIndex` stability across node removals, so cached data (PrecomputedAnalysis reachability index, etc.) remains valid.

**MetricsCache invalidation:** Since `MetricsCache` is separate from the graph, `remove_file_entities()` returns a `Vec<String>` of removed qualified names. The engine uses this to clear stale metrics entries:
```rust
let removed_qns = builder.remove_file_entities(&changed_files);
metrics_cache.remove_for_entities(&removed_qns);
```

### MetricsCache Extraction

`metrics_cache: DashMap<String, f64>` moves out of the graph into a separate struct:

```rust
/// Cross-phase metrics cache. Detectors write, scoring reads.
/// Not part of graph state — passed separately through AnalysisContext.
pub struct MetricsCache {
    cache: DashMap<String, f64>,
}

impl MetricsCache {
    pub fn new() -> Self;
    pub fn set(&self, key: &str, value: f64);
    pub fn get(&self, key: &str) -> Option<f64>;
    pub fn get_with_prefix(&self, prefix: &str) -> Vec<(String, f64)>;
}
```

Added to `AnalysisContext` as a new field. Detectors that currently call `graph.cache_metric()` will call `ctx.metrics.set()` instead.

### Persistence

**redb persistence** — unchanged in purpose, adapted for the new types:

```rust
impl GraphBuilder {
    /// Save to redb (structured, ACID).
    pub fn save_redb(&self, db_path: &Path) -> Result<()>;

    /// Load from redb into a builder.
    pub fn load_redb(db_path: &Path) -> Result<Self>;
}
```

**bincode cache** — for fast session persistence:

```rust
impl CodeGraph {
    /// Save frozen graph to bincode (fast, for session cache).
    pub fn save_cache(&self, path: &Path) -> Result<()>;

    /// Load frozen graph from bincode.
    pub fn load_cache(path: &Path) -> Option<Self>;
}
```

The engine's `save()`/`load()` uses bincode for session persistence (fast), while the CLI's `repotoire graph` command uses redb for structured persistence (queryable).

### Pipeline Changes

**Graph stage (stage 3):**
```rust
pub fn graph_stage(input: &GraphInput) -> Result<GraphOutput> {
    let mut builder = GraphBuilder::new();
    // ... add nodes and edges from parse results ...
    let graph = Arc::new(builder.freeze());
    let edge_fingerprint = graph.edge_fingerprint();
    Ok(GraphOutput { graph, edge_fingerprint, ... })
}
```

**Graph patch stage (incremental):**
```rust
pub fn graph_patch_stage(input: &GraphPatchInput) -> Result<GraphOutput> {
    // Try to take ownership (O(E) for into_builder edge_set rebuild).
    // Falls back to clone (O(N+E)) if other Arc references exist.
    let mut builder = match Arc::try_unwrap(input.graph) {
        Ok(graph) => graph.into_builder(),
        Err(arc) => arc.clone_into_builder(),
    };
    builder.remove_file_entities(&input.changed_files);
    builder.remove_file_entities(&input.removed_files);
    // ... add new nodes and edges from new parse results ...
    let graph = Arc::new(builder.freeze());
    let edge_fingerprint = graph.edge_fingerprint();
    Ok(GraphOutput { graph, edge_fingerprint, ... })
}
```

**Git enrichment (stage 4) — runs on builder before freeze:**

The pipeline order changes:
```
Current:  Parse → Build graph (GraphStore) → [Git enrich ∥ Detect precompute] → Detect
New:      Parse → Build graph (GraphBuilder) → Git enrich → freeze() → Detect
```

Git enrichment runs on the builder (~1s), then `freeze()` builds all indexes (~50ms). Sequential, but simpler and correct. The ~1s cold-run regression is acceptable.

**Detect stage:**
```rust
// No more CachedGraphQuery wrapper
let ctx = precomputed.to_context(graph.as_ref(), &resolver);
let findings = run_detectors(&detectors, &ctx, workers);
```

### File Structure

```
graph/
├── mod.rs            # Re-exports: CodeGraph, GraphBuilder, GraphQuery, etc.
├── traits.rs         # GraphQuery trait (redesigned)
├── builder.rs        # GraphBuilder — mutable accumulator
├── frozen.rs         # CodeGraph — immutable, indexed, impl GraphQuery
├── indexes.rs        # GraphIndexes struct, freeze() index-building logic
├── persistence.rs    # redb save/load, bincode cache
├── interner.rs       # StringInterner (unchanged)
├── store_models.rs   # CodeNode, CodeEdge, EdgeKind, NodeKind (unchanged)
└── compat.rs         # Deprecated bridge methods for gradual migration
```

Old files deleted:
- `store/mod.rs` (1,736 lines) — split across builder.rs, frozen.rs, indexes.rs, persistence.rs
- `cached.rs` (528 lines) — eliminated entirely

## What Gets Deleted

| Code | Lines | Reason |
|------|-------|--------|
| `GraphStore` struct + all impl blocks | ~1,736 | Split into GraphBuilder + CodeGraph |
| `CachedGraphQuery` struct + impl | ~528 | Pre-built indexes make it unnecessary |
| `RwLock<StableGraph>` wrapping | ~20 | Frozen graph is lockless |
| 6 `DashMap` index fields | ~30 | Replaced by plain `HashMap` in GraphIndexes |
| `Mutex<HashSet>` edge dedup | ~10 | Builder uses plain `HashSet` with `&mut self` |
| `RwLock<Option<CallMapsRaw>>` | ~50 | Adjacency indexes replace call maps cache |
| `metrics_cache` on GraphStore | ~30 | Moved to separate MetricsCache |
| Deprecated old `GraphQuery` methods | ~200 | After all consumers migrated |
| **Total** | ~2,264 (after full migration) | |

## What Gets Added

| Code | Lines | Purpose |
|------|-------|---------|
| `GraphBuilder` | ~400 | Mutable graph accumulator |
| `CodeGraph` + `impl GraphQuery` | ~300 | Immutable indexed graph |
| `GraphIndexes` + `freeze()` logic | ~200 | Index construction |
| Persistence (adapted) | ~250 | redb + bincode for new types |
| `MetricsCache` | ~30 | Extracted metrics cache |
| Compat bridges | ~150 | Deprecated bridges during migration |
| Consumer updates (21 files) | ~500 | NodeIndex-based patterns |
| **Total** | ~1,830 | |

Net: ~2,264 deleted, ~1,830 added. But the code is fundamentally better: no locks, O(1) queries, clean separation of mutation and query paths.

## Behavior Changes

### Intentional

1. **Git enrichment runs before freeze, not in parallel with detect precompute.** Cold runs are ~1s slower. The frozen graph is truly immutable during detection.

2. **`get_functions()` → `functions()` returns `&[NodeIndex]` instead of `Vec<CodeNode>`.** Consumers resolve nodes via `graph.node(idx)`. Zero allocation instead of N clones.

3. **`get_callers(qn)` → `callers(idx)` takes `NodeIndex` instead of `&str`.** Consumers look up the index once via `node_by_name()`, then use it for all queries. No per-query string interning.

4. **Import cycles pre-computed during freeze.** Currently computed lazily on first call. Now always computed. Cost: ~10ms during freeze. Benefit: zero-cost subsequent access.

5. **MetricsCache is separate from the graph.** Detectors access it via `AnalysisContext`, not `graph.cache_metric()`.

### Preserved

1. All graph data (nodes, edges, properties) is identical.
2. All query results are identical (same nodes returned, same edges, same cycles).
3. `NodeIndex` stability across incremental patching (StableGraph guarantees this).
4. Deterministic output (sorted results, BTreeMap for stats).
5. The `Detector` trait and all 100 detectors are unchanged (they access the graph through `AnalysisContext`, which adapts transparently).

## Migration Path

### Phase A: Split file + add indexes (internal only)

- Split `store/mod.rs` into `builder.rs`, `frozen.rs`, `indexes.rs`, `persistence.rs`
- Create `GraphBuilder` that wraps current mutation logic
- Create `CodeGraph` with `freeze()` that builds `GraphIndexes`
- Add `pub type GraphStore = CodeGraph;` alias for backward compat
- All existing code works — the alias makes the change invisible

### Phase B: Wire pipeline to Builder/Frozen

- `graph_stage` produces `CodeGraph` via `GraphBuilder::freeze()`
- `graph_patch_stage` uses `into_builder()` → patch → re-`freeze()`
- Git enrichment runs on builder before freeze
- `AnalysisEngine` stores `Arc<CodeGraph>` instead of `Arc<GraphStore>`
- Consumers still use old `GraphQuery` methods (bridges)

### Phase C: Redesign GraphQuery trait

- Add new methods to `GraphQuery` (`functions()`, `callers()`, `node()`, etc.)
- Add deprecated bridges for old methods in `compat.rs`
- Migrate consumers one file at a time (21 files, ~50 call site updates)
- Extract `MetricsCache` from graph, add to `AnalysisContext`

### Phase D: Eliminate CachedGraphQuery

- All consumers use `CodeGraph` directly
- Delete `cached.rs` (528 lines)
- Remove bridge methods from `compat.rs`
- Delete `compat.rs`
- Final cleanup of old `GraphQuery` trait methods

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| freeze() cost on large repos (100K+ nodes) | Slower graph stage | Benchmark freeze() on Django/Linux repos. Expected ~50-100ms, dwarfed by parse time |
| ~1s cold-run regression from sequential git enrichment | Slower cold analysis | Acceptable trade-off; incremental runs unaffected |
| NodeIndex instability across unfreeze/re-freeze | Corrupted incremental data | StableGraph guarantees index stability; integration tests verify |
| Consumer migration churn (21 files) | Merge conflicts, temporary regressions | Phase C is gradual (one file per commit); bridges provide backward compat |
| MetricsCache extraction changes AnalysisContext | Detector API change | MetricsCache is a new field; old `graph.cache_metric()` bridges during migration |
| Persistence format change | Cached sessions invalidated | Bump SESSION_VERSION; clean error on mismatch |
