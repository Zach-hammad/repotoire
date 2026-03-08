# Performance Architecture V3 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Optimize repotoire's analysis pipeline with persistent graph caching, shared detector context, and unified detection pipeline — targeting sub-5s cold and sub-2s incremental on CPython.

**Architecture:** Persistent graph via bincode + StableGraph for delta patching on incremental runs. DetectorContext struct pre-builds shared data (callers/callees maps, file contents, class hierarchy) during existing parallel precompute and injects into detectors. Streaming detection path removed — all repos use the speculative parallelism path.

**Tech Stack:** Rust, petgraph (StableGraph), bincode, rayon, crossbeam

---

### Task 1: Unify Detection Pipeline (remove streaming path)

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/mod.rs:685-711` (remove `use_streaming` branch in `execute_detection_phase`)
- Modify: `repotoire-cli/src/cli/analyze/detect.rs:439-504` (remove `run_detectors_streaming()`)
- Modify: `repotoire-cli/src/cli/analyze/mod.rs:22-23` (remove `run_detectors_streaming` import)

**Step 1: Remove the streaming detection path**

In `repotoire-cli/src/cli/analyze/mod.rs`, the `execute_detection_phase()` function (line 675) has a branching check at line 685:

```rust
let use_streaming = file_result.all_files.len() > 5000;
if use_streaming {
    // ... run_detectors_streaming path (lines 687-711)
}
```

Delete lines 685-711 (the entire `use_streaming` check and streaming branch). The speculative path (starting at line 713) becomes the only path.

Also remove the `run_detectors_streaming` import from line 23:
```rust
// Before
use detect::{
    apply_voting, finish_git_enrichment, run_detectors_speculative, run_detectors_streaming,
    run_gi_detectors, start_git_enrichment,
};
// After
use detect::{
    apply_voting, finish_git_enrichment, run_detectors_speculative,
    run_gi_detectors, start_git_enrichment,
};
```

**Step 2: Delete `run_detectors_streaming()`**

In `repotoire-cli/src/cli/analyze/detect.rs`, delete the `run_detectors_streaming()` function (lines 439-504). It's no longer called.

**Step 3: Build and verify**

Run: `cargo check`
Expected: Clean compilation with no errors. There may be dead_code warnings for streaming-related helpers — remove those too if they exist.

**Step 4: Run tests**

Run: `cargo test`
Expected: All tests pass.

**Step 5: Commit**

```bash
git add repotoire-cli/src/cli/analyze/mod.rs repotoire-cli/src/cli/analyze/detect.rs
git commit -m "perf: unify detection pipeline — remove streaming path, all repos use speculative parallelism"
```

---

### Task 2: Add bincode dependency and StableGraph migration

**Files:**
- Modify: `repotoire-cli/Cargo.toml` (add bincode)
- Modify: `repotoire-cli/src/graph/store/mod.rs:9,28,73,101,119,892` (DiGraph → StableGraph)

**Step 1: Add bincode dependency**

In `repotoire-cli/Cargo.toml`, add to `[dependencies]`:

```toml
bincode = "1.3"
```

**Step 2: Migrate DiGraph to StableGraph**

In `repotoire-cli/src/graph/store/mod.rs`:

1. Change the import (line 9):
```rust
// Before
use petgraph::graph::{DiGraph, NodeIndex};
// After
use petgraph::stable_graph::{NodeIndex, StableGraph};
```

2. Change the field type (line 28):
```rust
// Before
graph: RwLock<DiGraph<CodeNode, CodeEdge>>,
// After
graph: RwLock<StableGraph<CodeNode, CodeEdge>>,
```

3. Change all constructor calls (lines 73, 101, 119):
```rust
// Before
graph: RwLock::new(DiGraph::new()),
// After
graph: RwLock::new(StableGraph::new()),
```

4. Change the lock helper return types (lines 143, 150):
```rust
// Before
fn read_graph(&self) -> std::sync::RwLockReadGuard<'_, DiGraph<CodeNode, CodeEdge>> {
// After
fn read_graph(&self) -> std::sync::RwLockReadGuard<'_, StableGraph<CodeNode, CodeEdge>> {
```
```rust
// Before
fn write_graph(&self) -> std::sync::RwLockWriteGuard<'_, DiGraph<CodeNode, CodeEdge>> {
// After
fn write_graph(&self) -> std::sync::RwLockWriteGuard<'_, StableGraph<CodeNode, CodeEdge>> {
```

5. Change the `find_import_cycles()` filtered graph (around line 892):
```rust
// Before
let mut filtered_graph: DiGraph<NodeIndex, ()> = DiGraph::new();
// After
let mut filtered_graph: StableGraph<NodeIndex, ()> = StableGraph::new();
```

6. Search for any other `DiGraph` usages in the file and update them. Also check `repotoire-cli/src/graph/cached.rs` for any direct `DiGraph` references.

**Step 3: Fix any `EdgeIndex` import**

StableGraph uses `petgraph::stable_graph::EdgeIndex` instead of `petgraph::graph::EdgeIndex`. Check if EdgeIndex is imported anywhere in `store/mod.rs` and update if needed.

**Step 4: Build and verify**

Run: `cargo check`
Expected: Clean compilation. StableGraph has the same API as DiGraph for all operations used (add_node, add_edge, node_weight, node_weights, edge_references, neighbors, tarjan_scc, etc.).

If there are compilation errors, they'll be from API differences — the main ones are:
- `StableGraph::node_indices()` instead of iterating raw indices
- `StableGraph` uses `petgraph::visit::IntoNodeReferences` instead of `node_weights()`
- Fix each error individually

**Step 5: Run tests**

Run: `cargo test`
Expected: All tests pass. StableGraph preserves all graph semantics.

**Step 6: Commit**

```bash
git add repotoire-cli/Cargo.toml repotoire-cli/src/graph/store/mod.rs repotoire-cli/src/graph/cached.rs
git commit -m "refactor: migrate petgraph DiGraph to StableGraph for safe node removal support"
```

---

### Task 3: Add persistent graph cache — save/load

**Files:**
- Modify: `repotoire-cli/src/graph/store/mod.rs` (add cache methods)
- Modify: `repotoire-cli/src/graph/store_models.rs` (ensure Serialize/Deserialize on EdgeKind)

**Step 1: Add file→node reverse index to GraphStore**

In `repotoire-cli/src/graph/store/mod.rs`, add a new field to the `GraphStore` struct (after the existing `file_classes_index` field, around line 37):

```rust
/// Reverse index: file_path → all NodeIndexes belonging to that file.
/// Used for delta patching (removing a file's entities from the graph).
file_all_nodes_index: DashMap<String, Vec<NodeIndex>>,
```

Initialize it in all three constructors (`new`, `new_lazy`, `in_memory`):
```rust
file_all_nodes_index: DashMap::new(),
```

Populate it in `add_nodes_batch()` — after the existing index population block (around line 330), add:
```rust
// Populate file → all nodes reverse index
file_all_nodes_idx_entry.entry(node.file_path.clone()).or_default().push(idx);
```

Actually, simpler: add one line inside the `add_nodes_batch` loop after the node is added:
```rust
self.file_all_nodes_index.entry(node.file_path.clone()).or_default().push(idx);
```

Also populate in `add_node()` (around line 270):
```rust
self.file_all_nodes_index.entry(node.file_path.clone()).or_default().push(idx);
```

**Step 2: Add GraphCache struct and save_graph_cache()**

Add a new struct and methods at the end of `repotoire-cli/src/graph/store/mod.rs` (before the `impl GraphQuery for GraphStore` block):

```rust
use serde::{Serialize as SerdeSerialize, Deserialize as SerdeDeserialize};

