# Detector Trait Refactor & Graph-Integrated FP Reduction — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Unify the detector trait to a single `detect(ctx: &AnalysisContext)` entry point, enrich AnalysisContext with graph-derived data, and migrate top detectors to use context for FP reduction.

**Architecture:** Replace the 3-method dispatch (detect/detect_with_context/detect_ctx) with one method receiving `&AnalysisContext`. Add reachability, public API, module metrics, class cohesion, and decorator index to AnalysisContext. Migrate top 8 detectors to use enriched context.

**Tech Stack:** Rust, petgraph, rayon

---

## Phase 1: Trait Refactor (Tasks 1-5)

### Task 1: Add AnalysisContext::test() Constructor

**Files:**
- Modify: `src/detectors/analysis_context.rs`

**Step 1: Write the failing test**

Add a test in the existing `#[cfg(test)] mod tests` block:

```rust
#[test]
fn test_analysis_context_test_constructor() {
    let graph = GraphStore::in_memory();
    let ctx = AnalysisContext::test(&graph);
    assert_eq!(ctx.repo_path(), Path::new(""));
    assert!(ctx.functions.is_empty());
    assert!(ctx.hmm_classifications.is_empty());
}
```

**Step 2: Run test to verify it fails**

Run: `cargo test --lib "analysis_context::tests::test_analysis_context_test_constructor"`
Expected: FAIL — `test` method doesn't exist on AnalysisContext.

**Step 3: Implement AnalysisContext::test()**

Add to the `impl<'g> AnalysisContext<'g>` block:

```rust
/// Create a minimal AnalysisContext for unit tests.
///
/// Fills all fields with empty/default values. Tests only need
/// to provide a graph — everything else is zeroed out.
#[cfg(test)]
pub fn test(graph: &'g dyn GraphQuery) -> Self {
    use std::collections::HashMap;
    let (det_ctx, _) = DetectorContext::build(graph, &[], None, Path::new(""));
    Self {
        graph,
        files: Arc::new(FileIndex::new(vec![])),
        functions: Arc::new(HashMap::new()),
        taint: Arc::new(CentralizedTaintResults {
            cross_function: HashMap::new(),
            intra_function: HashMap::new(),
        }),
        detector_ctx: Arc::new(det_ctx),
        hmm_classifications: Arc::new(HashMap::new()),
        resolver: Arc::new(crate::calibrate::ThresholdResolver::default()),
    }
}
```

Also add a variant that accepts file data for detectors that scan file content:

```rust
/// Create a test AnalysisContext with file content pre-loaded.
#[cfg(test)]
pub fn test_with_files(
    graph: &'g dyn GraphQuery,
    file_data: Vec<(PathBuf, Arc<str>, ContentFlags)>,
) -> Self {
    use std::collections::HashMap;
    let (det_ctx, _) = DetectorContext::build(graph, &[], None, Path::new(""));
    Self {
        graph,
        files: Arc::new(FileIndex::new(file_data)),
        functions: Arc::new(HashMap::new()),
        taint: Arc::new(CentralizedTaintResults {
            cross_function: HashMap::new(),
            intra_function: HashMap::new(),
        }),
        detector_ctx: Arc::new(det_ctx),
        hmm_classifications: Arc::new(HashMap::new()),
        resolver: Arc::new(crate::calibrate::ThresholdResolver::default()),
    }
}
```

**Step 4: Run test to verify it passes**

Run: `cargo test --lib "analysis_context::tests::test_analysis_context_test_constructor"`
Expected: PASS

**Step 5: Commit**

```bash
git add src/detectors/analysis_context.rs
git commit -m "feat: add AnalysisContext::test() constructor for unit tests"
```

---

### Task 2: Change Detector Trait Signature

This is the core breaking change. Change `detect()` to take `&AnalysisContext` and remove the legacy methods.

**Files:**
- Modify: `src/detectors/base.rs`
- Modify: `src/detectors/engine.rs`

