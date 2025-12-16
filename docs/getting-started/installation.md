# Installation

This guide covers all installation methods and optional features for Repotoire.

## System Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| Python | 3.10+ | 3.11+ |
| RAM | 4 GB | 8 GB+ |
| Disk Space | 500 MB | 2 GB+ |
| Docker | 20.10+ | Latest |

### Supported Operating Systems

- Linux (Ubuntu 20.04+, Debian 11+, RHEL 8+)
- macOS (12 Monterey+)
- Windows (10/11 with WSL2)

## Installation Methods

### Option 1: pip (Standard)

```bash
# Install from PyPI
pip install repotoire

# Verify installation
repotoire --version
```

### Option 2: uv (Faster)

[uv](https://github.com/astral-sh/uv) is a fast Python package installer.

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install Repotoire
uv pip install repotoire

# Verify installation
repotoire --version
```

### Option 3: From Source

For development or to use the latest features:

```bash
# Clone the repository
git clone https://github.com/repotoire/repotoire.git
cd repotoire

# Install with development dependencies
pip install -e ".[dev]"

# Or with uv
uv pip install -e ".[dev]"

# Verify installation
repotoire --version
```

## Optional Dependencies

Repotoire has modular optional dependencies for different features. Install only what you need.

### Development (`[dev]`)

Testing, linting, and formatting tools for contributors.

```bash
pip install repotoire[dev]
```

Includes: pytest, black, ruff, mypy, coverage tools

### Graph Algorithms (`[gds]`)

Neo4j Graph Data Science library for advanced analytics.

```bash
pip install repotoire[gds]
```

Enables: Community detection, centrality algorithms, graph projections

### Multi-Language Parsing (`[all-languages]`)

Tree-sitter parsers for JavaScript and TypeScript support.

```bash
pip install repotoire[all-languages]
```

Supports: Python, JavaScript, TypeScript parsing

### Configuration Files (`[config]`)

YAML and TOML configuration file support.

```bash
pip install repotoire[config]
```

Enables: `.repotoirerc` (YAML), `repotoire.toml` config files

### Git History Analysis (`[graphiti]`)

Temporal knowledge graph for code evolution queries.

```bash
pip install repotoire[graphiti]
```

Enables: `repotoire historical` commands, natural language git queries

### AI-Powered Auto-Fix (`[ml]`)

Machine learning models for bug prediction and automated fixes.

```bash
pip install repotoire[ml]

# Also requires OpenAI API key
export OPENAI_API_KEY=sk-...
```

Enables: `repotoire auto-fix`, bug prediction, RAG-powered suggestions

### E2B Sandbox (`[sandbox]`)

Secure cloud sandboxes for isolated code execution.

```bash
pip install repotoire[sandbox]

# Requires E2B API key
export E2B_API_KEY=e2b_...
```

Enables: Secure test execution, isolated analysis environments

### Local Embeddings (`[local-embeddings]`)

Free, local embedding models (no API key required).

```bash
pip install repotoire[local-embeddings]
```

Enables: Local RAG with sentence-transformers (Qwen3-0.6B model)

### Security Scanning (`[security]`)

Dependency vulnerability scanning and SBOM generation.

```bash
pip install repotoire[security]
```

Enables: `pip-audit` integration, CycloneDX SBOM output

### External Detectors (`[detectors]`)

Additional code quality tools.

```bash
pip install repotoire[detectors]
```

Includes: mypy, pylint, bandit, radon, vulture, semgrep

### SaaS/API Features (`[saas]`)

For running Repotoire as a hosted service.

```bash
pip install repotoire[saas]
```

Includes: SQLAlchemy, Celery, Redis, Stripe, email services

### Observability (`[observability]`)

Metrics, tracing, and error tracking for production.

```bash
pip install repotoire[observability]
```

Includes: Prometheus, OpenTelemetry, Sentry integration

### Installing Multiple Groups

Combine optional dependencies as needed:

```bash
# Developer with all features
pip install repotoire[dev,config,gds,all-languages]

# Production deployment
pip install repotoire[saas,observability,security]

# AI-powered analysis
pip install repotoire[ml,local-embeddings,graphiti]
```

## Verifying Installation

After installation, verify everything is working:

```bash
# Check version
repotoire --version

# Validate configuration
repotoire validate

# Test Neo4j connection (after Neo4j is running)
repotoire validate --check-neo4j
```

## Post-Installation Setup

### 1. Download spaCy Model

Required for natural language processing features:

```bash
python -m spacy download en_core_web_lg
```

### 2. Start Neo4j

See [Quickstart](quickstart.md) for Neo4j setup instructions.

### 3. Configure Environment

```bash
export REPOTOIRE_NEO4J_URI=bolt://localhost:7687
export REPOTOIRE_NEO4J_PASSWORD=your-password
```

Or create a configuration file:

```bash
repotoire init
```

## Troubleshooting

### Import Errors

If you see `ModuleNotFoundError`:

```bash
# Reinstall with all dependencies
pip install --upgrade --force-reinstall repotoire[dev]
```

### Neo4j Connection Failed

```bash
# Check Neo4j is running
docker ps | grep neo4j

# Test connection
repotoire validate
```

### Permission Denied

On Linux/macOS, you may need to fix permissions:

```bash
# Use user installation
pip install --user repotoire

# Or use a virtual environment
python -m venv .venv
source .venv/bin/activate
pip install repotoire
```

### spaCy Model Not Found

```bash
# Download the required model
python -m spacy download en_core_web_lg

# Verify installation
python -c "import spacy; nlp = spacy.load('en_core_web_lg'); print('OK')"
```

### Memory Issues

For large codebases, increase available memory:

```bash
# Increase Python memory (Linux/macOS)
ulimit -v unlimited

# Or use smaller batch sizes
repotoire ingest /path/to/repo --batch-size 50
```

## Next Steps

- [Quick Start Guide](quickstart.md) - Analyze your first codebase
- [Configuration Guide](../configuration.md) - Customize Repotoire settings
- [CLAUDE.md](../../CLAUDE.md) - Full development documentation
