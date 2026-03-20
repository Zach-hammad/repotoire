#!/usr/bin/env bash
# Seed PostHog with benchmark data from popular open-source repos
# and capture profiling data for improving repotoire.
#
# Outputs:
#   seed-results/
#     summary.csv              — one row per repo (score, grade, findings, duration, memory)
#     json/{repo}.json         — full analysis JSON per repo
#     timings/{repo}.txt       — phase timing breakdown per repo
#     errors/{repo}.txt        — stderr for failed repos
#
# Prerequisites:
#   - repotoire built and in PATH
#   - Telemetry enabled: repotoire config telemetry on
#
# Usage:
#   ./scripts/seed-benchmarks.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKDIR=$(mktemp -d)
RESULTS_DIR="$REPO_ROOT/seed-results"

mkdir -p "$RESULTS_DIR"/{json,timings,errors}

trap "rm -rf $WORKDIR" EXIT

# CSV header
echo "repo,language,score,grade,findings,critical,high,medium,low,files,loc,duration_ms,memory_kb,exit_code" > "$RESULTS_DIR/summary.csv"

REPOS=(
  # Rust (15)
  "https://github.com/BurntSushi/ripgrep"
  "https://github.com/sharkdp/bat"
  "https://github.com/sharkdp/fd"
  "https://github.com/alacritty/alacritty"
  "https://github.com/starship/starship"
  "https://github.com/astral-sh/ruff"
  "https://github.com/tokio-rs/axum"
  "https://github.com/serde-rs/serde"
  "https://github.com/clap-rs/clap"
  "https://github.com/diesel-rs/diesel"
  "https://github.com/hyperium/hyper"
  "https://github.com/rust-lang/cargo"
  "https://github.com/nushell/nushell"
  "https://github.com/zellij-org/zellij"
  "https://github.com/helix-editor/helix"

  # Python (10)
  "https://github.com/psf/requests"
  "https://github.com/pallets/flask"
  "https://github.com/django/django"
  "https://github.com/fastapi/fastapi"
  "https://github.com/pydantic/pydantic"
  "https://github.com/sqlalchemy/sqlalchemy"
  "https://github.com/psf/black"
  "https://github.com/python-poetry/poetry"
  "https://github.com/httpie/cli"
  "https://github.com/celery/celery"

  # TypeScript/JavaScript (10)
  "https://github.com/microsoft/TypeScript"
  "https://github.com/vercel/next.js"
  "https://github.com/facebook/react"
  "https://github.com/expressjs/express"
  "https://github.com/prisma/prisma"
  "https://github.com/trpc/trpc"
  "https://github.com/t3-oss/create-t3-app"
  "https://github.com/shadcn-ui/ui"
  "https://github.com/tailwindlabs/tailwindcss"
  "https://github.com/vitejs/vite"

  # Go (10)
  "https://github.com/junegunn/fzf"
  "https://github.com/jesseduffield/lazygit"
  "https://github.com/charmbracelet/bubbletea"
  "https://github.com/go-chi/chi"
  "https://github.com/gofiber/fiber"
  "https://github.com/gin-gonic/gin"
  "https://github.com/spf13/cobra"
  "https://github.com/gorilla/mux"
  "https://github.com/containerd/containerd"
  "https://github.com/prometheus/prometheus"

  # Java (5)
  "https://github.com/spring-projects/spring-boot"
  "https://github.com/google/guava"
  "https://github.com/square/okhttp"
  "https://github.com/square/retrofit"
  "https://github.com/apache/kafka"

  # C/C++ (5)
  "https://github.com/redis/redis"
  "https://github.com/jqlang/jq"
  "https://github.com/curl/curl"
  "https://github.com/tmux/tmux"
  "https://github.com/git/git"

  # C# (2)
  "https://github.com/dotnet/aspnetcore"
  "https://github.com/jellyfin/jellyfin"
)

