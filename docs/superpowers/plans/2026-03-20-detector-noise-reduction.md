# Detector Noise Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce average findings per repo from ~1,500 to ~50-80 by fixing root causes in the 9 noisiest detectors.

**Architecture:** Three categories of fix — recalibrate thresholds (DeepNesting, LongMethods), enrich detection with context (MagicNumbers, DataClumps, ComplexityOutlier), and rearchitect broken mental models (ChangeCoupling, RedundantClass, CoreUtility removal, ModuleCohesion). A prerequisite task adds git history plumbing to AnalysisContext.

**Tech Stack:** Rust, existing graph/git infrastructure (petgraph, git2, CoChangeMatrix)

**Spec:** `docs/superpowers/specs/2026-03-20-detector-noise-reduction-design.md`

---

## File Structure

All paths relative to `repotoire-cli/src/`.

### Modified Files

| File | Change |
|---|---|
| `detectors/analysis_context.rs` | Add `git_churn` and `co_change_summary` fields |
| `engine/stages/detect.rs` | Pass git churn and co-change data into AnalysisContext |
| `engine/stages/git_enrich.rs` | Compute per-file churn summary |
| `graph/primitives.rs` | Add `core_utilities` set, `co_change_summary` map |
| `graph/traits.rs` | Add `is_core_utility()` and `co_change_score()` methods |
| `graph/frozen.rs` | Implement new GraphQuery methods |
| `detectors/deep_nesting.rs` | Language-aware thresholds |
| `detectors/long_methods.rs` | Raise to 80, simplify discounting |
| `detectors/core_utility.rs` | Remove from DETECTOR_FACTORIES, keep logic |
| `detectors/magic_numbers.rs` | Context-aware filtering |
| `detectors/data_clumps.rs` | Raise thresholds, add forwarding check |
| `detectors/module_cohesion.rs` | Simplify to pass-through detection |
| `detectors/lazy_class.rs` | Rearchitect to RedundantClass |
| `detectors/ai_complexity_spike.rs` | Rearchitect to ComplexityOutlier |
| `detectors/shotgun_surgery.rs` | Rearchitect to ChangeCoupling |
| `detectors/mod.rs` | Update DETECTOR_FACTORIES, add aliases |

---

## Task 1: DeepNesting — Language-Aware Thresholds

**Files:**
- Modify: `repotoire-cli/src/detectors/deep_nesting.rs`

The simplest change. Raises thresholds and adds per-language calibration.

- [ ] **Step 1: Write test for language-specific thresholds**

In `deep_nesting.rs`, add to the existing `#[cfg(test)] mod tests`:

```rust
#[test]
fn test_language_threshold_rust() {
    assert_eq!(language_threshold("rs"), 6);
    assert_eq!(language_match_discount("rs"), 2);
}

#[test]
fn test_language_threshold_python() {
    assert_eq!(language_threshold("py"), 5);
    assert_eq!(language_match_discount("py"), 1);
}

#[test]
fn test_language_threshold_default() {
    assert_eq!(language_threshold("unknown"), 5);
    assert_eq!(language_match_discount("unknown"), 2);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd repotoire-cli && cargo test --lib detectors::deep_nesting::tests::test_language_threshold -v`
Expected: FAIL — function not found

- [ ] **Step 3: Implement language threshold functions**

Add to `deep_nesting.rs`:

```rust
fn language_threshold(ext: &str) -> usize {
    match ext {
        "rs" | "go" | "java" | "cs" | "c" | "h" | "cpp" | "cc" | "cxx" | "hpp" => 6,
        "py" | "pyi" | "ts" | "tsx" | "js" | "jsx" | "mjs" => 5,
        _ => 5,
    }
}

fn language_match_discount(ext: &str) -> usize {
    match ext {
        "rs" | "java" | "cs" | "c" | "h" | "cpp" | "cc" | "cxx" | "hpp" => 2,
        _ => 1, // Python, Go, JS/TS, default
    }
}
```

Update `DEFAULT_THRESHOLD` from 4 to 5. Update `MATCH_DISCOUNT` from 1 to 2.

