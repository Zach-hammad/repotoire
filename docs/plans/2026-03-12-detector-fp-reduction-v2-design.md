# Detector FP Reduction V2 — Infrastructure-Driven Precision Overhaul

**Date:** 2026-03-12
**Status:** Approved
**Goal:** Fix the 6 worst-precision detectors by replacing regex/heuristic detection with existing AST, graph, and ML infrastructure that's built but underutilized.

## Problem Statement

Flask benchmark (364 labeled findings) revealed 9.1% overall precision across 6 detectors:

| Detector | TP | FP | Precision | Root Cause |
|----------|----|----|-----------|------------|
| DeadStoreDetector | 0 | 95 | 0% | Regex-based, can't track cross-file imports or attribute assignment |
| DeadCodeDetector | 0 | 82 | 0% | Pattern lists miss decorators, dunders, dynamic dispatch |
| AIMissingTestsDetector | 0 | 38 | 0% | Name matching, can't see integration test coverage |
| UnreachableCodeDetector | 1 | 32 | 3% | Overlaps with DeadCode, regex control flow |
| LazyClassDetector | 0 | 13 | 0% | 100+ EXCLUDE_PATTERNS still insufficient |
| ShotgunSurgeryDetector | 0 | 9 | 0% | Ignores architectural roles (Hub/Utility) |

## Key Insight: Infrastructure Exists But Is Unused

| Infrastructure | Status | Detectors Using | Could Benefit |
|---|---|---|---|
| ContextHMM (92% accuracy on function role classification) | Built | **0 detectors** | 40+ |
| Calibration/ThresholdResolver (adaptive percentile thresholds) | Built | **0 detectors** | All |
| FunctionContextMap (role, betweenness, module spread) | Built | 8 detectors | 30+ |
| ValueStore (per-function assignment tracking) | Built | 5 detectors | 15+ |
| ContentClassifier (bundled/fixture/test detection) | Built | 15 detectors | 30+ |

The biggest improvement comes from wiring in what we already have, not writing new detection logic.

---

## Phase 0: Shared Infrastructure Wiring

Wire 3 unused systems into the detection pipeline before touching individual detectors. This benefits ALL detectors, not just the 6 being fixed.

### 0a. ContextHMM Integration

The ContextHMM classifies every function into roles (Test, Utility, Handler, Internal, Source) with 92% accuracy using a 20-dimensional feature vector combining naming, path, graph metrics, and language signals. It's fully built but zero detectors use it.

**Wire into AnalysisContext:**
- Add `hmm_classifications: HashMap<String, (FunctionContext, f64)>` to `AnalysisContext`
- Pre-compute classifications for all functions during pipeline setup
- Detectors query: `context.hmm_role(func_qn)` instead of maintaining their own skip lists

**FileContext integration:**
- `FileContext::TestFile`, `FileContext::HandlerFile`, `FileContext::UtilFile` already classify files
- Wire into postprocess as a confidence signal

### 0b. Calibration/ThresholdResolver Integration

ThresholdResolver adapts thresholds to codebase percentiles (p90/p95) with floor/ceiling guardrails. Infrastructure is ready — StyleProfile, MetricDistribution, guardrails all built. Just needs plumbing.

**Wire into DetectorContext:**
- Pass `ThresholdResolver` via `DetectorContext` or `AnalysisContext`
- Detectors replace hardcoded thresholds:
  - `max_complexity: 10` → `resolver.warn(MetricKind::Complexity, 10.0)`
  - `max_methods: 3` → `resolver.warn(MetricKind::ClassMethodCount, 3.0)`
  - `min_params: 5` → `resolver.warn(MetricKind::ParameterCount, 5.0)`
- Codebase with many small functions → higher thresholds automatically

### 0c. FunctionContextMap Expansion

Currently 8 detectors use FunctionContextMap roles. Ensure all detectors receive it via AnalysisContext.

**Key role-based gating patterns:**
- `role == Test` → skip or severity=Info
- `role == Utility` → raise thresholds (high coupling expected)
- `role == Hub` → raise thresholds (central by design)
- `role == EntryPoint` → skip dead code checks (exported API)
- `severity_multiplier()` → scale finding severity by role

---

## Phase 1: DeadStoreDetector Rewrite

