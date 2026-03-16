# FileIndex Relative Paths Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make FileIndex store relative paths (matching the graph) so `find_function_at()` works for all detectors, eliminating AIDuplicateBlock FPs.

**Architecture:** `DetectorContext::build()` currently passes absolute paths from `source_files` into `file_data`. We change it to strip `repo_path` before building FileIndex entries. This single change cascades: the incremental cache path comparison in `detect.rs`, the test helpers in `analysis_context.rs`, and the AIDuplicateBlock workarounds all need updating.

**Tech Stack:** Rust, `std::path::Path::strip_prefix()`

---

### Task 1: Make DetectorContext::build() produce relative paths for FileIndex

**Files:**
- Modify: `src/detectors/detector_context.rs:356-371`

**Step 1: Change `file_data_for_index` to use relative paths**

In `DetectorContext::build()`, the `file_data_for_index` is cloned from `file_data` which uses absolute paths from `source_files`. Change it to strip `repo_path` prefix:

```rust
// Replace lines 367-371 in detector_context.rs:
// OLD:
// let file_data_for_index: Vec<(PathBuf, Arc<str>, ContentFlags)> = file_data
//     .iter()
//     .map(|(p, c, f)| (p.clone(), Arc::clone(c), *f))
//     .collect();

// NEW:
let file_data_for_index: Vec<(PathBuf, Arc<str>, ContentFlags)> = file_data
    .iter()
    .map(|(p, c, f)| {
        let rel = p.strip_prefix(repo_path).unwrap_or(p);
        (rel.to_path_buf(), Arc::clone(c), *f)
    })
    .collect();
```

**Important:** The `file_contents` and `content_flags` HashMaps in `DetectorContext` keep absolute paths — only `file_data_for_index` (which becomes `FileIndex`) switches to relative. This minimizes blast radius: detectors using `DetectorContext.file_contents` directly still work, while `FileIndex`-backed lookups now match the graph.

**Step 2: Verify it compiles**

Run: `cargo check`
Expected: Compilation succeeds (no API change, only path values change)

**Step 3: Commit**

```bash
git add src/detectors/detector_context.rs
git commit -m "feat: make FileIndex store relative paths matching graph"
```

---

### Task 2: Fix AnalysisContext test helpers for relative paths

**Files:**
- Modify: `src/detectors/analysis_context.rs:192-209` (`test_with_mock_files`)
- Modify: `src/detectors/analysis_context.rs:358-380` (test helper `make_ctx_with_sample_files`)
- Modify: `src/detectors/file_index.rs:177-205` (test data)

**Step 1: Update `test_with_mock_files` to use relative paths**

The test helper currently prepends `/mock/repo/` to make absolute paths. Change it to use bare relative paths (matching how `file_data_for_index` now works):

```rust
// In analysis_context.rs, test_with_mock_files:
// OLD:
// let full = PathBuf::from("/mock/repo").join(rel);
// NEW:
let full = PathBuf::from(rel);
```

**Step 2: Update `make_ctx_with_sample_files` test helper**

```rust
// OLD:
// PathBuf::from("/repo/app.py"),
// PathBuf::from("/repo/index.ts"),
// NEW:
// PathBuf::from("app.py"),
// PathBuf::from("index.ts"),
```

**Step 3: Update all test assertions that reference these paths**

Search for `"/repo/app.py"`, `"/mock/repo"`, etc. in analysis_context.rs tests and update:

```rust
// OLD: assert_eq!(py_files[0], Path::new("/repo/app.py"));
// NEW: assert_eq!(py_files[0], Path::new("app.py"));
```

**Step 4: Update file_index.rs test data**

```rust
// In file_index.rs test_data():
// OLD:
// PathBuf::from("/repo/app.py"),
// PathBuf::from("/repo/sql.py"),
// PathBuf::from("/repo/safe.py"),
// PathBuf::from("/repo/index.ts"),
// NEW:
// PathBuf::from("app.py"),
// PathBuf::from("sql.py"),
// PathBuf::from("safe.py"),
// PathBuf::from("index.ts"),
```

