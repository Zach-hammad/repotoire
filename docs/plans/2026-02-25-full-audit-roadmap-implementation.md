# Repotoire Full Audit + Improvement Roadmap — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve Repotoire's signal quality, reliability, test coverage, and market readiness through a phased approach prioritizing false positive reduction.

**Architecture:** Signal-to-noise first — fix self-analysis findings, eliminate unwraps, boost test coverage, then ship GitHub integration.

**Tech Stack:** Rust (cargo, clippy, rayon), tree-sitter, petgraph, GitHub Actions, SARIF

---

## Current State (verified 2026-02-25)

| Metric | Value |
|--------|-------|
| Self-analysis (Rust CLI only) | 119 findings (0C, 1H, 23M, 94L) |
| Full repo analysis | 328 findings (16C, 24H, 158M, 130L) |
| Score | 96.1/100 (A) |
| Unwraps (non-test) | 670 |
| Tests | 838 |
| Compilation warnings | 1 (unused imports) |

---

## Phase 1: Signal Quality + Cleanup (Weeks 1-2)

### Task 1: Fix AI Churn Detector False Positives

The AI churn detector generates 15+ critical findings on Repotoire itself (e.g., "AI churn pattern in `env_key`"). These are FPs because Repotoire is a legitimately actively-developed project, not AI-generated slop.

**Files:**
- Modify: `repotoire-cli/src/detectors/ai_churn.rs:25-45` (thresholds)
- Modify: `repotoire-cli/src/detectors/ai_churn.rs` (detect method — add self-exclusion heuristics)

**Step 1: Write failing test for FP scenario**

Add a test in `ai_churn.rs` that verifies a function with legitimate iterative development (3+ commits over 48h but with meaningful changes) does NOT produce a Critical finding:

```rust
#[test]
fn test_no_critical_for_legitimate_iterative_development() {
    // A function created and modified 3 times within 48h
    // but each modification is meaningful (not AI fix-up)
    // should produce at most a Low finding, not Critical
    let record = FunctionChurnRecord {
        qualified_name: "module::legitimate_function".to_string(),
        file_path: "src/lib.rs".to_string(),
        function_name: "legitimate_function".to_string(),
        created_at: Some(Utc::now() - Duration::hours(72)),
        creation_commit: "abc123".to_string(),
        lines_original: 20,
        first_modification_at: Some(Utc::now() - Duration::hours(48)),
        first_modification_commit: "def456".to_string(),
        modifications: vec![
            Modification {
                timestamp: Utc::now() - Duration::hours(48),
                commit_sha: "def456".to_string(),
                lines_added: 5,
                lines_deleted: 2,
            },
            Modification {
                timestamp: Utc::now() - Duration::hours(24),
                commit_sha: "ghi789".to_string(),
                lines_added: 8,
                lines_deleted: 3,
            },
            Modification {
                timestamp: Utc::now() - Duration::hours(1),
                commit_sha: "jkl012".to_string(),
                lines_added: 3,
                lines_deleted: 1,
            },
        ],
    };
    let score = record.churn_score();
    // Meaningful modifications (adding more than deleting) should score lower
    assert!(score < MIN_CHURN_SCORE, "Legitimate dev should not trigger churn finding");
}
```

**Step 2: Run test to verify it fails**

Run: `cargo test -p repotoire test_no_critical_for_legitimate_iterative_development -- --nocapture`
Expected: FAIL

**Step 3: Improve churn scoring to reduce FPs**

In `ai_churn.rs`, modify the `churn_score()` method to account for:
- **Net-positive modifications**: If `lines_added >> lines_deleted`, it's feature development, not AI fix-up
- **Commit message diversity**: AI fix-up commits tend to have similar messages
- **Churn ratio floor**: Only flag when `lines_modified / lines_original > 1.5` (currently 0.5)

