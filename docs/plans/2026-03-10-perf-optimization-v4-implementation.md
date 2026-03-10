# Performance Optimization V4: Detector & Pipeline Speedup

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Cut CPython analysis from 4.0s to <3.0s wall clock by optimizing the top 4 slowest detectors and the parse pipeline.

**Architecture:** Targeted optimizations across 6 files. No new crates. Leverages pre-normalization caching, HashMap pre-allocation, Arc wrapping for ParseResult, and early-exit patterns in detectors.

**Tech Stack:** Pure Rust. `aho-corasick` (already transitive via `regex`). No new dependencies.

**Baseline:** CPython 4.0s wall / 1.97GB RSS. Parse 2.0s (49%), detect 1.8s (43%).

---

## Task 1: Pre-normalize lines in duplicate_code.rs (~350ms savings)

The sliding window calls `normalize_line()` per window position. For a 1000-line file with `min_lines=6`, the same line is normalized up to 6 times. Pre-normalizing all lines once reduces total calls from O(lines × min_lines) to O(lines).

**Files:**
- Modify: `src/detectors/duplicate_code.rs:33-39, 160-182`

**Step 1: Replace normalize_line + sliding window with pre-normalized approach**

Replace lines 160-182:

```rust
// Before: normalize per window position (~6x redundant)
let per_file: Vec<Vec<(String, PathBuf, usize)>> = source_files
    .par_iter()
    .filter(|path| !Self::is_test_file(path))
    .filter_map(|path| {
        files.content(path).map(|content| {
            let lines: Vec<&str> = content.lines().collect();
            let mut file_blocks = Vec::new();
            for i in 0..lines.len().saturating_sub(min_lines) {
                let block: String = lines[i..i + min_lines]
                    .iter()
                    .map(|l| Self::normalize_line(l))
                    .filter(|l| !l.is_empty())
                    .collect::<Vec<_>>()
                    .join("\n");
                if block.len() > 50 {
                    file_blocks.push((block, path.to_path_buf(), i + 1));
                }
            }
            file_blocks
        })
    })
    .collect();
```

With:

```rust
// After: normalize each line once, then slice pre-normalized lines
let per_file: Vec<Vec<(u64, PathBuf, usize)>> = source_files
    .par_iter()
    .filter(|path| !Self::is_test_file(path))
    .filter_map(|path| {
        files.content(path).map(|content| {
            let lines: Vec<&str> = content.lines().collect();
            // Pre-normalize all lines once
            let normalized: Vec<String> = lines
                .iter()
                .map(|l| Self::normalize_line(l))
                .collect();
            let mut file_blocks = Vec::new();
            for i in 0..lines.len().saturating_sub(min_lines) {
                // Build block from pre-normalized lines
                let block: String = normalized[i..i + min_lines]
                    .iter()
                    .filter(|l| !l.is_empty())
                    .map(|s| s.as_str())
                    .collect::<Vec<_>>()
                    .join("\n");
                if block.len() > 50 {
                    // Use hash instead of full string as HashMap key
                    let hash = Self::hash_block(&block);
                    file_blocks.push((hash, path.to_path_buf(), i + 1));
                }
            }
            file_blocks
        })
    })
    .collect();
```

**Step 2: Add hash_block() helper and change HashMap key to u64**

Add to the impl block:

```rust
/// Hash a normalized block for fast dedup (FNV-1a, 64-bit)
fn hash_block(block: &str) -> u64 {
    use std::hash::{Hash, Hasher};
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    block.hash(&mut hasher);
    hasher.finish()
}
```

Change the blocks HashMap type (line 185):

```rust
// Before:
let mut blocks: HashMap<String, Vec<(PathBuf, usize)>> = HashMap::new();

// After: u64 key saves ~100 bytes per block vs String key
let mut blocks: HashMap<u64, Vec<(PathBuf, usize)>> = HashMap::new();
```

**Step 3: Fix analyze_caller_similarity O(n²) get_functions() loop**

Replace lines 109-126 (the loop that calls `get_functions()` per caller):

```rust
// Before: O(n²) — get_functions() called per common_caller
for caller in &common_callers {
    if let Some(func) = graph
        .get_functions()
        .into_iter()
        .find(|f| f.qn(i) == caller)
    {
        let module = func.path(i).rsplit('/').nth(1).unwrap_or("utils").to_string();
        *module_counts.entry(module).or_default() += 1;
    }
}

// After: O(n) — build lookup once
let func_map: HashMap<String, _> = graph
    .get_functions()
    .into_iter()
    .map(|f| (f.qn(i).to_string(), f))
    .collect();
for caller in &common_callers {
    if let Some(func) = func_map.get(caller.as_str()) {
        let module = func.path(i).rsplit('/').nth(1).unwrap_or("utils").to_string();
        *module_counts.entry(module).or_default() += 1;
    }
}
```

