# CompactNode Full Type Migration — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace CodeNode (~200 bytes, heap-allocated) with a compact ~40-byte `Copy` struct using string interning for 5x memory reduction and cache locality.

**Architecture:** Flag-day migration — CodeNode/CodeEdge structs change, then every consumer is updated. The interner already lives in `GraphStore.interner`. Parsers produce StrKey-based nodes. Detectors use `StrKey` for HashMap keys/equality, resolving to `&str` only for text operations. Compilation is broken after Task 1 and restored by the end of Task 6.

**Tech Stack:** Rust, petgraph, lasso (string interning), serde

**Design doc:** `docs/plans/2026-03-10-compact-node-migration-design.md`

---

## Important Notes

- **Tasks 1-6 are interdependent.** The codebase will NOT compile between tasks. Only run `cargo check` after Task 6 is complete.
- **Task 7 restores tests.** Run `cargo test` after Task 7.
- **Task 8 is the benchmark.** Run CPython analysis to verify speed.
- Use `cargo check 2>&1 | head -30` to track remaining compilation errors as you work through Tasks 2-6.

---

### Task 1: Redesign CodeNode and CodeEdge structs

**Files:**
- Modify: `repotoire-cli/src/graph/store_models.rs`
- Modify: `repotoire-cli/src/graph/interner.rs`

**Step 1: Rewrite `store_models.rs`**

Replace the entire CodeNode and CodeEdge with compact versions. Keep `NodeKind` and `EdgeKind` enums unchanged.

