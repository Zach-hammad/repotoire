# RELEASE_GATE.md

Release checklist for Repotoire. Any failed P0 blocks the release.

## How to Use

1. Run every check on the release candidate binary
2. Test both fresh (no cache) and cached paths
3. Record command, exit code, key output
4. Mark PASS/FAIL
5. All P0 PASS → ship. Any P0 FAIL → fix first.

---

## P0: Blockers

### 1. Cache/Fresh Parity
Run these flags on fresh repo, then re-run (cached). Results must match:
- `--fail-on`, `--severity`, `--top`, `--page`/`--per-page`
- `--skip-detector`, `--max-files`

### 2. Exit Codes
- `--fail-on <severity>` → exit 1 when findings meet threshold
- No findings at threshold → exit 0

### 3. Output Files
`--output` produces valid, non-empty files for: JSON, HTML, Markdown, SARIF

### 4. Clean Machine Output
JSON/SARIF stdout has zero log pollution. Parseable by `jq` without errors.

### 5. Detector Filtering
`--skip-detector <name>` removes all findings from that detector.

### 6. Pagination
`--per-page` and `--page` affect finding count consistently.

### 7. Max-Files
`--max-files N` limits analyzed files and filters findings accordingly.

---

## P1: High Priority

### 8. Top Detector Spot Check
Run on a real repo. Eyeball top-volume detectors for obvious FPs (string literals, test fixtures, non-executable contexts).

### 9. Self-Analysis
Run on repotoire-cli itself. Score should be B+ or above. Zero self-flagging issues.

---

## P2: Polish

### 10. No-Emoji
`--no-emoji` produces zero emoji glyphs anywhere in output.

### 11. Version
`--version` matches the crates.io version being published.

---

## Quick Verification Script

```bash
REPO=/path/to/test/repo
BIN=./target/release/repotoire

# P0: Clean JSON
$BIN analyze $REPO --format json 2>/dev/null | jq . > /dev/null && echo "P0.4 PASS"

# P0: Cache parity
$BIN analyze $REPO --format json 2>/dev/null > /tmp/fresh.json
$BIN analyze $REPO --format json 2>/dev/null > /tmp/cached.json
diff <(jq '.grade, (.findings|length)' /tmp/fresh.json) \
     <(jq '.grade, (.findings|length)' /tmp/cached.json) && echo "P0.1 PASS"

# P0: Fail-on exit code
$BIN analyze $REPO --fail-on low --format json 2>/dev/null; echo "Exit: $?"

# P0: Skip detector
$BIN analyze $REPO --skip-detector todo-scanner --format json 2>/dev/null | \
  jq '[.findings[] | select(.detector=="todo-scanner")] | length' | grep -q '^0$' && echo "P0.5 PASS"

# P0: Output file
$BIN analyze $REPO --format json --output /tmp/out.json 2>/dev/null
[ -s /tmp/out.json ] && echo "P0.3 PASS"

# P1: Self-analysis
$BIN analyze . --format json 2>/dev/null | jq '{score: .overall_score, grade: .grade}'
```
