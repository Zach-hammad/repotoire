# Centralized File Provider — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace 53 independent WalkBuilder instances with a shared FileProvider trait passed to detectors via detect().

**Architecture:** Create a FileProvider trait with files/content/masked_content methods. Build a SourceFiles implementation from the already-collected file list. Change the Detector trait to receive &dyn FileProvider. Migrate all 53 file-walking detectors to use it, and update 49 graph-only detectors' signatures.

**Tech Stack:** Rust, ignore crate (WalkBuilder removal), std::sync::Arc, DashMap (existing global cache)

---

### Task 1: Create FileProvider trait and SourceFiles implementation

**Files:**
- Create: `repotoire-cli/src/detectors/file_provider.rs`
- Modify: `repotoire-cli/src/detectors/mod.rs` (add module declaration)

**Step 1: Create the FileProvider trait and SourceFiles struct**

Create `repotoire-cli/src/detectors/file_provider.rs`:

```rust
//! Centralized file access for detectors.
//!
//! Provides a single pre-walked file list and cached content access,
//! replacing 53 independent WalkBuilder instances across detectors.

use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Provides file access to detectors without filesystem walking.
///
/// Built once by the engine from the pre-collected file list, then shared
/// across all detectors. Handles exclusion patterns, content caching, and
/// extension-based filtering.
pub trait FileProvider: Send + Sync {
    /// All source files (already filtered by exclusion patterns)
    fn files(&self) -> &[PathBuf];

    /// Files matching a specific extension (e.g., "py", "js")
    fn files_with_extension(&self, ext: &str) -> Vec<&Path>;

    /// Files matching any of the given extensions
    fn files_with_extensions(&self, exts: &[&str]) -> Vec<&Path>;

    /// Raw file content (cached)
    fn content(&self, path: &Path) -> Option<Arc<String>>;

    /// Masked content (comments/strings/docstrings replaced with spaces)
    fn masked_content(&self, path: &Path) -> Option<Arc<String>>;

    /// Repository root path
    fn repo_path(&self) -> &Path;
}

/// Production FileProvider backed by the pre-collected file list and global cache.
pub struct SourceFiles {
    files: Vec<PathBuf>,
    repo_path: PathBuf,
}

impl SourceFiles {
    pub fn new(files: Vec<PathBuf>, repo_path: PathBuf) -> Self {
        Self { files, repo_path }
    }
}

impl FileProvider for SourceFiles {
    fn files(&self) -> &[PathBuf] {
        &self.files
    }

    fn files_with_extension(&self, ext: &str) -> Vec<&Path> {
        self.files
            .iter()
            .filter(|p| p.extension().and_then(|e| e.to_str()) == Some(ext))
            .map(|p| p.as_path())
            .collect()
    }

    fn files_with_extensions(&self, exts: &[&str]) -> Vec<&Path> {
        self.files
            .iter()
            .filter(|p| {
                p.extension()
                    .and_then(|e| e.to_str())
                    .map(|e| exts.contains(&e))
                    .unwrap_or(false)
            })
            .map(|p| p.as_path())
            .collect()
    }

    fn content(&self, path: &Path) -> Option<Arc<String>> {
        crate::cache::global_cache().content(path)
    }

    fn masked_content(&self, path: &Path) -> Option<Arc<String>> {
        crate::cache::global_cache().masked_content(path)
    }

    fn repo_path(&self) -> &Path {
        &self.repo_path
    }
}

/// Mock FileProvider for unit testing detectors without filesystem access.
#[cfg(test)]
pub struct MockFileProvider {
    files: Vec<PathBuf>,
    contents: std::collections::HashMap<PathBuf, Arc<String>>,
    repo_path: PathBuf,
}

#[cfg(test)]
impl MockFileProvider {
    pub fn new(entries: Vec<(&str, &str)>) -> Self {
        let repo_path = PathBuf::from("/mock/repo");
        let mut files = Vec::new();
        let mut contents = std::collections::HashMap::new();
        for (path, content) in entries {
            let pb = repo_path.join(path);
            files.push(pb.clone());
            contents.insert(pb, Arc::new(content.to_string()));
        }
        Self {
            files,
            contents,
            repo_path,
        }
    }
}

#[cfg(test)]
impl FileProvider for MockFileProvider {
    fn files(&self) -> &[PathBuf] {
        &self.files
    }

    fn files_with_extension(&self, ext: &str) -> Vec<&Path> {
        self.files
            .iter()
            .filter(|p| p.extension().and_then(|e| e.to_str()) == Some(ext))
            .map(|p| p.as_path())
            .collect()
    }

    fn files_with_extensions(&self, exts: &[&str]) -> Vec<&Path> {
        self.files
            .iter()
            .filter(|p| {
                p.extension()
                    .and_then(|e| e.to_str())
                    .map(|e| exts.contains(&e))
                    .unwrap_or(false)
            })
            .map(|p| p.as_path())
            .collect()
    }

    fn content(&self, path: &Path) -> Option<Arc<String>> {
        self.contents.get(path).cloned()
    }

    fn masked_content(&self, path: &Path) -> Option<Arc<String>> {
        // In tests, masked = raw (no masking needed)
        self.content(path)
    }

    fn repo_path(&self) -> &Path {
        &self.repo_path
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mock_file_provider_basics() {
        let mock = MockFileProvider::new(vec![
            ("src/main.py", "print('hello')"),
            ("src/utils.js", "const x = 1;"),
        ]);
        assert_eq!(mock.files().len(), 2);
        assert_eq!(mock.files_with_extension("py").len(), 1);
        assert_eq!(mock.files_with_extension("js").len(), 1);
        assert_eq!(mock.files_with_extension("rs").len(), 0);
        assert!(mock.content(mock.files()[0].as_path()).is_some());
    }

    #[test]
    fn test_files_with_extensions() {
        let mock = MockFileProvider::new(vec![
            ("a.py", ""), ("b.js", ""), ("c.ts", ""), ("d.go", ""),
        ]);
        let result = mock.files_with_extensions(&["py", "js"]);
        assert_eq!(result.len(), 2);
    }
}
```

