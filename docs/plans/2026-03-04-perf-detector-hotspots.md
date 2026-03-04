# Detector Hotspot Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `repotoire analyze` complete on CPython (3.4k files) in under 60 seconds by fixing taint analysis O(n²) bottleneck, betweenness centrality O(n²), and misclassified detectors.

**Architecture:** Centralize taint analysis into a single engine that runs once for all 12 security detectors, with pre-filtering by sink keywords, shared file cache, and fixed inner loop complexity. Fix betweenness via sampling and remove unnecessary context building from graph-independent phase.

**Tech Stack:** Rust, DashMap, rayon, aho-corasick (transitive dep of regex)

---

## Task 1: Commit Misclassified Detector Fixes

Already done in working tree — 7 detectors flipped to `requires_graph = true`, test threshold updated.

**Files:**
- Modified: `repotoire-cli/src/detectors/infinite_loop.rs:238`
- Modified: `repotoire-cli/src/detectors/missing_await.rs:114`
- Modified: `repotoire-cli/src/detectors/ai_churn.rs`
- Modified: `repotoire-cli/src/detectors/ai_complexity_spike.rs`
- Modified: `repotoire-cli/src/detectors/ai_missing_tests.rs`
- Modified: `repotoire-cli/src/detectors/commented_code.rs`
- Modified: `repotoire-cli/src/detectors/single_char_names.rs`
- Modified: `repotoire-cli/src/detectors/base.rs:587-591`

**Step 1: Run tests**

```bash
cd repotoire-cli && cargo test test_requires_graph -- --nocapture
```

Expected: PASS, 35 graph-independent detectors.

**Step 2: Commit**

```bash
git add repotoire-cli/src/detectors/infinite_loop.rs repotoire-cli/src/detectors/missing_await.rs repotoire-cli/src/detectors/ai_churn.rs repotoire-cli/src/detectors/ai_complexity_spike.rs repotoire-cli/src/detectors/ai_missing_tests.rs repotoire-cli/src/detectors/commented_code.rs repotoire-cli/src/detectors/single_char_names.rs repotoire-cli/src/detectors/base.rs
git commit -m "fix: reclassify 7 detectors that use graph.get_functions() as graph-dependent"
```

---

## Task 2: Remove Context Building from Graph-Independent Phase

`run_graph_independent()` calls `get_or_build_contexts(graph)` at line 579. Graph-independent detectors don't use function contexts — this triggers O(n²) betweenness for no reason.

**Files:**
- Modify: `repotoire-cli/src/detectors/engine.rs:579`

**Step 1: Write test**

Add test in `repotoire-cli/src/detectors/engine.rs` in the existing `#[cfg(test)]` module:

```rust
#[test]
fn test_graph_independent_does_not_build_contexts() {
    // Run graph-independent detectors and verify function_contexts remains None
    let tmp = tempfile::tempdir().unwrap();
    let store = crate::graph::store::GraphStore::in_memory().unwrap();

    let mut engine = DetectorEngine::new(2);

    // Register a simple graph-independent detector
    for d in crate::detectors::default_detectors(tmp.path()) {
        if !d.requires_graph() {
            engine.register(d);
            break; // Just need one
        }
    }

    let file_provider = crate::detectors::file_provider::SourceFiles::new(vec![], tmp.path().to_path_buf());
    let _ = engine.run_graph_independent(&store, &file_provider);

    // function_contexts should NOT have been built
    assert!(engine.function_contexts().is_none(),
        "run_graph_independent should not build function contexts");
}
```

**Step 2: Run test — expect FAIL**

```bash
cargo test test_graph_independent_does_not_build_contexts -- --nocapture
```

Expected: FAIL — `function_contexts` is `Some` because `get_or_build_contexts` is called.

**Step 3: Fix — remove the call**

In `repotoire-cli/src/detectors/engine.rs`, in `run_graph_independent()`, replace line 579:

```rust
// BEFORE:
let contexts = self.get_or_build_contexts(graph);

// AFTER:
let contexts = Arc::new(HashMap::new());
```

Add `use std::collections::HashMap;` if not already imported.

**Step 4: Run test — expect PASS**

```bash
cargo test test_graph_independent_does_not_build_contexts -- --nocapture
```

**Step 5: Run full test suite**

```bash
cargo test
```

