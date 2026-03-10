# Graph-Based Constant Propagation Design

**Date**: 2026-03-09
**Status**: Draft
**Author**: Design session (human + AI)

## Problem Statement

Repotoire's detectors currently resolve variable values through ad-hoc methods: regex pattern matching on source code, per-detector AST re-parsing, and intra-function SSA analysis. This leads to:

1. **Redundant work** — multiple detectors re-read and re-parse the same source files
2. **Limited cross-function reasoning** — taint analysis can follow call edges but can't resolve concrete values across function boundaries
3. **High false positive rates** — detectors can't distinguish `db.execute("SELECT ...")` (safe constant) from `db.execute(user_input)` (tainted) without re-analyzing source

## Solution

Use the code knowledge graph as a **static value oracle**. During the parse phase, extract variable assignments and return expressions into a typed `ValueStore`. The graph carries cross-scope relationships (Variable nodes, Uses edges), and the ValueStore holds symbolic value representations. Detectors query the ValueStore instead of re-reading source code.

**Key insight**: parse once, query many. The graph already knows *what calls what* — we extend it to know *what value flows where*.

## Architecture

### Approach: Hybrid (Variable Nodes + ValueStore)

- **Graph** carries `Variable` nodes for module/class-level constants and `Uses` edges linking functions to the constants they reference. This leverages existing graph primitives (`Variable` node type and `Uses` edge type are defined but currently unpopulated).
- **ValueStore** holds intra-function symbolic values (assignments, return values) in a typed Rust struct. No JSON serialization overhead.
- **SSA replacement**: The ValueStore replaces the existing `ssa_flow/` module entirely. It provides a strict superset of SSA capabilities (def-use chains + symbolic values + cross-function propagation).

### Pipeline (Merged — No Separate Propagation Phase)

```
Parse Phase (parallel, per file via rayon)
  │
  ├── Extract functions, classes, calls (existing)
  └── NEW: Extract assignments + return expressions → RawParseValues
        ├── Resolve intra-function values immediately (literals, local refs)
        └── Leave cross-function refs as SymbolicValue::Call / SymbolicValue::Variable
  │
  ▼
Graph Build Phase (existing, now extended)
  │
  ├── Insert nodes + edges (existing)
  ├── NEW: Insert Variable nodes for module/class constants
  ├── NEW: Insert Uses edges (Function → Variable)
  ├── NEW: Build ValueStore from all RawParseValues
  └── NEW: Resolve cross-function refs (topological walk of call graph, ~O(N))
  │
  ▼
Detection Phase (parallel via rayon, unchanged interface)
  └── Detectors receive (&GraphStore, &ValueStore)
```

## Core Types

### SymbolicValue

The central type representing any statically-resolved value:

```rust
/// A symbolic representation of a value, resolved statically from source code.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum SymbolicValue {
    // Concrete values
    Literal(LiteralValue),

    // References
    Variable(String),                                    // Qualified name of another binding
    Parameter(usize),                                    // Function parameter by position
    FieldAccess(Box<SymbolicValue>, String),              // obj.field
    Index(Box<SymbolicValue>, Box<SymbolicValue>),        // arr[0], dict["key"]

    // Computed values
    BinaryOp(BinOp, Box<SymbolicValue>, Box<SymbolicValue>),
    Concat(Vec<SymbolicValue>),                          // String interpolation / concatenation
    Call(String, Vec<SymbolicValue>),                     // Function call with resolved args

    // Control flow
    Phi(Vec<SymbolicValue>),                             // SSA phi node — value is one of N branches

    // Escape hatch
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum LiteralValue {
    String(String),
    Integer(i64),
    Float(f64),
    Boolean(bool),
    Null,
    List(Vec<SymbolicValue>),                            // Capped at 32 entries
    Dict(Vec<(SymbolicValue, SymbolicValue)>),            // Capped at 32 entries
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub enum BinOp {
    Add, Sub, Mul, Div, Mod,
    And, Or,
    Eq, NotEq, Lt, Gt, LtEq, GtEq,
}
```

**Design choices:**
- `Phi` handles branches without path sensitivity — "this could be any of N values"
- `Unknown` is the safe default — any unresolvable value
- `Parameter(usize)` enables inter-procedural propagation — at call sites, substitute actual arguments
- `Box` for recursive cases keeps the enum size predictable
- `Serialize/Deserialize` for cache persistence

### ValueStore

