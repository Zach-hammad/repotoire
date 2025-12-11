# Repotoire Documentation

Welcome to the Repotoire documentation. Repotoire is a graph-powered code health platform that analyzes codebases using Neo4j knowledge graphs to detect code smells, architectural issues, and technical debt.

## Quick Links

- [Getting Started](getting-started/installation.md) - Set up Repotoire in 5 minutes
- [CLI Reference](cli/overview.md) - Command-line interface documentation
- [API Reference](api/overview.md) - REST API documentation
- [Guides](guides/overview.md) - Step-by-step tutorials

## What is Repotoire?

Unlike traditional linters that examine files in isolation, Repotoire builds a Neo4j knowledge graph combining:

- **Structural Analysis** - AST parsing to understand code structure
- **Semantic Understanding** - NLP and AI to understand code meaning
- **Relational Patterns** - Graph algorithms to detect architectural issues

This multi-layered approach enables detection of complex issues that traditional tools miss:

- Circular dependencies across modules
- Architectural bottlenecks and coupling
- Dead code and unused imports
- Security vulnerabilities with context
- Code duplication patterns

## How to Use Repotoire

### CLI (Command Line)

Best for local development and CI/CD pipelines:

```bash
# Install
pip install repotoire

# Analyze a codebase
repotoire ingest ./my-project
repotoire analyze ./my-project

# Ask questions with natural language
repotoire ask "Where is authentication handled?"
```

See the [CLI Reference](cli/overview.md) for all commands.

### REST API

Best for integrating with web applications and services:

```bash
# Trigger analysis
curl -X POST https://api.repotoire.io/api/v1/analysis/trigger \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"repository_id": "550e8400-e29b-41d4-a716-446655440000"}'

# Get findings
curl https://api.repotoire.io/api/v1/findings \
  -H "Authorization: Bearer $TOKEN"
```

See the [API Reference](api/overview.md) for all endpoints.

### Web Dashboard

Visit [app.repotoire.io](https://app.repotoire.io) for a visual interface with:

- Repository health dashboards
- Finding browser with code context
- AI-powered fix suggestions
- Team analytics and trends

## Documentation Sections

### Getting Started

- [Installation](getting-started/installation.md) - Install Repotoire and dependencies
- [Quick Start](getting-started/quickstart.md) - Your first analysis in 5 minutes
- [Configuration](getting-started/configuration.md) - Configure Repotoire for your needs

### CLI Reference

- [Overview](cli/overview.md) - CLI introduction and concepts
- [Commands](reference/cli-reference.md) - Complete command reference
- [Environment Variables](cli/environment.md) - Environment variable reference

### API Reference

- [Overview](api/overview.md) - API introduction and authentication
- [Endpoints](api/endpoints.md) - All REST API endpoints
- [Webhooks](webhooks/overview.md) - Webhook event payloads

### Guides

- [GitHub Integration](guides/github-integration.md) - Connect GitHub repositories
- [CI/CD Setup](guides/cicd.md) - Integrate with CI/CD pipelines
- [Custom Rules](guides/custom-rules.md) - Create custom detection rules
- [RAG & AI Features](guides/rag.md) - Use AI-powered features

## Support

- **GitHub Issues**: [github.com/repotoire/repotoire/issues](https://github.com/repotoire/repotoire/issues)
- **Email**: support@repotoire.io
- **Documentation**: https://docs.repotoire.io
