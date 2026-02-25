# Repotoire Full Audit + Improvement Roadmap

**Date:** 2026-02-25
**Approach:** Signal-to-Noise First (Approach B)
**Timeline:** No fixed deadline — quality over speed

## Current State (Audit Findings)

| Metric | Value |
|--------|-------|
| Rust LOC | 92,698 |
| Detector files | 97 |
| Self-analysis score | 92.5/100 (A-) |
| Self-analysis findings | 725 (4 critical, 134 high, 311 medium, 276 low) |
| Estimated FP rate | ~28% (~200+ false positives) |
| Known bugs | 3 (serialization crash, MCP path traversal, cache field drop) |
| Unwrap calls (non-test) | 691 across 112 files |
| Compilation warnings | 1 (unused imports) |
| Test functions | 796 |
| Dependencies | All current, no known CVEs |

## Design Principles

1. **Fix the signal before amplifying it** — no point integrating with GitHub if findings are 30% noise
2. **Self-analysis as benchmark** — Repotoire analyzing itself is the gold standard; track improvements quantitatively
3. **Zero unwraps in non-test code** — production-grade reliability
4. **Validate on real-world projects** — Flask, FastAPI, Django, Express as external benchmarks
5. **Both distribution channels equally** — CLI/MCP and GitHub integration are co-primary

---

## Phase 1: Signal Quality + Critical Fixes (Weeks 1-2)

**Goal:** Self-analysis from 725 findings (~200 FPs) to <300 findings with <5% FP rate.

### 1a. Fix 3 Known Bugs (2-3 days)

| Bug | Root Cause | Fix |
|-----|-----------|-----|
| `findings` command crash | Empty `threshold_metadata` serializes as `null`; Serde can't deserialize null into HashMap | Serialize empty HashMap as `{}` |
| MCP path traversal bypass | `canonicalize().unwrap_or(raw_path)` falls back to non-canonical path | Return error if canonicalize fails |
| Cache field drop | `CachedFinding` missing `threshold_metadata` | Add field to struct + update serialization |

### 1b. False Positive Reduction (5-7 days)

- **Orchestrator pattern detection:** Auto-detect routers, parsers, controllers, handler registries by analyzing graph degree + naming patterns. Lower severity for legitimate high-coupling nodes. Target: eliminate ~150 FPs from "central coordinator" category.
- **Masking layer extension:** Extend tree-sitter masking layer to 23 more detectors that currently use raw regex. Target: eliminate ~50 FPs from string/comment false matches.
- **Test code exclusion refinement:** Ensure all detectors consistently skip test files/modules.
- **Configurable thresholds via `repotoire.toml`:** Per-detector threshold overrides for user sensitivity tuning.

### 1c. Pipeline Hardening (2-3 days)

- `validate_file()` function for symlink detection + path traversal prevention
- Expose hidden `--since` flag for incremental analysis
- Pre-filter oversized files at collection time (not just parse time)

**Benchmark:** Run self-analysis before/after. Publish delta.

---

## Phase 2: Self-Analysis Benchmark + Unwrap Elimination (Weeks 3-5)

**Goal:** Establish quantitative benchmark. Zero unwraps in non-test code.

### 2a. Self-Analysis Benchmark System (2-3 days)

- `benchmarks/` directory with self-analysis assertion script:
  - Total findings < 300
  - FP rate < 5% (manually verified baseline)
  - No critical findings in Rust source
  - Score >= 95.0
- CI integration for regression detection
- Curated `expected_findings.json` with true/false positive labels

### 2b. Unwrap Elimination (7-10 days)

**Scope:** All 691 unwraps in non-test code.

**Priority order:**
1. `graph/store.rs` (56 unwraps — critical path)
2. Top 5 detector offenders: panic_density (18), insecure_tls (17), incremental_cache (17), eval_detector (16), empty_catch (13)
3. Parsers (132 unwraps)
4. Remaining detectors
5. Reporters + CLI

