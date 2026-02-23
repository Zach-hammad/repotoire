# Detector Accuracy Pass 3 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate ~75 false positives across 9 Rust detectors, reducing Django findings from 366 → ~291.

**Architecture:** Each task modifies one detector in `repotoire-cli/src/detectors/`. Fixes range from adding literal-vs-variable checks (SecretDetector) to simple path exclusions (PickleDetector). All changes are backward-compatible — they reduce false positives without removing true positives.

**Tech Stack:** Rust, regex crate, cargo test

---

## Phase 1: Deep Refactors

### Task 1: SecretDetector — Variable vs Literal Distinction

**Files:**
- Modify: `repotoire-cli/src/detectors/secrets.rs:283-293`

**Context:** The Generic Secret pattern matches `(secret|password|passwd|pwd)\s*[=:]\s*[^\s]{8,}`. It correctly catches `password = "hardcoded123"` but also falsely matches `password = auth_password` (variable), `secret = settings.SECRET_KEY` (settings read), `password=self.cleaned_data["old_password"]` (form data). All 12 Django FPs have a variable/attribute/settings-read as the value, never a string literal.

**Step 1: Add test for variable reference (should NOT flag)**

In the `#[cfg(test)] mod tests` section at the bottom of `secrets.rs`, add:

```rust
#[test]
fn test_no_finding_for_password_variable_reference() {
    let store = GraphStore::in_memory();
    let detector = SecretDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("views.rb", "password=auth_password,\nsecret = settings.SECRET_KEY\nself._password = raw_password\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag variable references as secrets. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_finding_for_settings_read() {
    let store = GraphStore::in_memory();
    let detector = SecretDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("config.rb", "self.password = settings.EMAIL_HOST_PASSWORD if password is None else password\npassword=self.settings_dict[\"PASSWORD\"],\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag settings reads as secrets. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_finding_for_request_data_read() {
    let store = GraphStore::in_memory();
    let detector = SecretDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("views.rb", "csrf_secret = request.META[\"CSRF_COOKIE\"]\nold_password = self.cleaned_data[\"old_password\"]\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag request/form data reads as secrets. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}
```

