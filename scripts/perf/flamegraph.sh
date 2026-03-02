#!/usr/bin/env bash
set -euo pipefail

INPUT="${1:-perf.data}"
OUTPUT="${2:-flamegraph.svg}"

if ! command -v inferno-collapse-perf &>/dev/null; then
    echo "inferno not found. Install with: cargo install inferno"
    exit 1
fi

echo "=== Generating flamegraph from $INPUT ==="
perf script -i "$INPUT" | inferno-collapse-perf | inferno-flamegraph > "$OUTPUT"

echo "=== Flamegraph saved to $OUTPUT ==="
echo "Open: xdg-open $OUTPUT"
