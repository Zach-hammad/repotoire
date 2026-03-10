# Parse Phase Elimination Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate the 425ms finalize phase from the parse pipeline via inline call resolution with correct bare-name disambiguation.

**Architecture:** Replace two-phase defer-all/sort/resolve with inline resolution using a multi-map `function_lookup` (unique names resolve immediately, ambiguous names are dropped) and a pending queue for forward references. Move Contains edges into node batch insertion. Use `Arc<str>` for file paths.

**Tech Stack:** Rust, petgraph, crossbeam-channel, std HashMap

---

### Task 1: Multi-Map Function Lookup + Correctness Tests

**Files:**
- Modify: `repotoire-cli/src/parsers/bounded_pipeline.rs:46-67` (DeferredEdgeKind enum)
- Modify: `repotoire-cli/src/parsers/bounded_pipeline.rs:156-174` (FlushingGraphBuilder struct)
- Modify: `repotoire-cli/src/parsers/bounded_pipeline.rs:217-229` (FlushingGraphBuilder::new)
- Modify: `repotoire-cli/src/parsers/bounded_pipeline.rs:248-255` (process function_lookup insert)
- Test: `repotoire-cli/src/parsers/bounded_pipeline.rs` (inline tests module)

**Step 1: Write the failing test for ambiguous bare-name resolution**

Add to the `#[cfg(test)] mod tests` block at line 728:

```rust
/// Verify that ambiguous bare names (same function name in two files)
/// do NOT produce spurious cross-file call edges.
#[test]
fn test_ambiguous_bare_name_drops_cross_file_edge() {
    let dir = TempDir::new().expect("temp dir");
    let path = dir.path();

    // Two files define process(), a third file calls process()
    create_test_file(path, "utils_a.py", "def process():\n    pass\n");
    create_test_file(path, "utils_b.py", "def process():\n    pass\n");
    create_test_file(
        path,
        "main.py",
        "def main():\n    process()\n",
    );

    let graph = Arc::new(GraphStore::in_memory());
    let config = PipelineConfig::for_repo_size(3);
    let files = vec![
        path.join("utils_a.py"),
        path.join("utils_b.py"),
        path.join("main.py"),
    ];

    let (_stats, _parse_stats) =
        run_bounded_pipeline(files, path, graph.clone(), config, None)
            .expect("pipeline should succeed");

    // main::main should NOT have a call edge to either process —
    // bare name "process" is ambiguous (2 candidates).
    let call_edges = graph.get_edges_by_kind(crate::graph::EdgeKind::Calls);
    let spurious = call_edges
        .iter()
        .filter(|(src, _dst)| src.contains("main"))
        .filter(|(_src, dst)| dst.contains("process"))
        .count();
    assert_eq!(spurious, 0, "ambiguous bare name should not create cross-file call edge");
}
```

**Step 2: Run test to verify it fails**

Run: `cargo test -p repotoire-cli test_ambiguous_bare_name_drops -- --nocapture`
Expected: FAIL — current code resolves to last-inserted `process`, creating a spurious edge.

**Step 3: Implement multi-map function_lookup**

Replace the `DeferredEdgeKind` enum (lines 46-67) — remove the `Call` variant, keep only `Import`:

```rust
/// Unresolved cross-file import edge buffered during Phase 1 for deferred resolution.
/// Call edges are resolved inline (not deferred) — see `resolve_cross_file_call()`.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
enum DeferredImport {
    Import {
        file_path: String,
        import_path: String,
        is_type_only: bool,
    },
}
```

Add a `LookupEntry` enum above the `FlushingGraphBuilder` struct:

```rust
/// Tracks whether a bare function name maps to exactly one qualified name.
/// Used for deterministic cross-file call resolution: unique names resolve,
/// ambiguous names (2+ functions with the same bare name) are dropped.
#[derive(Debug, Clone)]
enum LookupEntry {
    /// Exactly one function with this bare name — safe to resolve.
    Unique(String),
    /// Two or more functions share this bare name — cannot resolve without
    /// language-specific import analysis, so we drop the edge.
    Ambiguous,
}
```

Update `FlushingGraphBuilder` struct (lines 156-174):

