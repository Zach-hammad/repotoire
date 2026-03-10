# CompactNode Full Type Migration — Design

## Goal

Replace `CodeNode` (~200 bytes, heap-allocated strings, HashMap properties) with a compact ~40-byte `Copy` struct using string interning (`lasso::StrKey`). Both memory reduction (5x) and cache locality (1.6 nodes per cache line vs 0.3) are primary goals.

## Architecture

The graph layer switches from `DiGraph<CodeNode, CodeEdge>` with owned `String` fields and `HashMap<String, Value>` properties to a compact representation where all strings are interned via `lasso::ThreadedRodeo` (build phase) / `RodeoReader` (detect phase), and all properties are typed struct fields. Detectors work with `StrKey` natively for HashMap keys, equality, and set membership — resolving to `&str` only when substring matching or output text is needed.

## New CodeNode (~40 bytes, `Copy`)

```rust
#[derive(Debug, Clone, Copy)]
pub struct CodeNode {
    // Identity — StrKeys, not Strings (17 bytes + 3 pad)
    pub kind: NodeKind,           // 1 byte (6 variants: File, Function, Class, Module, Variable, Commit)
    pub name: StrKey,             // 4 bytes
    pub qualified_name: StrKey,   // 4 bytes
    pub file_path: StrKey,        // 4 bytes
    pub language: StrKey,         // 4 bytes (EMPTY_KEY = no language)

    // Location (8 bytes)
    pub line_start: u32,
    pub line_end: u32,

    // Metrics — typed fields replace HashMap (10 bytes)
    pub complexity: u16,          // max 65535 (was i64 in HashMap)
    pub param_count: u8,          // max 255
    pub method_count: u16,        // max 65535
    pub max_nesting: u8,          // max 255
    pub return_count: u8,         // max 255
    pub commit_count: u16,        // max 65535 (set by git enrichment)

    // Flags — packed booleans (1 byte)
    pub flags: u8,                // bit 0: is_async
                                  // bit 1: is_exported
                                  // bit 2: is_public
                                  // bit 3: is_method
                                  // bit 4: address_taken
                                  // bit 5: has_decorators
}
// ~40 bytes with alignment
```

### Design Decisions

- **`StrKey` for identity fields**: 4-byte interned key. Equality is integer compare (not string compare). HashMap keying hashes 4 bytes instead of 30+ chars.
- **`EMPTY_KEY`**: Sentinel value for "no value" — intern `""` at interner creation, use that `StrKey` as the default for optional fields like `language`.
- **Typed metrics instead of HashMap**: The 19 known property keys map to struct fields. No HashMap allocation per node. Direct field access instead of hash lookup + string compare.
- **`Copy` trait**: No heap allocations. `Arc<[CodeNode]>` builds are trivial memcpy. Eliminates all `.clone()` overhead on node vectors.
- **No `properties: HashMap`**: The extensibility of the HashMap is unused in practice — all 19 keys are known at compile time.

### Flag Accessors

```rust
impl CodeNode {
    pub fn is_async(&self) -> bool { self.flags & (1 << 0) != 0 }
    pub fn is_exported(&self) -> bool { self.flags & (1 << 1) != 0 }
    pub fn is_public(&self) -> bool { self.flags & (1 << 2) != 0 }
    pub fn is_method(&self) -> bool { self.flags & (1 << 3) != 0 }
    pub fn address_taken(&self) -> bool { self.flags & (1 << 4) != 0 }
    pub fn has_decorators(&self) -> bool { self.flags & (1 << 5) != 0 }
    pub fn loc(&self) -> u32 {
        if self.line_end >= self.line_start { self.line_end - self.line_start + 1 } else { 1 }
    }
}
```

### String Resolution

```rust
impl CodeNode {
    pub fn qn<'a>(&self, i: &'a StringInterner) -> &'a str {
        i.resolve(self.qualified_name)
    }
    pub fn path<'a>(&self, i: &'a StringInterner) -> &'a str {
        i.resolve(self.file_path)
    }
    pub fn node_name<'a>(&self, i: &'a StringInterner) -> &'a str {
        i.resolve(self.name)
    }
}
```

## New CodeEdge (~4 bytes, `Copy`)

```rust
#[derive(Debug, Clone, Copy)]
pub struct CodeEdge {
    pub kind: EdgeKind,  // 1 byte (6 variants: Calls, Imports, Contains, Inherits, Uses, ModifiedIn)
    pub flags: u8,       // bit 0: is_type_only (for import edges)
}
// ~2 bytes raw, ~4 with alignment
```

Down from ~48 bytes (enum + HashMap). petgraph stores source/target implicitly in the graph structure — no need for StrKeys on edges. The only edge property ever used across the entire codebase is `is_type_only` on import edges.

## Side Table for Cold Properties

Rarely-used string properties (~5 detectors access these) are stored in a separate side table on GraphStore, not on every node:

```rust
// On GraphStore:
extra_props: DashMap<StrKey, ExtraProps>,  // keyed by qualified_name

pub struct ExtraProps {
    pub params: StrKey,           // comma-separated parameter list
    pub doc_comment: StrKey,      // documentation comment
    pub decorators: StrKey,       // decorator/annotation list
    pub author: StrKey,           // git author (set by enrichment)
    pub last_modified: StrKey,    // git timestamp (set by enrichment)
}
```

Only populated for nodes that have these properties. Git enrichment writes `author`, `last_modified`, `commit_count` (commit_count on the node directly, strings to side table).

Access via GraphQuery:

```rust
fn extra_props(&self, qn: StrKey) -> Option<ExtraProps>;
```

## Interner Lifecycle

```
Build phase:    ThreadedRodeo (concurrent writes from rayon parsers)
                  ↓ freeze()
Detect phase:   RodeoReader (lock-free reads, array-indexed resolution)
```

The `StringInterner` wrapper already exists. `ReadOnlyInterner` (wrapping `RodeoReader`) already exists. After graph construction completes and before detection starts, call `freeze()` to convert `ThreadedRodeo` → `RodeoReader` for faster resolution during the detect phase.

`GraphQuery::interner()` returns a unified trait or enum that works in both phases:

```rust
pub trait GraphQuery {
    fn interner(&self) -> &StringInterner;
    // ... existing methods unchanged in signature
}
```

Note: The `StringInterner` type may need to become an enum or trait object to support both mutable (ThreadedRodeo) and frozen (RodeoReader) phases. Alternatively, freeze can be deferred to a follow-up optimization since `ThreadedRodeo::resolve()` is already fast.

## GraphQuery Trait Changes

```rust
pub trait GraphQuery {
    // NEW:
    fn interner(&self) -> &StringInterner;
    fn extra_props(&self, qn: StrKey) -> Option<ExtraProps>;

    // UNCHANGED signatures (but CodeNode is now compact):
    fn get_functions(&self) -> Vec<CodeNode>;
    fn get_functions_shared(&self) -> Arc<[CodeNode]>;
    fn get_classes(&self) -> Vec<CodeNode>;
    fn get_classes_shared(&self) -> Arc<[CodeNode]>;
    fn get_files(&self) -> Vec<CodeNode>;
    fn get_files_shared(&self) -> Arc<[CodeNode]>;
    fn get_node(&self, qn: &str) -> Option<CodeNode>;
    fn get_callers(&self, qn: &str) -> Vec<CodeNode>;
    fn get_callees(&self, qn: &str) -> Vec<CodeNode>;
    // ... etc.
}
```

Methods that take `qn: &str` parameters remain unchanged — the implementation interns the string internally to look up the StrKey.

## Detector Migration Pattern

Most detectors follow mechanical transforms:

```rust
// BEFORE:
fn detect(&self, graph: &dyn GraphQuery, files: &dyn FileProvider) -> Result<Vec<Finding>> {
    let functions = graph.get_functions_shared();
    let mut map: HashMap<String, Vec<&CodeNode>> = HashMap::new();
    for func in functions.iter() {
        if func.file_path.ends_with(".test.ts") { continue; }
        map.entry(func.file_path.clone()).or_default().push(func);
    }
}

// AFTER:
fn detect(&self, graph: &dyn GraphQuery, files: &dyn FileProvider) -> Result<Vec<Finding>> {
    let i = graph.interner();
    let functions = graph.get_functions_shared();
    let mut map: HashMap<StrKey, Vec<&CodeNode>> = HashMap::new();
    for func in functions.iter() {
        if i.resolve(func.file_path).ends_with(".test.ts") { continue; }
        map.entry(func.file_path).or_default().push(func);  // StrKey, no clone!
    }
}
```

Key rules:
- Add `let i = graph.interner();` at the start of detect()
- `HashMap<String, ...>` keyed by qualified_name/file_path → `HashMap<StrKey, ...>`
- Remove `.clone()` on identity fields (StrKey is Copy)
- `func.qualified_name` (field access, returns StrKey) for keys/equality
- `func.qn(i)` or `i.resolve(func.qualified_name)` when you need `&str`
- `func.complexity()` → `func.complexity` (direct field, was HashMap lookup)
- `func.get_i64("paramCount")` → `func.param_count as i64` (direct field)
- `func.get_bool("is_async")` → `func.is_async()` (flag accessor)

## Parser Changes

Parsers currently produce `CodeNode` with builder pattern:

```rust
// BEFORE:
CodeNode::function("foo", "src/main.rs")
    .with_qualified_name("module.foo")
    .with_lines(10, 25)
    .with_language("rust")
    .with_property("complexity", 5)
    .with_property("is_async", true)
    .with_property("paramCount", 3)

// AFTER:
CodeNode {
    kind: NodeKind::Function,
    name: interner.intern("foo"),
    qualified_name: interner.intern("module.foo"),
    file_path: interner.intern("src/main.rs"),
    language: interner.intern("rust"),
    line_start: 10,
    line_end: 25,
    complexity: 5,
    param_count: 3,
    max_nesting: 0,
    return_count: 0,
    method_count: 0,
    commit_count: 0,
    flags: 0,  // set is_async via helper
}
```

