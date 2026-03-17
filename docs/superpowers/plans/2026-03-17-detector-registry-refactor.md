# Detector Registry Refactor — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Standardize all 100 detector constructors behind a `RegisteredDetector` trait with `DetectorInit` context, replacing the 200-line manual factory with a one-line-per-detector const array.

**Architecture:** Add `DetectorInit` (unified init context) + `RegisteredDetector` trait (factory contract) + `DETECTOR_FACTORIES` const array (one `register::<D>()` entry per detector). Each detector gets a 4-line `impl RegisteredDetector` block. Old `default_detectors_full()` and its 4 wrappers are deleted. All 15 callers switch to `create_all_detectors()`.

**Tech Stack:** Rust, no new dependencies

**Spec:** `docs/superpowers/specs/2026-03-17-detector-registry-refactor-design.md`

---

## File Structure

### Files to modify

| File | Change |
|------|--------|
| `repotoire-cli/src/detectors/mod.rs` | Add `DetectorInit`, `RegisteredDetector`, `register()`, `DETECTOR_FACTORIES`, `create_all_detectors()`. Delete `default_detectors_full()` + 4 wrappers. Clean up `pub use`. |
| `repotoire-cli/src/detectors/base.rs` | Add `use super::DetectorInit;` and `RegisteredDetector` trait (or keep in mod.rs) |
| 100 detector files | Add `impl RegisteredDetector for XxxDetector` block (~4 lines each) |
| `repotoire-cli/src/engine/stages/detect.rs` | Switch from `default_detectors_with_ngram()` to `create_all_detectors()` |
| `repotoire-cli/src/mcp/tools/analysis.rs` | Switch callers |
| `repotoire-cli/src/mcp/tools/files.rs` | Switch `handle_list_detectors`, remove dummy NgramModel hack |
| `repotoire-cli/src/session.rs` | Switch 3 callers |
| `repotoire-cli/src/detectors/streaming_engine.rs` | Switch caller |
| `repotoire-cli/src/cli/analyze/detect.rs` | Switch 3 legacy callers |

---

## Chunk 1: Infrastructure + First Detector Batch

### Task 1: Add DetectorInit, RegisteredDetector, and registry infrastructure

**Files:**
- Modify: `repotoire-cli/src/detectors/mod.rs`

- [ ] **Step 1: Add DetectorInit struct**

Add after the existing `impl_taint_precompute` macro (around line 57), before the module declarations:

```rust
use crate::calibrate::{NgramModel, ThresholdResolver};
use crate::config::ProjectConfig;

/// Everything a detector needs for construction.
pub struct DetectorInit<'a> {
    pub repo_path: &'a Path,
    pub project_config: &'a ProjectConfig,
    pub resolver: ThresholdResolver,
    pub ngram_model: Option<&'a NgramModel>,
}

impl<'a> DetectorInit<'a> {
    /// Build a per-detector config with adaptive thresholds.
    pub fn config_for(&self, detector_name: &str) -> DetectorConfig {
        DetectorConfig::from_project_config_with_type(
            detector_name, self.project_config, self.repo_path
        ).with_adaptive(self.resolver.clone())
    }

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

- [ ] **Step 2: Add RegisteredDetector trait**

```rust
/// Trait for detectors that participate in the automatic registry.
pub trait RegisteredDetector: Detector {
    fn create(init: &DetectorInit) -> Arc<dyn Detector> where Self: Sized;
}
```

- [ ] **Step 3: Add register() helper and empty DETECTOR_FACTORIES**

```rust
type DetectorFactory = fn(&DetectorInit) -> Arc<dyn Detector>;

const fn register<D: RegisteredDetector>() -> DetectorFactory {
    D::create
}

/// Complete list of all registered detectors.
const DETECTOR_FACTORIES: &[DetectorFactory] = &[
    // Entries added in subsequent tasks
];

