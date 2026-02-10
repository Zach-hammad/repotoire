# Git + Graphiti Integration

Integrate git commit history with Graphiti temporal knowledge graphs to enable natural language queries about code evolution.

## Table of Contents

- [Overview](#overview)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [CLI Usage](#cli-usage)
- [API Usage](#api-usage)
- [Python SDK Usage](#python-sdk-usage)
- [Use Cases](#use-cases)
- [Architecture](#architecture)
- [Performance](#performance)
- [Troubleshooting](#troubleshooting)

## Overview

The Git-Graphiti integration automatically converts git commit history into a temporal knowledge graph using [Graphiti](https://github.com/getzep/graphiti). Each commit becomes an episode with:

- **Commit metadata**: Author, timestamp, SHA, message
- **Changed files**: Files modified in the commit
- **Code changes**: Functions/classes added or modified
- **LLM understanding**: Semantic extraction of entities and relationships

This enables powerful natural language queries like:
- "When did we add OAuth authentication?"
- "What changes led to the performance regression?"
- "Show me all refactorings of the UserManager class"
- "Which developer changed this function most frequently?"

### Benefits

✅ **Zero manual effort** - Automatic ingestion from git
✅ **Natural language queries** - Ask questions in plain English
✅ **Entity extraction** - LLM identifies affected code entities
✅ **Temporal context** - Track how code evolved over time
✅ **Root cause analysis** - Find when bugs were introduced
✅ **Developer insights** - See who changed what and when

## Installation

### Requirements

- Python 3.10+
- Neo4j 5.26+ (for Graphiti storage)
- OpenAI API key (for LLM processing)
- Git repository

### Install Dependencies

```bash
# Install Repotoire with Graphiti support
pip install repotoire[graphiti]

# Or with uv
uv pip install repotoire[graphiti]
```

### Configure Environment

```bash
# Required: OpenAI API key for Graphiti LLM processing
export OPENAI_API_KEY="sk-..."

# Required: Neo4j credentials
export REPOTOIRE_NEO4J_URI="bolt://localhost:7687"
export REPOTOIRE_NEO4J_PASSWORD="your-password"
```

### Start Neo4j

```bash
docker run \
    --name repotoire-neo4j \
    -p 7687:7687 \
    -e NEO4J_AUTH=neo4j/your-password \
    -e NEO4J_PLUGINS='["graph-data-science", "apoc"]' \
    neo4j:latest
```

## Quick Start

### 1. Ingest Git History

```bash
# Ingest last 100 commits from main branch
repotoire historical ingest-git /path/to/repo --max-commits 100

# Ingest commits since a specific date
repotoire historical ingest-git /path/to/repo --since 2024-01-01

# Ingest from specific branch
repotoire historical ingest-git /path/to/repo --branch develop --max-commits 500
```

### 2. Query Code Evolution

```bash
# Ask natural language questions
repotoire historical query "When did we add authentication?" /path/to/repo

repotoire historical query "What changes affected the database layer?" /path/to/repo

repotoire historical query "Show me all performance optimizations" /path/to/repo
```

### 3. Get Entity Timeline

```bash
# See all changes to a specific function
repotoire historical timeline authenticate_user /path/to/repo --entity-type function

# Track class evolution
repotoire historical timeline UserManager /path/to/repo --entity-type class
```

## CLI Usage

### `repotoire historical ingest-git`

Ingest git commit history into Graphiti.

**Arguments:**
- `REPOSITORY` - Path to git repository (required)

**Options:**
- `--since, -s` - Only ingest commits after this date (YYYY-MM-DD)
- `--until, -u` - Only ingest commits before this date (YYYY-MM-DD)
- `--branch, -b` - Git branch to analyze (default: main)
- `--max-commits, -m` - Maximum commits to process (default: 1000)
- `--batch-size` - Commits to process in parallel (default: 10)
- `--neo4j-uri` - Neo4j connection URI (default: from env)
- `--neo4j-password` - Neo4j password (default: from env)

**Examples:**

```bash
# Ingest last 500 commits
repotoire historical ingest-git /path/to/repo --max-commits 500

# Ingest commits from 2024 only
repotoire historical ingest-git /path/to/repo \
  --since 2024-01-01 \
  --until 2024-12-31

# Ingest from feature branch
repotoire historical ingest-git /path/to/repo \
  --branch feature/new-auth \
  --max-commits 100

# Custom Neo4j connection
repotoire historical ingest-git /path/to/repo \
  --neo4j-uri bolt://neo4j.example.com:7687 \
  --neo4j-password my-password
```

### `repotoire historical query`

Query git history using natural language.

**Arguments:**
- `QUERY` - Natural language question (required)
- `REPOSITORY` - Path to git repository (required)

**Options:**
- `--since, -s` - Filter results after this date (YYYY-MM-DD)
- `--until, -u` - Filter results before this date (YYYY-MM-DD)

**Examples:**

```bash
# Bug investigation
repotoire historical query \
  "When was the authentication bug introduced?" \
  /path/to/repo

# Feature history
repotoire historical query \
  "Show me the development history of the payment feature" \
  /path/to/repo

# Security audit
repotoire historical query \
  "What security changes were made in the last 6 months?" \
  /path/to/repo --since 2024-06-01

# Performance analysis
repotoire historical query \
  "What commits affected database query performance?" \
  /path/to/repo

# Refactoring history
repotoire historical query \
  "Show all refactorings that reduced code complexity" \
  /path/to/repo
```

### `repotoire historical timeline`

Get timeline of changes for a specific code entity.

**Arguments:**
- `ENTITY_NAME` - Name of the function/class/module (required)
- `REPOSITORY` - Path to git repository (required)

**Options:**
- `--entity-type, -t` - Type of entity: function, class, module (default: function)

**Examples:**

```bash
# Function timeline
repotoire historical timeline authenticate_user /path/to/repo

# Class evolution
repotoire historical timeline UserManager /path/to/repo --entity-type class

# Module changes
repotoire historical timeline auth.oauth /path/to/repo --entity-type module
```

## API Usage

### Start the API Server

```bash
# Start FastAPI server
python -m repotoire.api.app

# Or with uvicorn directly
uvicorn repotoire.api.app:app --host 0.0.0.0 --port 8000
```

Access API docs at: http://localhost:8000/docs

### Endpoints

#### POST /api/v1/historical/ingest-git

Ingest git commit history.

**Request Body:**
```json
{
  "repository_path": "/path/to/repo",
  "since": "2024-01-01T00:00:00Z",
  "until": "2024-12-31T23:59:59Z",
  "branch": "main",
  "max_commits": 1000,
  "batch_size": 10
}
```

**Response:**
```json
{
  "status": "success",
  "commits_processed": 523,
  "commits_skipped": 0,
  "errors": 0,
  "oldest_commit": "2024-01-15T10:30:00Z",
  "newest_commit": "2024-11-23T14:25:00Z",
  "message": "Successfully ingested 523 commits in 45230ms"
}
```

**cURL Example:**
```bash
curl -X POST "http://localhost:8000/api/v1/historical/ingest-git" \
  -H "Content-Type: application/json" \
  -d '{
    "repository_path": "/path/to/repo",
    "max_commits": 100
  }'
```

#### POST /api/v1/historical/query

Query git history using natural language.

**Request Body:**
```json
{
  "query": "When did we add OAuth authentication?",
  "repository_path": "/path/to/repo",
  "start_time": "2024-01-01T00:00:00Z",
  "end_time": "2024-12-31T23:59:59Z"
}
```

**Response:**
```json
{
  "query": "When did we add OAuth authentication?",
  "results": "OAuth authentication was added in commit abc123... on November 15, 2024...",
  "execution_time_ms": 1523
}
```

**cURL Example:**
```bash
curl -X POST "http://localhost:8000/api/v1/historical/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What changes affected the database layer?",
    "repository_path": "/path/to/repo"
  }'
```

#### POST /api/v1/historical/timeline

Get timeline for a specific code entity.

**Request Body:**
```json
{
  "entity_name": "authenticate_user",
  "entity_type": "function",
  "repository_path": "/path/to/repo"
}
```

**Response:**
```json
{
  "entity_name": "authenticate_user",
  "entity_type": "function",
  "timeline": "Timeline showing all commits that modified authenticate_user...",
  "execution_time_ms": 892
}
```

#### GET /api/v1/historical/health

Health check for historical analysis endpoints.

**Response:**
```json
{
  "status": "healthy",
  "graphiti_available": true,
  "openai_configured": true,
  "neo4j_configured": true,
  "message": "All dependencies available"
}
```

## Python SDK Usage

### Direct Integration

```python
from graphiti_core import Graphiti
from repotoire.historical import GitGraphitiIntegration
import asyncio

# Initialize Graphiti
graphiti = Graphiti(
    uri="bolt://localhost:7687",
    password="your-password",
    user="neo4j"
)

# Create integration
integration = GitGraphitiIntegration("/path/to/repo", graphiti)

# Ingest git history
async def ingest():
    stats = await integration.ingest_git_history(
        branch="main",
        max_commits=500
    )
    print(f"Processed {stats['commits_processed']} commits")

asyncio.run(ingest())

# Query history
async def query():
    results = await integration.query_history(
        "When did we add authentication?"
    )
    print(results)

asyncio.run(query())

# Get entity timeline
async def timeline():
    results = await integration.get_entity_timeline(
        "authenticate_user",
        entity_type="function"
    )
    print(results)

asyncio.run(timeline())
```

### Advanced Usage

```python
from datetime import datetime, timezone, timedelta

# Filter by date range
since = datetime.now(timezone.utc) - timedelta(days=90)
until = datetime.now(timezone.utc)

stats = await integration.ingest_git_history(
    since=since,
    until=until,
    branch="develop",
    max_commits=1000,
    batch_size=20  # Process 20 commits at a time
)

# Query with time filter
results = await integration.query_history(
    query="What security changes were made?",
    start_time=since,
    end_time=until
)
```

## Use Cases

### 1. Bug Investigation

**Question**: "When was this bug introduced?"

```bash
repotoire historical query \
  "Show me all changes to the user authentication module in the last month" \
  /path/to/repo --since 2024-10-01
```

### 2. Refactoring History

**Question**: "How did we improve code quality over time?"

```bash
repotoire historical query \
  "Show me all commits that reduced code complexity or improved modularity" \
  /path/to/repo
```

### 3. Performance Analysis

**Question**: "What caused the performance regression?"

```bash
repotoire historical query \
  "What changes affected database query performance between v1.5 and v2.0?" \
  /path/to/repo
```

### 4. Security Audit

**Question**: "What security-related changes were made?"

```bash
repotoire historical query \
  "Show all commits related to authentication, authorization, and security fixes" \
  /path/to/repo
```

### 5. Feature Development Tracking

**Question**: "How was this feature built?"

```bash
repotoire historical query \
  "Explain the development history of the payment processing feature" \
  /path/to/repo
```

### 6. Developer Onboarding

**Question**: "How has this code evolved?"

```bash
# Show complete history of a critical module
repotoire historical timeline PaymentProcessor /path/to/repo --entity-type class
```

### 7. Code Review Assistance

**Question**: "What changed in this release?"

```bash
repotoire historical query \
  "What are the major changes between v2.0 and v2.1?" \
  /path/to/repo
```

## Architecture

### Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│  Git Repository                                              │
│  • Commits (diffs, timestamps, messages, authors)            │
│  • Branches, tags, merges                                    │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│  GitGraphitiIntegration                                      │
│  • Convert commits → episodes                                │
│  • Extract changed files from diffs                          │
│  • Parse Python code changes (functions, classes)            │
│  • Format commit metadata for LLM                            │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│  Graphiti Temporal Knowledge Graph                           │
│                                                              │
│  Episodes (one per commit):                                  │
│  • Content: Commit message + diffs + code changes            │
│  • Timestamp: Commit datetime                                │
│  • Metadata: SHA, author, branch                             │
│                                                              │
│  Entities (extracted by LLM):                                │
│  • Functions, classes, modules                               │
│  • Relationships between entities                            │
│                                                              │
│  Storage: Neo4j with vector embeddings                       │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│  Natural Language Queries                                    │
│  • Semantic search via embeddings                            │
│  • Graph traversal for relationships                         │
│  • LLM synthesis of answers                                  │
└─────────────────────────────────────────────────────────────┘
```

### Storage Model

Each git commit creates a Graphiti episode containing:

```
Episode {
  name: "Add OAuth authentication"
  content: """
    Commit: abc123def456
    Author: John Doe <john@example.com>
    Date: 2024-11-15T10:30:00Z

    Summary: Add OAuth authentication

    Description:
    Implemented OAuth2 login flow with support for Google and GitHub

    Files Changed (3):
      - auth/oauth.py
      - auth/login.py
      - config/settings.py

    Code Changes:
      - Modified function: authenticate_user in auth/login.py
      - Modified class: OAuthProvider in auth/oauth.py

    Statistics:
      +145 insertions
      -23 deletions
      3 files changed
  """
  reference_time: 2024-11-15T10:30:00Z
  source_description: "Git commit abc123de"
}
```

### LLM Processing

Graphiti uses LLMs to:
1. **Extract entities** from episode content (functions, classes, etc.)
2. **Identify relationships** between entities
3. **Create embeddings** for semantic search
4. **Synthesize answers** to natural language queries

## Performance

### Ingestion Performance

| Repo Size | Commits | Time | Cost (OpenAI) |
|-----------|---------|------|---------------|
| Small (<1k commits) | 100 | ~2 min | ~$1-2 |
| Medium (1k-10k) | 1000 | ~20 min | ~$10-20 |
| Large (>10k) | 5000 | ~2 hours | ~$50-100 |

**Cost**: ~$0.01-0.02 per commit (one-time)

### Query Performance

- **Typical query**: <2 seconds
- **Complex query**: 2-5 seconds
- **Large result set**: 5-10 seconds

**Cost**: ~$0.01-0.05 per query (uses cached embeddings)

### Optimization Tips

1. **Batch size**: Increase `--batch-size` for faster ingestion (uses more memory)
2. **Limit commits**: Use `--max-commits` to process recent history only
3. **Date filtering**: Use `--since` to avoid processing old commits
4. **Branch selection**: Focus on main/master branch first

## Troubleshooting

### Issue: "Graphiti not installed"

**Solution**:
```bash
pip install repotoire[graphiti]
# or
uv pip install graphiti-core
```

### Issue: "OPENAI_API_KEY not set"

**Solution**:
```bash
export OPENAI_API_KEY="sk-..."
```

Get your API key from: https://platform.openai.com/api-keys

### Issue: "Neo4j password not provided"

**Solution**:
```bash
export REPOTOIRE_NEO4J_PASSWORD="your-password"
```

### Issue: "No commits found"

**Possible causes**:
1. Wrong branch name (check with `git branch`)
2. Date filter too restrictive
3. Not a git repository

**Solution**:
```bash
# Check branch
cd /path/to/repo && git branch

# Try without date filter
repotoire historical ingest-git /path/to/repo --max-commits 10

# Verify it's a git repo
cd /path/to/repo && git log --oneline -10
```

### Issue: Slow ingestion

**Solutions**:
1. Increase batch size: `--batch-size 20`
2. Reduce max commits: `--max-commits 100`
3. Use date filter: `--since 2024-01-01`

### Issue: High OpenAI costs

**Solutions**:
1. Process fewer commits
2. Use date filtering to focus on recent history
3. Consider cheaper LLM models (future feature)

### Issue: Query returns no results

**Possible causes**:
1. Commits not yet ingested
2. Query too specific
3. Date filter excludes relevant commits

**Solution**:
```bash
# Verify commits were ingested
# Check Neo4j for episodes

# Try broader query
repotoire historical query "Show me all authentication changes" /path/to/repo

# Remove date filter
repotoire historical query "your query" /path/to/repo
# (don't use --since or --until)
```

## Best Practices

### 1. Start Small

Begin with recent commits:
```bash
repotoire historical ingest-git /path/to/repo --max-commits 100
```

### 2. Focus on Important Branches

Ingest main/master first, then feature branches:
```bash
# Main branch
repotoire historical ingest-git /path/to/repo --branch main

# Key feature branch
repotoire historical ingest-git /path/to/repo --branch feature/important
```

### 3. Use Date Filtering

For large repositories, process recent history:
```bash
repotoire historical ingest-git /path/to/repo --since 2024-01-01
```

### 4. Incremental Updates

Re-run periodically to add new commits:
```bash
# Weekly cron job
repotoire historical ingest-git /path/to/repo --since $(date -d '7 days ago' +%Y-%m-%d)
```

### 5. Specific Queries

Be specific in your questions:
- ❌ "Show me changes"
- ✅ "Show me all authentication changes in the last 3 months"

### 6. Monitor Costs

Track OpenAI API usage:
- Check https://platform.openai.com/usage
- Set billing limits
- Use `--max-commits` to control costs

## FAQ

**Q: Can I use a different LLM instead of OpenAI?**
A: Currently Graphiti defaults to OpenAI. Support for other LLMs (Anthropic, local models) is on the Graphiti roadmap.

**Q: How much does this cost?**
A: ~$0.01-0.02 per commit for one-time ingestion, ~$0.01-0.05 per query. A 1000-commit repo costs ~$10-20 to ingest.

**Q: Can I delete old data?**
A: Yes, Graphiti stores episodes in Neo4j. You can delete episodes using Cypher queries.

**Q: Does this work with private repositories?**
A: Yes, as long as you have local access to the git repository.

**Q: What languages are supported?**
A: Currently Python code changes are extracted. Support for TypeScript, JavaScript, Go, and Java is planned.

**Q: Can I query across multiple repositories?**
A: Yes, ingest multiple repos and query each separately. Cross-repo queries are a future enhancement.

**Q: How does this compare to GitHub Copilot?**
A: This focuses on historical code evolution and git history, while Copilot focuses on code completion.

## Related Documentation

- [TimescaleDB Metrics Tracking](TIMESCALEDB_METRICS.md) - For aggregate metrics over time
- [RAG API Documentation](RAG_API.md) - For semantic code search
- [Graphiti Documentation](https://github.com/getzep/graphiti) - Temporal knowledge graph library

## Support

- GitHub Issues: https://github.com/yourusername/repotoire/issues
- Documentation: https://repotoire.readthedocs.io
- Graphiti: https://github.com/getzep/graphiti
