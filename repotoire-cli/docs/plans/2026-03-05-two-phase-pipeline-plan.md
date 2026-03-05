# Two-Phase Pipeline Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the overlapped pipeline (`FlushingGraphBuilder`) deterministic by deferring cross-file edge resolution to a second phase after all files are parsed.

**Architecture:** Two-pass assembler pattern — Phase 1 streams files, adds nodes, builds symbol tables, and buffers unresolved cross-file references as `DeferredEdge` structs. Phase 2 sorts deferred edges and resolves them against complete symbol tables. Intra-file edges (Contains, same-file Calls) still resolve immediately in Phase 1.

**Tech Stack:** Rust, petgraph, crossbeam-channel, rayon

**Design doc:** `repotoire-cli/docs/plans/2026-03-05-deterministic-graph-design.md`

---

### Task 1: Add DeferredEdge struct and buffer to FlushingGraphBuilder

**Files:**
- Modify: `repotoire-cli/src/parsers/bounded_pipeline.rs:30-143`

**Step 1: Add DeferredEdge types after the imports (line 39)**

Add these types between the imports and `PipelineConfig`:

```rust
/// Unresolved cross-file edge buffered during Phase 1 for deferred resolution.
#[derive(Debug, Clone)]
enum DeferredEdgeKind {
    /// Cross-file function call: source_qn is the caller qualified name,
    /// target_hint is the callee's bare name (e.g., "helper_from_b").
    Call {
        caller_qn: String,
        callee_name: String,
        /// Whether the callee has a module qualifier (e.g., "module.func").
        has_module_qualifier: bool,
    },
    /// Cross-file import: source_qn is the importing file's relative path,
    /// target_hint is the import path string.
    Import {
        file_path: String,
        import_path: String,
        is_type_only: bool,
    },
}
```

**Step 2: Add `deferred_edges` field to `FlushingGraphBuilder` (line 129-143)**

Replace the struct definition:

```rust
struct FlushingGraphBuilder {
    graph: Arc<GraphStore>,
    repo_path: PathBuf,

    // Lookup indexes (grow with repo but much smaller than full file info)
    function_lookup: HashMap<String, String>,
    module_lookup: ModuleLookupCompact,

    // Buffered resolved edges (flushed periodically)
    edge_buffer: Vec<(String, String, CodeEdge)>,
    edge_flush_threshold: usize,

    // Deferred cross-file edges (resolved in Phase 2)
    deferred_edges: Vec<DeferredEdgeKind>,

    // Stats
    stats: BoundedPipelineStats,
}
```

**Step 3: Initialize `deferred_edges` in `FlushingGraphBuilder::new()` (line 179-189)**

Add `deferred_edges: Vec::new()` to the `Self { ... }` block.

**Step 4: Run `cargo check` to verify compilation**

Run: `cargo check`
Expected: Compiles with warnings about unused `deferred_edges`

**Step 5: Commit**

```bash
git add repotoire-cli/src/parsers/bounded_pipeline.rs
git commit -m "feat: add DeferredEdgeKind and deferred_edges buffer to FlushingGraphBuilder"
```

---

### Task 2: Modify process() to defer cross-file edges

**Files:**
- Modify: `repotoire-cli/src/parsers/bounded_pipeline.rs:207-332` (the `process` method)

**Step 1: Replace the cross-file call edge resolution block (lines 276-307)**

The current code at lines 276-307 tries to resolve cross-file calls immediately via `self.function_lookup`. Replace with:

```rust
        // Resolve call edges — same-file immediately, cross-file deferred
        for call in &info.calls {
            let callee_name = call
                .callee
                .rsplit(&[':', '.'][..])
                .next()
                .unwrap_or(&call.callee);

            // Check same file first (always resolvable)
            let same_file_match = info
                .functions
                .iter()
                .find(|f| f.name == callee_name)
                .map(|f| f.qualified_name.clone());

            if let Some(qn) = same_file_match {
                // Same-file call — resolve immediately
                self.edge_buffer
                    .push((call.caller.clone(), qn, CodeEdge::calls()));
            } else {
                // Cross-file call — defer to Phase 2
                let has_module = call.callee.contains(':') || call.callee.contains('.');
                self.deferred_edges.push(DeferredEdgeKind::Call {
                    caller_qn: call.caller.clone(),
                    callee_name: callee_name.to_string(),
                    has_module_qualifier: has_module,
                });
            }
        }
```

