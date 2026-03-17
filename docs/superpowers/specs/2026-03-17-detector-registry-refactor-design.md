# Detector Registry Refactor

**Date:** 2026-03-17
**Status:** Design approved, pending implementation plan
**Scope:** Standardize detector constructors and simplify the registry in `detectors/mod.rs`

## Problem Statement

Adding a new detector to Repotoire requires editing 3 locations in `detectors/mod.rs`:

1. `mod detector_name;` declaration (~line 60-200, 94 entries)
2. `pub use detector_name::DetectorNameDetector;` re-export (~line 202-349, 86 entries)
3. `Arc::new(DetectorNameDetector::new(...))` in `default_detectors_full()` (~line 400-601, 99 entries with 6 different constructor patterns)

The 200-line `default_detectors_full()` function manually constructs each detector with inconsistent constructor signatures:

| Pattern | Example | Count |
|---------|---------|-------|
| `::new()` | `CircularDependencyDetector::new()` | ~8 |
| `::new(repo_path)` | `EmptyCatchDetector::new(repo_path)` | ~60 |
| `::with_config(config)` | `GodClassDetector::with_config(config)` | ~10 |
| `::with_repository_path(path.to_path_buf())` | `EvalDetector::with_repository_path(...)` | ~4 |
| `::with_resolver(path, &resolver)` | `DeepNestingDetector::with_resolver(...)` | ~2 |
| `::with_config(path, config)` | `LongMethodsDetector::with_config(path, config)` | ~1 |
| `::with_path(repo_path)` | `GeneratorMisuseDetector::with_path(repo_path)` | ~2 |

There is also special-case conditional logic for `SurprisalDetector` (only added when the n-gram model is confident).

### Concrete problems

- **Friction for contributors.** Adding detector #100 requires understanding 3 locations, choosing the right constructor pattern, and matching the style of nearby entries.
- **No compile-time enforcement.** Forgetting a `pub use` or factory entry causes silent omission — the detector exists but never runs. No error, no warning.
- **Inconsistent API.** 6 constructor patterns mean each detector has an ad-hoc interface for initialization. Callers (the engine, tests, MCP) each assemble detectors differently.
- **200 lines of boilerplate.** `default_detectors_full()` is pure plumbing that obscures what's actually being configured.

## Design

### DetectorInit — Unified Initialization Context

A single struct that bundles everything a detector might need during construction. Passed to every factory. Each detector takes what it needs and ignores the rest.

```rust
/// Everything a detector needs for construction.
/// Built once per analysis from ProjectConfig + StyleProfile.
pub struct DetectorInit<'a> {
    pub repo_path: &'a Path,
    pub project_config: &'a ProjectConfig,
    pub resolver: ThresholdResolver,
    pub ngram_model: Option<&'a NgramModel>,
}

impl<'a> DetectorInit<'a> {
    /// Build a per-detector config with adaptive thresholds.
    ///
    /// Replaces the `make_config` closure in the current `default_detectors_full()`.
    /// Each detector calls `init.config_for("DetectorName")` to get its
    /// project-config-aware, adaptive-threshold-aware DetectorConfig.
    pub fn config_for(&self, detector_name: &str) -> DetectorConfig {
        DetectorConfig::from_project_config_with_type(
            detector_name, self.project_config, self.repo_path
        ).with_adaptive(self.resolver.clone())
    }
}
```

### RegisteredDetector — Compile-Time Factory Contract

A trait that every registered detector implements, providing a uniform factory method.

```rust
/// Trait for detectors that participate in the automatic registry.
///
/// Every detector implements `create()` as its canonical factory.
/// Existing constructors (new, with_config, etc.) are preserved
/// for backward compatibility and tests.
pub trait RegisteredDetector: Detector {
    /// Create this detector from the unified init context.
    fn create(init: &DetectorInit) -> Arc<dyn Detector>
    where
        Self: Sized;
}
```

The trait extends `Detector`, so every `RegisteredDetector` is automatically a `Detector`. The `create()` method returns `Arc<dyn Detector>` because that's what the engine needs.

### Factory Registry — One Line Per Detector

