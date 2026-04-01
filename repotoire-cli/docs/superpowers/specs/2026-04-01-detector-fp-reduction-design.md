# Detector False Positive Reduction: GlobalVariables + UnhandledPromise

## Context

User reported 4 false positives on a real TypeScript codebase that created a deadlock (can't mark FPs because `feedback` was broken, can't commit because pre-commit hook blocks on HIGH findings). The `feedback` bug is now fixed, but the detectors should not produce these FPs in the first place.

The fixes align with industry-standard ESLint rules:
- `no-implicit-globals` — only flags script-mode globals, not module-scoped variables
- `no-floating-promises` — defines 5 valid handling patterns including `return`

---

## 1. GlobalVariablesDetector

### Problem

Flags module-scoped `let` in TypeScript/ES module files as "global mutable variable." Module-scoped variables are encapsulated by definition — the module IS the encapsulation boundary. ESLint's `no-implicit-globals` explicitly does not apply to modules.

**Reported FP:** `let currentAuth` in `lib/auth.ts:18` — standard in-memory session state pattern for client-side apps. Module-internal, used by exported functions but not exported itself.

### Fix

**Detect whether the file is an ES module or script.** A file is a module if:
- It has any `import` or `export` statement, OR
- File extension is `.ts`, `.tsx`, `.mts` (TypeScript is always modules), OR
- File extension is `.mjs` (ES module by definition)

Script-mode files: `.js` without import/export, `.cjs`, `.cts`.

**In module files:** Only flag true globals that escape module scope:
- `window.x = ...`
- `globalThis.x = ...`
- `global.x = ...` (Node.js)
- `self.x = ...` (Web Workers)

**In script files:** Keep current behavior — flag module-scope `var`/`let` that are reassigned.

### Implementation

**Step 1: Fix pre-existing bug — `files_with_extensions` loop**

The `file_extensions()` method returns `["py", "js", "ts", "jsx", "tsx"]` but the loop iterates `["py", "js", "ts"]`. Add `"jsx"` and `"tsx"` to the loop to match.

**Step 2: Module detection**

In `detect()`, after loading file content, before the per-line loop:

```rust
// Detect ES module: TS/MTS/MJS are always modules; JS/JSX are modules if they use import/export
let is_module = matches!(ext, "ts" | "tsx" | "mts" | "mjs")
    || content.lines().any(|l| {
        let t = l.trim();
        (t.starts_with("import ") || t.starts_with("import{"))
            && !t.starts_with("import(") // dynamic import() is an expression, not a module declaration
            || t.starts_with("export ")
            || t.starts_with("export{")
    });
```

**Step 3: Update `is_global` check for JS/TS**

```rust
if is_module {
    // In ES modules, only flag true globals that escape module scope
    let is_true_global = trimmed.starts_with("window.")
        || trimmed.starts_with("globalThis.")
        || trimmed.starts_with("global.")
        || trimmed.starts_with("self.");
    // Must also be an assignment (contains `=` but not `==`)
    is_true_global && trimmed.contains('=') && !trimmed.contains("==")
} else {
    // Script mode: current behavior (module-scope var/let that are reassigned)
    let at_module_scope = !line.starts_with(' ') && !line.starts_with('\t');
    let is_require = trimmed.contains("require(");
    at_module_scope
        && (trimmed.starts_with("var ") || trimmed.starts_with("let "))
        && !is_require
}
```

**Step 4: Update `extract_var_name()` to handle global object assignments**

Add a new pattern for `window.foo = ...`, `globalThis.foo = ...`, etc:

```rust
fn extract_var_name(line: &str) -> Option<String> {
    let trimmed = line.trim();

    // Handle global object property assignments: window.foo = ..., globalThis.bar = ...
    for prefix in &["window.", "globalThis.", "global.", "self."] {
        if let Some(rest) = trimmed.strip_prefix(prefix) {
            // Extract property name before the = sign
            let name = rest.split(|c: char| !c.is_alphanumeric() && c != '_').next()?;
            if !name.is_empty() {
                return Some(name.to_string());
            }
        }
    }

    // Existing: var/let/global keyword patterns
    if let Some(caps) = VAR_NAME.captures(trimmed) {
        return caps.get(1).map(|m| m.as_str().to_string());
    }
    // ... rest of existing logic
}
```

**Step 5: Skip `count_reassignments` for global object patterns**

`count_reassignments` searches for `\bfoo\b` assignments, which won't correctly match `window.foo = ...`. For global object findings, skip the reassignment check (these are always mutable by nature — you don't write `window.x = 1` for a constant).

### Tests

- `test_no_finding_for_module_scoped_let` — `let state = null` in `.js` file with `import` → no finding
- `test_no_finding_for_ts_module_scoped_let` — `let state = null` in `.ts` file → no finding
- `test_flags_window_global_in_module` — `window.myGlobal = 123; window.myGlobal = 456;` in module → finding
- `test_flags_globalthis_in_module` — `globalThis.config = {}` in module → finding
- `test_still_flags_script_mode_var` — `var count = 0; count++` in `.js` without import/export → finding
- `test_no_finding_for_mjs_module_scoped_let` — `let x = 1; x = 2;` in `.mjs` → no finding
- Keep all existing tests passing

