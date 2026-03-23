#!/usr/bin/env bash
# Repotoire installer — downloads the latest release binary for your platform.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Zach-hammad/repotoire/main/scripts/install.sh | bash
#
# Or with a specific version:
#   curl -fsSL https://raw.githubusercontent.com/Zach-hammad/repotoire/main/scripts/install.sh | bash -s -- v0.4.0

set -euo pipefail

REPO="Zach-hammad/repotoire"
INSTALL_DIR="${INSTALL_DIR:-/usr/local/bin}"
BINARY_NAME="repotoire"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[info]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
error() { echo -e "${RED}[error]${NC} $*" >&2; exit 1; }

# Detect platform
detect_platform() {
    local os arch

    case "$(uname -s)" in
        Linux*)  os="linux" ;;
        Darwin*) os="darwin" ;;
        *)       error "Unsupported OS: $(uname -s). Repotoire supports Linux and macOS." ;;
    esac

    case "$(uname -m)" in
        x86_64|amd64)  arch="x86_64" ;;
        aarch64|arm64) arch="aarch64" ;;
        *)             error "Unsupported architecture: $(uname -m). Repotoire supports x86_64 and aarch64." ;;
    esac

    echo "${os}-${arch}"
}

# Get the latest release version from GitHub API
get_latest_version() {
    local url="https://api.github.com/repos/${REPO}/releases/latest"
    if command -v curl &>/dev/null; then
        curl -fsSL "$url" | grep '"tag_name"' | head -1 | sed 's/.*"tag_name": *"\([^"]*\)".*/\1/'
    elif command -v wget &>/dev/null; then
        wget -qO- "$url" | grep '"tag_name"' | head -1 | sed 's/.*"tag_name": *"\([^"]*\)".*/\1/'
    else
        error "Neither curl nor wget found. Please install one of them."
    fi
}

# Download and install
install() {
    local version="${1:-}"
    local platform
    platform="$(detect_platform)"

    if [ -z "$version" ]; then
        info "Fetching latest version..."
        version="$(get_latest_version)"
        if [ -z "$version" ]; then
            error "Could not determine latest version. Try specifying a version: install.sh v0.4.0"
        fi
    fi

    info "Installing repotoire ${version} for ${platform}..."

    local archive_name="repotoire-${platform}.tar.gz"
    local download_url="https://github.com/${REPO}/releases/download/${version}/${archive_name}"

    # Create temp directory
    local tmpdir
    tmpdir="$(mktemp -d)"
    trap 'rm -rf "$tmpdir"' EXIT

    # Download
    info "Downloading ${download_url}..."
    if command -v curl &>/dev/null; then
        curl -fsSL -o "${tmpdir}/${archive_name}" "$download_url" || error "Download failed. Check that version ${version} exists and has a ${platform} binary."
    else
        wget -q -O "${tmpdir}/${archive_name}" "$download_url" || error "Download failed."
    fi

    # Extract
    info "Extracting..."
    tar -xzf "${tmpdir}/${archive_name}" -C "$tmpdir"

    # Find the binary (may be at top level or in a subdirectory)
    local binary
    binary="$(find "$tmpdir" -name "$BINARY_NAME" -type f | head -1)"
    if [ -z "$binary" ]; then
        error "Binary not found in archive. Contents: $(ls -la "$tmpdir")"
    fi

    # Install
    chmod +x "$binary"
    if [ -w "$INSTALL_DIR" ]; then
        mv "$binary" "${INSTALL_DIR}/${BINARY_NAME}"
    else
        info "Installing to ${INSTALL_DIR} (requires sudo)..."
        sudo mv "$binary" "${INSTALL_DIR}/${BINARY_NAME}"
    fi

    info "Installed repotoire ${version} to ${INSTALL_DIR}/${BINARY_NAME}"

    # Verify
    if command -v "$BINARY_NAME" &>/dev/null; then
        local installed_version
        installed_version="$("$BINARY_NAME" version 2>/dev/null || echo "unknown")"
        info "Verified: ${installed_version}"
    fi

    echo ""
    info "Get started:"
    echo "  repotoire analyze ."
    echo "  repotoire lsp          # for editor integration"
    echo ""
}

install "$@"
