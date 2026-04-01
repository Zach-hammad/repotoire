# Cache Auto-Invalidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate `repotoire clean` by making the cache self-managing — auto-invalidation via fingerprint, weekly pruning, and feedback labels layered after cache write.

**Architecture:** Cache fingerprint (XXH3 of binary hash + config + analysis mode + schema version) stored in both cache layers. Recomputed on load, mismatch = cold run. Feedback labels moved after cache write so re-labeling works without invalidation. Stale caches auto-pruned via `.last_used` marker files.

**Tech Stack:** Rust, xxhash-rust (XXH3, already a dependency), serde_json (config hashing), chrono (timestamps, already a dependency)

**Spec:** `docs/superpowers/specs/2026-04-01-cache-auto-invalidation-design.md`

---

## File Structure

### Modified Files
| File | Changes |
|------|---------|
| `src/config/project_config/mod.rs` | Add `Serialize` derive to `ProjectConfig` and nested types (~10 structs) |
| `src/detectors/incremental_cache.rs` | Add `fingerprint` to `CacheData`, `compute_fingerprint()`, `binary_file_hash()`, `sort_json_keys()`, `touch_last_used()`, `prune_stale_caches()` |
| `src/engine/state.rs` | Add `fingerprint` to `SessionMeta` |
| `src/engine/mod.rs` | Fingerprint check in `load()`, store in `save()` |
| `src/cli/analyze/postprocess.rs` | Move `apply_user_labels` to Step 1.5, `filter_by_min_confidence` to Step 1.6 |
| `src/cli/analyze/mod.rs` | Call `prune_stale_caches()` + legacy cleanup on startup, wire `--force-reanalyze`, pass config/mode to cache |
| `src/cli/mod.rs` | Remove `Clean` command, add `--force-reanalyze` flag |

### Deleted Files
| File | Reason |
|------|--------|
| `src/cli/clean.rs` | Replaced by auto-invalidation |

---

### Task 1: Add `Serialize` to ProjectConfig and nested types

**Files:** `src/config/project_config/mod.rs`

This is a prerequisite for fingerprinting — we need to serialize the config to hash it.

- [ ] **Step 1: Add Serialize derive to all config structs**

In `src/config/project_config/mod.rs`, add `Serialize` to the derive list for each of these structs/enums. They currently derive `Deserialize` but not `Serialize`:

| Line | Struct | Current derives |
|------|--------|----------------|
| ~129 | `ProjectType` (enum) | `Debug, Clone, PartialEq, Deserialize` |
| ~148 | `CliDefaults` | `Debug, Clone, Deserialize, Default` |
| ~164 | `CoChangeConfigToml` | `Debug, Clone, Deserialize, Default` |
| ~197 | `OwnershipConfigToml` | `Debug, Clone, Deserialize, Default` |
| ~244 | `ProjectConfig` | `Debug, Clone, Deserialize, Default` |
| ~282 | `DetectorConfigOverride` | `Debug, Clone, Deserialize, Default` |
| ~298 | `ThresholdValue` (enum) | `Debug, Clone, Deserialize` |
| ~310 | `PillarWeights` | `Debug, Clone, Deserialize` |
| ~345 | `ScoringConfig` | `Debug, Clone, Deserialize, Default` |
| ~425 | `ExcludeConfig` | `Debug, Clone, Deserialize, Default` |

For each, add `Serialize` to the derive list. Example:
```rust
// Before:
#[derive(Debug, Clone, Deserialize, Default)]
pub struct ProjectConfig { ... }

// After:
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ProjectConfig { ... }
```

Also ensure `use serde::{Deserialize, Serialize};` is at the top of the file (currently only `Deserialize` may be imported).

- [ ] **Step 2: Verify compilation**

Run: `cargo check`

The `#[serde(skip)] detected_type` field on `ProjectConfig` will correctly be excluded from serialization. The `#[serde(untagged)]` on `ThresholdValue` works with Serialize (distinct variant types: i64, f64, bool, String).

- [ ] **Step 3: Commit**

```bash
git add src/config/project_config/mod.rs
git commit -m "feat(config): add Serialize derive to ProjectConfig and nested types

Prerequisite for cache fingerprinting — config must be serializable
to compute a deterministic hash for cache invalidation."
```

---

### Task 2: Cache fingerprint in IncrementalCache

**Files:** `src/detectors/incremental_cache.rs`

- [ ] **Step 1: Add fingerprint field to CacheData**

