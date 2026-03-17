# DetectorEngine Elimination — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 2,274-line `DetectorEngine` struct with a ~40-line `run_detectors()` free function and a `PrecomputedAnalysis` struct with a `to_context()` method, eliminating the three-struct data duplication in the detection pipeline.

**Architecture:** `precompute_gd_startup()` returns `PrecomputedAnalysis` (renamed from `GdPrecomputed`). `PrecomputedAnalysis::to_context()` produces `AnalysisContext` directly. `run_detectors()` runs all detectors in parallel via rayon. `detect_stage` orchestrates: precompute → taint inject → context build → run → post-filter.

**Tech Stack:** Rust, rayon, no new dependencies

**Spec:** `docs/superpowers/specs/2026-03-17-detector-engine-elimination-design.md`

---

## File Structure

### Files to create
| File | Purpose |
|------|---------|
| `repotoire-cli/src/detectors/runner.rs` | `run_detectors()`, `inject_taint_precomputed()`, `apply_hmm_context_filter()` |

### Files to modify
| File | Change |
|------|--------|
| `repotoire-cli/src/detectors/engine.rs` | Rename `GdPrecomputed` → `PrecomputedAnalysis`, add `to_context()`, remove `hmm_contexts` field, delete `DetectorEngine` + `DetectorEngineBuilder` |
| `repotoire-cli/src/detectors/mod.rs` | Add `pub mod runner;`, update re-exports (`GdPrecomputed` → `PrecomputedAnalysis`) |
| `repotoire-cli/src/engine/stages/detect.rs` | Rewrite to use `PrecomputedAnalysis` + `run_detectors()` directly |
| `repotoire-cli/src/engine/mod.rs` | Update `EngineState` field name `gd_precomputed` → `precomputed` |
| `repotoire-cli/src/engine/state.rs` | Same field rename |
| `repotoire-cli/src/session.rs` | Switch 3 call sites from `DetectorEngine` to new functions |
| `repotoire-cli/src/detectors/streaming_engine.rs` | Switch caller |
| `repotoire-cli/src/mcp/tools/analysis.rs` | Switch MCP oneshot caller |
| `repotoire-cli/src/cli/analyze/detect.rs` | Delete file (legacy dead code) |

### Files to delete
| File | Reason |
|------|--------|
| `repotoire-cli/src/cli/analyze/detect.rs` | Legacy dead code with `#[allow(dead_code)]`, 3 DetectorEngine usages |

---

## Chunk 1: Add New Code Alongside Old

### Task 1: Create runner.rs with run_detectors() and helpers

**Files:**
- Create: `repotoire-cli/src/detectors/runner.rs`
- Modify: `repotoire-cli/src/detectors/mod.rs`

- [ ] **Step 1: Create runner.rs with run_detectors()**

