# Feature Completeness Audit - Core Engine

**Date**: 2026-02-25
**Type**: Feature completeness audit
**Goal**: Ship readiness - identify gaps between documented and implemented features
**Scope**: Core engine only (detectors, parsers, graph, pipeline, scoring)
**Approach**: Documentation-vs-Code Diff

## Context

Repotoire is a 93K-line Rust codebase with 100 detectors, 9 language parsers, and extensive infrastructure. Before shipping, we need to verify that every feature documented in CLAUDE.md, README.md, and docs/ has a corresponding, wired-up implementation in the Rust source.

## Subsystems Under Audit

| Subsystem | Source Location | Documentation Sources |
|-----------|----------------|----------------------|
| **Detectors** | `repotoire-cli/src/detectors/` | CLAUDE.md detector tables, docs/ |
| **Parsers** | `repotoire-cli/src/parsers/` | CLAUDE.md parser sections |
| **Graph** | `repotoire-cli/src/graph/` | CLAUDE.md graph schema, queries |
| **Pipeline** | `repotoire-cli/src/pipeline/` | CLAUDE.md pipeline flow |
| **Scoring** | `repotoire-cli/src/scoring/` | CLAUDE.md scoring framework |

## Audit Method

For each subsystem:
1. Extract every feature claim from CLAUDE.md, README.md, and docs/
2. Search the Rust source for corresponding implementation
3. Classify each claim as:
   - **Present**: Implementation exists and appears functional
   - **Partial**: Implementation exists but is incomplete (stubbed, TODO, missing key logic)
   - **Missing**: No implementation found for documented feature
   - **Undocumented**: Implementation exists but isn't documented (bonus finds)

## Output Format

Gap list organized by subsystem:
```
## Subsystem: [Name]
- [MISSING] Feature X - documented in CLAUDE.md line Y, no implementation found
- [PARTIAL] Feature Z - implementation in file.rs:123, but missing ABC
```

## Scope Boundaries

**In scope**: Feature existence and wiring. Does the code exist and is it reachable?
**Out of scope**: Correctness, performance, test coverage, non-core features (CLI, MCP, reporters, config).
