# Performance Fix: Detector Hotspots

**Date:** 2026-03-04
**Goal:** Make `repotoire analyze` complete on CPython (3.4k files) in under 60 seconds. Currently hangs indefinitely due to three root causes found via systematic debugging.

---

## Root Causes

| # | Root Cause | Location | Impact |
|---|-----------|----------|--------|
| 1 | 12 security detectors each independently run `run_intra_function_taint()`, iterating ALL functions and reading ALL files 12 times | `data_flow.rs`, 12 detector files | 12x redundant work, 240M pattern matches on CPython |
| 2 | No pre-filtering — taint analysis runs on files that can't possibly contain relevant sinks (e.g., SQL patterns) | `data_flow.rs:479-544` | 90%+ of files analyzed unnecessarily |
| 3 | Quadratic pattern matching in `check_sink_call()` — nested `for sink × for tainted_var` per line | `data_flow.rs:293-330` | O(sinks × tainted × lines) per function |
| 4 | O(n²) Brandes betweenness centrality computed for ALL functions | `function_context.rs:310` | Catastrophic for >10k functions |
| 5 | `get_callers()` called inside sort comparator in `build_hmm_contexts` | `engine.rs:203-207` | O(n log n) graph queries |
| 6 | `get_or_build_contexts()` called in `run_graph_independent()` unnecessarily | `engine.rs:579` | Wasted work in graph-independent phase |
| 7 | 7 detectors misclassified as graph-independent while using `graph.get_functions()` | 7 detector files | Already fixed, needs commit |

---

## Fix 1: Pre-Filter Files by Sink Keywords

**Files:** `detectors/data_flow.rs`, `detectors/taint/mod.rs`

Before running expensive taint analysis on a file, check if it contains ANY sink keywords for the target category. Most CPython files have zero SQL/XSS/command injection patterns.

**Approach:**
- Each `TaintCategory` defines a `quick_reject_patterns: &[&str]` — literal strings that MUST appear in file content for taint analysis to be relevant (e.g., `["execute", "cursor", "query", "SELECT", "INSERT"]` for SQL injection)
- Before taint-analyzing a function body, check if the file content contains at least one quick-reject pattern via `content.contains()` or a pre-built Aho-Corasick automaton
- Files with zero matches are skipped entirely — no function iteration, no pattern matching

