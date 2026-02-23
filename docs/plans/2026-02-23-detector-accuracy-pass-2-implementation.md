# Detector Accuracy Pass 2 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce Django false positives from 616 → ~250 by fixing 15 detectors (42 targeted fixes total).

**Architecture:** Each task modifies one Rust detector file — applying regex/logic fixes, adding unit tests for the FP patterns identified in the Django audit, and verifying compilation. Phase 1 covers simple regex/config changes. Phase 2 covers logic refactors requiring new tracking state.

**Tech Stack:** Rust (regex crate), cargo test, MockFileProvider for unit tests

**Design doc:** `docs/plans/2026-02-23-detector-accuracy-pass-2-design.md`

---

## Phase 1: Quick Wins (regex/config changes)

### Task 1: DebugCodeDetector — 5 fixes, eliminate 22 FPs

**Files:**
- Modify: `repotoire-cli/src/detectors/debug_code.rs`

**Step 1: Fix `print\(` → `\bprint\(` word boundary (line 22)**

In `debug_pattern()`, change the regex fragment `print\(` to `\bprint\(`. This prevents matching `pprint(`, `sprint(`, etc.

```rust
// Line 22 — change print\( to \bprint\(
DEBUG_PATTERN.get_or_init(|| Regex::new(r"(?i)(console\.(log|debug|info|warn)|\bprint\(|debugger;?|binding\.pry|byebug|import\s+pdb|pdb\.set_trace)").expect("valid regex"))
```

Note: `debug\s*=\s*True|DEBUG\s*=\s*true` patterns are **removed entirely** — they catch intentional config kwargs like `Engine(debug=True)` and `DEBUG = True` in settings (which is handled by DjangoSecurityDetector already).

**Step 2: Add "info", "output", "report" to `is_logging_utility` (line 41)**

```rust
fn is_logging_utility(func_name: &str) -> bool {
    let logging_patterns = [
        "log", "debug", "trace", "print", "dump", "inspect", "show", "display",
        "info", "output", "report",
    ];
    let name_lower = func_name.to_lowercase();
    logging_patterns.iter().any(|p| name_lower.contains(p))
}
```

**Step 3: Add management/CLI path exclusions (lines 49-57)**

```rust
fn is_dev_only_path(path: &str) -> bool {
    let dev_patterns = [
        "/dev/",
        "/debug/",
        "/utils/debug",
        "/helpers/debug",
        "debug_",
        "_debug.",
        "/logging/",
        "/management/commands/",
        "/management/",
        "/cli/",
        "/cmd/",
    ];
    dev_patterns.iter().any(|p| path.contains(p))
}
```

**Step 4: Add verbosity-guard detection (after line 131)**

After the comment check on line 131, add a verbosity guard check before the `debug_pattern().is_match(line)` call:

```rust
                    if trimmed.starts_with("//") || trimmed.starts_with("#") {
                        continue;
                    }

                    // Skip verbosity-guarded prints (CLI command output)
                    if (trimmed.starts_with("print(") || trimmed.starts_with("print (")) {
                        if let Some(prev) = prev_line {
                            let prev_trimmed = prev.trim();
                            if prev_trimmed.contains("verbosity") || prev_trimmed.contains("verbose") {
                                continue;
                            }
                        }
                    }

                    if debug_pattern().is_match(line) {
```

**Step 5: Write tests**

Add tests for: `pprint()` not flagged, `ogrinfo()` not flagged, `if verbosity >= 2: print()` not flagged, management/commands/ path not flagged.

```rust
#[test]
fn test_no_finding_for_pprint() {
    let store = GraphStore::in_memory();
    let detector = DebugCodeDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("filters.py", "def pprint(value):\n    return str(value)\n\nresult = pprint(data)\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(findings.is_empty(), "Should not flag pprint(). Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}

#[test]
fn test_no_finding_for_verbosity_guarded_print() {
    let store = GraphStore::in_memory();
    let detector = DebugCodeDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("mgmt.py", "def handle(self):\n    if verbosity >= 2:\n        print(\"Processing...\")\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(findings.is_empty(), "Should not flag verbosity-guarded print(). Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}

#[test]
fn test_no_finding_for_management_command_path() {
    let store = GraphStore::in_memory();
    let detector = DebugCodeDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("management/commands/migrate.py", "def handle(self):\n    print(\"Running migrations...\")\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(findings.is_empty(), "Should not flag print() in management commands. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}

#[test]
fn test_no_finding_for_debug_kwarg() {
    let store = GraphStore::in_memory();
    let detector = DebugCodeDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("views.py", "from django.template import Engine\n\nDEBUG_ENGINE = Engine(\n    debug=True,\n    libraries={},\n)\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(findings.is_empty(), "Should not flag debug=True as keyword argument. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}
```

**Step 6: Run tests**

Run: `cargo test --package repotoire-cli debug_code -- --nocapture`
Expected: All tests pass

**Step 7: Commit**

```bash
git add repotoire-cli/src/detectors/debug_code.rs
git commit -m "fix: DebugCodeDetector word boundary, remove debug=True, add verbosity guard and management paths"
```

---

### Task 2: InsecureCookieDetector — 2 fixes, eliminate 17 FPs

**Files:**
- Modify: `repotoire-cli/src/detectors/insecure_cookie.rs`

**Step 1: Remove `.cookies[` from regex (line 22)**

```rust
fn cookie_pattern() -> &'static Regex {
    COOKIE_PATTERN.get_or_init(|| {
        Regex::new(
            r"(?i)(\.set_cookie\s*\(|response\.set_cookie\s*\(|res\.cookie\s*\(|response\.cookie\s*\(|setcookie\s*\()",
        )
        .expect("valid regex")
    })
}
```

**Step 2: Widen context window from +5 to +15 (lines 69-70)**

```rust
fn check_cookie_flags(lines: &[&str], cookie_line: usize) -> CookieFlags {
    let start = cookie_line.saturating_sub(3);
    let end = (cookie_line + 15).min(lines.len());
```

**Step 3: Write tests**

