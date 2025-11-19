# Falkor 🐉

**Graph-Powered Code Health Platform**

Falkor automatically analyzes your codebase using knowledge graphs to detect code smells, architectural issues, and technical debt that traditional linters miss.

## What Makes Falkor Different?

Most code analysis tools examine files in isolation. Falkor builds a **knowledge graph** of your entire codebase, combining:
- **Structural analysis** (AST parsing)
- **Semantic understanding** (NLP + AI)
- **Relational patterns** (graph algorithms)

This enables detection of complex issues like circular dependencies, architectural bottlenecks, and modularity problems.

## Features

### Detection Capabilities
- 🔄 **Circular Dependencies** - Find import cycles using Tarjan's algorithm
- 👾 **God Classes** - Detect classes with too many responsibilities
- 💀 **Dead Code** - Identify unused functions and classes
- 🔗 **Tight Coupling** - Find architectural bottlenecks
- 📦 **Modularity Analysis** - Suggest module boundaries using community detection
- 📋 **Code Duplication** - Find similar code patterns across the codebase

### AI-Powered Insights
- 🤖 Semantic concept extraction from code
- 💡 Context-aware fix suggestions
- 📊 Natural language explanations of issues
- 🎯 Similarity-based code search

### Health Scoring
- 📈 Letter grade (A-F) with detailed breakdown
- 🎯 Category scores: Structure, Quality, Architecture
- 📊 Actionable metrics and priority recommendations

## Quick Start

```bash
# Install
pip install falkor

# Analyze a codebase
falkor analyze /path/to/repo

# View interactive graph
falkor serve /path/to/repo
```

## Architecture

```
┌─────────────────────────────────────────────┐
│  INGESTION PIPELINE                          │
│  Codebase → Parser → Graph → Neo4j          │
└─────────────────────────────────────────────┘
┌─────────────────────────────────────────────┐
│  ANALYSIS ENGINE                             │
│  Graph Algorithms → Detectors → Findings    │
└─────────────────────────────────────────────┘
┌─────────────────────────────────────────────┐
│  AI LAYER                                    │
│  spaCy + OpenAI → Semantic Enrichment       │
└─────────────────────────────────────────────┘
```

## Tech Stack

- **Graph Database**: Neo4j
- **NLP**: spaCy
- **AI**: OpenAI (GPT-4o, embeddings)
- **Parsing**: Python AST, tree-sitter (multi-language)
- **Graph Algorithms**: Neo4j Graph Data Science

## Roadmap

### MVP (Current)
- [x] Architecture design
- [ ] Python parser implementation
- [ ] Basic detectors (cycles, god classes, dead code)
- [ ] Neo4j integration
- [ ] CLI interface

### v1.0
- [ ] Multi-language support (TypeScript, Java)
- [ ] AI fix suggestions
- [ ] Web dashboard
- [ ] GitHub integration

### Future
- [ ] IDE plugins
- [ ] CI/CD integration
- [ ] Team analytics
- [ ] Custom rule engine

## Contributing

Falkor is in early development. Contributions welcome!

## License

MIT

---

**Named after the luck dragon from The NeverEnding Story** 🐉
