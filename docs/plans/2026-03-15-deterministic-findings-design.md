# Deterministic Findings Output

**Date:** 2026-03-15
**Status:** Draft

## Problem

Running `repotoire analyze` on the same codebase produces different finding counts between runs. Four categories of nondeterminism cause findings to appear/disappear unpredictably.

## Root Causes

### 1. MAX_FINDINGS_LIMIT Race Condition (CRITICAL)

**Location:** `engine.rs:1095` (6 instances across independent, dependent, and graph-independent detector loops)

The parallel detector execution checks `finding_count_parallel.load(Ordering::Relaxed)` before running each detector and updates with `fetch_add(Ordering::Relaxed)` after. With rayon work-stealing, multiple threads can pass the limit check simultaneously before any updates propagate, causing:
- Run A: 3 detectors slip past the 10k limit
- Run B: 5 detectors slip past, producing more findings

### 2. Voting Engine HashMap Iteration (CRITICAL)

**Location:** `voting_engine.rs:550-576` (MajorityVote, WeightedVote) and `voting_engine.rs:484-516` (Bayesian)

- **MajorityVote/WeightedVote:** `HashMap::into_iter().max_by_key()` — on severity ties, whichever HashMap entry appears first wins. Severity changes → finding content changes.
- **Bayesian:** `HashMap::values()` feeds a sequential Bayesian update. The update is **not commutative** — processing evidence A→B→C gives a different posterior than C→A→B. Different posteriors → different confidence → different filtering decisions.

### 3. Detector-Internal HashMap Iteration (HIGH)

**Locations:**
- `string_concat_loop.rs:165` — `HashMap::drain()` produces findings in hash-random order
- `ai_boilerplate.rs:255` — `HashMap::into_iter()` for dominant pattern selection
- `inappropriate_intimacy.rs:276` — coupling data accumulated in HashMaps

When findings from these detectors are later deduplicated or truncated, the hash-random ordering determines which survive.

### 4. FP Classifier Boundary Jitter (MEDIUM)

**Location:** `postprocess.rs:420-429`

Parallel feature extraction (`par_iter()`) produces floating-point values that vary by ~1e-15 between runs due to accumulation order. When `tp_probability` lands within this epsilon of the threshold (0.35 for Security, 0.52 for CodeQuality, etc.), the `>=` comparison flips nondeterministically.

## Design

### Fix 1: Deterministic Finding Limit

**Approach:** Remove the racy early-exit check. Let all detectors run to completion, then sort and truncate.

In `engine.rs`, for all 3 parallel loops (independent, dependent, graph-independent):
1. Remove the `finding_count_parallel.load(Relaxed) >= MAX_FINDINGS_LIMIT` early-exit check
2. After collecting all detector results and sorting by detector name, apply the limit:
   ```rust
   all_findings.sort_by(canonical_finding_order);
   all_findings.truncate(MAX_FINDINGS_LIMIT);
   ```

The finding limit exists as a safety valve for pathological repos, not a performance optimization — detectors already skip files that don't match their patterns. Removing the racy check has negligible performance impact.

### Fix 2: Voting Engine Determinism

**MajorityVote** (`voting_engine.rs:550-561`):
- Replace `HashMap<Severity, usize>` with `BTreeMap<Severity, usize>`
- Add deterministic tie-breaking: on equal counts, prefer higher severity
  ```rust
  .max_by(|(sev_a, count_a), (sev_b, count_b)| {
      count_a.cmp(count_b).then_with(|| (*sev_a as u8).cmp(&(*sev_b as u8)))
  })
  ```

**WeightedVote** (`voting_engine.rs:563-576`):
- Replace `HashMap<Severity, f64>` with `BTreeMap<Severity, f64>`
- Add deterministic tie-breaking: on equal scores, prefer higher severity

**Bayesian** (`voting_engine.rs:484-516`):
- Replace `HashMap<String, Vec<f64>>` with `BTreeMap<String, Vec<f64>>`
- This makes family iteration alphabetically ordered, making the sequential Bayesian update deterministic

### Fix 3: Detector-Internal Ordering

For each affected detector:

- **string_concat_loop.rs:** Replace `HashMap::drain()` with collect-to-Vec + sort by (file, line) before producing findings
- **ai_boilerplate.rs:** Sort `dominant_patterns` after collection from HashMap
- **inappropriate_intimacy.rs:** Sort coupling pairs before producing findings
- **Audit all other detectors** for HashMap-to-findings patterns and fix any found

### Fix 4: FP Classifier Stability

In `postprocess.rs:427`, round before comparison:
```rust
let rounded = (prediction.tp_probability * 10000.0).round() / 10000.0;
rounded >= config.filter_threshold as f64
```

4 decimal places (0.0001 precision) is well beyond the floating-point jitter range (~1e-15) while preserving meaningful classifier discrimination.

### Fix 5: Canonical Finding Sort at Engine Level

Add a canonical sort immediately after all findings are collected in `engine.rs`, before any postprocessing:
```rust
fn canonical_finding_order(a: &Finding, b: &Finding) -> std::cmp::Ordering {
    (b.severity as u8).cmp(&(a.severity as u8))
        .then_with(|| {
            let a_file = a.affected_files.first().map(|f| f.to_string_lossy()).unwrap_or_default();
            let b_file = b.affected_files.first().map(|f| f.to_string_lossy()).unwrap_or_default();
            a_file.cmp(&b_file)
        })
        .then_with(|| a.line_start.cmp(&b.line_start))
        .then_with(|| a.detector.cmp(&b.detector))
        .then_with(|| a.title.cmp(&b.title))
}
```

This sort is already partially implemented in `output.rs:57-75` but only for paginated display. Move it to the engine level so all consumers (reporters, cache, postprocessing) see deterministic order.

### Fix 6: Regression Test

Add `tests/determinism.rs`:
- Run analysis on a small fixture repo 3 times
- Serialize findings to JSON
- Assert all 3 runs produce identical output (count, order, scores)
- Run as part of `cargo test`

## Files to Modify

| File | Change |
|------|--------|
| `src/detectors/engine.rs` | Remove racy limit check, add canonical sort, truncate after sort |
| `src/detectors/voting_engine.rs` | BTreeMap for all severity/confidence maps, deterministic tie-breaking |
| `src/detectors/string_concat_loop.rs` | Sort before producing findings |
| `src/detectors/ai_boilerplate.rs` | Sort dominant patterns |
| `src/detectors/inappropriate_intimacy.rs` | Sort coupling pairs |
| `src/cli/analyze/postprocess.rs` | Round tp_probability before threshold comparison |
| `tests/determinism.rs` | New regression test |

## Non-Goals

- Making the graph node index order deterministic (it's already rebuilt from sorted file/parse order)
- Making rayon thread scheduling deterministic (unnecessary — we fix the outputs instead)
- Changing `DashMap` to `BTreeMap` in GraphStore (existing iteration points already sort)