```rust
#[test]
fn test_no_finding_for_cookie_attribute_access() {
    let store = GraphStore::in_memory();
    let detector = InsecureCookieDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("response.py", "def set_cookie(self, key, value):\n    self.cookies[key] = value\n    self.cookies[key][\"secure\"] = True\n    self.cookies[key][\"httponly\"] = True\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(findings.is_empty(), "Should not flag self.cookies[] attribute access. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}

#[test]
fn test_no_finding_for_multiline_set_cookie_with_flags() {
    let store = GraphStore::in_memory();
    let detector = InsecureCookieDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("middleware.py", "def process_response(self, request, response):\n    response.set_cookie(\n        settings.SESSION_COOKIE_NAME,\n        request.session.session_key,\n        max_age=max_age,\n        expires=expires,\n        domain=settings.SESSION_COOKIE_DOMAIN,\n        path=settings.SESSION_COOKIE_PATH,\n        secure=settings.SESSION_COOKIE_SECURE or None,\n        httponly=settings.SESSION_COOKIE_HTTPONLY or None,\n        samesite=settings.SESSION_COOKIE_SAMESITE,\n    )\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(findings.is_empty(), "Should detect flags in multi-line set_cookie() call. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}
```

**Step 4: Run tests**

Run: `cargo test --package repotoire-cli insecure_cookie -- --nocapture`
Expected: All tests pass

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/insecure_cookie.rs
git commit -m "fix: InsecureCookieDetector removes .cookies[] regex and widens context window to +15"
```

---

### Task 3: InsecureCryptoDetector — 5 fixes, eliminate 21 FPs

**Files:**
- Modify: `repotoire-cli/src/detectors/insecure_crypto.rs`

**Step 1: Add `usedforsecurity=False` check (in `is_hash_mention_not_usage`, after line 42)**

```rust
    // Skip comments
    let trimmed = line.trim();
    if trimmed.starts_with("//")
        || trimmed.starts_with("#")
        || trimmed.starts_with("*")
        || trimmed.starts_with("/*")
    {
        return true;
    }

    // Skip Python hashlib calls with usedforsecurity=False
    // This is Python's official way to signal non-cryptographic usage
    if lower.contains("usedforsecurity=false") || lower.contains("usedforsecurity = false") {
        return true;
    }
```

**Step 2: Skip class/function definitions (in `detect()`, after the enum skip at line 379)**

```rust
                    // Skip enum declarations
                    if trimmed.starts_with("enum ") || trimmed.starts_with("export enum ") {
                        continue;
                    }

                    // Skip class/function definitions where MD5/SHA1 is just a name
                    // e.g., "class MD5(Transform):" or "def _sqlite_md5(text):"
                    if trimmed.starts_with("class ") || trimmed.starts_with("def ") || trimmed.starts_with("fn ") {
                        continue;
                    }
```

**Step 3: Fix dead test-path check — move from line 54 to detect() at line 346**

Remove the dead code at lines 53-58 in `is_hash_mention_not_usage()`:
```rust
    // REMOVE these lines (53-58) — they pass line content instead of file path to is_test_path
    // if crate::detectors::base::is_test_path(&lower)
    //     && (lower.contains("fn ") || lower.contains("def ") || lower.contains("function "))
    // {
    //     return true;
    // }
```

Add file-level test skip in `detect()`, after the locale check at line 346:
```rust
            // Skip test files - hash usage in tests is for test fixtures
            if crate::detectors::base::is_test_path(&path_str) {
                continue;
            }
```

**Step 4: Add non-security context keywords (in `is_hash_mention_not_usage`, after the usedforsecurity check)**

```rust
    // Skip database function implementations and non-security contexts
    if lower.contains("_sqlite_") || lower.contains("checksum") || lower.contains("etag") || lower.contains("cache_key") {
        return true;
    }