```rust
/// Function pointer type for detector factories.
type Factory = fn(&DetectorInit) -> Arc<dyn Detector>;

/// Helper that ensures compile-time enforcement of the RegisteredDetector trait.
/// Without this, a typo like `SomeDetector::create` where `create` is an
/// inherent method (not the trait method) would compile but bypass the contract.
const fn register<D: RegisteredDetector>() -> Factory {
    D::create
}

/// Complete list of all registered detectors.
///
/// Adding a new detector: add one entry here + `mod` declaration above.
/// The `register::<T>()` wrapper enforces at compile time that T implements
/// RegisteredDetector — a bare function pointer would not enforce this.
const DETECTOR_FACTORIES: &[Factory] = &[
    // Architecture
    register::<ArchitecturalBottleneckDetector>(),
    register::<CircularDependencyDetector>(),
    register::<CoreUtilityDetector>(),
    register::<DegreeCentralityDetector>(),
    register::<GodClassDetector>(),
    register::<InfluentialCodeDetector>(),
    register::<LongParameterListDetector>(),
    register::<ModuleCohesionDetector>(),
    register::<ShotgunSurgeryDetector>(),
    // Code smells
    register::<DataClumpsDetector>(),
    register::<DeadCodeDetector>(),
    register::<FeatureEnvyDetector>(),
    register::<InappropriateIntimacyDetector>(),
    register::<LazyClassDetector>(),
    register::<MessageChainDetector>(),
    register::<MiddleManDetector>(),
    register::<RefusedBequestDetector>(),
    // AI watchdog
    register::<AIBoilerplateDetector>(),
    register::<AIChurnDetector>(),
    register::<AIComplexitySpikeDetector>(),
    register::<AIDuplicateBlockDetector>(),
    register::<AIMissingTestsDetector>(),
    register::<AINamingPatternDetector>(),
    // ... all 102 detectors, alphabetical within category
    // Predictive
    register::<HierarchicalSurprisalDetector>(),
    register::<SurprisalDetector>(),
];

/// Create all registered detectors from a unified init context.
///
/// Replaces default_detectors_full() and its 4 delegation wrappers.
/// Every detector is always instantiated. Detectors that need
/// specific conditions (e.g., confident n-gram model) handle
/// their own "should I produce findings?" logic in detect().
pub fn create_all_detectors(init: &DetectorInit) -> Vec<Arc<dyn Detector>> {
    DETECTOR_FACTORIES.iter().map(|f| f(init)).collect()
}
```

### What Each Detector File Changes

Each detector gets a `RegisteredDetector` impl with a `create()` method that wraps its existing construction logic. Existing constructors are preserved for tests.

**Example — simple detector (new(repo_path) pattern):**

```rust
// empty_catch.rs — before:
impl EmptyCatchDetector {
    detector_new!(500);
}

// after (add, don't replace):
impl RegisteredDetector for EmptyCatchDetector {
    fn create(init: &DetectorInit) -> Arc<dyn Detector> {
        Arc::new(Self::new(init.repo_path))
    }
}
```

**Example — config-aware detector:**

```rust
// god_class.rs — before:
impl GodClassDetector {
    pub fn with_config(config: DetectorConfig) -> Self { ... }
}

// after (add, don't replace):
impl RegisteredDetector for GodClassDetector {
    fn create(init: &DetectorInit) -> Arc<dyn Detector> {
        Arc::new(Self::with_config(init.config_for("GodClassDetector")))
    }
}
```

**Example — resolver-aware detector:**

```rust
// deep_nesting.rs — before:
impl DeepNestingDetector {
    pub fn with_resolver(repo_path: &Path, resolver: &ThresholdResolver) -> Self { ... }
}

// after:
impl RegisteredDetector for DeepNestingDetector {
    fn create(init: &DetectorInit) -> Arc<dyn Detector> {
        Arc::new(Self::with_resolver(init.repo_path, &init.resolver))
    }
}
```

**Example — conditional detector (n-gram):**

```rust
// surprisal.rs — before: conditionally added in default_detectors_full()

// after: always created, handles condition internally
impl RegisteredDetector for SurprisalDetector {
    fn create(init: &DetectorInit) -> Arc<dyn Detector> {
        // Always instantiate. If no model, detect() returns empty.
        let model = init.ngram_model.cloned()
            .unwrap_or_default(); // NgramModel::default() creates a non-confident empty model
        Arc::new(Self::new(init.repo_path, model))
    }
}
```

