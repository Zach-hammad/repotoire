# Detector Trait Refactor & Graph-Integrated FP Reduction — Design

## Problem

Repotoire has powerful pre-computed infrastructure (FunctionRole, ContextHMM, ThresholdResolver, taint analysis) that only **10 of 99 detectors** can access. The root cause is a fragmented trait API:

```
detect(graph, files)              — 99 detectors implement this (required)
detect_with_context(graph, files, ctx) — 4 detectors override this
detect_ctx(analysis_ctx)          — 6 detectors override this
set_detector_context(ctx)         — 5 detectors override this
set_precomputed_taint(cross, intra) — 12 detectors override this
uses_context() -> bool            — 4 detectors override this
```

The engine calls `detect_ctx()` for all detectors, but the default implementation strips the AnalysisContext and calls `detect(graph, files)` — so 89 detectors never see the enriched context.

**Result**: 1,057 findings on self-analysis. Top producers (LazyClass=163, DeadCode=100, CoreUtility=100, MagicNumbers=99, LongMethods=74) lack role/HMM/threshold gating.

## Solution

### 1. Unify Detector Trait to Single Entry Point

Replace the 3-method dispatch with one method:

```rust
// BEFORE: 3 entry points, confusing dispatch
fn detect(&self, graph, files) -> Result<Vec<Finding>>          // required
fn detect_with_context(&self, graph, files, contexts) -> ...    // optional override
fn detect_ctx(&self, ctx: &AnalysisContext) -> ...              // optional override

// AFTER: 1 entry point, context always available
fn detect(&self, ctx: &AnalysisContext) -> Result<Vec<Finding>>
```

**Remove**: `detect_with_context()`, `detect_ctx()`, `uses_context()`, `set_detector_context()`

**Keep**: `set_precomputed_taint()` temporarily (security detectors inject filtered results before detect; migrate to AnalysisContext access in Phase 3)

**Add**: `AnalysisContext::test(graph)` constructor for unit tests — fills all other fields with sensible defaults (empty maps, default resolver).

### 2. Enrich AnalysisContext with New Pre-Computed Fields

Add graph-derived data computed once during the existing parallel pre-compute phase:

| Field | Type | Cost | Purpose |
|-------|------|------|---------|
| `reachable_from_entry` | `Arc<HashSet<StrKey>>` | O(V+E) BFS | "Is this function reachable from any entry point?" |
| `public_api` | `Arc<HashSet<StrKey>>` | O(V) scan | "Is this function part of the public API?" |
| `module_metrics` | `Arc<HashMap<String, ModuleMetrics>>` | O(V+E) | Per-module coupling, cohesion, size |
| `class_cohesion` | `Arc<HashMap<StrKey, f64>>` | O(C*M) | LCOM4 per class |
| `decorator_index` | `Arc<HashMap<StrKey, Vec<String>>>` | O(V) parse | Pre-parsed decorator/annotation lists |

**ModuleMetrics** struct:
```rust
pub struct ModuleMetrics {
    pub function_count: usize,
    pub class_count: usize,
    pub incoming_calls: usize,    // calls from other modules
    pub outgoing_calls: usize,    // calls to other modules
    pub internal_calls: usize,    // calls within module
    pub coupling: f64,            // outgoing / total calls
    pub cohesion: f64,            // internal / total calls
}
```

All computed in parallel during `precompute_gd_startup()`, adding <100ms to the existing ~3s pre-compute.

### 3. Add Convenience Methods to AnalysisContext

Zero-cost O(1) queries that any detector can call:

