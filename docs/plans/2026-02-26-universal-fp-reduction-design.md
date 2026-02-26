# Universal FP Reduction Through Graph Accuracy — Design Doc

**Date:** 2026-02-26
**Status:** Approved

---

## Problem

Real-world validation (Flask, FastAPI, Express) revealed ~101 false positives across 237 total findings (~43% FP rate). Root cause: the graph loses information that tree-sitter already provides. Decorated functions appear dead. Callback handlers appear dead. Module-scope constants appear mutable. API definitions appear vulnerable.

## Design Principle

**Stop discarding information the AST already gives us.** Every FP comes from the graph being inaccurate. We're not adding heuristics — we're fixing information loss. The fixes are language-level universals that work for ANY codebase.

## Non-Goals

- Framework-specific rules (no hardcoded "Flask", "Express" patterns)
- Telemetry / self-learning system (Layer 3-4, separate design)
- Path-based exclusions (not universal)

---

## Change 1: Extract Decorators/Annotations in All Parsers

### What

Populate the existing `annotations: Vec<String>` field on `Function` and `Class` structs from tree-sitter AST nodes that are currently discarded.

### Why

Python parser sees `decorated_definition` nodes but throws away decorator names. JS/TS parser skips `decorator` nodes. Rust parser ignores `attribute_item`. This information is already in the AST — we just need to stop discarding it.

### Where

| Parser | AST Node | Current State | Change |
|--------|----------|--------------|--------|
| Python (`parsers/python.rs`) | `decorated_definition` → `decorator` children | Unwraps to inner def, discards decorators | Iterate `decorator` children, extract name, push to `annotations` |
| JS/TS (`parsers/typescript/mod.rs`) | `decorator` nodes | Skipped during JSDoc search (line 876-879) | Extract decorator name, push to `annotations` |
| Rust (`parsers/rust.rs`) | `attribute_item` siblings of `function_item` | Not extracted at all | Check `prev_sibling()` for `attribute_item`, extract, push to `annotations` |
| Go (`parsers/go.rs`) | N/A (no decorators) | Capitalized = exported, partially detected | Add `go:exported` annotation for capitalized names |
| Java (`parsers/java.rs`) | Already extracts annotations | Working | No change |

### How (Python example)

```rust
// In parse_function, after extracting the function from decorated_definition:
if node.kind() == "decorated_definition" {
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() == "decorator" {
            // The decorator's child is the expression (@app.route(...))
            if let Some(expr) = child.child(1) { // skip '@' token
                let decorator_text = expr.utf8_text(source).unwrap_or("");
                // Extract just the name part: "app.route" from "app.route('/users')"
                let name = decorator_text.split('(').next().unwrap_or(decorator_text);
                annotations.push(name.to_string());
            }
        }
    }
}
```

### Data Flow

```
Parser extracts decorator name → Function.annotations = ["app.route", "login_required"]
    → Graph builder stores as node property "annotations": ["app.route", "login_required"]
        → Detectors can query annotations on any function node
```

This path already works — Java parser populates annotations, graph builder serializes them, `CodeNode.properties["annotations"]` is a JSON array. We just need the other parsers to do the same.

---

## Change 2: Decorated Functions Get Synthetic `Calls` Edges

### What

During graph building, for any function with a non-empty `annotations` field, create a `Calls` edge from a synthetic `__decorator_dispatch__` node to the decorated function.

### Why

A decorator invocation IS a function call. `@app.route('/users') def get_users()` literally means `get_users = app.route('/users')(get_users)`. The function is called by the framework at runtime. The graph should reflect this.

### Where

`cli/analyze/graph.rs` — after inserting function nodes, check `annotations` and emit edges.

### How

```rust
// After inserting function node:
if !func.annotations.is_empty() {
    let dispatcher_qn = format!("{}.__decorator_dispatch__", module_qn);
    // Ensure dispatcher node exists (create once per module)
    if !graph.has_node(&dispatcher_qn) {
        graph.add_node(CodeNode::new(&dispatcher_qn, NodeKind::Function, ...));
    }
    graph.add_edge(&dispatcher_qn, &func.qualified_name, CodeEdge::calls());
}
```

