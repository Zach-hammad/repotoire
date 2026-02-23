# Detector Accuracy Pass Implementation Plan (Revised)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate false positives across 8 detectors by fixing overly-broad regex, adding framework idiom awareness, and improving Python import semantics.

**Architecture:** Each detector is modified independently. Changes are minimal and targeted -- only fixes verified against actual detector code. Each fix gets unit tests proving both TP preservation and FP elimination.

**Tech Stack:** Rust, regex crate, cargo test

**Revision Notes:** This plan was rewritten after rigorous verification of the original. Two original fixes (DjangoSecurity "skip definitions" and EvalDetector "skip compile()") were targeting non-existent problems. Six others needed adjustment. All fixes below have been verified against the actual source code.

---

### Task 1: Fix EmptyCatchDetector -- Tiered Handling of Exception Types

**Files:**
- Modify: `repotoire-cli/src/detectors/empty_catch.rs:139-147` (Python except detection)
- Test: inline `#[cfg(test)]` module at bottom of same file

**Context:** The detector flags all `except X: pass` equally. But `except ImportError: pass` is a well-known Python idiom for optional imports and should be fully skipped. Others like `except KeyError: pass` are sometimes intentional but can hide bugs -- downgrade to Low instead of skipping.

**Step 1: Write the FP-elimination tests**

Add to the `mod tests` block in `empty_catch.rs`:

```rust
#[test]
fn test_no_finding_for_except_importerror_pass() {
    let store = GraphStore::in_memory();
    let detector = EmptyCatchDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("optional.py", "try:\n    import yaml\nexcept ImportError:\n    pass\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag except ImportError: pass (optional import idiom). Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_low_severity_for_except_keyerror_pass() {
    let store = GraphStore::in_memory();
    let detector = EmptyCatchDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("lookup.py", "try:\n    value = cache[key]\nexcept KeyError:\n    pass\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        !findings.is_empty(),
        "Should still flag except KeyError: pass (can hide bugs)"
    );
    assert_eq!(
        findings[0].severity,
        Severity::Low,
        "except KeyError: pass should be Low severity (common idiom)"
    );
}

#[test]
fn test_still_detects_bare_except_pass() {
    let store = GraphStore::in_memory();
    let detector = EmptyCatchDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("bad.py", "try:\n    do_something()\nexcept:\n    pass\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        !findings.is_empty(),
        "Should still detect bare except: pass"
    );
    assert_ne!(
        findings[0].severity,
        Severity::Low,
        "Bare except: pass should NOT be Low severity"
    );
}

#[test]
fn test_still_detects_except_exception_pass() {
    let store = GraphStore::in_memory();
    let detector = EmptyCatchDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("bad.py", "try:\n    do_something()\nexcept Exception:\n    pass\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        !findings.is_empty(),
        "Should still detect except Exception: pass (too broad)"
    );
    assert_ne!(
        findings[0].severity,
        Severity::Low,
        "except Exception: pass should NOT be Low severity"
    );
}
```

**Step 2: Run tests to verify new tests fail**

Run: `cd repotoire-cli && cargo test --lib empty_catch -- --nocapture 2>&1 | tail -20`
Expected: `test_no_finding_for_except_importerror_pass` and `test_low_severity_for_except_keyerror_pass` FAIL

**Step 3: Implement the fix**

In `empty_catch.rs`, replace the Python except detection block (lines 139-147) with:

```rust
// Python: except: followed by pass
if ext == "py" && trimmed.starts_with("except") && trimmed.ends_with(":") {
    if let Some(next) = lines.get(i + 1) {
        let next_trimmed = next.trim();
        if next_trimmed == "pass" || next_trimmed == "..." {
            // Extract exception type from "except SomeError:" or "except (A, B):"
            let except_body = trimmed
                .strip_prefix("except")
                .unwrap_or("")
                .strip_suffix(":")
                .unwrap_or("")
                .trim();

            // Fully skip optional import idioms -- these are NEVER bugs
            let skip_entirely = [
                "ImportError", "ModuleNotFoundError",
            ];
            let should_skip = !except_body.is_empty()
                && skip_entirely.iter().any(|e| except_body.contains(e));

            if should_skip {
                // Don't flag at all -- well-known Python idiom
            } else {
                is_empty_catch = true;

                // Downgrade common specific-exception idioms to Low severity
                // These CAN hide bugs but are often intentional
                let common_idioms = [
                    "KeyError", "AttributeError", "StopIteration",
                    "FileNotFoundError", "NotImplementedError",
                    "OSError", "PermissionError", "FileExistsError",
                    "ProcessLookupError", "TypeError", "ValueError",
                ];
                if !except_body.is_empty()
                    && common_idioms.iter().any(|e| except_body.contains(e))
                {
                    // Mark for Low severity -- will be set below
                    // We set a flag here to override the risk-based severity
                    is_common_idiom = true;
                }
            }
        }
    }
}
```

Also add `let mut is_common_idiom = false;` next to `let mut is_empty_catch = false;` (line 136), and reset it each iteration.

In the finding creation block (after the severity is computed around line 160-173), add:

```rust
// Override severity for common exception idioms
let severity = if is_common_idiom {
    Severity::Low
} else {
    severity
};
```

**Step 4: Run tests to verify all pass**

Run: `cd repotoire-cli && cargo test --lib empty_catch -- --nocapture 2>&1 | tail -20`
Expected: ALL tests pass

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/empty_catch.rs
git commit -m "fix: EmptyCatchDetector skips ImportError, downgrades common idioms to Low"
```

---

### Task 2: Fix StringConcatLoopDetector -- Remove Overly-Broad Regex Alternative

**Files:**
- Modify: `repotoire-cli/src/detectors/string_concat_loop.rs:34-39` (regex only)
- Test: inline `#[cfg(test)]` module

**Context:** The third regex alternative `\w+\s*\+=\s*\w+` matches ALL `+=` operations including numeric accumulation (`count += 1`, `total += item.price`). This is the main FP source (50 findings in Django). Removing it leaves only the first two alternatives that require a string literal on the RHS.

**Important:** Do NOT add Python indentation tracking in this change. The brace-depth tracking is wrong for Python, but fixing it is a separate, more complex change. The regex fix alone eliminates the majority of FPs.

**Step 1: Write the FP-elimination tests**

Add to `mod tests`:

```rust
#[test]
fn test_no_finding_for_numeric_accumulation() {
    let store = GraphStore::in_memory();
    let detector = StringConcatLoopDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("calc.py", "def total_price(items):\n    total = 0\n    for item in items:\n        total += item.price\n    return total\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag numeric accumulation (total += item.price). Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_finding_for_counter_increment() {
    let store = GraphStore::in_memory();
    let detector = StringConcatLoopDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("count.py", "def count_active(users):\n    count = 0\n    for user in users:\n        count += 1\n    return count\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag counter increment (count += 1). Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_still_detects_string_literal_concat_in_loop() {
    let store = GraphStore::in_memory();
    let detector = StringConcatLoopDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("build.py", "def build(items):\n    result = \"\"\n    for item in items:\n        result += \"prefix_\"\n    return result\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        !findings.is_empty(),
        "Should still detect string literal concat in loop"
    );
}

#[test]
fn test_still_detects_string_concat_with_plus() {
    let store = GraphStore::in_memory();
    let detector = StringConcatLoopDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("build.py", "def build(items):\n    result = \"\"\n    for item in items:\n        result = result + \"value\"\n    return result\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        !findings.is_empty(),
        "Should still detect result = result + 'value' in loop"
    );
}
```

**Step 2: Run tests to verify new FP tests fail**

Run: `cd repotoire-cli && cargo test --lib string_concat_loop -- --nocapture 2>&1 | tail -20`
Expected: `test_no_finding_for_numeric_accumulation` and `test_no_finding_for_counter_increment` FAIL

**Step 3: Implement the fix**

In `string_concat_loop.rs`, replace the `string_concat()` function (lines 34-39):