**Step 6: Commit**

```bash
git add repotoire-cli/src/detectors/engine.rs
git commit -m "perf: skip function context building in graph-independent detection phase"
```

---

## Task 3: Pre-Compute Fan-In for HMM Sort

**Files:**
- Modify: `repotoire-cli/src/detectors/engine.rs:196-232`

**Step 1: Write test**

Add to existing test module in `engine.rs`:

```rust
#[test]
fn test_build_hmm_contexts_does_not_call_get_callers_in_sort() {
    // This test verifies the HMM context build completes quickly
    // even with many functions (it should NOT do O(n log n) graph queries)
    use std::time::Instant;

    let tmp = tempfile::tempdir().unwrap();
    let store = crate::graph::store::GraphStore::in_memory().unwrap();

    // Add many functions to make O(n log n) graph queries observable
    for i in 0..1000 {
        store.add_node(crate::graph::store_models::CodeNode {
            qualified_name: format!("mod.func_{}", i),
            name: format!("func_{}", i),
            kind: crate::graph::store_models::NodeKind::Function,
            file_path: format!("file_{}.py", i % 50),
            line_start: 1,
            line_end: 10,
            ..Default::default()
        });
    }

    let mut engine = DetectorEngine::new(2);
    let start = Instant::now();
    let _ = engine.build_hmm_contexts(&store);
    let elapsed = start.elapsed();

    // Should complete in under 2 seconds (was O(n log n) graph queries before)
    assert!(elapsed.as_secs() < 2,
        "build_hmm_contexts took {:?} — likely still doing graph queries in sort", elapsed);
}
```

**Step 2: Implement fix**

In `repotoire-cli/src/detectors/engine.rs`, replace lines 196-208 (the sort + truncation block inside `build_hmm_contexts`):

```rust
// BEFORE:
if functions.len() > MAX_FUNCTIONS_FOR_HMM {
    warn!(...);
    functions.sort_by(|a, b| {
        let a_callers = graph.get_callers(&a.qualified_name).len();
        let b_callers = graph.get_callers(&b.qualified_name).len();
        b_callers.cmp(&a_callers)
    });
    functions.truncate(MAX_FUNCTIONS_FOR_HMM);
}

// AFTER:
if functions.len() > MAX_FUNCTIONS_FOR_HMM {
    warn!(
        "Limiting HMM analysis to {} functions (codebase has {})",
        MAX_FUNCTIONS_FOR_HMM,
        functions.len()
    );
    // Pre-compute fan-in in one O(n) pass instead of O(n log n) graph queries in sort
    let fan_in: HashMap<String, usize> = functions.iter()
        .map(|f| (f.qualified_name.clone(), graph.get_callers(&f.qualified_name).len()))
        .collect();
    functions.sort_by(|a, b| {
        let a_fi = fan_in.get(&a.qualified_name).copied().unwrap_or(0);
        let b_fi = fan_in.get(&b.qualified_name).copied().unwrap_or(0);
        b_fi.cmp(&a_fi)
    });
    functions.truncate(MAX_FUNCTIONS_FOR_HMM);
}
```

Also fix lines 219-232 — pre-compute fan-in/fan-out for the stats loop:

```rust
// BEFORE: per-function graph queries
for func in &functions {
    let fan_in = graph.get_callers(&func.qualified_name).len();
    let fan_out = graph.get_callees(&func.qualified_name).len();
    ...
}

// AFTER: reuse pre-computed fan_in, compute fan_out in batch
let fan_out_map: HashMap<String, usize> = functions.iter()
    .map(|f| (f.qualified_name.clone(), graph.get_callees(&f.qualified_name).len()))
    .collect();

for func in &functions {
    let fan_in = fan_in.get(&func.qualified_name).copied().unwrap_or(0);
    let fan_out = fan_out_map.get(&func.qualified_name).copied().unwrap_or(0);
    ...
}
```

Note: when `functions.len() <= MAX_FUNCTIONS_FOR_HMM`, the `fan_in` map doesn't exist yet. Build it unconditionally before the stats loop if needed, or compute inline for the small case.

**Step 3: Run tests**

```bash
cargo test test_build_hmm -- --nocapture
cargo test
```

**Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/engine.rs
git commit -m "perf: pre-compute fan-in for HMM sort — eliminate O(n log n) graph queries"
```

---

## Task 4: Sampled Betweenness Centrality

**Files:**
- Modify: `repotoire-cli/src/detectors/function_context.rs:303-367`
- Modify: `repotoire-cli/Cargo.toml` (add `rand` if needed)

**Step 1: Check if `rand` is available**

```bash
grep "rand" repotoire-cli/Cargo.toml
```

If not present, add `rand = "0.8"` to `[dependencies]`.

**Step 2: Write test**

Add to `function_context.rs` test module:

```rust
#[test]
fn test_sampled_betweenness_completes_quickly_for_large_graphs() {
    use std::time::Instant;

    let store = crate::graph::store::GraphStore::in_memory().unwrap();

    // Create a graph with 5000 functions and some edges
    for i in 0..5000 {
        store.add_node(crate::graph::store_models::CodeNode {
            qualified_name: format!("mod.func_{}", i),
            name: format!("func_{}", i),
            kind: crate::graph::store_models::NodeKind::Function,
            file_path: format!("file_{}.py", i % 100),
            line_start: 1,
            line_end: 10,
            ..Default::default()
        });
    }
    // Add some call edges
    for i in 0..4000 {
        store.add_relationship(
            &format!("mod.func_{}", i),
            &format!("mod.func_{}", i + 1),
            crate::graph::store_models::EdgeKind::Calls,
        );
    }

    let start = Instant::now();
    let builder = FunctionContextBuilder::new(&store);
    let contexts = builder.build();
    let elapsed = start.elapsed();

    assert!(!contexts.is_empty());
    // With sampled betweenness (K=500), should complete in < 10 seconds
    // Full betweenness on 5000 nodes would take 60+ seconds
    assert!(elapsed.as_secs() < 10,
        "FunctionContextBuilder took {:?} for 5000 functions — sampling not working?", elapsed);
}
```

**Step 3: Implement sampled Brandes**

In `repotoire-cli/src/detectors/function_context.rs`, replace `calculate_betweenness` (lines 303-367):

```rust
fn calculate_betweenness(&self, adj: &[Vec<usize>]) -> Vec<f64> {
    let n = adj.len();
    if n == 0 {
        return vec![];
    }

    // Sample K source nodes for approximate betweenness (Brandes sampling)
    // K=500 gives ~95% rank correlation with exact betweenness
    const SAMPLE_SIZE: usize = 500;
    let k = n.min(SAMPLE_SIZE);

    let source_nodes: Vec<usize> = if k == n {
        // Small graph — exact computation
        (0..n).collect()
    } else {
        // Large graph — sample random source nodes
        use rand::seq::SliceRandom;
        let mut rng = rand::thread_rng();
        let mut indices: Vec<usize> = (0..n).collect();
        indices.shuffle(&mut rng);
        indices.truncate(k);
        indices
    };

    let scale = n as f64 / k as f64;

    // Parallel Brandes: each source node computed independently
    let partial_centralities: Vec<Vec<f64>> = source_nodes
        .into_par_iter()
        .map(|s| {
            let mut centrality = vec![0.0; n];
            let mut stack = Vec::new();
            let mut predecessors: Vec<Vec<usize>> = vec![vec![]; n];
            let mut sigma = vec![0.0; n];
            let mut dist = vec![-1i64; n];

            sigma[s] = 1.0;
            dist[s] = 0;

            let mut queue = VecDeque::new();
            queue.push_back(s);

            // BFS
            while let Some(v) = queue.pop_front() {
                stack.push(v);
                for &w in &adj[v] {
                    if dist[w] < 0 {
                        queue.push_back(w);
                        dist[w] = dist[v] + 1;
                    }
                    if dist[w] == dist[v] + 1 {
                        sigma[w] += sigma[v];
                        predecessors[w].push(v);
                    }
                }
            }

            // Back-propagation
            let mut delta = vec![0.0; n];
            while let Some(w) = stack.pop() {
                for &v in &predecessors[w] {
                    delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w]);
                }
                if w != s {
                    centrality[w] += delta[w];
                }
            }

            centrality
        })
        .collect();

    // Sum partial centralities and apply scale factor
    let mut centrality = vec![0.0; n];
    for partial in partial_centralities {
        for (i, &c) in partial.iter().enumerate() {
            centrality[i] += c;
        }
    }

    if k < n {
        for c in &mut centrality {
            *c *= scale;
        }
    }

    centrality
}
```

**Step 4: Run tests**

```bash
cargo test test_sampled_betweenness -- --nocapture
cargo test function_context -- --nocapture
cargo test
```

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/function_context.rs repotoire-cli/Cargo.toml
git commit -m "perf: sampled Brandes betweenness — K=500 source nodes for O(K*E) instead of O(N*E)"
```

