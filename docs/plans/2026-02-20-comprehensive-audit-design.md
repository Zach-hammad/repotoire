# Repotoire Comprehensive Audit & Fix Design

**Date:** 2026-02-20
**Approach:** Bottom-Up Fix Pass + Self-Analysis
**Scope:** All 17 issues identified in project audit

## Phase 1: Cache Architecture Unification

**Problem:** Three independent cache layers with no shared interface. Previously caused 7 bugs (score drift, empty caches, duplicate findings).

**Solution:**
- Define `CacheLayer` trait with `get`, `put`, `invalidate`, `flush` methods
- Create `CacheCoordinator` struct owning all three layers with cascading invalidation
- File content invalidation triggers finding invalidation automatically
- Add integration tests verifying cross-layer consistency
- Wire coordinator through analysis pipeline via dependency injection

**Files:** `cache/mod.rs`, `cache/paths.rs`, `detectors/incremental_cache.rs`, `pipeline/`, new `cache/coordinator.rs`

## Phase 2: Unwrap/Panic Aggressive Cleanup

**Problem:** 511 unwrap/panic calls across 112 files.

**Solution:**
- **Critical paths first:** `graph/store.rs` (56 unwraps), parsers, analysis pipeline — convert to `Result<T, E>` with `?`
- **Detector paths:** All 115 detectors return `Result<DetectorResult, DetectorError>` instead of panicking
- **Remaining:** Config loading, CLI, utilities — use `anyhow`/`thiserror` for error chains

**Policy:**
- Zero `unwrap()` in non-test code
- `expect()` only with comment explaining infallibility (e.g., hardcoded regex)
- `panic!()` only via `unreachable!()` in truly unreachable paths

## Phase 3: Detector Completeness

### 3a. NoSQL Injection
Wire into taint detector system. Add MongoDB/Redis/Elasticsearch sink patterns. Replace regex-only with graph-based data flow tracking.

### 3b. SSA Taint Flow
Implement `SsaFlow` trait (already defined in `taint.rs`). Build basic SSA form from tree-sitter ASTs for cross-line variable tracking. Fixes multi-line taint gap.

### 3c. Inline Suppression Comments
Parse `// repotoire:ignore` and `# repotoire:ignore` during detection. Support targeted suppression via `repotoire:ignore[detector-name]`.

### 3d. Orchestrator Pattern Detection
Auto-detect routers/controllers/dispatchers by pattern (many method calls to different services, low internal state). Lower severity or skip God Class / Feature Envy for these.

### 3e. Webhook Validation
Change from log-warning to default-deny when webhook secret is not configured.

### 3f. Threshold Harmonization
Replace hardcoded `const` thresholds with adaptive threshold lookups. Fall back to hardcoded defaults only when no calibration profile exists.

## Phase 4: Self-Analysis Investigation

After each fix phase:
1. Build and run `repotoire analyze` on its own source
2. Compare findings against manual audit
3. For each missed issue, determine root cause:
   - Missing detector → create new detector
   - False negative → fix detection logic
   - Below threshold → tune thresholds
   - Suppressed → review config
4. Document in self-analysis report
5. Create new detectors as needed (e.g., `panic_density`, architectural cohesion)

**Expected investigation targets:**
- Cache fragmentation detection (architectural issue — likely needs new detector)
- Unwrap density flagging (should have `panic_density` detector)
- TODO in production code (placeholder detector should catch this)
- Webhook default-allow (insecure_defaults detector should catch this)

## Issue Severity Summary

| # | Severity | Issue |
|---|----------|-------|
| 1 | Critical | Three-layer cache without unified interface |
| 2 | Critical | NoSQL injection detector incomplete |
| 3 | Critical | TODO in production format string |
| 4 | High | 511 unwrap/panic occurrences |
| 5 | High | Hardcoded thresholds conflict with adaptive system |
| 6 | High | Webhook validation logs instead of failing |
| 7 | High | Two-pass analysis for adaptive thresholds |
| 8 | Medium | No inline suppression comments |
| 9 | Medium | Data clumps uses heuristics only |
| 10 | Medium | Missing orchestrator pattern detection |
| 11 | Medium | SSA taint flow trait not implemented |
| 12 | Medium | Multi-line taint not tracked |
| 13 | Low | Missing metrics (LOC=0) for some findings |
| 14 | Low | Too many "central coordinator" findings (noise) |
| 15 | Low | expect() in regex compilation |
| 16 | Low | Rate limits hard-coded |
| 17 | Low | Dependency vulnerability scanning not automated |
