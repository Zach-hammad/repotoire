# Feature Completeness Audit - Core Engine Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Execute a documentation-vs-code audit of Repotoire's core engine (detectors, parsers, graph, pipeline, scoring) and produce a gap list.

**Architecture:** Research-only audit - no code changes. Each task verifies documented claims against Rust source, then writes findings to a single audit report file.

**Tech Stack:** Grep, Read, file analysis only. Output: `docs/audits/2026-02-25-core-engine-gaps.md`

---

### Task 1: Create Audit Report Skeleton

**Files:**
- Create: `docs/audits/2026-02-25-core-engine-gaps.md`

**Step 1: Create the output file with section headers**

```markdown
# Core Engine Feature Completeness Audit
**Date:** 2026-02-25
**Scope:** Detectors, Parsers, Graph, Pipeline, Scoring
**Method:** Documentation-vs-Code Diff

## Summary
<!-- Fill after all sections complete -->

## 1. Detectors
### Documented but Missing
### Documented but Partial
### Undocumented (in code, not in docs)

## 2. Parsers
### Documented but Missing
### Documented but Partial
### Undocumented

## 3. Graph Layer
### Documented but Missing
### Documented but Partial
### Undocumented

## 4. Pipeline
### Documented but Missing
### Documented but Partial
### Undocumented

## 5. Scoring
### Documented but Missing
### Documented but Partial
### Undocumented
```

**Step 2: Commit**

```bash
mkdir -p docs/audits
git add docs/audits/2026-02-25-core-engine-gaps.md
git commit -m "docs: add audit report skeleton"
```

---

### Task 2: Audit Detectors - Hybrid Detector Documentation Mismatch

CLAUDE.md documents 8 "hybrid detectors" that wrap external Python/Node tools (Ruff, Pylint, Mypy, Bandit, Radon, Jscpd, Vulture, Semgrep). The Rust rewrite replaced all of these with native implementations.

**Files:**
- Read: `CLAUDE.md` (Hybrid Detector Suite table)
- Search: `repotoire-cli/src/detectors/` for any subprocess/external tool invocations
- Modify: `docs/audits/2026-02-25-core-engine-gaps.md`

**Step 1: Verify no external tool wrappers exist**

Search for subprocess calls, `Command::new`, `std::process` usage in detectors:
```bash
rg "Command::new|std::process|subprocess" repotoire-cli/src/detectors/
```

**Step 2: Document the mismatch**

Write to the Detectors section:
- [MISSING] RuffLintDetector - CLAUDE.md Hybrid Detector table, replaced by native Rust linting detectors
- [MISSING] PylintDetector - CLAUDE.md Hybrid Detector table, replaced by native detectors
- [MISSING] MypyDetector - CLAUDE.md Hybrid Detector table, type checking not reimplemented
- [MISSING] BanditDetector - CLAUDE.md Hybrid Detector table, replaced by native security detectors
- [MISSING] RadonDetector - CLAUDE.md Hybrid Detector table, complexity metrics are native
- [MISSING] JscpdDetector - CLAUDE.md Hybrid Detector table, replaced by DuplicateCodeDetector
- [MISSING] VultureDetector - CLAUDE.md Hybrid Detector table, replaced by DeadCodeDetector
- [MISSING] SemgrepDetector - CLAUDE.md Hybrid Detector table, security patterns are native

Note: These are expected mismatches from the Python→Rust migration. The functionality is covered by native detectors.

**Step 3: Check for undocumented detectors**

Cross-reference all .rs files in `repotoire-cli/src/detectors/` against CLAUDE.md detector lists:
- ML smell detectors (8 in `ml_smells/`) - not mentioned in CLAUDE.md
- Rust smell detectors (7 in `rust_smells/`) - only partially mentioned
- SurprisalDetector - not documented
- DepAuditDetector - not in detector tables
- InfiniteLoopDetector - not documented
- DeadStoreDetector - not documented

**Step 4: Commit**

```bash
git add docs/audits/2026-02-25-core-engine-gaps.md
git commit -m "docs: audit detectors - hybrid mismatch and undocumented detectors"
```

---

### Task 3: Audit Parsers - Missing Documented Features

CLAUDE.md claims specific parser features that aren't implemented.

**Files:**
- Read: `CLAUDE.md` (parser section, lines ~639-641)
- Read: `repotoire-cli/src/parsers/typescript.rs`
- Read: `repotoire-cli/src/parsers/java.rs`
- Read: `repotoire-cli/src/parsers/go.rs`
- Modify: `docs/audits/2026-02-25-core-engine-gaps.md`

**Step 1: Verify each claimed parser feature**

