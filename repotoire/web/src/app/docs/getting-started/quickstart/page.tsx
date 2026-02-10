import { MarkdownContent } from "@/components/docs/markdown-content"

export const metadata = {
  title: "Quick Start | Repotoire Documentation",
  description: "Get Repotoire running and analyze your first codebase in under 2 minutes",
}

const content = `# Quick Start

Analyze your codebase in under 2 minutes. No sign-up, no Docker, no external services.

## Install

**Rust (recommended)** — fastest analysis, single binary:
\`\`\`bash
cargo install repotoire
\`\`\`
> ⏱️ First install takes ~10 min (compiles Kuzu graph database)

**Python** — quick install, requires Python 3.10+:
\`\`\`bash
pip install repotoire
\`\`\`

## Analyze

\`\`\`bash
cd your-project
repotoire analyze .
\`\`\`

That's it. Repotoire will:
- Parse your code (Python, TypeScript, Go, Java, Rust, C/C++, C#, Kotlin)
- Build a local knowledge graph
- Run 47 detectors
- Show your health score

## Example Output

\`\`\`
🎼 Repotoire Analysis

Repository: ./my-project
Files: 156 | Functions: 412 | Classes: 89

Running 47 detectors...

┌──────────────────┬───────┬───────┐
│ Category         │ Score │ Grade │
├──────────────────┼───────┼───────┤
│ Structure        │    82 │   B   │
│ Quality          │    75 │   C   │
│ Security         │    90 │   A   │
└──────────────────┴───────┴───────┘

Overall Health Score: 82/100 (B)

Top Issues:
  🔴 2 circular dependencies
  🟠 8 dead exports
  🟡 3 god classes
\`\`\`

## What's Analyzed

| Category | Detectors |
|----------|-----------|
| Structure | Circular deps, dead code, god classes |
| Quality | Complexity, duplication, naming |
| Security | Hardcoded secrets, SQL injection, data flow |
| Architecture | Layer violations, coupling |

## Common Commands

\`\`\`bash
# Quick analysis
repotoire analyze .

# View findings
repotoire findings

# Query the graph
repotoire query "MATCH (f:Function) RETURN f.name LIMIT 10"

# Check status
repotoire status
\`\`\`

## AI Features (Pro)

With your own API keys, unlock AI-powered features:

\`\`\`bash
# Set your API key
export OPENAI_API_KEY=sk-...
# or
export ANTHROPIC_API_KEY=sk-ant-...

# Ask questions about your code
repotoire ask "Where is authentication handled?"

# Get AI-powered fixes
repotoire fix
\`\`\`

## MCP Server

Connect Repotoire to Claude, Cursor, or other AI assistants:

\`\`\`bash
repotoire serve
\`\`\`

This starts an MCP server that exposes your codebase analysis to AI tools.

## Next Steps

- [CLI Reference](/docs/cli/overview) - All available commands
- [Detectors](/docs/detectors) - What we analyze
- [Configuration](/docs/configuration) - Customize behavior

## Troubleshooting

### No files found

\`\`\`bash
# Check supported extensions
repotoire analyze . --verbose

# Specify patterns
repotoire analyze . --include "**/*.py"
\`\`\`

### Memory issues on large repos

\`\`\`bash
# Analyze specific directories
repotoire analyze ./src

# Exclude directories
repotoire analyze . --exclude "node_modules,vendor,dist"
\`\`\`
`

export default function QuickStartPage() {
  return <MarkdownContent content={content} />
}