```rust
use crate::graph::interner::StrKey;
use serde::{Deserialize, Serialize};

/// Node types in the code graph
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum NodeKind {
    File,
    Function,
    Class,
    Module,
    Variable,
    Commit,
}

/// A node in the code graph — compact, Copy, ~40 bytes.
///
/// All string fields are interned StrKeys. Use `graph.interner().resolve(key)`
/// or the helper methods `qn()`, `path()`, `node_name()` to get `&str`.
/// Metric fields replace the old `properties: HashMap<String, Value>`.
#[derive(Debug, Clone, Copy)]
pub struct CodeNode {
    // Identity
    pub kind: NodeKind,
    pub name: StrKey,
    pub qualified_name: StrKey,
    pub file_path: StrKey,
    pub language: StrKey, // EMPTY_KEY = no language

    // Location
    pub line_start: u32,
    pub line_end: u32,

    // Metrics (typed fields, no HashMap)
    pub complexity: u16,
    pub param_count: u8,
    pub method_count: u16,
    pub max_nesting: u8,
    pub return_count: u8,
    pub commit_count: u16,

    // Packed boolean flags
    pub flags: u8,
}

// Flag bit positions
const FLAG_IS_ASYNC: u8 = 1 << 0;
const FLAG_IS_EXPORTED: u8 = 1 << 1;
const FLAG_IS_PUBLIC: u8 = 1 << 2;
const FLAG_IS_METHOD: u8 = 1 << 3;
const FLAG_ADDRESS_TAKEN: u8 = 1 << 4;
const FLAG_HAS_DECORATORS: u8 = 1 << 5;

impl CodeNode {
    /// Create an empty node with the given kind and all StrKeys set to EMPTY_KEY.
    /// Prefer the specific constructors `file()`, `function()`, `class()`.
    pub fn empty(kind: NodeKind, empty_key: StrKey) -> Self {
        Self {
            kind,
            name: empty_key,
            qualified_name: empty_key,
            file_path: empty_key,
            language: empty_key,
            line_start: 0,
            line_end: 0,
            complexity: 0,
            param_count: 0,
            method_count: 0,
            max_nesting: 0,
            return_count: 0,
            commit_count: 0,
            flags: 0,
        }
    }

    // --- Flag accessors ---

    pub fn is_async(&self) -> bool { self.flags & FLAG_IS_ASYNC != 0 }
    pub fn is_exported(&self) -> bool { self.flags & FLAG_IS_EXPORTED != 0 }
    pub fn is_public(&self) -> bool { self.flags & FLAG_IS_PUBLIC != 0 }
    pub fn is_method(&self) -> bool { self.flags & FLAG_IS_METHOD != 0 }
    pub fn address_taken(&self) -> bool { self.flags & FLAG_ADDRESS_TAKEN != 0 }
    pub fn has_decorators(&self) -> bool { self.flags & FLAG_HAS_DECORATORS != 0 }

    pub fn set_flag(&mut self, flag: u8) { self.flags |= flag; }

    // --- Metric accessors (backward compat shims) ---

    /// Lines of code
    pub fn loc(&self) -> u32 {
        if self.line_end >= self.line_start {
            self.line_end - self.line_start + 1
        } else {
            1
        }
    }

    /// Cyclomatic complexity (Option for backward compat — returns None if 0)
    pub fn complexity(&self) -> Option<i64> {
        if self.complexity > 0 { Some(self.complexity as i64) } else { None }
    }

    /// Parameter count (Option for backward compat — returns None if 0)
    pub fn param_count(&self) -> Option<i64> {
        if self.param_count > 0 { Some(self.param_count as i64) } else { None }
    }

    // --- String resolution helpers ---
    // These take &StringInterner to resolve StrKeys to &str.

    pub fn qn<'a>(&self, i: &'a crate::graph::interner::StringInterner) -> &'a str {
        i.resolve(self.qualified_name)
    }

    pub fn path<'a>(&self, i: &'a crate::graph::interner::StringInterner) -> &'a str {
        i.resolve(self.file_path)
    }

    pub fn node_name<'a>(&self, i: &'a crate::graph::interner::StringInterner) -> &'a str {
        i.resolve(self.name)
    }

    pub fn lang<'a>(&self, i: &'a crate::graph::interner::StringInterner) -> Option<&'a str> {
        let s = i.resolve(self.language);
        if s.is_empty() { None } else { Some(s) }
    }

    // --- Backward compat shims for property access ---
    // These let old code like `func.get_i64("complexity")` keep compiling
    // during migration. Remove after all callers are updated.

    pub fn get_i64(&self, key: &str) -> Option<i64> {
        match key {
            "complexity" => if self.complexity > 0 { Some(self.complexity as i64) } else { None },
            "paramCount" => if self.param_count > 0 { Some(self.param_count as i64) } else { None },
            "methodCount" => if self.method_count > 0 { Some(self.method_count as i64) } else { None },
            "maxNesting" | "nesting_depth" => if self.max_nesting > 0 { Some(self.max_nesting as i64) } else { None },
            "returnCount" => if self.return_count > 0 { Some(self.return_count as i64) } else { None },
            "commit_count" => if self.commit_count > 0 { Some(self.commit_count as i64) } else { None },
            "lineEnd" => Some(self.line_end as i64),
            "loc" => Some(self.loc() as i64),
            _ => None,
        }
    }

    pub fn get_bool(&self, key: &str) -> Option<bool> {
        match key {
            "is_async" => Some(self.is_async()),
            "is_exported" => Some(self.is_exported()),
            "is_public" => Some(self.is_public()),
            "is_method" => Some(self.is_method()),
            "address_taken" => Some(self.address_taken()),
            "has_decorators" => Some(self.has_decorators()),
            _ => None,
        }
    }

    pub fn get_str(&self, _key: &str) -> Option<&str> {
        // String properties are in the ExtraProps side table.
        // Callers should use graph.extra_props(qn) instead.
        None
    }

    pub fn get_f64(&self, _key: &str) -> Option<f64> {
        None
    }
}

/// Edge types in the code graph
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum EdgeKind {
    Calls,
    Imports,
    Contains,
    Inherits,
    Uses,
    ModifiedIn,
}

/// An edge in the code graph — compact, Copy, ~4 bytes.
#[derive(Debug, Clone, Copy)]
pub struct CodeEdge {
    pub kind: EdgeKind,
    pub flags: u8, // bit 0: is_type_only
}

impl CodeEdge {
    pub fn new(kind: EdgeKind) -> Self {
        Self { kind, flags: 0 }
    }
    pub fn calls() -> Self { Self::new(EdgeKind::Calls) }
    pub fn imports() -> Self { Self::new(EdgeKind::Imports) }
    pub fn contains() -> Self { Self::new(EdgeKind::Contains) }
    pub fn inherits() -> Self { Self::new(EdgeKind::Inherits) }
    pub fn uses() -> Self { Self::new(EdgeKind::Uses) }

    pub fn with_type_only(mut self) -> Self {
        self.flags |= 1;
        self
    }

    pub fn is_type_only(&self) -> bool {
        self.flags & 1 != 0
    }

    // Backward compat shim — old code uses `.with_property("is_type_only", true)`
    pub fn with_property(self, key: &str, value: impl Into<serde_json::Value>) -> Self {
        if key == "is_type_only" {
            let val: serde_json::Value = value.into();
            if val.as_bool().unwrap_or(false) {
                return self.with_type_only();
            }
        }
        self // ignore unknown properties
    }
}

/// Extra properties stored in a side table, not on every node.
/// Only populated for nodes that have these rarely-used fields.
#[derive(Debug, Clone, Default)]
pub struct ExtraProps {
    pub params: Option<StrKey>,
    pub doc_comment: Option<StrKey>,
    pub decorators: Option<StrKey>,
    pub author: Option<StrKey>,
    pub last_modified: Option<StrKey>,
}
```