**Step 2: Replace the import edge resolution block (lines 309-319)**

The current code resolves imports immediately. Replace with:

```rust
        // Defer all import edges to Phase 2 (need complete module lookup)
        for import in &info.imports {
            self.deferred_edges.push(DeferredEdgeKind::Import {
                file_path: relative.clone(),
                import_path: import.path.clone(),
                is_type_only: import.is_type_only,
            });
        }
```

**Step 3: Run `cargo check`**

Run: `cargo check`
Expected: Compiles cleanly (deferred_edges is now used)

**Step 4: Commit**

```bash
git add repotoire-cli/src/parsers/bounded_pipeline.rs
git commit -m "feat: defer cross-file call and import edges to Phase 2 in FlushingGraphBuilder"
```

---

### Task 3: Implement Phase 2 resolution in finalize()

**Files:**
- Modify: `repotoire-cli/src/parsers/bounded_pipeline.rs:352-357` (the `finalize` method)

**Step 1: Replace `finalize()` with two-phase resolution**

```rust
    /// Finalize — Phase 2: resolve deferred cross-file edges, flush, and save
    fn finalize(mut self) -> Result<BoundedPipelineStats> {
        // Phase 2: Resolve deferred cross-file edges with complete symbol tables
        //
        // Sort deferred edges for deterministic resolution order.
        // This ensures identical graphs regardless of file discovery order.
        self.deferred_edges.sort_by(|a, b| {
            // Sort by (source, target_hint) for deterministic iteration
            match (a, b) {
                (
                    DeferredEdgeKind::Call { caller_qn: a_src, callee_name: a_tgt, .. },
                    DeferredEdgeKind::Call { caller_qn: b_src, callee_name: b_tgt, .. },
                ) => a_src.cmp(b_src).then_with(|| a_tgt.cmp(b_tgt)),
                (
                    DeferredEdgeKind::Import { file_path: a_src, import_path: a_tgt, .. },
                    DeferredEdgeKind::Import { file_path: b_src, import_path: b_tgt, .. },
                ) => a_src.cmp(b_src).then_with(|| a_tgt.cmp(b_tgt)),
                // Calls before Imports for stable grouping
                (DeferredEdgeKind::Call { .. }, DeferredEdgeKind::Import { .. }) => std::cmp::Ordering::Less,
                (DeferredEdgeKind::Import { .. }, DeferredEdgeKind::Call { .. }) => std::cmp::Ordering::Greater,
            }
        });

        let deferred_count = self.deferred_edges.len();
        let mut resolved_count = 0usize;

        for deferred in std::mem::take(&mut self.deferred_edges) {
            match deferred {
                DeferredEdgeKind::Call {
                    caller_qn,
                    callee_name,
                    has_module_qualifier,
                } => {
                    // Skip ambiguous bare method names (same logic as sequential pipeline)
                    if !has_module_qualifier
                        && crate::cli::analyze::graph::AMBIGUOUS_METHOD_NAMES
                            .contains(&callee_name.as_str())
                    {
                        continue;
                    }
                    // Resolve against complete function_lookup
                    if let Some(callee_qn) = self.function_lookup.get(&callee_name) {
                        self.edge_buffer.push((
                            caller_qn,
                            callee_qn.clone(),
                            CodeEdge::calls(),
                        ));
                        resolved_count += 1;
                    }
                }
                DeferredEdgeKind::Import {
                    file_path,
                    import_path,
                    is_type_only,
                } => {
                    if let Some(target) = self.module_lookup.find_match(&import_path) {
                        if *target != file_path {
                            let edge = CodeEdge::imports()
                                .with_property("is_type_only", is_type_only);
                            self.edge_buffer
                                .push((file_path, target.clone(), edge));
                            resolved_count += 1;
                        }
                    }
                }
            }
        }

        tracing::info!(
            "Phase 2: resolved {}/{} deferred cross-file edges",
            resolved_count,
            deferred_count
        );

        // Flush all remaining edges (Phase 1 intra-file + Phase 2 cross-file)
        self.flush_edges()?;
        self.graph.save()?;
        Ok(self.stats)
    }
```