**Patterns:**
- `Regex::new("...").unwrap()` → `OnceLock` or `LazyLock` for compiled regexes
- `.unwrap()` on infallible ops → `.expect("reason: invariant X holds because Y")`
- All others → `Result<T, E>` with `?` operator
- Enforcement: `#![deny(clippy::unwrap_used)]`

### 2c. Real-World Validation (3-4 days)

- Run against Flask, FastAPI, Django, Express
- Compare finding counts and severity distributions before/after Phase 1
- Document results in validation report

---

## Phase 3: Test Coverage + Documentation Integrity (Weeks 6-8)

**Goal:** Test coverage to 80%+. Zero documentation-implementation gaps.

### 3a. Test Coverage Blitz (5-7 days)

| Priority | Category | Target | Current |
|----------|----------|--------|---------|
| P1 | Security detectors | 90%+ | ~27% |
| P2 | Framework detectors (React, Django, Express) | 80%+ | 0% |
| P3 | MCP tool handlers (error paths, edge cases) | 80%+ | Partial |
| P4 | Scoring algorithm (property-based tests) | Match Lean 4 proofs | Partial |
| P5 | Reporters (JSON schema, HTML, SARIF compliance) | 80%+ | Partial |

### 3b. Documentation-Implementation Gap Closure (3-4 days)

- Wire `ProjectType` into `GraphScorer` bonus calculations
- Validate all CLAUDE.md feature claims against actual code
- Remove or implement documented-but-missing features
- Update docs to reflect actual state

### 3c. Dead Code + Warning Cleanup (1-2 days)

- Fix unused `Language` and `LightweightParseStats` imports
- Resolve 3 deleted-but-uncommitted files (context_hmm.rs, sql_injection.rs, taint.rs)
- `cargo clippy -- -D warnings` clean
- Audit 58 unsafe blocks — document safety invariants

---

## Phase 4: Market Features (Weeks 9-12)

**Goal:** Ship GitHub integration + CLI polish. VS Code extension deferred to separate project.

### 4a. GitHub PR Integration (Weeks 9-11)

- **SARIF upload (lowest effort, highest value):** Leverage existing SARIF reporter for GitHub Code Scanning integration
- **GitHub Actions workflow:** Publish reusable action (`repotoire/action`) for CI integration
- **Differential analysis:** Only report findings introduced in the PR diff
- **GitHub App (stretch):** OAuth app with Check Runs API for pass/fail status checks
- **Quality gates:** Configurable thresholds (e.g., no new critical/high findings)

### 4b. CLI/MCP Polish (Weeks 11-12)

- Guided `repotoire init` with interactive prompts
- `repotoire diff` command — findings between two commits/branches
- MCP streaming progress updates during long analyses
- Better `--help` text and colored output defaults
- Configuration wizard for interactive threshold tuning

### 4c. Packaging + Distribution (Ongoing)

- cargo-binstall prebuilt binaries
- Homebrew formula
- npm wrapper testing
- Docker image for CI

---

## Deferred (Separate Projects)

- VS Code extension
- Web dashboard
- Free cloud tier / SaaS
- Custom rule engine
- Team analytics
- IDE plugins (JetBrains)

---

## Success Metrics

| Metric | Current | Phase 1 Target | Phase 2 Target | Phase 4 Target |
|--------|---------|---------------|---------------|---------------|
| Self-analysis findings | 725 | <300 | <250 | <200 |
| FP rate | ~28% | <5% | <3% | <2% |
| Self-analysis score | 92.5 | 95+ | 97+ | 98+ |
| Unwraps (non-test) | 691 | 691 | 0 | 0 |
| Test functions | 796 | 830+ | 900+ | 1000+ |
| Known bugs | 3 | 0 | 0 | 0 |
| GitHub integration | None | None | None | SARIF + Actions |

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| FP reduction breaks true positives | Self-analysis benchmark catches regressions in CI |
| Unwrap elimination changes detector behavior | Run full test suite + self-analysis after each module |
| GitHub integration scope creep | Start with SARIF upload (existing code), only build GitHub App if needed |
| Real-world validation reveals new FP categories | Feed back into Phase 1 masking layer as iterative improvements |