For TypeScript/JavaScript:
- Search for JSDoc extraction: `rg "jsdoc\|JSDoc\|/\*\*" repotoire-cli/src/parsers/typescript.rs`
- Search for React pattern detection: `rg "react\|React\|hook\|component" repotoire-cli/src/parsers/typescript.rs`

For Java:
- Search for Javadoc parsing: `rg "javadoc\|Javadoc\|/\*\*" repotoire-cli/src/parsers/java.rs`
- Search for annotation extraction: `rg "annotation\|@\|Annotation" repotoire-cli/src/parsers/java.rs`

For Go:
- Search for doc comment extraction: `rg "doc.comment\|comment" repotoire-cli/src/parsers/go.rs`
- Search for goroutine detection: `rg "goroutine\|go_statement\|chan" repotoire-cli/src/parsers/go.rs`
- Search for channel detection: `rg "channel\|chan\b" repotoire-cli/src/parsers/go.rs`

**Step 2: Document gaps**

Write to the Parsers section:
- [MISSING] TypeScript: JSDoc extraction - documented in CLAUDE.md, no implementation found
- [MISSING] TypeScript: React patterns - documented, only React import detection exists
- [MISSING] Java: Javadoc extraction - documented, not implemented
- [MISSING] Java: Annotation processing - documented, annotations visible in tests but not extracted
- [MISSING] Go: Doc comments - documented, not implemented
- [PARTIAL] Go: Goroutines - documented, only mentioned in code comment (not extracted/analyzed)
- [MISSING] Go: Channels - documented, not implemented