**Step 2: Run `cargo check`**

Run: `cargo check`
Expected: Compiles cleanly

**Step 3: Commit**

```bash
git add repotoire-cli/src/parsers/bounded_pipeline.rs
git commit -m "feat: implement Phase 2 deferred edge resolution in FlushingGraphBuilder::finalize"
```

---

### Task 4: Also defer cross-file edges in run_bounded_pipeline (non-channel variant)

**Files:**
- Modify: `repotoire-cli/src/parsers/bounded_pipeline.rs:377-486`

The `run_bounded_pipeline` function (used when file list is known upfront) pre-populates `module_lookup` via `add_file_paths()` on line 399. Import edges can resolve correctly in this path because the module lookup is already complete. However, `function_lookup` is still populated incrementally (in `process()`), so cross-file **call** edges still suffer from incomplete state.

Since `process()` has already been changed in Task 2 to defer cross-file edges, this variant automatically benefits. No code changes needed — `process()` is shared.

**Step 1: Verify no changes needed**

Read the `run_bounded_pipeline` function and confirm it calls `builder.process(info)` which uses the updated Phase 1/Phase 2 logic. Confirm `builder.finalize()` is called at the end.

Run: `cargo check`
Expected: Compiles cleanly

**Step 2: Commit (skip if no changes)**

No commit needed — this task is verification only.

---

### Task 5: Write determinism test for the overlapped pipeline

**Files:**
- Modify: `repotoire-cli/src/parsers/bounded_pipeline.rs:615-717` (test module)

**Step 1: Write the determinism test**

Add this test to the existing `#[cfg(test)] mod tests` block:

```rust
    /// Verify that the overlapped pipeline produces identical graphs regardless of
    /// the order files arrive. This is the core determinism invariant.
    #[test]
    fn test_overlapped_pipeline_deterministic_across_file_orders() {
        let dir = TempDir::new().expect("temp dir");
        let path = dir.path();

        // module_a imports module_b and calls helper_from_b
        create_test_file(
            path,
            "module_a.py",
            "from module_b import helper_from_b\n\ndef main():\n    helper_from_b()\n",
        );
        // module_b defines helper_from_b and calls main from module_a
        create_test_file(
            path,
            "module_b.py",
            "from module_a import main\n\ndef helper_from_b():\n    main()\n",
        );

        // Run N times with shuffled file order via channel
        let mut edge_snapshots: Vec<Vec<String>> = Vec::new();

        for run_idx in 0..5 {
            let graph = Arc::new(GraphStore::in_memory());
            let config = PipelineConfig::for_repo_size(2);

            let (tx, rx) = bounded::<PathBuf>(config.buffer_size);

            // Alternate file order between runs
            let files = if run_idx % 2 == 0 {
                vec![path.join("module_a.py"), path.join("module_b.py")]
            } else {
                vec![path.join("module_b.py"), path.join("module_a.py")]
            };

            let sender = thread::spawn(move || {
                for f in files {
                    tx.send(f).expect("send");
                }
            });

            let (_stats, _parse_stats) =
                run_bounded_pipeline_from_channel(rx, path, graph.clone(), config, None)
                    .expect("pipeline should succeed");

            sender.join().expect("sender thread");

            // Collect all edges as sorted strings for comparison
            let mut edges: Vec<String> = Vec::new();
            for (src, dst, kind) in graph.get_all_edges() {
                edges.push(format!("{} --{:?}--> {}", src, kind, dst));
            }
            edges.sort();
            edge_snapshots.push(edges);
        }

        // All runs must produce identical edge sets
        for (i, snapshot) in edge_snapshots.iter().enumerate().skip(1) {
            assert_eq!(
                &edge_snapshots[0], snapshot,
                "Run {} edges differ from run 0",
                i
            );
        }
    }
```

**Step 2: Run the test to verify it fails (before Phase 2 was implemented, it would fail; now it should pass)**

Run: `cargo test test_overlapped_pipeline_deterministic_across_file_orders -- --nocapture`
Expected: PASS — the Phase 2 fix from Tasks 1-3 makes this deterministic