/// Create all registered detectors from a unified init context.
pub fn create_all_detectors(init: &DetectorInit) -> Vec<Arc<dyn Detector>> {
    DETECTOR_FACTORIES.iter().map(|f| f(init)).collect()
}
```

- [ ] **Step 4: Add re-export of new types**

```rust
pub use base::RegisteredDetector; // if trait is in base.rs
// OR keep RegisteredDetector in mod.rs and export from there
```

- [ ] **Step 5: Verify compilation**

```bash
cd repotoire-cli && cargo check
```

- [ ] **Step 6: Commit**

```bash
git add repotoire-cli/src/detectors/mod.rs
git commit -m "feat: add DetectorInit, RegisteredDetector trait, and empty DETECTOR_FACTORIES"
```

### Task 2: Migrate zero-argument detectors (14 detectors)

**Files:**
- Modify: 14 detector files + `repotoire-cli/src/detectors/mod.rs`

These detectors use `::new()` with no arguments:
- `circular_dependency.rs` — CircularDependencyDetector
- `dead_code.rs` — DeadCodeDetector
- `inappropriate_intimacy.rs` — InappropriateIntimacyDetector
- `lazy_class.rs` — LazyClassDetector
- `middle_man.rs` — MiddleManDetector
- `refused_bequest.rs` — RefusedBequestDetector
- `core_utility.rs` — CoreUtilityDetector
- `ai_boilerplate.rs` — AIBoilerplateDetector
- `ai_churn.rs` — AIChurnDetector
- `ai_complexity_spike.rs` — AIComplexitySpikeDetector
- `ai_duplicate_block.rs` — AIDuplicateBlockDetector
- `ai_missing_tests.rs` — AIMissingTestsDetector
- `ai_naming_pattern.rs` — AINamingPatternDetector
- `hierarchical_surprisal.rs` — HierarchicalSurprisalDetector

- [ ] **Step 1: Add impl RegisteredDetector to each file**

For each of the 14 files, add at the end (before any `#[cfg(test)]` module):

```rust
use super::{DetectorInit, RegisteredDetector};
use std::sync::Arc;

impl RegisteredDetector for CircularDependencyDetector {
    fn create(_init: &DetectorInit) -> Arc<dyn super::base::Detector> {
        Arc::new(Self::new())
    }
}
```

Adapt type name for each file. The import and impl block is the same pattern for all 14.

- [ ] **Step 2: Add all 14 to DETECTOR_FACTORIES**

In `mod.rs`, add entries to the `DETECTOR_FACTORIES` array:

```rust
const DETECTOR_FACTORIES: &[DetectorFactory] = &[
    // Architecture (zero-arg)
    register::<CircularDependencyDetector>(),
    register::<CoreUtilityDetector>(),
    // Code smells (zero-arg)
    register::<DeadCodeDetector>(),
    register::<InappropriateIntimacyDetector>(),
    register::<LazyClassDetector>(),
    register::<MiddleManDetector>(),
    register::<RefusedBequestDetector>(),
    // AI watchdog (zero-arg)
    register::<AIBoilerplateDetector>(),
    register::<AIChurnDetector>(),
    register::<AIComplexitySpikeDetector>(),
    register::<AIDuplicateBlockDetector>(),
    register::<AIMissingTestsDetector>(),
    register::<AINamingPatternDetector>(),
    // Predictive (zero-arg)
    register::<HierarchicalSurprisalDetector>(),
];
```

- [ ] **Step 3: Verify compilation**

```bash
cargo check
```

- [ ] **Step 4: Run tests**

```bash
cargo test --lib
```

- [ ] **Step 5: Commit**

```bash
git commit -am "feat: add RegisteredDetector impl to 14 zero-arg detectors"
```

---

## Chunk 2: Migrate new(repo_path) Detectors (67 detectors)

### Task 3: Migrate new(repo_path) detectors — security batch (23)

**Files:**
- Modify: 23 detector files in security category

These all use the pattern `::new(repository_path)`:
- `secrets.rs`, `path_traversal.rs`, `command_injection.rs`, `ssrf.rs`, `regex_dos.rs`
- `insecure_crypto.rs`, `xss.rs`, `hardcoded_ips.rs`, `insecure_random.rs`, `cors_misconfig.rs`
- `xxe.rs`, `insecure_deserialize.rs`, `cleartext_credentials.rs`, `insecure_cookie.rs`
- `jwt_weak.rs`, `prototype_pollution.rs`, `nosql_injection.rs`, `log_injection.rs`
- `insecure_tls.rs`, `gh_actions.rs`, `dep_audit.rs`
- `react_hooks.rs`, `django_security.rs`, `express_security.rs`

- [ ] **Step 1: Add impl RegisteredDetector to each file**

Same pattern for all:

```rust
use super::{DetectorInit, RegisteredDetector};
use std::sync::Arc;

impl RegisteredDetector for SecretDetector {
    fn create(init: &DetectorInit) -> Arc<dyn super::base::Detector> {
        Arc::new(Self::new(init.repo_path))
    }
}
```