**Current:** 396 lines of regex. `^\s*(let|var|const...)` + line-by-line `is_used_after()` text search.

**Rewrite to:** ValueStore-based assignment/read analysis.

### Architecture

1. **Get assignments from ValueStore** — `value_store.assignments_in(func_qn)` already contains every variable assignment with name, SymbolicValue, line number. Extracted during tree-sitter parsing. No regex needed.

2. **Collect variable reads from AST** — Add a lightweight pass during parsing to collect variable *read* locations. Walk identifier nodes that aren't on the LHS of assignment. Store as `reads: HashMap<String, Vec<u32>>` per function (variable name → read line numbers).

3. **Dead store detection** — Compare assignment lines to read lines. An assignment is dead if:
   - No read of that variable exists between this assignment and the next assignment to the same variable (or end of function)
   - The variable is not returned or passed to a callee

4. **Module-level exemptions** — Skip assignments where:
   - `node.is_exported()` is true
   - Variable name appears in `__all__` (already tracked)
   - Variable doesn't start with `_` in Python (public by convention)
   - File is `__init__.py`, `conf.py`, `config.py`, `settings.py`

5. **`self.x = param` handling** — ValueStore's `SymbolicValue::FieldAccess` represents attribute stores. If RHS is a Parameter and LHS is FieldAccess, this is a store, not a dead assignment.

6. **Role-based gating** — ContextHMM `role == Test` → skip. `role == Utility` → lower confidence.

### Expected Impact

- 396 lines → ~150 lines
- Flask FPs: 95 → ~5
- Eliminates: config variable FPs, `__init__` parameter FPs, module-level assignment FPs

---

## Phase 2: DeadCodeDetector Overhaul

**Current:** 1,113 lines with 200+ hardcoded entry point patterns, 30 magic methods, 60+ framework auto-load patterns, dispatch paths.

**Overhaul to:** Graph flag checks + ContextHMM roles.

### Replace Pattern Lists With Infrastructure

| Current Pattern List | Lines | Replace With | Infrastructure |
|---|---|---|---|
| ENTRY_POINTS (50+ patterns) | ~50 | `FunctionContextMap.role == EntryPoint` | Already computed from graph metrics |
| MAGIC_METHODS (30 entries) | ~30 | `FunctionFeatures.is_python_dunder` | ContextHMM feature |
| FRAMEWORK_AUTO_LOAD_PATTERNS (60+ paths) | ~70 | `FileContext::HandlerFile` | ContextHMM FileContext |
| DISPATCH_PATHS (40+ paths) | ~40 | `ContentClassifier.is_non_production_path()` + `in_handler_path` | Already built |
| CALLBACK_PATTERNS (30+ patterns) | ~30 | `node.address_taken()` flag | Already in graph |

### Core Logic Stays

The fundamental check remains: `call_fan_in(func_qn) == 0 → possibly dead`. But exemptions collapse from 200+ patterns to ~20 lines of flag checks:

```
Skip if ANY of:
  - node.is_exported()
  - node.has_decorators()        (registered at runtime)
  - node.address_taken()         (used as callback)
  - role == EntryPoint           (exported API)
  - role == Test                 (test function)
  - features.is_python_dunder    (implicit caller)
  - file_context == HandlerFile  (framework auto-load)
  - file_context == TestFile     (test infrastructure)
```

### Expected Impact

- 1,113 lines → ~400 lines
- Flask FPs: 82 → ~10
- Eliminates: decorator FPs, dunder method FPs, public API FPs, framework callback FPs

---

## Phase 3: AIMissingTestsDetector Rewrite

**Current:** 707 lines. Name matching (`test_<func>` pattern) + complexity threshold (≥5 complexity, ≥15 LOC).

**Rewrite to:** Graph reachability from test functions.

### Architecture

1. **Build test function set** — Collect all functions where `FunctionContextMap.role == Test` OR `FunctionFeatures.has_test_prefix`. These are test entry points.

2. **Forward BFS from tests** — For each test function, traverse `graph.get_callees()` recursively (depth limit ~5). Every function reachable from a test function has test coverage. Store as `tested_functions: HashSet<String>`.

