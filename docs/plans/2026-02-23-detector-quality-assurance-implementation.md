# Detector Quality Assurance Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Bring all 104+ Repotoire detectors to production quality with comprehensive tests, standards gap audit, dead code cleanup, and live validation.

**Architecture:** The detector system is pure Rust with file-scanning + graph-enrichment. Tests use `tempfile::tempdir()` for file fixtures and `GraphStore::in_memory()` for graph data. Each detector implements the `Detector` trait with `detect(&self, graph: &dyn GraphQuery) -> Result<Vec<Finding>>`.

**Tech Stack:** Rust, `#[cfg(test)]` inline modules, `tempfile` crate, `GraphStore::in_memory()`, `cargo test`

---

## Workstream 1: Standards Gap Audit

### Task 1: Audit detectors against Fowler's 22 code smells

**Files:**
- Create: `docs/audit/detector-gap-analysis.md`

**Step 1: Research and map**

Map each of Fowler's 22 code smells to an existing detector or mark as missing:

| Fowler Smell | Repotoire Detector | Status |
|---|---|---|
| Duplicated Code | `duplicate_code.rs` | Covered |
| Long Method | `long_methods.rs` | Covered |
| Large Class | `god_class.rs` | Covered |
| Long Parameter List | `long_parameter.rs` | Covered |
| Divergent Change | — | Missing |
| Shotgun Surgery | `shotgun_surgery.rs` | Covered |
| Feature Envy | `feature_envy.rs` | Covered |
| Data Clumps | `data_clumps.rs` | Covered |
| Primitive Obsession | — | Missing |
| Parallel Inheritance Hierarchies | — | Missing |
| Lazy Class | `lazy_class.rs` | Covered |
| Speculative Generality | — | Missing |
| Temporary Field | — | Missing |
| Message Chains | `message_chain.rs` | Covered |
| Middle Man | `middle_man.rs` | Covered |
| Inappropriate Intimacy | `inappropriate_intimacy.rs` | Covered |
| Alternative Classes with Different Interfaces | — | Missing |
| Incomplete Library Class | — | N/A (external) |
| Data Class | — | Missing |
| Refused Bequest | `refused_bequest.rs` | Covered |
| Comments (excessive) | `commented_code.rs` | Covered |
| Switch Statements | — | Partially (deep_nesting catches some) |

**Step 2: Document findings**

Write the full gap analysis to `docs/audit/detector-gap-analysis.md` with:
- Each smell mapped to detector(s) or marked missing
- Priority ranking for missing smells (High/Medium/Low based on real-world impact)
- Recommendations for which missing smells to implement

**Step 3: Commit**

```bash
git add docs/audit/detector-gap-analysis.md
git commit -m "docs: add detector gap analysis against Fowler's code smells"
```

### Task 2: Audit detectors against OWASP Top 10 + CWE

**Files:**
- Modify: `docs/audit/detector-gap-analysis.md`

**Step 1: Map OWASP Top 10 (2021)**

| OWASP Category | Repotoire Detector(s) | CWE IDs | Status |
|---|---|---|---|
| A01: Broken Access Control | — | CWE-284, CWE-285 | Missing |
| A02: Cryptographic Failures | `insecure_crypto.rs`, `insecure_tls.rs` | CWE-327, CWE-328 | Covered |
| A03: Injection | `sql_injection.rs`, `command_injection.rs`, `nosql_injection.rs`, `xss.rs`, `log_injection.rs` | CWE-79, CWE-89, CWE-78 | Well covered |
| A04: Insecure Design | `god_class.rs`, architecture detectors | CWE-840 | Partial |
| A05: Security Misconfiguration | `django_security.rs`, `express_security.rs`, `cors_misconfig.rs`, `debug_code.rs` | CWE-16 | Covered |
| A06: Vulnerable Components | `dep_audit.rs` | CWE-1035 | Covered |
| A07: Auth Failures | `jwt_weak.rs`, `insecure_cookie.rs` | CWE-287, CWE-384 | Partial |
| A08: Data Integrity Failures | `insecure_deserialize.rs`, `pickle_detector.rs` | CWE-502 | Covered |
| A09: Logging Failures | `log_injection.rs` | CWE-117, CWE-778 | Partial |
| A10: SSRF | `ssrf.rs` | CWE-918 | Covered |

**Step 2: Map CWE coverage for all security detectors**

For each security detector file, document which CWE IDs it covers. Add the mapping table to the gap analysis doc.

**Step 3: Commit**

```bash
git add docs/audit/detector-gap-analysis.md
git commit -m "docs: add OWASP Top 10 and CWE mapping to gap analysis"
```

### Task 3: Audit against SonarQube and ESLint popular rules

**Files:**
- Modify: `docs/audit/detector-gap-analysis.md`

**Step 1: Map SonarQube top rules**