### Registration Completeness Test

```rust
#[test]
fn test_all_detectors_registered() {
    let init = DetectorInit::test_default();
    let detectors = create_all_detectors(&init);
    // Count includes all detectors: 100 in the main vec + HierarchicalSurprisal
    // + Surprisal (now unconditional) = 102.
    // Update this number when adding/removing detectors.
    assert_eq!(
        detectors.len(), 102,
        "Detector count changed. Did you add a detector to DETECTOR_FACTORIES?"
    );
}
```

### What Gets Deleted

| Code | Lines | Reason |
|------|-------|--------|
| `default_detectors_full()` | ~200 | Replaced by `create_all_detectors()` (~3 lines) |
| `default_detectors()` | ~3 | Delegation wrapper |
| `default_detectors_with_config()` | ~5 | Delegation wrapper |
| `default_detectors_with_profile()` | ~5 | Delegation wrapper |
| `default_detectors_with_ngram()` | ~5 | Delegation wrapper |
| Conditional SurprisalDetector logic | ~15 | Moved into detector's `create()` |
| **Total** | ~230 | |

### What Gets Added

| Code | Lines | Purpose |
|------|-------|---------|
| `DetectorInit` struct + impl | ~25 | Unified init context |
| `RegisteredDetector` trait | ~8 | Factory contract |
| `DETECTOR_FACTORIES` + `create_all_detectors()` | ~110 | Registry (one line per detector) |
| 99 `impl RegisteredDetector` blocks | ~400 | Factory methods (~4 lines each) |
| Registration test | ~10 | Completeness check |
| **Total** | ~550 | |

Net: ~230 deleted, ~550 added. But the 400 lines of factory impls are distributed across 99 files (4 lines each), and the registry itself shrinks from 350 lines (mod + pub use + factory body) to 110 lines (mod + factory entry).

### pub use Cleanup

