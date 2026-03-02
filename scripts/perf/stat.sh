#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-.}"
RUNS="${RUNS:-5}"
BINARY="${BINARY:-./target/profiling/repotoire}"

echo "=== Building with profiling profile ==="
cargo build --profile profiling -p repotoire-cli

echo "=== perf stat ($RUNS runs) ==="
perf stat -d -r "$RUNS" -- "$BINARY" analyze "$TARGET" --timings
