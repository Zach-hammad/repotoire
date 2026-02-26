# Universal FP Reduction Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce false positives by ~35 across Flask/FastAPI/Express by fixing information loss in parsers and graph building — no framework-specific rules.

**Architecture:** Enrich parsers to extract decorators, callbacks, and export status that tree-sitter already provides but we discard. The graph becomes accurate, and detectors automatically produce fewer FPs without code changes.

**Tech Stack:** Rust, tree-sitter (Python/JS/TS/Rust grammars), petgraph

---

## Task 1: Extract Python Decorators into Annotations

**Files:**
- Modify: `repotoire-cli/src/parsers/python.rs:120-133` (extract_functions)
- Modify: `repotoire-cli/src/parsers/python.rs:207-220` (parse_function_node)
- Modify: `repotoire-cli/src/parsers/python.rs:292-334` (extract_classes / parse_class_node)
- Test: `repotoire-cli/src/parsers/python.rs:685+` (inline tests)

**Context:** The Python parser already sees `decorated_definition` nodes (lines 63, 164, 292, 393, 536) but discards decorator names. The `annotations` field is hardcoded to `vec![]` at lines 132, 219, 334. The tree-sitter Python grammar has `decorated_definition` → `decorator` children, where each `decorator` has an expression child (after the `@` token).

**Step 1: Write failing test**

Add to the `#[cfg(test)] mod tests` block (after line 906):

```rust
#[test]
fn test_decorator_extraction() {
    let code = r#"
@app.route('/users')
def get_users():
    return []

@login_required
@cache(timeout=300)
def admin_page():
    pass

class MyModel:
    pass

@dataclass
class UserDTO:
    name: str
"#;
    let result = parse_python(code, Path::new("test.py"));

    // get_users should have annotation "app.route"
    let get_users = result.functions.iter().find(|f| f.name == "get_users").unwrap();
    assert!(
        get_users.annotations.iter().any(|a| a.contains("app.route")),
        "get_users should have app.route annotation, got: {:?}",
        get_users.annotations
    );

    // admin_page should have two annotations
    let admin = result.functions.iter().find(|f| f.name == "admin_page").unwrap();
    assert!(
        admin.annotations.len() >= 2,
        "admin_page should have 2+ annotations, got: {:?}",
        admin.annotations
    );

    // MyModel should have no annotations
    let my_model = result.classes.iter().find(|c| c.name == "MyModel").unwrap();
    assert!(my_model.annotations.is_empty());

    // UserDTO should have @dataclass annotation
    let user_dto = result.classes.iter().find(|c| c.name == "UserDTO").unwrap();
    assert!(
        user_dto.annotations.iter().any(|a| a.contains("dataclass")),
        "UserDTO should have dataclass annotation, got: {:?}",
        user_dto.annotations
    );
}
```

**Step 2: Run test to verify it fails**

Run: `cargo test test_decorator_extraction -- --nocapture`
Expected: FAIL — annotations are empty `vec![]`

**Step 3: Implement decorator extraction**

Add a helper function near the top of `python.rs` (after the imports):

```rust
/// Extract decorator names from a `decorated_definition` node.
/// Returns a list of decorator name strings (e.g., "app.route", "login_required").
fn extract_decorators(node: &tree_sitter::Node, source: &[u8]) -> Vec<String> {
    let mut decorators = Vec::new();
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() == "decorator" {
            // Decorator children: '@' token, then the expression
            // Find the first non-'@' child (the expression)
            let mut inner_cursor = child.walk();
            for inner in child.children(&mut inner_cursor) {
                if inner.kind() != "@" && inner.kind() != "comment" {
                    let text = inner.utf8_text(source).unwrap_or("");
                    // Extract just the name part: "app.route" from "app.route('/users')"
                    let name = text.split('(').next().unwrap_or(text).trim();
                    if !name.is_empty() {
                        decorators.push(name.to_string());
                    }
                    break;
                }
            }
        }
    }
    decorators
}
```

