#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-.}"

echo "=== Building with dhat feature ==="
cargo build --profile profiling --features dhat -p repotoire-cli

echo "=== Running with DHAT heap profiler ==="
./target/profiling/repotoire analyze "$TARGET"

echo "=== DHAT output written to dhat-heap.json ==="
echo "View at: https://nnethercote.github.io/dh_view/dh_view.html"