/// Serializable graph cache for persistent storage between runs.
#[derive(SerdeSerialize, SerdeDeserialize)]
struct GraphCache {
    version: u32,
    binary_version: String,
    graph: StableGraph<CodeNode, CodeEdge>,
    node_index: HashMap<String, NodeIndex>,
    file_all_nodes: HashMap<String, Vec<NodeIndex>>,
}

const GRAPH_CACHE_VERSION: u32 = 1;

impl GraphStore {
    /// Save the in-memory graph to a bincode cache file for fast reload.
    pub fn save_graph_cache(&self, cache_path: &std::path::Path) -> Result<()> {
        let graph = self.read_graph();
        let node_index: HashMap<String, NodeIndex> = self.node_index.iter()
            .map(|entry| (entry.key().clone(), *entry.value()))
            .collect();
        let file_all_nodes: HashMap<String, Vec<NodeIndex>> = self.file_all_nodes_index.iter()
            .map(|entry| (entry.key().clone(), entry.value().clone()))
            .collect();

        let cache = GraphCache {
            version: GRAPH_CACHE_VERSION,
            binary_version: env!("CARGO_PKG_VERSION").to_string(),
            graph: graph.clone(),
            node_index,
            file_all_nodes,
        };

        let bytes = bincode::serialize(&cache)
            .context("Failed to serialize graph cache")?;

        if let Some(parent) = cache_path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(cache_path, bytes)
            .context("Failed to write graph cache")?;

        Ok(())
    }

