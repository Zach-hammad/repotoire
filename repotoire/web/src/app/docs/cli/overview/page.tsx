import { MarkdownContent } from "@/components/docs/markdown-content"

export const metadata = {
  title: "CLI Overview | Repotoire Documentation",
  description: "The Repotoire CLI provides graph-powered code analysis from your terminal",
}

const content = `# CLI Overview

Graph-powered code analysis from your terminal. No sign-up required.

## Installation

**Rust (recommended)**:
\`\`\`bash
cargo install repotoire
\`\`\`

**Python**:
\`\`\`bash
pip install repotoire
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

## Core Commands

| Command | Description |
|---------|-------------|
| \`analyze\` | Parse codebase, build graph, run detectors |
| \`findings\` | List all findings with filters |
| \`status\` | Show project health summary |
| \`query\` | Run Cypher queries on the graph |
| \`hotspots\` | Find code with multiple issues |

## AI Commands (Pro)

These require an API key (\`OPENAI_API_KEY\` or \`ANTHROPIC_API_KEY\`):

| Command | Description |
|---------|-------------|
| \`ask\` | Ask questions about your code (RAG) |
| \`fix\` | Generate AI-powered fixes |
| \`auto-fix\` | Batch fix multiple issues |

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
