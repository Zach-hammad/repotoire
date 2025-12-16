# MCP Server Integration

Use Repotoire directly in Claude Code, Cursor, or any MCP-compatible AI assistant.

## Overview

Repotoire provides an MCP (Model Context Protocol) server that exposes code intelligence tools to AI assistants. The server follows an **Open Core** model:

| Tier | Features | Requirements |
|------|----------|--------------|
| **Free** | Graph analysis, detectors, Cypher queries | Local Neo4j |
| **Pro** | AI search, RAG Q&A, embeddings | API key + subscription |

## Quick Start

### 1. Install Repotoire

```bash
pip install repotoire
```

### 2. Configure Claude Code

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "repotoire": {
      "type": "stdio",
      "command": "repotoire-mcp",
      "env": {
        "REPOTOIRE_NEO4J_URI": "bolt://localhost:7687",
        "REPOTOIRE_NEO4J_PASSWORD": "your-password",
        "REPOTOIRE_API_KEY": "${REPOTOIRE_API_KEY}"
      }
    }
  }
}
```

### 3. Start Using

In Claude Code:
```
> Use the health_check tool to verify Repotoire is connected

> Use analyze_codebase to check code health

> Use search_code to find authentication functions
```

## Available Tools

### Free Tools (Local)

These tools run locally against your Neo4j graph database:

#### `health_check`
Check if Repotoire and Neo4j are running.

```
> health_check

**Repotoire Health Check**
- Neo4j: Connected
- API: Not configured (set REPOTOIRE_API_KEY for Pro features)
```

#### `analyze_codebase`
Run full code health analysis with all detectors.

```
> analyze_codebase {"repository_path": "."}

**Code Health Analysis**

Overall Score: 72.3/100 (Grade: C)

**Category Scores:**
- Structure: 78.5/100
- Quality: 65.2/100
- Architecture: 73.1/100

**Findings:** 47 issues detected
- CRITICAL: 2
- HIGH: 8
- MEDIUM: 22
- LOW: 15
```

#### `query_graph`
Execute custom Cypher queries on the code knowledge graph.

```
> query_graph {"cypher": "MATCH (f:Function) WHERE f.complexity > 20 RETURN f.name, f.complexity ORDER BY f.complexity DESC LIMIT 5"}

**Query Results** (5 rows)

[
  {"f.name": "process_data", "f.complexity": 45},
  {"f.name": "validate_input", "f.complexity": 32},
  ...
]
```

#### `get_codebase_stats`
Get statistics about the ingested codebase.

```
> get_codebase_stats

**Codebase Statistics**

- Functions: 1,234
- Classes: 156
- Files: 89
- Modules: 23
```

### Pro Tools (Via API)

These tools require a Repotoire subscription and API key:

#### `search_code`
Semantic code search using AI embeddings.

```
> search_code {"query": "user authentication", "top_k": 5}

Found 5 results for: "user authentication"

**1. auth.service.authenticate_user** (Function)
   File: src/auth/service.py:45
   Score: 0.92
   Authenticate user with email and password...

**2. models.User** (Class)
   File: src/models/user.py:12
   Score: 0.87
   User model with authentication fields...
```

#### `ask_code_question`
AI-powered Q&A about your codebase using RAG.

```
> ask_code_question {"question": "How does the authentication flow work?"}

**Answer** (confidence: 85%)

The authentication flow works as follows:

1. User submits credentials to `/api/auth/login`
2. `AuthService.authenticate_user()` validates against the database
3. On success, a JWT token is generated via `TokenService.create_token()`
4. The token is returned and stored client-side

**Sources:** 5 code snippets
  1. auth.service.authenticate_user
  2. routes.auth.login
  3. services.token.create_token

**Follow-up questions:**
- How are refresh tokens handled?
- What happens when authentication fails?
```

#### `get_embeddings_status`
Check AI embeddings coverage.

```
> get_embeddings_status

**Embeddings Status**

Coverage: 94.2%
Total: 1,234
Embedded: 1,162
- Functions: 892
- Classes: 156
- Files: 114
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `REPOTOIRE_NEO4J_URI` | Neo4j connection URI | `bolt://localhost:7688` |
| `REPOTOIRE_NEO4J_PASSWORD` | Neo4j password | (empty) |
| `REPOTOIRE_API_KEY` | API key for Pro features | (none) |
| `REPOTOIRE_API_URL` | API base URL | `https://api.repotoire.com` |

### Getting an API Key

1. Sign up at [repotoire.com/pricing](https://repotoire.com/pricing)
2. Go to [Settings > API Keys](https://repotoire.com/settings/api-keys)
3. Create a new API key
4. Set the environment variable:

```bash
export REPOTOIRE_API_KEY="rpt_your_key_here"
```

## Pricing

| Plan | Local Features | AI Features | Price |
|------|----------------|-------------|-------|
| **Free** | Unlimited | - | $0 |
| **Pro** | Unlimited | 1,000 searches/mo, 200 Q&A/mo | $29/mo |
| **Team** | Unlimited | Unlimited | $99/mo |

## Troubleshooting

### "Local features unavailable"

The MCP server can't import Repotoire modules. Ensure:
- Repotoire is installed: `pip install repotoire`
- You're using the correct Python environment

### "Neo4j: Error - Connection refused"

Neo4j isn't running or accessible. Check:
- Neo4j is running: `docker ps | grep neo4j`
- URI is correct: `REPOTOIRE_NEO4J_URI`
- Password is correct: `REPOTOIRE_NEO4J_PASSWORD`

### "This feature requires a Repotoire subscription"

You're trying to use a Pro feature without an API key. Either:
- Set `REPOTOIRE_API_KEY` environment variable
- Use free local features instead
- Sign up at [repotoire.com/pricing](https://repotoire.com/pricing)

### "Invalid API key"

Your API key is incorrect or expired. Regenerate at:
[repotoire.com/settings/api-keys](https://repotoire.com/settings/api-keys)

### "Rate limited"

You've exceeded your plan's usage limits. Either:
- Wait for the rate limit to reset
- Upgrade your plan at [repotoire.com/settings/billing](https://repotoire.com/settings/billing)

## IDE Integration

### Cursor

Add to Cursor settings (`.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "repotoire": {
      "command": "repotoire-mcp",
      "env": {
        "REPOTOIRE_API_KEY": "${REPOTOIRE_API_KEY}"
      }
    }
  }
}
```

### VS Code + Continue

Add to Continue config:

```json
{
  "mcpServers": [
    {
      "name": "repotoire",
      "command": "repotoire-mcp"
    }
  ]
}
```

## See Also

- [RAG & AI Features](rag.md) - Detailed AI feature documentation
- [Configuration](../getting-started/configuration.md) - Full configuration options
- [API Reference](../api/overview.md) - REST API documentation