- [ ] **Step 2: Add all 23 to DETECTOR_FACTORIES in mod.rs**

- [ ] **Step 3: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 4: Commit**

```bash
git commit -am "feat: add RegisteredDetector impl to 23 security/framework detectors"
```

### Task 4: Migrate new(repo_path) detectors — code quality batch (28)

**Files:**
- Modify: 28 detector files in code quality/async/testing/performance categories

- `empty_catch.rs`, `todo_scanner.rs`, `magic_numbers.rs`, `missing_docstrings.rs`
- `debug_code.rs`, `commented_code.rs`, `duplicate_code.rs`, `unreachable_code.rs`
- `string_concat_loop.rs`, `wildcard_imports.rs`, `mutable_default_args.rs`
- `global_variables.rs`, `implicit_coercion.rs`, `single_char_names.rs`
- `broad_exception.rs`, `boolean_trap.rs`, `inconsistent_returns.rs`
- `dead_store.rs`, `hardcoded_timeout.rs`
- `missing_await.rs`, `unhandled_promise.rs`, `callback_hell.rs`
- `test_in_production.rs`
- `sync_in_async.rs`, `n_plus_one.rs`, `regex_in_loop.rs`
- `large_files.rs` (Note: actually uses `with_resolver` — handle in Task 5)
- `unused_imports.rs`

Same `Self::new(init.repo_path)` pattern. Check each file to confirm it uses `::new(repository_path)` before adding the impl.

- [ ] **Step 1: Add impl RegisteredDetector to each file**

- [ ] **Step 2: Add all entries to DETECTOR_FACTORIES**

- [ ] **Step 3: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 4: Commit**

```bash
git commit -am "feat: add RegisteredDetector impl to 28 quality/async/perf detectors"
```

### Task 5: Migrate new(repo_path) detectors — ML + Rust batch (16)

**Files:**
- Modify: ML detectors (8) + Rust detectors (7) + message_chain (1)

ML:
- `ml_smells.rs` — contains 8 detectors (TorchLoadUnsafe, NanEquality, MissingZeroGrad, ForwardMethod, MissingRandomSeed, ChainIndexing, RequireGradTypo, DeprecatedTorchApi). Each needs its own `impl RegisteredDetector`.

Rust:
- `rust_smells.rs` — contains 7 detectors (UnwrapWithoutContext, UnsafeWithoutSafetyComment, CloneInHotPath, MissingMustUse, BoxDynTrait, MutexPoisoningRisk, PanicDensity). Each needs its own `impl RegisteredDetector`.

Other:
- `message_chain.rs` — MessageChainDetector::new(repository_path)

- [ ] **Step 1: Add impl RegisteredDetector in ml_smells.rs (8 impls)**

Each ML detector follows `Self::new(init.repo_path)`:

```rust
impl RegisteredDetector for TorchLoadUnsafeDetector {
    fn create(init: &DetectorInit) -> Arc<dyn Detector> {
        Arc::new(Self::new(init.repo_path))
    }
}
// ... repeat for all 8
```

- [ ] **Step 2: Add impl RegisteredDetector in rust_smells.rs (7 impls)**

- [ ] **Step 3: Add impl RegisteredDetector in message_chain.rs**

- [ ] **Step 4: Add all 16 entries to DETECTOR_FACTORIES**

- [ ] **Step 5: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 6: Commit**

```bash
git commit -am "feat: add RegisteredDetector impl to 16 ML/Rust/misc detectors"
```

---

## Chunk 3: Migrate Special-Constructor Detectors (19 detectors)

### Task 6: Migrate with_config detectors (9)

**Files:**
- Modify: 9 detector files that use `with_config(make_config("name"))`

- `god_class.rs` — `GodClassDetector::with_config(make_config("GodClassDetector"))`
- `long_parameter.rs` — `LongParameterListDetector::with_config(make_config(...))`
- `data_clumps.rs` — `DataClumpsDetector::with_config(make_config(...))`
- `feature_envy.rs` — `FeatureEnvyDetector::with_config(make_config(...))`
- `architectural_bottleneck.rs` — same pattern
- `degree_centrality.rs` — same
- `influential_code.rs` — same
- `module_cohesion.rs` — same
- `shotgun_surgery.rs` — same

- [ ] **Step 1: Add impl RegisteredDetector to each**

```rust
impl RegisteredDetector for GodClassDetector {
    fn create(init: &DetectorInit) -> Arc<dyn Detector> {
        Arc::new(Self::with_config(init.config_for("GodClassDetector")))
    }
}
```