Raise `MIN_CHURN_SCORE` from `0.8` to `1.2` and `MEDIUM_CHURN_RATIO` from `0.5` to `0.8`.

**Step 4: Run test to verify it passes**

Run: `cargo test -p repotoire test_no_critical_for_legitimate_iterative_development -- --nocapture`
Expected: PASS

**Step 5: Run self-analysis to verify FP reduction**

Run: `cargo run --release -- analyze /home/zhammad/personal/repotoire 2>&1 | tail -20`
Expected: Critical count drops from 16 to <5

**Step 6: Commit**

```bash
git add repotoire-cli/src/detectors/ai_churn.rs
git commit -m "fix: reduce AI churn detector false positives on active projects"
```

---

### Task 2: Reduce Deep Nesting Findings

17 of 23 medium findings are "Excessive nesting: 5-6 levels." Many are in complex but unavoidable control flow (parser dispatching, config loading).

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/mod.rs:200` (6 levels)
- Modify: `repotoire-cli/src/cli/analyze/files.rs:211` (6 levels)
- Modify: `repotoire-cli/src/scoring/graph_scorer.rs:292` (5 levels)
- Modify: `repotoire-cli/src/git/enrichment.rs:224` (6 levels)
- Modify: `repotoire-cli/src/cli/analyze/graph.rs:528` (6 levels)
- Plus ~12 more files with 5-level nesting

**Step 1: Extract helper functions to reduce nesting**

For each file with 6-level nesting, use early returns and helper extraction:
- Convert `if condition { ... long block ... }` to `if !condition { return; }` + flat code
- Extract deeply nested match arms into named helper functions
- Use `?` operator to flatten error handling chains

**Step 2: Run self-analysis after each file**

Run: `cargo run --release -- findings 2>&1 | grep -c "nesting"`
Track: Nesting findings should decrease

**Step 3: Commit per batch of 3-5 files**

```bash
git commit -m "refactor: reduce nesting depth in analyze pipeline"
```

---

### Task 3: Commit Deleted Files and Fix Unused Import

Three files show as deleted but uncommitted. One unused import warning persists.

**Files:**
- Delete: `repotoire-cli/src/detectors/context_hmm.rs` (moved to `context_hmm/mod.rs`)
- Delete: `repotoire-cli/src/detectors/sql_injection.rs` (moved or removed)
- Delete: `repotoire-cli/src/detectors/taint.rs` (moved or removed)
- Modify: `repotoire-cli/src/parsers/mod.rs:27` (unused imports)

**Step 1: Verify the deleted files are truly superseded**

Check if `context_hmm/mod.rs`, `sql_injection/mod.rs`, or equivalent replacements exist:

```bash
ls -la repotoire-cli/src/detectors/context_hmm/
ls -la repotoire-cli/src/detectors/sql_injection/
ls -la repotoire-cli/src/detectors/taint/
```

**Step 2: Stage and commit deletions**

```bash
git add repotoire-cli/src/detectors/context_hmm.rs
git add repotoire-cli/src/detectors/sql_injection.rs
git add repotoire-cli/src/detectors/taint.rs
git commit -m "chore: remove superseded detector files (moved to module dirs)"
```

**Step 3: Fix unused import**

In `repotoire-cli/src/parsers/mod.rs:27`, remove `Language` and `LightweightParseStats` from the `pub use` statement if they're no longer needed externally.

**Step 4: Verify clean compilation**

Run: `cargo check 2>&1`
Expected: 0 warnings

**Step 5: Commit**

```bash
git add repotoire-cli/src/parsers/mod.rs
git commit -m "fix: remove unused Language and LightweightParseStats imports"
```

---

### Task 4: Architecture Consensus Finding Review

Two consensus findings exist: one about `find()` (576 callers, high-impact function) and architecture detector consensus findings. Evaluate whether these are true positives.

**Files:**
- Review: `repotoire-cli/src/detectors/ai_boilerplate.rs:139` (find function)
- Modify: `repotoire-cli/src/detectors/architectural_bottleneck.rs` (threshold tuning if needed)
- Modify: `repotoire-cli/src/detectors/degree_centrality.rs` (threshold tuning if needed)

**Step 1: Evaluate the `find()` function**

Read `repotoire-cli/src/detectors/ai_boilerplate.rs:139` and understand why `find()` has 576 callers. If it's a utility function (like a HashMap lookup wrapper), it's a true positive — refactor. If it's a standard library function reference, it's an FP — adjust the detector.

**Step 2: Decide and act**

If TP: refactor the function to reduce coupling.
If FP: add exclusion logic for stdlib/builtin function references in the detector.

**Step 3: Commit**

```bash
git commit -m "fix: address high-impact function finding in ai_boilerplate"
```

---

### Task 5: Self-Analysis Benchmark Script

Create an automated benchmark that CI can use to prevent regressions.

**Files:**
- Create: `benchmarks/self_analysis.sh`
- Create: `benchmarks/expected_findings.json` (baseline)

**Step 1: Write the benchmark script**

```bash
#!/bin/bash
set -euo pipefail