```rust
fn string_concat() -> &'static Regex {
    STRING_CONCAT.get_or_init(|| {
        // Only match when RHS starts with a string literal quote or f-string
        // Removed: \w+\s*\+=\s*\w+ (matched ALL += including count += 1)
        Regex::new(r#"\w+\s*\+=\s*["'`f]|\w+\s*=\s*\w+\s*\+\s*["'`f]"#)
            .expect("valid regex")
    })
}
```

**Step 4: Run tests to verify all pass**

Run: `cd repotoire-cli && cargo test --lib string_concat_loop -- --nocapture 2>&1 | tail -20`
Expected: ALL tests pass

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/string_concat_loop.rs
git commit -m "fix: StringConcatLoopDetector removes overly-broad regex that matched all += operations"
```

---

### Task 3: Fix UnusedImportsDetector -- TYPE_CHECKING Block Tracking + Multi-line Imports

**Files:**
- Modify: `repotoire-cli/src/detectors/unused_imports.rs:209-265` (detect method)
- Test: inline `#[cfg(test)]` module

**Context:** The detector's TYPE_CHECKING handling (line 235-238) only skips the line containing "TYPE_CHECKING" itself, not the indented import block underneath. So `from models import User` inside a `if TYPE_CHECKING:` block gets flagged if `User` only appears in type annotations. Also, multi-line imports `from X import (\n    A,\n    B\n)` only parse the first line, missing subsequent names (false negative, but fixing it makes the detector more complete).

**Note:** The `__all__` regex already handles multiline correctly -- `[^\]]` in Rust regex matches newlines. No `(?s)` change needed.

**Step 1: Write the FP-elimination tests**

Add to `mod tests`:

```rust
#[test]
fn test_no_finding_for_type_checking_block() {
    let store = GraphStore::in_memory();
    let detector = UnusedImportsDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("typed.py", "from __future__ import annotations\nfrom typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from models import User\n\ndef greet(user: \"User\") -> str:\n    return f\"Hello {user.name}\"\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    // Filter to only UnusedImportsDetector findings about User
    let user_findings: Vec<_> = findings.iter()
        .filter(|f| f.title.contains("User"))
        .collect();
    assert!(
        user_findings.is_empty(),
        "Should not flag imports inside TYPE_CHECKING block. Found: {:?}",
        user_findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_handles_multiline_import() {
    let store = GraphStore::in_memory();
    let detector = UnusedImportsDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("views.py", "from django.db.models import (\n    CharField,\n    IntegerField,\n)\n\nname = CharField(max_length=100)\nage = IntegerField()\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        findings.is_empty(),
        "Should handle multi-line imports (CharField and IntegerField are used). Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_still_detects_unused_import() {
    let store = GraphStore::in_memory();
    let detector = UnusedImportsDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("unused.py", "import os\nimport sys\n\nprint(sys.argv)\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        !findings.is_empty(),
        "Should still detect unused import (os)"
    );
}
```

**Step 2: Run tests to verify FP tests fail**

Run: `cd repotoire-cli && cargo test --lib unused_imports -- --nocapture 2>&1 | tail -20`
Expected: `test_no_finding_for_type_checking_block` and `test_handles_multiline_import` FAIL

**Step 3: Implement the fixes**

3a. Replace the single-line TYPE_CHECKING check (lines 235-238) with block tracking. Before the main `for (line_num, line) in lines.iter().enumerate()` loop (around line 213), add:

```rust
let mut in_type_checking = false;
let mut type_checking_indent: usize = 0;
```

At the top of the loop body (after the comment/noqa checks, around line 233), replace the old TYPE_CHECKING check with:

```rust
// Track TYPE_CHECKING blocks -- skip all indented lines within
if trimmed == "if TYPE_CHECKING:" || trimmed.starts_with("if TYPE_CHECKING:") {
    in_type_checking = true;
    type_checking_indent = line.len() - line.trim_start().len();
    continue;
}
if in_type_checking {
    let current_indent = line.len() - line.trim_start().len();
    if !trimmed.is_empty() && current_indent <= type_checking_indent {
        in_type_checking = false;
        // Fall through to process this line normally
    } else {
        continue; // Skip lines inside TYPE_CHECKING block
    }
}
```

