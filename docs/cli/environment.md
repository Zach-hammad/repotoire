# Environment Variables

Reference for all environment variables used by Repotoire CLI.

For complete configuration options, see [CONFIG.md](../../CONFIG.md).

## Quick Reference

| Variable | Description | Default |
|----------|-------------|---------|
| `REPOTOIRE_NEO4J_URI` | Neo4j connection URI | `bolt://localhost:7687` |
| `REPOTOIRE_NEO4J_USER` | Neo4j username | `neo4j` |
| `REPOTOIRE_NEO4J_PASSWORD` | Neo4j password | - |
| `REPOTOIRE_LOG_LEVEL` | Logging level | `INFO` |
| `OPENAI_API_KEY` | OpenAI API key for RAG | - |

## Neo4j Connection

### REPOTOIRE_NEO4J_URI

Neo4j connection URI. Supports various protocols:

```bash
# Local development
export REPOTOIRE_NEO4J_URI=bolt://localhost:7687

# Remote with encryption
export REPOTOIRE_NEO4J_URI=bolt+s://neo4j.example.com:7687

# Neo4j Aura
export REPOTOIRE_NEO4J_URI=neo4j+s://abc123.databases.neo4j.io
```

**Supported schemes:** `bolt://`, `neo4j://`, `bolt+s://`, `neo4j+s://`, `bolt+ssc://`, `neo4j+ssc://`

### REPOTOIRE_NEO4J_USER

Neo4j username. Default: `neo4j`

```bash
export REPOTOIRE_NEO4J_USER=neo4j
```

### REPOTOIRE_NEO4J_PASSWORD

Neo4j password. Required for authentication.

```bash
export REPOTOIRE_NEO4J_PASSWORD=your-secure-password
```

**Security:** Never commit passwords. Use environment variables or secret managers.

## Ingestion Settings

### REPOTOIRE_INGESTION_PATTERNS

Comma-separated glob patterns for files to analyze.

```bash
# Python and JavaScript files
export REPOTOIRE_INGESTION_PATTERNS="**/*.py,**/*.js,**/*.ts"
```

### REPOTOIRE_INGESTION_MAX_FILE_SIZE_MB

Maximum file size to process (in MB).

```bash
export REPOTOIRE_INGESTION_MAX_FILE_SIZE_MB=10
```

### REPOTOIRE_INGESTION_BATCH_SIZE

Number of entities to batch before loading to graph.

```bash
export REPOTOIRE_INGESTION_BATCH_SIZE=100
```

## Logging

### REPOTOIRE_LOG_LEVEL

Logging verbosity level.

```bash
export REPOTOIRE_LOG_LEVEL=DEBUG   # Verbose output
export REPOTOIRE_LOG_LEVEL=INFO    # Standard output (default)
export REPOTOIRE_LOG_LEVEL=WARNING # Warnings and errors only
export REPOTOIRE_LOG_LEVEL=ERROR   # Errors only
```

**Aliases:** `LOG_LEVEL` also works.

### REPOTOIRE_LOG_FORMAT

Log output format.

```bash
export REPOTOIRE_LOG_FORMAT=human  # Human-readable (default)
export REPOTOIRE_LOG_FORMAT=json   # Structured JSON for log aggregators
```

### REPOTOIRE_LOG_FILE

Path to log file. If unset, logs to stderr.

```bash
export REPOTOIRE_LOG_FILE=/var/log/repotoire.log
```

## AI/Embedding Features

### OPENAI_API_KEY

OpenAI API key for embeddings and AI-powered features.

```bash
export OPENAI_API_KEY=sk-...
```

Required for:
- `--generate-embeddings` flag
- `repotoire ask` command
- `/api/v1/code/ask` endpoint

### DEEPINFRA_API_KEY

DeepInfra API key for alternative embedding backend.

```bash
export DEEPINFRA_API_KEY=...
```

Use with `--embedding-backend deepinfra`.

## Analysis Settings

### REPOTOIRE_ANALYSIS_MIN_MODULARITY

Minimum acceptable modularity score (0.0-1.0).

```bash
export REPOTOIRE_ANALYSIS_MIN_MODULARITY=0.3
```

### REPOTOIRE_ANALYSIS_MAX_COUPLING

Maximum acceptable coupling score.

```bash
export REPOTOIRE_ANALYSIS_MAX_COUPLING=5.0
```

## Detector Thresholds

### God Class Detection

```bash
export REPOTOIRE_DETECTOR_GOD_CLASS_HIGH_METHOD_COUNT=20
export REPOTOIRE_DETECTOR_GOD_CLASS_MEDIUM_METHOD_COUNT=15
export REPOTOIRE_DETECTOR_GOD_CLASS_HIGH_COMPLEXITY=100
export REPOTOIRE_DETECTOR_GOD_CLASS_MEDIUM_COMPLEXITY=50
export REPOTOIRE_DETECTOR_GOD_CLASS_HIGH_LOC=500
export REPOTOIRE_DETECTOR_GOD_CLASS_MEDIUM_LOC=300
```

## Connection Retry

### REPOTOIRE_NEO4J_MAX_RETRIES

Maximum connection retry attempts (0-10).

```bash
export REPOTOIRE_NEO4J_MAX_RETRIES=3
```

### REPOTOIRE_NEO4J_RETRY_BACKOFF_FACTOR

Exponential backoff multiplier for retries.

```bash
export REPOTOIRE_NEO4J_RETRY_BACKOFF_FACTOR=2.0
```

### REPOTOIRE_NEO4J_RETRY_BASE_DELAY

Base delay between retries in seconds.

```bash
export REPOTOIRE_NEO4J_RETRY_BASE_DELAY=1.0
```

## SaaS/API Features

### E2B_API_KEY

E2B API key for sandbox execution.

```bash
export E2B_API_KEY=e2b_...
```

### GITHUB_APP_ID

GitHub App ID for GitHub integration.

```bash
export GITHUB_APP_ID=123456
```

### GITHUB_APP_PRIVATE_KEY

GitHub App private key (PEM format).

```bash
export GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
...
-----END RSA PRIVATE KEY-----"
```

## CI/CD Recommendations

### Minimal CI Setup

```bash
export REPOTOIRE_NEO4J_URI=bolt://neo4j:7687
export REPOTOIRE_NEO4J_PASSWORD=$NEO4J_SECRET
export REPOTOIRE_LOG_LEVEL=WARNING
export REPOTOIRE_LOG_FORMAT=json
```

### With AI Features

```bash
export REPOTOIRE_NEO4J_URI=bolt://neo4j:7687
export REPOTOIRE_NEO4J_PASSWORD=$NEO4J_SECRET
export OPENAI_API_KEY=$OPENAI_SECRET
```

## Validating Configuration

```bash
# Show resolved environment
repotoire config show

# Validate all settings
repotoire validate

# Test Neo4j connection
repotoire validate --check-neo4j
```

## See Also

- [Configuration Guide](../getting-started/configuration.md) - Config files
- [Full Configuration Reference](../../CONFIG.md) - All options
- [CI/CD Integration](../guides/cicd.md) - Pipeline setup
