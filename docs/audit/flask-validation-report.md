# Flask Live Validation Report

**Date**: 2026-02-23
**Target**: Flask web framework (Pallets project)
**Overall Score**: 78.7 / 100 (C+)
**Total Findings**: 63

## Score Breakdown

| Metric | Value |
|--------|-------|
| Structure Score | 99.4 |
| Quality Score | 30.0 |
| Architecture Score | 99.8 |
| Files: 83 | Functions: 525 | Classes: 64 | LOC: 18,399 |

## Findings by Severity

| Severity | Count |
|----------|-------|
| Critical | 4 |
| High | 26 |
| Medium | 9 |
| Low | 24 |

## Top Detectors by Finding Count

| Detector | Count |
|----------|-------|
| DebugCodeDetector | 20 |
| UnusedImportsDetector | 6 |
| InsecureCookieDetector | 5 |
| HardcodedIpsDetector | 4 |
| InsecureCryptoDetector | 3 |
| UnsafeTemplateDetector | 3 |
| LargeFilesDetector | 3 |

## Per-Detector FP Rates (34 findings sampled)

| Detector | Sampled | TP | FP | Debatable | FP Rate |
|----------|---------|----|----|-----------|---------|
| DebugCodeDetector | 7 | 0 | 7 | 0 | **100%** |
| InsecureCookieDetector | 4 | 0 | 4 | 0 | **100%** |
| UnusedImportsDetector | 4 | 0 | 4 | 0 | **100%** |
| HardcodedIpsDetector | 4 | 0 | 3 | 1 | **75-100%** |
| UnsafeTemplateDetector | 3 | 0 | 2 | 1 | **67-100%** |
| InsecureCryptoDetector | 3 | 0 | 0 | 3 | 0% (debatable) |
| LargeFilesDetector | 3 | 3 | 0 | 0 | **0%** |

## Overall Sample Statistics

- True Positive: 3 (8.8%)
- False Positive: 25 (73.5%)
- Debatable: 6 (17.6%)
- **Estimated FP rate: 74-91%**

## Root Causes of False Positives

1. **DebugCodeDetector**: Flags "debugger"/"debug" in docstrings and CLI options (Flask's debugger is a core feature)
2. **InsecureCookieDetector**: Flags lines near cookie code without verifying actual set_cookie() parameters
3. **UnusedImportsDetector**: No `# noqa` support, doesn't recognize re-exports or TYPE_CHECKING
4. **HardcodedIpsDetector**: Flags IPs in docstrings and framework defaults
5. **SecretDetector**: Flags variable names containing "password" regardless of whether value is hardcoded
6. **UnsafeTemplateDetector**: Flags framework API definitions rather than dangerous usages