Remove the old check at lines 235-238:
```rust
// DELETE THIS:
// if trimmed.contains("TYPE_CHECKING") {
//     continue;
// }
```

3b. Add multi-line import handling. Replace the import extraction section (around lines 240-244) with:

```rust
let imports = if ext == "py" {
    if trimmed.starts_with("import ") || trimmed.starts_with("from ") {
        // Handle multi-line imports: from X import (\n    A,\n    B,\n)
        let effective_line = if trimmed.contains("(") && !trimmed.contains(")") {
            let mut accumulated = trimmed.to_string();
            let mut j = line_num + 1;
            while j < lines.len() {
                let continuation = lines[j].trim();
                accumulated.push(' ');
                accumulated.push_str(continuation);
                if continuation.contains(")") {
                    break;
                }
                j += 1;
            }
            accumulated
        } else {
            trimmed.to_string()
        };
        Self::extract_python_imports(&effective_line)
    } else {
        continue;
    }
} else if trimmed.starts_with("import ") {
    // existing JS/TS handling...
```

Keep the existing JS/TS handling unchanged after the else-if.

**Step 4: Run tests to verify all pass**

Run: `cd repotoire-cli && cargo test --lib unused_imports -- --nocapture 2>&1 | tail -20`
Expected: ALL tests pass

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/unused_imports.rs
git commit -m "fix: UnusedImportsDetector tracks TYPE_CHECKING blocks and handles multi-line imports"
```

---

### Task 4: Fix WildcardImportsDetector -- Skip Only Relative Imports in __init__.py

**Files:**
- Modify: `repotoire-cli/src/detectors/wildcard_imports.rs:89-101`
- Test: inline `#[cfg(test)]` module

**Context:** In `__init__.py`, wildcard imports for re-exports are standard Python: `from .models import *`. But absolute wildcard imports like `from os.path import *` in `__init__.py` are still bad. The fix should only skip relative imports (those starting with `.`) in `__init__.py`.

**Important:** Do NOT skip `conftest.py` -- pytest conftest files should use explicit imports.

**Step 1: Write the FP-elimination tests**

Add to `mod tests`:

```rust
#[test]
fn test_no_finding_for_relative_import_in_init_py() {
    let store = GraphStore::in_memory();
    let detector = WildcardImportsDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("__init__.py", "from .models import *\nfrom .views import *\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag relative wildcard imports in __init__.py (re-export pattern). Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_still_detects_absolute_import_in_init_py() {
    let store = GraphStore::in_memory();
    let detector = WildcardImportsDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("__init__.py", "from os.path import *\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        !findings.is_empty(),
        "Should still detect absolute wildcard imports in __init__.py"
    );
}

#[test]
fn test_still_detects_wildcard_in_regular_file() {
    let store = GraphStore::in_memory();
    let detector = WildcardImportsDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("app.py", "from os.path import *\nresult = join('/tmp', 'file')\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(!findings.is_empty(), "Should still detect wildcard in regular files");
}
```

**Step 2: Run tests to verify FP tests fail**

Run: `cd repotoire-cli && cargo test --lib wildcard_imports -- --nocapture 2>&1 | tail -20`
Expected: `test_no_finding_for_relative_import_in_init_py` FAILS

**Step 3: Implement the fix**

In the `detect()` method, in the inner loop where `wildcard_pattern().is_match(line)` is checked (around line 102), add a skip before creating the finding:

```rust
if wildcard_pattern().is_match(line) {
    // Skip relative wildcard imports in __init__.py -- standard re-export pattern
    let is_init_py = path.file_name()
        .and_then(|n| n.to_str())
        .map(|n| n == "__init__.py")
        .unwrap_or(false);
    if is_init_py {
        let import_trimmed = line.trim();
        // Relative imports start with "from ." or "from .."
        if import_trimmed.starts_with("from .") {
            continue;
        }
    }

    // ... rest of existing detection logic (extract module name, find symbols, create finding)
```

