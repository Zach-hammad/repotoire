#!/usr/bin/env bash
# Repotoire pre-commit hook for Claude Code.
#
# Runs repotoire analysis before git commit. Blocks the commit if
# critical or high severity findings are found. Medium/low findings
# are reported but don't block.
#
# Installed by: scripts/install.sh
# Hook entry in: ~/.claude/settings.json

set -euo pipefail

# Bail silently if repotoire isn't installed
command -v repotoire &>/dev/null || exit 0

# Read hook input from stdin (JSON with tool_name, tool_input, etc.)
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0

# Only fire on git commit commands
if ! echo "$COMMAND" | grep -q 'git commit'; then
    exit 0
fi

# Run repotoire analysis (incremental — typically <2s)
OUTPUT=$(repotoire analyze --format json --severity medium 2>/dev/null) || true

# Check for critical/high findings
HAS_CRITICAL=$(echo "$OUTPUT" | jq -r '.findings_summary.critical // 0' 2>/dev/null) || HAS_CRITICAL=0
HAS_HIGH=$(echo "$OUTPUT" | jq -r '.findings_summary.high // 0' 2>/dev/null) || HAS_HIGH=0

if [ "$HAS_CRITICAL" -gt 0 ] 2>/dev/null || [ "$HAS_HIGH" -gt 0 ] 2>/dev/null; then
    # Build a summary of critical/high findings for Claude
    SUMMARY=$(echo "$OUTPUT" | jq -r '
        [.findings[] | select(.severity == "critical" or .severity == "high")]
        | map("- [\(.severity | ascii_upcase)] \(.title) (\(.affected_files[0] // "unknown"):\(.line_start // "?"))")
        | join("\n")
    ' 2>/dev/null) || SUMMARY="Run 'repotoire analyze' to see details."

    # Deny the commit with actionable feedback
    jq -n --arg reason "$(printf "Repotoire found %d critical and %d high severity issues:\n\n%s\n\nFix these issues before committing. Run 'repotoire analyze' for full details." "$HAS_CRITICAL" "$HAS_HIGH" "$SUMMARY")" '{
        hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "deny",
            permissionDecisionReason: $reason
        }
    }'
    exit 0
fi

# No critical/high findings — allow the commit
exit 0
