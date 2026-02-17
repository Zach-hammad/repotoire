# RELEASE_GATE.md

Hard release gate for Repotoire.
Any failed P0 gate blocks release.

## How to Use
- Run every check on the target commit/tag candidate
- Capture command, exit code, and key output
- Mark each gate PASS/FAIL
- Release only when all P0 are PASS

---

## P0: Contract & Correctness Gates (Blockers)

## 1) Cache/Fresh Parity
For each critical flag, run command on:
- Fresh repo path (no cache)
- Same path rerun (cached)

Expected: identical semantics.

Flags to parity-check:
- `--fail-on`
- `--severity`
- `--top`
- `--page` / `--per-page`
- `--skip-detector`
- `--max-files`

## 2) Exit Code Contract
- `--fail-on <severity>` must return non-zero when threshold is met
- Must return zero when threshold is not met

## 3) Output File Contract
For each format that supports `--output`:
- JSON
- HTML
- Markdown
- SARIF

Expected: output file exists and is non-empty.

## 4) Machine-Readable Cleanliness
JSON/SARIF stdout must be machine-parse safe in intended mode.
No log pollution in parser-facing output channels.

## 5) Pagination Contract
`--per-page` and `--page` must affect returned finding set consistently across modes where expected.

## 6) Detector Scope Contract
`--skip-detector` must remove matching detector findings.

## 7) Max-Files Consistency
When `--max-files N` is used:
- analyzed file count and findings must reflect same truncated set.

---

## P1: Accuracy Gates (High Priority)

## 8) Top Detector Precision Spot Check
Check top-volume detectors on real repo + synthetic fixtures.
Verify no obvious false positives from:
- string literals/doc blocks
- test fixture-only patterns
- non-executable contexts

## 9) Regression Fixtures
Run synthetic fixtures:
- known-bad fixture (must detect expected issues)
- known-clean fixture (must not emit noise)

---

## P2: UX Quality Gates

## 10) `--no-emoji` Contract
No emoji glyphs in output when disabled.

## 11) Help/Docs Consistency
Help text, behavior, and docs must match.

## 12) Release Notes Accuracy
List only validated fixes.
No claims without reproducible proof.

---

## Required Evidence Format
For each gate:
- Gate ID
- Command(s)
- Exit code
- Output snippet
- PASS/FAIL
- Owner (Zero/Sloth)

---

## Go/No-Go Rule
- Any P0 FAIL => **NO-GO**
- All P0 PASS + no unresolved critical accuracy issue => **GO**

---

## Fast Verification Command Checklist (Template)

```bash
# Example placeholders - replace <repo>
repotoire analyze <repo> --lite --no-emoji --fail-on medium --format text
repotoire analyze <repo> --lite --no-emoji --severity high --format json
repotoire analyze <repo> --lite --no-emoji --top 1 --format json
repotoire analyze <repo> --lite --no-emoji --per-page 1 --page 2 --format json
repotoire analyze <repo> --lite --no-emoji --skip-detector TodoScanner --format json
repotoire analyze <repo> --lite --no-emoji --max-files 1 --format json
repotoire analyze <repo> --lite --no-emoji --format json --output /tmp/out.json
```

Add cache reruns for parity.