### Effect on Detectors

| Detector | Before | After |
|----------|--------|-------|
| UnreachableCodeDetector | `@app.route` handler has 0 callers → flagged as dead | Has 1+ callers → not dead |
| ShotgunSurgeryDetector | Inaccurate fan-in for decorated functions | Accurate fan-in |
| DegreeCentralityDetector | Missing edges | Complete edges |

**Zero detector code changes needed.**

---

## Change 3: Callback Arguments Create `Calls` Edges

### What

When a parser extracts a function call, also check if any argument is a reference to a known function. If so, emit an additional `Calls` edge from the call site to the callback.

### Why

`app.get('/path', handler)` means `handler` will be called. `array.map(transform)` means `transform` will be called. This is how first-class functions work in every language that supports them. The call graph should reflect it.

### Where

JS/TS parser (`parsers/typescript/mod.rs`) — `extract_calls_recursive` function (lines 762-799). Python parser (`parsers/python.rs`) — call extraction logic.

### How

In `extract_calls_recursive`, after extracting the call target, also iterate `arguments`:

```rust
// After extracting (caller, callee) for the call itself:
if let Some(args) = call_node.child_by_field_name("arguments") {
    let mut cursor = args.walk();
    for arg in args.children(&mut cursor) {
        if arg.kind() == "identifier" {
            let arg_name = arg.utf8_text(source).unwrap_or("");
            // Check if this identifier references a known function in scope
            if known_functions.contains(arg_name) {
                calls.push((caller_scope.clone(), arg_name.to_string()));
            }
        }
    }
}
```

The `known_functions` set is built from all function names extracted earlier in the same file parse. This handles the common case: function defined in same file, passed as callback.

### Scope

For cross-file callbacks (function imported then passed as argument), the graph builder already resolves qualified names from imports. The `(caller, callee_name)` tuple in `ParseResult.calls` gets resolved against import mappings during graph construction. No additional cross-file logic needed.

### Limitations

- Anonymous callbacks (`app.get('/path', (req, res) => {...})`) don't need this — they're inline, not "dead"
- Dynamic function references (`app.get('/path', handlers[name])`) can't be resolved statically — acceptable FN

---

## Change 4: Reassignment Count for GlobalVariablesDetector

### What

For module-scope variables flagged by GlobalVariablesDetector, count how many times the variable is assigned in the file. If assigned exactly once (at declaration), it's effectively constant — skip or downgrade to Info.

### Why

`var app = express()` is assigned once and never reassigned. It's effectively `const`. The only reason it's `var` is CommonJS convention. `var count = 0; ... count++` is genuinely mutable. The difference is observable from the AST.

### Where

`detectors/global_variables.rs` — after identifying a global variable, scan remaining lines for reassignment.

### How

Two options:

**Option A (simple — regex scan):**
```rust
fn count_assignments(content: &str, var_name: &str, decl_line: usize) -> usize {
    let assign_re = Regex::new(&format!(r"\b{}\s*[+\-*/]?=", regex::escape(var_name))).unwrap();
    content.lines()
        .enumerate()
        .filter(|(i, line)| *i != decl_line && assign_re.is_match(line))
        .count()
}
// If count_assignments == 0, variable is never reassigned → skip
```

**Option B (precise — reuse SSA flow):**
The SSA flow module (`detectors/ssa_flow/mod.rs`) already collects every `VarDef` per function via tree-sitter ASTs. Extend to module scope: collect module-scope `VarDef`s, group by name, count.

**Recommendation:** Option A. It's 10 lines, handles the 90% case, and avoids coupling GlobalVariablesDetector to the SSA infrastructure. Option B is more precise but overkill for module-scope variables where reassignment patterns are simple.

### Effect

`var app = express()` → 0 reassignments → effectively const → not flagged.
`var count = 0; ... count++` → 1+ reassignments → genuinely mutable → flagged.

---

## Change 5: Exported + High Fan-In = API Definition

### What

