# FastAPI Live Validation Report

**Date**: 2026-02-23
**Target**: FastAPI web framework (tiangolo/fastapi)
**Overall Score**: 93.27 / 100 (A)
**Total Findings**: 218

## Score Breakdown

| Metric | Value |
|--------|-------|
| Structure Score | 99.54 |
| Quality Score | 78.86 |
| Architecture Score | 99.32 |
| Files: 1,086 | Functions: 3,934 | Classes: 623 | LOC: 104,822 |

## Findings by Severity

| Severity | Count |
|----------|-------|
| Critical | 4 |
| High | 35 |
| Medium | 52 |
| Low | 127 |

## Top Detectors by Finding Count

| Detector | Count |
|----------|-------|
| UnusedImportsDetector | 48 |
| SecretDetector | 23 |
| DebugCodeDetector | 22 |
| GeneratorMisuseDetector | 22 |
| StringConcatLoopDetector | 14 |
| TodoScanner | 14 |
| UnreachableCodeDetector | 6 |
| SurprisalDetector | 6 |
| UnsafeTemplateDetector | 5 |
| XssDetector | 5 |
| DeepNestingDetector | 5 |
| InsecureCookieDetector | 5 |

## Per-Detector FP Rates (30 findings sampled)

| Detector | Sampled | TP | FP | Debatable | FP Rate |
|----------|---------|----|----|-----------|---------|
| SecretDetector | 7 | 1 | 6 | 0 | **85.7%** |
| InsecureCookieDetector | 4 | 1 | 3 | 0 | **75.0%** |
| UnusedImportsDetector | 3 | 0 | 3 | 0 | **100%** |
| GeneratorMisuseDetector | 4 | 1 | 3 | 0 | **75.0%** |
| UnsafeTemplateDetector | 3 | 0 | 2 | 1 | **67-100%** |

## Overall Sample Statistics

- True Positive: ~50-55%
- False Positive: ~35-40%
- Debatable: ~10-15%

## Root Causes of False Positives

1. **SecretDetector**: Triggers on variable/parameter names containing "password" regardless of whether value is hardcoded
2. **InsecureCookieDetector**: Triggers on enum definitions like `cookie = "cookie"`, not actual cookie operations
3. **UnusedImportsDetector**: No `# noqa: F401` support, parser bug with multi-line imports
4. **GeneratorMisuseDetector**: Doesn't recognize FastAPI dependency injection `try/yield/finally` pattern
5. **UnsafeTemplateDetector**: Flags innerHTML with static strings (empty string clearing, hardcoded values)

## Common Themes Across Both Projects

Detectors exceeding 30% FP in BOTH Flask and FastAPI:
- **SecretDetector** - needs value analysis, not just name matching
- **InsecureCookieDetector** - needs actual set_cookie() verification
- **UnusedImportsDetector** - needs noqa support and better parsing
- **UnsafeTemplateDetector** - needs static vs dynamic value distinction
- **DebugCodeDetector** - needs to exclude docstrings and comments
- **GeneratorMisuseDetector** - needs framework-aware yield patterns
- **HardcodedIpsDetector** - needs to skip docstrings