Also document what IS present:
- [PRESENT] All 9 language parsers (Python, TS/JS, Java, Go, Rust, C#, C, C++, lightweight fallback)
- [PRESENT] Tree-sitter for all parsers
- [PRESENT] Java interfaces, enums, records
- [PRESENT] Go structs, interfaces, methods with receivers

**Step 3: Check for undocumented parser features**

Look for C# parser features not mentioned in docs, C/C++ parser capabilities, etc.

**Step 4: Commit**

```bash
git add docs/audits/2026-02-25-core-engine-gaps.md
git commit -m "docs: audit parsers - missing JSDoc, Javadoc, Go channels"
```

---

### Task 4: Audit Graph Layer - Architecture Mismatch

The biggest finding: CLAUDE.md describes FalkorDB but the Rust codebase uses petgraph + redb.

**Files:**
- Read: `CLAUDE.md` (graph layer sections)
- Read: `repotoire-cli/src/graph/store.rs`
- Read: `repotoire-cli/src/graph/store_models.rs`
- Read: `repotoire-cli/src/graph/schema.rs`
- Read: `repotoire-cli/src/graph/interner.rs`
- Modify: `docs/audits/2026-02-25-core-engine-gaps.md`

**Step 1: Verify FalkorDB absence**

```bash
rg "falkordb\|FalkorDB\|redis" repotoire-cli/src/graph/
rg "petgraph\|redb" repotoire-cli/src/graph/
```

**Step 2: Verify node/edge type coverage**

Count implemented node types vs documented (9 documented, ~6 implemented).
Count implemented edge types vs documented (20+ documented, ~6 implemented).

**Step 3: Document gaps**

Write to the Graph Layer section:
- [MISSING] FalkorDB integration - CLAUDE.md describes Redis-based graph DB, actual implementation uses petgraph + redb
- [MISSING] Connection pooling - documented, not applicable (in-memory graph)
- [MISSING] Retry logic - documented, not applicable
- [PARTIAL] Node types - 6 of 9+ documented types implemented (missing: ExternalClass, ExternalFunction, BuiltinFunction, Type, Component, Domain, Concept, DetectorMetadata)
- [PARTIAL] Edge types - 6 of 20+ documented types implemented (missing: CALLS_CLASS, CALLS_EXT_FUNC, OVERRIDES, DECORATES, TESTS, RETURNS, HAS_PARAMETER, SUBTYPES, DATA_FLOWS_TO, SIMILAR_TO, FLAGGED_BY, etc.)
- [MISSING] Vector indexes - documented in RAG_API.md, not implemented
- [MISSING] Vector similarity search - documented, not implemented
- [MISSING] Tainted data flow queries - documented, not implemented
- [PARTIAL] String interning - infrastructure exists (StringInterner with lasso) but not actively used in GraphStore
- [PRESENT] Cycle detection (Tarjan's SCC) - fully implemented with tests
- [PRESENT] Multi-hop traversal - get_callers, get_callees, get_importers
- [PRESENT] Batch operations - add_nodes_batch, add_edges_batch
- [PRESENT] Graph metrics - fan_in, fan_out, call_fan_in, call_fan_out

**Step 4: Commit**

```bash
git add docs/audits/2026-02-25-core-engine-gaps.md
git commit -m "docs: audit graph layer - FalkorDB not implemented, schema partial"
```

---

### Task 5: Audit Pipeline - Incremental Analysis Gap

CLAUDE.md has an extensive incremental analysis section that may not be fully implemented in Rust.

**Files:**
- Read: `CLAUDE.md` (Incremental Analysis section)
- Read: `repotoire-cli/src/pipeline/mod.rs`
- Search: `repotoire-cli/src/` for hash-based change detection
- Read: `repotoire-cli/src/cache/` (incremental cache module)
- Modify: `docs/audits/2026-02-25-core-engine-gaps.md`

**Step 1: Verify incremental analysis implementation**

```bash
rg "md5\|MD5\|hash.*file\|file.*hash\|content_hash" repotoire-cli/src/
rg "incremental\|dependent_files\|find_dependent" repotoire-cli/src/
rg "force.full\|force_full" repotoire-cli/src/
```

**Step 2: Check pipeline module completeness**

Read `repotoire-cli/src/pipeline/mod.rs` and check if it's a thin stub or full implementation.
Check for `#![allow(dead_code)]` annotations.

**Step 3: Document gaps**

Write to the Pipeline section:
- [MISSING] Hash-based change detection (MD5 per file) - documented in CLAUDE.md Incremental Analysis section, not found in Rust
- [MISSING] Dependency-aware re-ingestion (3-hop traversal) - documented, not implemented
- [MISSING] `--force-full` CLI option - documented, not found
- [PARTIAL] Pipeline module - exists but marked `#![allow(dead_code)]`, appears under-developed
- [PRESENT] Incremental cache at findings level - IncrementalCache exists for caching analysis results
- [MISSING] Security validation (path validation, symlink rejection, file size limits) - documented in CLAUDE.md, not verified in pipeline

**Step 4: Commit**

```bash
git add docs/audits/2026-02-25-core-engine-gaps.md
git commit -m "docs: audit pipeline - incremental analysis not at graph level"
```

---

### Task 6: Audit Scoring - Framework-Aware Gaps

**Files:**
- Read: `repotoire-cli/src/scoring/graph_scorer.rs`
- Read: `repotoire-cli/src/calibrate/` (all files)
- Read: `repotoire-cli/src/config/project_type_scoring.rs`
- Modify: `docs/audits/2026-02-25-core-engine-gaps.md`

**Step 1: Verify scoring weights**

Check if default weights are actually 40/30/30 as documented:
```bash
rg "0\.4\|0\.3\|weight" repotoire-cli/src/scoring/
```

**Step 2: Verify framework-aware scoring**

```bash
rg "framework\|django\|flask\|fastapi\|react" repotoire-cli/src/scoring/
rg "framework\|django\|flask\|fastapi\|react" repotoire-cli/src/config/project_type_scoring.rs
```

**Step 3: Document gaps**

Write to the Scoring section:
- [PRESENT] Three-category scoring (Structure/Quality/Architecture) - fully implemented
- [PRESENT] Grade mapping (A+ through F) - extended grading system with clear thresholds
- [PRESENT] Adaptive thresholds (p90/p95) - full ThresholdResolver + StyleProfile system
- [MISSING] Framework-aware scoring adjustments - CLAUDE.md claims these exist, verify if project_type_scoring.rs is wired up
- [PRESENT] Configurable weights - pillar weights are configurable via ProjectConfig
- Verify: Whether default weights match documented 40/30/30

**Step 4: Commit**

```bash
git add docs/audits/2026-02-25-core-engine-gaps.md
git commit -m "docs: audit scoring - framework-aware gaps identified"
```

---

### Task 7: Write Summary and Final Commit

**Files:**
- Modify: `docs/audits/2026-02-25-core-engine-gaps.md`

**Step 1: Write the Summary section**

Count totals across all subsystems:
- Total MISSING items
- Total PARTIAL items
- Total UNDOCUMENTED items
- Total PRESENT items

Highlight the top-level findings:
1. CLAUDE.md describes the Python/FalkorDB architecture; Rust uses petgraph + redb
2. 8 hybrid detectors documented but replaced by native Rust implementations
3. Parser feature claims (JSDoc, Javadoc, Go channels) not implemented
4. Incremental analysis at wrong layer (findings vs graph)
5. Framework-aware scoring not implemented

**Step 2: Final commit**

```bash
git add docs/audits/2026-02-25-core-engine-gaps.md
git commit -m "docs: complete core engine feature completeness audit"
```