For security detectors (UnsafeTemplateDetector, ExpressSecurityDetector), check if the flagged function is exported AND has high fan-in. If so, it's an API definition — the vulnerability is in the caller's usage, not the definition.

### Why

Flask's `render_template_string()` is an API called by thousands of projects. Flagging its definition as XSS is like flagging `eval()` in V8's source code. The risk is in HOW it's called, not THAT it exists.

### Where

New shared utility in `detectors/mod.rs` or a new `detectors/api_surface.rs`. Used by UnsafeTemplateDetector and ExpressSecurityDetector.

### How

```rust
/// Check if a function at the given location is part of the project's public API surface.
/// API surface = exported function with significant external usage.
pub fn is_api_surface(graph: &dyn GraphQuery, file_path: &str, line: u32) -> bool {
    for func in graph.get_functions() {
        if func.file_path == file_path
            && func.line_start <= line
            && func.line_end >= line
        {
            let fan_in = graph.call_fan_in(&func.qualified_name);
            // Check if function is exported (pub, export, __all__)
            let is_exported = func.get_bool("is_exported").unwrap_or(false);
            // API surface: exported with multiple callers from different modules
            return is_exported && fan_in >= 3;
        }
    }
    false
}
```

### Effect on Security Detectors

- UnsafeTemplateDetector: If `render_template_string` is API surface → downgrade from High to Info with message "API definition — ensure callers sanitize inputs"
- ExpressSecurityDetector: If file's functions are all exported with high fan-in → likely a library, not an app → skip or downgrade

### Prerequisite

Parsers need to set `is_exported` on function nodes. This partially exists:
- Rust: `pub fn` detection exists in UnreachableCodeDetector but not stored on nodes
- JS/TS: `export` detection exists in UnreachableCodeDetector but not stored on nodes
- Python: `__all__` detection exists but not stored on nodes
- Go: Capitalized = exported, not stored on nodes

These checks should move from UnreachableCodeDetector into the parsers, stored as a node property. One-time refactor.

---

## Implementation Order

1. **Change 1** (decorator extraction) — prerequisite for Change 2
2. **Change 2** (synthetic edges) — highest impact, fixes dead route handlers
3. **Change 4** (reassignment count) — independent, fixes GlobalVariablesDetector
4. **Change 3** (callback edges) — independent, fixes dead callback handlers
5. **Change 5** (API surface) — depends on export detection, fixes security FPs

---

## Validation

Re-run validation against Flask, FastAPI, Express after each change. Expected results:

| Metric | Before | After All Changes |
|--------|--------|-------------------|
| Flask findings | 26 | ~23 (-3 API surface FPs) |
| FastAPI findings | 78 (post-fix) | ~73 (-5 dead handler FPs) |
| Express findings | 91 (post-fix) | ~64 (-23 reassignment + -4 callback FPs) |
| Total FPs eliminated | — | ~35 |

---

## Future: Self-Evolving Intelligence (Layers 2-4)

This design is **Layer 1** — deterministic fixes based on language-level universals. The roadmap for Layers 2-4:

### Layer 2: Expanded Validation Suite
- Grow from 3 to 20+ real-world repos across all 9 supported languages
- Automated benchmark CI that gates every release
- Per-detector FP rate tracking over time

### Layer 3: Anonymous Telemetry
- Opt-in structural pattern telemetry (no code content, no file paths)
- Pattern fingerprints: "decorator on function, detector=UnreachableCode, disposition=suppressed"
- Central aggregation of pattern→outcome across all users
- Published "wisdom file" with confidence scores

### Layer 4: Self-Evolving Detectors
- AI agent reads telemetry patterns + detector source code
- Generates Rust code patches to reduce identified FP patterns
- Validation suite gates every auto-generated change
- Auto-release: if FPs decrease AND FNs stable AND tests pass → merge and ship
- The CLI literally improves itself based on collective usage

This is the "hive mind" vision — every user's analysis implicitly trains the system, and every user benefits from everyone else's patterns. Repotoire becomes the first code analysis tool that gets smarter the more people use it.