3. **Flag untested complex functions** — A function is "missing tests" only if ALL of:
   - NOT in `tested_functions`
   - `complexity > resolver.warn(MetricKind::Complexity, 5.0)` (adaptive)
   - `role != Utility && role != Leaf` (simple utilities don't need dedicated tests)
   - `node.is_exported() || caller_modules >= 2` (important enough to test)

4. **ContentClassifier gating** — Skip `is_test_infrastructure()` files (fixtures, mocks, conftest).

5. **Confidence scaling** — Function 1 hop from tested function → low confidence. Completely isolated from test call graph → high confidence.

### Expected Impact

- 707 lines → ~200 lines
- Flask FPs: 38 → ~3
- Eliminates: indirectly-tested function FPs, utility function FPs

---

## Phase 4: UnreachableCodeDetector Refactor

**Current:** 1,544 lines. Two overlapping concerns: (1) regex for code after return/throw/raise, (2) dead function detection (fan_in == 0) duplicating DeadCodeDetector. Plus 300+ lines of conditional compilation handling.

**Refactor to:** AST-only control flow, remove dead function overlap.

### Architecture

1. **Remove dead function detection entirely** — DeadCodeDetector handles fan_in == 0. UnreachableCodeDetector should ONLY detect code-after-return-statement. This eliminates the duplicate ENTRY_POINT_PATTERNS list and all dunder/framework FPs.

2. **Replace regex with AST control flow** — During parsing, add a lightweight pass:
   - Walk function body statements in order via tree-sitter
   - When hitting `return_statement`, `throw_statement`, `raise_statement`, `break_statement` node, check if there are sibling statements after it at the same scope level
   - This is scope-aware — code after `return` inside an `if` branch doesn't make the `else` branch unreachable
   - Store unreachable ranges in a side table or ExtraProps

3. **Conditional compilation at parse time** — Rust `#[cfg(...)]` already in `ExtraProps.decorators`. C/C++ `#ifdef` blocks tagged during parsing. Python `if __name__` detected by parser. No runtime heuristic scanning.

4. **ContextHMM gating** — Skip functions in test files (unreachable test code is harmless).

### Expected Impact

- 1,544 lines → ~200 lines (most logic moves to parsers)
- Flask FPs: 32 → ~3
- Eliminates: ALL dunder method FPs, ALL framework callback FPs (those were from dead function overlap)

---

## Phase 5: LazyClassDetector Refinement

**Current:** 1,451 lines with 100+ EXCLUDE_PATTERNS.

**Refinement:** Replace pattern matching with role + metric analysis.

### Changes

1. **Replace EXCLUDE_PATTERNS with FunctionContextMap role analysis** — Instead of checking class name for "adapter", "wrapper", "proxy", check the class's methods' roles. If any method has `role == Hub` or high betweenness, the class is architecturally important regardless of size.

2. **Use ContextHMM FileContext** — Classes in `HandlerFile` or framework auto-load paths are intentionally small. Skip.

3. **Use ThresholdResolver** — `max_methods` threshold adapts to codebase. Codebase with many small classes (Rust, Go) gets higher threshold automatically via `resolver.warn(MetricKind::ClassMethodCount, 3.0)`.

4. **Keep language-specific logic** — Rust impl-block counting, Go/C#/Java exclusions from Phase A work are good. No changes.

5. **Trim EXCLUDE_PATTERNS** — Keep only unambiguous patterns: "exception", "error", "test", "mock", "stub", "fixture". Remove role-detectable patterns (50+ entries).

### Expected Impact

- EXCLUDE_PATTERNS: 100+ → ~20
- Flask FPs: 13 → ~2
- More maintainable — new frameworks don't require pattern list updates

---

## Phase 6: ShotgunSurgeryDetector Refinement

**Current:** 606 lines with 60+ SKIP_METHODS, 25+ UTILITY_PREFIXES.

**Refinement:** Role-aware threshold scaling.

### Changes

1. **Scale thresholds by FunctionContextMap.role** — `role == Hub` or `role == Utility` → multiply min_callers threshold by 3x. These are EXPECTED to have high coupling.

2. **Use `severity_multiplier()`** — Already exists. Hub = 1.2x, Utility = 0.5x. Invert for thresholds: `effective_min_callers = base / severity_multiplier()`.

3. **Replace SKIP_METHODS + UTILITY_PREFIXES with ContextHMM** — `FunctionFeatures.looks_like_utility()` combines naming, path, and graph signals. Replaces 85+ static patterns.

4. **Use pre-computed `caller_module_spread`** — `graph.caller_module_spread(qn)` already exists. Replace manual module counting.

### Expected Impact

- 606 lines → ~250 lines
- Flask FPs: 9 → ~1
- Hub classes (Flask, Scaffold, Blueprint) auto-exempted by role

---

## Execution Order

**Phase 0 → 1 → 2 → 3 → 4 → 5 → 6**

Phase 0 (infrastructure wiring) must come first — all subsequent phases depend on ContextHMM, ThresholdResolver, and expanded FunctionContextMap being available.

Phases 1-4 are the heavy rewrites (DeadStore, DeadCode, AIMissingTests, UnreachableCode). Phases 5-6 are lighter refinements.

## Success Metrics

| Metric | Before (Flask) | Target | Measurement |
|--------|---------------|--------|-------------|
| DeadStoreDetector precision | 0% (0/95) | ≥70% | Re-run Flask benchmark |
| DeadCodeDetector precision | 0% (82/82) | ≥70% | Re-run Flask benchmark |
| AIMissingTestsDetector precision | 0% (0/38) | ≥70% | Re-run Flask benchmark |
| UnreachableCodeDetector precision | 3% (1/33) | ≥70% | Re-run Flask benchmark |
| LazyClassDetector precision (Flask) | 0% (0/13) | ≥70% | Re-run Flask benchmark |
| ShotgunSurgeryDetector precision | 0% (0/9) | ≥70% | Re-run Flask benchmark |
| Overall Flask precision | 9.1% | ≥60% | Re-run Flask benchmark |
| Self-analysis total findings | 1,657 | <1,000 | Self-analysis |
| Self-analysis grade | C+ (80.0) | B (83+) | Self-analysis |
| Total code lines (6 detectors) | 5,817 | <2,000 | `wc -l` |
| ContextHMM adoption | 0 detectors | 6+ | Grep usage |
| ThresholdResolver adoption | 0 detectors | 6+ | Grep usage |

## Files Affected

### Phase 0 (infrastructure wiring)
- Modify: `src/detectors/analysis_context.rs` — add ContextHMM + ThresholdResolver
- Modify: `src/cli/analyze/mod.rs` — wire calibration into pipeline
- Modify: `src/detectors/mod.rs` — pass expanded context to detectors

### Phase 1 (DeadStore rewrite)
- Rewrite: `src/detectors/dead_store.rs`
- Modify: `src/parsers/python.rs` — add variable read collection
- Modify: `src/parsers/rust.rs` — add variable read collection (if needed)
- Modify: `src/values/store.rs` — expose reads API

### Phase 2 (DeadCode overhaul)
- Rewrite: `src/detectors/dead_code/mod.rs`
- Modify: `src/detectors/dead_code/tests.rs`

### Phase 3 (AIMissingTests rewrite)
- Rewrite: `src/detectors/ai_missing_tests.rs`

### Phase 4 (UnreachableCode refactor)
- Rewrite: `src/detectors/unreachable_code.rs`
- Modify: `src/parsers/python.rs` — add unreachable range detection
- Modify: `src/parsers/rust.rs` — add unreachable range detection

### Phase 5 (LazyClass refinement)
- Modify: `src/detectors/lazy_class.rs`

### Phase 6 (ShotgunSurgery refinement)
- Modify: `src/detectors/shotgun_surgery.rs`

### Validation
- Modify: `benchmark/flask/labels.json` — update labels after re-analysis
- Run: `cargo test benchmark_precision_flask -- --ignored`

## Risks

| Risk | Mitigation |
|------|------------|
| Over-suppression (hiding TPs) | Flask benchmark catches precision drops; run before/after |
| ContextHMM misclassification (8% error rate) | Use as confidence signal, not hard gate. ContextHMM reduces severity, doesn't suppress. |
| ThresholdResolver baseline poisoning | Guardrails: can't go below default or above 5x default. Min 40 samples. |
| Parser changes break existing tests | Run full `cargo test` after each parser modification |
| DeadCode + UnreachableCode deduplication | Clear ownership: DeadCode = fan_in==0, Unreachable = code-after-return only |