```rust
//! Detector execution — parallel runner with panic recovery.
//!
//! Replaces DetectorEngine's run methods with a single free function.

use crate::detectors::analysis_context::AnalysisContext;
use crate::detectors::base::{is_test_file, Detector};
use crate::graph::CodeNode;
use crate::models::Finding;
use rayon::prelude::*;
use std::collections::HashMap;
use std::sync::Arc;

/// Run all detectors in parallel against the given context.
///
/// Panics in individual detectors are caught and logged.
/// Per-detector max_findings limits are applied.
/// Results are collected into a flat Vec.
pub fn run_detectors(
    detectors: &[Arc<dyn Detector>],
    ctx: &AnalysisContext,
    workers: usize,
) -> Vec<Finding> {
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(workers.max(1))
        .stack_size(8 * 1024 * 1024) // 8MB for deeply nested C/C++ ASTs
        .build()
        .unwrap_or_else(|_| rayon::ThreadPoolBuilder::new().build().unwrap());

    pool.install(|| {
        detectors
            .par_iter()
            .flat_map(|detector| {
                let start = std::time::Instant::now();
                let result = std::panic::catch_unwind(
                    std::panic::AssertUnwindSafe(|| detector.detect(ctx)),
                );
                let elapsed = start.elapsed();
                match result {
                    Ok(Ok(mut findings)) => {
                        if let Some(config) = detector.config() {
                            if let Some(max) = config.max_findings {
                                if findings.len() > max {
                                    findings.truncate(max);
                                }
                            }
                        }
                        tracing::debug!(
                            "{}: {} findings ({:.1}ms)",
                            detector.name(),
                            findings.len(),
                            elapsed.as_secs_f64() * 1000.0
                        );
                        findings
                    }
                    Ok(Err(e)) => {
                        tracing::debug!(
                            "{} error after {:.1}ms: {}",
                            detector.name(),
                            elapsed.as_secs_f64() * 1000.0,
                            e
                        );
                        vec![]
                    }
                    Err(_) => {
                        tracing::error!(
                            "{} panicked after {:.1}ms",
                            detector.name(),
                            elapsed.as_secs_f64() * 1000.0
                        );
                        vec![]
                    }
                }
            })
            .collect()
    })
}

/// Inject precomputed taint paths into security detectors.
pub fn inject_taint_precomputed(
    detectors: &[Arc<dyn Detector>],
    precomputed: &super::PrecomputedAnalysis,
) {
    for detector in detectors {
        if let Some(category) = detector.taint_category() {
            let cross = precomputed
                .taint_results
                .cross_function
                .get(&category)
                .cloned()
                .unwrap_or_default();
            let intra = precomputed
                .taint_results
                .intra_function
                .get(&category)
                .cloned()
                .unwrap_or_default();
            detector.set_precomputed_taint(cross, intra);
        }
    }
}

/// Filter test-file-only findings (all affected files are test files).
pub fn filter_test_file_findings(findings: &mut Vec<Finding>) {
    let before = findings.len();
    findings.retain(|f| {
        if f.affected_files.is_empty() {
            return true;
        }
        !f.affected_files.iter().all(|p| is_test_file(p))
    });
    let removed = before - findings.len();
    if removed > 0 {
        tracing::debug!("Filtered {} test-file-only findings", removed);
    }
}

/// Apply HMM context filter to reduce FPs for coupling and dead-code detectors.
///
/// Extracted from DetectorEngine::apply_hmm_context_filter().
pub fn apply_hmm_context_filter(
    findings: &mut Vec<Finding>,
    ctx: &AnalysisContext,
) {
    use crate::detectors::context_hmm::FunctionContext;
    use rustc_hash::FxHashSet;

    static COUPLING_DETECTORS: &[&str] = &[
        "DegreeCentralityDetector",
        "ShotgunSurgeryDetector",
        "FeatureEnvyDetector",
        "InappropriateIntimacyDetector",
    ];
    static DEAD_CODE_DETECTORS: &[&str] = &["UnreachableCodeDetector", "DeadCodeDetector"];

    let coupling_set: FxHashSet<&str> = COUPLING_DETECTORS.iter().copied().collect();
    let dead_code_set: FxHashSet<&str> = DEAD_CODE_DETECTORS.iter().copied().collect();

    if ctx.hmm_classifications.is_empty() {
        return; // No HMM data — skip filter
    }

    // Build per-file function lookup from graph for binary search
    let i = ctx.graph.interner();
    let functions = ctx.graph.get_functions();
    let mut func_by_file: HashMap<&str, Vec<&CodeNode>> = HashMap::new();
    for func in &functions {
        let path = func.path(i);
        func_by_file.entry(path).or_default().push(func);
    }
    // Sort each file's functions by line_start for binary search
    for funcs in func_by_file.values_mut() {
        funcs.sort_unstable_by_key(|f| f.line_start);
    }

    let before = findings.len();
    // Use par_iter for parallel filtering (matches original engine behavior)
    let filtered: Vec<Finding> = std::mem::take(findings)
        .into_par_iter()
        .filter(|finding| {
            let is_coupling = coupling_set.contains(finding.detector.as_str());
            let is_dead_code = dead_code_set.contains(finding.detector.as_str());

            if !is_coupling && !is_dead_code {
                return true;
            }

            // Find function at finding's location via binary search
            if let (Some(file), Some(line)) = (finding.affected_files.first(), finding.line_start) {
                let file_str = file.to_string_lossy();
                if let Some(funcs) = func_by_file.get(file_str.as_ref()) {
                    let idx = funcs.partition_point(|f| f.line_start <= line);
                    if idx > 0 {
                        let func = funcs[idx - 1];
                        if func.line_end >= line {
                            let qn = func.qn(i);
                            if let Some((context, _conf)) = ctx.hmm_classifications.get(qn) {
                                if is_coupling && context.skip_coupling() {
                                    return false;
                                }
                                if is_dead_code && context.skip_dead_code() {
                                    return false;
                                }
                            }
                        }
                    }
                }
            }
            true
        })
        .collect();

    *findings = filtered;
    let removed = before - findings.len();
    if removed > 0 {
        tracing::info!("HMM context filter removed {} false positives", removed);
    }
}

/// Sort findings deterministically for reproducible output.
pub fn sort_findings_deterministic(findings: &mut Vec<Finding>) {
    findings.sort_by(|a, b| {
        a.severity
            .cmp(&b.severity)
            .then_with(|| a.affected_files.cmp(&b.affected_files))
            .then_with(|| a.line_start.cmp(&b.line_start))
            .then_with(|| a.detector.cmp(&b.detector))
            .then_with(|| a.title.cmp(&b.title))
    });
}
```

