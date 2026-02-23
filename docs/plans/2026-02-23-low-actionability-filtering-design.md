# Low-Actionability Filtering Design

## Problem Statement

After 3 detector accuracy passes, Django has 293 findings with a ~95% true positive rate. However, ~153 of those TPs are **low-actionability** — they flag intentional patterns that no developer would change. The biggest offenders:

- **EmptyCatchDetector** (101): Every single finding is an intentional Django pattern (cleanup, optional import probing, fallback logic). None are accidentally swallowing errors.
- **WildcardImportsDetector** (20): All are `__init__.py` public API re-exports — standard Python package convention.
- **TodoScanner** (32): Real TODO/FIXME/HACK annotations — already Low severity, no change needed.

Additionally, ~16 remaining FPs across DjangoSecurityDetector (4), GeneratorMisuseDetector (7), and UnusedImportsDetector (5).

## Goal

Reduce Django from 293 → ~166 findings by teaching 5 detectors to skip intentional/low-value patterns. Fix the remaining 16 FPs.

## Approach

Per-detector suppression rules. Each detector gets context-aware checks for the specific patterns identified in the audit. No global NOQA system — keep it targeted.

---

## Detector 1: EmptyCatchDetector (101 → ~10)

**File:** `repotoire-cli/src/detectors/empty_catch.rs` (436 lines)

**Audit finding:** All 101 Django empty catches are intentional. Three dominant patterns:

### Pattern A: Cleanup/Teardown Methods (~30 findings)

Empty catches inside `close()`, `__del__`, `__exit__`, `__aexit__`, `shutdown()`, `_close()`, `dispose()` methods. These intentionally swallow errors during resource cleanup because cleanup failures shouldn't propagate.

**Examples:**
- `response.py:336` — `except Exception: pass` in `close()` for response closers
- `response.py:411` — `except Exception: pass` closing a value in content setter
- `temp.py:58` — `except OSError: pass` on `file.close()` in temp file cleanup
- `utils.py:50` — `except db.Database.Error: pass` in cursor `__exit__`

**Fix:** In `scan_file()` (line 115-267), after detecting an empty catch, check if the containing function is a cleanup method. If so, skip the finding.

Look backward from the catch line to find the function definition. Check if the function name matches cleanup patterns: `close`, `__del__`, `__exit__`, `__aexit__`, `shutdown`, `_close`, `dispose`, `cleanup`, `teardown`, `finalize`.

### Pattern B: Optional Import Probing (~15 findings)

`try: import X; except ImportError: pass` is already handled — the detector skips ImportError catches. But related patterns like `try: from X import Y; except Exception: pass` (broader exception type for import probing) are not caught.

**Note:** This pattern is already partially handled at lines 154-156 (ImportError/ModuleNotFoundError). Extend to check if the try body is a single `import` or `from X import Y` statement with a broader except type.

### Pattern C: Safe Single-Line Probing (~50 findings)

Pattern: try body is 1-2 lines doing a probe/fallback, catch is `pass` with specific safe exceptions. Examples:

- `except (FileNotFoundError | KeyError | TypeError | OSError | ValueError): pass` with 1-line try body
- `except AttributeError: pass` after `getattr()` probe
- `except (TypeError, AttributeError): pass` after `len()` probe

**Fix:** If the try body is ≤2 non-blank lines AND the exception type is a known "safe probe" type (KeyError, AttributeError, TypeError, ValueError, FileNotFoundError, OSError, PermissionError, NotImplementedError), downgrade or skip. The logic already captures severity at lines 191-217 — modify to skip entirely when pattern matches.

### Expected: ~91 findings eliminated, ~10 remaining (genuinely risky empty catches)

---

## Detector 2: WildcardImportsDetector (20 → 0)

**File:** `repotoire-cli/src/detectors/wildcard_imports.rs` (279 lines)

**Audit finding:** All 20 findings are in `__init__.py` files doing public API re-exports. This is the standard Python convention for package namespaces.

**Current state:** Lines 103-113 already skip relative wildcard imports (`from .module import *`) in `__init__.py`. But they DON'T skip absolute wildcard imports (`from django.db.models.fields import *`) in `__init__.py`.

**Fix:** At line 103-113, change the condition from "skip if `__init__.py` AND relative import" to "skip if `__init__.py`" (period). ALL wildcard imports in `__init__.py` are re-exports by definition.

### Expected: 20 findings eliminated

---

## Detector 3: DjangoSecurityDetector (4 → 0) [FP Fix]

**File:** `repotoire-cli/src/detectors/django_security.rs` (617 lines)

**Audit finding:** All 4 findings are false positives.