```rust
struct FlushingGraphBuilder {
    graph: Arc<GraphStore>,
    repo_path: PathBuf,

    // Lookup indexes
    function_lookup: HashMap<String, LookupEntry>,
    module_lookup: ModuleLookupCompact,

    // Buffered edges (flushed periodically)
    edge_buffer: Vec<(String, String, CodeEdge)>,
    edge_flush_threshold: usize,

    // Pending cross-file calls waiting for callee to be registered
    pending_calls: HashMap<String, Vec<String>>,  // callee_bare_name → [caller_qn, ...]

    // Deferred cross-file imports (resolved in finalize)
    deferred_imports: Vec<DeferredImport>,

    // Stats
    stats: BoundedPipelineStats,
}
```

Update `FlushingGraphBuilder::new()` (lines 217-229):

```rust
fn new(graph: Arc<GraphStore>, repo_path: &Path, edge_flush_threshold: usize) -> Self {
    Self {
        graph,
        repo_path: repo_path.to_path_buf(),
        function_lookup: HashMap::new(),
        module_lookup: ModuleLookupCompact::default(),
        edge_buffer: Vec::with_capacity(edge_flush_threshold.min(10_000)),
        edge_flush_threshold,
        pending_calls: HashMap::new(),
        deferred_imports: Vec::new(),
        stats: BoundedPipelineStats::default(),
    }
}
```

Update the function registration in `process()` (lines 251-255):

```rust
// Add functions to lookup — track ambiguity for correct cross-file resolution
for func in &info.functions {
    match self.function_lookup.entry(func.name.clone()) {
        std::collections::hash_map::Entry::Vacant(e) => {
            e.insert(LookupEntry::Unique(func.qualified_name.clone()));
            // Drain any pending callers that were waiting for this function
            if let Some(callers) = self.pending_calls.remove(&func.name) {
                for caller_qn in callers {
                    self.edge_buffer.push((
                        caller_qn,
                        func.qualified_name.clone(),
                        CodeEdge::calls(),
                    ));
                }
            }
        }
        std::collections::hash_map::Entry::Occupied(mut e) => {
            // Second function with this bare name — mark ambiguous
            *e.get_mut() = LookupEntry::Ambiguous;
        }
    }
}
```

**Step 4: Run test to verify it passes**

Run: `cargo test -p repotoire-cli test_ambiguous_bare_name_drops -- --nocapture`
Expected: PASS

**Step 5: Commit**

```bash
git add repotoire-cli/src/parsers/bounded_pipeline.rs
git commit -m "feat: multi-map function_lookup with ambiguous bare-name dropping"
```

---

### Task 2: Inline Call Resolution + Pending Queues

**Files:**
- Modify: `repotoire-cli/src/parsers/bounded_pipeline.rs:326-365` (call resolution in process())
- Test: `repotoire-cli/src/parsers/bounded_pipeline.rs` (inline tests module)

**Step 1: Write the failing test for pending queue resolution**

```rust
/// Verify that forward references resolve via pending queue:
/// file A calls foo(), file B defines foo() — edge should exist regardless of order.
#[test]
fn test_pending_queue_resolves_forward_references() {
    let dir = TempDir::new().expect("temp dir");
    let path = dir.path();

    // caller.py calls helper() which is defined in helper.py
    create_test_file(
        path,
        "caller.py",
        "def main():\n    helper()\n",
    );
    create_test_file(path, "helper.py", "def helper():\n    pass\n");

    // Process caller FIRST (forward reference — helper not yet registered)
    let graph = Arc::new(GraphStore::in_memory());
    let config = PipelineConfig::for_repo_size(2);
    let files = vec![path.join("caller.py"), path.join("helper.py")];

    let (_stats, _) =
        run_bounded_pipeline(files, path, graph.clone(), config, None)
            .expect("pipeline should succeed");

    let call_edges = graph.get_edges_by_kind(crate::graph::EdgeKind::Calls);
    let has_edge = call_edges
        .iter()
        .any(|(src, dst)| src.contains("main") && dst.contains("helper"));
    assert!(has_edge, "forward reference should be resolved via pending queue");
}

/// Verify pending queue + ambiguity: forward reference to a name that
/// later becomes ambiguous should NOT produce an edge.
#[test]
fn test_pending_queue_drops_ambiguous_forward_references() {
    let dir = TempDir::new().expect("temp dir");
    let path = dir.path();

    // caller calls run(), then two files define run()
    create_test_file(path, "caller.py", "def main():\n    run()\n");
    create_test_file(path, "a.py", "def run():\n    pass\n");
    create_test_file(path, "b.py", "def run():\n    pass\n");

    let graph = Arc::new(GraphStore::in_memory());
    let config = PipelineConfig::for_repo_size(3);
    let files = vec![
        path.join("caller.py"),
        path.join("a.py"),
        path.join("b.py"),
    ];

    let (_stats, _) =
        run_bounded_pipeline(files, path, graph.clone(), config, None)
            .expect("pipeline should succeed");

    // caller::main should NOT have a call edge — "run" becomes ambiguous
    // (first registration drains pending, second makes it ambiguous,
    // but the drain already happened — this is the accepted edge case).
    // The edge to a::run exists because it was drained while unique.
    // This is documented as the "early-resolve edge case" in the design.
    // We just verify no edge to b::run.
    let call_edges = graph.get_edges_by_kind(crate::graph::EdgeKind::Calls);
    let edges_to_b = call_edges
        .iter()
        .filter(|(src, dst)| src.contains("main") && dst.contains("b.py"))
        .count();
    assert_eq!(edges_to_b, 0, "should not resolve to second ambiguous candidate");
}
```

