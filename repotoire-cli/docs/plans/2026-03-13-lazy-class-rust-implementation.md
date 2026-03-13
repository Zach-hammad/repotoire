# LazyClass Rust-Aware Detection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate 163 false positives from LazyClassDetector on Rust by adding field counting to the parser and multi-dimensional type evaluation to the detector.

**Architecture:** Three layers of change: (1) Rust parser counts struct fields/enum variants, (2) CodeNode gains a `field_count` field wired through the streaming pipeline, (3) LazyClass detector evaluates Rust types on fields + methods + trait impls instead of just method count.

**Tech Stack:** Rust, tree-sitter-rust, petgraph

---

### Task 1: Add `field_count` to `Class` Model

**Files:**
- Modify: `src/models.rs:228-243`

**Step 1: Add field to Class struct**

Add `field_count: usize` after `methods`:

```rust
pub struct Class {
    pub name: String,
    pub qualified_name: String,
    pub file_path: PathBuf,
    pub line_start: u32,
    pub line_end: u32,
    pub methods: Vec<String>,
    pub field_count: usize,  // NEW: struct fields or enum variants
    pub bases: Vec<String>,
    pub doc_comment: Option<String>,
    pub annotations: Vec<String>,
}
```

Add `#[serde(default)]` on `field_count` for backward compat with any serialized Class data.

**Step 2: Fix all construction sites**

Every place that constructs a `Class` needs `field_count: 0` added. This includes:
- `src/parsers/rust.rs` — `parse_struct_node`, `parse_enum_node`, `parse_trait_node` (will be updated in Task 3)
- `src/parsers/python.rs`, `src/parsers/typescript.rs`, `src/parsers/go.rs`, `src/parsers/java.rs`, `src/parsers/csharp.rs`, `src/parsers/cpp.rs`, `src/parsers/c.rs`, `src/parsers/fallback.rs`
- Any test code that constructs Class literals

Run: `cargo check` to find all sites, add `field_count: 0` to each.

**Step 3: Verify compilation**

Run: `cargo check`
Expected: Clean compilation

**Step 4: Commit**

```bash
git add src/models.rs src/parsers/*.rs
git commit -m "feat: add field_count to Class model"
```

---

### Task 2: Add `field_count` to `CodeNode` and Pipeline

**Files:**
- Modify: `src/graph/store_models.rs:20-43` (CodeNode struct)
- Modify: `src/graph/store_models.rs:55-72` (CodeNode::empty)
- Modify: `src/graph/store_models.rs:271-319` (get_i64)
- Modify: `src/graph/store_models.rs:150-184` (with_property)
- Modify: `src/parsers/streaming.rs:95-104` (ClassInfo struct)
- Modify: `src/parsers/streaming.rs:143-156` (from_parse_result)
- Modify: `src/cli/analyze/graph.rs:164-190` (build_class_node)

**Step 1: Add `field_count: u16` to CodeNode**

In `src/graph/store_models.rs`, add after `method_count`:

```rust
pub method_count: u16,
pub field_count: u16,   // NEW: struct fields / enum variants
```

Update `CodeNode::empty()` to include `field_count: 0`.

Update `CodeNode::new()` and any other constructors/builder methods to include `field_count: 0`.

**Step 2: Update `get_i64`**

Add case in the match:

```rust
"fieldCount" | "field_count" => {
    if self.field_count > 0 {
        Some(self.field_count as i64)
    } else {
        None
    }
}
```

**Step 3: Update `with_property`**

Add case in the match:

```rust
"fieldCount" | "field_count" => {
    self.field_count = val.as_i64().unwrap_or(0) as u16;
}
```

**Step 4: Add `field_count` to ClassInfo**

In `src/parsers/streaming.rs`, add to `ClassInfo`:

```rust
pub struct ClassInfo {
    pub name: String,
    pub qualified_name: String,
    pub line_start: u32,
    pub line_end: u32,
    pub method_count: usize,
    pub field_count: usize,  // NEW
    pub methods: Vec<String>,
    pub bases: Vec<String>,
}
```

Update `from_parse_result` to wire it:

```rust
.map(|c| ClassInfo {
    name: c.name,
    qualified_name: c.qualified_name,
    line_start: c.line_start,
    line_end: c.line_end,
    method_count: c.methods.len(),
    field_count: c.field_count,  // NEW
    methods: c.methods,
    bases: c.bases,
})
```

**Step 5: Wire into `build_class_node`**