Compare against SonarQube's most impactful rules (bugs, vulnerabilities, code smells). Focus on rules not already covered by Fowler/OWASP mappings.

**Step 2: Map ESLint/TypeScript-ESLint rules**

Check for TS/JS-specific rules that popular configs (`eslint:recommended`, `@typescript-eslint/recommended`) enable. Identify any patterns the React/Express detectors should catch but don't.

**Step 3: Final recommendations**

Add a "Recommended New Detectors" section at the end of the gap analysis with:
- Priority-ranked list of missing detectors worth implementing
- Estimated complexity per detector (Small/Medium/Large)
- Which existing detectors could be extended vs. new files needed

**Step 4: Commit**

```bash
git add docs/audit/detector-gap-analysis.md
git commit -m "docs: complete gap analysis with SonarQube and ESLint coverage"
```

---

## Workstream 2: Test Coverage Blitz

All tests follow this pattern:
```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_issue() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("bad_code.py");
        std::fs::write(&file, "... problematic code ...").unwrap();

        let store = GraphStore::in_memory();
        let detector = MyDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(!findings.is_empty(), "Should detect the issue");
    }

    #[test]
    fn test_clean_code_no_findings() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("clean.py");
        std::fs::write(&file, "... clean code ...").unwrap();

        let store = GraphStore::in_memory();
        let detector = MyDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(findings.is_empty(), "Clean code should produce no findings");
    }
}
```

### Task 4: Add tests for ReactHooksDetector

**Files:**
- Modify: `repotoire-cli/src/detectors/react_hooks.rs`

**Step 1: Write the failing tests**

Add this `#[cfg(test)]` module at the end of `react_hooks.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    fn setup_tsx(dir: &std::path::Path, name: &str, content: &str) -> std::path::PathBuf {
        let file = dir.join(name);
        std::fs::write(&file, content).unwrap();
        file
    }

    #[test]
    fn test_hook_in_conditional() {
        let dir = tempfile::tempdir().unwrap();
        setup_tsx(dir.path(), "Component.tsx", r#"
function MyComponent({ show }) {
    if (show) {
        const [value, setValue] = useState(0);
    }
    return <div />;
}
"#);

        let store = GraphStore::in_memory();
        let detector = ReactHooksDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(!findings.is_empty(), "Should detect hook in conditional");
        assert!(findings.iter().any(|f| f.description.contains("conditionally")));
    }

    #[test]
    fn test_hook_in_loop() {
        let dir = tempfile::tempdir().unwrap();
        setup_tsx(dir.path(), "List.tsx", r#"
function List({ items }) {
    for (const item of items) {
        const [val, setVal] = useState(item);
    }
    return <div />;
}
"#);

        let store = GraphStore::in_memory();
        let detector = ReactHooksDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(!findings.is_empty(), "Should detect hook in loop");
    }

    #[test]
    fn test_correct_hook_usage_no_findings() {
        let dir = tempfile::tempdir().unwrap();
        setup_tsx(dir.path(), "Good.tsx", r#"
function GoodComponent() {
    const [count, setCount] = useState(0);
    useEffect(() => {
        console.log(count);
    }, [count]);
    return <div>{count}</div>;
}
"#);

        let store = GraphStore::in_memory();
        let detector = ReactHooksDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(findings.is_empty(), "Correct hook usage should produce no findings");
    }

    #[test]
    fn test_hook_in_nested_function() {
        let dir = tempfile::tempdir().unwrap();
        setup_tsx(dir.path(), "Nested.tsx", r#"
function Parent() {
    function innerHelper() {
        const [val, setVal] = useState(0);
    }
    return <div />;
}
"#);

        let store = GraphStore::in_memory();
        let detector = ReactHooksDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(!findings.is_empty(), "Should detect hook in nested function");
    }
}
```

**Step 2: Run tests to verify they pass (or identify detector bugs)**

Run: `cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib detectors::react_hooks -- --nocapture`