---

## Task 5: Shared File Content Cache

**Files:**
- Create: `repotoire-cli/src/detectors/file_cache.rs`
- Modify: `repotoire-cli/src/detectors/mod.rs` (register module)

**Step 1: Create FileContentCache**

Create `repotoire-cli/src/detectors/file_cache.rs`:

```rust
//! Shared file content cache for cross-detector file access.
//!
//! Uses DashMap for lock-free concurrent reads. Arc<String> avoids cloning
//! file contents when multiple detectors access the same file.

use dashmap::DashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Maximum file size to cache (2MB, matches parser guardrail)
const MAX_CACHE_FILE_SIZE: u64 = 2 * 1024 * 1024;

/// Thread-safe shared file content cache.
///
/// Populated lazily on first access. All consumers share the same Arc<String>
/// allocation — zero copying for repeated file access across detectors.
pub struct FileContentCache {
    cache: DashMap<PathBuf, Arc<String>>,
}

impl FileContentCache {
    pub fn new() -> Self {
        Self {
            cache: DashMap::new(),
        }
    }

    /// Get file content, reading from disk on cache miss.
    /// Returns None for files that don't exist, aren't UTF-8, or exceed 2MB.
    pub fn get_or_read(&self, path: &Path) -> Option<Arc<String>> {
        if let Some(entry) = self.cache.get(path) {
            return Some(Arc::clone(entry.value()));
        }

        // Check size before reading
        if let Ok(meta) = std::fs::metadata(path) {
            if meta.len() > MAX_CACHE_FILE_SIZE {
                return None;
            }
        }

        let content = std::fs::read_to_string(path).ok()?;
        let arc = Arc::new(content);
        self.cache.insert(path.to_path_buf(), Arc::clone(&arc));
        Some(arc)
    }

    /// Number of cached files
    #[allow(dead_code)]
    pub fn len(&self) -> usize {
        self.cache.len()
    }
}

impl Default for FileContentCache {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn test_file_cache_reads_and_caches() {
        let tmp = tempfile::tempdir().unwrap();
        let file_path = tmp.path().join("test.py");
        std::fs::write(&file_path, "print('hello')").unwrap();

        let cache = FileContentCache::new();

        // First read — cache miss
        let content1 = cache.get_or_read(&file_path).unwrap();
        assert_eq!(&*content1, "print('hello')");
        assert_eq!(cache.len(), 1);

        // Second read — cache hit, same Arc
        let content2 = cache.get_or_read(&file_path).unwrap();
        assert!(Arc::ptr_eq(&content1, &content2));
    }

    #[test]
    fn test_file_cache_skips_large_files() {
        let tmp = tempfile::tempdir().unwrap();
        let file_path = tmp.path().join("huge.py");
        let mut f = std::fs::File::create(&file_path).unwrap();
        // Write 3MB — exceeds 2MB limit
        f.write_all(&vec![b'x'; 3 * 1024 * 1024]).unwrap();

        let cache = FileContentCache::new();
        assert!(cache.get_or_read(&file_path).is_none());
    }

    #[test]
    fn test_file_cache_returns_none_for_missing_file() {
        let cache = FileContentCache::new();
        assert!(cache.get_or_read(Path::new("/nonexistent/file.py")).is_none());
    }
}
```

**Step 2: Register module**

In `repotoire-cli/src/detectors/mod.rs`, add:

```rust
pub mod file_cache;
pub use file_cache::FileContentCache;
```

**Step 3: Run tests**

```bash
cargo test file_cache -- --nocapture
cargo test
```

**Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/file_cache.rs repotoire-cli/src/detectors/mod.rs
git commit -m "perf: add shared FileContentCache — DashMap + Arc<String> for zero-copy cross-detector file access"
```

---

## Task 6: Pre-Filter Files by Sink Keywords

**Files:**
- Modify: `repotoire-cli/src/detectors/taint/mod.rs` (add quick_reject_patterns to TaintCategory)
- Modify: `repotoire-cli/src/detectors/data_flow.rs` (add pre-filter in run_intra_function_taint)

**Step 1: Add quick-reject patterns to TaintCategory**

In `repotoire-cli/src/detectors/taint/types.rs` (or wherever TaintCategory is defined), add a method:

```rust
impl TaintCategory {
    /// Literal strings that MUST appear in file content for this taint category
    /// to be relevant. Files without any of these patterns are skipped entirely.
    pub fn quick_reject_patterns(&self) -> &'static [&'static str] {
        match self {
            TaintCategory::SqlInjection => &["execute", "cursor", "query", "SELECT", "INSERT", "UPDATE", "DELETE", "sql", "SQL", "db."],
            TaintCategory::CommandInjection => &["exec", "spawn", "system", "popen", "subprocess", "shell", "Process"],
            TaintCategory::Xss => &["innerHTML", "document.write", "dangerouslySetInnerHTML", "render", "template", "html"],
            TaintCategory::Ssrf => &["fetch", "request", "http", "urllib", "requests.get", "curl", "urlopen"],
            TaintCategory::PathTraversal => &["open(", "readFile", "readdir", "path.join", "os.path", "file_get_contents"],
            TaintCategory::CodeInjection => &["eval(", "exec(", "compile(", "Function(", "setInterval(", "setTimeout("],
            TaintCategory::LogInjection => &["log(", "logger", "logging", "console.log", "print(", "warn(", "error("],
        }
    }

    /// Check if file content might contain relevant sinks for this category.
    pub fn file_might_be_relevant(&self, content: &str) -> bool {
        self.quick_reject_patterns().iter().any(|p| content.contains(p))
    }
}
```

**Step 2: Add pre-filter to run_intra_function_taint**

In `repotoire-cli/src/detectors/data_flow.rs`, at the top of `run_intra_function_taint()` (line ~489), after reading the file content, add:

```rust
// Pre-filter: skip files that don't contain any relevant sink patterns
if !category.file_might_be_relevant(&content) {
    continue;
}
```

**Step 3: Write test**

```rust
#[test]
fn test_pre_filter_skips_irrelevant_files() {
    // A file with no SQL patterns should produce no SQL injection findings
    let content = "def hello():\n    print('world')\n";
    assert!(!TaintCategory::SqlInjection.file_might_be_relevant(content));

    // A file with SQL patterns should pass the filter
    let content_sql = "cursor.execute(query)";
    assert!(TaintCategory::SqlInjection.file_might_be_relevant(content_sql));
}
```

**Step 4: Run tests**

```bash
cargo test test_pre_filter -- --nocapture
cargo test
```

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/taint/ repotoire-cli/src/detectors/data_flow.rs
git commit -m "perf: pre-filter files by sink keywords — skip 90%+ of irrelevant files before taint analysis"
```

---

## Task 7: Fix Inner Loop Complexity in check_sink_call

**Files:**
- Modify: `repotoire-cli/src/detectors/data_flow.rs:293-330`

**Step 1: Pre-lowercase sinks at construction**

In `HeuristicFlow` (or wherever sinks are initialized), pre-compute lowercased sink set once:

```rust
// In HeuristicFlow or as a lazy static
let sinks_lower: HashSet<String> = sinks.iter().map(|s| s.to_lowercase()).collect();
```

**Step 2: Fix check_sink_call**

Replace the nested loop (lines 293-330) with:

```rust
fn check_sink_call(
    &self,
    line: &str,
    line_num: usize,
    tainted: &HashMap<String, TaintSource>,
    sinks: &HashSet<String>,
    sanitized: &HashSet<String>,
) -> Vec<SinkReach> {
    let mut reaches = Vec::new();
    let line_lower = line.to_lowercase();

    // Single pass: find which sinks appear in this line
    let matching_sinks: Vec<&String> = sinks.iter()
        .filter(|s| line_lower.contains(s.as_str()))
        .collect();

    if matching_sinks.is_empty() {
        return reaches;
    }

    // Only check tainted vars for lines that actually contain a sink
    for sink in matching_sinks {
        for (var, source) in tainted {
            if sanitized.contains(var) {
                continue;
            }
            if line_contains_var_in_call(line, sink, var) {
                reaches.push(SinkReach {
                    sink_name: sink.clone(),
                    line_num,
                    source: source.clone(),
                    var_name: var.clone(),
                });
            }
        }
    }
    reaches
}
```

