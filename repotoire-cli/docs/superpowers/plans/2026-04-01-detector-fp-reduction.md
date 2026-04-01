# Detector FP Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate false positives in GlobalVariablesDetector (module-scoped let in ES modules) and UnhandledPromiseDetector (returned/voided promises). Align with ESLint's `no-implicit-globals` and `no-floating-promises`.

**Spec:** `docs/superpowers/specs/2026-04-01-detector-fp-reduction-design.md`

---

## File Structure

### Modified Files
| File | Changes |
|------|---------|
| `src/detectors/bugs/global_variables.rs` | Module detection, true-global-only mode, extract_var_name update, files_with_extensions fix |
| `src/detectors/bugs/unhandled_promise.rs` | Return/void/two-arg-then exemptions, assignment-then-return helper |

---

### Task 1: GlobalVariablesDetector — module detection + true-global-only mode

**Files:** `src/detectors/bugs/global_variables.rs`

- [ ] **Step 1: Fix `files_with_extensions` loop**

The `file_extensions()` returns `["py", "js", "ts", "jsx", "tsx"]` but the iteration loop on line 220 uses `["py", "js", "ts"]`. Add `"jsx"`, `"tsx"`, `"mjs"`, `"mts"` to both:

Update `file_extensions()`:
```rust
fn file_extensions(&self) -> &'static [&'static str] {
    &["py", "js", "ts", "jsx", "tsx", "mjs", "mts"]
}
```

Update the loop:
```rust
for path in files.files_with_extensions(&["py", "js", "ts", "jsx", "tsx", "mjs", "mts"]) {
```

- [ ] **Step 2: Add module detection after loading content**

After `if let Some(content) = files.content(path) {` and the bundled/minified/fixture checks, add:

```rust
let is_module = matches!(ext, "ts" | "tsx" | "mts" | "mjs")
    || content.lines().any(|l| {
        let t = l.trim();
        ((t.starts_with("import ") || t.starts_with("import{"))
            && !t.starts_with("import("))
            || t.starts_with("export ")
            || t.starts_with("export{")
    });
```

- [ ] **Step 3: Update the JS/TS `is_global` check**

Replace the existing JS/TS branch (lines 296-307) with:

```rust
} else if is_module {
    // ES modules: only flag true globals that escape module scope
    let is_true_global = trimmed.starts_with("window.")
        || trimmed.starts_with("globalThis.")
        || trimmed.starts_with("global.")
        || trimmed.starts_with("self.");
    is_true_global && trimmed.contains('=') && !trimmed.contains("==")
} else {
    // Script mode: current behavior
    let at_module_scope = !line.starts_with(' ') && !line.starts_with('\t');
    let is_require = trimmed.contains("require(");
    at_module_scope
        && (trimmed.starts_with("var ") || trimmed.starts_with("let "))
        && !is_require
};
```

- [ ] **Step 4: Update `extract_var_name()` for global object patterns**

Add handling for `window.foo = ...` etc at the top of the function, before the existing `VAR_NAME` regex:

```rust
fn extract_var_name(line: &str) -> Option<String> {
    let trimmed = line.trim();
    // Handle global object property assignments
    for prefix in &["window.", "globalThis.", "global.", "self."] {
        if let Some(rest) = trimmed.strip_prefix(prefix) {
            let name: String = rest.chars()
                .take_while(|c| c.is_alphanumeric() || *c == '_')
                .collect();
            if !name.is_empty() {
                return Some(name);
            }
        }
    }
    // ... existing VAR_NAME regex logic unchanged
```

- [ ] **Step 5: Skip `count_reassignments` for global object findings**

In the section after `extract_var_name` (lines 322-330), the reassignment check uses `\bfoo\b` which won't match `window.foo`. Add a condition:

```rust
if ext != "py" && !is_module {
    let reassignments = Self::count_reassignments(&content, &var_name, i);
    if reassignments == 0 {
        continue;
    }
}
// For module-mode true globals (window.x etc), always flag — they're mutable by nature
```