echo "=== Repotoire Self-Analysis Benchmark ==="

cd "$(dirname "$0")/.."
RESULT=$(cd repotoire-cli && cargo run --release -- analyze .. --format json 2>/dev/null)

TOTAL=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['findings_summary']['total'])")
CRITICAL=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['findings_summary']['critical'])")
HIGH=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['findings_summary']['high'])")
SCORE=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['overall_score'])")

echo "Score: $SCORE"
echo "Total findings: $TOTAL (C:$CRITICAL H:$HIGH)"

# Assertions
PASS=true
if (( $(echo "$SCORE < 95.0" | bc -l) )); then
    echo "FAIL: Score $SCORE < 95.0"
    PASS=false
fi
if [ "$CRITICAL" -gt 5 ]; then
    echo "FAIL: $CRITICAL critical findings (max 5)"
    PASS=false
fi
if [ "$TOTAL" -gt 200 ]; then
    echo "FAIL: $TOTAL total findings (max 200)"
    PASS=false
fi

if $PASS; then
    echo "=== BENCHMARK PASSED ==="
else
    echo "=== BENCHMARK FAILED ==="
    exit 1
fi
```

**Step 2: Run it to establish baseline**

Run: `bash benchmarks/self_analysis.sh`
Expected: Current numbers should pass or identify exactly what needs fixing.

**Step 3: Commit**

```bash
git add benchmarks/
git commit -m "feat: add self-analysis benchmark script for CI regression detection"
```

---

## Phase 2: Unwrap Elimination (Weeks 3-5)

### Task 6: Eliminate Unwraps in MCP Handlers (139 unwraps)

**Files:**
- Modify: `repotoire-cli/src/mcp/tools/analysis.rs`
- Modify: `repotoire-cli/src/mcp/tools/graph_queries.rs`
- Modify: `repotoire-cli/src/mcp/tools/evolution.rs`
- Modify: `repotoire-cli/src/mcp/tools/ai.rs`
- Modify: `repotoire-cli/src/mcp/tools/files.rs`

**Step 1: Audit unwrap patterns**

```bash
grep -n '\.unwrap()' repotoire-cli/src/mcp/ -r
```

Categorize each unwrap:
- **Regex compilation**: Convert to `LazyLock<Regex>` or `OnceLock`
- **JSON serialization**: Convert to `?` with anyhow context
- **Lock acquisition**: Keep as `.expect("lock poisoned")` (appropriate)
- **Option access**: Convert to `.ok_or_else(|| anyhow!("reason"))?`

**Step 2: Convert each file, one at a time**

For each file:
1. Replace `Regex::new("...").unwrap()` with `static RE: LazyLock<Regex> = LazyLock::new(|| Regex::new("...").expect("valid regex"));`
2. Replace `.unwrap()` on fallible ops with `?` or `.context("what happened")?`
3. Run `cargo check` after each file
4. Run `cargo test` after each file

**Step 3: Commit per file**

```bash
git commit -m "refactor: eliminate unwraps in mcp/tools/analysis.rs"
```

---

### Task 7: Eliminate Unwraps in Parsers (132 unwraps)

**Files:**
- Modify: All files in `repotoire-cli/src/parsers/` (python.rs, typescript.rs, rust_parser.rs, go.rs, java.rs, csharp.rs, c.rs, cpp.rs, mod.rs)

**Step 1: Audit parser unwrap patterns**

Most parser unwraps are on tree-sitter operations:
- `node.child_by_field_name("name").unwrap()` — fallible, convert to `if let Some()`
- `Regex::new().unwrap()` — convert to `LazyLock`
- `str::from_utf8().unwrap()` — convert to `?`

**Step 2: Convert each parser file**

Pattern for tree-sitter node access:
```rust
// Before:
let name = node.child_by_field_name("name").unwrap();
// After:
let Some(name) = node.child_by_field_name("name") else { continue; };
```

**Step 3: Run parser tests after each file**

Run: `cargo test -p repotoire parsers -- --nocapture`

**Step 4: Commit per parser**

```bash
git commit -m "refactor: eliminate unwraps in python parser"
```

---

### Task 8: Eliminate Unwraps in Detectors (342 unwraps)

This is the largest module. Work through by sub-category.

**Files:**
- Modify: All detector files in `repotoire-cli/src/detectors/`

**Step 1: Start with top 5 offenders**

1. `rust_smells/panic_density.rs` (18 unwraps)
2. `insecure_tls.rs` (17 unwraps)
3. `incremental_cache.rs` (17 unwraps)
4. `eval_detector.rs` (16 unwraps)
5. `empty_catch.rs` (13 unwraps)

**Step 2: Common patterns**

```rust
// Regex — use LazyLock
use std::sync::LazyLock;
static PATTERN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"...").expect("valid regex: PATTERN")
});