**Step 4: Run tests**

Run: `cd repotoire-cli && cargo test --lib wildcard_imports -- --nocapture 2>&1 | tail -20`
Expected: ALL pass

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/wildcard_imports.rs
git commit -m "fix: WildcardImportsDetector skips relative wildcard imports in __init__.py"
```

---

### Task 5: Fix CommentedCodeDetector -- Strong/Weak Pattern Split + License Headers

**Files:**
- Modify: `repotoire-cli/src/detectors/commented_code.rs:40-71` (looks_like_code), `155-159` (detection loop)
- Test: inline `#[cfg(test)]` module

**Context:** `looks_like_code()` currently returns true if ANY of 27 patterns match, including `=` alone. This flags comments like `# timeout = 30 seconds` and `# The default buffer size = 4096`. The fix splits patterns into strong (almost certainly code) and weak (common in prose), requiring at least one strong indicator. Also adds license/copyright header detection.

**Step 1: Write the FP-elimination tests**

Add to `mod tests`:

```rust
#[test]
fn test_no_finding_for_license_header() {
    let store = GraphStore::in_memory();
    let detector = CommentedCodeDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("licensed.py", "# Copyright (c) 2024 Django Software Foundation\n# All rights reserved.\n# Permission is hereby granted, free of charge,\n# to any person obtaining a copy of this software\n# and associated documentation files (the \"Software\"),\n# to deal in the Software without restriction.\n\ndef main():\n    pass\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag license headers as commented code. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_finding_for_technical_comments_with_equals() {
    let store = GraphStore::in_memory();
    let detector = CommentedCodeDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("doc.py", "# The default timeout = 30 seconds for all connections.\n# Each worker handles requests independently.\n# When count = 0, the queue is considered empty.\n# The maximum retry count = 3 before giving up.\n# Buffer size = 4096 bytes is optimal for most cases.\n# Connection pool size = 10 is the recommended default.\n\ndef process():\n    pass\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag technical docs (contain '=' but aren't code). Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_still_detects_real_commented_code() {
    let store = GraphStore::in_memory();
    let detector = CommentedCodeDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("dead.py", "def active():\n    pass\n\n# def old_function():\n#     x = compute()\n#     if x > 0:\n#         return process(x)\n#     else:\n#         return fallback()\n\ndef another():\n    pass\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        !findings.is_empty(),
        "Should still detect real commented-out code"
    );
}
```

**Step 2: Run tests to verify FP tests fail**

Run: `cd repotoire-cli && cargo test --lib commented_code -- --nocapture 2>&1 | tail -20`

**Step 3: Implement the fix**

3a. Replace `looks_like_code()` (lines 40-71) with a strong/weak pattern split:

```rust
fn looks_like_code(line: &str) -> bool {
    // Strong indicators: almost certainly code, not prose
    let strong_indicators = [
        "def ", "fn ", "function ", "class ", "import ", "from ",
        "return ", "const ", "let ", "var ",
        "==", "!=", "&&", "||", "->", "=>",
        "+=", "-=",
    ];

    // A line must have at least one strong indicator to be considered code.
    // Weak indicators (=, (), {}, [], ;) are common in prose and documentation
    // comments like "# timeout = 30 seconds" and should not count alone.
    strong_indicators.iter().any(|p| line.contains(p))
}
```

3b. Add license/copyright detection. Add a new helper:

```rust
fn is_license_comment(line: &str) -> bool {
    let upper = line.to_uppercase();
    upper.contains("COPYRIGHT") || upper.contains("LICENSE")
        || upper.contains("PERMISSION IS HEREBY GRANTED")
        || upper.contains("ALL RIGHTS RESERVED")
        || upper.contains("SPDX-LICENSE")
        || upper.contains("WARRANTY")
        || upper.contains("REDISTRIBUTION")
}
```