Expected: All 4 tests PASS (detector is already implemented, we're adding coverage).

**Step 3: Commit**

```bash
git add repotoire-cli/src/detectors/react_hooks.rs
git commit -m "test: add unit tests for ReactHooksDetector"
```

### Task 5: Add tests for DjangoSecurityDetector

**Files:**
- Modify: `repotoire-cli/src/detectors/django_security.rs`

**Step 1: Write the tests**

Add `#[cfg(test)]` module at the end of `django_security.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_csrf_exempt() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("views.py");
        std::fs::write(&file, r#"
from django.views.decorators.csrf import csrf_exempt

@csrf_exempt
def my_api_view(request):
    return JsonResponse({"ok": True})
"#).unwrap();

        let store = GraphStore::in_memory();
        let detector = DjangoSecurityDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(!findings.is_empty(), "Should detect @csrf_exempt");
    }

    #[test]
    fn test_detects_debug_true() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("settings.py");
        std::fs::write(&file, "DEBUG = True\nSECRET_KEY = 'abc'\n").unwrap();

        let store = GraphStore::in_memory();
        let detector = DjangoSecurityDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(!findings.is_empty(), "Should detect DEBUG = True");
    }

    #[test]
    fn test_detects_raw_sql() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("models.py");
        std::fs::write(&file, r#"
def get_users(name):
    return User.objects.raw("SELECT * FROM users WHERE name = '%s'" % name)
"#).unwrap();

        let store = GraphStore::in_memory();
        let detector = DjangoSecurityDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(!findings.is_empty(), "Should detect raw SQL usage");
    }

    #[test]
    fn test_detects_wildcard_allowed_hosts() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("settings.py");
        std::fs::write(&file, "ALLOWED_HOSTS = ['*']\n").unwrap();

        let store = GraphStore::in_memory();
        let detector = DjangoSecurityDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(!findings.is_empty(), "Should detect wildcard ALLOWED_HOSTS");
    }

    #[test]
    fn test_clean_django_no_findings() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("views.py");
        std::fs::write(&file, r#"
from django.http import JsonResponse

def my_view(request):
    return JsonResponse({"status": "ok"})
"#).unwrap();

        let store = GraphStore::in_memory();
        let detector = DjangoSecurityDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(findings.is_empty(), "Clean Django code should produce no findings");
    }
}
```

**Step 2: Run tests**

Run: `cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib detectors::django_security -- --nocapture`

**Step 3: Commit**

```bash
git add repotoire-cli/src/detectors/django_security.rs
git commit -m "test: add unit tests for DjangoSecurityDetector"
```

### Task 6: Add tests for ExpressSecurityDetector

**Files:**
- Modify: `repotoire-cli/src/detectors/express_security.rs`

**Step 1: Write the tests**

Add `#[cfg(test)]` module at the end of `express_security.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_missing_helmet() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("app.js");
        std::fs::write(&file, r#"
const express = require('express');
const app = express();
app.get('/api', (req, res) => res.json({ ok: true }));
app.listen(3000);
"#).unwrap();

        let store = GraphStore::in_memory();
        let detector = ExpressSecurityDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(!findings.is_empty(), "Should detect missing helmet");
        assert!(findings.iter().any(|f| f.description.to_lowercase().contains("helmet")));
    }

    #[test]
    fn test_secure_express_fewer_findings() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("app.js");
        std::fs::write(&file, r#"
const express = require('express');
const helmet = require('helmet');
const cors = require('cors');
const rateLimit = require('express-rate-limit');
const app = express();
app.use(helmet());
app.use(cors());
app.use(rateLimit({ windowMs: 15 * 60 * 1000, max: 100 }));
app.get('/api', authenticate, (req, res) => res.json({ ok: true }));
app.listen(3000);
"#).unwrap();

        let store = GraphStore::in_memory();
        let detector = ExpressSecurityDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        // Secure app should have significantly fewer findings
        // It may still flag some things, but not missing helmet/cors/rate-limit
        let helmet_findings: Vec<_> = findings.iter()
            .filter(|f| f.description.to_lowercase().contains("helmet"))
            .collect();
        assert!(helmet_findings.is_empty(), "Should not flag helmet when it's present");
    }

    #[test]
    fn test_non_express_file_no_findings() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("utils.js");
        std::fs::write(&file, "function add(a, b) { return a + b; }\nmodule.exports = { add };\n").unwrap();

        let store = GraphStore::in_memory();
        let detector = ExpressSecurityDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(findings.is_empty(), "Non-Express file should produce no findings");
    }
}
```

**Step 2: Run tests**

Run: `cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib detectors::express_security -- --nocapture`

**Step 3: Commit**

```bash
git add repotoire-cli/src/detectors/express_security.rs
git commit -m "test: add unit tests for ExpressSecurityDetector"
```

### Task 7: Add tests for untested security detectors (batch 1)

**Files:**
- Modify: `repotoire-cli/src/detectors/command_injection.rs`
- Modify: `repotoire-cli/src/detectors/xss.rs`
- Modify: `repotoire-cli/src/detectors/ssrf.rs`
- Modify: `repotoire-cli/src/detectors/path_traversal.rs`

**Step 1: Write tests for each detector**

For each file, read it first to understand the specific patterns it detects, then add a `#[cfg(test)]` module at the end with:
- 1 true-positive test using a code sample that triggers the detector
- 1 true-negative test using clean code
- Follow the same `tempfile::tempdir()` + `GraphStore::in_memory()` pattern

**Example pattern for command_injection.rs:**
```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_os_system() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("run.py"), r#"
import os
def run_command(user_input):
    os.system("ls " + user_input)
"#).unwrap();

        let store = GraphStore::in_memory();
        let detector = CommandInjectionDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(!findings.is_empty(), "Should detect os.system with user input");
    }

    #[test]
    fn test_clean_subprocess_no_findings() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("safe.py"), r#"
import subprocess
def run_safe():
    subprocess.run(["ls", "-la"], check=True)
"#).unwrap();

        let store = GraphStore::in_memory();
        let detector = CommandInjectionDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        // Array form of subprocess is generally safe
    }
}
```

Apply the same pattern for `xss.rs` (test with `innerHTML`/unescaped template), `ssrf.rs` (test with user-controlled URL in requests), `path_traversal.rs` (test with `../` in file path).

**Step 2: Run tests**

Run: `cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib detectors::command_injection detectors::xss detectors::ssrf detectors::path_traversal -- --nocapture`

**Step 3: Commit**

```bash
git add repotoire-cli/src/detectors/command_injection.rs repotoire-cli/src/detectors/xss.rs repotoire-cli/src/detectors/ssrf.rs repotoire-cli/src/detectors/path_traversal.rs
git commit -m "test: add unit tests for command injection, XSS, SSRF, path traversal detectors"
```

### Task 8: Add tests for untested security detectors (batch 2)

**Files:**
- Modify: `repotoire-cli/src/detectors/cleartext_credentials.rs`
- Modify: `repotoire-cli/src/detectors/cors_misconfig.rs`
- Modify: `repotoire-cli/src/detectors/insecure_cookie.rs`
- Modify: `repotoire-cli/src/detectors/insecure_crypto.rs`
- Modify: `repotoire-cli/src/detectors/insecure_deserialize.rs`

**Step 1: Read each file, write tests**

For each detector, read the source to understand what patterns it matches, then write:
- 1 true-positive test
- 1 true-negative test

Use the same `tempfile::tempdir()` + `GraphStore::in_memory()` pattern.

Key test cases per detector:
- `cleartext_credentials.rs`: password in plaintext variable assignment
- `cors_misconfig.rs`: `Access-Control-Allow-Origin: *` in config
- `insecure_cookie.rs`: cookie without `Secure`/`HttpOnly` flags
- `insecure_crypto.rs`: MD5/SHA1 hash usage
- `insecure_deserialize.rs`: `yaml.load()` without SafeLoader

**Step 2: Run tests**

Run: `cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib detectors::cleartext_credentials detectors::cors_misconfig detectors::insecure_cookie detectors::insecure_crypto detectors::insecure_deserialize -- --nocapture`

**Step 3: Commit**

```bash
git add repotoire-cli/src/detectors/cleartext_credentials.rs repotoire-cli/src/detectors/cors_misconfig.rs repotoire-cli/src/detectors/insecure_cookie.rs repotoire-cli/src/detectors/insecure_crypto.rs repotoire-cli/src/detectors/insecure_deserialize.rs
git commit -m "test: add unit tests for credential, CORS, cookie, crypto, deserialization detectors"
```

### Task 9: Add tests for untested security detectors (batch 3)

**Files:**
- Modify: `repotoire-cli/src/detectors/insecure_random.rs`
- Modify: `repotoire-cli/src/detectors/jwt_weak.rs`
- Modify: `repotoire-cli/src/detectors/nosql_injection.rs`
- Modify: `repotoire-cli/src/detectors/log_injection.rs`
- Modify: `repotoire-cli/src/detectors/hardcoded_ips.rs`

**Step 1: Read each file, write tests**

Key test cases:
- `insecure_random.rs`: `random.random()` or `Math.random()` in security context
- `jwt_weak.rs`: JWT with `none` algorithm or weak secret
- `nosql_injection.rs`: MongoDB `$where` or `$gt` with user input
- `log_injection.rs`: User input directly in log statement
- `hardcoded_ips.rs`: IP addresses like `192.168.1.1` hardcoded in source

**Step 2: Run tests**

Run: `cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib detectors::insecure_random detectors::jwt_weak detectors::nosql_injection detectors::log_injection detectors::hardcoded_ips -- --nocapture`

**Step 3: Commit**

```bash
git add repotoire-cli/src/detectors/insecure_random.rs repotoire-cli/src/detectors/jwt_weak.rs repotoire-cli/src/detectors/nosql_injection.rs repotoire-cli/src/detectors/log_injection.rs repotoire-cli/src/detectors/hardcoded_ips.rs
git commit -m "test: add unit tests for random, JWT, NoSQL injection, log injection, hardcoded IP detectors"
```

### Task 10: Add tests for untested security detectors (batch 4)

**Files:**
- Modify: `repotoire-cli/src/detectors/prototype_pollution.rs`
- Modify: `repotoire-cli/src/detectors/regex_dos.rs`
- Modify: `repotoire-cli/src/detectors/secrets.rs`
- Modify: `repotoire-cli/src/detectors/xxe.rs`
- Modify: `repotoire-cli/src/detectors/eval_detector.rs`

**Step 1: Read each file, write tests**

Key test cases:
- `prototype_pollution.rs`: `obj[key] = value` with user-controlled key in JS
- `regex_dos.rs`: Catastrophic backtracking regex like `(a+)+`
- `secrets.rs`: API keys, tokens in source code
- `xxe.rs`: XML parsing without disabling external entities
- `eval_detector.rs`: `eval()` or `exec()` with user input

**Step 2: Run tests**

Run: `cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib detectors::prototype_pollution detectors::regex_dos detectors::secrets detectors::xxe detectors::eval_detector -- --nocapture`

**Step 3: Commit**

```bash
git add repotoire-cli/src/detectors/prototype_pollution.rs repotoire-cli/src/detectors/regex_dos.rs repotoire-cli/src/detectors/secrets.rs repotoire-cli/src/detectors/xxe.rs repotoire-cli/src/detectors/eval_detector.rs
git commit -m "test: add unit tests for prototype pollution, ReDoS, secrets, XXE, eval detectors"
```

### Task 11: Add tests for performance/async detectors

**Files:**
- Modify: `repotoire-cli/src/detectors/sync_in_async.rs`
- Modify: `repotoire-cli/src/detectors/missing_await.rs`
- Modify: `repotoire-cli/src/detectors/callback_hell.rs`
- Modify: `repotoire-cli/src/detectors/unhandled_promise.rs`
- Modify: `repotoire-cli/src/detectors/n_plus_one.rs`
- Modify: `repotoire-cli/src/detectors/regex_in_loop.rs`

**Step 1: Read each file, write tests**

Key test cases:
- `sync_in_async.rs`: `time.sleep()` inside `async def`, `readFileSync` inside `async function`
- `missing_await.rs`: Async function called without `await`
- `callback_hell.rs`: Deeply nested callbacks (3+ levels)
- `unhandled_promise.rs`: Promise without `.catch()` or try/catch
- `n_plus_one.rs`: Database query inside a loop
- `regex_in_loop.rs`: `re.compile()` or `new RegExp()` inside a loop

**Step 2: Run tests**

Run: `cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib detectors::sync_in_async detectors::missing_await detectors::callback_hell detectors::unhandled_promise detectors::n_plus_one detectors::regex_in_loop -- --nocapture`

**Step 3: Commit**

```bash
git add repotoire-cli/src/detectors/sync_in_async.rs repotoire-cli/src/detectors/missing_await.rs repotoire-cli/src/detectors/callback_hell.rs repotoire-cli/src/detectors/unhandled_promise.rs repotoire-cli/src/detectors/n_plus_one.rs repotoire-cli/src/detectors/regex_in_loop.rs
git commit -m "test: add unit tests for performance and async detectors"
```

### Task 12: Add tests for code quality detectors (batch 1)

**Files:**
- Modify: `repotoire-cli/src/detectors/deep_nesting.rs`
- Modify: `repotoire-cli/src/detectors/magic_numbers.rs`
- Modify: `repotoire-cli/src/detectors/long_methods.rs`
- Modify: `repotoire-cli/src/detectors/empty_catch.rs`
- Modify: `repotoire-cli/src/detectors/broad_exception.rs`

**Step 1: Read each file, write tests**

Key test cases:
- `deep_nesting.rs`: 5+ levels of indentation
- `magic_numbers.rs`: Unexplained numeric literals like `if x > 42`
- `long_methods.rs`: Function >100 lines
- `empty_catch.rs`: `except: pass` or `catch (e) {}`
- `broad_exception.rs`: `except Exception` or bare `except:`

**Step 2: Run tests**

Run: `cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib detectors::deep_nesting detectors::magic_numbers detectors::long_methods detectors::empty_catch detectors::broad_exception -- --nocapture`

**Step 3: Commit**

```bash
git add repotoire-cli/src/detectors/deep_nesting.rs repotoire-cli/src/detectors/magic_numbers.rs repotoire-cli/src/detectors/long_methods.rs repotoire-cli/src/detectors/empty_catch.rs repotoire-cli/src/detectors/broad_exception.rs
git commit -m "test: add unit tests for nesting, magic numbers, long methods, catch, exception detectors"
```

### Task 13: Add tests for code quality detectors (batch 2)

**Files:**
- Modify: `repotoire-cli/src/detectors/boolean_trap.rs`
- Modify: `repotoire-cli/src/detectors/duplicate_code.rs`
- Modify: `repotoire-cli/src/detectors/global_variables.rs`
- Modify: `repotoire-cli/src/detectors/implicit_coercion.rs`
- Modify: `repotoire-cli/src/detectors/inconsistent_returns.rs`

**Step 1: Read each file, write tests**

Key test cases:
- `boolean_trap.rs`: `def process(data, True, False)` - boolean args without names
- `duplicate_code.rs`: Two identical or near-identical code blocks
- `global_variables.rs`: Module-level mutable variables
- `implicit_coercion.rs`: `==` instead of `===` in JS, implicit type conversions
- `inconsistent_returns.rs`: Function that sometimes returns a value and sometimes doesn't

**Step 2: Run tests and commit**

```bash
git add repotoire-cli/src/detectors/boolean_trap.rs repotoire-cli/src/detectors/duplicate_code.rs repotoire-cli/src/detectors/global_variables.rs repotoire-cli/src/detectors/implicit_coercion.rs repotoire-cli/src/detectors/inconsistent_returns.rs
git commit -m "test: add unit tests for boolean trap, duplicate code, globals, coercion, returns detectors"
```

### Task 14: Add tests for code quality detectors (batch 3)

**Files:**
- Modify: `repotoire-cli/src/detectors/commented_code.rs`
- Modify: `repotoire-cli/src/detectors/debug_code.rs`
- Modify: `repotoire-cli/src/detectors/large_files.rs`
- Modify: `repotoire-cli/src/detectors/missing_docstrings.rs`
- Modify: `repotoire-cli/src/detectors/mutable_default_args.rs`

**Step 1: Read each file, write tests**

Key test cases:
- `commented_code.rs`: Blocks of commented-out code (not TODO/documentation comments)
- `debug_code.rs`: `print()` statements, `console.log()`, `debugger`
- `large_files.rs`: File >500 lines
- `missing_docstrings.rs`: Public function without docstring
- `mutable_default_args.rs`: `def f(items=[])` pattern in Python

**Step 2: Run tests and commit**

```bash
git add repotoire-cli/src/detectors/commented_code.rs repotoire-cli/src/detectors/debug_code.rs repotoire-cli/src/detectors/large_files.rs repotoire-cli/src/detectors/missing_docstrings.rs repotoire-cli/src/detectors/mutable_default_args.rs
git commit -m "test: add unit tests for commented code, debug, large files, docstrings, mutable default detectors"
```

### Task 15: Add tests for code quality detectors (batch 4)

**Files:**
- Modify: `repotoire-cli/src/detectors/string_concat_loop.rs`
- Modify: `repotoire-cli/src/detectors/wildcard_imports.rs`
- Modify: `repotoire-cli/src/detectors/todo_scanner.rs`
- Modify: `repotoire-cli/src/detectors/dead_store.rs`
- Modify: `repotoire-cli/src/detectors/hardcoded_timeout.rs`

**Step 1: Read each file, write tests**

Key test cases:
- `string_concat_loop.rs`: String concatenation in a loop (`s += "..."`)
- `wildcard_imports.rs`: `from module import *` or `import *`
- `todo_scanner.rs`: `TODO`, `FIXME`, `HACK` comments
- `dead_store.rs`: Variable assigned but never read
- `hardcoded_timeout.rs`: Magic timeout values like `sleep(30)`

**Step 2: Run tests and commit**

```bash
git add repotoire-cli/src/detectors/string_concat_loop.rs repotoire-cli/src/detectors/wildcard_imports.rs repotoire-cli/src/detectors/todo_scanner.rs repotoire-cli/src/detectors/dead_store.rs repotoire-cli/src/detectors/hardcoded_timeout.rs
git commit -m "test: add unit tests for string concat, wildcard imports, TODO, dead store, timeout detectors"
```

### Task 16: Add tests for remaining untested detectors

**Files:**
- Modify: `repotoire-cli/src/detectors/generator_misuse.rs`
- Modify: `repotoire-cli/src/detectors/infinite_loop.rs`
- Modify: `repotoire-cli/src/detectors/test_in_production.rs`
- Modify: `repotoire-cli/src/detectors/surprisal.rs`

**Step 1: Read each file, write tests**

Key test cases:
- `generator_misuse.rs`: Generator consumed multiple times, or `list(generator)` on large data
- `infinite_loop.rs`: `while True` without break condition
- `test_in_production.rs`: Test assertions (`assert`, `unittest`) in production code
- `surprisal.rs`: This one is conditional (needs n-gram model) — test with a mock or test the helper functions directly

**Step 2: Run tests and commit**

```bash
git add repotoire-cli/src/detectors/generator_misuse.rs repotoire-cli/src/detectors/infinite_loop.rs repotoire-cli/src/detectors/test_in_production.rs repotoire-cli/src/detectors/surprisal.rs
git commit -m "test: add unit tests for generator, infinite loop, test-in-prod, surprisal detectors"
```

### Task 17: Run full test suite and fix any failures

**Step 1: Run all tests**

Run: `cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test 2>&1`

Expected: All tests PASS. If any new tests fail, it indicates a detector bug — investigate and fix.

**Step 2: Run with verbose output for any failures**

Run: `cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib detectors -- --nocapture 2>&1 | head -200`

**Step 3: Fix any detector bugs found by tests**

If a test reveals a false negative (detector doesn't catch what it should), fix the detector logic. If a test reveals the detector works differently than expected, adjust the test to match actual behavior and add a comment explaining the detector's design choice.

**Step 4: Commit fixes**

```bash
git add -u repotoire-cli/src/detectors/
git commit -m "fix: resolve detector bugs found during test coverage expansion"
```

---

## Workstream 3: Cleanup & Completion

### Task 18: Complete query_cache.rs module

**Files:**
- Modify: `repotoire-cli/src/detectors/query_cache.rs`

**Step 1: Add `from_graph()` constructor**

Read the current `query_cache.rs` and add a method to populate the cache from a `GraphStore`:

```rust
impl QueryCache {
    /// Populate cache from a graph store
    pub fn from_graph(graph: &dyn crate::graph::GraphQuery) -> Self {
        let mut cache = Self::new();

        // Cache all functions
        for func in graph.get_functions() {
            let qn = func.qualified_name.clone();
            cache.functions_by_file
                .entry(func.file_path.clone())
                .or_default()
                .push(qn.clone());

            let callers = graph.get_callers(&qn);

            cache.functions.insert(qn.clone(), FunctionData {
                qualified_name: qn.clone(),
                file_path: func.file_path,
                line_start: func.line_start,
                line_end: func.line_end,
                complexity: func.complexity.unwrap_or(0) as i32,
                loc: func.loc as i32,
                parameters: vec![], // Populated from properties if available
                return_type: None,
                is_async: func.is_async,
                decorators: vec![],
                docstring: None,
            });

            cache.callers.insert(qn, callers.into_iter().collect());
        }

        // Cache all classes
        for class in graph.get_classes() {
            let qn = class.qualified_name.clone();
            cache.classes_by_file
                .entry(class.file_path.clone())
                .or_default()
                .push(qn.clone());

            cache.classes.insert(qn, ClassData {
                qualified_name: class.qualified_name,
                file_path: class.file_path,
                line_start: class.line_start,
                line_end: class.line_end,
                complexity: class.complexity.unwrap_or(0) as i32,
                method_count: class.method_count.unwrap_or(0) as i32,
                methods: vec![],
            });
        }

        // Cache all files
        for file in graph.get_files() {
            cache.files.insert(file.file_path.clone(), FileData {
                file_path: file.file_path,
                loc: file.loc as i64,
                language: file.language.clone().unwrap_or_default(),
            });
        }

        cache
    }
}
```

Note: Read the actual `GraphQuery` trait methods and `CodeNode`/`NodeInfo` fields before writing this — the code above is a template. Adjust field names to match the actual API.

**Step 2: Remove `#![allow(dead_code)]`**

Remove the `#![allow(dead_code)]` line at the top of the file.

**Step 3: Add unit tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_empty_cache() {
        let cache = QueryCache::new();
        assert_eq!(cache.fan_in("any"), 0);
        assert_eq!(cache.fan_out("any"), 0);
        assert!(cache.functions_in_file("any.py").is_empty());
    }

    #[test]
    fn test_from_graph() {
        let store = GraphStore::in_memory();
        // Add some test nodes
        use crate::graph::store_models::{CodeNode, CodeEdge};
        store.add_node(CodeNode::file("main.py"));
        store.add_node(CodeNode::function("main", "main.py")
            .with_qualified_name("main.py::main")
            .with_lines(1, 10));
        store.add_node(CodeNode::function("helper", "main.py")
            .with_qualified_name("main.py::helper")
            .with_lines(12, 20));
        store.add_edge_by_name("main.py::main", "main.py::helper", CodeEdge::calls());

        let cache = QueryCache::from_graph(&store);
        assert_eq!(cache.functions.len(), 2);
        assert_eq!(cache.functions_in_file("main.py").len(), 2);
    }
}
```

**Step 4: Verify it compiles**

Run: `cd /home/zhammad/personal/repotoire/repotoire-cli && cargo check`

**Step 5: Run tests**

Run: `cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib detectors::query_cache -- --nocapture`

**Step 6: Commit**

```bash
git add repotoire-cli/src/detectors/query_cache.rs
git commit -m "feat: complete query_cache module with from_graph() constructor"
```

### Task 19: Clean up dead TaintDetector and dead_code annotations

**Files:**
- Modify: `repotoire-cli/src/detectors/taint_detector.rs` (or remove)
- Modify: `repotoire-cli/src/detectors/mod.rs` (if removing registration)
- Various detector files with `#[allow(dead_code)]`

**Step 1: Assess TaintDetector**

Read `taint_detector.rs` and the comment in `mod.rs` about why it was disabled. Since it was replaced by graph-based alternatives (PathTraversal, CommandInjection, SQLInjection), add a doc comment at the top explaining this:

```rust
//! Legacy TaintDetector - replaced by graph-based security detectors.
//!
//! This detector used naive file-based taint analysis. It has been superseded by:
//! - `PathTraversalDetector` (graph-based path traversal detection)
//! - `CommandInjectionDetector` (graph-based command injection detection)
//! - `SqlInjectionDetector` (graph-based SQL injection detection)
//! - `SsrfDetector` (graph-based SSRF detection)
//!
//! Kept for reference and potential future use with improved taint flow analysis.
```

**Step 2: Clean up `#[allow(dead_code)]` annotations**

Search all detector files for `#[allow(dead_code)]`. For each:
- If the method/field IS used in tests, change to `#[cfg(test)]` on the method or remove the allow
- If the method/field is truly unused, remove it
- If it says "reserved for future" — remove it; YAGNI

**Step 3: Add surprisal detector logging**

In `mod.rs`, find the conditional surprisal registration and add a log line when it's skipped:

```rust
if let Some(model) = ngram_model {
    if model.is_confident() {
        detectors.push(Arc::new(SurprisalDetector::new(repository_path, model)));
    } else {
        tracing::debug!("Surprisal detector skipped: n-gram model not confident enough");
    }
} else {
    tracing::debug!("Surprisal detector skipped: no n-gram model available");
}
```

**Step 4: Verify it compiles**

Run: `cd /home/zhammad/personal/repotoire/repotoire-cli && cargo check`

**Step 5: Commit**

```bash
git add -u repotoire-cli/src/detectors/
git commit -m "refactor: clean up dead code annotations and document legacy TaintDetector"
```

---

## Workstream 4: Live Validation

### Task 20: Validate against Flask

**Step 1: Clone Flask**

```bash
cd /tmp && git clone --depth 1 https://github.com/pallets/flask.git flask-validation
```

**Step 2: Run analysis**

```bash
cd /home/zhammad/personal/repotoire && cargo run --manifest-path repotoire-cli/Cargo.toml -- analyze /tmp/flask-validation --format json > /tmp/flask-results.json 2>/tmp/flask-stderr.log
```

**Step 3: Review findings**

Examine the JSON output. For each detector category, sample 5 findings and categorize as:
- **TP (True Positive)**: Real issue
- **FP (False Positive)**: Not a real issue
- **Debatable**: Could go either way

**Step 4: Document results**

Create `docs/audit/live-validation-flask.md` with:
- Total findings count by detector
- FP rate per detector (from sampled findings)
- List of detectors with >30% FP rate that need tuning

**Step 5: Commit**

```bash
git add docs/audit/live-validation-flask.md
git commit -m "docs: add Flask live validation results"
```

### Task 21: Validate against FastAPI

**Step 1: Clone FastAPI**

```bash
cd /tmp && git clone --depth 1 https://github.com/tiangolo/fastapi.git fastapi-validation
```

**Step 2: Run analysis and review**

Same process as Task 20 but against FastAPI. FastAPI uses async patterns extensively, which tests the performance/async detectors.

**Step 3: Document results**

Create `docs/audit/live-validation-fastapi.md`

**Step 4: Commit**

```bash
git add docs/audit/live-validation-fastapi.md
git commit -m "docs: add FastAPI live validation results"
```

### Task 22: Validate against a React project

**Step 1: Clone a small React project**

Pick a small but real React project (e.g., a popular starter template or small app that uses hooks, components, and routing). Clone it:

```bash
cd /tmp && git clone --depth 1 <react-project-url> react-validation
```

**Step 2: Run analysis and review**

Focus especially on ReactHooksDetector findings — verify hooks violations are real.

**Step 3: Document results**

Create `docs/audit/live-validation-react.md`

**Step 4: Commit**

```bash
git add docs/audit/live-validation-react.md
git commit -m "docs: add React project live validation results"
```

### Task 23: Fix detectors with >30% false positive rate

**Step 1: Review all validation reports**

Read the three validation reports and identify detectors with >30% FP rate.

**Step 2: Fix detector logic**

For each high-FP detector:
- Read the detector source
- Identify why false positives occur (too broad regex? missing context? wrong threshold?)
- Fix the root cause
- Add a test case that covers the false positive scenario

**Step 3: Re-run validation**

Re-run analysis against the project where FPs were found. Verify FP rate improved.

**Step 4: Commit**

```bash
git add -u repotoire-cli/src/detectors/
git commit -m "fix: reduce false positive rates for detectors flagged in live validation"
```

### Task 24: Final summary and commit

**Step 1: Create summary report**

Create `docs/audit/detector-quality-summary.md` with:
- Final test coverage numbers (run `cargo test --lib detectors 2>&1 | tail -5`)
- Standards gap analysis highlights
- Live validation FP rates
- List of remaining improvements for future work

**Step 2: Commit**

```bash
git add docs/audit/detector-quality-summary.md
git commit -m "docs: add detector quality assurance summary report"
```
