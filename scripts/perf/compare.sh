#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-.}"
RUNS="${RUNS:-5}"
BINARY="${BINARY:-./target/profiling/repotoire}"

echo "=== Baseline measurement ==="
perf stat -d -r "$RUNS" -o /tmp/perf-before.txt -- "$BINARY" analyze "$TARGET" 2>&1
BEFORE=$(/usr/bin/time -v "$BINARY" analyze "$TARGET" 2>&1 | grep "Maximum resident" | awk '{print $NF}')
echo "Baseline RSS: ${BEFORE}KB"

echo ""
echo "=== Make your changes, rebuild with: cargo build --profile profiling -p repotoire-cli ==="
echo "=== Press Enter when ready ==="
read -r

echo "=== After measurement ==="
perf stat -d -r "$RUNS" -o /tmp/perf-after.txt -- "$BINARY" analyze "$TARGET" 2>&1
AFTER=$(/usr/bin/time -v "$BINARY" analyze "$TARGET" 2>&1 | grep "Maximum resident" | awk '{print $NF}')

echo ""
echo "=== Results ==="
echo "RSS: ${BEFORE}KB -> ${AFTER}KB"
echo "Full stats: diff /tmp/perf-before.txt /tmp/perf-after.txt"
