# Post-Hotfix Validation Results

**Date:** 2026-03-04
**Branch:** perf/optimization-v2
**Fixes applied:** Tasks 2-9 from detector hotspot plan

---

## Self-Analysis (405 files, 5083 functions)

**Before:** 17.28s | **After:** 15.27s | **Improvement:** 12%

```
Phase timings:
  setup            0.001s  (0.0%)
  init+parse       0.557s  (3.7%)
  calibrate        0.000s  (0.0%)
  detect           12.199s  (79.9%)
  postprocess      1.485s  (9.7%)
  scoring          0.981s  (6.4%)
  output           0.030s  (0.2%)
  TOTAL            15.268s
```

SQLInjectionDetector: 3.3s (was the single hang-blocker before)

---

## CPython Benchmark (3415 files, 71918 functions)

**Before:** Never finished (hung on SQLInjection taint analysis)
**After:** Progresses through all phases but exceeds 60s target

### Phase breakdown (from debug logs):

| Phase | Time | Status |
|-------|------|--------|
| Parse + graph build | ~3s | OK |
| Graph-independent detectors | ~28s | OK (AIBoilerplate=16s dominates) |
| Function context (sampled betweenness K=500) | 1.6s | FIXED (was catastrophic) |
| HMM context (pre-computed fan-in) | 3.2s | FIXED (was O(n log n) queries) |
| Cross-function trace_taint BFS | 85s+ | NEW BOTTLENECK |
| Intra-function taint (centralized) | not reached | blocked by above |

### Key improvement: sampled betweenness

71918 functions with K=500 sampling → 1.6s (was O(N²) full Brandes)

### Remaining bottleneck: trace_taint() BFS

The cross-function taint analysis (`trace_taint()` in `taint/mod.rs`) does BFS from source functions to sink functions. For CPython with 71k functions:
- Many false-positive sources (functions matching `get_*`, `handle_*` patterns)
- Each source triggers full call-graph BFS (depth 10)
- 7 categories × thousands of sources = catastrophic

This is a SEPARATE issue from the taint analysis optimizations (pre-filter, inner loop, centralized engine), which target the intra-function (file-based) analysis. The cross-function BFS needs its own optimization:
- Pre-filter source functions more aggressively
- Limit BFS candidates per category
- Cache call graph traversals across categories

---

## What was fixed

| Fix | Impact |
|-----|--------|
| Reclassify 7 graph-dependent detectors | Correct phase assignment |
| Remove context building from GI phase | Eliminates wasted O(N²) betweenness |
| Pre-compute fan-in for HMM sort | O(n) vs O(n log n) graph queries |
| Sampled betweenness (K=500) | 71918 functions in 1.6s |
| Shared FileContentCache | Zero-copy cross-detector file access |
| Pre-filter by sink keywords | 90%+ files skipped before taint analysis |
| Inner loop fix (pre-lowercase sinks) | Eliminate per-line string allocations |
| Centralized taint engine | Single pass for all 12 security detectors |
| 8MB thread stack | No more stack overflow on nested C |

## Tests

All 978 tests pass (938 unit + 12 doc + 21 integration + 7 postprocess).