// HashMap access — use if let
// Before: map.get("key").unwrap()
// After: let Some(val) = map.get("key") else { continue; };

// String parsing — use ? or if let
// Before: line.split(':').nth(1).unwrap()
// After: let Some(val) = line.split(':').nth(1) else { continue; };
```

**Step 3: Run detector tests after each batch**

Run: `cargo test -p repotoire detectors -- --nocapture`

**Step 4: Commit per batch of 3-5 detectors**

```bash
git commit -m "refactor: eliminate unwraps in security detectors (insecure_tls, eval_detector)"
```

---

### Task 9: Eliminate Remaining Unwraps (reporters, CLI, config, etc.)

**Files:**
- Modify: `repotoire-cli/src/reporters/` (26 unwraps)
- Modify: `repotoire-cli/src/cli/` (16 unwraps)
- Modify: `repotoire-cli/src/config/` (9 unwraps)
- Modify: `repotoire-cli/src/classifier/` (8 unwraps)
- Modify: Others (calibrate, fixes, git, scoring — <10 each)

**Step 1: Apply same patterns as Tasks 6-8**

**Step 2: Add clippy deny lint**

After all unwraps are eliminated, add to `repotoire-cli/src/main.rs`:
```rust
#![deny(clippy::unwrap_used)]
```

**Step 3: Verify clean build**

Run: `cargo clippy -- -D clippy::unwrap_used 2>&1 | head -20`
Expected: No violations

**Step 4: Commit**

```bash
git commit -m "refactor: enable clippy::unwrap_used deny lint — zero unwraps in production code"
```

---

## Phase 3: Test Coverage + Documentation (Weeks 6-8)

### Task 10: Security Detector Test Coverage

**Files:**
- Modify: `repotoire-cli/src/detectors/xss.rs` (add tests)
- Modify: `repotoire-cli/src/detectors/ssrf.rs` (add tests)
- Modify: `repotoire-cli/src/detectors/command_injection.rs` (add tests)
- Modify: `repotoire-cli/src/detectors/path_traversal.rs` (add tests)
- Modify: `repotoire-cli/src/detectors/nosql_injection.rs` (add tests)
- Modify: `repotoire-cli/src/detectors/log_injection.rs` (add tests)

**Step 1: For each security detector, add multi-language test cases**

Each detector should have tests covering:
- Python true positive
- JavaScript/TypeScript true positive
- Rust/Go true positive (if applicable)
- False positive: string literal containing pattern
- False positive: comment containing pattern
- False positive: test code containing pattern

**Step 2: Run tests after each detector**

Run: `cargo test -p repotoire detectors::xss -- --nocapture`

**Step 3: Commit per detector batch**

```bash
git commit -m "test: add multi-language test cases for XSS detector"
```

---

### Task 11: Framework Detector Test Coverage

**Files:**
- Modify: `repotoire-cli/src/detectors/react_hooks.rs` (add tests)
- Modify: `repotoire-cli/src/detectors/django_security.rs` (add tests)
- Modify: `repotoire-cli/src/detectors/express_security.rs` (add tests)

**Step 1: Add realistic test fixtures**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_detects_hooks_outside_component() {
        let code = r#"
function notAComponent() {
    const [state, setState] = useState(0);
    return state;
}
"#;
        // Test detection logic
    }

    #[test]
    fn test_no_false_positive_in_component() {
        let code = r#"
function MyComponent() {
    const [state, setState] = useState(0);
    return <div>{state}</div>;
}
"#;
        // Should not produce a finding
    }
}
```