**Step 4: Run tests**

```bash
cargo test duplicate_code -- --nocapture
```

**Step 5: Commit**

```bash
git add src/detectors/duplicate_code.rs
git commit -m "perf(duplicate-code): pre-normalize lines and hash blocks for O(n) window creation"
```

---

## Task 2: Early exit in magic_numbers.rs (~120ms savings)

The regex runs on every line of every file. Add a fast byte scan to skip files/lines that can't contain 2+ digit numbers.

**Files:**
- Modify: `src/detectors/magic_numbers.rs:225-284`

**Step 1: Add early exit per file**

After the file extension check (around line 237), add a quick byte scan before entering the line loop:

```rust
if let Some(content) = files.content(path) {
    // Fast exit: skip files with no 2+ digit sequences
    if !content.as_bytes().windows(2).any(|w| w[0].is_ascii_digit() && w[1].is_ascii_digit()) {
        continue;
    }
    let lines: Vec<&str> = content.lines().collect();
    // ... existing line loop ...
```

**Step 2: Skip comment-only lines before regex**

The existing check (lines 246-250) already skips `//`, `#`, `*` prefixed lines. Add a fast digit check before regex:

```rust
// Before regex, quick check if line has any digits at all
if !trimmed.as_bytes().iter().any(|b| b.is_ascii_digit()) {
    continue;
}

for cap in NUMBER_PATTERN.captures_iter(line) {
```

Insert this right before line 255 (`for cap in NUMBER_PATTERN...`).

**Step 3: Run tests**

```bash
cargo test magic_numbers -- --nocapture
```

**Step 4: Commit**

```bash
git add src/detectors/magic_numbers.rs
git commit -m "perf(magic-numbers): early exit for files/lines without digit sequences"
```

---

## Task 3: Aho-Corasick for path_traversal.rs pre-filter (~80ms savings)

Replace 20 separate `contains()` calls with a single Aho-Corasick automaton scan.

**Files:**
- Modify: `src/detectors/path_traversal.rs:96-107`

**Step 1: Add Aho-Corasick static**

At the top of the file, add:

```rust
use std::sync::LazyLock;

static FILE_KEYWORDS: LazyLock<aho_corasick::AhoCorasick> = LazyLock::new(|| {
    aho_corasick::AhoCorasick::new([
        "open", "unlink", "rmdir", "mkdir", "copyFile", "rename",
        "readFile", "writeFile", "shutil", "os.remove", "path.join",
        "path.resolve", "os.path", "filepath", "pathlib", "sendFile",
        "send_file", "serve_file", "createReadStream", "createWriteStream",
    ]).expect("valid patterns")
});
```

**Step 2: Replace the 20 contains() chain**

Replace lines 96-107:

```rust
// Before: 20 separate contains() calls
if !raw.contains("open") && !raw.contains("unlink") && !raw.contains("rmdir")
    && !raw.contains("mkdir") && ... {
    continue;
}

// After: single Aho-Corasick scan
if FILE_KEYWORDS.find(raw.as_bytes()).is_none() {
    continue;
}
```

**Step 3: Run tests**

```bash
cargo test path_traversal -- --nocapture
```

**Step 4: Commit**

```bash
git add src/detectors/path_traversal.rs
git commit -m "perf(path-traversal): replace 20 contains() with Aho-Corasick automaton"
```

---

## Task 4: HashMap pre-allocation in build_call_maps_raw (~30ms savings)

**Files:**
- Modify: `src/graph/store/mod.rs:797-812`

**Step 1: Add capacity hints**

Replace the four HashMap creations:

```rust
// Before:
let pg_to_func: HashMap<NodeIndex, usize> = funcs_pg.iter().enumerate()...collect();
let qn_to_idx: HashMap<StrKey, usize> = funcs_pg.iter().enumerate()...collect();
let mut callers: HashMap<usize, Vec<usize>> = HashMap::new();
let mut callees: HashMap<usize, Vec<usize>> = HashMap::new();

// After:
let func_count = funcs_pg.len();
let pg_to_func: HashMap<NodeIndex, usize> = {
    let mut m = HashMap::with_capacity(func_count);
    m.extend(funcs_pg.iter().enumerate().map(|(i, (pg_idx, _))| (*pg_idx, i)));
    m
};
let qn_to_idx: HashMap<StrKey, usize> = {
    let mut m = HashMap::with_capacity(func_count);
    m.extend(funcs_pg.iter().enumerate().map(|(i, (_, node))| (node.qualified_name, i)));
    m
};
let mut callers: HashMap<usize, Vec<usize>> = HashMap::with_capacity(func_count / 2);
let mut callees: HashMap<usize, Vec<usize>> = HashMap::with_capacity(func_count / 2);
```