The 86 `pub use` re-exports currently serve two purposes:
1. Allow `default_detectors_full()` to reference types without path prefix (no longer needed — `create()` is in each detector's own module)
2. Allow external code to reference detector types (e.g., `use crate::detectors::GodClassDetector`)

After the migration:
- Remove `pub use` entries that are only used by the deleted factory function
- Keep `pub use` entries that are used elsewhere in the codebase (check with `grep` before removing)
- The compiler will warn about unused `pub use` — follow its guidance

### Callers to Update

Every call site that constructs detectors via `default_detectors*()` must switch to `create_all_detectors()`. Full inventory:

| Caller | File | Current function | Change |
|--------|------|-----------------|--------|
| `detect_stage` | `engine/stages/detect.rs:72` | `default_detectors_with_ngram` | Use `create_all_detectors()` |
| MCP analyze oneshot | `mcp/tools/analysis.rs:71` | `default_detectors_with_ngram` | Use `create_all_detectors()` |
| MCP get_findings fallback | `mcp/tools/analysis.rs:169` | `default_detectors_with_ngram` | Use `create_all_detectors()` |
| MCP list_detectors | `mcp/tools/files.rs:162` | `default_detectors_with_ngram` | Use `create_all_detectors()`. Remove dummy-model hack — detectors are now always instantiated |
| Legacy CLI detect | `cli/analyze/detect.rs:155` | `default_detectors_with_ngram` | Update (legacy module, #[allow(dead_code)]) |
| Legacy CLI GI detect | `cli/analyze/detect.rs:214` | `default_detectors_with_ngram` | Update |
| Legacy CLI speculative | `cli/analyze/detect.rs:302` | `default_detectors_with_ngram` | Update |
| `create_default_engine` | `detectors/mod.rs:621` | `default_detectors` | Use `create_all_detectors()` |
| Session cold analysis | `session.rs:821` | `default_detectors_with_config` | Use `create_all_detectors()` with `resolver: ThresholdResolver::default()`, `ngram_model: None` |
| Session incremental | `session.rs:886` | `default_detectors_with_config` | Same |
| Session standalone | `session.rs:1425` | `default_detectors_with_config` | Same |
| Streaming engine | `detectors/streaming_engine.rs:126` | `default_detectors_with_config` | Use `create_all_detectors()` |
| Test in `base.rs` | `detectors/base.rs:577` | `default_detectors` | Update |
| Test in `engine.rs` | `detectors/engine.rs:2228` | `default_detectors` | Update |
| Test in `mod.rs` | `detectors/mod.rs:1006` | `default_detectors` | Update |

**Note on `session.rs` callers:** These use `default_detectors_with_config(path, config)` without a StyleProfile or NgramModel. Under the new design, they construct `DetectorInit` with `resolver: ThresholdResolver::default()` and `ngram_model: None`.

**Note on `handle_list_detectors`:** The current code trains a dummy NgramModel (800 iterations) so SurprisalDetector appears in the list. With the new always-instantiate approach, this hack is unnecessary — remove the dummy model construction and pass `ngram_model: None`. SurprisalDetector will appear in the list regardless.

### Preserved Public Functions

`build_threshold_resolver(style_profile: Option<&StyleProfile>) -> ThresholdResolver` is preserved unchanged. It serves a different purpose from detector construction — callers like `detect_stage` use it to set the engine's threshold resolver independently.

### DetectorInit::test_default()

For tests that need a `DetectorInit` without real config:

```rust
impl DetectorInit<'_> {
    /// Test helper — creates a DetectorInit with defaults and a temp path.
    /// Uses a leaked PathBuf to satisfy the lifetime (acceptable in tests).
    #[cfg(test)]
    pub fn test_default() -> DetectorInit<'static> {
        let path: &'static Path = Box::leak(
            std::env::current_dir().unwrap().into_boxed_path()
        );
        DetectorInit {
            repo_path: path,
            project_config: Box::leak(Box::new(ProjectConfig::default())),
            resolver: ThresholdResolver::default(),
            ngram_model: None,
        }
    }
}
```

### Ordering

`DETECTOR_FACTORIES` entries are ordered by category (architecture, code smells, AI, ML, security, quality, async, framework, performance, testing, CI/CD, predictive) then alphabetically within each category. This matches the current `default_detectors_full()` ordering. Downstream code should not rely on detector ordering — finding IDs are deterministic based on content, not position.

## Behavior Changes

### Intentional

1. **SurprisalDetector is always instantiated.** Previously conditionally added when n-gram model was confident. Now always created — returns empty findings when model is unavailable or not confident. This means `create_all_detectors()` always returns the same count regardless of n-gram state.

### Preserved

1. All 99 existing detectors, their `Detector` trait implementations, and their behavior are unchanged.
2. Existing constructors (`new()`, `with_config()`, etc.) are preserved — tests that construct detectors directly continue working.
3. The `Detector` trait is unchanged — `detect(&self, ctx: &AnalysisContext) -> Vec<Finding>`.
4. DetectorScope, DetectorConfig, and all detector infrastructure are unchanged.

## Migration Path

### Phase 1: Add infrastructure (no behavior change)

- Add `DetectorInit` struct and `RegisteredDetector` trait to `detectors/mod.rs`
- Add `DETECTOR_FACTORIES` const (empty initially) and `create_all_detectors()`
- Everything compiles, old path still active

### Phase 2: Migrate detectors in batches

- Add `impl RegisteredDetector` to each detector file (~10 detectors per batch)
- Add corresponding entry to `DETECTOR_FACTORIES`
- Each batch is independently verifiable — `cargo test` passes after each

### Phase 3: Switch callers

- Update `detect_stage`, MCP, and any other callers to use `create_all_detectors()`
- Remove `default_detectors_full()` and its delegation chain
- Clean up unused `pub use` re-exports

### Phase 4: Verification

- Run full analysis on real repos (Flask, FastAPI, Django) and compare finding counts
- Verify SurprisalDetector behavior with and without n-gram model
- Update CLAUDE.md detector count if changed

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| A detector's `create()` doesn't match its old constructor behavior | Different findings | Compare finding counts before/after on benchmark repos |
| SurprisalDetector always-on produces noise | Extra low-quality findings | Detector returns empty when model not confident (same as before, just decided internally) |
| Forgetting to add factory entry for new detector | Detector silently omitted | Registration count test fails; count is in the same file as the entry |
| 99-file churn causes merge conflicts | Branch conflicts | Batch commits (10 detectors each); merge quickly |