Add `fingerprint: Option<u64>` to the `CacheData` struct (line ~88). Use `#[serde(default)]` for backwards compatibility with old caches:

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
struct CacheData {
    version: u32,
    #[serde(default)]
    binary_version: String,
    files: HashMap<String, CachedFile>,
    graph: GraphCache,
    #[serde(default)]
    parse_cache: HashMap<String, CachedParseResult>,
    /// Cache fingerprint — hash of binary + config + analysis mode + schema version.
    /// Old caches without this field deserialize as None and auto-invalidate.
    #[serde(default)]
    fingerprint: Option<u64>,
}
```

- [ ] **Step 2: Add helper functions**

Add these functions at module level (before `impl IncrementalCache`):

```rust
/// Hash the repotoire binary itself for dev-rebuild detection.
/// Returns None if the binary can't be read (deleted, permissions, etc).
fn binary_file_hash() -> Option<u64> {
    let exe = std::env::current_exe().ok()?;
    let bytes = fs::read(&exe).ok()?;
    Some(xxhash_rust::xxh3::xxh3_64(&bytes))
}

/// Recursively sort JSON object keys for deterministic serialization.
/// HashMap iteration order is non-deterministic; this normalizes it.
fn sort_json_keys(value: serde_json::Value) -> serde_json::Value {
    match value {
        serde_json::Value::Object(map) => {
            let sorted: serde_json::Map<String, serde_json::Value> = map
                .into_iter()
                .map(|(k, v)| (k, sort_json_keys(v)))
                .collect::<std::collections::BTreeMap<_, _>>()
                .into_iter()
                .collect();
            serde_json::Value::Object(sorted)
        }
        serde_json::Value::Array(arr) => {
            serde_json::Value::Array(arr.into_iter().map(sort_json_keys).collect())
        }
        other => other,
    }
}