In the detection loop (around line 155-159), add license comment skip alongside the annotation check:

```rust
// Skip annotation and license comments
if is_comment && (Self::is_annotation_comment(line) || Self::is_license_comment(line)) {
    i += 1;
    continue;
}
```

**Step 4: Run tests**

Run: `cd repotoire-cli && cargo test --lib commented_code -- --nocapture 2>&1 | tail -20`
Expected: ALL pass

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/commented_code.rs
git commit -m "fix: CommentedCodeDetector uses strong/weak pattern split and skips license headers"
```

---

### Task 6: Fix DjangoSecurityDetector -- Fix is_test_path + Remove Broad has_user_input

**Files:**
- Modify: `repotoire-cli/src/detectors/django_security.rs:216` (DEBUG is_test_path), `258-265` (SECRET_KEY), `322-327` (has_user_input)
- Test: inline `#[cfg(test)]` module

**Context:** Verification found that 2 of 4 original sub-fixes were targeting non-existent problems:
- `def raw(` does NOT match the regex `\.raw\(` (requires literal dot prefix) -- no fix needed
- Parameterized `cursor.execute` already gets Medium severity (appropriate) -- no fix needed

The valid fixes are:
1. `is_test_path(fname)` uses only the filename, not full path -- `tests/settings.py` has fname `settings.py` which doesn't match
2. SECRET_KEY check has no `is_test_path` guard at all
3. `has_user_input` includes `"+ "` which is too broad -- catches literal string concatenation

**Step 1: Write the FP-elimination tests**

Add to `mod tests`:

```rust
#[test]
fn test_no_finding_for_debug_in_test_settings() {
    let store = GraphStore::in_memory();
    let detector = DjangoSecurityDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("tests/settings.py", "DEBUG = True\nSECRET_KEY = 'test-secret-key-for-testing'\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag DEBUG=True or SECRET_KEY in test settings. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_raw_sql_concat_not_critical_without_user_input() {
    let store = GraphStore::in_memory();
    let detector = DjangoSecurityDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("queries.py", "def get_table_data(table_name):\n    return Model.objects.raw(\"SELECT * FROM \" + table_name)\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    let raw_sql: Vec<_> = findings.iter().filter(|f| f.title.contains("Raw SQL")).collect();
    assert!(
        !raw_sql.is_empty(),
        "Should still detect raw SQL usage"
    );
    // Without request./f-string/.format(), it should NOT be Critical
    assert!(
        !raw_sql.iter().any(|f| f.severity == Severity::Critical),
        "Raw SQL with '+ ' alone (no request./f-string) should not be Critical. Got: {:?}",
        raw_sql.iter().map(|f| (&f.title, &f.severity)).collect::<Vec<_>>()
    );
}

#[test]
fn test_still_detects_debug_in_production_settings() {
    let store = GraphStore::in_memory();
    let detector = DjangoSecurityDetector::new("/mock/repo");
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("settings.py", "import os\n\nDEBUG = True\nALLOWED_HOSTS = []\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        findings.iter().any(|f| f.title.contains("DEBUG")),
        "Should still detect DEBUG = True in production settings"
    );
}
```

**Step 2: Run tests to verify FP tests fail**

Run: `cd repotoire-cli && cargo test --lib django_security -- --nocapture 2>&1 | tail -20`
Expected: `test_no_finding_for_debug_in_test_settings` and `test_raw_sql_concat_not_critical_without_user_input` FAIL

**Step 3: Implement the fixes**

3a. Fix `is_test_path` in DEBUG check -- use full path instead of filename (line 216):

```rust
// Before:
&& !crate::detectors::base::is_test_path(fname)
// After:
&& !crate::detectors::base::is_test_path(&path_str)
```

3b. Add `is_test_path` check to SECRET_KEY (line 259-265). Add to the condition chain:

```rust
if secret_key().is_match(line)
    && !line.contains("os.environ")
    && !line.contains("env(")
    && fname.contains("settings")
    && !fname.contains("dev")
    && !fname.contains("local")
    && !crate::detectors::base::is_test_path(&path_str)
```