If this test requires `get_all_edges()` that doesn't exist on `GraphStore`, we may need to use existing methods. Check `GraphStore` for available edge query methods and adjust accordingly.

**Step 3: Commit**

```bash
git add repotoire-cli/src/parsers/bounded_pipeline.rs
git commit -m "test: add determinism test for overlapped pipeline across file orders"
```

---

### Task 6: Write determinism test for run_bounded_pipeline (file-list variant)

**Files:**
- Modify: `repotoire-cli/src/parsers/bounded_pipeline.rs` (test module)

**Step 1: Write the test**

```rust
    /// Verify that the file-list pipeline variant is also deterministic
    /// with the two-phase approach.
    #[test]
    fn test_bounded_pipeline_deterministic_cross_file_calls() {
        let dir = TempDir::new().expect("temp dir");
        let path = dir.path();

        create_test_file(
            path,
            "module_a.py",
            "from module_b import helper_from_b\n\ndef main():\n    helper_from_b()\n",
        );
        create_test_file(
            path,
            "module_b.py",
            "from module_a import main\n\ndef helper_from_b():\n    main()\n",
        );

        let mut edge_snapshots: Vec<Vec<String>> = Vec::new();

        for run_idx in 0..5 {
            let graph = Arc::new(GraphStore::in_memory());
            let config = PipelineConfig::for_repo_size(2);

            // Alternate file order
            let files = if run_idx % 2 == 0 {
                vec![path.join("module_a.py"), path.join("module_b.py")]
            } else {
                vec![path.join("module_b.py"), path.join("module_a.py")]
            };

            let (_stats, _parse_stats) =
                run_bounded_pipeline(files, path, graph.clone(), config, None)
                    .expect("pipeline should succeed");

            let mut edges: Vec<String> = Vec::new();
            for (src, dst, kind) in graph.get_all_edges() {
                edges.push(format!("{} --{:?}--> {}", src, kind, dst));
            }
            edges.sort();
            edge_snapshots.push(edges);
        }

        for (i, snapshot) in edge_snapshots.iter().enumerate().skip(1) {
            assert_eq!(
                &edge_snapshots[0], snapshot,
                "Run {} edges differ from run 0",
                i
            );
        }
    }
```

**Step 2: Check if `get_all_edges()` exists on GraphStore**

If not, add a helper method or use existing edge query methods. The test needs to collect all edges in the graph to compare across runs.

Search for existing edge access methods:
```bash
grep -n "fn get_all_edges\|fn get_calls\|fn get_imports\|fn edge_count" repotoire-cli/src/graph/store/mod.rs
```

If `get_all_edges()` doesn't exist, use:
```rust
let calls = graph.get_calls();
let imports = graph.get_imports();
// ... combine and sort
```

Or add a simple `get_all_edges()` method to `GraphStore`:
```rust
pub fn get_all_edges(&self) -> Vec<(String, String, EdgeKind)> {
    let graph = self.graph.read().expect("graph lock");
    graph.edge_indices()
        .filter_map(|ei| {
            let (src, dst) = graph.edge_endpoints(ei)?;
            let edge = graph.edge_weight(ei)?;
            let src_node = graph.node_weight(src)?;
            let dst_node = graph.node_weight(dst)?;
            Some((src_node.qualified_name.clone(), dst_node.qualified_name.clone(), edge.kind))
        })
        .collect()
}
```

**Step 3: Run tests**

Run: `cargo test test_bounded_pipeline_deterministic -- --nocapture`
Expected: PASS

**Step 4: Commit**

```bash
git add repotoire-cli/src/parsers/bounded_pipeline.rs repotoire-cli/src/graph/store/mod.rs
git commit -m "test: add cross-file call determinism test for file-list pipeline variant"
```

---

### Task 7: Add get_all_edges() to GraphStore (if needed)

**Files:**
- Modify: `repotoire-cli/src/graph/store/mod.rs`

**Prerequisite:** Only do this task if Tasks 5-6 require it.

**Step 1: Add `get_all_edges()` method**

Find the impl block for `GraphStore` and add:

```rust
    /// Get all edges in the graph as (source_qn, dest_qn, edge_kind) tuples.
    /// Used primarily for testing determinism.
    pub fn get_all_edges(&self) -> Vec<(String, String, crate::graph::EdgeKind)> {
        let graph = self.graph.read().expect("graph lock");
        let mut edges: Vec<_> = graph
            .edge_indices()
            .filter_map(|ei| {
                let (src_idx, dst_idx) = graph.edge_endpoints(ei)?;
                let src_node = graph.node_weight(src_idx)?;
                let dst_node = graph.node_weight(dst_idx)?;
                let edge = graph.edge_weight(ei)?;
                Some((
                    src_node.qualified_name.clone(),
                    dst_node.qualified_name.clone(),
                    edge.kind,
                ))
            })
            .collect();
        edges.sort_by(|a, b| a.0.cmp(&b.0).then_with(|| a.1.cmp(&b.1)));
        edges
    }
```

**Step 2: Run `cargo check`**

Run: `cargo check`
Expected: Compiles cleanly

**Step 3: Commit**

```bash
git add repotoire-cli/src/graph/store/mod.rs
git commit -m "feat: add get_all_edges() to GraphStore for determinism testing"
```

---

### Task 8: Also sort ModuleLookupCompact for determinism

**Files:**
- Modify: `repotoire-cli/src/parsers/bounded_pipeline.rs:145-176`

The `ModuleLookupCompact` in the overlapped pipeline uses `HashMap<String, Vec<String>>` for `by_stem`. The `find_match()` method calls `v.first()`, so if multiple files share a stem (e.g., `utils.py` in two directories), the first one wins — but `Vec` order depends on insertion order which depends on file discovery order.

**Step 1: Change `by_stem` to `BTreeMap` and sort candidate vecs**

```rust
use std::collections::BTreeMap;

/// Compact module lookup - only stores what we need
#[derive(Debug, Default)]
struct ModuleLookupCompact {
    by_stem: BTreeMap<String, Vec<String>>,
}

impl ModuleLookupCompact {
    fn add_file(&mut self, relative_path: &str) {
        let path = Path::new(relative_path);
        if let Some(stem) = path.file_stem().and_then(|s| s.to_str()) {
            self.by_stem
                .entry(stem.to_string())
                .or_default()
                .push(relative_path.to_string());
        }
    }

    /// Sort all candidate vecs for deterministic resolution.
    /// Must be called after all files have been added (end of Phase 1).
    fn sort_candidates(&mut self) {
        for candidates in self.by_stem.values_mut() {
            candidates.sort();
        }
    }

    fn find_match(&self, import_path: &str) -> Option<&String> {
        let clean = import_path
            .trim_start_matches("./")
            .trim_start_matches("../")
            .trim_start_matches("crate::")
            .trim_start_matches("super::");

        let stem = clean
            .split(&[':', '.', '/'][..])
            .next_back()
            .unwrap_or(clean);
        self.by_stem.get(stem).and_then(|v| v.first())
    }
}
```

**Step 2: Call `sort_candidates()` at the start of `finalize()`**

In `finalize()`, before the deferred edge resolution loop, add:

```rust
        // Sort module lookup candidates for deterministic import resolution
        self.module_lookup.sort_candidates();
```

**Step 3: Also call `sort_candidates()` after `add_file_paths()` in `run_bounded_pipeline`**

In `run_bounded_pipeline()` (line 399), after `builder.add_file_paths(&files);`, the module lookup is fully populated. But since `finalize()` already sorts, this is redundant. However, for the file-list variant where imports *could* be resolved in Phase 1 (module lookup is pre-populated), we should still sort. Actually — since all imports are now deferred (Task 2), sorting in `finalize()` is sufficient.

**Step 4: Update the `HashMap` import**

The `use std::collections::HashMap;` on line 35 is still needed for `function_lookup`. Add `BTreeMap`:

```rust
use std::collections::{BTreeMap, HashMap};
```

**Step 5: Run `cargo check`**

Run: `cargo check`
Expected: Compiles cleanly

**Step 6: Commit**

```bash
git add repotoire-cli/src/parsers/bounded_pipeline.rs
git commit -m "fix: use BTreeMap and sort candidates in ModuleLookupCompact for determinism"
```

---

### Task 9: Sort function_lookup for deterministic call resolution