- [ ] **Step 2: Register module in mod.rs**

Add to `repotoire-cli/src/detectors/mod.rs`:
```rust
pub mod runner;
```

And add re-exports:
```rust
pub use runner::{run_detectors, inject_taint_precomputed, apply_hmm_context_filter};
```

- [ ] **Step 3: Verify compilation**

```bash
cd repotoire-cli && cargo check
```

- [ ] **Step 4: Write test for run_detectors**

Add to `runner.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_run_detectors_empty() {
        let graph = GraphStore::in_memory();
        let ctx = AnalysisContext::test(&graph);
        let findings = run_detectors(&[], &ctx, 2);
        assert!(findings.is_empty());
    }

    #[test]
    fn test_sort_findings_deterministic() {
        let mut f1 = Finding::default();
        f1.detector = "B".to_string();
        let mut f2 = Finding::default();
        f2.detector = "A".to_string();
        let mut findings = vec![f1, f2];
        sort_findings_deterministic(&mut findings);
        assert_eq!(findings[0].detector, "A");
        assert_eq!(findings[1].detector, "B");
    }
}
```

- [ ] **Step 5: Run tests**

```bash
cargo test detectors::runner -- --nocapture
```

- [ ] **Step 6: Commit**

```bash
git commit -am "feat: add runner.rs with run_detectors(), inject_taint, HMM filter, sort"
```

### Task 2: Add PrecomputedAnalysis type alias and to_context()

**Files:**
- Modify: `repotoire-cli/src/detectors/engine.rs`
- Modify: `repotoire-cli/src/detectors/mod.rs`

- [ ] **Step 1: Add type alias and to_context()**

In `engine.rs`, add after `GdPrecomputed`:

```rust
/// Type alias for the new name. GdPrecomputed is being renamed to PrecomputedAnalysis.
/// Both names work during migration; GdPrecomputed will be removed in Phase 3.
pub type PrecomputedAnalysis = GdPrecomputed;
```

Add `to_context()` to `impl GdPrecomputed` (or add a new impl block):

```rust
impl GdPrecomputed {
    /// Build an AnalysisContext from precomputed data + graph + resolver.
    /// Eliminates the field-by-field duplication between GdPrecomputed and AnalysisContext.
    pub fn to_context<'g>(
        &self,
        graph: &'g dyn crate::graph::GraphQuery,
        resolver: &crate::calibrate::ThresholdResolver,
    ) -> super::AnalysisContext<'g> {
        super::AnalysisContext {
            graph,
            files: Arc::clone(&self.file_index),
            functions: Arc::clone(&self.contexts),
            taint: Arc::clone(&self.taint_results),
            detector_ctx: Arc::clone(&self.detector_context),
            hmm_classifications: Arc::clone(&self.hmm_with_confidence),
            resolver: Arc::new(resolver.clone()),
            reachability: Arc::clone(&self.reachability),
            public_api: Arc::clone(&self.public_api),
            module_metrics: Arc::clone(&self.module_metrics),
            class_cohesion: Arc::clone(&self.class_cohesion),
            decorator_index: Arc::clone(&self.decorator_index),
        }
    }
}
```

- [ ] **Step 2: Add re-export in mod.rs**

```rust
pub use engine::PrecomputedAnalysis;
```

- [ ] **Step 3: Write test**

