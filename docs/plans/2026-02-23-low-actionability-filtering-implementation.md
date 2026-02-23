# Low-Actionability Filtering Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce Django from 293 → ~166 findings by teaching 5 detectors to skip intentional/low-value patterns and fixing 16 remaining FPs.

**Architecture:** Each task modifies one detector in `repotoire-cli/src/detectors/`. Changes add context-aware suppression for low-actionability patterns (cleanup methods, `__init__.py` re-exports) and fix remaining false positives (CSRF decorator definitions, polymorphic generators, function-scoped imports).

**Tech Stack:** Rust, regex crate, cargo test

---

### Task 1: EmptyCatchDetector — Skip Cleanup/Teardown and Safe Probing Patterns

**Files:**
- Modify: `repotoire-cli/src/detectors/empty_catch.rs:115-267`

**Context:** All 101 Django empty catches are intentional. Three patterns dominate: (A) empty catches in cleanup/teardown methods like `close()`, `__del__`, `__exit__`; (B) broader-than-ImportError import probing (e.g., `except Exception: pass` where try body is a single `import`); (C) safe single-line probing with specific exceptions (e.g., `except KeyError: pass` with a 1-line try body).

**Step 1: Add tests**

In the `#[cfg(test)] mod tests` section at the bottom of `empty_catch.rs`, add:

```rust
#[test]
fn test_no_finding_for_cleanup_method() {
    let store = GraphStore::in_memory();
    let detector = EmptyCatchDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("response.py", "class Response:\n    def close(self):\n        for closer in self._closers:\n            try:\n                closer()\n            except Exception:\n                pass\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag empty catch in close() method. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_finding_for_exit_method() {
    let store = GraphStore::in_memory();
    let detector = EmptyCatchDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("cursor.py", "class Cursor:\n    def __exit__(self, exc_type, exc_val, tb):\n        try:\n            self.close()\n        except db.Error:\n            pass\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag empty catch in __exit__ method. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_finding_for_import_probing_with_broad_except() {
    let store = GraphStore::in_memory();
    let detector = EmptyCatchDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("compat.py", "try:\n    from yaml import CSafeLoader as SafeLoader\nexcept Exception:\n    pass\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag import probing with broad except. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_finding_for_safe_single_line_probe() {
    let store = GraphStore::in_memory();
    let detector = EmptyCatchDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("utils.py", "def get_size(f):\n    try:\n        return os.path.getsize(f.name)\n    except (OSError, TypeError):\n        pass\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag safe single-line probe with specific exceptions. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_still_detects_broad_except_in_regular_function() {
    let store = GraphStore::in_memory();
    let detector = EmptyCatchDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("handler.py", "def process_data():\n    try:\n        result = complex_operation()\n        save_to_db(result)\n    except Exception:\n        pass\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        !findings.is_empty(),
        "Should still flag broad except in regular function with multi-line try body"
    );
}
```

