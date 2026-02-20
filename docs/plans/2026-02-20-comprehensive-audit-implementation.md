# Repotoire Comprehensive Audit Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix all 17 issues from the audit: wire cache coordination, eliminate all unwrap/expect/panic from non-test code, complete partially-implemented detectors, and run self-analysis to verify detection gaps.

**Architecture:** Bottom-up fix pass — cache layer first (foundational), then unwrap cleanup (broadest impact), then detector completeness, then self-analysis. After each phase, `cargo check` and `cargo test` to verify no regressions.

**Tech Stack:** Rust, petgraph, redb, tree-sitter, rayon, dashmap, anyhow/thiserror

---

## Phase 1: Cache Architecture — Wire Existing Traits

The `CacheLayer` trait and `CacheCoordinator` already exist in `src/cache/traits.rs` but nothing implements them. Wire the actual caches.

### Task 1: Implement CacheLayer for FileCache

**Files:**
- Modify: `repotoire-cli/src/cache/mod.rs`
- Reference: `repotoire-cli/src/cache/traits.rs`

**Step 1: Write the failing test**

Add to the bottom of `src/cache/mod.rs` in the `#[cfg(test)]` block (create one if needed):

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::cache::traits::CacheLayer;
    use std::path::Path;

    #[test]
    fn test_file_cache_implements_cache_layer() {
        let mut cache = FileCache::new();
        assert_eq!(cache.name(), "file-content");
        assert!(!cache.is_populated());

        // Warm with a known file
        let manifest = Path::new(env!("CARGO_MANIFEST_DIR")).join("Cargo.toml");
        cache.get_content(&manifest);
        assert!(cache.is_populated());

        // Invalidate specific file
        cache.invalidate_files(&[manifest.as_path()]);
        assert!(!cache.is_populated());
    }

    #[test]
    fn test_file_cache_invalidate_all() {
        let mut cache = FileCache::new();
        let manifest = Path::new(env!("CARGO_MANIFEST_DIR")).join("Cargo.toml");
        cache.get_content(&manifest);
        assert!(cache.is_populated());

        cache.invalidate_all();
        assert!(!cache.is_populated());
    }
}
```

**Step 2: Run test to verify it fails**

Run: `cargo test -p repotoire --lib cache::tests -- --nocapture`
Expected: FAIL — `FileCache` doesn't implement `CacheLayer`

**Step 3: Implement CacheLayer for FileCache**

Add to `src/cache/mod.rs` after the `impl Default for FileCache` block:

```rust
impl crate::cache::traits::CacheLayer for FileCache {
    fn name(&self) -> &str {
        "file-content"
    }

    fn is_populated(&self) -> bool {
        !self.contents.is_empty()
    }

    fn invalidate_files(&mut self, changed_files: &[&Path]) {
        for path in changed_files {
            self.contents.remove(*path);
            self.lines.remove(*path);
        }
    }

    fn invalidate_all(&mut self) {
        self.clear();
    }
}
```

**Step 4: Run test to verify it passes**

Run: `cargo test -p repotoire --lib cache::tests -- --nocapture`
Expected: PASS

**Step 5: Commit**

```bash
git add repotoire-cli/src/cache/mod.rs
git commit -m "feat(cache): implement CacheLayer trait for FileCache"
```

---

### Task 2: Implement CacheLayer for IncrementalCache

**Files:**
- Modify: `repotoire-cli/src/detectors/incremental_cache.rs`

**Step 1: Write the failing test**

Add to the existing test module in `incremental_cache.rs`:

```rust
#[test]
fn test_incremental_cache_implements_cache_layer() {
    use crate::cache::traits::CacheLayer;
    let dir = tempfile::tempdir().unwrap();
    let mut cache = IncrementalCache::new(dir.path());
    assert_eq!(cache.name(), "incremental-findings");
    assert!(!cache.is_populated());
}
```

**Step 2: Run test to verify it fails**

Run: `cargo test -p repotoire --lib detectors::incremental_cache::tests -- --nocapture`
Expected: FAIL

**Step 3: Implement CacheLayer for IncrementalCache**

Add `impl CacheLayer for IncrementalCache` with appropriate logic — invalidate_files should remove entries for changed files from the hash map, invalidate_all should clear the entire cache.

**Step 4: Run test to verify it passes**

Run: `cargo test -p repotoire --lib detectors::incremental_cache::tests -- --nocapture`
Expected: PASS

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/incremental_cache.rs
git commit -m "feat(cache): implement CacheLayer trait for IncrementalCache"
```