- [ ] **Step 6: Add tests**

```rust
#[test]
fn test_no_finding_for_module_scoped_let() {
    let store = GraphBuilder::new().freeze();
    let detector = GlobalVariablesDetector::new("/mock/repo");
    let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
        &store,
        vec![(
            "state.js",
            "import { something } from './other';\nlet currentAuth = null;\ncurrentAuth = getAuth();\nexport function getState() { return currentAuth; }\n",
        )],
    );
    let findings = detector.detect(&ctx).unwrap();
    assert!(findings.is_empty(), "Module-scoped let should not be flagged, got: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}

#[test]
fn test_no_finding_for_ts_module_scoped_let() {
    let store = GraphBuilder::new().freeze();
    let detector = GlobalVariablesDetector::new("/mock/repo");
    let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
        &store,
        vec![(
            "auth.ts",
            "let currentAuth: Auth | null = null;\ncurrentAuth = login();\nexport function getAuth() { return currentAuth; }\n",
        )],
    );
    let findings = detector.detect(&ctx).unwrap();
    assert!(findings.is_empty(), "TS module-scoped let should not be flagged");
}

#[test]
fn test_flags_window_global_in_module() {
    let store = GraphBuilder::new().freeze();
    let detector = GlobalVariablesDetector::new("/mock/repo");
    let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
        &store,
        vec![(
            "globals.js",
            "import { x } from './y';\nwindow.myGlobal = 123;\n",
        )],
    );
    let findings = detector.detect(&ctx).unwrap();
    assert!(!findings.is_empty(), "window.x assignment should be flagged in module");
    assert!(findings[0].title.contains("myGlobal"));
}

#[test]
fn test_still_flags_script_mode_var() {
    let store = GraphBuilder::new().freeze();
    let detector = GlobalVariablesDetector::new("/mock/repo");
    let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
        &store,
        vec![("script.js", "var count = 0;\ncount++;\nconsole.log(count);")],
    );
    let findings = detector.detect(&ctx).unwrap();
    assert!(!findings.is_empty(), "Script-mode var should still be flagged");
}

#[test]
fn test_flags_globalthis_in_module() {
    let store = GraphBuilder::new().freeze();
    let detector = GlobalVariablesDetector::new("/mock/repo");
    let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
        &store,
        vec![(
            "config.js",
            "import { defaults } from './defaults';\nglobalThis.appConfig = defaults;\n",
        )],
    );
    let findings = detector.detect(&ctx).unwrap();
    assert!(!findings.is_empty(), "globalThis.x assignment should be flagged");
    assert!(findings[0].title.contains("appConfig"));
}

#[test]
fn test_no_finding_for_mjs_module_scoped_let() {
    let store = GraphBuilder::new().freeze();
    let detector = GlobalVariablesDetector::new("/mock/repo");
    let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
        &store,
        vec![("state.mjs", "let x = 1;\nx = 2;\nconsole.log(x);\n")],
    );
    let findings = detector.detect(&ctx).unwrap();
    assert!(findings.is_empty(), ".mjs module-scoped let should not be flagged");
}
```

- [ ] **Step 7: Verify**

Run: `cargo test global_variables -- --nocapture`

- [ ] **Step 8: Commit**

```bash
git add src/detectors/bugs/global_variables.rs
git commit -m "fix(detectors): skip module-scoped variables in GlobalVariablesDetector

Only flag true globals (window.x, globalThis.x) in ES module/TypeScript
files. Module-scoped let/var are encapsulated by the module boundary.
Aligns with ESLint no-implicit-globals behavior."
```

---

### Task 2: UnhandledPromiseDetector — return exemption

**Files:** `src/detectors/bugs/unhandled_promise.rs`

- [ ] **Step 1: Add `void` exemption**

Near the existing `await` skip (line 134), add:

```rust
// Skip void-prefixed promises — explicitly marked as intentionally unawaited
if trimmed.starts_with("void ") {
    continue;
}
```

- [ ] **Step 2: Add return exemption (conditional)**