```rust
/// Holds all resolved symbolic values for the codebase.
/// Built during graph construction, consumed by detectors.
pub struct ValueStore {
    /// Module/class-level constants: qualified_name → value
    pub constants: HashMap<String, SymbolicValue>,

    /// Per-function assignments: func_qn → ordered assignments
    pub function_values: HashMap<String, Vec<Assignment>>,

    /// Resolved return values: func_qn → return value
    pub return_values: HashMap<String, SymbolicValue>,
}

pub struct Assignment {
    pub variable: String,          // Local variable name
    pub value: SymbolicValue,      // Resolved or partially resolved
    pub line: u32,
    pub column: u32,
}
```

### RawParseValues (Parser Output)

```rust
/// Emitted by parsers alongside existing ParseResult.
pub struct RawParseValues {
    /// Module-level constant assignments (name, value)
    pub module_constants: Vec<(String, SymbolicValue)>,

    /// Per-function assignments: func_qn → assignments
    pub function_assignments: HashMap<String, Vec<Assignment>>,

    /// Return expressions per function: func_qn → value
    pub return_expressions: HashMap<String, SymbolicValue>,
}
```

### ValueStore API (Detector Interface)

```rust
impl ValueStore {
    /// What value does this variable hold at this point in the function?
    /// Scans assignments up to the given line, returns the last assignment's value.
    /// If multiple paths assign different values (branches), returns Phi.
    pub fn resolve_at(&self, func_qn: &str, var_name: &str, line: u32) -> SymbolicValue;

    /// What does this function return?
    pub fn return_value(&self, func_qn: &str) -> SymbolicValue;

    /// Get all assignments within a function (for scanning detectors).
    pub fn assignments_in(&self, func_qn: &str) -> &[Assignment];

    /// Is this value a known constant (no Unknown/Parameter/Variable leaves)?
    pub fn is_constant(value: &SymbolicValue) -> bool;

    /// Try to evaluate a SymbolicValue to a concrete LiteralValue.
    /// Returns None if any part is unresolvable.
    pub fn try_evaluate(&self, value: &SymbolicValue) -> Option<LiteralValue>;

    /// Does this value contain any tainted (Parameter/Unknown) components?
    pub fn is_tainted(value: &SymbolicValue) -> bool;

    /// Resolve a module/class constant by qualified name.
    pub fn resolve_constant(&self, qualified_name: &str) -> SymbolicValue;
}
```

## Extraction Strategy: Table-Driven

Instead of 9 bespoke extractors, a **language config table** maps tree-sitter node kind names to SymbolicValue constructors. One generic `node_to_symbolic` function dispatches based on the config. Language-specific edge cases get `SpecialHandler` overrides.

### Language Config

```rust
pub struct LanguageValueConfig {
    pub assignment_kinds: &'static [&'static str],
    pub string_literal_kinds: &'static [&'static str],
    pub integer_literal_kinds: &'static [&'static str],
    pub float_literal_kinds: &'static [&'static str],
    pub bool_true_kinds: &'static [&'static str],
    pub bool_false_kinds: &'static [&'static str],
    pub null_kinds: &'static [&'static str],
    pub call_kinds: &'static [&'static str],
    pub return_kinds: &'static [&'static str],
    pub binary_op_kinds: &'static [&'static str],
    pub string_interpolation_kinds: &'static [&'static str],
    pub list_kinds: &'static [&'static str],
    pub dict_kinds: &'static [&'static str],
    pub subscript_kinds: &'static [&'static str],
    pub field_access_kinds: &'static [&'static str],
    pub conditional_kinds: &'static [&'static str],
    pub special_handlers: Vec<Box<dyn SpecialHandler>>,
}
```

### Node Kind Mapping (All 9 Languages)

| Concept | Python | TypeScript/JS | Rust | Go | Java | C# | C | C++ |
|---------|--------|--------------|------|-----|------|-----|---|-----|
| Assignment | `assignment` | `variable_declaration`, `assignment_expression` | `let_declaration` | `short_var_declaration`, `assignment_statement` | `local_variable_declaration` | `variable_declaration` | `declaration`, `assignment_expression` | `declaration`, `assignment_expression` |
| String | `string` | `string`, `template_string` | `string_literal`, `raw_string_literal` | `interpreted_string_literal`, `raw_string_literal` | `string_literal` | `string_literal` | `string_literal` | `string_literal`, `raw_string_literal` |
| Integer | `integer` | `number` | `integer_literal` | `int_literal` | `decimal_integer_literal` | `integer_literal` | `number_literal` | `number_literal` |
| Call | `call` | `call_expression` | `call_expression` | `call_expression` | `method_invocation` | `invocation_expression` | `call_expression` | `call_expression` |
| Return | `return_statement` | `return_statement` | `return_expression` | `return_statement` | `return_statement` | `return_statement` | `return_statement` | `return_statement` |