**Step 2: Add `EMPTY_KEY` to `interner.rs`**

Add a lazily-initialized empty key sentinel to `StringInterner`:

```rust
// In StringInterner impl, add:
/// Get the StrKey for the empty string "".
/// Used as a sentinel for "no value" in CodeNode's optional StrKey fields.
pub fn empty_key(&self) -> StrKey {
    self.intern("")
}
```

**Step 3: Remove old `CompactNode`, `CompactNodeKind`, `CompactEdge`, `CompactEdgeKind`**

Delete the `CompactNode` struct, its `impl` block, `CompactNodeKind`, `CompactEdge`, `CompactEdgeKind` from `interner.rs` (lines 123-299). These are superseded by the redesigned CodeNode/CodeEdge.

**Step 4: Add size assertions**

Add to the bottom of `store_models.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn compact_node_size() {
        assert!(std::mem::size_of::<CodeNode>() <= 48,
            "CodeNode is {} bytes, target ≤48", std::mem::size_of::<CodeNode>());
    }
    #[test]
    fn compact_edge_size() {
        assert!(std::mem::size_of::<CodeEdge>() <= 4,
            "CodeEdge is {} bytes, target ≤4", std::mem::size_of::<CodeEdge>());
    }
}
```

**Step 5: Commit**

```bash
git add repotoire-cli/src/graph/store_models.rs repotoire-cli/src/graph/interner.rs
git commit -m "refactor(graph): redesign CodeNode (~40B Copy) and CodeEdge (~4B Copy)

Replace heap-allocated String fields with StrKey (interned), HashMap
properties with typed struct fields, and boolean properties with
packed flags byte. Add backward-compat shims for get_i64/get_bool
to ease migration. Add ExtraProps for rarely-used string properties."
```

> **Note:** The codebase will NOT compile after this step. Many files reference `CodeNode` fields that no longer exist as Strings. This is expected — continue to Task 2.

---

### Task 2: Update GraphQuery trait and CachedGraphQuery

**Files:**
- Modify: `repotoire-cli/src/graph/traits.rs`
- Modify: `repotoire-cli/src/graph/cached.rs`
- Modify: `repotoire-cli/src/graph/mod.rs` (if re-exports need updating)

**Step 1: Add `interner()` and `extra_props()` to GraphQuery**

In `traits.rs`, add imports and two new methods to the trait:

```rust
use crate::graph::interner::{StringInterner, StrKey};
use crate::graph::store_models::ExtraProps;
```

Add to the `GraphQuery` trait:

```rust
    /// Access the string interner for resolving StrKey → &str.
    fn interner(&self) -> &StringInterner;

    /// Get extra (cold) properties for a node by its qualified_name StrKey.
    fn extra_props(&self, qn: StrKey) -> Option<ExtraProps>;
```

**Step 2: Update `build_call_maps_raw` and `get_call_adjacency` default impls**

These reference `f.qualified_name.clone()` which is now a `StrKey` (Copy). Update:

- `f.qualified_name.clone()` → `f.qualified_name` (StrKey is Copy)
- `HashMap<String, usize>` → `HashMap<StrKey, usize>`
- `caller.as_str()` / `callee.as_str()` — these come from `get_calls()` which still returns `Vec<(String, String)>`. The default impls need to intern these strings to look up StrKeys. OR: change `get_calls()` to return `Vec<(StrKey, StrKey)>`.