**Step 2: Pre-allocate all_edges in graph.rs**

In `src/cli/analyze/graph.rs:476`, replace:

```rust
// Before:
let mut all_edges = Vec::new();

// After: estimate ~3 edges per function (calls + imports + contains)
let estimated_edges = total_functions * 3 + parse_results.len();
let mut all_edges = Vec::with_capacity(estimated_edges);
```

**Step 3: Merge three node batch inserts into one (graph.rs:487-489)**

Replace:

```rust
// Before: three separate write_graph() locks
graph.add_nodes_batch(all_file_nodes);
graph.add_nodes_batch(all_func_nodes);
graph.add_nodes_batch(all_class_nodes);

// After: single lock acquisition
let mut combined_nodes = Vec::with_capacity(
    all_file_nodes.len() + all_func_nodes.len() + all_class_nodes.len()
);
combined_nodes.extend(all_file_nodes);
combined_nodes.extend(all_func_nodes);
combined_nodes.extend(all_class_nodes);
graph.add_nodes_batch(combined_nodes);
```

**Step 4: Run tests**

```bash
cargo test --lib --tests
```

**Step 5: Commit**

```bash
git add src/graph/store/mod.rs src/cli/analyze/graph.rs
git commit -m "perf: pre-allocate HashMaps and merge node batch inserts"
```

---

## Task 5: Arc<ParseResult> for cache hits (~200ms savings)

Eliminate deep cloning of ParseResult on cache hits by wrapping in Arc.

**Files:**
- Modify: `src/cli/analyze/parse.rs:36, 51-77`
- Modify: `src/cli/analyze/parse.rs` (chunked variant ~line 170-210)
- Modify: `src/cli/analyze/graph.rs` (downstream signature)
- Modify: `src/cli/analyze/mod.rs` (ParsePhaseResult type)

**Step 1: Change ParsePhaseResult to use Arc<ParseResult>**

In `parse.rs:19-23`:

```rust
// Before:
pub(super) struct ParsePhaseResult {
    pub parse_results: Vec<(PathBuf, ParseResult)>,
    pub total_functions: usize,
    pub total_classes: usize,
}

// After:
pub(super) struct ParsePhaseResult {
    pub parse_results: Vec<(PathBuf, Arc<ParseResult>)>,
    pub total_functions: usize,
    pub total_classes: usize,
}
```

Add `use std::sync::Arc;` to the imports.

**Step 2: Change cache_view DashMap to store Arc<ParseResult>**

In the `ConcurrentCacheView` type (likely in `src/detectors/incremental_cache.rs`), change the DashMap value type from `ParseResult` to `Arc<ParseResult>`. Then update `parse_files()`:

```rust
// Line 60-63: cache hit path
if let Some(cached) = cache_view.parse_cache.get(file_path) {
    cache_hits.fetch_add(1, Ordering::Relaxed);
    let pr = Arc::clone(cached.value());  // Arc clone: 8 bytes, not deep clone
    return Some((file_path.clone(), pr));
}

// Line 75-77: cache miss path
let arc_result = Arc::new(result);
new_results.insert(file_path.clone(), Arc::clone(&arc_result));
Some((file_path.clone(), arc_result))
```

**Step 3: Update downstream consumers**

In `graph.rs`, change `build_graph()` signature:

```rust
// Before:
pub(super) fn build_graph(
    graph: &Arc<GraphStore>,
    repo_path: &Path,
    parse_results: &[(PathBuf, ParseResult)],
    ...

// After:
pub(super) fn build_graph(
    graph: &Arc<GraphStore>,
    repo_path: &Path,
    parse_results: &[(PathBuf, Arc<ParseResult>)],
    ...
```

The body accesses `result.functions`, `result.classes`, etc. — these work unchanged through Arc's Deref impl.

Update the same for `build_graph_chunked()`.

In `mod.rs`, update `build_ngram_model()` and `parse_and_build()` accordingly. The `pr.clone()` in the n-gram model loop becomes `Arc::clone(&pr)`.

**Step 4: Run tests**

```bash
cargo test --lib --tests
```

**Step 5: Commit**

```bash
git add src/cli/analyze/parse.rs src/cli/analyze/graph.rs src/cli/analyze/mod.rs src/detectors/incremental_cache.rs
git commit -m "perf: wrap ParseResult in Arc to avoid deep clones on cache hits"
```

