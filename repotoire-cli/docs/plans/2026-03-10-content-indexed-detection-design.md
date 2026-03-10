# Content-Indexed Detection Architecture

## Problem

The detect phase takes 9.46s single-threaded on CPython (3,415 files, 71,943 functions, 2.1M LOC). 99 detectors each independently iterate files, apply regex, lowercase strings, and tokenize content — repeating identical work. Target: sub-8s total wall time (detect phase ~4s).

## Design

### 1. Unified AnalysisContext

Replace the current `detect(&self, graph: &dyn GraphQuery, files: &dyn FileProvider)` signature with a single rich context that bundles all pre-computed data:

```rust
trait Detector: Send + Sync {
    fn name(&self) -> &'static str;
    fn description(&self) -> &'static str;
    fn detect(&self, ctx: &AnalysisContext) -> Result<Vec<Finding>>;

    // Declarative requirements — engine uses these to optimize
    fn file_extensions(&self) -> &[&str] { &[] }
    fn content_requirements(&self) -> ContentFlags { ContentFlags::empty() }
    fn needs_taint(&self) -> bool { false }
    fn needs_function_contexts(&self) -> bool { false }
}

struct AnalysisContext {
    graph: Arc<dyn GraphQuery>,
    files: Arc<FileIndex>,
    functions: Arc<FunctionContextMap>,
    taint: Arc<TaintResults>,
    detector_ctx: Arc<DetectorContext>,
}
```

### 2. FileIndex with Lazy Pre-Computation

New core data structure that replaces raw `SourceFiles`. Pre-indexes files by extension and content flags. Expensive per-file computations (lowercase, tokenization) are lazy via `OnceLock` — computed on first access, then cached for all subsequent detectors.

```rust
struct FileIndex {
    entries: Vec<FileEntry>,
    by_extension: HashMap<&'static str, Vec<usize>>,
    by_flags: HashMap<ContentFlags, Vec<usize>>,
}

struct FileEntry {
    path: PathBuf,
    content: Arc<str>,
    flags: ContentFlags,
    lowercased: OnceLock<Arc<str>>,
    word_set: OnceLock<Arc<HashSet<SmolStr>>>,
    lines: OnceLock<Arc<Vec<&str>>>,
    lines_lower: OnceLock<Arc<Vec<String>>>,
}
```

### 3. Extended ContentFlags

Expand from 2 flags to 16, computed once per file during FileIndex construction:

```
HAS_SQL_KEYWORD, HAS_IMPORT, HAS_EVAL, HAS_FILE_OP, HAS_PATH_OP,
HAS_HTTP_CLIENT, HAS_USER_INPUT, HAS_CRYPTO, HAS_TEMPLATE,
HAS_SERIALIZE, HAS_EXEC, HAS_SECRET_PATTERN, HAS_ML_IMPORT,
HAS_REACT, HAS_DJANGO, HAS_EXPRESS
```

Detectors declare their `content_requirements()`. The engine's `matching()` method uses the flag index for O(1) file filtering.

### 4. Execution Flow

```
Phase 1: Build FileIndex (~0.3s, parallel file I/O)
  - Read all files, compute ContentFlags, build indices
  - Lazy fields (lowercased, word_set) initialized but NOT computed

Phase 2: Build AnalysisContext (~1.5s wall, existing precompute threads)
  - Taint, HMM, FunctionContextBuilder (existing, conditional)
  - Bundle everything into AnalysisContext

Phase 3: Execute detectors (rayon parallel)
  - Each detector calls ctx.files.matching(extensions, flags)
  - OnceLock ensures thread-safe lazy init of per-file data
  - First detector to access lowercased() triggers computation
```

### 5. Per-Detector Micro-Optimizations

Bundled with the migration to the new API:

- **duplicate-code**: Pre-allocate sliding window buffer, avoid per-block string allocation
- **UnusedImports**: Use `word_set` from FileEntry instead of regex tokenization
- **magic-numbers**: Single regex pass, use `lines_lower` from FileEntry
- **SQLInjection**: Combine 6 regexes into 1, use pre-lowered lines
- **path-traversal**: Combine 4 regexes into 1, use pre-lowered lines
- **SSRF**: Eliminate O(n^2) context join, use content flags
- **UnreachableCode**: Short-circuit on graph fan-in > 0

## Estimated Impact

| Component | Before | After |
|-----------|--------|-------|
| File iteration overhead | ~3s (99 × 3.4K) | ~0.3s (indexed) |
| to_lowercase() repeated | ~0.8s | ~0.1s (cached) |
| Content flag filtering | N/A | -1.5s (skip 60-90%) |
| Per-detector fixes | N/A | -1.4s |
| **Detect phase** | **9.46s** | **~3.5-4s** |
| **Total wall time** | **13.47s** | **~7-8s** |

## Migration Strategy

All 99 detectors must be updated. Group by complexity:
- **Trivial** (~50 detectors): Just change function signature, use `ctx.files.matching()` instead of `files.files_with_extensions()`
- **Moderate** (~35 detectors): Replace `to_lowercase()` with `entry.lines_lower()`, replace manual tokenization with `entry.word_set()`
- **Complex** (~14 detectors): Refactor internal algorithms (duplicate-code, UnusedImports, security detectors with combined regex)
