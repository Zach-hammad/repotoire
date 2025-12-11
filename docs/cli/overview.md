# CLI Overview

The Repotoire CLI provides a powerful command-line interface for analyzing codebases, managing knowledge graphs, and integrating with CI/CD pipelines.

## Installation

```bash
# Using pip
pip install repotoire

# Using uv (recommended)
uv pip install repotoire

# With optional dependencies
pip install repotoire[dev,gds,all-languages]
```

## Quick Start

```bash
# 1. Initialize configuration
repotoire init

# 2. Start Neo4j (if not running)
docker run -d --name neo4j -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password neo4j:latest

# 3. Ingest your codebase
repotoire ingest ./my-project

# 4. Run analysis
repotoire analyze ./my-project

# 5. Ask questions with natural language
repotoire ask "Where is authentication handled?"
```

## Command Categories

### Core Commands

| Command | Description |
|---------|-------------|
| `ingest` | Parse codebase and build knowledge graph |
| `analyze` | Run health analysis and generate reports |
| `ask` | Ask questions using natural language (RAG) |
| `validate` | Test configuration and connectivity |

### Setup Commands

| Command | Description |
|---------|-------------|
| `init` | Create configuration file interactively |
| `show-config` | Display effective configuration |
| `backends` | Show available embedding backends |

### Analysis Commands

| Command | Description |
|---------|-------------|
| `hotspots` | Find code flagged by multiple detectors |
| `auto-fix` | AI-powered automatic code fixing |
| `security` | Security scanning and compliance |
| `style` | Analyze code style conventions |

### Graph Commands

| Command | Description |
|---------|-------------|
| `graph` | Database management for multi-tenancy |
| `schema` | Manage and inspect graph schema |
| `embeddings` | Manage vector embeddings |

### History Commands

| Command | Description |
|---------|-------------|
| `history` | Ingest git history for temporal analysis |
| `historical` | Query code evolution |
| `metrics` | Query historical metrics from TimescaleDB |
| `compare` | Compare metrics between commits |

### Advanced Commands

| Command | Description |
|---------|-------------|
| `ml` | ML commands for training data extraction |
| `monorepo` | Monorepo analysis and optimization |
| `rule` | Manage custom code quality rules |
| `templates` | Manage fix templates |
| `migrate` | Database schema migrations |

## Global Options

These options apply to all commands:

```bash
repotoire --help                    # Show help
repotoire --version                 # Show version
repotoire -c config.toml <cmd>      # Use specific config file
repotoire --log-level DEBUG <cmd>   # Set log level
repotoire --log-format json <cmd>   # JSON log output
repotoire --log-file app.log <cmd>  # Write logs to file
```

## Configuration

Repotoire uses a hierarchical configuration system:

1. **Command-line options** (highest priority)
2. **Config file** (`.reporc`, `falkor.toml`)
3. **Environment variables**
4. **Built-in defaults** (lowest priority)

### Configuration File

Create a config file with `repotoire init` or manually:

```toml
# .reporc or falkor.toml

[neo4j]
uri = "bolt://localhost:7687"
user = "neo4j"
password = "${NEO4J_PASSWORD}"  # Environment variable interpolation

[ingestion]
patterns = ["**/*.py", "**/*.js", "**/*.ts"]
batch_size = 100
max_file_size_mb = 10
follow_symlinks = false

[embeddings]
backend = "auto"  # auto, openai, local, deepinfra, voyage

[secrets]
policy = "redact"  # redact, block, warn, fail

[logging]
level = "INFO"
format = "human"  # human, json
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `REPOTOIRE_NEO4J_URI` | Neo4j connection URI |
| `REPOTOIRE_NEO4J_PASSWORD` | Neo4j password |
| `REPOTOIRE_NEO4J_USER` | Neo4j username |
| `REPOTOIRE_DB_TYPE` | Database type (neo4j/falkordb) |
| `REPOTOIRE_TIMESCALE_URI` | TimescaleDB connection string |
| `REPOTOIRE_OFFLINE` | Run in offline mode |
| `OPENAI_API_KEY` | OpenAI API key |
| `VOYAGE_API_KEY` | Voyage AI API key |
| `ANTHROPIC_API_KEY` | Anthropic API key |

## Common Workflows

### First-Time Setup

```bash
# 1. Create config
repotoire init

# 2. Verify connectivity
repotoire validate

# 3. Ingest codebase
repotoire ingest ./my-project

# 4. Run first analysis
repotoire analyze ./my-project
```

### Daily Development

```bash
# Quick health check
repotoire analyze ./my-project -q

# Find problem areas
repotoire hotspots

# Get AI-powered fixes
repotoire auto-fix

# Ask about code
repotoire ask "What does the UserService do?"
```

### CI/CD Integration

```bash
# JSON output for parsing
repotoire analyze ./my-project -f json -o results.json

# Exit with error on critical findings
repotoire analyze ./my-project --fail-on-critical

# Security-focused scan
repotoire security audit --format sarif -o security.sarif
```

### Historical Analysis

```bash
# Ingest git history
repotoire history --max-commits 500

# Compare two commits
repotoire compare HEAD~10 HEAD

# Query metrics over time
repotoire metrics trend --metric health_score --days 30
```

## Output Formats

### Terminal (Default)

Rich, colorized output with tables and progress bars:

```bash
repotoire analyze ./my-project
```

### JSON

Machine-readable output for CI/CD:

```bash
repotoire analyze ./my-project -f json
```

### HTML

Visual report with code snippets:

```bash
repotoire analyze ./my-project -f html -o report.html
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Critical findings detected |
| 3 | Configuration error |

## Next Steps

- [Full command reference](../reference/cli-reference.md)
- [Environment variables](environment.md)
- [CI/CD integration guide](../guides/cicd.md)
