# GD Detector Performance Optimization — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate redundant graph queries and cloning in graph-dependent detectors.

**Architecture:** Two-layer approach — DashMap indexes in GraphStore for O(N)→O(1) file-scoped lookups, plus a CachedGraphQuery wrapper that memoizes expensive full-scan methods. Zero detector code changes required.

**Tech Stack:** Rust, petgraph, DashMap, std::sync::OnceLock

---

## Task 1: Add file→functions and file→classes DashMap indexes to GraphStore

**Files:**
- Modify: `src/graph/store/mod.rs`

**Step 1: Add index fields to GraphStore struct**

After the existing `function_spatial_index` field (line 26), add:

```rust
/// File-scoped function index: file_path → [NodeIndex] for O(1) get_functions_in_file().
file_functions_index: DashMap<String, Vec<NodeIndex>>,
/// File-scoped class index: file_path → [NodeIndex] for O(1) get_classes_in_file().
file_classes_index: DashMap<String, Vec<NodeIndex>>,
```

**Step 2: Initialize in all 3 constructors**

Add `file_functions_index: DashMap::new(),` and `file_classes_index: DashMap::new(),` to `new()` (line ~58), `new_lazy()` (line ~82), and `in_memory()` (line ~96).

**Step 3: Populate in `add_node()`**

After the existing spatial index population block (lines 226-234), add class index population. Also extend the existing function block to populate `file_functions_index`:

```rust
// Existing function spatial index block stays as-is.
// Add file-scoped indexes:
if is_function {
    if let Some(ref fp) = file_path {
        self.file_functions_index
            .entry(fp.clone())
            .or_default()
            .push(idx);
    }
}
let is_class = node.kind == NodeKind::Class;  // capture before move
// ... (capture this BEFORE the node is moved into the graph)
if is_class {
    self.file_classes_index
        .entry(node.file_path.clone())  // need file_path before move
        .or_default()
        .push(idx);
}
```

**Important:** The `is_class` check and `file_path` capture must happen BEFORE `node` is moved into `graph.add_node(node)`. Follow the same pattern as `is_function` / `file_path` which are already captured at line 196-198. Add `is_class` and `class_file_path` captures there:

```rust
let is_class = node.kind == NodeKind::Class;
let class_file_path = if is_class { Some(node.file_path.clone()) } else { None };
```

Then after the spatial index block:

```rust
if is_class {
    if let Some(fp) = class_file_path {
        self.file_classes_index
            .entry(fp)
            .or_default()
            .push(idx);
    }
}
```

**Step 4: Same in `add_nodes_batch()`**

Mirror the same captures and index insertions inside the `for node in nodes` loop (lines 244-276), following the identical pattern.

**Step 5: Clear indexes in `clear()`**

After `self.function_spatial_index.clear();` (line 177), add:

```rust
self.file_functions_index.clear();
self.file_classes_index.clear();
```

**Step 6: Override `get_functions_in_file()` to use the index**

Replace the existing method (lines 355-363):

```rust
pub fn get_functions_in_file(&self, file_path: &str) -> Vec<CodeNode> {
    if let Some(indices) = self.file_functions_index.get(file_path) {
        let graph = self.read_graph();
        indices.value().iter()
            .filter_map(|&idx| graph.node_weight(idx).cloned())
            .collect()
    } else {
        vec![]
    }
}
```

**Step 7: Override `get_classes_in_file()` to use the index**

Replace the existing method (lines 379-387):

```rust
pub fn get_classes_in_file(&self, file_path: &str) -> Vec<CodeNode> {
    if let Some(indices) = self.file_classes_index.get(file_path) {
        let graph = self.read_graph();
        indices.value().iter()
            .filter_map(|&idx| graph.node_weight(idx).cloned())
            .collect()
    } else {
        vec![]
    }
}
```

**Step 8: Verify**

Run: `cargo check`
Expected: Compiles clean (same warnings as before)