**Step 2: Run and commit**

```bash
cargo test -p repotoire detectors::react_hooks -- --nocapture
git commit -m "test: add framework detector test coverage (React, Django, Express)"
```

---

### Task 12: Real-World Validation

**Files:**
- Create: `benchmarks/validate_real_world.sh`

**Step 1: Write validation script**

Script that clones Flask, FastAPI, Django (or uses local copies) and runs Repotoire analysis, capturing:
- Total findings count
- FP rate (manually reviewed subset)
- Score distribution
- Detector breakdown

**Step 2: Run against 3 real projects**

```bash
# Flask (Python, small-medium)
repotoire analyze /tmp/flask --format json > benchmarks/results/flask.json

# FastAPI (Python, medium)
repotoire analyze /tmp/fastapi --format json > benchmarks/results/fastapi.json

# Express (JS/TS, medium)
repotoire analyze /tmp/express --format json > benchmarks/results/express.json
```

**Step 3: Document findings and adjust thresholds**

Create `benchmarks/VALIDATION_REPORT.md` with per-project analysis.

**Step 4: Commit**

```bash
git commit -m "test: add real-world validation suite (Flask, FastAPI, Express)"
```

---

### Task 13: Documentation-Implementation Parity

**Files:**
- Modify: `CLAUDE.md` — update any stale claims
- Modify: `README.md` — update feature list to match reality

**Step 1: Cross-reference CLAUDE.md claims against code**

For each claimed feature in CLAUDE.md, verify with `grep` that the implementation exists. Flag any mismatches.

**Step 2: Update documentation**

Remove claims for features that don't exist. Add documentation for features that exist but aren't documented.

**Step 3: Commit**

```bash
git commit -m "docs: sync CLAUDE.md and README.md with actual implementation state"
```

---

## Phase 4: Market Features (Weeks 9-12)

### Task 14: SARIF Upload for GitHub Code Scanning

The SARIF reporter already exists. This task wires it into a GitHub Actions workflow.

**Files:**
- Create: `.github/workflows/repotoire-analysis.yml`
- Create: `action.yml` (reusable composite action)

**Step 1: Create GitHub Actions workflow**