Also update `file_entry_content_lower` and `file_entry_word_set` tests that use `PathBuf::from("test.py")` — these are already relative, no change needed.

**Step 5: Update `test_constructor_with_files` test**

```rust
// OLD:
// PathBuf::from("/repo/main.rs"),
// let entry = ctx.files.get(Path::new("/repo/main.rs"));
// NEW:
// PathBuf::from("main.rs"),
// let entry = ctx.files.get(Path::new("main.rs"));
```

**Step 6: Run tests**

Run: `cargo test -- analysis_context file_index`
Expected: All analysis_context and file_index tests pass

**Step 7: Commit**

```bash
git add src/detectors/analysis_context.rs src/detectors/file_index.rs
git commit -m "fix: update test helpers for relative FileIndex paths"
```

---

### Task 3: Fix detector tests that construct FileIndex paths

**Files:**
- Grep for `PathBuf::from("/` in `src/detectors/` test modules
- Fix each to use relative paths

**Step 1: Find all affected tests**

Search `src/detectors/` for test code constructing absolute paths for FileIndex:

```bash
grep -rn 'PathBuf::from("/' src/detectors/ --include="*.rs" | grep -v "repo_path\|mock/repo\|nonexistent\|tmp"
```

Common patterns to fix in detector tests that use `test_with_mock_files`:
- These pass `("relative/path.py", "content")` which is then joined with `/mock/repo/` — now it's used as-is
- Most detector tests use the `test_with_mock_files` helper which we already fixed in Task 2

Detector tests that directly call `test_with_files` with absolute PathBuf need updating. Check all `test_with_files` call sites.

**Step 2: Fix each affected test**

For each test calling `AnalysisContext::test_with_files(graph, vec![(PathBuf::from("/absolute/path"), ...)])`:
- Change to `PathBuf::from("relative/path")`

**Step 3: Run full test suite**

Run: `cargo test`
Expected: All tests pass

**Step 4: Commit**

```bash
git add src/detectors/
git commit -m "fix: update detector tests for relative FileIndex paths"
```

---

### Task 4: Fix detectors that construct paths for FileIndex lookup

**Files:**
- Any detector calling `ctx.files.get(path)` or `ctx.as_file_provider().content(path)` with absolute paths

**Step 1: Search for detectors building paths from FileEntry**

The key change: `FileEntry.path` is now relative (e.g., `src/foo.rs` instead of `/home/user/repo/src/foo.rs`). Detectors that:
1. Get `entry.path` from FileIndex iteration — **no change needed** (they get relative paths back)
2. Construct a path and pass to `ctx.files.get(path)` — **may need updating** if they construct absolute paths

Search for `files.get(` calls in detectors:
```bash
grep -rn 'files\.get\|file_index\.get' src/detectors/ --include="*.rs"
```

Most detectors iterate via `files.by_extensions()` or `files.matching()` which return `&FileEntry` — no path construction needed. Only detectors that build paths manually need fixes.

**Step 2: Check the FileProvider shim**

The `AnalysisContextFileProvider::content(path)` first checks the global cache (which uses absolute paths), then falls back to `ctx.files.get(path)`. The global cache was populated with absolute paths during parsing, so `content()` still works for absolute paths via the cache. But `FileIndex.get()` now requires relative paths.

This is a potential issue if any code path calls `ctx.files.get(absolute_path)`. Check for this pattern.

**Step 3: Fix the CORS detector and similar path usage**

In `cors_misconfig.rs:148`:
```rust
let containing_func = graph.find_function_at(&path_str, line_num)...
```
Here `path_str` comes from `path.to_string_lossy()` where `path` is from `files.files_with_extensions()` — which now returns relative paths. So `find_function_at` will now work correctly! No change needed.

This is the systemic benefit: all detectors using FileIndex paths to call `find_function_at` now work automatically.

**Step 4: Run full test suite**

Run: `cargo test`
Expected: All tests pass

**Step 5: Commit**

```bash
git add src/detectors/
git commit -m "fix: ensure detectors use relative paths for FileIndex lookups"
```

---

### Task 5: Fix incremental cache path comparison

