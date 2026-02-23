# Flask Live Validation Report

**Date**: 2026-02-23
**Target**: Flask web framework (Pallets project)

## Round 1 (Before FP Reduction)

**Overall Score**: 78.7 / 100 (C+)
**Total Findings**: 63

| Metric | Value |
|--------|-------|
| Structure Score | 99.4 |
| Quality Score | 30.0 |
| Architecture Score | 99.8 |
| Files: 83 | Functions: 525 | Classes: 64 | LOC: 18,399 |

### Findings by Severity

| Severity | Count |
|----------|-------|
| Critical | 4 |
| High | 26 |
| Medium | 9 |
| Low | 24 |

### Per-Detector FP Rates (34 findings sampled)

| Detector | Sampled | TP | FP | Debatable | FP Rate |
|----------|---------|----|----|-----------|---------|
| DebugCodeDetector | 7 | 0 | 7 | 0 | **100%** |
| InsecureCookieDetector | 4 | 0 | 4 | 0 | **100%** |
| UnusedImportsDetector | 4 | 0 | 4 | 0 | **100%** |
| HardcodedIpsDetector | 4 | 0 | 3 | 1 | **75-100%** |
| UnsafeTemplateDetector | 3 | 0 | 2 | 1 | **67-100%** |
| InsecureCryptoDetector | 3 | 0 | 0 | 3 | 0% (debatable) |
| LargeFilesDetector | 3 | 3 | 0 | 0 | **0%** |

**Estimated FP rate: 74-91%**

---

## Round 2 (After FP Reduction)

**Overall Score**: 89.4 / 100 (B+) — **+10.7 improvement**
**Total Findings**: 37 — **-41% reduction**

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Structure Score | 99.4 | 99 | -0.4 |
| Quality Score | 30.0 | 66 | **+36** |
| Architecture Score | 99.8 | 100 | +0.2 |

### Findings by Severity

| Severity | Count |
|----------|-------|
| High | 11 |
| Medium | 8 |
| Low | 18 |

### Key Reductions

| Detector | Before | After | Eliminated |
|----------|--------|-------|------------|
| DebugCodeDetector | 20 | 4 | 16 FPs (docstrings/strings) |
| InsecureCookieDetector | 5 | 1 | 4 FPs (enum/class values) |
| UnusedImportsDetector | 6 | 0 | 6 FPs (noqa support) |
| HardcodedIpsDetector | 4 | 0 | 4 FPs (docstrings/comments) |

### Root Causes Fixed

1. **DebugCodeDetector**: Now uses tree-sitter masked content — docstrings/strings masked before regex scan
2. **InsecureCookieDetector**: Tightened regex to only match `set_cookie()` API calls
3. **UnusedImportsDetector**: Added `# noqa` and `__all__` re-export support
4. **HardcodedIpsDetector**: Now uses tree-sitter masked content
5. **SecretDetector**: Now uses tree-sitter masked content — docstrings masked
6. **UnsafeTemplateDetector**: Skips static innerHTML string assignments
7. **GeneratorMisuseDetector**: Recognizes try/yield/finally DI patterns