The interner must be available during parsing. It's already thread-safe (`ThreadedRodeo`), so rayon parallel parsing works.

Parsers need access to a shared `&StringInterner` — pass it through the parse pipeline alongside the file list.

## GraphStore Internal Changes

### Node Storage
- `graph: RwLock<StableGraph<CodeNode, CodeEdge>>` — same type name, smaller nodes
- Node indices (`DashMap<StrKey, NodeIndex>`) — keyed by `StrKey` instead of `String`

### Edge Deduplication
- `edge_set: Mutex<HashSet<(NodeIndex, NodeIndex, EdgeKind)>>` — EdgeKind is Copy, no change needed

### Spatial Index
- `function_spatial_index: DashMap<StrKey, Vec<(u32, u32, NodeIndex)>>` — keyed by file_path StrKey

### Persistence (redb)
- Serialization must resolve StrKeys to strings before writing (redb stores human-readable data)
- Deserialization must intern strings back to StrKeys on load
- Bincode graph cache: serialize the StableGraph directly (StrKeys are u32, trivially serializable)

## Old CompactNode/CompactEdge Removal

The existing `CompactNode`, `CompactNodeKind`, `CompactEdge`, `CompactEdgeKind` types in `interner.rs` become dead code — superseded by the redesigned `CodeNode`/`CodeEdge`. Remove them.

## Performance Expectations

| Metric | Before | After |
|--------|--------|-------|
| Node size | ~200 bytes | ~40 bytes (5x reduction) |
| Edge size | ~48 bytes | ~4 bytes (12x reduction) |
| Nodes per 64KB L1 cache | ~300 | ~1,600 |
| HashMap key hash | hash 30+ chars | hash 4 bytes |
| Node clone/copy | heap alloc + memcpy strings | memcpy 40 bytes |
| Property access | HashMap lookup + string compare | direct field access |
| `Arc<[CodeNode]>` build | expensive (Clone each String) | cheap (Copy 40 bytes each) |
| Interner resolution | N/A | array index + pointer chase (same as current String deref) |

### CPython Estimates (72k functions, 14k classes, 3.4k files)
- Node memory: ~200 bytes × 90k = 18 MB → ~40 bytes × 90k = 3.6 MB
- Edge memory: ~48 bytes × 140k = 6.7 MB → ~4 bytes × 140k = 0.6 MB
- Total graph memory reduction: ~25 MB → ~4.2 MB (6x reduction)

## Migration Order

1. **Redesign CodeNode/CodeEdge structs** — new fields, remove HashMap, add flag accessors
2. **Add `interner()` and `extra_props()` to GraphQuery trait** — implement on GraphStore and CachedGraphQuery
3. **Update GraphStore internals** — node storage, indices, add_node/add_edge, persistence
4. **Update parsers** — thread interner through parse pipeline, produce StrKey-based nodes
5. **Update detectors** — mechanical transforms (add `let i`, use StrKey for maps, resolve for text)
6. **Freeze optimization** — ThreadedRodeo → RodeoReader after build, before detect
7. **Remove old CompactNode/CompactEdge** — dead code cleanup

## Files Modified

### Core (steps 1-3)
- `repotoire-cli/src/graph/store_models.rs` — CodeNode/CodeEdge redesign
- `repotoire-cli/src/graph/interner.rs` — EMPTY_KEY, remove old CompactNode/CompactEdge
- `repotoire-cli/src/graph/traits.rs` — GraphQuery trait additions
- `repotoire-cli/src/graph/cached.rs` — CachedGraphQuery interner/extra_props impl
- `repotoire-cli/src/graph/store/mod.rs` — GraphStore internals (indices, persistence, add/remove)

### Parsers (step 4)
- `repotoire-cli/src/parsers/bounded_pipeline.rs` — main parse pipeline
- `repotoire-cli/src/parsers/mod.rs` — interner threading
- Individual parser files (python.rs, typescript.rs, etc.) — if they create CodeNode directly

### Detectors (step 5)
- All ~99 detector files — mechanical transforms
- `repotoire-cli/src/detectors/engine.rs` — interner access, context building
- `repotoire-cli/src/detectors/base.rs` — if Detector trait needs changes

### Other
- `repotoire-cli/src/git/enrichment.rs` — write to side table + commit_count field
- `repotoire-cli/src/predictive/mod.rs` — StrKey usage in scoring
- `repotoire-cli/src/scoring/` — StrKey usage
- `repotoire-cli/src/reporters/` — resolve StrKeys for output
- `repotoire-cli/src/mcp/` — resolve StrKeys for MCP responses
- `repotoire-cli/src/cli/` — resolve StrKeys for CLI output

## Testing

- All existing tests must pass after migration
- Add `assert!(std::mem::size_of::<CodeNode>() <= 48)` compile-time check
- Add `assert!(std::mem::size_of::<CodeEdge>() <= 4)` compile-time check
- Benchmark CPython before/after to measure actual speedup
