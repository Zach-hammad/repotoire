# Content-Indexed Detection Pipeline — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace per-detector file iteration with a shared FileIndex + AnalysisContext, reducing single-threaded detect phase from 9.46s to ~4s on CPython.

**Architecture:** Extend the `Detector` trait with declarative `file_extensions()` and `content_requirements()` methods. Build a `FileIndex` with lazy pre-computed lowercased content and word sets. The engine filters files before dispatching to detectors. Migrate all 96 detectors to the new `detect(&self, ctx: &AnalysisContext)` signature.

**Tech Stack:** Rust, rayon, `OnceLock` for lazy thread-safe caching, bitflags for ContentFlags

---

## Phase 1: Infrastructure (FileIndex + AnalysisContext + Extended ContentFlags)

### Task 1: Extend ContentFlags from 2 to 16 categories

**Files:**
- Modify: `src/detectors/detector_context.rs:12-75`

**Step 1: Add new flag constants**

In `detector_context.rs`, extend the `ContentFlags` impl block (currently lines 19-32) with 14 new flags:

```rust
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct ContentFlags(u32);

impl ContentFlags {
    pub const FILE_OPS: Self = Self(1 << 0);
    pub const PATH_OPS: Self = Self(1 << 1);
    // NEW flags:
    pub const HAS_SQL: Self = Self(1 << 2);
    pub const HAS_IMPORT: Self = Self(1 << 3);
    pub const HAS_EVAL: Self = Self(1 << 4);
    pub const HAS_HTTP_CLIENT: Self = Self(1 << 5);
    pub const HAS_USER_INPUT: Self = Self(1 << 6);
    pub const HAS_CRYPTO: Self = Self(1 << 7);
    pub const HAS_TEMPLATE: Self = Self(1 << 8);
    pub const HAS_SERIALIZE: Self = Self(1 << 9);
    pub const HAS_EXEC: Self = Self(1 << 10);
    pub const HAS_SECRET_PATTERN: Self = Self(1 << 11);
    pub const HAS_ML: Self = Self(1 << 12);
    pub const HAS_REACT: Self = Self(1 << 13);
    pub const HAS_DJANGO: Self = Self(1 << 14);
    pub const HAS_EXPRESS: Self = Self(1 << 15);

    pub const fn empty() -> Self { Self(0) }
    pub const fn all() -> Self { Self(u32::MAX) }

    pub fn has(self, flag: Self) -> bool { self.0 & flag.0 != 0 }
    pub fn set(&mut self, flag: Self) { self.0 |= flag.0; }
    pub fn union(self, other: Self) -> Self { Self(self.0 | other.0) }
    pub fn is_empty(self) -> bool { self.0 == 0 }
}
```

**Step 2: Extend `compute_content_flags()`**

Add detection logic for each new flag category in `compute_content_flags()`. Use simple `str::contains()` checks (no regex):

