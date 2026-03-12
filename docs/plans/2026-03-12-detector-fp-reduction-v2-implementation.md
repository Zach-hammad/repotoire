# Detector FP Reduction V2 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace regex/heuristic detection with existing AST, graph, and ML infrastructure across 6 worst-precision detectors, improving Flask benchmark precision from 9.1% to ≥60%.

**Architecture:** Wire 3 underutilized systems (ContextHMM, ThresholdResolver, FunctionContextMap) into the detection pipeline first, then rewrite/refine each detector to use them. All infrastructure already exists — this is integration work, not greenfield.

**Tech Stack:** Rust, petgraph, tree-sitter, existing ValueStore/ContextHMM/ThresholdResolver/FunctionContextMap

---

## Task 1: Wire ContextHMM classifications into AnalysisContext

**Files:**
- Modify: `repotoire-cli/src/detectors/analysis_context.rs`
- Modify: `repotoire-cli/src/cli/analyze/mod.rs` (or the file that builds AnalysisContext)
- Test: inline `#[cfg(test)]` module in `analysis_context.rs`

**Context:**
- `ContextHMM` is at `src/detectors/context_hmm/mod.rs`. It has `classify_with_confidence(&FunctionFeatures) -> (FunctionContext, f64)`.
- `FunctionFeatures::extract(name, file_path, fan_in, fan_out, ...)` builds the feature vector.
- `AnalysisContext` currently has: `graph`, `files`, `functions: Arc<FunctionContextMap>`, `taint`, `detector_ctx`.
- **Goal**: Add `hmm_classifications: Arc<HashMap<String, (context_hmm::FunctionContext, f64)>>` to AnalysisContext so any detector can query `ctx.hmm_role(func_qn)`.

**Step 1: Write the failing test**

In `analysis_context.rs`, add a test that calls a new `hmm_role()` method:

```rust
#[test]
fn test_hmm_role_returns_classification() {
    // Build a minimal AnalysisContext with hmm_classifications populated
    // Assert that hmm_role("some_func") returns a FunctionContext + confidence
}
```

**Step 2: Add the field and accessor**

Add to `AnalysisContext`:
```rust
pub hmm_classifications: Arc<HashMap<String, (crate::detectors::context_hmm::FunctionContext, f64)>>,
```

Add helper method:
```rust
pub fn hmm_role(&self, qn: &str) -> Option<(crate::detectors::context_hmm::FunctionContext, f64)> {
    self.hmm_classifications.get(qn).copied()
}
```

**Step 3: Build classifications during pipeline setup**

In the analyze pipeline (where `AnalysisContext` is constructed), after `FunctionContextMap` is built:

```rust
let hmm = crate::detectors::context_hmm::ContextHMM::new();
let mut hmm_classifications = HashMap::new();
let functions = graph.get_functions();
let avg_complexity = /* compute from functions */;
let avg_loc = /* compute from functions */;
let max_fan_in = /* compute */;
let max_fan_out = /* compute */;

for func in &functions {
    let fan_in = graph.call_fan_in(&func.qualified_name());
    let fan_out = graph.call_fan_out(&func.qualified_name());
    let features = FunctionFeatures::extract(
        &func.name(), &func.file_path(), fan_in, fan_out,
        max_fan_in, max_fan_out, graph.caller_file_spread(&func.qualified_name()),
        Some(func.complexity as i64), avg_complexity, func.loc(),
        avg_loc, func.param_count as usize, avg_params, func.address_taken(),
    );
    let (role, confidence) = hmm.classify_with_confidence(&features);
    hmm_classifications.insert(func.qualified_name().to_string(), (role, confidence));
}
```

Pass `Arc::new(hmm_classifications)` into `AnalysisContext`.

**Step 4: Run tests**

