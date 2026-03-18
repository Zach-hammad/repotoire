# Detector Language Audit & GBDT Bypass Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all detector language-support gaps, add per-detector GBDT bypass, and ensure every detector's declared language support matches its actual implementation.

**Architecture:** Add `bypass_postprocessor()` trait method to `Detector`, propagate via `HashSet<String>` bypass set (not on `Finding` struct), fix extension loops / content access / content flags per audit. Extract shared `has_nearby_user_input()` helper for cross-line context.

**Tech Stack:** Rust, petgraph, tree-sitter, rayon

---

## Audit Results Summary

From the full 107-detector audit, the following detectors have mismatches requiring fixes:

### Extension-Loop Mismatches
| Detector | `file_extensions()` has | `detect()` scan loop missing |
|----------|------------------------|------------------------------|
| AIBoilerplateDetector | c, cpp, cs | c, cpp, cs |
| BooleanTrapDetector | py,js,ts,jsx,tsx,java,go,rs | scan has rb,cs instead of jsx,tsx — scan should match extensions |
| BroadExceptionDetector | py,js,ts,jsx,tsx,java,go,rs | scan has cs,rb instead of jsx,tsx — scan should match extensions |

### Content Access Issues
| Detector | Issue |
|----------|-------|
| CorsMisconfigDetector | Uses `masked_content` for CORS `*` pattern — masking removes the `*` |

### GBDT Bypass Candidates (22 detectors — excluding those done in Tasks 3-4)
CommandInjectionDetector, SQLInjectionDetector, XssDetector, SsrfDetector, XxeDetector, PathTraversalDetector, LogInjectionDetector, NosqlInjectionDetector, PickleDeserializationDetector, InsecureTlsDetector, InsecureRandomDetector, InsecureCookieDetector, CleartextCredentialsDetector, SecretDetector, EvalDetector, JwtWeakDetector, GHActionsInjectionDetector, UnsafeTemplateDetector, ExpressSecurityDetector, DjangoSecurityDetector, ReactHooksDetector, RegexDosDetector

(InsecureCryptoDetector, InsecureDeserializeDetector, and CorsMisconfigDetector get bypass in Tasks 3-4)

### Logic Fixes Needed
| Detector | Issue |
|----------|-------|
| InsecureDeserializeDetector | `HAS_SERIALIZE` missing Java `ObjectInputStream`; same-line user-input check |
| CommandInjectionDetector | Go and Java exec same-line user-input check too narrow |

---

### Task 1: Add `bypass_postprocessor()` Trait Method + Propagation

**Files:**
- Modify: `repotoire-cli/src/detectors/base.rs` — add trait method
- Modify: `repotoire-cli/src/detectors/runner.rs` — build bypass set, change return type
- Modify: `repotoire-cli/src/cli/analyze/postprocess.rs` — accept bypass set param
- Modify: `repotoire-cli/src/engine/stages/postprocess.rs` — thread bypass set through pipeline

- [ ] **Step 1: Add trait method to Detector trait in base.rs**

In `repotoire-cli/src/detectors/base.rs`, add to the `Detector` trait (after `is_deterministic()`):

```rust
/// Whether this detector's findings should bypass GBDT postprocessor filtering.
/// High-precision pattern-based detectors should return true.
fn bypass_postprocessor(&self) -> bool {
    false
}
```

- [ ] **Step 2: Read runner.rs to understand run_detectors() signature**

Read `repotoire-cli/src/detectors/runner.rs` to find:
- The `run_detectors()` function signature and return type
- How it's called from `engine/stages/`
- How findings flow to the postprocessor

- [ ] **Step 3: Build bypass set in runner.rs and return alongside findings**

After all detectors run in `run_detectors()`, build the bypass set:

```rust
use std::collections::HashSet;

let bypass_set: HashSet<String> = detectors
    .iter()
    .filter(|d| d.bypass_postprocessor())
    .map(|d| d.name().to_string())
    .collect();
```