### Special Handlers

Language-specific edge cases that need custom logic beyond table lookup:

| Language | Special Case | Handler |
|----------|-------------|---------|
| Python | f-strings (`f"hello {name}"`) | Parse interpolation expressions into `Concat` |
| Python | Augmented assignment (`x += 1`) | Convert to `BinaryOp(Add, Variable("x"), Literal(1))` |
| Python | Walrus operator (`:=`) | Treat as assignment + expression |
| Rust | Pattern matching (`let (a, b) = ...`) | Destructure into multiple assignments |
| Rust | `match` expressions | Convert arms to `Phi` |
| Rust | `let mut` tracking | Mark mutable, downgrade to `Unknown` on reassignment |
| Go | Multiple return values (`a, b := foo()`) | Destructure, or `Unknown` if not tuple-like |
| Go | `:=` vs `=` | Both are assignments, different scoping |
| TypeScript | Template literals (`` `${x}` ``) | Parse into `Concat` |
| TypeScript | Optional chaining (`a?.b`) | Wrap in `Phi([FieldAccess(...), Literal(Null)])` |
| C/C++ | Pointer operations (`*p = x`) | `Unknown` (aliasing) |
| C/C++ | Compound assignment (`+=`, `-=`) | Same as Python augmented assignment |

### Core Extraction Function

```rust
/// Convert a tree-sitter expression node to a SymbolicValue.
/// Language-agnostic, dispatches via config table.
pub fn node_to_symbolic(
    node: tree_sitter::Node,
    source: &[u8],
    config: &LanguageValueConfig,
    func_qn: &str,
) -> SymbolicValue {
    let kind = node.kind();

    // Check special handlers first (language-specific overrides)
    for handler in &config.special_handlers {
        if let Some(value) = handler.try_handle(node, source, func_qn) {
            return value;
        }
    }

    // Table-driven dispatch
    if config.string_literal_kinds.contains(&kind) {
        let text = node_text(node, source);
        SymbolicValue::Literal(LiteralValue::String(unquote(text)))
    } else if config.integer_literal_kinds.contains(&kind) {
        let text = node_text(node, source);
        match text.parse::<i64>() {
            Ok(n) => SymbolicValue::Literal(LiteralValue::Integer(n)),
            Err(_) => SymbolicValue::Unknown,
        }
    } else if config.call_kinds.contains(&kind) {
        let callee = extract_callee(node, source);
        let args: Vec<_> = extract_args(node, source)
            .map(|arg| node_to_symbolic(arg, source, config, func_qn))
            .collect();
        SymbolicValue::Call(callee, args)
    } else if config.binary_op_kinds.contains(&kind) {
        let op = extract_binary_op(node, source);
        let lhs = node_to_symbolic(node.child_by_field_name("left").unwrap(), source, config, func_qn);
        let rhs = node_to_symbolic(node.child_by_field_name("right").unwrap(), source, config, func_qn);
        SymbolicValue::BinaryOp(op, Box::new(lhs), Box::new(rhs))
    } else if config.subscript_kinds.contains(&kind) {
        let obj = node_to_symbolic(/* object child */, source, config, func_qn);
        let key = node_to_symbolic(/* subscript child */, source, config, func_qn);
        SymbolicValue::Index(Box::new(obj), Box::new(key))
    }
    // ... other cases ...
    else {
        SymbolicValue::Unknown
    }
}
```

## Cross-Function Propagation

After all files are parsed and the graph is built, resolve cross-function value references:

```rust
fn resolve_cross_function(store: &mut ValueStore, graph: &GraphStore) {
    let mut resolving: HashSet<String> = HashSet::new();

    // Topological sort of call graph (leaves first)
    let order = graph.topological_sort();

    for func_qn in order {
        resolve_function(store, graph, &func_qn, &mut resolving);
    }
}

fn resolve_function(
    store: &mut ValueStore,
    graph: &GraphStore,
    func_qn: &str,
    resolving: &mut HashSet<String>,
) {
    // Cycle detection
    if resolving.contains(func_qn) {
        store.return_values.insert(func_qn.to_string(), SymbolicValue::Unknown);
        return;
    }
    resolving.insert(func_qn.to_string());

    // Resolve return value
    if let Some(raw_return) = store.return_expressions.get(func_qn) {
        let resolved = substitute_references(raw_return, store, graph, func_qn, resolving);
        store.return_values.insert(func_qn.to_string(), resolved);
    }

    // Resolve assignments that reference other functions
    if let Some(assignments) = store.function_values.get_mut(func_qn) {
        for assignment in assignments.iter_mut() {
            assignment.value = substitute_references(&assignment.value, store, graph, func_qn, resolving);
        }
    }

    resolving.remove(func_qn);
}

fn substitute_references(
    value: &SymbolicValue,
    store: &ValueStore,
    graph: &GraphStore,
    context_func: &str,
    resolving: &mut HashSet<String>,
) -> SymbolicValue {
    match value {
        SymbolicValue::Call(callee, args) => {
            // Resolve the callee's return value
            let return_val = store.return_value(callee);
            match return_val {
                SymbolicValue::Unknown => SymbolicValue::Call(callee.clone(), args.clone()),
                resolved => {
                    // Substitute Parameter(n) with actual arguments
                    substitute_params(&resolved, args)
                }
            }
        }
        SymbolicValue::Variable(qn) => {
            // Resolve module/class constants
            if let Some(const_val) = store.constants.get(qn) {
                const_val.clone()
            } else {
                SymbolicValue::Variable(qn.clone())
            }
        }
        // Recurse into compound expressions
        SymbolicValue::BinaryOp(op, lhs, rhs) => {
            let l = substitute_references(lhs, store, graph, context_func, resolving);
            let r = substitute_references(rhs, store, graph, context_func, resolving);
            SymbolicValue::BinaryOp(*op, Box::new(l), Box::new(r))
        }
        // ... other recursive cases ...
        other => other.clone(),
    }
}
```

## Detector Usage Examples

### SQL Injection Detector (Before/After)

**Before** (regex on source):
```rust
if line.contains("execute") && line.contains("format") { flag_finding() }
```

**After** (ValueStore query):
```rust
fn detect(&self, graph: &GraphStore, values: &ValueStore) -> Vec<Finding> {
    for func in graph.functions() {
        for assignment in values.assignments_in(func.qualified_name()) {
            if is_sql_sink_call(&assignment.value) {
                let query_arg = extract_first_arg(&assignment.value);
                match values.try_evaluate(query_arg) {
                    Some(LiteralValue::String(_)) => continue, // hardcoded SQL — safe
                    None if ValueStore::is_tainted(query_arg) => {
                        findings.push(Finding::sql_injection(...));
                    }
                    _ => {} // Unknown — not enough info to flag
                }
            }
        }
    }
}
```

### Magic Numbers Detector (Before/After)

**Before** (regex for numeric literals):
```rust
Regex::new(r"\b(\d{2,})\b")  // high FP rate
```

**After** (ValueStore query):
```rust
for assignment in values.assignments_in(func_qn) {
    if let SymbolicValue::Literal(LiteralValue::Integer(n)) = &assignment.value {
        if is_descriptive_name(&assignment.variable) {
            continue; // TIMEOUT = 3600 — named constant, skip
        }
        findings.push(Finding::magic_number(n, assignment.line));
    }
}
```

### Cross-Function Taint (New Capability)

```python
# auth.py
def get_token():
    return request.headers.get("Authorization")

# api.py
def handle_request():
    token = get_token()
    db.execute(f"SELECT * FROM sessions WHERE token='{token}'")
```

Resolution chain:
```
values.resolve_at("api.handle_request", "token", line=7)
  → Call("auth.get_token", [])
  → values.return_value("auth.get_token")
  → Call("request.headers.get", [Literal("Authorization")])
  → TAINTED (request.headers is a known taint source)
```

## Edge Cases

| Case | Resolution |
|------|-----------|
| Recursion / cycles | Detect back-edge during topological walk, mark return value as `Unknown` |
| Reassignment | `Phi` of all possible values for the variable at a given line |
| Closures / lambdas | Capture enclosing scope variables, resolve at call site via `Parameter` substitution |
| Multiple return paths | `Phi` of all return expressions |
| Dynamic dispatch | Use `Inherits` edges to find all implementations → `Phi` of their return values |
| Mutable globals | `Unknown` after any mutation (detect `global` declarations, mutable statics) |
| Dynamic container keys | `Unknown` (static keys into known containers resolve) |
| Pointer aliasing (C/C++) | `Unknown` (conservative — aliasing is undecidable) |
| Generics / trait objects | `Unknown` in V1 (type erasure makes resolution infeasible) |
| Reflection / eval | `Unknown` (undecidable) |
| Async / await | `await expr` transparently unwraps the return value of `expr` |

