# Detector Quality Assurance Design

**Date:** 2026-02-23
**Status:** Approved
**Goal:** Ensure all 104+ Repotoire detectors work properly through systematic testing, standards auditing, live validation, and dead code cleanup.

## Context

Audit of the detector system found:
- All 104+ detectors are fully implemented (no stubs)
- Self-analysis scores 98.9/100 A+ on Repotoire's own codebase
- ~90% false positive reduction achieved over project lifetime
- **But:** 48% overall test coverage, with framework detectors at 0%, performance at 20%, security at 27%

## Workstreams

### Workstream 1: Standards Gap Audit (Execute First)

Map existing detectors against industry standards to identify missing patterns and inform test priorities.

**Standards to audit against:**
- Martin Fowler's Refactoring Catalog (22 classic code smells)
- OWASP Top 10 (2021)
- CWE (Common Weakness Enumeration) for security detectors
- SonarQube rule catalog (reference for mature static analysis)
- ESLint/TypeScript-ESLint popular rules (for TS/JS gaps)

**Deliverable:** Gap analysis document mapping each standard smell/rule to:
- An existing Repotoire detector (with confidence level)
- "Not applicable" (language-specific rules for unsupported languages)
- "Missing - should add" (with priority ranking)

### Workstream 2: Test Coverage Blitz

Add unit tests for all untested detectors using existing patterns (inline `#[cfg(test)]` modules + `GraphStore::in_memory()`).

**Priority tiers:**

| Tier | Detectors | Current | Target |
|------|-----------|---------|--------|
| P0 | Framework-specific (React, Django, Express) | 0% | 100% |
| P0 | Security detectors (SQL injection, XSS, SSRF, etc.) | 27% | 90%+ |
| P1 | Performance/async detectors | 20% | 80%+ |
| P2 | AI detectors | 50% | 80%+ |
| P3 | Code quality detectors | 43% | 60%+ |

**Test requirements per detector (minimum):**
- 1 true-positive test (catches the smell in synthetic code)
- 1 true-negative test (clean code produces no findings)
- Edge case tests for complex detectors (boundary thresholds, multi-file patterns)

**Test fixtures:**
- Expand `tests/fixtures/` with per-category fixture files
- Examples: `react_hooks_bad.tsx`, `django_bad.py`, `async_bad.py`
- Each fixture contains intentionally problematic code
- Matching clean fixtures where needed

**Languages:** Python + TypeScript focus (most mature parsers, most users).

### Workstream 3: Cleanup & Completion

Remove dead code and complete unfinished modules.

| Item | Action |
|------|--------|
| `query_cache.rs` | Complete the caching implementation (currently `#![allow(dead_code)]`) |
| `TaintDetector` | Remove dead file or document why it's retained alongside graph-based replacements |
| `#[allow(dead_code)]` annotations | Remove unused builder methods or integrate them (4-5 instances) |
| Surprisal detector | Add logging when conditionally skipped so users know |

### Workstream 4: Live Validation Against Open-Source Projects (Execute Last)

Run detectors against real-world projects and verify findings are sensible.

**Target projects:**
- **Python:** Flask (small, well-structured) + FastAPI (modern async patterns)
- **TypeScript:** A small-medium React project (with hooks, components)

**Process:**
1. Clone each project into a temp directory
2. Run `repotoire analyze` with JSON output
3. Sample 20-30 findings per project across detector categories
4. Categorize each as: True Positive, False Positive, or Debatable
5. Track false positive rate per detector
6. Fix any detector with >30% false positive rate

**Deliverable:** Validation report with FP rates per detector and list of detectors that need tuning.

## Execution Order

1. **Standards Gap Audit** - Informs what tests to write and what detectors might be missing
2. **Test Coverage Blitz** - Systematic coverage of all detector categories
3. **Cleanup & Completion** - Complete `query_cache.rs`, remove dead code
4. **Live Validation** - Final check against real-world projects

## Success Criteria

- All P0 detectors have unit tests (framework + security)
- Standards gap analysis document complete with CWE mapping
- `query_cache.rs` completed and integrated
- Dead `TaintDetector` resolved (removed or documented)
- Live validation FP rate <30% per detector across Flask, FastAPI, and React targets
- All existing tests continue to pass (`cargo test`)
