#!/usr/bin/env bash
# =============================================================================
# validate.sh — End-to-end validation of repotoire against real-world projects
#
# Runs repotoire against its own source code, Flask, FastAPI, and Django to
# verify all output formats, scoring, and the findings subcommand.
#
# Usage:
#   ./scripts/validate.sh            # full run (~5-10 min)
#   ./scripts/validate.sh --self     # self-analysis only (~1 min)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLI_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$CLI_DIR/.." && pwd)"
BINARY="$REPO_ROOT/target/release/repotoire"
TMPDIR=$(mktemp -d /tmp/repotoire-validate-XXXXXX)

PASSED=0
FAILED=0
TOTAL=0
SELF_ONLY=false

# Parse arguments
for arg in "$@"; do
    case "$arg" in
        --self) SELF_ONLY=true ;;
        *) echo "Unknown argument: $arg"; exit 1 ;;
    esac
done

# Cleanup on exit (success or failure)
trap 'rm -rf "$TMPDIR"' EXIT

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

check() {
    local desc="$1"
    shift
    TOTAL=$((TOTAL + 1))
    if "$@" > /dev/null 2>&1; then
        PASSED=$((PASSED + 1))
        echo "  PASS: $desc"
    else
        FAILED=$((FAILED + 1))
        echo "  FAIL: $desc"
    fi
}

check_json_field() {
    local desc="$1"
    local file="$2"
    local query="$3"
    TOTAL=$((TOTAL + 1))
    if jq -e "$query" "$file" > /dev/null 2>&1; then
        PASSED=$((PASSED + 1))
        echo "  PASS: $desc"
    else
        FAILED=$((FAILED + 1))
        echo "  FAIL: $desc"
    fi
}

check_file_size_gt() {
    local desc="$1"
    local file="$2"
    local min_bytes="$3"
    TOTAL=$((TOTAL + 1))
    if [ -f "$file" ] && [ "$(wc -c < "$file")" -gt "$min_bytes" ]; then
        PASSED=$((PASSED + 1))
        echo "  PASS: $desc"
    else
        FAILED=$((FAILED + 1))
        echo "  FAIL: $desc"
    fi
}

# Run a full suite of format/findings checks against a project.
#   validate_project <label> <path> [extra_flags...]
validate_project() {
    local label="$1"
    local target_path="$2"
    shift 2
    local extra_flags=("$@")

    echo ""
    echo "=== $label ==="
    echo "    path: $target_path"
    echo ""

    local out_dir="$TMPDIR/$label"
    mkdir -p "$out_dir"

    # --- 1. JSON format ---
    set +e
    "$BINARY" "$target_path" analyze --format json --no-git --no-emoji \
        "${extra_flags[@]}" -o "$out_dir/report.json" 2>/dev/null
    local json_exit=$?
    set -e

    TOTAL=$((TOTAL + 1))
    if [ $json_exit -eq 0 ]; then
        PASSED=$((PASSED + 1))
        echo "  PASS: analyze --format json exits 0"
    else
        FAILED=$((FAILED + 1))
        echo "  FAIL: analyze --format json exits 0 (got $json_exit)"
    fi

    # Validate JSON
    check "JSON output is valid" jq '.' "$out_dir/report.json"
    check_json_field "JSON has numeric overall_score" "$out_dir/report.json" '.overall_score | type == "number"'
    check_json_field "JSON has findings array with length > 0" "$out_dir/report.json" '.findings | length > 0'

    # --- 2. Text format ---
    set +e
    "$BINARY" "$target_path" analyze --format text --no-git --no-emoji \
        "${extra_flags[@]}" > "$out_dir/report.txt" 2>/dev/null
    local text_exit=$?
    set -e

    TOTAL=$((TOTAL + 1))
    if [ $text_exit -eq 0 ]; then
        PASSED=$((PASSED + 1))
        echo "  PASS: analyze --format text exits 0"
    else
        FAILED=$((FAILED + 1))
        echo "  FAIL: analyze --format text exits 0 (got $text_exit)"
    fi

    # --- 3. SARIF format ---
    set +e
    "$BINARY" "$target_path" analyze --format sarif --no-git --no-emoji \
        "${extra_flags[@]}" -o "$out_dir/report.sarif" 2>/dev/null
    local sarif_exit=$?
    set -e

    TOTAL=$((TOTAL + 1))
    if [ $sarif_exit -eq 0 ]; then
        PASSED=$((PASSED + 1))
        echo "  PASS: analyze --format sarif exits 0"
    else
        FAILED=$((FAILED + 1))
        echo "  FAIL: analyze --format sarif exits 0 (got $sarif_exit)"
    fi

    check "SARIF is valid JSON" jq '.' "$out_dir/report.sarif"
    check_json_field "SARIF version is 2.1.0" "$out_dir/report.sarif" '.version == "2.1.0"'

    # --- 4. HTML format ---
    set +e
    "$BINARY" "$target_path" analyze --format html --no-git --no-emoji \
        "${extra_flags[@]}" -o "$out_dir/report.html" 2>/dev/null
    local html_exit=$?
    set -e

    TOTAL=$((TOTAL + 1))
    if [ $html_exit -eq 0 ]; then
        PASSED=$((PASSED + 1))
        echo "  PASS: analyze --format html exits 0"
    else
        FAILED=$((FAILED + 1))
        echo "  FAIL: analyze --format html exits 0 (got $html_exit)"
    fi

    check_file_size_gt "HTML output > 1KB" "$out_dir/report.html" 1024

    # --- 5. Markdown format ---
    set +e
    "$BINARY" "$target_path" analyze --format markdown --no-git --no-emoji \
        "${extra_flags[@]}" -o "$out_dir/report.md" 2>/dev/null
    local md_exit=$?
    set -e

    TOTAL=$((TOTAL + 1))
    if [ $md_exit -eq 0 ]; then
        PASSED=$((PASSED + 1))
        echo "  PASS: analyze --format markdown exits 0"
    else
        FAILED=$((FAILED + 1))
        echo "  FAIL: analyze --format markdown exits 0 (got $md_exit)"
    fi

    # --- 6. findings --top 5 ---
    set +e
    "$BINARY" "$target_path" findings --top 5 > /dev/null 2>&1
    local findings_exit=$?
    set -e

    TOTAL=$((TOTAL + 1))
    if [ $findings_exit -eq 0 ]; then
        PASSED=$((PASSED + 1))
        echo "  PASS: findings --top 5 exits 0"
    else
        FAILED=$((FAILED + 1))
        echo "  FAIL: findings --top 5 exits 0 (got $findings_exit)"
    fi

    # --- 7. findings --json --top 10 ---
    set +e
    "$BINARY" "$target_path" findings --json --top 10 > "$out_dir/findings.json" 2>/dev/null
    local findings_json_exit=$?
    set -e

    TOTAL=$((TOTAL + 1))
    if [ $findings_json_exit -eq 0 ]; then
        PASSED=$((PASSED + 1))
        echo "  PASS: findings --json --top 10 exits 0"
    else
        FAILED=$((FAILED + 1))
        echo "  FAIL: findings --json --top 10 exits 0 (got $findings_json_exit)"
    fi

    check "findings JSON output is valid" jq '.' "$out_dir/findings.json"
}

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