    /// Load a graph from a bincode cache file, rebuilding all indexes.
    /// Returns None if cache is missing, corrupt, or version-mismatched.
    pub fn load_graph_cache(cache_path: &std::path::Path) -> Option<Self> {
        let bytes = std::fs::read(cache_path).ok()?;
        let cache: GraphCache = bincode::deserialize(&bytes).ok()?;

        // Version check
        if cache.version != GRAPH_CACHE_VERSION
            || cache.binary_version != env!("CARGO_PKG_VERSION")
        {
            tracing::info!("Graph cache version mismatch, rebuilding");
            return None;
        }

        let store = Self {
            graph: RwLock::new(cache.graph),
            node_index: DashMap::new(),
            function_spatial_index: DashMap::new(),
            file_functions_index: DashMap::new(),
            file_classes_index: DashMap::new(),
            file_all_nodes_index: DashMap::new(),
            metrics_cache: DashMap::new(),
            interner: super::interner::StringInterner::new(),
            edge_set: Mutex::new(HashSet::new()),
            call_maps_cache: OnceLock::new(),
            db: None,
            db_path: None,
            lazy_mode: false,
        };

        // Rebuild DashMap indexes from cached data
        for (key, idx) in cache.node_index {
            store.node_index.insert(key, idx);
        }
        for (file, nodes) in cache.file_all_nodes {
            store.file_all_nodes_index.insert(file, nodes);
        }

        // Rebuild file_functions_index, file_classes_index, and spatial_index from graph
        let graph = store.read_graph();
        for idx in graph.node_indices() {
            if let Some(node) = graph.node_weight(idx) {
                match node.kind {
                    NodeKind::Function => {
                        store.file_functions_index
                            .entry(node.file_path.clone())
                            .or_default()
                            .push(idx);
                        store.function_spatial_index
                            .entry(node.file_path.clone())
                            .or_default()
                            .push((node.line_start, node.line_end, idx));
                    }
                    NodeKind::Class => {
                        store.file_classes_index
                            .entry(node.file_path.clone())
                            .or_default()
                            .push(idx);
                    }
                    _ => {}
                }
            }
        }
        drop(graph);

        // Rebuild edge_set from graph edges
        {
            let graph = store.read_graph();
            let mut edge_set = store.edge_set.lock().unwrap();
            for edge_ref in graph.edge_references() {
                edge_set.insert((edge_ref.source(), edge_ref.target(), edge_ref.weight().kind.clone()));
            }
        }

        tracing::info!("Loaded graph cache ({} nodes)", store.node_index.len());
        Some(store)
    }
}
```

**Step 3: Build and verify**

Run: `cargo check`
Expected: May need to add `Serialize`/`Deserialize` derives to `EdgeKind` in `store_models.rs` (check if already present — `CodeNode` and `CodeEdge` already have them). Also check that `StableGraph` supports serde (it does when petgraph's `serde-1` feature is enabled).

Check `Cargo.toml` for petgraph features:
```toml
# May need to add:
petgraph = { version = "0.7", features = ["serde-1"] }
```

**Step 4: Write tests**

Add inline tests at the bottom of `store/mod.rs`:

```rust
#[cfg(test)]
mod graph_cache_tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn test_save_and_load_graph_cache() {
        let store = GraphStore::in_memory();
        // Add some nodes and edges
        let n1 = store.add_node(CodeNode::function("foo", "src/main.rs").with_qualified_name("main.foo").with_lines(1, 10));
        let n2 = store.add_node(CodeNode::function("bar", "src/main.rs").with_qualified_name("main.bar").with_lines(12, 20));
        store.add_node(CodeNode::class("MyClass", "src/lib.rs").with_qualified_name("lib.MyClass").with_lines(1, 50));
        store.add_edges_batch(vec![
            ("main.foo".to_string(), "main.bar".to_string(), CodeEdge::new(EdgeKind::Calls)),
        ]);

        let tmp = TempDir::new().unwrap();
        let cache_path = tmp.path().join("graph_cache.bin");

        // Save
        store.save_graph_cache(&cache_path).unwrap();
        assert!(cache_path.exists());

        // Load
        let loaded = GraphStore::load_graph_cache(&cache_path).unwrap();
        assert_eq!(loaded.node_index.len(), 3);
        assert!(loaded.get_node("main.foo").is_some());
        assert!(loaded.get_node("main.bar").is_some());
        assert!(loaded.get_node("lib.MyClass").is_some());

        // Verify indexes rebuilt
        assert_eq!(loaded.get_functions_in_file("src/main.rs").len(), 2);
        assert_eq!(loaded.get_classes_in_file("src/lib.rs").len(), 1);