Run: `cargo test`
Expected: All 978 tests pass

**Step 9: Commit**

```bash
git add src/graph/store/mod.rs
git commit -m "perf: DashMap indexes for get_functions_in_file/get_classes_in_file — O(N)→O(1)"
```

---

## Task 2: Create CachedGraphQuery wrapper

**Files:**
- Create: `src/graph/cached.rs`
- Modify: `src/graph/mod.rs` (add `pub mod cached;` and re-export)

**Step 1: Create `src/graph/cached.rs`**

```rust
//! Caching wrapper for GraphQuery that memoizes expensive full-scan methods.
//!
//! Wraps a `&dyn GraphQuery` and caches results of methods that scan all nodes
//! or all edges. Indexed/cheap methods delegate directly to the inner query.
//!
//! Used by DetectorEngine to avoid redundant graph scans across multiple
//! detectors in the same analysis run.

use super::store_models::CodeNode;
use super::traits::GraphQuery;
use std::collections::HashMap;
use std::sync::OnceLock;

/// Caching wrapper for GraphQuery.
///
/// Memoizes expensive full-scan methods (get_functions, get_classes, get_calls,
/// get_imports, get_inheritance) on first access. Cheap indexed methods
/// (get_node, get_callers, get_callees, call_fan_in, call_fan_out, etc.)
/// delegate directly to the inner GraphQuery.
pub struct CachedGraphQuery<'a> {
    inner: &'a dyn GraphQuery,
    functions: OnceLock<Vec<CodeNode>>,
    classes: OnceLock<Vec<CodeNode>>,
    files: OnceLock<Vec<CodeNode>>,
    calls: OnceLock<Vec<(String, String)>>,
    imports: OnceLock<Vec<(String, String)>>,
    inheritance: OnceLock<Vec<(String, String)>>,
}

impl<'a> CachedGraphQuery<'a> {
    pub fn new(inner: &'a dyn GraphQuery) -> Self {
        Self {
            inner,
            functions: OnceLock::new(),
            classes: OnceLock::new(),
            files: OnceLock::new(),
            calls: OnceLock::new(),
            imports: OnceLock::new(),
            inheritance: OnceLock::new(),
        }
    }
}

impl GraphQuery for CachedGraphQuery<'_> {
    // === Cached methods (expensive full-scan) ===

    fn get_functions(&self) -> Vec<CodeNode> {
        self.functions
            .get_or_init(|| self.inner.get_functions())
            .clone()
    }

    fn get_classes(&self) -> Vec<CodeNode> {
        self.classes
            .get_or_init(|| self.inner.get_classes())
            .clone()
    }

    fn get_files(&self) -> Vec<CodeNode> {
        self.files
            .get_or_init(|| self.inner.get_files())
            .clone()
    }

    fn get_calls(&self) -> Vec<(String, String)> {
        self.calls
            .get_or_init(|| self.inner.get_calls())
            .clone()
    }

    fn get_imports(&self) -> Vec<(String, String)> {
        self.imports
            .get_or_init(|| self.inner.get_imports())
            .clone()
    }

    fn get_inheritance(&self) -> Vec<(String, String)> {
        self.inheritance
            .get_or_init(|| self.inner.get_inheritance())
            .clone()
    }

    // === Delegated methods (already indexed/cheap) ===

    fn get_functions_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        self.inner.get_functions_in_file(file_path)
    }

    fn get_classes_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        self.inner.get_classes_in_file(file_path)
    }

    fn get_node(&self, qn: &str) -> Option<CodeNode> {
        self.inner.get_node(qn)
    }

    fn get_callers(&self, qn: &str) -> Vec<CodeNode> {
        self.inner.get_callers(qn)
    }

    fn get_callees(&self, qn: &str) -> Vec<CodeNode> {
        self.inner.get_callees(qn)
    }

    fn call_fan_in(&self, qn: &str) -> usize {
        self.inner.call_fan_in(qn)
    }

    fn call_fan_out(&self, qn: &str) -> usize {
        self.inner.call_fan_out(qn)
    }

    fn get_child_classes(&self, qn: &str) -> Vec<CodeNode> {
        self.inner.get_child_classes(qn)
    }

    fn get_importers(&self, qn: &str) -> Vec<CodeNode> {
        self.inner.get_importers(qn)
    }

    fn find_import_cycles(&self) -> Vec<Vec<String>> {
        self.inner.find_import_cycles()
    }

    fn stats(&self) -> HashMap<String, i64> {
        self.inner.stats()
    }

    fn find_function_at(&self, file_path: &str, line: u32) -> Option<CodeNode> {
        self.inner.find_function_at(file_path, line)
    }
}
```

