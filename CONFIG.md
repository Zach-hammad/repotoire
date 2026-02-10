# Repotoire Configuration Guide

This guide covers all configuration options for Repotoire, including examples for common scenarios.

## Configuration Priority Chain

Repotoire uses a priority chain to resolve configuration values (highest to lowest):

1. **Command-line arguments** (`--neo4j-uri`, `--log-level`, etc.)
2. **Environment variables** (`REPOTOIRE_NEO4J_URI`, `REPOTOIRE_NEO4J_USER`, etc.)
3. **Config file** (`.repotoirerc`, `repotoire.toml`)
4. **Built-in defaults**

## Config File Locations

Repotoire searches for config files hierarchically:

1. **Current directory**: `.repotoirerc` or `repotoire.toml`
2. **Parent directories**: Searches up to root
3. **Home directory**: `~/.repotoirerc`
4. **XDG config directory**: `~/.config/repotoire.toml`

## File Formats

Repotoire supports multiple configuration formats:

- **YAML**: `.repotoirerc`, `.yaml`, `.yml` (requires PyYAML)
- **JSON**: `.repotoirerc`, `.json`
- **TOML**: `repotoire.toml` (requires tomli or Python 3.11+)

## Configuration Sections

### Neo4j Configuration

Connection and retry settings for Neo4j database.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `uri` | string | `bolt://localhost:7687` | Neo4j connection URI (bolt://, neo4j://, bolt+s://, etc.) |
| `user` | string | `neo4j` | Neo4j username |
| `password` | string | `null` | Neo4j password (use environment variables for security) |
| `max_retries` | int | `3` | Maximum number of connection retry attempts |
| `retry_backoff_factor` | float | `2.0` | Exponential backoff multiplier (delay = base_delay * factor^attempt) |
| `retry_base_delay` | float | `1.0` | Base delay in seconds between retries |

**Validation Rules:**
- `uri`: Must use valid Neo4j scheme (bolt, neo4j, bolt+s, neo4j+s, bolt+ssc, neo4j+ssc)
- `max_retries`: 0-10 (0 disables retries)
- `retry_backoff_factor`: >= 1.0, recommended 1.5-3.0
- `retry_base_delay`: > 0, recommended 0.5-2.0 seconds

**Environment Variables:**
- `REPOTOIRE_NEO4J_URI`
- `REPOTOIRE_NEO4J_USER`
- `REPOTOIRE_NEO4J_PASSWORD`
- `REPOTOIRE_NEO4J_MAX_RETRIES`
- `REPOTOIRE_NEO4J_RETRY_BACKOFF_FACTOR`
- `REPOTOIRE_NEO4J_RETRY_BASE_DELAY`

### Ingestion Configuration

Settings for code ingestion pipeline.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `patterns` | list[string] | `["**/*.py"]` | Glob patterns for files to analyze |
| `follow_symlinks` | bool | `false` | Whether to follow symbolic links (security consideration) |
| `max_file_size_mb` | float | `10.0` | Maximum file size in MB to process |
| `batch_size` | int | `100` | Number of entities to batch before loading to graph |

**Validation Rules:**
- `patterns`: Must be valid glob patterns
- `follow_symlinks`: Disabled by default for security
- `max_file_size_mb`: 0.1-1000 MB, recommended 10-50 MB
- `batch_size`: 10-10000, recommended 100-500

**Environment Variables:**
- `REPOTOIRE_INGESTION_PATTERNS` (comma-separated)
- `REPOTOIRE_INGESTION_FOLLOW_SYMLINKS` (true/false)
- `REPOTOIRE_INGESTION_MAX_FILE_SIZE_MB`
- `REPOTOIRE_INGESTION_BATCH_SIZE`

### Analysis Configuration

Settings for code analysis engine.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `min_modularity` | float | `0.3` | Minimum acceptable modularity score (0-1) |
| `max_coupling` | float | `5.0` | Maximum acceptable coupling score |

**Validation Rules:**
- `min_modularity`: 0.0-1.0, optimal range 0.3-0.7
- `max_coupling`: > 0, lower is better

**Environment Variables:**
- `REPOTOIRE_ANALYSIS_MIN_MODULARITY`
- `REPOTOIRE_ANALYSIS_MAX_COUPLING`

### Detector Configuration

Thresholds for code smell detectors.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `god_class_high_method_count` | int | `20` | Method count threshold for high severity god class |
| `god_class_medium_method_count` | int | `15` | Method count threshold for medium severity god class |
| `god_class_high_complexity` | int | `100` | Cyclomatic complexity threshold for high severity |
| `god_class_medium_complexity` | int | `50` | Cyclomatic complexity threshold for medium severity |
| `god_class_high_loc` | int | `500` | Lines of code threshold for high severity |
| `god_class_medium_loc` | int | `300` | Lines of code threshold for medium severity |
| `god_class_high_lcom` | float | `0.8` | LCOM (Lack of Cohesion) threshold for high severity (0-1) |
| `god_class_medium_lcom` | float | `0.6` | LCOM threshold for medium severity (0-1) |

**Validation Rules:**
- All count/LOC thresholds: > 0
- LCOM thresholds: 0.0-1.0 (higher values indicate less cohesion)
- High thresholds should be >= medium thresholds

**Environment Variables:**
- `REPOTOIRE_DETECTOR_GOD_CLASS_HIGH_METHOD_COUNT`
- `REPOTOIRE_DETECTOR_GOD_CLASS_MEDIUM_METHOD_COUNT`
- (etc. - all detector options support environment variables)

### Logging Configuration

Settings for logging output.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `level` | string | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `format` | string | `human` | Log format (human or json) |
| `file` | string | `null` | Path to log file (optional, logs to stderr if not set) |

**Validation Rules:**
- `level`: Must be valid Python log level
- `format`: "human" or "json"
- `file`: Must be writable path if specified

**Environment Variables:**
- `REPOTOIRE_LOG_LEVEL` or `LOG_LEVEL`
- `REPOTOIRE_LOG_FORMAT` or `LOG_FORMAT`
- `REPOTOIRE_LOG_FILE` or `LOG_FILE`

## Configuration Examples

### Example 1: Local Development (YAML)

`.repotoirerc`:
```yaml
neo4j:
  uri: bolt://localhost:7687
  user: neo4j
  password: ${NEO4J_PASSWORD}  # Use environment variable

ingestion:
  patterns:
    - "**/*.py"
  follow_symlinks: false
  max_file_size_mb: 10
  batch_size: 100

analysis:
  min_modularity: 0.3
  max_coupling: 5.0

logging:
  level: INFO
  format: human
```

### Example 2: Multi-Language Project (TOML)

`repotoire.toml`:
```toml
[neo4j]
uri = "bolt://localhost:7687"
user = "neo4j"
password = "${NEO4J_PASSWORD}"

[ingestion]
patterns = ["**/*.py", "**/*.js", "**/*.ts", "**/*.tsx"]
follow_symlinks = false
max_file_size_mb = 20  # Larger files for TypeScript projects
batch_size = 200       # Larger batches for performance

[analysis]
min_modularity = 0.4   # Stricter modularity requirement
max_coupling = 3.0     # Lower coupling threshold

[detectors]
god_class_high_method_count = 25
god_class_medium_method_count = 18

[logging]
level = "DEBUG"
format = "json"
file = "logs/repotoire.log"
```

### Example 3: Production/CI Environment (JSON)

`.repotoirerc`:
```json
{
  "neo4j": {
    "uri": "${NEO4J_URI}",
    "user": "${NEO4J_USER}",
    "password": "${NEO4J_PASSWORD}",
    "max_retries": 5,
    "retry_backoff_factor": 2.0,
    "retry_base_delay": 2.0
  },
  "ingestion": {
    "patterns": ["**/*.py"],
    "follow_symlinks": false,
    "max_file_size_mb": 50,
    "batch_size": 500
  },
  "logging": {
    "level": "WARNING",
    "format": "json",
    "file": "/var/log/repotoire/analysis.log"
  }
}
```

### Example 4: Large Codebase with Custom Detectors

`repotoire.toml`:
```toml
[neo4j]
uri = "bolt://production-neo4j:7687"
user = "repotoire_user"
password = "${NEO4J_PASSWORD}"
max_retries = 5
retry_backoff_factor = 1.5
retry_base_delay = 1.0

[ingestion]
patterns = ["**/*.py", "!**/tests/**", "!**/migrations/**"]
follow_symlinks = false
max_file_size_mb = 100    # Very large files
batch_size = 1000         # Large batches for big codebase

[analysis]
min_modularity = 0.5      # Stricter requirements
max_coupling = 2.0

[detectors]
# More lenient thresholds for legacy codebase
god_class_high_method_count = 30
god_class_medium_method_count = 20
god_class_high_complexity = 150
god_class_medium_complexity = 75
god_class_high_loc = 1000
god_class_medium_loc = 500

[logging]
level = "INFO"
format = "json"
file = "logs/repotoire-production.log"
```

### Example 5: Security-Focused Configuration

`.repotoirerc`:
```yaml
neo4j:
  uri: bolt+s://secure-neo4j:7687  # Secure connection
  user: ${NEO4J_USER}
  password: ${NEO4J_PASSWORD}
  max_retries: 3
  retry_backoff_factor: 2.0
  retry_base_delay: 1.0

ingestion:
  patterns:
    - "**/*.py"
  follow_symlinks: false      # IMPORTANT: Disabled for security
  max_file_size_mb: 10        # Strict limit to prevent DoS
  batch_size: 50              # Smaller batches for memory safety

logging:
  level: WARNING              # Minimal logging
  format: json                # Structured logging for SIEM
  file: /var/log/repotoire/secure.log
```

## Environment Variable Reference

All configuration options can be set via environment variables using the `REPOTOIRE_` prefix:

```bash
# Neo4j
export REPOTOIRE_NEO4J_URI="bolt://localhost:7687"
export REPOTOIRE_NEO4J_USER="neo4j"
export REPOTOIRE_NEO4J_PASSWORD="your-password"
export REPOTOIRE_NEO4J_MAX_RETRIES=3
export REPOTOIRE_NEO4J_RETRY_BACKOFF_FACTOR=2.0
export REPOTOIRE_NEO4J_RETRY_BASE_DELAY=1.0

# Ingestion
export REPOTOIRE_INGESTION_PATTERNS="**/*.py,**/*.js"
export REPOTOIRE_INGESTION_FOLLOW_SYMLINKS=false
export REPOTOIRE_INGESTION_MAX_FILE_SIZE_MB=10
export REPOTOIRE_INGESTION_BATCH_SIZE=100

# Analysis
export REPOTOIRE_ANALYSIS_MIN_MODULARITY=0.3
export REPOTOIRE_ANALYSIS_MAX_COUPLING=5.0

# Logging (can use REPOTOIRE_ prefix or unprefixed)
export LOG_LEVEL=INFO
export LOG_FORMAT=human
export LOG_FILE=logs/repotoire.log
```

## Environment Variable Interpolation

Config files support environment variable interpolation using `${VAR_NAME}` or `$VAR_NAME` syntax:

```yaml
neo4j:
  uri: bolt://${NEO4J_HOST}:${NEO4J_PORT}
  user: ${NEO4J_USER}
  password: ${NEO4J_PASSWORD}
```

This is useful for:
- Keeping secrets out of config files
- Different environments (dev/staging/prod)
- CI/CD pipelines

## Generating Config Templates

Generate a config template in your preferred format:

```bash
# Generate YAML template
repotoire config --generate yaml > .repotoirerc

# Generate TOML template
repotoire config --generate toml > repotoire.toml

# Generate JSON template
repotoire config --generate json > .repotoirerc
```

## Validating Configuration

Validate your configuration before running operations:

```bash
repotoire validate
```

This checks:
- Configuration file syntax
- Neo4j URI format
- Neo4j credentials
- Neo4j connectivity
- All value ranges and types

## Common Configuration Patterns

### Pattern 1: Development Environment

- Local Neo4j instance
- Human-readable logs
- Standard file patterns
- Default security settings

```yaml
neo4j:
  uri: bolt://localhost:7687
  user: neo4j
  password: ${NEO4J_PASSWORD}

logging:
  level: DEBUG
  format: human
```

### Pattern 2: CI/CD Pipeline

- Environment-based configuration
- JSON logging for parsing
- Strict validation
- No interactive prompts

```bash
export REPOTOIRE_NEO4J_URI="${CI_NEO4J_URI}"
export REPOTOIRE_NEO4J_PASSWORD="${CI_NEO4J_PASSWORD}"
export LOG_FORMAT=json
export LOG_LEVEL=WARNING

repotoire validate && repotoire ingest . && repotoire analyze .
```

### Pattern 3: Production Monitoring

- Secure connections (bolt+s://)
- Persistent logs
- Retry configuration
- Performance tuning

```toml
[neo4j]
uri = "bolt+s://prod-neo4j.example.com:7687"
max_retries = 5
retry_backoff_factor = 2.0

[ingestion]
batch_size = 500

[logging]
level = "INFO"
format = "json"
file = "/var/log/repotoire/production.log"
```

## Troubleshooting

### Config File Not Found

If Repotoire can't find your config file:
1. Check the search path (current dir, parents, home dir)
2. Verify filename is `.repotoirerc` or `repotoire.toml`
3. Check file permissions (must be readable)
4. Use `--config` flag to specify explicit path

### Environment Variables Not Working

If environment variables aren't being recognized:
1. Verify the `REPOTOIRE_` prefix
2. Check variable name matches documented format
3. Ensure variables are exported: `export REPOTOIRE_NEO4J_URI=...`
4. Restart shell if variables were just set

### Password Interpolation Failing

If `${NEO4J_PASSWORD}` isn't working:
1. Verify environment variable is set: `echo $NEO4J_PASSWORD`
2. Use correct syntax: `${VAR_NAME}` or `$VAR_NAME`
3. Check quotes in YAML/TOML (some formats need escaping)

### Validation Errors

Common validation errors:
- **Port 7474**: Use 7687 for Bolt protocol, not 7474 (HTTP)
- **Missing scheme**: URI must start with `bolt://` or `neo4j://`
- **Empty password**: Set via environment variable or config file
- **Invalid batch size**: Must be 10-10000

## Security Best Practices

1. **Never commit passwords** to version control
2. **Use environment variables** for secrets: `password: ${NEO4J_PASSWORD}`
3. **Disable symlinks** unless explicitly needed: `follow_symlinks: false`
4. **Use secure connections** in production: `bolt+s://` or `neo4j+s://`
5. **Set file size limits** to prevent DoS: `max_file_size_mb: 10`
6. **Use JSON logging** for security monitoring: `format: json`
7. **Restrict file permissions** on config files: `chmod 600 .repotoirerc`

## See Also

- [JSON Schema](schema.json) - Machine-readable schema for validation
- [Validation Guide](repotoire/validation.py) - Input validation utilities
- [CLI Reference](README.md#cli-reference) - Command-line options
