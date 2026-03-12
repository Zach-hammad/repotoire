# Detection Quality Overhaul — FP Reduction & Confidence Gateway

**Date:** 2026-03-12
**Status:** Approved
**Goal:** Reduce false positives across all 99 detectors, build measurement infrastructure, and add confidence-gated output

## Problem Statement

Repotoire's self-analysis produces 1,984 findings (grade C, 75.8/100) with significant false positive noise:
- 404 LazyClass findings (many are idiomatic Rust structs)
- 5 critical SQL injection findings on the taint detector's own test fixtures
- ~300 dead code findings on benchmark functions, `#[cfg(test)]` blocks, and `pub` API surface
- Quality score of 26.3/100 drags overall grade despite excellent Architecture (99.4) and Structure (95.2)

Users who see too many FPs stop trusting the tool entirely. This overhaul addresses the problem at three levels: tactical fixes, architectural confidence, and measurement infrastructure.

## Execution Order

**A → C → B** (tactical → architectural → measurement)

1. **Phase A** — Language-idiomatic FP fixes (immediate credibility)
2. **Phase C** — Confidence gateway (architectural foundation)
3. **Phase B** — Precision benchmark pipeline (measurement & regression prevention)

---

## Phase A: Language-Idiomatic FP Fixes

### A1. Rust-Aware LazyClass

**File:** `repotoire-cli/src/detectors/lazy_class.rs`

Add Rust-specific exclusions:
- Structs with `impl Trait for Struct` blocks — trait implementations count as methods
- Newtypes (single-field structs wrapping another type)
- Builder pattern structs (methods returning `Self`)
- Enum variants with methods — Rust enums are rich types, not lazy
- Structs in `mod.rs` serving as module entry points
- Detection: Parse `impl` blocks from tree-sitter AST, count trait impls as "effective methods"

**Expected impact:** ~300 of 404 LazyClass findings eliminated on self-analysis

### A2. Self-Referencing Test Fixture Suppression

When a security detector analyzes a file that is part of the detector's own test infrastructure, auto-suppress.

- If file path contains `detectors/` and finding detector name matches the file's module name, skip
- Add `// repotoire:ignore-file` directive for whole-file suppression
- Apply to all detectors, not just security

**Expected impact:** 5 critical SQL injection FPs eliminated, plus similar patterns across test files

### A3. Dead Code Exemptions

- Skip `#[cfg(test)]` blocks and `#[bench]` functions
- Skip functions only reachable from `#[test]` call paths
- Skip `pub` functions in library crates (API surface, not dead code)
- Skip conditional compilation gates (`#[cfg(feature = "...")]`)

**Expected impact:** ~100 dead code findings reduced

### A4. Language-Specific Threshold Profiles

Extend `ThresholdResolver` with per-language overrides:

| Language | Detector | Adjustment | Rationale |
|----------|----------|------------|-----------|
| Rust | LazyClass | `max_methods: 2 → 5` | Rust uses many small structs + separate impl blocks |
| Rust | DeadCode | Skip `pub` items | Library crates expose API surface |
| Go | LazyClass | `max_methods: 3 → 1` | Go interfaces are intentionally small |
| Go | DeadCode | Skip exported functions | Go convention: PascalCase = public API |
| Python | LazyClass | Skip `@dataclass`, `NamedTuple` | Data carriers by design |
| Python | DeadCode | Skip `__all__` exported names | Explicit public API |

### A5. Java-Specific FP Fixes

- Interfaces with few methods → skip LazyClass (Single Responsibility)
- Abstract classes with template methods → not lazy
- `@Override` methods count as real methods
- Record classes (Java 16+) → data carriers, skip LazyClass
- Enum classes with few methods → idiomatic

### A6. C#-Specific FP Fixes

- `partial class` → methods may be in another file, don't count visible methods only
- `interface` → small is intentional
- Records and record structs → data carriers, skip LazyClass
- Extension method classes (static class with `this` parameter) → helpers by design
- `[Obsolete]` attributed code → don't flag as dead code

---

## Phase C: Confidence Gateway

### C1. Mandatory Confidence on All Findings

Add default confidence to `Finding` construction:

| Detector Category | Default Confidence | Rationale |
|-------------------|--------------------|-----------|
| Graph-based (god class, circular deps) | 0.85 | Structural evidence is strong |
| Security taint | Based on taint path length | Shorter path = higher confidence |
| Code quality (regex-based) | 0.65 | Pattern matching has higher FP rate |
| Architecture | 0.80 | Graph metrics are reliable |
| AI-specific | 0.60 | Heuristic detection |

### C2. Post-Detection Confidence Enrichment Pipeline

New pass after all detectors, before output. Runs on all findings.

**Phase 1 signals (ship immediately):**

| Signal | Effect | Source |
|--------|--------|--------|
| Context HMM role | Utility function flagged for dead code → confidence −0.3 | `context_hmm/mod.rs` |
| Calibration percentile | Metric at p50 → confidence −0.2; at p95 → confidence +0.1 | `calibrate/` |
| Voting engine agreement | 2+ detectors on same entity → confidence +0.1 per extra | `voting_engine.rs` |
| Content classifier | Bundled/minified/fixture → confidence −0.4 | `content_classifier.rs` |

**Phase 2 signals (add later):**

