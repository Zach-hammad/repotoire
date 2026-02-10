# Repotoire vs Graphite: A Comparison

This guide compares Repotoire with [Graphite](https://graphite.dev), helping teams understand when to use each tool and how they complement each other.

## At a Glance

| | **Graphite** | **Repotoire** |
|---|---|---|
| **Purpose** | Stacked PRs & code review acceleration | Graph-powered code health analysis |
| **Problem Solved** | PR review bottlenecks, merge delays | Technical debt, architectural issues |
| **Data Storage** | Git-native (no external DB) | Neo4j knowledge graph |
| **AI Features** | AI code reviewer (Diamond) | RAG search, GPT-4o auto-fix |

## Different Tools for Different Problems

**Graphite** and **Repotoire** are complementary tools that solve different problems at different stages of the development lifecycle.

### Graphite: Ship Faster

Graphite is an AI-powered code review platform focused on **pull request workflows**:

- **Stacked PRs**: Break large features into smaller, reviewable changes
- **AI Code Review**: Automated feedback before human review
- **Merge Queue**: Topology-aware merging that respects PR dependencies
- **Smart Rebasing**: Automatic conflict resolution for stacked branches

**Best for**: Teams struggling with review bottlenecks wanting to accelerate development velocity.

### Repotoire: Ship Better

Repotoire is a graph-powered platform focused on **codebase health analysis**:

- **Knowledge Graph**: Combines AST, NLP, and graph algorithms
- **Architectural Analysis**: Detects issues traditional linters miss
- **Technical Debt Tracking**: Historical metrics and regression detection
- **AI-Powered Fixes**: GPT-4o suggestions with evidence-based justification

**Best for**: Teams dealing with architectural complexity and technical debt that accumulates over time.

## Feature Comparison

| Use Case | Graphite | Repotoire |
|----------|----------|-----------|
| Speeding up PR reviews | Yes | No |
| Reducing merge bottlenecks | Yes | No |
| Detecting code smells | Limited | Yes |
| Finding circular dependencies | No | Yes |
| Architectural bottleneck analysis | No | Yes |
| Technical debt assessment | Limited | Yes |
| AI code review on each PR | Yes | Via RAG |
| Historical metrics tracking | No | Yes |
| Pre-commit hook checks | No | Yes |
| Security vulnerability detection | Limited | Yes (8 detectors) |

## Technical Architecture

### Graphite Architecture

- **Dependency Model**: DAG (Directed Acyclic Graph) of Git branches
- **Storage**: Stateless, Git-native with GitHub PR metadata
- **Key Features**:
  - Topology-aware bisection for CI failure debugging
  - Speculative merge testing (1 CI run vs N)
  - Smart rebasing with automatic conflict resolution

### Repotoire Architecture

```
Codebase → Parser (AST) → Entities + Relationships → Neo4j Graph → Detectors → Health Report
```

- **Storage**: Neo4j graph database with optional TimescaleDB
- **Graph Schema**:
  - **Nodes**: File, Module, Class, Function, Variable, Concept
  - **Relationships**: IMPORTS, CALLS, CONTAINS, INHERITS, USES
- **Key Features**:
  - 8 hybrid detectors (Ruff, Mypy, Bandit, Semgrep, etc.)
  - Incremental analysis with MD5 change detection (10-100x speedup)
  - RAG system with semantic code search

## Performance Characteristics

### Graphite

- Branch operations: Sub-second for typical stacks
- CI cost reduction: Dramatic via speculative merge
- Handles 50+ PR stacks efficiently

### Repotoire

| Codebase Size | Ingestion | Analysis | Incremental |
|---------------|-----------|----------|-------------|
| <1k files | <1 min | Sub-second | - |
| 1k-10k files | 5-15 min | 10-60s | 10-100x faster |
| 10k-100k files | 30-60 min | 1-10 min | 10-100x faster |

## Pricing Comparison

| Tier | Graphite | Repotoire |
|------|----------|-----------|
| **Free** | Hobby tier (individuals) | Open source projects |
| **Trial** | - | 14-day (private repos) |
| **Starter** | $20/user/month | Custom pricing |
| **Team** | $40/user/month | Custom pricing |
| **Enterprise** | Custom | Custom |

## Ideal Combined Workflow

For teams wanting both velocity and quality, use both tools together:

```
1. Developer writes code locally
2. Repotoire pre-commit hook catches issues instantly
3. Developer creates stacked PRs with Graphite CLI
4. Graphite Agent reviews code on PR
5. Graphite merge queue handles dependencies
6. After merge, Repotoire tracks health metrics
7. Team uses Repotoire insights for sprint planning
```

## When to Use Each Tool

### Use Graphite When:

- PR reviews are a bottleneck
- You have large features that need breaking down
- Team members frequently block each other waiting for reviews
- You want AI-assisted code review on every PR

### Use Repotoire When:

- You need to track codebase health over time
- Traditional linters miss architectural issues
- You have circular dependencies or tight coupling
- You want to quantify and reduce technical debt
- You need semantic code search across the codebase

### Use Both When:

- You want end-to-end code quality
- Fast reviews (Graphite) + long-term health (Repotoire)
- You're building systems that must remain maintainable

## Integration Points

Both tools integrate with your existing workflow:

**Graphite**:
- GitHub (native integration)
- VS Code extension
- CLI (`gt` commands)

**Repotoire**:
- Pre-commit hooks
- MCP server (Claude Code, Cursor)
- CLI (`repotoire` commands)
- GitHub Actions (planned)

## Summary

| Aspect | Graphite | Repotoire |
|--------|----------|-----------|
| **Focus** | PR workflow velocity | Codebase health |
| **Stage** | During review/merge | During development & over time |
| **Analysis** | Individual diffs | System-wide relationships |
| **Best For** | Shipping faster | Shipping better |

**Bottom line**: Graphite accelerates how quickly code gets merged. Repotoire ensures the code being merged maintains long-term health. They solve different problems and work well together.

## Learn More

- [Graphite Documentation](https://graphite.dev/docs)
- [Repotoire Getting Started](../getting-started/quickstart.md)
- [Repotoire Architecture](../../CLAUDE.md)
