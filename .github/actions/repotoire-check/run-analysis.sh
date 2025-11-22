#!/bin/bash
set -e

# Parse arguments
FAIL_ON="critical"
INCREMENTAL="true"
FILES=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --fail-on)
      FAIL_ON="$2"
      shift 2
      ;;
    --incremental)
      INCREMENTAL="$2"
      shift 2
      ;;
    --files)
      FILES="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

echo "🔍 Running Repotoire analysis..."
echo "  Fail on: $FAIL_ON"
echo "  Incremental: $INCREMENTAL"
echo "  Files: $FILES"

# Create output directory
mkdir -p .repotoire

# Get repository root
REPO_ROOT=$(git rev-parse --show-toplevel)

# Run analysis and capture output
if [ -n "$FILES" ]; then
  # Analyze specific files
  uv run python -m repotoire.github.pr_analyzer \
    --repo-path "$REPO_ROOT" \
    --fail-on "$FAIL_ON" \
    --files $FILES \
    --output .repotoire/analysis.json \
    --pr-comment .repotoire/pr-comment.md
else
  # Full analysis
  uv run python -m repotoire.github.pr_analyzer \
    --repo-path "$REPO_ROOT" \
    --fail-on "$FAIL_ON" \
    --output .repotoire/analysis.json \
    --pr-comment .repotoire/pr-comment.md
fi

EXIT_CODE=$?

# Parse analysis results for outputs
if [ -f .repotoire/analysis.json ]; then
  FINDINGS_COUNT=$(jq '.findings_count' .repotoire/analysis.json)
  CRITICAL_COUNT=$(jq '.critical_count' .repotoire/analysis.json)
  HEALTH_SCORE=$(jq '.health_score' .repotoire/analysis.json)

  echo "findings-count=$FINDINGS_COUNT" >> $GITHUB_OUTPUT
  echo "critical-count=$CRITICAL_COUNT" >> $GITHUB_OUTPUT
  echo "health-score=$HEALTH_SCORE" >> $GITHUB_OUTPUT

  echo "📊 Analysis complete!"
  echo "  Findings: $FINDINGS_COUNT"
  echo "  Critical: $CRITICAL_COUNT"
  echo "  Health Score: $HEALTH_SCORE/100"
fi

exit $EXIT_CODE
