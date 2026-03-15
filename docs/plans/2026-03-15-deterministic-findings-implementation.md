# Deterministic Findings Output - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate all sources of nondeterminism in `repotoire analyze` so identical inputs always produce identical findings (count, order, scores).

**Architecture:** Fix 4 categories of nondeterminism: (1) racy finding limit in engine.rs, (2) HashMap iteration in voting_engine.rs, (3) HashMap iteration in individual detectors, (4) floating-point boundary jitter in FP classifier. Add canonical sort at engine level. Add regression test.

**Tech Stack:** Rust, BTreeMap, rayon (unchanged)

---

### Task 1: Remove Racy MAX_FINDINGS_LIMIT Early-Exit

**Files:**
- Modify: `repotoire-cli/src/detectors/engine.rs:1094-1102` (independent loop)
- Modify: `repotoire-cli/src/detectors/engine.rs:1152-1161` (dependent loop)
- Modify: `repotoire-cli/src/detectors/engine.rs:1345-1351` (graph-independent loop)

**Step 1: Remove racy early-exit in independent detector loop**

In `engine.rs`, remove lines 1094-1102 (the `finding_count_parallel.load(Relaxed) >= MAX_FINDINGS_LIMIT` check and its skip block) from the parallel `.map()` closure. Also remove the `finding_count_parallel.fetch_add()` on line 1105 since we no longer track live counts.

The closure should go from skip_set check directly to `run_single_detector`:

```rust
                .map(|detector| {
                    // Skip if no matching files for this detector
                    if skip_set.contains(detector.name()) {
                        let done = completed.fetch_add(1, Ordering::SeqCst) + 1;
                        if let Some(ref callback) = self.progress_callback {
                            callback(detector.name(), done, total);
                        }
                        return DetectorResult::skipped(detector.name());
                    }

                    let result = self.run_single_detector(detector, &analysis_ctx);

                    // Update progress
                    let done = completed.fetch_add(1, Ordering::SeqCst) + 1;
                    if let Some(ref callback) = self.progress_callback {
                        callback(detector.name(), done, total);
                    }

                    result
                })
```

**Step 2: Remove racy early-exit in dependent detector loop**

In `engine.rs`, remove lines 1152-1161 (the `finding_count.load(Relaxed) >= MAX_FINDINGS_LIMIT` check in the sequential dependent loop). Also remove the `finding_count.fetch_add()` on line 1164.

**Step 3: Remove racy early-exit in graph-independent detector loop**

In `engine.rs`, remove lines 1345-1351 (the `finding_count_clone.load(Relaxed) >= MAX_FINDINGS_LIMIT` check). Also remove the `finding_count_clone.fetch_add()` on line 1355.

**Step 4: Remove the `finding_count` AtomicUsize declarations**

Remove `finding_count` and `finding_count_parallel` declarations that are now unused:
- Line 1080: `let finding_count_parallel = Arc::clone(&finding_count);`
- Around line 1060 (where `finding_count` is declared): the original `Arc::new(AtomicUsize::new(0))`
- Line 1285: `let finding_count = Arc::new(AtomicUsize::new(0));`
- Line 1340: `let finding_count_clone = Arc::clone(&finding_count);`
- Lines 1184-1190: The "Log early termination" block

**Step 5: Verify compilation**

Run: `cargo check -p repotoire-cli`
Expected: compiles with possible unused import warnings for `AtomicUsize`/`Ordering` (clean these up)

**Step 6: Commit**

```bash
git add repotoire-cli/src/detectors/engine.rs
git commit -m "fix: remove racy MAX_FINDINGS_LIMIT early-exit checks

The Ordering::Relaxed atomic checks allowed multiple detectors to
slip past the 10k limit simultaneously, causing nondeterministic
finding counts between runs. The limit is still enforced after
collection via the existing truncation at line 1233."
```

---

### Task 2: Add Canonical Finding Sort at Engine Level

**Files:**
- Modify: `repotoire-cli/src/detectors/engine.rs:1229-1230`

**Step 1: Replace severity-only sort with full canonical sort**

In `engine.rs`, replace the sort at line 1229-1230:

```rust
        // Sort by severity (highest first)
        all_findings.sort_by(|a, b| b.severity.cmp(&a.severity));
```

With a canonical sort that breaks all ties deterministically:

```rust
        // Canonical sort for deterministic output: severity (desc), file, line, detector, title
        all_findings.sort_by(|a, b| {
            b.severity
                .cmp(&a.severity)
                .then_with(|| {
                    let a_file = a
                        .affected_files
                        .first()
                        .map(|f| f.to_string_lossy())
                        .unwrap_or_default();
                    let b_file = b
                        .affected_files
                        .first()
                        .map(|f| f.to_string_lossy())
                        .unwrap_or_default();
                    a_file.cmp(&b_file)
                })
                .then_with(|| a.line_start.cmp(&b.line_start))
                .then_with(|| a.detector.cmp(&b.detector))
                .then_with(|| a.title.cmp(&b.title))
        });
```

This ensures the subsequent `truncate(self.max_findings)` on line 1233 always keeps the same findings.

**Step 2: Verify compilation**

Run: `cargo check -p repotoire-cli`

**Step 3: Commit**

```bash
git add repotoire-cli/src/detectors/engine.rs
git commit -m "fix: canonical finding sort for deterministic truncation

The previous severity-only sort left findings with equal severity
in arbitrary order, making truncation nondeterministic. Now sorts
by severity, file, line, detector, and title."
```

---

### Task 3: Fix Voting Engine HashMap Nondeterminism

**Files:**
- Modify: `repotoire-cli/src/detectors/voting_engine.rs:22` (imports)
- Modify: `repotoire-cli/src/detectors/voting_engine.rs:550-576` (MajorityVote + WeightedVote)
- Modify: `repotoire-cli/src/detectors/voting_engine.rs:484-516` (Bayesian)

**Step 1: Add BTreeMap import**

At line 22, change:
```rust
use std::collections::{HashMap, HashSet};
```
to:
```rust
use std::collections::{BTreeMap, HashMap, HashSet};
```

**Step 2: Fix MajorityVote (lines 550-561)**

Replace:
```rust
            SeverityResolution::MajorityVote => {
                // Most common severity
                let mut counts: HashMap<Severity, usize> = HashMap::new();
                for finding in findings {
                    *counts.entry(finding.severity).or_insert(0) += 1;
                }
                counts
                    .into_iter()
                    .max_by_key(|(_, count)| *count)
                    .map(|(sev, _)| sev)
                    .unwrap_or(Severity::Medium)
            }
```

With:
```rust
            SeverityResolution::MajorityVote => {
                // Most common severity; ties broken by higher severity
                let mut counts: BTreeMap<Severity, usize> = BTreeMap::new();
                for finding in findings {
                    *counts.entry(finding.severity).or_insert(0) += 1;
                }
                counts
                    .into_iter()
                    .max_by(|(sev_a, count_a), (sev_b, count_b)| {
                        count_a.cmp(count_b).then_with(|| sev_a.cmp(sev_b))
                    })
                    .map(|(sev, _)| sev)
                    .unwrap_or(Severity::Medium)
            }
```

**Step 3: Fix WeightedVote (lines 563-576)**

Replace:
```rust
            SeverityResolution::WeightedVote => {
                // Weight by confidence
                let mut severity_scores: HashMap<Severity, f64> = HashMap::new();
                for finding in findings {
                    let conf = self.get_finding_confidence(finding);
                    let weight = self.get_detector_weight(&finding.detector);
                    *severity_scores.entry(finding.severity).or_insert(0.0) += conf * weight;
                }
                severity_scores
                    .into_iter()
                    .max_by(|(_, a), (_, b)| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal))
                    .map(|(sev, _)| sev)
                    .unwrap_or(Severity::Medium)
            }
```

With:
```rust
            SeverityResolution::WeightedVote => {
                // Weight by confidence; ties broken by higher severity
                let mut severity_scores: BTreeMap<Severity, f64> = BTreeMap::new();
                for finding in findings {
                    let conf = self.get_finding_confidence(finding);
                    let weight = self.get_detector_weight(&finding.detector);
                    *severity_scores.entry(finding.severity).or_insert(0.0) += conf * weight;
                }
                severity_scores
                    .into_iter()
                    .max_by(|(sev_a, a), (sev_b, b)| {
                        a.partial_cmp(b)
                            .unwrap_or(std::cmp::Ordering::Equal)
                            .then_with(|| sev_a.cmp(sev_b))
                    })
                    .map(|(sev, _)| sev)
                    .unwrap_or(Severity::Medium)
            }
```

**Step 4: Fix Bayesian (lines 484-516)**