Then modify `extract_functions()` — around line 120, the Function struct construction. The function currently matches `decorated_definition` nodes at line 63. When it finds a function inside a `decorated_definition`, pass the parent node to `extract_decorators`. Replace `annotations: vec![],` at line 132 with the extracted decorators.

The tricky part: in the query-based path (`extract_functions`), the matched node is the inner `function_definition`, not the `decorated_definition`. You need to check `node.parent()` to see if it's a `decorated_definition`:

```rust
// Replace: annotations: vec![],
// With:
annotations: {
    if let Some(parent) = node.parent() {
        if parent.kind() == "decorated_definition" {
            extract_decorators(&parent, source)
        } else {
            vec![]
        }
    } else {
        vec![]
    }
},
```

Apply the same pattern at:
- `parse_function_node()` line 219 — check if the node passed in has a parent `decorated_definition`
- `parse_class_node()` line 334 — same pattern for class definitions

For `extract_async_functions()` (line 164): when the function finds a `decorated_definition` containing an async function, extract decorators from the `decorated_definition` node before unwrapping to the inner function.

**Step 4: Run test to verify it passes**

Run: `cargo test test_decorator_extraction -- --nocapture`
Expected: PASS

**Step 5: Run full test suite**

Run: `cargo test`
Expected: All 887+ tests pass

**Step 6: Commit**

```bash
git add repotoire-cli/src/parsers/python.rs
git commit -m "feat: extract Python decorator names into function/class annotations"
```

---

## Task 2: Extract Rust Attributes into Annotations

**Files:**
- Modify: `repotoire-cli/src/parsers/rust.rs:107-120` (extract_functions)
- Modify: `repotoire-cli/src/parsers/rust.rs:412-425` (parse_impl_method)
- Modify: `repotoire-cli/src/parsers/rust.rs:225-285` (struct/enum/trait parsing)
- Test: `repotoire-cli/src/parsers/rust.rs:571+` (inline tests)

**Context:** The Rust parser has zero attribute extraction. `attribute_item` nodes are siblings (preceding) of `function_item` in the tree-sitter Rust grammar. The Java parser already does this pattern — use it as reference.

**Step 1: Write failing test**

Add to the `#[cfg(test)] mod tests` block:

```rust
#[test]
fn test_attribute_extraction() {
    let code = r#"
#[test]
fn test_something() {
    assert!(true);
}

#[derive(Debug, Clone)]
struct MyStruct {
    field: i32,
}

#[tokio::main]
async fn main() {
    println!("hello");
}

pub fn no_attrs() {}
"#;
    let result = parse_rust(code, Path::new("test.rs"));

    let test_fn = result.functions.iter().find(|f| f.name == "test_something").unwrap();
    assert!(
        test_fn.annotations.iter().any(|a| a.contains("test")),
        "test_something should have #[test] annotation, got: {:?}",
        test_fn.annotations
    );

    let main_fn = result.functions.iter().find(|f| f.name == "main").unwrap();
    assert!(
        main_fn.annotations.iter().any(|a| a.contains("tokio::main")),
        "main should have #[tokio::main] annotation, got: {:?}",
        main_fn.annotations
    );

    let my_struct = result.classes.iter().find(|c| c.name == "MyStruct").unwrap();
    assert!(
        my_struct.annotations.iter().any(|a| a.contains("derive")),
        "MyStruct should have #[derive] annotation, got: {:?}",
        my_struct.annotations
    );

    let no_attrs_fn = result.functions.iter().find(|f| f.name == "no_attrs").unwrap();
    assert!(no_attrs_fn.annotations.is_empty());
}
```

**Step 2: Run test to verify it fails**

Run: `cargo test test_attribute_extraction -- --nocapture`
Expected: FAIL

**Step 3: Implement attribute extraction**

Add a helper function to `rust.rs`:

```rust
/// Extract attributes from preceding sibling `attribute_item` nodes.
/// Returns attribute strings like "test", "derive(Debug, Clone)", "tokio::main".
fn extract_rust_attributes(node: &tree_sitter::Node, source: &[u8]) -> Vec<String> {
    let mut attrs = Vec::new();
    let mut sibling = node.prev_sibling();
    while let Some(sib) = sibling {
        if sib.kind() == "attribute_item" {
            // attribute_item contains: '[' ... ']'
            // Extract the text between [ and ]
            let text = sib.utf8_text(source).unwrap_or("");
            let inner = text.trim_start_matches("#[").trim_end_matches(']').trim();
            if !inner.is_empty() {
                attrs.push(inner.to_string());
            }
        } else if sib.kind() == "line_comment" || sib.kind() == "block_comment" {
            // Skip comments between attributes and definition
        } else {
            break; // Stop at non-attribute, non-comment nodes
        }
        sibling = sib.prev_sibling();
    }
    attrs.reverse(); // Preserve declaration order
    attrs
}
```

Then replace `annotations: vec![],` at:
- Line 119 (`extract_functions`): `annotations: extract_rust_attributes(&node, source),`
- Line 424 (`parse_impl_method`): `annotations: extract_rust_attributes(&node, source),`
- Line 234 (`parse_struct_node`): `annotations: extract_rust_attributes(&node, source),`
- Line 256 (`parse_enum_node`): `annotations: extract_rust_attributes(&node, source),`
- Line 284 (`parse_trait_node`): `annotations: extract_rust_attributes(&node, source),`

**Step 4: Run test to verify it passes**

Run: `cargo test test_attribute_extraction -- --nocapture`
Expected: PASS

**Step 5: Run full test suite**

Run: `cargo test`
Expected: All tests pass

**Step 6: Commit**

```bash
git add repotoire-cli/src/parsers/rust.rs
git commit -m "feat: extract Rust #[attribute] into function/class annotations"
```

---

## Task 3: Extract JS/TS Decorators into Annotations

**Files:**
- Modify: `repotoire-cli/src/parsers/typescript/mod.rs:629-642` (parse_arrow_field_node)
- Modify: `repotoire-cli/src/parsers/typescript/mod.rs:665-678` (parse_method_node)
- Modify: `repotoire-cli/src/parsers/typescript/mod.rs:450-462` (extract_classes)
- Test: `repotoire-cli/src/parsers/typescript/` (test file)

**Context:** The TS/JS parser already has annotation extraction for React patterns (lines 260-264). It also already skips `decorator` nodes in JSDoc search (line 877). TC39 decorators use `decorator` node type in tree-sitter. Note: Express-style `app.get('/path', handler)` is NOT a decorator — that's a callback, handled in Task 5.

**Step 1: Write failing test**

Find the test file for the TS parser (referenced at line 991: `mod tests;`). Add:

```rust
#[test]
fn test_decorator_extraction_ts() {
    let code = r#"
function Controller(path: string) { return (target: any) => {}; }
function Get(path: string) { return (target: any, key: string) => {}; }

@Controller('/users')
class UserController {
    @Get('/')
    getUsers() {
        return [];
    }
}
"#;
    let result = parse_typescript(code, Path::new("test.ts"));

    let controller = result.classes.iter().find(|c| c.name == "UserController").unwrap();
    assert!(
        controller.annotations.iter().any(|a| a.contains("Controller")),
        "UserController should have @Controller annotation, got: {:?}",
        controller.annotations
    );
}
```

