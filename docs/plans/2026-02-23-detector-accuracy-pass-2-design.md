# Detector Accuracy Pass 2 — Design Document

**Date**: 2026-02-23
**Scope**: 15 detectors with 10+ findings in Django validation
**Methodology**: Exhaustive manual audit of every finding against Django source code
**Goal**: Reduce Django findings from 616 → ~250 while preserving all true positives

## Executive Summary

Round 1 reduced Django findings from 833 → 616 (26% reduction) by fixing 8 detectors. Round 2 targets all remaining detectors with 10+ findings — 15 detectors producing 441 findings total, of which ~364 are false positives (83% FP rate). After applying 42 targeted fixes, we project ~250 remaining findings (all true positives).

## Validation Baseline (Post Round 1)

| Project | Score | Grade | Findings |
|---------|-------|-------|----------|
| Flask | 90.7 | A- | 34 |
| FastAPI | 97.5 | A+ | 133 |
| Django | 93.2 | A | 616 |

## Detectors and Fixes

### Tier 1: High-Impact Detectors (>30 findings each)

---

### 1. EmptyCatchDetector (101 findings → ~56)

**Current state**: 49 Low (all appropriate), 45 Medium (~42 FP), 7 High (all TP)

**Root cause**: The `common_idioms` allowlist at line 164 contains only 11 exception types. Django catches specific exceptions like `UserModel.DoesNotExist`, `ValidationError`, `FieldError`, `LookupError`, `FullResultSet`, `Http404` — all intentional and specific but not in the allowlist, so they get Medium severity.

**Fix**: Invert the idiom logic — instead of allowlisting specific exception types, treat ANY named specific exception as Low severity. Only keep Medium/High for broad catches (`except:`, `except Exception:`, `except BaseException:`).

**File**: `repotoire-cli/src/detectors/empty_catch.rs`, lines 163-174

---

### 2. DjangoSecurityDetector — Raw SQL rule (52 findings → ~2)

**Current state**: 1 Critical + 51 Medium, ~98% FP

**Root cause**: All 52 findings are "Raw SQL usage" in Django's own ORM/database layer (`db/backends/`, `db/models/sql/`, `core/cache/backends/`). Flagging the ORM itself for "using raw SQL instead of the ORM" is fundamentally wrong.

**Fix**: Add path-based exclusions for ORM/database-internal paths. Skip files in `db/backends/`, `db/models/sql/`, `db/models/expressions`, `core/cache/backends/`, `migrations/`.

**File**: `repotoire-cli/src/detectors/django_security.rs`, ~line 115

---

### 3. EvalDetector (43 findings → ~3)

**Current state**: 20 Critical + 20 High + 3 Medium, ~93% FP

**Root causes and fixes**:

1. **`.eval()` method calls** (19 FPs): Django template `smartif.py` defines `.eval()` methods on AST nodes. The detector can't distinguish `node.eval(context)` from builtin `eval()`. **Fix**: Skip `eval(` when preceded by `.` (method call). Lines ~200-215.

2. **Path matching bug** (20 FPs): The downgrade logic for `/django/` paths (lines 445-455) fails because paths are relative (`django/apps/config.py`), not absolute. **Fix**: Also match `starts_with("django/")`.

3. **Safe subprocess calls** (4 FPs): `subprocess.run(args, ...)` with list arguments and NO `shell=True` is the safe pattern. **Fix**: Don't flag subprocess.run/Popen/call when `shell=True` is absent. Lines ~238-246.

**File**: `repotoire-cli/src/detectors/eval_detector.rs`

---

### 4. StringConcatLoopDetector (39 findings → ~5)

**Current state**: ~95% FP

**Root causes and fixes**:

1. **`f` in regex character class** (3 FPs): The regex `["'\x60f]` matches any word starting with `f` (like `fs.media`), not just f-strings. **Fix**: Change to `(?:["'\x60]|f["'])`. Line 38.

