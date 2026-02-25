# Pipeline Hardening Design

**Date:** 2026-02-25
**Goal:** Protect the analysis pipeline from symlink escapes, path traversal attacks, oversized files, and expose the hidden `--since` flag.

## Context

The feature completeness audit found that the analysis pipeline lacks several security protections that were claimed in documentation:
- **Symlinks:** Zero `is_symlink()` checks across the codebase. `WalkBuilder` follows symlinks by default.
- **Path traversal:** MCP handler has canonicalize+starts_with protection, but the main analysis pipeline does not. The `get_changed_files_since()` function joins git output directly to `repo_path` without boundary validation — `../../etc/passwd` would pass.
- **File size:** Only the parser has a 2MB guardrail (`parsers/mod.rs:34`). No pre-filtering at file collection time.
- **`--since` flag:** Fully implemented in `cli/analyze/files.rs:65-82` but hardcoded to `None` in the CLI dispatcher.

## Design

### Core: `validate_file()` function

A single validation function in `cli/analyze/files.rs` that all file collection paths call before accepting a file:

```
validate_file(path: &Path, repo_canonical: &Path) -> Option<PathBuf>
  1. path.is_symlink()? -> skip, tracing::warn
  2. path.canonicalize() -> resolve to real path
  3. canonical.starts_with(repo_canonical)? -> reject if outside boundary
  4. metadata().len() > MAX_FILE_BYTES? -> skip, tracing::warn
  Returns: Some(canonical_path) or None
```

**File size constant:** Reuse the 2MB limit from `parsers/mod.rs`. The parser guardrail stays as defense-in-depth.

### Integration points

1. **`collect_source_files()`** — call `validate_file()` after WalkBuilder yields each entry, before adding to file list.
2. **`get_changed_files_since()`** — call `validate_file()` after `repo_path.join(line)`, before adding to changed files list.

### `--since` flag

Add `#[arg(long)] since: Option<String>` to the `Analyze` command in `cli/mod.rs`, pass through to `analyze::run()` instead of the hardcoded `None`.

### Logging

Use `tracing::warn!` for all rejected files, consistent with the existing parser guardrail behavior. This lets users see what was skipped via `--log-level warn`.

### Testing

Unit tests for `validate_file()` covering:
- Normal files (accepted)
- Symlinks (rejected)
- Path traversal via `../` (rejected)
- Oversized files (rejected)
- Non-existent paths (rejected gracefully)

## Files to modify

- `repotoire-cli/src/cli/analyze/files.rs` — add `validate_file()`, integrate into both collection functions
- `repotoire-cli/src/cli/mod.rs` — add `--since` flag, pass through to `analyze::run()`
- `repotoire-cli/src/cli/analyze/mod.rs` — accept `since` parameter from CLI

## Approach

Centralized `validate_file()` function rather than inline guards at each call site. Single source of truth, one place to test, consistent behavior across all file collection paths.