### FP 1-2: CSRF decorator definition and comment

The detector flags `csrf_exempt` at:
- `views/decorators/csrf.py` — This IS the definition of the `csrf_exempt` decorator. Flagging it is like flagging a fire extinguisher as a fire risk.
- `views/generic/base.py` — A comment mentioning `@csrf_exempt`, not actual usage.

**Fix:** In the CSRF check (lines 135-209), add two filters:
1. If path contains `decorators/csrf` — skip (this is the decorator definition module)
2. If the matching line is a comment (starts with `#` after trim) — skip

### FP 3-4: Raw SQL in management commands

The detector flags `cursor.execute()` in management commands that use ORM-generated SQL.

**Fix:** In the raw SQL path exclusions (lines 320-334), add `management/commands/` to the exclusion list. Management commands are admin tools, not user-facing endpoints.

### Expected: 4 findings eliminated

---

## Detector 4: GeneratorMisuseDetector (13 → ~6) [FP Fix]

**File:** `repotoire-cli/src/detectors/generator_misuse.rs` (482 lines)

**Audit finding:** ~7 FPs across three categories:

### FP Category 1: Polymorphic Interface Methods (~3 FPs)

Single-yield generators that implement a base class protocol. Examples:
- `locmem.py:get_template_sources` — overrides `Loader.get_template_sources()` which yields many in other implementations
- `widgets.py:subwidgets` — base class impl, `ChoiceWidget.subwidgets` yields many
- `uploadedfile.py:chunks` — overrides `File.chunks()` which yields multiple

**Fix:** For single-yield generators (lines 273-284), check if the function name matches a known base-class protocol method. Use a configurable list: `get_template_sources`, `subwidgets`, `chunks`, `iter_*` methods.

Alternative (more robust): Check if other functions with the same name exist in the file set and yield more. If any sibling implementation yields 2+, this is a polymorphic interface — skip.

### FP Category 2: Not Actually a Generator (~1 FP)

`get_filter_kwargs_for_object` returns a dict, not a generator. The detector misidentified it.

**Fix:** Already handled by `count_yields()` — if it returns 0, the function is skipped. Verify this works. If the FP persists, the issue is in how the function body is delimited.

### FP Category 3: Lazy Consumption Not Detected (~3 FPs)

`smart_split` and `get_finders` are consumed lazily via `for x in func()` in production code. The detector only found `list()` calls in tests/doctests.

**Fix:** `is_consumed_lazily()` (lines 187-212) already checks for `for` loop consumption. Verify it works correctly. If FPs persist, the issue may be that the function is consumed in a DIFFERENT file than where it's defined. Expand search scope.

### Expected: ~7 findings eliminated

---

## Detector 5: UnusedImportsDetector (11 → ~6) [FP Fix]

**File:** `repotoire-cli/src/detectors/unused_imports.rs` (488 lines)

**Audit finding:** ~5 FPs from function-scoped imports and import-existence checks.

### FP 1: Function-Scoped Imports (~3 FPs)

Imports inside function bodies (e.g., `def get_prep_lookup(): from django.db.models.sql.query import Query`). The detector treats these as module-level imports and doesn't find usage because the symbol is used on the very next line, within the function scope.

**Fix:** Before extracting imports (around line 257), check if the import line is indented (inside a function body). If the line has leading whitespace ≥4 spaces (or 1 tab), it's a function-scoped import — skip it. Function-scoped imports are always intentional (they exist to avoid circular imports or lazy-load modules).

### FP 2: Import-Existence Checks (~2 FPs)

`from PIL import Image # NOQA` — the import itself IS the check (testing if Pillow is installed). The value is never used.

**Fix:** Already handled by NOQA skip at line 228. Verify the NOQA check works for these specific patterns. If the `# NOQA` appears on the same line, it should be caught.

### Expected: ~5 findings eliminated

---

## Summary

| Detector | Before | After | Eliminated | Type |
|----------|--------|-------|-----------|------|
| EmptyCatchDetector | 101 | ~10 | ~91 | Low-actionability suppression |
| WildcardImportsDetector | 20 | 0 | 20 | Low-actionability suppression |
| DjangoSecurityDetector | 4 | 0 | 4 | FP fix |
| GeneratorMisuseDetector | 13 | ~6 | ~7 | FP fix |
| UnusedImportsDetector | 11 | ~6 | ~5 | FP fix |
| **Total** | **293** | **~166** | **~127** | |

## Validation

After all changes:
1. `cargo test` — all tests pass
2. Django: ~166 findings, score ≥99/A+
3. Flask: no regressions (~23 findings)
4. FastAPI: no regressions (~106 findings)