Key change: sinks should already be lowercased (pre-computed), eliminating per-line `to_lowercase()` calls on sinks. The early return on `matching_sinks.is_empty()` skips the tainted var loop entirely for most lines.

**Step 3: Run tests**

```bash
cargo test data_flow -- --nocapture
cargo test
```

**Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/data_flow.rs
git commit -m "perf: pre-lowercase sinks + early return in check_sink_call — eliminate per-line allocations"
```

---

## Task 8: Centralized Taint Engine

This is the largest task. Create a centralized taint engine that runs once and shares results across all 12 security detectors.

**Files:**
- Create: `repotoire-cli/src/detectors/taint/engine.rs`
- Modify: `repotoire-cli/src/detectors/taint/mod.rs` (register module)
- Modify: `repotoire-cli/src/detectors/engine.rs` (add taint_results field, run centralized taint before graph-dependent phase)

**Step 1: Create TaintEngine**

Create `repotoire-cli/src/detectors/taint/engine.rs`:

```rust
//! Centralized taint engine — runs taint analysis once for all categories.
//!
//! Instead of 12 security detectors each independently iterating all functions
//! and reading all files, this engine:
//! 1. Reads each file ONCE into shared FileContentCache
//! 2. Pre-filters files by category sink keywords
//! 3. Iterates each function ONCE
//! 4. For each function body, checks ALL categories' sinks
//! 5. Returns results grouped by category

use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;

use super::types::{TaintCategory, TaintPath};
use super::TaintAnalyzer;
use crate::detectors::file_cache::FileContentCache;
use crate::graph::GraphQuery;

/// Results from centralized taint analysis, grouped by category.
pub struct TaintResults {
    /// Cross-function taint paths (from graph-based trace_taint)
    pub cross_function: HashMap<TaintCategory, Vec<TaintPath>>,
    /// Intra-function taint paths (from heuristic data flow)
    pub intra_function: HashMap<TaintCategory, Vec<TaintPath>>,
}

impl TaintResults {
    pub fn empty() -> Self {
        Self {
            cross_function: HashMap::new(),
            intra_function: HashMap::new(),
        }
    }

    /// Get all taint paths for a category (both cross-function and intra-function)
    pub fn get(&self, category: TaintCategory) -> Vec<TaintPath> {
        let mut paths = Vec::new();
        if let Some(cross) = self.cross_function.get(&category) {
            paths.extend(cross.iter().cloned());
        }
        if let Some(intra) = self.intra_function.get(&category) {
            paths.extend(intra.iter().cloned());
        }
        paths
    }
}

