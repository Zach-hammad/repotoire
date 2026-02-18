import { MarkdownContent } from "@/components/docs/markdown-content"

export const metadata = {
  title: "CLI Overview | Repotoire Documentation",
  description: "The Repotoire CLI provides graph-powered code analysis from your terminal",
}

const content = `# CLI Overview

Graph-powered code analysis from your terminal. No sign-up required.

## Installation

**Rust CLI**:
\`\`\`bash
cargo install repotoire
\`\`\`

## Quick Start

\`\`\`bash
# Analyze current directory
repotoire analyze .

# View findings
repotoire findings

# Check project status
repotoire status
\`\`\`

## Commands

| Command | Description |
|---------|-------------|
| \`analyze\` | Parse codebase, build graph, run 114 detectors |
| \`calibrate\` | Generate adaptive thresholds from your coding style |
| \`findings\` | List all findings with filters |
| \`fix\` | AI-powered fixes (BYOK — Claude, GPT-4, Ollama) |
| \`graph\` | Query the code graph directly |
| \`doctor\` | Check environment setup |
| \`clean\` | Remove cached analysis data |
| \`init\` | Generate \`repotoire.toml\` config |
| \`status\` | Show project health summary |

## Adaptive Thresholds

Repotoire learns your coding patterns. On first \`analyze\`, it auto-calibrates
thresholds based on your codebase's p90/p95 percentiles. No manual setup needed.

\`\`\`bash
# Explicit calibration (optional)
repotoire calibrate .

# Auto-calibrates on first run, reuses profile after
repotoire analyze .
\`\`\`

## MCP Server

| Command | Description |
|---------|-------------|
| \`serve\` | Start MCP server for AI assistants |

## Output Formats

\`\`\`bash
# Terminal (default) - rich formatting
repotoire analyze .

# JSON - for CI/CD
repotoire analyze . --format json

# Quiet - just the score
repotoire analyze . --quiet
\`\`\`

## Common Workflows

### Daily Development

\`\`\`bash
# Quick health check
repotoire analyze .

# Focus on critical issues
repotoire findings --severity critical,high

# Get fixes
repotoire fix
\`\`\`

### CI/CD Integration

\`\`\`bash
# JSON output for parsing
repotoire analyze . --format json > results.json

# Fail on critical findings
repotoire analyze . --fail-on critical
\`\`\`

### AI Assistant Integration

\`\`\`bash
# Start MCP server
repotoire serve

# In Claude/Cursor, connect to the MCP server
# Then ask: "What are the main issues in this codebase?"
\`\`\`

## Configuration

Repotoire works out of the box with sensible defaults. For customization:

\`\`\`bash
# Create config file
repotoire init

# Show current config
repotoire config
\`\`\`

### Environment Variables

| Variable | Description |
|----------|-------------|
| \`OPENAI_API_KEY\` | Enable AI features (ask, fix) |
| \`ANTHROPIC_API_KEY\` | Alternative to OpenAI |
| \`REPOTOIRE_LOG_LEVEL\` | DEBUG, INFO, WARNING, ERROR |

## Supported Languages

- Python
- TypeScript / JavaScript
- Go
- Java
- Rust
- C / C++
- C#
- Kotlin

All languages are parsed with tree-sitter for accurate AST analysis.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Critical findings detected (with --fail-on) |
`

export default function CLIOverviewPage() {
  return <MarkdownContent content={content} />
}
