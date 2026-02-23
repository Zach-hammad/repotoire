# Centralized File Provider — Design Document

**Date**: 2026-02-23
**Status**: Approved

## Problem

53 of ~65 detectors independently walk the repository filesystem using their own `WalkBuilder`. This causes:

1. **53x redundant filesystem I/O** — each detector traverses the entire repo tree independently
2. **No exclusion pattern support** — detector walks only respect `.gitignore`, not `ExcludeConfig` default patterns (vendor, node_modules, dist, etc.)
3. **Postprocess workaround** — findings from excluded paths are filtered after detection, wasting compute
4. **Poor testability** — detector tests require real filesystem fixtures instead of in-memory mocks
5. **Duplicated boilerplate** — identical `WalkBuilder` setup code in 53 files

## Solution

Add a `FileProvider` trait that the engine builds once (from the already-collected file list) and passes to every detector via the `detect()` signature. Detectors replace their `WalkBuilder` loops with `files.files_with_extension()` and `files.content()` calls.

## Architecture

### FileProvider Trait

```rust
// src/detectors/file_provider.rs
pub trait FileProvider: Send + Sync {
    /// All source files (already filtered by exclusion patterns)
    fn files(&self) -> &[PathBuf];

    /// Files matching a specific extension (e.g., "py", "js")
    fn files_with_extension(&self, ext: &str) -> Vec<&Path>;

    /// Raw file content (cached)
    fn content(&self, path: &Path) -> Option<Arc<String>>;

    /// Masked content (comments/strings replaced for pattern matching)
    fn masked_content(&self, path: &Path) -> Option<Arc<String>>;

    /// Repository root path (for relative path computation)
    fn repo_path(&self) -> &Path;
}
```

### SourceFiles Implementation

```rust
pub struct SourceFiles {
    files: Vec<PathBuf>,
    repo_path: PathBuf,
}

impl FileProvider for SourceFiles {
    fn files(&self) -> &[PathBuf] { &self.files }
    fn content(&self, path: &Path) -> Option<Arc<String>> {
        crate::cache::global_cache().content(path)
    }
    fn masked_content(&self, path: &Path) -> Option<Arc<String>> {
        crate::cache::global_cache().masked_content(path)
    }
    fn files_with_extension(&self, ext: &str) -> Vec<&Path> {
        self.files.iter()
            .filter(|p| p.extension().and_then(|e| e.to_str()) == Some(ext))
            .map(|p| p.as_path())
            .collect()
    }
    fn repo_path(&self) -> &Path { &self.repo_path }
}
```

### Detector Trait Change

```rust
pub trait Detector: Send + Sync {
    fn name(&self) -> &'static str;
    fn description(&self) -> &'static str;

    fn detect(
        &self,
        graph: &dyn GraphQuery,
        files: &dyn FileProvider,
    ) -> Result<Vec<Finding>>;

    fn detect_with_context(
        &self,
        graph: &dyn GraphQuery,
        files: &dyn FileProvider,
        contexts: &Arc<FunctionContextMap>,
    ) -> Result<Vec<Finding>> {
        self.detect(graph, files)
    }

    // ... other methods unchanged
}
```

### Migration Pattern

Each file-scanning detector changes from:
```rust
fn detect(&self, graph: &dyn GraphQuery) -> Result<Vec<Finding>> {
    let walker = WalkBuilder::new(&self.repository_path)
        .hidden(false).git_ignore(true).build();
    for entry in walker.filter_map(|e| e.ok()) {
        let path = entry.path();
        if path.extension() == Some("py") {
            if let Some(content) = global_cache().masked_content(path) { ... }
        }
    }
}
```

To:
```rust
fn detect(&self, _graph: &dyn GraphQuery, files: &dyn FileProvider) -> Result<Vec<Finding>> {
    for path in files.files_with_extension("py") {
        if let Some(content) = files.masked_content(path) { ... }
    }
}
```

### Data Flow

```
collect_files_for_analysis(repo_path, exclude)  [already exists]
  -> FileCollectionResult { all_files }
  -> SourceFiles::new(all_files, repo_path)      [new]
  -> engine.run(graph, &source_files)             [changed]
  -> detector.detect(graph, files)                [changed]
  -> files.files_with_extension("py")             [new - replaces WalkBuilder]
  -> files.content(path)                          [delegates to global_cache]
```

### Testing Benefits

```rust
// MockFileProvider for unit tests
struct MockFileProvider {
    files: Vec<(PathBuf, String)>,
}

impl FileProvider for MockFileProvider {
    fn content(&self, path: &Path) -> Option<Arc<String>> {
        self.files.iter()
            .find(|(p, _)| p == path)
            .map(|(_, c)| Arc::new(c.clone()))
    }
    // ...
}

// Detector test without filesystem
let mock = MockFileProvider::new(vec![("src/main.py", "eval(user_input)")]);
let findings = EvalDetector::new().detect(&mock_graph, &mock)?;
assert_eq!(findings.len(), 1);
```

## Scope

- **53 detector files** changed (mechanical: remove WalkBuilder, use `files` parameter)
- **1 new file**: `src/detectors/file_provider.rs`
- **3 modified infrastructure files**: `base.rs` (trait), `engine.rs` (pass files), `detect.rs` (build SourceFiles)
- **1 modified registration file**: `mod.rs` (simplify constructors)
- **Graph-only detectors** (~10): just add `_files: &dyn FileProvider` to signature, ignore it
- **Existing tests**: update to pass a `MockFileProvider` or `SourceFiles`

## Expected Impact

- **Performance**: 53x filesystem walks → 1x (already done by file collection)
- **Correctness**: Vendor exclusion at source, not postprocess
- **Testability**: Detectors testable without filesystem fixtures
- **Code reduction**: ~50 lines of WalkBuilder boilerplate removed per detector (~2,500 lines total)
- **Consistency**: All detectors use the same filtered file list