Replace:
```rust
            ConfidenceMethod::Bayesian => {
                // Bayesian update with detector-family de-correlation (#52).
                // Correlated detectors (same family/prefix) should not count as
                // independent evidence.
                let mut by_family: HashMap<String, Vec<f64>> = HashMap::new();
                for f in findings {
                    let family = f
                        .detector
                        .split(['[', '+', ':'])
                        .next()
                        .unwrap_or(f.detector.as_str())
                        .to_string();
                    by_family
                        .entry(family)
                        .or_default()
                        .push(self.get_finding_confidence(f));
                }

                let family_confidences: Vec<f64> = by_family
                    .values()
                    .map(|vals| vals.iter().sum::<f64>() / vals.len() as f64)
                    .collect();

                let mut prior = 0.5;
                for conf in family_confidences {
                    let likelihood = conf;
                    let denom = prior * likelihood + (1.0 - prior) * (1.0 - likelihood);
                    if denom > 0.0 {
                        prior = (prior * likelihood) / denom;
                    }
                }
                prior
            }
```

With:
```rust
            ConfidenceMethod::Bayesian => {
                // Bayesian update with detector-family de-correlation (#52).
                // Correlated detectors (same family/prefix) should not count as
                // independent evidence.
                // BTreeMap ensures alphabetical family order for deterministic
                // sequential Bayesian updates (the update is non-commutative).
                let mut by_family: BTreeMap<String, Vec<f64>> = BTreeMap::new();
                for f in findings {
                    let family = f
                        .detector
                        .split(['[', '+', ':'])
                        .next()
                        .unwrap_or(f.detector.as_str())
                        .to_string();
                    by_family
                        .entry(family)
                        .or_default()
                        .push(self.get_finding_confidence(f));
                }

                let family_confidences: Vec<f64> = by_family
                    .values()
                    .map(|vals| vals.iter().sum::<f64>() / vals.len() as f64)
                    .collect();

                let mut prior = 0.5;
                for conf in family_confidences {
                    let likelihood = conf;
                    let denom = prior * likelihood + (1.0 - prior) * (1.0 - likelihood);
                    if denom > 0.0 {
                        prior = (prior * likelihood) / denom;
                    }
                }
                prior
            }
```

**Step 5: Verify compilation and run existing tests**

Run: `cargo check -p repotoire-cli && cargo test -p repotoire-cli voting`

**Step 6: Commit**

```bash
git add repotoire-cli/src/detectors/voting_engine.rs
git commit -m "fix: deterministic voting engine with BTreeMap

HashMap iteration order is randomized, causing:
- MajorityVote/WeightedVote severity ties to resolve differently
- Bayesian non-commutative updates to produce different posteriors
Replace all HashMap with BTreeMap and add deterministic tie-breaking."
```

---

### Task 4: Fix Detector-Internal HashMap Iteration

**Files:**
- Modify: `repotoire-cli/src/detectors/string_concat_loop.rs:160-165`
- Modify: `repotoire-cli/src/detectors/ai_boilerplate.rs:261-265`

**Step 1: Fix string_concat_loop.rs**

Replace the `drain()` loop at lines 160-165. Change:
```rust
                let flush_loop_concats = |concats: &mut HashMap<String, (usize, u32)>,
                                               findings: &mut Vec<Finding>,
                                               loop_start_line: usize,
                                               file_path: &std::path::Path,
                                               extension: &str| {
                    for (var_name, (first_line, count)) in concats.drain() {
```

To:
```rust
                let flush_loop_concats = |concats: &mut HashMap<String, (usize, u32)>,
                                               findings: &mut Vec<Finding>,
                                               loop_start_line: usize,
                                               file_path: &std::path::Path,
                                               extension: &str| {
                    // Sort by variable name for deterministic finding order
                    let mut entries: Vec<_> = concats.drain().collect();
                    entries.sort_by(|a, b| a.0.cmp(&b.0));
                    for (var_name, (first_line, count)) in entries {
```

**Step 2: Fix ai_boilerplate.rs**

Replace lines 261-265. Change:
```rust
        let dominant_patterns: Vec<BoilerplatePattern> = pattern_counts
            .into_iter()
            .filter(|(_, count)| *count >= functions.len() / 2)
            .map(|(p, _)| p)
            .collect();
```

To:
```rust
        let mut dominant_patterns: Vec<BoilerplatePattern> = pattern_counts
            .into_iter()
            .filter(|(_, count)| *count >= functions.len() / 2)
            .map(|(p, _)| p)
            .collect();
        dominant_patterns.sort();
```

Note: This requires `BoilerplatePattern` to implement `Ord`. Check if it does — if not, sort by debug representation: `dominant_patterns.sort_by(|a, b| format!("{a:?}").cmp(&format!("{b:?}")));`

**Step 3: Verify compilation**

Run: `cargo check -p repotoire-cli`

**Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/string_concat_loop.rs repotoire-cli/src/detectors/ai_boilerplate.rs
git commit -m "fix: sort detector-internal HashMap iterations

