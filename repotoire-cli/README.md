# Repotoire

**Graph-powered code analysis.** Find code smells, security issues, and architectural problems — 100% local, no account needed.

## Install

```bash
cargo install repotoire
```

> ⏱️ First install takes 10-15 min (compiles Kuzu graph database). After that, instant.

## Quick Start

```bash
# Analyze your codebase
repotoire analyze .

# View findings
repotoire findings

# Get AI fix suggestions (BYOK)
export OPENAI_API_KEY=sk-...
repotoire fix 1
```

## Features

- **37 detectors** — God classes, circular deps, dead code, security issues, and more
- **Graph-based analysis** — Understands call graphs, inheritance, and data flow
- **9 languages** — Python, TypeScript, JavaScript, Go, Java, Rust, C#, C/C++
- **100% local** — No data leaves your machine
- **BYOK AI fixes** — Bring your own OpenAI/Anthropic key for AI-powered suggestions

## Commands

| Command | Description |
|---------|-------------|
| `analyze <path>` | Run analysis on a codebase |
| `findings` | View findings from last analysis |
| `fix <index>` | Generate AI fix for a finding |
| `graph <query>` | Run raw Cypher queries |
| `stats` | Show graph statistics |
| `status` | Show analysis status |
| `serve` | Start MCP server for IDE integration |

## Output Formats

```bash
repotoire analyze . --format json    # JSON
repotoire analyze . --format sarif   # SARIF (GitHub, VS Code)
repotoire analyze . --format html    # HTML report
repotoire analyze . --format md      # Markdown
```

## BYOK (Bring Your Own Key)

AI features require an API key:

```bash
export OPENAI_API_KEY=sk-...
# or
export ANTHROPIC_API_KEY=sk-ant-...
```

## License

MIT
