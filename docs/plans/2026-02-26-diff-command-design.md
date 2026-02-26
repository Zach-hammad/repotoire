# `repotoire diff` Command Design

**Date:** 2026-02-26
**Approach:** Thin wrapper on existing analyze pipeline + MCP tool

## Problem

Repotoire analyzes entire codebases, but developers and CI systems need to know: "what new issues did this PR introduce?" Currently there's no way to see only the delta between two git refs.

## CLI Interface

```
repotoire diff [BASE_REF] [--head HEAD] [--format text|json|sarif] [--fail-on SEVERITY]
```

**Examples:**
```bash
repotoire diff main                    # Diff HEAD vs main
repotoire diff v1.0.0                  # Diff HEAD vs tag
repotoire diff                         # Diff HEAD vs last cached analysis
repotoire diff main --format json      # JSON output for CI
repotoire diff main --fail-on high     # Exit 1 if new high+ findings
repotoire diff main --format sarif     # SARIF with only new findings
```

**Arguments:**
- `BASE_REF` (optional, positional) — git ref for baseline. If omitted, uses last cached analysis (`last_findings.json`).
- `--head` (optional, default: working tree) — git ref for "after" state.
- `--format` — output format: text (default), json, sarif.
- `--fail-on` — exit non-zero if new findings at this severity or above.

## MCP Tool

Add `repotoire_diff` to the MCP server (FREE tier):

```json
{
  "name": "repotoire_diff",
  "description": "Compare findings between two git refs. Shows new, fixed findings and score delta.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "base_ref": { "type": "string", "description": "Git ref for baseline (branch, tag, commit). Omit to use last cached analysis." },
      "head_ref": { "type": "string", "description": "Git ref for current state. Default: HEAD." },
      "severity": { "type": "string", "enum": ["critical", "high", "medium", "low"] }
    }
  }
}
```

## Data Flow

```
1. Load baseline findings
   ├── BASE_REF given → run `analyze --since BASE_REF` to get baseline context,
   │                     then load last_findings.json as the baseline
   └── No BASE_REF → load last_findings.json directly

2. Collect changed files
   └── git diff --name-only BASE_REF..HEAD (reuse get_changed_files_since)

3. Run analysis on HEAD (changed files only)
   └── Reuse analyze pipeline with since=BASE_REF

4. Diff findings
   ├── new_findings    = head_findings - baseline_findings (by match key)
   ├── fixed_findings  = baseline_findings - head_findings (by match key)
   └── score_delta     = head_score - baseline_score

5. Output (text / json / sarif)
```

### Two-Pass Strategy for Git Ref Mode

When `BASE_REF` is provided:

1. **Baseline pass:** Check out base ref content via `git show BASE_REF:path` for each changed file, analyze those versions, collect baseline findings for changed files. For unchanged files, use current analysis results (they're the same in both refs).

2. **Head pass:** Analyze the current working tree (changed files only).

3. **Diff:** Compare baseline findings (changed files only) against head findings (changed files only).

This avoids needing to run full analysis twice. Only changed files are analyzed in both versions.

**Simplification:** For v1, we can skip the baseline pass entirely. Instead:
- Run full `analyze` on HEAD
- Load `last_findings.json` from most recent prior analysis as baseline
- Diff the two sets

This works because the common workflow is: `repotoire analyze` on main → make changes → `repotoire diff` to see what's new. The cached findings from the last `analyze` serve as the baseline.

## Finding Match Strategy

Findings are matched using a fuzzy key: `(detector, file_path, line_number ± 3)`.

```rust
fn findings_match(a: &Finding, b: &Finding) -> bool {
    a.detector == b.detector
        && a.affected_files.first() == b.affected_files.first()
        && match (a.line_start, b.line_start) {
            (Some(la), Some(lb)) => la.abs_diff(lb) <= 3,
            (None, None) => true,   // file-level findings
            _ => false,
        }
}
```

The ±3 line tolerance handles findings that shift slightly due to nearby code changes but are logically the same issue.

## Output Formats

### Text (terminal, default)

```
Repotoire Diff: main..HEAD (12 files changed)

 NEW FINDINGS (3)
  1. [C] SQL injection via f-string        src/db.py:42
  2. [H] Missing input validation           src/api.py:87
  3. [M] Magic number                        src/calc.py:15

 FIXED FINDINGS (2)
  ✓ [H] Hardcoded credentials              src/config.py:12
  ✓ [M] Dead store variable                 src/utils.py:55

 SCORE: 96.1 → 95.8 (-0.3)
```

### JSON

```json
{
  "base_ref": "main",
  "head_ref": "HEAD",
  "files_changed": 12,
  "new_findings": [...],
  "fixed_findings": [...],
  "score_before": 96.1,
  "score_after": 95.8,
  "score_delta": -0.3,
  "summary": {
    "new": { "critical": 1, "high": 1, "medium": 1, "low": 0 },
    "fixed": { "critical": 0, "high": 1, "medium": 1, "low": 0 }
  }
}
```

### SARIF

Standard SARIF 2.1.0 output containing only NEW findings. Compatible with `github/codeql-action/upload-sarif`. Fixed findings are not included in SARIF (GitHub tracks dismissals separately).

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `src/cli/diff.rs` | Create | Main diff command logic (~200 lines) |
| `src/cli/mod.rs` | Modify | Add `Diff` variant to `Commands` enum |
| `src/cli/analyze/output.rs` | Modify | Extract `load_cached_findings()` as pub fn |
| `src/mcp/tools/analysis.rs` | Modify | Add `handle_diff()` MCP handler |
| `src/mcp/rmcp_server.rs` | Modify | Register `repotoire_diff` tool |

## CI Integration

Update `.github/workflows/repotoire-analysis.yml` to use diff mode on PRs:

```yaml
- name: Run Diff Analysis
  if: github.event_name == 'pull_request'
  run: |
    repotoire diff ${{ github.event.pull_request.base.sha }} \
      --format sarif --output diff-results.sarif.json \
      --fail-on high
```

## Edge Cases

1. **No cached baseline:** If no `last_findings.json` exists and no BASE_REF given → run full analyze first, then explain that diff requires a baseline.
2. **Binary version mismatch:** If cached findings are from a different binary version → warn and suggest re-running analyze on the base ref.
3. **Non-git repo:** Error with clear message: "diff requires a git repository."
4. **Same findings, different line:** ±3 tolerance handles this. Beyond ±3, treated as new finding + fixed finding.
5. **File deleted:** Findings in deleted files are all "fixed."
6. **File added:** Findings in new files are all "new."

## Success Criteria

1. `repotoire diff main` shows new/fixed findings and score delta in <10s (changed files only)
2. `repotoire diff main --fail-on high` exits non-zero when new high+ findings exist
3. `repotoire diff main --format sarif` produces valid SARIF with only new findings
4. MCP `repotoire_diff` tool returns structured JSON response
5. All existing tests pass
6. New tests cover: matching logic, edge cases, output formats