**Step 2: Register module in `src/graph/mod.rs`**

Add after `pub mod traits;` (line 10):

```rust
pub mod cached;
```

Add to the re-exports:

```rust
pub use cached::CachedGraphQuery;
```

**Step 3: Verify**

Run: `cargo check`
Expected: Compiles clean

Run: `cargo test`
Expected: All 978 tests pass

**Step 4: Commit**

```bash
git add src/graph/cached.rs src/graph/mod.rs
git commit -m "perf: add CachedGraphQuery wrapper — memoizes expensive full-scan methods"
```

---

## Task 3: Wire CachedGraphQuery into DetectorEngine

**Files:**
- Modify: `src/detectors/engine.rs`

**Step 1: Add import**

At the top of `engine.rs`, add:

```rust
use crate::graph::CachedGraphQuery;
```

**Step 2: Wrap graph in `run_graph_dependent()`**

In `run_graph_dependent()` (line 728), after the function signature, wrap the graph:

```rust
pub fn run_graph_dependent(
    &mut self,
    graph: &dyn crate::graph::GraphQuery,
    files: &dyn crate::detectors::file_provider::FileProvider,
) -> Result<Vec<Finding>> {
    // Cache expensive graph queries across all detectors in this phase
    let cached = CachedGraphQuery::new(graph);
    let graph: &dyn crate::graph::GraphQuery = &cached;

    // ... rest of the function unchanged, now uses cached graph
```

This shadows the `graph` parameter with the cached wrapper. All downstream code (including `get_or_build_contexts`, `run_single_detector`, taint analysis) now goes through the cache transparently.

**Step 3: Also wrap in `run_graph_independent()` if it uses graph**

Check if `run_graph_independent()` passes graph to detectors. If so, wrap it the same way. (GI detectors declare `requires_graph() = false` but still receive a graph reference in `detect()` — the cache helps if any GI detector opportunistically queries the graph.)

At the start of `run_graph_independent()`:

```rust
let cached = CachedGraphQuery::new(graph);
let graph: &dyn crate::graph::GraphQuery = &cached;
```

**Step 4: Verify**

Run: `cargo check`
Expected: Compiles clean

Run: `cargo test`
Expected: All 978 tests pass

**Step 5: Commit**

```bash
git add src/detectors/engine.rs
git commit -m "perf: wire CachedGraphQuery into DetectorEngine — all detectors use cached graph"
```

---

## Task 4: Add tests for CachedGraphQuery

**Files:**
- Modify: `src/graph/cached.rs` (add `#[cfg(test)] mod tests`)

**Step 1: Add test module**