```rust
fn compute_content_flags(content: &str) -> ContentFlags {
    let mut flags = ContentFlags::default();

    // FILE_OPS (existing)
    if content.contains("open(") || content.contains("unlink") || content.contains("rmdir")
        || content.contains("mkdir") || content.contains("readFile") || content.contains("writeFile")
        || content.contains("shutil") || content.contains("os.remove") || content.contains("sendFile")
        || content.contains("send_file") || content.contains("createReadStream")
    { flags.set(ContentFlags::FILE_OPS); }

    // PATH_OPS (existing)
    if content.contains("path.join") || content.contains("path.resolve")
        || content.contains("os.path") || content.contains("filepath") || content.contains("pathlib")
    { flags.set(ContentFlags::PATH_OPS); }

    // HAS_SQL
    if content.contains("SELECT ") || content.contains("INSERT ") || content.contains("UPDATE ")
        || content.contains("DELETE ") || content.contains("CREATE ") || content.contains("DROP ")
        || content.contains("select ") || content.contains("insert ") || content.contains("execute(")
    { flags.set(ContentFlags::HAS_SQL); }

    // HAS_IMPORT
    if content.contains("import ") || content.contains("require(") || content.contains("from ")
    { flags.set(ContentFlags::HAS_IMPORT); }

    // HAS_EVAL
    if content.contains("eval(") || content.contains("exec(") || content.contains("Function(")
    { flags.set(ContentFlags::HAS_EVAL); }

    // HAS_HTTP_CLIENT
    if content.contains("requests.") || content.contains("fetch(") || content.contains("axios")
        || content.contains("urllib") || content.contains("http.get") || content.contains("http.post")
        || content.contains("HttpClient") || content.contains("ureq") || content.contains("reqwest")
    { flags.set(ContentFlags::HAS_HTTP_CLIENT); }

    // HAS_USER_INPUT
    if content.contains("request.") || content.contains("req.body") || content.contains("req.query")
        || content.contains("req.params") || content.contains("request.GET") || content.contains("request.POST")
        || content.contains("input(") || content.contains("sys.argv") || content.contains("process.argv")
    { flags.set(ContentFlags::HAS_USER_INPUT); }

    // HAS_CRYPTO
    if content.contains("hashlib") || content.contains("crypto") || content.contains("md5")
        || content.contains("sha1") || content.contains("DES") || content.contains("AES")
        || content.contains("cipher") || content.contains("encrypt") || content.contains("decrypt")
    { flags.set(ContentFlags::HAS_CRYPTO); }

    // HAS_TEMPLATE
    if content.contains("render(") || content.contains("template") || content.contains("jinja")
        || content.contains("Markup(") || content.contains("innerHTML")
    { flags.set(ContentFlags::HAS_TEMPLATE); }

    // HAS_SERIALIZE
    if content.contains("pickle") || content.contains("marshal") || content.contains("yaml.load")
        || content.contains("json.loads") || content.contains("deserialize")
    { flags.set(ContentFlags::HAS_SERIALIZE); }

    // HAS_EXEC
    if content.contains("os.system") || content.contains("subprocess") || content.contains("child_process")
        || content.contains("exec(") || content.contains("popen")
    { flags.set(ContentFlags::HAS_EXEC); }

    // HAS_SECRET_PATTERN
    if content.contains("password") || content.contains("secret") || content.contains("api_key")
        || content.contains("token") || content.contains("private_key") || content.contains("BEGIN RSA")
    { flags.set(ContentFlags::HAS_SECRET_PATTERN); }

    // HAS_ML
    if content.contains("torch") || content.contains("numpy") || content.contains("tensorflow")
        || content.contains("sklearn") || content.contains("pandas")
    { flags.set(ContentFlags::HAS_ML); }

    // HAS_REACT
    if content.contains("useState") || content.contains("useEffect") || content.contains("React")
        || content.contains("react")
    { flags.set(ContentFlags::HAS_REACT); }

    // HAS_DJANGO
    if content.contains("django") || content.contains("Django")
    { flags.set(ContentFlags::HAS_DJANGO); }

    // HAS_EXPRESS
    if content.contains("express") || content.contains("app.get(") || content.contains("app.post(")
        || content.contains("router.")
    { flags.set(ContentFlags::HAS_EXPRESS); }

    flags
}
```

**Step 3: Add tests for new flags**

Add inline tests after the existing content flags tests:

```rust
#[test]
fn test_content_flags_sql() {
    let flags = compute_content_flags("cursor.execute(\"SELECT * FROM users\")");
    assert!(flags.has(ContentFlags::HAS_SQL));
}

#[test]
fn test_content_flags_import() {
    let flags = compute_content_flags("import os\nfrom pathlib import Path");
    assert!(flags.has(ContentFlags::HAS_IMPORT));
}

#[test]
fn test_content_flags_ml() {
    let flags = compute_content_flags("import torch\nmodel = torch.nn.Linear(10, 5)");
    assert!(flags.has(ContentFlags::HAS_ML));
}
```

**Step 4: Verify**

Run: `cargo test detectors::detector_context`
Expected: All tests pass

**Step 5: Commit**

```bash
git add src/detectors/detector_context.rs
git commit -m "feat: extend ContentFlags from 2 to 16 categories"
```

---

### Task 2: Create FileIndex with lazy pre-computation

**Files:**
- Create: `src/detectors/file_index.rs`
- Modify: `src/detectors/mod.rs` (add `pub mod file_index;` and re-export)

**Step 1: Create `file_index.rs`**