echo "==========================================="
echo " Repotoire E2E Validation"
echo "==========================================="
echo ""

echo "--- Checking prerequisites ---"

missing=0
for cmd in jq git; do
    if ! command -v "$cmd" > /dev/null 2>&1; then
        echo "  MISSING: $cmd"
        missing=1
    else
        echo "  OK: $cmd"
    fi
done
if [ $missing -ne 0 ]; then
    echo ""
    echo "ERROR: Missing prerequisites. Install them and re-run."
    exit 1
fi

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

echo ""
echo "--- Building release binary ---"

(cd "$CLI_DIR" && cargo build --release 2>&1 | tail -5)

if [ ! -x "$BINARY" ]; then
    echo "ERROR: Binary not found at $BINARY"
    exit 1
fi
echo "  Binary: $BINARY"
echo "  Version: $("$BINARY" version 2>/dev/null || echo 'unknown')"

# ---------------------------------------------------------------------------
# Phase 1: Self-analysis
# ---------------------------------------------------------------------------

validate_project "self" "$CLI_DIR/src"

# Self-analysis bonus check: score between 50 and 100
SELF_JSON="$TMPDIR/self/report.json"
if [ -f "$SELF_JSON" ]; then
    TOTAL=$((TOTAL + 1))
    score=$(jq -r '.overall_score // 0' "$SELF_JSON" 2>/dev/null)
    # Use awk for float comparison
    in_range=$(awk "BEGIN { print ($score >= 50 && $score <= 100) ? 1 : 0 }")
    if [ "$in_range" -eq 1 ]; then
        PASSED=$((PASSED + 1))
        echo "  PASS: self-analysis score in [50, 100] (got $score)"
    else
        FAILED=$((FAILED + 1))
        echo "  FAIL: self-analysis score in [50, 100] (got $score)"
    fi
fi

# ---------------------------------------------------------------------------
# Phase 2: Real-world projects (skip if --self)
# ---------------------------------------------------------------------------

if [ "$SELF_ONLY" = false ]; then

    echo ""
    echo "--- Cloning real-world projects ---"

    echo "  Cloning Flask..."
    git clone --depth 1 --quiet https://github.com/pallets/flask.git "$TMPDIR/flask"

    echo "  Cloning FastAPI..."
    git clone --depth 1 --quiet https://github.com/fastapi/fastapi.git "$TMPDIR/fastapi"

    echo "  Cloning Django..."
    git clone --depth 1 --quiet https://github.com/django/django.git "$TMPDIR/django"

    echo "  Done."

    validate_project "flask"   "$TMPDIR/flask/src/flask"
    validate_project "fastapi" "$TMPDIR/fastapi/fastapi"
    validate_project "django"  "$TMPDIR/django/django" --max-files 500 --skip-graph

fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "==========================================="
echo " Summary"
echo "==========================================="
echo ""

printf "  %-12s %s\n" "Total:"  "$TOTAL"
printf "  %-12s %s\n" "Passed:" "$PASSED"
printf "  %-12s %s\n" "Failed:" "$FAILED"
echo ""

if [ "$FAILED" -eq 0 ]; then
    echo "  ALL TESTS PASSED"
    exit 0
else
    echo "  $FAILED TEST(S) FAILED"
    exit 1
fi