---

### Task 3: Wire CacheCoordinator into Analysis Pipeline

**Files:**
- Modify: `repotoire-cli/src/pipeline/` (find the main analysis entry point)
- Modify: `repotoire-cli/src/cli/` (find the analyze command)

**Step 1: Find where caches are created and used**

Search for `FileCache::new`, `IncrementalCache::new`, `global_cache()` usage to find wiring points.

**Step 2: Create CacheCoordinator at pipeline startup**

At the analysis entry point, create a `CacheCoordinator`, register both cache layers, and pass it through the pipeline so `invalidate_files` can be called when files change (watch mode).

**Step 3: Run full test suite**

Run: `cargo test -p repotoire`
Expected: All tests PASS

**Step 4: Commit**

```bash
git add repotoire-cli/src/pipeline/ repotoire-cli/src/cli/
git commit -m "feat(cache): wire CacheCoordinator into analysis pipeline"
```

---

### Task 4: Remove stale TODO comment

**Files:**
- Modify: `repotoire-cli/src/cache/mod.rs:5-12`

**Step 1: Remove the TODO block**

Delete lines 5-12 (the TODO comment about three independent cache layers) since the trait now exists and is wired.

**Step 2: Run cargo check**

Run: `cargo check -p repotoire`
Expected: No errors

**Step 3: Commit**

```bash
git add repotoire-cli/src/cache/mod.rs
git commit -m "chore: remove stale TODO — cache layers now unified via CacheCoordinator"
```

---

## Phase 2: Unwrap/Panic Aggressive Cleanup

**Scope:** 259 `.unwrap()`, 237 `.expect()`, 7 `panic!()` across non-test code.

**Strategy:** Work in priority order by risk:
1. `graph/store.rs` — 56 expect() calls (highest concentration, core infrastructure)
2. Parser files — user-provided code could cause unexpected states
3. Detector files — each detector processes external code
4. Cache/config/CLI — remaining utilities

**Pattern to apply everywhere:**

| Before | After |
|--------|-------|
| `.unwrap()` | `?` (if function returns Result) |
| `.unwrap()` | `.ok()?` (if function returns Option) |
| `.unwrap()` | `.unwrap_or_default()` (if default is safe) |
| `.expect("msg")` | `.context("msg")?` (using anyhow) |
| `lock.write().expect("poisoned")` | `lock.write().map_err(\|e\| anyhow::anyhow!("lock poisoned: {e}"))?` |
| `panic!("msg")` | `return Err(anyhow::anyhow!("msg"))` |

### Task 5: Fix graph/store.rs — Convert lock expects to Results

**Files:**
- Modify: `repotoire-cli/src/graph/store.rs`

**Step 1: Change method signatures to return Result**

Every public method that currently does `.expect("graph lock poisoned")` needs to return `Result<T>` instead of `T`. This is the biggest change — 50 expect calls for lock acquisition.

The pattern for every method:

```rust
// BEFORE:
pub fn get_node(&self, qn: &str) -> Option<CodeNode> {
    let index = self.node_index.read().expect("graph lock poisoned");
    let graph = self.graph.read().expect("graph lock poisoned");
    ...
}

// AFTER:
pub fn get_node(&self, qn: &str) -> Result<Option<CodeNode>> {
    let index = self.node_index.read()
        .map_err(|e| anyhow::anyhow!("graph index lock poisoned: {e}"))?;
    let graph = self.graph.read()
        .map_err(|e| anyhow::anyhow!("graph lock poisoned: {e}"))?;
    ...
    Ok(result)
}
```

**Step 2: Update all call sites**

Search for all uses of `GraphStore` methods and add `?` or handle the new Result. This will cascade through detectors.

**Step 3: Run cargo check**

Run: `cargo check -p repotoire`
Fix all compilation errors from the signature changes.

**Step 4: Run tests**

Run: `cargo test -p repotoire`
Expected: All tests PASS (tests can still use .unwrap() — only non-test code is cleaned)

**Step 5: Commit**

```bash
git add repotoire-cli/src/graph/store.rs
git commit -m "fix(graph): convert all lock expects to Result propagation"
```

---

### Task 6: Fix graph/store.rs call sites in detectors

**Files:**
- Modify: All detector files that call GraphStore methods

**Step 1: Find all call sites**

