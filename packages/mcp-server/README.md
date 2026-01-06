# repotoire-mcp

Repotoire MCP server for Claude Code, Cursor, and other AI agents.

## Quick Start

```bash
# Option 1: Login via CLI (recommended - no env vars needed!)
pip install repotoire
repotoire login

# Option 2: Set API key directly
export REPOTOIRE_API_KEY=your_api_key
```

Then run:
```bash
npx repotoire-mcp
```

## Setup

### Claude Code

Add to your `~/.claude.json`:

```json
{
  "mcpServers": {
    "repotoire": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "repotoire-mcp"]
    }
  }
}
```

If you've run `repotoire login`, no `env` config is needed! The MCP server automatically reads your credentials from `~/.repotoire/credentials`.

To use an explicit API key instead:

```json
{
  "mcpServers": {
    "repotoire": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "repotoire-mcp"],
      "env": {
        "REPOTOIRE_API_KEY": "your_api_key"
      }
    }
  }
}
```

### Cursor

Add to your MCP settings:

```json
{
  "repotoire": {
    "command": "npx",
    "args": ["-y", "repotoire-mcp"]
  }
}
```

## Authentication

The MCP server looks for credentials in this order:

1. `REPOTOIRE_API_KEY` environment variable
2. `~/.repotoire/credentials` file (created by `repotoire login`)

## Get Your API Key

- **Via CLI**: Run `repotoire login` to authenticate via browser
- **Manual**: Get your API key at https://repotoire.com/dashboard/settings/api-keys

## Available Tools

- **search_code** - Semantic code search using AI embeddings
- **ask_code_question** - RAG-powered Q&A about your codebase
- **get_prompt_context** - Get relevant code context for AI tasks
- **get_file_content** - Read file contents with metadata
- **get_architecture** - Get codebase architecture overview

## Environment Variables

- `REPOTOIRE_API_KEY` - Your Repotoire API key (optional if logged in via CLI)
- `REPOTOIRE_API_URL` - API endpoint (default: https://api.repotoire.com)

## License

MIT
