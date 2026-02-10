#!/bin/bash
#
# Generate TypeScript types from FastAPI OpenAPI spec.
#
# Usage:
#   ./scripts/generate-api-types.sh              # Try server, fallback to Python export
#   ./scripts/generate-api-types.sh --server     # Fetch from running server only
#   ./scripts/generate-api-types.sh --python     # Generate via Python only
#
# This script generates TypeScript types from the FastAPI OpenAPI spec.
# Run this whenever backend API models change to keep frontend types in sync.
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$(dirname "$SCRIPT_DIR")"
ROOT_DIR="$(dirname "$(dirname "$WEB_DIR")")"
SPEC_FILE="$WEB_DIR/.openapi-spec.json"
OUTPUT_FILE="$WEB_DIR/src/types/api.generated.ts"
API_URL="${API_URL:-http://localhost:8000}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}🔄 Generating API types...${NC}"
echo ""

fetch_from_server() {
    echo -e "🌐 Fetching OpenAPI spec from: ${API_URL}/openapi.json"
    if curl -sf "${API_URL}/openapi.json" -o "$SPEC_FILE" 2>/dev/null; then
        echo -e "${GREEN}✅ Fetched OpenAPI spec from server${NC}"
        return 0
    else
        return 1
    fi
}

generate_via_python() {
    echo -e "🐍 Generating OpenAPI spec via Python..."
    cd "$ROOT_DIR"
    # Try uv run first (for projects using uv), then fallback to python
    if uv run python scripts/export_openapi.py -o "$SPEC_FILE" 2>/dev/null; then
        echo -e "${GREEN}✅ Generated OpenAPI spec via Python (uv)${NC}"
        return 0
    elif python scripts/export_openapi.py -o "$SPEC_FILE" 2>/dev/null; then
        echo -e "${GREEN}✅ Generated OpenAPI spec via Python${NC}"
        return 0
    else
        return 1
    fi
}

# Parse arguments
MODE="auto"
if [[ "$1" == "--server" ]]; then
    MODE="server"
elif [[ "$1" == "--python" ]]; then
    MODE="python"
fi

# Get the OpenAPI spec
case "$MODE" in
    server)
        if ! fetch_from_server; then
            echo -e "${RED}❌ Failed to fetch from server. Is the API running?${NC}"
            echo -e "   Start it with: cd $ROOT_DIR && uvicorn repotoire.api.app:app"
            exit 1
        fi
        ;;
    python)
        if ! generate_via_python; then
            echo -e "${RED}❌ Failed to generate via Python${NC}"
            exit 1
        fi
        ;;
    auto)
        if ! fetch_from_server; then
            echo -e "${YELLOW}⚠️  Server not available, trying Python export...${NC}"
            if ! generate_via_python; then
                echo -e "${RED}❌ Failed to generate OpenAPI spec${NC}"
                echo -e "   Either start the API server or fix Python import errors"
                exit 1
            fi
        fi
        ;;
esac

echo ""

# Generate TypeScript types
echo -e "📝 Generating TypeScript types..."
cd "$WEB_DIR"
npx openapi-typescript "$SPEC_FILE" -o "$OUTPUT_FILE"

# Add header comment (no timestamp to avoid CI diffs)
HEADER="/**
 * AUTO-GENERATED FILE - DO NOT EDIT DIRECTLY
 *
 * This file is generated from the FastAPI OpenAPI spec.
 * Run \`npm run generate:types\` to regenerate.
 */

"

# Prepend header to file (portable approach)
TEMP_FILE=$(mktemp)
echo "$HEADER" > "$TEMP_FILE"
cat "$OUTPUT_FILE" >> "$TEMP_FILE"
mv "$TEMP_FILE" "$OUTPUT_FILE"

echo ""
echo -e "${GREEN}✅ Types generated successfully!${NC}"
echo -e "   Output: $OUTPUT_FILE"
echo ""
echo -e "💡 Import types in your code:"
echo -e "   import type { paths, components } from '@/types/api.generated';"
echo -e "   type Finding = components['schemas']['FindingResponse'];"