Search for patterns like `store.get_node(`, `store.get_functions(`, `store.fan_in(`, etc. across all detector files.

**Step 2: Add ? propagation**

Each detector's `detect()` method should already return a type compatible with Result. Add `?` to GraphStore calls.

**Step 3: Run cargo check and cargo test**

Run: `cargo check -p repotoire && cargo test -p repotoire`
Expected: PASS

**Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/
git commit -m "fix(detectors): propagate Result from GraphStore API changes"
```

---

### Task 7: Fix unwraps in parser files

**Files:**
- Modify: All files in `repotoire-cli/src/parsers/`

**Step 1: Find all unwrap/expect calls**

```bash
grep -rn 'unwrap()\|\.expect(' src/parsers/ --include='*.rs'
```

**Step 2: Convert each to proper error handling**

For tree-sitter operations, use `ok()?` or `unwrap_or_default()` since parsing can legitimately fail on malformed code.

**Step 3: Run cargo check and cargo test**

Run: `cargo check -p repotoire && cargo test -p repotoire`
Expected: PASS

**Step 4: Commit**

```bash
git add repotoire-cli/src/parsers/
git commit -m "fix(parsers): replace all unwrap/expect with proper error handling"
```

---

### Task 8: Fix unwraps in remaining detector files

**Files:**
- Modify: All files in `repotoire-cli/src/detectors/` with unwrap/expect

**Step 1: Find all remaining unwrap/expect calls in detectors**

```bash
grep -rn 'unwrap()\|\.expect(' src/detectors/ --include='*.rs' | grep -v '#\[cfg(test)\]' | grep -v 'mod tests'
```

**Step 2: Convert each to proper error handling**

Apply the pattern table from above. For regex compilation with hardcoded patterns, convert `expect("valid regex")` to compile-time validation using `once_cell::sync::Lazy` or document why the expect is safe with a `// SAFETY:` comment.

**Step 3: Run cargo check and cargo test**

Run: `cargo check -p repotoire && cargo test -p repotoire`
Expected: PASS

**Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/
git commit -m "fix(detectors): eliminate all unwrap/expect from non-test code"
```

---

### Task 9: Fix unwraps in cache, config, CLI, and remaining files

**Files:**
- Modify: `src/cache/paths.rs` (3 unwrap_or_else with fallbacks — review each)
- Modify: `src/config/`, `src/cli/`, `src/scoring/`, `src/reporters/`, `src/ai/`, `src/mcp/`

**Step 1: Find all remaining unwrap/expect calls**

```bash
grep -rn 'unwrap()\|\.expect(' src/ --include='*.rs' | grep -v test | grep -v '#\[cfg(test)\]'
```

**Step 2: Convert each**

**Step 3: Run full cargo check and cargo test**

Run: `cargo check -p repotoire && cargo test -p repotoire`
Expected: PASS

**Step 4: Verify zero unwraps in non-test code**

```bash
# Should return 0
grep -rn 'unwrap()' src/ --include='*.rs' | grep -v '#\[test\]' | grep -v 'mod tests' | grep -v '#\[cfg(test)\]' | wc -l
```

**Step 5: Commit**

```bash
git add repotoire-cli/src/
git commit -m "fix: zero unwrap/expect/panic in all non-test code"
```

---

## Phase 3: Detector Completeness

### Task 10: Wire inline suppression to ALL detectors

**Current state:** `is_line_suppressed()` exists and is used in 7 of 104 detector files.

**Files:**
- Modify: All 97 detector files that DON'T use `is_line_suppressed()`

**Step 1: Identify detectors missing suppression**

```bash
# List detector files NOT using suppression
comm -23 \
  <(ls src/detectors/*.rs | sort) \
  <(grep -rl 'is_line_suppressed' src/detectors/ | sort)
```

**Step 2: Add suppression check to each detector's line-scanning loop**

Every detector that iterates over file lines should check suppression. The pattern is:

```rust
let prev_line = if line_idx > 0 {
    lines.get(line_idx - 1).map(|s| s.as_str())
} else {
    None
};
if crate::detectors::is_line_suppressed(line, prev_line) {
    continue;
}
```

**Step 3: Write a test verifying suppression works**

Add to `src/detectors/mod.rs` tests:

```rust
#[test]
fn test_is_line_suppressed_inline() {
    assert!(is_line_suppressed("x = 1  // repotoire:ignore", None));
    assert!(is_line_suppressed("x = 1  # repotoire: ignore", None));
    assert!(!is_line_suppressed("x = 1", None));
}

#[test]
fn test_is_line_suppressed_prev_line() {
    assert!(is_line_suppressed("x = 1", Some("// repotoire:ignore")));
    assert!(!is_line_suppressed("x = 1", Some("// normal comment")));
}
```

**Step 4: Run cargo check and cargo test**

Run: `cargo check -p repotoire && cargo test -p repotoire`
Expected: PASS

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/
git commit -m "feat(detectors): wire inline suppression to all 104 detectors"
```

---

### Task 11: Add targeted suppression (detector-specific ignore)

**Files:**
- Modify: `repotoire-cli/src/detectors/mod.rs` — enhance `is_line_suppressed()`

**Step 1: Write the failing test**

```rust
#[test]
fn test_targeted_suppression() {
    // Should suppress only the named detector
    assert!(is_line_suppressed_for(
        "x = 1  // repotoire:ignore[sql-injection]",
        None,
        "sql-injection"
    ));
    // Should NOT suppress a different detector
    assert!(!is_line_suppressed_for(
        "x = 1  // repotoire:ignore[sql-injection]",
        None,
        "xss"
    ));
    // Bare ignore suppresses everything
    assert!(is_line_suppressed_for(
        "x = 1  // repotoire:ignore",
        None,
        "xss"
    ));
}
```

**Step 2: Implement `is_line_suppressed_for()`**

```rust
pub fn is_line_suppressed_for(line: &str, prev_line: Option<&str>, detector_name: &str) -> bool {
    let check = |text: &str| -> bool {
        if let Some(idx) = text.find("repotoire:ignore").or_else(|| text.find("repotoire: ignore")) {
            let after = &text[idx..];
            // Check for targeted suppression: repotoire:ignore[detector-name]
            if let Some(bracket_start) = after.find('[') {
                if let Some(bracket_end) = after.find(']') {
                    let target = after[bracket_start + 1..bracket_end].trim();
                    return target == detector_name;
                }
            }
            // Bare ignore suppresses everything
            return true;
        }
        false
    };

    check(line) || prev_line.map_or(false, check)
}
```

**Step 3: Run tests**

Run: `cargo test -p repotoire --lib detectors -- --nocapture`
Expected: PASS

**Step 4: Update detectors to use targeted version**

Each detector should pass its own name to `is_line_suppressed_for()`.

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/
git commit -m "feat(detectors): add targeted suppression via repotoire:ignore[detector-name]"
```

---

### Task 12: Fix TODO in long_parameter.rs production format string

**Files:**
- Modify: `repotoire-cli/src/detectors/long_parameter.rs:192`

**Step 1: Read the file context around line 192**

Understand what type information is available from the graph node.

**Step 2: Replace TODO with actual type from graph**

If the graph node has type information, use it. Otherwise, use a language-appropriate default (e.g., `Any` for Python, `any` for TypeScript).

**Step 3: Run cargo check**

Run: `cargo check -p repotoire`
Expected: PASS

**Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/long_parameter.rs
git commit -m "fix(detectors): replace TODO with proper type annotation in long_parameter output"
```

---

### Task 13: Harmonize hardcoded thresholds with adaptive system

**Files:**
- Modify: Detectors with hardcoded `const` thresholds (god_class, feature_envy, long_methods, deep_nesting, etc.)
- Reference: `repotoire-cli/src/calibrate/` for the adaptive threshold API

**Step 1: Find all hardcoded threshold constants**

```bash
grep -rn 'const MIN_\|const MAX_\|const THRESHOLD' src/detectors/ --include='*.rs'
```

**Step 2: Replace with calibration profile lookups**

Each detector should check the calibration profile first, falling back to hardcoded defaults:

```rust
let threshold = config
    .and_then(|c| c.threshold_for(self.name(), "min_methods"))
    .unwrap_or(MIN_METHOD_COUNT);
```

**Step 3: Run cargo check and cargo test**

Run: `cargo check -p repotoire && cargo test -p repotoire`
Expected: PASS

**Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/
git commit -m "feat(detectors): harmonize hardcoded thresholds with adaptive calibration system"
```

---

### Task 14: Add orchestrator pattern detection to reduce false positives

**Files:**
- Create: `repotoire-cli/src/detectors/class_context.rs` (if not already sufficient)
- Modify: `repotoire-cli/src/detectors/god_class.rs`
- Modify: `repotoire-cli/src/detectors/feature_envy.rs`

**Step 1: Write failing test**

```rust
#[test]
fn test_orchestrator_not_flagged_as_god_class() {
    // A router/controller with many delegating calls should not be a god class
    let code = r#"
class Router:
    def handle_users(self, req): return self.user_service.handle(req)
    def handle_posts(self, req): return self.post_service.handle(req)
    def handle_comments(self, req): return self.comment_service.handle(req)
    def handle_auth(self, req): return self.auth_service.handle(req)
    def handle_search(self, req): return self.search_service.handle(req)
"#;
    // ... test that god_class detector does NOT flag this
}
```

**Step 2: Implement orchestrator detection**

In the class context module, add a function that checks:
- High outgoing calls to different services
- Low internal state (few fields used in methods)
- Methods are short delegators

When detected, the god_class and feature_envy detectors should lower severity or skip.

**Step 3: Run tests**

Run: `cargo test -p repotoire`
Expected: PASS

**Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/
git commit -m "feat(detectors): auto-detect orchestrator patterns, reduce god_class/feature_envy noise"
```

---

## Phase 4: Self-Analysis

### Task 15: Build repotoire and run self-analysis

**Files:**
- No code changes — analysis only

**Step 1: Build release binary**

```bash
cd repotoire-cli && cargo build --release
```

**Step 2: Run repotoire on itself**

```bash
./target/release/repotoire analyze ./src/ -o self-analysis.json --format json
```

**Step 3: Review findings**

Compare repotoire's findings against our 17-issue audit. Document:
- Which issues repotoire DID detect
- Which issues repotoire MISSED
- Why each gap exists (missing detector, below threshold, wrong scope)

**Step 4: Save analysis report**

```bash
# Save to docs/
cp self-analysis.json docs/plans/2026-02-20-self-analysis-results.json
```

**Step 5: Commit**

```bash
git add docs/plans/2026-02-20-self-analysis-results.json
git commit -m "docs: add self-analysis results for audit gap investigation"
```

---

### Task 16: Create missing detectors identified by self-analysis

Based on self-analysis results, create new detectors for gaps. Expected:

**Panic Density Detector** — flags files/functions with high unwrap/expect/panic density:

**Files:**
- Create: `repotoire-cli/src/detectors/panic_density.rs`
- Modify: `repotoire-cli/src/detectors/mod.rs` (register)

**Step 1: Write failing test**

```rust
#[test]
fn test_panic_density_flags_high_unwrap_files() {
    let code = r#"
fn process() {
    let x = data.unwrap();
    let y = more.unwrap();
    let z = other.expect("bad");
    panic!("fatal");
}
"#;
    // Should produce a finding with severity Medium
}
```

**Step 2: Implement detector**

Count occurrences of `unwrap()`, `.expect(`, `panic!(` per function. Flag when density exceeds threshold (e.g., >3 per function or >10 per file).

**Step 3: Register in mod.rs**

Add `mod panic_density;` and register in `default_detectors()`.

**Step 4: Run tests**

Run: `cargo test -p repotoire`
Expected: PASS

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/panic_density.rs repotoire-cli/src/detectors/mod.rs
git commit -m "feat(detectors): add panic_density detector for unwrap/expect/panic usage"
```

---

### Task 17: Re-run self-analysis to verify gap closure

**Step 1: Rebuild**

```bash
cargo build --release -p repotoire
```

**Step 2: Re-run analysis**

```bash
./target/release/repotoire analyze ./src/ -o self-analysis-v2.json --format json
```

**Step 3: Compare**

Verify new detectors catch the previously-missed issues.

**Step 4: Final commit**

```bash
git add docs/
git commit -m "docs: self-analysis v2 — verify detection gap closure"
```

---

## Verification Checklist

After all tasks complete, verify:

- [ ] `cargo check -p repotoire` — zero warnings
- [ ] `cargo test -p repotoire` — all tests pass
- [ ] `grep -rn 'unwrap()' src/ | grep -v test | wc -l` — returns 0
- [ ] `grep -rn '\.expect(' src/ | grep -v test | wc -l` — returns 0 (or only documented SAFETY comments)
- [ ] `grep -rn 'panic!' src/ | grep -v test | wc -l` — returns 0
- [ ] CacheCoordinator is wired and invalidates all layers
- [ ] All 104+ detectors support inline suppression
- [ ] Self-analysis shows no critical/high findings missed