Change the return type to include the bypass set. If `run_detectors()` currently returns `Vec<Finding>`, change to `(Vec<Finding>, HashSet<String>)`. Update all callers.

- [ ] **Step 4: Thread bypass set through engine pipeline to postprocessor**

In `repotoire-cli/src/engine/stages/postprocess.rs`, update the postprocess stage function signature to accept `bypass_set: &HashSet<String>` and forward it to `filter_false_positives()`.

Follow the call chain from `run_detectors()` → engine stage → `filter_false_positives()` and add the parameter at each level.

- [ ] **Step 5: Check bypass set in GBDT filter**

In `repotoire-cli/src/cli/analyze/postprocess.rs`, update `filter_false_positives()`:
- Add `bypass_set: &HashSet<String>` parameter
- Around the deterministic check (line ~491), update to:

```rust
if f.deterministic || bypass_set.contains(&f.detector) {
    return true; // skip GBDT filtering
}
```

- [ ] **Step 6: Verify compilation**

Run: `cd repotoire-cli && cargo check`
Expected: Compiles with pre-existing warnings only

- [ ] **Step 7: Run tests**

Run: `cd repotoire-cli && cargo test runner && cargo test postprocess`
Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add repotoire-cli/src/detectors/base.rs repotoire-cli/src/detectors/runner.rs repotoire-cli/src/cli/analyze/postprocess.rs repotoire-cli/src/engine/stages/postprocess.rs
git commit -m "feat: add bypass_postprocessor() trait method for per-detector GBDT bypass"
```

---

### Task 2: Extract Cross-Line User Input Helper

**Files:**
- Create: `repotoire-cli/src/detectors/user_input.rs`
- Modify: `repotoire-cli/src/detectors/mod.rs` — add module

- [ ] **Step 1: Create the helper module with tests**

Create `repotoire-cli/src/detectors/user_input.rs`:

```rust
/// Check if any line within ±window of `line_num` contains user-input indicators.
///
/// Uses specific patterns to avoid false positives from generic words like "data":
/// - HTTP request accessors: `req.`, `request.`, `.body`, `.query`, `.params`
/// - Framework-specific: `getParameter`, `FormValue`, `getInputStream`, `PostForm`
/// - Variable naming: `user_input`, `userInput`, `payload`
pub fn has_nearby_user_input(lines: &[&str], line_num: usize, window: usize) -> bool {
    let start = line_num.saturating_sub(window);
    let end = (line_num + window + 1).min(lines.len());
    lines[start..end].iter().any(|l| {
        // HTTP request object accessors
        l.contains("req.") || l.contains("request.") || l.contains("r.URL")
        // Body/query/params accessors
        || l.contains(".body") || l.contains(".query") || l.contains(".params")
        // Framework-specific input methods
        || l.contains("getParameter") || l.contains("FormValue")
        || l.contains("getInputStream") || l.contains("PostForm")
        || l.contains("getHeader") || l.contains("r.Form")
        // Common user-input variable names
        || l.contains("user_input") || l.contains("userInput")
        || l.contains("user_data") || l.contains("userData")
        || l.contains("payload")
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_finds_request_on_nearby_line() {
        let lines = vec![
            "func handle(w http.ResponseWriter, r *http.Request) {",
            "    cmd := r.FormValue(\"command\")",
            "    // some processing",
            "    exec.Command(cmd).Run()",
        ];
        assert!(has_nearby_user_input(&lines, 3, 5));
    }

    #[test]
    fn test_no_match_without_user_input() {
        let lines = vec![
            "func processFile() {",
            "    data := readConfig()",
            "    exec.Command(\"ls\").Run()",
        ];
        assert!(!has_nearby_user_input(&lines, 2, 5));
    }

    #[test]
    fn test_window_boundary() {
        let lines = vec![
            "req := request.getParameter(\"id\")",
            "", "", "", "", "", "", "", "", "", "",
            "exec.Command(cmd).Run()",
        ];
        // Window of 5 shouldn't reach line 0 from line 11
        assert!(!has_nearby_user_input(&lines, 11, 5));
        // Window of 15 should
        assert!(has_nearby_user_input(&lines, 11, 15));
    }

    #[test]
    fn test_does_not_match_generic_words() {
        let lines = vec![
            "let metadata = process_formatted_data(records);",
            "exec.Command(\"ls\").Run()",
        ];
        // "metadata" contains "data" but we check "user_data" not "data"
        assert!(!has_nearby_user_input(&lines, 1, 5));
    }
}
```

- [ ] **Step 2: Register module in mod.rs**

Add `pub mod user_input;` to `repotoire-cli/src/detectors/mod.rs`.

- [ ] **Step 3: Verify compilation and run tests**

Run: `cd repotoire-cli && cargo check && cargo test user_input`
Expected: All 4 tests pass

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/user_input.rs repotoire-cli/src/detectors/mod.rs
git commit -m "feat: extract shared cross-line user-input helper with tests"
```

---

### Task 3: Fix InsecureDeserializeDetector

**Files:**
- Modify: `repotoire-cli/src/detectors/insecure_deserialize.rs`
- Modify: `repotoire-cli/src/detectors/detector_context.rs`

- [ ] **Step 1: Add Java patterns to HAS_SERIALIZE content flag**

In `repotoire-cli/src/detectors/detector_context.rs` (around line 199-207), add Java deserialization keywords to the existing `HAS_SERIALIZE` check:

```rust
if content.contains("pickle")
    || content.contains("marshal")
    || content.contains("yaml.load")
    || content.contains("json.loads")
    || content.contains("deserialize")
    || content.contains("ObjectInputStream")
    || content.contains("readObject")
    || content.contains("XMLDecoder")
{
    flags.set(ContentFlags::HAS_SERIALIZE);
}
```

- [ ] **Step 2: Read insecure_deserialize.rs to understand current detection logic**

Read the full file to understand:
- The regex patterns used
- The file iteration loop
- The existing user-input check (lines 179-190)
- Existing unit tests

- [ ] **Step 3: Replace same-line user-input check with cross-line helper**

In `repotoire-cli/src/detectors/insecure_deserialize.rs`:

Add import:
```rust
use super::user_input::has_nearby_user_input;
```

Replace the same-line `has_user_input` check block (around lines 179-190) with:
```rust
if !has_nearby_user_input(&lines, i, 10) {
    continue;
}
```

- [ ] **Step 4: Add Java ObjectInputStream/XMLDecoder detection patterns**

Add a Java-specific regex pattern (alongside existing patterns) in the static declarations:

```rust
static JAVA_DESERIALIZE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?:ObjectInputStream|XMLDecoder|readObject|readUnshared)\s*\(").expect("valid regex")
});
```

In the detection loop, add a check for `.java` files against this pattern, creating findings with appropriate title/description/CWE (CWE-502).

- [ ] **Step 5: Add `bypass_postprocessor()` override**

In the `impl Detector for InsecureDeserializeDetector` block, add:

```rust
fn bypass_postprocessor(&self) -> bool {
    true
}
```

- [ ] **Step 6: Add Java unit test**

Add to the inline `#[test]` module:

```rust
#[test]
fn test_detects_java_object_input_stream() {
    let store = GraphStore::in_memory();
    let detector = InsecureDeserializeDetector::new("/mock/repo");
    let ctx = AnalysisContext::test_with_mock_files(&store, vec![
        ("Handler.java", "import java.io.*;\nimport javax.servlet.*;\n\npublic class Handler {\n    public void handle(HttpServletRequest request) {\n        InputStream in = request.getInputStream();\n        ObjectInputStream ois = new ObjectInputStream(in);\n        Object obj = ois.readObject();\n    }\n}\n"),
    ]);
    let findings = detector.detect(&ctx).expect("detection should succeed");
    assert!(!findings.is_empty(), "Should detect ObjectInputStream deserialization");
}
```

- [ ] **Step 7: Verify**

Run: `cd repotoire-cli && cargo test insecure_deserialize`
Expected: All tests pass including new Java test

- [ ] **Step 8: Commit**

```bash
git add repotoire-cli/src/detectors/insecure_deserialize.rs repotoire-cli/src/detectors/detector_context.rs
git commit -m "fix: overhaul InsecureDeserializeDetector — Java support, cross-line context, GBDT bypass"
```

---

### Task 4: Fix CorsMisconfigDetector Content Access

**Files:**
- Modify: `repotoire-cli/src/detectors/cors_misconfig.rs`

- [ ] **Step 1: Read the current implementation**

Read `repotoire-cli/src/detectors/cors_misconfig.rs` fully to understand the detection flow, noting:
- Where raw content is used (pre-filter, line ~117)
- Where masked content is used (pattern matching, line ~128)
- The CORS_PATTERN regex

- [ ] **Step 2: Fix CORS pattern matching to use raw content with masked validation**

Replace the masked-content matching section with a dual-access approach:

```rust
// Get both raw and masked content
let raw = match files.content(path) { Some(c) => c, None => continue };
let masked = match files.masked_content(path) { Some(c) => c, None => continue };
let raw_lines: Vec<&str> = raw.lines().collect();
let masked_lines: Vec<&str> = masked.lines().collect();

for (i, line) in raw_lines.iter().enumerate() {
    if CORS_PATTERN.is_match(line) {
        // Verify the line contains real code (not entirely a comment/string)
        let masked_line = masked_lines.get(i).map(|l| *l).unwrap_or("");
        if masked_line.trim().is_empty() {
            continue; // Entire line was comment/string — skip
        }
        // Create finding...
    }
}
```

Keep the existing finding creation logic — just change what content the regex runs against.

- [ ] **Step 3: Add `bypass_postprocessor()` override**

```rust
fn bypass_postprocessor(&self) -> bool {
    true
}
```

- [ ] **Step 4: Verify**

Run: `cd repotoire-cli && cargo test cors`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/cors_misconfig.rs
git commit -m "fix: CorsMisconfigDetector — match on raw content, validate with masked to handle '*'"
```

---

### Task 5: Opt Security Detectors into GBDT Bypass

**Files:**
- Modify: 22 detector files (excludes InsecureDeserialize, CorsMisconfig, InsecureCrypto which are done in Tasks 3-4 and prior commits)

- [ ] **Step 1: Add `bypass_postprocessor() -> true` to all security regex detectors**

For each of these 22 detectors, add the override in their `impl Detector for` block:

```rust
fn bypass_postprocessor(&self) -> bool {
    true
}
```

Read each file first to find the correct `impl Detector for` block. The detectors:

1. `command_injection.rs` — CommandInjectionDetector
2. `sql_injection/mod.rs` — SQLInjectionDetector
3. `xss.rs` — XssDetector
4. `ssrf.rs` — SsrfDetector
5. `xxe.rs` — XxeDetector
6. `path_traversal.rs` — PathTraversalDetector
7. `log_injection.rs` — LogInjectionDetector
8. `nosql_injection.rs` — NosqlInjectionDetector
9. `pickle_detector.rs` — PickleDeserializationDetector
10. `insecure_tls.rs` — InsecureTlsDetector
11. `insecure_random.rs` — InsecureRandomDetector
12. `insecure_cookie.rs` — InsecureCookieDetector
13. `cleartext_credentials.rs` — CleartextCredentialsDetector
14. `secrets.rs` — SecretDetector
15. `eval_detector.rs` — EvalDetector
16. `jwt_weak.rs` — JwtWeakDetector
17. `gh_actions.rs` — GHActionsInjectionDetector
18. `unsafe_template.rs` — UnsafeTemplateDetector
19. `express_security.rs` — ExpressSecurityDetector
20. `django_security.rs` — DjangoSecurityDetector
21. `react_hooks.rs` — ReactHooksDetector
22. `regex_dos.rs` — RegexDosDetector

- [ ] **Step 2: Verify compilation**

Run: `cd repotoire-cli && cargo check`

- [ ] **Step 3: Run full test suite**

Run: `cd repotoire-cli && cargo test`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/*.rs repotoire-cli/src/detectors/sql_injection/mod.rs
git commit -m "feat: opt 22 security detectors into GBDT bypass"
```

---

### Task 6: Fix Extension-Loop Mismatches

**Files:**
- Modify: `repotoire-cli/src/detectors/ai_boilerplate.rs`
- Modify: `repotoire-cli/src/detectors/boolean_trap.rs`
- Modify: `repotoire-cli/src/detectors/broad_exception.rs`

- [ ] **Step 1: Fix AIBoilerplateDetector**

Read `ai_boilerplate.rs`. Find the `files_with_extensions()` call in `detect()`. Add `"c", "cpp", "cs"` to match `file_extensions()`.

- [ ] **Step 2: Fix BooleanTrapDetector**

Read `boolean_trap.rs`. The scan loop has `["py", "js", "ts", "java", "go", "rb", "cs"]` but `file_extensions()` returns `["py", "js", "ts", "jsx", "tsx", "java", "go", "rs"]`.

Fix: Change the scan loop to `["py", "js", "ts", "jsx", "tsx", "java", "go", "rs"]` to match `file_extensions()`. Remove `"rb"` and `"cs"` (not in extensions), add `"jsx"`, `"tsx"`, `"rs"` (in extensions but missing from scan).

- [ ] **Step 3: Fix BroadExceptionDetector**

Read `broad_exception.rs`. The scan loop has `["py", "js", "ts", "java", "cs", "rb", "go"]` but `file_extensions()` returns `["py", "js", "ts", "jsx", "tsx", "java", "go", "rs"]`.

Fix: Change the scan loop to `["py", "js", "ts", "jsx", "tsx", "java", "go", "rs"]` to match `file_extensions()`. Remove `"cs"` and `"rb"`, add `"jsx"`, `"tsx"`, `"rs"`.

- [ ] **Step 4: Verify**

Run: `cd repotoire-cli && cargo check && cargo test ai_boilerplate && cargo test boolean_trap && cargo test broad_exception`

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/ai_boilerplate.rs repotoire-cli/src/detectors/boolean_trap.rs repotoire-cli/src/detectors/broad_exception.rs
git commit -m "fix: align extension loops with file_extensions() for 3 detectors"
```

---

### Task 7: Fix CommandInjectionDetector Cross-Line Context

**Files:**
- Modify: `repotoire-cli/src/detectors/command_injection.rs`

- [ ] **Step 1: Read the current Go and Java detection blocks**

Read `repotoire-cli/src/detectors/command_injection.rs` around lines 400-480 (Go exec.Command block and the new Java Runtime.exec block added in prior commit).

- [ ] **Step 2: Replace same-line user-input checks with cross-line helper**

Add import:
```rust
use super::user_input::has_nearby_user_input;
```

In the **Go exec.Command block** (around line 401-414), replace the inline `has_user_input` boolean with:
```rust
let has_user_input = has_nearby_user_input(&lines, i, 10);
```

In the **Java Runtime.exec block** (added in prior commit), do the same replacement for the inline `has_user_input` check.

- [ ] **Step 3: Verify**

Run: `cd repotoire-cli && cargo test command_injection`
Expected: All tests pass (including the Go exec.Command test that uses `r.FormValue` 1 line above `exec.Command`)

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/command_injection.rs
git commit -m "fix: CommandInjectionDetector — use cross-line context for Go and Java exec"
```

---

### Task 8: Update Integration Tests

**Files:**
- Modify: `repotoire-cli/tests/lang_java.rs` — InsecureDeserialize, InsecureCrypto, CommandInjection may now fire
- Modify: `repotoire-cli/tests/lang_go.rs` — CommandInjection, DebugCode may now fire
- Modify: `repotoire-cli/tests/lang_javascript.rs` — CorsMisconfig, InsecureRandom may now fire
- Modify: `repotoire-cli/tests/lang_typescript.rs` — various detectors may now fire via GBDT bypass

- [ ] **Step 1: Run all language integration tests to identify failures**

Run: `cd repotoire-cli && cargo test --test lang_typescript --test lang_javascript --test lang_java --test lang_python --test lang_rust --test lang_c --test lang_cpp --test lang_go --test lang_csharp 2>&1 | grep -E "(FAILED|test result)"`

- [ ] **Step 2: For each failure, check whether the detector now correctly fires**

For each failing test, run the analysis manually on the fixture:
```bash
cargo run -- analyze tests/fixtures/<fixture> --format json 2>/dev/null | python3 -c "import sys,json; [print(f['detector']) for f in json.load(sys.stdin)['findings']]" | sort -u
```

If the detector now fires, flip the negative assertion to positive. If it still doesn't fire, the test is correct — investigate.

- [ ] **Step 3: Verify all tests pass**

Run: `cd repotoire-cli && cargo test --test lang_typescript --test lang_javascript --test lang_java --test lang_python --test lang_rust --test lang_c --test lang_cpp --test lang_go --test lang_csharp`
Expected: All 98+ tests pass

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/tests/lang_*.rs
git commit -m "test: update integration tests for fixed detector language support"
```

---

### Task 9: Validation — Self-Analysis + Documentation

**Files:**
- Modify: `docs/QA_FINDINGS.md`

- [ ] **Step 1: Run dogfooding test**

Run: `cd repotoire-cli && cargo test --test dogfood -- --ignored`
Expected: Passes (score may change — acceptable within ±5 points of 89.7)

- [ ] **Step 2: Run full test suite**

Run: `cd repotoire-cli && cargo test`
Expected: All tests pass

- [ ] **Step 3: Run clippy**

Run: `cd repotoire-cli && cargo clippy -- -D warnings 2>&1 | grep -c "error"`
Note the count — should not increase from baseline.

- [ ] **Step 4: Capture before/after self-analysis stats**

Run analysis and capture finding count and detector list:
```bash
cargo run -- analyze . --format json 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Score: {d[\"score\"]}, Findings: {len(d[\"findings\"])}')
detectors = set(f['detector'] for f in d['findings'])
print(f'Unique detectors: {len(detectors)}')
for det in sorted(detectors):
    count = sum(1 for f in d['findings'] if f['detector'] == det)
    print(f'  {det}: {count}')
"
```

Compare with pre-fix baseline (score 89.7, 849 findings, 37 detectors).

- [ ] **Step 5: Update QA_FINDINGS.md**

Append a "Detector Audit Results" section to `docs/QA_FINDINGS.md` documenting:
- Which detectors were fixed and how
- GBDT bypass impact (new findings that weren't showing before)
- Before/after self-analysis stats
- Remaining gaps (PrototypePollution TS — scoped out for separate investigation)

- [ ] **Step 6: Commit**

```bash
git add docs/QA_FINDINGS.md
git commit -m "docs: update QA findings with detector audit results"
```

---

## Task Dependencies

```
Task 1 (trait method) ──┐
Task 2 (helper)     ────┤
                        ├──→ Task 3 (InsecureDeserialize) ──┐
                        ├──→ Task 4 (CorsMisconfig)     ────┤
                        ├──→ Task 5 (GBDT bypass x22)   ────┤
                        ├──→ Task 6 (extension loops)   ────┤
                        ├──→ Task 7 (CommandInjection)  ────┤
                        │                                   │
                        └───────────────────────────────────┴──→ Task 8 (tests) ──→ Task 9 (validation)
```

Tasks 1-2 must complete first (infrastructure). Tasks 3-7 can run in parallel. Task 8 depends on all fixes. Task 9 is last.