        // Verify edges
        let callers = loaded.get_callers("main.bar");
        assert_eq!(callers.len(), 1);
        assert_eq!(callers[0].qualified_name, "main.foo");
    }

    #[test]
    fn test_cache_version_mismatch_returns_none() {
        let store = GraphStore::in_memory();
        store.add_node(CodeNode::function("foo", "a.rs").with_qualified_name("a.foo"));

        let tmp = TempDir::new().unwrap();
        let cache_path = tmp.path().join("graph_cache.bin");
        store.save_graph_cache(&cache_path).unwrap();

        // Corrupt version by writing garbage
        std::fs::write(&cache_path, b"invalid data").unwrap();
        assert!(GraphStore::load_graph_cache(&cache_path).is_none());
    }
}
```

**Step 5: Run tests**

Run: `cargo test graph_cache_tests`
Expected: Both tests pass.

**Step 6: Commit**

```bash
git add repotoire-cli/Cargo.toml repotoire-cli/src/graph/store/mod.rs repotoire-cli/src/graph/store_models.rs
git commit -m "feat: add persistent graph cache — bincode save/load with index rebuild"
```

---

### Task 4: Add delta patching — remove_file_entities()

**Files:**
- Modify: `repotoire-cli/src/graph/store/mod.rs` (add removal method)

**Step 1: Implement `remove_file_entities()`**

Add this method to the `impl GraphStore` block:

```rust
/// Remove all nodes and edges belonging to a set of files.
/// Used for delta patching: remove stale data before re-parsing changed files.
///
/// StableGraph allows safe node removal without invalidating other indexes.
pub fn remove_file_entities(&self, files: &[PathBuf]) {
    let mut graph = self.write_graph();
    let mut edge_set = self.edge_set.lock().unwrap();

    for file in files {
        let file_str = file.to_string_lossy();

        // Get all nodes in this file
        let node_idxs: Vec<NodeIndex> = self.file_all_nodes_index
            .remove(file_str.as_ref())
            .map(|(_, v)| v)
            .unwrap_or_default();

        for idx in &node_idxs {
            // Remove all edges connected to this node
            let edges_to_remove: Vec<_> = graph.edges(*idx)
                .map(|e| e.id())
                .collect();
            // Also incoming edges
            let incoming: Vec<_> = graph.edges_directed(*idx, Direction::Incoming)
                .map(|e| e.id())
                .collect();

            let mut all_edges: Vec<_> = edges_to_remove;
            all_edges.extend(incoming);
            all_edges.sort();
            all_edges.dedup();

            for eid in all_edges {
                if let (Some(src), Some(tgt)) = (graph.edge_endpoints(eid).map(|(s, _)| s), graph.edge_endpoints(eid).map(|(_, t)| t)) {
                    if let Some(edge) = graph.edge_weight(eid) {
                        edge_set.remove(&(src, tgt, edge.kind.clone()));
                    }
                }
                graph.remove_edge(eid);
            }

            // Remove the node from QN index
            if let Some(node) = graph.node_weight(*idx) {
                self.node_index.remove(&node.qualified_name);
            }

            // Remove the node
            graph.remove_node(*idx);
        }

        // Clean up file-scoped indexes
        self.file_functions_index.remove(file_str.as_ref());
        self.file_classes_index.remove(file_str.as_ref());
        self.function_spatial_index.remove(file_str.as_ref());
    }

    // Invalidate call maps cache (will be rebuilt lazily)
    // OnceLock doesn't support reset, so we leave it — CachedGraphQuery
    // will be created fresh after patching anyway.
}
```

**Step 2: Write tests**

```rust
#[test]
fn test_remove_file_entities() {
    let store = GraphStore::in_memory();
    let _n1 = store.add_node(CodeNode::function("foo", "src/a.rs").with_qualified_name("a.foo").with_lines(1, 10));
    let _n2 = store.add_node(CodeNode::function("bar", "src/a.rs").with_qualified_name("a.bar").with_lines(12, 20));
    let _n3 = store.add_node(CodeNode::function("baz", "src/b.rs").with_qualified_name("b.baz").with_lines(1, 10));
    store.add_edges_batch(vec![
        ("a.foo".to_string(), "a.bar".to_string(), CodeEdge::new(EdgeKind::Calls)),
        ("a.foo".to_string(), "b.baz".to_string(), CodeEdge::new(EdgeKind::Calls)),
    ]);

    assert_eq!(store.get_functions().len(), 3);

    // Remove file a.rs
    store.remove_file_entities(&[PathBuf::from("src/a.rs")]);

    // a.rs nodes gone
    assert!(store.get_node("a.foo").is_none());
    assert!(store.get_node("a.bar").is_none());
    // b.rs node still exists
    assert!(store.get_node("b.baz").is_some());

    // Only 1 function remaining
    let funcs = store.get_functions();
    assert_eq!(funcs.len(), 1);
    assert_eq!(funcs[0].qualified_name, "b.baz");

    // Edge from a.foo to b.baz should be gone
    assert_eq!(store.get_callers("b.baz").len(), 0);
}