After `has_promise` and `calls_async` are computed (around line 199), before the error-handling context check, add:

```rust
// Skip returned promises — caller handles errors (no-floating-promises #4)
if (has_promise || calls_async)
    && (trimmed.starts_with("return ") || trimmed.starts_with("return("))
{
    continue;
}
```

This is intentionally placed AFTER `has_promise`/`calls_async` are known, so `return 42` is not affected.

- [ ] **Step 3: Add two-arg `.then()` exemption**

In the error-handling check section (around line 206), update the `has_catch` / `in_try` condition to also check for two-arg `.then()`:

```rust
// Check for two-arg .then(success, error) — handles rejections
let has_two_arg_then = context.contains(".then(").then(|| {
    // Find .then( and check if there's a comma at depth 0 before closing paren
    if let Some(then_idx) = context.find(".then(") {
        let after = &context[then_idx + 6..];
        let mut depth = 0i32;
        for ch in after.chars() {
            match ch {
                '(' => depth += 1,
                ')' if depth == 0 => return false,
                ')' => depth -= 1,
                ',' if depth == 0 => return true,
                _ => {}
            }
        }
    }
    false
}).unwrap_or(false);

if has_catch || in_try || has_two_arg_then {
    continue;
}
```

NOTE: The `context.contains(".then(")` guard avoids the paren-scanning work when there's no `.then(` at all. Verify that `.then(` isn't already part of the `has_catch` check — if so, just extend that block.

- [ ] **Step 4: Add assignment-then-return helper**

Add two helper functions before the `impl Detector`:

```rust
/// Extract variable name from an assignment: `const x = ...` → Some("x")
fn extract_assignment_target(line: &str) -> Option<&str> {
    let trimmed = line.trim();
    for prefix in &["const ", "let ", "var "] {
        if let Some(rest) = trimmed.strip_prefix(prefix) {
            let name = rest.split(|c: char| !c.is_alphanumeric() && c != '_').next()?;
            if !name.is_empty() {
                return Some(name);
            }
        }
    }
    None
}

/// Check if a variable is returned later in the same function scope.
fn is_returned_in_scope(lines: &[&str], from: usize, var_name: &str) -> bool {
    // Use word-boundary regex to avoid substring matches (e.g., var "p" matching "promise")
    let pattern = format!(r"\b{}\b", regex::escape(var_name));
    let re = match Regex::new(&pattern) {
        Ok(r) => r,
        Err(_) => return false,
    };
    let mut depth = 0i32;
    for line in &lines[from + 1..] {
        let t = line.trim();
        depth += t.matches('{').count() as i32;
        depth -= t.matches('}').count() as i32;
        if depth < 0 {
            break;
        }
        if t.starts_with("return ") && re.is_match(t) {
            return true;
        }
    }
    false
}
```

Then in the detection loop, after the return exemption and before the error-handling check:

```rust
// Skip assigned promises that are later returned
if has_promise || calls_async {
    if let Some(var_name) = extract_assignment_target(trimmed) {
        if is_returned_in_scope(&lines, i, var_name) {
            continue;
        }
    }
}
```

- [ ] **Step 5: Add tests**