/// Compute cache fingerprint from all analysis inputs.
fn compute_fingerprint(
    binary_hash: u64,
    config: &crate::config::ProjectConfig,
    all_detectors: bool,
) -> u64 {
    let mut buf = Vec::with_capacity(256);
    buf.extend_from_slice(&binary_hash.to_le_bytes());
    match serde_json::to_value(config)
        .and_then(|v| serde_json::to_string(&sort_json_keys(v)))
    {
        Ok(json) => buf.extend_from_slice(json.as_bytes()),
        Err(_) => buf.extend_from_slice(b"__config_serialize_error__"),
    }
    buf.push(all_detectors as u8);
    buf.extend_from_slice(&CACHE_VERSION.to_le_bytes());
    xxhash_rust::xxh3::xxh3_64(&buf)
}
```

Add `xxhash_rust` to the imports at the top if not already there (it's already a dependency via the existing file hashing).

- [ ] **Step 3: Update `IncrementalCache::new()` to accept config and mode**

Change the `new()` signature to accept config and mode for fingerprint computation. The `load_cache` method (called from `new`) needs these to check the fingerprint:

```rust
pub fn new(cache_dir: &Path, config: &crate::config::ProjectConfig, all_detectors: bool) -> Self {
    // ... existing dir creation ...
    let mut instance = Self {
        cache_dir: cache_dir.to_path_buf(),
        cache_file: cache_dir.join("findings_cache.bin"),
        cache: CacheData::default(),
        dirty: false,
        memoized_files_hash: None,
        config_fingerprint_inputs: (config.clone(), all_detectors),
    };
    instance.load_cache();
    instance
}
```

Add a field to store the inputs for use in `save_cache`:

```rust
pub struct IncrementalCache {
    // ... existing fields ...
    /// Stored for fingerprint computation in save_cache
    config_fingerprint_inputs: (crate::config::ProjectConfig, bool),
}
```

- [ ] **Step 4: Add fingerprint check to `load_cache()`**

After the existing binary version check (line ~250), add:

```rust
// Fingerprint check — catches config changes, dev rebuilds, mode changes.
// Only compute binary hash if version string matched (lazy — saves ~3ms for release users).
let binary_hash = match binary_file_hash() {
    Some(h) => h,
    None => {
        info!("Cannot hash binary, forcing cache invalidation");
        self.invalidate_all();
        return Ok(());
    }
};
let (ref config, all_detectors) = self.config_fingerprint_inputs;
let current_fp = compute_fingerprint(binary_hash, config, all_detectors);
if data.fingerprint != Some(current_fp) {
    info!("Cache fingerprint mismatch, rebuilding");
    self.invalidate_all();
    return Ok(());
}
```

- [ ] **Step 5: Store fingerprint in `save_cache()`**

In `save_cache()`, before serialization, compute and store the fingerprint:

```rust
// Compute fingerprint before saving
let (ref config, all_detectors) = self.config_fingerprint_inputs;
if let Some(binary_hash) = binary_file_hash() {
    self.cache.fingerprint = Some(compute_fingerprint(binary_hash, config, all_detectors));
}
```

- [ ] **Step 6: Add `touch_last_used()` method**

```rust
/// Write a last_used marker for stale cache pruning.
pub fn touch_last_used(&self) {
    let marker = self.cache_dir.join(".last_used");
    let _ = fs::write(&marker, chrono::Utc::now().to_rfc3339().as_bytes());
}
```

- [ ] **Step 7: Add `prune_stale_caches()` free function**

```rust
/// Delete cache directories not used in the given duration.
/// Called once at startup. Errors silently ignored.
pub fn prune_stale_caches(max_age: std::time::Duration) {
    let cache_base = match dirs::cache_dir() {
        Some(d) => d.join("repotoire"),
        None => return,
    };

    let Ok(entries) = fs::read_dir(&cache_base) else { return };
    let cutoff = std::time::SystemTime::now() - max_age;

    for entry in entries.flatten() {
        let marker = entry.path().join(".last_used");
        let last_used = fs::metadata(&marker)
            .and_then(|m| m.modified())
            .unwrap_or(std::time::UNIX_EPOCH);

        if last_used < cutoff {
            let _ = fs::remove_dir_all(entry.path());
            debug!("Pruned stale cache: {}", entry.path().display());
        }
    }
}
```

- [ ] **Step 8: Fix all callers of `IncrementalCache::new()`**

Search for all calls to `IncrementalCache::new(` and update to pass config + all_detectors. Known call sites:

1. `src/engine/stages/postprocess.rs` line ~49 (dummy cache): pass `&ProjectConfig::default(), false`
2. Main engine pipeline (wherever the real cache is constructed): pass real config and flag
3. **All test calls inside `incremental_cache.rs`** — there are 10+ tests that call `IncrementalCache::new(...)`. Update each to pass `&ProjectConfig::default(), false`.

Run: `cargo check` to find any remaining broken call sites.

- [ ] **Step 9: Add tests**

```rust
#[cfg(test)]
mod fingerprint_tests {
    use super::*;

    #[test]
    fn test_compute_fingerprint_deterministic() {
        let config = crate::config::ProjectConfig::default();
        let fp1 = compute_fingerprint(12345, &config, false);
        let fp2 = compute_fingerprint(12345, &config, false);
        assert_eq!(fp1, fp2, "Same inputs should produce same fingerprint");
    }

    #[test]
    fn test_compute_fingerprint_changes_on_binary_hash() {
        let config = crate::config::ProjectConfig::default();
        let fp1 = compute_fingerprint(12345, &config, false);
        let fp2 = compute_fingerprint(99999, &config, false);
        assert_ne!(fp1, fp2, "Different binary hash should change fingerprint");
    }

    #[test]
    fn test_compute_fingerprint_changes_on_mode() {
        let config = crate::config::ProjectConfig::default();
        let fp1 = compute_fingerprint(12345, &config, false);
        let fp2 = compute_fingerprint(12345, &config, true);
        assert_ne!(fp1, fp2, "Different all_detectors should change fingerprint");
    }

    #[test]
    fn test_sort_json_keys_deterministic() {
        let json1 = serde_json::json!({"b": 2, "a": 1, "c": {"z": 3, "y": 4}});
        let json2 = serde_json::json!({"c": {"y": 4, "z": 3}, "a": 1, "b": 2});
        let s1 = serde_json::to_string(&sort_json_keys(json1)).unwrap();
        let s2 = serde_json::to_string(&sort_json_keys(json2)).unwrap();
        assert_eq!(s1, s2, "Same data with different key order should produce same output");
    }
}
```

- [ ] **Step 10: Verify**

Run: `cargo test fingerprint_tests -- --nocapture` and `cargo check`

- [ ] **Step 11: Commit**

```bash
git add src/detectors/incremental_cache.rs
git commit -m "feat(cache): add fingerprint-based auto-invalidation

Cache fingerprint = XXH3(binary_hash + config_json + analysis_mode +
CACHE_VERSION). Mismatch on load = automatic cold run. Binary hash
computed lazily (only when version string matches). Old caches without
fingerprint auto-invalidate on first load. Stale caches pruned via
.last_used marker file."
```

---

### Task 3: Engine session fingerprint

**Files:** `src/engine/state.rs`, `src/engine/mod.rs`

- [ ] **Step 1: Add fingerprint to SessionMeta**

In `src/engine/state.rs`, add to `SessionMeta` struct:

```rust
    /// Cache fingerprint — auto-invalidates when config, binary, or mode changes.
    /// Old sessions without this field deserialize as None and trigger cold run.
    #[serde(default)]
    pub fingerprint: Option<u64>,
```

- [ ] **Step 2: Add fingerprint check to `AnalysisEngine::load()`**

In `src/engine/mod.rs`, in the `load()` function, after the existing binary version check (line ~866), add:

```rust
// Fingerprint check — catches config changes, dev rebuilds, mode changes
let binary_hash = match crate::detectors::incremental_cache::binary_file_hash() {
    Some(h) => h,
    None => {
        anyhow::bail!("Cannot hash binary for cache validation");
    }
};
let current_fp = crate::detectors::incremental_cache::compute_fingerprint(
    binary_hash, &project_config, all_detectors,
);
if meta.fingerprint != Some(current_fp) {
    anyhow::bail!("Session fingerprint mismatch — config or binary changed");
}
```

Note: `load()` currently takes `(session_path, repo_path)`. It needs `all_detectors` too. Add it as a parameter:

```rust
pub fn load(session_path: &Path, repo_path: &Path, all_detectors: bool) -> anyhow::Result<Self> {
```

Update all callers of `AnalysisEngine::load()` (in `src/cli/analyze/mod.rs` and potentially `src/cli/watch/` and `src/cli/worker/`).

- [ ] **Step 3: Store `all_detectors` on the engine struct and use in `save()`**

Add a field to `AnalysisEngine`:

```rust
pub struct AnalysisEngine {
    // ... existing fields ...
    all_detectors: bool,
}
```

Set it in both `new()` and `load()`. Then in `save()`, when constructing `SessionMeta`, add:

```rust
fingerprint: {
    let binary_hash = crate::detectors::incremental_cache::binary_file_hash().unwrap_or(0);
    Some(crate::detectors::incremental_cache::compute_fingerprint(
        binary_hash, &self.project_config, self.all_detectors,
    ))
},
```

This avoids threading `all_detectors` through every `save()` call site (watch, worker, analyze all call `save()`).

- [ ] **Step 4: Make `binary_file_hash` and `compute_fingerprint` public**

In `src/detectors/incremental_cache.rs`, change both functions from `fn` to `pub fn` so the engine module can call them.

- [ ] **Step 5: Verify**

Run: `cargo check` — fix any broken call sites for `load()` and `save()`.

- [ ] **Step 6: Commit**

```bash
git add src/engine/state.rs src/engine/mod.rs src/detectors/incremental_cache.rs
git commit -m "feat(engine): add fingerprint to session cache

SessionMeta gets the same fingerprint as IncrementalCache. On load,
mismatch triggers cold run. Covers the engine fast-path (nothing_changed)
which bypasses the findings cache entirely."
```

---

### Task 4: Move labels and min-confidence after cache write

**Files:** `src/cli/analyze/postprocess.rs`

- [ ] **Step 1: Reorder the pipeline**

In `postprocess_findings()`, move `apply_user_labels` and `filter_by_min_confidence` to after the cache update step.

Find the current ordering:

```rust
    // Step 0.65: Apply user FP/TP labels
    apply_user_labels(findings, show_all);

    // Step 0.7: Confidence threshold filter
    filter_by_min_confidence(findings, min_confidence, show_all);

    // Step 1: Update incremental cache
    update_incremental_cache(...);
```

Reorder to:

```rust
    // Step 1: Update incremental cache — stores enriched findings BEFORE
    // any filtering. Labels and filters are applied fresh on every run.
    update_incremental_cache(...);

    // Step 1.5: Apply user FP/TP labels from feedback command.
    apply_user_labels(findings, show_all);

    // Step 1.6: Confidence threshold filter (--min-confidence).
    // Runs after labels so TP-pinned findings (confidence 0.95) survive.
    filter_by_min_confidence(findings, min_confidence, show_all);
```

**Note:** There is a second `filter_by_min_confidence` call in `src/cli/analyze/mod.rs:~417` inside `prepare_report()`. This is a consumer-side re-application for report formatting and does NOT need `apply_user_labels` — labels are already applied in the postprocess pipeline. Leave the `prepare_report` call as-is.

Also add `touch_last_used` after cache update by calling the instance method:

```rust
    incremental_cache.touch_last_used();
```

- [ ] **Step 2: Update pipeline comment header**

Update the comment at the top of the file that documents the pipeline order to reflect the new Step 1.5/1.6 positions.

- [ ] **Step 3: Verify existing tests still pass**

Run: `cargo test postprocess -- --nocapture` and `cargo test label_tests -- --nocapture`

- [ ] **Step 4: Commit**

```bash
git add src/cli/analyze/postprocess.rs
git commit -m "fix(cache): move labels and min-confidence after cache write

Cache now stores pre-filter findings. Labels and min-confidence are
applied fresh every run. Re-labeling FP→TP works immediately without
clean. TP-pinned findings survive min-confidence filter."
```

---

### Task 5: Remove `clean`, add `--force-reanalyze`, wire startup pruning

**Files:** `src/cli/mod.rs`, `src/cli/clean.rs`, `src/cli/analyze/mod.rs`

- [ ] **Step 1: Add `--force-reanalyze` flag**

In `src/cli/mod.rs`, add to the `Analyze` variant of `Commands`:

```rust
        /// Force a fresh analysis, ignoring cached results
        #[arg(long, hide = true)]
        force_reanalyze: bool,
```

- [ ] **Step 2: Remove `Clean` command**

In `src/cli/mod.rs`:
- Remove the `Clean { dry_run: bool }` variant from `Commands` enum
- Remove the `Some(Commands::Clean { dry_run }) => clean::run(...)` match arm
- Remove the `Some(Commands::Clean { .. }) => ("clean".into(), None)` telemetry arm
- Remove `mod clean;` declaration
- Remove `"clean"` from the `known_commands` array (~line 1069) — otherwise the CLI won't show "unknown command" for `repotoire clean`
- Remove any `clean` references in `after_help` strings or doc comments

- [ ] **Step 3: Delete `src/cli/clean.rs`**

```bash
rm src/cli/clean.rs
```

- [ ] **Step 4: Wire `--force-reanalyze` in analyze command**

In `src/cli/analyze/mod.rs` (or wherever `AnalysisEngine::load()` is called), check the flag:

```rust
let engine = if force_reanalyze {
    AnalysisEngine::new(repo_path)
} else {
    match AnalysisEngine::load(session_path, repo_path, all_detectors) {
        Ok(e) => e,
        Err(_) => AnalysisEngine::new(repo_path),
    }
};
```

- [ ] **Step 5: Add startup pruning + legacy cleanup**

At the start of the analyze command, before loading the engine:

```rust
// Prune stale caches (7 days) and legacy directories
crate::detectors::incremental_cache::prune_stale_caches(
    std::time::Duration::from_secs(7 * 24 * 3600)
);
let legacy = repo_path.join(".repotoire");
if legacy.is_dir() {
    let _ = std::fs::remove_dir_all(&legacy);
    tracing::info!("Removed legacy cache directory: {}", legacy.display());
}
```

- [ ] **Step 6: Verify**

```bash
cargo test
cargo clippy --all-features -- -D warnings
cargo fmt --all -- --check
```

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(cli): remove clean command, add --force-reanalyze

Cache is now self-managing. Stale caches pruned after 7 days of
inactivity. Legacy .repotoire dirs cleaned on startup. --force-reanalyze
is a hidden escape hatch for debugging."
```

---

### Task 6: End-to-end verification

- [ ] **Step 1: Build and install**

```bash
cargo install --path .
```

- [ ] **Step 2: Verify config change auto-invalidates**

```bash
repotoire analyze ~/personal/web/humanitarian-platform
# Note: should be incremental/cached run
# Now change something in config:
echo '[scoring]\nstructure_weight = 50' >> ~/personal/web/humanitarian-platform/repotoire.toml
repotoire analyze ~/personal/web/humanitarian-platform
# Should show cold run (fingerprint mismatch)
# Clean up:
git -C ~/personal/web/humanitarian-platform checkout -- repotoire.toml 2>/dev/null || rm ~/personal/web/humanitarian-platform/repotoire.toml
```

- [ ] **Step 3: Verify feedback re-labeling without clean**

```bash
repotoire analyze ~/personal/web/humanitarian-platform
repotoire feedback 1 --fp --reason "test" -C ~/personal/web/humanitarian-platform
repotoire analyze ~/personal/web/humanitarian-platform
# Finding #1 should be gone
repotoire feedback 1 --tp --reason "undo" -C ~/personal/web/humanitarian-platform
repotoire analyze ~/personal/web/humanitarian-platform
# Finding should be back (no clean needed)
```

- [ ] **Step 4: Verify clean command is gone**

```bash
repotoire clean . 2>&1 | grep -i "unrecognized\|unknown\|error"
# Should show an error about unknown command
```

- [ ] **Step 5: Verify --force-reanalyze**

```bash
repotoire analyze ~/personal/web/humanitarian-platform --force-reanalyze
# Should be a cold run regardless of cache state
```
