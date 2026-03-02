#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-.}"
BINARY="${BINARY:-./target/profiling/repotoire}"

echo "=== Building with profiling profile ==="
cargo build --profile profiling -p repotoire-cli

echo "=== Recording perf data (dwarf call graph, 997 Hz) ==="
perf record -g --call-graph dwarf -F 997 -o perf.data -- "$BINARY" analyze "$TARGET" --timings

echo "=== Done. perf.data written ==="
echo "Next: ./scripts/perf/flamegraph.sh"