**Decision:** Change `get_calls()`, `get_imports()`, `get_inheritance()` to return `Vec<(StrKey, StrKey)>` instead of `Vec<(String, String)>`. This avoids materializing millions of Strings. Update `get_calls_shared()` similarly to `Arc<[(StrKey, StrKey)]>`.

Update all the default impl methods that reference `c.file_path` as `&str` to use `self.interner().resolve(c.file_path)`.

Also update `caller_file_spread()`, `caller_module_spread()`, `count_external_callers_of()`, `get_complex_functions()`, `get_long_param_functions()`, `is_in_import_cycle()`, `find_function_at()`.

**Step 3: Update `CachedGraphQuery` in `cached.rs`**

- Add `interner()` → delegates to `self.inner.interner()`
- Add `extra_props()` → delegates to `self.inner.extra_props(qn)`
- Update cached types: `calls: OnceLock<Arc<[(StrKey, StrKey)]>>`
- Update `qn_to_idx: OnceLock<HashMap<StrKey, usize>>`
- Update all methods that reference `f.qualified_name` as a String to use StrKey
- Remove `.clone()` calls on CodeNode fields that are now Copy
- Update `caller_file_spread()` to use `self.interner().resolve(f.file_path)` for HashSet dedup

**Step 4: Commit**

```bash
git add repotoire-cli/src/graph/traits.rs repotoire-cli/src/graph/cached.rs repotoire-cli/src/graph/mod.rs
git commit -m "refactor(graph): update GraphQuery trait for StrKey-based CodeNode

Add interner() and extra_props() to GraphQuery. Change get_calls/
get_imports/get_inheritance to return (StrKey, StrKey). Update
CachedGraphQuery for StrKey-keyed caches."
```

---

### Task 3: Update GraphStore internals

**Files:**
- Modify: `repotoire-cli/src/graph/store/mod.rs` (~1400 lines)

This is the largest single-file change. Focus areas:

**Step 1: Update index types**

```rust
// Change from:
node_index: DashMap<String, NodeIndex>,
function_spatial_index: DashMap<String, Vec<(u32, u32, NodeIndex)>>,
file_functions_index: DashMap<String, Vec<NodeIndex>>,
file_classes_index: DashMap<String, Vec<NodeIndex>>,
file_all_nodes_index: DashMap<String, Vec<NodeIndex>>,

// Change to:
node_index: DashMap<StrKey, NodeIndex>,
function_spatial_index: DashMap<StrKey, Vec<(u32, u32, NodeIndex)>>,
file_functions_index: DashMap<StrKey, Vec<NodeIndex>>,
file_classes_index: DashMap<StrKey, Vec<NodeIndex>>,
file_all_nodes_index: DashMap<StrKey, Vec<NodeIndex>>,
```

Add `extra_props: DashMap<StrKey, ExtraProps>` field.

**Step 2: Update `add_node()` and `add_nodes_batch()`**

- Node lookups use `node.qualified_name` (StrKey) directly as DashMap key
- Remove all `.to_string()` / `.clone()` calls on identity fields
- Update spatial index insertion to use `node.file_path` (StrKey)
- `add_edge_by_name()` needs to intern the `from_qn`/`to_qn` strings to look up StrKeys

**Step 3: Update query methods**

All `get_*` methods that iterate graph nodes:
- `get_functions()`, `get_classes()`, `get_files()` — return `Vec<CodeNode>` (now Copy, no clone needed — just collect)
- `get_callers()`, `get_callees()` — same pattern
- `get_calls()` — iterate edges, resolve both endpoints' qualified_names, return `Vec<(StrKey, StrKey)>`
- `find_import_cycles()` / `find_cycles_scc()` — internal use of Strings for qualified names needs to switch to StrKeys or resolve at the boundary

**Step 4: Update `update_node_properties()`**

This method is called by git enrichment. It currently takes `&[(key, serde_json::Value)]`. Rewrite it to:
- Set typed fields directly on the graph node for known keys (`commit_count`)
- Write to `extra_props` side table for string properties (`author`, `last_modified`)