---

## Task 6: Reduce string allocations in duplicate_code caller analysis

The `analyze_caller_similarity()` method converts interned StrKeys to String for HashSet operations. Use StrKey directly.

**Files:**
- Modify: `src/detectors/duplicate_code.rs:60-136`

**Step 1: Use StrKey in caller sets**

Replace lines 86-95:

```rust
// Before: allocates String per caller
let caller_sets: Vec<HashSet<String>> = valid_funcs
    .iter()
    .map(|qn| {
        graph
            .get_callers(qn)
            .into_iter()
            .map(|c| c.qn(i).to_string())
            .collect()
    })
    .collect();

// After: use qualified_name StrKey directly (no allocation)
use crate::graph::interner::StrKey;
let caller_sets: Vec<HashSet<StrKey>> = valid_funcs
    .iter()
    .map(|qn| {
        graph
            .get_callers(qn)
            .into_iter()
            .map(|c| c.qualified_name)
            .collect()
    })
    .collect();
```

Then update the common_callers computation (lines 102-106) to use StrKey:

```rust
let common_callers: HashSet<StrKey> = caller_sets[0]
    .iter()
    .filter(|caller| caller_sets.iter().skip(1).all(|set| set.contains(*caller)))
    .copied()
    .collect();
```

And the func_map lookup (lines 109-126):

```rust
let func_map: HashMap<StrKey, _> = graph
    .get_functions()
    .into_iter()
    .map(|f| (f.qualified_name, f))
    .collect();
for caller_key in &common_callers {
    if let Some(func) = func_map.get(caller_key) {
        let module = func.path(i).rsplit('/').nth(1).unwrap_or("utils").to_string();
        *module_counts.entry(module).or_default() += 1;
    }
}
```

**Step 2: Update find_containing_functions to return StrKey**

Replace lines 55-68:

```rust
// Before: returns Vec<Option<String>>
fn find_containing_functions(
    &self,
    graph: &dyn crate::graph::GraphQuery,
    locations: &[(PathBuf, usize)],
) -> Vec<Option<String>> {
    let i = graph.interner();
    locations.iter().map(|(path, line)| {
        let path_str = path.to_string_lossy();
        graph.find_function_at(&path_str, *line as u32)
            .map(|f| f.qn(i).to_string())
    }).collect()
}

// After: returns Vec<Option<StrKey>>
fn find_containing_functions(
    &self,
    graph: &dyn crate::graph::GraphQuery,
    locations: &[(PathBuf, usize)],
) -> Vec<Option<StrKey>> {
    locations.iter().map(|(path, line)| {
        let path_str = path.to_string_lossy();
        graph.find_function_at(&path_str, *line as u32)
            .map(|f| f.qualified_name)
    }).collect()
}
```

Update `analyze_caller_similarity` signature to match:

```rust
fn analyze_caller_similarity(
    &self,
    graph: &dyn crate::graph::GraphQuery,
    containing_funcs: &[Option<StrKey>],
) -> (usize, String) {
    let i = graph.interner();
    let valid_funcs: Vec<StrKey> =
        containing_funcs.iter().filter_map(|f| *f).collect();
    if valid_funcs.len() < 2 {
        return (0, String::new());
    }
    // Use graph.get_callers with resolved string from interner
    let caller_sets: Vec<HashSet<StrKey>> = valid_funcs
        .iter()
        .map(|&qn| {
            graph
                .get_callers(i.resolve(qn))
                .into_iter()
                .map(|c| c.qualified_name)
                .collect()
        })
        .collect();
    // ... rest unchanged but using StrKey ...
}
```

**Step 3: Run tests**

```bash
cargo test duplicate_code -- --nocapture
```

**Step 4: Commit**

```bash
git add src/detectors/duplicate_code.rs
git commit -m "perf(duplicate-code): use StrKey instead of String in caller analysis"
```

---

## Verification

After all tasks, run the full benchmark:

```bash
cargo build --release && cargo install --path .
repotoire clean ~/personal/cpython
/usr/bin/time -v repotoire analyze ~/personal/cpython --log-level warn --timings
```

**Target:** <3.0s wall clock, <1.95GB peak RSS.

Expected breakdown:
- duplicate-code: 855ms → ~450ms (pre-normalize + hash blocks + StrKey callers)
- magic-numbers: 420ms → ~300ms (early exit)
- path-traversal: 361ms → ~280ms (Aho-Corasick)
- Parse pipeline: ~200ms savings (Arc + pre-allocation)
- Total: ~800ms savings → ~3.2s wall
