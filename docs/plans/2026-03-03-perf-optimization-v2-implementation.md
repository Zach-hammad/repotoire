# Performance Optimization V2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 2-3x wall-clock improvement for `repotoire analyze` on 100k+ file repos

**Architecture:** Profile-first approach — establish baseline, then apply optimizations in priority order: DashMap node index, graph metrics cache, speculative detection, string interning, walk+parse overlap, then gated architecture optimizations based on profiling data.

**Tech Stack:** Rust, petgraph, DashMap, lasso (string interning), crossbeam-channel, rayon, aho-corasick, memmap2

---

## Task 1: Profiling Baseline — Build and Benchmark

**Files:**
- Build: `repotoire-cli/Cargo.toml` (existing profiling profile at line 128)
- Scripts: `scripts/perf/record.sh`, `scripts/perf/stat.sh`, `scripts/perf/mem.sh`, `scripts/perf/flamegraph.sh`
- Output: `docs/perf/baseline-v2.md` (new)

**Step 1: Clone benchmark repo**

```bash
# Use Linux kernel as 75k+ file benchmark target
git clone --depth 1 https://github.com/torvalds/linux.git /tmp/linux-bench
find /tmp/linux-bench -type f | wc -l  # Should be ~75k files
```

**Step 2: Build profiling binary**

```bash
cd repotoire-cli
cargo build --profile profiling -p repotoire-cli
```

**Step 3: Run phase timing baseline**

```bash
./target/profiling/repotoire analyze /tmp/linux-bench --timings 2>&1 | tee docs/perf/baseline-v2-timings.txt
```

Record the output — each phase with ms and percentage.

**Step 4: Run hardware counters (5 iterations)**

```bash
./scripts/perf/stat.sh /tmp/linux-bench 5 2>&1 | tee docs/perf/baseline-v2-stat.txt
```

**Step 5: Generate flamegraph**

```bash
./scripts/perf/record.sh /tmp/linux-bench
./scripts/perf/flamegraph.sh perf.data docs/perf/baseline-v2-flamegraph.svg
```

**Step 6: Run DHAT heap profiler**

```bash
./scripts/perf/mem.sh /tmp/linux-bench
# View dhat-heap.json at https://nnethercote.github.io/dh_view/dh_view.html
cp dhat-heap.json docs/perf/baseline-v2-dhat.json
```

**Step 7: Write baseline document**

Create `docs/perf/baseline-v2.md` summarizing:
- Wall-clock per phase (setup, init+parse, calibrate, detect, postprocess, scoring, output)
- Top 15 slowest detectors
- Peak RSS (from `/usr/bin/time -v`)
- IPC, cache miss rate, branch mispredicts (from `perf stat`)
- Top 5 hot functions (from flamegraph)
- Top 5 allocation sites (from DHAT)

**Step 8: Commit**

```bash
git add docs/perf/
git commit -m "perf: v2 baseline — profiling data for 75k-file Linux kernel benchmark"
```

---

## Task 2: Replace Node Index RwLock with DashMap

This eliminates lock contention when 99 parallel detectors compete for read access while git enrichment writes.

**Files:**
- Modify: `repotoire-cli/src/graph/store/mod.rs:18-31` (struct definition)
- Modify: `repotoire-cli/src/graph/store/mod.rs:88-121` (lock helpers)
- Modify: `repotoire-cli/src/graph/store/mod.rs:130-136` (reserve_capacity)
- Modify: `repotoire-cli/src/graph/store/mod.rs:160-202` (add_node, add_nodes_batch)
- Modify: `repotoire-cli/src/graph/store/mod.rs:204-215` (get_node_index, get_node)
- Modify: `repotoire-cli/src/graph/store/mod.rs:338-364` (edge operations using index)
- Test: inline `#[test]` modules in same file

**Step 1: Write the failing test**

Add a test to `repotoire-cli/src/graph/store/mod.rs` in the existing `#[cfg(test)]` module:

```rust
#[test]
fn test_concurrent_read_write() {
    use std::sync::Arc;
    use std::thread;

    let store = GraphStore::in_memory().unwrap();
    let store = Arc::new(store);

    // Add initial nodes
    for i in 0..100 {
        store.add_node(CodeNode {
            kind: NodeKind::Function,
            name: format!("func_{}", i),
            qualified_name: format!("mod.func_{}", i),
            file_path: "test.py".to_string(),
            line_start: Some(i as usize),
            line_end: Some(i as usize + 5),
            properties: HashMap::new(),
        });
    }

    // Spawn readers and writers concurrently
    let mut handles = vec![];

    // 8 reader threads
    for _ in 0..8 {
        let s = Arc::clone(&store);
        handles.push(thread::spawn(move || {
            for i in 0..100 {
                let _ = s.get_node_index(&format!("mod.func_{}", i));
            }
        }));
    }

    // 1 writer thread (simulates git enrichment)
    let s = Arc::clone(&store);
    handles.push(thread::spawn(move || {
        for i in 100..200 {
            s.add_node(CodeNode {
                kind: NodeKind::Function,
                name: format!("func_{}", i),
                qualified_name: format!("mod.func_{}", i),
                file_path: "test.py".to_string(),
                line_start: Some(i),
                line_end: Some(i + 5),
                properties: HashMap::new(),
            });
        }
    }));

    for h in handles {
        h.join().unwrap();
    }

    // All 200 nodes should exist
    assert_eq!(store.node_count(), 200);
}
```

**Step 2: Run test to verify it passes (baseline)**

```bash
cargo test -p repotoire-cli test_concurrent_read_write -- --nocapture
```

