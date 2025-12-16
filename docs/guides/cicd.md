# CI/CD Integration

Integrate Repotoire into your continuous integration pipelines to catch code quality issues before they reach production.

## Overview

Repotoire can be run in CI/CD pipelines to:

- Analyze code on every commit
- Fail builds when quality thresholds aren't met
- Generate reports for review
- Track quality trends over time

## GitHub Actions

### Basic Workflow

```yaml
# .github/workflows/repotoire.yml
name: Code Health

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  analyze:
    runs-on: ubuntu-latest

    services:
      neo4j:
        image: neo4j:5
        ports:
          - 7687:7687
        env:
          NEO4J_AUTH: neo4j/password
        options: >-
          --health-cmd "cypher-shell -u neo4j -p password 'RETURN 1'"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install Repotoire
        run: pip install repotoire

      - name: Ingest Codebase
        env:
          REPOTOIRE_NEO4J_URI: bolt://localhost:7687
          REPOTOIRE_NEO4J_PASSWORD: password
        run: repotoire ingest .

      - name: Analyze
        env:
          REPOTOIRE_NEO4J_URI: bolt://localhost:7687
          REPOTOIRE_NEO4J_PASSWORD: password
        run: |
          repotoire analyze . --format json --output report.json
          repotoire analyze . --format html --output report.html

      - name: Upload Report
        uses: actions/upload-artifact@v4
        with:
          name: repotoire-report
          path: |
            report.json
            report.html
```

### With Quality Gates

```yaml
      - name: Check Quality Gate
        env:
          REPOTOIRE_NEO4J_URI: bolt://localhost:7687
          REPOTOIRE_NEO4J_PASSWORD: password
        run: |
          repotoire analyze . --format json --output report.json

          # Check health score
          SCORE=$(jq '.overall_score' report.json)
          if (( $(echo "$SCORE < 70" | bc -l) )); then
            echo "Health score $SCORE is below threshold (70)"
            exit 1
          fi

          # Check for critical findings
          CRITICAL=$(jq '.findings_summary.critical' report.json)
          if [ "$CRITICAL" -gt 0 ]; then
            echo "Found $CRITICAL critical issues"
            exit 1
          fi
```

### Caching Neo4j Data

Speed up subsequent runs by caching the graph database:

```yaml
      - name: Cache Neo4j Data
        uses: actions/cache@v4
        with:
          path: ~/.neo4j-data
          key: neo4j-${{ hashFiles('**/*.py', '**/*.js', '**/*.ts') }}
          restore-keys: |
            neo4j-
```

## GitLab CI

```yaml
# .gitlab-ci.yml
stages:
  - analyze

code-health:
  stage: analyze
  image: python:3.11

  services:
    - name: neo4j:5
      alias: neo4j
      variables:
        NEO4J_AUTH: neo4j/password

  variables:
    REPOTOIRE_NEO4J_URI: bolt://neo4j:7687
    REPOTOIRE_NEO4J_PASSWORD: password

  script:
    - pip install repotoire
    - repotoire ingest .
    - repotoire analyze . --format html --output report.html
    - repotoire analyze . --format json --output report.json

  artifacts:
    paths:
      - report.html
      - report.json
    reports:
      codequality: report.json

  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
```

## Generic CI Setup

For other CI systems (Jenkins, CircleCI, Azure Pipelines, etc.):

### 1. Start Neo4j

```bash
# Docker
docker run -d \
  --name repotoire-neo4j \
  -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password \
  neo4j:5

# Wait for startup
sleep 30
```

### 2. Install and Run

```bash
# Install
pip install repotoire

# Configure
export REPOTOIRE_NEO4J_URI=bolt://localhost:7687
export REPOTOIRE_NEO4J_PASSWORD=password

# Ingest
repotoire ingest .

# Analyze
repotoire analyze . --format json --output report.json
```

### 3. Check Exit Code

Repotoire exits with:

| Exit Code | Meaning |
|-----------|---------|
| 0 | Success, no critical issues |
| 1 | Analysis found critical issues |
| 2 | Configuration or connection error |

Use exit codes for quality gates:

```bash
repotoire analyze . --fail-on critical
if [ $? -ne 0 ]; then
  echo "Quality gate failed"
  exit 1
fi
```

## Exit Codes and Thresholds

Configure failure thresholds:

```bash
# Fail on any critical finding
repotoire analyze . --fail-on critical

# Fail on high or critical findings
repotoire analyze . --fail-on high

# Fail if health score below threshold
repotoire analyze . --min-score 70
```

## Environment Variables for CI

| Variable | Description |
|----------|-------------|
| `REPOTOIRE_NEO4J_URI` | Neo4j connection |
| `REPOTOIRE_NEO4J_PASSWORD` | Neo4j password |
| `REPOTOIRE_LOG_LEVEL` | Set to `WARNING` for cleaner logs |
| `REPOTOIRE_LOG_FORMAT` | Set to `json` for structured logs |

## Best Practices

### 1. Run in Parallel

Analyze multiple repositories in parallel:

```yaml
strategy:
  matrix:
    repo: [api, web, worker]
```

### 2. Incremental Analysis

For faster runs on large codebases:

```bash
repotoire ingest . --incremental
```

### 3. Cache Dependencies

Cache the Python environment:

```yaml
- uses: actions/cache@v4
  with:
    path: ~/.cache/pip
    key: pip-${{ hashFiles('**/requirements.txt') }}
```

### 4. Fail Fast

Set appropriate timeouts:

```yaml
timeout-minutes: 15
```

## Next Steps

- [GitHub Integration](github-integration.md) - Native GitHub App
- [Custom Rules](custom-rules.md) - Project-specific checks
- [Configuration](../getting-started/configuration.md) - CI-specific settings