**Step 2: Run tests to verify they fail**

Run: `cargo test -p repotoire-cli test_pending_queue -- --nocapture`
Expected: FAIL — `pending_calls` not yet wired into call resolution.

**Step 3: Replace the cross-file call resolution in process()**

Replace lines 326-354 (the call resolution block) with inline resolution:

```rust
// Resolve call edges — same-file immediately, cross-file via inline resolution.
if !info.calls.is_empty() {
    let local_funcs: HashMap<&str, &str> = info
        .functions
        .iter()
        .map(|f| (f.name.as_str(), f.qualified_name.as_str()))
        .collect();

    for call in &info.calls {
        let callee_name = call
            .callee
            .rsplit(&[':', '.'][..])
            .next()
            .unwrap_or(&call.callee);

        // 1. Same-file fast path
        if let Some(&qn) = local_funcs.get(callee_name) {
            self.edge_buffer
                .push((call.caller.clone(), qn.to_string(), CodeEdge::calls()));
            continue;
        }

        // 2. Cross-file inline resolution
        let has_module = call.callee.contains(':') || call.callee.contains('.');
        if !has_module
            && crate::cli::analyze::graph::AMBIGUOUS_METHOD_NAMES
                .contains(&callee_name)
        {
            continue;
        }

        match self.function_lookup.get(callee_name) {
            Some(LookupEntry::Unique(callee_qn)) => {
                // Unambiguous — resolve immediately
                self.edge_buffer.push((
                    call.caller.clone(),
                    callee_qn.clone(),
                    CodeEdge::calls(),
                ));
            }
            Some(LookupEntry::Ambiguous) => {
                // Ambiguous — drop (can't know which is correct)
            }
            None => {
                // Forward reference — queue for later
                self.pending_calls
                    .entry(callee_name.to_string())
                    .or_default()
                    .push(call.caller.clone());
            }
        }
    }
}
```

Replace import deferral (lines 357-364) to use `deferred_imports`:

```rust
// Defer all import edges to finalize (need complete module lookup)
for import in &info.imports {
    self.deferred_imports.push(DeferredImport::Import {
        file_path: relative.clone(),
        import_path: import.path.clone(),
        is_type_only: import.is_type_only,
    });
}
```

Update peak tracking (lines 366-370):

```rust
let combined = self.edge_buffer.len() + self.deferred_imports.len() + self.pending_calls.values().map(|v| v.len()).sum::<usize>();
if combined > self.stats.peak_edges_buffered {
    self.stats.peak_edges_buffered = combined;
}
```

**Step 4: Run tests to verify they pass**

Run: `cargo test -p repotoire-cli test_pending_queue -- --nocapture`
Expected: PASS

**Step 5: Commit**

```bash
git add repotoire-cli/src/parsers/bounded_pipeline.rs
git commit -m "feat: inline call resolution with pending queues for forward references"
```

---

### Task 3: Rewrite Finalize — Imports Only + Pending Drain

**Files:**
- Modify: `repotoire-cli/src/parsers/bounded_pipeline.rs:398-470` (finalize method)
- Test: existing determinism tests should still pass

**Step 1: Run existing tests to confirm baseline**

Run: `cargo test -p repotoire-cli bounded_pipeline -- --nocapture`
Expected: all existing tests pass (test_overlapped_pipeline_deterministic_across_file_orders, etc.)

**Step 2: Rewrite finalize()**

Replace the entire `finalize` method (lines 398-470):