## Guard Rails

| Guard | Limit | Rationale |
|-------|-------|-----------|
| Phi arms | Max 8 | Prevents combinatorial explosion in deeply branched code |
| Container entries | Max 32 | Prevents huge `Dict`/`List` SymbolicValue trees |
| Resolution depth | Max 16 | Prevents unbounded recursion in nested Call/Variable resolution |
| Parse budget | <20% regression | Extraction adds AST walking; must not significantly regress parse time |

Values exceeding these limits collapse to `Unknown`.

## Incremental Cache Interaction

**Problem**: The existing per-file incremental cache doesn't handle cross-file value dependencies. If `config.py` changes `TIMEOUT = 3600` → `TIMEOUT = 7200`, files that *use* `config.TIMEOUT` still have warm caches — their findings won't reflect the new value.

**Solution**: Track value dependencies per file.

```rust
struct FileCacheEntry {
    content_hash: u64,
    findings: Vec<Finding>,
    // NEW: qualified names of external values this file depends on
    value_dependencies: HashSet<String>,
}
```

During cache validation, a file is invalidated if:
1. Its content hash changed (existing behavior), OR
2. Any of its `value_dependencies` have a different resolved value than when the cache was written

This ensures cross-file value changes propagate correctly through the cache.

## Files Changed

### New Files
- `repotoire-cli/src/values/mod.rs` — `SymbolicValue`, `LiteralValue`, `BinOp` types
- `repotoire-cli/src/values/store.rs` — `ValueStore` struct and query API
- `repotoire-cli/src/values/extraction.rs` — Table-driven extraction, `node_to_symbolic`
- `repotoire-cli/src/values/propagation.rs` — Cross-function resolution, topological walk
- `repotoire-cli/src/values/configs/` — Per-language `LanguageValueConfig` tables (9 files)

### Modified Files
- `repotoire-cli/src/parsers/mod.rs` — Add `RawParseValues` to `ParseResult`, call extraction during parse
- `repotoire-cli/src/parsers/*.rs` — Each parser creates its `LanguageValueConfig` and invokes extraction
- `repotoire-cli/src/cli/analyze/graph.rs` — Build `ValueStore` during graph construction, run propagation
- `repotoire-cli/src/cli/analyze/mod.rs` — Pass `ValueStore` to detector runner
- `repotoire-cli/src/detectors/mod.rs` — Update `Detector` trait to accept `&ValueStore`
- `repotoire-cli/src/detectors/incremental_cache.rs` — Add `value_dependencies` tracking

### Removed Files
- `repotoire-cli/src/detectors/ssa_flow/` — Replaced entirely by ValueStore
- `repotoire-cli/src/detectors/data_flow.rs` — `DataFlowProvider` trait superseded by `ValueStore`

## Performance Expectations

| Metric | Current | Expected | Notes |
|--------|---------|----------|-------|
| Parse phase time | ~1.2s (10k files) | ~1.4s (+17%) | Additional AST walking for extraction |
| Detection phase time | ~2.5s | ~1.8s (-28%) | No source re-reading, O(1) value lookups |
| Peak memory | ~200MB | ~220MB (+10%) | ValueStore overhead, offset by AST release |
| Net wall time | ~4.5s | ~4.0s (-11%) | Parse regression offset by detection speedup |

*Estimates based on a typical 10k-file Python/TypeScript codebase. Actual numbers depend on code density.*

## Testing Strategy

1. **Unit tests per language config**: Small code snippets → verify `node_to_symbolic` produces correct `SymbolicValue` for each language
2. **Propagation tests**: Multi-function test cases → verify cross-function resolution and cycle handling
3. **Detector integration tests**: Existing detector tests should continue passing (or improve — fewer FPs)
4. **Regression benchmark**: Run analysis on Flask/FastAPI/Django before and after, compare timing and finding counts
5. **Guard rail tests**: Verify Phi/container/depth caps produce `Unknown` as expected

## Open Questions

1. **Should `SymbolicValue` use interned strings (`lasso::Spur`)** instead of owned `String` for qualified names? Saves memory but adds complexity.
2. **Should the ValueStore be persisted to redb** alongside the graph for faster incremental warm-up? Or is it cheap enough to rebuild?
3. **How do we handle decorator/annotation-modified functions** (e.g., `@cached` changing return semantics)?