**Files:**
- Modify: `src/cli/analyze/detect.rs:507-530` (`update_incremental_cache`)

**Step 1: Understand the path flow**

`update_incremental_cache` receives `files: &[PathBuf]` which comes from `files_to_parse` (absolute paths from the file walker). It compares `af == file_path` where `af` comes from `Finding.affected_files`.

After our change, `Finding.affected_files` from detectors using FileIndex will contain **relative** paths. But `files` still has absolute paths. This mismatch will cause cache misses.

**Step 2: Normalize the comparison**

Two options:
- A: Strip prefix from `file_path` before comparing (match the relative Finding paths)
- B: Make `files_to_parse` relative too (broader change)

Option A is safer — change the comparison in `update_incremental_cache`:

```rust
// In update_incremental_cache, replace the filter:
// OLD:
// .filter(|f| f.affected_files.iter().any(|af| af == file_path))
// NEW:
.filter(|f| f.affected_files.iter().any(|af| {
    af == file_path || {
        // Finding may have relative path, files_to_parse has absolute
        let file_name = file_path.file_name();
        let af_name = af.file_name();
        file_name.is_some() && af_name.is_some() && file_path.ends_with(af)
    }
}))
```

Actually, a cleaner approach: normalize both sides to use `file_path.ends_with(af) || af.ends_with(file_path)` isn't right either. The cleanest fix is to strip the repo path from `file_path` before comparing:

The function doesn't have `repo_path`. Let's look at how `postprocess.rs` calls it — it passes `files_to_parse` which are absolute. We need to add repo_path parameter or strip inline.

Better approach: strip-then-compare. Add repo_path parameter to `update_incremental_cache`:

```rust
pub(super) fn update_incremental_cache(
    is_incremental_mode: bool,
    incremental_cache: &mut IncrementalCache,
    files: &[PathBuf],
    findings: &[Finding],
    repo_path: &Path,  // NEW
) {
    if !is_incremental_mode {
        return;
    }

    for file_path in files {
        let rel_path = file_path.strip_prefix(repo_path).unwrap_or(file_path);
        let file_findings: Vec<_> = findings
            .iter()
            .filter(|f| f.affected_files.iter().any(|af| af == rel_path || af == file_path))
            .cloned()
            .collect();
        incremental_cache.cache_findings(file_path, &file_findings);
    }

    if let Err(e) = incremental_cache.save_cache() {
        tracing::warn!("Failed to save incremental cache: {}", e);
    }
}
```

**Step 3: Update the call site in `postprocess.rs`**

Find the `update_incremental_cache` call in `postprocess.rs` and add the `repo_path` argument:

```rust
// In postprocess.rs, add repo_path parameter to postprocess_findings
// and pass it through to update_incremental_cache
update_incremental_cache(
    is_incremental_mode,
    incremental_cache,
    files_to_parse,
    findings,
    repo_path,  // NEW
);
```

You'll also need to add `repo_path: &Path` to the `postprocess_findings` function signature and update all its call sites.

**Step 4: Run tests**

Run: `cargo test`
Expected: All tests pass

**Step 5: Commit**

```bash
git add src/cli/analyze/detect.rs src/cli/analyze/postprocess.rs src/cli/analyze/mod.rs
git commit -m "fix: normalize paths in incremental cache comparison"
```

---

### Task 6: Clean up AIDuplicateBlock workarounds

**Files:**
- Modify: `src/detectors/ai_duplicate_block.rs:296-307` (`resolve_graph_qn`)
- Modify: `src/detectors/ai_duplicate_block.rs:617-644` (test filter)
- Modify: `src/detectors/ai_duplicate_block.rs:311-371` (`verify_semantic_overlap`)

**Step 1: Simplify `resolve_graph_qn`**

With FileIndex paths now relative, `func.file_path` already matches graph paths. Remove the `repo_path` parameter and `strip_prefix` logic:

```rust
// OLD:
fn resolve_graph_qn(func: &FunctionData, graph: &dyn crate::graph::GraphQuery, repo_path: &std::path::Path) -> String {
    let i = graph.interner();
    let rel_path = std::path::Path::new(&func.file_path)
        .strip_prefix(repo_path)
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_else(|_| func.file_path.clone());
    graph
        .find_function_at(&rel_path, func.line_start)
        .map(|n| n.qn(i).to_string())
        .unwrap_or_else(|| func.qualified_name.clone())
}

// NEW:
fn resolve_graph_qn(func: &FunctionData, graph: &dyn crate::graph::GraphQuery) -> String {
    let i = graph.interner();
    graph
        .find_function_at(&func.file_path, func.line_start)
        .map(|n| n.qn(i).to_string())
        .unwrap_or_else(|| func.qualified_name.clone())
}
```

**Step 2: Simplify `verify_semantic_overlap`**

Remove `repo_path` parameter:

```rust
// OLD:
fn verify_semantic_overlap(
    func1: &FunctionData, func2: &FunctionData, _similarity: f64,
    graph: &dyn crate::graph::GraphQuery, repo_path: &std::path::Path,
) -> bool {
    let gqn1 = Self::resolve_graph_qn(func1, graph, repo_path);
    let gqn2 = Self::resolve_graph_qn(func2, graph, repo_path);

// NEW:
fn verify_semantic_overlap(
    func1: &FunctionData, func2: &FunctionData, _similarity: f64,
    graph: &dyn crate::graph::GraphQuery,
) -> bool {
    let gqn1 = Self::resolve_graph_qn(func1, graph);
    let gqn2 = Self::resolve_graph_qn(func2, graph);
```

**Step 3: Simplify the test function filter**

Remove `strip_prefix` workaround from the test filter:

```rust
// OLD:
let repo_path_for_filter = ctx.repo_path();
let func_sigs: Vec<FuncWithSig> = func_sigs
    .into_iter()
    .filter(|fs| {
        let rel = std::path::Path::new(&fs.file_path)
            .strip_prefix(repo_path_for_filter)
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_else(|_| fs.file_path.clone());
        if let Some(graph_func) =
            ctx.graph.find_function_at(&rel, fs.line_start)
        {
            ...
        }
    })

// NEW:
let func_sigs: Vec<FuncWithSig> = func_sigs
    .into_iter()
    .filter(|fs| {
        // FileIndex paths are now relative, matching graph paths
        if let Some(graph_func) =
            ctx.graph.find_function_at(&fs.file_path, fs.line_start)
        {
            let i = ctx.graph.interner();
            let qn = graph_func.qn(i);
            if ctx.is_test_function(qn) {
                return false;
            }
            for d in ctx.decorators(qn) {
                if d == "test" || d.starts_with("cfg(test") {
                    return false;
                }
            }
            true
        } else {
            // Fallback: name heuristic for functions not in graph
            !fs.name.starts_with("test_")
        }
    })
    .collect();
```

**Step 4: Update `verify_semantic_overlap` call site**

```rust
// OLD:
let repo_path = ctx.repo_path();
let duplicates: Vec<_> = duplicates
    .into_iter()
    .filter(|(func1, func2, similarity)| {
        Self::verify_semantic_overlap(func1, func2, *similarity, ctx.graph, repo_path)
    })
    .collect();

// NEW:
let duplicates: Vec<_> = duplicates
    .into_iter()
    .filter(|(func1, func2, similarity)| {
        Self::verify_semantic_overlap(func1, func2, *similarity, ctx.graph)
    })
    .collect();
```

**Step 5: Remove debug logging**

Remove the `info!("AIDuplicateBlock: find_function_at miss...")` line since we expect hits now, and misses will be the exception.

**Step 6: Run tests**

Run: `cargo test ai_duplicate_block`
Expected: All 6 AIDuplicateBlock tests pass

**Step 7: Commit**

```bash
git add src/detectors/ai_duplicate_block.rs
git commit -m "refactor: remove path workarounds from AIDuplicateBlock (FileIndex now relative)"
```

---

### Task 7: Fix the `DetectorContext.file_contents` / `content_flags` path format

**Files:**
- Modify: `src/detectors/detector_context.rs:356-378`

**Step 1: Assess whether `file_contents` and `content_flags` need relative paths**