3c. Remove `"+ "` from `has_user_input` (line 322-327):

```rust
let has_user_input = line.contains("request.")
    || line.contains("f\"")
    || line.contains("f'")
    || line.contains(".format(");
// Removed: || line.contains("+ ") -- too broad, catches literal string concatenation
```

**Step 4: Run tests**

Run: `cd repotoire-cli && cargo test --lib django_security -- --nocapture 2>&1 | tail -20`
Expected: ALL pass

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/django_security.rs
git commit -m "fix: DjangoSecurityDetector uses full path for is_test_path, removes broad has_user_input check"
```

---

### Task 7: Fix SecretDetector -- Skip Function Calls and Collection Literals

**Files:**
- Modify: `repotoire-cli/src/detectors/secrets.rs:201-229` (false positive filtering in `scan_file`)
- Test: inline `#[cfg(test)]` module

**Context:** The "Generic Secret" pattern `(?i)(secret|password|passwd|pwd)\s*[=:]\s*[^\s]{8,}` matches Django settings like `PASSWORD_HASHERS = [...]` and `password_field = CharField(...)`. The fix skips values that are clearly not secrets: function/class calls (contain `(`) and collection literals (start with `[` or `{`).

**Important:** Do NOT skip values starting with uppercase or digits -- `password = HARDCODED_CONSTANT` is a real finding that should not be missed.

**Step 1: Write the FP-elimination tests**

Add to `mod tests`:

```rust
#[test]
fn test_no_finding_for_password_field_definition() {
    let store = GraphStore::in_memory();
    let detector = SecretDetector::new("/mock/repo");
    // Use .rb -- no tree-sitter masking, content passes through
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("models.rb", "password_field = CharField(max_length=128)\nsecret_backends = SecretManager.from_config(settings)\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag function/class calls as secrets. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_finding_for_password_list_assignment() {
    let store = GraphStore::in_memory();
    let detector = SecretDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("config.rb", "PASSWORD_HASHERS = [\"django.contrib.auth.hashers.PBKDF2PasswordHasher\"]\nSECRET_KEY_FALLBACKS = []\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag list literal assignments as secrets. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_still_detects_real_hardcoded_password() {
    let store = GraphStore::in_memory();
    let detector = SecretDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("config.rb", "password = \"super_secret_password_123\"\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        !findings.is_empty(),
        "Should still detect real hardcoded password"
    );
}

#[test]
fn test_still_detects_uppercase_constant_password() {
    let store = GraphStore::in_memory();
    let detector = SecretDetector::new("/mock/repo");
    let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("config.rb", "password = HARDCODED_SECRET_VALUE\n"),
    ]);
    let findings = detector.detect(&store, &mock_files).unwrap();
    assert!(
        !findings.is_empty(),
        "Should still detect password assigned to uppercase constant"
    );
}
```

**Step 2: Run tests to verify FP tests fail**

Run: `cd repotoire-cli && cargo test --lib secrets -- --nocapture 2>&1 | tail -20`

**Step 3: Implement the fix**

In `scan_file()`, after the existing false positive filtering (after line 229, before the effective severity logic), add:

```rust
// Value-type filtering for Generic Secret pattern:
// Skip when the value is clearly not a secret (function call or collection literal)
if pattern.name == "Generic Secret" {
    // Extract the value part after = or :
    let value_part = if let Some(eq_pos) = line.find('=') {
        line[eq_pos + 1..].trim()
    } else if let Some(colon_pos) = line.find(':') {
        line[colon_pos + 1..].trim()
    } else {
        ""
    };

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
}
```

**Step 4: Run tests**

Run: `cd repotoire-cli && cargo test --lib secrets -- --nocapture 2>&1 | tail -20`
Expected: ALL pass

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/secrets.rs
git commit -m "fix: SecretDetector skips function calls and collection literals in Generic Secret pattern"
```

---

### Task 8: Fix EvalDetector -- Add management/commands/ to Exclude Patterns

**Files:**
- Modify: `repotoire-cli/src/detectors/eval_detector.rs:48-58` (DEFAULT_EXCLUDE_PATTERNS)
- Test: inline `#[cfg(test)]` module