In `src/cli/analyze/graph.rs`, find `build_class_node` and add:

```rust
field_count: class.methods.len().min(65535) as u16,  // existing method_count line
```

Wait — `build_class_node` takes a `Class` directly. Update to:

```rust
method_count: class.methods.len().min(65535) as u16,
field_count: class.field_count.min(65535) as u16,  // NEW
```

Also find any other place that builds CodeNode for classes (e.g., streaming pipeline's `build_class_node_from_info`) and add `field_count` there too. Search for `method_count:` in `graph.rs` and `streaming.rs` to find all sites.

**Step 6: Verify compilation**

Run: `cargo check`
Expected: Clean compilation (may need to fix additional construction sites found by compiler)

**Step 7: Run tests**

Run: `cargo test`
Expected: All tests pass

**Step 8: Commit**

```bash
git add src/graph/store_models.rs src/parsers/streaming.rs src/cli/analyze/graph.rs
git commit -m "feat: add field_count to CodeNode and pipeline"
```

---

### Task 3: Rust Parser — Count Struct Fields and Enum Variants

**Files:**
- Modify: `src/parsers/rust.rs:243-267` (parse_struct_node)
- Modify: `src/parsers/rust.rs:270-294` (parse_enum_node)

**Step 1: Add field counting to `parse_struct_node`**

After getting `name`, `line_start`, `line_end`, count fields:

```rust
// Count struct fields
let field_count = node
    .child_by_field_name("body")
    .map(|body| {
        let mut count = 0;
        let mut cursor = body.walk();
        for child in body.children(&mut cursor) {
            if child.kind() == "field_declaration" {
                count += 1;
            }
        }
        count
    })
    .unwrap_or(0);

// Also handle tuple structs: children that are ordered_field_declaration_list
let tuple_field_count = {
    let mut count = 0;
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() == "ordered_field_declaration_list" {
            let mut inner_cursor = child.walk();
            for inner in child.children(&mut inner_cursor) {
                if inner.kind() == "ordered_field_declaration" {
                    count += 1;
                }
            }
        }
    }
    count
};

let total_fields = field_count + tuple_field_count;
```

Then set `field_count: total_fields` in the returned `Class`.

**Step 2: Add variant counting to `parse_enum_node`**

After getting `name`, count variants:

```rust
let field_count = node
    .child_by_field_name("body")
    .map(|body| {
        let mut count = 0;
        let mut cursor = body.walk();
        for child in body.children(&mut cursor) {
            if child.kind() == "enum_variant" {
                count += 1;
            }
        }
        count
    })
    .unwrap_or(0);
```

Set `field_count` in the returned `Class`.

**Step 3: Write test for field counting**

```rust
#[test]
fn test_struct_field_count() {
    let source = r#"
pub struct Config {
    pub name: String,
    pub value: i32,
    pub enabled: bool,
}

struct Pair(i32, String);

struct Unit;

pub enum Color {
    Red,
    Green,
    Blue,
    Custom(u8, u8, u8),
}
"#;
    let result = parse_source(source, Path::new("test.rs")).unwrap();

    let config = result.classes.iter().find(|c| c.name == "Config").unwrap();
    assert_eq!(config.field_count, 3, "Config has 3 fields");

    let pair = result.classes.iter().find(|c| c.name == "Pair").unwrap();
    assert_eq!(pair.field_count, 2, "Pair tuple struct has 2 fields");

    let unit = result.classes.iter().find(|c| c.name == "Unit").unwrap();
    assert_eq!(unit.field_count, 0, "Unit struct has 0 fields");

    let color = result.classes.iter().find(|c| c.name == "Color").unwrap();
    assert_eq!(color.field_count, 4, "Color enum has 4 variants");
}
```

**Step 4: Run test**

Run: `cargo test test_struct_field_count`
Expected: PASS

**Step 5: Commit**

```bash
git add src/parsers/rust.rs
git commit -m "feat: count struct fields and enum variants in Rust parser"
```

---

### Task 4: Fix Generic QN Pattern Matching

**Files:**
- Modify: `src/detectors/lazy_class.rs:189-207` (count_rust_impl_methods)

**Step 1: Fix the matching logic**

Replace the current implementation:

```rust
fn count_rust_impl_methods(
    file_funcs: &[&crate::graph::store_models::CodeNode],
    type_name: &str,
    interner: &crate::graph::interner::StringInterner,
) -> usize {
    // Match patterns like:
    //   path::impl<TypeName>::method:line
    //   path::impl<TypeName<'g>>::method:line
    //   path::impl<Trait for TypeName>::method:line
    //   path::impl<Trait for TypeName<'g>>::method:line
    let impl_prefix = format!("impl<{}", type_name);
    let trait_infix = format!(" for {}", type_name);

    file_funcs
        .iter()
        .filter(|f| {
            let qn = f.qn(interner);
            // Direct impl: "impl<TypeName>" or "impl<TypeName<...>>"
            if let Some(pos) = qn.find(&impl_prefix) {
                let after = pos + impl_prefix.len();
                if let Some(&ch) = qn.as_bytes().get(after) {
                    if ch == b'>' || ch == b'<' {
                        return true;
                    }
                }
            }
            // Trait impl: "impl<Trait for TypeName>" or "impl<Trait for TypeName<...>>"
            if let Some(pos) = qn.find(&trait_infix) {
                let after = pos + trait_infix.len();
                if let Some(&ch) = qn.as_bytes().get(after) {
                    if ch == b'>' || ch == b'<' {
                        return true;
                    }
                }
            }
            false
        })
        .count()
}
```

**Step 2: Add test for generic types**

```rust
#[test]
fn test_count_rust_impl_methods_generic() {
    let graph = GraphStore::in_memory();
    let i = graph.interner();

    let funcs = vec![
        CodeNode::function("new", "src/lib.rs")
            .with_qualified_name("src/lib.rs::impl<AnalysisContext<'g>>::new:10")
            .with_lines(10, 15),
        CodeNode::function("graph", "src/lib.rs")
            .with_qualified_name("src/lib.rs::impl<AnalysisContext<'g>>::graph:20")
            .with_lines(20, 22),
        CodeNode::function("fmt", "src/lib.rs")
            .with_qualified_name("src/lib.rs::impl<Debug for AnalysisContext<'g>>::fmt:30")
            .with_lines(30, 35),
    ];
    for f in &funcs {
        graph.add_node(f.clone());
    }

    let file_funcs = graph.get_functions_in_file("src/lib.rs");
    let refs: Vec<&crate::graph::store_models::CodeNode> = file_funcs.iter().collect();

    let count = LazyClassDetector::count_rust_impl_methods(&refs, "AnalysisContext", i);
    assert_eq!(count, 3, "Should count all 3 methods for generic AnalysisContext<'g>");
}
```

**Step 3: Run tests**

Run: `cargo test lazy_class`
Expected: All pass including new generic test

**Step 4: Commit**

```bash
git add src/detectors/lazy_class.rs
git commit -m "fix: generic QN pattern matching in count_rust_impl_methods"
```

---

### Task 5: Add `count_rust_trait_impls` and Multi-Dimensional Evaluation

**Files:**
- Modify: `src/detectors/lazy_class.rs:189-207` (add new method after count_rust_impl_methods)
- Modify: `src/detectors/lazy_class.rs:431-453` (replace Rust block)

**Step 1: Add `count_rust_trait_impls` method**

Add after `count_rust_impl_methods`:

```rust
/// Count distinct trait implementations for a Rust type.
///
/// Counts unique `impl<Trait for TypeName>` patterns in qualified names.
/// This measures the type's "contract fulfillment" — how many traits it
/// manually implements (as opposed to derive macros which aren't visible
/// to tree-sitter).
fn count_rust_trait_impls(
    file_funcs: &[&crate::graph::store_models::CodeNode],
    type_name: &str,
    interner: &crate::graph::interner::StringInterner,
) -> usize {
    let trait_infix = format!(" for {}", type_name);

    let mut trait_names: HashSet<&str> = HashSet::new();

    for f in file_funcs {
        let qn = f.qn(interner);
        if let Some(pos) = qn.find(&trait_infix) {
            let after = pos + trait_infix.len();
            if let Some(&ch) = qn.as_bytes().get(after) {
                if ch == b'>' || ch == b'<' {
                    // Extract trait name from "impl<TraitName for TypeName>"
                    // Find the "impl<" before the trait name
                    if let Some(impl_start) = qn[..pos].rfind("impl<") {
                        let trait_start = impl_start + 5; // len("impl<")
                        let trait_name = &qn[trait_start..pos];
                        trait_names.insert(trait_name);
                    }
                }
            }
        }
    }

    trait_names.len()
}
```

**Step 2: Replace Rust evaluation block**

Replace lines 431-453 (the current Rust-specific block) with:

```rust
if let Some(ref func_refs) = file_func_refs {
    let type_name = class.node_name(i);
    let field_count = class.field_count as usize;
    let impl_methods =
        Self::count_rust_impl_methods(func_refs, type_name, i);
    let trait_impls =
        Self::count_rust_trait_impls(func_refs, type_name, i);

    // Multi-dimensional evaluation: a Rust type provides value if
    // ANY dimension shows substance.
    //
    // - Fields >= 2: groups related data (data container)
    // - Impl methods >= 2: has behavior (functional type)
    // - Trait impls >= 2: fulfills contracts (polymorphic type)
    //
    // Types failing all three still face the external callers
    // check downstream.
    if field_count >= 2 || impl_methods >= 2 || trait_impls >= 2 {
        debug!(
            "Skipping Rust type {} - fields={}, impl_methods={}, trait_impls={} (provides value)",
            type_name, field_count, impl_methods, trait_impls
        );
        continue;
    }
}
```

**Step 3: Add test for trait impl counting**

```rust
#[test]
fn test_count_rust_trait_impls() {
    let graph = GraphStore::in_memory();
    let i = graph.interner();

    let funcs = vec![
        CodeNode::function("new", "src/lib.rs")
            .with_qualified_name("src/lib.rs::impl<Foo>::new:10")
            .with_lines(10, 15),
        CodeNode::function("fmt", "src/lib.rs")
            .with_qualified_name("src/lib.rs::impl<Display for Foo>::fmt:20")
            .with_lines(20, 25),
        CodeNode::function("default", "src/lib.rs")
            .with_qualified_name("src/lib.rs::impl<Default for Foo>::default:30")
            .with_lines(30, 35),
        CodeNode::function("from", "src/lib.rs")
            .with_qualified_name("src/lib.rs::impl<From<i32> for Foo>::from:40")
            .with_lines(40, 45),
    ];
    for f in &funcs {
        graph.add_node(f.clone());
    }

    let file_funcs = graph.get_functions_in_file("src/lib.rs");
    let refs: Vec<&crate::graph::store_models::CodeNode> = file_funcs.iter().collect();

    let count = LazyClassDetector::count_rust_trait_impls(&refs, "Foo", i);
    assert_eq!(count, 3, "Should count 3 distinct trait impls: Display, Default, From<i32>");
}
```

**Step 4: Add integration test for multi-dimensional evaluation**

```rust
#[test]
fn test_rust_struct_with_fields_not_lazy() {
    // A struct with 3+ fields should NOT be flagged as lazy
    // even if it has 0 methods
    let graph = GraphStore::in_memory();
    let i = graph.interner();

    // Add a struct with field_count=3 but no methods
    let class_node = CodeNode::class("Config", "src/config.rs")
        .with_lines(1, 10);
    // Set field_count = 3
    let class_node = CodeNode { field_count: 3, ..class_node };
    graph.add_node(class_node);

    let detector = LazyClassDetector::new("/mock");
    let ctx = crate::detectors::analysis_context::AnalysisContext::test(&graph);
    let findings = detector.detect(&ctx).unwrap();

    assert!(
        !findings.iter().any(|f| f.title.contains("Config")),
        "Struct with 3 fields should not be flagged as lazy"
    );
}
```

**Step 5: Run all tests**

Run: `cargo test lazy_class`
Expected: All tests pass

**Step 6: Commit**

```bash
git add src/detectors/lazy_class.rs
git commit -m "feat: multi-dimensional Rust type evaluation in LazyClass detector"
```

---

### Task 6: Validation — Self-Analysis

**Step 1: Build release**

Run: `cargo build --release`

**Step 2: Run self-analysis**

Run: `./target/release/repotoire analyze . --format json --output /tmp/lazy-class-after.json`

**Step 3: Count LazyClass findings**

Run: `cat /tmp/lazy-class-after.json | jq '[.findings[] | select(.detector == "LazyClassDetector")] | length'`

Expected: Near 0 (down from 163)

**Step 4: Spot-check any remaining findings**

If any LazyClass findings remain, verify they are true positives (types that genuinely have <2 fields, <2 methods, <2 trait impls, and <5 callers).

**Step 5: Verify no regressions**

Run: `cargo test`
Expected: All tests pass

**Step 6: Commit validation results** (if any adjustments needed)

```bash
git add -A
git commit -m "fix: address validation feedback from self-analysis"
```
