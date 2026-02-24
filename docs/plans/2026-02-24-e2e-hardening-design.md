# E2E Hardening Design — Ship Quality

## Goal

Make every existing feature in repotoire-cli production-grade: fix all known bugs, harden all error paths, and add tests for every untested module.

## Scope

This is NOT about adding new features. It's about making what exists bulletproof.

## Phase 1: Fix Bugs (3 bugs)

### Bug 1: `findings` command deserialization crash

- **Symptom**: `repotoire findings --top 3` fails with "Failed to parse findings array" / "invalid type: null, expected a map"
- **Root cause**: `cli/analyze/output.rs:345` serializes empty `threshold_metadata` HashMap as JSON `null`. When `cli/findings.rs:37-43` deserializes back via `serde_json::from_value::<Vec<Finding>>()`, serde can't deserialize `null` into `HashMap<String, String>` even with `#[serde(default)]` (default only applies when field is MISSING, not when it's explicitly `null`).
- **Fix**: Change line 345 to serialize empty HashMap as `{}` instead of `null`:
  ```rust
  "threshold_metadata": &f.threshold_metadata,  // {} when empty, {k:v} when populated
  ```

### Bug 2: Path traversal vulnerability in MCP `get_file`

- **Symptom**: `mcp/tools/files.rs:24-28` uses `canonicalize().unwrap_or(raw_path)` — if canonicalize fails (symlinks, permissions), falls back to non-canonical path, defeating the `starts_with` security check.
- **Fix**: Return error if canonicalize fails instead of falling back:
  ```rust
  let canonical = full_path.canonicalize()
      .map_err(|_| anyhow::anyhow!("Cannot resolve path: {}", params.file_path))?;
  let repo_canonical = state.repo_path.canonicalize()
      .map_err(|_| anyhow::anyhow!("Cannot resolve repository root"))?;
  ```

### Bug 3: `CachedFinding` missing `threshold_metadata` field

- **Symptom**: `detectors/incremental_cache.rs:37-54` `CachedFinding` struct is missing `threshold_metadata` field that exists in `Finding`. Cache round-trip silently drops this data.
- **Fix**: Add the field to `CachedFinding` and update `From<&Finding>` and `to_finding()`.

## Phase 2: Harden Error Paths

### 2a: MCP handler silent failures

- `mcp/tools/analysis.rs:83-87` and `analysis.rs:184-188`: Missing "findings" key in JSON silently returns empty array. Should check and return error.
- `mcp/tools/graph.rs:120-143`: `get_callers(name)` returns empty vec for both "no callers" and "function not found". Should check node existence first.

### 2b: Serde null-vs-empty hardening

- All HashMap fields across the codebase need `#[serde(deserialize_with = "deserialize_null_as_default")]` or equivalent to handle explicit JSON `null` safely.
- Affected structs: `Finding`, `CachedFinding`, `GitCache`, `CategoryThresholds`, `StyleProfile`.

### 2c: Config loading resilience

- `config/project_config.rs` silently returns defaults on parse failure — should warn.
- `calibrate/profile.rs` fails silently on unknown enum variants — should skip unknown keys.

## Phase 3: Test Coverage

Add unit tests for all untested modules:

| Module | Files | Test Focus |
|--------|-------|------------|
| Reporters | html.rs, json.rs, markdown.rs, text.rs, sarif.rs | Output structure, edge cases (empty findings, special chars) |
| Config | project_config.rs, user_config.rs | Load/save round-trip, invalid input, missing files |
| Graph query | store_query.rs, store_models.rs | Query correctness, edge cases |
| Git enrichment | enrichment.rs | Graph edge creation from git data |
| MCP handlers | All 5 tool files | Happy path + error path for each of 13 tools |
| CLI findings | findings.rs | Round-trip with fixed serialization, filtering, pagination |
| Cache | incremental_cache.rs round-trip | Finding → CachedFinding → Finding preserves all fields |

## Success Criteria

1. `repotoire findings` works without crash
2. Path traversal in MCP `get_file` is fixed
3. All cache round-trips preserve data
4. Every MCP tool handler tested with happy + error paths
5. All reporter formats tested
6. Config loading tested with valid/invalid/missing files
7. 0 clippy warnings, all tests pass