2. **Python indentation tracking** (19 FPs): `in_loop` state never resets for Python because it uses brace-depth tracking. A `for` loop at indent 8 marks everything after it as "in loop." **Fix**: Track loop indent level and exit when indentation decreases. Lines 147-174.

3. **Non-accumulation assignments** (8 FPs): Pattern matches `formset_name = form_name + "Set"` — a one-time assignment, not loop accumulation. **Fix**: Only match `+=` patterns, not `= x + y`. Line 38.

**File**: `repotoire-cli/src/detectors/string_concat_loop.rs`

---

### Tier 2: Medium-Impact Detectors (15-30 findings each)

---

### 5. PathTraversalDetector (28 findings → ~1)

**Current state**: 100% FP

**Root causes and fixes**:

1. **`Path\s*\(` with case-insensitive flag** (23 FPs): `(?i)` makes `Path\s*\(` match `get_full_path()`. **Fix**: Remove `(?i)`, keep `Path` capital-P only. Add `(?<![.\w])` lookbehind before `path\.join` and `path\.resolve`. Line 26.

2. **`request.` too broad for user-input detection** (25 FPs): `request.get_full_path()`, `request.scheme`, `request.user` are not user-supplied filesystem paths. **Fix**: Replace `request.` with specific accessors: `request.GET`, `request.POST`, `request.FILES`, `request.args`, `request.form`, `request.data`. Lines 112-119.

3. **`remove` in file_op regex** (1 FP): Matches `list.remove()`. **Fix**: Require `os.remove` or add word boundary. Line 18.

4. **Skip comment/docstring lines**: Add `#`/`//`/`*` prefix check. After line 108.

**File**: `repotoire-cli/src/detectors/path_traversal.rs`

---

### 6. GeneratorMisuseDetector (27 findings → ~3)

**Current state**: 100% FP

**Root causes and fixes**:

1. **`\byield\b` matches strings/docstrings** (2 FPs): `warnings.warn("may yield inconsistent")` triggers false match. **Fix**: Strip string contents before yield detection. Line 76.

2. **`yield from` treated as single-yield** (9 FPs): `yield from self.cursor` delegates 0-N items but counted as 1 yield. **Fix**: Add `\byield\s+from\b` regex; treat as multi-yield (return early). Lines 76-78 + new regex at line 20.

3. **`@contextmanager` without `try/finally`** (1 FP): Skip logic requires both decorator AND `try/finally`. **Fix**: Make `@contextmanager` alone sufficient to skip. Lines 236-241.

4. **Python builtins in list-wrapped set** (3 FPs): Methods named `list` collide with `list(` builtin detection. **Fix**: Exclude builtins from wrapped set. Lines 138-139.

**File**: `repotoire-cli/src/detectors/generator_misuse.rs`

---

### 7. GodClassDetector (24 findings → ~4)

**Current state**: 100% FP (8 framework base classes + 16 test classes)

**Root causes and fixes**:

1. **Test classes not excluded** (16 FPs): `ClassContext.is_test` is computed but never checked by `skip_god_class()`. **Fix**: Add `|| ctx.is_test` to skip condition. `god_class.rs` line 502.

2. **Framework base class suffixes missing** (4 FPs): `ModelAdmin`, `BaseDatabaseSchemaEditor`, etc. not in `FRAMEWORK_CORE_NAMES`. **Fix**: Add `FRAMEWORK_CORE_SUFFIXES` (`SchemaEditor`, `Autodetector`, `Compiler`, `Admin`). `class_context.rs` after line 94.

3. **Fallback test path check**: When no graph context available, check file path directly. After line 513.

**File**: `repotoire-cli/src/detectors/god_class.rs`, `repotoire-cli/src/detectors/class_context.rs`

---

### 8. InsecureCryptoDetector (23 findings → ~2)

**Current state**: 2 TPs, 21 FPs (91% FP rate)

