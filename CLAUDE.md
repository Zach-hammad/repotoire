# CLAUDE.md

This file provides essential guidance to Claude Code (claude.ai/code) and developers working with the Repotoire codebase.

## Project Overview

Repotoire is a graph-powered code health platform that analyzes codebases using knowledge graphs to detect code smells, architectural issues, and technical debt. Unlike traditional linters that examine files in isolation, Repotoire builds a FalkorDB knowledge graph combining:
- **Structural analysis** (AST parsing)
- **Semantic understanding** (NLP + AI)
- **Relational patterns** (graph algorithms)

This multi-layered approach enables detection of complex issues that traditional tools miss, such as circular dependencies, architectural bottlenecks, and modularity problems.

## Development Rules

- **Always use `uv` for package management** - Never use `pip` directly. Use `uv pip install`, `uv run`, etc.
- **Run tests with**: `uv run pytest`
- **Run commands with**: `uv run <command>`

## Development Setup

### Installation

```bash
# Install with development dependencies
uv pip install -e ".[dev]"

# Install with all optional dependencies
pip install -e ".[dev,gds,all-languages,config]"

# Download spaCy model for NLP features
python -m spacy download en_core_web_lg
```

### Cloud Setup

Set your API key and start developing:

```bash
export REPOTOIRE_API_KEY=your_dev_key  # Get from repotoire.com/settings/api-keys
repotoire ingest .
repotoire analyze .
```

### TimescaleDB Setup (Optional)

TimescaleDB provides historical metrics tracking for trend analysis and regression detection. Start with Docker:

```bash
cd docker/timescaledb
docker compose up -d
```

Configure connection:
```bash
export REPOTOIRE_TIMESCALE_URI="postgresql://repotoire:repotoire-dev-password@localhost:5432/repotoire_metrics"
```

Track metrics during analysis:
```bash
repotoire analyze /path/to/repo --track-metrics
```

See [docs/TIMESCALEDB_METRICS.md](docs/TIMESCALEDB_METRICS.md) for complete documentation.

### Git + Graphiti Integration (Optional)

Graphiti provides temporal knowledge graph integration for git history, enabling natural language queries about code evolution.

Install dependencies:
```bash
pip install repotoire[graphiti]
export OPENAI_API_KEY="sk-..."
```

Ingest git history:
```bash
repotoire historical ingest-git /path/to/repo --max-commits 500
```

Query code evolution:
```bash
repotoire historical query "When did we add authentication?" /path/to/repo
```

See [docs/GIT_GRAPHITI.md](docs/GIT_GRAPHITI.md) for complete documentation.

### Auto-Fix: AI-Powered Code Fixing (Optional)

Repotoire provides AI-powered automatic code fixing with human-in-the-loop approval, using GPT-4o and RAG for intelligent, evidence-based fixes.

Install dependencies:
```bash
pip install repotoire[autofix]
export OPENAI_API_KEY="sk-..."
```

Generate fixes for detected issues:
```bash
# Ingest codebase with embeddings for RAG
repotoire ingest /path/to/repo --generate-embeddings

# Auto-fix issues with interactive review
repotoire auto-fix /path/to/repo

# Fix critical issues with auto-approve
repotoire auto-fix /path/to/repo --severity critical --auto-approve-high
```

See [docs/AUTO_FIX.md](docs/AUTO_FIX.md) for complete documentation.

### E2B Sandbox: Secure Isolated Execution (Optional)