In the `detect()` method, extract the file extension from the finding's file path and use `language_threshold(ext)` instead of the hardcoded default. Use `language_match_discount(ext)` instead of the constant.

Update severity thresholds:
- `effective_depth > threshold + 4` → High
- `effective_depth > threshold + 2` → Medium
- `effective_depth > threshold` → Low

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd repotoire-cli && cargo test --lib detectors::deep_nesting -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/deep_nesting.rs
git commit -m "fix(detectors): language-aware nesting thresholds (4→5-6, discount 1→1-2)"
```

---

## Task 2: LongMethods — Simplify and Raise

**Files:**
- Modify: `repotoire-cli/src/detectors/long_methods.rs`

- [ ] **Step 1: Write test for new thresholds**

```rust
#[test]
fn test_language_threshold_long_methods() {
    assert_eq!(language_line_threshold("rs"), 80);
    assert_eq!(language_line_threshold("py"), 60);
    assert_eq!(language_line_threshold("java"), 100);
    assert_eq!(language_line_threshold("go"), 80);
    assert_eq!(language_line_threshold("unknown"), 80);
}

#[test]
fn test_severity_by_overshoot() {
    // threshold 80: length 250 → 3.1x → High
    assert_eq!(compute_severity(250, 80), Severity::High);
    // threshold 80: length 170 → 2.1x → Medium
    assert_eq!(compute_severity(170, 80), Severity::Medium);
    // threshold 80: length 90 → 1.1x → Low
    assert_eq!(compute_severity(90, 80), Severity::Low);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd repotoire-cli && cargo test --lib detectors::long_methods::tests::test_language_threshold -v`
Expected: FAIL

- [ ] **Step 3: Implement changes**

Add `language_line_threshold(ext)`:
```rust
fn language_line_threshold(ext: &str) -> usize {
    match ext {
        "py" | "pyi" => 60,
        "rs" | "go" | "ts" | "tsx" | "js" | "jsx" | "mjs" => 80,
        "java" | "cs" | "c" | "h" | "cpp" | "cc" | "cxx" | "hpp" => 100,
        _ => 80,
    }
}
```

In `detect()`:
1. Replace the base threshold (50) with `language_line_threshold(ext).max(ctx.threshold(MetricKind::FunctionLength, ...))`
2. **Remove** the match arm discount logic entirely
3. **Remove** the handler 2x multiplier
4. **Keep** orchestrator severity reduction: if orchestrator role detected, reduce High→Medium
5. Severity: `length > threshold * 3` → High, `length > threshold * 2` → Medium, else → Low

- [ ] **Step 4: Run all detector tests**

Run: `cd repotoire-cli && cargo test --lib detectors::long_methods -v`
Expected: All pass (update existing tests that depend on old thresholds)

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/long_methods.rs
git commit -m "fix(detectors): raise long methods threshold to 80, remove match arm discount"
```

---

## Task 3: CoreUtility — Remove from Findings

**Files:**
- Modify: `repotoire-cli/src/detectors/mod.rs`
- Modify: `repotoire-cli/src/detectors/core_utility.rs`

- [ ] **Step 1: Remove CoreUtilityDetector from DETECTOR_FACTORIES**

In `detectors/mod.rs`, find the `DETECTOR_FACTORIES` array and comment out or remove the `register::<CoreUtilityDetector>()` line.

- [ ] **Step 2: Add a public helper function**

In `core_utility.rs`, add a standalone function that other detectors can call:

```rust
/// Check if a function is a core utility (high fan-in, low fan-out, cross-module callers).
/// Used by other detectors to adjust their behavior.
pub fn is_core_utility_node(graph: &dyn GraphQuery, idx: NodeIndex) -> bool {
    let fan_in = graph.call_fan_in_idx(idx);
    let fan_out = graph.call_fan_out_idx(idx);
    if fan_in < 10 || fan_out > 2 {
        return false;
    }
    let module_spread = graph.caller_module_spread(
        &graph.get_node(idx).map(|n| n.qualified_name.clone()).unwrap_or_default()
    );
    module_spread >= 3
}
```

- [ ] **Step 3: Run tests**

Run: `cd repotoire-cli && cargo test --lib detectors -v`
Expected: CoreUtility tests still pass (the detector code exists, just not registered)

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/mod.rs repotoire-cli/src/detectors/core_utility.rs
git commit -m "fix(detectors): remove CoreUtility from findings, keep as helper for other detectors"
```

---

## Task 4: MagicNumbers — Context-Aware Filtering

**Files:**
- Modify: `repotoire-cli/src/detectors/magic_numbers.rs`

- [ ] **Step 1: Write tests for context filtering**

```rust
#[test]
fn test_is_in_conditional_context() {
    assert!(is_conditional_or_arithmetic("    if x > 750 {"));
    assert!(is_conditional_or_arithmetic("    while count < 1024 {"));
    assert!(is_conditional_or_arithmetic("    let y = x * 750;"));
    assert!(!is_conditional_or_arithmetic("    const TIMEOUT = 750;"));
    assert!(!is_conditional_or_arithmetic("    let timeout = 750;"));
    assert!(!is_conditional_or_arithmetic("    set_timeout(750)"));
    assert!(!is_conditional_or_arithmetic("    [1, 2, 750, 4]"));
}

#[test]
fn test_single_file_not_flagged() {
    // Magic numbers appearing in only 1 file should not be flagged
    // (existing multi-file logic, but threshold changes from 2→2 and single-file is dropped)
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd repotoire-cli && cargo test --lib detectors::magic_numbers::tests::test_is_in_conditional -v`
Expected: FAIL

- [ ] **Step 3: Implement context filtering**

Add a function:
```rust
fn is_conditional_or_arithmetic(line: &str) -> bool {
    let trimmed = line.trim();
    // Check for comparison operators with the number
    let has_comparison = ["<", ">", "<=", ">=", "==", "!=", "< ", "> "]
        .iter()
        .any(|op| trimmed.contains(op));
    // Check for arithmetic operators
    let has_arithmetic = [" * ", " + ", " - ", " / ", " % "]
        .iter()
        .any(|op| trimmed.contains(op));
    // Check for conditional keywords
    let has_conditional = trimmed.starts_with("if ")
        || trimmed.starts_with("while ")
        || trimmed.starts_with("} else if ")
        || trimmed.contains("match ")
        || trimmed.contains("case ");

    has_comparison || has_arithmetic || has_conditional
}
```

Extend the existing `NAMED_CONST_PATTERN` to also match `let`/`var`/`:=` declarations.

In the detection loop, add a filter: only proceed with a magic number if `is_conditional_or_arithmetic(line)` is true.

Drop single-file magic numbers entirely (don't produce a finding if the number appears in only 1 file).

- [ ] **Step 4: Run tests**

Run: `cd repotoire-cli && cargo test --lib detectors::magic_numbers -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/magic_numbers.rs
git commit -m "fix(detectors): only flag magic numbers in conditional/arithmetic context"
```

---

## Task 5: DataClumps — Raise Thresholds + Forwarding Check

**Files:**
- Modify: `repotoire-cli/src/detectors/data_clumps.rs`

- [ ] **Step 1: Write tests**

```rust
#[test]
fn test_new_thresholds_filter_small_clumps() {
    // 3 params appearing 3 times → should NOT be flagged (below new thresholds)
    // 4 params appearing 5 times → SHOULD be flagged
}

#[test]
fn test_subset_deduplication() {
    // (a, b, c) and (a, b, c, d) → only (a, b, c, d) reported
}

#[test]
fn test_framework_convention_skipped() {
    // (req, res, next) → not flagged
    // (ctx, w, r) → not flagged
}
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd repotoire-cli && cargo test --lib detectors::data_clumps::tests -v`

- [ ] **Step 3: Implement changes**

1. Change `min_params` default from 3 to 4
2. Change `min_occurrences` default from 3 to 5
3. Add framework convention skip list:
```rust
const FRAMEWORK_CONVENTIONS: &[&[&str]] = &[
    &["req", "res", "next"],
    &["ctx", "w", "r"],
    &["self", "other"],
    &["request", "response"],
    &["app", "req", "res"],
    &["t", "ctx"],
];
```
4. Add subset deduplication: after collecting all clumps, remove any clump that is a strict subset of another clump with the same or greater occurrence count.
5. Add forwarding check using the graph: for each clump, check if all functions containing it call the same target function. If so, skip (it's a pass-through convention).

- [ ] **Step 4: Run tests**

Run: `cd repotoire-cli && cargo test --lib detectors::data_clumps -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/data_clumps.rs
git commit -m "fix(detectors): raise data clumps thresholds, add forwarding check and dedup"
```

---

## Task 6: ModuleCohesion — Simplify to Pass-Through Detection

**Files:**
- Modify: `repotoire-cli/src/detectors/module_cohesion.rs`

- [ ] **Step 1: Write test**

```rust
#[test]
fn test_pass_through_detection() {
    // File with 0 internal calls, 10 external calls, in a 5+ file module → flagged Medium
    // File with 0 internal calls, 3 external calls → not flagged (below threshold)
    // File with 1 internal call, 10 external calls → not flagged (not pure pass-through)
    // File with 0 internal calls, 10 external calls, in a 2-file module → not flagged (module too small)
}
```

- [ ] **Step 2: Run test to verify failure**

- [ ] **Step 3: Rewrite detection logic**

Replace the cohesion ratio check with pass-through detection:

```rust
fn detect(&self, ctx: &AnalysisContext) -> Vec<Finding> {
    let mut findings = Vec::new();

    for file_node in ctx.graph.get_files() {
        let file_path = &file_node.qualified_name;

        // Count internal vs external calls
        let functions_in_file = ctx.graph.get_functions_in_file(file_path);
        let mut internal_calls = 0;
        let mut external_calls = 0;

        for func in &functions_in_file {
            for callee in ctx.graph.get_callees(&func.qualified_name) {
                if functions_in_file.iter().any(|f| f.qualified_name == callee.qualified_name) {
                    internal_calls += 1;
                } else {
                    external_calls += 1;
                }
            }
        }

        // Only flag pure pass-through files
        if internal_calls > 0 || external_calls < 5 {
            continue;
        }

        // Check module size (directory must have 5+ files)
        let module_dir = std::path::Path::new(file_path).parent();
        let module_file_count = /* count files in same directory from graph */;
        if module_file_count < 5 {
            continue;
        }

        let severity = if external_calls >= 10 {
            Severity::Medium
        } else {
            Severity::Low
        };

        // Create finding...
    }

    findings
}
```

Remove the dead Louvain code (or leave it with `#[allow(dead_code)]` — it may be used in a future iteration).

- [ ] **Step 4: Run tests**

Run: `cd repotoire-cli && cargo test --lib detectors::module_cohesion -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/module_cohesion.rs
git commit -m "fix(detectors): simplify module cohesion to pass-through detection only"
```

---

## Task 7: Git History Plumbing (Prerequisite for Tasks 8-9)

**Files:**
- Modify: `repotoire-cli/src/detectors/analysis_context.rs`
- Modify: `repotoire-cli/src/engine/stages/detect.rs`
- Modify: `repotoire-cli/src/engine/stages/git_enrich.rs`

This adds git churn data to AnalysisContext so ChangeCoupling and ComplexityOutlier can use it.

- [ ] **Step 1: Define FileChurnInfo struct**

In `analysis_context.rs`, add:

```rust
/// Per-file git churn summary for detectors that need change-frequency data.
#[derive(Debug, Clone, Default)]
pub struct FileChurnInfo {
    /// Commits touching this file in the last 90 days
    pub commits_90d: u32,
    /// Whether this file has high churn (commits_90d >= 5)
    pub is_high_churn: bool,
}
```

- [ ] **Step 2: Add fields to AnalysisContext**

Add to the `AnalysisContext` struct:

```rust
pub git_churn: Arc<HashMap<String, FileChurnInfo>>,
pub co_change_summary: Arc<HashMap<NodeIndex, f64>>,
```

- [ ] **Step 3: Compute churn data in git_enrich stage**

In `git_enrich.rs`, after the existing git enrichment, compute per-file churn:

```rust
pub fn compute_file_churn(repo_path: &Path) -> HashMap<String, FileChurnInfo> {
    let mut churn = HashMap::new();
    if let Ok(history) = GitHistory::open(repo_path) {
        // Use get_file_churn() or iterate recent commits
        // For each file, count commits in last 90 days
    }
    churn
}
```

Add the churn map to `GitEnrichOutput`.

- [ ] **Step 4: Pass churn data through to AnalysisContext**

In `detect.rs`, update the `PrecomputedAnalysis::to_context()` call to include `git_churn` and `co_change_summary` from the engine state.

- [ ] **Step 5: Run tests**

Run: `cd repotoire-cli && cargo test --lib detectors -v`
Expected: All pass (new fields default to empty Arc<HashMap>)

- [ ] **Step 6: Commit**

```bash
git add repotoire-cli/src/detectors/analysis_context.rs repotoire-cli/src/engine/stages/detect.rs repotoire-cli/src/engine/stages/git_enrich.rs
git commit -m "feat(detectors): add git churn and co-change data to AnalysisContext"
```

---

## Task 8: AIComplexitySpike → ComplexityOutlier

**Files:**
- Modify: `repotoire-cli/src/detectors/ai_complexity_spike.rs`
- Modify: `repotoire-cli/src/detectors/mod.rs` (add alias)

Depends on Task 7 (git churn in AnalysisContext).

- [ ] **Step 1: Write tests for compound signal**

```rust
#[test]
fn test_requires_compound_signal() {
    // High z-score alone → not flagged
    // High z-score + generic name → flagged
    // High z-score + high churn file → flagged
    // High z-score + structural anomaly → flagged
    // z-score < 3.0 → never flagged regardless of other signals
    // complexity < 20 → never flagged
}

#[test]
fn test_generic_name_detection() {
    assert!(is_generic_name("process"));
    assert!(is_generic_name("handle"));
    assert!(is_generic_name("execute"));
    assert!(is_generic_name("doStuff"));
    assert!(is_generic_name("run"));
    assert!(is_generic_name("func1"));
    assert!(is_generic_name("helper"));
    assert!(is_generic_name("impl_"));
    assert!(!is_generic_name("parse_http_header"));
    assert!(!is_generic_name("validate_user_input"));
    assert!(!is_generic_name("compute_modularity"));
}
```

- [ ] **Step 2: Run test to verify failure**

- [ ] **Step 3: Implement changes**

1. Raise `z_score_threshold` from 2.0 to 3.0
2. Raise minimum complexity from 10 to 20
3. Add `is_generic_name(name: &str) -> bool`:
```rust
const GENERIC_NAMES: &[&str] = &[
    "process", "handle", "execute", "run", "do", "go", "work",
    "func", "helper", "impl", "inner", "main_logic",
    "do_something", "do_work", "process_data",
];

fn is_generic_name(name: &str) -> bool {
    let lower = name.to_lowercase();
    GENERIC_NAMES.iter().any(|g| lower == *g || lower.starts_with(&format!("{}_", g)))
        || lower.starts_with("func") && lower[4..].chars().all(|c| c.is_ascii_digit())
}
```

4. Add compound signal check in `detect()`:
   - Check naming anomaly: `is_generic_name(func_name)`
   - Check recency: `ctx.git_churn.get(file_path).map(|c| c.is_high_churn).unwrap_or(false)`
   - Check structural anomaly: high complexity AND high nesting (depth > 5) AND calls to 5+ different modules
   - Require at least 1 compound signal alongside z > 3.0

5. Rename finding title from "Complexity Spike" to "Complexity Outlier"
6. Remove all "AI-generated" language from descriptions

- [ ] **Step 4: Update severity**

- All three signals → High
- Two signals → Medium
- One signal → Low
- Zero signals → don't flag

- [ ] **Step 5: Add backward compatibility alias**

In `detectors/mod.rs`, add the old name as a suppression alias:
```rust
const DETECTOR_ALIASES: &[(&str, &str)] = &[
    ("AIComplexitySpikeDetector", "ComplexityOutlierDetector"),
];
```

- [ ] **Step 6: Run tests**

Run: `cd repotoire-cli && cargo test --lib detectors::ai_complexity_spike -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add repotoire-cli/src/detectors/ai_complexity_spike.rs repotoire-cli/src/detectors/mod.rs
git commit -m "fix(detectors): rearchitect AIComplexitySpike → ComplexityOutlier with compound signals"
```

---

## Task 9: LazyClass → RedundantClass

**Files:**
- Modify: `repotoire-cli/src/detectors/lazy_class.rs`
- Modify: `repotoire-cli/src/detectors/mod.rs` (add alias)

- [ ] **Step 1: Write tests**

```rust
#[test]
fn test_standalone_small_class_not_flagged() {
    // A class with 2 methods, no overlap with siblings → NOT flagged
}

#[test]
fn test_overlapping_classes_flagged() {
    // Two classes in same module with 3+ methods of same name+arity → flagged
}

#[test]
fn test_trivial_wrapper_flagged() {
    // Class where every method has exactly 1 callee on the same target class → flagged
}

#[test]
fn test_data_class_skipped() {
    // Class with only getters/constructors → NOT flagged even if small
}
```

- [ ] **Step 2: Run test to verify failure**

- [ ] **Step 3: Rearchitect detection logic**

Replace the "small class = lazy" logic with:

1. **Overlap detection**: For each class with <= 5 methods, compare method names and arities with other classes in the same directory. Flag if 3+ methods overlap.

2. **Trivial wrapper detection**: For each class, check if all its methods have exactly 1 outgoing `Calls` edge AND all those edges target methods on the same single other class.

3. **Skip list**: Don't flag classes that match data class patterns (all getters/setters, constructors, `__init__`, `new`, enums, error types).

4. Update finding titles:
   - Overlap: "Redundant Class: {name} (shares {N} methods with {other})"
   - Wrapper: "Trivial Wrapper: {name} (delegates entirely to {target})"

- [ ] **Step 4: Add alias**

Add `("LazyClassDetector", "RedundantClassDetector")` to `DETECTOR_ALIASES`.

- [ ] **Step 5: Run tests**

Run: `cd repotoire-cli && cargo test --lib detectors::lazy_class -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add repotoire-cli/src/detectors/lazy_class.rs repotoire-cli/src/detectors/mod.rs
git commit -m "fix(detectors): rearchitect LazyClass → RedundantClass (overlap + wrapper detection)"
```

---

## Task 10: ShotgunSurgery → ChangeCoupling

**Files:**
- Modify: `repotoire-cli/src/detectors/shotgun_surgery.rs`
- Modify: `repotoire-cli/src/detectors/mod.rs` (add alias)

Depends on Task 7 (git churn + co-change in AnalysisContext).

- [ ] **Step 1: Write tests**

```rust
#[test]
fn test_stable_widely_called_not_flagged() {
    // High fan-in, zero churn → risk ≈ 0 → NOT flagged
}

#[test]
fn test_volatile_widely_called_flagged() {
    // High fan-in, high churn, high co-change → flagged
}

#[test]
fn test_no_git_data_skips_entirely() {
    // When git_churn is empty, detector produces zero findings
}

#[test]
fn test_risk_formula_bounded() {
    // Verify risk is always in [0, 1] range
}
```

- [ ] **Step 2: Run test to verify failure**

- [ ] **Step 3: Implement new algorithm**

Replace the fan-in-only detection with the change coupling formula:

```rust
fn detect(&self, ctx: &AnalysisContext) -> Vec<Finding> {
    // If no git data available, skip entirely
    if ctx.git_churn.is_empty() {
        return Vec::new();
    }

    let mut findings = Vec::new();

    for class in ctx.graph.get_classes() {
        let fan_in = ctx.graph.call_fan_in(&class.qualified_name);
        if fan_in < 10 { continue; }

        // Get file path for this class
        let file_path = /* extract from class node */;

        // Churn rate: commits_90d / 9, capped at 1.0
        let churn_rate = ctx.git_churn
            .get(file_path)
            .map(|c| (c.commits_90d as f64 / 9.0).min(1.0))
            .unwrap_or(0.0);

        if churn_rate < 0.01 { continue; } // Stable code, skip

        // Co-change score (normalized by fan-in)
        let node_idx = ctx.graph.node_idx(&class.qualified_name);
        let co_change = node_idx
            .and_then(|idx| ctx.co_change_summary.get(&idx))
            .copied()
            .unwrap_or(0.0);
        let normalized_co_change = (co_change / fan_in as f64).min(1.0);

        let risk = normalized_co_change * churn_rate;

        if risk < 0.1 { continue; }

        let severity = if risk > 0.5 && fan_in >= 30 {
            Severity::Critical
        } else if risk > 0.3 && fan_in >= 15 {
            Severity::High
        } else {
            Severity::Medium
        };

        // Create finding with title "Change Coupling Risk: {name}"
    }

    findings
}
```

- [ ] **Step 4: Add alias**

Add `("ShotgunSurgeryDetector", "ChangeCouplingDetector")` to `DETECTOR_ALIASES`.

- [ ] **Step 5: Run tests**

Run: `cd repotoire-cli && cargo test --lib detectors::shotgun_surgery -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add repotoire-cli/src/detectors/shotgun_surgery.rs repotoire-cli/src/detectors/mod.rs
git commit -m "fix(detectors): rearchitect ShotgunSurgery → ChangeCoupling (co-change × churn)"
```

---

## Task 11: Validation — Re-run Seed Analysis

**Files:** None (validation only)

- [ ] **Step 1: Rebuild**

```bash
cd repotoire-cli && nix-shell -p gnumake --run "cargo build --release"
```

- [ ] **Step 2: Re-run seed script**

```bash
rm -rf seed-results
PATH="$PWD/target/release:$PATH" ./scripts/seed-benchmarks.sh
```

- [ ] **Step 3: Compare finding counts**

```bash
# Extract total findings per repo from JSON
python3 -c "
import json, os
for f in sorted(os.listdir('seed-results/json')):
    if not f.endswith('.json'): continue
    try:
        raw = open(f'seed-results/json/{f}').read()
        brace = 0
        for i, c in enumerate(raw):
            if c == '{': brace += 1
            elif c == '}':
                brace -= 1
                if brace == 0 and i > 0:
                    data = json.loads(raw[:i+1])
                    break
        findings = len(data.get('findings', []))
        print(f'{f.replace(\".json\",\"\"):30s} {findings:>6}')
    except: pass
"
```

Expected: Average findings per repo should be ~200-400 (down from ~1,500).

- [ ] **Step 4: Spot-check false negatives**

For 3 repos (one Rust, one Python, one Go), manually review 5 findings that were previously reported but are now suppressed. Verify they were correctly removed.

- [ ] **Step 5: Commit validation results**

```bash
git add seed-results/summary.csv
git commit -m "docs: validation results after detector noise reduction"
```

---

## Dependency Graph

```
Tasks 1-6 are independent (can run in parallel)
Task 7 blocks Tasks 8, 9, 10
Tasks 8, 9, 10 are independent of each other (can run in parallel after Task 7)
Task 11 depends on all other tasks
```

```
1 (DeepNesting) ──────────────────────────┐
2 (LongMethods) ──────────────────────────┤
3 (CoreUtility) ──────────────────────────┤
4 (MagicNumbers) ─────────────────────────┤
5 (DataClumps) ───────────────────────────┼──→ 11 (Validation)
6 (ModuleCohesion) ───────────────────────┤
7 (Git Plumbing) ──→ 8 (ComplexityOutlier)┤
                 ──→ 9 (RedundantClass)   ┤
                 ──→ 10 (ChangeCoupling)  ┘
```

Note: Task 9 (RedundantClass) does not technically require Task 7, but is listed after it for ordering convenience. It can run in parallel with Tasks 1-6.