**Step 2: Run tests to verify new tests fail**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib empty_catch -- --nocapture 2>&1 | tail -20
```

Expected: 4 new tests FAIL (cleanup, exit, import probing, safe probe), 1 new test PASSES (still detects).

**Step 3: Implement the fix**

In `scan_file()`, the empty catch is detected at line 141-178 (Python path). After detecting an empty catch but BEFORE creating the finding at line 191, add three checks:

After line 176 (closing brace of `else` block), and before line 191 (`if is_empty_catch`), add a helper to find the containing function name by searching backward from `catch_line`:

```rust
if is_empty_catch {
    // --- NEW: Skip low-actionability patterns ---

    // Pattern A: Cleanup/teardown methods
    // Search backward to find containing function definition
    let mut containing_func = None;
    for j in (0..catch_line).rev() {
        let func_trimmed = lines[j].trim();
        if func_trimmed.starts_with("def ") {
            // Extract function name
            if let Some(name_end) = func_trimmed.find('(') {
                containing_func = Some(func_trimmed[4..name_end].trim().to_string());
            }
            break;
        }
        // Stop at class definition (don't search too far)
        if func_trimmed.starts_with("class ") {
            break;
        }
    }

    if let Some(ref func_name) = containing_func {
        let cleanup_methods = [
            "close", "_close", "__del__", "__exit__", "__aexit__",
            "shutdown", "dispose", "cleanup", "teardown", "finalize",
            "_cleanup", "_teardown", "_dispose", "_shutdown",
        ];
        if cleanup_methods.iter().any(|m| func_name == m) {
            continue; // Skip — cleanup methods intentionally swallow errors
        }
    }

    // Pattern B: Import probing with broader-than-ImportError exception
    if let Some(try_start) = Self::find_try_block_start(&lines, catch_line) {
        let try_body_lines: Vec<&str> = lines[try_start + 1..catch_line]
            .iter()
            .map(|l| l.trim())
            .filter(|l| !l.is_empty())
            .collect();

        if try_body_lines.len() <= 2
            && try_body_lines.iter().any(|l| {
                l.starts_with("import ") || l.starts_with("from ")
            })
        {
            continue; // Skip — import probing pattern
        }

        // Pattern C: Safe single-line probing with specific exceptions
        let safe_exceptions = [
            "KeyError", "AttributeError", "TypeError", "ValueError",
            "FileNotFoundError", "OSError", "PermissionError",
            "NotImplementedError", "StopIteration", "UnicodeDecodeError",
            "UnicodeEncodeError", "LookupError", "IndexError",
        ];

        let except_body = lines[catch_line]
            .trim()
            .strip_prefix("except")
            .unwrap_or("")
            .strip_suffix(":")
            .unwrap_or("")
            .trim();

        let all_safe = if !except_body.is_empty() {
            // Parse exception types from "except (A, B):" or "except A:"
            let types_str = except_body
                .trim_start_matches('(')
                .trim_end_matches(')')
                .trim_end_matches(" as _")
                .trim_end_matches(" as exc")
                .trim_end_matches(" as e");
            types_str
                .split(|c: char| c == ',' || c == '|')
                .map(|t| t.trim())
                .filter(|t| !t.is_empty())
                .all(|t| safe_exceptions.iter().any(|s| t.contains(s)))
        } else {
            false
        };

        if all_safe && try_body_lines.len() <= 2 {
            continue; // Skip — safe single-line probe
        }
    }

    // --- END new checks ---

    // Find the try block and analyze it (existing code continues from here)
```

Move the existing code from line 191 (`if is_empty_catch {`) into the new block. The `is_empty_catch` boolean is already set above — just add the three `continue;` checks before the existing severity assessment code.

**Important:** The existing `if is_empty_catch {` block at line 191 should be restructured. The new checks need to wrap the existing code. Replace lines 191-264 with:

```rust
if is_empty_catch {
    // [INSERT NEW CHECKS HERE - the 3 patterns above]

    // [EXISTING CODE - severity assessment and finding creation from lines 192-264]
}
```

**Step 4: Run tests to verify they pass**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib empty_catch 2>&1 | tail -10
```

Expected: ALL tests pass (8 existing + 5 new = 13 total).

**Step 5: Commit**

```bash
cd /home/zhammad/personal/repotoire && git add repotoire-cli/src/detectors/empty_catch.rs && git commit -m "fix: EmptyCatchDetector skips cleanup methods, import probing, and safe single-line probes"
```

---

### Task 2: WildcardImportsDetector — Skip All Wildcard Imports in `__init__.py`

**Files:**
- Modify: `repotoire-cli/src/detectors/wildcard_imports.rs:108-114`

**Context:** All 20 Django wildcard import findings are in `__init__.py` files doing public API re-exports. The detector already skips relative imports (`from .module import *`) in `__init__.py` at lines 108-113, but doesn't skip absolute imports (`from django.db.models.fields import *`). ALL wildcard imports in `__init__.py` are re-exports by definition.

**Step 1: Update existing test**

The test `test_still_detects_absolute_import_in_init_py` at line 254-266 currently asserts that absolute wildcard imports in `__init__.py` ARE flagged. We need to invert this test since the fix will skip ALL wildcard imports in `__init__.py`:

Replace the test with:

```rust
#[test]
fn test_no_finding_for_absolute_import_in_init_py() {
    let store = GraphStore::in_memory();
    let detector = WildcardImportsDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("__init__.py", "from django.db.models.fields import *\nfrom os.path import *\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag ANY wildcard imports in __init__.py (all are re-exports). Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}
```

**Step 2: Implement the fix**

In `wildcard_imports.rs`, lines 108-113 currently check `if is_init_py` and then only skip relative imports. Change it to skip ALL imports in `__init__.py`:

Replace lines 108-114:

```rust
                        if is_init_py {
                            let import_trimmed = line.trim();
                            // Relative imports start with "from ." or "from .."
                            if import_trimmed.starts_with("from .") {
                                continue;
                            }
                        }
```

With:

```rust
                        if is_init_py {
                            // ALL wildcard imports in __init__.py are re-exports
                            // This is the standard Python convention for package namespaces
                            continue;
                        }
```

**Step 3: Run tests**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib wildcard_imports 2>&1 | tail -10
```

Expected: ALL 5 tests pass.

**Step 4: Commit**

```bash
cd /home/zhammad/personal/repotoire && git add repotoire-cli/src/detectors/wildcard_imports.rs && git commit -m "fix: WildcardImportsDetector skips all wildcard imports in __init__.py (re-export convention)"
```

---

### Task 3: DjangoSecurityDetector — CSRF Decorator Definition + Management Command Exclusion

**Files:**
- Modify: `repotoire-cli/src/detectors/django_security.rs:135-209` (CSRF check)
- Modify: `repotoire-cli/src/detectors/django_security.rs:320-334` (raw SQL exclusions)

**Context:** All 4 remaining findings are FPs: (1) csrf_exempt decorator definition file flagged, (2) comment mentioning csrf_exempt flagged, (3-4) raw SQL in management commands (ORM-generated).

**Step 1: Add tests**

In the test section of `django_security.rs`, add:

```rust
#[test]
fn test_no_csrf_finding_for_decorator_definition_module() {
    let store = GraphStore::in_memory();
    let detector = DjangoSecurityDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("views/decorators/csrf.py", "from functools import wraps\n\ndef csrf_exempt(view_func):\n    \"\"\"Mark a view as exempt from CSRF protection.\"\"\"\n    @wraps(view_func)\n    def wrapper(*args, **kwargs):\n        return view_func(*args, **kwargs)\n    wrapper.csrf_exempt = True\n    return wrapper\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    let csrf_findings: Vec<_> = findings.iter().filter(|f| f.title.contains("CSRF")).collect();
    assert!(
        csrf_findings.is_empty(),
        "Should not flag CSRF in decorator definition module. Found: {:?}",
        csrf_findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_csrf_finding_for_comment() {
    let store = GraphStore::in_memory();
    let detector = DjangoSecurityDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("views/base.py", "class View:\n    # Copy possible attributes set by decorators, e.g. @csrf_exempt\n    view.__dict__.update(cls.dispatch.__dict__)\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    let csrf_findings: Vec<_> = findings.iter().filter(|f| f.title.contains("CSRF")).collect();
    assert!(
        csrf_findings.is_empty(),
        "Should not flag CSRF mentioned in comments. Found: {:?}",
        csrf_findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_raw_sql_finding_for_management_command() {
    let store = GraphStore::in_memory();
    let detector = DjangoSecurityDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("management/commands/loaddata.py", "class Command(BaseCommand):\n    def handle(self):\n        cursor.execute(line)\n"),
        ("contrib/sites/management.py", "def create_default_site(app_config):\n    cursor.execute(command)\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    let raw_sql_findings: Vec<_> = findings.iter().filter(|f| f.title.contains("Raw SQL")).collect();
    assert!(
        raw_sql_findings.is_empty(),
        "Should not flag raw SQL in management commands. Found: {:?}",
        raw_sql_findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}
```

**Step 2: Implement Fix 1 — CSRF decorator definition and comment skip**

Find the CSRF check (around line 135). The existing code matches `csrf_exempt` via regex. After the match, add:

```rust
// Skip CSRF decorator definition modules (flagging the definition is like flagging a fire extinguisher)
if path_str.contains("decorators/csrf") {
    // This is the module that DEFINES csrf_exempt, not a module that USES it
    // Skip all CSRF findings in this file
} else {
    // Skip lines that are comments mentioning csrf_exempt
    let csrf_trimmed = line.trim();
    if csrf_trimmed.starts_with("#") || csrf_trimmed.starts_with("//") {
        // Comment line — not actual CSRF exemption usage
    } else {
        // ... existing CSRF finding logic ...
    }
}
```

Restructure the CSRF finding block to only create findings when the line is NOT in a decorator definition module AND NOT a comment.

**Step 3: Implement Fix 2 — Management command raw SQL exclusion**

In the raw SQL path exclusions (around line 320-334), add:

```rust
|| lower_path.contains("management/commands/")
|| lower_path.contains("management.py")
```

**Step 4: Run tests**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib django_security 2>&1 | tail -10
```

Expected: ALL tests pass (existing + 3 new).

**Step 5: Commit**

```bash
cd /home/zhammad/personal/repotoire && git add repotoire-cli/src/detectors/django_security.rs && git commit -m "fix: DjangoSecurityDetector skips CSRF decorator definitions, comments, and management commands"
```

---

### Task 4: GeneratorMisuseDetector — Polymorphic Interface Skip + Lazy Consumption Fix

**Files:**
- Modify: `repotoire-cli/src/detectors/generator_misuse.rs:272-284` (single-yield check)
- Modify: `repotoire-cli/src/detectors/generator_misuse.rs:187-212` (lazy consumption)

**Context:** ~7 FPs: (1) single-yield generators that implement polymorphic interfaces (e.g., `get_template_sources`, `subwidgets`, `chunks`); (2) generators consumed lazily via `for` loop in other files but detector doesn't find cross-file lazy consumption.

**Step 1: Add tests**

```rust
#[test]
fn test_no_finding_for_polymorphic_single_yield() {
    let store = GraphStore::in_memory();
    let detector = GeneratorMisuseDetector::with_path("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("loaders/locmem.py", "class Loader(BaseLoader):\n    def get_template_sources(self, template_name):\n        yield Origin(name=template_name, loader=self)\n"),
        ("widgets.py", "class Widget:\n    def subwidgets(self, name, value):\n        yield self.get_context(name, value)\n"),
        ("files/uploadedfile.py", "class InMemoryUploadedFile(UploadedFile):\n    def chunks(self, chunk_size=None):\n        yield self.read()\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag polymorphic interface methods. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_finding_for_cross_file_lazy_consumption() {
    let store = GraphStore::in_memory();
    let detector = GeneratorMisuseDetector::with_path("/mock/repo");
    // smart_split is defined in text.py but consumed lazily in template/base.py via for loop
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("utils/text.py", "def smart_split(text):\n    for bit in _smart_split_re.finditer(text):\n        yield bit\n"),
        ("template/base.py", "from utils.text import smart_split\nfor token in smart_split(template_string):\n    process(token)\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    // smart_split has yield in loop, so it shouldn't be flagged as single-yield
    // But if it were detected as always-list-wrapped, the cross-file for loop should clear it
    assert!(
        findings.is_empty(),
        "Should detect cross-file lazy consumption. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}
```

**Step 2: Implement Fix 1 — Polymorphic interface method skip**

In the single-yield check at lines 272-284, after the contextmanager and resource management checks, add:

```rust
// Skip known polymorphic interface methods — these implement a
// protocol where other implementations yield many items. A single-yield
// override is expected (not a misuse).
let polymorphic_methods = [
    "get_template_sources", "subwidgets", "chunks",
    "__iter__", "__aiter__",
    "get_urls", "get_permissions",
];
if polymorphic_methods.iter().any(|m| func_name == *m)
    || func_name.starts_with("iter_")
    || func_name.starts_with("get_")
{
    continue;
}
```

Note: The `get_` prefix is broad — this will skip ALL single-yield `get_*` generators. If that's too aggressive, narrow to only the specific names. But in practice, `get_*` generators that single-yield are usually protocol implementations.

**Step 3: Implement Fix 2 — Cross-file lazy consumption**

The `is_consumed_lazily()` method at lines 187-212 uses the graph to find callers, then reads their files. But it reads via `std::fs::read_to_string` which uses the repository path, not the MockFileProvider. For cross-file detection to work with the file provider, add a fallback that scans all files in the provider:

After line 211 (end of `is_consumed_lazily`), add a new method:

```rust
/// Check if generator is consumed lazily in any file (via file provider)
fn is_consumed_lazily_in_files(
    &self,
    func_name: &str,
    files: &dyn crate::detectors::file_provider::FileProvider,
) -> bool {
    for path in files.files_with_extension("py") {
        if let Some(content) = files.content(path) {
            // Check for lazy for-loop consumption
            let for_pattern = format!("for ");
            let call_pattern = format!("{}(", func_name);
            if content.contains(&for_pattern) && content.contains(&call_pattern) {
                // Verify it's `for x in func_name(...)` not `list(func_name(...))`
                let list_pattern = format!("list({}(", func_name);
                if !content.contains(&list_pattern) {
                    return true;
                }
            }
        }
    }
    false
}
```

Then in the list-wrapped check at line 326-327, add the file-based check:

```rust
if list_wrapped.contains(func_name)
    && !self.is_consumed_lazily(func_name, graph)
    && !self.is_consumed_lazily_in_files(func_name, files)
{
```

**Step 4: Run tests**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib generator_misuse 2>&1 | tail -10
```

Expected: ALL tests pass.

**Step 5: Commit**

```bash
cd /home/zhammad/personal/repotoire && git add repotoire-cli/src/detectors/generator_misuse.rs && git commit -m "fix: GeneratorMisuseDetector skips polymorphic interface methods and detects cross-file lazy consumption"
```

---

### Task 5: UnusedImportsDetector — Skip Function-Scoped Imports

**Files:**
- Modify: `repotoire-cli/src/detectors/unused_imports.rs:257-280`

**Context:** ~5 FPs from function-scoped imports (imports inside function bodies that are used on the very next line). These exist to avoid circular imports or lazy-load modules. The detector treats them as module-level and misses their usage.

**Step 1: Add tests**

```rust
#[test]
fn test_no_finding_for_function_scoped_import() {
    let store = GraphStore::in_memory();
    let detector = UnusedImportsDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("lookups.py", "def get_prep_lookup(self):\n    from django.db.models.sql.query import Query\n    if isinstance(self.rhs, Query):\n        return self.rhs\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag function-scoped imports (used to avoid circular imports). Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_finding_for_deeply_indented_import() {
    let store = GraphStore::in_memory();
    let detector = UnusedImportsDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("base.py", "class DatabaseWrapper:\n    def connect(self):\n        from .psycopg_any import IsolationLevel, is_psycopg3\n        if is_psycopg3:\n            conn.isolation_level = IsolationLevel.READ_COMMITTED\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag indented imports inside methods. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}
```

**Step 2: Implement the fix**

In the Python import extraction block at lines 257-280, before extracting imports, check if the import is indented (inside a function/method body):

Replace lines 257-280:

```rust
                    let imports = if ext == "py" {
                        if trimmed.starts_with("import ") || trimmed.starts_with("from ") {
```

With:

```rust
                    let imports = if ext == "py" {
                        if trimmed.starts_with("import ") || trimmed.starts_with("from ") {
                            // Skip function-scoped imports (indented imports inside function bodies)
                            // These exist to avoid circular imports or lazy-load modules and are
                            // always intentional. Check if the line has leading whitespace.
                            let leading_spaces = line.len() - line.trim_start().len();
                            if leading_spaces >= 4 {
                                // Indented import — inside a function/method body, skip it
                                continue;
                            }
```

Keep the rest of the import extraction code (multi-line handling etc.) unchanged, just add this check at the top.

**Step 3: Run tests**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib unused_imports 2>&1 | tail -10
```

Expected: ALL tests pass (existing + 2 new).

**Step 4: Commit**

```bash
cd /home/zhammad/personal/repotoire && git add repotoire-cli/src/detectors/unused_imports.rs && git commit -m "fix: UnusedImportsDetector skips function-scoped imports (indented imports inside function bodies)"
```

---

### Task 6: Full Validation

**Files:** None (validation only)

**Step 1: Run full test suite**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test 2>&1 | tail -20
```

Expected: ALL tests pass (716+ existing + new tests from each task).

**Step 2: Validate against Django**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo run -- analyze /tmp/django-repo 2>&1 | tail -30
```

Expected: ~166 findings, score ≥99/A+.

**Step 3: Validate against Flask**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo run -- analyze /tmp/flask-repo 2>&1 | tail -10
```

Expected: No regressions (~23 findings).

**Step 4: Validate against FastAPI**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo run -- analyze /tmp/fastapi-repo 2>&1 | tail -10
```

Expected: No regressions (~106 findings).

**Step 5: Record results**

Create a summary of before/after findings for each project.