**Root causes and fixes**:

1. **`usedforsecurity=False` not recognized** (8 FPs): Python's hashlib flag for non-cryptographic usage. **Fix**: Check for `usedforsecurity=false` in line. After line 42 in `is_hash_mention_not_usage()`.

2. **Class/function definitions matched** (4 FPs): `class MD5(Transform):` matches `md5\s*\(`. **Fix**: Skip lines starting with `class `, `def `, `fn `. After line 379.

3. **Dead test-path check** (4 FPs): Line 54 passes line content (not file path) to `is_test_path()`. **Fix**: Move test-path check to `detect()` where file path is available. Line 346.

4. **Non-security contexts** (2 FPs): DB shims and checksum generation. **Fix**: Skip `_sqlite_`, `checksum`, `etag` contexts.

5. **Regex matches identifiers** (4 FPs): `_sqlite_md5(` matches. **Fix**: Add `(?:^|[^_\w])` before `(md5|sha1)`. Line 15.

**File**: `repotoire-cli/src/detectors/insecure_crypto.rs`

---

### 9. DebugCodeDetector (22 findings → 0)

**Current state**: 100% FP

**Root causes and fixes**:

1. **`print\(` matches `pprint(`** (2 FPs): No word boundary. **Fix**: Change to `\bprint\(`. Line 22.

2. **`debug\s*=\s*True` catches kwargs** (1 FP): `Engine(debug=True)` is intentional config. **Fix**: Remove `debug\s*=\s*True` and `DEBUG\s*=\s*true` patterns entirely. Line 22.

3. **`is_logging_utility` misses "info"** (9 FPs): `ogrinfo()` not recognized. **Fix**: Add `"info"`, `"output"`, `"report"` to pattern list. Line 41.

4. **No verbosity-guard detection** (5 FPs): `if verbosity >= 2: print(...)` is CLI output. **Fix**: Check previous line for `verbosity`/`verbose`. Around line 128.

5. **No management path exclusion** (7 FPs): **Fix**: Add `/management/commands/`, `/management/`, `/cli/`, `/cmd/` to `is_dev_only_path`. Line 49.

**File**: `repotoire-cli/src/detectors/debug_code.rs`

---

### 10. LazyClassDetector (21 findings → 0)

**Current state**: 100% FP (9 ORM Lookup/Transform classes + 12 test models)

**Root causes and fixes**:

1. **Missing ORM patterns in EXCLUDE_PATTERNS** (9 FPs): `Lookup`, `Transform`, `Descriptor`, `Field`, `Widget`, etc. not excluded. **Fix**: Add to `EXCLUDE_PATTERNS`. Lines 41-89.

2. **No test path exclusion** (12 FPs): Test fixture model classes flagged. **Fix**: Add test path check. After line 224.

**File**: `repotoire-cli/src/detectors/lazy_class.rs`

---

### Tier 3: Lower-Impact Detectors (<15 findings each)

---

### 11. InsecureCookieDetector (17 findings → 0)

**Current state**: 100% FP

**Fixes**:

1. **Remove `.cookies[` from regex** (12 FPs): Matches cookie attribute-setting (`self.cookies[key]["secure"] = True`) and reads. **Fix**: Remove `\.cookies\[` from regex, keep only `set_cookie\(` patterns. Lines 21-23.

2. **Widen context window** (5 FPs): `+5` lines too narrow for Django's 7-10 line `set_cookie()` calls. **Fix**: Change to `+15`. Lines 69-70.

**File**: `repotoire-cli/src/detectors/insecure_cookie.rs`

---

### 12. SQLInjectionDetector (14 findings → 0)

**Current state**: 100% FP (all Django ORM internals using `quote_name()`)

**Fixes**:

1. **Recognize `quote_name()` as sanitizer** (10 FPs): Django's SQL identifier quoting function. **Fix**: Add `is_sanitized_value()` method checking for `quote_name(`, `escape_name(`, `quote_ident(`. Around line 334, used at line 606.

