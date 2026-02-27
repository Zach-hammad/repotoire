# Scoring Recalibration Design

## Problem

After Python class method extraction (+334 functions in Flask, +236 in FastAPI), Flask's health score dropped from A- (91.34) to B+ (89.27). Investigation revealed:

1. **The scoring formula is correct** — density normalization uses kLOC, not function count
2. **The -2.07 drop is from 8 new architecture findings** (shotgun surgery on Flask, Blueprint, Scaffold, App, _AppCtxGlobals)
3. **Root cause**: Flask is detected as `Web` (1.0x coupling multiplier) instead of `Framework` (3.0x)
4. **Secondary issue**: Architecture penalties don't account for API surface functions where high fan-in is expected

## Research

- [Role Stereotypes paper (arXiv 2406.19254)](https://arxiv.org/html/2406.19254v1): Controller/hub classes have only 2.7% smell prevalence — high connectivity is architecturally intentional
- [Code Smell Interactions (arXiv 2509.03896)](https://arxiv.org/html/2509.03896v2): Excludes third-party libraries; selects stricter thresholds to minimize FPs
- No existing paper proposes context-dependent thresholds based on project role — this is a gap

## Solution: Two Changes

### Part A: Fix Python/Rust Framework Detection

`score_framework_markers()` in `project_type_scoring.rs` only detects JS frameworks (React, Vue, Angular). Add detection for:

- **Python**: `pyproject.toml` classifiers containing `"Framework ::"` or `"Application Frameworks"`, or `name` matching known frameworks (flask, django, fastapi, starlette, tornado, sanic)
- **Rust**: `Cargo.toml` `[package] name` matching known frameworks (axum, actix-web, rocket, warp)

When Flask scores as `Framework`, coupling multiplier goes from 1.0x to 3.0x. Shotgun surgery thresholds scale automatically (min_callers: 5 -> 15, medium_files: 3 -> 9, etc.).

### Part C: API Surface Penalty Scaling

In `graph_scorer.rs` penalty loop (line 308-328), reduce architecture penalties by 50% for findings on API surface functions (exported + 3+ callers). Quality/security penalties unchanged.

This uses the existing `is_api_surface()` function. Public API entry points are expected to have high fan-in — coupling smells on them are less actionable.

## Files Changed

- `repotoire-cli/src/config/project_type_scoring.rs` — add Python/Rust framework detection
- `repotoire-cli/src/scoring/graph_scorer.rs` — API surface penalty discount
