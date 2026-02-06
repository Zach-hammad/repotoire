# Repotoire Documentation

Repotoire is a graph-powered code health platform. Analyze your codebase locally with 42+ detectors, get AI-powered fixes, and optionally sync to a team dashboard.

## Quick Start

```bash
pip install repotoire
repotoire analyze .
```

That's it. No signup, no Docker, no external services.

## Quick Links

- [Quick Start](getting-started/quickstart.md) — First analysis in 30 seconds
- [CLI Reference](reference/cli-reference.md) — All commands and options
- [Team Features](/teams) — Dashboard, code ownership, PR checks

## What Makes Repotoire Different?

Unlike traditional linters that examine files in isolation, Repotoire builds a **knowledge graph** of your code:

```
┌─────────────────────────────────────────────┐
│  Traditional Linters    │    Repotoire      │
├─────────────────────────┼───────────────────┤
│  File by file           │  Graph analysis   │
│  Syntax only            │  Semantic context │
│  No relationships       │  Calls, imports   │
│  Miss architecture      │  Detects patterns │
└─────────────────────────┴───────────────────┘
```

This enables detection of complex issues that traditional tools miss:

- **Circular dependencies** across modules
- **Architectural bottlenecks** — high coupling, low cohesion
- **Dead code** with call graph proof
- **Security vulnerabilities** with context
- **God classes** that should be split

## How to Use Repotoire

### CLI (Free)

Local analysis on your machine. Code never leaves your computer.

```bash
# Analyze
repotoire analyze .

# Fix with AI (bring your own key)
export OPENAI_API_KEY=sk-...
repotoire fix 1

# Sync to team dashboard
repotoire login
repotoire sync
```

### Team Dashboard (Paid)

Cloud features for engineering teams:

- **Code ownership** — Who owns what code?
- **Bus factor alerts** — Knowledge concentration risks
- **PR quality gates** — Block PRs with critical issues
- **Team trends** — 90-day health history
- **Slack/Teams integration** — Get notified

Visit [repotoire.com/teams](/teams) to get started.

## Documentation

### Getting Started

- [Installation](getting-started/installation.md) — Install with pip
- [Quick Start](getting-started/quickstart.md) — Your first analysis
- [Configuration](getting-started/configuration.md) — Customize behavior

### CLI Reference

- [Overview](cli/overview.md) — CLI introduction
- [Commands](reference/cli-reference.md) — Full command reference

### API Reference

- [Overview](api/overview.md) — REST API introduction
- [Endpoints](api/endpoints.md) — All endpoints

### Guides

- [GitHub Integration](GITHUB_APP.md) — Connect GitHub repos
- [CI/CD Setup](CI_CD_AUTO_FIX.md) — Automated PR checks
- [Webhooks](webhooks/overview.md) — Event notifications

## Support

- **GitHub**: [github.com/repotoire/repotoire](https://github.com/repotoire/repotoire)
- **Email**: support@repotoire.io
- **Discord**: [discord.gg/repotoire](https://discord.gg/repotoire)
