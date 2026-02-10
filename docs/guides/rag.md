# RAG & AI Features

Use Repotoire's AI-powered features for natural language code search and intelligent analysis.

## Overview

Repotoire's RAG (Retrieval-Augmented Generation) system combines:

- **Vector embeddings** - Semantic understanding of code
- **Knowledge graph** - Structural relationships
- **LLM generation** - Natural language answers

## Quick Start

### 1. Generate Embeddings

First, ingest your codebase with embeddings enabled:

```bash
# Set API key for embedding generation
export OPENAI_API_KEY=sk-...

# Ingest with embeddings
repotoire ingest /path/to/repo --generate-embeddings
```

### 2. Search Your Code

```bash
# Semantic search
repotoire search "authentication logic"

# Ask questions
repotoire ask "How does the payment processing work?"
```

### 3. Use the API

```bash
# Search endpoint
curl -X POST http://localhost:8000/api/v1/code/search \
  -H "Content-Type: application/json" \
  -d '{"query": "error handling", "top_k": 5}'

# Ask endpoint
curl -X POST http://localhost:8000/api/v1/code/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "How do I add a new API endpoint?"}'
```

## Embedding Backends

Repotoire supports multiple embedding providers:

| Backend | Model | Cost | Quality | Setup |
|---------|-------|------|---------|-------|
| **OpenAI** | text-embedding-3-small | $0.02/1M tokens | Great | API key |
| **DeepInfra** | Qwen3-Embedding-8B | $0.01/1M tokens | Best | API key |
| **Local** | Qwen3-Embedding-0.6B | Free | Excellent | ~1.5GB download |

### OpenAI (Default)

```bash
export OPENAI_API_KEY=sk-...
repotoire ingest . --generate-embeddings --embedding-backend openai
```

### DeepInfra (Recommended)

Best quality-to-cost ratio:

```bash
export DEEPINFRA_API_KEY=...
repotoire ingest . --generate-embeddings --embedding-backend deepinfra
```

### Local (Free)

No API key required:

```bash
pip install repotoire[local-embeddings]
repotoire ingest . --generate-embeddings --embedding-backend local
```

## CLI Commands

### `repotoire search`

Semantic code search:

```bash
# Basic search
repotoire search "database connection"

# Limit results
repotoire search "authentication" --top-k 10

# Filter by entity type
repotoire search "user model" --type Class
```

### `repotoire ask`

Natural language questions:

```bash
# Ask about architecture
repotoire ask "What design patterns are used?"

# Ask about specific functionality
repotoire ask "How is caching implemented?"

# Ask about relationships
repotoire ask "What calls the payment service?"
```

## API Endpoints

### POST `/api/v1/code/search`

Semantic code search.

**Request:**

```json
{
  "query": "authentication functions",
  "top_k": 10,
  "entity_types": ["Function", "Class"],
  "include_related": true
}
```

**Response:**

```json
{
  "results": [
    {
      "entity_type": "Function",
      "qualified_name": "auth.authenticate_user",
      "name": "authenticate_user",
      "code": "def authenticate_user(username, password): ...",
      "docstring": "Authenticate user with credentials",
      "similarity_score": 0.89,
      "file_path": "auth.py",
      "line_start": 10,
      "line_end": 25
    }
  ],
  "total": 5,
  "search_strategy": "hybrid",
  "execution_time_ms": 245
}
```

### POST `/api/v1/code/ask`

Ask questions with AI-generated answers.

**Request:**

```json
{
  "question": "How do I add a new API endpoint?",
  "top_k": 5,
  "include_code": true
}
```

**Response:**

```json
{
  "answer": "To add a new API endpoint:\n\n1. Create a route in `api/routes/`\n2. Define the handler function...",
  "sources": [
    {
      "entity": "api.routes.users",
      "relevance": 0.92,
      "code_snippet": "..."
    }
  ],
  "confidence": 0.85
}
```

### GET `/api/v1/code/embeddings/status`

Check embedding coverage.

**Response:**

```json
{
  "total_entities": 1500,
  "embedded_entities": 1450,
  "coverage_percentage": 96.7,
  "missing_types": {
    "Function": 30,
    "Class": 20
  }
}
```

## Configuration

### In Config File

```yaml
# .repotoirerc
embeddings:
  backend: deepinfra  # openai, deepinfra, local
  model: Qwen/Qwen3-Embedding-8B  # optional, uses backend default
  batch_size: 100

rag:
  top_k: 10
  include_related: true
  llm_model: gpt-4o  # for /ask endpoint
```

### Environment Variables

```bash
# OpenAI backend
export OPENAI_API_KEY=sk-...

# DeepInfra backend
export DEEPINFRA_API_KEY=...

# Local backend (no key needed)
# Downloads model on first use
```

## Use Cases

### Code Discovery

Find code by functionality, not just names:

```bash
repotoire search "handles user login"
repotoire search "validates credit card numbers"
repotoire search "sends email notifications"
```

### Architecture Understanding

Understand how systems work:

```bash
repotoire ask "What is the data flow for order processing?"
repotoire ask "How are background jobs scheduled?"
repotoire ask "What external APIs do we call?"
```

### Onboarding

Help new developers get up to speed:

```bash
repotoire ask "How is the project structured?"
repotoire ask "Where should I add new feature code?"
repotoire ask "What testing patterns are used?"
```

### Code Review Support

Find related code during reviews:

```bash
repotoire search "similar to PaymentProcessor"
repotoire ask "What else uses the cache decorator?"
```

## Performance

| Codebase Size | Embedding Time | Search Latency |
|---------------|----------------|----------------|
| 1,000 files | ~2 minutes | < 500ms |
| 10,000 files | ~15 minutes | < 1s |
| 50,000 files | ~1 hour | < 2s |

### Optimization Tips

1. **Use incremental embeddings** - Only embed changed files:

   ```bash
   repotoire ingest . --generate-embeddings --incremental
   ```

2. **Filter entity types** - Embed only what you need:

   ```yaml
   embeddings:
     entity_types: [Function, Class]  # Skip variables
   ```

3. **Use local backend** - Faster for large codebases (no network latency)

## Troubleshooting

### No Results Returned

```bash
# Check embedding status
repotoire embeddings status

# Re-generate embeddings if needed
repotoire ingest . --generate-embeddings --force
```

### Slow Search

```bash
# Check vector index exists
repotoire validate --check-indexes

# Rebuild indexes if needed
repotoire schema --rebuild-indexes
```

### API Key Errors

```bash
# Verify key is set
echo $OPENAI_API_KEY

# Test key
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"
```

## Next Steps

- [API Reference](../api/overview.md) - Full API documentation
- [Configuration](../getting-started/configuration.md) - All settings
- [RAG API Documentation](../RAG_API.md) - Detailed API reference