Run: `cd repotoire-cli && cargo test analysis_context -- --nocapture`
Then: `cargo test --lib --tests`

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/analysis_context.rs repotoire-cli/src/cli/analyze/
git commit -m "feat: wire ContextHMM classifications into AnalysisContext"
```

---

## Task 2: Wire ThresholdResolver into AnalysisContext

**Files:**
- Modify: `repotoire-cli/src/detectors/analysis_context.rs`
- Modify: `repotoire-cli/src/cli/analyze/mod.rs` (or setup file)
- Test: inline `#[cfg(test)]`

**Context:**
- `ThresholdResolver` is at `src/calibrate/resolver.rs`. Already constructed during detector setup with `ThresholdResolver::new(style_profile)`.
- Has `warn(MetricKind, default) -> f64` and `high(MetricKind, default) -> f64`.
- **Goal**: Make it available to all detectors via AnalysisContext, not just during construction.

**Step 1: Add to AnalysisContext**

```rust
pub resolver: Arc<crate::calibrate::ThresholdResolver>,
```

**Step 2: Wire in pipeline**

The ThresholdResolver is already created in `default_detectors_full()`. Pass it through to AnalysisContext construction.

**Step 3: Run tests, commit**

Run: `cargo test --lib --tests`

```bash
git add repotoire-cli/src/detectors/analysis_context.rs repotoire-cli/src/cli/analyze/
git commit -m "feat: wire ThresholdResolver into AnalysisContext for adaptive thresholds"
```

---

## Task 3: Ensure FunctionContextMap is passed to all detectors

**Files:**
- Modify: `repotoire-cli/src/detectors/analysis_context.rs` (if needed)
- Verify: existing `functions: Arc<FunctionContextMap>` field is populated

**Context:**
- `FunctionContextMap` is `HashMap<String, FunctionContext>` where `FunctionContext` has `.role`, `.betweenness`, `.caller_modules`, `.severity_multiplier()`.
- Already a field on AnalysisContext: `pub functions: Arc<FunctionContextMap>`.
- **Goal**: Verify it's populated (not an empty HashMap) and add a convenience accessor.

**Step 1: Add helper method**

```rust
pub fn function_role(&self, qn: &str) -> Option<FunctionRole> {
    self.functions.get(qn).map(|fc| fc.role)
}

pub fn is_test_function(&self, qn: &str) -> bool {
    self.functions.get(qn).map_or(false, |fc| fc.role == FunctionRole::Test || fc.is_test)
}

pub fn is_utility_function(&self, qn: &str) -> bool {
    self.functions.get(qn).map_or(false, |fc| fc.role == FunctionRole::Utility)
}

pub fn is_hub_function(&self, qn: &str) -> bool {
    self.functions.get(qn).map_or(false, |fc| fc.role == FunctionRole::Hub)
}
```

**Step 2: Write tests, verify, commit**

Run: `cargo test --lib --tests`

```bash
git add repotoire-cli/src/detectors/analysis_context.rs
git commit -m "feat: add role convenience accessors to AnalysisContext"
```

---

## Task 4: DeadStoreDetector — Rewrite with ValueStore

**Files:**
- Rewrite: `repotoire-cli/src/detectors/dead_store.rs`
- Test: inline `#[cfg(test)]` module

**Context:**
- Current: 396 lines, regex-based `ASSIGNMENT` pattern + `is_used_after()` line scan.
- `ValueStore.assignments_in(func_qn)` returns `&[Assignment]` with `variable`, `value`, `line` for every assignment in a function. Already populated during parsing.
- `SymbolicValue::FieldAccess` represents `self.attr = param` — the RHS being a parameter means it's a store, not dead.
- Need access to ValueStore from the detector. ValueStore is available via `DetectorContext` or can be added to `AnalysisContext`.

**Step 1: Write failing tests for new behavior**