TOTAL=${#REPOS[@]}
COUNT=0
FAILED=0
TOTAL_DURATION=0

echo "Seeding benchmarks from $TOTAL open-source repos"
echo "Results: $RESULTS_DIR"
echo "Temp clones: $WORKDIR"
echo ""

for REPO_URL in "${REPOS[@]}"; do
  COUNT=$((COUNT + 1))
  REPO_NAME=$(basename "$REPO_URL")
  CLONE_DIR="$WORKDIR/$REPO_NAME"

  printf "[%2d/%d] %-30s " "$COUNT" "$TOTAL" "$REPO_NAME"

  # Shallow clone
  if ! git clone --depth 1 --quiet "$REPO_URL" "$CLONE_DIR" 2>/dev/null; then
    echo "SKIP (clone failed)"
    FAILED=$((FAILED + 1))
    echo "$REPO_NAME,,,,,,,,,,,clone_failed,1" >> "$RESULTS_DIR/summary.csv"
    continue
  fi

  # Run repotoire with JSON output + timings, capture memory via time
  START_MS=$(($(date +%s%N) / 1000000))

  EXIT_CODE=0
  /usr/bin/env time -v repotoire analyze "$CLONE_DIR" \
    --format json \
    --timings \
    > "$RESULTS_DIR/json/$REPO_NAME.json" \
    2> "$RESULTS_DIR/timings/$REPO_NAME.txt" \
    || EXIT_CODE=$?

  END_MS=$(($(date +%s%N) / 1000000))
  DURATION_MS=$((END_MS - START_MS))
  TOTAL_DURATION=$((TOTAL_DURATION + DURATION_MS))

  # Extract memory from time output (Maximum resident set size in KB)
  MEMORY_KB=$(grep "Maximum resident" "$RESULTS_DIR/timings/$REPO_NAME.txt" 2>/dev/null | awk '{print $NF}' || echo "0")

  if [ "$EXIT_CODE" -ne 0 ]; then
    echo "FAIL (exit $EXIT_CODE, ${DURATION_MS}ms)"
    FAILED=$((FAILED + 1))
    mv "$RESULTS_DIR/timings/$REPO_NAME.txt" "$RESULTS_DIR/errors/$REPO_NAME.txt" 2>/dev/null || true
    echo "$REPO_NAME,,,,,,,,,,${DURATION_MS},${MEMORY_KB},${EXIT_CODE}" >> "$RESULTS_DIR/summary.csv"
    rm -rf "$CLONE_DIR"
    continue
  fi

  # Extract stats from JSON
  SCORE=$(jq -r '.overall_score // 0' "$RESULTS_DIR/json/$REPO_NAME.json" 2>/dev/null || echo "0")
  GRADE=$(jq -r '.grade // "?"' "$RESULTS_DIR/json/$REPO_NAME.json" 2>/dev/null || echo "?")
  FINDINGS=$(jq -r '.findings | length' "$RESULTS_DIR/json/$REPO_NAME.json" 2>/dev/null || echo "0")
  CRITICAL=$(jq -r '[.findings[] | select(.severity == "Critical")] | length' "$RESULTS_DIR/json/$REPO_NAME.json" 2>/dev/null || echo "0")
  HIGH=$(jq -r '[.findings[] | select(.severity == "High")] | length' "$RESULTS_DIR/json/$REPO_NAME.json" 2>/dev/null || echo "0")
  MEDIUM=$(jq -r '[.findings[] | select(.severity == "Medium")] | length' "$RESULTS_DIR/json/$REPO_NAME.json" 2>/dev/null || echo "0")
  LOW=$(jq -r '[.findings[] | select(.severity == "Low")] | length' "$RESULTS_DIR/json/$REPO_NAME.json" 2>/dev/null || echo "0")
  FILES=$(jq -r '.total_files // 0' "$RESULTS_DIR/json/$REPO_NAME.json" 2>/dev/null || echo "0")
  LOC=$(jq -r '.total_loc // 0' "$RESULTS_DIR/json/$REPO_NAME.json" 2>/dev/null || echo "0")

  # Detect primary language from file extensions in findings
  LANGUAGE=$(jq -r '
    [.findings[].affected_files[]?] |
    map(split(".")[-1]) |
    group_by(.) |
    sort_by(-length) |
    first |
    first // "unknown"
  ' "$RESULTS_DIR/json/$REPO_NAME.json" 2>/dev/null || echo "unknown")

  # Map extension to language name
  case "$LANGUAGE" in
    rs) LANGUAGE="rust" ;;
    py|pyi) LANGUAGE="python" ;;
    ts|tsx) LANGUAGE="typescript" ;;
    js|jsx|mjs) LANGUAGE="javascript" ;;
    go) LANGUAGE="go" ;;
    java) LANGUAGE="java" ;;
    cs) LANGUAGE="csharp" ;;
    c|h) LANGUAGE="c" ;;
    cpp|cc|cxx|hpp) LANGUAGE="cpp" ;;
  esac

  printf "%-4s %5.1f (%s) %4d findings  %6dms  %6dMB\n" \
    "$GRADE" "$SCORE" "$LANGUAGE" "$FINDINGS" "$DURATION_MS" "$((MEMORY_KB / 1024))"

  echo "$REPO_NAME,$LANGUAGE,$SCORE,$GRADE,$FINDINGS,$CRITICAL,$HIGH,$MEDIUM,$LOW,$FILES,$LOC,$DURATION_MS,$MEMORY_KB,$EXIT_CODE" >> "$RESULTS_DIR/summary.csv"

  # Clean up clone
  rm -rf "$CLONE_DIR"
done

echo ""
echo "════════════════════════════════════════════════════"
echo "  Completed: $((COUNT - FAILED))/$TOTAL repos"
echo "  Failed:    $FAILED"
echo "  Total time: $((TOTAL_DURATION / 1000))s"
echo ""
echo "  Results:   $RESULTS_DIR/summary.csv"
echo "  JSON:      $RESULTS_DIR/json/"
echo "  Timings:   $RESULTS_DIR/timings/"
echo "════════════════════════════════════════════════════"
echo ""
echo "Next steps:"
echo "  1. Trigger benchmark generator: GitHub Actions → Run workflow"
echo "  2. Analyze profiling data: cat $RESULTS_DIR/summary.csv | column -t -s,"
