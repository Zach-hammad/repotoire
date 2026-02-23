# FP Reduction Design

**Date**: 2026-02-23
**Problem**: 7 detectors exceed 30% false positive rate on Flask and FastAPI validation
**Goal**: Reduce overall FP rate from ~74-91% (Flask) and ~35-40% (FastAPI) to <15%

## Root Cause

Detectors scan raw source text with regex. Comments, docstrings, and string literals
contain keywords that trigger false positives. Each detector has ad-hoc comment skipping
(`trimmed.starts_with("#")`) but none handles multi-line comments, docstrings, or strings.

## Solution: Two-Layer Architecture

### Layer 1: Tree-Sitter Masking (shared)

New module `src/cache/masking.rs` provides `mask_non_code()`:
- Parses file with tree-sitter (already a dependency)
- Walks CST, collects byte ranges for `comment`, `string`, and docstring nodes
- Replaces those ranges with spaces (preserving `\n` for line/column stability)
- Cached in `FileCache` as `masked_content(path)`

### Layer 2: Per-Detector Targeted Fixes (7 detectors)

| Detector | FP Rate | Fix |
|----------|---------|-----|
| SecretDetector | 85.7% | Use masked_content + skip param declarations |
| InsecureCookieDetector | 75-100% | Tighten regex to actual set_cookie() calls only |
| UnusedImportsDetector | 100% | Add `# noqa` support + `__all__` re-export handling |
| DebugCodeDetector | 100% | Use masked_content (docstrings masked away) |
| GeneratorMisuseDetector | 75% | Recognize FastAPI try/yield/finally pattern |
| UnsafeTemplateDetector | 67-100% | Skip static string innerHTML assignments |
| HardcodedIpsDetector | 75-100% | Use masked_content + skip localhost/0.0.0.0 defaults |

## Research Basis

- Tree-sitter captures comments as `is_extra()` nodes across all languages
- Python docstrings are `expression_statement > string` (not comment nodes)
- No production linter uses regex for comment detection (all use AST)
- Masking (replace with spaces) preserves line/column positions (proven approach)