```yaml
name: Repotoire Analysis

on:
  pull_request:
    branches: [main]

permissions:
  security-events: write

jobs:
  analyze:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install Repotoire
        run: cargo binstall repotoire --no-confirm || cargo install repotoire

      - name: Run Analysis
        run: repotoire analyze . --format sarif --output results.sarif.json

      - name: Upload SARIF
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: results.sarif.json
```

**Step 2: Create reusable action**

```yaml
# action.yml
name: 'Repotoire Analysis'
description: 'Run Repotoire code health analysis'
inputs:
  path:
    description: 'Path to analyze'
    default: '.'
  format:
    description: 'Output format'
    default: 'sarif'
runs:
  using: 'composite'
  steps:
    - run: |
        cargo binstall repotoire --no-confirm 2>/dev/null || cargo install repotoire
        repotoire analyze ${{ inputs.path }} --format ${{ inputs.format }} --output repotoire-results.sarif.json
      shell: bash
    - uses: github/codeql-action/upload-sarif@v3
      with:
        sarif_file: repotoire-results.sarif.json
```

**Step 3: Test locally**

```bash
repotoire analyze . --format sarif --output test.sarif.json
python3 -c "import json; d=json.load(open('test.sarif.json')); print(f'SARIF valid: {len(d[\"runs\"][0][\"results\"])} results')"
```

**Step 4: Commit**

```bash
git add .github/workflows/repotoire-analysis.yml action.yml
git commit -m "feat: add GitHub Actions workflow and reusable action for SARIF upload"
```

---

### Task 15: Differential Analysis (`repotoire diff`)

Only report findings introduced in a PR diff, not the entire codebase.

**Files:**
- Create: `repotoire-cli/src/cli/diff.rs`
- Modify: `repotoire-cli/src/cli/mod.rs` (add Diff command)

**Step 1: Design the diff command**

```
repotoire diff [--base main] [--head HEAD]
```

Compare findings between base and head. Show only NEW findings (present in head but not in base).

**Step 2: Implement using existing infrastructure**

The `--since` flag already collects changed files. The diff command:
1. Runs analysis on changed files only
2. Loads cached findings for base branch
3. Computes set difference (new findings = head - base)
4. Reports only new findings

**Step 3: Write tests**

**Step 4: Commit**

```bash
git commit -m "feat: add 'repotoire diff' command for PR-scoped analysis"
```

---

### Task 16: CLI Polish

**Files:**
- Modify: `repotoire-cli/src/cli/mod.rs` (improve help text)
- Modify: `repotoire-cli/src/cli/analyze/mod.rs` (better progress UX)

**Step 1: Improve `--help` text**

Add examples and clearer descriptions to each subcommand.

**Step 2: Add colored output defaults**

Ensure terminal output uses colors by default (already partially done via `console` crate).

**Step 3: Improve error messages**

Replace any remaining `anyhow!("something failed")` with contextual error messages.

**Step 4: Commit**

```bash
git commit -m "feat: improve CLI help text and error messages"
```

---

## Execution Dependencies

```
Task 1 (AI churn FPs) ──→ Task 5 (benchmark)
Task 2 (nesting) ────────→ Task 5 (benchmark)
Task 3 (cleanup) ─────────────────────────────→ Task 9 (clippy deny)
Task 4 (architecture) ───→ Task 5 (benchmark)
Tasks 6-9 (unwraps) ─────→ Task 9 (clippy deny)
Tasks 10-11 (tests) ──────────────────────────→ Task 12 (validation)
Task 14 (SARIF) ──────────→ Task 15 (diff)
```

**Parallelizable groups:**
- Tasks 1, 2, 3, 4 (Phase 1 — independent)
- Tasks 6, 7, 8 (Phase 2 — independent by module)
- Tasks 10, 11 (Phase 3 — independent)
- Tasks 14, 16 (Phase 4 — independent)