At the bottom of `cached.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeNode, GraphStore};

    #[test]
    fn test_cached_get_functions_returns_same_data() {
        let store = GraphStore::in_memory();
        store.add_node(CodeNode::function("foo", "app.py").with_qualified_name("mod.foo"));
        store.add_node(CodeNode::function("bar", "app.py").with_qualified_name("mod.bar"));

        let cached = CachedGraphQuery::new(&store);
        let first = cached.get_functions();
        let second = cached.get_functions();

        assert_eq!(first.len(), second.len());
        assert_eq!(first.len(), 2);
    }

    #[test]
    fn test_cached_delegates_indexed_methods() {
        let store = GraphStore::in_memory();
        store.add_node(CodeNode::function("foo", "app.py").with_qualified_name("mod.foo"));

        let cached = CachedGraphQuery::new(&store);
        let node = cached.get_node("mod.foo");
        assert!(node.is_some());
        assert_eq!(node.unwrap().name, "foo");
    }

    #[test]
    fn test_cached_get_calls_returns_same_data() {
        let store = GraphStore::in_memory();
        store.add_node(CodeNode::function("a", "a.py").with_qualified_name("a"));
        store.add_node(CodeNode::function("b", "b.py").with_qualified_name("b"));
        store.add_edge_by_name("a", "b", crate::graph::CodeEdge::calls());

        let cached = CachedGraphQuery::new(&store);
        let first = cached.get_calls();
        let second = cached.get_calls();

        assert_eq!(first.len(), second.len());
        assert_eq!(first.len(), 1);
    }
}
```

**Step 2: Verify**

Run: `cargo test graph::cached`
Expected: All 3 tests pass

**Step 3: Commit**

```bash
git add src/graph/cached.rs
git commit -m "test: add CachedGraphQuery unit tests"
```

---

## Task 5: Add tests for file-scoped indexes

**Files:**
- Modify: `src/graph/store/mod.rs` (add tests to existing test module)

**Step 1: Add tests**

Find the existing `#[cfg(test)] mod tests` block in `store/mod.rs` and add:

```rust
#[test]
fn test_get_functions_in_file_uses_index() {
    let store = GraphStore::in_memory();
    store.add_node(CodeNode::function("foo", "app.py").with_qualified_name("app.foo"));
    store.add_node(CodeNode::function("bar", "app.py").with_qualified_name("app.bar"));
    store.add_node(CodeNode::function("baz", "other.py").with_qualified_name("other.baz"));

    let app_funcs = store.get_functions_in_file("app.py");
    assert_eq!(app_funcs.len(), 2);

    let other_funcs = store.get_functions_in_file("other.py");
    assert_eq!(other_funcs.len(), 1);

    let empty = store.get_functions_in_file("nonexistent.py");
    assert!(empty.is_empty());
}

#[test]
fn test_get_classes_in_file_uses_index() {
    let store = GraphStore::in_memory();
    store.add_node(CodeNode::class("Foo", "app.py").with_qualified_name("app.Foo"));
    store.add_node(CodeNode::class("Bar", "app.py").with_qualified_name("app.Bar"));
    store.add_node(CodeNode::class("Baz", "other.py").with_qualified_name("other.Baz"));

    let app_classes = store.get_classes_in_file("app.py");
    assert_eq!(app_classes.len(), 2);

    let other_classes = store.get_classes_in_file("other.py");
    assert_eq!(other_classes.len(), 1);
}
```

**Step 2: Verify**

Run: `cargo test graph::store -- test_get_functions_in_file test_get_classes_in_file`
Expected: Both tests pass

**Step 3: Commit**

```bash
git add src/graph/store/mod.rs
git commit -m "test: add file-scoped index tests for get_functions_in_file/get_classes_in_file"
```

---

## Task 6: Build release and benchmark

**Files:** None (build + measure only)

**Step 1: Build release**

Run: `cargo build --release`

**Step 2: Benchmark on CPython**

Run the CPython benchmark and capture timing:

```bash
timeout 300 target/release/repotoire analyze /tmp/cpython-bench --no-git --format json --output /dev/null 2>&1
```

Compare total time and per-detector timings against the pre-optimization baseline.

**Step 3: Commit any cleanup**

If benchmark reveals issues, fix and commit.

**Step 4: Final commit message summarizing the optimization**

```bash
git add -A
git commit -m "perf: GD detector optimization — DashMap indexes + CachedGraphQuery"
```