**Files:** `src/detectors/bugs/global_variables.rs`

---

## 2. UnhandledPromiseDetector

### Problem

Flags async functions and `new Promise()` that don't have try/catch or .catch() *internally*, ignoring that the Promise may be properly handled at the call site. The industry standard (`no-floating-promises`) defines a Promise as "handled" if it is:

1. `.then()` with two arguments (success + error handler)
2. `.catch()`
3. `await`ed
4. `return`ed (caller's responsibility)
5. `void`ed (explicitly marked as intentionally unawaited)

The detector already handles #2 and #3. It misses **#1, #4, and #5**.

**Reported FPs:**
- `putLocalEnvelopes` / `getLocalEnvelopes` (db.ts:115, 123) — async functions that return Promises. Callers await them. Detector flags the function internals.
- `awaitDeleteDatabase` (db.ts:132) — returns `new Promise(...)`. Caller consumes via `await Promise.all(...)`. Detector flags the `new Promise()` line.

### Fix

Three new exemptions, applied **after** `has_promise` and `calls_async` are determined (not before):

**(a) Return statement exemption (#4):** If the line is a `return` statement containing a Promise-producing expression, skip it. The caller is responsible.

```rust
// After has_promise and calls_async are computed, before the error-handling check:
if (has_promise || calls_async)
    && (trimmed.starts_with("return ") || trimmed.starts_with("return("))
{
    continue;
}
```

This is conditional on the line actually involving a promise — `return 42` is not skipped.

**(b) Two-arg `.then()` exemption (#1):** If `.then(` is present and the then call has two arguments (comma between opening and closing parens), it handles rejections.

```rust
// In the has_catch check, also count two-arg .then():
let has_two_arg_then = {
    if let Some(then_idx) = context.find(".then(") {
        let after_then = &context[then_idx + 6..];
        // Count commas before the closing paren at depth 0
        let mut depth = 0i32;
        let mut has_comma = false;
        for ch in after_then.chars() {
            match ch {
                '(' => depth += 1,
                ')' if depth == 0 => break,
                ')' => depth -= 1,
                ',' if depth == 0 => { has_comma = true; break; }
                _ => {}
            }
        }
        has_comma
    } else {
        false
    }
};

if has_catch || in_try || has_two_arg_then {
    continue;
}
```

**(c) Void exemption (#5):** If the line starts with `void `, it's explicitly marked as intentionally unawaited.

```rust
// Early in the loop, near the await skip:
if trimmed.starts_with("void ") {
    continue;
}
```

**(d) Assignment-then-return exemption:** If the result is assigned to a variable that is later returned in the same function scope, skip.

```rust
fn extract_assignment_target(line: &str) -> Option<&str> {
    let trimmed = line.trim();
    for prefix in &["const ", "let ", "var "] {
        if let Some(rest) = trimmed.strip_prefix(prefix) {
            return rest.split(|c: char| !c.is_alphanumeric() && c != '_').next()
                .filter(|s| !s.is_empty());
        }
    }
    None
}

fn is_returned_in_scope(lines: &[&str], from: usize, var_name: &str) -> bool {
    let mut depth = 0i32;
    for line in &lines[from + 1..] {
        let t = line.trim();
        depth += t.matches('{').count() as i32;
        depth -= t.matches('}').count() as i32;
        if depth < 0 { break; } // left the scope
        if t.starts_with("return ") && t.contains(var_name) {
            return true;
        }
    }
    false
}
```

**NOT implementing:** Arrow function implicit return via `=>` check. The reviewer correctly identified this as too broad — `items.forEach(item => fetch(item))` is fire-and-forget and SHOULD be flagged. Arrow function implicit returns are an edge case that can be addressed later with proper AST analysis.

### Tests

- `test_no_finding_for_returned_promise` — `return new Promise(...)` → no finding
- `test_no_finding_for_returned_async_call` — `return fetchData()` in async function → no finding
- `test_no_finding_for_assigned_and_returned_promise` — `const p = fetch(...); return p;` → no finding
- `test_no_finding_for_void_promise` — `void fetchData()` → no finding
- `test_no_finding_for_two_arg_then` — `.then(success, error)` → no finding
- `test_still_flags_fire_and_forget` — `fetchData();` without await/assign/return → finding
- `test_still_flags_then_without_catch` — `.then(cb)` with one arg, no `.catch()` → finding
- `test_still_flags_arrow_fire_and_forget` — `items.forEach(item => fetch(item))` → finding
- Keep all existing tests passing

**Files:** `src/detectors/bugs/unhandled_promise.rs`

---

## Verification

```bash
cd repotoire-cli

# Unit tests
cargo test global_variables -- --nocapture
cargo test unhandled_promise -- --nocapture

# Full test suite
cargo test

# Lint
cargo clippy --all-features -- -D warnings
cargo fmt --all -- --check
```

Manual verification: run repotoire against a TypeScript codebase with module-scoped `let` and async functions that return Promises. Confirm zero false positives on those patterns.
