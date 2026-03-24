# Incremental Analysis Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make incremental CLI analysis fast enough for a pre-commit hook (~1s for a 1-file change on a 300k LOC repo, down from 15s).

**Architecture:** Three layered optimizations: (1) when topology is unchanged after disk load (common case), skip graph rebuild and precompute entirely — use the loaded `CodeGraph` and cached `PrecomputedAnalysis` directly; (2) when topology IS changed, migrate the incremental graph path from legacy `GraphStore` to `GraphBuilder` via `into_builder()` (consuming, zero-copy) so the loaded `CodeGraph` produces a correct graph; (3) wire up incremental detection hints in `detect_stage` so per-file detectors only run on changed files and graph-wide detectors reuse cached findings when topology is unchanged.

**Tech Stack:** Rust, petgraph, rayon, bincode (graph persistence)

---

## Background

### Current state

The `AnalysisEngine` persists state to disk via `save()`/`load()` (session JSON + `graph.bin`). After loading, the engine detects file changes via content hashing and enters the incremental path. Two bugs make this path as slow as cold analysis:

1. **Broken graph after load:** `EngineState.mutable_graph` is `None` after `load()`. The incremental path creates an empty `GraphStore` as fallback (engine/mod.rs:542-551), so the patched graph only contains the changed file's nodes. Git enrich runs on this empty graph (enriching nothing). The loaded `CodeGraph` (which has the full graph) is unused.

2. **Detection ignores incremental hints:** `detect_stage` receives `changed_files`, `cached_file_findings`, `cached_graph_wide_findings`, and `topology_changed` but never reads them (engine/stages/detect.rs:72-182). Every run executes all 73 detectors on all files.

### Architecture context

Three graph types exist:
- **`GraphStore`** (`graph/store/mod.rs`): Legacy, RwLock-protected mutable graph. Used by `build_graph()` and `git_enrich`. Has `remove_file_entities()`, `add_node()`, `add_edges_batch()`.
- **`GraphBuilder`** (`graph/builder.rs`): Modern mutable graph. No locks, `&mut self`. Has `remove_file_entities()`, `add_node()`, `add_edge()`, `get_node_index()` (node lookup by qualified name), `add_nodes_batch()`, `add_edges_batch()`. Produces `CodeGraph` via `freeze()`. Note: `freeze_with_co_change()` exists but is currently `#[cfg(test)]` — must remove that gate for production use.
- **`CodeGraph`** (`graph/frozen.rs`): Immutable, indexed, implements `GraphQuery` (24+ methods). Round-trips via `clone_into_builder() -> GraphBuilder`. Persisted via bincode `save_cache()`/`load_cache()`.

Pipeline: `GraphStore` -> `to_code_graph()` -> `CodeGraph` (cold path) or `CodeGraph` -> `clone_into_builder()` -> `GraphBuilder` -> `freeze()` -> `CodeGraph` (round-trip).

### Detector scopes

Each detector declares a `detector_scope()`:
- **`FileLocal`**: Only reads file content. No graph queries. Safe to scope to changed files always.
- **`FileScopedGraph`**: Uses graph but findings are per-file. Can scope to changed files when topology is unchanged.
- **`GraphWide`**: Needs full graph topology (SCC, PageRank, etc.). Must re-run when topology changes, can reuse cache when stable.

### Key files

| File | Role |
|------|------|
| `engine/mod.rs` | `AnalysisEngine`: `analyze()`, `analyze_cold()`, `analyze_incremental()`, `save()`, `load()` |
| `engine/state.rs` | `EngineState` struct (13 fields: graph, hashes, findings cache, etc.) |
| `engine/stages/detect.rs` | `detect_stage()` — detector orchestration, currently ignores incremental hints |
| `engine/stages/graph.rs` | `graph_stage()`, `graph_patch_stage()`, `freeze_graph()` — graph build/patch/freeze |
| `graph/builder.rs` | `GraphBuilder` — modern mutable graph with `freeze()`, `remove_file_entities()` |
| `graph/frozen.rs` | `CodeGraph` — immutable indexed graph with `clone_into_builder()`, `save_cache()`/`load_cache()` |
| `graph/store/mod.rs` | `GraphStore` — legacy mutable graph (RwLock-based) |
| `cli/analyze/graph.rs` | `build_graph()` — populates `GraphStore` from parse results |
| `detectors/engine.rs` | `precompute_gd_startup()` — builds taint, HMM, contexts (~3.9s) |
| `detectors/runner.rs` | `run_detectors()` — parallel detector execution via rayon |
| `detectors/base.rs` | `Detector` trait, `DetectorScope` enum |
| `detectors/analysis_context.rs` | `AnalysisContext` struct passed to all detectors |
| `detectors/file_index.rs` | `FileIndex` — pre-indexed file collection that file-scoped detectors iterate over |

---

## File Structure

### New files
- `engine/stages/graph_builder_patch.rs` — `GraphBuilder`-based graph patching for incremental analysis after disk load

### Modified files
- `engine/mod.rs` — update `analyze_incremental()` to use `GraphBuilder` path when `mutable_graph` is None
- `engine/stages/mod.rs` — add `pub mod graph_builder_patch`
- `engine/stages/detect.rs` — implement incremental detection logic using hints
- `engine/stages/graph.rs` — add `freeze_builder()` helper that takes `GraphBuilder` instead of `GraphStore`

### Test files
- `engine/stages/graph_builder_patch.rs` — inline `#[test]` module
- `engine/stages/detect.rs` — inline `#[test]` additions
- `engine/mod.rs` — extend existing round-trip tests

---

## Task 1: GraphBuilder-based graph patching

**Goal:** Create a function that patches a `GraphBuilder` with new parse results, replacing the broken `GraphStore` fallback path.

**Files:**
- Create: `repotoire-cli/src/engine/stages/graph_builder_patch.rs`
- Modify: `repotoire-cli/src/engine/stages/mod.rs`
- Modify: `repotoire-cli/src/engine/stages/graph.rs`

- [ ] **Step 1: Write failing test for builder patching**

