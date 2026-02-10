# Quick Start

Analyze your codebase in 30 seconds. No signup required.

## Install

```bash
pip install repotoire
```

> ⚠️ **Don't use `uvx repotoire`** — it won't preserve state between commands. Use `pip install` or `uv pip install` instead.

## Analyze

```bash
repotoire analyze .
```

That's it! Repotoire uses an embedded graph database (Kuzu) — no Docker, no external services needed.

## What You'll See

```
🎼 Repotoire Analysis

Scanning repository...
✓ Built code graph (847 nodes, 2,341 edges)
✓ Running 42 detectors...

╭───────────────────────────────────────╮
│  Health Score: 87/100  (Grade: B)     │
├───────────────────────────────────────┤
│  Structure:     92%  ████████████░░   │
│  Quality:       85%  ██████████░░░░   │
│  Architecture:  78%  █████████░░░░░   │
╰───────────────────────────────────────╯

Found 23 issues:
  🔴 Critical:  3
  🟠 High:      8
  🟡 Medium:   12
```

## Fix Issues

Generate AI-powered fixes using your own API key (BYOK):

```bash
# Set your API key (one-time)
export OPENAI_API_KEY=sk-...

# Fix a specific issue
repotoire fix 1
```

## Filter Results

```bash
# Only show high+ severity
repotoire analyze . --severity high

# Show top 10 issues
repotoire analyze . --top 10

# Export as JSON
repotoire analyze . -f json -o findings.json
```

## Share with Your Team (Optional)

Sync your local analysis to the cloud dashboard:

```bash
# Login (one-time)
repotoire login

# Upload analysis results
repotoire sync
```

View results at [repotoire.com/dashboard](https://repotoire.com/dashboard).

## Next Steps

- [CLI Reference](/docs/reference/cli-reference) — All commands and options
- [Team Features](/teams) — Dashboard, code ownership, PR checks
- [GitHub Integration](/docs/guides/github-integration) — Automated PR analysis