```rust
impl AnalysisContext {
    // Reachability
    fn is_reachable(&self, qn: &str) -> bool
    fn is_public_api(&self, qn: &str) -> bool

    // Role (already exists: function_role, is_test_function, etc.)
    fn is_utility_function(&self, qn: &str) -> bool    // existing
    fn is_hub_function(&self, qn: &str) -> bool         // existing
    fn is_handler(&self, qn: &str) -> bool              // NEW: HMM Handler check
    fn is_infrastructure(&self, qn: &str) -> bool       // NEW: Utility | Hub | handler

    // Class
    fn class_cohesion(&self, qn: &str) -> Option<f64>
    fn class_decorators(&self, qn: &str) -> &[String]

    // Module
    fn module_metrics(&self, module: &str) -> Option<&ModuleMetrics>
    fn module_coupling(&self, module: &str) -> f64

    // Thresholds (already exists: resolver)
    fn threshold(&self, kind: MetricKind, default: f64) -> f64
}
```

### 4. Migrate Top Detectors to Use Context

After the trait refactor, incrementally upgrade the top finding producers:

| Detector | Findings | Context Usage |
|----------|----------|---------------|
| CoreUtility | 100 | Skip Hub/Orchestrator; use module_metrics for spread |
| MagicNumbers | 99 | Exempt test functions; adaptive threshold |
| LongMethods | 74 | HMM Handler exemption; adaptive threshold; orchestrator check |
| LongParamList | 51 | Exempt constructors; Hub allowance; adaptive threshold |
| DuplicateCode | 50 | Exempt test files; skip fixture/generated |
| CloneInHotPath | 43 | Only flag in reachable code; exempt test functions |
| DeepNesting | 19 | HMM Handler exemption; match/switch context |
| InconsistentReturns | 16 | Use callers from graph; exempt constructors |

## Architecture

### Pre-Compute Data Flow (existing + new)

```
Graph Build Complete
        │
        ├── Thread 1: CentralizedTaint (~1.5s)
        ├── Thread 2: ContextHMM (~0.4s)
        ├── Thread 3: DetectorContext::build() (~0.3s)
        ├── Thread 4: ReachabilityIndex::build() (~0.05s)    ← NEW
        ├── Thread 5: ModuleMetrics::build() (~0.03s)        ← NEW
        └── Main:     FunctionContextBuilder::build() (~1.5s)
                      + PublicApiSet (~0.01s)                 ← NEW
                      + ClassCohesion (~0.02s)                ← NEW
                      + DecoratorIndex (~0.01s)               ← NEW
        │
        ▼
    AnalysisContext (wraps all of the above)
        │
        ▼
    99 Detectors (all receive &AnalysisContext)
        │
        ▼
    Findings → Scoring → Report
```

### Migration Strategy

**Phase 1**: Trait refactor — mechanical change to all 99 implementations. Each detect() just receives `ctx: &AnalysisContext` and extracts `ctx.graph` + `ctx.as_file_provider()` to keep existing logic working.

**Phase 2**: Add new fields to AnalysisContext — purely additive, no detector changes needed.

**Phase 3**: Targeted detector upgrades — each detector independently wires in context queries.

### Test Strategy

- `AnalysisContext::test(graph: &dyn GraphQuery)` provides a minimal context for existing unit tests
- Existing tests only need signature updates: `detect(graph, files)` → `detect(&ctx)` where `ctx = AnalysisContext::test(&graph)`
- New tests for enriched fields test the pre-compute functions directly
- Integration test: self-analysis finding count regression check

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Breaking all 99 detectors at once | Phase 1 is mechanical — extract graph/files from ctx. Compile-driven. |
| Test breakage | AnalysisContext::test() helper makes migration trivial |
| Performance regression from new pre-compute | All new fields are O(V+E) or less, total <100ms |
| Incremental cache invalidation | Cache version bump forces rebuild; no logic change |
| Security detectors need taint injection | Keep set_precomputed_taint() in Phase 1; migrate to ctx.taint in Phase 3 |

## Success Criteria

- All 99 detectors use single `detect(ctx)` signature
- Self-analysis findings drop from ~1,057 to <600
- Self-analysis grade improves from B- to B or B+
- No test regressions
- No performance regression (total analysis time within 5% of current)