In `graph_builder_patch.rs`, write a test that:
1. Creates a `GraphBuilder` with nodes from two files (a.py, b.py)
2. Freezes it to `CodeGraph`
3. Round-trips via `clone_into_builder()`
4. Calls `patch_builder()` with changed_files=[a.py], removed_files=[], new parse results for a.py with modified content
5. Freezes again
6. Asserts: b.py nodes unchanged, a.py nodes updated, edge fingerprint may differ

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::builder::GraphBuilder;
    use crate::graph::{CodeNode, CodeEdge};
    use std::path::PathBuf;
    use std::sync::Arc;

    #[test]
    fn test_patch_builder_replaces_changed_file() {
        // Build initial graph with two files
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("foo", "a.py")
            .with_qualified_name("a.foo").with_lines(1, 10));
        let f2 = builder.add_node(CodeNode::function("bar", "b.py")
            .with_qualified_name("b.bar").with_lines(1, 10));
        builder.add_edge(f1, f2, CodeEdge::calls());

        // Freeze and round-trip
        let graph = builder.freeze();
        assert_eq!(graph.functions().len(), 2);
        let mut builder2 = graph.clone_into_builder();

        // Simulate: a.py changed, now has foo_v2 instead of foo
        let mut parse_result = crate::parsers::ParseResult::default();
        parse_result.functions.push(crate::models::Function {
            name: "foo_v2".to_string(),
            qualified_name: "a.foo_v2".to_string(),
            file_path: std::path::PathBuf::from("a.py"),
            line_start: 1,
            line_end: 15,
            parameters: Vec::new(),
            return_type: None,
            is_async: false,
            complexity: None,
            max_nesting: None,
            doc_comment: None,
            annotations: Vec::new(),
        });

        // Patch
        patch_builder(
            &mut builder2,
            &[PathBuf::from("a.py")],
            &[],
            &[(PathBuf::from("a.py"), Arc::new(parse_result))],
        );

        // Freeze and verify
        let graph2 = builder2.freeze();
        let funcs = graph2.functions();
        assert_eq!(funcs.len(), 2); // foo_v2 + bar
        // b.py's bar is still there
        assert!(funcs.iter().any(|n| n.name == "bar"));
        // a.py's foo is replaced by foo_v2
        assert!(funcs.iter().any(|n| n.name == "foo_v2"));
        assert!(!funcs.iter().any(|n| n.name == "foo"));
    }

    #[test]
    fn test_patch_builder_handles_removed_file() {
        let mut builder = GraphBuilder::new();
        builder.add_node(CodeNode::function("foo", "a.py")
            .with_qualified_name("a.foo").with_lines(1, 10));
        builder.add_node(CodeNode::function("bar", "b.py")
            .with_qualified_name("b.bar").with_lines(1, 10));

        let graph = builder.freeze();
        let mut builder2 = graph.clone_into_builder();

        patch_builder(
            &mut builder2,
            &[],
            &[PathBuf::from("a.py")], // removed
            &[],
        );

        let graph2 = builder2.freeze();
        assert_eq!(graph2.functions().len(), 1);
        assert!(graph2.functions().iter().any(|n| n.name == "bar"));
    }

    #[test]
    fn test_patch_builder_no_changes_is_identity() {
        let mut builder = GraphBuilder::new();
        builder.add_node(CodeNode::function("foo", "a.py")
            .with_qualified_name("a.foo").with_lines(1, 10));
        let graph = builder.freeze();
        let node_count = graph.node_count();

        let mut builder2 = graph.clone_into_builder();
        patch_builder(&mut builder2, &[], &[], &[]);
        let graph2 = builder2.freeze();

        assert_eq!(graph2.node_count(), node_count);
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd repotoire-cli && cargo test engine::stages::graph_builder_patch::tests -v`
Expected: FAIL — module and function don't exist yet

- [ ] **Step 3: Write `patch_builder()` implementation**

```rust
//! GraphBuilder-based incremental graph patching.
//!
//! Used when the engine loads a persisted session from disk and needs to
//! patch the graph for changed files. The loaded `CodeGraph` is round-tripped
//! via `clone_into_builder()`, patched here, then re-frozen.

use crate::graph::builder::GraphBuilder;
use crate::graph::{CodeEdge, CodeNode};
use crate::parsers::ParseResult;
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Patch a `GraphBuilder` with incremental file changes.
///
/// Steps:
/// 1. Remove all nodes/edges for changed + removed files
/// 2. Re-insert nodes/edges from fresh parse results for changed files
///
/// This is the `GraphBuilder` equivalent of `graph_patch_stage()` (which uses `GraphStore`).
pub fn patch_builder(
    builder: &mut GraphBuilder,
    changed_files: &[PathBuf],
    removed_files: &[PathBuf],
    new_parse_results: &[(PathBuf, Arc<ParseResult>)],
) {
    // Step 1: Remove old entities for changed + removed files
    let files_to_remove: Vec<PathBuf> = changed_files
        .iter()
        .chain(removed_files.iter())
        .cloned()
        .collect();

    if !files_to_remove.is_empty() {
        builder.remove_file_entities(&files_to_remove);
    }

    // Step 2: Re-insert from fresh parse results
    for (file_path, parse_result) in new_parse_results {
        let file_str = file_path.to_string_lossy();

        // Add file node
        builder.add_node(
            CodeNode::file(&file_str).with_qualified_name(&file_str),
        );

        // Add function nodes
        for func in &parse_result.functions {
            let qn = if func.qualified_name.is_empty() {
                format!("{}.{}", file_str, func.name)
            } else {
                func.qualified_name.clone()
            };
            let mut node = CodeNode::function(&func.name, &file_str)
                .with_qualified_name(&qn)
                .with_lines(func.line_start, func.line_end);
            node.complexity = func.complexity.unwrap_or(0) as u16;
            node.max_nesting = func.max_nesting.unwrap_or(0) as u8;
            node.param_count = func.parameters.len() as u8;
            let func_idx = builder.add_node(node);

            // Contains edge: file -> function
            if let Some(file_idx) = builder.get_node_index(&file_str) {
                builder.add_edge(file_idx, func_idx, CodeEdge::contains());
            }
        }

        // Add class nodes
        for class in &parse_result.classes {
            let qn = if class.qualified_name.is_empty() {
                format!("{}.{}", file_str, class.name)
            } else {
                class.qualified_name.clone()
            };
            let node = CodeNode::class(&class.name, &file_str)
                .with_qualified_name(&qn)
                .with_lines(class.line_start, class.line_end);
            let class_idx = builder.add_node(node);

            // Contains edge: file -> class
            if let Some(file_idx) = builder.get_node_index(&file_str) {
                builder.add_edge(file_idx, class_idx, CodeEdge::contains());
            }

            // Note: class.methods is Vec<String> (method names), not full method structs.
            // Method nodes are in parse_result.functions with qualified names like "Class.method".
            // They get added in the function loop above.
        }

        // Add import edges
        for import in &parse_result.imports {
            // Import edges are best-effort — target may not exist yet
            if let Some(src_idx) = builder.get_node_index(&*file_str) {
                let target = &import.path;
                if let Some(tgt_idx) = builder.get_node_index(target) {
                    builder.add_edge(src_idx, tgt_idx, CodeEdge::imports());
                }
            }
        }

        // Add call edges from parse_result.calls: Vec<(caller_qn, callee_qn)>
        for (caller_qn, callee_qn) in &parse_result.calls {
            if let Some(caller_idx) = builder.get_node_index(caller_qn) {
                if let Some(callee_idx) = builder.get_node_index(callee_qn) {
                    builder.add_edge(caller_idx, callee_idx, CodeEdge::calls());
                }
            }
        }
    }
}
```

**Implementation notes:**
- Uses `get_node_index()` (the actual `GraphBuilder` API) instead of `find_node()`.
- `CodeNode` fields: `complexity: u16`, `max_nesting: u8`, `param_count: u8` (not i64).
- `Function` struct: `line_start`/`line_end` (not `start_line`/`end_line`), `complexity: Option<u32>`, `max_nesting: Option<u32>`.
- `Class.methods` is `Vec<String>` (names only) — method nodes are in `parse_result.functions`.
- `ImportInfo.path` (not `module_path`).
- `ParseResult.calls` is `Vec<(String, String)>` — `(caller_qn, callee_qn)` pairs.
- This is intentionally simpler than the full `build_graph()` in `cli/analyze/graph.rs`. It doesn't do cross-file call resolution or value store propagation. For a 1-file incremental patch, the existing cross-file edges from unchanged files remain intact.

- [ ] **Step 4: Register the module**

In `engine/stages/mod.rs`, add:
```rust
pub mod graph_builder_patch;
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd repotoire-cli && cargo test engine::stages::graph_builder_patch::tests -v`
Expected: All 3 tests PASS

- [ ] **Step 6: Remove `#[cfg(test)]` from `freeze_with_co_change()`**

In `graph/builder.rs`, remove the `#[cfg(test)]` gate from `freeze_with_co_change()` (line 530) so it can be used in production:

```rust
// BEFORE:
    #[cfg(test)]
    pub fn freeze_with_co_change(self, co_change: &crate::git::co_change::CoChangeMatrix) -> CodeGraph {

// AFTER:
    pub fn freeze_with_co_change(self, co_change: &crate::git::co_change::CoChangeMatrix) -> CodeGraph {
```

- [ ] **Step 7: Add `freeze_builder()` helper to `engine/stages/graph.rs`**

Add a freeze function that takes `GraphBuilder` instead of `&GraphStore`:

```rust
/// Freeze a `GraphBuilder` into an immutable `CodeGraph` with pre-built indexes.
///
/// Equivalent to `freeze_graph()` but for the `GraphBuilder` path (used after
/// incremental patching via `graph_builder_patch::patch_builder()`).
pub fn freeze_builder(
    builder: GraphBuilder,
    value_store: Option<Arc<ValueStore>>,
    co_change: Option<&CoChangeMatrix>,
) -> FrozenGraphOutput {
    let code_graph = if let Some(cc) = co_change {
        builder.freeze_with_co_change(cc)
    } else {
        builder.freeze()
    };
    let edge_fingerprint = code_graph.edge_fingerprint();

    FrozenGraphOutput {
        graph: Arc::new(code_graph),
        value_store,
        edge_fingerprint,
    }
}
```

- [ ] **Step 8: Run `cargo check`**

Run: `cd repotoire-cli && cargo check`
Expected: Compiles with no new errors

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: add GraphBuilder-based graph patching for incremental analysis

Adds patch_builder() in engine/stages/graph_builder_patch.rs and
freeze_builder() in engine/stages/graph.rs. These enable the incremental
path to patch a loaded CodeGraph via clone_into_builder() instead of
falling back to an empty GraphStore."
```

---

## Task 2: Wire GraphBuilder path into analyze_incremental

**Goal:** When `mutable_graph` is `None` (after disk load), use `clone_into_builder()` + `patch_builder()` + `freeze_builder()` instead of the broken empty-GraphStore fallback.

**Files:**
- Modify: `repotoire-cli/src/engine/mod.rs` (lines 511-716, `analyze_incremental()`)

- [ ] **Step 1: Write failing test for incremental-after-load**

Add to the existing test module in `engine/mod.rs`:

```rust
#[test]
fn test_incremental_after_load_uses_full_graph() {
    // Setup: create a temp dir with two source files
    let dir = tempfile::tempdir().unwrap();
    let a_path = dir.path().join("a.py");
    let b_path = dir.path().join("b.py");
    std::fs::write(&a_path, "def foo():\n    pass\n").unwrap();
    std::fs::write(&b_path, "def bar():\n    pass\n").unwrap();

    // Cold analysis
    let config = AnalysisConfig::default();
    let mut engine = AnalysisEngine::new(dir.path()).unwrap();
    let result = engine.analyze(&config).unwrap();
    assert!(matches!(result.stats.mode, AnalysisMode::Cold));
    let cold_files = result.stats.files_analyzed;
    assert!(cold_files >= 2);

    // Save session
    let session_dir = tempfile::tempdir().unwrap();
    engine.save(session_dir.path()).unwrap();

    // Modify a.py
    std::fs::write(&a_path, "def foo_v2():\n    return 42\n").unwrap();

    // Load and re-analyze
    let mut engine2 = AnalysisEngine::load(session_dir.path(), dir.path()).unwrap();
    let result2 = engine2.analyze(&config).unwrap();

    // Should be incremental, not cold
    assert!(matches!(result2.stats.mode, AnalysisMode::Incremental { .. }));
    // Should analyze the same number of files (full graph, not just changed file)
    assert_eq!(result2.stats.files_analyzed, cold_files);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd repotoire-cli && cargo test test_incremental_after_load_uses_full_graph -v`
Expected: FAIL — incremental path uses empty graph, stats differ

- [ ] **Step 3: Update `analyze_incremental()` to use GraphBuilder path**

Replace the `mutable_graph` fallback block (lines 540-562) in `analyze_incremental()`:

**Current code (lines 540-562):**
```rust
// Stage 3: Graph — patch existing mutable graph with delta.
// If no mutable_graph is cached (e.g., after load from disk), rebuild from scratch.
let mutable_graph = prev_state.mutable_graph.unwrap_or_else(|| {
    let store = crate::graph::GraphStore::in_memory();
    Arc::new(store)
});

let graph_out = timed(&mut timings, "graph", || {
    graph::graph_patch_stage(&graph::GraphPatchInput {
        mutable_graph,
        changed_files: &changes.changed,
        removed_files: &changes.removed,
        new_parse_results: &parse_out.results,
        repo_path: &self.repo_path,
    })
})?;
```

**Replace with three paths based on graph state:**

The key performance insight: **when topology is unchanged (edge fingerprint matches), we don't need to rebuild the graph or recompute primitives at all.** The loaded `CodeGraph` is still valid. We only need to re-run per-file detectors on changed files.

```rust
// Stage 3: Graph — three paths depending on state.
//
// Path A (in-process): mutable GraphStore available → patch + git enrich + freeze
// Path B (after-load, topology changed): consume loaded CodeGraph via into_builder() → patch + freeze
// Path C (after-load, topology unchanged): SKIP graph rebuild entirely — reuse loaded CodeGraph
//
// Topology change is detected AFTER freeze by comparing edge_fingerprint
// (new frozen graph) vs prev_state.edge_fingerprint (saved from last run).
// This means freeze always runs, even when topology is unchanged.
// A future optimization could detect topology change before freeze by
// comparing import/export edges from parse results only.

let (frozen, file_churn, co_change, mutable_for_state) = if let Some(mutable_graph) = prev_state.mutable_graph {
    // ── Path A: In-process incremental (mutable GraphStore available) ──
    let graph_out = timed(&mut timings, "graph", || {
        graph::graph_patch_stage(&graph::GraphPatchInput {
            mutable_graph,
            changed_files: &changes.changed,
            removed_files: &changes.removed,
            new_parse_results: &parse_out.results,
            repo_path: &self.repo_path,
        })
    })?;

    let git_out = if !config.no_git {
        timed(&mut timings, "git_enrich", || {
            git_enrich::git_enrich_stage(&git_enrich::GitEnrichInput {
                repo_path: &self.repo_path,
                graph: &graph_out.mutable_graph,
                co_change_config: self.project_config.co_change.to_runtime(),
            })
        })?
    } else {
        git_enrich::GitEnrichOutput::skipped()
    };

    let file_churn = Arc::new(git_out.file_churn);
    let co_change = if config.no_git { prev_co_change.take() } else { Some(git_out.co_change_matrix) };

    let frozen = timed(&mut timings, "freeze", || {
        graph::freeze_graph(
            &graph_out.mutable_graph,
            graph_out.value_store,
            co_change.as_ref(),
        )
    });

    (frozen, file_churn, co_change, Some(graph_out.mutable_graph))
} else {
    // After-load: check if we can use the fast path (topology unchanged).
    // Build the patched graph to get an edge fingerprint, then compare.
    //
    // Use into_builder() (consuming, zero-copy) via Arc::try_unwrap.
    // If Arc has other refs (shouldn't after take()), fall back to clone.
    let prev_graph = prev_state.graph; // Arc<CodeGraph>
    let prev_fingerprint = prev_state.edge_fingerprint;

    let mut builder = timed(&mut timings, "graph", || {
        let mut b = match Arc::try_unwrap(prev_graph) {
            Ok(graph) => graph.into_builder(),      // zero-copy
            Err(arc) => arc.clone_into_builder(),    // fallback clone
        };
        let rel_changed: Vec<PathBuf> = changes.changed.iter()
            .filter_map(|p| p.strip_prefix(&self.repo_path).ok().map(|r| r.to_path_buf()))
            .collect();
        let rel_removed: Vec<PathBuf> = changes.removed.iter()
            .filter_map(|p| p.strip_prefix(&self.repo_path).ok().map(|r| r.to_path_buf()))
            .collect();
        graph_builder_patch::patch_builder(
            &mut b,
            &rel_changed,
            &rel_removed,
            &parse_out.results,
        );
        b
    });

    // Compute file churn (lightweight, ~50ms) for detectors that need it.
    let file_churn = Arc::new(if !config.no_git {
        timed(&mut timings, "git_enrich", || {
            git_enrich::compute_file_churn(&self.repo_path)
        })
    } else {
        std::collections::HashMap::new()
    });

    let co_change = prev_co_change.take();

    let frozen = timed(&mut timings, "freeze", || {
        graph::freeze_builder(
            builder,
            None,
            co_change.as_ref(),
        )
    });

    (frozen, file_churn, co_change, None)
};
```

Then update the rest of `analyze_incremental()`:
- Remove the old git_enrich block (lines 564-575) and freeze block (lines 580-587) — now inside the branches above.
- The detect/postprocess/score stages remain outside, using `frozen.graph`, `frozen.edge_fingerprint`.
- State caching: set `mutable_graph: mutable_for_state`.
- `prev_co_change` is consumed inside the branches via `.take()`, so remove any later references to it. **Important:** the existing declaration `let prev_co_change = prev_state.co_change;` (line 526) must change to `let mut prev_co_change = prev_state.co_change;` since `Option::take()` requires `&mut self`.

**Performance note on `into_builder()`:** `Arc::try_unwrap()` succeeds when refcount is 1 (which it should be after `prev_state.take()`). This gives us zero-copy conversion — the `StableGraph` is moved, not cloned. ~0ms instead of ~5ms.

**Performance note on freeze:** `freeze()` still recomputes all GraphPrimitives (PageRank, betweenness, etc.) which takes 2-4s. This is unavoidable when the graph is patched. However, the detect stage optimization (Task 3) means graph-wide detectors reuse cached findings when topology is unchanged — so even if primitives are recomputed, the expensive detectors don't re-run.

**Follow-up optimization (not in this plan):** Skip primitives computation in `freeze()` when edge fingerprint matches previous. This would require passing the old primitives to `GraphIndexes::build()`. Would save 2-4s but requires changes to the graph layer.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd repotoire-cli && cargo test test_incremental_after_load_uses_full_graph -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd repotoire-cli && cargo test`
Expected: All tests pass (1650+)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "fix: use GraphBuilder path for incremental analysis after disk load

When mutable_graph is None (after load from disk), round-trip the loaded
CodeGraph via clone_into_builder() + patch_builder() + freeze_builder()
instead of creating an empty GraphStore. This ensures the incremental
path has a complete graph with all nodes from all files."
```

---

## Task 3: Incremental detection in detect_stage

**Goal:** Wire up the incremental hints so per-file detectors only run on changed files and graph-wide detectors reuse cached findings when topology is unchanged.

**Files:**
- Modify: `repotoire-cli/src/engine/stages/detect.rs`

- [ ] **Step 1: Write failing test for incremental detection**

Add to `detect.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::detectors::base::DetectorScope;

    /// Verify that unchanged file findings are carried forward, not re-computed.
    #[test]
    fn test_incremental_detection_carries_forward_unchanged() {
        // This test verifies the contract: when changed_files is Some,
        // cached_file_findings for unchanged files appear in the output
        // without re-running detectors on those files.
        //
        // We test this by checking that cached findings for file "b.py"
        // appear in the output when only "a.py" is in changed_files.

        // Build a minimal graph with two files
        let mut builder = crate::graph::builder::GraphBuilder::new();
        builder.add_node(crate::graph::CodeNode::function("foo", "a.py")
            .with_qualified_name("a.foo").with_lines(1, 10));
        builder.add_node(crate::graph::CodeNode::function("bar", "b.py")
            .with_qualified_name("b.bar").with_lines(1, 10));
        let graph = builder.freeze();

        // Create cached findings for b.py
        let cached_finding = crate::models::Finding {
            id: "cached-123".to_string(),
            detector: "TestDetector".to_string(),
            severity: crate::models::Severity::Medium,
            title: "Cached finding for b.py".to_string(),
            description: String::new(),
            affected_files: vec![std::path::PathBuf::from("b.py")],
            line_start: Some(1),
            line_end: Some(10),
            ..Default::default()
        };
        let mut cached_file_findings = HashMap::new();
        cached_file_findings.insert(
            std::path::PathBuf::from("b.py"),
            vec![cached_finding.clone()],
        );

        let input = DetectInput {
            graph: &graph,
            source_files: &[
                std::path::PathBuf::from("a.py"),
                std::path::PathBuf::from("b.py"),
            ],
            repo_path: std::path::Path::new("/tmp/test"),
            project_config: &crate::config::ProjectConfig::default(),
            style_profile: None,
            ngram_model: None,
            value_store: None,
            skip_detectors: &[],
            workers: 1,
            progress: None,
            file_churn: Arc::new(HashMap::new()),
            all_detectors: false,
            // Incremental hints
            changed_files: Some(&[std::path::PathBuf::from("a.py")]),
            topology_changed: false,
            cached_gd_precomputed: None,
            cached_file_findings: Some(&cached_file_findings),
            cached_graph_wide_findings: Some(&HashMap::new()),
        };

        let output = detect_stage(&input).unwrap();

        // The cached finding for b.py should appear in the output
        assert!(
            output.findings.iter().any(|f| f.id == "cached-123"),
            "Cached finding for unchanged file b.py should be carried forward"
        );
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd repotoire-cli && cargo test engine::stages::detect::tests -v`
Expected: FAIL — cached finding not in output (detect_stage ignores hints)

- [ ] **Step 3: Implement incremental detection logic**

Replace the body of `detect_stage()` with logic that checks for incremental hints. The key insight: when `changed_files` is `Some`, we split the work:

```rust
pub fn detect_stage(input: &DetectInput) -> Result<DetectOutput> {
    let skip_set: HashSet<&str> = input.skip_detectors.iter().map(|s| s.as_str()).collect();

    let resolver = build_threshold_resolver(input.style_profile);

    let init = DetectorInit {
        repo_path: input.repo_path,
        project_config: input.project_config,
        resolver: resolver.clone(),
        ngram_model: input.ngram_model,
    };

    let detectors: Vec<Arc<dyn crate::detectors::Detector>> = if input.all_detectors {
        create_all_detectors(&init)
    } else {
        create_default_detectors(&init)
    }
        .into_iter()
        .filter(|d| !skip_set.contains(d.name()))
        .collect();

    let detectors_run = detectors.len();
    let detectors_skipped = skip_set.len();
    let graph = input.graph;

    // ── Incremental fast path ──────────────────────────────────────────────
    //
    // When changed_files is provided, we avoid re-running detectors on
    // unchanged files. Per-file detectors run only on changed files;
    // graph-wide detectors reuse cached findings when topology is stable.
    if let (
        Some(changed_files),
        Some(cached_file_findings),
        Some(cached_graph_wide_findings),
    ) = (
        input.changed_files,
        input.cached_file_findings,
        input.cached_graph_wide_findings,
    ) {
        return detect_stage_incremental(
            input,
            &detectors,
            changed_files,
            cached_file_findings,
            cached_graph_wide_findings,
            &resolver,
        );
    }

    // ── Cold path (unchanged) ──────────────────────────────────────────────
    let hmm_cache_path = input.repo_path.join(".repotoire");
    let vs_clone = input.value_store.cloned();

    let precompute_start = Instant::now();
    let mut precomputed = precompute_gd_startup(
        graph,
        input.repo_path,
        Some(&hmm_cache_path),
        input.source_files,
        vs_clone,
        &detectors,
    );
    let precompute_duration = precompute_start.elapsed();

    inject_taint_precomputed(&detectors, &precomputed.taint_results);
    precomputed.git_churn = Arc::clone(&input.file_churn);

    let ctx = precomputed.to_context(graph, &resolver);

    let (mut findings, bypass_set) = run_detectors(&detectors, &ctx, input.workers);

    let total_findings = findings.len();
    findings = apply_hmm_context_filter(findings, &ctx);
    filter_test_file_findings(&mut findings);
    sort_findings_deterministic(&mut findings);

    // Build scope lookup
    let scope_map: HashMap<String, DetectorScope> = detectors
        .iter()
        .map(|d| (d.name().to_string(), d.detector_scope()))
        .collect();

    let mut findings_by_file: HashMap<PathBuf, Vec<Finding>> = HashMap::new();
    let mut graph_wide_findings: HashMap<String, Vec<Finding>> = HashMap::new();

    for finding in &findings {
        let scope = scope_map
            .get(&finding.detector)
            .copied()
            .unwrap_or(DetectorScope::FileScopedGraph);
        if scope == DetectorScope::GraphWide {
            graph_wide_findings
                .entry(finding.detector.clone())
                .or_default()
                .push(finding.clone());
        } else {
            for file in &finding.affected_files {
                findings_by_file
                    .entry(file.clone())
                    .or_default()
                    .push(finding.clone());
            }
        }
    }

    Ok(DetectOutput {
        findings,
        precomputed,
        findings_by_file,
        graph_wide_findings,
        bypass_set,
        stats: DetectStats {
            detectors_run,
            detectors_skipped,
            gi_findings: 0,
            gd_findings: total_findings,
            precompute_duration,
        },
    })
}

/// Incremental detection: only re-run detectors where needed.
fn detect_stage_incremental(
    input: &DetectInput,
    detectors: &[Arc<dyn crate::detectors::Detector>],
    changed_files: &[PathBuf],
    cached_file_findings: &HashMap<PathBuf, Vec<Finding>>,
    cached_graph_wide_findings: &HashMap<String, Vec<Finding>>,
    resolver: &crate::calibrate::ThresholdResolver,
) -> Result<DetectOutput> {
    let graph = input.graph;
    let changed_set: HashSet<&PathBuf> = changed_files.iter().collect();

    // Partition detectors by scope
    let mut file_local: Vec<Arc<dyn crate::detectors::Detector>> = Vec::new();
    let mut file_scoped_graph: Vec<Arc<dyn crate::detectors::Detector>> = Vec::new();
    let mut graph_wide: Vec<Arc<dyn crate::detectors::Detector>> = Vec::new();

    for d in detectors {
        match d.detector_scope() {
            DetectorScope::FileLocal => file_local.push(Arc::clone(d)),
            DetectorScope::FileScopedGraph => file_scoped_graph.push(Arc::clone(d)),
            DetectorScope::GraphWide => graph_wide.push(Arc::clone(d)),
        }
    }

    // Precompute shared data.
    // When topology is unchanged AND we have cached precomputed data, REUSE it.
    // This saves ~3.9s (taint 1.5s, HMM 0.4s, function contexts 1.5s, etc.)
    let precompute_start = Instant::now();
    let precomputed = if !input.topology_changed && input.cached_gd_precomputed.is_some() {
        // Fast path: reuse most of cached PrecomputedAnalysis.
        //
        // We reuse: HMM contexts (~0.4s), function contexts (~1.5s),
        // reachability, module metrics, class cohesion, decorator index.
        //
        // We MUST re-run taint analysis (~1.5s) because changed files may
        // have added new sinks/sources (e.g., a new SQL query). Taint traces
        // data flow across functions — stale taint would miss new vulnerabilities.
        //
        // Net savings: ~2.4s (skip HMM + function contexts + enrichment threads)
        let cached = input.cached_gd_precomputed.unwrap();
        let mut reused = cached.clone(); // cheap: all Arc bumps
        reused.git_churn = Arc::clone(&input.file_churn);

        // Re-run taint on full source file list (changed files have new content)
        let needs_taint = detectors.iter().any(|d| d.taint_category().is_some());
        if needs_taint {
            let taint = crate::detectors::taint::centralized::run_centralized_taint(
                graph,
                input.repo_path,
                None, // file_cache — let taint read from disk/global cache
            );
            reused.taint_results = Arc::new(taint);
        }

        // Rebuild file index ONLY for changed files (merge with cached).
        // The cached file index has correct content for unchanged files;
        // we only need to update entries for changed files.
        let changed_set: std::collections::HashSet<&PathBuf> = changed_files.iter().collect();
        let mut file_data: Vec<_> = reused.file_index
            .all()
            .iter()
            .filter(|entry| !changed_set.contains(&entry.path))
            .map(|entry| (entry.path.clone(), Arc::clone(&entry.content), entry.flags))
            .collect();
        // Add fresh content for changed files
        for p in changed_files {
            if let Some(content_string) = crate::cache::global_cache().content(p) {
                let content: Arc<str> = Arc::from(content_string.as_str());
                let flags = crate::detectors::detector_context::compute_content_flags(&content);
                file_data.push((p.clone(), content, flags));
            }
        }
        reused.file_index = Arc::new(crate::detectors::file_index::FileIndex::new(file_data));

        inject_taint_precomputed(detectors, &reused.taint_results);
        reused
    } else {
        // Slow path: full precompute (topology changed or no cache)
        let hmm_cache_path = input.repo_path.join(".repotoire");
        let vs_clone = input.value_store.cloned();
        let mut precomputed = precompute_gd_startup(
            graph,
            input.repo_path,
            Some(&hmm_cache_path),
            input.source_files,
            vs_clone,
            detectors,
        );
        inject_taint_precomputed(detectors, &precomputed.taint_results);
        precomputed.git_churn = Arc::clone(&input.file_churn);
        precomputed
    };
    let precompute_duration = precompute_start.elapsed();

    // Build context scoped to CHANGED files only (for file-scoped detectors)
    let scoped_ctx = precomputed.to_context_scoped(graph, resolver, changed_files);
    // Full context (for graph-wide detectors if needed)
    let full_ctx = precomputed.to_context(graph, resolver);

    let mut all_findings: Vec<Finding> = Vec::new();
    let mut findings_by_file: HashMap<PathBuf, Vec<Finding>> = HashMap::new();
    let mut graph_wide_findings_out: HashMap<String, Vec<Finding>> = HashMap::new();

    // Build bypass_set from ALL detectors (not just those that run).
    // Cached findings may come from detectors that bypass the GBDT postprocessor;
    // their names must be in bypass_set so postprocess doesn't filter them.
    let bypass_set: HashSet<String> = detectors
        .iter()
        .filter(|d| d.bypass_postprocessor())
        .map(|d| d.name().to_string())
        .collect();

    // ── 1. Carry forward cached findings for UNCHANGED files ───────────
    for (file, findings) in cached_file_findings {
        if !changed_set.contains(file) {
            findings_by_file.insert(file.clone(), findings.clone());
            all_findings.extend(findings.iter().cloned());
        }
    }

    // ── 2. Run FileLocal detectors on CHANGED files only ───────────────
    if !file_local.is_empty() {
        let (mut fl_findings, fl_bypass) = run_detectors(&file_local, &scoped_ctx, input.workers);
        // fl_bypass not needed — bypass_set pre-built from all detectors
        fl_findings = apply_hmm_context_filter(fl_findings, &scoped_ctx);
        filter_test_file_findings(&mut fl_findings);
        for f in &fl_findings {
            for file in &f.affected_files {
                findings_by_file.entry(file.clone()).or_default().push(f.clone());
            }
        }
        all_findings.extend(fl_findings);
    }

    // ── 3. FileScopedGraph detectors ───────────────────────────────────
    if !file_scoped_graph.is_empty() {
        if input.topology_changed {
            // Topology changed: re-run on ALL files
            let (mut fsg_findings, fsg_bypass) = run_detectors(&file_scoped_graph, &full_ctx, input.workers);
            // fsg_bypass not needed — bypass_set pre-built from all detectors
            fsg_findings = apply_hmm_context_filter(fsg_findings, &full_ctx);
            filter_test_file_findings(&mut fsg_findings);
            // Rebuild file findings for ALL files from these detectors
            for f in &fsg_findings {
                for file in &f.affected_files {
                    findings_by_file.entry(file.clone()).or_default().push(f.clone());
                }
            }
            all_findings.extend(fsg_findings);
        } else {
            // Topology stable: run only on changed files
            let (mut fsg_findings, fsg_bypass) = run_detectors(&file_scoped_graph, &scoped_ctx, input.workers);
            // fsg_bypass not needed — bypass_set pre-built from all detectors
            fsg_findings = apply_hmm_context_filter(fsg_findings, &scoped_ctx);
            filter_test_file_findings(&mut fsg_findings);
            for f in &fsg_findings {
                for file in &f.affected_files {
                    findings_by_file.entry(file.clone()).or_default().push(f.clone());
                }
            }
            all_findings.extend(fsg_findings);
        }
    }

    // ── 4. GraphWide detectors ─────────────────────────────────────────
    if input.topology_changed {
        // Re-run graph-wide detectors
        let (mut gw_findings, _gw_bypass) = run_detectors(&graph_wide, &full_ctx, input.workers);
        // gw_bypass not needed — bypass_set pre-built from all detectors
        gw_findings = apply_hmm_context_filter(gw_findings, &full_ctx);
        filter_test_file_findings(&mut gw_findings);
        for f in &gw_findings {
            graph_wide_findings_out
                .entry(f.detector.clone())
                .or_default()
                .push(f.clone());
        }
        all_findings.extend(gw_findings);
    } else {
        // Reuse cached graph-wide findings
        for (detector, findings) in cached_graph_wide_findings {
            graph_wide_findings_out.insert(detector.clone(), findings.clone());
            all_findings.extend(findings.iter().cloned());
        }
    }

    sort_findings_deterministic(&mut all_findings);

    Ok(DetectOutput {
        findings: all_findings,
        precomputed,
        findings_by_file,
        graph_wide_findings: graph_wide_findings_out,
        bypass_set,
        stats: DetectStats {
            detectors_run: detectors.len(),
            detectors_skipped: 0,
            gi_findings: 0,
            gd_findings: 0,
            precompute_duration,
        },
    })
}
```

- [ ] **Step 4: Add `to_context_scoped()` method to `PrecomputedAnalysis`**

In `detectors/engine.rs`, add a method that creates an `AnalysisContext` with a `FileIndex` scoped to only the changed files:

```rust
impl PrecomputedAnalysis {
    /// Build an AnalysisContext scoped to a subset of files.
    ///
    /// The graph and precomputed data cover the full repo, but the FileIndex
    /// only contains `scoped_files`. File-scoped detectors will only iterate
    /// over these files.
    pub fn to_context_scoped<'g>(
        &self,
        graph: &'g dyn crate::graph::GraphQuery,
        resolver: &crate::calibrate::ThresholdResolver,
        scoped_files: &[PathBuf],
    ) -> AnalysisContext<'g> {
        use crate::detectors::file_index::FileIndex;

        // Build a FileIndex with only the scoped files.
        // FileIndex::all() returns &[FileEntry] with path, content, flags.
        let scoped_set: std::collections::HashSet<&PathBuf> = scoped_files.iter().collect();
        let file_data: Vec<_> = self.file_index
            .all()
            .iter()
            .filter(|entry| scoped_set.contains(&entry.path))
            .map(|entry| (entry.path.clone(), Arc::clone(&entry.content), entry.flags))
            .collect();
        let scoped_file_index = Arc::new(FileIndex::new(file_data));

        AnalysisContext {
            graph,
            files: scoped_file_index,
            functions: Arc::clone(&self.contexts),
            taint: Arc::clone(&self.taint_results),
            detector_ctx: Arc::clone(&self.detector_context),
            hmm_classifications: Arc::clone(&self.hmm_with_confidence),
            resolver: Arc::new(resolver.clone()),
            reachability: Arc::clone(&self.reachability),
            public_api: Arc::clone(&self.public_api),
            module_metrics: Arc::clone(&self.module_metrics),
            class_cohesion: Arc::clone(&self.class_cohesion),
            decorator_index: Arc::clone(&self.decorator_index),
            git_churn: Arc::clone(&self.git_churn),
            co_change_summary: Arc::clone(&self.co_change_summary),
        }
    }
}
```

**Note:** `FileIndex::all()` returns `&[FileEntry]`. `FileEntry` has fields `path: PathBuf`, `content: Arc<str>`, `flags: ContentFlags`. Check the exact field visibility — if `content` is private, you may need to add a `pub fn content(&self) -> Arc<str>` accessor to `FileEntry` in `detectors/file_index.rs`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd repotoire-cli && cargo test engine::stages::detect::tests -v`
Expected: PASS — cached finding carried forward

- [ ] **Step 6: Run full test suite**

Run: `cd repotoire-cli && cargo test`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: incremental detection — skip unchanged files, reuse precompute + findings

detect_stage now uses incremental hints: FileLocal and FileScopedGraph
detectors only run on changed files (with cached findings carried forward
for unchanged files). GraphWide detectors reuse cached findings when
topology is unchanged. PrecomputedAnalysis is reused from cache when
topology is stable, saving ~3.9s per incremental run."
```

---

## Task 4: Integration test — end-to-end incremental performance

**Goal:** Verify the full save → load → incremental pipeline produces correct results and is significantly faster than cold analysis.

**Files:**
- Modify: `repotoire-cli/src/engine/mod.rs` (test module)

- [ ] **Step 1: Write integration test**

```rust
#[test]
fn test_full_incremental_pipeline_correctness() {
    // Setup: temp dir with multiple files
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("main.py"),
        "import helper\n\ndef main():\n    helper.greet()\n",
    ).unwrap();
    std::fs::write(
        dir.path().join("helper.py"),
        "def greet():\n    print('hello')\n",
    ).unwrap();
    // Initialize git repo (needed for git enrich)
    std::process::Command::new("git")
        .args(["init"])
        .current_dir(dir.path())
        .output()
        .ok();
    std::process::Command::new("git")
        .args(["add", "."])
        .current_dir(dir.path())
        .output()
        .ok();
    std::process::Command::new("git")
        .args(["commit", "-m", "init"])
        .current_dir(dir.path())
        .output()
        .ok();

    let config = AnalysisConfig { no_git: true, ..Default::default() };

    // Cold analysis
    let mut engine = AnalysisEngine::new(dir.path()).unwrap();
    let cold_result = engine.analyze(&config).unwrap();
    let cold_score = cold_result.score.overall;
    let cold_findings_count = cold_result.findings.len();

    // Save
    let session_dir = tempfile::tempdir().unwrap();
    engine.save(session_dir.path()).unwrap();

    // No changes → should be Cached
    let mut engine2 = AnalysisEngine::load(session_dir.path(), dir.path()).unwrap();
    let cached_result = engine2.analyze(&config).unwrap();
    assert!(matches!(cached_result.stats.mode, AnalysisMode::Cached));
    assert_eq!(cached_result.score.overall, cold_score);
    assert_eq!(cached_result.findings.len(), cold_findings_count);

    // Modify one file → should be Incremental
    std::fs::write(
        dir.path().join("helper.py"),
        "def greet():\n    print('hello world')\n\ndef farewell():\n    print('bye')\n",
    ).unwrap();

    let mut engine3 = AnalysisEngine::load(session_dir.path(), dir.path()).unwrap();
    let incr_result = engine3.analyze(&config).unwrap();
    assert!(matches!(incr_result.stats.mode, AnalysisMode::Incremental { .. }));
    // Score and findings should be reasonable (not zero, not wildly different)
    assert!(incr_result.score.overall > 0.0);
    assert_eq!(incr_result.stats.files_analyzed, 2);
}
```

- [ ] **Step 2: Run test**

Run: `cd repotoire-cli && cargo test test_full_incremental_pipeline_correctness -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "test: end-to-end incremental pipeline correctness test"
```

---

## Task 5: Benchmark on real repo

**Goal:** Verify the performance improvement on the scout repo (326k LOC).

- [ ] **Step 1: Build release binary**

Run: `cd repotoire-cli && nix-shell -p gnumake --run "cargo build --release"`

- [ ] **Step 2: Clear scout session cache**

Run: `rm -rf ~/.cache/repotoire/scout-*/session`

- [ ] **Step 3: Cold run (baseline)**

Run:
```bash
cd ~/work/scout
time repotoire-cli/target/release/repotoire analyze . --format text --timings 2>&1 | grep -E "Phase|TOTAL|Analysis complete"
```
Expected: ~19s cold

- [ ] **Step 4: Cached run (no changes)**

Run same command again.
Expected: <0.5s (Cached mode)

- [ ] **Step 5: Incremental run (1 file changed)**

```bash
# Touch a real source file
ts_file=$(find ~/work/scout -name "*.ts" -not -path "*/node_modules/*" | head -1)
echo "" >> "$ts_file"