```rust
pub fn update_node_properties(&self, qn: &str, props: &[(&str, serde_json::Value)]) {
    let intern_qn = self.interner.intern(qn);
    if let Some(idx) = self.node_index.get(&intern_qn) {
        let mut graph = self.graph.write().unwrap();
        if let Some(node) = graph.node_weight_mut(*idx) {
            let mut extras = ExtraProps::default();
            let mut has_extras = false;
            for (key, value) in props {
                match *key {
                    "commit_count" => {
                        node.commit_count = value.as_i64().unwrap_or(0) as u16;
                    }
                    "author" => {
                        if let Some(s) = value.as_str() {
                            extras.author = Some(self.interner.intern(s));
                            has_extras = true;
                        }
                    }
                    "last_modified" => {
                        if let Some(s) = value.as_str() {
                            extras.last_modified = Some(self.interner.intern(s));
                            has_extras = true;
                        }
                    }
                    _ => {} // ignore unknown properties
                }
            }
            if has_extras {
                drop(graph); // release write lock before DashMap access
                self.extra_props.insert(intern_qn, extras);
            }
        }
    }
}
```

**Step 5: Update persistence (redb save/load)**

- **Save**: Resolve StrKeys to strings before serializing to JSON for redb. Create a serializable intermediate struct.
- **Load**: Intern strings from JSON back to StrKeys when loading from redb.
- **Bincode graph cache**: StrKey is `NonZeroU32`, trivially serializable. But CodeNode now lacks `Serialize`/`Deserialize` derives (StrKey doesn't impl them by default). Add manual Serialize/Deserialize impls that convert StrKey ↔ u32, or use a separate serializable struct for the cache.

**Step 6: Implement `interner()` and `extra_props()` on GraphStore**

```rust
fn interner(&self) -> &StringInterner {
    &self.interner
}

fn extra_props(&self, qn: StrKey) -> Option<ExtraProps> {
    self.extra_props.get(&qn).map(|e| e.clone())
}
```

**Step 7: Commit**

```bash
git add repotoire-cli/src/graph/store/
git commit -m "refactor(graph): update GraphStore for compact CodeNode/CodeEdge

Switch all DashMap indices from String to StrKey. Update add_node,
add_edge, query methods, persistence, and update_node_properties
for the new compact types."
```

---

### Task 4: Update parsers

**Files:**
- Modify: `repotoire-cli/src/parsers/bounded_pipeline.rs` (main pipeline)
- Modify: `repotoire-cli/src/parsers/mod.rs` (if interner access is needed)
- Modify: Individual parsers only if they create CodeNode directly

**Step 1: Update `FlushingGraphBuilder` and node creation**

In `bounded_pipeline.rs`, node creation currently uses the builder pattern:

```rust
// OLD:
CodeNode::function("foo", "src/main.rs")
    .with_qualified_name("module.foo")
    .with_lines(10, 25)
    .with_property("complexity", 5)

// NEW:
let i = &graph.interner; // access via Arc<GraphStore>
CodeNode {
    kind: NodeKind::Function,
    name: i.intern("foo"),
    qualified_name: i.intern("module.foo"),
    file_path: i.intern("src/main.rs"),
    language: i.intern("python"), // or i.empty_key()
    line_start: 10,
    line_end: 25,
    complexity: 5,
    param_count: 0,
    method_count: 0,
    max_nesting: 0,
    return_count: 0,
    commit_count: 0,
    flags: 0,
}
```

The interner is accessible via `Arc<GraphStore>` which is already passed to the pipeline.

**Step 2: Map property assignments to typed fields**

For each `.with_property()` call in bounded_pipeline.rs:

| Old code | New code |
|----------|----------|
| `.with_property("complexity", c as i64)` | `node.complexity = c as u16;` |
| `.with_property("paramCount", p as i64)` | `node.param_count = p as u8;` |
| `.with_property("loc", l as i64)` | *(computed from line_start/line_end, skip)* |
| `.with_property("is_async", true)` | `node.set_flag(FLAG_IS_ASYNC);` |
| `.with_property("address_taken", true)` | `node.set_flag(FLAG_ADDRESS_TAKEN);` |
| `.with_property("nesting_depth", n)` | `node.max_nesting = n as u8;` |
| `.with_property("methodCount", m as i64)` | `node.method_count = m as u16;` |

**Step 3: Update edge creation**

```rust
// OLD:
CodeEdge::imports().with_property("is_type_only", true)

// NEW (backward compat shim handles this):
CodeEdge::imports().with_property("is_type_only", true)
// OR cleaner:
CodeEdge::imports().with_type_only()
```

**Step 4: Update the lightweight parse pipeline and any other parsers**

Check `repotoire-cli/src/parsers/lightweight.rs` and individual language parsers for direct CodeNode construction. Apply the same pattern.

**Step 5: Commit**

```bash
git add repotoire-cli/src/parsers/
git commit -m "refactor(parsers): produce compact StrKey-based CodeNode

Update bounded pipeline to create CodeNode with StrKey fields via
interner and typed metric fields instead of HashMap properties."
```

---

### Task 5: Bulk-update detectors

**Files:**
- Modify: All ~99 detector files in `repotoire-cli/src/detectors/`
- Modify: `repotoire-cli/src/detectors/engine.rs`
- Modify: `repotoire-cli/src/detectors/function_context.rs`
- Modify: `repotoire-cli/src/detectors/context_hmm.rs`
- Modify: `repotoire-cli/src/detectors/data_flow.rs`
- Modify: `repotoire-cli/src/detectors/ast_fingerprint.rs`
- Modify: `repotoire-cli/src/detectors/base.rs`

**The mechanical pattern for every detector:**

1. **Add interner access** at the start of `detect()`:
   ```rust
   let i = graph.interner();
   ```

2. **String field access** — resolve only when you need text:
   ```rust
   // OLD: func.qualified_name (was String)
   // NEW for HashMap key / equality: func.qualified_name (StrKey, Copy)
   // NEW for text: func.qn(i) or i.resolve(func.qualified_name)
   ```

3. **HashMap key type** changes:
   ```rust
   // OLD: HashMap<String, ...>, entry(func.file_path.clone())
   // NEW: HashMap<StrKey, ...>, entry(func.file_path)
   ```

4. **HashSet** changes:
   ```rust
   // OLD: HashSet<String>, seen.insert(func.qualified_name.clone())
   // NEW: HashSet<StrKey>, seen.insert(func.qualified_name)
   ```

5. **Property access** — backward compat shims handle most cases:
   ```rust
   // These still work via the shim (no change needed):
   func.get_i64("complexity")
   func.get_bool("is_async")

   // But direct field access is faster:
   func.complexity as i64  // instead of func.get_i64("complexity").unwrap_or(0)
   func.is_async()         // instead of func.get_bool("is_async").unwrap_or(false)
   ```

6. **String comparisons** need resolution:
   ```rust
   // OLD: func.file_path.ends_with(".test.ts")
   // NEW: func.path(i).ends_with(".test.ts")

   // OLD: func.file_path.contains("/test/")
   // NEW: func.path(i).contains("/test/")

   // OLD: func.qualified_name.starts_with("test_")
   // NEW: func.qn(i).starts_with("test_")
   ```

7. **Finding creation** — findings still use owned Strings:
   ```rust
   // OLD: PathBuf::from(&func.file_path)
   // NEW: PathBuf::from(func.path(i))

   // OLD: func.qualified_name.clone() (for finding title)
   // NEW: func.qn(i).to_string()
   ```

8. **Remove `.clone()` on identity fields** — StrKey is Copy:
   ```rust
   // OLD: func.qualified_name.clone()  (for HashMap insert)
   // NEW: func.qualified_name           (StrKey is Copy)
   ```

9. **`.as_str()` calls removed** — StrKey has no as_str():
   ```rust
   // OLD: func.qualified_name.as_str()
   // NEW: func.qn(i)
   ```

10. **`func.properties.get("key")` direct access** — use shims or typed fields:
    ```rust
    // OLD: func.properties.get("is_async").and_then(|v| v.as_bool())
    // NEW: Some(func.is_async())
    ```

**Step 1: Update `engine.rs`**

This is the detector orchestration engine. Key changes:
- `precompute_gd_startup()` uses CodeNode fields — update string accesses
- Context building (HMM, function contexts) uses qualified_name, file_path
- The engine calls `graph.interner()` and passes it where needed
- `FunctionContextBuilder` needs interner access

**Step 2: Update `function_context.rs`**

`FunctionContextMap` is `HashMap<String, FunctionContext>`. Change to `HashMap<StrKey, FunctionContext>`.

Update `FunctionContextBuilder` to use StrKey for keys and resolve strings only when needed.

**Step 3: Update `context_hmm.rs`, `data_flow.rs`, `ast_fingerprint.rs`**

These infrastructure modules access CodeNode fields extensively. Apply the mechanical pattern.

**Step 4: Update all detector files**

Use `cargo check 2>&1 | grep "error\[" | head -5` iteratively to find remaining errors. Fix each file following the mechanical pattern above.

High-touch detectors (many string operations, large files):
- `repotoire-cli/src/detectors/god_class.rs`
- `repotoire-cli/src/detectors/shotgun_surgery.rs`
- `repotoire-cli/src/detectors/dead_code.rs`
- `repotoire-cli/src/detectors/circular_dependency.rs`
- `repotoire-cli/src/detectors/duplicate_code.rs`
- `repotoire-cli/src/detectors/unused_imports.rs`
- `repotoire-cli/src/detectors/feature_envy.rs`
- `repotoire-cli/src/detectors/module_cohesion.rs`
- `repotoire-cli/src/detectors/degree_centrality.rs`

Low-touch detectors (simple pattern, few string ops):
- Most security detectors (xss, sql_injection, ssrf, etc.) — iterate files, check content
- ML detectors (torch_load, nan_equality, etc.) — simple pattern matching
- Rust detectors — no Python node access

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/
git commit -m "refactor(detectors): migrate all 99 detectors to compact CodeNode

Add interner access, switch HashMap/HashSet keys from String to StrKey,
resolve strings only for text operations, use typed fields for metrics."
```

---

### Task 6: Update remaining consumers

**Files:**
- Modify: `repotoire-cli/src/git/enrichment.rs`
- Modify: `repotoire-cli/src/scoring/` (all scoring files)
- Modify: `repotoire-cli/src/reporters/` (text, json, html, sarif, markdown)
- Modify: `repotoire-cli/src/mcp/` (MCP tool handlers)
- Modify: `repotoire-cli/src/cli/` (CLI commands that display nodes)
- Modify: `repotoire-cli/src/predictive/` (predictive coding engine)
- Modify: `repotoire-cli/src/calibrate/` (threshold calibration)
- Modify: `repotoire-cli/src/classifier/` (if it accesses CodeNode)

**Step 1: Git enrichment**

`repotoire-cli/src/git/enrichment.rs` — update `enrich_functions()` and `enrich_classes()`:
- Iterate functions/classes using StrKey
- Write `commit_count` to node field, `author`/`last_modified` to `extra_props` side table
- Update `update_node_properties()` calls

**Step 2: Scoring**

Scoring modules access CodeNode fields for penalty calculations. Apply the same pattern: `let i = graph.interner()`, resolve when needed.

**Step 3: Reporters**

Reporters output strings — they MUST resolve all StrKeys to strings. Each reporter's output function needs `let i = graph.interner()` and uses `func.qn(i)`, `func.path(i)` for display.

**Step 4: MCP tools**

MCP tool handlers return JSON with node data. Resolve StrKeys when building response structs.

**Step 5: CLI commands**

`graph`, `stats`, `findings` commands display node info. Resolve for output.

**Step 6: Predictive coding**

`repotoire-cli/src/predictive/mod.rs` — already uses `func.qualified_name` for HashMap keys (becomes StrKey, faster). Resolve for file content lookups.

**Step 7: Compile check**

```bash
cargo check 2>&1 | grep "error\[" | wc -l
```

Target: 0 errors. If errors remain, fix them following the mechanical pattern.

**Step 8: Commit**

```bash
git add repotoire-cli/src/
git commit -m "refactor: migrate all consumers to compact CodeNode

Update git enrichment, scoring, reporters, MCP, CLI, predictive,
and calibration modules for StrKey-based CodeNode."
```

---

### Task 7: Fix tests and verify correctness

**Step 1: Run tests**

```bash
cargo test --lib --tests 2>&1
```

Fix any test failures. Common issues:
- Tests that construct CodeNode with old builder pattern — update to struct literal with interner
- Tests that compare `node.qualified_name` as String — compare StrKeys or resolve
- Tests that serialize/deserialize CodeNode — update for new format
- Mock `GraphQuery` implementations in tests — add `interner()` method

**Step 2: Update test helpers**

If there's a `MockFileProvider` or `MockGraphQuery`, add `interner()` support. The mock needs a `StringInterner` it can return a reference to.

**Step 3: Run clippy**

```bash
cargo clippy 2>&1 | head -30
```

Fix any warnings (unused imports, unnecessary clones, etc.).

**Step 4: Run fmt**

```bash
cargo fmt
```

**Step 5: Commit**

```bash
git add repotoire-cli/
git commit -m "test: fix all tests for compact CodeNode migration"
```

---

### Task 8: Benchmark and optimize

**Step 1: Build release**

```bash
cargo install --path .
```

**Step 2: Benchmark CPython**

```bash
repotoire clean ~/personal/cpython
time repotoire analyze ~/personal/cpython --log-level warn --timings
```

Compare with pre-migration baseline (~5.2s).

**Step 3: Check memory**

```bash
/usr/bin/time -v repotoire analyze ~/personal/cpython --log-level warn 2>&1 | grep "Maximum resident"
```

Compare with pre-migration memory usage.

**Step 4: Freeze optimization (optional follow-up)**

Convert `ThreadedRodeo` → `RodeoReader` after graph build for faster resolution during detection. This is a follow-up optimization, not required for correctness.

In `repotoire-cli/src/graph/store/mod.rs`, add a `freeze()` method:
```rust
pub fn freeze_interner(&self) {
    // Convert ThreadedRodeo to RodeoReader
    // Requires making interner an enum or swapping via RwLock
}
```

**Step 5: Remove backward compat shims (optional follow-up)**

Once all callers use typed fields directly, remove `get_i64()`, `get_bool()`, `get_str()`, `get_f64()` shims from CodeNode. Also remove `CodeEdge::with_property()` shim.

**Step 6: Commit results**

```bash
git add repotoire-cli/
git commit -m "perf(graph): compact CodeNode migration complete

CodeNode: ~200B → ~40B (5x reduction, Copy)
CodeEdge: ~48B → ~4B (12x reduction, Copy)
[Include benchmark numbers]"
```

---

## Quick Reference: Field Migration Table

| Old accessor | New accessor | Notes |
|-------------|-------------|-------|
| `func.qualified_name` (String) | `func.qualified_name` (StrKey) | Use directly for HashMap keys, equality |
| `func.qualified_name.as_str()` | `func.qn(i)` | When you need &str |
| `func.qualified_name.clone()` | `func.qualified_name` | StrKey is Copy, no clone |
| `func.file_path` (String) | `func.file_path` (StrKey) | Use directly for HashMap keys |
| `func.file_path.as_str()` | `func.path(i)` | When you need &str |
| `func.name` (String) | `func.name` (StrKey) | Use directly |
| `func.name.as_str()` | `func.node_name(i)` | When you need &str |
| `func.language` (Option\<String\>) | `func.lang(i)` | Returns Option\<&str\> |
| `func.get_i64("complexity")` | `func.complexity as i64` | Direct field (or shim works) |
| `func.get_i64("paramCount")` | `func.param_count as i64` | Direct field |
| `func.get_i64("methodCount")` | `func.method_count as i64` | Direct field |
| `func.get_i64("maxNesting")` | `func.max_nesting as i64` | Direct field |
| `func.get_i64("returnCount")` | `func.return_count as i64` | Direct field |
| `func.get_i64("commit_count")` | `func.commit_count as i64` | Direct field |
| `func.get_bool("is_async")` | `func.is_async()` | Flag accessor |
| `func.get_bool("is_exported")` | `func.is_exported()` | Flag accessor |
| `func.get_bool("is_public")` | `func.is_public()` | Flag accessor |
| `func.get_bool("is_method")` | `func.is_method()` | Flag accessor |
| `func.get_bool("address_taken")` | `func.address_taken()` | Flag accessor |
| `func.get_bool("has_decorators")` | `func.has_decorators()` | Flag accessor |
| `func.get_str("params")` | `graph.extra_props(func.qualified_name)` | Side table |
| `func.get_str("doc_comment")` | `graph.extra_props(func.qualified_name)` | Side table |
| `func.get_str("decorators")` | `graph.extra_props(func.qualified_name)` | Side table |
| `func.complexity()` | `func.complexity()` | Shim returns Option\<i64\> |
| `func.param_count()` | `func.param_count()` | Shim returns Option\<i64\> |
| `func.loc()` | `func.loc()` | Unchanged |
| `HashMap<String, ...>` (by QN) | `HashMap<StrKey, ...>` | Faster hashing |
| `HashSet<String>` (QN/path) | `HashSet<StrKey>` | Faster hashing |
