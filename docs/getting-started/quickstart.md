# Quick Start

Analyze your codebase in under 2 minutes.

## Step 1: Install

```bash
pip install repotoire
```

## Step 2: Get Your API Key

1. Go to [repotoire.com/settings/api-keys](https://repotoire.com/settings/api-keys)
2. Create a new API key
3. Set it in your environment:

```bash
export REPOTOIRE_API_KEY=ak_your_key_here
```

## Step 3: Analyze

```bash
repotoire analyze ./my-project
```

That's it! View your results at [repotoire.com/dashboard](https://repotoire.com/dashboard).

## Step 4: Ingest Your Codebase (Optional)

For deeper analysis, ingest your codebase first:

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