time repotoire-cli/target/release/repotoire analyze . --format text --timings 2>&1 | grep -E "Phase|TOTAL|Analysis complete|Incremental"
```
Expected: detect stage under 1s. Total under 5s (precompute is the likely bottleneck).

- [ ] **Step 6: Revert test file change**

```bash
git checkout -- "$ts_file"
```

- [ ] **Step 7: Record results and commit any final adjustments**

If performance targets are met, no further changes needed. If precompute is still >3s, note it as a follow-up optimization (cache `PrecomputedAnalysis` to disk).

---

## Summary

| Task | What | Risk | Est. complexity |
|------|------|------|-----------------|
| 1 | `patch_builder()` + `freeze_builder()` | Low — new code, no existing code changed | Small |
| 2 | Wire GraphBuilder into `analyze_incremental()` with `into_builder()` | Medium — restructures a 200-line function | Medium |
| 3 | Incremental detection + precompute reuse in `detect_stage()` | Medium — needs `to_context_scoped()` + precompute cache logic | Medium |
| 4 | Integration test | Low | Small |
| 5 | Benchmark | Low | Tiny |

### Expected performance (1-file change on scout, 326k LOC)

| Scenario | Before | After |
|----------|--------|-------|
| **Cached (no changes)** | 0.14s | 0.14s (unchanged) |
| **Topology unchanged (common)** | 15s | ~4-5s (freeze 2-4s + taint 1.5s + detect ~100ms) |
| **Topology changed (rare)** | 15s | ~8s (freeze 2-4s + full precompute 3.9s + detect ~100ms) |

The topology-unchanged case is by far the most common (editing function bodies, changing strings, fixing bugs). Topology only changes when imports/exports/function signatures change.

Taint re-runs even when topology is unchanged because changed files may add new sinks/sources. HMM contexts, function contexts, reachability, and module metrics are safely reused (~2.4s saved).

**Memory:** Peak ~15MB for scout (loaded graph consumed via `into_builder()`, not cloned). Acceptable.

**Disk I/O:** `save()` writes ~7MB per run (blocking, ~300ms). Acceptable for CLI; could be made async as follow-up.

### Follow-up optimizations (not in scope)
- **Skip primitives in `freeze()` when edge fingerprint unchanged** — would save 2-4s on topology-unchanged case, making it <1s. Requires passing old primitives to `GraphIndexes::build()`.
- **Persist `PrecomputedAnalysis` to disk** — would eliminate precompute on topology-changed case too
- **Async `save()`** — move disk I/O to background thread
- **Migrate cold path from `GraphStore` to `GraphBuilder`** — full legacy elimination