string_concat_loop: sort drain() results by variable name
ai_boilerplate: sort dominant patterns after HashMap collection"
```

---

### Task 5: Fix FP Classifier Floating-Point Boundary Jitter

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/postprocess.rs:427`

**Step 1: Round tp_probability before threshold comparison**

Replace line 427:
```rust
                prediction.tp_probability >= config.filter_threshold as f64
```

With:
```rust
                // Round to 4 decimal places to eliminate floating-point
                // jitter from parallel feature extraction (#determinism)
                let rounded = (prediction.tp_probability * 10_000.0).round() / 10_000.0;
                rounded >= config.filter_threshold as f64
```

**Step 2: Verify compilation**

Run: `cargo check -p repotoire-cli`

**Step 3: Commit**

```bash
git add repotoire-cli/src/cli/analyze/postprocess.rs
git commit -m "fix: round FP classifier probability to eliminate boundary jitter

Parallel feature extraction via par_iter() produces slightly different
floating-point values across runs. Rounding to 4 decimal places creates
a stable decision boundary while preserving classifier discrimination."
```

---

### Task 6: Audit Remaining Detectors for HashMap Nondeterminism

**Files:**
- Audit: `repotoire-cli/src/detectors/*.rs` (all detectors)

**Step 1: Search for HashMap iteration patterns that produce findings**

Run: `grep -rn 'HashMap.*drain\|into_iter\|\.iter()' repotoire-cli/src/detectors/ | grep -v test | grep -v '//' | head -40`

For each hit, check if the iteration feeds into finding creation. If so, add sorting. The `inappropriate_intimacy.rs` detector iterates `&a_to_b` (HashMap) on line 324 but since findings get canonically sorted at the engine level (Task 2), and no truncation happens before that sort, the iteration order doesn't affect finding counts — only ordering, which the canonical sort handles.

**Step 2: Fix any additional nondeterministic patterns found**

Apply the same pattern as Task 4: collect into Vec, sort, then iterate.

**Step 3: Verify compilation**

Run: `cargo check -p repotoire-cli`

**Step 4: Commit any fixes**

```bash
git add -u repotoire-cli/src/detectors/
git commit -m "fix: sort remaining HashMap iterations in detectors"
```

---

### Task 7: Add Determinism Regression Test

**Files:**
- Create: `repotoire-cli/tests/determinism.rs`

**Step 1: Write the regression test**

```rust
//! Regression test: verify that running analysis on the same codebase
//! multiple times produces identical findings (count, order, content).

use std::path::PathBuf;

/// Run a full analysis on a fixture directory and return serialized findings.
fn run_analysis(repo_path: &std::path::Path) -> String {
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_repotoire"))
        .args(["analyze", &repo_path.to_string_lossy(), "--format", "json"])
        .output()
        .expect("failed to run repotoire");
    assert!(output.status.success(), "repotoire analyze failed: {}", String::from_utf8_lossy(&output.stderr));
    String::from_utf8(output.stdout).expect("invalid utf8")
}

#[test]
fn findings_are_deterministic() {
    // Use the repotoire-cli source itself as the fixture (it's always available)
    let repo_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"));

    let run1 = run_analysis(&repo_path);
    let run2 = run_analysis(&repo_path);
    let run3 = run_analysis(&repo_path);

    assert_eq!(run1, run2, "Run 1 and Run 2 produced different output");
    assert_eq!(run2, run3, "Run 2 and Run 3 produced different output");
}
```

**Step 2: Run the test**

Run: `cargo test -p repotoire-cli --test determinism -- --nocapture`
Expected: PASS — all 3 runs produce identical JSON output.

If it fails, the diff between runs will show which findings are nondeterministic, guiding further fixes.

**Step 3: Commit**

```bash
git add repotoire-cli/tests/determinism.rs
git commit -m "test: add determinism regression test

Runs analysis 3 times on the same codebase and asserts identical
JSON output. Catches any future nondeterminism regressions."
```

---

### Task 8: Final Verification

**Step 1: Run full test suite**

Run: `cargo test -p repotoire-cli`
Expected: All tests pass.

**Step 2: Run the determinism test specifically**

Run: `cargo test -p repotoire-cli --test determinism -- --nocapture`
Expected: PASS

**Step 3: Run clippy**

Run: `cargo clippy -p repotoire-cli -- -D warnings`
Expected: No warnings.

**Step 4: Commit any final cleanup**

If any cleanup is needed, commit with:
```bash
git commit -m "chore: cleanup after determinism fixes"
```