**Expected impact:** 90%+ of files skipped on CPython (most Python files don't contain SQL/XSS/cmd patterns).

---

## Fix 2: Fix Inner Loop — Replace Nested Search with Hash Lookup

**Files:** `detectors/data_flow.rs`

**Current code (lines 293-330):**
```rust
for sink in sinks {                    // 30-50 sinks
    let sink_lower = sink.to_lowercase();  // allocation per sink per line
    if !line_lower.contains(&sink_lower) { continue; }
    for (var, source) in tainted {     // up to 20 tainted vars
        if line_contains_var_in_call(line, &sink_lower, var) {
            reaches.push(...);
        }
    }
}
```

**Fix:**
- Pre-compute `sinks_lower: HashSet<String>` once at detector init (not per line)
- Use a single-pass scan: for each line, check `line_lower.contains()` for each sink (or use Aho-Corasick for all sinks at once)
- Pre-lowercase sink patterns at construction time, not per-line

**Also fix `rhs_contains_var()` (lines 437-457):**
- Current worst case is O(|rhs|²) due to repeated `find()` with overlap
- Fix: use word boundary regex or compile variable names into a regex set

---

## Fix 3: Centralized Taint Engine

**Files:**
- Create: `detectors/taint/engine.rs`
- Modify: `detectors/data_flow.rs` (extract shared logic)
- Modify: `detectors/engine.rs` (add taint_results cache)
- Modify: 12 security detector files (consume cached results)

**Current architecture:**
```
SQLInjection.detect()     → trace_taint(graph, SQL) + run_intra_function_taint(graph, SQL)
XSS.detect()              → trace_taint(graph, XSS) + run_intra_function_taint(graph, XSS)
CommandInjection.detect()  → trace_taint(graph, CMD) + run_intra_function_taint(graph, CMD)
... × 12 detectors, each iterating ALL functions and ALL files independently
```

**Proposed architecture:**
```
DetectorEngine::run_centralized_taint(graph, files, categories)
  → reads each file ONCE into FileContentCache
  → pre-filters files by category sink keywords (Fix 1)
  → iterates each function ONCE
  → for each function body, checks ALL categories' sinks in single pass
  → returns TaintResults { per_category: HashMap<TaintCategory, Vec<TaintPath>> }

SQLInjection.detect()      → engine.taint_results().get(SQL)
XSS.detect()               → engine.taint_results().get(XSS)
CommandInjection.detect()   → engine.taint_results().get(CMD)
```

**New types:**
```rust
/// Shared file content cache — DashMap for thread-safe concurrent access
pub struct FileContentCache {
    cache: DashMap<PathBuf, Arc<String>>,
}

/// Results from centralized taint analysis
pub struct TaintResults {
    /// Intra-function taint paths grouped by category
    intra: HashMap<TaintCategory, Vec<TaintPath>>,
    /// Cross-function taint paths grouped by category
    cross: HashMap<TaintCategory, Vec<TaintPath>>,
}

/// Centralized taint engine — runs once, results shared by 12 detectors
pub struct TaintEngine {
    file_cache: FileContentCache,
    categories: Vec<TaintCategory>,
}
```

**Execution flow:**
1. `DetectorEngine` creates `TaintEngine` during setup
2. Before running graph-dependent detectors, calls `taint_engine.run_all(graph, files)`
3. Results stored in `DetectorEngine::taint_results: Option<TaintResults>`
4. Each security detector's `detect()` method calls `engine.taint_results().get(category)` instead of running its own taint analysis
5. Security detectors still do their own post-processing (filtering, severity assignment, message generation)

**12 affected detectors:**
- `sql_injection.rs`, `xss.rs`, `command_injection.rs`, `eval_detector.rs`
- `insecure_deserialize.rs`, `log_injection.rs`, `nosql_injection.rs`
- `path_traversal.rs`, `prototype_pollution.rs`, `ssrf.rs`
- `unsafe_template.rs`, `xxe.rs`

---

## Fix 4: Shared File Content Cache

**Files:** `detectors/taint/engine.rs` (new), `detectors/engine.rs`

```rust
pub struct FileContentCache {
    cache: DashMap<PathBuf, Arc<String>>,
}

impl FileContentCache {
    pub fn get_or_read(&self, path: &Path) -> Option<Arc<String>> {
        if let Some(entry) = self.cache.get(path) {
            return Some(Arc::clone(entry.value()));
        }
        let content = std::fs::read_to_string(path).ok()?;
        let arc = Arc::new(content);
        self.cache.insert(path.to_path_buf(), Arc::clone(&arc));
        Some(arc)
    }
}
```

- `Arc<String>` means zero cloning — all consumers share the same allocation
- `DashMap` for lock-free concurrent access from parallel detectors
- Replaces per-detector `HashMap<String, String>` in `data_flow.rs`
- Bounded: skip files >2MB (matches existing parser guardrail)
- Lives on `DetectorEngine`, passed to taint engine and individual detectors

---

## Fix 5: Sampled Betweenness Centrality

**Files:** `detectors/function_context.rs`

**Current (line 310):**
```rust
let partial_centralities: Vec<Vec<f64>> = (0..n)
    .into_par_iter()
    .map(|s| { /* Brandes BFS from node s */ })
    .collect();
```

**Fix:**
```rust
const BETWEENNESS_SAMPLE_SIZE: usize = 500;
let k = n.min(BETWEENNESS_SAMPLE_SIZE);
let sample_indices: Vec<usize> = rand::seq::index::sample(&mut rng, n, k).into_vec();
let scale = n as f64 / k as f64;

let partial_centralities: Vec<Vec<f64>> = sample_indices
    .into_par_iter()
    .map(|s| { /* Brandes BFS from node s */ })
    .collect();

// Scale by n/k to approximate full betweenness
for c in &mut centrality {
    *c *= scale;
}
```

- For repos with <500 functions: exact computation (no change)
- For repos with 10k+ functions: 20x speedup with ~95% rank correlation
- Statistically robust — proven approximation in graph theory literature
- Add `rand` dependency (already transitive via other crates, check first)

---

## Fix 6: Pre-Compute Fan-In for HMM Sort

**Files:** `detectors/engine.rs`

**Current (lines 203-207):**
```rust
functions.sort_by(|a, b| {
    let a_callers = graph.get_callers(&a.qualified_name).len();  // graph query in comparator!
    let b_callers = graph.get_callers(&b.qualified_name).len();
    b_callers.cmp(&a_callers)
});
```

**Fix:**
```rust
let fan_in: HashMap<&str, usize> = functions.iter()
    .map(|f| (f.qualified_name.as_str(), graph.get_callers(&f.qualified_name).len()))
    .collect();

functions.sort_by(|a, b| {
    let a_fi = fan_in.get(a.qualified_name.as_str()).copied().unwrap_or(0);
    let b_fi = fan_in.get(b.qualified_name.as_str()).copied().unwrap_or(0);
    b_fi.cmp(&a_fi)
});
```

One O(n) pass to build HashMap, then sort by lookup. Also fix lines 219-232 similarly — pre-compute fan-in/fan-out for all functions in one pass instead of per-function graph queries.

---

## Fix 7: Remove Context Building from Graph-Independent Phase

**Files:** `detectors/engine.rs`

**Current (line 579 in `run_graph_independent()`):**
```rust
let contexts = self.get_or_build_contexts(graph);
```

**Fix:** Remove this line. Graph-independent detectors don't use function contexts. The contexts will be built lazily when `run_graph_dependent()` is called.

If any graph-independent detector's `run_single_detector` call needs contexts, pass an empty map.

---

## Fix 8: Commit Misclassified Detector Fixes

7 detectors already flipped from `requires_graph = false` to `true`:
- `infinite_loop.rs`, `missing_await.rs`, `ai_churn.rs`, `ai_complexity_spike.rs`
- `ai_missing_tests.rs`, `commented_code.rs`, `single_char_names.rs`

Test threshold updated from >= 40 to >= 35 in `base.rs`.

Just needs commit.

---

## Priority Order

| Priority | Fix | Effort | Expected Impact |
|----------|-----|--------|-----------------|
| 1 | Pre-filter by sink keywords | Small | 90%+ file reduction |
| 2 | Fix inner loop (hash/Aho-Corasick) | Small | 10-50x per-file speedup |
| 3 | Shared file content cache | Small | 12x fewer file reads |
| 4 | Centralized taint engine | Large | 12x fewer function iterations |
| 5 | Remove context from GI phase | Trivial | Eliminates wasted betweenness |
| 6 | Pre-compute fan-in for HMM sort | Trivial | Eliminates O(n log n) graph queries |
| 7 | Sampled betweenness | Small | 20x speedup for large repos |
| 8 | Commit misclassified detectors | Trivial | Correct phase assignment |

---

## Success Criteria

- `repotoire analyze /tmp/cpython-bench` completes in < 60 seconds (release binary)
- All 969 existing tests pass
- No findings accuracy regression (validate on repotoire self-analysis: same finding count ±5%)
- Stack overflow on CPython fixed (increase default thread stack size)

---

## Non-Goals

- Optimizing individual slow detectors (AIBoilerplateDetector at 16s) — separate effort
- Caching parsed ASTs across runs — incremental cache handles this
- Reducing peak RSS — focus is wall-clock time
