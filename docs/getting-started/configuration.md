# Configuration

This guide covers the essential configuration options to get Repotoire running. For complete configuration reference, see [CONFIG.md](../../CONFIG.md).

## Configuration Priority

Repotoire uses a priority chain (highest to lowest):

1. **Command-line arguments** (`--neo4j-uri`, `--log-level`, etc.)
2. **Environment variables** (`REPOTOIRE_NEO4J_URI`, etc.)
3. **Config file** (`.repotoirerc`, `repotoire.toml`)
4. **Built-in defaults**

## Quick Setup

The fastest way to configure Repotoire:

```bash
# Set required environment variables
export REPOTOIRE_NEO4J_URI=bolt://localhost:7687
export REPOTOIRE_NEO4J_PASSWORD=your-password

# Verify configuration
repotoire validate
```

## Config File

For persistent configuration, create a config file in your project root.

### Supported Formats

| Format | Filename | Requirements |
|--------|----------|--------------|
| YAML | `.repotoirerc` | `pip install pyyaml` |
| JSON | `.repotoirerc` | Built-in |
| TOML | `repotoire.toml` | Python 3.11+ or `pip install tomli` |

### File Locations (Search Order)

1. Current directory: `.repotoirerc` or `repotoire.toml`
2. Parent directories (searched up to root)
3. Home directory: `~/.repotoirerc`
4. XDG config: `~/.config/repotoire.toml`

## Essential Settings

### Neo4j Connection

```yaml
# .repotoirerc (YAML)
neo4j:
  uri: bolt://localhost:7687
  user: neo4j
  password: ${NEO4J_PASSWORD}  # Use env var interpolation for secrets
```

| Setting | Environment Variable | Default | Description |
|---------|---------------------|---------|-------------|
| `uri` | `REPOTOIRE_NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection URI |
| `user` | `REPOTOIRE_NEO4J_USER` | `neo4j` | Database username |
| `password` | `REPOTOIRE_NEO4J_PASSWORD` | - | Database password |

**Supported URI schemes:** `bolt://`, `neo4j://`, `bolt+s://`, `neo4j+s://`

### Ingestion Settings

```yaml
ingestion:
  patterns:
    - "**/*.py"
    - "**/*.js"
    - "**/*.ts"
  max_file_size_mb: 10
  batch_size: 100
```

| Setting | Environment Variable | Default | Description |
|---------|---------------------|---------|-------------|
| `patterns` | `REPOTOIRE_INGESTION_PATTERNS` | `["**/*.py"]` | Glob patterns for files to analyze |
| `max_file_size_mb` | `REPOTOIRE_INGESTION_MAX_FILE_SIZE_MB` | `10` | Max file size in MB |
| `batch_size` | `REPOTOIRE_INGESTION_BATCH_SIZE` | `100` | Entities per batch |

### Logging

```yaml
logging:
  level: INFO
  format: human  # or "json" for structured logging
```

| Setting | Environment Variable | Default | Description |
|---------|---------------------|---------|-------------|
| `level` | `REPOTOIRE_LOG_LEVEL` | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR) |
| `format` | `REPOTOIRE_LOG_FORMAT` | `human` | Output format (human, json) |

## Example Configurations

### Local Development

`.repotoirerc`:

```yaml
neo4j:
  uri: bolt://localhost:7687
  password: dev-password

ingestion:
  patterns:
    - "**/*.py"
  max_file_size_mb: 10

logging:
  level: DEBUG
  format: human
```

### CI/CD Pipeline

Use environment variables for sensitive data:

```bash
# In your CI environment
export REPOTOIRE_NEO4J_URI=bolt://neo4j-service:7687
export REPOTOIRE_NEO4J_PASSWORD=$NEO4J_SECRET
export REPOTOIRE_LOG_LEVEL=WARNING
export REPOTOIRE_LOG_FORMAT=json
```

### Multi-Language Project

```yaml
ingestion:
  patterns:
    - "src/**/*.py"
    - "src/**/*.js"
    - "src/**/*.ts"
    - "!**/*.test.js"  # Exclude test files
    - "!**/node_modules/**"
```

## Environment Variables Quick Reference

| Variable | Description |
|----------|-------------|
| `REPOTOIRE_NEO4J_URI` | Neo4j connection URI |
| `REPOTOIRE_NEO4J_USER` | Neo4j username |
| `REPOTOIRE_NEO4J_PASSWORD` | Neo4j password |
| `REPOTOIRE_LOG_LEVEL` | Logging level |
| `REPOTOIRE_INGESTION_PATTERNS` | Comma-separated file patterns |
| `OPENAI_API_KEY` | For RAG and AI features |

## Validating Configuration

```bash
# Check all settings
repotoire validate

# Test Neo4j connection
repotoire validate --check-neo4j

# Show resolved configuration
repotoire config show
```

## Security Best Practices

1. **Never commit passwords** - Use environment variables or `.env` files
2. **Use encrypted connections** - Prefer `bolt+s://` for production
3. **Restrict config permissions** - `chmod 600 .repotoirerc`

## Next Steps

- [Quick Start](quickstart.md) - Run your first analysis
- [Full Configuration Reference](../../CONFIG.md) - All available options
- [CLI Reference](../cli/overview.md) - Command-line options