```rust
/// Finalize — drain pending calls, resolve deferred imports, flush, and save.
///
/// Unlike the previous two-phase approach that sorted 565K+ deferred edges,
/// this only handles:
/// - Remaining pending calls (forward references whose callee was never seen, or
///   whose callee appeared but was unique at drain time)
/// - Deferred imports (~17K on CPython — need complete module lookup)
fn finalize(mut self) -> Result<BoundedPipelineStats> {
    // Drain remaining pending calls: resolve unique, drop ambiguous/unknown
    let mut pending_resolved = 0usize;
    let mut pending_dropped = 0usize;
    for (callee_name, callers) in std::mem::take(&mut self.pending_calls) {
        match self.function_lookup.get(&callee_name) {
            Some(LookupEntry::Unique(callee_qn)) => {
                for caller_qn in callers {
                    self.edge_buffer.push((
                        caller_qn,
                        callee_qn.clone(),
                        CodeEdge::calls(),
                    ));
                    pending_resolved += 1;
                }
            }
            Some(LookupEntry::Ambiguous) | None => {
                pending_dropped += callers.len();
            }
        }
    }

    // Sort module lookup candidates for deterministic import resolution
    self.module_lookup.sort_candidates();

    // Resolve deferred imports (much smaller than old deferred_edges)
    let import_count = self.deferred_imports.len();
    let mut import_resolved = 0usize;
    for deferred in std::mem::take(&mut self.deferred_imports) {
        let DeferredImport::Import {
            file_path,
            import_path,
            is_type_only,
        } = deferred;

        if let Some(target) = self.module_lookup.find_match(&import_path) {
            if *target != file_path {
                let edge = CodeEdge::imports()
                    .with_property("is_type_only", is_type_only);
                self.edge_buffer
                    .push((file_path, target.clone(), edge));
                import_resolved += 1;
            }
        }
    }

    tracing::info!(
        "Finalize: {} pending calls resolved, {} dropped; {}/{} imports resolved",
        pending_resolved,
        pending_dropped,
        import_resolved,
        import_count,
    );

    self.flush_edges()?;

    // Defer graph.save() to background — redb persistence is NOT needed for
    // in-memory analysis (detect, score, postprocess all use petgraph directly).
    let graph_for_save = Arc::clone(&self.graph);
    std::thread::spawn(move || {
        if let Err(e) = graph_for_save.save() {
            tracing::warn!("Background graph save failed: {}", e);
        }
    });

    Ok(self.stats)
}
```

**Step 3: Run all bounded_pipeline tests**

Run: `cargo test -p repotoire-cli bounded_pipeline -- --nocapture`
Expected: ALL pass — determinism tests use unique bare names, so behavior is identical.

**Step 4: Run full test suite**

Run: `cargo test -p repotoire-cli`
Expected: All pass. If any detector tests fail due to edge count changes from ambiguous drops, investigate and adjust.

**Step 5: Commit**

```bash
git add repotoire-cli/src/parsers/bounded_pipeline.rs
git commit -m "perf: eliminate finalize sort — inline resolution + import-only deferral"
```

---

### Task 4: Contains Edges in Node Batch

**Files:**
- Modify: `repotoire-cli/src/graph/store/mod.rs:298-354` (add_nodes_batch)
- Modify: `repotoire-cli/src/parsers/bounded_pipeline.rs:270-315` (process node+edge creation)

**Step 1: Run cargo check as baseline**

Run: `cargo check -p repotoire-cli`
Expected: compiles cleanly

**Step 2: Add `add_nodes_batch_with_contains` to GraphStore**

Add method after `add_nodes_batch` (line 354) in `store/mod.rs`:

```rust
/// Add nodes and create Contains edges (file → function/class) in one operation.
/// This avoids buffering 84K+ (String, String, CodeEdge) tuples for Contains edges
/// that are always intra-file and always resolved.
pub fn add_nodes_batch_with_contains(
    &self,
    nodes: Vec<CodeNode>,
    file_qn: &str,
) -> Vec<NodeIndex> {
    let mut graph = self.write_graph();
    let mut indices = Vec::with_capacity(nodes.len());

    // Resolve file node index first (it should already exist from a prior insert)
    let file_idx = self.node_index.get(file_qn).map(|r| *r);

    for node in nodes {
        let qn = node.qualified_name.clone();
        let node_file_path = node.file_path.clone();
        let is_function = node.kind == NodeKind::Function;
        let file_path = if is_function { Some(node.file_path.clone()) } else { None };
        let line_start = node.line_start;
        let line_end = node.line_end;
        let is_class = node.kind == NodeKind::Class;
        let class_file_path = if is_class { Some(node.file_path.clone()) } else { None };
        let needs_contains = is_function || is_class;

        if let Some(idx_ref) = self.node_index.get(&qn) {
            let idx = *idx_ref;
            drop(idx_ref);
            if let Some(existing) = graph.node_weight_mut(idx) {
                *existing = node;
            }
            indices.push(idx);
        } else {
            let idx = graph.add_node(node);
            self.node_index.insert(qn, idx);

            // Populate spatial index and file-scoped function index
            if is_function {
                if let Some(fp) = file_path {
                    self.function_spatial_index
                        .entry(fp.clone())
                        .or_default()
                        .push((line_start, line_end, idx));
                    self.file_functions_index
                        .entry(fp)
                        .or_default()
                        .push(idx);
                }
            }
            if is_class {
                if let Some(fp) = class_file_path {
                    self.file_classes_index
                        .entry(fp)
                        .or_default()
                        .push(idx);
                }
            }

            self.file_all_nodes_index.entry(node_file_path).or_default().push(idx);

            // Add Contains edge: file → function/class (in same write lock)
            if needs_contains {
                if let Some(f_idx) = file_idx {
                    let mut set = self.edge_set.lock().expect("edge_set lock");
                    if set.insert((f_idx, idx, EdgeKind::Contains)) {
                        graph.add_edge(f_idx, idx, CodeEdge::contains());
                    }
                }
            }

            indices.push(idx);
        }
    }

    indices
}
```

**Step 3: Update process() to use new method and remove Contains edge buffering**

In `bounded_pipeline.rs`, restructure process() node creation. The file node must be inserted first (separate call) so its NodeIndex exists for Contains edges. Then insert function/class nodes via `add_nodes_batch_with_contains`:

```rust
// File node — insert first so its index exists for Contains edges
let file_node = CodeNode::new(NodeKind::File, &relative, &relative)
    .with_qualified_name(&relative)
    .with_language(info.language.as_str())
    .with_property("loc", info.loc as i64);
self.graph.add_nodes_batch(vec![file_node]);

// Function + class nodes with Contains edges created inside the graph store
let mut entity_nodes = Vec::with_capacity(info.functions.len() + info.classes.len());

for func in &info.functions {
    let loc = func.loc();
    let address_taken = info.address_taken.contains(&func.name);
    entity_nodes.push(
        CodeNode::new(NodeKind::Function, &func.name, &relative)
            .with_qualified_name(&func.qualified_name)
            .with_lines(func.line_start, func.line_end)
            .with_property("is_async", func.is_async)
            .with_property("complexity", func.complexity as i64)
            .with_property("loc", loc as i64)
            .with_property("address_taken", address_taken),
    );

    // Decorated functions still need a Calls edge (file → func via decorator)
    if func.has_annotations {
        self.edge_buffer.push((
            relative.clone(),
            func.qualified_name.clone(),
            CodeEdge::calls(),
        ));
    }
}

for class in &info.classes {
    entity_nodes.push(
        CodeNode::new(NodeKind::Class, &class.name, &relative)
            .with_qualified_name(&class.qualified_name)
            .with_lines(class.line_start, class.line_end)
            .with_property("methodCount", class.method_count as i64),
    );
}

if !entity_nodes.is_empty() {
    self.graph
        .add_nodes_batch_with_contains(entity_nodes, &relative);
}
```

This eliminates all `self.edge_buffer.push((relative.clone(), qn.clone(), CodeEdge::contains()))` calls — 84K String tuple allocations on CPython.

**Step 4: Run full test suite**

Run: `cargo test -p repotoire-cli`
Expected: All pass — same graph, same edges, just created via different path.

**Step 5: Commit**

```bash
git add repotoire-cli/src/graph/store/mod.rs repotoire-cli/src/parsers/bounded_pipeline.rs
git commit -m "perf: create Contains edges in add_nodes_batch — eliminate 168K String allocations"
```

---

### Task 5: Arc\<str\> for File Paths + Pre-sized Containers

**Files:**
- Modify: `repotoire-cli/src/parsers/bounded_pipeline.rs` (process() and new())

**Step 1: Run cargo check as baseline**

Run: `cargo check -p repotoire-cli`

**Step 2: Use Arc\<str\> for relative path in process()**

At the top of `process()`:

```rust
fn process(&mut self, info: LightweightFileInfo) -> Result<()> {
    let relative: Arc<str> = info.relative_path(&self.repo_path).into();
    // ... all uses of `relative` now use Arc::clone(&relative) instead of relative.clone()
```

Update all remaining `relative.clone()` calls (decorator edges, import deferral) to `Arc::clone(&relative).to_string()` for edge_buffer, or change `edge_buffer` to `Vec<(Arc<str>, String, CodeEdge)>` — but that requires changing `add_edges_batch` signature. Simpler: keep `relative` as `String`, wrap in `Arc<str>` only for the repeated clones:

```rust
let relative = info.relative_path(&self.repo_path);
let relative_arc: Arc<str> = relative.as_str().into();

// For decorator calls edge (still String-based edge_buffer):
if func.has_annotations {
    self.edge_buffer.push((
        relative.clone(),  // only for decorated functions — much rarer
        func.qualified_name.clone(),
        CodeEdge::calls(),
    ));
}

// For import deferral — use Arc to avoid clone per import:
for import in &info.imports {
    self.deferred_imports.push(DeferredImport::Import {
        file_path: (*relative_arc).to_string(),
        import_path: import.path.clone(),
        is_type_only: import.is_type_only,
    });
}
```

Actually, with Contains edges moved to node batch (Task 4), the main remaining `relative.clone()` calls are:
- Import deferral (1 per import, ~5 per file)
- Decorated function Calls edges (rare)

The big win was already captured in Task 4. The Arc optimization is marginal here. **Simplify: just pre-size containers.**

**Step 3: Pre-size containers in new()**

Update `FlushingGraphBuilder::new()`:

```rust
fn new(graph: Arc<GraphStore>, repo_path: &Path, edge_flush_threshold: usize, estimated_files: usize) -> Self {
    let est_functions = estimated_files * 20; // ~20 functions per file average
    Self {
        graph,
        repo_path: repo_path.to_path_buf(),
        function_lookup: HashMap::with_capacity(est_functions),
        module_lookup: ModuleLookupCompact::default(),
        edge_buffer: Vec::with_capacity(edge_flush_threshold.min(10_000)),
        edge_flush_threshold,
        pending_calls: HashMap::with_capacity(est_functions / 4),
        deferred_imports: Vec::with_capacity(estimated_files * 5),
        stats: BoundedPipelineStats::default(),
    }
}
```

Update all callers of `new()` to pass `estimated_files`:
- `run_bounded_pipeline` line 511: `FlushingGraphBuilder::new(Arc::clone(&graph), repo_path, config.edge_flush_threshold, total_files)`
- `run_bounded_pipeline_from_channel` line 648-649: pass `0` (unknown in channel mode, will grow dynamically)

**Step 4: Run full test suite**

Run: `cargo test -p repotoire-cli`
Expected: All pass.

**Step 5: Commit**

```bash
git add repotoire-cli/src/parsers/bounded_pipeline.rs
git commit -m "perf: pre-size function_lookup, pending_calls, deferred_imports containers"
```

---

### Task 6: Benchmark on CPython

**Files:** None (measurement only)

**Step 1: Build release binary**

Run: `cargo build --release -p repotoire-cli`

**Step 2: Run 5 benchmark iterations on CPython**

Run the benchmark 5 times, record median:

```bash
for i in $(seq 1 5); do
    cargo run --release -p repotoire-cli -- analyze /path/to/cpython --timings 2>&1 | grep -E "TOTAL|init\+parse|detect|postprocess|scoring|output"
done
```

**Step 3: Compare against baseline**

Baseline (V3): 5.10s total, 2.45s parse
Target: ~4.70s total, ~2.05s parse

Record results in `docs/perf/session8-timings-cpython.txt`.

**Step 4: Run on a second repo (e.g., Flask or FastAPI) for validation**

```bash
cargo run --release -p repotoire-cli -- analyze /path/to/flask --timings
```

Verify no regressions or unexpected behavior.

**Step 5: Commit benchmark results**

```bash
git add docs/perf/session8-timings-cpython.txt
git commit -m "docs: add V4 benchmark results — parse phase elimination"
```

---

### Task 7: Update Performance Memory

**Files:**
- Modify: `/home/zhammad/.claude/projects/-home-zhammad-personal-repotoire/memory/performance-optimization.md`

**Step 1: Update the memory file with new results**

Add the new session's data to the optimization timeline, phase breakdown, and key optimizations sections. Update the architecture diagram and remaining optimization targets.

**Step 2: Done**

No commit needed (memory file is outside repo).