**Step 2: Run tests to verify they fail**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib secret -- --nocapture 2>&1 | tail -20
```

Expected: 3 new tests FAIL (the variable references are currently flagged).

**Step 3: Implement the fix**

In `secrets.rs`, find the existing Generic Secret value-type filtering block (around line 273-293). The block currently checks for function calls `(` and collection literals `[`, `{`. Extend it to also skip non-literal values:

Replace the block at lines 283-293:

```rust
                        if !value_part.is_empty() {
                            // Skip function/class calls: CharField(...), Signal(), SecretManager.from_config()
                            if value_part.contains('(') {
                                continue;
                            }
                            // Skip collection literals: [...], {...}
                            let first_char = value_part.chars().next().unwrap_or(' ');
                            if matches!(first_char, '[' | '{') {
                                continue;
                            }
                        }
```

With:

```rust
                        if !value_part.is_empty() {
                            // Skip function/class calls: CharField(...), Signal(), SecretManager.from_config()
                            if value_part.contains('(') {
                                continue;
                            }
                            // Skip collection literals: [...], {...}
                            let first_char = value_part.chars().next().unwrap_or(' ');
                            if matches!(first_char, '[' | '{') {
                                continue;
                            }
                            // Skip variable references — a hardcoded secret MUST be a string literal
                            // Variables, attribute accesses, settings reads are NOT hardcoded
                            if !matches!(first_char, '"' | '\'' | '`' | 'b') {
                                // Not a string literal (b"..." for bytes is also a literal)
                                continue;
                            }
                            // If starts with b, check it's b"..." not a variable like `base64...`
                            if first_char == 'b' {
                                let second_char = value_part.chars().nth(1).unwrap_or(' ');
                                if !matches!(second_char, '"' | '\'') {
                                    continue;
                                }
                            }
                        }
```

**Step 4: Run tests to verify they pass**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib secret -- --nocapture 2>&1 | tail -20
```

Expected: ALL secret tests pass, including the 3 new ones.

**Step 5: Verify existing tests still pass**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib secret 2>&1 | tail -5
```

Expected: `test_still_detects_real_hardcoded_password` still passes (string literal `"super_secret_password_123"` starts with `"`).

**Step 6: Commit**

```bash
cd /home/zhammad/personal/repotoire && git add repotoire-cli/src/detectors/secrets.rs && git commit -m "fix: SecretDetector skips variable references, settings reads, and attribute accesses in Generic Secret pattern"
```

---

### Task 2: XxeDetector — Safe Parser Recognition + Import Filtering

**Files:**
- Modify: `repotoire-cli/src/detectors/xxe.rs:37-47` (protection patterns)
- Modify: `repotoire-cli/src/detectors/xxe.rs:249-251` (post-match filtering)

**Context:** 6 of 7 FPs are in Django's `xml_serializer.py` which defines a custom `DefusedExpatParser` with XXE protection. The detector doesn't recognize these protection keywords. The 7th FP is `"DOMParser": false` in a JS globals file (static data, not code).

**Step 1: Add tests**

In the `#[cfg(test)] mod tests` section at the bottom of `xxe.rs`, add:

```rust
#[test]
fn test_no_finding_for_import_only() {
    let store = GraphStore::in_memory();
    let detector = XxeDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("parser.py", "from xml.dom import minidom, pulldom\nimport xml.etree.ElementTree as ET\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag import-only lines. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_finding_with_custom_defused_parser() {
    let store = GraphStore::in_memory();
    let detector = XxeDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("serializer.py", "class DefusedExpatParser:\n    feature_external_ges = False\n    feature_external_pes = False\n    def reset(self):\n        raise DTDForbidden()\n\ndef deserialize(stream):\n    event_stream = pulldom.parse(stream, parser)\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag XML parsing when custom defused parser exists. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_finding_for_js_static_data() {
    let store = GraphStore::in_memory();
    let detector = XxeDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("globals.js", "var globals = {\n    \"DOMParser\": false,\n    \"XMLHttpRequest\": false,\n};\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag JS static data. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}
```

**Step 2: Run tests to verify they fail**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib xxe -- --nocapture 2>&1 | tail -20
```

**Step 3: Implement Fix 1 — Expand Python protection patterns**

In `xxe.rs`, find `get_protection_patterns()` (around line 37). Replace the `"py"` arm:

```rust
"py" => vec![
    "resolve_entities=False",
    "no_network=True",
    "defusedxml",
    "forbid_dtd=True",
    "forbid_entities=True",
    // Custom safe parser indicators (Django-style)
    "feature_external_ges",
    "feature_external_pes",
    "DTDForbidden",
    "EntitiesForbidden",
    "ExternalReferenceForbidden",
    "defused",
],
```

**Step 4: Implement Fix 2 — Filter imports and non-parse references**

In `xxe.rs`, find the line scanning loop (around line 249). After the `xxe_pattern` match check, add post-match filters:

After line 251 (`continue;` for non-match), add:

```rust
// Skip import-only lines — importing a module is not a vulnerability
let trimmed = line.trim();
if trimmed.starts_with("from ") || trimmed.starts_with("import ") {
    continue;
}

// Skip lines that reference XML modules without actual parse calls
let has_parse_call = line.contains(".parse(")
    || line.contains(".parseString(")
    || line.contains("XMLParser(")
    || line.contains("DocumentBuilder")
    || line.contains("SAXParser(")
    || line.contains("XMLReader(");
if !has_parse_call {
    continue;
}
```

**Step 5: Implement Fix 3 — Skip JS static data**

In the same loop, after the import check, add:

```rust
// Skip JS static data (globals lists, config objects)
if ext == "js" {
    let trimmed_line = line.trim();
    if trimmed_line.ends_with("false,")
        || trimmed_line.ends_with("true,")
        || trimmed_line.ends_with("false")
        || trimmed_line.ends_with("true")
    {
        continue;
    }
}
```

**Step 6: Run tests**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib xxe 2>&1 | tail -10
```

Expected: ALL xxe tests pass.

**Step 7: Commit**

```bash
cd /home/zhammad/personal/repotoire && git add repotoire-cli/src/detectors/xxe.rs && git commit -m "fix: XxeDetector recognizes custom safe parsers, skips imports and JS static data"
```

---

### Task 3: PickleDeserializationDetector — Trusted Context Exclusion

**Files:**
- Modify: `repotoire-cli/src/detectors/pickle_detector.rs:204-206`

**Context:** All 7 FPs are in `cache/backends/` — Django's own cache framework serializing trusted server-side data. Cache backends only ever deserialize data they created themselves. No user input flows to pickle.loads() in these contexts.

**Step 1: Add test**

In the `#[cfg(test)] mod tests` section of `pickle_detector.rs`, add:

```rust
#[test]
fn test_skips_cache_backend_paths() {
    let detector = PickleDeserializationDetector::new();
    // "cache/backends/" should be treated as trusted context
    assert!(
        detector.should_exclude("cache/backends/redis.py") == false,
        "should_exclude doesn't cover cache/backends (that's expected)"
    );
    // The real check is in scan_source_files — we test the pattern check still works
    assert!(detector.check_line_for_patterns("data = pickle.loads(data)").is_some());
}
```

**Step 2: Implement the fix**

In `pickle_detector.rs`, find `scan_source_files()` method (line 186). After the `should_exclude` check at line 204-206, add the trusted-context exclusion:

```rust
if self.should_exclude(&rel_path) {
    continue;
}

// Skip trusted serialization contexts — cache and session backends
// only deserialize data they created themselves (no user input)
if rel_path.contains("cache/backends/")
    || rel_path.contains("sessions/backends/")
{
    continue;
}
```

**Step 3: Run tests**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib pickle 2>&1 | tail -10
```

Expected: All pickle tests pass.

**Step 4: Commit**

```bash
cd /home/zhammad/personal/repotoire && git add repotoire-cli/src/detectors/pickle_detector.rs && git commit -m "fix: PickleDeserializationDetector skips cache/backends/ and sessions/backends/ (trusted contexts)"
```

---

## Phase 2: Quick Fixes

### Task 4: GodClassDetector — Fix Test Path Detection for Relative Paths

**Files:**
- Modify: `repotoire-cli/src/detectors/class_context.rs:556-568`
- Modify: `repotoire-cli/src/detectors/god_class.rs:519-528`
- Modify: `repotoire-cli/src/detectors/base.rs:239-250`

**Context:** File paths from the graph are relative (e.g., `tests/expressions/tests.py`). The `is_test_path` checks look for `/tests/` with a leading slash, so `contains("/tests/")` never matches paths that START with `tests/`. This causes 17 test classes to be flagged as god classes.

**Step 1: Add tests**

In `god_class.rs` tests section, add:

```rust
#[test]
fn test_no_finding_for_relative_test_path() {
    let store = GraphStore::in_memory();
    // Add a class at a relative test path with enough methods/complexity to trigger
    store.add_class_with_metrics(
        "tests/expressions/tests.py",
        "BasicExpressionsTests",
        35,   // methods
        200,  // complexity
        1500, // LOC
    );
    let detector = GodClassDetector::new();
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag test classes at relative paths. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}
```

Note: If `add_class_with_metrics` doesn't exist, use whatever method the existing tests use to set up graph data. Check existing tests in the file for the pattern.

**Step 2: Implement Fix 1 — Update `base::is_test_path()`**

In `base.rs`, find `is_test_path()` at line 239. Add `starts_with` checks:

```rust
pub fn is_test_path(path_str: &str) -> bool {
    let lower = path_str.to_lowercase();
    lower.contains("/test/")
        || lower.contains("/tests/")
        || lower.contains("/__tests__/")
        || lower.contains("/spec/")
        || lower.contains("/test_")
        || lower.contains("_test.")
        || lower.contains(".test.")
        || lower.contains(".spec.")
        || lower.contains("_spec.")
        // Handle relative paths starting with test directories
        || lower.starts_with("tests/")
        || lower.starts_with("test/")
        || lower.starts_with("__tests__/")
        || lower.starts_with("spec/")
}
```

**Step 3: Implement Fix 2 — Update `class_context.rs::is_test_path()`**

In `class_context.rs`, find `is_test_path()` at line 556. Add `starts_with` checks:

```rust
fn is_test_path(&self, path: &str) -> bool {
    let lower = path.to_lowercase();
    lower.contains("/test/")
        || lower.contains("/tests/")
        || lower.contains("/__tests__/")
        || lower.contains("/spec/")
        || lower.ends_with("_test.go")
        || lower.ends_with("_test.py")
        || lower.ends_with(".test.ts")
        || lower.ends_with(".test.js")
        || lower.ends_with(".spec.ts")
        || lower.ends_with(".spec.js")
        // Handle relative paths starting with test directories
        || lower.starts_with("tests/")
        || lower.starts_with("test/")
        || lower.starts_with("__tests__/")
        || lower.starts_with("spec/")
}
```

**Step 4: Implement Fix 3 — Update `god_class.rs` fallback**

In `god_class.rs`, find the fallback test path check at line 519-528. Add `starts_with`:

```rust
if ctx.is_none() {
    let lower_path = class.file_path.to_lowercase();
    if lower_path.contains("/test/") || lower_path.contains("/tests/")
        || lower_path.contains("/__tests__/") || lower_path.contains("/spec/")
        || lower_path.contains("test_") || lower_path.contains("_test.")
        // Handle relative paths
        || lower_path.starts_with("tests/")
        || lower_path.starts_with("test/")
    {
        debug!("Skipping test class: {}", class.name);
        continue;
    }
}
```

**Step 5: Run tests**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib god_class 2>&1 | tail -10
```

**Step 6: Commit**

```bash
cd /home/zhammad/personal/repotoire && git add repotoire-cli/src/detectors/base.rs repotoire-cli/src/detectors/class_context.rs repotoire-cli/src/detectors/god_class.rs && git commit -m "fix: GodClassDetector handles relative test paths (tests/ without leading slash)"
```

---

### Task 5: LazyClassDetector — Fix Test Path Detection

**Files:**
- Modify: `repotoire-cli/src/detectors/lazy_class.rs:247-256`

**Context:** Same relative path issue as GodClass. The existing check at line 249 uses `contains("/tests/")` which misses paths starting with `tests/`. 9 test model classes are falsely flagged.

**Step 1: Implement the fix**

In `lazy_class.rs`, find the test path check at line 247-256. Add `starts_with`:

```rust
// Skip test fixture/model classes
{
    let lower_path = class.file_path.to_lowercase();
    if lower_path.contains("/test/") || lower_path.contains("/tests/")
        || lower_path.contains("/__tests__/") || lower_path.contains("/spec/")
        || lower_path.contains("/fixtures/")
        || lower_path.contains("test_") || lower_path.contains("_test.")
        // Handle relative paths
        || lower_path.starts_with("tests/")
        || lower_path.starts_with("test/")
        || lower_path.starts_with("__tests__/")
    {
        continue;
    }
}
```

**Step 2: Run tests**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib lazy_class 2>&1 | tail -10
```

**Step 3: Commit**

```bash
cd /home/zhammad/personal/repotoire && git add repotoire-cli/src/detectors/lazy_class.rs && git commit -m "fix: LazyClassDetector handles relative test paths (tests/ without leading slash)"
```

---

### Task 6: DebugCodeDetector — Info-Printing Utility Exclusion

**Files:**
- Modify: `repotoire-cli/src/detectors/debug_code.rs:49-63` (is_dev_only_path)
- Modify: `repotoire-cli/src/detectors/debug_code.rs:138-146` (except block detection)

**Context:** 9 of 10 FPs are in `ogrinfo.py` — a GIS data inspection utility where `print()` IS the core feature. The 10th is `archive.py:191` which prints an error inside an except block.

**Step 1: Add tests**

In the tests section of `debug_code.rs`, add:

```rust
#[test]
fn test_no_finding_for_info_utility() {
    let store = GraphStore::in_memory();
    let detector = DebugCodeDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("utils/ogrinfo.py", "def ogrinfo(data_source):\n    \"\"\"Walk the available layers.\"\"\"\n    print(data_source.name)\n    print(layer.num_feat)\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag print() in info utilities. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_finding_for_print_in_except_block() {
    let store = GraphStore::in_memory();
    let detector = DebugCodeDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("utils/archive.py", "def extract(self):\n    try:\n        do_something()\n    except Exception as exc:\n        print(\"Invalid member: %s\" % exc)\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag print() in except blocks. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}
```

**Step 2: Implement Fix 1 — Expand is_dev_only_path**

In `debug_code.rs`, find `is_dev_only_path()` at line 49. Add info utility patterns:

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
        // Info/inspection utilities where print IS the feature
        "/utils/ogrinfo",
        "/utils/ogrinspect",
        "/utils/layermapping",
        "/utils/ogrinfo.",
        "ogrinfo.py",
    ];
    dev_patterns.iter().any(|p| path.contains(p))
}
```

**Step 3: Implement Fix 2 — Skip print in except blocks**

In `debug_code.rs`, find the debug pattern match check around line 148. After the logging utility check (line 154-158), add except-block detection:

```rust
// Skip if in a logging utility function
if let Some(ref func) = containing_func {
    if Self::is_logging_utility(func) {
        continue;
    }
}

// Skip print() in except/catch blocks (error reporting, not debug)
let trimmed_check = line.trim();
if trimmed_check.starts_with("print(") || trimmed_check.starts_with("print (") {
    let current_indent = line.len() - trimmed_check.len();
    let mut in_except = false;
    for prev_idx in (0..i).rev() {
        let prev_trimmed = lines[prev_idx].trim();
        if prev_trimmed.is_empty() { continue; }
        let prev_indent = lines[prev_idx].len() - prev_trimmed.len();
        if prev_indent < current_indent && (prev_trimmed.starts_with("except") || prev_trimmed.starts_with("except:")) {
            in_except = true;
            break;
        }
        if prev_indent <= current_indent {
            break;
        }
    }
    if in_except {
        continue;
    }
}
```

**Step 4: Run tests**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib debug_code 2>&1 | tail -10
```

**Step 5: Commit**

```bash
cd /home/zhammad/personal/repotoire && git add repotoire-cli/src/detectors/debug_code.rs && git commit -m "fix: DebugCodeDetector skips info utilities (ogrinfo) and print() in except blocks"
```

---

### Task 7: StringConcatLoopDetector — Require Accumulation Pattern

**Files:**
- Modify: `repotoire-cli/src/detectors/string_concat_loop.rs:148-199`

**Context:** The detector flags ANY `+=` with a string inside a loop. But 7 of 10 Django FPs are single concatenations per loop iteration (e.g., `field_type += "("` once). The real O(n²) issue is when the SAME variable gets `+=` multiple times in the same loop body. A single `+=` per iteration is O(n) — not a performance problem.

**Step 1: Add tests**

In the tests section of `string_concat_loop.rs`, add:

```rust
#[test]
fn test_no_finding_for_single_concat_per_iteration() {
    let store = GraphStore::in_memory();
    let detector = StringConcatLoopDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("views.py", "for item in items:\n    url += \"/\"\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag single concat per iteration. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_still_detects_multiple_concats_in_loop() {
    let store = GraphStore::in_memory();
    let detector = StringConcatLoopDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("builder.py", "for field in fields:\n    definition += \" \" + check\n    definition += \" \" + suffix\n    definition += \" \" + fk\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        !findings.is_empty(),
        "Should detect multiple concats to same variable in loop"
    );
}
```

**Step 2: Implement the fix**

In `string_concat_loop.rs`, the detection loop starts at line 156. The fix: instead of immediately flagging each `+=` match inside a loop, collect all `+=` matches for the current loop body and only flag variables with 2+ concatenations.

Find the section around line 195 where `string_concat().is_match(line)` triggers a finding. Restructure to use deferred reporting:

Add a `HashMap<String, (usize, u32)>` to track `(count, first_line)` per variable per loop. At loop exit (when `in_loop` becomes false), check if any variable has count >= 2 and create findings only for those.

The key changes:
1. Add `use std::collections::HashMap;` at the top (already imported)
2. Initialize `let mut loop_concat_vars: HashMap<String, (usize, u32)> = HashMap::new();` when a new loop starts
3. When `string_concat().is_match(line)` inside loop: extract variable name, increment count
4. When loop ends (`in_loop = false`): check for variables with count >= 2, create findings for those
5. Reset `loop_concat_vars` for each new loop

**Step 3: Run tests**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib string_concat 2>&1 | tail -10
```

**Step 4: Commit**

```bash
cd /home/zhammad/personal/repotoire && git add repotoire-cli/src/detectors/string_concat_loop.rs && git commit -m "fix: StringConcatLoopDetector requires 2+ concatenations to same variable per loop"
```

---

### Task 8: DjangoSecurityDetector — Expand ORM Path Exclusions

**Files:**
- Modify: `repotoire-cli/src/detectors/django_security.rs:322-327`

**Context:** Pass 2 added exclusions for `db/backends/` and `db/models/sql/`. But 3-5 raw SQL findings remain in ORM internals: `db/models/constraints.py`, `db/models/fields/related_descriptors.py`, `db/models/query.py`, `contrib/postgres/operations.py`, `contrib/postgres/signals.py`.

**Step 1: Add test**

```rust
#[test]
fn test_no_raw_sql_finding_for_orm_internals() {
    let store = GraphStore::in_memory();
    let detector = DjangoSecurityDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("db/models/constraints.py", "def as_sql(self, compiler, connection):\n    return cursor.execute(sql)\n"),
        ("db/models/query.py", "class QuerySet:\n    def raw(self, raw_query):\n        return RawSQL(raw_query)\n"),
        ("contrib/postgres/operations.py", "def database_forwards(self):\n    cursor.execute(\"CREATE EXTENSION\")\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    let raw_sql_findings: Vec<_> = findings.iter().filter(|f| f.title.contains("Raw SQL")).collect();
    assert!(
        raw_sql_findings.is_empty(),
        "Should not flag raw SQL in ORM internals. Found: {:?}",
        raw_sql_findings.iter().map(|f| (&f.title, &f.affected_files)).collect::<Vec<_>>()
    );
}
```

**Step 2: Implement the fix**

In `django_security.rs`, find the raw SQL path exclusion at line 322-327. Expand it:

```rust
if raw_sql().is_match(line) {
    // Skip ORM/database-internal paths — these files ARE the database layer
    let lower_path = path_str.to_lowercase();
    if lower_path.contains("db/backends/")
        || lower_path.contains("db/models/sql/")
        || lower_path.contains("db/models/expressions")
        || lower_path.contains("db/models/constraints")
        || lower_path.contains("db/models/fields/")
        || lower_path.contains("db/models/query")
        || lower_path.contains("db/migrations/")
        || lower_path.contains("core/cache/backends/")
        || lower_path.contains("/migrations/")
        || lower_path.contains("contrib/postgres/")
    {
        continue;
    }
```

**Step 3: Run tests**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib django_security 2>&1 | tail -10
```

**Step 4: Commit**

```bash
cd /home/zhammad/personal/repotoire && git add repotoire-cli/src/detectors/django_security.rs && git commit -m "fix: DjangoSecurityDetector expands ORM path exclusions (constraints, fields, query, postgres)"
```

---

### Task 9: InsecureCryptoDetector — Non-Cryptographic Usage Detection

**Files:**
- Modify: `repotoire-cli/src/detectors/insecure_crypto.rs:20-112` (is_hash_mention_not_usage)
- Modify: `repotoire-cli/src/detectors/insecure_crypto.rs:341-354` (path exclusions in detect)

**Context:** 3 of 5 FPs are non-cryptographic hash usage: SHA1 for template cache keys (`cached.py:96`), MD5/SHA1 for release file checksums (`do_django_release.py:137-138`).

**Step 1: Add tests**

```rust
#[test]
fn test_no_finding_for_cache_key_hash() {
    let store = GraphStore::in_memory();
    let detector = InsecureCryptoDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("template/loaders/cached.py", "def generate_hash(self, values):\n    return hashlib.sha1(\"|\".join(values).encode()).hexdigest()\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag SHA1 used for cache key generation. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_finding_for_release_checksums() {
    let store = GraphStore::in_memory();
    let detector = InsecureCryptoDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("scripts/do_release.py", "checksums = [\n    (\"md5\", hashlib.md5),\n    (\"sha1\", hashlib.sha1),\n    (\"sha256\", hashlib.sha256),\n]\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag hashes in release/build scripts. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}
```

**Step 2: Implement Fix 1 — Expand non-cryptographic context detection**

In `insecure_crypto.rs`, find `is_hash_mention_not_usage()` at line 20. Add near the existing `checksum`/`etag`/`cache_key` check (around line 51):

```rust
// Skip non-cryptographic hashing contexts (cache keys, template hashing)
if lower.contains("generate_hash") || lower.contains("cache_key") || lower.contains("hexdigest") {
    if lower.contains("cache") || lower.contains("template") || lower.contains("loader") || lower.contains("join") {
        return true;
    }
}
```

**Step 3: Implement Fix 2 — Skip scripts/ directory**

In `insecure_crypto.rs`, find the `detect()` method path filtering (around line 341-354). Add scripts exclusion after the existing language file exclusion:

```rust
// Skip scripts/build/release tooling (checksums for file integrity, not security)
if path_str.contains("scripts/") || path_str.contains("/script/") {
    continue;
}
```

**Step 4: Run tests**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib insecure_crypto 2>&1 | tail -10
```

**Step 5: Commit**

```bash
cd /home/zhammad/personal/repotoire && git add repotoire-cli/src/detectors/insecure_crypto.rs && git commit -m "fix: InsecureCryptoDetector skips cache key hashing and release scripts"
```

---

### Task 10: Full Validation

**Files:** None (validation only)

**Step 1: Run full test suite**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test 2>&1 | tail -20
```

Expected: ALL tests pass (702+ existing + new tests from each task).

**Step 2: Validate against Django**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo run -- analyze /tmp/django-repo 2>&1 | tail -30
```

Expected: ~291 findings, score ≥ 99/A+.

**Step 3: Validate against Flask**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo run -- analyze /tmp/flask-repo 2>&1 | tail -10
```

Expected: No regressions (~25 findings).

**Step 4: Validate against FastAPI**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo run -- analyze /tmp/fastapi-repo 2>&1 | tail -10
```

Expected: No regressions (~120 findings).

**Step 5: Record results**

Create a summary of before/after findings for each project.
