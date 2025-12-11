# Quick Start

Get Repotoire running and analyze your first codebase in under 5 minutes.

## Prerequisites

- Python 3.10+
- Docker (for Neo4j) or an existing Neo4j instance
- Your codebase (Python, JavaScript, or TypeScript)

## Step 1: Install Repotoire

```bash
# Using pip
pip install repotoire

# Or using uv (faster)
uv pip install repotoire

# Verify installation
repotoire --version
```

## Step 2: Start Neo4j

The easiest way is with Docker:

```bash
docker run -d \
  --name repotoire-neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/your-password \
  -e NEO4J_PLUGINS='["apoc"]' \
  neo4j:latest
```

Wait for Neo4j to start (~30 seconds), then verify:

```bash
# Open Neo4j Browser
open http://localhost:7474
```

## Step 3: Configure Repotoire

Set environment variables:

```bash
export REPOTOIRE_NEO4J_URI=bolt://localhost:7687
export REPOTOIRE_NEO4J_PASSWORD=your-password
```

Or create a config file:

```bash
repotoire init
```

Verify connectivity:

```bash
repotoire validate
```

Expected output:
```
✓ Neo4j connection validated
✓ Configuration valid
```

## Step 4: Ingest Your Codebase

```bash
repotoire ingest ./my-project
```

This will:
- Parse all Python/JS/TS files
- Extract classes, functions, and relationships
- Build a knowledge graph in Neo4j

Example output:
```
🎼 Repotoire Ingestion

Repository: ./my-project
Database: neo4j
Patterns: **/*.py, **/*.js, **/*.ts

Processing: 156 files [00:45, 3.5 files/s]

┌─────────────────────┬───────┐
│ Metric              │ Count │
├─────────────────────┼───────┤
│ Files               │   156 │
│ Modules             │    42 │
│ Classes             │    89 │
│ Functions           │   412 │
│ Relationships       │  1847 │
└─────────────────────┴───────┘
```

## Step 5: Run Analysis

```bash
repotoire analyze ./my-project
```

This will:
- Run 8+ code quality detectors
- Calculate health scores
- Generate findings report

Example output:
```
🏥 Code Health Analysis

Overall Health Score: 78/100 (B)

┌──────────────────┬───────┬───────┐
│ Category         │ Score │ Grade │
├──────────────────┼───────┼───────┤
│ Structure        │    82 │   B   │
│ Quality          │    75 │   C   │
│ Architecture     │    77 │   C   │
└──────────────────┴───────┴───────┘

Findings Summary:
  🔴 Critical:  2
  🟠 High:      8
  🟡 Medium:   15
  🟢 Low:      12
  ℹ️  Info:     5

Top Issues:
  1. [HIGH] Hardcoded password in config.py:42
  2. [HIGH] SQL injection risk in queries.py:89
  3. [MEDIUM] Cyclomatic complexity 15 in processor.py
```

## Step 6: Ask Questions (Optional)

If you generated embeddings, you can ask natural language questions:

```bash
# Re-ingest with embeddings
repotoire ingest ./my-project --generate-embeddings

# Ask questions
repotoire ask "Where is user authentication handled?"
repotoire ask "What does the OrderService do?"
repotoire ask "Show me database connection code"
```

## Next Steps

- [Generate HTML reports](../cli/overview.md#output-formats)
- [Set up CI/CD integration](../guides/cicd.md)
- [Configure custom rules](../guides/custom-rules.md)
- [Connect GitHub repositories](../guides/github-integration.md)

## Troubleshooting

### Neo4j Connection Failed

```
❌ Neo4j connection failed: Unable to retrieve routing information
```

**Solutions:**
1. Check Neo4j is running: `docker ps | grep neo4j`
2. Verify URI: Should be `bolt://localhost:7687`
3. Check password matches

### No Files Found

```
⚠️ No files matched patterns
```

**Solutions:**
1. Check you're in the right directory
2. Verify patterns: `repotoire show-config | grep patterns`
3. Try explicit pattern: `repotoire ingest . -p "**/*.py"`

### Memory Issues

For large codebases:

```bash
# Reduce batch size
repotoire ingest ./large-project --batch-size 50

# Or increase Neo4j memory
docker run -e NEO4J_HEAP_SIZE=4G ...
```