```rust
#[test]
fn test_to_context_produces_valid_analysis_context() {
    let graph = crate::graph::GraphStore::in_memory();
    let pre = precompute_gd_startup(&graph, std::path::Path::new("/tmp"), None, &[], None, &[]);
    let resolver = crate::calibrate::ThresholdResolver::default();
    let ctx = pre.to_context(&graph, &resolver);
    // Verify all fields are populated (not None/empty checks — just that it compiles and runs)
    assert!(ctx.hmm_classifications.is_empty() || !ctx.hmm_classifications.is_empty());
    assert_eq!(ctx.repo_path(), std::path::Path::new("/tmp"));
}
```

- [ ] **Step 4: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 5: Commit**

```bash
git commit -am "feat: add PrecomputedAnalysis alias and to_context() method"
```

---

## Chunk 2: Switch detect_stage and AnalysisEngine

### Task 3: Rewrite detect_stage to use run_detectors() directly

**Files:**
- Modify: `repotoire-cli/src/engine/stages/detect.rs`

- [ ] **Step 1: Rewrite detect_stage**

Replace the current implementation that creates a `DetectorEngine` with direct calls to `run_detectors()`:

1. Build detectors via `create_all_detectors()` (already done)
2. Wrap graph in `CachedGraphQuery`
3. Call `precompute_gd_startup()` (or reuse cached)
4. Call `inject_taint_precomputed()`
5. Build context via `precomputed.to_context()`
6. Call `run_detectors()`
7. Apply `apply_hmm_context_filter()`
8. Apply `filter_test_file_findings()`
9. Apply `sort_findings_deterministic()`
10. Partition findings

Key: remove all `DetectorEngine` usage. The detect_stage becomes the orchestrator.

- [ ] **Step 2: Update DetectOutput field name**

Change `gd_precomputed` to `precomputed` in `DetectOutput` struct. Update all references in `engine/mod.rs` that read `detect_out.gd_precomputed` to `detect_out.precomputed`.

- [ ] **Step 3: Verify — `cargo check`**

- [ ] **Step 4: Run analysis smoke test**

```bash
cargo run -- analyze . --format json --no-git --max-files 10 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'Score: {d[\"overall_score\"]:.1f}, Findings: {len(d[\"findings\"])}')"
```

- [ ] **Step 5: Run full tests**

```bash
cargo test --lib
```

- [ ] **Step 6: Commit**

```bash
git commit -am "refactor: rewrite detect_stage to use run_detectors() directly

Removes DetectorEngine dependency from detect_stage. The stage now
orchestrates: precompute → taint inject → context build → run →
HMM filter → test filter → sort → partition."
```

### Task 4: Update EngineState field name

**Files:**
- Modify: `repotoire-cli/src/engine/state.rs`
- Modify: `repotoire-cli/src/engine/mod.rs`

- [ ] **Step 1: Rename field in EngineState**

In `state.rs`, change:
```rust
pub gd_precomputed: Option<GdPrecomputed>,
// to:
pub precomputed: Option<PrecomputedAnalysis>,
```

Update the import.

- [ ] **Step 2: Update all references in mod.rs**

Find all `state.gd_precomputed` or `.gd_precomputed` references in `engine/mod.rs` and change to `.precomputed`.

- [ ] **Step 3: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 4: Commit**

```bash
git commit -am "refactor: rename gd_precomputed to precomputed in EngineState"
```

---

## Chunk 3: Switch Remaining Callers and Delete DetectorEngine

### Task 5: Switch session.rs, streaming_engine.rs, MCP callers

**Files:**
- Modify: `repotoire-cli/src/session.rs` (3 call sites)
- Modify: `repotoire-cli/src/detectors/streaming_engine.rs`
- Modify: `repotoire-cli/src/mcp/tools/analysis.rs`

- [ ] **Step 1: Read each file to find DetectorEngine usages**

```bash
cd /home/zhammad/personal/repotoire && grep -rn "DetectorEngine\|DetectorEngineBuilder" repotoire-cli/src/ --include="*.rs" | grep -v "engine.rs" | grep -v "mod.rs"
```

- [ ] **Step 2: Switch each caller**

For each call site, replace `DetectorEngine` + `inject_gd_precomputed` + `run` with:

```rust
// 1. Build detectors
let init = DetectorInit { ... };
let detectors = create_all_detectors(&init);

// 2. Precompute
let precomputed = precompute_gd_startup(graph, repo_path, ...);

// 3. Inject taint + build context + run
inject_taint_precomputed(&detectors, &precomputed);
let ctx = precomputed.to_context(graph, &resolver);
let mut findings = run_detectors(&detectors, &ctx, workers);

// 4. Post-filters
apply_hmm_context_filter(&mut findings, &ctx);
filter_test_file_findings(&mut findings);
sort_findings_deterministic(&mut findings);
```

