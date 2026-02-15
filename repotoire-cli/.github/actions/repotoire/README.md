# Repotoire GitHub Action

Graph-powered code analysis with 108 detectors for security, architecture, and code quality.

## Quick Start

```yaml
- uses: Zach-hammad/repotoire/.github/actions/repotoire@v1
```

## Features

- 🔍 **108 Detectors** — Security, architecture, code quality
- 📊 **SARIF Output** — GitHub Code Scanning integration
- ⚡ **Fast Mode** — Skip git history for speed
- 🎯 **Threshold Enforcement** — Fail PRs below quality score
- 💾 **Caching** — Incremental analysis across runs
- 🖥️ **Cross-Platform** — Linux, macOS, Windows

## Usage

### Basic Analysis

```yaml
name: Code Quality
on: [push, pull_request]
jobs:
  analyze:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: Zach-hammad/repotoire/.github/actions/repotoire@v1
```

### With GitHub Code Scanning

```yaml
name: Code Quality
on: [push, pull_request]
jobs:
  analyze:
    runs-on: ubuntu-latest
    permissions:
      security-events: write  # Required for SARIF upload
    steps:
      - uses: actions/checkout@v4
      - uses: Zach-hammad/repotoire/.github/actions/repotoire@v1
        with:
          sarif: true
      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with:
          sarif_file: repotoire.sarif
```

### Fail on Low Score

```yaml
- uses: Zach-hammad/repotoire/.github/actions/repotoire@v1
  with:
    threshold: 70  # Fail if score < 70
```

### Fail on Critical Findings

```yaml
- uses: Zach-hammad/repotoire/.github/actions/repotoire@v1
  with:
    fail-on: critical  # Fail if any critical findings
```

### Fast Mode

```yaml
- uses: Zach-hammad/repotoire/.github/actions/repotoire@v1
  with:
    fast: true  # Skip git history analysis
```

### Relaxed Mode

```yaml
- uses: Zach-hammad/repotoire/.github/actions/repotoire@v1
  with:
    relaxed: true  # Only report high/critical findings
```

### Full Example

```yaml
name: Code Quality
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

permissions:
  contents: read
  security-events: write

jobs:
  repotoire:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # Full history for git detectors

      - uses: Zach-hammad/repotoire/.github/actions/repotoire@v1
        id: analysis
        with:
          sarif: true
          threshold: 70
          cache: true

      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with:
          sarif_file: repotoire.sarif

      - name: Check Score
        run: |
          echo "Score: ${{ steps.analysis.outputs.score }}"
          echo "Findings: ${{ steps.analysis.outputs.findings-count }}"
```

## Inputs

| Input | Description | Default |
|-------|-------------|---------|
| `path` | Path to analyze | `.` |
| `version` | Repotoire version | `latest` |
| `threshold` | Minimum score (0-100, fails below) | `0` |
| `fail-on` | Fail on severity: critical, high, medium, low | - |
| `sarif` | Generate SARIF output | `false` |
| `sarif-file` | SARIF output path | `repotoire.sarif` |
| `fast` | Skip git history (faster) | `false` |
| `relaxed` | Only high/critical findings | `false` |
| `workers` | Parallel workers (1-64) | `8` |
| `severity` | Minimum severity to report | - |
| `skip-detectors` | Comma-separated detectors to skip | - |
| `thorough` | Thorough analysis (slower) | `false` |
| `cache` | Cache .repotoire directory | `true` |
| `token` | GitHub token for releases | `github.token` |

## Outputs

| Output | Description |
|--------|-------------|
| `score` | Code quality score (0-100) |
| `findings-count` | Total findings |
| `critical-count` | Critical findings |
| `high-count` | High severity findings |
| `sarif-file` | SARIF file path (if generated) |

## License

MIT
