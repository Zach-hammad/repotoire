# Framework-Aware Scoring Design

**Date:** 2026-02-25
**Goal:** Wire `ProjectType` into the scoring module so bonus thresholds adjust per project type, not just detector thresholds.

## Context

The feature completeness audit found that `GraphScorer` has zero references to `ProjectType`. Detector thresholds already adjust for project type (compilers get lenient coupling thresholds), but the scoring bonus calculations use hardcoded thresholds that treat all project types identically.

A compiler with 50% cross-module coupling gets the same modularity score as a web app — both receive zero bonus. But 50% coupling is healthy for a compiler where AST/IR types are shared across every pass.

## Design

### Core: Scale bonus thresholds by project type multipliers

`ProjectType` already defines `coupling_multiplier()` (1.0-3.0) and `complexity_multiplier()` (1.0-2.0). Pipe these into the scorer's bonus calculations.

### Changes to `GraphScorer`

1. Add `repo_path: &'a Path` field so `GraphScorer` can resolve `ProjectType`
2. Scale bonus thresholds in four calculations:

**Modularity bonus** (coupling):
- Current: full bonus at coupling ≤ 0.3, zero at ≥ 0.7
- New: full bonus at `0.3 × coupling_mult`, zero at `min(0.7 × coupling_mult, 1.0)`
- Effect: Compiler (3.0x) gets full bonus at ≤ 0.9, Web (1.0x) unchanged

**Cohesion bonus**:
- Current: full bonus at cohesion ≥ 0.7, zero at ≤ 0.3
- New: full bonus at `max(0.7 / coupling_mult, 0.2)`, zero at `max(0.3 / coupling_mult, 0.1)`
- Effect: Compiler needs only ~23% cohesion for full bonus (high coupling = lower cohesion expected)

**Complexity distribution bonus**:
- Current: full bonus at 90%+ simple functions, zero at 50%
- New: full bonus at `max(0.9 / complexity_mult, 0.4)`, zero at `max(0.5 / complexity_mult, 0.2)`
- Effect: Kernel (2.0x) gets full bonus at 45%+ simple functions

**Clean dependencies bonus**: No change — cycle count is absolute, not relative to project type.

### Integration points

Two call sites need `repo_path`:
1. `cli/analyze/scoring.rs:19` — main scoring call
2. `cli/analyze/mod.rs:555` — explain_score path

### Logging

Log the resolved project type and adjusted thresholds at `debug!` level, consistent with existing scoring logs.

### Testing

Unit tests covering:
- Default (Web) project type: unchanged thresholds
- Compiler project type: lenient modularity bonus
- Kernel project type: lenient complexity bonus
- Explicit project type from config: correct override

## Files to modify

- `repotoire-cli/src/scoring/graph_scorer.rs` — add `repo_path`, scale bonus calculations
- `repotoire-cli/src/cli/analyze/scoring.rs` — pass `repo_path`
- `repotoire-cli/src/cli/analyze/mod.rs` — pass `repo_path` to scorer in explain_score

## Approach

Minimal change — reuse existing `coupling_multiplier()` and `complexity_multiplier()` values. No new config fields, no pillar weight changes, no penalty changes.