| Signal | Effect | Source |
|--------|--------|--------|
| Framework detection | ORM-safe pattern → confidence −0.5 for SQL injection | `framework_detection/` |
| Inline adjacency | Finding near `// TODO` or `// FIXME` → confidence +0.05 | New |
| File churn | High-churn files → confidence +0.05 | git2 |

### C3. Configurable Output Filter

- Config option: `min_confidence = 0.6` (default)
- CLI flag: `--min-confidence 0.5`
- Below-threshold findings stored in cache but hidden from output
- `--show-all` flag to see everything (for debugging/labeling)

### C4. Confidence Provenance in Output

| Format | Display |
|--------|---------|
| Text | `[confidence: 0.82 — graph evidence, 2 detectors agree]` |
| JSON | `"confidence": 0.82, "confidence_signals": [{"signal": "voting_engine", "delta": 0.1}]` |
| HTML | Color-coded confidence badges |
| SARIF | Map to SARIF `confidence` property |
| Markdown | Confidence column in findings table |

### C5. Finding Triage UX (Phase 2)

- New `repotoire triage` command: interactive review of borderline findings (0.5–0.7 confidence)
- User marks TP/FP → feeds back into ML classifier
- Stored as `feedback.json` alongside labeled benchmark data
- Connects to Phase B's labeling pipeline

---

## Phase B: Precision Benchmark Pipeline

### B1. Benchmark Suite

8 representative OSS projects across languages, pinned to specific commits:

| Project | Language | ~LOC | Purpose |
|---------|----------|------|---------|
| Flask | Python | 15K | Web framework, decorators, routing |
| FastAPI | Python | 20K | Async, type hints, dependency injection |
| Django (core/) | Python | 30K | ORM, middleware, large codebase |
| tokio | Rust | 50K | Async runtime, complex Rust patterns |
| serde | Rust | 15K | Derive macros, generics |
| Express.js | TypeScript | 10K | Middleware, callbacks |
| Next.js (subset) | TypeScript | — | React framework patterns |
| Go net/http | Go | 10K | Stdlib, idiomatic Go |

### B2. Labeling Strategy

- Run Repotoire on each project, export findings as JSON
- Manual labeling: TP / FP / Disputed
- Focus on top-10 noisiest detectors first
- Store labels: `benchmark/<project>/labels.json` with deterministic finding IDs
- Target: ~500 labeled findings initially
- Per-language precision tracking (not just per-detector)

### B3. Precision Scoring Harness

Integration test (`cargo test benchmark_precision`) that:
1. Runs analysis on each benchmark project
2. Compares findings against labels using deterministic finding IDs
3. Computes per-detector and per-language precision, recall, F1
4. Fails if any detector drops below threshold:
   - Security detectors: 80% precision minimum
   - Quality detectors: 70% precision minimum
5. Outputs Markdown report with per-detector scores

### B4. Threshold Optimization

- Grid search over confidence thresholds per detector
- Maximize F1 score against labeled data
- Store optimized thresholds in `repotoire-cli/src/detectors/optimized_thresholds.rs`
- Re-run optimization when labels are updated

### B5. CI Integration

- GitHub Actions workflow: on PR, run benchmark suite
- Report precision delta as PR comment
- Block merge if precision regression detected (configurable)

---

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Self-analysis findings | 1,984 | < 800 |
| Self-analysis grade | C (75.8) | B+ (85+) |
| LazyClass FPs on Rust | ~300 | < 30 |
| Critical FPs | 5 (SQL injection on test fixtures) | 0 |
| Benchmark precision (security) | Unknown | ≥ 80% |
| Benchmark precision (quality) | Unknown | ≥ 70% |
| Findings with confidence scores | ~10% | 100% |

## Risks

| Risk | Mitigation |
|------|------------|
| Over-suppression (hiding true positives) | Benchmark pipeline catches precision drops; `--show-all` for debugging |
| Confidence signals interfering with each other | Phase 2 signals added incrementally with measurement |
| Manual labeling bottleneck | Start with 500 labels, expand; consider using existing linter output as proxy |
| Scope creep | Strict phasing: A ships first, C second, B third |

## Files Affected

### Phase A (existing files modified)
- `repotoire-cli/src/detectors/lazy_class.rs` — Rust/Java/C#/Go/Python exclusions
- `repotoire-cli/src/detectors/mod.rs` — `ignore-file` directive, test fixture auto-suppress
- `repotoire-cli/src/detectors/dead_code.rs` — cfg(test), pub API exemptions
- `repotoire-cli/src/detectors/unreachable_code.rs` — conditional compilation
- `repotoire-cli/src/calibrate/` — per-language threshold profiles

### Phase C (new + modified)
- `repotoire-cli/src/models.rs` — default confidence in Finding constructor
- `repotoire-cli/src/detectors/confidence_enrichment.rs` — **new**, post-detection pipeline
- `repotoire-cli/src/cli/analyze/mod.rs` — wire enrichment pipeline, `--min-confidence` flag
- `repotoire-cli/src/config/` — `min_confidence` config option
- `repotoire-cli/src/reporters/` — confidence display in all 5 formats
- `repotoire-cli/src/cli/triage.rs` — **new**, phase 2 triage command

### Phase B (new)
- `benchmark/` — **new directory**, project repos + labels
- `repotoire-cli/tests/benchmark_precision.rs` — **new**, precision harness
- `repotoire-cli/src/detectors/optimized_thresholds.rs` — **new**, data-driven thresholds
- `.github/workflows/benchmark.yml` — **new**, CI precision gate