**Files:**
- Modify: `repotoire-cli/src/parsers/bounded_pipeline.rs`

The `function_lookup: HashMap<String, String>` maps bare function names to qualified names. When two files define a function with the same bare name (e.g., `helper`), the last one inserted wins — and insertion order depends on file discovery order.

**Step 1: Change `function_lookup` to `BTreeMap`**

In the `FlushingGraphBuilder` struct, change:
```rust
    function_lookup: BTreeMap<String, String>,
```

In `FlushingGraphBuilder::new()`, change:
```rust
    function_lookup: BTreeMap::new(),
```

This ensures that when multiple functions share a name, the lexicographically first qualified name wins consistently. Actually — `BTreeMap` insert replaces existing values just like `HashMap`, so the *last* insert still wins. The fix is that `finalize()` sorts deferred edges, so resolution order is deterministic regardless of lookup structure. But using `BTreeMap` is still safer for consistency.

**Step 2: Run `cargo check`**

Run: `cargo check`
Expected: Compiles cleanly

**Step 3: Run all tests**

Run: `cargo test`
Expected: All tests pass

**Step 4: Commit**

```bash
git add repotoire-cli/src/parsers/bounded_pipeline.rs
git commit -m "fix: use BTreeMap for function_lookup in FlushingGraphBuilder for determinism"
```

---

### Task 10: Run full determinism verification

**Files:** None (verification only)

**Step 1: Run `cargo test`**

Run: `cargo test`
Expected: All tests pass, including the new determinism tests from Tasks 5-6

**Step 2: Run 5 cold-start analysis runs on the test fixtures**

Use the test fixtures `module_a.py` and `module_b.py` from `repotoire-cli/tests/fixtures/`:

```bash
cd /home/zhammad/personal/repotoire

# Cold-start determinism test
for i in $(seq 1 5); do
    cargo run -- clean repotoire-cli/tests/fixtures/ 2>/dev/null
    cargo run -- analyze repotoire-cli/tests/fixtures/ --format json 2>/dev/null | python3 -c "
import sys, json, hashlib
data = sys.stdin.read()
h = hashlib.sha256(data.encode()).hexdigest()[:16]
print(f'Run {int(sys.argv[1])}: {h}')
" "$i"
done
```

Expected: All 5 runs produce the same hash

**Step 3: Run a warm-start after one cold-start and compare**

```bash
# Cold start
cargo run -- clean repotoire-cli/tests/fixtures/ 2>/dev/null
cargo run -- analyze repotoire-cli/tests/fixtures/ --format json 2>/dev/null > /tmp/cold.json

# Warm start (no clean)
cargo run -- analyze repotoire-cli/tests/fixtures/ --format json 2>/dev/null > /tmp/warm.json

# Compare
diff /tmp/cold.json /tmp/warm.json
```

Expected: No diff (cold and warm produce identical output)

**Step 4: Commit (no code changes — just verification)**

If all checks pass, no commit needed. If issues found, fix and loop back.

---

### Task 11: Run cargo clippy and fix any warnings

**Files:**
- Modify: `repotoire-cli/src/parsers/bounded_pipeline.rs` (if clippy finds issues)

**Step 1: Run clippy**

Run: `cargo clippy -- -W clippy::all`
Expected: No new warnings in `bounded_pipeline.rs`

**Step 2: Fix any clippy warnings**

Apply fixes as needed.

**Step 3: Commit**

```bash
git add -u
git commit -m "fix: address clippy warnings in bounded_pipeline two-phase changes"
```

---

## Summary of Changes

| File | Change |
|------|--------|
| `repotoire-cli/src/parsers/bounded_pipeline.rs` | Add `DeferredEdgeKind` enum, `deferred_edges` buffer, modify `process()` to defer cross-file edges, implement Phase 2 in `finalize()`, use `BTreeMap` for lookups, add determinism tests |
| `repotoire-cli/src/graph/store/mod.rs` | Add `get_all_edges()` method (if needed for tests) |

## Verification Criteria

1. `cargo test` — all tests pass
2. `cargo clippy` — no new warnings
3. 5 cold-start runs produce identical JSON output
4. Warm-start matches cold-start output
5. New determinism tests explicitly verify edge stability across file orders
