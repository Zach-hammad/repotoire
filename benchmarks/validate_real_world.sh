#!/usr/bin/env bash
# Real-world validation script for Repotoire
# Clones well-known open-source projects and runs analysis to validate
# finding quality, false positive rates, and score reasonableness.
#
# Usage: ./benchmarks/validate_real_world.sh [--skip-clone]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="$SCRIPT_DIR/results"
REPOTOIRE="${REPOTOIRE_BIN:-repotoire}"

# Projects to validate against
declare -A PROJECTS=(
    ["flask"]="https://github.com/pallets/flask.git"
    ["fastapi"]="https://github.com/fastapi/fastapi.git"
    ["express"]="https://github.com/expressjs/express.git"
)

mkdir -p "$RESULTS_DIR"

# Clone projects (shallow) unless --skip-clone
if [[ "${1:-}" != "--skip-clone" ]]; then
    echo "Cloning projects..."
    for name in "${!PROJECTS[@]}"; do
        if [[ -d "/tmp/$name" ]]; then
            echo "  $name: already exists, skipping"
        else
            echo "  $name: cloning..."
            git clone --depth 1 "${PROJECTS[$name]}" "/tmp/$name" 2>/dev/null
        fi
    done
fi

# Run analysis on each project
echo ""
echo "Running analysis..."
for name in "${!PROJECTS[@]}"; do
    echo "  Analyzing $name..."
    $REPOTOIRE analyze "/tmp/$name" \
        --format json \
        --no-emoji \
        --output "$RESULTS_DIR/$name.json" \
        2>/dev/null
    echo "  $name: done"
done

# Extract summary from each result
echo ""
echo "=== Validation Results ==="
echo ""
printf "%-12s %-8s %-6s %-10s %-6s %-6s %-6s %-6s\n" \
    "Project" "Score" "Grade" "Findings" "Crit" "High" "Med" "Low"
echo "---------------------------------------------------------------"

for name in "${!PROJECTS[@]}"; do
    json="$RESULTS_DIR/$name.json"
    if [[ -f "$json" ]]; then
        score=$(jq -r '.health_score // .overall_score // "N/A"' "$json")
        grade=$(jq -r '.grade // "N/A"' "$json")
        total=$(jq '.findings | length' "$json")
        crit=$(jq '[.findings[] | select(.severity == "critical")] | length' "$json")
        high=$(jq '[.findings[] | select(.severity == "high")] | length' "$json")
        med=$(jq '[.findings[] | select(.severity == "medium")] | length' "$json")
        low=$(jq '[.findings[] | select(.severity == "low")] | length' "$json")
        printf "%-12s %-8s %-6s %-10s %-6s %-6s %-6s %-6s\n" \
            "$name" "$score" "$grade" "$total" "$crit" "$high" "$med" "$low"
    fi
done

echo ""
echo "Results saved to $RESULTS_DIR/"