```rust
//! Pre-indexed file content with lazy per-file computations.
//!
//! Built once before detector execution. Detectors query the index
//! instead of iterating raw files. Expensive per-file operations
//! (lowercase, tokenization) are computed lazily via OnceLock and
//! shared across all detectors.

use crate::detectors::detector_context::ContentFlags;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

/// A single file in the index with lazy pre-computed fields.
pub struct FileEntry {
    pub path: PathBuf,
    pub content: Arc<str>,
    pub flags: ContentFlags,
    lowercased: OnceLock<Arc<str>>,
    word_set: OnceLock<Arc<HashSet<String>>>,
}

impl FileEntry {
    pub fn new(path: PathBuf, content: Arc<str>, flags: ContentFlags) -> Self {
        Self {
            path,
            content,
            flags,
            lowercased: OnceLock::new(),
            word_set: OnceLock::new(),
        }
    }

    /// Get lowercased content (computed once, then cached).
    pub fn content_lower(&self) -> &Arc<str> {
        self.lowercased.get_or_init(|| {
            Arc::from(self.content.to_ascii_lowercase())
        })
    }

    /// Get the set of word tokens in this file (computed once, then cached).
    /// Words are sequences of [a-zA-Z_][a-zA-Z0-9_]* — valid identifiers.
    pub fn word_set(&self) -> &Arc<HashSet<String>> {
        self.word_set.get_or_init(|| {
            let mut words = HashSet::new();
            let bytes = self.content.as_bytes();
            let mut i = 0;
            while i < bytes.len() {
                if bytes[i].is_ascii_alphabetic() || bytes[i] == b'_' {
                    let start = i;
                    i += 1;
                    while i < bytes.len() && (bytes[i].is_ascii_alphanumeric() || bytes[i] == b'_') {
                        i += 1;
                    }
                    let word = &self.content[start..i];
                    if word.len() >= 2 {
                        words.insert(word.to_string());
                    }
                } else {
                    i += 1;
                }
            }
            Arc::new(words)
        })
    }
}

/// Pre-indexed collection of source files.
///
/// Built once before detector execution. Provides O(1) filtering
/// by file extension and content flags.
pub struct FileIndex {
    entries: Vec<FileEntry>,
    /// Extension → Vec<index into entries>
    by_extension: rustc_hash::FxHashMap<String, Vec<usize>>,
}

impl FileIndex {
    /// Build a FileIndex from pre-loaded file data.
    ///
    /// Takes ownership of the (path, content, flags) tuples already
    /// computed during DetectorContext::build().
    pub fn new(file_data: Vec<(PathBuf, Arc<str>, ContentFlags)>) -> Self {
        let mut by_extension: rustc_hash::FxHashMap<String, Vec<usize>> =
            rustc_hash::FxHashMap::default();

        let mut entries = Vec::with_capacity(file_data.len());
        for (i, (path, content, flags)) in file_data.into_iter().enumerate() {
            if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
                by_extension
                    .entry(ext.to_string())
                    .or_default()
                    .push(i);
            }
            entries.push(FileEntry::new(path, content, flags));
        }

        Self { entries, by_extension }
    }

    /// Get all file entries.
    pub fn all(&self) -> &[FileEntry] {
        &self.entries
    }

    /// Get file entries matching ANY of the given extensions AND having
    /// at least one of the required content flags.
    ///
    /// If `required_flags` is empty, returns all files with matching extensions.
    pub fn matching(&self, extensions: &[&str], required_flags: ContentFlags) -> Vec<&FileEntry> {
        let mut result = Vec::new();
        for ext in extensions {
            if let Some(indices) = self.by_extension.get(*ext) {
                for &idx in indices {
                    let entry = &self.entries[idx];
                    if required_flags.is_empty() || entry.flags.has(required_flags) {
                        result.push(entry);
                    }
                }
            }
        }
        result
    }

    /// Get file entries matching ANY of the given extensions (no flag filter).
    pub fn by_extensions(&self, extensions: &[&str]) -> Vec<&FileEntry> {
        let mut result = Vec::new();
        for ext in extensions {
            if let Some(indices) = self.by_extension.get(*ext) {
                for &idx in indices {
                    result.push(&self.entries[idx]);
                }
            }
        }
        result
    }

    /// Get a file entry by path.
    pub fn get(&self, path: &Path) -> Option<&FileEntry> {
        self.entries.iter().find(|e| e.path == path)
    }

    /// Number of files in the index.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Whether the index is empty.
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_data() -> Vec<(PathBuf, Arc<str>, ContentFlags)> {
        vec![
            (PathBuf::from("/repo/app.py"), Arc::from("import os\ndef main(): pass"), ContentFlags::HAS_IMPORT),
            (PathBuf::from("/repo/sql.py"), Arc::from("SELECT * FROM users"), ContentFlags::HAS_SQL),
            (PathBuf::from("/repo/safe.py"), Arc::from("x = 1 + 2"), ContentFlags::empty()),
            (PathBuf::from("/repo/index.ts"), Arc::from("import React from 'react'"), {
                let mut f = ContentFlags::empty();
                f.set(ContentFlags::HAS_IMPORT);
                f.set(ContentFlags::HAS_REACT);
                f
            }),
        ]
    }

    #[test]
    fn test_file_index_matching_with_flags() {
        let index = FileIndex::new(test_data());
        let sql_files = index.matching(&["py"], ContentFlags::HAS_SQL);
        assert_eq!(sql_files.len(), 1);
        assert!(sql_files[0].path.ends_with("sql.py"));
    }

    #[test]
    fn test_file_index_by_extensions() {
        let index = FileIndex::new(test_data());
        let py_files = index.by_extensions(&["py"]);
        assert_eq!(py_files.len(), 3);
        let ts_files = index.by_extensions(&["ts"]);
        assert_eq!(ts_files.len(), 1);
    }

    #[test]
    fn test_file_entry_content_lower() {
        let entry = FileEntry::new(
            PathBuf::from("test.py"),
            Arc::from("Hello WORLD"),
            ContentFlags::empty(),
        );
        assert_eq!(entry.content_lower().as_ref(), "hello world");
        // Second call returns cached value (same Arc)
        let ptr1 = Arc::as_ptr(entry.content_lower());
        let ptr2 = Arc::as_ptr(entry.content_lower());
        assert_eq!(ptr1, ptr2);
    }

    #[test]
    fn test_file_entry_word_set() {
        let entry = FileEntry::new(
            PathBuf::from("test.py"),
            Arc::from("def hello_world():\n    x = 42"),
            ContentFlags::empty(),
        );
        let words = entry.word_set();
        assert!(words.contains("def"));
        assert!(words.contains("hello_world"));
        // Single-char 'x' excluded (len < 2)
        assert!(!words.contains("x"));
        // Number '42' excluded (not alphabetic start)
        assert!(!words.contains("42"));
    }

    #[test]
    fn test_file_index_empty_flags_returns_all_matching_ext() {
        let index = FileIndex::new(test_data());
        let all_py = index.matching(&["py"], ContentFlags::empty());
        assert_eq!(all_py.len(), 3);
    }
}
```

