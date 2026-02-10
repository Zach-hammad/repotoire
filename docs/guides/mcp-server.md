# MCP Server Integration

Use Repotoire directly in Claude Code, Cursor, or any MCP-compatible AI assistant.

## Quick Start

### 1. Get Your API Key

1. Sign up at [repotoire.com](https://repotoire.com)
2. Go to [Settings > API Keys](https://repotoire.com/settings/api-keys)
3. Create a new API key

### 2. Install Repotoire

```bash
pip install repotoire
```

### 3. Configure Claude Code

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "repotoire": {
      "type": "stdio",
      "command": "repotoire-mcp",
      "env": {
        "REPOTOIRE_API_KEY": "your_api_key_here"
      }
    }
  }
}
```

### 4. Start Using

In Claude Code:
```
> Use search_code to find authentication functions

> Use ask_code_question to understand how error handling works

> Use get_architecture to see the codebase structure
```

## Available Tools

### `search_code`
Semantic code search using AI embeddings.

```
> search_code {"query": "user authentication", "top_k": 5}

**Found 5 results** for: "user authentication"

### 1. auth.service.authenticate_user
**Type:** Function
**Location:** `src/auth/service.py:45`
**Relevance:** 92%

> Authenticate user with email and password...
```

### `ask_code_question`
AI-powered Q&A about your codebase using RAG.

```
> ask_code_question {"question": "How does the authentication flow work?"}

**Answer** (confidence: 85%)

The authentication flow works as follows:

1. User submits credentials to `/api/auth/login`
2. `AuthService.authenticate_user()` validates against the database
3. On success, a JWT token is generated via `TokenService.create_token()`

---

**Sources** (3 code snippets):
1. `auth.service.authenticate_user` - src/auth/service.py:45
2. `routes.auth.login` - src/routes/auth.py:12
3. `services.token.create_token` - src/services/token.py:28

**Suggested follow-up questions:**
- How are refresh tokens handled?
- What happens when authentication fails?
```

### `get_prompt_context`
Get curated code context for prompt engineering.

```
> get_prompt_context {"task": "implement user registration"}

**Context for task:** implement user registration

### 1. models.User
**Type:** Class
**File:** `src/models/user.py`

```python
class User(BaseModel):
    email: str
    password_hash: str
    created_at: datetime
```

### 2. services.auth.hash_password
**Type:** Function
**File:** `src/services/auth.py`
...
```

### `get_file_content`
Read the content of a specific file from the codebase.

```
> get_file_content {"file_path": "src/auth/service.py"}

**File:** `src/auth/service.py`

**Metadata:**
- Lines: 156
- Functions: 8
- Classes: 1

```python
"""Authentication service module."""

from typing import Optional
from .models import User
...
```

### `get_architecture`
Get an overview of the codebase architecture.

```
> get_architecture {"depth": 2}

**Codebase Architecture**

**Project:** my-project

**Structure:**
└── src/
    ├── auth/
    ├── models/
    ├── routes/
    └── services/

**Modules:** 4
- `auth` (5 files)
- `models` (3 files)
- `routes` (8 files)
- `services` (6 files)

**Detected Patterns:**
- Repository pattern
- Service layer
- MVC architecture
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `REPOTOIRE_API_KEY` | API key (required) | (none) |
| `REPOTOIRE_API_URL` | API base URL | `https://repotoire.com` |

### Getting an API Key

1. Sign up at [repotoire.com/pricing](https://repotoire.com/pricing)
2. Go to [Settings > API Keys](https://repotoire.com/settings/api-keys)
3. Create a new API key
4. Set the environment variable:

```bash
export REPOTOIRE_API_KEY="rpt_your_key_here"
```

## Pricing

| Plan | Features | Price |
|------|----------|-------|
| **Pro** | 1,000 searches/mo, 200 Q&A/mo | $29/mo |
| **Team** | Unlimited | $99/mo |

## Troubleshooting

### "REPOTOIRE_API_KEY environment variable not set"

The MCP server requires an API key. Set it:
```bash
export REPOTOIRE_API_KEY="your_key_here"
```

Or in your MCP configuration:
```json
{
  "env": {
    "REPOTOIRE_API_KEY": "your_key_here"
  }
}
```

Get your key at [repotoire.com/settings/api-keys](https://repotoire.com/settings/api-keys)

### "Invalid API key"

Your API key is incorrect or expired. Regenerate at:
[repotoire.com/settings/api-keys](https://repotoire.com/settings/api-keys)

### "Subscription required"

You need an active subscription to use the API. Upgrade at:
[repotoire.com/pricing](https://repotoire.com/pricing)

### "Rate limited"

You've exceeded your plan's usage limits. The server implements exponential backoff, but if retries are exhausted:
- Wait for the rate limit to reset
- Upgrade your plan at [repotoire.com/settings/billing](https://repotoire.com/settings/billing)

### "Repotoire API temporarily unavailable"

The API is experiencing issues. This is usually temporary - wait a few minutes and try again.

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
      "command": "repotoire-mcp",
      "env": {
        "REPOTOIRE_API_KEY": "${REPOTOIRE_API_KEY}"
      }
    }
  ]
}
```

## See Also

- [RAG & AI Features](rag.md) - Detailed AI feature documentation
- [API Reference](../api/overview.md) - REST API documentation
