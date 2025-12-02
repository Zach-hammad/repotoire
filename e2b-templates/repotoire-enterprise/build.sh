#!/bin/bash
# Build script for repotoire-enterprise E2B template
#
# This script:
# 1. Copies Rust source code into the build context
# 2. Builds the E2B template
# 3. Cleans up copied source
#
# Usage: ./build.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$SCRIPT_DIR"

echo "=== Building repotoire-enterprise E2B template ==="
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

echo "1. Copying Rust source code..."
cp -r "$REPO_ROOT/repotoire-fast" .
cp -r "$REPO_ROOT/repotoire_fast" .

echo "2. Building E2B template (this may take ~10 minutes)..."
e2b template build

echo "3. Cleaning up..."
rm -rf repotoire-fast repotoire_fast

echo ""
echo "=== Build complete! ==="
echo ""
echo "Template 'repotoire-enterprise' is now available."
echo ""
echo "Test with: e2b sandbox spawn --template repotoire-enterprise"
echo "Verify:    python -c 'import repotoire_fast; print(dir(repotoire_fast))'"