**Context:** Verification found the original fix was wrong -- `compile()` is NOT in `CODE_EXEC_FUNCTIONS` so skipping 3-arg compile() solves nothing. The only valid sub-fix is adding `management/commands/` to the exclude patterns. Django management commands like `shell.py` use `exec()` intentionally for the interactive shell -- these are framework internals, not security vulnerabilities.

**Step 1: Write the FP-elimination test**

Add to `mod tests`:

```rust
#[test]
fn test_no_finding_for_management_command() {
    let dir = tempfile::tempdir().unwrap();
    let mgmt_dir = dir.path().join("management").join("commands");
    std::fs::create_dir_all(&mgmt_dir).unwrap();
    let file = mgmt_dir.join("shell.py");
    std::fs::write(
        &file,
        "def handle(self, **options):\n    code = compile(source, '<shell>', 'exec')\n    exec(code)\n",
    ).unwrap();

    let store = GraphStore::in_memory();
    let detector = EvalDetector::with_repository_path(dir.path().to_path_buf());
    let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
    let findings = detector.detect(&store, &empty_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag exec() in management/commands/. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}
```

**Step 2: Run test to verify it fails**

Run: `cd repotoire-cli && cargo test --lib eval_detector -- --nocapture 2>&1 | tail -20`
Expected: `test_no_finding_for_management_command` FAILS

**Step 3: Implement the fix**

Add `management/commands/` to `DEFAULT_EXCLUDE_PATTERNS` (line 48-58):

```rust
const DEFAULT_EXCLUDE_PATTERNS: &[&str] = &[
    "tests/",
    "test_",
    "_test.py",
    "migrations/",
    "__pycache__/",
    ".git/",
    "node_modules/",
    "venv/",
    ".venv/",
    "management/commands/",
];
```

**Step 4: Run tests**

Run: `cd repotoire-cli && cargo test --lib eval_detector -- --nocapture 2>&1 | tail -20`
Expected: ALL pass

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/eval_detector.rs
git commit -m "fix: EvalDetector excludes management/commands/ (Django shell uses exec() intentionally)"
```

---

### Task 9: Run Full Test Suite

**Step 1: Run all tests**

Run: `cd repotoire-cli && cargo test 2>&1 | tail -10`
Expected: All 644+ tests pass (some new tests added)

**Step 2: Fix any failures**

If any pre-existing tests break, analyze and fix. The changes should be backward-compatible -- we're only reducing detections, not adding new ones.

**Step 3: Commit any fixes**

```bash
git add -A
git commit -m "test: fix any test regressions from accuracy pass"
```

---

### Task 10: Build Release Binary and Validate Against All 3 Targets

**Step 1: Build release binary**

Run: `cd repotoire-cli && cargo build --release 2>&1 | tail -5`

**Step 2: Validate Flask**

Run: `repotoire-cli/target/release/repotoire analyze /tmp/flask --format json --per-page 0 2>&1 | head -30`
Expected: Score >= 90.4 (A-), findings <= 36

**Step 3: Validate FastAPI**

Run: `repotoire-cli/target/release/repotoire analyze /tmp/fastapi --format json --per-page 0 2>&1 | head -30`
Expected: Score >= 95.5 (A), findings <= 186

**Step 4: Validate Django**

Run: `repotoire-cli/target/release/repotoire analyze /tmp/django --format json --per-page 0 2>&1 | head -30`
Expected: Score >= 92.0, findings < 700 (down from 833)

**Step 5: Update validation reports**

Update `docs/audit/django-validation-report.md`, `flask-validation-report.md`, `fastapi-validation-report.md` with the new results.

**Step 6: Commit**

```bash
git add docs/audit/
git commit -m "docs: update validation reports after detector accuracy pass"
```