/// Run centralized taint analysis for all categories.
///
/// This replaces 12 independent calls to `run_intra_function_taint()` with
/// a single pass that shares file reads and function iteration.
pub fn run_centralized_taint(
    analyzer: &TaintAnalyzer,
    graph: &dyn GraphQuery,
    repository_path: &Path,
    file_cache: &FileContentCache,
    categories: &[TaintCategory],
) -> TaintResults {
    let mut results = TaintResults::empty();

    // Phase 1: Cross-function taint (graph-based) — one pass per category
    for &category in categories {
        let cross_paths = analyzer.trace_taint(graph, category);
        if !cross_paths.is_empty() {
            results.cross_function.insert(category, cross_paths);
        }
    }

    // Phase 2: Intra-function taint (heuristic) — single pass over all functions
    let functions = graph.get_functions();
    if functions.is_empty() {
        return results;
    }

    // Pre-compute which categories are relevant per file
    // (avoids re-checking file content for each function)
    let mut file_categories: HashMap<String, Vec<TaintCategory>> = HashMap::new();

    for func in &functions {
        let full_path = repository_path.join(&func.file_path);
        let content = match file_cache.get_or_read(&full_path) {
            Some(c) => c,
            None => continue,
        };

        // Determine relevant categories for this file (cached per file)
        let relevant = file_categories
            .entry(func.file_path.clone())
            .or_insert_with(|| {
                categories.iter()
                    .filter(|cat| cat.file_might_be_relevant(&content))
                    .copied()
                    .collect()
            });

        if relevant.is_empty() {
            continue;
        }

        // Extract function body
        let lines: Vec<&str> = content.lines().collect();
        let line_start = func.line_start as usize;
        let line_end = func.line_end as usize;

        if line_end == 0 || line_end > lines.len() || line_start == 0 {
            continue;
        }

        let func_body = lines[line_start.saturating_sub(1)..line_end].join("\n");

        // Run heuristic taint analysis for each relevant category
        for &category in relevant.iter() {
            let sinks = analyzer.sinks_for_category(category);
            let sources = analyzer.sources_for_category(category);
            let sanitizers = analyzer.sanitizers_for_category(category);

            // Use the existing HeuristicFlow analysis
            let flow = crate::detectors::data_flow::HeuristicFlow::new(
                sources.clone(),
                sinks.clone(),
                sanitizers.clone(),
            );

            let taint_paths = flow.analyze_function_body(
                &func_body,
                &func.qualified_name,
                &func.file_path,
                func.line_start,
            );

            if !taint_paths.is_empty() {
                results.intra_function
                    .entry(category)
                    .or_default()
                    .extend(taint_paths);
            }
        }
    }

    results
}
```

Note: The exact API of `TaintAnalyzer` methods (`sinks_for_category`, `sources_for_category`, `sanitizers_for_category`) and `HeuristicFlow::analyze_function_body` may need adapting based on the actual existing API. The implementer should:
1. Read the existing `TaintAnalyzer` interface in `taint/mod.rs`
2. Read `HeuristicFlow` in `data_flow.rs`
3. Adapt the centralized engine to use the existing API, adding accessor methods if needed
4. The key constraint: iterate functions ONCE, read files ONCE, check all categories per function

**Step 2: Add TaintResults to DetectorEngine**

In `repotoire-cli/src/detectors/engine.rs`, add field to `DetectorEngine` struct:

```rust
taint_results: Option<Arc<TaintResults>>,
file_cache: Arc<FileContentCache>,
```

Initialize in constructor:

```rust
taint_results: None,
file_cache: Arc::new(FileContentCache::new()),
```

Add method:

```rust
/// Run centralized taint analysis (call before graph-dependent detectors)
pub fn run_centralized_taint(&mut self, graph: &dyn GraphQuery, repo_path: &Path) {
    let analyzer = TaintAnalyzer::new(repo_path);
    let categories = vec![
        TaintCategory::SqlInjection,
        TaintCategory::CommandInjection,
        TaintCategory::Xss,
        TaintCategory::Ssrf,
        TaintCategory::PathTraversal,
        TaintCategory::CodeInjection,
        TaintCategory::LogInjection,
    ];
    let results = crate::detectors::taint::engine::run_centralized_taint(
        &analyzer, graph, repo_path, &self.file_cache, &categories,
    );
    self.taint_results = Some(Arc::new(results));
}

/// Get cached taint results
pub fn taint_results(&self) -> Option<&Arc<TaintResults>> {
    self.taint_results.as_ref()
}
```

Call `run_centralized_taint()` at the start of `run_graph_dependent()`, before running individual detectors.

**Step 3: Modify 12 security detectors**

Each of the 12 security detectors needs to be modified to consume cached taint results instead of running its own taint analysis. The pattern for each detector:

```rust
// BEFORE (in each detector's detect() method):
let mut taint_paths = self.taint_analyzer.trace_taint(graph, TaintCategory::SqlInjection);
let intra_paths = crate::detectors::data_flow::run_intra_function_taint(
    &self.taint_analyzer, graph, TaintCategory::SqlInjection, &self.repository_path,
);
taint_paths.extend(intra_paths);