2. **Framework backend path exclusion** (4 FPs): All in `db/backends/` internals. **Fix**: Extend `should_exclude()`. Lines 148-164.

**File**: `repotoire-cli/src/detectors/sql_injection.rs`

---

### 13. RegexInLoopDetector (13 findings → ~2)

**Current state**: 1-2 TPs, ~85% FP

**Fixes**:

1. **Python indentation-based scope tracking** (11 FPs): Brace-depth tracking is fundamentally broken for Python. `in_loop` never resets after a `for` statement. **Fix**: Track `loop_indent` and exit loop scope when indentation decreases to or below loop level. Lines 163-236.

2. **Skip Python comments** (1 FP): `re.compile` inside `#` comment flagged. **Fix**: Skip lines starting with `#`. Line 192.

3. **Skip list comprehensions** (3 FPs): `[re.compile(r) for r in ...]` is one-shot. **Fix**: Detect `[` + `for` + `in` on same line. Line 176.

**File**: `repotoire-cli/src/detectors/regex_in_loop.rs`

---

### 14. CallbackHellDetector (9 findings → 0)

**Current state**: 100% FP

**Fixes**:

1. **Filter non-callback `function(` patterns** (all FPs): Object methods, prototype assigns, variable declarations counted as nested callbacks. **Fix**: Filter patterns before counting. Lines 128-129.

2. **Brace-depth-aware nesting** (all FPs): Sibling functions treated as nested because depth only grows. **Fix**: Track brace depth and reset when exiting scope. Lines 85-168.

**File**: `repotoire-cli/src/detectors/callback_hell.rs`

---

### 15. GlobalVariablesDetector (8 findings → 5)

**Current state**: 7 TPs, 1 FP (87.5% precision — best of all detectors)

**Fixes**:

1. **Skip Python docstrings** (1 FP): `global or in CSS you might...` inside triple-quoted docstring matched as `global or`. **Fix**: Track `in_docstring` state via triple-quote toggling. Lines 221-226.

2. **Deduplicate per (file, variable)** (2 duplicate TPs): `_default` flagged 3 times from 3 functions. **Fix**: Add `HashSet<(PathBuf, String)>` dedup. Lines 197-198, 276-288.

**File**: `repotoire-cli/src/detectors/global_variables.rs`

---

## Implementation Strategy

### Phase 1: Quick Wins (regex/config changes, ~10 detectors)
Detectors where fixes are simple regex adjustments, list additions, or condition tweaks:
- DebugCodeDetector, InsecureCookieDetector, InsecureCryptoDetector, SQLInjectionDetector, PathTraversalDetector, LazyClassDetector, GodClassDetector, GlobalVariablesDetector, DjangoSecurityDetector, CallbackHellDetector

### Phase 2: Logic Refactors (~5 detectors)
Detectors requiring new tracking state or algorithm changes:
- EmptyCatchDetector (invert idiom logic)
- EvalDetector (method call vs builtin, subprocess safety)
- GeneratorMisuseDetector (yield-from tracking, string-aware yield)
- RegexInLoopDetector (Python indentation scope)
- StringConcatLoopDetector (Python indentation scope, regex overhaul)

### Testing Strategy
- Each detector gets unit tests covering the specific FP patterns identified
- Full test suite regression after each detector
- Final validation against Flask, FastAPI, Django

## Projected Results

| Project | Before R2 | After R2 (projected) |
|---------|-----------|---------------------|
| Flask | 90.7 (A-) / 34 | ~92 (A) / ~30 |
| FastAPI | 97.5 (A+) / 133 | ~98 (A+) / ~90 |
| Django | 93.2 (A) / 616 | ~96 (A+) / ~250 |

**Total FP reduction across Django**: ~364 false positives eliminated (59% reduction in findings).
