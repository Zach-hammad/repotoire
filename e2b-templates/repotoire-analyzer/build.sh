#!/bin/bash
# Build script for repotoire-analyzer E2B template
#
# Usage: ./build.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$SCRIPT_DIR"

echo "=== Building repotoire-analyzer E2B template ==="
echo ""

# Check E2B CLI is available
if ! command -v e2b &> /dev/null; then
    echo "Error: e2b CLI not found. Install with: npm install -g @e2b/cli"
    exit 1
fi

# Check authenticated
if ! e2b auth whoami &> /dev/null; then
    echo "Error: Not authenticated with E2B. Run: e2b auth login"
    exit 1
fi

echo "Building E2B template (this may take ~2 minutes)..."
e2b template build

echo ""
echo "=== Build complete! ==="
echo ""
echo "Template 'repotoire-analyzer' is now available."
echo ""
echo "Test with: e2b sandbox spawn --template repotoire-analyzer"
echo "Verify:    ruff --version && semgrep --version"