// AFTER:
// Taint results are pre-computed by the centralized engine.
// Access them via a new method on the detector trait or passed as parameter.
```

The exact wiring depends on how the `Detector` trait passes context. Options:
1. Add `taint_results: Option<&TaintResults>` parameter to `detect()` — breaking change
2. Store `Arc<TaintResults>` on DetectorEngine, pass to detectors via `run_single_detector`
3. Use a `DetectorContext` struct that wraps graph + taint_results + file_cache

The implementer should choose the approach that minimizes changes to the `Detector` trait. Option 2 is recommended — pass through `run_single_detector` and let security detectors downcast or check for it.

**Affected files (12):**
1. `repotoire-cli/src/detectors/sql_injection/mod.rs:811`
2. `repotoire-cli/src/detectors/command_injection.rs:66`
3. `repotoire-cli/src/detectors/xss.rs:43`
4. `repotoire-cli/src/detectors/ssrf.rs:43`
5. `repotoire-cli/src/detectors/path_traversal.rs:54`
6. `repotoire-cli/src/detectors/nosql_injection.rs:323`
7. `repotoire-cli/src/detectors/xxe.rs:352`
8. `repotoire-cli/src/detectors/eval_detector.rs:607`
9. `repotoire-cli/src/detectors/log_injection.rs:97`
10. `repotoire-cli/src/detectors/unsafe_template.rs:685`
11. `repotoire-cli/src/detectors/insecure_deserialize.rs:298`
12. `repotoire-cli/src/detectors/prototype_pollution.rs:317`

**Step 4: Run full test suite**

```bash
cargo test
cargo clippy
```

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/taint/ repotoire-cli/src/detectors/engine.rs repotoire-cli/src/detectors/data_flow.rs repotoire-cli/src/detectors/sql_injection/ repotoire-cli/src/detectors/command_injection.rs repotoire-cli/src/detectors/xss.rs repotoire-cli/src/detectors/ssrf.rs repotoire-cli/src/detectors/path_traversal.rs repotoire-cli/src/detectors/nosql_injection.rs repotoire-cli/src/detectors/xxe.rs repotoire-cli/src/detectors/eval_detector.rs repotoire-cli/src/detectors/log_injection.rs repotoire-cli/src/detectors/unsafe_template.rs repotoire-cli/src/detectors/insecure_deserialize.rs repotoire-cli/src/detectors/prototype_pollution.rs
git commit -m "perf: centralized taint engine — single pass for all 12 security detectors"
```

---

## Task 9: Fix Stack Overflow on Large C Repos

CPython's C files cause stack overflow in tree-sitter parser threads. Need to increase default thread stack size.

**Files:**
- Modify: `repotoire-cli/src/parsers/bounded_pipeline.rs` (worker thread stack size)
- Modify: `repotoire-cli/src/main.rs` or thread pool setup (main thread stack)

**Step 1: Increase rayon/worker thread stack size**

In bounded_pipeline.rs where worker threads are spawned, set stack size to 8MB:

```rust
// When building rayon thread pool or spawning worker threads:
std::thread::Builder::new()
    .stack_size(8 * 1024 * 1024)  // 8MB stack
    .spawn(move || { ... })
```

Or if using rayon globally:

```rust
rayon::ThreadPoolBuilder::new()
    .stack_size(8 * 1024 * 1024)
    .build_global()
    .unwrap();
```

**Step 2: Test**

```bash
cargo build --release
# Should not crash with stack overflow
./target/release/repotoire analyze /tmp/cpython-bench --lite 2>&1 | head -5
```

**Step 3: Commit**

```bash
git add repotoire-cli/src/...
git commit -m "fix: increase thread stack size to 8MB — prevent stack overflow on deeply nested C/C++ files"
```

---

## Task 10: Validation — CPython Benchmark

**Step 1: Build release binary**

```bash
cd repotoire-cli && cargo build --release
```

**Step 2: Run CPython benchmark**

```bash
RUST_MIN_STACK=8388608 /usr/bin/time -v ./target/release/repotoire analyze /tmp/cpython-bench --timings 2>&1 | tee docs/perf/post-hotfix-timings-cpython.txt
```

**Success criteria:**
- Completes in < 60 seconds
- No stack overflow
- Findings are generated (not empty)

**Step 3: Run self-analysis comparison**

```bash
./target/release/repotoire analyze /home/zhammad/personal/repotoire --timings 2>&1 | tee docs/perf/post-hotfix-timings-self.txt
```

Compare with baseline (17.28s). Should be faster or equivalent.

**Step 4: Verify findings accuracy**

```bash
# Compare finding counts
./target/release/repotoire analyze /home/zhammad/personal/repotoire --format json | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Findings: {len(d.get(\"findings\",[]))}')"
```

Should match baseline ±5%.

**Step 5: Commit results**

```bash
git add docs/perf/
git commit -m "perf: validation — CPython benchmark completes in Xs (was: never finished)"
```