**Step 2: Add module declaration in mod.rs**

In `repotoire-cli/src/detectors/mod.rs`, add near the top with other module declarations:

```rust
pub mod file_provider;
```

And add to the public exports:

```rust
pub use file_provider::{FileProvider, SourceFiles};
```

**Step 3: Run `cargo check`**

Run: `cd /home/zhammad/personal/repotoire/repotoire-cli && cargo check`
Expected: Compiles (new code, no existing code changed yet)

**Step 4: Run new tests**

Run: `cargo test -- test_mock_file_provider test_files_with_extensions`
Expected: 2 tests pass

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/file_provider.rs repotoire-cli/src/detectors/mod.rs
git commit -m "feat: add FileProvider trait and SourceFiles implementation"
```

---

### Task 2: Change the Detector trait signature

**Files:**
- Modify: `repotoire-cli/src/detectors/base.rs`

**Step 1: Add FileProvider import**

At the top of `base.rs`, add:

```rust
use super::file_provider::FileProvider;
```

**Step 2: Update detect() signature**

Change the `Detector` trait `detect` method from:

```rust
fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>>;
```

to:

```rust
fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn FileProvider) -> Result<Vec<Finding>>;
```

**Step 3: Update detect_with_context() signature**

Change from:

```rust
fn detect_with_context(
    &self,
    graph: &dyn crate::graph::GraphQuery,
    _contexts: &Arc<FunctionContextMap>,
) -> Result<Vec<Finding>> {
    self.detect(graph)
}
```

to:

```rust
fn detect_with_context(
    &self,
    graph: &dyn crate::graph::GraphQuery,
    files: &dyn FileProvider,
    _contexts: &Arc<FunctionContextMap>,
) -> Result<Vec<Finding>> {
    self.detect(graph, files)
}
```

**Step 4: Run `cargo check`**

Run: `cargo check`
Expected: MANY compile errors — every detector's `detect()` impl now has wrong signature. This is expected; we'll fix them in subsequent tasks.

**Step 5: Commit (WIP)**

Do NOT commit yet — wait until at least some detectors are migrated so the build compiles.

---

### Task 3: Update DetectorEngine to pass FileProvider

**Files:**
- Modify: `repotoire-cli/src/detectors/engine.rs`

**Step 1: Update `run()` signature**

Change `run()` to accept `&dyn FileProvider`:

```rust
pub fn run(&mut self, graph: &dyn crate::graph::GraphQuery, files: &dyn FileProvider) -> Result<Vec<Finding>>
```

Add import at top:
```rust
use super::file_provider::FileProvider;
```

**Step 2: Pass `files` to `run_single_detector()`**

Update `run_single_detector` signature to accept `files: &dyn FileProvider` and pass it to `detector.detect(graph, files)` and `detector.detect_with_context(graph, files, contexts)`.

**Step 3: Pass `files` through parallel dispatch**

In the rayon parallel section, pass `files` to each detector call. Since `&dyn FileProvider` is `Send + Sync`, this works with rayon.

---

### Task 4: Update detect.rs to build SourceFiles and pass to engine

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/detect.rs`

**Step 1: Build SourceFiles from all_files**