Each detector passes its own name to `init.config_for()`.

- [ ] **Step 2: Add all 9 to DETECTOR_FACTORIES**

- [ ] **Step 3: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 4: Commit**

```bash
git commit -am "feat: add RegisteredDetector impl to 9 config-aware detectors"
```

### Task 7: Migrate remaining special-constructor detectors (10)

**Files:**
- Modify: 10 detector files with non-standard constructors

**with_repository_path (4):**
- `eval_detector.rs` — `EvalDetector::with_repository_path(path.to_path_buf())`
- `pickle_detector.rs` — same
- `sql_injection/mod.rs` — same
- `unsafe_template.rs` — same

```rust
impl RegisteredDetector for EvalDetector {
    fn create(init: &DetectorInit) -> Arc<dyn Detector> {
        Arc::new(Self::with_repository_path(init.repo_path.to_path_buf()))
    }
}
```

**with_path (2):**
- `generator_misuse.rs` — `GeneratorMisuseDetector::with_path(repo_path)`
- `infinite_loop.rs` — `InfiniteLoopDetector::with_path(repo_path)`

```rust
impl RegisteredDetector for GeneratorMisuseDetector {
    fn create(init: &DetectorInit) -> Arc<dyn Detector> {
        Arc::new(Self::with_path(init.repo_path))
    }
}
```

**with_resolver (2):**
- `deep_nesting.rs` — `DeepNestingDetector::with_resolver(path, &resolver)`
- `large_files.rs` — `LargeFilesDetector::with_resolver(path, &resolver)`

```rust
impl RegisteredDetector for DeepNestingDetector {
    fn create(init: &DetectorInit) -> Arc<dyn Detector> {
        Arc::new(Self::with_resolver(init.repo_path, &init.resolver))
    }
}
```

**with_config(path, config) (1):**
- `long_methods.rs` — `LongMethodsDetector::with_config(path, make_config("long-methods"))`

```rust
impl RegisteredDetector for LongMethodsDetector {
    fn create(init: &DetectorInit) -> Arc<dyn Detector> {
        Arc::new(Self::with_config(init.repo_path, init.config_for("long-methods")))
    }
}
```

**Conditional n-gram (1):**
- `surprisal.rs` — `SurprisalDetector::new(repo_path, model)` (currently conditional)

```rust
impl RegisteredDetector for SurprisalDetector {
    fn create(init: &DetectorInit) -> Arc<dyn Detector> {
        let model = init.ngram_model.cloned().unwrap_or_default();
        Arc::new(Self::new(init.repo_path, model))
    }
}
```

- [ ] **Step 1: Add impl RegisteredDetector to all 10 files**

- [ ] **Step 2: Add all 10 to DETECTOR_FACTORIES**

- [ ] **Step 3: Verify DETECTOR_FACTORIES now has 100 entries total**

```bash
cd repotoire-cli && grep -c "register::<" src/detectors/mod.rs
```

Expected: 100

- [ ] **Step 4: Add registration count test**

Add to the `#[cfg(test)]` module at the bottom of `mod.rs`:

```rust
#[test]
fn test_all_detectors_registered() {
    let init = DetectorInit::test_default();
    let detectors = create_all_detectors(&init);
    assert_eq!(
        detectors.len(), 100,
        "Detector count changed. Update DETECTOR_FACTORIES in mod.rs."
    );
}
```

- [ ] **Step 5: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 6: Commit**

```bash
git commit -am "feat: add RegisteredDetector impl to 10 special-constructor detectors

All 100 detectors now implement RegisteredDetector. DETECTOR_FACTORIES
has 100 entries. Registration count test added."
```

---

## Chunk 4: Switch Callers + Delete Old Factory

### Task 8: Switch all callers to create_all_detectors()

**Files:**
- Modify: `engine/stages/detect.rs`, `mcp/tools/analysis.rs`, `mcp/tools/files.rs`, `session.rs`, `detectors/streaming_engine.rs`, `cli/analyze/detect.rs`, `detectors/mod.rs` (tests + create_default_engine), `detectors/base.rs` (test), `detectors/engine.rs` (test)

- [ ] **Step 1: Update engine/stages/detect.rs**

Replace the call to `default_detectors_with_ngram()` with:

```rust
let init = crate::detectors::DetectorInit {
    repo_path: input.repo_path,
    project_config: input.project_config,
    resolver: crate::detectors::build_threshold_resolver(input.style_profile),
    ngram_model: input.ngram_model,
};
let detectors = crate::detectors::create_all_detectors(&init);
```