**Step 1: Update the trait definition in base.rs**

Replace the current trait methods. The new trait should look like:

```rust
pub trait Detector: Send + Sync {
    fn name(&self) -> &'static str;
    fn description(&self) -> &'static str;

    /// Primary detection entry point.
    ///
    /// Receives the unified AnalysisContext containing:
    /// - `ctx.graph` — the code knowledge graph (GraphQuery)
    /// - `ctx.files` — pre-indexed file content (FileIndex)
    /// - `ctx.functions` — pre-computed function roles and metrics
    /// - `ctx.taint` — pre-computed taint analysis results
    /// - `ctx.detector_ctx` — pre-built callers/callees/class hierarchy
    /// - `ctx.hmm_classifications` — HMM-based role classifications
    /// - `ctx.resolver` — adaptive threshold resolver
    fn detect(&self, ctx: &super::analysis_context::AnalysisContext) -> Result<Vec<Finding>>;

    // Keep all other methods unchanged:
    fn category(&self) -> &'static str { "code_smell" }
    fn config(&self) -> Option<&DetectorConfig> { None }
    fn scope(&self) -> DetectorScope { DetectorScope::GraphWide }
    fn detector_scope(&self) -> DetectorScope { ... }
    fn requires_graph(&self) -> bool { true }
    fn is_dependent(&self) -> bool { false }
    fn dependencies(&self) -> Vec<&'static str> { vec![] }
    fn file_extensions(&self) -> &'static [&'static str] { &[] }
    fn content_requirements(&self) -> super::detector_context::ContentFlags { ... }

    // KEEP for now — security detectors still need pre-injection.
    // Will migrate to ctx.taint access in Phase 3.
    fn set_precomputed_taint(&self, _cross: ..., _intra: ...) {}
    fn taint_category(&self) -> Option<super::taint::TaintCategory> { None }
}
```

**Remove entirely:**
- `fn detect_ctx()` — folded into `detect()`
- `fn detect_with_context()` — folded into `detect()`
- `fn uses_context()` — no longer needed
- `fn set_detector_context()` — context comes via `detect()` parameter

**Step 2: Update engine.rs**

In `run_single_detector()`, simplify the dispatch:

```rust
// BEFORE (3-way dispatch):
if let Some(ctx) = analysis_ctx {
    detector.detect_ctx(ctx)
} else if detector.uses_context() {
    detector.detect_with_context(graph, files, &contexts_clone)
} else {
    detector.detect(graph, files)
}

// AFTER (1 call):
detector.detect(ctx)
```

The engine must ALWAYS have an AnalysisContext now. The `build_analysis_ctx()` method already ensures this — it returns `None` only if pre-computed data is missing, which shouldn't happen in normal operation. Add a fallback that creates a minimal context if needed.

Also remove `inject_detector_context()` calls from `inject_gd_precomputed()` — detectors no longer store context in OnceLock fields.

**Step 3: Verify compilation fails with clear errors**

Run: `cargo check 2>&1 | head -50`
Expected: ~99 compile errors, all of the form "expected 1 parameter, found 2" or similar — one per detector implementing the old `detect(graph, files)` signature.

**Step 4: Commit the trait change (broken build is OK)**

```bash
git add src/detectors/base.rs src/detectors/engine.rs
git commit -m "refactor: unify Detector trait to single detect(&AnalysisContext) entry point

BREAKING: All detector implementations must update their detect() signature.
Removes detect_ctx(), detect_with_context(), uses_context(), set_detector_context().
Context is now always available via the AnalysisContext parameter."
```

---

### Task 3: Migrate All Detectors — Mechanical Signature Update

This is the largest task. Update all ~99 detect() implementations to the new signature. This is purely mechanical — each detector just extracts `ctx.graph` and creates a FileProvider shim.

**Files:**
- Modify: ALL 87 detector files in `src/detectors/`

**Approach:** For each detector, the change is:

```rust
// BEFORE:
fn detect(
    &self,
    graph: &dyn crate::graph::GraphQuery,
    _files: &dyn crate::detectors::file_provider::FileProvider,
) -> Result<Vec<Finding>> {
    // ... existing logic using `graph` and `_files` ...
}

// AFTER:
fn detect(
    &self,
    ctx: &crate::detectors::analysis_context::AnalysisContext,
) -> Result<Vec<Finding>> {
    let graph = ctx.graph;
    let _files = &ctx.as_file_provider();
    // ... existing logic unchanged ...
}
```

**For the 6 detectors with detect_ctx():**
- `ai_missing_tests.rs`: Merge detect_ctx() logic INTO detect(). Remove detect_ctx() override.
- `dead_code/mod.rs`: Same — merge detect_ctx() into detect(). Remove set_detector_context() override.
- `dead_store.rs`: Same.
- `lazy_class.rs`: Call `detect_inner(ctx.graph, Some(ctx))` directly. Remove detect_ctx() override.
- `shotgun_surgery.rs`: Same as lazy_class. Remove set_detector_context() override.
- `unreachable_code.rs`: Same.

**For the 4 detectors with detect_with_context():**
- `architectural_bottleneck.rs`: Move detect_with_context() logic into detect(). Access `ctx.functions` directly. Remove uses_context() override.
- `degree_centrality.rs`: Same.
- `influential_code.rs`: Same.
- `hierarchical_surprisal.rs`: Same.

**For the 5 detectors with set_detector_context():**
- `dead_code/mod.rs`: Remove OnceLock<Arc<DetectorContext>> field. Access via `ctx.detector_ctx`.
- `god_class.rs`: Same.
- `path_traversal.rs`: Same.
- `regex_in_loop.rs`: Same.
- `shotgun_surgery.rs`: Same.