**Step 2: Register the module**

In `src/detectors/mod.rs`, add after `pub mod file_provider;` (line 21):
```rust
pub mod file_index;
```

And add re-export after the file_provider re-export (line 164):
```rust
pub use file_index::{FileIndex, FileEntry};
```

**Step 3: Verify**

Run: `cargo test detectors::file_index`
Expected: All 5 tests pass

**Step 4: Commit**

```bash
git add src/detectors/file_index.rs src/detectors/mod.rs
git commit -m "feat: add FileIndex with lazy pre-computed content"
```

---

### Task 3: Create AnalysisContext and extend Detector trait

**Files:**
- Modify: `src/detectors/base.rs:304-450` (Detector trait)
- Create: `src/detectors/analysis_context.rs`
- Modify: `src/detectors/mod.rs`

**Step 1: Create `analysis_context.rs`**

```rust
//! Unified context passed to all detectors.
//!
//! Bundles graph, files, function contexts, taint results, and
//! detector context into a single struct. Built once before
//! detector execution.

use crate::detectors::detector_context::DetectorContext;
use crate::detectors::file_index::FileIndex;
use crate::detectors::function_context::FunctionContextMap;
use crate::detectors::taint::centralized::CentralizedTaintResults;
use crate::graph::GraphQuery;
use std::sync::Arc;

/// Unified analysis context passed to every detector's `detect()` method.
///
/// All fields are `Arc`-wrapped for zero-cost sharing across parallel detectors.
pub struct AnalysisContext<'g> {
    /// Read-only access to the code graph (petgraph backend).
    pub graph: &'g dyn GraphQuery,

    /// Pre-indexed files with lazy lowercased content and word sets.
    pub files: Arc<FileIndex>,

    /// Pre-computed function contexts (betweenness, roles, degree).
    pub functions: Arc<FunctionContextMap>,

    /// Pre-computed taint analysis results.
    pub taint: Arc<CentralizedTaintResults>,

    /// Shared detector context (callers/callees maps, class hierarchy).
    pub detector_ctx: Arc<DetectorContext>,
}

impl<'g> AnalysisContext<'g> {
    /// Repository root path.
    pub fn repo_path(&self) -> &std::path::Path {
        // Use the first file's parent directory as a heuristic, or fall back
        // to the path stored in detector_ctx
        &self.detector_ctx.repo_path
    }
}
```