Note: TC39 decorators may not be fully supported in the tree-sitter JS grammar (they're stage 3). If tree-sitter doesn't produce `decorator` nodes for the above code, this test needs to be adapted to whatever the grammar supports. Check with `tree-sitter parse` first. If not supported, skip this task — Express-style callback handling (Task 5) is more impactful for JS.

**Step 2: Run test to verify it fails**

Run: `cargo test test_decorator_extraction_ts -- --nocapture`
Expected: FAIL (or possibly compile error if tree-sitter grammar doesn't support)

**Step 3: Implement decorator extraction**

Similar to Python — add a helper that finds `decorator` children on `class_declaration` or `method_definition` nodes, extracts the name. Apply to:
- `parse_method_node()` line 677: replace `annotations: vec![]`
- `parse_arrow_field_node()` line 641: replace `annotations: vec![]`
- `extract_classes()` line 462: replace `annotations: vec![]`

If the tree-sitter grammar doesn't support decorators, skip this task entirely. The JS/TS FP reduction comes primarily from callback detection (Task 5), not decorators.

**Step 4: Run tests**

Run: `cargo test`
Expected: All tests pass

**Step 5: Commit**

```bash
git add repotoire-cli/src/parsers/typescript/
git commit -m "feat: extract JS/TS decorator names into annotations"
```

---

## Task 4: Synthetic Calls Edges for Decorated Functions

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/graph.rs:92-121` (build_func_node helper)
- Modify: `repotoire-cli/src/cli/analyze/graph.rs:189-223` (build_graph inline)
- Modify: `repotoire-cli/src/cli/analyze/graph.rs:360-372` (build_graph_chunked)
- Modify: `repotoire-cli/src/cli/analyze/graph.rs:907-914` (streaming builder)

**Context:** After Tasks 1-3, function nodes will have populated `annotations`. The graph builder already serializes annotations to node properties (lines 109-119). Now we need to emit `Calls` edges so detectors see decorated functions as having callers.

**Step 1: Write failing integration test**

Create a test that verifies a decorated Python function has callers in the graph:

```rust
#[test]
fn test_decorated_function_has_callers() {
    // Parse a Python file with a decorated function
    let code = r#"
@app.route('/users')
def get_users():
    return []

def undecorated():
    pass
"#;
    let parse_result = parse_python(code, Path::new("test_app.py"));

    // Build graph
    let mut graph = /* create a test graph store */;
    // Insert parsed functions into graph
    // ...

    // get_users should have callers (from synthetic decorator dispatch)
    let callers = graph.get_callers("test_app.get_users");
    assert!(!callers.is_empty(), "Decorated function should have callers");

    // undecorated should have no callers
    let callers = graph.get_callers("test_app.undecorated");
    assert!(callers.is_empty(), "Undecorated function should have no callers");
}
```

Place this in the appropriate test module for the graph builder or as an integration test.

**Step 2: Run test to verify it fails**

Run: `cargo test test_decorated_function_has_callers -- --nocapture`
Expected: FAIL — no synthetic callers exist yet

**Step 3: Implement synthetic Calls edges**

In `build_func_node()` (lines 92-121 of `graph.rs`), after building the node, return both the node AND the annotations so the caller can create edges. Or better — add the edge creation logic in the graph building loop itself.

In all three graph building paths (`build_graph`, `build_graph_chunked`, `StreamingGraphBuilderImpl`), after inserting a function node:

```rust
// After inserting the function node into the graph:
if !func.annotations.is_empty() {
    // Create a synthetic dispatcher node for this module (once per module)
    let module_qn = /* extract module qualified name */;
    let dispatcher_qn = format!("{}.__decorator_dispatch__", module_qn);

    // Ensure dispatcher node exists
    if graph.get_node(&dispatcher_qn).is_none() {
        let mut dispatcher = CodeNode::new(&dispatcher_qn, NodeKind::Function);
        dispatcher.file_path = func.file_path.to_string_lossy().to_string();
        dispatcher.set("synthetic", serde_json::Value::Bool(true));
        graph.add_node(dispatcher);
    }

    // Add Calls edge: dispatcher → decorated function
    graph.add_edge(&dispatcher_qn, &func_qn, CodeEdge::calls());
}
```

Apply to all three code paths:
1. `build_graph()` inline path (line ~223) — after `graph.add_node(node)`
2. `build_graph_chunked()` (line ~372) — after `graph.add_node(node)`
3. `StreamingGraphBuilderImpl::on_file()` (line ~914) — after `graph.add_node(node)`

**Step 4: Run test to verify it passes**

Run: `cargo test test_decorated_function_has_callers -- --nocapture`
Expected: PASS

**Step 5: Run full test suite**

Run: `cargo test`
Expected: All tests pass

**Step 6: Commit**

```bash
git add repotoire-cli/src/cli/analyze/graph.rs
git commit -m "feat: emit synthetic Calls edges for decorated functions"
```

---

## Task 5: Callback Argument Detection in JS/TS

**Files:**
- Modify: `repotoire-cli/src/parsers/typescript/mod.rs:762-799` (extract_calls_recursive)
- Test: `repotoire-cli/src/parsers/typescript/` (test file)

**Context:** `extract_calls_recursive` extracts `(caller, callee)` from `call_expression` nodes but ignores arguments. When a function reference is passed as an argument (e.g., `app.get('/path', handler)`), `handler` should appear in the calls list as a callee.

**Step 1: Write failing test**

```rust
#[test]
fn test_callback_argument_detection() {
    let code = r#"
function handler(req, res) {
    res.send('ok');
}

function transform(item) {
    return item.name;
}

app.get('/path', handler);
items.map(transform);
"#;
    let result = parse_typescript(code, Path::new("test.js"));

    // "handler" should appear as a callee (called by app.get)
    let handler_called = result.calls.iter().any(|(_, callee)| callee == "handler");
    assert!(handler_called, "handler should be in calls list as callback arg");

    // "transform" should appear as a callee (called by items.map)
    let transform_called = result.calls.iter().any(|(_, callee)| callee == "transform");
    assert!(transform_called, "transform should be in calls list as callback arg");
}
```

**Step 2: Run test to verify it fails**

Run: `cargo test test_callback_argument_detection -- --nocapture`
Expected: FAIL — callback arguments not extracted

**Step 3: Implement callback argument detection**

In `extract_calls_recursive` (lines 762-799), after extracting the call target, also inspect the arguments node:

```rust
// After the existing call extraction logic, add:
// Check arguments for function references (callbacks)
if let Some(args_node) = node.child_by_field_name("arguments") {
    let mut arg_cursor = args_node.walk();
    for arg in args_node.children(&mut arg_cursor) {
        match arg.kind() {
            // Direct function reference: app.get('/path', handler)
            "identifier" => {
                let arg_name = arg.utf8_text(source).unwrap_or("").to_string();
                if !arg_name.is_empty() && arg_name != "undefined" && arg_name != "null" {
                    // Only count as callback if it looks like a function name
                    // (starts with lowercase, not a common value like "true"/"false")
                    let first_char = arg_name.chars().next().unwrap_or('_');
                    if first_char.is_lowercase() || first_char == '_' {
                        calls.push((scope.clone(), arg_name));
                    }
                }
            }
            // Member expression: app.get('/path', controllers.getUsers)
            "member_expression" => {
                if let Some(target) = Self::extract_call_target(&arg, source) {
                    calls.push((scope.clone(), target));
                }
            }
            _ => {}
        }
    }
}
```

The `scope` variable should be the containing function scope (same one used for the call itself). The `extract_call_target` method at lines 807-818 already handles `member_expression` nodes.

Build a `known_functions` set from the functions already parsed in this file to reduce false edges:

```rust
let known_functions: HashSet<&str> = parse_result.functions.iter()
    .map(|f| f.name.as_str())
    .collect();

// Then filter: only add callback edge if arg_name is in known_functions
if known_functions.contains(arg_name.as_str()) {
    calls.push((scope.clone(), arg_name));
}
```

Note: This requires restructuring `extract_calls_recursive` to receive the known functions set. Alternatively, skip the filter and let the graph builder handle unresolved names (they'll just be dangling edges that get dropped).

**Step 4: Run test to verify it passes**

Run: `cargo test test_callback_argument_detection -- --nocapture`
Expected: PASS

**Step 5: Run full test suite**

Run: `cargo test`
Expected: All tests pass

**Step 6: Commit**

```bash
git add repotoire-cli/src/parsers/typescript/
git commit -m "feat: detect callback function arguments as call edges in JS/TS"
```

---

## Task 6: Reassignment Count for GlobalVariablesDetector

**Files:**
- Modify: `repotoire-cli/src/detectors/global_variables.rs:292-317` (if is_global block)
- Test: `repotoire-cli/src/detectors/global_variables.rs:330+` (inline tests)

**Context:** The detector flags all module-scope `var`/`let` as mutable globals. We need to count reassignments — if the variable is assigned exactly once (at declaration), it's effectively constant and should be skipped or downgraded to Info.

**Step 1: Write failing test**

Add to the `#[cfg(test)] mod tests` block (after line 392):

```rust
#[test]
fn test_single_assignment_not_flagged() {
    // var app = express() — assigned once, never reassigned → should NOT be flagged
    let code = "var app = express();\napp.get('/', handler);\napp.listen(3000);";
    let detector = GlobalVariablesDetector::default();
    let graph = /* create empty mock graph */;
    let files = /* create mock file provider with test.js containing code */;
    let findings = detector.detect(&graph, &files).unwrap();

    // "app" is assigned once → effectively const → no finding
    assert!(
        findings.is_empty(),
        "Single-assignment var should not be flagged, got: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_reassigned_variable_flagged() {
    // var count = 0; count++ — genuinely mutable → should be flagged
    let code = "var count = 0;\ncount++;\nconsole.log(count);";
    let detector = GlobalVariablesDetector::default();
    let graph = /* create empty mock graph */;
    let files = /* create mock file provider with test.js containing code */;
    let findings = detector.detect(&graph, &files).unwrap();

    // "count" is reassigned → genuinely mutable → finding expected
    assert!(
        !findings.is_empty(),
        "Reassigned var should be flagged"
    );
}
```

Match the existing test pattern in the file (lines 335-392) for how the mock graph and file provider are constructed.

**Step 2: Run test to verify it fails**

Run: `cargo test test_single_assignment_not_flagged -- --nocapture`
Expected: FAIL — single-assignment vars are still flagged

**Step 3: Implement reassignment count**

Add a helper method to `GlobalVariablesDetector`:

```rust
/// Count how many times a variable is reassigned after its declaration line.
/// Returns 0 if never reassigned (effectively const).
fn count_reassignments(content: &str, var_name: &str, decl_line: usize) -> usize {
    let escaped = regex::escape(var_name);
    // Match: varName = (assignment), varName += -= *= /= (compound), varName++ varName--
    let pattern = format!(
        r"\b{}\s*(?:[+\-*/&|^%]?=(?!=)|(\+\+|--))",
        escaped
    );
    let re = match regex::Regex::new(&pattern) {
        Ok(r) => r,
        Err(_) => return 0,
    };

    content
        .lines()
        .enumerate()
        .filter(|(i, line)| *i != decl_line && re.is_match(line))
        .count()
}
```

Then in the `if is_global { ... }` block (line 292), after extracting the variable name (line 294) and before creating the finding (line 305), add:

```rust
// Skip effectively-constant variables (assigned once, never reassigned)
if ext != "py" {
    let reassignments = Self::count_reassignments(&content, &var_name, i);
    if reassignments == 0 {
        continue;
    }
}
```

We only apply this to JS/TS (not Python) because Python's `global` keyword already implies intentional mutation.

**Step 4: Run tests to verify they pass**

Run: `cargo test test_single_assignment_not_flagged test_reassigned_variable_flagged -- --nocapture`
Expected: PASS

**Step 5: Run full test suite**

Run: `cargo test`
Expected: All tests pass (including existing global_variables tests)

**Step 6: Commit**

```bash
git add repotoire-cli/src/detectors/global_variables.rs
git commit -m "feat: skip single-assignment module-scope vars (effectively const)"
```

---

## Task 7: Export Detection in Parsers

**Files:**
- Modify: `repotoire-cli/src/parsers/python.rs` (detect `__all__` membership or module-level defs)
- Modify: `repotoire-cli/src/parsers/rust.rs` (detect `pub` keyword)
- Modify: `repotoire-cli/src/parsers/typescript/mod.rs` (detect `export` keyword)
- Modify: `repotoire-cli/src/parsers/go.rs` (detect capitalized names)
- Modify: `repotoire-cli/src/models.rs` (add `is_exported` field or use annotations)

**Context:** Export detection logic currently lives in `unreachable_code.rs:270-352` (`is_exported_function`) and reads file content at detection time. This should move into parsers so it's available as a node property for all detectors.

**Step 1: Write failing test (Python)**

```rust
#[test]
fn test_export_detection_python() {
    let code = r#"
__all__ = ['public_func', 'PublicClass']

def public_func():
    pass

def _private_func():
    pass

class PublicClass:
    pass
"#;
    let result = parse_python(code, Path::new("test.py"));

    let public = result.functions.iter().find(|f| f.name == "public_func").unwrap();
    assert!(public.annotations.iter().any(|a| a == "exported"));

    let private = result.functions.iter().find(|f| f.name == "_private_func").unwrap();
    assert!(!private.annotations.iter().any(|a| a == "exported"));
}
```

**Step 2: Run test to verify it fails**

Run: `cargo test test_export_detection_python -- --nocapture`
Expected: FAIL

**Step 3: Implement export detection**

Use the existing `annotations` field rather than adding a new field to `Function` — push `"exported"` as an annotation. This avoids model changes and works with the existing graph property serialization.

**Python:** Scan for `__all__` list in file content. If found, check each function/class name against it. If no `__all__`, treat all non-underscore-prefixed module-level definitions as exported. Push `"exported"` to annotations.

**Rust:** Check if the tree-sitter `function_item` node's text starts with `pub`. Push `"exported"`.

**JS/TS:** The parser already detects `export_statement` wrapping function/class declarations (lines 43-48, 83-88). Push `"exported"` when function/class is inside an `export_statement`.

**Go:** Check if the function name's first character is uppercase. Push `"exported"`.

**Step 4: Run tests**

Run: `cargo test`
Expected: All tests pass

**Step 5: Commit**

```bash
git add repotoire-cli/src/parsers/
git commit -m "feat: detect exported functions/classes in all parsers via annotation"
```

---

## Task 8: API Surface Detection Utility

**Files:**
- Create: `repotoire-cli/src/detectors/api_surface.rs`
- Modify: `repotoire-cli/src/detectors/mod.rs` (add `pub mod api_surface;`)
- Modify: `repotoire-cli/src/detectors/unsafe_template.rs:195-225` (use API surface check)
- Test: `repotoire-cli/src/detectors/api_surface.rs` (inline tests)

**Context:** Security detectors flag API definitions (like Flask's `render_template_string`) as vulnerabilities. An exported function with high fan-in is an API — the vulnerability is in callers, not the definition. The graph already has `call_fan_in()` and the `annotations` property now includes `"exported"`.

**Step 1: Write the utility with test**

```rust
//! API surface detection utility.
//!
//! Determines whether a function at a given location is part of the project's
//! public API surface (exported + high fan-in), which affects how security
//! findings should be reported.

use crate::graph::GraphQuery;

/// Check if the function at the given file:line is part of the public API surface.
/// API surface = exported function with 3+ callers from different locations.
pub fn is_api_surface(graph: &dyn GraphQuery, file_path: &str, line: u32) -> bool {
    for func in graph.get_functions() {
        if func.file_path == file_path
            && func.line_start <= line
            && func.line_end >= line
        {
            // Check if exported (via annotation)
            let is_exported = func
                .get("annotations")
                .and_then(|v| v.as_array())
                .map(|arr| arr.iter().any(|a| a.as_str() == Some("exported")))
                .unwrap_or(false);

            if !is_exported {
                return false;
            }

            // Check fan-in (callers)
            let fan_in = graph.call_fan_in(&func.qualified_name);
            return fan_in >= 3;
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_api_surface_requires_exported_and_high_fan_in() {
        // Test with a mock graph that has an exported function with 5 callers
        // → should return true
        // Test with a non-exported function with 5 callers
        // → should return false
        // Test with an exported function with 1 caller
        // → should return false
    }
}
```

**Step 2: Integrate into UnsafeTemplateDetector**

In `scan_python_files()` (lines 195-225), before creating a finding for `render_template_string` or `Markup`, check:

```rust
// Before pushing the finding:
if is_api_surface(graph, &file_path, line_num) {
    // This is an API definition — downgrade or skip
    // Change severity to Info and add context to description
    finding.severity = Severity::Info;
    finding.description.push_str(
        "\n\nNote: This is a public API definition. The security risk is in caller code that passes unsanitized input, not in this definition."
    );
}
```

This requires threading the `graph` parameter into `scan_python_files` and `scan_javascript_files`. Currently they use `&self` with `self.repository_path` for filesystem access. Add `graph: &dyn GraphQuery` parameter.

**Step 3: Run tests**

Run: `cargo test`
Expected: All tests pass

**Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/api_surface.rs repotoire-cli/src/detectors/mod.rs repotoire-cli/src/detectors/unsafe_template.rs
git commit -m "feat: add API surface detection, downgrade security findings on API definitions"
```

---

## Task 9: Validation — Re-run Benchmarks

**Files:**
- Run: `benchmarks/validate_real_world.sh --skip-clone`
- Update: `benchmarks/VALIDATION_REPORT.md`

**Context:** All changes are in. Re-run against Flask, FastAPI, Express to measure FP reduction.

**Step 1: Build release binary**

Run: `cargo build --release`

**Step 2: Clear caches**

```bash
repotoire clean /tmp/flask
repotoire clean /tmp/fastapi
repotoire clean /tmp/express
```

**Step 3: Run analysis on all three**

```bash
repotoire analyze /tmp/flask --format json --no-emoji --output benchmarks/results/flask-v3.json
repotoire analyze /tmp/fastapi --format json --no-emoji --output benchmarks/results/fastapi-v3.json
repotoire analyze /tmp/express --format json --no-emoji --output benchmarks/results/express-v3.json
```

**Step 4: Compare results**

```bash
for project in flask fastapi express; do
  echo "=== $project ==="
  v1="benchmarks/results/$project.json"
  v3="benchmarks/results/$project-v3.json"
  echo "  Before: $(jq '.findings | length' $v1) findings"
  echo "  After:  $(jq '.findings | length' $v3) findings"
done
```

**Expected delta:**

| Project | Before | Expected After | Delta |
|---------|--------|---------------|-------|
| Flask | 26 | ~23 | -3 (API surface) |
| FastAPI | 78 | ~73 | -5 (dead handlers) |
| Express | 91 | ~64 | -27 (reassignment + callbacks) |

**Step 5: Update validation report**

Add a "Post-Universal-FP-Reduction" section to `benchmarks/VALIDATION_REPORT.md` with the new numbers.

**Step 6: Commit**

```bash
git add benchmarks/
git commit -m "test: validate universal FP reduction (Flask, FastAPI, Express)"
```

---

## Implementation Order and Dependencies

```
Task 1 (Python decorators) ──┐
Task 2 (Rust attributes)  ───┤
Task 3 (JS/TS decorators) ───┼──→ Task 4 (Synthetic edges) ──→ Task 9 (Validation)
Task 5 (Callbacks)  ──────────┘                                     ↑
Task 6 (Reassignment) ────────────────────────────────────────────────┤
Task 7 (Export detection) ──→ Task 8 (API surface) ───────────────────┘
```

Tasks 1, 2, 3, 5, 6 are independent and can run in parallel.
Task 4 depends on Tasks 1-3.
Task 8 depends on Task 7.
Task 9 depends on all.
