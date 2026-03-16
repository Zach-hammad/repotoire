# Dead Code FP Reduction Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate false positives in the DeadCode detector by recognizing trait impl methods from QN patterns and enriching the graph with trait implementation edges.

**Architecture:** Two-phase approach — Phase 1 fixes the detector with QN pattern detection (immediate FP elimination), Phase 2 enriches the graph model with Inherits edges for trait implementations (benefits all detectors).

**Tech Stack:** Rust, tree-sitter, petgraph

---

## Phase 1: Dead Code Detector Fix

### Task 1: Replace COMMON_TRAIT_METHODS with QN Pattern Check

**Files:**
- Modify: `src/detectors/dead_code/mod.rs`

**Step 1: Add `is_trait_impl_method()` function**

Replace the existing `is_common_trait_method()` function (line 132-136) with:

```rust
/// Check if a function is a trait implementation method.
///
/// Trait impl methods have QN format: `path::impl<TraitName for TypeName>::method:line`
/// These are called via dynamic dispatch (`&dyn Trait`) which is invisible
/// to the static call graph. They should never be flagged as dead code.
fn is_trait_impl_method(func_qn: &str) -> bool {
    // QN format for trait impls: path::impl<Trait for Type>::method:line
    // QN format for inherent impls: path::impl<Type>::method:line
    // The " for " distinguishes trait impls from inherent impls.
    func_qn.contains("::impl<") && func_qn.contains(" for ")
}
```

**Step 2: Replace the call site in `find_dead_functions()`**

Change line 437 from:
```rust
if Self::is_common_trait_method(name) {
```
to:
```rust
if Self::is_trait_impl_method(func_qn) {
```

And update the debug message on line 438:
```rust
debug!("Skipping trait impl method: {}", func_qn);
```

**Step 3: Remove `COMMON_TRAIT_METHODS` constant and `is_common_trait_method()`**

Delete lines 23-28 (the `COMMON_TRAIT_METHODS` constant) and lines 132-136 (the `is_common_trait_method` function).

**Step 4: Run `cargo check`**

Run: `cargo check`
Expected: Compiles with no errors.

**Step 5: Run existing tests**

Run: `cargo test dead_code`
Expected: All existing tests pass.

**Step 6: Commit**

```bash
git add src/detectors/dead_code/mod.rs
git commit -m "feat: replace COMMON_TRAIT_METHODS with structural QN pattern check in DeadCode detector"
```

---

### Task 2: Extend Self-Call Detection

**Files:**
- Modify: `src/detectors/dead_code/mod.rs`

**Step 1: Add `Self::method()` pattern to `is_called_via_self()`**

In `is_called_via_self()` (line 163-185), after the existing `self.method(` and `self.method,` checks, add `Self::method(` pattern:

Change the existing check block (lines 168-171) to:

```rust
if let Some(entry) = ctx.files.get(path) {
    let self_call = format!("self.{}(", name);
    let self_call_alt = format!("self.{},", name); // Passed as closure
    let self_static = format!("Self::{}(", name); // Associated function via Self
    if entry.content.contains(&self_call)
        || entry.content.contains(&self_call_alt)
        || entry.content.contains(&self_static)
    {
        return true;
    }
}
```

And the fallback block (lines 175-179):

```rust
if let Some(content) = crate::cache::global_cache().content(path) {
    let self_call = format!("self.{}(", name);
    let self_call_alt = format!("self.{},", name);
    let self_static = format!("Self::{}(", name);
    if content.contains(&self_call)
        || content.contains(&self_call_alt)
        || content.contains(&self_static)
    {
        return true;
    }
}
```

**Step 2: Run `cargo check`**

Run: `cargo check`
Expected: Compiles.

**Step 3: Commit**

```bash
git add src/detectors/dead_code/mod.rs
git commit -m "feat: detect Self::method() calls in dead code detector"
```

---

### Task 3: Phase 1 Validation

**Step 1: Clean cache**

Run: `repotoire clean .`

**Step 2: Run self-analysis**

Run: `cargo run --release -- analyze . --format json --output /tmp/dead-code-after.json`

**Step 3: Count DeadCode findings**

Run: `cat /tmp/dead-code-after.json | python3 -c "import json,sys; d=json.load(sys.stdin); findings=[f for f in d.get('findings',[]) if f['detector']=='DeadCodeDetector']; print(f'Total: {len(findings)}'); sevs={}; [sevs.__setitem__(f['severity'], sevs.get(f['severity'],0)+1) for f in findings]; print(sevs)"`

Expected: Total drops from 100 to ~5-15. High severity drops from 19 to 0.

**Step 4: Review remaining findings**

Manually review the remaining findings to verify they are genuine dead code or edge cases.

**Step 5: Commit validation results as a comment in the commit message or note**

---

## Phase 2: Graph Enrichment — Trait Impl Edges

### Task 4: Add `trait_impls` Field to ParseResult

**Files:**
- Modify: `src/parsers/mod.rs`

**Step 1: Add field to ParseResult struct**

In `ParseResult` (line 525), add after the `address_taken` field (line 540):

```rust
    /// Trait implementations as (type_name, trait_name) pairs.
    /// Used to create Inherits edges from implementing types to traits.
    pub trait_impls: Vec<(String, String)>,
```

**Step 2: Update `ParseResult::merge()`**