#[test]
fn test_delta_patching_roundtrip() {
    let store = GraphStore::in_memory();
    store.add_node(CodeNode::function("foo", "src/a.rs").with_qualified_name("a.foo").with_lines(1, 10));
    store.add_node(CodeNode::function("bar", "src/b.rs").with_qualified_name("b.bar").with_lines(1, 10));
    store.add_edges_batch(vec![
        ("a.foo".to_string(), "b.bar".to_string(), CodeEdge::new(EdgeKind::Calls)),
    ]);

    // Save
    let tmp = tempfile::TempDir::new().unwrap();
    let cache_path = tmp.path().join("cache.bin");
    store.save_graph_cache(&cache_path).unwrap();

    // Load
    let mut loaded = GraphStore::load_graph_cache(&cache_path).unwrap();
    assert_eq!(loaded.get_functions().len(), 2);

    // Patch: remove a.rs, add new version
    loaded.remove_file_entities(&[PathBuf::from("src/a.rs")]);
    assert_eq!(loaded.get_functions().len(), 1);

    // Re-add with modified content
    loaded.add_node(CodeNode::function("foo_v2", "src/a.rs").with_qualified_name("a.foo_v2").with_lines(1, 15));
    loaded.add_edges_batch(vec![
        ("a.foo_v2".to_string(), "b.bar".to_string(), CodeEdge::new(EdgeKind::Calls)),
    ]);

    assert_eq!(loaded.get_functions().len(), 2);
    assert!(loaded.get_node("a.foo").is_none());
    assert!(loaded.get_node("a.foo_v2").is_some());
    assert_eq!(loaded.get_callers("b.bar").len(), 1);
}
```

**Step 3: Run tests**

Run: `cargo test remove_file_entities`
Run: `cargo test delta_patching`
Expected: All pass.

**Step 4: Commit**

```bash
git add repotoire-cli/src/graph/store/mod.rs
git commit -m "feat: add remove_file_entities() for graph delta patching"
```

---

### Task 5: Wire persistent graph cache into analyze pipeline

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/mod.rs` (load cache in `init_graph_db`, save cache after build)
- Modify: `repotoire-cli/src/cli/analyze/graph.rs` (skip full build when cache loaded)

**Step 1: Modify `init_graph_db()` to try loading cache**