```rust
#[test]
fn test_skip_module_level_public_assignments() {
    // Module-level `SECRET_KEY = "..."` in Python should NOT be flagged
    // because it's public (no underscore prefix) and could be imported
}

#[test]
fn test_skip_self_attribute_stores() {
    // `self.name = name` in __init__ should NOT flag `name` as dead store
}

#[test]
fn test_detect_genuine_dead_store() {
    // `x = 5; x = 10; print(x)` — first assignment IS dead
}

#[test]
fn test_skip_exported_variables() {
    // Variables where node.is_exported() should be skipped
}
```

**Step 2: Rewrite the detector**

Replace the three detection methods with ValueStore-based logic:

1. **`find_local_dead_stores()`**: Instead of regex, iterate `value_store.assignments_in(func_qn)`. For each assignment, check if the variable has any read between this assignment line and the next assignment to the same variable. Use the file source (from FileProvider) to find identifier reads via simple word-boundary search scoped between the two assignment lines.

2. **Module-level exemptions**: For assignments at module scope (line < first function's line_start), skip if:
   - Variable doesn't start with `_` (public by Python convention)
   - File is `__init__.py`, `conf.py`, `config.py`, `settings.py`
   - Node for the variable has `is_exported()` flag

3. **`self.x = param` handling**: Check `Assignment.value` — if it's `SymbolicValue::Variable(name)` where `name` matches a parameter, and `Assignment.variable` contains `.` (attribute access), skip it.

4. **Role-based gating**: If `ctx.is_test_function(func_qn)` → skip. If `ctx.is_utility_function(func_qn)` → lower severity.

5. **Remove `find_unused_params()` and `find_cross_function_dead_stores()`** — These were stub/heuristic. The ValueStore approach handles parameter tracking properly.

**Step 3: Run tests**

Run: `cargo test dead_store -- --nocapture`
Then: `cargo test --lib --tests`

**Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/dead_store.rs
git commit -m "feat: rewrite DeadStoreDetector with ValueStore-based analysis"
```

---

## Task 5: DeadCodeDetector — Replace pattern lists with graph flags

**Files:**
- Rewrite: `repotoire-cli/src/detectors/dead_code/mod.rs`
- Modify: `repotoire-cli/src/detectors/dead_code/tests.rs`
- Test: inline + `tests.rs`

**Context:**
- Current: 1,113 lines, 200+ hardcoded patterns in `ENTRY_POINTS`, `DISPATCH_PATHS`, `MAGIC_METHODS`, `FRAMEWORK_AUTO_LOAD_PATTERNS`, `CALLBACK_PATTERNS`.
- Graph nodes have: `is_exported()`, `has_decorators()`, `address_taken()` flags (packed in `CodeNode.flags` u8).
- FunctionContextMap has: `role` (EntryPoint, Test, Utility, Hub, etc.).
- ContextHMM has: `is_python_dunder`, `FileContext::HandlerFile`, `FileContext::TestFile`.

**Step 1: Write failing tests**

```rust
#[test]
fn test_skip_exported_functions() {
    // Function with is_exported() flag should NOT be flagged as dead
}

#[test]
fn test_skip_decorated_functions() {
    // Function with has_decorators() flag should NOT be flagged as dead
}

#[test]
fn test_skip_address_taken_functions() {
    // Function with address_taken() flag should NOT be flagged as dead
}

#[test]
fn test_skip_entry_point_role() {
    // Function with role == EntryPoint should NOT be flagged as dead
}

#[test]
fn test_skip_python_dunder_methods() {
    // __repr__, __contains__, __get__ should NOT be flagged
}

#[test]
fn test_detect_genuinely_dead_function() {
    // Private, non-decorated, non-exported, zero fan-in → IS dead
}
```

**Step 2: Rewrite `find_dead_functions()`**

Replace the pattern-matching exemptions with flag checks. The core logic stays: `call_fan_in(func_qn) == 0 → candidate`. But exemptions become:

```rust
// Skip if ANY of these graph-based conditions are true:
if func.is_exported() { continue; }          // Public API
if func.has_decorators() { continue; }       // Runtime-registered
if func.address_taken() { continue; }        // Used as callback

// Skip if role-based conditions:
if let Some(role) = ctx.function_role(&func_qn) {
    match role {
        FunctionRole::EntryPoint | FunctionRole::Test => continue,
        FunctionRole::Hub | FunctionRole::Utility => continue, // High-value infrastructure
        _ => {}
    }
}

// Skip Python dunder methods:
let name = func.name();
if name.starts_with("__") && name.ends_with("__") { continue; }

// Skip handler files (framework auto-load):
if let Some((hmm_ctx, _)) = ctx.hmm_role(&func_qn) {
    if matches!(hmm_ctx, context_hmm::FunctionContext::Handler) { continue; }
}
```

**Step 3: Remove pattern list constants**

Delete `ENTRY_POINTS`, `DISPATCH_PATHS`, `FRAMEWORK_AUTO_LOAD_PATTERNS`, `CALLBACK_PATTERNS`. Keep `MAGIC_METHODS` as a fallback for non-Python dunder patterns (Rust trait methods etc.) but trim it to ~10 essential entries.

**Step 4: Rewrite `find_dead_classes()`**

Same approach — replace pattern checks with `class.is_exported()`, `class.has_decorators()`, and role checks on class methods.

**Step 5: Run tests**

Run: `cargo test dead_code -- --nocapture`
Then: `cargo test --lib --tests`

**Step 6: Commit**

```bash
git add repotoire-cli/src/detectors/dead_code/
git commit -m "feat: replace DeadCodeDetector pattern lists with graph flags and ContextHMM roles"
```

---

## Task 6: AIMissingTestsDetector — Graph reachability from tests

**Files:**
- Rewrite: `repotoire-cli/src/detectors/ai_missing_tests.rs`
- Test: inline `#[cfg(test)]` module

**Context:**
- Current: 707 lines, name-matching (`test_<func>` pattern).
- `FunctionContextMap` has `role == Test` for test functions.
- `graph.get_callees(qn)` returns all functions called by a function.
- **Goal**: BFS from test functions → mark all reachable functions as "tested".

**Step 1: Write failing tests**

```rust
#[test]
fn test_function_reachable_from_test_is_not_flagged() {
    // test_foo() calls helper() calls target() → target() has coverage
}

#[test]
fn test_function_not_reachable_from_any_test_is_flagged() {
    // No test function reaches isolated_func() → flag it
}

#[test]
fn test_utility_functions_not_flagged() {
    // Functions with role == Utility should be skipped
}

#[test]
fn test_adaptive_complexity_threshold() {
    // Use ThresholdResolver instead of hardcoded complexity >= 5
}
```

**Step 2: Build test reachability set**

```rust
fn build_tested_functions(ctx: &AnalysisContext) -> HashSet<String> {
    let mut tested = HashSet::new();
    let mut queue: VecDeque<String> = VecDeque::new();

    // Seed: all test functions
    for (qn, fc) in ctx.functions.iter() {
        if fc.role == FunctionRole::Test || fc.is_test {
            queue.push_back(qn.clone());
            tested.insert(qn.clone());
        }
    }

    // BFS forward through callees (depth limit 5)
    let mut depth = 0;
    while !queue.is_empty() && depth < 5 {
        let level_size = queue.len();
        for _ in 0..level_size {
            let func_qn = queue.pop_front().unwrap();
            for callee in ctx.graph.get_callees(&func_qn) {
                let callee_qn = callee.qualified_name().to_string();
                if tested.insert(callee_qn.clone()) {
                    queue.push_back(callee_qn);
                }
            }
        }
        depth += 1;
    }

    tested
}
```

**Step 3: Rewrite detect()**

```rust
fn detect_ctx(&self, ctx: &AnalysisContext) -> DetectorResult {
    let tested = build_tested_functions(ctx);
    let complexity_threshold = ctx.resolver.warn(MetricKind::Complexity, 5.0);

    let mut findings = Vec::new();
    for func in ctx.graph.get_functions() {
        let qn = func.qualified_name().to_string();

        // Skip if tested
        if tested.contains(&qn) { continue; }

        // Skip tests themselves
        if ctx.is_test_function(&qn) { continue; }

        // Skip simple functions (adaptive threshold)
        if (func.complexity as f64) < complexity_threshold { continue; }
        if func.loc() < 15 { continue; }

        // Skip utility/leaf roles
        if matches!(ctx.function_role(&qn), Some(FunctionRole::Utility | FunctionRole::Leaf)) {
            continue;
        }

        // Skip non-important functions
        if !func.is_exported() && ctx.functions.get(&qn).map_or(true, |fc| fc.caller_modules < 2) {
            continue;
        }

        // Flag it
        findings.push(create_finding(&func, &tested));
    }
    findings
}
```

**Step 4: Run tests, commit**

Run: `cargo test ai_missing_tests -- --nocapture`
Then: `cargo test --lib --tests`

```bash
git add repotoire-cli/src/detectors/ai_missing_tests.rs
git commit -m "feat: rewrite AIMissingTestsDetector with graph-based test reachability"
```

---

## Task 7: UnreachableCodeDetector — Remove dead function overlap, focus on code-after-return

**Files:**
- Rewrite: `repotoire-cli/src/detectors/unreachable_code.rs`
- Test: inline `#[cfg(test)]` module

**Context:**
- Current: 1,544 lines. Two concerns: (1) code after return/throw (regex), (2) dead functions (fan_in == 0, duplicates DeadCodeDetector). Plus 300+ lines of conditional compilation.
- **Goal**: Remove ALL dead-function logic (let DeadCodeDetector own that). Keep ONLY code-after-return detection. Replace regex with scope-aware line analysis.

**Step 1: Write failing tests**

```rust
#[test]
fn test_code_after_return_detected() {
    // fn foo() { return 1; let x = 2; } → flag `let x = 2`
}

#[test]
fn test_code_in_different_branch_not_flagged() {
    // if cond { return 1; } else { x = 2; } → `x = 2` is NOT unreachable
}

#[test]
fn test_conditional_compilation_skipped() {
    // #[cfg(test)] fn bar() { ... } → skip entirely
}

#[test]
fn test_no_dead_function_findings() {
    // Unreachable detector should NOT produce "unused function" findings
    // That's DeadCodeDetector's job
}
```

**Step 2: Rewrite detect()**

Remove:
- ALL `ENTRY_POINT_PATTERNS` and `ENTRY_POINT_PATHS` (duplicates DeadCode)
- ALL dead function detection logic (fan_in == 0 checks)
- `is_entry_point()`, `has_runtime_prefix()`, `is_exported_function_with_content()`

Keep and improve:
- Code-after-return detection, but scope-aware:
  - Read function source from FileProvider
  - Track brace/indent depth
  - When encountering `return`/`throw`/`raise`/`break`/`continue` at a scope level, check if there are more statements at the SAME scope level after it
  - Don't flag code at outer scope levels

Keep:
- `is_conditionally_compiled_rust()` — uses ExtraProps.decorators, lightweight
- `is_in_conditional_block()` — dispatches by language
- ContextHMM gating: skip test files

**Step 3: Run tests, commit**

Run: `cargo test unreachable_code -- --nocapture`
Then: `cargo test --lib --tests`

```bash
git add repotoire-cli/src/detectors/unreachable_code.rs
git commit -m "feat: refactor UnreachableCodeDetector — remove dead function overlap, scope-aware return detection"
```

---

## Task 8: LazyClassDetector — Replace EXCLUDE_PATTERNS with role analysis

**Files:**
- Modify: `repotoire-cli/src/detectors/lazy_class.rs`
- Test: inline `#[cfg(test)]` module

**Context:**
- Current: 1,451 lines, 100+ EXCLUDE_PATTERNS. Already has language-specific logic from Phase A.
- FunctionContextMap: can check method roles within a class. If any method is Hub/high-betweenness, class is important.
- ThresholdResolver: `resolver.warn(MetricKind::ClassMethodCount, 3.0)` adapts to codebase.
- ContextHMM FileContext: skip handler/framework files.

**Step 1: Write failing tests**

```rust
#[test]
fn test_class_with_hub_method_not_flagged() {
    // Class with 1 method but that method has high betweenness → skip
}

#[test]
fn test_adaptive_method_threshold() {
    // Codebase where median class has 2 methods → threshold should increase
}

#[test]
fn test_handler_file_classes_skipped() {
    // Class in a handler file → skip (framework pattern)
}
```

**Step 2: Add role-based exemptions**

In `detect()`, after existing language-specific checks, add:

```rust
// Role-based: skip if any method is architecturally important
if let Some(ctx) = analysis_context {
    let methods = graph.get_functions_in_file(&class.file_path())
        .iter()
        .filter(|f| /* f belongs to this class by line range */)
        .collect::<Vec<_>>();

    let has_important_method = methods.iter().any(|m| {
        ctx.functions.get(&m.qualified_name().to_string())
            .map_or(false, |fc| fc.betweenness > 0.05 || fc.role == FunctionRole::Hub)
    });
    if has_important_method { continue; }

    // Adaptive threshold
    let max_methods = ctx.resolver.warn_usize(MetricKind::ClassMethodCount, 3);
    if method_count > max_methods { continue; } // Not lazy — above adaptive threshold
}
```

**Step 3: Trim EXCLUDE_PATTERNS**

Remove patterns that are now covered by role analysis:
- Remove: "adapter", "wrapper", "proxy", "facade", "bridge" → covered by betweenness check
- Remove: "handler", "listener", "observer", "factory", "builder", "provider", "service" → covered by FileContext + role
- Keep: "exception", "error", "test", "mock", "stub", "fixture", "config", "settings", "dto", "entity", "model" → these are semantic, not role-based

Target: 100+ → ~25 patterns.

**Step 4: Run tests, commit**

Run: `cargo test lazy_class -- --nocapture`
Then: `cargo test --lib --tests`

```bash
git add repotoire-cli/src/detectors/lazy_class.rs
git commit -m "feat: replace LazyClass EXCLUDE_PATTERNS with role analysis and adaptive thresholds"
```

---

## Task 9: ShotgunSurgeryDetector — Role-aware threshold scaling

**Files:**
- Modify: `repotoire-cli/src/detectors/shotgun_surgery.rs`
- Test: inline `#[cfg(test)]` module

**Context:**
- Current: 606 lines, 60+ SKIP_METHODS, 25+ UTILITY_PREFIXES.
- FunctionContextMap: `role` and `severity_multiplier()` already exist.
- `graph.caller_module_spread(qn)` pre-computed.
- ContextHMM: `looks_like_utility()` combines naming + path + graph signals.

**Step 1: Write failing tests**

```rust
#[test]
fn test_hub_class_not_flagged() {
    // Class where all methods have role == Hub → thresholds scaled 3x
}

#[test]
fn test_utility_function_not_flagged() {
    // Function where looks_like_utility() is true → skip
}

#[test]
fn test_genuinely_coupled_code_flagged() {
    // Non-utility, non-hub function with 20 callers across 8 modules → flag
}
```

**Step 2: Add role-based threshold scaling**

In `detect()`, when evaluating each class/function:

```rust
// Get the role of the class's primary methods
let primary_role = class_methods.iter()
    .filter_map(|m| ctx.functions.get(&m.qualified_name().to_string()))
    .map(|fc| fc.role)
    .max_by_key(|r| match r {
        FunctionRole::Hub => 4,
        FunctionRole::Utility => 3,
        FunctionRole::Orchestrator => 2,
        _ => 1,
    })
    .unwrap_or(FunctionRole::Unknown);

let threshold_multiplier = match primary_role {
    FunctionRole::Hub => 3.0,
    FunctionRole::Utility => 2.5,
    FunctionRole::Orchestrator => 2.0,
    _ => 1.0,
};

let effective_min_callers = (self.thresholds.min_callers as f64 * threshold_multiplier) as usize;
let effective_critical_modules = (self.thresholds.critical_modules as f64 * threshold_multiplier) as usize;
```

**Step 3: Replace SKIP_METHODS and UTILITY_PREFIXES with ContextHMM**

```rust
// Instead of checking 85+ static patterns:
if let Some((hmm_ctx, conf)) = ctx.hmm_role(&method_qn) {
    if matches!(hmm_ctx, context_hmm::FunctionContext::Utility) && conf > 0.7 {
        continue; // Skip utility functions
    }
}
```

Keep a small SKIP_METHODS list (~10 entries) for stdlib methods that conflate graph edges: `new`, `default`, `clone`, `fmt`, `eq`, `hash`, `from`, `into`, `drop`, `deref`.

**Step 4: Use pre-computed `caller_module_spread`**

Replace manual module counting:
```rust
let module_spread = graph.caller_module_spread(&func_qn);
```

**Step 5: Run tests, commit**

Run: `cargo test shotgun_surgery -- --nocapture`
Then: `cargo test --lib --tests`

```bash
git add repotoire-cli/src/detectors/shotgun_surgery.rs
git commit -m "feat: role-aware ShotgunSurgery thresholds with ContextHMM utility detection"
```

---

## Task 10: Re-run Flask benchmark and update labels

**Files:**
- Modify: `benchmark/flask/labels.json`

**Step 1: Re-analyze Flask**

```bash
cd repotoire-cli
cargo run --release -- clean ../benchmark/flask/repo
cargo run --release -- analyze ../benchmark/flask/repo --format json --output ../benchmark/flask/results.json
```

**Step 2: Compare finding counts**

```bash
jq '[.findings[] | .detector] | group_by(.) | map({detector: .[0], count: length}) | sort_by(-.count)' ../benchmark/flask/results.json
```

Compare against before:
- DeadStoreDetector: was 95, target <10
- DeadCodeDetector: was 82, target <15
- AIMissingTestsDetector: was 38, target <5
- UnreachableCodeDetector: was 33, target <5
- LazyClassDetector: was 13, target <3
- ShotgunSurgeryDetector: was 9, target <2

**Step 3: Re-label changed findings**

Review new findings from the 6 detectors. Update labels.json with TP/FP for new finding IDs. Remove labels for findings that no longer exist.

**Step 4: Run precision test**

```bash
cargo test benchmark_precision_flask -- --ignored --nocapture
```

Target: overall precision ≥60%.

**Step 5: Commit**

```bash
cd .. && git add benchmark/flask/labels.json
git commit -m "feat: update Flask benchmark labels after detector FP reduction v2"
```

---

## Task 11: Self-analysis validation and final metrics

**Step 1: Clean and run self-analysis**

```bash
cd repotoire-cli
cargo run --release -- clean ..
cargo run --release -- analyze .. --format json --output /tmp/repotoire-v2-final.json
```

**Step 2: Compare metrics**

```bash
jq '{total_findings: (.findings | length), grade: .grade, overall_score: .overall_score, quality_score: .quality_score}' /tmp/repotoire-v2-final.json
```

Target:
- Total findings: <1,000 (was 1,657)
- Grade: B (83+) (was C+ 80.0)
- Quality score: >50 (was 40.1)

**Step 3: Check detector-specific counts**

```bash
jq '[.findings[] | .detector] | group_by(.) | map({detector: .[0], count: length}) | sort_by(-.count) | .[:10]' /tmp/repotoire-v2-final.json
```

**Step 4: Run full test suite**

```bash
cargo test --lib --tests
```

All tests must pass.

**Step 5: Commit**

```bash
cd .. && git commit --allow-empty -m "chore: detector FP reduction v2 complete — final metrics recorded"
```
