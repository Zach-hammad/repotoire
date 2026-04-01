# Cache Auto-Invalidation: Kill `clean`, Self-Managing Cache

## Context

`repotoire clean` is a user-facing command that deletes the analysis cache. Users currently need it when:
- Config changes don't take effect (cache doesn't track config)
- Dev rebuilds don't take effect (cache only checks version string, not binary content)
- Feedback re-labels don't take effect (cache stores post-label findings)
- `--all-detectors` results leak into default runs (cache doesn't track analysis mode)

This is bad UX. Caches should be invisible infrastructure. Users shouldn't manage cache state.

## Goals

1. Cache auto-invalidates when any analysis input changes
2. Feedback labels always apply correctly without cache interaction
3. Stale cache dirs auto-pruned after 7 days of inactivity
4. Remove `repotoire clean` from the CLI
5. Add `--force-reanalyze` as a hidden escape hatch for corrupt state

## Non-Goals

- Changing the cache storage format (bincode stays)
- Changing the incremental file-hash detection (XXH3 stays)
- Changing how graph topology changes are detected
- Persistent cross-session label caching

---

## Part 1: Cache Fingerprint

### What Changes

Add a `fingerprint: Option<u64>` field to both `CacheData` (findings cache) and `SessionMeta` (engine session). On every cache load, recompute the fingerprint from current inputs and compare. Mismatch → discard, cold run. Old caches without a fingerprint (`None`) auto-invalidate on first load — backwards compatible.

### Fingerprint Computation

Hash all inputs through a single XXH3 pass (no XOR mixing — avoids collision weakness):

```rust
fn compute_fingerprint(binary_hash: u64, config: &ProjectConfig, all_detectors: bool) -> u64 {
    use xxhash_rust::xxh3::xxh3_64;

    let mut buf = Vec::with_capacity(256);
    buf.extend_from_slice(&binary_hash.to_le_bytes());
    // Serialize config with sorted keys to avoid HashMap ordering non-determinism.
    // serde_json's to_string uses insertion order for HashMap, which is randomized
    // across process restarts. Sorting keys prevents spurious fingerprint mismatches.
    match serde_json::to_value(config).and_then(|v| serde_json::to_string(&sort_json_keys(v))) {
        Ok(json) => buf.extend_from_slice(json.as_bytes()),
        Err(_) => buf.extend_from_slice(b"__config_serialize_error__"),
    }
    buf.push(all_detectors as u8);
    buf.extend_from_slice(&CACHE_VERSION.to_le_bytes());
    xxh3_64(&buf)
}

/// Recursively sort JSON object keys for deterministic serialization.
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
```

**Prerequisite**: `ProjectConfig` and its nested types (`DetectorConfigOverride`, `ThresholdValue`, `ScoringConfig`, `PillarWeights`, `ExcludeConfig`, `CliDefaults`, `CoChangeConfigToml`, `OwnershipConfigToml`, `ProjectType`) must derive `Serialize`. Currently they only derive `Deserialize`. Add `#[derive(Serialize)]` to ~10 structs/enums in `src/config/project_config/mod.rs`. The `#[serde(skip)] detected_type` field correctly serializes as absent (transient runtime state should not affect the fingerprint).

### Binary Hash — Lazy Evaluation

Hashing the ~10MB binary takes ~3ms (XXH3). To avoid this cost on the common path:

1. If no cache exists → cold run (no hash needed)
2. If cached `binary_version` string differs from current → cold run (no hash needed)
3. If version string matches → compute binary hash, compare fingerprint

Release users never pay the binary hash cost (version changes every release). Only developers who rebuild without bumping the version trigger it.

```rust
fn binary_file_hash() -> Option<u64> {
    let exe = std::env::current_exe().ok()?;
    let bytes = std::fs::read(&exe).ok()?;
    Some(xxhash_rust::xxh3::xxh3_64(&bytes))
}
```

If `binary_file_hash()` returns `None` (deleted binary, permission error), treat as forced invalidation — don't default to 0:

```rust
let binary_hash = match binary_file_hash() {
    Some(h) => h,
    None => {
        info!("Cannot hash binary, forcing cache invalidation");
        self.invalidate_all();
        return Ok(());
    }
};
```

### Integration — Both Cache Layers

**Findings cache** (`IncrementalCache::load_cache()` in `incremental_cache.rs`):

After the existing version string check, add fingerprint check:

```rust
let current_fp = compute_fingerprint(binary_hash, config, all_detectors);
if data.fingerprint != Some(current_fp) {
    info!("Cache fingerprint mismatch, rebuilding");
    self.invalidate_all();
    return Ok(());
}
```

In `save_cache()`, store the fingerprint.

**Engine session** (`AnalysisEngine::load()` in `engine/mod.rs`):

Add `fingerprint: Option<u64>` to `SessionMeta`. On load, recompute and compare. On mismatch, bail (engine falls back to `AnalysisEngine::new()` cold path). In `save()`, store the fingerprint.

This covers both cache layers, including the engine's fast-path return for "nothing changed" (which currently bypasses the findings cache entirely).

### What This Replaces

- `binary_version` check (kept as fast-path optimization, fingerprint is authoritative)
- `CACHE_VERSION` constant (folded into fingerprint, constant still exists for fast-path)
- Manual `repotoire clean` for config/binary/mode changes

---

## Part 2: Feedback Labels — Layered, Not Cached

### What Changes

Move `apply_user_labels()` from Step 0.65 (before cache write) to Step 1.5 (after cache write). Move `filter_by_min_confidence()` from Step 0.7 to Step 1.6 (after labels, so TP-pinned findings survive).

### New Pipeline Order

```
Step 0.6:  Confidence enrichment
Step 1:    Cache update ← stores enriched findings (pre-filter, pre-label)
Step 1.5:  apply_user_labels ← fresh from JSONL every run
Step 1.6:  Min-confidence filter ← runs after labels so TP-pinned findings survive
Step 2+:   Everything else (unchanged)
```

### Why This Order

- **Cache stores pre-filter findings.** All filtering (labels, min-confidence, detector overrides) is applied fresh on every run. This is the cleanest model: cache = raw enriched findings, filters = applied on top.
- **Labels before min-confidence.** A TP-pinned finding gets confidence 0.95 at Step 1.5, then passes Step 1.6 easily. If min-confidence ran first, a finding at 0.3 confidence would be removed before the TP label could pin it.
- **Re-labeling works immediately.** Cache always has all findings. FP→TP re-label takes effect next run without any invalidation.
- **`--show-all` works correctly.** FP findings exist in cache, re-inserted with low confidence at Step 1.5.

---

## Part 3: Cache Pruning (7 Days)

### What Changes

On startup, before analysis, check all cache dirs in `~/.cache/repotoire/`. Delete any that haven't been used in 7+ days.

### Reliable Staleness Detection

Filesystem `atime` is unreliable (`noatime`/`relatime` mounts). Instead, write a `last_used` timestamp file in each cache dir during every analysis run. Check this file's `mtime` for pruning — works on all filesystems.

```rust
/// Touch the last_used marker in a cache directory.
fn touch_last_used(cache_dir: &Path) {
    let marker = cache_dir.join(".last_used");
    let _ = std::fs::write(&marker, chrono::Utc::now().to_rfc3339().as_bytes());
}

/// Prune cache directories not used in `max_age` duration.
/// Also cleans up legacy `.repotoire` directories if found.
fn prune_stale_caches(max_age: Duration) {
    let cache_base = dirs::cache_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("repotoire");

    let Ok(entries) = std::fs::read_dir(&cache_base) else { return };
    let cutoff = SystemTime::now() - max_age;

    for entry in entries.flatten() {
        let marker = entry.path().join(".last_used");
        let last_used = std::fs::metadata(&marker)
            .and_then(|m| m.modified())
            .unwrap_or(SystemTime::UNIX_EPOCH);

        if last_used < cutoff {
            let _ = std::fs::remove_dir_all(entry.path());
            tracing::debug!("Pruned stale cache: {}", entry.path().display());
        }
    }
}
```

Called once at the start of `repotoire analyze` with `Duration::from_secs(7 * 24 * 3600)`. Non-blocking — errors silently ignored.

### Legacy Directory Cleanup

The current `clean` command also removes legacy `.repotoire/` directories in repos. Since `clean` is being removed, the pruning logic should also check for and remove legacy dirs in the analyzed repo's root during startup:

```rust
let legacy = repo_path.join(".repotoire");
if legacy.is_dir() {
    let _ = std::fs::remove_dir_all(&legacy);
    tracing::info!("Removed legacy cache directory: {}", legacy.display());
}
```

---

## Part 4: Remove `clean`, Add `--force-reanalyze`

### Remove `clean`

- Delete `src/cli/clean.rs`
- Remove `Clean` variant from `Commands` enum in `src/cli/mod.rs`
- Remove references in help text, README, docs

### Add `--force-reanalyze`

Add a `--force-reanalyze` flag to the `analyze` command. When set:
- Skip BOTH cache layers: findings cache (`IncrementalCache`) AND engine session (`AnalysisEngine::load()`)
- Treat as cold run (`AnalysisEngine::new()`)
- Still write results to cache afterward (cache is refreshed)
- Does NOT delete existing cache files

```rust
/// Force a fresh analysis, ignoring cached results
#[arg(long, hide = true)]
force_reanalyze: bool,
```

Hidden from `--help`. The escape hatch for debugging or truly corrupt state.

---

## Part 5: Style Profile Preservation

Currently `clean` preserves `style-profile.json`. With `clean` gone:
- Style profile lives in the cache dir, pruned after 7 days of inactivity
- Calibration is fast and re-runs automatically every cold run
- If a user hasn't analyzed in 7+ days, fresh calibration is appropriate

No special handling needed.

---

## Verification

```bash
# 1. Config change auto-invalidates
repotoire analyze .                    # cold run, cache created
# Edit repotoire.toml (change a threshold)
repotoire analyze .                    # auto-detects config change, cold run

# 2. Dev rebuild auto-invalidates
cargo install --path .                 # rebuild same version
repotoire analyze .                    # auto-detects binary change, cold run

# 3. Feedback labels work without clean
repotoire feedback 1 --fp             # label as FP
repotoire analyze .                    # FP finding gone (no clean needed)
repotoire feedback 1 --tp             # re-label as TP
repotoire analyze .                    # finding back (no clean needed)

# 4. --all-detectors mode switch
repotoire analyze . --all-detectors    # deep scan, cache created
repotoire analyze .                    # default mode, auto-invalidates, no deep-scan findings

# 5. Stale cache pruning
# After 7 days without analyzing a repo, its cache dir is auto-deleted

# 6. Force reanalyze
repotoire analyze . --force-reanalyze  # cold run regardless of cache state

# 7. clean command removed
repotoire clean .                      # "unknown command" error

# 8. Old cache format (no fingerprint) auto-invalidates
# Existing users upgrading get one automatic cold run, then fingerprinted cache going forward
```

---

## Files Changed

| File | Changes |
|------|---------|
| `src/config/project_config/mod.rs` | Add `#[derive(Serialize)]` to `ProjectConfig` and ~10 nested types |
| `src/detectors/incremental_cache.rs` | Add `fingerprint: Option<u64>` to `CacheData`, `compute_fingerprint()`, `sort_json_keys()`, `binary_file_hash()`, fingerprint check in `load_cache()`, store in `save_cache()`, `touch_last_used()` |
| `src/engine/state.rs` | Add `fingerprint: Option<u64>` to `SessionMeta` |
| `src/engine/mod.rs` | Fingerprint check in `load()`, store in `save()`, bail on mismatch |
| `src/cli/analyze/postprocess.rs` | Move `apply_user_labels()` to Step 1.5, move `filter_by_min_confidence()` to Step 1.6 |
| `src/cli/analyze/mod.rs` | Add `prune_stale_caches()` + legacy dir cleanup on startup, pass config/all_detectors to cache, wire `--force-reanalyze` to skip both cache layers |
| `src/cli/mod.rs` | Remove `Clean` command, add `--force-reanalyze` flag to `Analyze` |
| `src/cli/clean.rs` | Delete file |
