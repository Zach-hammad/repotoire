# FileIndex Relative Paths & AIDuplicateBlock FP Elimination Design

## Problem

FileIndex stores **absolute canonical paths** (`/home/user/repo/src/file.rs`). The graph stores **relative paths** (`src/file.rs`). This systemic mismatch causes `find_function_at()` to silently return `None` whenever a detector uses a FileIndex path to query the graph.

**Impact on AIDuplicateBlockDetector:** 12 false positives on self-analysis because:
1. Test function filter calls `find_function_at()` → fails → falls back to weak name heuristic → test functions leak through
2. `verify_semantic_overlap()` calls `resolve_graph_qn()` → `find_function_at()` fails → falls back to simplified QN without impl-type info → all three verification tiers (trait impl, callee overlap, leaf context) degrade to no-ops

**Impact on other detectors:** Any detector that calls `find_function_at()` with a FileIndex-derived path silently gets `None`. This is a latent bug across the codebase.

## Root Cause

```
FileIndex paths:  /home/user/repo/src/file.rs  (absolute, from fs::canonicalize())
Graph paths:      src/file.rs                   (relative, from strip_prefix(repo_path))
                  ↑
                  find_function_at() interns the input string and looks up the spatial index
                  These NEVER match → always None
```

The path format diverges at construction time:
- **FileIndex:** `cli/analyze/files.rs` → `validate_file()` → `fs::canonicalize()` → absolute paths stored in `SourceFiles` → passed to `DetectorContext::build()` → stored in `FileIndex`
- **Graph:** `cli/analyze/graph.rs` → `file_path.strip_prefix(repo_path)` → relative paths interned → stored in `CodeNode.file_path` and `function_spatial_index`

## Design

### Phase 1: FileIndex stores relative paths

**Core change:** In `DetectorContext::build()`, strip `repo_path` prefix from file paths before constructing FileIndex entries. Add `repo_path` field to FileIndex for re-absolutizing if needed.

**file_index.rs:**
```rust
pub struct FileIndex {
    entries: Vec<FileEntry>,
    by_path: HashMap<PathBuf, usize>,
    by_ext: HashMap<String, Vec<usize>>,
    repo_path: PathBuf,  // NEW: for re-absolutizing
}
```

**detector_context.rs** (`build()`):
```rust
// Before passing to FileIndex, convert to relative:
let relative = abs_path.strip_prefix(&repo_path).unwrap_or(&abs_path);
// But still read content using the absolute path
let content = std::fs::read_to_string(&abs_path)?;
file_data.push((relative.to_path_buf(), Arc::from(content.as_str()), flags));
```

**Downstream fixes:**
- `cli/analyze/detect.rs` incremental cache: path comparisons must use relative paths
- `analysis_context.rs` test helpers: mock paths should be relative
- `ai_duplicate_block.rs`: remove the manual `strip_prefix` workaround in the test filter and `resolve_graph_qn`

### Phase 2: AIDuplicateBlock cleanup

With FileIndex paths now matching graph paths, `find_function_at()` works. This means:

1. **Test filter (line ~625):** `find_function_at()` succeeds → `is_test_function(qn)` checks the `FunctionContextMap` → `#[test]`-decorated functions are filtered via the `has_test_decorator()` fix already in `FunctionContextBuilder`

2. **`verify_semantic_overlap()`:** `resolve_graph_qn()` succeeds → real QNs with impl-type info → tiers 1-3 work:
   - Tier 1: Same trait, different types → reject
   - Tier 2: Callee overlap < 0.3 → reject
   - Tier 3: Leaf functions in different impl types → reject

3. **Remove workarounds:** Delete `strip_prefix` logic from the detector's test filter and `resolve_graph_qn()`, since paths already match.

### Phase 3: Validate and iterate

After Phase 1+2, run self-analysis to verify:
- Test function FPs eliminated (expected: all `test_*` functions filtered)
- Semantic overlap FPs eliminated (expected: tiers 1-3 catch structurally similar but unrelated functions)
- True positives preserved (e.g., `should_exclude_from_calibration ≈ is_test_or_fixture_path`)

If FPs remain, add targeted tiers to `verify_semantic_overlap()`:
- Tier 4: Matched enum type comparison (extract enum name from AST, reject if different enums)
- Tier 5: Return value comparison (functions returning different string literals)

## Files to Modify

| File | Change | Risk |
|------|--------|------|
| `src/detectors/file_index.rs` | Add `repo_path` field, document relative path contract | Low — internal type |
| `src/detectors/detector_context.rs` | Strip prefix in `build()` before FileIndex construction | Medium — affects all detectors |
| `src/cli/analyze/detect.rs` | Fix incremental cache path comparison | Low — isolated comparison |
| `src/detectors/ai_duplicate_block.rs` | Remove manual `strip_prefix` workarounds | Low — simplification |
| `src/detectors/analysis_context.rs` | Update test helpers for relative paths | Low — test-only |
| `src/detectors/function_context.rs` | Keep `has_test_decorator()` (already added) | None — already done |

## Expected Outcome

- 12 → ~0 AIDuplicateBlock FPs on self-analysis
- `find_function_at()` works reliably for ALL detectors (systemic fix)
- SARIF output uses relative paths (more portable)
- Finding display paths shorter and cleaner
- Graph ↔ FileIndex path lookups work naturally without manual normalization

## Verification

```bash
cargo test                           # All tests pass
cargo run --release -- clean .
cargo run --release -- analyze .     # Check AIDuplicateBlock findings
# Expected: 0 test function pairs, only genuine duplicates
```
