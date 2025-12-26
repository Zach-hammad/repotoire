# Repotoire Code Quality Check Action

Automatically analyze code quality on every pull request using Repotoire's incremental analysis.

## Features

- ✅ **Incremental Analysis**: Only analyzes changed files (10-100x faster)
- 💬 **PR Comments**: Posts findings directly to pull requests
- 🎯 **Configurable Thresholds**: Set severity levels to block merges
- 📊 **Health Scores**: Track code quality trends
- 🚀 **Fast**: Typically completes in <30 seconds

## Usage

### Basic Example

```yaml
name: Code Quality Check

on: [pull_request]

permissions:
  contents: read
  pull-requests: write

jobs:
  repotoire:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: ./.github/actions/repotoire-check
        with:
          neo4j-password: ${{ secrets.NEO4J_PASSWORD }}
          github-token: ${{ secrets.GITHUB_TOKEN }}
```

### With Neo4j Service

```yaml
name: Code Quality Check

on: [pull_request]

permissions:
  contents: read
  pull-requests: write

jobs:
  repotoire:
    runs-on: ubuntu-latest

    services:
      neo4j:
        image: neo4j:latest
        env:
          NEO4J_AUTH: neo4j/test-password
          NEO4J_PLUGINS: '["graph-data-science","apoc"]'
        ports:
          - 7687:7687
        options: >-
          --health-cmd "cypher-shell -u neo4j -p test-password 'RETURN 1'"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: ./.github/actions/repotoire-check
        with:
          neo4j-uri: bolt://localhost:7687
          neo4j-password: test-password
          fail-on: high
          comment-on-pr: true
          github-token: ${{ secrets.GITHUB_TOKEN }}
```

### With External Neo4j

```yaml
- uses: ./.github/actions/repotoire-check
  with:
    neo4j-uri: ${{ secrets.NEO4J_URI }}
    neo4j-password: ${{ secrets.NEO4J_PASSWORD }}
    fail-on: critical
    github-token: ${{ secrets.GITHUB_TOKEN }}
```

## Inputs

| Input | Description | Required | Default |
|-------|-------------|----------|---------|
| `fail-on` | Minimum severity to fail check (`critical`, `high`, `medium`, `low`) | No | `critical` |
| `comment-on-pr` | Post findings as PR comment | No | `true` |
| `incremental` | Use incremental analysis | No | `true` |
| `neo4j-uri` | Neo4j connection URI | No | `bolt://localhost:7687` |
| `neo4j-password` | Neo4j password | **Yes** | - |
| `github-token` | GitHub token for PR comments | No | `${{ github.token }}` |
| `python-version` | Python version to use | No | `3.10` |

## Outputs

| Output | Description |
|--------|-------------|
| `findings-count` | Total number of findings |
| `critical-count` | Number of critical findings |
| `health-score` | Overall health score (0-100) |

## Example Output

The action will post a comment on your PR that looks like:

```markdown
## 🤖 Repotoire Code Quality Report

**Health Score**: 78/100

### 📊 Found 5 issue(s)

- 🔴 **Critical**: 0
- 🟠 **High**: 1
- 🟡 **Medium**: 2
- 🟢 **Low**: 2

### ⚠️ Critical & High Priority Issues

#### 🟠 Complex function detected

**Severity**: HIGH
**Files**: src/auth.py
**Description**: Function `validate_token` has cyclomatic complexity of 18

**💡 Suggested Fix**: Break into smaller functions

### ✅ Check Passed

All issues are below the `CRITICAL` severity threshold.
```

## Permissions

The action requires these permissions in your workflow:

```yaml
permissions:
  contents: read        # Read repository contents
  pull-requests: write  # Post PR comments
```

## Neo4j Setup

You have three options for Neo4j:

### 1. Service Container (Recommended for CI)

Use a service container in your workflow (see example above). This is the simplest option for CI/CD.

### 2. External Neo4j Instance

Use an external Neo4j instance (cloud or self-hosted):

```yaml
- uses: ./.github/actions/repotoire-check
  with:
    neo4j-uri: ${{ secrets.NEO4J_URI }}  # e.g., bolt://your-host:7687 or bolt+s://host:7687
    neo4j-password: ${{ secrets.NEO4J_PASSWORD }}
```

### 3. GitHub-hosted Neo4j

Use a Neo4j instance hosted on GitHub Actions runners.

## Performance

- **With incremental analysis**: ~10-30 seconds (typical PR with 5-10 changed files)
- **Full analysis**: 2-5 minutes (depending on codebase size)

## Troubleshooting

### Action fails with "Neo4j connection timeout"

Ensure the Neo4j service is healthy before running the action:

```yaml
services:
  neo4j:
    options: >-
      --health-cmd "cypher-shell -u neo4j -p test-password 'RETURN 1'"
      --health-interval 10s
      --health-timeout 5s
      --health-retries 5
```

### PR comments not posting

Check that your workflow has the correct permissions:

```yaml
permissions:
  pull-requests: write
```

### Analysis takes too long

Enable incremental analysis (it's on by default) and ensure `fetch-depth: 0` in checkout:

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0  # Required for git diff
```

## License

MIT