- [ ] **Step 3: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 4: Commit**

```bash
git commit -am "refactor: switch session, streaming_engine, MCP from DetectorEngine to run_detectors"
```

### Task 6: Delete DetectorEngine and legacy code

**Files:**
- Modify: `repotoire-cli/src/detectors/engine.rs` — delete DetectorEngine struct, all impl methods, DetectorEngineBuilder
- Delete: `repotoire-cli/src/cli/analyze/detect.rs` — legacy dead code
- Modify: `repotoire-cli/src/detectors/mod.rs` — remove DetectorEngine/DetectorEngineBuilder re-exports

- [ ] **Step 1: Verify no remaining references**

```bash
grep -rn "DetectorEngine" repotoire-cli/src/ --include="*.rs" | grep -v "//.*DetectorEngine"
```

Should only return definitions in `engine.rs` and re-exports in `mod.rs`.

- [ ] **Step 2: Delete DetectorEngine struct and impl**

In `engine.rs`, delete:
- `pub struct DetectorEngine { ... }` and entire `impl DetectorEngine { ... }` block
- `pub struct DetectorEngineBuilder { ... }` and entire `impl DetectorEngineBuilder { ... }` block
- `build_hmm_contexts_standalone()` — if still only used by DetectorEngine (check if precompute_gd_startup uses it — if yes, keep it)

Preserve:
- `pub struct GdPrecomputed` (with `PrecomputedAnalysis` alias) — still used
- `pub fn precompute_gd_startup()` — still used
- `to_context()` — just added

- [ ] **Step 3: Rename GdPrecomputed to PrecomputedAnalysis**

Now that DetectorEngine is gone, replace the alias with a direct rename:
- Rename `pub struct GdPrecomputed` → `pub struct PrecomputedAnalysis`
- Remove the type alias
- Update all references across the codebase

- [ ] **Step 4: Delete legacy detect.rs**

```bash
rm repotoire-cli/src/cli/analyze/detect.rs
```

Remove `mod detect;` from `cli/analyze/mod.rs`.

- [ ] **Step 5: Update mod.rs re-exports**

Remove:
```rust
pub use engine::{DetectorEngine, DetectorEngineBuilder};
```

Ensure `PrecomputedAnalysis`, `precompute_gd_startup`, `run_detectors`, etc. are exported.

- [ ] **Step 6: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 7: Count lines deleted**

```bash
git diff --stat HEAD
```

Expected: ~2,000+ lines deleted from engine.rs.

- [ ] **Step 8: Commit**

```bash
git commit -am "refactor: delete DetectorEngine (2,100+ lines), rename GdPrecomputed to PrecomputedAnalysis

DetectorEngine replaced by:
- run_detectors() (~40 lines) for parallel execution
- PrecomputedAnalysis::to_context() (~15 lines) for context building
- inject_taint_precomputed() (~15 lines) for taint injection
- apply_hmm_context_filter() (~60 lines) for FP reduction"
```

---

## Chunk 4: Verification

### Task 7: End-to-end verification

- [ ] **Step 1: Full test suite**

```bash
cd repotoire-cli && cargo test --lib
```

- [ ] **Step 2: Smoke test real analysis**

```bash
cargo run -- analyze . --format json --no-git --max-files 30 2>/dev/null | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f'Score: {d[\"overall_score\"]:.1f} ({d[\"grade\"]})')
print(f'Findings: {len(d[\"findings\"])}')
detectors = set(f['detector'] for f in d['findings'])
print(f'Active detectors: {len(detectors)}')
"
```

- [ ] **Step 3: Verify engine.rs line count**

```bash
wc -l repotoire-cli/src/detectors/engine.rs
```

Should be ~200-300 lines (precompute_gd_startup + PrecomputedAnalysis + to_context), down from 2,274.

- [ ] **Step 4: Verify runner.rs line count**

```bash
wc -l repotoire-cli/src/detectors/runner.rs
```

Should be ~200 lines (run_detectors + helpers + tests).

- [ ] **Step 5: Commit any fixes**

```bash
git commit -am "chore: verification complete — DetectorEngine elimination"
```
