# Detector Accuracy Pass — Design Document

**Date**: 2026-02-23
**Status**: Approved (Revised after verification)

## Problem

After vendor exclusion and masking layer improvements, Django still has 833 findings. Audit of the top 8 noisiest detectors reveals false positives from overly-broad regex patterns, missing framework idiom awareness, and incomplete Python import semantics.

## Revision Notes

The original design was verified against actual detector source code before implementation. Key corrections:
- **DjangoSecurityDetector**: `def raw(` does NOT match regex `\.raw\(` (requires dot prefix). "Skip definitions" fix was removed.
- **EvalDetector**: `compile()` is NOT in `CODE_EXEC_FUNCTIONS`. "Skip 3-arg compile()" fix was removed.
- **EmptyCatchDetector**: Blanket-skipping KeyError/AttributeError was too aggressive. Changed to tiered approach (skip ImportError, downgrade others).
- **WildcardImportsDetector**: Blanket-skipping `__init__.py` was too aggressive. Changed to skip only relative imports.
- **SecretDetector**: Skipping uppercase starts would miss `password = HARDCODED_CONSTANT`. Changed to skip function calls (contains `(`) only.
- **CommentedCodeDetector**: Scoring system replaced with simpler strong/weak pattern split.

## Detectors & Fixes

### Category A: Regex Precision

**StringConcatLoopDetector** (50 findings in Django)
- Remove `\w+\s*\+=\s*\w+` regex alternative — catches all `+=` in loops, not just string concat
- Do NOT add Python indentation tracking in same change (separate, complex task)

**SecretDetector** (44 findings in Django)
- Skip values containing `(` (function/class calls like `CharField(...)`)
- Skip values starting with `[` or `{` (collection literals)
- Do NOT skip uppercase starts or digit starts (would miss real findings)

**CommentedCodeDetector** (31 findings in Django)
- Split patterns into strong (definitely code: `def `, `class `, `return `, `import `, `==`, `!=`, etc.) and weak (common in prose: `=`, `()`, `[]`, etc.)
- Require at least one strong indicator per line
- Add license/copyright header detection and exclusion

### Category B: Framework Idiom Awareness

**EmptyCatchDetector** (101 findings in Django)
- Fully skip: `except ImportError: pass`, `except ModuleNotFoundError: pass` (always intentional)
- Downgrade to Low severity: `except KeyError: pass`, `except AttributeError: pass`, `except StopIteration: pass`, etc. (common but can hide bugs)
- Keep flagging at full severity: bare `except: pass`, `except Exception: pass`

**DjangoSecurityDetector** (52 findings in Django)
- Use full path (not filename) for `is_test_path()` in DEBUG check — `tests/settings.py` has fname `settings.py` which doesn't match
- Add `is_test_path()` guard to SECRET_KEY check (currently missing)
- Remove `"+ "` from `has_user_input` heuristic (too broad — catches all string concatenation)
- ~~Skip method/class definitions~~ (REMOVED: `def raw(` doesn't match `\.raw\(` regex)
- ~~Recognize parameterized cursor.execute~~ (REMOVED: already gets Medium severity, appropriate)

**EvalDetector** (44 findings in Django)
- Add `management/commands/` to exclude patterns (Django shell uses exec() intentionally)
- ~~Skip 3-arg compile()~~ (REMOVED: compile() not in CODE_EXEC_FUNCTIONS, detector doesn't detect it)

### Category C: Python Import Semantics

**UnusedImportsDetector** (146 findings in Django)
- Track `TYPE_CHECKING` as a block (skip all indented lines), not just the line itself
- Handle multi-line `from X import (...)` imports (buffer until closing paren)
- ~~Add (?s) for __all__~~ (REMOVED: `[^\]]` already matches newlines in Rust regex)

**WildcardImportsDetector** (31 findings in Django)
- Skip only relative imports (`from .x import *`) in `__init__.py` files
- Do NOT skip all wildcard imports in `__init__.py` (`from os.path import *` is still bad)
- Do NOT skip `conftest.py` (should use explicit imports)

## Testing Strategy

Each fix gets unit tests proving:
1. True positives still detected
2. False positive case eliminated

## Validation

Rebuild release binary and validate against:
- **Flask**: Baseline 90.4 (A-), 36 findings — must not regress
- **FastAPI**: Baseline 95.5 (A), 186 findings — must not regress
- **Django**: Baseline 92.0 (A-), 833 findings — target reduction

## Expected Impact

- FP reduction in Django (conservative estimate, reduced from original since some fixes were removed)
- Django grade maintained or improved
- Flask/FastAPI scores maintained or improved
- All 644 existing tests still pass