Find `init_graph_db()` in `repotoire-cli/src/cli/analyze/mod.rs` (or wherever it's defined — likely in the mod.rs or graph.rs). The function currently does:

```rust
let graph = Arc::new(GraphStore::new(&db_path)?);
```

Change to:

```rust
fn init_graph_db(env: &EnvironmentSetup, file_result: &FileCollectionResult, multi: &MultiProgress) -> Result<Arc<GraphStore>> {
    let db_path = env.repotoire_dir.join("graph_db");
    let cache_path = env.repotoire_dir.join("graph_cache.bin");

    // Try loading persistent graph cache for incremental mode
    if env.config.is_incremental_mode {
        if let Some(mut cached_store) = GraphStore::load_graph_cache(&cache_path) {
            // Delta patch: remove entities for changed files, re-parse will add new ones
            if !file_result.files_to_parse.is_empty() {
                tracing::info!("Delta patching graph: removing {} changed files", file_result.files_to_parse.len());
                cached_store.remove_file_entities(&file_result.files_to_parse);
            }
            // Open redb for save() compatibility
            // (or skip if we only need in-memory for analysis)
            return Ok(Arc::new(cached_store));
        }
    }

    // Cold path: create fresh graph
    let graph = Arc::new(GraphStore::new(&db_path)?);
    Ok(graph)
}
```

**Step 2: Save graph cache after build**

In the main `run()` function in `mod.rs`, after `parse_and_build()` completes (around line 517) and before the detection phase, add:

```rust
// Save graph cache for future incremental runs (background thread)
let cache_path = env.repotoire_dir.join("graph_cache.bin");
let graph_for_cache = Arc::clone(&graph);
let _cache_handle = std::thread::spawn(move || {
    if let Err(e) = graph_for_cache.save_graph_cache(&cache_path) {
        tracing::warn!("Failed to save graph cache: {}", e);
    }
});
```

Also add this in `initialize_graph_overlapped()` after the parse completes.

**Step 3: Build and test**

Run: `cargo check`
Run: `cargo test`
Expected: All pass. Incremental mode now loads cached graph when available.

**Step 4: Manual validation on CPython**

Run two consecutive analyses:
```bash
# Cold run (builds cache)
cargo run --release -- analyze /path/to/cpython --timings

# Incremental run (should load cache)
cargo run --release -- analyze /path/to/cpython --timings --incremental
```

Check that:
- First run creates `graph_cache.bin` in `.repotoire/`
- Second run prints "Loaded graph cache" in debug logs
- init+parse phase is significantly faster on second run

**Step 5: Commit**

```bash
git add repotoire-cli/src/cli/analyze/mod.rs repotoire-cli/src/cli/analyze/graph.rs
git commit -m "feat: wire persistent graph cache into analyze pipeline — load on incremental, save after build"
```

---

### Task 6: Add DetectorContext struct and build in precompute

**Files:**
- Create: `repotoire-cli/src/detectors/detector_context.rs`
- Modify: `repotoire-cli/src/detectors/mod.rs` (add module)
- Modify: `repotoire-cli/src/detectors/engine.rs` (build context in precompute, add to GdPrecomputed)

**Step 1: Create DetectorContext struct**

Create `repotoire-cli/src/detectors/detector_context.rs`:

```rust
//! Shared pre-computed data for detector execution.
//!
//! Built once during `precompute_gd_startup()` and injected into detectors
//! that override `set_detector_context()`. Avoids redundant graph queries
//! and Vec<CodeNode> cloning across 99 detectors.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

/// Shared pre-computed data available to all detectors.
///
/// This is built in parallel with taint analysis and HMM (zero wall-clock cost)
/// and provides zero-copy access to commonly needed graph data.
pub struct DetectorContext {
    /// QN → Vec<caller QN> — avoids Vec<CodeNode> cloning in get_callers()
    pub callers_by_qn: HashMap<String, Vec<String>>,
    /// QN → Vec<callee QN> — avoids Vec<CodeNode> cloning in get_callees()
    pub callees_by_qn: HashMap<String, Vec<String>>,
    /// Parent class QN → Vec<child class QN>
    pub class_children: HashMap<String, Vec<String>>,
    /// Pre-loaded raw file content
    pub file_contents: HashMap<PathBuf, Arc<str>>,
}

impl DetectorContext {
    /// Build the detector context from the graph and source files.
    ///
    /// This reads the call graph, inheritance edges, and file contents.
    /// Designed to run in parallel with other precompute work.
    pub fn build(
        graph: &dyn crate::graph::GraphQuery,
        source_files: &[PathBuf],
    ) -> Self {
        use rayon::prelude::*;

        // Build callers/callees from call maps
        let functions = graph.get_functions();
        let (qn_to_idx, callers_by_idx, callees_by_idx) = graph.build_call_maps_raw();

        let mut callers_by_qn: HashMap<String, Vec<String>> = HashMap::new();
        let mut callees_by_qn: HashMap<String, Vec<String>> = HashMap::new();

        for (&callee_idx, caller_idxs) in &callers_by_idx {
            if let Some(callee_qn) = functions.get(callee_idx).map(|f| f.qualified_name.clone()) {
                let caller_qns: Vec<String> = caller_idxs.iter()
                    .filter_map(|&ci| functions.get(ci).map(|f| f.qualified_name.clone()))
                    .collect();
                callers_by_qn.insert(callee_qn, caller_qns);
            }
        }

        for (&caller_idx, callee_idxs) in &callees_by_idx {
            if let Some(caller_qn) = functions.get(caller_idx).map(|f| f.qualified_name.clone()) {
                let callee_qns: Vec<String> = callee_idxs.iter()
                    .filter_map(|&ci| functions.get(ci).map(|f| f.qualified_name.clone()))
                    .collect();
                callees_by_qn.insert(caller_qn, callee_qns);
            }
        }

        // Build class hierarchy
        let inheritance = graph.get_inheritance();
        let mut class_children: HashMap<String, Vec<String>> = HashMap::new();
        for (child, parent) in &inheritance {
            class_children.entry(parent.clone()).or_default().push(child.clone());
        }

        // Pre-load file contents in parallel
        let file_contents: HashMap<PathBuf, Arc<str>> = source_files
            .par_iter()
            .filter_map(|f| {
                std::fs::read_to_string(f)
                    .ok()
                    .map(|c| (f.clone(), Arc::from(c.as_str())))
            })
            .collect();

        Self {
            callers_by_qn,
            callees_by_qn,
            class_children,
            file_contents,
        }
    }
}
```

**Step 2: Register module**

In `repotoire-cli/src/detectors/mod.rs`, add:
```rust
pub mod detector_context;
pub use detector_context::DetectorContext;
```

**Step 3: Build context in precompute**

In `repotoire-cli/src/detectors/engine.rs`, modify `GdPrecomputed` (line 44):

```rust
pub struct GdPrecomputed {
    pub contexts: Arc<FunctionContextMap>,
    pub hmm_contexts: Arc<HashMap<String, FunctionContext>>,
    pub taint_results: crate::detectors::taint::centralized::CentralizedTaintResults,
    pub detector_context: Arc<DetectorContext>,
}
```

Modify `precompute_gd_startup()` (line 54) to build the context in parallel. Add a fourth thread to the existing `thread::scope`:

```rust
pub fn precompute_gd_startup(
    graph: &dyn crate::graph::GraphQuery,
    repo_path: &std::path::Path,
    hmm_cache_path: Option<&std::path::PathBuf>,
    source_files: &[std::path::PathBuf],  // NEW PARAMETER
) -> GdPrecomputed {
    let (contexts, hmm_contexts, taint_results, detector_context) = std::thread::scope(|s| {
        // Thread 1: Taint analysis
        let taint_handle = s.spawn(|| {
            crate::detectors::taint::centralized::run_centralized_taint(graph, repo_path, None)
        });

        // Thread 2: HMM context extraction
        let hmm_handle = s.spawn(|| {
            build_hmm_contexts_standalone(graph, hmm_cache_path)
        });

        // Thread 3: DetectorContext (callers/callees maps, file contents, class hierarchy)
        let ctx_handle = s.spawn(|| {
            Arc::new(super::DetectorContext::build(graph, source_files))
        });

        // Main thread: Function contexts (adjacency + betweenness + context map)
        let ctx = FunctionContextBuilder::new(graph).build();

        let taint = taint_handle.join().expect("taint thread panicked");
        let hmm = hmm_handle.join().expect("HMM thread panicked");
        let det_ctx = ctx_handle.join().expect("DetectorContext thread panicked");
        (ctx, hmm, taint, det_ctx)
    });

    GdPrecomputed {
        contexts: Arc::new(contexts),
        hmm_contexts: Arc::new(hmm_contexts),
        taint_results,
        detector_context,
    }
}
```

**Step 4: Update all callers of precompute_gd_startup()**

Search for all calls to `precompute_gd_startup()` in `detect.rs` and add the `source_files` parameter. There should be 2-3 call sites:

```rust
// In detect.rs, wherever precompute_gd_startup is called:
crate::detectors::precompute_gd_startup(
    graph.as_ref(),
    &repo_path_clone,
    Some(&hmm_cache_path_clone),
    &source_files.files,  // or &all_files — pass the file list
)
```

**Step 5: Add `set_detector_context()` to Detector trait**

In `repotoire-cli/src/detectors/base.rs`, add a new method to the `Detector` trait (after `set_precomputed_taint`, around line 432):

```rust
    /// Inject shared pre-computed detector context.
    ///
    /// Called by the engine before `detect()` for detectors that benefit from
    /// pre-built callers/callees maps, file content cache, or class hierarchy.
    ///
    /// Default: no-op.
    fn set_detector_context(&self, _ctx: Arc<crate::detectors::DetectorContext>) {
        // Default: no-op
    }
```

**Step 6: Inject context in engine**

In `engine.rs`, in `inject_gd_precomputed()` (line 533), add context injection:

```rust
pub fn inject_gd_precomputed(&mut self, pre: GdPrecomputed) {
    self.function_contexts = Some(pre.contexts);
    self.hmm_contexts = Some(pre.hmm_contexts);

    // Inject detector context into all detectors
    for detector in &self.detectors {
        detector.set_detector_context(Arc::clone(&pre.detector_context));
    }

    // Inject taint results... (existing code)
    self.gd_precomputed = true;
}
```

**Step 7: Build and test**

Run: `cargo check`
Run: `cargo test`
Expected: All pass. DetectorContext is built but no detectors use it yet (no-op default).

**Step 8: Commit**

```bash
git add repotoire-cli/src/detectors/detector_context.rs repotoire-cli/src/detectors/mod.rs \
       repotoire-cli/src/detectors/engine.rs repotoire-cli/src/detectors/base.rs \
       repotoire-cli/src/cli/analyze/detect.rs
git commit -m "feat: add DetectorContext — shared callers/callees maps, file contents, class hierarchy in precompute"
```

---

### Task 7: Migrate ShotgunSurgeryDetector to use DetectorContext

**Files:**
- Modify: `repotoire-cli/src/detectors/shotgun_surgery.rs`

This is the pattern for migrating detectors. ShotgunSurgery is the best first candidate because it heavily uses `graph.get_callers()` with Vec<CodeNode> cloning.

**Step 1: Add OnceLock for context**

At the top of the ShotgunSurgeryDetector struct, add:

```rust
use std::sync::OnceLock;
use std::sync::Arc;
use crate::detectors::DetectorContext;

pub struct ShotgunSurgeryDetector {
    // ... existing fields ...
    detector_context: OnceLock<Arc<DetectorContext>>,
}
```

Initialize in the constructor:
```rust
detector_context: OnceLock::new(),
```

**Step 2: Override `set_detector_context()`**

```rust
fn set_detector_context(&self, ctx: Arc<DetectorContext>) {
    let _ = self.detector_context.set(ctx);
}
```

**Step 3: Use callers_by_qn in detect()**

In the `detect()` method, where it currently does:
```rust
let callers = graph.get_callers(&method.qualified_name);
```

Replace with:
```rust
let callers_qns = self.detector_context.get()
    .and_then(|ctx| ctx.callers_by_qn.get(&method.qualified_name))
    .map(|v| v.as_slice())
    .unwrap_or(&[]);
```

Then adjust the code that uses callers — it previously iterated `Vec<CodeNode>` for file paths and module spread. Now it has `Vec<String>` (qualified names). To get file paths from QNs, use `graph.get_node(qn)` or pre-build a QN→file map from the context.

The key savings come from avoiding the Vec<CodeNode> clone per caller query. The cascade tracing (depth 3) also benefits.

**Step 4: Run tests**

Run: `cargo test shotgun`
Expected: All pass.

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/shotgun_surgery.rs
git commit -m "perf: ShotgunSurgeryDetector uses DetectorContext — eliminate Vec<CodeNode> cloning"
```

---

### Task 8: Migrate UnreachableCodeDetector to use DetectorContext

**Files:**
- Modify: `repotoire-cli/src/detectors/dead_code/mod.rs`

Same pattern as Task 7. Key changes:
- Use `file_contents` from context instead of `std::fs::read_to_string()` in `is_exported_in_source()`
- Use `class_children` from context instead of `graph.get_child_classes()`

**Step 1-5: Same pattern as Task 7**

Follow the OnceLock + set_detector_context() + use context data pattern.

**Commit:**
```bash
git commit -m "perf: UnreachableCodeDetector uses DetectorContext — pre-loaded file contents + class hierarchy"
```

---

### Task 9: Migrate BooleanTrapDetector to use DetectorContext

**Files:**
- Modify: `repotoire-cli/src/detectors/boolean_trap.rs`

Key changes:
- Use `file_contents` from context instead of `crate::cache::global_cache().content()`
- Avoid per-file HashMap<&str, &CodeNode> rebuilds

**Commit:**
```bash
git commit -m "perf: BooleanTrapDetector uses DetectorContext — pre-loaded file contents"
```

---

### Task 10: Migrate RegexInLoopDetector to use DetectorContext

**Files:**
- Modify: `repotoire-cli/src/detectors/regex_in_loop.rs`

Key changes:
- Use `file_contents` instead of per-file line caching with String allocation
- Use `callees_by_qn` instead of `graph.get_callees()` for transitive regex function discovery

**Commit:**
```bash
git commit -m "perf: RegexInLoopDetector uses DetectorContext — pre-loaded contents + callees map"
```

---

### Task 11: Migrate PathTraversalDetector and GodClassDetector

**Files:**
- Modify: `repotoire-cli/src/detectors/path_traversal.rs`
- Modify: `repotoire-cli/src/detectors/god_class.rs`

Same pattern. PathTraversal uses `file_contents`, GodClass uses `class_children`.

**Commit:**
```bash
git commit -m "perf: PathTraversal + GodClass use DetectorContext — file contents + class hierarchy"
```

---

### Task 12: Benchmark and validate

**Files:** None (testing only)

**Step 1: Cold analysis benchmark on CPython**

```bash
# Clean cache first
repotoire clean /path/to/cpython

# Run 3 times, take median
time cargo run --release -- analyze /path/to/cpython --timings
time cargo run --release -- analyze /path/to/cpython --timings
time cargo run --release -- analyze /path/to/cpython --timings
```

Expected: ~5.0-5.3s (was 5.7s)

**Step 2: Incremental benchmark**

```bash
# First run builds cache
cargo run --release -- analyze /path/to/cpython --timings

# Touch a few files to simulate changes
touch /path/to/cpython/Lib/os.py
touch /path/to/cpython/Lib/json/__init__.py

# Incremental run
cargo run --release -- analyze /path/to/cpython --timings --incremental
```

Expected: ~1.5-2.5s (was 5.7s)

**Step 3: Correctness validation**

Compare findings between cold and incremental runs:
```bash
cargo run --release -- analyze /path/to/cpython --format json --output cold.json
# (touch files, run incremental)
cargo run --release -- analyze /path/to/cpython --format json --output incr.json --incremental
diff <(jq '.findings | sort_by(.title)' cold.json) <(jq '.findings | sort_by(.title)' incr.json)
```

Expected: Findings should be identical (or very close — incremental may miss some cross-file findings for unchanged files).

**Step 4: Save timing results**

```bash
# Save to docs/perf/
echo "V3 results" > docs/perf/v3-timings-cpython.txt
# Paste timing output
```

**Step 5: Commit**

```bash
git add docs/perf/
git commit -m "docs: add v3 benchmark results — persistent graph + DetectorContext"
```