**Step 2: Add `repo_path` to `DetectorContext`**

In `detector_context.rs`, add a field to `DetectorContext` struct (around line 82):

```rust
pub struct DetectorContext {
    // ... existing fields ...
    /// Repository root path
    pub repo_path: std::path::PathBuf,
}
```

Update `DetectorContext::build()` to accept and store `repo_path`:
- Add `repo_path: &std::path::Path` parameter
- Store `repo_path: repo_path.to_path_buf()` in the returned struct

Update all call sites of `DetectorContext::build()` in `engine.rs` and `detect.rs` to pass `repo_path`.

**Step 3: Add new methods to `Detector` trait**

In `base.rs`, add these methods to the `Detector` trait (after `set_detector_context` at line 449):

```rust
    /// File extensions this detector processes.
    ///
    /// Return empty slice for graph-only detectors that don't scan files.
    /// Engine uses this to pre-filter files before calling detect_ctx().
    fn file_extensions(&self) -> &'static [&'static str] {
        &[]
    }

    /// Content flags required for files this detector processes.
    ///
    /// Files without ANY of these flags are skipped.
    /// Return `ContentFlags::empty()` to receive all files (no filtering).
    fn content_requirements(&self) -> super::detector_context::ContentFlags {
        super::detector_context::ContentFlags::empty()
    }

    /// Run detection with the unified AnalysisContext.
    ///
    /// This is the new primary detection entry point. The default implementation
    /// delegates to the legacy detect() method for backward compatibility.
    fn detect_ctx(&self, ctx: &super::analysis_context::AnalysisContext) -> anyhow::Result<Vec<crate::models::Finding>> {
        // Default: delegate to legacy detect() with a shim FileProvider
        self.detect(ctx.graph, &ctx.as_file_provider())
    }
```

**Step 4: Add `as_file_provider()` shim to AnalysisContext**

This enables gradual migration — detectors that haven't been updated yet still work:

```rust
impl<'g> AnalysisContext<'g> {
    /// Create a backward-compatible FileProvider shim.
    ///
    /// Used by the default detect_ctx() implementation to delegate to
    /// legacy detect() methods during incremental migration.
    pub fn as_file_provider(&self) -> AnalysisContextFileProvider<'_> {
        AnalysisContextFileProvider { ctx: self }
    }
}

/// Backward-compatible FileProvider wrapping an AnalysisContext.
pub struct AnalysisContextFileProvider<'a> {
    ctx: &'a AnalysisContext<'a>,
}

impl<'a> crate::detectors::file_provider::FileProvider for AnalysisContextFileProvider<'a> {
    fn files(&self) -> &[std::path::PathBuf] {
        // Collect paths from FileIndex entries
        // Note: this allocates, but only used during migration
        &[] // TODO: store file list in FileIndex
    }

    fn files_with_extension(&self, ext: &str) -> Vec<&std::path::Path> {
        self.ctx.files.by_extensions(&[ext])
            .iter()
            .map(|e| e.path.as_path())
            .collect()
    }

    fn files_with_extensions(&self, exts: &[&str]) -> Vec<&std::path::Path> {
        self.ctx.files.by_extensions(exts)
            .iter()
            .map(|e| e.path.as_path())
            .collect()
    }

    fn content(&self, path: &std::path::Path) -> Option<std::sync::Arc<String>> {
        self.ctx.files.get(path).map(|e| std::sync::Arc::new(e.content.to_string()))
    }

    fn masked_content(&self, path: &std::path::Path) -> Option<std::sync::Arc<String>> {
        crate::cache::global_cache().masked_content(path)
    }

    fn repo_path(&self) -> &std::path::Path {
        &self.ctx.detector_ctx.repo_path
    }
}
```

**Step 5: Register the module and add re-exports**

In `src/detectors/mod.rs`:
```rust
pub mod analysis_context;
pub use analysis_context::AnalysisContext;
```

**Step 6: Verify**

Run: `cargo check`
Expected: Compiles with no errors (backward compatible — no detector needs to change yet)

Run: `cargo test --lib`
Expected: All existing tests pass

**Step 7: Commit**

```bash
git add src/detectors/analysis_context.rs src/detectors/base.rs src/detectors/detector_context.rs src/detectors/mod.rs src/detectors/engine.rs src/cli/analyze/detect.rs
git commit -m "feat: add AnalysisContext, FileIndex shim, and new Detector trait methods"
```

---

### Task 4: Wire FileIndex into the engine pipeline

