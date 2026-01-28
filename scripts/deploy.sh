#!/bin/bash
# Deploy Repotoire services to Fly.io
# Usage: ./scripts/deploy.sh [all|api|worker|marketplace|status]
#
# Features:
# - Builds shared image once for API + Worker (saves time)
# - Auth check with auto-login
# - Colored output with status
#
# Examples:
#   ./scripts/deploy.sh           # Deploy all services
#   ./scripts/deploy.sh api       # Deploy only API
#   ./scripts/deploy.sh worker    # Deploy only worker
#   ./scripts/deploy.sh marketplace # Deploy only marketplace MCP
#   ./scripts/deploy.sh status    # Check all service statuses

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Shared image reference (set after build)
SHARED_IMAGE=""

# Check if fly CLI is installed
if ! command -v fly &> /dev/null; then
    echo -e "${RED}Error: fly CLI not installed${NC}"
    echo "Install with: curl -L https://fly.io/install.sh | sh"
    exit 1
fi

# Verify authentication
echo -e "${BLUE}Checking Fly.io authentication...${NC}"
if ! fly auth whoami &> /dev/null; then
    echo -e "${YELLOW}Not authenticated. Running fly auth login...${NC}"
    fly auth login
fi
echo -e "${GREEN}Authenticated as: $(fly auth whoami)${NC}"

# Parse arguments
SERVICE="${1:-all}"

build_shared_image() {
    echo -e "\n${BLUE}━━━ Building shared image for API + Worker ━━━${NC}"

    # Build and push image for API app (Worker uses same image)
    fly deploy --config fly.toml --build-only --push

    # Get the image reference from the latest release
    SHARED_IMAGE=$(fly releases list -a repotoire-api --json 2>/dev/null | jq -r '.[0].ImageRef' || echo "")

    if [ -z "$SHARED_IMAGE" ] || [ "$SHARED_IMAGE" == "null" ]; then
        echo -e "${YELLOW}Warning: Could not get image ref, will rebuild for each service${NC}"
        SHARED_IMAGE=""
    else
        echo -e "${GREEN}Built image: $SHARED_IMAGE${NC}"
    fi
}

deploy_api() {
    echo -e "\n${BLUE}━━━ Deploying API ━━━${NC}"

    if [ -n "$SHARED_IMAGE" ]; then
        fly deploy --config fly.toml --image "$SHARED_IMAGE"
    else
        fly deploy --config fly.toml
    fi

    echo -e "${GREEN}✓ API deployed${NC}"
}

deploy_worker() {
    echo -e "\n${BLUE}━━━ Deploying Worker ━━━${NC}"

    if [ -n "$SHARED_IMAGE" ]; then
        fly deploy --config fly.worker.toml --image "$SHARED_IMAGE"
    else
        fly deploy --config fly.worker.toml
    fi

    echo -e "${GREEN}✓ Worker deployed${NC}"
}

deploy_marketplace() {
    echo -e "\n${BLUE}━━━ Deploying Marketplace MCP ━━━${NC}"
    # Marketplace has its own Dockerfile, always builds fresh
    fly deploy --config deploy/marketplace-mcp/fly.toml
    echo -e "${GREEN}✓ Marketplace MCP deployed${NC}"
}

check_status() {
    echo -e "\n${BLUE}━━━ Deployment Status ━━━${NC}"

    echo -e "\n${YELLOW}API:${NC}"
    fly status -a repotoire-api 2>/dev/null | head -15 || echo "  Not deployed"

    echo -e "\n${YELLOW}Worker:${NC}"
    fly status -a repotoire-worker 2>/dev/null | head -15 || echo "  Not deployed"

    echo -e "\n${YELLOW}Marketplace MCP:${NC}"
    fly status -a repotoire-marketplace-mcp 2>/dev/null | head -15 || echo "  Not deployed"

    echo -e "\n${YELLOW}FalkorDB:${NC}"
    fly status -a repotoire-falkor 2>/dev/null | head -15 || echo "  Not deployed"
}

check_health() {
    echo -e "\n${BLUE}━━━ Health Checks ━━━${NC}"

    echo -ne "${YELLOW}API:${NC} "
    if curl -sf https://repotoire-api.fly.dev/health > /dev/null 2>&1; then
        echo -e "${GREEN}healthy${NC}"
    else
        echo -e "${RED}unhealthy${NC}"
    fi

    echo -ne "${YELLOW}Marketplace MCP:${NC} "
    if curl -sf https://repotoire-marketplace-mcp.fly.dev/health > /dev/null 2>&1; then
        echo -e "${GREEN}healthy${NC}"
    else
        echo -e "${RED}unhealthy${NC}"
    fi
}

case $SERVICE in
    all)
        echo -e "${BLUE}Deploying all services...${NC}"
        build_shared_image
        deploy_api
        deploy_worker
        deploy_marketplace
        check_status
        check_health
        ;;
    api)
        deploy_api
        fly status -a repotoire-api
        ;;
    worker)
        deploy_worker
        fly status -a repotoire-worker
        ;;
    marketplace)
        deploy_marketplace
        fly status -a repotoire-marketplace-mcp
        ;;
    status)
        check_status
        check_health
        ;;
    *)
        echo -e "${RED}Unknown service: $SERVICE${NC}"
        echo "Usage: $0 [all|api|worker|marketplace|status]"
        exit 1
        ;;
esac

echo -e "\n${GREEN}✓ Done!${NC}"