In `run_detectors()`, after building the engine, create `SourceFiles`:

```rust
use crate::detectors::{SourceFiles, FileProvider};

let source_files = SourceFiles::new(all_files.to_vec(), repo_path.to_path_buf());
```

**Step 2: Pass to engine.run()**

Change:
```rust
let findings = engine.run(graph)?;
```
to:
```rust
let findings = engine.run(graph, &source_files)?;
```

**Step 3: Do the same for streaming engine if applicable**

Check if `run_detectors_streaming()` also exists and update similarly.

---

### Task 5: Migrate graph-only detectors (Category D — ~49 detectors)

These detectors don't use WalkBuilder. They just need their `detect()` signature updated to accept `files: &dyn FileProvider` (ignored).

**Files:**
- Modify: All Category D detector files (listed below)

**Migration pattern (mechanical, same for all):**

Change:
```rust
fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
```
to:
```rust
fn detect(&self, graph: &dyn crate::graph::GraphQuery, _files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
```

If any of these implement `detect_with_context`, update that signature too.

**Detector list (graph-only):**
```
architectural_bottleneck.rs, circular_dependency.rs, data_clumps.rs,
dead_code.rs, degree_centrality.rs, feature_envy.rs, god_class.rs,
inappropriate_intimacy.rs, inconsistent_returns.rs, influential_code.rs,
lazy_class.rs, long_methods.rs, long_parameter.rs, middle_man.rs,
missing_docstrings.rs, module_cohesion.rs, refused_bequest.rs,
shotgun_surgery.rs, sql_injection.rs, unsafe_template.rs,
content_classifier.rs, context_hmm.rs, core_utility.rs, class_context.rs,
function_context.rs, health_delta.rs, risk_analyzer.rs, root_cause_analyzer.rs,
voting_engine.rs, dep_audit.rs, gh_actions.rs, insecure_tls.rs,
pickle_detector.rs, ai_boilerplate.rs, ai_churn.rs, ai_complexity_spike.rs,
ai_duplicate_block.rs, ai_missing_tests.rs, ai_naming_pattern.rs,
eval_detector.rs, data_flow.rs, ssa_flow.rs, taint.rs, taint_detector.rs
```

**Step 1: Run a sed-like replacement across all graph-only detector files**

For each file, find all `fn detect(&self, graph: &dyn crate::graph::GraphQuery)` and add `_files: &dyn crate::detectors::file_provider::FileProvider` parameter.

**Step 2: Run `cargo check` to find any missed files**

**Step 3: Do NOT commit yet — wait for Task 6**

---

### Task 6: Migrate Category A.2 detectors (WalkBuilder + masked_content — 16 detectors)

These are the simplest file-walking migration since they all use `masked_content()`.

**Files:**
```
boolean_trap.rs, command_injection.rs, cors_misconfig.rs, dead_store.rs,
debug_code.rs, hardcoded_ips.rs, hardcoded_timeout.rs, insecure_cookie.rs,
insecure_crypto.rs, insecure_deserialize.rs, insecure_random.rs,
magic_numbers.rs, message_chain.rs, n_plus_one.rs, secrets.rs,
test_in_production.rs
```

**Migration pattern for each:**

Before:
```rust
pub struct FooDetector {
    repository_path: PathBuf,
    // ... other fields
}

impl FooDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), ... }
    }
}

impl Detector for FooDetector {
    fn detect(&self, graph: &dyn GraphQuery) -> Result<Vec<Finding>> {
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            let path = entry.path();
            if !path.is_file() { continue; }
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py" | "js" | ...) { continue; }

            if let Some(content) = crate::cache::global_cache().masked_content(path) {
                // ... analyze content
            }
        }
    }
}
```

After:
```rust
pub struct FooDetector {
    // repository_path REMOVED
    // ... other fields kept
}

impl FooDetector {
    pub fn new() -> Self {
        Self { ... }
    }
}

impl Detector for FooDetector {
    fn detect(&self, _graph: &dyn GraphQuery, files: &dyn FileProvider) -> Result<Vec<Finding>> {
        for path in files.files_with_extensions(&["py", "js", ...]) {
            if let Some(content) = files.masked_content(path) {
                // ... analyze content (UNCHANGED)
            }
        }
    }
}
```

**Key changes per file:**
1. Remove `repository_path: PathBuf` from struct
2. Remove `repository_path` from `new()` constructor
3. Change `detect()` signature to add `files: &dyn FileProvider`
4. Replace `WalkBuilder` loop with `files.files_with_extensions(&[...])`
5. Replace `crate::cache::global_cache().masked_content(path)` with `files.masked_content(path)`
6. Remove `use ignore::WalkBuilder` import