```rust
#[test]
fn test_no_finding_for_returned_promise() {
    let store = GraphBuilder::new().freeze();
    let detector = UnhandledPromiseDetector::new("/mock/repo");
    let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
        &store,
        vec![(
            "db.ts",
            "async function deleteDb() {\n  return new Promise((resolve, reject) => {\n    const req = indexedDB.deleteDatabase('mydb');\n    req.onsuccess = () => resolve(undefined);\n    req.onerror = () => reject(req.error);\n  });\n}\n",
        )],
    );
    let findings = detector.detect(&ctx).unwrap();
    assert!(findings.is_empty(), "Returned Promise should not be flagged, got: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}

#[test]
fn test_no_finding_for_returned_async_call() {
    let store = GraphBuilder::new().freeze();
    let detector = UnhandledPromiseDetector::new("/mock/repo");
    let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
        &store,
        vec![(
            "api.ts",
            "async function getUser(id: string) {\n  return fetch(`/api/users/${id}`);\n}\n",
        )],
    );
    let findings = detector.detect(&ctx).unwrap();
    assert!(findings.is_empty(), "Returned async call should not be flagged");
}

#[test]
fn test_no_finding_for_assigned_and_returned() {
    let store = GraphBuilder::new().freeze();
    let detector = UnhandledPromiseDetector::new("/mock/repo");
    let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
        &store,
        vec![(
            "store.ts",
            "async function saveData(data: any) {\n  const result = fetch('/api/save', { method: 'POST' });\n  return result;\n}\n",
        )],
    );
    let findings = detector.detect(&ctx).unwrap();
    assert!(findings.is_empty(), "Assigned-then-returned promise should not be flagged");
}

#[test]
fn test_no_finding_for_void_promise() {
    let store = GraphBuilder::new().freeze();
    let detector = UnhandledPromiseDetector::new("/mock/repo");
    let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
        &store,
        vec![(
            "fire.ts",
            "async function init() {\n  void fetch('/api/ping');\n}\n",
        )],
    );
    let findings = detector.detect(&ctx).unwrap();
    assert!(findings.is_empty(), "void-prefixed promise should not be flagged");
}

#[test]
fn test_no_finding_for_two_arg_then() {
    let store = GraphBuilder::new().freeze();
    let detector = UnhandledPromiseDetector::new("/mock/repo");
    let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
        &store,
        vec![(
            "handler.js",
            "async function load() {\n  fetch('/data').then(res => res.json(), err => console.error(err));\n}\n",
        )],
    );
    let findings = detector.detect(&ctx).unwrap();
    assert!(findings.is_empty(), "Two-arg .then() should not be flagged");
}

#[test]
fn test_still_flags_fire_and_forget() {
    let store = GraphBuilder::new().freeze();
    let detector = UnhandledPromiseDetector::new("/mock/repo");
    let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
        &store,
        vec![(
            "bad.js",
            "async function doStuff() {\n  fetch('/api/data').then(res => res.json());\n}\n",
        )],
    );
    let findings = detector.detect(&ctx).unwrap();
    assert!(!findings.is_empty(), "Fire-and-forget .then() without .catch() should be flagged");
}

#[test]
fn test_still_flags_then_without_catch() {
    let store = GraphBuilder::new().freeze();
    let detector = UnhandledPromiseDetector::new("/mock/repo");
    let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
        &store,
        vec![(
            "single_arg.js",
            "async function load() {\n  fetch('/data').then(res => process(res));\n}\n",
        )],
    );
    let findings = detector.detect(&ctx).unwrap();
    assert!(!findings.is_empty(), ".then() with one arg and no .catch() should be flagged");
}

#[test]
fn test_still_flags_arrow_fire_and_forget() {
    let store = GraphBuilder::new().freeze();
    let detector = UnhandledPromiseDetector::new("/mock/repo");
    let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
        &store,
        vec![(
            "loop.js",
            "async function processAll(items) {\n  items.forEach(item => fetch(`/api/${item}`).then(r => r.json()));\n}\n",
        )],
    );
    let findings = detector.detect(&ctx).unwrap();
    assert!(!findings.is_empty(), "Arrow fire-and-forget should still be flagged");
}
```

- [ ] **Step 6: Verify**

Run: `cargo test unhandled_promise -- --nocapture`

- [ ] **Step 7: Run full CI checks**

```bash
cargo test
cargo clippy --all-features -- -D warnings
cargo fmt --all -- --check
```

- [ ] **Step 8: Commit**

```bash
git add src/detectors/bugs/unhandled_promise.rs
git commit -m "fix(detectors): add return/void/two-arg-then exemptions to UnhandledPromiseDetector

Returned promises delegate error handling to callers. Void-prefixed
promises are explicitly intentional. Two-arg .then(success, error)
handles rejections. Aligns with typescript-eslint no-floating-promises."
```