```

**Step 5: Add word boundary to regex (line 15)**

```rust
fn weak_hash() -> &'static Regex {
    WEAK_HASH.get_or_init(|| Regex::new(r#"(?i)(?:^|[^_\w])(md5|sha1|sha-1)\s*\(|hashlib\.(md5|sha1)|Digest::(MD5|SHA1)|MessageDigest\.getInstance"#).expect("valid regex"))
}
```

**Step 6: Write tests**

```rust
#[test]
fn test_no_finding_for_usedforsecurity_false() {
    let store = GraphStore::in_memory();
    let detector = InsecureCryptoDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("cache.py", "import hashlib\n\ndef make_cache_key(data):\n    return hashlib.md5(data.encode(), usedforsecurity=False).hexdigest()\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(findings.is_empty(), "Should not flag md5 with usedforsecurity=False. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}

#[test]
fn test_no_finding_for_class_definition() {
    let store = GraphStore::in_memory();
    let detector = InsecureCryptoDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("text.py", "from django.db.models import Transform\n\nclass MD5(Transform):\n    function = 'MD5'\n    lookup_name = 'md5'\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(findings.is_empty(), "Should not flag class MD5() definition. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}

#[test]
fn test_no_finding_for_sqlite_shim() {
    let store = GraphStore::in_memory();
    let detector = InsecureCryptoDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("_functions.py", "import hashlib\n\ndef _sqlite_md5(text):\n    return hashlib.md5(text.encode()).hexdigest()\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(findings.is_empty(), "Should not flag _sqlite_md5 shim function. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}

#[test]
fn test_still_detects_real_md5_usage() {
    let store = GraphStore::in_memory();
    let detector = InsecureCryptoDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("auth.py", "import hashlib\n\ndef hash_password(password, salt):\n    return hashlib.md5((salt + password).encode()).hexdigest()\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(!findings.is_empty(), "Should still detect real md5 password hashing");
}
```

**Step 7: Run tests**

Run: `cargo test --package repotoire-cli insecure_crypto -- --nocapture`
Expected: All tests pass

**Step 8: Commit**

```bash
git add repotoire-cli/src/detectors/insecure_crypto.rs
git commit -m "fix: InsecureCryptoDetector recognizes usedforsecurity=False, skips class/def, fixes test-path bug"
```

---

### Task 4: SQLInjectionDetector — 2 fixes, eliminate 14 FPs

**Files:**
- Modify: `repotoire-cli/src/detectors/sql_injection.rs`

**Step 1: Add `is_sanitized_value()` method (after `is_sql_structure_variable`, ~line 331)**

```rust
    /// Check if the line contains sanitized SQL values (e.g., quote_name())
    fn is_sanitized_value(&self, line: &str) -> bool {
        let line_lower = line.to_lowercase();
        line_lower.contains("quote_name(")
            || line_lower.contains("escape_name(")
            || line_lower.contains("quote_ident(")
            || line_lower.contains("quotename(")
    }
```

**Step 2: Use it at line 606 (after is_safe_orm_pattern check)**

```rust
                    if is_safe_orm_pattern(line, &detected_frameworks) {
                        debug!("Skipping safe ORM pattern at {}:{}", rel_path, line_num);
                        continue;
                    }

                    // Skip lines with sanitized SQL identifiers (quote_name, etc.)
                    if self.is_sanitized_value(&check_line) {
                        debug!("Skipping sanitized SQL value at {}:{}", rel_path, line_num);
                        continue;
                    }
```

**Step 3: Add framework backend path exclusion (extend `should_exclude`, lines 148-164)**

```rust
    fn should_exclude(&self, path: &Path) -> bool {
        // Use shared test file detection utility
        if is_test_file(path) {
            return true;
        }

        let path_str = path.to_string_lossy();

        // Skip ORM/database internal paths — these files ARE the database layer
        let db_internal_patterns = [
            "db/backends/",
            "db/models/sql/",
            "db/models/expressions",
            "core/cache/backends/",
        ];
        if db_internal_patterns.iter().any(|p| path_str.contains(p)) {
            return true;
        }

        // Check excluded directories
        for dir in &self.exclude_dirs {
            if path_str.split('/').any(|p| p == dir) {
                return true;
            }
        }

        false
    }
```

**Step 4: Write tests**

```rust
#[test]
fn test_no_finding_for_quote_name_sanitized() {
    let detector = SQLInjectionDetector::new();
    // quote_name() is a SQL identifier sanitizer — should not be flagged
    assert!(detector.is_sanitized_value(
        r#"cursor.execute("SELECT * FROM %s" % connection.ops.quote_name(table_name))"#
    ));
}

#[test]
fn test_excludes_db_backend_paths() {
    let detector = SQLInjectionDetector::new();
    assert!(detector.should_exclude(std::path::Path::new("django/db/backends/postgresql/introspection.py")));
    assert!(detector.should_exclude(std::path::Path::new("django/db/models/sql/compiler.py")));
    assert!(detector.should_exclude(std::path::Path::new("django/core/cache/backends/db.py")));
    // Should NOT exclude application code
    assert!(!detector.should_exclude(std::path::Path::new("myapp/views.py")));
}
```

**Step 5: Run tests**

Run: `cargo test --package repotoire-cli sql_injection -- --nocapture`
Expected: All tests pass

**Step 6: Commit**

```bash
git add repotoire-cli/src/detectors/sql_injection.rs
git commit -m "fix: SQLInjectionDetector recognizes quote_name() sanitizer and skips DB backend internals"
```

---

### Task 5: PathTraversalDetector — 4 fixes, eliminate 27 FPs

**Files:**
- Modify: `repotoire-cli/src/detectors/path_traversal.rs`

**Step 1: Fix path_join regex — remove `(?i)`, add lookbehinds (line 26)**

```rust
fn path_join() -> &'static Regex {
    PATH_JOIN.get_or_init(|| Regex::new(r"(os\.path\.join|(?<![.\w])path\.join|(?<![.\w])path\.resolve|filepath\.Join|filepath\.Clean|(?:pathlib\.)?Path\s*\()").expect("valid regex"))
}
```

Key changes: removed `(?i)` (case-sensitive now), added `(?<![.\w])` lookbehinds before `path\.join` and `path\.resolve` to prevent matching `get_full_path()`, kept `Path\s*\(` capital-P only.

**Step 2: Fix file_op regex — require `os.` prefix for ambiguous names (line 18)**

```rust
fn file_op() -> &'static Regex {
    FILE_OP.get_or_init(|| Regex::new(r"(?i)(?:^|[^.\w])(open|unlink|unlinkSync|rmdir|mkdir|copyFile|rename)\s*\(|(?:os\.remove|os\.unlink|shutil\.copy|shutil\.move|readFile|writeFile|readFileSync|writeFileSync|appendFile|createReadStream|createWriteStream|statSync|accessSync)\s*\(").expect("valid regex"))
}
```

Removes ambiguous `remove`, `stat`, `access`, `read`, `write` as standalone matches. Only matches them with `os.` or as full function names like `readFile`.

**Step 3: Replace broad `request.` with specific user-input accessors (lines 112-119)**

```rust
                    let has_user_input = line.contains("req.params") || line.contains("req.query") ||
                        line.contains("req.body") || line.contains("req.file") ||
                        line.contains("request.GET") || line.contains("request.POST") ||
                        line.contains("request.FILES") || line.contains("request.args") ||
                        line.contains("request.form") || line.contains("request.data") ||
                        line.contains("request.values") ||
                        line.contains("params[") ||
                        line.contains("input(") ||
                        line.contains("sys.argv") || line.contains("process.argv") ||
                        line.contains("r.URL") || line.contains("c.Param") || line.contains("c.Query") ||
                        line.contains("FormValue") || line.contains("r.Form") ||
                        line.contains("query[") || line.contains("query.get") ||
                        line.contains("body[") || line.contains("body.get");
```

**Step 4: Skip comment/docstring lines (after line 107)**

```rust
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    // Skip comments and docstrings
                    let trimmed_line = line.trim();
                    if trimmed_line.starts_with('#') || trimmed_line.starts_with("//") || trimmed_line.starts_with('*') || trimmed_line.starts_with("/*") {
                        continue;
                    }
```

**Step 5: Write tests**

```rust
#[test]
fn test_no_finding_for_get_full_path() {
    let store = GraphStore::in_memory();
    let detector = PathTraversalDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("views.py", "from django.http import HttpResponseRedirect\n\ndef my_view(request):\n    return HttpResponseRedirect(request.get_full_path())\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(findings.is_empty(), "Should not flag request.get_full_path() as path traversal. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}

#[test]
fn test_no_finding_for_list_remove() {
    let store = GraphStore::in_memory();
    let detector = PathTraversalDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("library.py", "def process(request):\n    params = list(request.GET.keys())\n    params.remove('page')\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(findings.is_empty(), "Should not flag list.remove() as file operation. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}

#[test]
fn test_still_detects_real_path_traversal() {
    let store = GraphStore::in_memory();
    let detector = PathTraversalDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("download.py", "import os\n\ndef download(request):\n    filename = request.GET.get('file')\n    filepath = os.path.join('/uploads', filename)\n    return open(filepath, 'r').read()\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(!findings.is_empty(), "Should still detect real path traversal with request.GET");
}
```

**Step 6: Run tests**

Run: `cargo test --package repotoire-cli path_traversal -- --nocapture`
Expected: All tests pass

**Step 7: Commit**

```bash
git add repotoire-cli/src/detectors/path_traversal.rs
git commit -m "fix: PathTraversalDetector narrows path_join regex, replaces broad request. with specific accessors"
```

---

### Task 6: LazyClassDetector — 2 fixes, eliminate 21 FPs

**Files:**
- Modify: `repotoire-cli/src/detectors/lazy_class.rs`

**Step 1: Add ORM/framework patterns to EXCLUDE_PATTERNS (lines 41-89)**

Add before the closing `];`:

```rust
    // ORM patterns (intentionally small - Strategy pattern)
    "Lookup",
    "Transform",
    "Descriptor",
    "Attribute",
    "Field",
    "Constraint",
    "Index",
    "Expression",
    "Widget",
    "Migration",
    "Command",
    "Middleware",
```

**Step 2: Add test path exclusion (in detect(), after line 224)**

```rust
            // Skip interfaces and type aliases
            if class.qualified_name.contains("::interface::")
                || class.qualified_name.contains("::type::")
            {
                continue;
            }

            // Skip test fixture/model classes
            {
                let lower_path = class.file_path.to_lowercase();
                if lower_path.contains("/test/") || lower_path.contains("/tests/")
                    || lower_path.contains("/__tests__/") || lower_path.contains("/spec/")
                    || lower_path.contains("/fixtures/")
                    || lower_path.contains("test_") || lower_path.contains("_test.")
                {
                    continue;
                }
            }
```

**Step 3: Run tests**

Run: `cargo test --package repotoire-cli lazy_class -- --nocapture`
Expected: All tests pass

**Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/lazy_class.rs
git commit -m "fix: LazyClassDetector adds ORM Lookup/Transform/Field patterns and test path exclusion"
```

---

### Task 7: GodClassDetector — 3 fixes, eliminate 20 FPs

**Files:**
- Modify: `repotoire-cli/src/detectors/god_class.rs`
- Modify: `repotoire-cli/src/detectors/class_context.rs`

**Step 1: Add `|| ctx.is_test` to skip condition (god_class.rs, line 503)**

```rust
            if let Some(ctx) = ctx {
                if ctx.skip_god_class() || ctx.is_test {
                    debug!(
                        "Skipping {} ({:?}): {}",
                        class.name, ctx.role, ctx.role_reason
                    );
                    continue;
                }
            }
```

**Step 2: Add fallback test path check when no graph context (after line 513)**

```rust
            // Fall back to pattern exclusion if no graph context
            if ctx.is_none() && self.is_excluded_pattern(&class.name) {
                debug!("Skipping excluded pattern: {}", class.name);
                continue;
            }

            // Fall back to test path check if no graph context
            if ctx.is_none() {
                let lower_path = class.file_path.to_lowercase();
                if lower_path.contains("/test/") || lower_path.contains("/tests/")
                    || lower_path.contains("/__tests__/") || lower_path.contains("/spec/")
                    || lower_path.contains("test_") || lower_path.contains("_test.")
                {
                    debug!("Skipping test class: {}", class.name);
                    continue;
                }
            }
```

**Step 3: Add framework core suffixes (class_context.rs)**

Find the `FRAMEWORK_CORE_NAMES` constant and add a suffix check. In the `infer_role()` method, add a check for framework core suffixes after the name matching:

```rust
const FRAMEWORK_CORE_SUFFIXES: &[&str] = &[
    "SchemaEditor", "Autodetector", "Compiler", "Admin",
    "Manager", "Registry", "Dispatcher",
];
```

Then in the role inference logic, add:
```rust
// Check framework core suffixes
if FRAMEWORK_CORE_SUFFIXES.iter().any(|s| class_name.ends_with(s)) {
    return (ClassRole::FrameworkCore, format!("Name ends with framework suffix"));
}
```

**Step 4: Run tests**

Run: `cargo test --package repotoire-cli god_class -- --nocapture`
Expected: All tests pass

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/god_class.rs repotoire-cli/src/detectors/class_context.rs
git commit -m "fix: GodClassDetector skips test classes and recognizes framework core suffixes"
```

---

### Task 8: GlobalVariablesDetector — 2 fixes, eliminate 1 FP + 2 dups

**Files:**
- Modify: `repotoire-cli/src/detectors/global_variables.rs`

**Step 1: Add docstring tracking (lines 221-226)**

```rust
                let mut py_indent_stack: Vec<usize> = Vec::new();
                let mut py_in_function = false;
                let mut in_docstring = false;
                let all_lines: Vec<&str> = content.lines().collect();

                for (i, line) in all_lines.iter().enumerate() {
                    let trimmed = line.trim();

                    // Track Python triple-quoted strings (docstrings)
                    if ext == "py" {
                        let triple_double = trimmed.matches("\"\"\"").count();
                        let triple_single = trimmed.matches("'''").count();
                        let triple_count = triple_double + triple_single;
                        if triple_count % 2 != 0 {
                            in_docstring = !in_docstring;
                        }
                        if in_docstring {
                            continue;
                        }
                    }
```

**Step 2: Add per-(file, variable) dedup (lines 197, 276)**

Add at line 197:
```rust
        let mut findings = vec![];
        let mut seen_globals: std::collections::HashSet<(PathBuf, String)> = std::collections::HashSet::new();
```

Add at line 276 (inside the `if let Some(var_name)` block):
```rust
                        if let Some(var_name) = Self::extract_var_name(trimmed) {
                            let key = (path.to_path_buf(), var_name.clone());
                            if seen_globals.contains(&key) {
                                continue;
                            }
                            seen_globals.insert(key);

                            let usage_count = ...
```

**Step 3: Write tests**

```rust
#[test]
fn test_no_finding_for_global_in_docstring() {
    let store = GraphStore::in_memory();
    let detector = GlobalVariablesDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("widgets.py", "class Widget:\n    def merge(self):\n        \"\"\"\n        global or in CSS you might want to override a style.\n        \"\"\"\n        pass\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(findings.is_empty(), "Should not flag 'global' in docstring. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}

#[test]
fn test_dedup_same_variable_across_functions() {
    let store = GraphStore::in_memory();
    let detector = GlobalVariablesDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("trans.py", "def func_a():\n    global _default\n    _default = get_default()\n\ndef func_b():\n    global _default\n    return _default\n\ndef func_c():\n    global _default\n    _default = None\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert_eq!(findings.len(), 1, "Should deduplicate same variable across functions. Found {} findings", findings.len());
}
```

**Step 4: Run tests**

Run: `cargo test --package repotoire-cli global_variables -- --nocapture`
Expected: All tests pass

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/global_variables.rs
git commit -m "fix: GlobalVariablesDetector skips docstrings and deduplicates per (file, variable)"
```

---

### Task 9: DjangoSecurityDetector — 1 fix, eliminate ~50 FPs

**Files:**
- Modify: `repotoire-cli/src/detectors/django_security.rs`

**Step 1: Add ORM/database-internal path exclusion for raw SQL rule (before line 319)**

```rust
                    // Check raw SQL
                    if raw_sql().is_match(line) {
                        // Skip ORM/database-internal paths — these files ARE the database layer
                        let lower_path = path_str.to_lowercase();
                        if lower_path.contains("db/backends/")
                            || lower_path.contains("db/models/sql/")
                            || lower_path.contains("db/models/expressions")
                            || lower_path.contains("core/cache/backends/")
                            || lower_path.contains("/migrations/")
                        {
                            continue;
                        }
```

**Step 2: Write tests**

```rust
#[test]
fn test_no_raw_sql_finding_for_db_backend() {
    let store = GraphStore::in_memory();
    let detector = DjangoSecurityDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("db/backends/postgresql/introspection.py", "def get_table_list(self, cursor):\n    cursor.execute(\"SELECT c.relname FROM pg_catalog.pg_class c\")\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    let raw_sql_findings: Vec<_> = findings.iter().filter(|f| f.title.contains("Raw SQL")).collect();
    assert!(raw_sql_findings.is_empty(), "Should not flag raw SQL in db/backends/. Found: {:?}",
        raw_sql_findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}
```

**Step 3: Run tests**

Run: `cargo test --package repotoire-cli django_security -- --nocapture`
Expected: All tests pass

**Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/django_security.rs
git commit -m "fix: DjangoSecurityDetector skips ORM/database backend paths for raw SQL rule"
```

---

### Task 10: CallbackHellDetector — 2 fixes, eliminate 9 FPs

**Files:**
- Modify: `repotoire-cli/src/detectors/callback_hell.rs`

**Step 1: Filter non-callback `function(` patterns (lines 128-129)**

Replace the anon_funcs counting (lines 128-129) to filter out object methods, prototype assigns, and variable declarations:

```rust
                    // anonymous functions explicitly passed as arguments
                    let anon_funcs = {
                        let mut count = 0usize;
                        for m in line.match_indices("function(").chain(line.match_indices("function (")) {
                            let before = line[..m.0].trim_end();
                            // Skip object methods: key: function(
                            let is_object_method = before.ends_with(':');
                            // Skip prototype assigns: Foo.prototype.bar = function(
                            let is_prototype = before.contains(".prototype.");
                            // Skip variable declarations: var/let/const foo = function(
                            let is_var_decl = before.ends_with('=') && (before.contains("var ") || before.contains("let ") || before.contains("const "));
                            if !is_object_method && !is_prototype && !is_var_decl {
                                count += 1;
                            }
                        }
                        count
                    };
```

**Step 2: Add brace-depth-aware nesting tracking (lines 85-168)**

Replace the entire nesting tracking block with brace-depth-aware tracking:

```rust
                let mut callback_depth = 0;
                let mut brace_depth: i32 = 0;
                let mut callback_brace_depths: Vec<i32> = Vec::new();
                let mut max_depth = 0;
                let mut max_line = 0;
                let mut then_count = 0;
                let mut anonymous_count = 0;

                for (i, line) in content.lines().enumerate() {
                    // ... (keep existing JSX/React skip logic, lines 92-119) ...

                    // Track brace depth
                    let open_braces = line.matches('{').count() as i32;
                    let close_braces = line.matches('}').count() as i32;
                    brace_depth += open_braces;

                    // ... (anon_funcs and arrows counting) ...

                    let new_callbacks = anon_funcs + arrows + thens;
                    anonymous_count += anon_funcs + arrows;
                    then_count += thens;

                    // Push brace depth for each new callback
                    for _ in 0..new_callbacks {
                        callback_brace_depths.push(brace_depth);
                        callback_depth += 1;
                    }

                    // Process closing braces — pop callbacks when we exit their scope
                    brace_depth -= close_braces;
                    while let Some(&cb_depth) = callback_brace_depths.last() {
                        if brace_depth < cb_depth {
                            callback_brace_depths.pop();
                            callback_depth = callback_depth.saturating_sub(1);
                        } else {
                            break;
                        }
                    }

                    if callback_depth > max_depth {
                        max_depth = callback_depth;
                        max_line = i + 1;
                    }
                }
```

**Step 3: Write tests**

```rust
#[test]
fn test_no_finding_for_object_methods() {
    let store = GraphStore::in_memory();
    let detector = CallbackHellDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("admin.js", "var DateTimeShortcuts = {\n    init: function() {\n        this.setup();\n    },\n    setup: function() {\n        this.render();\n    },\n    render: function() {\n        this.draw();\n    },\n    draw: function() {\n        console.log('done');\n    }\n};\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(findings.is_empty(), "Object methods should not be counted as callback nesting. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}
```

**Step 4: Run tests**

Run: `cargo test --package repotoire-cli callback_hell -- --nocapture`
Expected: All tests pass

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/callback_hell.rs
git commit -m "fix: CallbackHellDetector filters object methods and tracks brace-depth-aware nesting"
```

---

## Phase 2: Logic Refactors

### Task 11: EmptyCatchDetector — invert idiom logic, eliminate ~42 FPs

**Files:**
- Modify: `repotoire-cli/src/detectors/empty_catch.rs`

**Step 1: Replace allowlist with broad-catch blocklist (lines 163-174)**

Replace the `common_idioms` allowlist approach with an inverted approach — treat ANY specific named exception as Low severity, and only keep Medium/High for broad catches:

```rust
                            // Broad catch patterns that deserve higher severity
                            let broad_catches = [
                                "except:", "except Exception:", "except BaseException:",
                                "except Exception as", "except BaseException as",
                            ];
                            // Check if this is a broad catch (no specific exception named)
                            let is_broad_catch = except_body.is_empty()
                                || broad_catches.iter().any(|b| except_body.contains(b));

                            if !is_broad_catch {
                                // Specific named exception — always downgrade to Low
                                is_common_idiom = true;
                            }
```

**Step 2: Write tests**

```rust
#[test]
fn test_specific_exception_gets_low_severity() {
    let store = GraphStore::in_memory();
    let detector = EmptyCatchDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("views.py", "def get_user(pk):\n    try:\n        return User.objects.get(pk=pk)\n    except User.DoesNotExist:\n        pass\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(!findings.is_empty(), "Should still detect empty catch");
    assert!(findings.iter().all(|f| f.severity == Severity::Low),
        "Specific named exception should be Low severity. Got: {:?}",
        findings.iter().map(|f| (&f.title, &f.severity)).collect::<Vec<_>>());
}

#[test]
fn test_broad_except_gets_higher_severity() {
    let store = GraphStore::in_memory();
    let detector = EmptyCatchDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("handler.py", "def process():\n    try:\n        do_something()\n    except Exception:\n        pass\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(!findings.is_empty(), "Should detect broad except");
    assert!(findings.iter().any(|f| f.severity != Severity::Low),
        "Broad 'except Exception:' should NOT be Low severity. Got: {:?}",
        findings.iter().map(|f| (&f.title, &f.severity)).collect::<Vec<_>>());
}
```

**Step 3: Run tests**

Run: `cargo test --package repotoire-cli empty_catch -- --nocapture`
Expected: All tests pass

**Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/empty_catch.rs
git commit -m "fix: EmptyCatchDetector inverts idiom logic — any named exception is Low, only broad catches get Medium/High"
```

---

### Task 12: EvalDetector — 3 fixes, eliminate ~40 FPs

**Files:**
- Modify: `repotoire-cli/src/detectors/eval_detector.rs`

**Step 1: Skip `.eval()` method calls (in `check_line_for_patterns`, after line 226)**

After the `has_simple_exec` check, add a method-call filter:

```rust
        let has_simple_exec = CODE_EXEC_FUNCTIONS.iter().any(|f| line.contains(f));
        // Filter out method calls like node.eval(context) — these are NOT Python's builtin eval()
        let has_simple_exec = if has_simple_exec && line.contains("eval(") {
            // Check if eval( is preceded by a dot (method call)
            let eval_preceded_by_dot = line.find("eval(").map(|pos| {
                pos > 0 && line[..pos].trim_end().ends_with('.')
            }).unwrap_or(false);
            if eval_preceded_by_dot {
                // Remove "eval" from consideration, check if other exec functions remain
                CODE_EXEC_FUNCTIONS.iter().any(|f| *f != "eval(" && line.contains(f))
            } else {
                true
            }
        } else {
            has_simple_exec
        };
```

**Step 2: Fix path matching for framework downgrade (lines 445-454)**

```rust
        let severity = if ["__import__", "import_module"].contains(&callee_name) {
            if file_lower.contains("/flask/")
                || file_lower.contains("/django/")
                || file_lower.starts_with("django/")  // relative paths
                || file_lower.starts_with("flask/")    // relative paths
                || file_lower.contains("/werkzeug/")
                || file_lower.contains("/celery/")
                || file_lower.contains("/fastapi/")
                || file_lower.starts_with("fastapi/")
                || file_lower.contains("helpers.py")
                || file_lower.contains("loader")
                || file_lower.contains("importer")
                || file_lower.contains("plugin")
            {
                Severity::Low
            } else {
                Severity::High
            }
        } else {
            Severity::Critical
        };
```

**Step 3: Skip safe subprocess calls without shell=True (in `check_line_for_patterns`, after line 246)**

After the `shell_true_pattern` check, add a subprocess safety check:

```rust
        // Check for shell=True (high severity for subprocess calls)
        if let Some(caps) = self.shell_true_pattern.captures(line) {
            return Some(PatternMatch {
                pattern_type: "shell_true".to_string(),
                function: caps.get(1).map(|m| m.as_str().to_string()).unwrap_or_default(),
            });
        }

        // Skip subprocess.run/Popen/call WITHOUT shell=True — list args are safe
        if has_shell_exec && !line.contains("shell=True") && !line.contains("shell = True") {
            // Check if this is a subprocess call with list args (safe pattern)
            let is_subprocess = lower.contains("subprocess.run") || lower.contains("subprocess.popen")
                || lower.contains("subprocess.call") || lower.contains("subprocess.check_call")
                || lower.contains("subprocess.check_output");
            if is_subprocess {
                return None;
            }
        }
```

**Step 4: Write tests**

```rust
#[test]
fn test_no_finding_for_method_eval() {
    let store = GraphStore::in_memory();
    let detector = EvalDetector::new(PathBuf::from("/mock/repo"));
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("smartif.py", "class Operator:\n    def eval(self, context):\n        return self.value\n\nresult = op.eval(context)\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    let eval_findings: Vec<_> = findings.iter().filter(|f| f.title.contains("eval")).collect();
    assert!(eval_findings.is_empty(), "Should not flag .eval() method call. Found: {:?}",
        eval_findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}

#[test]
fn test_no_finding_for_safe_subprocess() {
    let store = GraphStore::in_memory();
    let detector = EvalDetector::new(PathBuf::from("/mock/repo"));
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("runner.py", "import subprocess\n\ndef run_command(args):\n    result = subprocess.run(args, capture_output=True)\n    return result.stdout\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    let subprocess_findings: Vec<_> = findings.iter().filter(|f| f.title.contains("subprocess") || f.title.contains("command")).collect();
    assert!(subprocess_findings.is_empty(), "Should not flag subprocess.run without shell=True. Found: {:?}",
        subprocess_findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}
```

**Step 5: Run tests**

Run: `cargo test --package repotoire-cli eval_detector -- --nocapture`
Expected: All tests pass

**Step 6: Commit**

```bash
git add repotoire-cli/src/detectors/eval_detector.rs
git commit -m "fix: EvalDetector skips .eval() methods, fixes framework path matching, skips safe subprocess"
```

---

### Task 13: GeneratorMisuseDetector — 4 fixes, eliminate 15+ FPs

**Files:**
- Modify: `repotoire-cli/src/detectors/generator_misuse.rs`

**Step 1: Add `yield from` regex and treat as multi-yield (line 20 area + lines 76-78)**

Add a new static regex:
```rust
static YIELD_FROM: OnceLock<Regex> = OnceLock::new();

fn yield_from_stmt() -> &'static Regex {
    YIELD_FROM.get_or_init(|| Regex::new(r"\byield\s+from\b").expect("valid regex"))
}
```

Modify `count_yields()` to handle `yield from` and skip strings/comments:
```rust
    fn count_yields(lines: &[&str], func_start: usize, indent: usize) -> (usize, bool) {
        let mut count = 0;
        let mut in_loop = false;

        for line in lines.iter().skip(func_start + 1) {
            let current_indent = line.chars().take_while(|c| c.is_whitespace()).count();
            if !line.trim().is_empty() && current_indent <= indent {
                break;
            }

            if line.contains("for ") || line.contains("while ") {
                in_loop = true;
            }

            let trimmed = line.trim();
            // Skip comments
            if trimmed.starts_with('#') {
                continue;
            }

            // Skip yield inside string literals
            if (trimmed.starts_with('"') || trimmed.starts_with('\'') || trimmed.starts_with("\"\"\"") || trimmed.starts_with("'''"))
                && !trimmed.starts_with("yield")
            {
                continue;
            }

            // yield from delegates to another iterable — treat as multi-yield
            if yield_from_stmt().is_match(line) {
                return (2, true); // Early return: at least 2 yields worth
            }

            if yield_stmt().is_match(line) {
                // Make sure "yield" is in code, not inside a string on this line
                // Simple heuristic: if the line contains yield and a quote before it, likely string
                if let Some(yield_pos) = line.find("yield") {
                    let before_yield = &line[..yield_pos];
                    let quote_count = before_yield.matches('"').count() + before_yield.matches('\'').count();
                    if quote_count % 2 != 0 {
                        continue; // yield is inside a string
                    }
                }
                count += 1;
            }
        }
        (count, in_loop)
    }
```

**Step 2: Make @contextmanager standalone skip (lines 236-241)**

```rust
                        if yield_count == 1 && !yield_in_loop {
                            // Skip @contextmanager decorated functions — always intentional
                            if Self::has_contextmanager_decorator(&lines, i) {
                                continue;
                            }

                            // Skip resource management patterns (try/yield/finally) with framework imports
                            if Self::is_resource_management_yield(&lines, i, indent)
                                && Self::has_framework_yield_import(&content)
                            {
                                continue;
                            }
```

**Step 3: Exclude Python builtins from list-wrapped set (lines 138-139)**

```rust
                for cap in list_call().captures_iter(&content) {
                    if let Some(func_name) = cap.get(1) {
                        let name = func_name.as_str();
                        // Exclude Python builtins — list(x.list(...)) is not wrapping a generator
                        let builtins = ["list", "dict", "set", "tuple", "str", "int", "float",
                            "bool", "map", "filter", "range", "zip", "sorted", "reversed", "enumerate",
                            "iter", "next", "type", "super", "print", "len", "max", "min", "sum", "any", "all"];
                        if !builtins.contains(&name) {
                            wrapped.insert(name.to_string());
                        }
                    }
                }
```

**Step 4: Write tests**

```rust
#[test]
fn test_no_finding_for_yield_from() {
    let store = GraphStore::in_memory();
    let detector = GeneratorMisuseDetector::new();
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("iterators.py", "def __iter__(self):\n    yield from self.items\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(findings.is_empty(), "Should not flag yield from as single-yield. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}

#[test]
fn test_no_finding_for_yield_in_string() {
    let store = GraphStore::in_memory();
    let detector = GeneratorMisuseDetector::new();
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("paginator.py", "def _check(self):\n    warnings.warn(\"Pagination may yield inconsistent results\")\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(findings.is_empty(), "Should not flag 'yield' inside string literal. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}

#[test]
fn test_no_finding_for_contextmanager_without_finally() {
    let store = GraphStore::in_memory();
    let detector = GeneratorMisuseDetector::new();
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("errors.py", "from contextlib import contextmanager\n\n@contextmanager\ndef wrap_errors():\n    try:\n        yield\n    except DatabaseError:\n        raise\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(findings.is_empty(), "Should not flag @contextmanager even without finally. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}
```

**Step 5: Run tests**

Run: `cargo test --package repotoire-cli generator_misuse -- --nocapture`
Expected: All tests pass

**Step 6: Commit**

```bash
git add repotoire-cli/src/detectors/generator_misuse.rs
git commit -m "fix: GeneratorMisuseDetector handles yield-from, string-aware yield, standalone @contextmanager"
```

---

### Task 14: RegexInLoopDetector — 3 fixes, eliminate 11 FPs

**Files:**
- Modify: `repotoire-cli/src/detectors/regex_in_loop.rs`

**Step 1: Add Python indentation-based scope tracking (lines 163-236)**

Replace the loop tracking block:

```rust
        for path in files.files_with_extensions(&["py", "js", "ts", "java", "rs", "go"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            if let Some(content) = files.content(path) {
                let _path_str = path.to_string_lossy().to_string();
                let is_python = path.extension().and_then(|e| e.to_str()) == Some("py");
                let mut in_loop = false;
                let mut loop_line = 0;
                let mut brace_depth = 0;
                let mut loop_indent: usize = 0;
                let mut loop_line_idx: usize = 0;
                let all_lines: Vec<&str> = content.lines().collect();

                for (i, line) in all_lines.iter().enumerate() {
                    // Skip Python comments
                    if is_python && line.trim().starts_with('#') {
                        continue;
                    }

                    // Skip list comprehensions (one-shot constructs)
                    if is_python && line.contains('[') && line.contains(" for ") && line.contains(" in ") {
                        continue;
                    }

                    if loop_pattern().is_match(line) {
                        in_loop = true;
                        loop_line = i + 1;
                        loop_line_idx = i;
                        if is_python {
                            loop_indent = line.len() - line.trim_start().len();
                        } else {
                            brace_depth = 0;
                        }
                    }

                    if in_loop {
                        if is_python {
                            // Python: exit loop scope when indentation returns to/below loop level
                            let trimmed = line.trim();
                            if !trimmed.is_empty() && i > loop_line_idx {
                                let current_indent = line.len() - line.trim_start().len();
                                if current_indent <= loop_indent {
                                    in_loop = false;
                                    continue;
                                }
                            }
                        } else {
                            // Brace-based languages
                            brace_depth += line.matches('{').count() as i32;
                            brace_depth -= line.matches('}').count() as i32;
                            if brace_depth < 0 {
                                in_loop = false;
                                continue;
                            }
                        }

                        // Direct regex compilation in loop
                        if regex_new().is_match(line)
                            && !is_cached_regex(line)
                            && !is_cached_regex_context(&content, i)
                        {
                            // ... (keep existing finding creation logic) ...
```

**Step 2: Write tests**

```rust
#[test]
fn test_no_finding_for_python_regex_outside_loop() {
    let store = GraphStore::in_memory();
    let detector = RegexInLoopDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("parser.py", "import re\n\nfor item in items:\n    process(item)\n\npattern = re.compile(r'\\w+')\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(findings.is_empty(), "re.compile after loop exits should not be flagged. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}

#[test]
fn test_no_finding_for_list_comprehension() {
    let store = GraphStore::in_memory();
    let detector = RegexInLoopDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("security.py", "import re\n\nREDIRECT_HOSTS = [re.compile(r) for r in settings.ALLOWED_REDIRECTS]\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(findings.is_empty(), "List comprehension re.compile should not be flagged. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}

#[test]
fn test_no_finding_for_regex_in_comment() {
    let store = GraphStore::in_memory();
    let detector = RegexInLoopDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("settings.py", "LANGUAGES = [\n    ('en', 'English'),\n    ('fr', 'French'),\n]\n# LANGUAGES_BIDI = re.compile(r'...')\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(findings.is_empty(), "Commented-out re.compile should not be flagged. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}

#[test]
fn test_still_detects_regex_inside_python_loop() {
    let store = GraphStore::in_memory();
    let detector = RegexInLoopDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("slow.py", "import re\n\nfor pattern in patterns:\n    compiled = re.compile(pattern)\n    compiled.match(text)\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(!findings.is_empty(), "Should still detect re.compile inside a for loop");
}
```

**Step 3: Run tests**

Run: `cargo test --package repotoire-cli regex_in_loop -- --nocapture`
Expected: All tests pass

**Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/regex_in_loop.rs
git commit -m "fix: RegexInLoopDetector uses Python indentation-based scope, skips comments and list comprehensions"
```

---

### Task 15: StringConcatLoopDetector — 3 fixes, eliminate ~34 FPs

**Files:**
- Modify: `repotoire-cli/src/detectors/string_concat_loop.rs`

**Step 1: Fix `f` in regex to require f-string prefix (line 38)**

```rust
fn string_concat() -> &'static Regex {
    STRING_CONCAT.get_or_init(|| {
        // Only match += with string literal or f-string
        // Fix: 'f' must be followed by a quote to be an f-string prefix
        Regex::new(r#"\w+\s*\+=\s*(?:["'`]|f["'])"#)
            .expect("valid regex")
    })
}
```

Note: The `= x + y` pattern is removed entirely — only `+=` accumulation matters for loop detection.

**Step 2: Add Python indentation-based loop scope tracking (lines 147-174)**

Replace the loop tracking block:

```rust
            if let Some(content) = files.content(path) {
                let is_python = ext == "py";
                let mut in_loop = false;
                let mut loop_line = 0;
                let mut brace_depth = 0;
                let mut loop_indent: usize = 0;
                let mut loop_line_idx: usize = 0;
                let mut _loop_var = String::new();
                let all_lines: Vec<&str> = content.lines().collect();

                for (i, line) in all_lines.iter().enumerate() {
                    if loop_pattern().is_match(line) {
                        in_loop = true;
                        loop_line = i + 1;
                        loop_line_idx = i;
                        if is_python {
                            loop_indent = line.len() - line.trim_start().len();
                        } else {
                            brace_depth = 0;
                        }

                        if let Some(caps) = for_var_pattern().captures(line) {
                            _loop_var = caps.get(1).map(|m| m.as_str().to_string()).unwrap_or_default();
                        }
                    }

                    if in_loop {
                        if is_python {
                            let trimmed = line.trim();
                            if !trimmed.is_empty() && i > loop_line_idx {
                                let current_indent = line.len() - line.trim_start().len();
                                if current_indent <= loop_indent {
                                    in_loop = false;
                                    continue;
                                }
                            }
                        } else {
                            brace_depth += line.matches('{').count() as i32;
                            brace_depth -= line.matches('}').count() as i32;
                            if brace_depth < 0 {
                                in_loop = false;
                                continue;
                            }
                        }

                        // Check for string concatenation
                        if string_concat().is_match(line) {
                            // ... (keep existing finding creation) ...
```

**Step 3: Write tests**

```rust
#[test]
fn test_no_finding_for_media_iadd() {
    let store = GraphStore::in_memory();
    let detector = StringConcatLoopDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("forms.py", "for fs in formsets:\n    media += fs.media\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(findings.is_empty(), "Should not flag media += fs.media (not string concat). Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}

#[test]
fn test_no_finding_for_concat_after_loop() {
    let store = GraphStore::in_memory();
    let detector = StringConcatLoopDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("builder.py", "for item in items:\n    process(item)\n\nresult = prefix + \"_suffix\"\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(findings.is_empty(), "Concat after loop exits should not be flagged. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>());
}

#[test]
fn test_still_detects_string_concat_in_loop() {
    let store = GraphStore::in_memory();
    let detector = StringConcatLoopDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("slow.py", "result = \"\"\nfor item in items:\n    result += \"item: \" + str(item)\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(!findings.is_empty(), "Should still detect string concat inside loop");
}
```

**Step 4: Run tests**

Run: `cargo test --package repotoire-cli string_concat_loop -- --nocapture`
Expected: All tests pass

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/string_concat_loop.rs
git commit -m "fix: StringConcatLoopDetector fixes f-string regex, adds Python indentation scope, removes = x + y pattern"
```

---

## Final Validation

### Task 16: Full test suite + real-world validation

**Step 1: Run full test suite**

Run: `cargo test --package repotoire-cli 2>&1`
Expected: All tests pass (669+ tests, 0 failures)

**Step 2: Build release**

Run: `cargo build --release --package repotoire-cli`
Expected: Build succeeds

**Step 3: Validate against Django**

```bash
# Use existing /tmp/django-repo or clone fresh
cd /tmp && [ -d django-repo ] || git clone --depth 1 https://github.com/django/django.git django-repo
cd /home/zhammad/personal/repotoire
cargo run --release --package repotoire-cli -- analyze /tmp/django-repo -o /tmp/django-r2-results.json --format json
```

Expected: Findings drop from 616 → ~250, score improves from 93.2 → ~96

**Step 4: Validate against Flask and FastAPI**

```bash
cd /tmp && [ -d flask-repo ] || git clone --depth 1 https://github.com/pallets/flask.git flask-repo
cd /tmp && [ -d fastapi-repo ] || git clone --depth 1 https://github.com/fastapi/fastapi.git fastapi-repo
cargo run --release --package repotoire-cli -- analyze /tmp/flask-repo -o /tmp/flask-r2-results.json --format json
cargo run --release --package repotoire-cli -- analyze /tmp/fastapi-repo -o /tmp/fastapi-r2-results.json --format json
```

Expected: Flask ~30 findings, FastAPI ~90 findings

**Step 5: Commit all plan docs**

```bash
git add docs/plans/2026-02-23-detector-accuracy-pass-2-design.md docs/plans/2026-02-23-detector-accuracy-pass-2-implementation.md
git commit -m "docs: add detector accuracy pass 2 design and implementation plans"
```