---

### Task 7: Migrate Category A detectors (WalkBuilder + content — 31 detectors)

Same pattern as Task 6, but using `files.content()` instead of `files.masked_content()`.

**Files:**
```
broad_exception.rs, callback_hell.rs, cleartext_credentials.rs,
commented_code.rs, deep_nesting.rs, django_security.rs, duplicate_code.rs,
express_security.rs, global_variables.rs, implicit_coercion.rs,
jwt_weak.rs, large_files.rs, log_injection.rs, missing_await.rs,
mutable_default_args.rs, nosql_injection.rs, path_traversal.rs,
prototype_pollution.rs, react_hooks.rs, regex_dos.rs, regex_in_loop.rs,
single_char_names.rs, ssrf.rs, string_concat_loop.rs, todo_scanner.rs,
unhandled_promise.rs, unreachable_code.rs, unused_imports.rs,
wildcard_imports.rs, xss.rs, xxe.rs
```

**Migration pattern:** Same as Task 6 but `content()` → `files.content()` instead of `masked_content()`.

**Special cases to watch:**
- `large_files.rs`: May need `files.files()` (all files, no extension filter) to check file sizes
- `duplicate_code.rs`: May walk directories differently
- `django_security.rs`, `jwt_weak.rs`, `cleartext_credentials.rs`, `log_injection.rs`: These 4 use raw `content()` deliberately (not masked) because they scan string values

---

### Task 8: Migrate Category C detectors (WalkBuilder + read_to_string — 5 detectors)

**Files:**
```
empty_catch.rs, generator_misuse.rs, infinite_loop.rs,
surprisal.rs, sync_in_async.rs
```

**Migration pattern:** Replace `std::fs::read_to_string(path)` with `files.content(path)`. The global cache already handles reading, so this is a pure simplification.

---

### Task 9: Update detector registration in mod.rs

**Files:**
- Modify: `repotoire-cli/src/detectors/mod.rs`

**Step 1: Simplify constructors**

In `default_detectors_full()`, `default_detectors_with_config()`, and `default_detectors_with_ngram()`, change all detectors that previously required `repository_path` to use simpler constructors:

Before:
```rust
Arc::new(SecretDetector::new(repository_path)),
Arc::new(EvalDetector::with_repository_path(repository_path.to_path_buf())),
```

After:
```rust
Arc::new(SecretDetector::new()),
Arc::new(EvalDetector::new()),
```

**Step 2: Remove `repository_path` parameter if no longer needed**

If ALL detectors no longer need `repository_path` at construction time, remove it from the function signatures. If some graph-only detectors still need it (e.g., `data_flow.rs` uses it for reading function bodies), keep it for those.

---

### Task 10: Update existing tests

**Files:**
- Modify: All test files that call `detector.detect(graph)` to pass a `MockFileProvider`

**Pattern:**

Before:
```rust
let findings = detector.detect(&mock_graph)?;
```

After:
```rust
use crate::detectors::file_provider::MockFileProvider;
let mock_files = MockFileProvider::new(vec![]);
let findings = detector.detect(&mock_graph, &mock_files)?;
```

For detectors that had file-based tests with temp directories, keep the fixture files but create a `SourceFiles` from the temp dir's file list.

---

### Task 11: Verify and commit

**Step 1: Run cargo check**

Run: `cargo check`
Expected: No errors

**Step 2: Run full test suite**

Run: `cargo test`
Expected: All tests pass (603+ unit + integration)

**Step 3: Run clippy**

Run: `cargo clippy -- -D warnings`
Expected: No warnings

**Step 4: Commit all changes**

```bash
git add -A
git commit -m "refactor: centralize file walking with FileProvider trait

Replace 53 independent WalkBuilder instances with a shared FileProvider
trait. The engine builds a SourceFiles from the pre-collected file list
and passes it to all detectors via detect(). This eliminates redundant
filesystem walks, enforces exclusion patterns at the source, and enables
mock-based detector testing.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 12: Build release binary and validate

**Step 1: Build release**

Run: `cargo build --release`

**Step 2: Validate against Flask**

Run: `./target/release/repotoire analyze /tmp/flask --format json --per-page 0 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{d[\"overall_score\"]:.1f} ({d[\"grade\"]}), {len(d[\"findings\"])} findings')"`

Expected: `90.4 (A-), 36 findings` (unchanged)

**Step 3: Validate against FastAPI**

Expected: `95.5 (A), 186 findings` (unchanged)

**Step 4: Validate against Django**

Expected: `92.1 (A-), 818 findings` (unchanged)

**Step 5: Commit validation results if any changes**
