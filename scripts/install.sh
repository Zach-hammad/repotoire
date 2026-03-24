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

    # Install Claude Code hook
    install_claude_hook

    echo ""
    info "Get started:"
    echo "  repotoire analyze ."
    echo "  repotoire lsp          # for editor integration"
    echo ""
}

# Install Claude Code pre-commit hook
install_claude_hook() {
    local hook_dir="$HOME/.repotoire/hooks"
    local hook_script="$hook_dir/pre-commit.sh"
    local claude_settings="$HOME/.claude/settings.json"

    # Create hook directory
    mkdir -p "$hook_dir"

    # Write the hook script
    cat > "$hook_script" << 'HOOKEOF'
#!/usr/bin/env bash
# Repotoire pre-commit hook for Claude Code.
# Blocks commits with critical/high findings. Medium/low are advisory.
set -euo pipefail
command -v repotoire &>/dev/null || exit 0
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0
if ! echo "$COMMAND" | grep -q 'git commit'; then
    exit 0
fi
OUTPUT=$(repotoire analyze --format json --severity medium 2>/dev/null) || true
HAS_CRITICAL=$(echo "$OUTPUT" | jq -r '.findings_summary.critical // 0' 2>/dev/null) || HAS_CRITICAL=0
HAS_HIGH=$(echo "$OUTPUT" | jq -r '.findings_summary.high // 0' 2>/dev/null) || HAS_HIGH=0
if [ "$HAS_CRITICAL" -gt 0 ] 2>/dev/null || [ "$HAS_HIGH" -gt 0 ] 2>/dev/null; then
    SUMMARY=$(echo "$OUTPUT" | jq -r '[.findings[] | select(.severity == "critical" or .severity == "high")] | map("- [\(.severity | ascii_upcase)] \(.title) (\(.affected_files[0] // "unknown"):\(.line_start // "?"))") | join("\n")' 2>/dev/null) || SUMMARY="Run 'repotoire analyze' for details."
    jq -n --arg reason "$(printf "Repotoire found %d critical and %d high severity issues:\n\n%s\n\nFix these before committing." "$HAS_CRITICAL" "$HAS_HIGH" "$SUMMARY")" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$reason}}'
    exit 0
fi
exit 0
HOOKEOF
    chmod +x "$hook_script"
    info "Installed hook script to ${hook_script}"

    # Merge into Claude Code settings.json
    if ! command -v jq &>/dev/null; then
        warn "jq not found — skipping Claude Code hook setup."
        warn "Install jq and re-run, or manually add the hook to ${claude_settings}"
        return
    fi

    mkdir -p "$HOME/.claude"

    local hook_entry
    hook_entry=$(jq -n --arg cmd "$hook_script" '{
        hooks: {
            PreToolUse: [{
                matcher: "Bash",
                hooks: [{
                    type: "command",
                    command: $cmd
                }]
            }]
        }
    }')

    if [ -f "$claude_settings" ]; then
        # Check if hook already exists
        if jq -e '.hooks.PreToolUse[]?.hooks[]? | select(.command | contains("repotoire"))' "$claude_settings" &>/dev/null; then
            info "Claude Code hook already configured"
            return
        fi

        # Merge: add our PreToolUse entry to existing hooks
        local merged
        merged=$(jq --argjson new "$hook_entry" '
            .hooks //= {} |
            .hooks.PreToolUse //= [] |
            .hooks.PreToolUse += $new.hooks.PreToolUse
        ' "$claude_settings")
        echo "$merged" > "$claude_settings"
    else
        # Create new settings file
        echo "$hook_entry" > "$claude_settings"
    fi

    info "Claude Code hook configured — repotoire will check code before commits"
}

install "$@"
