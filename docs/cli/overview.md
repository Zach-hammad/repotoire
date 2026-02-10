# CLI Overview

The Repotoire CLI provides local-first code analysis with 42+ detectors, AI-powered fixes, and optional cloud sync for team features.

## Installation

```bash
# Using pip
pip install repotoire

# Using uv (recommended)
uv pip install repotoire
```

No Docker required. Repotoire uses Kuzu, an embedded graph database that runs locally.

## Quick Start

```bash
# Analyze your codebase (that's it!)
repotoire analyze .

# Filter by severity
repotoire analyze . --severity high

# Export to JSON
repotoire analyze . -f json -o findings.json
```

## Core Commands

| Command | Description |
|---------|-------------|
| `analyze` | Run health analysis and generate reports |
| `fix` | AI-powered automatic code fixing (BYOK) |
| `findings` | View and filter analysis findings |
| `sync` | Upload local analysis to cloud dashboard |
| `login` | Authenticate with Repotoire cloud |

## Analysis Options

```bash
# Show only critical and high severity
repotoire analyze . --severity high

# Show top N issues
repotoire analyze . --top 10

# Only analyze changed files (faster)
repotoire analyze . --changed HEAD~5

# Disable embeddings (faster, less features)
repotoire analyze . --no-embeddings

# Output formats
repotoire analyze . -f json    # JSON
repotoire analyze . -f html    # HTML report
repotoire analyze . -f table   # Terminal table (default)
```

## AI-Powered Fixes

Repotoire uses your own API keys (BYOK) — your code never leaves your machine:

```bash
# Set your API key
export OPENAI_API_KEY=sk-...
# or
export ANTHROPIC_API_KEY=sk-ant-...

# Generate a fix for issue #1
repotoire fix 1

# Use a specific model
repotoire fix 1 --model gpt-4o

# Apply fix automatically
repotoire fix 1 --apply
```

## Team Sync

Share local analysis with your team via the cloud dashboard:

```bash
# One-time login
repotoire login

# After any analysis, sync to cloud
repotoire analyze .
repotoire sync
```

View results at [repotoire.com/dashboard](https://repotoire.com/dashboard).

## Environment Variables

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | OpenAI API key (for fixes and embeddings) |
| `ANTHROPIC_API_KEY` | Anthropic API key (for fixes) |
| `VOYAGE_API_KEY` | Voyage AI API key (for embeddings) |
| `DEEPINFRA_API_KEY` | DeepInfra API key (for embeddings) |
| `REPOTOIRE_API_KEY` | Cloud API key (alternative to `login`) |

## Output Formats

### Terminal (Default)

Rich, colorized output with tables and progress bars.

### JSON

Machine-readable output for CI/CD:

```bash
repotoire analyze . -f json -o results.json
```

### HTML

Visual report with code snippets:

```bash
repotoire analyze . -f html -o report.html
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Critical findings detected (use with `--fail-on-critical`) |

## CI/CD Integration

Add Repotoire to your GitHub Actions:

```yaml
- name: Run Repotoire
  uses: repotoire/repotoire/.github/actions/pr-check@main
  with:
    severity: medium
    fail-on: high
```

See [GitHub PR Checks](/docs/guides/github-integration) for full setup.

## Detectors

Repotoire includes 42 code quality detectors:

**Security**: Bandit, Semgrep, hardcoded secrets, SQL injection  
**Quality**: Ruff, Pylint, Mypy, ESLint, dead code, complexity  
**Architecture**: Circular dependencies, god classes, coupling  
**Graph-based**: Influential code, bottlenecks, cohesion  

Run `repotoire detectors list` to see all available detectors.

## Troubleshooting

### Slow Analysis

```bash
# Disable embeddings for faster runs
repotoire analyze . --no-embeddings

# Only analyze changed files
repotoire analyze . --changed HEAD~1
```

### Missing Detectors

Some detectors require external tools:

```bash
# Python
pip install bandit mypy pylint ruff

# JavaScript/TypeScript
npm install -g eslint
```

### Memory Issues

For very large codebases:

```bash
# Reduce batch size
export REPOTOIRE_BATCH_SIZE=50
repotoire analyze .
```