Then filter out skipped detectors as before.

- [ ] **Step 2: Update MCP callers**

In `mcp/tools/analysis.rs`, replace `default_detectors_with_ngram()` calls with `create_all_detectors()`. Build a `DetectorInit` from available state.

In `mcp/tools/files.rs` (`handle_list_detectors`), remove the dummy NgramModel training hack. Build `DetectorInit` with `ngram_model: None`.

- [ ] **Step 3: Update session.rs callers (3 sites)**

Replace `default_detectors_with_config(path, config)` with:

```rust
let init = DetectorInit {
    repo_path: &self.repo_path,
    project_config: &project_config,
    resolver: ThresholdResolver::default(),
    ngram_model: None,
};
let detectors = create_all_detectors(&init);
```

- [ ] **Step 4: Update streaming_engine.rs**

Same pattern as session.rs.

- [ ] **Step 5: Update legacy cli/analyze/detect.rs (3 sites)**

Replace `default_detectors_with_ngram()` calls. These are in `#[allow(dead_code)]` legacy code.

- [ ] **Step 6: Update test call sites**

In `detectors/base.rs`, `detectors/engine.rs`, and `detectors/mod.rs` tests — replace `default_detectors()` with `create_all_detectors(&DetectorInit::test_default())`.

- [ ] **Step 7: Update create_default_engine in mod.rs**

Replace `default_detectors()` with `create_all_detectors()`.

- [ ] **Step 8: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 9: Commit**

```bash
git commit -am "refactor: switch all 15 callers from default_detectors*() to create_all_detectors()"
```

### Task 9: Delete old factory functions and clean up pub use

**Files:**
- Modify: `repotoire-cli/src/detectors/mod.rs`

- [ ] **Step 1: Delete default_detectors_full() and its 4 wrappers**

Remove these functions from `mod.rs`:
- `default_detectors()` (~line 369)
- `default_detectors_with_config()` (~line 376)
- `default_detectors_with_profile()` (~line 383)
- `default_detectors_with_ngram()` (~line 391)
- `default_detectors_full()` (~line 400-601)

- [ ] **Step 2: Verify compilation after deletion**

```bash
cargo check
```

If anything fails, a caller was missed in Task 8. Fix it.

- [ ] **Step 3: Audit pub use re-exports**

For each `pub use` line in mod.rs (lines ~202-349), check if the type is used outside mod.rs:

```bash
# Example for one detector:
grep -r "GodClassDetector" repotoire-cli/src/ --include="*.rs" | grep -v "mod.rs" | grep -v "god_class.rs"
```

Remove `pub use` entries that have zero external references. Keep entries used by tests, the engine, or the MCP server.

- [ ] **Step 4: Verify — `cargo check && cargo test --lib`**

- [ ] **Step 5: Commit**

```bash
git commit -am "refactor: delete default_detectors_full() and 4 delegation wrappers (~230 lines)

All callers now use create_all_detectors(). Cleaned up unused pub use
re-exports. DETECTOR_FACTORIES const array is the single source of truth."
```

---

## Chunk 5: Verification

### Task 10: End-to-end verification

**Files:** None modified — verification only.

- [ ] **Step 1: Run full test suite**

```bash
cd repotoire-cli && cargo test
```

All tests must pass.

- [ ] **Step 2: Run analysis on the repotoire codebase itself**

```bash
cargo run -- analyze . --format json --no-git --max-files 30 2>/dev/null | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(f'Score: {data[\"overall_score\"]:.1f} ({data[\"grade\"]})')
print(f'Findings: {len(data[\"findings\"])}')
"
```

Verify output is reasonable (score > 0, findings > 0).

- [ ] **Step 3: Verify detector count matches**

```bash
cargo run -- analyze . --format json --no-git --max-files 5 2>/dev/null | python3 -c "
import json, sys
data = json.load(sys.stdin)
detectors = set(f['detector'] for f in data['findings'])
print(f'Active detectors: {len(detectors)}')
for d in sorted(detectors):
    print(f'  {d}')
"
```

- [ ] **Step 4: Verify DETECTOR_FACTORIES count**

```bash
grep -c "register::<" repotoire-cli/src/detectors/mod.rs
```

Expected: 100

- [ ] **Step 5: Commit any final fixes**

```bash
git commit -am "chore: verification complete — detector registry refactor"
```
