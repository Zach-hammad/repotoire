# MCP Server Integration

Repotoire includes a built-in MCP (Model Context Protocol) server that enables AI assistants like Claude to directly interact with your codebase analysis tools.

## What is MCP?

The [Model Context Protocol](https://modelcontextprotocol.io/) is an open standard that lets AI assistants use external tools. Instead of copying and pasting analysis results, your AI assistant can:

- Run code analysis directly
- Query your code's knowledge graph
- Read files with context
- Search code semantically
- Generate fixes for issues

This creates a seamless workflow where your AI assistant has deep, real-time access to your codebase structure and quality metrics.

## Quick Start

```bash
# Start the MCP server (in your project directory)
repotoire serve

# Or force local-only mode (no cloud API calls)
repotoire serve --local
```

The server communicates via JSON-RPC 2.0 over stdin/stdout, which is the standard MCP transport.

## Configuring AI Assistants

### Claude Desktop

Add to your Claude Desktop config file:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`  
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`  
**Linux:** `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "repotoire": {
      "command": "repotoire",
      "args": ["serve"],
      "cwd": "/path/to/your/project"
    }
  }
}
```

**Multiple projects:**
```json
{
  "mcpServers": {
    "my-app": {
      "command": "repotoire",
      "args": ["serve"],
      "cwd": "/path/to/my-app"
    },
    "another-project": {
      "command": "repotoire",
      "args": ["serve"],
      "cwd": "/path/to/another-project"
    }
  }
}
```

Restart Claude Desktop after editing the config.

### Cursor

In Cursor settings, add an MCP server:

1. Open Settings → MCP
2. Add a new server with:
   - **Name:** `repotoire`
   - **Command:** `repotoire serve`
   - **Working Directory:** Your project path

### Other MCP-Compatible Clients

Any client supporting MCP's stdio transport can use Repotoire. The server follows the [MCP specification](https://spec.modelcontextprotocol.io/).

## Available Tools

### FREE Tier (Local CLI)

These tools work offline with no API key required:

| Tool | Description |
|------|-------------|
| `analyze` | Run code analysis with all detectors. Returns summary of findings by severity. |
| `query_graph` | Query the code knowledge graph. Get functions, classes, files, or stats. |
| `get_findings` | List findings from the last analysis. Filter by severity or detector. |
| `get_file` | Read file content, optionally specifying line ranges. |
| `get_architecture` | Get codebase overview: languages, node counts, top classes. |
| `list_detectors` | List all available code quality detectors. |
| `get_hotspots` | Find files with the most issues (problem areas). |

### AI-Powered Tools (BYOK)

Set your own AI API key to enable these locally:

```bash
# Use any of these (checked in order):
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
export DEEPINFRA_API_KEY="..."
export OPENROUTER_API_KEY="..."
# Or have Ollama running locally
```

| Tool | Description |
|------|-------------|
| `generate_fix` | Generate AI-powered fix suggestions for findings. |

### PRO Tier (Cloud API)

With a `REPOTOIRE_API_KEY`, unlock cloud-powered features:

| Tool | Description |
|------|-------------|
| `search_code` | Semantic code search using AI embeddings. Find code by natural language. |
| `ask` | RAG-powered Q&A about your codebase. Get answers with source citations. |
| `generate_fix` | Generate fixes (also works with BYOK keys above). |

## Tool Reference

### analyze

Run code analysis on the repository.

**Parameters:**
- `repo_path` (string, optional): Path to repository. Default: current directory
- `incremental` (boolean, optional): Only analyze changed files. Default: true

**Example response:**
```json
{
  "status": "completed",
  "total_findings": 42,
  "by_severity": {
    "critical": 2,
    "high": 8,
    "medium": 15,
    "low": 12,
    "info": 5
  }
}
```

### query_graph

Query the code knowledge graph.

**Parameters:**
- `type` (string): Query type - `functions`, `classes`, `files`, or `stats`

**Example:**
```json
{"type": "stats"}
```

### get_findings

Get findings from the last analysis.

**Parameters:**
- `severity` (string, optional): Filter by `critical`, `high`, `medium`, `low`, or `info`
- `detector` (string, optional): Filter by detector name
- `limit` (integer, optional): Max results. Default: 20

### get_file

Read file content from the repository.

**Parameters:**
- `file_path` (string, required): Path relative to repo root
- `start_line` (integer, optional): Start line (1-indexed)
- `end_line` (integer, optional): End line (1-indexed)

**Example:**
```json
{"file_path": "src/main.rs", "start_line": 1, "end_line": 50}
```

### get_architecture

Get codebase architecture overview. No parameters required.

Returns node counts, language distribution, and top classes by method count.

### list_detectors

List all available code quality detectors. No parameters required.

### get_hotspots

Get files with the most issues.

**Parameters:**
- `limit` (integer, optional): Max files to return. Default: 10

### search_code (PRO)

Semantic code search using AI embeddings.

**Parameters:**
- `query` (string, required): Natural language search query
- `top_k` (integer, optional): Max results. Default: 10
- `entity_types` (array, optional): Filter by type (`Function`, `Class`, `File`)

**Example:**
```json
{"query": "authentication and password validation", "top_k": 5}
```

### ask (PRO)

Ask questions about the codebase using RAG.

**Parameters:**
- `question` (string, required): Natural language question
- `top_k` (integer, optional): Context snippets to retrieve. Default: 10

**Example:**
```json
{"question": "How does the user authentication flow work?"}
```

### generate_fix (PRO/BYOK)

Generate an AI-powered fix for a finding.

**Parameters:**
- `finding_id` (string, required): 1-based index from analyze results

**Example:**
```json
{"finding_id": "3"}
```

## Example Workflows

### Code Review Assistance

```
You: "What are the most critical issues in this codebase?"

Claude: [Uses analyze tool, then get_findings with severity=critical]
       "I found 2 critical issues:
        1. SQL injection vulnerability in user_query.py:45
        2. Hardcoded credentials in config.py:12
        
        Would you like me to generate fixes for these?"
```

### Understanding New Codebases

```
You: "Give me an overview of this project's architecture"

Claude: [Uses get_architecture tool]
       "This is a Python project with 45 files across 3 main modules:
        - api/ (FastAPI routes, 12 endpoints)
        - services/ (business logic, 8 service classes)
        - models/ (SQLAlchemy models, 15 entities)
        
        The largest class is UserService with 23 methods."
```

### Finding Specific Code

```
You: "Find all the database query functions"

Claude: [Uses query_graph with type=functions, then filters]
       "Found 8 database-related functions:
        - get_user_by_id (users.py:34)
        - create_user (users.py:56)
        ..."
```

### Semantic Search (PRO)

```
You: "Find code related to payment processing"

Claude: [Uses search_code tool]
       "Found 5 relevant code sections:
        1. PaymentProcessor.charge() - handles Stripe integration
        2. RefundService.process_refund() - refund logic
        ..."
```

### Automated Fixes (PRO/BYOK)

```
You: "Fix the SQL injection vulnerability"

Claude: [Uses generate_fix with the finding index]
       "Here's the proposed fix:
        
        - query = f'SELECT * FROM users WHERE id = {user_id}'
        + query = 'SELECT * FROM users WHERE id = ?'
        + cursor.execute(query, (user_id,))
        
        This uses parameterized queries to prevent injection."
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `REPOTOIRE_API_KEY` | Enable PRO cloud features |
| `REPOTOIRE_API_URL` | Custom API endpoint (default: https://api.repotoire.io) |
| `ANTHROPIC_API_KEY` | Enable local AI fixes via Claude |
| `OPENAI_API_KEY` | Enable local AI fixes via GPT |
| `DEEPINFRA_API_KEY` | Enable local AI fixes via DeepInfra |
| `OPENROUTER_API_KEY` | Enable local AI fixes via OpenRouter |

## Modes

The server operates in three modes:

1. **FREE** - All local analysis tools, no AI features
2. **BYOK** (Bring Your Own Key) - Local analysis + AI fixes with your API key
3. **PRO** - Full cloud features including semantic search and RAG

Check the startup message to see which mode is active:
```
🎼 Repotoire MCP server started (BYOK)
   Repository: /path/to/project
```

## Troubleshooting

### Server not starting

1. Ensure Repotoire is installed: `repotoire --version`
2. Check you're in a valid git repository
3. Run `repotoire init` if you haven't initialized the project

### Tools not appearing in Claude

1. Restart Claude Desktop after config changes
2. Check the config JSON syntax is valid
3. Verify the `cwd` path exists and is a Repotoire project

### "No findings available" error

Run `repotoire analyze` or use the `analyze` tool first to generate findings.

### AI features not working

Ensure you have at least one AI API key set:
```bash
echo $ANTHROPIC_API_KEY  # Should show your key
```

## Protocol Details

- **Transport:** stdio (stdin/stdout)
- **Protocol:** JSON-RPC 2.0
- **MCP Version:** 2024-11-05

The server is fully compliant with the MCP specification and can be used with any compatible client.