Repotoire uses [E2B](https://e2b.dev) cloud sandboxes to securely run tests, analysis tools, and MCP skills in isolation, protecting your host system and secrets.

Install and configure:
```bash
pip install repotoire[sandbox]
export E2B_API_KEY="e2b_xxx_your_key"
```

Run with sandbox:
```bash
# Analysis tools run in sandbox automatically
repotoire analyze /path/to/repo

# Run tests in sandbox during auto-fix
repotoire auto-fix /path/to/repo --sandbox-tests

# View sandbox metrics and costs
repotoire sandbox-stats --period 7
```

**Security features:**
- Firecracker microVM isolation (separate filesystem, network, processes)
- Automatic secret filtering (`.env`, `*.key`, `credentials.json` never uploaded)
- Resource limits (CPU, memory, timeout)
- Audit logging for all operations

**Custom templates** provide pre-installed tools for faster startup (~5-10s vs ~30-60s):
```bash
cd e2b-templates/repotoire-analyzer
e2b template build
export E2B_SANDBOX_TEMPLATE="repotoire-analyzer"
```

See [docs/SANDBOX.md](docs/SANDBOX.md) for complete documentation.

### Common Commands

```bash
# Run tests
pytest

# Run tests with coverage
pytest --cov=repotoire --cov-report=html

# Format and lint
black repotoire tests
ruff check repotoire tests
mypy repotoire

# Ingest codebase
repotoire ingest /path/to/repo

# Analyze codebase health
repotoire analyze /path/to/repo -o report.html --format html

# Validate configuration
repotoire validate
```

### MCP Server (Claude Code Integration)

Repotoire provides an MCP server for use with Claude Code, Cursor, and other MCP-compatible AI assistants. The server follows an **Open Core** model:

| Tier | Features | Requirements |
|------|----------|--------------|
| **Free** | Graph analysis, detectors, Cypher queries | Local FalkorDB |
| **Pro** | AI search, RAG Q&A, embeddings | `REPOTOIRE_API_KEY` |

**Start the MCP server:**
```bash
repotoire-mcp
```

**Configure in Claude Code** (`~/.claude.json`):
```json
{
  "mcpServers": {
    "repotoire": {
      "type": "stdio",
      "command": "repotoire-mcp",
      "env": {
        "FALKORDB_HOST": "localhost",
        "FALKORDB_PORT": "6379",
        "FALKORDB_PASSWORD": "your-password",
        "REPOTOIRE_API_KEY": "${REPOTOIRE_API_KEY}"
      }
    }
  }
}
```

**Available tools:**
- `health_check` - [FREE] Check system status
- `analyze_codebase` - [FREE] Run code health analysis
- `query_graph` - [FREE] Execute Cypher queries
- `get_codebase_stats` - [FREE] Get codebase statistics
- `search_code` - [PRO] Semantic code search
- `ask_code_question` - [PRO] RAG-powered Q&A
- `get_embeddings_status` - [PRO] Check embeddings coverage

See [docs/guides/mcp-server.md](docs/guides/mcp-server.md) for complete documentation.

## Architecture

### Core Pipeline Flow

```
Codebase → Parser (AST) → Entities + Relationships → FalkorDB Graph → Detectors → Analysis Engine → Health Report → CLI/Reports
```

### System Components

**Graph Schema**: Nodes (File, Module, Class, Function, Variable, Attribute, Concept), Relationships (IMPORTS, CALLS, CONTAINS, INHERITS, USES, DEFINES, DESCRIBES), unique constraints on qualified names

**Core Modules**:
1. **Parsers** (`repotoire/parsers/`): `CodeParser` abstract base, `PythonParser` (AST), future TypeScript/Java (tree-sitter)
2. **Graph Layer** (`repotoire/graph/`): `FalkorDBClient` (connection pooling, retry logic, batch ops), `GraphSchema` (constraints, indexes, vector indexes)
3. **Pipeline** (`repotoire/pipeline/`): `IngestionPipeline` orchestrates scan → parse → batch (100) → load with security validation
4. **Detectors** (`repotoire/detectors/`): Graph-based (Cypher queries) + 8 hybrid detectors (Ruff, Pylint, Mypy, Bandit, Radon, Jscpd, Vulture, Semgrep)

**Hybrid Detector Suite** (Production-Ready):

| Detector | Tool | Purpose | Performance |
|----------|------|---------|-------------|
| RuffLintDetector | ruff | General linting (400+ rules) | ~1s |
| PylintDetector | pylint | Specialized checks (11 rules) | ~1min (22 cores) |
| MypyDetector | mypy | Type checking | ~10s |
| BanditDetector | bandit | Security | ~5s |
| RadonDetector | radon | Complexity metrics | ~5s |
| JscpdDetector | jscpd | Duplicate code | ~5-10s |
| VultureDetector | vulture | Dead code | ~2-5s |
| SemgrepDetector | semgrep | Advanced security (OWASP) | ~5-15s |

5. **Models** (`repotoire/models.py`): Entity hierarchy (File, Class, Function), Relationships, Findings, CodebaseHealth, severity levels
6. **CLI** (`repotoire/cli.py`): Commands (ingest, analyze, validate, auto-fix), Rich UI (colors, trees, progress bars)
7. **Reporters** (`repotoire/reporters/`): HTML (Jinja2 templates, code snippets), JSON, terminal output
8. **Config** (`repotoire/config.py`): YAML/JSON/TOML support, hierarchical search, env var interpolation
9. **Validation** (`repotoire/validation.py`): Path/URI/credential validation with helpful error messages
10. **Auto-Fix** (`repotoire/autofix/`): AI-powered code fixing with GPT-4o + RAG, human-in-the-loop approval, evidence-based justification, git integration
11. **Sandbox** (`repotoire/sandbox/`): E2B cloud sandbox integration for secure test/tool/skill execution with secret filtering, metrics, and alerts

**Total Analysis Time**: ~3-4 minutes (6x faster than original 12+ minutes)

## Design Decisions (Key Points)

### Why FalkorDB?
- Redis-based graph database with native graph storage
- Cypher for expressive pattern matching
- High performance with in-memory processing
- Production-ready with Redis ecosystem compatibility

### Why Batch Processing?
- Memory efficiency (prevents loading entire codebase)
- Network optimization (reduces round-trips)
- Progress tracking and error recovery

### Why Qualified Names as IDs?
- Human readable (e.g., `module.Class.method`)
- Globally unique, no collisions
- Fast direct lookups in FalkorDB

### Why Three-Category Scoring?
- Holistic view: Structure (40%) + Quality (30%) + Architecture (30%)
- Maps to specific, actionable improvements
- Industry-standard approach

### Why Hybrid Detectors?
- **Accuracy**: External tools (ruff, mypy) use AST/semantic analysis (0% false positives)
- **Context**: Graph enrichment adds relationships, metrics, file complexity
- **Performance**: External tools often faster than pure Cypher queries
- **Actionability**: Auto-fix suggestions from mature tooling

## Incremental Analysis

Repotoire provides **10-100x faster re-analysis** through intelligent incremental analysis that only processes changed files and their dependents.

### How It Works

1. **Hash-based Change Detection**: MD5 hashes stored in FalkorDB detect file modifications
2. **Dependency-Aware Analysis**: Graph queries find files that import changed files
3. **Selective Re-ingestion**: Only affected files are re-parsed and updated
4. **Graph Cleanup**: Deleted files automatically removed from knowledge graph

### Usage

```bash
# Incremental analysis (enabled by default)
repotoire ingest /path/to/repo

# Force full re-analysis
repotoire ingest /path/to/repo --force-full
```

### Performance Example

```
Codebase: 1,234 files
Changed: 10 files (0.8%)
Dependent files: 19 files (via IMPORTS)

Processing: 29 files (2.3% of codebase)
Time: 8 seconds (vs 5 minutes full analysis)
Speedup: 37.5x
```

### Key Features

- **Automatic dependency resolution**: Finds files that import changed files (up to 3 hops)
- **Bidirectional impact**: Tracks both upstream and downstream dependencies
- **Safe deletion**: Removes nodes for deleted files from graph
- **Preserves embeddings**: Reuses expensive vector embeddings for unchanged entities
- **Transaction safety**: Rollback on failure

### Implementation

See `repotoire/pipeline/ingestion.py`:
- `_find_dependent_files()`: Graph query to find import relationships
- `ingest(incremental=True)`: Main incremental ingestion logic
- `get_file_metadata()`: Hash-based change detection

For complete documentation, see [docs/INCREMENTAL_ANALYSIS.md](docs/INCREMENTAL_ANALYSIS.md).

## Pre-commit Hook Integration

Repotoire integrates with the [pre-commit](https://pre-commit.com) framework to automatically check code quality before commits are finalized. This provides **instant feedback** and prevents critical issues from entering the codebase.

### How It Works

1. **Staged Files Only**: Analyzes only files in the git staging area (`git diff --cached`)
2. **Incremental Analysis**: Uses hash-based change detection for sub-5-second performance
3. **Configurable Thresholds**: Block commits based on severity level (critical, high, medium, low)
4. **Rich Feedback**: Clear, emoji-annotated terminal output with fix suggestions
5. **Bypass Option**: Use `git commit --no-verify` to override in emergencies

### Installation

Add to your `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: local
    hooks:
      - id: repotoire-check
        name: Repotoire Code Quality Check
        entry: uv run repotoire-pre-commit
        language: system
        pass_filenames: true
        types: [python]
        require_serial: true
        stages: [commit]
        # Optional: Fail on high or medium severity (default: critical)
        # args: [--fail-on=high]
```

Install pre-commit hooks:
```bash
pre-commit install
```

### Configuration Options

The `repotoire-pre-commit` command accepts these arguments:

- `--fail-on {critical,high,medium,low}`: Minimum severity to fail commit (default: critical)
- `--falkordb-host HOST`: FalkorDB host (default: localhost)
- `--falkordb-password PASSWORD`: FalkorDB password (or use `FALKORDB_PASSWORD` env var)
- `--skip-ingestion`: Skip ingestion and only run analysis (for cached data)

Example with custom configuration:
```yaml
args: [--fail-on=high, --falkordb-host=localhost]
```

### Environment Variables

Set environment variables for FalkorDB authentication:
```bash
export FALKORDB_PASSWORD=your-password
export FALKORDB_HOST=localhost  # Optional
export FALKORDB_PORT=6379  # Optional
```

### Usage Example

```bash
# Stage some files
git add src/module.py

# Commit (pre-commit hook runs automatically)
git commit -m "Add new feature"

# Output:
# 🔍 Checking 1 staged file(s)...
#    Analyzing code...
#
# 📊 Found 2 issue(s) in staged files:
#
# 🟡 [MEDIUM] Complex function detected
#    Files: src/module.py
#    Function calculate_score has cyclomatic complexity of 15
#    💡 Fix: Break into smaller functions
#
# 🟢 [LOW] Missing docstring
#    Files: src/module.py
#    Function helper lacks documentation
#
# ⚠️  Warning: Found 2 issue(s) below 'critical' threshold
# ✅ Commit allowed
```

### Performance

- **Fast**: Typically <5 seconds for small commits (1-5 files)
- **Scalable**: Uses incremental analysis (only changed files + dependencies)
- **Efficient**: Hash-based change detection avoids redundant processing

### Bypass in Emergencies

If you need to commit despite failing checks:
```bash
git commit --no-verify -m "Hotfix: emergency production issue"
```

### Implementation

See `repotoire/hooks/pre_commit.py`:
- `get_staged_files()`: Detects staged Python files via git
- `format_finding_output()`: Formats findings with emoji icons
- `main()`: Entry point with argument parsing and exit codes

Tests: `tests/integration/test_pre_commit_hook.py` (19 tests covering all functionality)

## RAG (Retrieval-Augmented Generation)

Repotoire includes a complete RAG system for natural language code intelligence. See [docs/RAG_API.md](docs/RAG_API.md) for comprehensive documentation.

### Quick Start

```bash
# Option 1: OpenAI backend (high quality, paid)
export OPENAI_API_KEY="sk-..."
repotoire ingest /path/to/repo --generate-embeddings

# Option 2: DeepInfra backend (cheap, high-quality Qwen3)
export DEEPINFRA_API_KEY="..."
repotoire ingest /path/to/repo --generate-embeddings --embedding-backend deepinfra

# Option 3: Local backend (free, no API key required)
pip install repotoire[local-embeddings]
repotoire ingest /path/to/repo --generate-embeddings --embedding-backend local

# Query via API
python -m repotoire.api.app
curl -X POST "http://localhost:8000/api/v1/code/search" \
  -H "Content-Type: application/json" \
  -d '{"query": "authentication functions", "top_k": 5}'
```

### Embedding Backends

| Aspect | OpenAI | DeepInfra | Local (Qwen3-0.6B) |
|--------|--------|-----------|---------------------|
| Model | text-embedding-3-small | Qwen3-Embedding-8B | Qwen3-Embedding-0.6B |
| Quality | Great | Best (MTEB-Code #1) | Excellent |
| Cost | $0.02/1M tokens | ~$0.01/1M tokens | $0 |
| Latency | 50-150ms | 30-100ms | 5-20ms |
| Dependencies | API key | API key | +1.5GB model |
| Dimensions | 1536 | 4096 | 1024 |

**Note**: Local backend falls back to all-MiniLM-L6-v2 (384 dims) on low-memory systems (<4GB RAM).

### Configuration

Via CLI:
```bash
# Local (free, high quality)
repotoire ingest /path/to/repo --generate-embeddings --embedding-backend local

# DeepInfra (cheap API, best quality)
repotoire ingest /path/to/repo --generate-embeddings --embedding-backend deepinfra

# OpenAI (high quality)
repotoire ingest /path/to/repo --generate-embeddings --embedding-backend openai
```

Via config file (`.repotoirerc` or `falkor.toml`):
```yaml
embeddings:
  backend: "deepinfra"  # or "openai", "local"
  model: "Qwen/Qwen3-Embedding-8B"  # optional, uses backend default if not set
```

### Key Components
- **CodeEmbedder**: Supports OpenAI (1536d), DeepInfra (4096d), and local (1024d) backends
- **GraphRAGRetriever**: Hybrid vector + graph search
- **FastAPI Endpoints**: `/search`, `/ask`, `/embeddings/status`
- **Vector Indexes**: FalkorDB native vector support (dimensions auto-configured)

### Performance

**OpenAI backend:**
- **Embedding**: ~10-20 entities/sec, ~$0.02/1M tokens
- **Query**: <2s total (vector search + GPT-4o generation)
- **Cost**: ~$0.10 for 10k files (one-time), $0.0075/query

**DeepInfra backend:**
- **Embedding**: ~20-40 entities/sec, ~$0.01/1M tokens
- **Query**: <1.5s total
- **Cost**: ~$0.05 for 10k files (one-time)

**Local backend:**
- **Embedding**: ~20-50 entities/sec, $0
- **Query**: <1s total (no network latency)
- **Cost**: Free (one-time ~1.5GB model download for Qwen3-0.6B)

## Formal Verification (Lean 4)

Repotoire uses the Lean 4 theorem prover to formally verify correctness of core algorithms. See [docs/VERIFICATION.md](docs/VERIFICATION.md) for comprehensive documentation.

### Quick Start

```bash
# Install Lean 4 via elan
curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf | sh

# Build and verify proofs
cd lean && lake build
```

### What's Verified
- **Weight Conservation**: Category weights sum to 100%
- **Score Bounds**: Scores valid in [0, 100]
- **Grade Coverage**: Every score maps to exactly one grade
- **Boundary Correctness**: All grade thresholds verified

### Project Structure
```
lean/
├── lakefile.toml           # Build configuration
├── lean-toolchain          # Lean version pinning
├── Repotoire.lean          # Library root
└── Repotoire/
    └── HealthScore.lean    # Health score proofs
```

### Adding New Proofs
1. Create `lean/Repotoire/{ProofName}.lean`
2. Add `import Repotoire.{ProofName}` to `Repotoire.lean`
3. Run `lake build` to verify proofs compile

## Extension Points

For detailed examples and step-by-step guides, see the relevant documentation files.

### Adding a New Language Parser
1. Create `repotoire/parsers/{language}_parser.py`
2. Inherit from `CodeParser`, implement `parse()`, `extract_entities()`, `extract_relationships()`
3. Register in `IngestionPipeline.__init__()`
4. Add tests in `tests/unit/parsers/`

### Adding a New Detector
1. Create `repotoire/detectors/{detector_name}.py`
2. Inherit from `CodeSmellDetector`, implement `detect() → List[Finding]`
3. Write Cypher query or call external tool
4. Register in `AnalysisEngine.detectors` list
5. Add tests in `tests/unit/detectors/`

### Adding a Hybrid Detector (External Tool + Graph)
1. Run external tool (subprocess), parse JSON/text output
2. Group findings by file, enrich with graph context
3. Create `Finding` objects with combined metadata
4. Pass `repository_path` via detector_config

### Adding a New Report Format
1. Create `repotoire/reporters/{format}_reporter.py`
2. Implement `generate(health: CodebaseHealth, output_path: Path)`
3. Add to CLI's format choices in `analyze` command

## Troubleshooting

**Common Issues**: FalkorDB connection failures, ingestion performance, parser errors, missing findings, configuration not loading, memory issues.

**Quick Fixes**:
- Connection: `repotoire validate`, check `docker ps | grep falkordb`, verify host/port settings
- Performance: Increase batch size, filter test files, increase Redis memory
- Errors: Check Python version, UTF-8 encoding, review skipped files

See full troubleshooting guide in project documentation.

## Testing

**Organization**: `tests/unit/` (component tests), `tests/integration/` (end-to-end)

**Commands**: `pytest` (all), `pytest --cov=repotoire --cov-report=html` (coverage), `pytest -n auto` (parallel)

## Current Status

### Completed Features ✅
- Core architecture and models
- FalkorDB client with retry logic and connection pooling
- Ingestion pipeline with security validation
- **Incremental analysis** (10-100x faster re-analysis with dependency tracking)
- **Pre-commit hooks integration** (instant code quality checks before commits)
- **TimescaleDB metrics tracking** (historical trends, regression detection, period comparison)
- **Auto-fix system** (AI-powered code fixing with GPT-4o + RAG, human-in-the-loop, evidence-based)
- **E2B Sandbox** (secure isolated execution for tests, tools, and MCP skills with secret filtering)
- CLI interface with Rich formatting
- 8 hybrid detectors + graph detectors
- Health scoring framework (Structure/Quality/Architecture)
- Configuration management (YAML, JSON, TOML)
- HTML report generation with code snippets
- RAG system with OpenAI embeddings
- **Graph detectors** (feature envy, data clumps, god class, inappropriate intimacy, etc.)
- **TypeScript/JavaScript support** (tree-sitter parser with JSDoc, React patterns, nesting tracking)
- **Java support** (tree-sitter parser with Javadoc, annotations, interfaces, enums)
- **Go support** (tree-sitter parser with doc comments, structs, interfaces, methods, goroutines, channels)
- **GitHub Checks integration** (automatic PR analysis with check runs)

### In Progress 🚧
- Additional graph detectors

### Planned Features 📋
- Web dashboard
- IDE plugins (VS Code, JetBrains)
- GitHub Actions integration
- Custom rule engine
- Team analytics

## Dependencies

### Core
- **falkordb** (>=1.0.0): FalkorDB graph database client
- **click** (>=8.1.0): CLI framework
- **rich** (>=13.0.0): Terminal formatting
- **pydantic** (>=2.0.0): Data validation
- **networkx** (>=3.2.0): Graph algorithms
- **jinja2** (>=3.1.0): HTML templates

### AI/NLP
- **spacy** (>=3.7.0): Natural language processing
- **openai** (>=1.0.0): GPT-4o and embeddings

### Configuration
- **pyyaml** (>=6.0): YAML support
- **tomli** (>=2.0.0): TOML support (Python <3.11)

### Optional
- **networkx** (>=3.0.0): Graph algorithms (alternative to FalkorDB native)
- **tree-sitter** (>=0.20.0): Multi-language parsing
- **sentence-transformers** (>=2.2.0): Local embeddings (free, no API key)
- **e2b** (>=0.17.0): E2B cloud sandbox for secure execution

### Development
- **pytest** (>=7.4.0): Testing framework
- **pytest-cov** (>=4.1.0): Coverage reporting
- **black** (>=23.0.0): Code formatting
- **ruff** (>=0.1.0): Linting
- **mypy** (>=1.7.0): Type checking

## Performance

### Scalability
| Codebase Size | Ingestion Time | Analysis Time |
|---------------|----------------|---------------|
| <1k files | <1 minute | Sub-second |
| 1k-10k files | 5-15 minutes | 10-60 seconds |
| 10k-100k files | 30-60 minutes | 1-10 minutes |
| >100k files | Chunking recommended | Incremental |

### Memory Usage
- **Batch size**: Larger (500) = faster but more memory
- **FalkorDB/Redis memory**: Default 512MB, increase for large codebases
- **Python process**: ~100-500MB depending on batch size

### FalkorDB Connection Pool

**Env vars**: `FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_PASSWORD`, `FALKORDB_MAX_CONNECTIONS`

**Guidelines**: Dev (pool=20, timeout=60s), Staging (pool=100, timeout=30s), Production (pool=200, timeout=15s)

## Multi-Tenant Architecture (REPO-600)

Repotoire uses **defense-in-depth** for multi-tenant data isolation with two independent layers:

### Layer 1: Graph-Level Isolation
- Each organization gets a separate FalkorDB graph (e.g., `repotoire_org_abc123`)
- Graph selection happens at connection time via `GraphClientFactory`
- Complete physical separation - no cross-tenant queries possible

### Layer 2: Node-Level Filtering
- Every node has a `tenantId` property (organization UUID)
- Every query includes tenant filtering via `QueryBuilder.with_tenant()` or `_get_isolation_filter()`
- FalkorDB indexes on `tenantId` for all 8 node types ensure fast filtering
- Defense against misconfigured graph selection

### Automatic Tenant Resolution
The CLI automatically resolves tenant from authenticated user for better UX:
```
CLI request → API key validation → org_id from response → TenantContext
```

No `--tenant-id` flag needed - just `repotoire ingest ./repo`. Override available for multi-org users.

### Key Components
| Component | File | Purpose |
|-----------|------|---------|
| TenantContext | `tenant/context.py` | Async-safe ContextVar for tenant propagation |
| TenantContextManager | `tenant/context.py` | Context manager for CLI/API tenant scope |
| QueryBuilder.with_tenant() | `graph/queries/builders.py` | Automatic tenant filtering in queries |
| CodeSmellDetector.tenant_id | `detectors/base.py` | Tenant property for all detectors |
| _get_tenant_from_auth() | `cli/__init__.py` | Auto-resolve tenant from API key |

### Adding Tenant Filtering to New Code
```python
# Option 1: Use QueryBuilder (preferred)
query, params = (QueryBuilder()
    .match("(n:File)")
    .with_tenant("n")  # Automatically adds tenantId filter
    .where("n.language = $lang")
    .return_("n.filePath")
    .build({"lang": "python"}))

# Option 2: Use detector base class methods
filter_clause = self._get_isolation_filter("n")  # Returns "AND n.tenantId = $tenant_id AND n.repoId = $repo_id"
params = self._get_query_params(extra_param="value")  # Includes tenant_id automatically

# Option 3: Inject into existing queries
query, params = inject_tenant_filter(raw_query, existing_params, node_alias="n")
```

## Security Considerations

### Input Validation
- All file paths validated before access
- Symlinks rejected by default
- File size limits enforced (10MB default)
- Repository boundary checks prevent traversal attacks

### Credential Management
- Never commit passwords to version control
- Use environment variables: `${FALKORDB_PASSWORD}`
- Restrict config file permissions: `chmod 600 .repotoirerc`
- Use secure connections in production (TLS/SSL)

### FalkorDB Access Control
- Use dedicated Redis ACL user for Repotoire
- Limit permissions to necessary graph operations
- Enable authentication and TLS for production

## References

- [FalkorDB Documentation](https://docs.falkordb.com/)
- [Python AST Documentation](https://docs.python.org/3/library/ast.html)
- [Cypher Query Language](https://docs.falkordb.com/cypher/)
- [Click Framework](https://click.palletsprojects.com/)
- [Rich Terminal Library](https://rich.readthedocs.io/)
- [Tree-sitter](https://tree-sitter.github.io/)

---

**For user-facing documentation**, see [README.md](README.md) and [CONFIG.md](CONFIG.md).
**For RAG/AI features**, see [docs/RAG_API.md](docs/RAG_API.md).
**For auto-fix features**, see [docs/AUTO_FIX.md](docs/AUTO_FIX.md).
**For sandbox/security**, see [docs/SANDBOX.md](docs/SANDBOX.md).
**For contributing**, see CONTRIBUTING.md (planned).