In `merge()` (line 555), add after `self.address_taken.extend(other.address_taken);` (line 560):

```rust
        self.trait_impls.extend(other.trait_impls);
```

**Step 3: Run `cargo check`**

Run: `cargo check`
Expected: Compiles (Vec has Default as empty vec).

**Step 4: Commit**

```bash
git add src/parsers/mod.rs
git commit -m "feat: add trait_impls field to ParseResult"
```

---

### Task 5: Emit Trait Impls from Rust Parser

**Files:**
- Modify: `src/parsers/rust.rs`

**Step 1: Push trait_impl in `extract_impl_methods()`**

In `extract_impl_methods()` (line 412-452), after the `trait_name` extraction (line 429), add:

```rust
    // Record trait implementation relationship for graph enrichment
    if let Some(ref trait_n) = trait_name {
        result.trait_impls.push((type_name.clone(), trait_n.clone()));
    }
```

Place this after line 431 (after `let impl_line = ...`), before the body iteration.

**Step 2: Run `cargo check`**

Run: `cargo check`
Expected: Compiles.

**Step 3: Add test**

Add a test to verify trait_impls are emitted:

```rust
#[test]
fn test_trait_impl_relationships() {
    let source = r#"
trait MyTrait {
    fn do_thing(&self);
}

struct MyStruct;

impl MyTrait for MyStruct {
    fn do_thing(&self) {}
}

impl MyStruct {
    fn new() -> Self { MyStruct }
}
"#;
    let result = parse_rust_source(source, Path::new("test.rs")).unwrap();
    // Should have one trait impl: (MyStruct, MyTrait)
    assert_eq!(result.trait_impls.len(), 1);
    assert_eq!(result.trait_impls[0].0, "MyStruct");
    assert_eq!(result.trait_impls[0].1, "MyTrait");
}
```

**Step 4: Run test**

Run: `cargo test test_trait_impl_relationships`
Expected: PASS

**Step 5: Commit**

```bash
git add src/parsers/rust.rs
git commit -m "feat: emit trait implementation relationships from Rust parser"
```

---

### Task 6: Wire trait_impls Through Streaming Pipeline

**Files:**
- Modify: `src/parsers/streaming.rs`

**Step 1: Add `trait_impls` to `ParsedFileInfo` struct**

In the `ParsedFileInfo` struct, add:

```rust
    pub trait_impls: Vec<(String, String)>,
```

**Step 2: Wire in `from_parse_result()`**

In `ParsedFileInfo::from_parse_result()`, add (alongside the `calls` assignment):

```rust
            trait_impls: result.trait_impls,
```

**Step 3: Wire in `to_parse_result()`**

In `ParsedFileInfo::to_parse_result()`, add in the ParseResult construction:

```rust
            trait_impls: self.trait_impls.clone(),
```

**Step 4: Run `cargo check`**

Run: `cargo check`
Expected: Compiles.

**Step 5: Commit**

```bash
git add src/parsers/streaming.rs
git commit -m "feat: wire trait_impls through streaming pipeline"
```

---

### Task 7: Create Inherits Edges from Trait Impls in Graph Building

**Files:**
- Modify: `src/cli/analyze/graph.rs`
- Modify: `src/session.rs`

**Step 1: Add trait_impl edges in `session.rs`**

In `build_file_graph()`, after the existing Inherits edge creation loop for `class.bases` (around line 1244), add:

```rust
    // Trait implementation edges (type -> trait)
    for (type_name, trait_name) in &result.trait_impls {
        // Find the type's qualified name in this file's classes
        let type_qn = result.classes.iter()
            .find(|c| c.name == *type_name)
            .map(|c| c.qualified_name.clone());
        // Find the trait's qualified name in this file's classes
        let trait_qn = result.classes.iter()
            .find(|c| c.name == *trait_name)
            .map(|c| c.qualified_name.clone())
            .unwrap_or_else(|| trait_name.clone());

        if let Some(type_qn) = type_qn {
            edges.push((
                type_qn,
                trait_qn,
                CodeEdge::inherits(),
            ));
        }
    }
```

**Step 2: Add trait_impl edges in `graph.rs`**

Find where `build_call_edges_fast()` is called in graph.rs, and after the imports edge creation, add similar logic for trait_impls. The pattern should match the session.rs approach — iterate `result.trait_impls`, resolve type/trait QNs from the file's classes, and create Inherits edges.

**Step 3: Run `cargo check`**

Run: `cargo check`
Expected: Compiles.

**Step 4: Run full test suite**

Run: `cargo test`
Expected: All tests pass.

**Step 5: Commit**

```bash
git add src/cli/analyze/graph.rs src/session.rs
git commit -m "feat: create Inherits edges for trait implementations in graph building"
```

---

### Task 8: Final Validation

**Step 1: Clean cache and run analysis**

```bash
repotoire clean .
cargo run --release -- analyze . --format json --output /tmp/dead-code-final.json
```

**Step 2: Verify DeadCode findings**

Count and review remaining DeadCode findings. Expected: ~5-10 total, 0 high severity.

**Step 3: Verify graph has Inherits edges for trait impls**

```bash
cargo run --release -- graph . --query "edges" | grep -i "inherits" | head -20
```

**Step 4: Run full test suite one final time**

```bash
cargo test
```

Expected: All tests pass.