**Files:**
- Modify: `src/detectors/engine.rs` (GdPrecomputed, precompute_gd_startup, run_graph_dependent, run_single_detector)
- Modify: `src/detectors/detector_context.rs` (return file data for FileIndex construction)
- Modify: `src/cli/analyze/detect.rs` (construct FileIndex + AnalysisContext)

**Step 1: Make DetectorContext::build() return file data for FileIndex**

Change `DetectorContext::build()` to also return the raw `Vec<(PathBuf, Arc<str>, ContentFlags)>` so the caller can construct a `FileIndex` without re-reading files:

```rust
pub fn build(...) -> (Self, Vec<(PathBuf, Arc<str>, ContentFlags)>) {
    // ... existing code ...
    // Instead of consuming file_data into file_contents + content_flags,
    // clone the data for FileIndex construction
    let file_data_for_index: Vec<(PathBuf, Arc<str>, ContentFlags)> = file_data
        .iter()
        .map(|(p, c, f)| (p.clone(), Arc::clone(c), *f))
        .collect();

    // ... build file_contents and content_flags as before ...

    (Self { ... }, file_data_for_index)
}
```

**Step 2: Add FileIndex to GdPrecomputed**

In `engine.rs`:
```rust
pub struct GdPrecomputed {
    pub contexts: Arc<FunctionContextMap>,
    pub hmm_contexts: Arc<HashMap<String, FunctionContext>>,
    pub taint_results: CentralizedTaintResults,
    pub detector_context: Arc<DetectorContext>,
    pub file_index: Arc<FileIndex>,  // NEW
}
```

**Step 3: Update precompute_gd_startup() to build FileIndex**

In the DetectorContext thread, capture the file data and construct FileIndex:

```rust
let ctx_handle = s.spawn(move || {
    let (det_ctx, file_data) = DetectorContext::build(graph, source_files, vs_clone);
    let file_index = Arc::new(FileIndex::new(file_data));
    (Arc::new(det_ctx), file_index)
});
// ...
let (det_ctx, file_index) = ctx_handle.join().expect("...");

GdPrecomputed {
    contexts: Arc::new(contexts),
    hmm_contexts: Arc::new(hmm_contexts),
    taint_results,
    detector_context: det_ctx,
    file_index,
}
```

**Step 4: Update engine to build AnalysisContext and call detect_ctx()**

In `run_single_detector()`, construct `AnalysisContext` and call `detect_ctx()` instead of `detect()`:

```rust
fn run_single_detector(
    &self,
    detector: &Arc<dyn Detector>,
    graph: &dyn crate::graph::GraphQuery,
    files: &dyn crate::detectors::file_provider::FileProvider,
    contexts: &Arc<FunctionContextMap>,
    analysis_ctx: Option<&AnalysisContext>,  // NEW param
) -> DetectorResult {
    // ... existing start/timing code ...

    let detect_result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        if let Some(ctx) = analysis_ctx {
            detector.detect_ctx(ctx)
        } else if detector.uses_context() {
            detector.detect_with_context(graph, files, &contexts_clone)
        } else {
            detector.detect(graph, files)
        }
    }));
    // ... rest unchanged ...
}
```

**Step 5: Update all call sites of run_single_detector**

