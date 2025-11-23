# TimescaleDB Historical Metrics Tracking

Complete guide to using TimescaleDB for historical code health metrics tracking in Repotoire.

## Table of Contents

- [Overview](#overview)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
  - [Recording Metrics](#recording-metrics)
  - [Querying Trends](#querying-trends)
  - [Detecting Regressions](#detecting-regressions)
  - [Comparing Periods](#comparing-periods)
  - [Exporting Data](#exporting-data)
- [Architecture](#architecture)
- [Schema Reference](#schema-reference)
- [Integration with CI/CD](#integration-with-cicd)
- [Grafana Dashboards](#grafana-dashboards)
- [Troubleshooting](#troubleshooting)

---

## Overview

Repotoire's TimescaleDB integration provides **historical metrics tracking** for code health analysis. This enables:

- **Trend analysis**: Track how code quality changes over time
- **Regression detection**: Automatically detect significant quality drops
- **Period comparison**: Compare metrics between sprints/releases
- **Data export**: Export metrics for visualization in Grafana, spreadsheets, etc.

### Key Features

- ✅ **Time-series optimized**: Hypertables with 7-day chunks
- ✅ **Automatic compression**: Data compressed after 30 days
- ✅ **Retention policies**: Automatic cleanup after 1 year
- ✅ **Continuous aggregates**: Pre-computed daily/weekly summaries
- ✅ **Git metadata**: Tracks branch and commit SHA for every analysis
- ✅ **Multi-repository**: Supports tracking multiple repos and branches
- ✅ **CLI + MCP**: Works with both CLI and MCP server

---

## Quick Start

### 1. Start TimescaleDB (Docker)

```bash
cd docker/timescaledb
docker compose up -d
```

### 2. Configure Connection

```bash
export REPOTOIRE_TIMESCALE_URI="postgresql://repotoire:repotoire-dev-password@localhost:5432/repotoire_metrics"
```

### 3. Run Analysis with Tracking

```bash
repotoire analyze /path/to/repo --track-metrics
```

### 4. View Trends

```bash
repotoire metrics trend /path/to/repo --days 30
```

---

## Installation

### Prerequisites

- **Docker**: For running TimescaleDB locally
- **PostgreSQL Client** (optional): For manual database access

### Install TimescaleDB Support

```bash
# Install repotoire with TimescaleDB dependencies
pip install repotoire[timescale]

# Or with all optional dependencies
pip install repotoire[all]
```

This installs `psycopg2-binary` for PostgreSQL connectivity.

### Start TimescaleDB Container

```bash
cd docker/timescaledb
docker compose up -d
```

The Docker Compose configuration:
- Creates `repotoire-timescaledb` container
- Exposes port `5432`
- Automatically initializes schema on first run
- Persists data in Docker volume

**Optional: Start with pgAdmin**

```bash
docker compose --profile tools up -d
```

Access pgAdmin at http://localhost:8080
- Email: `admin@repotoire.local`
- Password: Set via `PGADMIN_PASSWORD` (default: `admin`)

---

## Configuration

### Environment Variables

TimescaleDB configuration uses environment variables:

```bash
# Connection string (required)
export REPOTOIRE_TIMESCALE_URI="postgresql://user:password@host:port/database"

# Enable metrics tracking (optional)
export REPOTOIRE_TIMESCALE_ENABLED=true

# Auto-track after every analysis (optional)
export REPOTOIRE_TIMESCALE_AUTO_TRACK=true
```

### Config File (`.reporc` or `falkor.toml`)

**YAML (`.reporc`):**
```yaml
timescale:
  enabled: true
  connection_string: "postgresql://repotoire:password@localhost:5432/repotoire_metrics"
  auto_track: false
```

**TOML (`falkor.toml`):**
```toml
[timescale]
enabled = true
connection_string = "postgresql://repotoire:password@localhost:5432/repotoire_metrics"
auto_track = false
```

### Configuration Priority

1. CLI flags (`--track-metrics`)
2. Environment variables (`REPOTOIRE_TIMESCALE_URI`)
3. Config file (`.reporc`, `falkor.toml`)
4. Defaults (tracking disabled)

---

## Usage

### Recording Metrics

#### CLI: Analyze with Metrics Tracking

```bash
# Explicitly enable tracking for this analysis
repotoire analyze /path/to/repo --track-metrics

# Output:
# ... analysis results ...
# Recording metrics to TimescaleDB...
# ✓ Metrics recorded to TimescaleDB
#   Branch: main
#   Commit: abc12345
```

#### Auto-Track (Config)

Set `auto_track: true` in config to record metrics after every analysis:

```yaml
timescale:
  enabled: true
  connection_string: "postgresql://..."
  auto_track: true
```

Then run analysis normally:
```bash
repotoire analyze /path/to/repo
# Metrics automatically recorded
```

#### MCP Server

```json
{
  "tool": "analyze",
  "arguments": {
    "track_metrics": true
  }
}
```

### Querying Trends

#### View Health Trend Over Time

```bash
repotoire metrics trend /path/to/repo --days 30
```

Output (table format):
```
┌────────────────────────┬─────────┬───────────┬─────────┬──────────────┬────────┬──────────┬──────────┐
│ Time                   │ Overall │ Structure │ Quality │ Architecture │ Issues │ Critical │ Commit   │
├────────────────────────┼─────────┼───────────┼─────────┼──────────────┼────────┼──────────┼──────────┤
│ 2024-11-01 10:30:00    │ 82.5    │ 85.0      │ 78.0    │ 84.0         │ 42     │ 2        │ abc12345 │
│ 2024-11-08 11:15:00    │ 84.2    │ 86.5      │ 80.0    │ 85.5         │ 38     │ 1        │ def67890 │
│ 2024-11-15 14:20:00    │ 85.8    │ 88.0      │ 82.0    │ 87.0         │ 35     │ 1        │ ghi11121 │
│ 2024-11-22 09:45:00    │ 87.1    │ 89.5      │ 83.5    │ 88.2         │ 32     │ 0        │ jkl31415 │
└────────────────────────┴─────────┴───────────┴─────────┴──────────────┴────────┴──────────┴──────────┘
```

#### JSON Output

```bash
repotoire metrics trend /path/to/repo --days 30 --format json
```

```json
[
  {
    "time": "2024-11-01T10:30:00+00:00",
    "overall_health": 82.5,
    "structure_health": 85.0,
    "quality_health": 78.0,
    "architecture_health": 84.0,
    "total_findings": 42,
    "critical_count": 2,
    "high_count": 8,
    "commit_sha": "abc12345"
  }
]
```

#### CSV Output

```bash
repotoire metrics trend /path/to/repo --days 90 --format csv > trend.csv
```

### Detecting Regressions

#### Check for Quality Drops

```bash
repotoire metrics regression /path/to/repo --threshold 5.0
```

**No regression:**
```
✓ No significant regression detected
Threshold: 5.0 points
```

**Regression detected:**
```
┌─────────────────────────────────────────┐
│ ⚠️  Quality Regression Detected         │
│─────────────────────────────────────────│
│ Health Score Drop: 8.5 points           │
│                                          │
│ Previous: 87.5 at 2024-11-20 10:00:00   │
│   Commit: abc12345                       │
│                                          │
│ Current: 79.0 at 2024-11-22 14:30:00    │
│   Commit: def67890                       │
│                                          │
│ This exceeds the threshold of 5.0       │
└─────────────────────────────────────────┘
```

#### Custom Threshold

```bash
# More sensitive (smaller threshold)
repotoire metrics regression /path/to/repo --threshold 3.0

# Less sensitive (larger threshold)
repotoire metrics regression /path/to/repo --threshold 10.0
```

### Comparing Periods

#### Compare Metrics Between Date Ranges

```bash
repotoire metrics compare /path/to/repo \
  --start 2024-10-01 \
  --end 2024-10-31
```

Output:
```
┌───────────────────────────────────────┐
│ Period Comparison: /path/to/repo      │
│───────────────────────────────────────│
│ Period: 2024-10-01 to 2024-10-31      │
│ Analyses: 12                          │
│                                        │
│ Health Scores:                         │
│   Average: 84.2                        │
│   Best:    88.5                        │
│   Worst:   79.3                        │
│                                        │
│ Issues:                                │
│   Avg per analysis: 38.5               │
│   Total critical:   8                  │
│   Total high:       42                 │
└───────────────────────────────────────┘
```

#### Use Cases

- **Sprint comparison**: Compare quality between two sprints
- **Release comparison**: Compare quality before/after major releases
- **Team performance**: Track quality trends for different teams

### Exporting Data

#### Export to JSON

```bash
repotoire metrics export /path/to/repo --format json --output metrics.json
```

#### Export to CSV

```bash
repotoire metrics export /path/to/repo --format csv --output metrics.csv
```

#### Export Specific Time Range

```bash
repotoire metrics export /path/to/repo \
  --days 90 \
  --format csv \
  --output last-90-days.csv
```

#### Export All Data

```bash
# Omit --days to export all historical data
repotoire metrics export /path/to/repo --format json --output all-metrics.json
```

---

## Architecture

### Components

1. **MetricsCollector**: Extracts flat metrics from `CodebaseHealth`
2. **TimescaleClient**: Handles all database operations
3. **Schema (SQL)**: Defines hypertables, indexes, and aggregates
4. **CLI Commands**: User-facing query interface
5. **MCP Integration**: Enables metrics tracking from MCP clients

### Data Flow

```
Codebase → AnalysisEngine → CodebaseHealth
                                    ↓
                            MetricsCollector
                                    ↓
                              (flat metrics)
                                    ↓
                            TimescaleClient
                                    ↓
                              TimescaleDB
```

### Storage Layout

**Hypertable: `code_health_metrics`**
- Partitioned by time (7-day chunks)
- Compressed after 30 days
- Retained for 1 year
- Primary key: `(time, repository, branch)`

**Continuous Aggregates:**
- `daily_health_summary`: Daily averages
- `weekly_health_summary`: Weekly averages

---

## Schema Reference

### Main Table: `code_health_metrics`

| Column | Type | Description |
|--------|------|-------------|
| `time` | TIMESTAMPTZ | Analysis timestamp |
| `repository` | TEXT | Repository path/name |
| `branch` | TEXT | Git branch |
| `commit_sha` | TEXT | Git commit SHA |
| `overall_health` | FLOAT | Overall health score (0-100) |
| `structure_health` | FLOAT | Structure category score |
| `quality_health` | FLOAT | Quality category score |
| `architecture_health` | FLOAT | Architecture category score |
| `critical_count` | INT | Critical severity issues |
| `high_count` | INT | High severity issues |
| `medium_count` | INT | Medium severity issues |
| `low_count` | INT | Low severity issues |
| `total_findings` | INT | Total issues |
| `total_files` | INT | Number of files |
| `total_classes` | INT | Number of classes |
| `total_functions` | INT | Number of functions |
| `modularity` | FLOAT | Modularity score |
| `avg_coupling` | FLOAT | Average coupling |
| `circular_dependencies` | INT | Circular dependency count |
| `dead_code_percentage` | FLOAT | Dead code percentage |
| `duplication_percentage` | FLOAT | Code duplication percentage |
| `god_class_count` | INT | God class count |
| `layer_violations` | INT | Layer violations |
| `metadata` | JSONB | Additional metadata |

### Indexes

- Primary key: `(time, repository, branch)`
- Time index (automatic via hypertable)
- Repository + branch index

### Policies

- **Compression**: After 30 days
- **Retention**: Delete after 1 year
- **Refresh**: Continuous aggregates updated hourly

---

## Integration with CI/CD

### GitHub Actions

```yaml
name: Code Quality Tracking

on:
  push:
    branches: [main, develop]
  pull_request:

jobs:
  analyze:
    runs-on: ubuntu-latest

    services:
      neo4j:
        image: neo4j:5.14
        env:
          NEO4J_AUTH: neo4j/password
        ports:
          - 7687:7687

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install Repotoire
        run: pip install repotoire[timescale]

      - name: Ingest Codebase
        env:
          REPOTOIRE_NEO4J_PASSWORD: password
        run: repotoire ingest .

      - name: Analyze with Metrics Tracking
        env:
          REPOTOIRE_NEO4J_PASSWORD: password
          REPOTOIRE_TIMESCALE_URI: ${{ secrets.TIMESCALE_URI }}
        run: |
          repotoire analyze . --track-metrics --output report.json

      - name: Check for Regression
        env:
          REPOTOIRE_TIMESCALE_URI: ${{ secrets.TIMESCALE_URI }}
        run: |
          repotoire metrics regression . --threshold 5.0
```

### GitLab CI

```yaml
code_quality:
  stage: test
  image: python:3.11
  services:
    - neo4j:5.14
  variables:
    NEO4J_AUTH: neo4j/password
    REPOTOIRE_NEO4J_URI: bolt://neo4j:7687
    REPOTOIRE_NEO4J_PASSWORD: password
  before_script:
    - pip install repotoire[timescale]
  script:
    - repotoire ingest .
    - repotoire analyze . --track-metrics --output report.json
    - repotoire metrics regression . --threshold 5.0
  artifacts:
    reports:
      codequality: report.json
```

---

## Grafana Dashboards

### Setup Data Source

1. In Grafana, add PostgreSQL data source:
   - **Host**: Your TimescaleDB host:port
   - **Database**: `repotoire_metrics`
   - **User**: `repotoire`
   - **Password**: Your password
   - **TLS/SSL Mode**: Disable (for local) or Require (for production)

2. Test connection

### Example Queries

**Health Trend Over Time:**
```sql
SELECT
  time_bucket('1 day', time) AS day,
  avg(overall_health) AS avg_health,
  min(overall_health) AS min_health,
  max(overall_health) AS max_health
FROM code_health_metrics
WHERE repository = '/path/to/repo'
  AND branch = 'main'
  AND time > NOW() - INTERVAL '90 days'
GROUP BY day
ORDER BY day
```

**Issue Count by Severity:**
```sql
SELECT
  time,
  critical_count,
  high_count,
  medium_count,
  low_count
FROM code_health_metrics
WHERE repository = '/path/to/repo'
  AND branch = 'main'
  AND time > NOW() - INTERVAL '30 days'
ORDER BY time
```

**Recent Regressions:**
```sql
WITH recent AS (
  SELECT
    time,
    overall_health,
    LAG(overall_health) OVER (ORDER BY time) AS prev_health,
    commit_sha
  FROM code_health_metrics
  WHERE repository = '/path/to/repo'
    AND branch = 'main'
    AND time > NOW() - INTERVAL '30 days'
)
SELECT
  time,
  overall_health,
  prev_health,
  (prev_health - overall_health) AS health_drop,
  commit_sha
FROM recent
WHERE (prev_health - overall_health) > 5
ORDER BY time DESC
```

### Sample Dashboard Panels

1. **Health Score Line Chart**: Overall health over time
2. **Category Scores**: Structure/Quality/Architecture stacked area chart
3. **Issue Count Bar Chart**: Critical/High/Medium/Low by week
4. **Regression Alerts**: Table of recent quality drops
5. **Commit Activity**: Heatmap of analysis frequency

---

## Troubleshooting

### Connection Issues

**Problem**: `REPOTOIRE_TIMESCALE_URI not set`

```bash
# Check if environment variable is set
echo $REPOTOIRE_TIMESCALE_URI

# Set it
export REPOTOIRE_TIMESCALE_URI="postgresql://repotoire:password@localhost:5432/repotoire_metrics"
```

**Problem**: `Connection refused`

```bash
# Check if TimescaleDB container is running
docker ps | grep timescaledb

# Start it
cd docker/timescaledb && docker compose up -d

# Check logs
docker compose logs -f timescaledb
```

### Schema Issues

**Problem**: `TimescaleDB extension not found`

```sql
-- Connect with psql
docker compose exec timescaledb psql -U repotoire -d repotoire_metrics

-- Check extension
SELECT * FROM pg_extension WHERE extname = 'timescaledb';

-- Install if missing (shouldn't be needed with our image)
CREATE EXTENSION timescaledb;
```

**Problem**: `Table does not exist`

The schema should be auto-initialized on first container start. If not:

```bash
cd docker/timescaledb
docker compose exec -T timescaledb psql -U repotoire -d repotoire_metrics < ../../repotoire/historical/schema.sql
```

### Performance Issues

**Problem**: Queries are slow

```sql
-- Check hypertable chunks
SELECT * FROM timescaledb_information.chunks WHERE hypertable_name = 'code_health_metrics';

-- Verify compression is working
SELECT * FROM timescaledb_information.compression_settings WHERE hypertable_name = 'code_health_metrics';

-- Manual compression (if needed)
SELECT compress_chunk(chunk)
FROM timescaledb_information.chunks
WHERE hypertable_name = 'code_health_metrics'
  AND NOT is_compressed;
```

### Missing Dependencies

**Problem**: `No module named 'psycopg2'`

```bash
pip install psycopg2-binary

# Or install with TimescaleDB extras
pip install repotoire[timescale]
```

---

## Best Practices

### Production Deployment

1. **Use managed TimescaleDB**: Timescale Cloud, AWS RDS, or self-hosted
2. **Enable SSL**: Use `sslmode=require` in connection string
3. **Secure credentials**: Use secret management (AWS Secrets Manager, Vault)
4. **Configure backups**: Automated daily backups with point-in-time recovery
5. **Monitor performance**: Set up Prometheus + Grafana monitoring
6. **Tune PostgreSQL**: Adjust `shared_buffers`, `work_mem` for your workload

### Retention Tuning

Default retention is 1 year. To adjust:

```sql
-- Remove existing policy
SELECT remove_retention_policy('code_health_metrics');

-- Add new policy (2 years)
SELECT add_retention_policy('code_health_metrics', INTERVAL '2 years');
```

### Compression Tuning

Default compression after 30 days. To adjust:

```sql
-- Remove existing policy
SELECT remove_compression_policy('code_health_metrics');

-- Add new policy (14 days)
SELECT add_compression_policy('code_health_metrics', INTERVAL '14 days');
```

---

## API Reference

### Python API

```python
from repotoire.historical import TimescaleClient, MetricsCollector
from repotoire.models import CodebaseHealth

# Create client
client = TimescaleClient("postgresql://user:pass@host:port/db")
client.connect()

# Extract metrics from analysis
collector = MetricsCollector()
metrics = collector.extract_metrics(health)

# Record metrics
client.record_metrics(
    metrics=metrics,
    repository="/path/to/repo",
    branch="main",
    commit_sha="abc123"
)

# Query trend
trend = client.get_trend("/path/to/repo", branch="main", days=30)

# Detect regression
regression = client.detect_regression("/path/to/repo", threshold=5.0)

# Close connection
client.close()
```

### Context Manager

```python
with TimescaleClient(connection_string) as client:
    client.record_metrics(metrics, repository="/path/to/repo")
    trend = client.get_trend("/path/to/repo", days=30)
```

---

## Support

- **Documentation**: https://docs.repotoire.dev (coming soon)
- **Issues**: https://github.com/your-org/repotoire/issues
- **Discussions**: https://github.com/your-org/repotoire/discussions

---

## License

This feature is part of Repotoire and follows the same license.
