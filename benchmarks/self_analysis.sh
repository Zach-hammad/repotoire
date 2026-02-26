#!/bin/bash
set -euo pipefail

echo "=== Repotoire Self-Analysis Benchmark ==="
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Build release binary
echo "Building release binary..."
cd "$REPO_ROOT/repotoire-cli"
cargo build --release 2>/dev/null

BINARY="$REPO_ROOT/repotoire-cli/target/release/repotoire"

if [ ! -x "$BINARY" ]; then
    echo "ERROR: Binary not found at $BINARY"
    exit 1
fi

# Clean stale cache to avoid redb lock conflicts
echo "Cleaning stale cache..."
"$BINARY" clean "$REPO_ROOT" 2>/dev/null || true

# Run analysis
echo "Running self-analysis..."
RESULT=$("$BINARY" analyze "$REPO_ROOT" --format json 2>/dev/null)

if [ -z "$RESULT" ]; then
    echo "ERROR: Analysis produced no output"
    exit 1
fi

# Extract metrics using python3
TOTAL=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['findings_summary']['total'])")
CRITICAL=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['findings_summary']['critical'])")
HIGH=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['findings_summary']['high'])")
MEDIUM=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['findings_summary']['medium'])")
LOW=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['findings_summary']['low'])")
SCORE=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(round(d['overall_score'], 1))")
GRADE=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['grade'])")

echo ""
echo "--- Results ---"
echo "Score:    $SCORE ($GRADE)"
echo "Findings: $TOTAL total"
echo "  Critical: $CRITICAL"
echo "  High:     $HIGH"
echo "  Medium:   $MEDIUM"
echo "  Low:      $LOW"
echo ""

# ── Threshold assertions ──
# Baseline (2026-02-26): Score 97.4, C:1 H:24 M:138 L:130 Total:293
# Thresholds set with headroom to catch real regressions without flaking.

PASS=true

# Score must stay above 95.0 (baseline: 97.4)
if (( $(echo "$SCORE < 95.0" | bc -l) )); then
    echo "FAIL: Score $SCORE < 95.0"
    PASS=false
fi

# Critical findings capped at 5 (baseline: 1)
if [ "$CRITICAL" -gt 5 ]; then
    echo "FAIL: $CRITICAL critical findings (max 5)"
    PASS=false
fi

# High findings capped at 40 (baseline: 24)
if [ "$HIGH" -gt 40 ]; then
    echo "FAIL: $HIGH high findings (max 40)"
    PASS=false
fi

# Total findings capped at 400 (baseline: 293)
if [ "$TOTAL" -gt 400 ]; then
    echo "FAIL: $TOTAL total findings (max 400)"
    PASS=false
fi

echo ""
if $PASS; then
    echo "=== BENCHMARK PASSED ==="
    exit 0
else
    echo "=== BENCHMARK FAILED ==="
    exit 1
fi