Currently `file_contents` and `content_flags` in `DetectorContext` use absolute paths. These are accessed via `detector_ctx.file_contents.get(path)` and `detector_ctx.content_flags.get(path)`.

Search for direct usage:
```bash
grep -rn 'file_contents\.\|content_flags\.' src/detectors/ --include="*.rs"
```

If detectors access these maps using FileIndex-derived paths (now relative), they'll miss. We need to also make these relative.

**Step 2: Make `file_contents` and `content_flags` store relative paths**

```rust
// In DetectorContext::build(), change the file_data loop:
// OLD:
let mut file_contents = HashMap::with_capacity(file_data.len());
let mut content_flags = HashMap::with_capacity(file_data.len());
for (path, content, flags) in file_data {
    file_contents.insert(path.clone(), content);
    content_flags.insert(path, flags);
}

// NEW:
let mut file_contents = HashMap::with_capacity(file_data.len());
let mut content_flags = HashMap::with_capacity(file_data.len());
for (path, content, flags) in file_data {
    let rel = path.strip_prefix(repo_path).unwrap_or(&path);
    file_contents.insert(rel.to_path_buf(), content);
    content_flags.insert(rel.to_path_buf(), flags);
}
```

**Step 3: Update `test_file_contents_loaded` test**

```rust
// The test creates a real temp file, so strip_prefix will work.
// But the assertion checks for the absolute key:
// OLD: assert!(ctx.file_contents.contains_key(&file_path));
// NEW:
let rel = file_path.strip_prefix(dir.path()).unwrap();
assert!(ctx.file_contents.contains_key(rel));
```

**Step 4: Update `test_content_flags_populated_in_build` test**

Same pattern — use relative paths in assertions:
```rust
// OLD: let app_flags = ctx.content_flags[&py_file];
// NEW:
let rel_py = py_file.strip_prefix(dir.path()).unwrap();
let app_flags = ctx.content_flags[&rel_py.to_path_buf()];
```

**Step 5: Update `analysis_context.rs` `test_with_files` content_flags population**

In `test_with_files`, after building `DetectorContext`, it populates `content_flags` from FileIndex entries (which are now relative). The entries already have relative paths, so no change needed there.

**Step 6: Run tests**

Run: `cargo test detector_context`
Expected: All DetectorContext tests pass

**Step 7: Commit**

```bash
git add src/detectors/detector_context.rs
git commit -m "fix: make DetectorContext file_contents/content_flags use relative paths"
```

---

### Task 8: Full test suite verification

**Files:** None (verification only)

**Step 1: Run full test suite**

Run: `cargo test`
Expected: All tests pass (0 failures)

**Step 2: Fix any remaining path-related test failures**

If any tests fail due to absolute vs relative path assertions, update them to use relative paths.

**Step 3: Commit any fixes**

```bash
git add -u
git commit -m "fix: resolve remaining path-related test failures"
```

---

### Task 9: Self-analysis validation

**Files:** None (validation only)

**Step 1: Clean cached analysis**

Run: `cargo run --release -- clean .`
Expected: Cache cleaned

**Step 2: Run self-analysis**

Run: `cargo run --release -- analyze .`
Expected: Analysis completes without errors

**Step 3: Count AIDuplicateBlock findings**

Run: `cargo run --release -- analyze . 2>&1 | grep -i "AIDuplicate\|duplicate"`
Expected: 0-2 AIDuplicateBlock findings (down from 12)

**Step 4: Verify no test function FPs**

Check that no finding mentions `test_` functions:
Run: `cargo run --release -- findings . 2>&1 | grep "test_"`
Expected: No test functions in findings

**Step 5: Verify `find_function_at` works**

Check that the debug logging (if still present) shows hits:
Run: `RUST_LOG=info cargo run --release -- analyze . 2>&1 | grep "find_function_at miss"`
Expected: Zero or very few misses (previously ALL were misses)

**Step 6: Document results**

Record before/after FP counts.

**Step 7: Commit**

```bash
git commit --allow-empty -m "verify: AIDuplicateBlock FPs reduced from 12 to N after FileIndex relative paths fix"
```
