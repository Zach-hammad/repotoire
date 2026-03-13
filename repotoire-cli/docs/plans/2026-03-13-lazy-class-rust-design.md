# LazyClass Rust-Aware Detection Design

**Goal:** Eliminate 163 false positives from LazyClassDetector on Rust codebases by properly evaluating Rust types across multiple value dimensions instead of applying OOP assumptions.

## Problem

All 163 LazyClass findings on Rust self-analysis are false positives. Every one is a Rust struct with `methodCount=0` (because Rust methods live in `impl` blocks, not inside `struct {}`) and 0 detected external callers.

Two root causes:

1. **Generic QN pattern matching bug** — `count_rust_impl_methods()` builds `"impl<TypeName>"` which fails to match QNs like `"impl<TypeName<'g>>"` because the `>` hits `<'g>` instead of closing the pattern. Affects 6 generic types.

2. **Missing data dimension** — 157 types are genuinely method-less data containers (idiomatic Rust). The detector only checks method count, with no way to know these structs have multiple fields and serve as proper data containers.

## Design: Multi-Dimensional Type Evaluation

Instead of skipping Rust types or applying blanket thresholds, evaluate each type across three value dimensions plus the existing usage check:

| Dimension | What it measures | Source | "Provides value" threshold |
|-----------|-----------------|--------|---------------------------|
| **Data structuring** | Fields in the struct/enum variants | New `field_count` on `CodeNode`, populated by Rust parser | 2+ fields/variants |
| **Behavior** | Methods in impl blocks | Fixed `count_rust_impl_methods()` (generic QN bug fix) | 2+ impl methods |
| **Type contracts** | Distinct manually-written trait impls | New `count_rust_trait_impls()` counting unique `impl<Trait for Type>` patterns | 2+ trait impls |
| **Usage** | External callers of type's methods | Existing `count_external_callers()` check downstream | 5+ callers (existing) |

A Rust type is flagged as lazy **only if ALL dimensions show no value**.

### Why No Derive Dimension

`#[derive(Debug, Clone)]` is near-universal on Rust types — counting it would exempt everything. And tree-sitter doesn't expand macros, so derive-generated `impl` blocks don't appear in the graph. The three structural dimensions plus existing usage check are sufficient.

## Three Implementation Changes

### 1. Rust Parser — Field Counting

In `src/parsers/rust.rs`:
- `parse_struct_node()`: Count `field_declaration` children (named struct) or `ordered_field_declaration` children (tuple struct). Unit structs get 0.
- `parse_enum_node()`: Count `enum_variant` children.
- Store count in a new field on the `Class` model.

### 2. CodeNode — `field_count` Field

In `src/graph/store_models.rs`:
- Add `pub field_count: u16` to `CodeNode` alongside existing `method_count: u16`.
- Update `get_i64()` to handle `"fieldCount"` key.
- Update `with_property()` to handle `"fieldCount"` key.
- Wire through `build_class_node()` in `src/cli/analyze/graph.rs` and `ParsedFileInfo` in `src/parsers/streaming.rs`.

### 3. LazyClass Detector — Multi-Dimensional Evaluation

In `src/detectors/lazy_class.rs`:

**Fix generic QN matching** in `count_rust_impl_methods()`:
- Current: `format!("impl<{}>", type_name)` — fails for generic types
- Fixed: `format!("impl<{}", type_name)` then verify next char is `>` or `<`

**Add `count_rust_trait_impls()`**: Count distinct `impl<Trait for Type>` patterns using the same fixed matching logic.

**Replace Rust block (lines 431-453)** with multi-dimensional check:
```rust
let field_count = class.field_count as usize;
let impl_methods = Self::count_rust_impl_methods(func_refs, type_name, i);
let trait_impls = Self::count_rust_trait_impls(func_refs, type_name, i);

// Type provides value if ANY dimension shows substance
if field_count >= 2 || impl_methods >= 2 || trait_impls >= 2 {
    continue; // not lazy — provides value through data, behavior, or contracts
}
// Types that fail all three still face the external callers check downstream
```

## Expected Impact

- 163 LazyClass FPs on self-analysis -> near zero (most Rust structs have 2+ fields)
- No false negatives: truly empty/useless types still flagged
- Proper evaluation, not skipping — every Rust type measured on its merits
