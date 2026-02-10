# CLI Reference

Complete reference for Repotoire CLI commands.

## Installation

```bash
pip install repotoire
```

## Core Commands

### `repotoire analyze`

Analyze codebase health and generate report.

```bash
repotoire analyze <path> [options]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--severity LEVEL` | Minimum severity: info, low, medium, high, critical |
| `--top N` | Show top N issues only |
| `-f, --format FORMAT` | Output format: table, json, html |
| `-o, --output PATH` | Save output to file |
| `--changed REF` | Only analyze files changed since REF |
| `--embeddings/--no-embeddings` | Generate embeddings (default: on) |
| `-q, --quiet` | Minimal output |

**Examples:**

```bash
# Basic analysis
repotoire analyze .

# Only high+ severity, top 10
repotoire analyze . --severity high --top 10

# Export JSON for CI
repotoire analyze . -f json -o findings.json

# Only changed files
repotoire analyze . --changed HEAD~5
```

---

### `repotoire fix`

Generate AI-powered fix for an issue.

```bash
repotoire fix <issue_number> [options]
```

**Options:**

| Option | Description |
|--------|-------------|
| `-f, --findings PATH` | Path to findings JSON file |
| `--model MODEL` | Model to use: gpt-4o, claude-opus-4-5, etc. |
| `--apply` | Apply fix without confirmation |

**Examples:**

```bash
# Fix issue #1 from last analysis
repotoire fix 1

# Fix from saved findings
repotoire fix 3 -f findings.json

# Use specific model
repotoire fix 1 --model gpt-4o
```

**Environment Variables:**

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | OpenAI API key |
| `ANTHROPIC_API_KEY` | Anthropic API key |

---

### `repotoire sync`

Upload local analysis to cloud dashboard.

```bash
repotoire sync [options]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--org SLUG` | Organization slug (if multiple) |

**Prerequisites:**
- Must run `repotoire login` first
- Must have run `repotoire analyze` (results cached locally)

---

### `repotoire login`

Authenticate with Repotoire cloud.

```bash
repotoire login
```

Opens browser for OAuth authentication. Stores credentials in `~/.repotoire/credentials.json`.

---

### `repotoire findings`

View and filter findings from last analysis.

```bash
repotoire findings [path] [options]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--severity LEVEL` | Filter by severity |
| `--detector NAME` | Filter by detector |
| `-f, --format FORMAT` | Output format: table, json |

---

### `repotoire detectors`

List available detectors.

```bash
repotoire detectors list [options]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--enabled` | Show only enabled detectors |
| `--category CAT` | Filter by category: security, quality, architecture |

---

## Global Options

These apply to all commands:

| Option | Description |
|--------|-------------|
| `--version` | Show version |
| `--help` | Show help |
| `--log-level LEVEL` | DEBUG, INFO, WARNING, ERROR |

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | OpenAI API key (fixes, embeddings) |
| `ANTHROPIC_API_KEY` | Anthropic API key (fixes) |
| `VOYAGE_API_KEY` | Voyage AI API key (embeddings) |
| `DEEPINFRA_API_KEY` | DeepInfra API key (embeddings) |
| `REPOTOIRE_API_KEY` | Cloud API key (alternative to login) |

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Error |
| 2 | Critical findings (with `--fail-on-critical`) |

---

## CI/CD Integration

### GitHub Actions

```yaml
name: Repotoire
on: [pull_request]

jobs:
  analyze:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: repotoire/repotoire/.github/actions/pr-check@main
        with:
          severity: medium
          fail-on: high
```

### GitLab CI

```yaml
repotoire:
  image: python:3.11
  script:
    - pip install repotoire
    - repotoire analyze . -f json -o report.json
  artifacts:
    reports:
      codequality: report.json
```

---

## Detectors

Repotoire includes 42 code quality detectors:

### Security
- **Bandit** — Python security linter
- **Semgrep** — Advanced security patterns
- **Hardcoded secrets** — API keys, passwords in code
- **SQL injection** — Vulnerable query patterns

### Quality
- **Ruff** — 400+ Python linting rules
- **Pylint** — Python code analysis
- **Mypy** — Type checking
- **ESLint** — JavaScript/TypeScript linting
- **Dead code** — Unused functions, imports
- **Complexity** — Cyclomatic complexity

### Architecture
- **Circular dependencies** — Import cycles
- **God classes** — Classes doing too much
- **Coupling** — High module interdependence
- **Cohesion** — Low module cohesion

### Graph-Based
- **Influential code** — High PageRank nodes
- **Bottlenecks** — High betweenness centrality
- **Bus factor** — Knowledge concentration

Run `repotoire detectors list` for the full list.