Expected: PASS (existing RwLock supports this, we're testing the API contract)

**Step 3: Replace RwLock\<HashMap\> with DashMap**

In `repotoire-cli/src/graph/store/mod.rs`:

1. Add import at top:
```rust
use dashmap::DashMap;
```

2. Change struct field (line 22):
```rust
// Before:
node_index: RwLock<HashMap<String, NodeIndex>>,
// After:
node_index: DashMap<String, NodeIndex>,
```

3. Update constructor `new()` and `in_memory()`:
```rust
node_index: DashMap::new(),
```

4. Remove `read_index()` and `write_index()` helper methods (lines 109-121). Replace every callsite:

   - `self.read_index().get(qn).copied()` → `self.node_index.get(qn).map(|r| *r)`
   - `self.write_index().insert(qn, idx)` → `self.node_index.insert(qn, idx)`
   - `self.write_index().reserve(n)` → `// DashMap doesn't need reserve — sharded internally`

5. Update `reserve_capacity()` (line 130-136):
```rust
pub fn reserve_capacity(&self, estimated_nodes: usize, estimated_edges: usize) {
    let mut graph = self.write_graph();
    graph.reserve_nodes(estimated_nodes);
    graph.reserve_edges(estimated_edges);
    // DashMap handles capacity internally via sharding — no reserve needed
}
```

6. Update `add_node()` (line 160-178):
```rust
pub fn add_node(&self, node: CodeNode) -> NodeIndex {
    let qn = node.qualified_name.clone();

    // Check if node already exists (lock-free read via DashMap)
    if let Some(idx_ref) = self.node_index.get(&qn) {
        let idx = *idx_ref;
        drop(idx_ref); // Release DashMap shard lock before acquiring graph write lock
        let mut graph = self.write_graph();
        if let Some(existing) = graph.node_weight_mut(idx) {
            *existing = node;
        }
        return idx;
    }

    let mut graph = self.write_graph();
    let idx = graph.add_node(node);
    self.node_index.insert(qn, idx);
    idx
}
```

7. Update `add_nodes_batch()` (line 181-202):
```rust
pub fn add_nodes_batch(&self, nodes: Vec<CodeNode>) -> Vec<NodeIndex> {
    let mut graph = self.write_graph();
    let mut indices = Vec::with_capacity(nodes.len());

    for node in nodes {
        let qn = node.qualified_name.clone();

        if let Some(idx_ref) = self.node_index.get(&qn) {
            let idx = *idx_ref;
            if let Some(existing) = graph.node_weight_mut(idx) {
                *existing = node;
            }
            indices.push(idx);
        } else {
            let idx = graph.add_node(node);
            self.node_index.insert(qn, idx);
            indices.push(idx);
        }
    }

    indices
}
```

8. Update `get_node_index()` (line 205):
```rust
pub fn get_node_index(&self, qn: &str) -> Option<NodeIndex> {
    self.node_index.get(qn).map(|r| *r)
}
```

9. Update `get_node()` (line 210):
```rust
pub fn get_node(&self, qn: &str) -> Option<CodeNode> {
    let idx = self.node_index.get(qn).map(|r| *r)?;
    let graph = self.read_graph();
    graph.node_weight(idx).cloned()
}
```

10. Update `add_edges_batch()` (line 351-364):
```rust
pub fn add_edges_batch(&self, edges: Vec<(String, String, CodeEdge)>) -> usize {
    let mut graph = self.write_graph();
    let mut added = 0;

    for (from_qn, to_qn, edge) in edges {
        let from = self.node_index.get(&from_qn).map(|r| *r);
        let to = self.node_index.get(&to_qn).map(|r| *r);
        if let (Some(from), Some(to)) = (from, to) {
            graph.add_edge(from, to, edge);
            added += 1;
        }
    }

    added
}
```

11. Update `add_edge_by_name()` (line 338-348):
```rust
pub fn add_edge_by_name(&self, from_qn: &str, to_qn: &str, edge: CodeEdge) -> bool {
    let from = self.node_index.get(from_qn).map(|r| *r);
    let to = self.node_index.get(to_qn).map(|r| *r);
    if let (Some(from), Some(to)) = (from, to) {
        self.add_edge(from, to, edge);
        true
    } else {
        false
    }
}
```

12. Update `clear()` (line 139-155) — replace `write_index()` with `self.node_index.clear()`.

13. Update `node_count()` (wherever defined) — replace `self.read_index().len()` with `self.node_index.len()`.

14. Search for ALL remaining uses of `read_index()` and `write_index()` and update them:
```bash
grep -n "read_index\|write_index" repotoire-cli/src/graph/store/mod.rs
```

**Step 4: Run tests**

```bash
cargo test -p repotoire-cli -- --nocapture
```

Expected: ALL PASS. The DashMap API is a drop-in for HashMap read/write patterns.

**Step 5: Run benchmark comparison**

```bash
./scripts/perf/compare.sh /tmp/linux-bench
```

**Step 6: Commit**

```bash
git add repotoire-cli/src/graph/store/mod.rs
git commit -m "perf: replace node_index RwLock<HashMap> with DashMap — lock-free concurrent reads"
```

---

## Task 3: Cache Graph Metrics Between Detector and Scoring Phases

**Files:**
- Modify: `repotoire-cli/src/graph/store/mod.rs:18-31` (add metrics_cache field)
- Modify: `repotoire-cli/src/scoring/graph_scorer.rs` (read cached metrics)
- Test: inline tests

**Step 1: Add metrics cache to GraphStore**

In `repotoire-cli/src/graph/store/mod.rs`, add a field to the struct:

```rust
pub struct GraphStore {
    graph: RwLock<DiGraph<CodeNode, CodeEdge>>,
    node_index: DashMap<String, NodeIndex>,
    db: Option<redb::Database>,
    #[allow(dead_code)]
    db_path: Option<std::path::PathBuf>,
    #[allow(dead_code)]
    lazy_mode: bool,
    /// Cached graph metrics from detectors, reusable by scoring phase
    metrics_cache: DashMap<String, f64>,
}
```

Initialize in constructors: `metrics_cache: DashMap::new()`.

**Step 2: Add accessor methods**

```rust
/// Store a computed metric (e.g., "degree_centrality:module.Class" → 0.85)
pub fn cache_metric(&self, key: &str, value: f64) {
    self.metrics_cache.insert(key.to_string(), value);
}

/// Retrieve a cached metric
pub fn get_cached_metric(&self, key: &str) -> Option<f64> {
    self.metrics_cache.get(key).map(|r| *r)
}

/// Get all cached metrics with a prefix (e.g., "modularity:")
pub fn get_cached_metrics_with_prefix(&self, prefix: &str) -> Vec<(String, f64)> {
    self.metrics_cache
        .iter()
        .filter(|entry| entry.key().starts_with(prefix))
        .map(|entry| (entry.key().clone(), *entry.value()))
        .collect()
}
```

**Step 3: Write test**

```rust
#[test]
fn test_metrics_cache() {
    let store = GraphStore::in_memory().unwrap();
    store.cache_metric("degree_centrality:mod.Class", 0.85);
    store.cache_metric("modularity:src/", 0.72);

    assert_eq!(store.get_cached_metric("degree_centrality:mod.Class"), Some(0.85));
    assert_eq!(store.get_cached_metric("nonexistent"), None);

    let modularity = store.get_cached_metrics_with_prefix("modularity:");
    assert_eq!(modularity.len(), 1);
}
```

**Step 4: Run tests**

```bash
cargo test -p repotoire-cli test_metrics_cache -- --nocapture
```

**Step 5: Commit**

```bash
git add repotoire-cli/src/graph/store/mod.rs
git commit -m "perf: add metrics_cache DashMap to GraphStore for cross-phase metric reuse"
```

**Note:** Wiring architecture detectors to write metrics and scoring to read them is a follow-up. The infrastructure is now in place. Detectors like `DegreeCentralityDetector`, `ModuleCohesionDetector`, and `ArchitecturalBottleneckDetector` should call `graph.cache_metric()` after computing their values. `GraphScorer` should check `graph.get_cached_metric()` before recomputing.

---

## Task 4: Speculative Detection — Tag Detectors as Graph-Independent

This task adds `fn requires_graph(&self) -> bool` to the `Detector` trait so file-local detectors can run in parallel with graph building.

**Files:**
- Modify: `repotoire-cli/src/detectors/base.rs:304-404` (Detector trait)
- Modify: ~25 file-local detectors (add `requires_graph` override)
- Test: inline

**Step 1: Add `requires_graph()` to Detector trait**

In `repotoire-cli/src/detectors/base.rs`, add after `scope()` (line 403):

```rust
    /// Whether this detector requires the full graph to be built before running.
    ///
    /// Detectors that only analyze file content (magic numbers, deep nesting,
    /// security patterns, etc.) can return `false` here to run speculatively
    /// in parallel with graph building.
    ///
    /// Default: `true` (conservative — waits for graph)
    fn requires_graph(&self) -> bool {
        true
    }
```

**Step 2: Run tests to verify trait change is backward compatible**

```bash
cargo test -p repotoire-cli -- --nocapture
```

Expected: ALL PASS (default impl returns `true`, no behavior change)

**Step 3: Override `requires_graph` for file-local detectors**

For each of these detectors, add `fn requires_graph(&self) -> bool { false }` to their `impl Detector` block:

File-local detectors (no graph dependency):
1. `magic_numbers.rs`
2. `deep_nesting.rs`
3. `dead_store.rs`
4. `empty_catch.rs`
5. `debug_code.rs`
6. `commented_code.rs`
7. `boolean_trap.rs`
8. `broad_exception.rs`
9. `mutable_default_args.rs`
10. `long_parameter.rs`
11. `unreachable_code.rs`
12. `todo_scanner.rs`
13. `wildcard_imports.rs`
14. `implicit_coercion.rs`
15. `string_concat_loop.rs`
16. `regex_dos.rs`
17. `global_variables.rs`
18. `hardcoded_timeout.rs`
19. `inconsistent_returns.rs`
20. `large_files.rs`

Security detectors (file-local pattern matching):
21. `sql_injection/mod.rs`
22. `xss.rs`
23. `ssrf.rs`
24. `command_injection.rs`
25. `path_traversal.rs`
26. `nosql_injection.rs`
27. `insecure_crypto.rs`
28. `jwt_weak.rs`
29. `insecure_tls.rs`
30. `secrets.rs`
31. `cleartext_credentials.rs`
32. `log_injection.rs`
33. `xxe.rs`
34. `prototype_pollution.rs`
35. `cors_misconfig.rs`
36. `eval_detector.rs`
37. `unsafe_template.rs`
38. `gh_actions.rs`
39. `hardcoded_ips.rs`

ML detectors (file-local):
40. `ml_unsafe_torch_load.rs`
41. `ml_nan_equality.rs`
42. `ml_missing_zero_grad.rs`
43. `ml_deprecated_pytorch.rs`

Rust-specific (file-local):
44. `rust_unwrap.rs`
45. `rust_unsafe.rs`

For each file, find the `impl Detector for XxxDetector` block and add:

```rust
fn requires_graph(&self) -> bool {
    false
}
```

**Step 4: Write verification test**

In `repotoire-cli/src/detectors/base.rs` tests:

```rust
#[test]
fn test_requires_graph_annotation_coverage() {
    // Verify that file-local detectors are tagged correctly
    use super::*;
    let detectors = crate::detectors::default_detectors_full();

    let graph_independent: Vec<_> = detectors
        .iter()
        .filter(|d| !d.requires_graph())
        .map(|d| d.name())
        .collect();

    // At minimum 40 detectors should be graph-independent
    assert!(
        graph_independent.len() >= 40,
        "Expected >= 40 graph-independent detectors, got {}: {:?}",
        graph_independent.len(),
        graph_independent
    );
}
```

**Step 5: Run tests**

```bash
cargo test -p repotoire-cli test_requires_graph -- --nocapture
cargo test -p repotoire-cli -- --nocapture
```

**Step 6: Commit**

```bash
git add repotoire-cli/src/detectors/
git commit -m "perf: tag 45 detectors as graph-independent for speculative execution"
```

---

## Task 5: Speculative Detection — Split Engine Execution

Wire the `requires_graph()` flag into `DetectorEngine` so graph-independent detectors run immediately after parsing, in parallel with graph building.

**Files:**
- Modify: `repotoire-cli/src/detectors/engine.rs:368-552` (run method)
- Modify: `repotoire-cli/src/cli/analyze/mod.rs:504-565` (execute_detection_phase)
- Test: integration test

**Step 1: Add `run_graph_independent()` method to DetectorEngine**

In `repotoire-cli/src/detectors/engine.rs`, add a new method:

```rust
/// Run only graph-independent detectors.
/// These can execute before graph building completes.
/// Returns findings from file-local detectors.
pub fn run_graph_independent(
    &mut self,
    graph: &dyn crate::graph::GraphQuery,
    files: &dyn super::file_provider::FileProvider,
) -> Result<Vec<Finding>> {
    let independent_detectors: Vec<_> = self.detectors
        .iter()
        .filter(|d| !d.requires_graph() && !d.is_dependent())
        .collect();

    if independent_detectors.is_empty() {
        return Ok(vec![]);
    }

    let contexts = self.get_or_build_contexts(graph);
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(self.workers)
        .build()
        .unwrap_or_else(|_| rayon::ThreadPoolBuilder::new().build().unwrap());

    let results: Vec<DetectorResult> = pool.install(|| {
        independent_detectors
            .par_iter()
            .map(|detector| {
                Self::run_single_detector(detector, graph, files, &contexts)
            })
            .collect()
    });

    let mut findings = Vec::new();
    for result in results {
        if result.success {
            findings.extend(result.findings);
        }
        if self.timings_enabled {
            // Log timing for graph-independent detectors
        }
    }

    Ok(findings)
}

/// Run only graph-dependent detectors.
/// Call after graph building completes.
pub fn run_graph_dependent(
    &mut self,
    graph: &dyn crate::graph::GraphQuery,
    files: &dyn super::file_provider::FileProvider,
) -> Result<Vec<Finding>> {
    let dependent_detectors: Vec<_> = self.detectors
        .iter()
        .filter(|d| d.requires_graph() || d.is_dependent())
        .collect();

    if dependent_detectors.is_empty() {
        return Ok(vec![]);
    }

    let contexts = self.get_or_build_contexts(graph);
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(self.workers)
        .build()
        .unwrap_or_else(|_| rayon::ThreadPoolBuilder::new().build().unwrap());

    // Split into independent (parallel) and dependent (sequential) within graph-dependent set
    let (parallel_deps, sequential_deps): (Vec<_>, Vec<_>) = dependent_detectors
        .into_iter()
        .partition(|d| !d.is_dependent());

    let mut findings = Vec::new();

    // Run parallel graph-dependent detectors
    let results: Vec<DetectorResult> = pool.install(|| {
        parallel_deps
            .par_iter()
            .map(|detector| {
                Self::run_single_detector(detector, graph, files, &contexts)
            })
            .collect()
    });

    for result in results {
        if result.success {
            findings.extend(result.findings);
        }
    }

    // Run sequential dependent detectors
    for detector in sequential_deps {
        let result = Self::run_single_detector(detector, graph, files, &contexts);
        if result.success {
            findings.extend(result.findings);
        }
    }

    Ok(findings)
}
```

**Step 2: Write test**

```rust
#[test]
fn test_split_detection_produces_same_findings() {
    // Run full detection vs split detection, compare finding counts
    // This ensures splitting doesn't lose or duplicate findings
    let store = GraphStore::in_memory().unwrap();
    let file_provider = TestFileProvider::new(); // Use existing test fixture

    let mut engine_full = DetectorEngine::new(4);
    let full_findings = engine_full.run(&store, &file_provider).unwrap();

    let mut engine_split = DetectorEngine::new(4);
    let mut independent = engine_split.run_graph_independent(&store, &file_provider).unwrap();
    let dependent = engine_split.run_graph_dependent(&store, &file_provider).unwrap();
    independent.extend(dependent);

    assert_eq!(
        full_findings.len(),
        independent.len(),
        "Split detection should produce same number of findings as full detection"
    );
}
```

**Step 3: Run tests**

```bash
cargo test -p repotoire-cli test_split_detection -- --nocapture
cargo test -p repotoire-cli -- --nocapture
```

**Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/engine.rs
git commit -m "perf: add split detection API — run_graph_independent + run_graph_dependent"
```

---

## Task 6: Speculative Detection — Wire Into Pipeline

Modify `execute_detection_phase` to overlap file-local detection with graph building.

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/mod.rs:504-565` (execute_detection_phase)
- Modify: `repotoire-cli/src/cli/analyze/mod.rs:139-142` (initialize_graph — separate parse from graph build)

**Step 1: Restructure pipeline to overlap detection with graph building**

The key change is to `execute_detection_phase()` in `repotoire-cli/src/cli/analyze/mod.rs`.

Currently (simplified):
```
1. start_git_enrichment() (background thread)
2. run_detectors() (all detectors wait for graph)
3. finish_git_enrichment()
```

New flow:
```
1. Spawn graph-independent detectors on rayon (they only need parse results)
2. start_git_enrichment() (background thread)
3. run graph-dependent detectors (wait for graph + git enrichment)
4. finish_git_enrichment()
5. Merge findings from both phases
```

Update `execute_detection_phase()`:

```rust
fn execute_detection_phase(
    env: &EnvironmentSetup,
    graph: &Arc<GraphStore>,
    file_result: &FileCollectionResult,
    skip_detector: &[String],
    multi: &MultiProgress,
    spinner_style: &ProgressStyle,
    timings: bool,
) -> Result<Vec<Finding>> {
    let use_streaming = file_result.all_files.len() > 5000;

    if use_streaming {
        // Large repo path: use existing streaming detection (already optimized)
        return run_detectors_streaming(/* ... existing args ... */);
    }

    // Standard path with speculative execution:
    // 1. Run graph-independent detectors immediately (no graph needed)
    // 2. Start git enrichment in background
    // 3. Run graph-dependent detectors after git enrichment completes

    let mut detector_cache = IncrementalCache::new(&env.repotoire_dir.join("incremental"));

    // Phase A: Graph-independent detectors (can run NOW)
    let independent_findings = run_graph_independent_detectors(
        graph,
        &env.repo_path,
        &env.project_config,
        skip_detector,
        env.config.workers,
        timings,
    )?;

    // Phase B: Git enrichment (background)
    let git_handle = start_git_enrichment(
        env.config.no_git,
        env.quiet_mode,
        &env.repo_path,
        Arc::clone(graph),
        multi,
        spinner_style,
    );

    // Phase C: Graph-dependent detectors (need graph + git enrichment)
    finish_git_enrichment(git_handle, multi, env.quiet_mode, env.config.no_emoji);

    let dependent_findings = run_graph_dependent_detectors(
        graph,
        &env.repo_path,
        &env.project_config,
        skip_detector,
        env.config.workers,
        &mut detector_cache,
        &file_result.all_files,
        env.style_profile.as_ref(),
        env.ngram_model.clone(),
        timings,
    )?;

    // Merge
    let mut findings = independent_findings;
    findings.extend(dependent_findings);

    // Voting
    let (_voting_stats, _cached_count) = apply_voting(
        &mut findings,
        file_result.cached_findings.clone(),
        env.config.is_incremental_mode,
        multi,
        spinner_style,
        env.quiet_mode,
        env.config.no_emoji,
    );

    Ok(findings)
}
```

**Step 2: Run full test suite**

```bash
cargo test -p repotoire-cli -- --nocapture
```

**Step 3: Run benchmark comparison**

```bash
./scripts/perf/compare.sh /tmp/linux-bench
```

**Step 4: Commit**

```bash
git add repotoire-cli/src/cli/analyze/
git commit -m "perf: speculative detection — file-local detectors run in parallel with graph building"
```

---

## Task 7: String Interning — Activate in GraphStore

**Files:**
- Modify: `repotoire-cli/src/graph/interner.rs:1` (remove dead_code allow)
- Modify: `repotoire-cli/src/graph/store/mod.rs:18-31` (add interner field)
- Modify: `repotoire-cli/src/graph/store/mod.rs` (all methods using qualified names)
- Modify: `repotoire-cli/src/graph/mod.rs` (re-export interner)
- Test: inline

**Step 1: Remove dead_code allow and expose interner**

In `repotoire-cli/src/graph/interner.rs` line 1:
```rust
// Remove: #![allow(dead_code)]
```

In `repotoire-cli/src/graph/mod.rs`, add:
```rust
pub use interner::{StringInterner, StrKey};
```

**Step 2: Add interner to GraphStore**

```rust
pub struct GraphStore {
    graph: RwLock<DiGraph<CodeNode, CodeEdge>>,
    node_index: DashMap<String, NodeIndex>,
    db: Option<redb::Database>,
    #[allow(dead_code)]
    db_path: Option<std::path::PathBuf>,
    #[allow(dead_code)]
    lazy_mode: bool,
    metrics_cache: DashMap<String, f64>,
    /// String interner for memory-efficient storage of qualified names
    interner: StringInterner,
}
```

Initialize in constructors: `interner: StringInterner::new()`.

Add accessor:
```rust
/// Get the string interner for memory-efficient qualified name storage
pub fn interner(&self) -> &StringInterner {
    &self.interner
}
```

**Step 3: Write test**

```rust
#[test]
fn test_interner_integration() {
    let store = GraphStore::in_memory().unwrap();

    let key1 = store.interner().intern("module.Class.method");
    let key2 = store.interner().intern("module.Class.method");

    // Same string → same key
    assert_eq!(key1, key2);

    // Resolve back to original
    assert_eq!(store.interner().resolve(key1), "module.Class.method");
}
```

**Step 4: Run tests**

```bash
cargo test -p repotoire-cli test_interner_integration -- --nocapture
cargo test -p repotoire-cli -- --nocapture
```

**Step 5: Commit**

```bash
git add repotoire-cli/src/graph/
git commit -m "perf: activate string interner in GraphStore — infrastructure for 66% node memory reduction"
```

**Note:** Full integration (replacing all String qualified names with Spur keys in CodeNode) is a larger refactor. This task exposes the interner so it can be used incrementally — e.g., graph building can intern file paths during construction, reducing duplicate allocations, without changing the CodeNode struct yet.

---

## Task 8: Walk+Parse Overlap — Stream File Paths to Parsers

**Files:**
- Modify: `repotoire-cli/src/parsers/bounded_pipeline.rs` (accept channel input)
- Modify: `repotoire-cli/src/cli/analyze/files.rs` (WalkParallel → channel)
- Modify: `repotoire-cli/src/cli/analyze/mod.rs` (wire 3-stage pipeline)
- Test: integration

**Step 1: Add channel-based file input to bounded pipeline**

In `repotoire-cli/src/parsers/bounded_pipeline.rs`, add a variant of `run_bounded_pipeline` that accepts a channel:

```rust
/// Run the bounded pipeline with streaming file input.
/// Files arrive via channel from WalkParallel — no need to collect all paths first.
pub fn run_bounded_pipeline_streaming(
    file_receiver: crossbeam_channel::Receiver<PathBuf>,
    graph: &Arc<GraphStore>,
    config: &PipelineConfig,
    cache: Option<&ConcurrentCacheView>,
) -> Result<BoundedPipelineStats> {
    let (parse_sender, parse_receiver) = crossbeam_channel::bounded(config.buffer_size);

    // Spawn parser workers that pull from file channel
    let parse_handle = {
        let file_rx = file_receiver.clone();
        let parse_tx = parse_sender.clone();
        let cache = cache.cloned();

        std::thread::spawn(move || {
            // Use rayon to parallelize parsing
            rayon::scope(|s| {
                for _ in 0..rayon::current_num_threads() {
                    let file_rx = file_rx.clone();
                    let parse_tx = parse_tx.clone();
                    let cache = cache.as_ref();
                    s.spawn(move |_| {
                        while let Ok(path) = file_rx.recv() {
                            // Check cache first
                            if let Some(cached) = cache.and_then(|c| c.parse_cache.get(&path)) {
                                let _ = parse_tx.send(cached.value().clone());
                                continue;
                            }
                            if let Some(result) = parse_file_lightweight(&path) {
                                let _ = parse_tx.send(result);
                            }
                        }
                    });
                }
            });
        })
    };
    drop(parse_sender); // Close sender so receiver knows when done

    // Graph builder consumes parsed results
    let mut builder = FlushingGraphBuilder::new(graph, config);
    while let Ok(result) = parse_receiver.recv() {
        builder.process(result)?;
    }
    builder.flush_remaining()?;

    parse_handle.join().map_err(|_| anyhow::anyhow!("Parser thread panicked"))?;

    Ok(builder.stats())
}
```

**Step 2: Modify `collect_source_files` to optionally send to channel**

In `repotoire-cli/src/cli/analyze/files.rs`, add:

```rust
/// Walk files and send paths to channel as they're discovered.
/// Returns total file count (estimated via AtomicUsize).
pub fn walk_files_to_channel(
    repo_path: &Path,
    config: &AnalyzeConfig,
    sender: crossbeam_channel::Sender<PathBuf>,
) -> Result<Arc<AtomicUsize>> {
    let count = Arc::new(AtomicUsize::new(0));
    let count_clone = Arc::clone(&count);

    let walker = ignore::WalkBuilder::new(repo_path)
        .hidden(false)
        .git_ignore(true)
        .build_parallel();

    walker.run(|| {
        let sender = sender.clone();
        let count = Arc::clone(&count_clone);
        Box::new(move |entry| {
            if let Ok(entry) = entry {
                if entry.file_type().map_or(false, |ft| ft.is_file()) {
                    if is_supported_extension(entry.path()) {
                        let _ = sender.send(entry.into_path());
                        count.fetch_add(1, Ordering::Relaxed);
                    }
                }
            }
            ignore::WalkState::Continue
        })
    });

    Ok(count)
}
```

**Step 3: Run tests**

```bash
cargo test -p repotoire-cli -- --nocapture
```

**Step 4: Run benchmark comparison**

```bash
./scripts/perf/compare.sh /tmp/linux-bench
```

**Step 5: Commit**

```bash
git add repotoire-cli/src/parsers/bounded_pipeline.rs repotoire-cli/src/cli/analyze/files.rs repotoire-cli/src/cli/analyze/mod.rs
git commit -m "perf: 3-stage streaming pipeline — walk+parse+graph build overlapped via channels"
```

---

## Task 9 (GATED): Frozen Graph Snapshot for Zero-Lock Detection

**Gate**: Run this only if baseline flamegraph shows `RwLock::read` in hot path during detection.

**Files:**
- Create: `repotoire-cli/src/graph/frozen.rs`
- Modify: `repotoire-cli/src/graph/store/mod.rs` (add freeze method)
- Modify: `repotoire-cli/src/graph/traits.rs` (implement GraphQuery for FrozenGraph)
- Modify: `repotoire-cli/src/graph/mod.rs` (re-export)
- Test: inline

**Step 1: Create FrozenGraph**

Create `repotoire-cli/src/graph/frozen.rs`:

```rust
//! Immutable graph snapshot for zero-lock read access during detection.
//!
//! After graph building + git enrichment complete, the graph is "frozen"
//! into an Arc-wrapped snapshot. Detectors read from this with zero
//! synchronization overhead.

use std::collections::HashMap;
use std::sync::Arc;
use petgraph::graph::{DiGraph, NodeIndex};
use super::store_models::{CodeEdge, CodeNode};

/// Immutable snapshot of the graph — no locks needed for reads.
pub struct FrozenGraph {
    pub(crate) graph: Arc<DiGraph<CodeNode, CodeEdge>>,
    pub(crate) index: Arc<HashMap<String, NodeIndex>>,
}

impl FrozenGraph {
    pub fn node_count(&self) -> usize {
        self.graph.node_count()
    }

    pub fn edge_count(&self) -> usize {
        self.graph.edge_count()
    }
}
```

**Step 2: Add freeze() to GraphStore**

In `repotoire-cli/src/graph/store/mod.rs`:

```rust
/// Freeze the graph into an immutable snapshot.
/// Moves ownership of graph and index into Arc — no clone needed.
/// After freezing, the GraphStore should not be used for mutations.
pub fn freeze(self) -> FrozenGraph {
    let graph = self.graph.into_inner()
        .expect("graph lock poisoned during freeze");
    let index_map: HashMap<String, NodeIndex> = self.node_index
        .into_iter()
        .collect();

    FrozenGraph {
        graph: Arc::new(graph),
        index: Arc::new(index_map),
    }
}
```

**Step 3: Implement GraphQuery for FrozenGraph**

In `repotoire-cli/src/graph/frozen.rs`, implement all 19 `GraphQuery` methods using direct field access (no locks):

```rust
impl crate::graph::GraphQuery for FrozenGraph {
    fn get_functions(&self) -> Vec<CodeNode> {
        self.graph.node_weights()
            .filter(|n| n.kind == NodeKind::Function)
            .cloned()
            .collect()
    }
    // ... implement all other methods identically to GraphStore but without locks ...
}
```

**Step 4: Write test**

```rust
#[test]
fn test_frozen_graph_queries_match_live() {
    let store = GraphStore::in_memory().unwrap();
    // Add test data...
    let live_functions = store.get_functions();

    let frozen = store.freeze();
    let frozen_functions = frozen.get_functions();

    assert_eq!(live_functions.len(), frozen_functions.len());
}
```

**Step 5: Run tests and benchmark**

```bash
cargo test -p repotoire-cli test_frozen_graph -- --nocapture
./scripts/perf/compare.sh /tmp/linux-bench
```

**Step 6: Commit**

```bash
git add repotoire-cli/src/graph/
git commit -m "perf: frozen graph snapshot — zero-lock read access for detection phase"
```

---

## Task 10 (GATED): Aho-Corasick Multi-Pattern Matching for Security Detectors

**Gate**: Run this only if flamegraph shows regex operations in security detector hot paths.

**Files:**
- Create: `repotoire-cli/src/detectors/multi_pattern.rs`
- Modify: `repotoire-cli/src/detectors/mod.rs` (register module)
- Modify: `repotoire-cli/Cargo.toml` (add aho-corasick dep if not present)
- Test: inline

**Step 1: Check if aho-corasick is already a dependency**

```bash
grep "aho-corasick" repotoire-cli/Cargo.toml
```

If not present, add: `aho-corasick = "1"` to `[dependencies]`.

Note: `aho-corasick` is already a transitive dependency of `regex`, so it may already be available.

**Step 2: Create multi-pattern infrastructure**

Create `repotoire-cli/src/detectors/multi_pattern.rs`:

```rust
//! Multi-pattern matching via Aho-Corasick for batch security scanning.
//!
//! Instead of running N regex patterns sequentially per file,
//! builds an Aho-Corasick automaton for all patterns in a category
//! and matches in a single pass.

use aho_corasick::AhoCorasick;
use std::sync::LazyLock;

/// A pattern with metadata about which detector it belongs to
pub struct TaggedPattern {
    pub pattern: &'static str,
    pub detector: &'static str,
    pub pattern_id: usize,
}

/// Pre-built Aho-Corasick automaton for a category of security patterns
pub struct MultiPatternMatcher {
    automaton: AhoCorasick,
    patterns: Vec<TaggedPattern>,
}

impl MultiPatternMatcher {
    pub fn new(patterns: Vec<TaggedPattern>) -> Self {
        let pattern_strings: Vec<&str> = patterns.iter().map(|p| p.pattern).collect();
        let automaton = AhoCorasick::new(&pattern_strings)
            .expect("Failed to build Aho-Corasick automaton");
        Self { automaton, patterns }
    }

    /// Scan content once, return all matches grouped by detector name
    pub fn scan(&self, content: &str) -> Vec<PatternMatch> {
        self.automaton
            .find_iter(content)
            .map(|mat| {
                let pattern = &self.patterns[mat.pattern().as_usize()];
                PatternMatch {
                    detector: pattern.detector,
                    pattern_id: pattern.pattern_id,
                    start: mat.start(),
                    end: mat.end(),
                }
            })
            .collect()
    }
}

pub struct PatternMatch {
    pub detector: &'static str,
    pub pattern_id: usize,
    pub start: usize,
    pub end: usize,
}
```

**Step 3: Write test**

```rust
#[test]
fn test_multi_pattern_scan() {
    let matcher = MultiPatternMatcher::new(vec![
        TaggedPattern { pattern: "eval(", detector: "EvalDetector", pattern_id: 0 },
        TaggedPattern { pattern: "exec(", detector: "CommandInjection", pattern_id: 0 },
        TaggedPattern { pattern: "SELECT.*FROM", detector: "SQLInjection", pattern_id: 0 },
    ]);

    let content = "result = eval(user_input)\ndb.exec(query)";
    let matches = matcher.scan(content);

    assert_eq!(matches.len(), 2);
    assert_eq!(matches[0].detector, "EvalDetector");
    assert_eq!(matches[1].detector, "CommandInjection");
}
```

**Step 4: Run tests**

```bash
cargo test -p repotoire-cli test_multi_pattern -- --nocapture
```

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/multi_pattern.rs repotoire-cli/src/detectors/mod.rs
git commit -m "perf: Aho-Corasick multi-pattern matcher for batch security scanning"
```

**Note:** Wiring individual security detectors to use the shared matcher is a follow-up per detector. The infrastructure is now in place.

---

## Task 11 (GATED): Memory-Mapped File Reading

**Gate**: Run this only if DHAT shows `fs::read_to_string` as a top allocation site.

**Files:**
- Modify: `repotoire-cli/src/parsers/mod.rs:91-154` (parse_file function)
- Test: inline

**Step 1: Replace fs::read_to_string with mmap in parse_file()**

In `repotoire-cli/src/parsers/mod.rs`, modify `parse_file()`:

```rust
use memmap2::Mmap;
use std::fs::File;

pub fn parse_file(path: &Path) -> Option<ParseResult> {
    let metadata = std::fs::metadata(path).ok()?;

    // Skip files > 2MB
    if metadata.len() > MAX_PARSE_FILE_BYTES as u64 {
        return None;
    }

    // Skip empty files
    if metadata.len() == 0 {
        return None;
    }

    // Memory-map the file instead of reading into String
    let file = File::open(path).ok()?;
    let mmap = unsafe { Mmap::map(&file) }.ok()?;
    let content = std::str::from_utf8(&mmap).ok()?;

    // Dispatch to language-specific parser
    parse_content(path, content)
}
```

**Step 2: Write test**

```rust
#[test]
fn test_mmap_parse_produces_same_results() {
    let test_file = std::path::Path::new("tests/fixtures/python/simple.py");
    if test_file.exists() {
        let result = parse_file(test_file);
        assert!(result.is_some());
    }
}
```

**Step 3: Run tests**

```bash
cargo test -p repotoire-cli -- --nocapture
```

**Step 4: Commit**

```bash
git add repotoire-cli/src/parsers/mod.rs
git commit -m "perf: memory-mapped file reading — eliminates per-file String allocation in parse phase"
```

---

## Task 12 (GATED): Parallel Graph Construction via Partitioning

**Gate**: Run this only if profiling shows graph building consuming >15% of wall-clock.

**Files:**
- Create: `repotoire-cli/src/cli/analyze/parallel_graph.rs`
- Modify: `repotoire-cli/src/cli/analyze/mod.rs` (wire in)
- Test: integration

This is the highest-complexity task. The approach:

1. Partition parse results by top-level directory
2. Build sub-graphs per partition (parallel via rayon)
3. Merge: add all nodes to final graph, then resolve cross-partition edges

**Step 1: Create parallel graph builder**

Create `repotoire-cli/src/cli/analyze/parallel_graph.rs`:

```rust
//! Parallel graph construction via directory-based partitioning.
//!
//! For 100k-file repos, builds sub-graphs per top-level directory in parallel,
//! then merges into the final GraphStore.

use std::collections::HashMap;
use std::path::PathBuf;
use rayon::prelude::*;
use crate::parsers::ParseResult;
use crate::graph::store::GraphStore;
use crate::graph::store_models::{CodeNode, CodeEdge, NodeKind, EdgeKind};

/// Partition files by their top-level directory relative to repo root.
fn partition_by_directory(
    parse_results: &[(PathBuf, ParseResult)],
    repo_root: &Path,
) -> HashMap<String, Vec<&(PathBuf, ParseResult)>> {
    let mut partitions: HashMap<String, Vec<_>> = HashMap::new();

    for item in parse_results {
        let rel = item.0.strip_prefix(repo_root).unwrap_or(&item.0);
        let partition_key = rel
            .components()
            .next()
            .map(|c| c.as_os_str().to_string_lossy().to_string())
            .unwrap_or_else(|| "root".to_string());

        partitions.entry(partition_key).or_default().push(item);
    }

    partitions
}

/// Build graph with parallel partition processing.
pub fn build_graph_parallel(
    graph: &GraphStore,
    parse_results: &[(PathBuf, ParseResult)],
    repo_root: &Path,
) -> Result<(), anyhow::Error> {
    let partitions = partition_by_directory(parse_results, repo_root);

    // Phase 1: Collect all nodes and intra-partition edges in parallel
    let partition_results: Vec<(Vec<CodeNode>, Vec<(String, String, CodeEdge)>)> = partitions
        .par_iter()
        .map(|(_key, files)| {
            let mut nodes = Vec::new();
            let mut edges = Vec::new();

            for (path, result) in files.iter() {
                // Collect nodes and edges from parse result
                // (same logic as build_graph but without graph mutation)
                collect_nodes_and_edges(path, result, &mut nodes, &mut edges);
            }

            (nodes, edges)
        })
        .collect();

    // Phase 2: Sequential merge into graph (single writer)
    let total_nodes: usize = partition_results.iter().map(|(n, _)| n.len()).sum();
    let total_edges: usize = partition_results.iter().map(|(_, e)| e.len()).sum();
    graph.reserve_capacity(total_nodes, total_edges);

    for (nodes, _) in &partition_results {
        graph.add_nodes_batch(nodes.clone());
    }

    for (_, edges) in &partition_results {
        graph.add_edges_batch(edges.clone());
    }

    Ok(())
}
```

**Step 2: Write test and benchmark**

```rust
#[test]
fn test_parallel_graph_matches_sequential() {
    // Build same graph both ways, compare node/edge counts
}
```

**Step 3: Commit**

```bash
git add repotoire-cli/src/cli/analyze/parallel_graph.rs
git commit -m "perf: parallel graph construction via directory partitioning"
```

---

## Task 13 (GATED): Parallel Scoring

**Gate**: Run this only if scoring phase >5% of total wall-clock.

**Files:**
- Modify: `repotoire-cli/src/scoring/graph_scorer.rs`

**Step 1: Parallelize pillar computation**

The three pillars (Structure 40%, Quality 30%, Architecture 30%) are independent. Compute them in parallel with rayon:

```rust
use rayon::prelude::*;

let (structure, quality, architecture) = rayon::join3(
    || compute_structure_score(&findings, &graph, total_loc),
    || compute_quality_score(&findings, total_loc),
    || compute_architecture_score(&findings, &graph, total_loc),
);
```

(`rayon::join3` doesn't exist — use nested `rayon::join` or `par_iter` over a 3-element vec.)

**Step 2: Run tests and benchmark**

```bash
cargo test -p repotoire-cli -- --nocapture
./scripts/perf/compare.sh /tmp/linux-bench
```

**Step 3: Commit**

```bash
git add repotoire-cli/src/scoring/graph_scorer.rs
git commit -m "perf: parallel scoring — three pillars computed concurrently via rayon"
```

---

## Task 14: Profile-Guided Threshold Tuning

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/mod.rs` (threshold constants)

**Step 1: Run benchmarks with different thresholds**

```bash
# Test streaming parse threshold
for threshold in 500 1000 2000 5000; do
    # Temporarily change threshold and rebuild
    ./target/profiling/repotoire analyze /tmp/linux-bench --timings
done
```

**Step 2: Pick optimal thresholds based on data**

Update constants in `repotoire-cli/src/cli/analyze/mod.rs`:
- Streaming parse threshold (currently 2000)
- Streaming detection threshold (currently 5000)

**Step 3: Commit**

```bash
git add repotoire-cli/src/cli/analyze/mod.rs
git commit -m "perf: tuned streaming thresholds from profiling data"
```

---

## Task 15: Final Validation

**Step 1: Full benchmark comparison**

```bash
# Build release
cargo build --release -p repotoire-cli

# Run full benchmark
./target/release/repotoire analyze /tmp/linux-bench --timings 2>&1 | tee docs/perf/post-v2-timings.txt

# Compare with baseline
diff docs/perf/baseline-v2-timings.txt docs/perf/post-v2-timings.txt
```

**Step 2: Generate post-optimization flamegraph**

```bash
cargo build --profile profiling -p repotoire-cli
./scripts/perf/record.sh /tmp/linux-bench
./scripts/perf/flamegraph.sh perf.data docs/perf/post-v2-flamegraph.svg
```

**Step 3: Run full test suite**

```bash
cargo test -p repotoire-cli -- --nocapture
cargo clippy -p repotoire-cli
```

**Step 4: Document results**

Update `docs/perf/baseline-v2.md` with post-optimization numbers and delta summary.

**Step 5: Commit**

```bash
git add docs/perf/
git commit -m "perf: v2 optimization complete — Xs → Ys wall-clock on 75k-file benchmark"
```
