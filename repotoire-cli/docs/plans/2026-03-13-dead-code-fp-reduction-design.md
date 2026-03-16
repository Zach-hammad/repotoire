# Dead Code Detector FP Reduction Design

## Problem

The DeadCode detector reports 100 findings on self-analysis, 19 high severity — almost all false positives. The main FP categories:

1. **Trait impl methods** (19 high, ~40 medium): Methods like `detect()`, `run()`, `extract()` are called via dynamic dispatch (`&dyn Detector`), which creates zero static call edges. The detector sees `call_fan_in() == 0` and flags them as dead.

2. **CLI entry points**: `run()` functions dispatched by clap command routing.

3. **Self-call blindness**: Functions called via `self.method()` or `Self::method()` where the parser doesn't always track these in the call graph.

## Root Cause Analysis

The Rust parser already encodes trait impl information in qualified names:
- Trait impl methods: `path::impl<TraitName for TypeName>::method:line`
- Inherent impl methods: `path::impl<TypeName>::method:line`

But the dead code detector ignores this information. Instead, it uses a hardcoded `COMMON_TRAIT_METHODS` list of 16 generic names (`new`, `default`, `fmt`, etc.), which can't cover domain-specific trait methods like `detect`, `run`, `extract`.

Additionally, the graph model loses trait impl relationships — when parsing `impl Trait for Type`, the trait name is used to construct the QN but no `Inherits` edge is created. This information disappears after parsing.

## Design

### Phase 1: Trait-Aware Dead Code Detection

Replace the `COMMON_TRAIT_METHODS` hardcoded list with a structural QN pattern check:

```rust
fn is_trait_impl_method(func_qn: &str) -> bool {
    // QN format: path::impl<Trait for Type>::method:line
    func_qn.contains("::impl<") && func_qn.contains(" for ")
}
```

**Rationale:** Trait impl methods are contractual obligations. If a type implements a trait, those methods exist because something requires the trait. They are always potentially reachable via dynamic dispatch (`&dyn Trait`). Flagging them as dead code is wrong by definition.

This replaces the fragile `COMMON_TRAIT_METHODS` approach entirely.

### Phase 2: Graph Enrichment — Trait Impl Edges

Add a `trait_impls` field to `ParseResult` to emit trait implementation relationships:

```rust
pub struct ParseResult {
    // ... existing fields ...
    /// Trait implementations as (type_name, trait_name) pairs.
    /// Used to create Inherits edges in the graph.
    pub trait_impls: Vec<(String, String)>,
}
```

In `extract_impl_methods()`, when `trait_name` is `Some`, push `(type_name, trait_name)` to `result.trait_impls`.

In graph building (both `graph.rs` and `session.rs`), create `Inherits` edges from type → trait, matching types/traits by name against existing Class nodes in the graph.

**Benefits:** Other detectors (LazyClass, FeatureEnvy, etc.) can reason about trait relationships. The graph model becomes more complete.

### Phase 3: Self-Call Pattern Enhancement

Extend `is_called_via_self()` to also detect `Self::method()` patterns:

```rust
let self_call_static = format!("Self::{}(", name);
```

This catches associated function calls through `Self::` which are common in Rust but not currently detected.

## Expected Impact

| Category | Before | After |
|----------|--------|-------|
| Trait impl methods (high) | 19 | 0 |
| Trait impl methods (medium) | ~40 | 0 |
| Self-call FPs | ~10 | 0 |
| Genuine dead code | ~5 | ~5 |
| **Total** | **100** | **~5-10** |

## What Changes

| File | Change |
|------|--------|
| `src/detectors/dead_code/mod.rs` | Replace `COMMON_TRAIT_METHODS` with `is_trait_impl_method()` QN check |
| `src/detectors/dead_code/mod.rs` | Extend `is_called_via_self` with `Self::method()` pattern |
| `src/parsers/mod.rs` | Add `trait_impls: Vec<(String, String)>` to ParseResult |
| `src/parsers/rust.rs` | Emit trait_impls in `extract_impl_methods()` |
| `src/cli/analyze/graph.rs` | Create Inherits edges from trait_impls |
| `src/session.rs` | Create Inherits edges from trait_impls |
| `src/parsers/streaming.rs` | Wire trait_impls through streaming pipeline |
| `src/parsers/bounded_pipeline.rs` | Wire trait_impls through bounded pipeline |

## What Does NOT Change

- CodeNode struct (no new flags — QN pattern is sufficient)
- Other language parsers (Python/Java already handle inheritance via `class.bases`)
- EdgeKind enum (existing `Inherits` edge type is correct)
- Other detectors (they benefit passively from new Inherits edges)