Pass `Some(&analysis_ctx)` in run_graph_dependent() and `None` in run_graph_independent() (since GI detectors don't have the full context yet).

**Step 6: Verify**

Run: `cargo check`
Expected: Compiles

Run: `cargo test --lib`
Expected: All tests pass (detect_ctx default delegates to detect)

**Step 7: Benchmark baseline**

```bash
cargo install --path .
repotoire clean ~/personal/cpython
repotoire analyze ~/personal/cpython --workers 1 --log-level warn --timings
```
Expected: Same timing as before (no functional change yet)

**Step 8: Commit**

```bash
git add src/detectors/engine.rs src/detectors/detector_context.rs src/cli/analyze/detect.rs
git commit -m "feat: wire FileIndex into engine pipeline, call detect_ctx()"
```

---

## Phase 2: Migrate Detectors to detect_ctx() (Batched)

Each task migrates a batch of detectors. The pattern is the same for each:

1. Override `file_extensions()` and `content_requirements()` on the detector
2. Override `detect_ctx()` to use `ctx.files.matching()` instead of `files.files_with_extensions()`
3. Replace `content.to_lowercase()` / `line.to_ascii_lowercase()` with `entry.content_lower()`
4. Run `cargo test <detector_module>` after each detector
5. Commit the batch

### Task 5: Migrate security LINE_SCAN detectors (13 TAINT detectors)

**Files (13 detectors):**
- `src/detectors/command_injection.rs`
- `src/detectors/eval_detector.rs`
- `src/detectors/insecure_deserialize.rs`
- `src/detectors/log_injection.rs`
- `src/detectors/nosql_injection.rs`
- `src/detectors/path_traversal.rs`
- `src/detectors/prototype_pollution.rs`
- `src/detectors/sql_injection/mod.rs`
- `src/detectors/ssrf.rs`
- `src/detectors/unsafe_template.rs`
- `src/detectors/xss.rs`
- `src/detectors/xxe.rs`

**Migration pattern for each taint detector:**

```rust
// Add to impl block:
fn file_extensions(&self) -> &'static [&'static str] {
    &["py", "js", "ts", "jsx", "tsx", "rb", "php", "java", "go"]
}

fn content_requirements(&self) -> ContentFlags {
    ContentFlags::HAS_SQL  // or HAS_EVAL, HAS_HTTP_CLIENT, etc.
}

fn detect_ctx(&self, ctx: &AnalysisContext) -> Result<Vec<Finding>> {
    let mut findings = vec![];
    for entry in ctx.files.matching(self.file_extensions(), self.content_requirements()) {
        // Use entry.content instead of files.content(path)
        // Use entry.content_lower() instead of line.to_ascii_lowercase()
        // Taint access via ctx.taint
        // ...existing detection logic adapted to FileEntry...
    }
    Ok(findings)
}
```

**Specific content_requirements per detector:**
- SQLInjectionDetector: `HAS_SQL`
- PathTraversalDetector: `FILE_OPS.union(PATH_OPS)`
- SsrfDetector: `HAS_HTTP_CLIENT`
- EvalDetector: `HAS_EVAL`
- CommandInjectionDetector: `HAS_EXEC`
- XssDetector: `HAS_TEMPLATE`
- UnsafeTemplateDetector: `HAS_TEMPLATE`
- LogInjectionDetector: `ContentFlags::empty()` (logging is everywhere)
- InsecureDeserializeDetector: `HAS_SERIALIZE`
- PrototypePollutionDetector: `ContentFlags::empty()` (JS-specific, check ext)
- NosqlInjectionDetector: `HAS_SQL` (NoSQL uses similar patterns)
- XxeDetector: `HAS_SERIALIZE`

**Micro-optimizations bundled:**
- SQLInjection: Combine 6 regexes into 1 with alternation groups
- PathTraversal: Combine 4 regexes into 1
- SSRF: Eliminate O(n^2) context window join — use pre-indexed lines
- All: Use `entry.content_lower()` instead of per-line `to_ascii_lowercase()`

**Verify after each batch:** `cargo test --lib`

**Commit:**
```bash
git commit -m "perf: migrate 13 taint detectors to AnalysisContext + content flags"
```

---

### Task 6: Migrate remaining LINE_SCAN detectors (34 detectors)

**Files (34 detectors):**
- All detectors classified as LINE_SCAN in the detector classification

**Migration priority (by timing impact):**

1. **UnusedImportsDetector** (553ms) — use `entry.word_set()` instead of regex tokenization
2. **MagicNumbersDetector** (338ms) — eliminate double regex pass
3. **AIBoilerplateDetector** (290ms) — use FileIndex filtering
4. **AIDuplicateBlockDetector** (313ms) — use FileIndex filtering
5. **InsecureTlsDetector** (168ms) — add `HAS_CRYPTO` flag filter
6. **SecretDetector** (113ms) — add `HAS_SECRET_PATTERN` flag filter
7. Remaining 28 detectors: straightforward signature change

**Key optimization for UnusedImportsDetector:**

Replace the regex-based word extraction:
```rust
// BEFORE: per-line regex (expensive)
for cap in WORD.find_iter(line) { usage_set.insert(cap.as_str().to_string()); }

// AFTER: use pre-computed word set from FileEntry
let usage = entry.word_set();
// Check each import symbol against the word set
```

**Verify:** `cargo test --lib`

**Commit:**
```bash
git commit -m "perf: migrate 34 line-scan detectors to AnalysisContext"
```

---

### Task 7: Migrate FUNCTION_GRAPH detectors (39 detectors)

**Files:** All 39 FUNCTION_GRAPH detectors

These detectors iterate `graph.get_functions()` or `graph.get_classes()`. The migration is simpler — they mainly need the signature change, not the FileIndex optimizations.

**Pattern:**
```rust
fn detect_ctx(&self, ctx: &AnalysisContext) -> Result<Vec<Finding>> {
    // Same as current detect(), but using ctx.graph instead of graph parameter
    let functions = ctx.graph.get_functions();
    // ... existing logic ...
}
```

Most of these don't use `files` at all (they pass `_files`), so the migration is mechanical.

**Verify:** `cargo test --lib`

**Commit:**
```bash
git commit -m "refactor: migrate 39 function-graph detectors to AnalysisContext"
```

---

### Task 8: Migrate CLASS_GRAPH + CROSS_FILE + ARCHITECTURE + METADATA detectors (10 detectors)

**Files:** GodClassDetector, LazyClassDetector, MiddleManDetector, ShotgunSurgeryDetector, DeadCodeDetector, InappropriateIntimacyDetector, ModuleCohesionDetector, CircularDependencyDetector, DepAuditDetector, GHActionsInjectionDetector

Same mechanical migration as Task 7.

**Verify:** `cargo test --lib`

**Commit:**
```bash
git commit -m "refactor: migrate remaining 10 detectors to AnalysisContext"
```

---

## Phase 3: Remove Legacy Path + Benchmark

### Task 9: Remove legacy detect() default and clean up

**Files:**
- Modify: `src/detectors/base.rs` — remove `detect()` and `detect_with_context()` from required trait methods, make them optional with deprecation warnings
- Modify: `src/detectors/engine.rs` — simplify `run_single_detector()` to always call `detect_ctx()`
- Modify: `src/detectors/file_provider.rs` — add deprecation notice (still needed for tests)
- Remove: `AnalysisContextFileProvider` shim from `analysis_context.rs`

**Step 1: Simplify engine dispatch**

```rust
fn run_single_detector(&self, detector: &Arc<dyn Detector>, ctx: &AnalysisContext) -> DetectorResult {
    let name = detector.name().to_string();
    let start = Instant::now();
    let detect_result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        detector.detect_ctx(ctx)
    }));
    // ... rest unchanged ...
}
```

**Step 2: Update all engine methods**

Update `run()`, `run_graph_independent()`, `run_graph_dependent()` to construct and pass `AnalysisContext` instead of separate graph+files+contexts parameters.

**Step 3: Verify**

Run: `cargo test --lib`
Expected: All tests pass

Run: `cargo clippy`
Expected: No new warnings

**Step 4: Commit**

```bash
git commit -m "refactor: remove legacy detect() dispatch, always use detect_ctx()"
```

---

### Task 10: Final benchmark and per-detector micro-optimizations

**Step 1: Benchmark**

```bash
cargo install --path .
repotoire clean ~/personal/cpython
/usr/bin/time -v repotoire analyze ~/personal/cpython --workers 1 --log-level warn --timings
```

Compare against baseline (13.47s single-threaded).

**Step 2: Profile remaining bottlenecks**

Check the `--timings` output. If specific detectors are still slow, apply targeted fixes:

- **duplicate-code**: Pre-allocate sliding window buffer, use rolling hash
- **magic-numbers**: Single regex pass with `entry.content_lower()`
- **UnreachableCode**: Short-circuit on `graph.get_fan_in() > 0`
- **GodClass**: Use pre-built class contexts from DetectorContext

**Step 3: Multi-threaded benchmark**

```bash
repotoire clean ~/personal/cpython
/usr/bin/time -v repotoire analyze ~/personal/cpython --log-level warn --timings
```

**Step 4: Accuracy check**

Compare finding counts between old binary and new:
- Total findings should be within ±5%
- No critical/high findings should disappear (content flags might cause some to be missed if flags are too aggressive)

**Step 5: Commit**

```bash
git commit -m "perf: final micro-optimizations for content-indexed detection"
```

---

## Verification Checklist

After all tasks:

```bash
# All tests pass
cargo test --lib

# No clippy warnings
cargo clippy

# Single-threaded benchmark
repotoire clean ~/personal/cpython
/usr/bin/time -v repotoire analyze ~/personal/cpython --workers 1 --log-level warn --timings
# Target: <10s wall (down from 13.47s)
# Detect phase: <6s (down from 9.46s)

# Multi-threaded benchmark
repotoire clean ~/personal/cpython
/usr/bin/time -v repotoire analyze ~/personal/cpython --log-level warn --timings
# Target: <3.5s wall (down from 4.25s)

# Accuracy: finding count within ±5% of baseline
```