**Strategy:** This task should be split across parallel subagents by category:
- **Batch A** (30 files): Security detectors (command_injection, eval_detector, sql_injection, xss, xxe, ssrf, path_traversal, nosql_injection, cors_misconfig, insecure_*, jwt_weak, log_injection, prototype_pollution, cleartext_credentials, secrets, unsafe_template, hardcoded_ips, hardcoded_timeout)
- **Batch B** (25 files): Code quality + smells (magic_numbers, deep_nesting, long_methods, long_parameter, empty_catch, boolean_trap, broad_exception, commented_code, debug_code, inconsistent_returns, mutable_default_args, single_char_names, string_concat_loop, todo_scanner, wildcard_imports, large_files, global_variables, missing_docstrings, missing_await, unhandled_promise, implicit_coercion, sync_in_async)
- **Batch C** (20 files): Graph-based detectors + AI (god_class, feature_envy, data_clumps, inappropriate_intimacy, lazy_class, message_chain, middle_man, refused_bequest, dead_code, dead_store, core_utility, shotgun_surgery, circular_dependency, architectural_bottleneck, degree_centrality, influential_code, module_cohesion, duplicate_code, callback_hell, unreachable_code)
- **Batch D** (15 files): AI + ML + Rust + framework + remaining (ai_*, ml_smells/*, rust_smells/*, react_hooks, django_security, express_security, test_in_production, surprisal, hierarchical_surprisal, generator_misuse, infinite_loop, unused_imports, dep_audit, gh_actions, regex_dos, regex_in_loop, pickle_detector, n_plus_one)

**Verification after each batch:**

Run: `cargo check`
Expected: Compilation succeeds for migrated files. Remaining files still error.

**Final verification:**

Run: `cargo test --lib --tests`
Expected: All tests pass. Some tests may need updating to use `AnalysisContext::test()`.

**Step N: Commit after all batches**

```bash
git add src/detectors/
git commit -m "refactor: migrate all 99 detectors to unified detect(&AnalysisContext) signature"
```

---

### Task 4: Update Tests to Use AnalysisContext::test()

**Files:**
- Modify: All detector test modules that call `detect()` directly

**Approach:** Tests currently do:
```rust
let graph = GraphStore::in_memory();
// ... add nodes/edges ...
let files = TestFileProvider::new(...);
let findings = detector.detect(&graph, &files).unwrap();
```

Change to:
```rust
let graph = GraphStore::in_memory();
// ... add nodes/edges ...
let ctx = AnalysisContext::test(&graph);
// OR for file-content tests:
let ctx = AnalysisContext::test_with_files(&graph, file_data);
let findings = detector.detect(&ctx).unwrap();
```

**Verification:**

Run: `cargo test --lib --tests`
Expected: ALL tests pass.

**Commit:**

```bash
git add src/detectors/
git commit -m "test: update all detector tests to use AnalysisContext::test()"
```

---

### Task 5: Clean Up Removed Infrastructure

Remove dead code left over from the trait refactor.

**Files:**
- Modify: `src/detectors/base.rs` — remove any remaining references to old methods
- Modify: `src/detectors/engine.rs` — remove `inject_detector_context` loops, simplify dispatch
- Modify: `src/detectors/analysis_context.rs` — remove `as_file_provider()` backward compat shim (if no longer needed) OR keep for detectors still using FileProvider pattern internally
- Modify: `src/detectors/file_provider.rs` — evaluate if FileProvider trait is still needed
- Delete or modify: `src/detectors/streaming_engine.rs` — if it references old API

**Verification:**

Run: `cargo test --lib --tests && cargo clippy`
Expected: All pass, no dead code warnings for removed methods.

**Commit:**

```bash
git add src/detectors/
git commit -m "refactor: clean up removed trait methods and dead infrastructure code"
```

---

## Phase 2: Enrich AnalysisContext (Tasks 6-10)

### Task 6: Add ReachabilityIndex

Compute which functions are reachable from entry points via BFS on the call graph.

**Files:**
- Create: `src/detectors/reachability.rs`
- Modify: `src/detectors/analysis_context.rs`
- Modify: `src/detectors/engine.rs` (add to pre-compute)
- Modify: `src/detectors/mod.rs` (add mod declaration)

**Step 1: Write the failing test**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_reachability_from_entry_points() {
        // Build graph: main -> A -> B, C (unreachable)
        let graph = GraphStore::in_memory();
        // Add function nodes: main, A, B, C
        // Add call edges: main->A, A->B
        // Mark main as entry point (exported, in_degree=0)

        let reachable = ReachabilityIndex::build(&graph);
        assert!(reachable.is_reachable("main"));
        assert!(reachable.is_reachable("A"));
        assert!(reachable.is_reachable("B"));
        assert!(!reachable.is_reachable("C"));
    }

    #[test]
    fn test_reachability_handles_cycles() {
        // A -> B -> A (cycle), C unreachable
        // Both A and B are reachable if either is an entry point
    }
}
```

**Step 2: Implement ReachabilityIndex**

```rust
use std::collections::HashSet;
use crate::graph::GraphQuery;

pub struct ReachabilityIndex {
    reachable: HashSet<String>,
}

impl ReachabilityIndex {
    pub fn build(graph: &dyn GraphQuery) -> Self {
        let functions = graph.get_functions_shared();
        let interner = graph.interner();

        // Find entry points: exported OR in_degree == 0 OR is_test
        let mut entry_points: Vec<&str> = Vec::new();
        for func in functions.iter() {
            let qn = func.qn(interner);
            if func.is_exported() || graph.call_fan_in(qn) == 0 {
                entry_points.push(qn);
            }
        }

        // BFS from all entry points simultaneously
        let mut reachable = HashSet::new();
        let mut queue: std::collections::VecDeque<String> = entry_points
            .into_iter()
            .map(|s| s.to_string())
            .collect();

        while let Some(qn) = queue.pop_front() {
            if !reachable.insert(qn.clone()) {
                continue; // already visited
            }
            for callee in graph.get_callees(&qn) {
                let cqn = callee.qn(interner).to_string();
                if !reachable.contains(&cqn) {
                    queue.push_back(cqn);
                }
            }
        }

        Self { reachable }
    }

    pub fn is_reachable(&self, qn: &str) -> bool {
        self.reachable.contains(qn)
    }

    pub fn reachable_count(&self) -> usize {
        self.reachable.len()
    }
}
```

**Step 3: Wire into AnalysisContext**

Add field: `pub reachability: Arc<ReachabilityIndex>`
Add accessor: `pub fn is_reachable(&self, qn: &str) -> bool`
Update `AnalysisContext::test()` to include default (empty reachability).

**Step 4: Wire into pre-compute phase in engine.rs**

Add ReachabilityIndex::build() call in `precompute_gd_startup()`, running in parallel alongside existing threads.

**Verification:**

Run: `cargo test --lib "reachability"`
Expected: All reachability tests pass.

**Commit:**

```bash
git add src/detectors/reachability.rs src/detectors/analysis_context.rs src/detectors/engine.rs src/detectors/mod.rs
git commit -m "feat: add ReachabilityIndex to AnalysisContext — BFS from entry points"
```

---

### Task 7: Add PublicApiSet

**Files:**
- Modify: `src/detectors/analysis_context.rs`
- Modify: `src/detectors/engine.rs`

**Implementation:**

Compute `HashSet<String>` of all function/class qualified names where `is_exported() || is_public()`.

```rust
pub fn build_public_api(graph: &dyn GraphQuery) -> HashSet<String> {
    let interner = graph.interner();
    let functions = graph.get_functions_shared();
    let classes = graph.get_classes_shared();

    let mut api = HashSet::new();
    for func in functions.iter() {
        if func.is_exported() || func.is_public() {
            api.insert(func.qn(interner).to_string());
        }
    }
    for class in classes.iter() {
        if func.is_exported() || class.is_public() {
            api.insert(class.qn(interner).to_string());
        }
    }
    api
}
```

Add to AnalysisContext: `pub public_api: Arc<HashSet<String>>`
Add accessor: `pub fn is_public_api(&self, qn: &str) -> bool`

**Tests:** Verify exported functions are in set, private functions are not.

**Commit:**

```bash
git commit -m "feat: add PublicApiSet to AnalysisContext"
```

---

### Task 8: Add ModuleMetrics

**Files:**
- Create: `src/detectors/module_metrics.rs`
- Modify: `src/detectors/analysis_context.rs`
- Modify: `src/detectors/engine.rs`
- Modify: `src/detectors/mod.rs`

**Implementation:**

```rust
pub struct ModuleMetrics {
    pub function_count: usize,
    pub class_count: usize,
    pub incoming_calls: usize,
    pub outgoing_calls: usize,
    pub internal_calls: usize,
}

impl ModuleMetrics {
    pub fn coupling(&self) -> f64 {
        let total = self.incoming_calls + self.outgoing_calls + self.internal_calls;
        if total == 0 { return 0.0; }
        (self.incoming_calls + self.outgoing_calls) as f64 / total as f64
    }

    pub fn cohesion(&self) -> f64 {
        let total = self.incoming_calls + self.outgoing_calls + self.internal_calls;
        if total == 0 { return 1.0; }
        self.internal_calls as f64 / total as f64
    }
}

pub fn build_module_metrics(graph: &dyn GraphQuery) -> HashMap<String, ModuleMetrics> {
    // Iterate all call edges, classify each as internal or cross-module
    // Module = directory component of file path
}
```

Add to AnalysisContext: `pub module_metrics: Arc<HashMap<String, ModuleMetrics>>`
Add accessor: `pub fn module_coupling(&self, module: &str) -> f64`

**Tests:** Build graph with 2 modules, verify coupling/cohesion calculations.

**Commit:**

```bash
git commit -m "feat: add ModuleMetrics to AnalysisContext"
```

---

### Task 9: Add ClassCohesion (LCOM4)

**Files:**
- Modify: `src/detectors/analysis_context.rs`
- Modify: `src/detectors/engine.rs`

**Implementation:**

For each class, compute LCOM4: number of connected components when methods are connected by shared field access. Higher = less cohesive.

Since we don't have field-level access edges, approximate using call graph: methods that call each other are connected. Count components.

```rust
pub fn build_class_cohesion(graph: &dyn GraphQuery) -> HashMap<String, f64> {
    let interner = graph.interner();
    let classes = graph.get_classes_shared();
    let mut cohesion = HashMap::new();

    for class in classes.iter() {
        let file = class.path(interner);
        let methods: Vec<_> = graph.get_functions_in_file(file)
            .into_iter()
            .filter(|f| f.line_start >= class.line_start && f.line_end <= class.line_end)
            .collect();

        if methods.len() <= 1 {
            cohesion.insert(class.qn(interner).to_string(), 1.0);
            continue;
        }

        // Build method adjacency (union-find for connected components)
        // Methods connected if one calls the other
        let components = count_connected_components(&methods, graph, interner);
        let lcom = components as f64 / methods.len() as f64;
        cohesion.insert(class.qn(interner).to_string(), lcom);
    }

    cohesion
}
```

Add to AnalysisContext: `pub class_cohesion: Arc<HashMap<String, f64>>`
Add accessor: `pub fn class_cohesion(&self, qn: &str) -> Option<f64>`

**Commit:**

```bash
git commit -m "feat: add ClassCohesion (LCOM4 approximation) to AnalysisContext"
```

---

### Task 10: Add DecoratorIndex

**Files:**
- Modify: `src/detectors/analysis_context.rs`
- Modify: `src/detectors/engine.rs`

**Implementation:**

Pre-parse the comma-separated decorator strings from ExtraProps into Vec<String>.

```rust
pub fn build_decorator_index(graph: &dyn GraphQuery) -> HashMap<String, Vec<String>> {
    let interner = graph.interner();
    let functions = graph.get_functions_shared();
    let mut index = HashMap::new();

    for func in functions.iter() {
        if func.has_decorators() {
            let qn = func.qn(interner);
            if let Some(props) = graph.extra_props(func.qualified_name) {
                if let Some(decs) = props.decorators {
                    let dec_str = interner.resolve(&decs);
                    let parsed: Vec<String> = dec_str
                        .split(',')
                        .map(|s| s.trim().to_string())
                        .filter(|s| !s.is_empty())
                        .collect();
                    if !parsed.is_empty() {
                        index.insert(qn.to_string(), parsed);
                    }
                }
            }
        }
    }
    index
}
```

Add to AnalysisContext: `pub decorator_index: Arc<HashMap<String, Vec<String>>>`
Add accessor: `pub fn decorators(&self, qn: &str) -> &[String]`
Add convenience: `pub fn has_decorator(&self, qn: &str, decorator: &str) -> bool`

**Commit:**

```bash
git commit -m "feat: add DecoratorIndex to AnalysisContext"
```

---

### Task 10.5: Add Convenience Methods to AnalysisContext

**Files:**
- Modify: `src/detectors/analysis_context.rs`

Add the following convenience methods:

```rust
impl<'g> AnalysisContext<'g> {
    /// Check if function is an HMM-classified handler with sufficient confidence.
    pub fn is_handler(&self, qn: &str) -> bool {
        self.hmm_role(qn).map_or(false, |(role, conf)| {
            role == crate::detectors::context_hmm::FunctionContext::Handler && conf > 0.5
        })
    }

    /// Check if function is infrastructure (utility, hub, or handler).
    /// Infrastructure code has different expectations — FP reduction signal.
    pub fn is_infrastructure(&self, qn: &str) -> bool {
        self.is_utility_function(qn)
            || self.is_hub_function(qn)
            || self.is_handler(qn)
    }

    /// Get adaptive threshold, falling back to default.
    pub fn threshold(&self, kind: crate::calibrate::MetricKind, default: f64) -> f64 {
        self.resolver.warn(kind, default)
    }

    pub fn threshold_usize(&self, kind: crate::calibrate::MetricKind, default: usize) -> usize {
        self.resolver.warn_usize(kind, default)
    }
}
```

**Commit:**

```bash
git commit -m "feat: add convenience methods to AnalysisContext (is_handler, is_infrastructure, threshold)"
```

---

## Phase 3: Targeted Detector Upgrades (Tasks 11-18)

Each task is independent and modifies a single detector to use the enriched context.

### Task 11: CoreUtilityDetector — Role-Based Filtering

**Files:**
- Modify: `src/detectors/core_utility.rs`

**Current:** Flags functions with high fan-in as "core utilities". Produces 100 findings.

**Changes:**
- Skip functions with `FunctionRole::Hub` or `FunctionRole::Orchestrator` — these aren't utilities, they're critical infrastructure
- Use `ctx.module_metrics()` to distinguish true cross-module utilities from functions called many times within one module
- Use `ctx.is_reachable()` to only flag reachable code
- Use adaptive threshold for fan-in cutoff

**Expected reduction:** 100 → ~30-50

**Commit:**

```bash
git commit -m "feat: CoreUtilityDetector uses role-based filtering and module metrics"
```

---

### Task 12: MagicNumbersDetector — Test/Role Exemption

**Files:**
- Modify: `src/detectors/magic_numbers.rs`

**Current:** Regex-based number detection. 99 findings.

**Changes:**
- Look up containing function via `ctx.graph.find_function_at(file, line)`
- If function is test (`ctx.is_test_function(qn)`) → skip entirely
- If function is infrastructure (`ctx.is_infrastructure(qn)`) → reduce severity to Info
- Use adaptive threshold for minimum digit count

**Expected reduction:** 99 → ~40-60

**Commit:**

```bash
git commit -m "feat: MagicNumbersDetector exempts test functions and infrastructure code"
```

---

### Task 13: LongMethodsDetector — Handler/Role Exemption

**Files:**
- Modify: `src/detectors/long_methods.rs`

**Current:** Flags functions > 50 lines. Already detects orchestrators. 74 findings.

**Changes:**
- If HMM handler (`ctx.is_handler(qn)`) → double threshold (handlers are legitimately long)
- If test function (`ctx.is_test_function(qn)`) → cap severity at Low
- Use adaptive threshold from `ctx.threshold(MetricKind::MethodLength, 50.0)`
- If function is not reachable (`!ctx.is_reachable(qn)`) → reduce severity

**Expected reduction:** 74 → ~30-40

**Commit:**

```bash
git commit -m "feat: LongMethodsDetector uses HMM handler exemption and adaptive thresholds"
```

---

### Task 14: LongParameterListDetector — Constructor/Hub Exemption

**Files:**
- Modify: `src/detectors/long_parameter.rs`

**Current:** Flags functions with many params. 51 findings.

**Changes:**
- If function name contains "new", "init", "create", "build", "constructor" → double threshold (constructors take many params)
- If `FunctionRole::Hub` → increase threshold by 50% (hubs aggregate)
- If test function → cap severity at Low
- Use adaptive threshold from resolver

**Expected reduction:** 51 → ~20-30

**Commit:**

```bash
git commit -m "feat: LongParameterListDetector exempts constructors and hub functions"
```

---

### Task 15: DuplicateCodeDetector — Test/Generated Exemption

**Files:**
- Modify: `src/detectors/duplicate_code.rs`

**Current:** AST fingerprint-based duplication. 50 findings.

**Changes:**
- Skip findings where both duplicates are in test files (`is_test_path()`)
- Skip findings in files classified as fixture/generated (`content_classifier`)
- If both functions are infrastructure → reduce severity to Low
- Use adaptive threshold for similarity score

**Expected reduction:** 50 → ~20-30

**Commit:**

```bash
git commit -m "feat: DuplicateCodeDetector skips test files and generated code"
```

---

### Task 16: CloneInHotPathDetector — Reachability/Test Gating

**Files:**
- Modify: `src/detectors/rust_smells/clone_hot_path.rs`

**Current:** Flags .clone() calls in Rust. 43 findings.

**Changes:**
- If function is not reachable from entry points → skip (dead code, don't flag clone)
- If function is test → skip entirely
- If function is infrastructure/utility → reduce severity (cloning in utility is often necessary)

**Expected reduction:** 43 → ~20-30

**Commit:**

```bash
git commit -m "feat: CloneInHotPathDetector uses reachability and role context"
```

---

### Task 17: DeepNestingDetector — Handler Exemption

**Files:**
- Modify: `src/detectors/deep_nesting.rs`

**Current:** Flags deep nesting > 4 levels. 19 findings.

**Changes:**
- If HMM handler → increase threshold to 6 (handlers have dispatch logic)
- If test function → skip entirely
- Use adaptive threshold from resolver

**Expected reduction:** 19 → ~8-12

**Commit:**

```bash
git commit -m "feat: DeepNestingDetector uses HMM handler exemption"
```

---

### Task 18: InconsistentReturnsDetector — Graph-Based Caller Analysis

**Files:**
- Modify: `src/detectors/inconsistent_returns.rs`

**Current:** Text-based caller detection. 16 findings.

**Changes:**
- Use `ctx.detector_ctx.callers_by_qn` to get actual callers from graph instead of regex
- If function is constructor/init → skip (expected to return None/self)
- If function is test → skip
- If no callers in graph AND not exported → reduce severity (nobody uses the return value)

**Expected reduction:** 16 → ~6-10

**Commit:**

```bash
git commit -m "feat: InconsistentReturnsDetector uses graph-based caller analysis"
```

---

## Phase 4: Validation (Task 19)

### Task 19: Self-Analysis Validation

**Files:**
- None (validation only)

**Steps:**

1. Clean cache:
```bash
rm -rf ~/.cache/repotoire/ .repotoire/session/
```

2. Run self-analysis:
```bash
cargo run --release -- analyze . --format json --output /tmp/self-analysis-post-refactor.json
```

3. Count findings per detector:
```bash
cat /tmp/self-analysis-post-refactor.json | python3 -c "
import json, sys
from collections import Counter
data = json.load(sys.stdin)
findings = data.get('findings', [])
counts = Counter(f.get('detector', 'unknown') for f in findings)
for det, count in counts.most_common(30):
    print(f'{count:>4}  {det}')
print(f'\nTotal: {len(findings)}')
print(f'Grade: {data.get(\"grade\", \"?\")}')
"
```

4. Compare with baseline (1,057 findings, grade B-):

**Expected:**
- Total findings: <600
- Grade: B or B+
- No new false negatives (true positive findings should remain)

5. Run full test suite:
```bash
cargo test --lib --tests
```
Expected: ALL tests pass.

6. Performance check:
```bash
time cargo run --release -- analyze . 2>&1 | tail -5
```
Expected: Within 5% of current analysis time.

**Commit:**

```bash
git commit -m "docs: validation results for detector trait refactor"
```

---

## Summary

| Phase | Tasks | Scope | Key Change |
|-------|-------|-------|-----------|
| Phase 1 | 1-5 | Trait refactor | `detect(graph, files)` → `detect(ctx)`, all 99 detectors |
| Phase 2 | 6-10.5 | Context enrichment | Reachability, PublicAPI, ModuleMetrics, ClassCohesion, DecoratorIndex |
| Phase 3 | 11-18 | Detector upgrades | Top 8 detectors use context for FP reduction |
| Phase 4 | 19 | Validation | Self-analysis regression check |

**Total: 20 tasks, ~99 files modified**

**Expected outcome:**
- 1,057 → <600 findings (40%+ reduction)
- Grade B- → B/B+
- Clean, unified detector API
- Every detector has access to role, HMM, thresholds, reachability without opt-in
