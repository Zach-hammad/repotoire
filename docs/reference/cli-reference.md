# Repotoire CLI Reference

Complete reference for all Repotoire command-line interface commands.

## Installation

```bash
pip install repotoire
```

## Quick Start

```bash
# Initialize configuration
repotoire init

# Ingest a codebase
repotoire ingest ./my-project

# Run analysis
repotoire analyze ./my-project

# Ask questions
repotoire ask "Where is authentication handled?"
```

## Global Options

These options apply to all commands:

| Option | Description |
|--------|-------------|
| `--version` | Show version and exit |
| `-c, --config PATH` | Path to config file (.reporc or falkor.toml) |
| `--log-level LEVEL` | Set logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `--log-format FORMAT` | Log output format (json, human) |
| `--log-file PATH` | Write logs to file |
| `--help` | Show help message and exit |

## Table of Contents

- [`repotoire analyze`](#analyze)
- [`repotoire ask`](#ask)
- [`repotoire auth`](#auth)
- [`repotoire auth`](#auth)
- [`repotoire auto-fix`](#auto-fix)
- [`repotoire backends`](#backends)
- [`repotoire compare`](#compare)
- [`repotoire embeddings`](#embeddings)
- [`repotoire embeddings`](#embeddings)
- [`repotoire generate-mcp`](#generate-mcp)
- [`repotoire graph`](#graph)
- [`repotoire graph`](#graph)
- [`repotoire historical`](#historical)
- [`repotoire historical`](#historical)
- [`repotoire history`](#history)
- [`repotoire hotspots`](#hotspots)
- [`repotoire ingest`](#ingest)
- [`repotoire init`](#init)
- [`repotoire metrics`](#metrics)
- [`repotoire metrics`](#metrics)
- [`repotoire migrate`](#migrate)
- [`repotoire migrate`](#migrate)
- [`repotoire ml`](#ml)
- [`repotoire ml`](#ml)
- [`repotoire monorepo`](#monorepo)
- [`repotoire monorepo`](#monorepo)
- [`repotoire rule`](#rule)
- [`repotoire rule`](#rule)
- [`repotoire sandbox-stats`](#sandbox-stats)
- [`repotoire scan-secrets`](#scan-secrets)
- [`repotoire schema`](#schema)
- [`repotoire schema`](#schema)
- [`repotoire security`](#security)
- [`repotoire security`](#security)
- [`repotoire show-config`](#show-config)
- [`repotoire style`](#style)
- [`repotoire templates`](#templates)
- [`repotoire templates`](#templates)
- [`repotoire validate`](#validate)

## Commands

## `repotoire analyze`

Analyze codebase health and generate a comprehensive report.


    Runs 8+ detectors to identify code smells, security issues, and
    architectural problems. Combines graph-based analysis with external
    tools (ruff, pylint, mypy, bandit, radon, jscpd, vulture, semgrep).


    EXAMPLES:
      # Basic analysis with terminal output
      $ repotoire analyze ./my-project

# Generate HTML report
      $ repotoire analyze ./my-project -o report.html -f html

# JSON output for CI/CD
      $ repotoire analyze ./my-project -f json -o results.json

# Track metrics over time (requires TimescaleDB)
      $ repotoire analyze ./my-project --track-metrics


    HEALTH SCORES:
      The analysis produces three category scores (0-100):
      - Structure (40%): Modularity, dependencies, coupling
      - Quality (30%): Complexity, duplication, dead code
      - Architecture (30%): Patterns, layering, cohesion

Overall health = weighted average of category scores.


    SEVERITY LEVELS:
      critical   Must fix immediately (security, crashes)
      high       Should fix soon (bugs, major issues)
      medium     Should address (maintainability)
      low        Nice to fix (style, minor issues)
      info       Informational only


    DETECTORS:
      ruff       400+ linting rules (fast)
      pylint     Python-specific checks
      mypy       Type checking errors
      bandit     Security vulnerabilities
      radon      Complexity metrics
      jscpd      Duplicate code detection
      vulture    Dead code detection
      semgrep    Advanced security patterns


    PARALLEL EXECUTION:
      Detectors run in parallel by default for 3-4x speedup.
      Use --no-parallel to disable, --workers to adjust threads.


    EXIT CODES:
      0   Success (no critical findings)
      1   Analysis error
      2   Critical findings detected (CI/CD fail condition)

**Arguments:**

- `REPO_PATH` - The repo path

**Options:**

| Option | Description |
|--------|-------------|
| `--neo4j-uri` `TEXT` | Neo4j connection URI (overrides config) |
| `--neo4j-user` `TEXT` | Neo4j username (overrides config) |
| `--neo4j-password` `TEXT` | Neo4j password (overrides config, prompts if not provided) |
| `--output, -o` `PATH` | Output file for report |
| `--format, -f` `[json|html]` (default: json) | Output format (json or html) |
| `--quiet, -q` | Disable progress indicators and reduce output |
| `--track-metrics` | Record metrics to TimescaleDB for historical tracking |
| `--keep-metadata` | Keep detector metadata in graph after analysis (enables 'repotoire hotspots' queries) |
| `--parallel, --no-parallel` | Run independent detectors in parallel (default: enabled, REPO-217) |
| `--workers` `INTEGER` (default: 4) | Number of parallel workers for detector execution (default: 4) |
| `--offline` | Run without authentication (skip API auth and tier limit checks) |

**Environment Variables:**

- `REPOTOIRE_OFFLINE` - Run without authentication (skip API auth and tier limit checks)

---

## `repotoire ask`

Ask a question about the codebase using RAG.

Uses hybrid search (dense embeddings + BM25) to find relevant code,
    optionally reranks results, then generates an answer using GPT-4o or Claude.

Requires embeddings to be generated first:
        repotoire ingest /path/to/repo --generate-embeddings

Examples:
        repotoire ask "How does authentication work?"
        repotoire ask "What functions call the database?" --top-k 20
        repotoire ask "Explain the caching mechanism" --llm-backend anthropic
        repotoire ask "JWT middleware" --hybrid-search --reranker voyage
        repotoire ask "calculate_score function" --no-hybrid-search --reranker none

**Arguments:**

- `QUERY` - The query

**Options:**

| Option | Description |
|--------|-------------|
| `--neo4j-uri` `TEXT` (default: bolt://localhost:7687) | Neo4j connection URI |
| `--neo4j-password` `TEXT` | Neo4j password |
| `--embedding-backend` `[auto|openai|local|deepinfra|voyage]` (default: auto) | Embedding backend for retrieval ('auto' selects best available) |
| `--llm-backend` `[openai|anthropic]` (default: openai) | LLM backend for answer generation: 'openai' (GPT-4o) or 'anthropic' (Claude Opus 4.5) |
| `--llm-model` `TEXT` | LLM model (default: gpt-4o for OpenAI, claude-opus-4-20250514 for Anthropic) |
| `--top-k` `INTEGER` (default: 10) | Number of code snippets to retrieve for context (default: 10) |
| `--hybrid-search, --no-hybrid-search` | Enable hybrid search (dense + BM25) for improved recall (default: enabled) |
| `--fusion-method` `[rrf|linear]` (default: rrf) | Fusion method for hybrid search: 'rrf' (Reciprocal Rank Fusion) or 'linear' |
| `--reranker` `[voyage|local|none]` (default: local) | Reranker backend: 'voyage' (API), 'local' (cross-encoder), or 'none' |
| `--reranker-model` `TEXT` | Reranker model (default: rerank-2 for voyage, ms-marco-MiniLM for local) |

**Environment Variables:**

- `REPOTOIRE_NEO4J_URI` - Neo4j connection URI
- `REPOTOIRE_NEO4J_PASSWORD` - Neo4j password

---

## `repotoire auth`

Authentication and account commands.

---

### `repotoire auth login`

Login to Repotoire via browser.

Opens your default browser for authentication.
    Credentials are stored locally in ~/.repotoire/credentials.json.

---

### `repotoire auth logout`

Clear stored credentials.

Removes locally stored credentials from ~/.repotoire/credentials.json.

---

### `repotoire auth status`

Show authentication status and token validity.

Displays detailed information about the current authentication state,
    including token expiration time.

---

### `repotoire auth switch-org`

Switch to a different organization.

Changes the active organization context for all CLI commands.
    You must be a member of the target organization.

ORG_SLUG is the URL-friendly identifier of the organization.

**Arguments:**

- `ORG_SLUG` - The org slug

---

### `repotoire auth upgrade`

Open billing portal to upgrade plan.

Opens your browser to the Repotoire billing portal
    where you can upgrade your subscription.

---

### `repotoire auth usage`

Show current usage and limits.

Displays a table showing:
    - Current plan tier
    - Repository usage vs limits
    - Analysis usage vs limits (this month)

---

### `repotoire auth whoami`

Show current user and organization.

Displays information about the currently authenticated user
    and their organization membership.

---

## `repotoire auto-fix`

AI-powered automatic code fixing with human-in-the-loop approval.

Analyzes your codebase, generates AI-powered fixes, and presents them
    for interactive review. Approved fixes are automatically applied with
    git integration.

Test Execution Security:
        By default, tests run in isolated E2B sandboxes to prevent malicious
        auto-fix code from accessing host resources. Use --local-tests only
        for trusted code in development environments.

Examples:
        # Generate and review up to 10 fixes
        repotoire auto-fix /path/to/repo

# Auto-approve high-confidence fixes
        repotoire auto-fix /path/to/repo --auto-approve-high

# Only fix critical issues
        repotoire auto-fix /path/to/repo --severity critical

# Apply fixes and run tests (sandbox by default)
        repotoire auto-fix /path/to/repo --run-tests

# Run tests locally (WARNING: full host access)
        repotoire auto-fix /path/to/repo --run-tests --local-tests

# Custom test timeout (30 minutes for slow test suites)
        repotoire auto-fix /path/to/repo --run-tests --test-timeout 1800

# CI mode: auto-apply all fixes with JSON output
        repotoire auto-fix /path/to/repo --ci-mode --auto-apply --output fixes.json

# Dry run: generate fixes without applying
        repotoire auto-fix /path/to/repo --dry-run --output fixes.json

**Arguments:**

- `REPOSITORY` - The repository

**Options:**

| Option | Description |
|--------|-------------|
| `--max-fixes, -n` `INTEGER` (default: 10) | Maximum fixes to generate |
| `--severity, -s` `[critical|high|medium|low]` | Minimum severity to fix |
| `--auto-approve-high` | Auto-approve high-confidence fixes |
| `--auto-apply` | Auto-apply all fixes without review (CI mode) |
| `--ci-mode` | Enable CI-friendly output and behavior |
| `--dry-run` | Generate fixes but don't apply them |
| `--output, -o` `PATH` | Save fix details to JSON file |
| `--create-branch, --no-branch` | Create git branch for fixes |
| `--run-tests` | Run tests after applying fixes |
| `--test-command` `TEXT` (default: pytest) | Test command to run |
| `--local-tests` | Run tests locally (SECURITY WARNING: full host access) |
| `--test-timeout` `INTEGER` (default: 300) | Test execution timeout in seconds (default: 300) |
| `--neo4j-uri` `TEXT` (default: bolt://localhost:7687) | Neo4j connection URI |
| `--neo4j-password` `TEXT` | Neo4j password |

**Environment Variables:**

- `REPOTOIRE_NEO4J_URI` - Neo4j connection URI
- `REPOTOIRE_NEO4J_PASSWORD` - Neo4j password

---

## `repotoire backends`

Show available embedding backends and their status.

Displays all embedding backends with their configuration status,
    API key availability, and which backend would be auto-selected.

Example:
        repotoire backends

---

## `repotoire compare`

Compare code metrics between two commits.

Shows how code quality metrics changed between commits:
    - Improvements (metrics got better)
    - Regressions (metrics got worse)
    - Percentage changes

Example:
        falkor compare abc123 def456

**Arguments:**

- `BEFORE_COMMIT` - The before commit
- `AFTER_COMMIT` - The after commit

**Options:**

| Option | Description |
|--------|-------------|
| `--neo4j-uri` `TEXT` | Neo4j connection URI |
| `--neo4j-user` `TEXT` | Neo4j username |
| `--neo4j-password` `TEXT` | Neo4j password |

---

## `repotoire embeddings`

Manage graph embeddings for structural similarity.

Graph embeddings capture structural patterns in the code graph,
    enabling similarity search based on call relationships, imports,
    and code organization.

Examples:
        repotoire embeddings generate     # Generate FastRP embeddings
        repotoire embeddings stats        # Show embedding statistics
        repotoire embeddings similar X    # Find similar to X

---

### `repotoire embeddings clones`

Find potential code clones based on structural similarity.

Identifies function pairs with very high structural similarity,
    which may indicate duplicated or copy-pasted code.

Examples:
        repotoire embeddings clones
        repotoire embeddings clones --threshold 0.9 --limit 100

**Options:**

| Option | Description |
|--------|-------------|
| `--threshold, -t` `FLOAT` (default: 0.95) | Minimum similarity to be considered a clone (default: 0.95) |
| `--limit, -l` `INTEGER` (default: 50) | Maximum results (default: 50) |
| `--neo4j-uri` `TEXT` (default: bolt://localhost:7687) | Neo4j connection URI |
| `--neo4j-password` `TEXT` | Neo4j password |

**Environment Variables:**

- `REPOTOIRE_NEO4J_URI` - Neo4j connection URI
- `REPOTOIRE_NEO4J_PASSWORD` - Neo4j password

---

### `repotoire embeddings generate`

Generate FastRP graph embeddings for structural similarity.

FastRP (Fast Random Projection) creates embeddings that capture
    the structural position of code entities in the call graph.

Requirements:
        - Neo4j with Graph Data Science (GDS) plugin
        - Code already ingested into graph

Examples:
        repotoire embeddings generate
        repotoire embeddings generate --dimension 256
        repotoire embeddings generate --force

**Options:**

| Option | Description |
|--------|-------------|
| `--dimension, -d` `INTEGER` (default: 128) | Embedding dimension (default: 128) |
| `--force` | Regenerate even if embeddings exist |
| `--neo4j-uri` `TEXT` (default: bolt://localhost:7687) | Neo4j connection URI |
| `--neo4j-password` `TEXT` | Neo4j password |

**Environment Variables:**

- `REPOTOIRE_NEO4J_URI` - Neo4j connection URI
- `REPOTOIRE_NEO4J_PASSWORD` - Neo4j password

---

### `repotoire embeddings similar`

Find entities structurally similar to the given entity.

Uses FastRP embeddings to find entities with similar structural
    patterns in the code graph.

Examples:
        repotoire embeddings similar "my.module.MyClass.method"
        repotoire embeddings similar "my.module" --type Function -k 20

**Arguments:**

- `QUALIFIED_NAME` - The qualified name

**Options:**

| Option | Description |
|--------|-------------|
| `--top-k, -k` `INTEGER` (default: 10) | Number of results (default: 10) |
| `--type, -t` `[Function|Class|File]` | Filter by node type |
| `--neo4j-uri` `TEXT` (default: bolt://localhost:7687) | Neo4j connection URI |
| `--neo4j-password` `TEXT` | Neo4j password |

**Environment Variables:**

- `REPOTOIRE_NEO4J_URI` - Neo4j connection URI
- `REPOTOIRE_NEO4J_PASSWORD` - Neo4j password

---

### `repotoire embeddings stats`

Show statistics about generated graph embeddings.

Examples:
        repotoire embeddings stats

**Options:**

| Option | Description |
|--------|-------------|
| `--neo4j-uri` `TEXT` (default: bolt://localhost:7687) | Neo4j connection URI |
| `--neo4j-password` `TEXT` | Neo4j password |

**Environment Variables:**

- `REPOTOIRE_NEO4J_URI` - Neo4j connection URI
- `REPOTOIRE_NEO4J_PASSWORD` - Neo4j password

---

## `repotoire generate-mcp`

Generate MCP (Model Context Protocol) server from codebase.

Automatically detects FastAPI routes, Click commands, and public functions,
    then generates a complete runnable MCP server with enhanced descriptions.

Examples:
        # Basic generation
        repotoire generate-mcp

# With RAG enhancements
        repotoire generate-mcp --enable-rag

# Custom output and limits
        repotoire generate-mcp -o ./my_server --max-routes 5 --max-functions 10

**Options:**

| Option | Description |
|--------|-------------|
| `--output-dir, -o` `PATH` (default: ./mcp_server) | Output directory for generated server |
| `--server-name` `TEXT` (default: mcp_server) | Name for the generated MCP server |
| `--neo4j-uri` `TEXT` | Neo4j connection URI (overrides config) |
| `--neo4j-user` `TEXT` | Neo4j username (overrides config) |
| `--neo4j-password` `TEXT` | Neo4j password (overrides config) |
| `--enable-rag` | Enable RAG enhancements (requires OpenAI API key) |
| `--min-params` `INTEGER` (default: 2) | Minimum parameters for public functions |
| `--max-params` `INTEGER` (default: 10) | Maximum parameters for public functions |
| `--max-routes` `INTEGER` | Maximum FastAPI routes to include |
| `--max-commands` `INTEGER` | Maximum Click commands to include |
| `--max-functions` `INTEGER` | Maximum public functions to include |

---

## `repotoire graph`

Graph database management commands for multi-tenancy.

---

### `repotoire graph clear`

Clear all data in an organization's graph.

WARNING: This deletes all nodes and relationships in the graph!
    The graph/database itself remains, only the data is deleted.

ORG_ID: UUID of the organization

Example:
        repotoire graph clear 550e8400-... --slug acme-corp --confirm

**Arguments:**

- `ORG_ID` - The org id

**Options:**

| Option | Description |
|--------|-------------|
| `--slug, -s` `TEXT` | Organization slug |
| `--backend` `[neo4j|falkordb]` | Graph database backend |
| `--confirm` | Confirm without prompting |

---

### `repotoire graph close-all`

Close all cached graph clients.

Releases all database connections held by the factory cache.
    Useful for cleanup or before reconfiguration.

Example:
        repotoire graph close-all

**Options:**

| Option | Description |
|--------|-------------|
| `--backend` `[neo4j|falkordb]` | Graph database backend |

---

### `repotoire graph config`

Show current graph configuration.

Displays environment variables and settings used for graph connections.

Example:
        repotoire graph config

**Options:**

| Option | Description |
|--------|-------------|
| `--backend` `[neo4j|falkordb]` | Graph database backend to show config for |

---

### `repotoire graph deprovision`

Remove graph storage for an organization.

WARNING: This permanently deletes ALL graph data for the organization!

ORG_ID: UUID of the organization

Example:
        repotoire graph deprovision 550e8400-... --slug acme-corp --confirm

**Arguments:**

- `ORG_ID` - The org id

**Options:**

| Option | Description |
|--------|-------------|
| `--slug, -s` `TEXT` **(required)** | Organization slug |
| `--backend` `[neo4j|falkordb]` | Graph database backend |
| `--confirm` | Confirm deletion without prompting |

---

### `repotoire graph list-cached`

List currently cached graph clients.

Shows all organizations with active graph connections in the factory cache.

Example:
        repotoire graph list-cached

**Options:**

| Option | Description |
|--------|-------------|
| `--backend` `[neo4j|falkordb]` | Graph database backend |

---

### `repotoire graph provision`

Provision graph storage for an organization.

Creates a new graph (FalkorDB) or database (Neo4j Enterprise) for
    the specified organization.

ORG_ID: UUID of the organization

Example:
        repotoire graph provision 550e8400-e29b-41d4-a716-446655440000 --slug acme-corp

**Arguments:**

- `ORG_ID` - The org id

**Options:**

| Option | Description |
|--------|-------------|
| `--slug, -s` `TEXT` **(required)** | Organization slug for naming the graph/database |
| `--backend` `[neo4j|falkordb]` | Graph database backend (defaults to REPOTOIRE_DB_TYPE env var or 'neo4j') |

---

### `repotoire graph stats`

Show graph statistics for an organization.

ORG_ID: UUID of the organization

Example:
        repotoire graph stats 550e8400-... --slug acme-corp

**Arguments:**

- `ORG_ID` - The org id

**Options:**

| Option | Description |
|--------|-------------|
| `--slug, -s` `TEXT` | Organization slug (optional, uses UUID prefix if not provided) |
| `--backend` `[neo4j|falkordb]` | Graph database backend |

---

## `repotoire historical`

Query and analyze git history using temporal knowledge graphs.

Commands for integrating git commit history with Graphiti temporal knowledge
    graph, enabling natural language queries about code evolution.

Requires Graphiti to be configured via OPENAI_API_KEY and Neo4j connection.

Examples:
        repotoire historical ingest-git /path/to/repo --since 2024-01-01
        repotoire historical query "When did we add authentication?"
        repotoire historical timeline authenticate_user --entity-type function

---

### `repotoire historical ingest-git`

Ingest git commit history into Graphiti temporal knowledge graph.

Analyzes git repository and creates Graphiti episodes for each commit,
    enabling natural language queries about code evolution over time.

Example:
        repotoire historical ingest-git /path/to/repo --since 2024-01-01 --max-commits 500

**Arguments:**

- `REPOSITORY` - The repository

**Options:**

| Option | Description |
|--------|-------------|
| `--since, -s` `TEXT` | Only ingest commits after this date (YYYY-MM-DD) |
| `--until, -u` `TEXT` | Only ingest commits before this date (YYYY-MM-DD) |
| `--branch, -b` `TEXT` (default: main) | Git branch to analyze |
| `--max-commits, -m` `INTEGER` (default: 1000) | Maximum commits to process |
| `--batch-size` `INTEGER` (default: 10) | Commits to process in parallel |
| `--neo4j-uri` `TEXT` (default: bolt://localhost:7687) | Neo4j connection URI |
| `--neo4j-password` `TEXT` | Neo4j password |

**Environment Variables:**

- `REPOTOIRE_NEO4J_URI` - Neo4j connection URI
- `REPOTOIRE_NEO4J_PASSWORD` - Neo4j password

---

### `repotoire historical query`

Query git history using natural language.

Ask questions about code evolution, when features were added, who made changes,
    and other historical questions about the codebase.

Examples:
        repotoire historical query "When did we add OAuth authentication?" /path/to/repo
        repotoire historical query "What changes led to performance regression?" /path/to/repo
        repotoire historical query "Show all refactorings of UserManager class" /path/to/repo

**Arguments:**

- `QUERY` - The query
- `REPOSITORY` - The repository

**Options:**

| Option | Description |
|--------|-------------|
| `--since, -s` `TEXT` | Filter results after this date (YYYY-MM-DD) |
| `--until, -u` `TEXT` | Filter results before this date (YYYY-MM-DD) |
| `--neo4j-uri` `TEXT` (default: bolt://localhost:7687) | Neo4j connection URI |
| `--neo4j-password` `TEXT` | Neo4j password |

**Environment Variables:**

- `REPOTOIRE_NEO4J_URI` - Neo4j connection URI
- `REPOTOIRE_NEO4J_PASSWORD` - Neo4j password

---

### `repotoire historical timeline`

Get timeline of changes for a specific code entity.

Shows all commits that modified a particular function, class, or module
    over time, helping understand how that code evolved.

Examples:
        repotoire historical timeline authenticate_user /path/to/repo --entity-type function
        repotoire historical timeline UserManager /path/to/repo --entity-type class

**Arguments:**

- `ENTITY_NAME` - The entity name
- `REPOSITORY` - The repository

**Options:**

| Option | Description |
|--------|-------------|
| `--entity-type, -t` `TEXT` (default: function) | Type of entity (function, class, module) |
| `--neo4j-uri` `TEXT` (default: bolt://localhost:7687) | Neo4j connection URI |
| `--neo4j-password` `TEXT` | Neo4j password |

**Environment Variables:**

- `REPOTOIRE_NEO4J_URI` - Neo4j connection URI
- `REPOTOIRE_NEO4J_PASSWORD` - Neo4j password

---

## `repotoire history`

Ingest Git history for temporal analysis.

Analyzes code evolution across Git commits to track:
    - Metric trends over time
    - Code quality degradation
    - Technical debt velocity

Strategies:
      recent      - Last N commits (default, fast)
      milestones  - Tagged releases only
      all         - All commits (expensive)

Example:
        falkor history /path/to/repo --strategy recent --max-commits 10

**Arguments:**

- `REPO_PATH` - The repo path

**Options:**

| Option | Description |
|--------|-------------|
| `--neo4j-uri` `TEXT` | Neo4j connection URI |
| `--neo4j-user` `TEXT` | Neo4j username |
| `--neo4j-password` `TEXT` | Neo4j password |
| `--strategy` `[recent|all|milestones]` (default: recent) | Commit selection strategy |
| `--max-commits` `INTEGER` (default: 10) | Maximum commits to analyze (default: 10) |
| `--branch` `TEXT` (default: HEAD) | Branch to analyze (default: HEAD) |
| `--generate-clues` | Generate semantic clues for each commit |

---

## `repotoire hotspots`

Find code hotspots flagged by multiple detectors.

Hotspots are code entities (files, classes, functions) that have been
    flagged by multiple detectors, indicating high-priority issues.

Examples:

# Find entities flagged by 3+ detectors
        repotoire hotspots --min-detectors 3

# Find high-confidence critical issues
        repotoire hotspots --min-confidence 0.9 --severity HIGH

# Show hotspots for specific file
        repotoire hotspots --file path/to/file.py

**Options:**

| Option | Description |
|--------|-------------|
| `--min-detectors` `INTEGER` (default: 2) | Minimum number of detectors that must flag an entity (default: 2) |
| `--min-confidence` `FLOAT` (default: 0.0) | Minimum average confidence score 0.0-1.0 (default: 0.0) |
| `--severity` `[CRITICAL|HIGH|MEDIUM|LOW|INFO]` | Filter by severity level |
| `--file` `TEXT` | Show hotspots for a specific file |
| `--limit` `INTEGER` (default: 50) | Maximum results to return (default: 50) |
| `--neo4j-uri` `TEXT` (default: bolt://localhost:7687) | Neo4j connection URI |
| `--neo4j-password` `TEXT` | Neo4j password |

**Environment Variables:**

- `REPOTOIRE_NEO4J_URI` - Neo4j connection URI
- `REPOTOIRE_NEO4J_PASSWORD` - Neo4j password

---

## `repotoire ingest`

Ingest a codebase into the knowledge graph.


    Parses source code and builds a Neo4j knowledge graph containing:
    - Files, modules, classes, functions, and variables
    - Relationships: IMPORTS, CALLS, CONTAINS, INHERITS, USES
    - Optional: AI-powered semantic clues and vector embeddings


    EXAMPLES:
      # Basic ingestion
      $ repotoire ingest ./my-project

# With embeddings for RAG search
      $ repotoire ingest ./my-project --generate-embeddings

# Force full re-ingestion (ignore cache)
      $ repotoire ingest ./my-project --force-full

# Use FalkorDB instead of Neo4j
      $ repotoire ingest ./my-project --db-type falkordb


    INCREMENTAL MODE (default):
      Only processes files changed since last ingestion. Uses MD5 hashes
      stored in the graph to detect changes. 10-100x faster than full
      re-ingestion. Use --force-full to override.


    SECURITY FEATURES:
      - Repository boundary validation (prevents path traversal)
      - Symlink protection (disabled by default)
      - File size limits (10MB default)
      - Secrets detection with configurable policy


    DATABASE BACKENDS:
      neo4j     Full-featured graph database (recommended)
      falkordb  Lightweight Redis-based alternative (faster startup)


    EMBEDDING BACKENDS:
      auto      Auto-select best available (default)
      voyage    Voyage AI code-optimized embeddings (best for code)
      openai    OpenAI text-embedding-3-small (high quality)
      deepinfra DeepInfra Qwen3-Embedding-8B (cheap API)
      local     Local Qwen3-Embedding-0.6B (free, no API key)


    ENVIRONMENT VARIABLES:
      REPOTOIRE_NEO4J_URI       Neo4j connection URI
      REPOTOIRE_NEO4J_PASSWORD  Neo4j password
      REPOTOIRE_DB_TYPE         Database type (neo4j/falkordb)
      OPENAI_API_KEY            For OpenAI embeddings
      VOYAGE_API_KEY            For Voyage embeddings
      DEEPINFRA_API_KEY         For DeepInfra embeddings

**Arguments:**

- `REPO_PATH` - The repo path

**Options:**

| Option | Description |
|--------|-------------|
| `--db-type` `[neo4j|falkordb]` | Database type: neo4j or falkordb (default: neo4j, or REPOTOIRE_DB_TYPE env) |
| `--neo4j-uri` `TEXT` | Neo4j connection URI (overrides config) |
| `--neo4j-user` `TEXT` | Neo4j username (overrides config) |
| `--neo4j-password` `TEXT` | Neo4j password (overrides config, prompts if not provided for neo4j) |
| `--pattern, -p` `TEXT` | File patterns to analyze (overrides config) |
| `--follow-symlinks` | Follow symbolic links (overrides config) |
| `--max-file-size` `FLOAT` | Maximum file size in MB (overrides config) |
| `--secrets-policy` `[redact|block|warn|fail]` | Policy for handling detected secrets (overrides config, default: redact) |
| `--incremental, --no-incremental` | Use incremental ingestion (skip unchanged files, default: enabled) |
| `--force-full` | Force full re-ingestion (ignore file hashes) |
| `--quiet, -q` | Disable progress bars and reduce output |
| `--generate-clues` | Generate AI-powered semantic clues (requires spaCy) |
| `--generate-embeddings` | Generate vector embeddings for RAG (requires OpenAI API key or local backend) |
| `--embedding-backend` `[auto|openai|local|deepinfra|voyage]` | Embedding backend: 'auto' (selects best available), 'voyage' (code-optimized), 'openai' (high quality), 'deepinfra' (cheap API), or 'local' (free) |
| `--embedding-model` `TEXT` | Embedding model (default: text-embedding-3-small for OpenAI, Qwen3-Embedding-0.6B for local, Qwen3-Embedding-8B for DeepInfra, voyage-code-3 for Voyage) |
| `--batch-size` `INTEGER` | Number of entities to batch before loading to graph (overrides config, default: 100) |
| `--generate-contexts` | Generate semantic contexts using Claude for improved retrieval (adds cost) |
| `--context-model` `[claude-haiku-3-5-20241022|claude-sonnet-4-20250514]` (default: claude-haiku-3-5-20241022) | Claude model for context generation (haiku is cheaper, default: claude-haiku-3-5-20241022) |
| `--max-context-cost` `FLOAT` | Maximum USD to spend on context generation (default: unlimited) |

**Environment Variables:**

- `REPOTOIRE_DB_TYPE` - Database type: neo4j or falkordb (default: neo4j, or REPOTOIRE_DB_TYPE env)
- `REPOTOIRE_NEO4J_PASSWORD` - Neo4j password (overrides config, prompts if not provided for neo4j)

---

## `repotoire init`

Initialize a new Repotoire configuration file.

Creates a config file template with default values and comments.

Examples:
        falkor init                    # Create .reporc (YAML)
        falkor init -f json            # Create .reporc (JSON)
        falkor init -f toml            # Create falkor.toml
        falkor init -o myconfig.yaml   # Custom output path

**Options:**

| Option | Description |
|--------|-------------|
| `--format, -f` `[yaml|json|toml]` (default: yaml) | Config file format (default: yaml) |
| `--output, -o` `PATH` | Output file path (default: .reporc for yaml/json, falkor.toml for toml) |
| `--force` | Overwrite existing config file |

---

## `repotoire metrics`

Query and export historical metrics from TimescaleDB.

Commands for analyzing code health trends, detecting regressions,
    and exporting metrics data for visualization in tools like Grafana.

Requires TimescaleDB to be configured via REPOTOIRE_TIMESCALE_URI.

Examples:
        repotoire metrics trend myrepo --days 30
        repotoire metrics regression myrepo
        repotoire metrics compare myrepo --start 2024-01-01 --end 2024-01-31
        repotoire metrics export myrepo --format csv --output metrics.csv

---

### `repotoire metrics compare`

Compare metrics between two time periods.

Calculates aggregate statistics (average, min, max) for a date range,
    useful for comparing sprint performance or release quality.

Example:
        repotoire metrics compare /path/to/repo --start 2024-01-01 --end 2024-01-31

**Arguments:**

- `REPOSITORY` - The repository

**Options:**

| Option | Description |
|--------|-------------|
| `--branch, -b` `TEXT` (default: main) | Git branch to query |
| `--start, -s` `TEXT` **(required)** | Start date (YYYY-MM-DD) |
| `--end, -e` `TEXT` **(required)** | End date (YYYY-MM-DD) |

---

### `repotoire metrics export`

Export metrics data for external analysis.

Exports raw metrics data in JSON or CSV format for use in visualization
    tools like Grafana, spreadsheets, or custom analytics pipelines.

Example:
        repotoire metrics export /path/to/repo --format csv --output metrics.csv

**Arguments:**

- `REPOSITORY` - The repository

**Options:**

| Option | Description |
|--------|-------------|
| `--branch, -b` `TEXT` (default: main) | Git branch to query |
| `--days, -d` `INTEGER` | Number of days to look back (optional) |
| `--format, -f` `[json|csv]` (default: json) | Output format |
| `--output, -o` `PATH` | Output file (prints to stdout if not specified) |

---

### `repotoire metrics regression`

Detect if health score dropped significantly.

Compares the most recent analysis with the previous one to identify
    sudden quality regressions that may require immediate attention.

Example:
        repotoire metrics regression /path/to/repo --threshold 10.0

**Arguments:**

- `REPOSITORY` - The repository

**Options:**

| Option | Description |
|--------|-------------|
| `--branch, -b` `TEXT` (default: main) | Git branch to query |
| `--threshold, -t` `FLOAT` (default: 5.0) | Minimum health score drop to flag |

---

### `repotoire metrics trend`

Show health score trend over time.

Displays how code health metrics have changed over the specified time period.
    Useful for identifying gradual quality degradation or improvements.

Example:
        repotoire metrics trend /path/to/repo --days 90 --format table

**Arguments:**

- `REPOSITORY` - The repository

**Options:**

| Option | Description |
|--------|-------------|
| `--branch, -b` `TEXT` (default: main) | Git branch to query |
| `--days, -d` `INTEGER` (default: 30) | Number of days to look back |
| `--format, -f` `[table|json|csv]` (default: table) | Output format |

---

## `repotoire migrate`

Manage database schema migrations.

Schema migrations allow you to safely evolve the Neo4j database schema
    over time with version tracking and rollback capabilities.

Examples:
        falkor migrate status              # Show current migration state
        falkor migrate up                  # Apply pending migrations
        falkor migrate down --to-version 1 # Rollback to version 1

---

### `repotoire migrate down`

Rollback migrations to a previous version.

WARNING: This operation may result in data loss. Use with caution!

**Options:**

| Option | Description |
|--------|-------------|
| `--neo4j-uri` `TEXT` | Neo4j connection URI (overrides config) |
| `--neo4j-user` `TEXT` | Neo4j username (overrides config) |
| `--neo4j-password` `TEXT` | Neo4j password (overrides config, prompts if not provided) |
| `--to-version` `INTEGER` **(required)** | Target version to rollback to |
| `--force` | Skip confirmation prompt |

---

### `repotoire migrate export`

Export graph data to a portable JSON format.

Exports all nodes and relationships for migration between
    Neo4j and FalkorDB or for backup purposes.

Example:
        repotoire migrate export -o backup.json.gz
        repotoire migrate export -o backup.json --no-compress

**Options:**

| Option | Description |
|--------|-------------|
| `--output, -o` `PATH` **(required)** | Output file path (JSON or .json.gz) |
| `--neo4j-uri` `TEXT` | Neo4j/FalkorDB connection URI |
| `--neo4j-user` `TEXT` | Neo4j username |
| `--neo4j-password` `TEXT` | Neo4j password (prompts if not provided) |
| `--compress, --no-compress` | Compress output with gzip (default: true) |

---

### `repotoire migrate import`

Import graph data from a portable JSON format.

Imports nodes and relationships from an export file,
    useful for migration between Neo4j and FalkorDB.

Example:
        repotoire migrate import -i backup.json.gz
        repotoire migrate import -i backup.json --clear

**Options:**

| Option | Description |
|--------|-------------|
| `--input, -i` `PATH` **(required)** | Input file path (JSON or .json.gz) |
| `--neo4j-uri` `TEXT` | Neo4j/FalkorDB connection URI |
| `--neo4j-user` `TEXT` | Neo4j username |
| `--neo4j-password` `TEXT` | Neo4j password (prompts if not provided) |
| `--clear, --no-clear` | Clear existing data before import (default: false) |
| `--batch-size` `INTEGER` (default: 100) | Batch size for import operations (default: 100) |

---

### `repotoire migrate status`

Show current migration status and pending migrations.

**Options:**

| Option | Description |
|--------|-------------|
| `--neo4j-uri` `TEXT` | Neo4j connection URI (overrides config) |
| `--neo4j-user` `TEXT` | Neo4j username (overrides config) |
| `--neo4j-password` `TEXT` | Neo4j password (overrides config, prompts if not provided) |

---

### `repotoire migrate up`

Apply pending migrations to upgrade schema.

**Options:**

| Option | Description |
|--------|-------------|
| `--neo4j-uri` `TEXT` | Neo4j connection URI (overrides config) |
| `--neo4j-user` `TEXT` | Neo4j username (overrides config) |
| `--neo4j-password` `TEXT` | Neo4j password (overrides config, prompts if not provided) |
| `--to-version` `INTEGER` | Target version to migrate to (default: latest) |

---

### `repotoire migrate validate`

Validate graph data integrity after migration.

Checks node counts, relationship counts, and schema integrity
    to ensure data was migrated correctly.

Example:
        repotoire migrate validate

**Options:**

| Option | Description |
|--------|-------------|
| `--neo4j-uri` `TEXT` | Neo4j/FalkorDB connection URI |
| `--neo4j-user` `TEXT` | Neo4j username |
| `--neo4j-password` `TEXT` | Neo4j password (prompts if not provided) |

---

## `repotoire ml`

Machine learning commands for training data extraction.

---

### `repotoire ml export-graph-data`

Export graph data for offline GraphSAGE training.

Exports node features and edges to JSON files that can be used
    for training GraphSAGE without requiring a live Neo4j connection.

Useful for:
    - Training on large clusters without Neo4j access
    - Sharing training data between team members
    - Archiving project graphs for reproducibility

Examples:

# Export current project's graph
        repotoire ml export-graph-data -o ./exports -n myproject

# Export after ingesting
        repotoire ingest /path/to/repo --generate-embeddings
        repotoire ml export-graph-data -o ./exports -n myproject

**Arguments:**

- `REPO_PATH` (optional) - The repo path

**Options:**

| Option | Description |
|--------|-------------|
| `--output-dir, -o` `PATH` **(required)** | Output directory for exported graph files |
| `--project-name, -n` `TEXT` **(required)** | Project name for the exported files |

---

### `repotoire ml extract-multi-project-labels`

Extract training labels from multiple projects' git history.

Analyzes commit history from multiple repositories to build a
    comprehensive training dataset for cross-project defect prediction.

Examples:

# Extract from two projects
        repotoire ml extract-multi-project-labels \
            -p /path/to/flask,/path/to/requests \
            -o combined_labels.json

# With limits for faster testing
        repotoire ml extract-multi-project-labels \
            -p ./proj1,./proj2,./proj3 \
            -o labels.json \
            --max-commits 100 \
            --max-examples 500

**Options:**

| Option | Description |
|--------|-------------|
| `--projects, -p` `TEXT` **(required)** | Comma-separated project paths (e.g., /path/to/proj1,/path/to/proj2) |
| `--output, -o` `TEXT` **(required)** | Output JSON file for combined labels |
| `--since` `TEXT` (default: 2020-01-01) | Start date for git history (default: 2020-01-01) |
| `--max-commits` `INTEGER` | Maximum commits to analyze per project |
| `--max-examples` `INTEGER` | Maximum examples per project (balanced 50/50) |

---

### `repotoire ml extract-training-data`

Extract training data from git history for bug prediction.

Analyzes commit history to identify functions changed in bug-fix commits
    (labeled as 'buggy') vs functions never involved in bugs ('clean').

Examples:

# Basic extraction
        repotoire ml extract-training-data /path/to/repo

# Limit to recent commits
        repotoire ml extract-training-data /path/to/repo --since 2023-01-01

# Custom output and limits
        repotoire ml extract-training-data ./myrepo -o data.json --max-examples 1000

# Custom keywords
        repotoire ml extract-training-data ./myrepo -k fix -k defect -k regression

**Arguments:**

- `REPO_PATH` - The repo path

**Options:**

| Option | Description |
|--------|-------------|
| `--since` `TEXT` (default: 2020-01-01) | Start date for commit history (YYYY-MM-DD, default: 2020-01-01) |
| `--output, -o` `TEXT` (default: training_data.json) | Output file for training data (default: training_data.json) |
| `--max-examples` `INTEGER` | Maximum total examples to extract (will be balanced 50/50) |
| `--max-commits` `INTEGER` | Maximum commits to analyze (for faster testing) |
| `--keywords, -k` `TEXT` | Custom bug-fix keywords (can specify multiple, e.g., -k fix -k bug) |
| `--min-loc` `INTEGER` (default: 5) | Minimum lines of code for functions (default: 5) |
| `--include-source, --no-source` | Include function source code in output (default: yes) |

---

### `repotoire ml generate-embeddings`

Generate Node2Vec embeddings for code graph nodes.

Creates graph embeddings using random walks that capture both local
    (BFS-like) and global (DFS-like) structural patterns in the call graph.

Prerequisites:
    - Codebase must be ingested first (repotoire ingest)
    - Neo4j with GDS plugin must be running

Examples:

# Basic embedding generation
        repotoire ml generate-embeddings

# Custom parameters
        repotoire ml generate-embeddings --dimension 256 --walks-per-node 20

# BFS-biased walks (tight communities)
        repotoire ml generate-embeddings --return-factor 0.5 --in-out-factor 2.0

# DFS-biased walks (structural roles)
        repotoire ml generate-embeddings --return-factor 2.0 --in-out-factor 0.5

**Arguments:**

- `REPO_PATH` (optional) - The repo path

**Options:**

| Option | Description |
|--------|-------------|
| `--type` `[node2vec]` (default: node2vec) | Embedding algorithm (default: node2vec) |
| `--dimension` `INTEGER` (default: 128) | Embedding dimension (default: 128) |
| `--walk-length` `INTEGER` (default: 80) | Random walk length (default: 80) |
| `--walks-per-node` `INTEGER` (default: 10) | Number of walks per node (default: 10) |
| `--return-factor` `FLOAT` (default: 1.0) | Return factor p - controls BFS vs DFS behavior (default: 1.0) |
| `--in-out-factor` `FLOAT` (default: 1.0) | In-out factor q - controls explore vs exploit (default: 1.0) |
| `--node-types` `TEXT` (default: Function,Class,Module) | Comma-separated node types to include (default: Function,Class,Module) |
| `--relationship-types` `TEXT` (default: CALLS,IMPORTS,USES) | Comma-separated relationship types (default: CALLS,IMPORTS,USES) |

---

### `repotoire ml label`

Interactive labeling with active learning.

Presents uncertain samples for human review to improve label quality.
    Uses uncertainty sampling to prioritize samples where the model is
    least confident.

Examples:

# Basic interactive labeling
        repotoire ml label training_data.json

# Multiple iterations with more samples
        repotoire ml label data.json --iterations 3 --samples 30

# Continue from previous session
        repotoire ml label data.json --import-labels previous_labels.json

**Arguments:**

- `DATASET_PATH` - The dataset path

**Options:**

| Option | Description |
|--------|-------------|
| `--samples` `INTEGER` (default: 20) | Number of samples to label per iteration (default: 20) |
| `--iterations` `INTEGER` (default: 1) | Number of active learning iterations (default: 1) |
| `--show-source, --no-source` | Show function source code during labeling |
| `--export-labels` `PATH` | Export labels to separate file after session |
| `--import-labels` `PATH` | Import previously saved labels before starting |

---

### `repotoire ml merge-datasets`

Merge multiple training datasets into one.

Combines examples from multiple dataset files, optionally deduplicating.

Examples:

repotoire ml merge-datasets combined.json data1.json data2.json data3.json

**Arguments:**

- `OUTPUT_PATH` - The output path
- `DATASET_PATHS` (optional) - The dataset paths

**Options:**

| Option | Description |
|--------|-------------|
| `--deduplicate, --allow-duplicates` | Remove duplicate functions (default: deduplicate) |

---

### `repotoire ml multimodal-predict`

Run multimodal predictions using trained fusion model.

Combines text (semantic) and graph (structural) embeddings for
    enhanced prediction accuracy. Shows modality contribution for
    each prediction.

Prerequisites:
        - Codebase ingested with embeddings
        - Trained multimodal model

Examples:

# Predict all functions
        repotoire ml multimodal-predict -m models/multimodal.pt

# Predict code smells
        repotoire ml multimodal-predict -m model.pt -t smell_detection

# Predict single function with explanation
        repotoire ml multimodal-predict -m model.pt -f mymodule.MyClass.method

# Export results
        repotoire ml multimodal-predict -m model.pt -o predictions.json --top-n 50

**Arguments:**

- `REPO_PATH` (optional) - The repo path

**Options:**

| Option | Description |
|--------|-------------|
| `--model, -m` `PATH` **(required)** | Trained multimodal model path |
| `--task, -t` `[bug_prediction|smell_detection|refactoring_benefit]` (default: bug_prediction) | Prediction task (default: bug_prediction) |
| `--threshold` `FLOAT` (default: 0.7) | Confidence threshold for showing predictions (default: 0.7) |
| `--output, -o` `PATH` | Output JSON file for predictions |
| `--top-n` `INTEGER` (default: 20) | Show top N predictions (default: 20) |
| `--function, -f` `TEXT` | Predict for a single function by qualified name |

---

### `repotoire ml predict-bugs`

Predict bug-prone functions using trained model.

Uses a trained bug prediction model to identify functions with high
    probability of containing bugs based on structural patterns and metrics.

Examples:

# Predict all functions
        repotoire ml predict-bugs -m models/bug_predictor.pkl

# Export results to JSON
        repotoire ml predict-bugs -m model.pkl -o predictions.json

# Show more results
        repotoire ml predict-bugs -m model.pkl --top-n 50

# Predict single function
        repotoire ml predict-bugs -m model.pkl -f mymodule.MyClass.risky_method

**Arguments:**

- `REPO_PATH` (optional) - The repo path

**Options:**

| Option | Description |
|--------|-------------|
| `--model, -m` `PATH` **(required)** | Path to trained model file |
| `--threshold` `FLOAT` (default: 0.7) | Risk threshold for flagging (0.0-1.0, default: 0.7) |
| `--output, -o` `PATH` | Output JSON file for predictions |
| `--top-n` `INTEGER` (default: 20) | Show top N risky functions (default: 20) |
| `--function, -f` `TEXT` | Predict for a single function by qualified name |

---

### `repotoire ml prepare-multimodal-data`

Prepare multi-task training data for multimodal fusion.

Fetches text and graph embeddings from Neo4j and combines them
    with labels for multi-task learning.

Label JSON format:
        [{"qualified_name": "module.Class.method", "label": "buggy"}, ...]

Label values:
        - bug_prediction: "clean" or "buggy"
        - smell_detection: "none", "long_method", "god_class", "feature_envy", "data_clump"
        - refactoring_benefit: "low", "medium", "high"

Prerequisites:
        - Run 'repotoire ingest --generate-embeddings' for text embeddings
        - Run 'repotoire ml generate-embeddings' for graph embeddings

Examples:

# Prepare with bug labels only
        repotoire ml prepare-multimodal-data --bug-labels bugs.json -o train_data.pkl

# Prepare with all label types
        repotoire ml prepare-multimodal-data \
            --bug-labels bugs.json \
            --smell-labels smells.json \
            --refactor-labels refactor.json \
            -o train_data.pkl

**Options:**

| Option | Description |
|--------|-------------|
| `--bug-labels` `PATH` | Bug labels JSON file |
| `--smell-labels` `PATH` | Code smell labels JSON file |
| `--refactor-labels` `PATH` | Refactoring benefit labels JSON file |
| `--output, -o` `TEXT` **(required)** | Output pickle file for prepared data |
| `--test-split` `FLOAT` (default: 0.2) | Fraction of data for validation (default: 0.2) |

---

### `repotoire ml train-bug-predictor`

Train bug prediction model on labeled training data.

Trains a RandomForest classifier using Node2Vec embeddings combined
    with code metrics (complexity, LOC, coupling) to predict bug probability.

Prerequisites:
    - Training data extracted with 'repotoire ml extract-training-data'
    - Node2Vec embeddings generated with 'repotoire ml generate-embeddings'

Examples:

# Basic training
        repotoire ml train-bug-predictor -d training_data.json

# With hyperparameter search
        repotoire ml train-bug-predictor -d data.json --grid-search -o models/tuned.pkl

# Custom parameters
        repotoire ml train-bug-predictor -d data.json --n-estimators 200 --max-depth 15

**Options:**

| Option | Description |
|--------|-------------|
| `--training-data, -d` `PATH` **(required)** | Path to training data JSON file |
| `--output, -o` `TEXT` (default: models/bug_predictor.pkl) | Output path for trained model (default: models/bug_predictor.pkl) |
| `--test-split` `FLOAT` (default: 0.2) | Fraction of data for testing (default: 0.2) |
| `--cv-folds` `INTEGER` (default: 5) | Number of cross-validation folds (default: 5) |
| `--grid-search, --no-grid-search` | Run hyperparameter tuning with GridSearchCV |
| `--n-estimators` `INTEGER` (default: 100) | Number of trees in RandomForest (default: 100) |
| `--max-depth` `INTEGER` (default: 10) | Maximum tree depth (default: 10) |

---

### `repotoire ml train-graphsage`

Train GraphSAGE for cross-project defect prediction.

Trains a GraphSAGE model on labeled data from multiple projects.
    The model learns aggregation functions that generalize to any
    new codebase (zero-shot inference).

Prerequisites:
    - Training labels from 'repotoire ml extract-multi-project-labels'
    - Each project's codebase ingested with embeddings

Examples:

# Basic training
        repotoire ml train-graphsage -d combined_labels.json

# With held-out project for zero-shot evaluation
        repotoire ml train-graphsage -d labels.json --holdout-project flask

# Custom hyperparameters
        repotoire ml train-graphsage -d labels.json \
            --hidden-dim 256 --num-layers 3 --epochs 200

**Options:**

| Option | Description |
|--------|-------------|
| `--training-data, -d` `PATH` **(required)** | Training data JSON file from extract-multi-project-labels |
| `--hidden-dim` `INTEGER` (default: 128) | Hidden layer dimension (default: 128) |
| `--num-layers` `INTEGER` (default: 2) | Number of GraphSAGE layers (default: 2) |
| `--batch-size` `INTEGER` (default: 128) | Mini-batch size (default: 128) |
| `--epochs` `INTEGER` (default: 100) | Maximum training epochs (default: 100) |
| `--learning-rate` `FLOAT` (default: 0.001) | Initial learning rate (default: 0.001) |
| `--holdout-project` `TEXT` | Project to hold out for cross-project testing |
| `--output, -o` `TEXT` (default: models/graphsage_universal.pt) | Output model path (default: models/graphsage_universal.pt) |

---

### `repotoire ml train-multimodal`

Train multimodal fusion model for multi-task prediction.

Uses attention-based fusion to combine text (semantic) and graph
    (structural) embeddings for bug prediction, smell detection, and
    refactoring benefit estimation.

The model uses:
    - Cross-modal attention between text and graph modalities
    - Gated fusion with learned modality importance
    - Multi-task learning with uncertainty weighting

Examples:

# Train on all tasks
        repotoire ml train-multimodal -d train_data.pkl

# Train only bug prediction
        repotoire ml train-multimodal -d train_data.pkl -t bug_prediction

# Custom hyperparameters
        repotoire ml train-multimodal -d train_data.pkl \
            --epochs 100 --batch-size 128 --learning-rate 0.0005

**Options:**

| Option | Description |
|--------|-------------|
| `--training-data, -d` `PATH` **(required)** | Training data pickle file from prepare-multimodal-data |
| `--tasks, -t` `TEXT` (default: ['bug_prediction', 'smell_detection', 'refactoring_benefit']) | Tasks to train (can specify multiple) |
| `--epochs` `INTEGER` (default: 50) | Maximum training epochs (default: 50) |
| `--batch-size` `INTEGER` (default: 64) | Training batch size (default: 64) |
| `--learning-rate` `FLOAT` (default: 0.001) | Initial learning rate (default: 0.001) |
| `--output, -o` `TEXT` (default: models/multimodal.pt) | Output model path (default: models/multimodal.pt) |

---

### `repotoire ml training-stats`

Display statistics for a training dataset.

Shows label distribution, complexity metrics, and coverage information.

Examples:

repotoire ml training-stats training_data.json
        repotoire ml training-stats data.json --detailed

**Arguments:**

- `DATASET_PATH` - The dataset path

**Options:**

| Option | Description |
|--------|-------------|
| `--detailed, --summary` | Show detailed per-file statistics |

---

### `repotoire ml validate-dataset`

Validate training dataset for quality issues.

Checks for duplicates, label imbalance, and data quality issues.

Examples:

repotoire ml validate-dataset training_data.json
        repotoire ml validate-dataset data.json --fix

**Arguments:**

- `DATASET_PATH` - The dataset path

**Options:**

| Option | Description |
|--------|-------------|
| `--check-duplicates, --no-check-duplicates` | Check for duplicate function names |
| `--check-balance, --no-check-balance` | Check label balance |
| `--fix, --no-fix` | Attempt to fix issues (removes duplicates, rebalances) |

---

### `repotoire ml zero-shot-predict`

Apply pre-trained GraphSAGE to new codebase (zero-shot).

Uses a model trained on other projects to predict defect risk
    in a completely new codebase - no project-specific training needed!

Prerequisites:
    - Codebase ingested with 'repotoire ingest --generate-embeddings'
    - Pre-trained GraphSAGE model

Examples:

# Basic zero-shot prediction
        repotoire ml zero-shot-predict -m models/graphsage_universal.pt

# Export results
        repotoire ml zero-shot-predict -m model.pt -o predictions.json

# Higher threshold for fewer, higher-confidence predictions
        repotoire ml zero-shot-predict -m model.pt --threshold 0.7

**Arguments:**

- `REPO_PATH` (optional) - The repo path

**Options:**

| Option | Description |
|--------|-------------|
| `--model, -m` `PATH` **(required)** | Pre-trained GraphSAGE model path |
| `--threshold` `FLOAT` (default: 0.5) | Risk threshold for flagging (0.0-1.0, default: 0.5) |
| `--output, -o` `PATH` | Output JSON file for predictions |
| `--top-n` `INTEGER` (default: 20) | Show top N risky functions (default: 20) |

---

## `repotoire monorepo`

Monorepo analysis and optimization.

---

### `repotoire monorepo affected`

Detect packages affected by code changes.

Uses git to find changed files and dependency graph to determine
    which packages need to be tested/rebuilt.

Example:
        repotoire monorepo affected /path/to/monorepo --since main
        repotoire monorepo affected /path/to/monorepo --since HEAD~5 --show-commands

**Arguments:**

- `REPOSITORY_PATH` - The repository path

**Options:**

| Option | Description |
|--------|-------------|
| `--since, -s` `TEXT` (default: origin/main) | Git reference to compare against (default: origin/main) |
| `--max-depth, -d` `INTEGER` (default: 10) | Maximum dependency traversal depth (default: 10) |
| `--show-commands, -c` | Show build/test commands for affected packages |
| `--tool, -t` `[auto|nx|turborepo|lerna]` (default: auto) | Monorepo tool to generate commands for (default: auto-detect) |
| `--output, -o` `PATH` | Output file for results (JSON format) |

---

### `repotoire monorepo analyze`

Analyze monorepo packages with per-package health scores.

Provides detailed health analysis for each package in the monorepo,
    including coupling scores, independence metrics, and affected packages.

Example:
        repotoire monorepo analyze /path/to/monorepo
        repotoire monorepo analyze /path/to/monorepo --package packages/auth

**Arguments:**

- `REPOSITORY_PATH` - The repository path

**Options:**

| Option | Description |
|--------|-------------|
| `--package, -p` `TEXT` | Analyze specific package (path or name) |
| `--neo4j-uri` `TEXT` (default: bolt://localhost:7687) | Neo4j connection URI |
| `--neo4j-password` `TEXT` **(required)** | Neo4j password |
| `--output, -o` `PATH` | Output file for results (JSON format) |

**Environment Variables:**

- `REPOTOIRE_NEO4J_URI` - Neo4j connection URI
- `REPOTOIRE_NEO4J_PASSWORD` - Neo4j password

---

### `repotoire monorepo cross-package`

Analyze cross-package issues.

Detects problems spanning multiple packages:
    - Circular dependencies between packages
    - Excessive package coupling
    - Package boundary violations
    - Inconsistent dependency versions

Example:
        repotoire monorepo cross-package /path/to/monorepo
        repotoire monorepo cross-package /path/to/monorepo --output issues.json

**Arguments:**

- `REPOSITORY_PATH` - The repository path

**Options:**

| Option | Description |
|--------|-------------|
| `--output, -o` `PATH` | Output file for findings (JSON format) |

---

### `repotoire monorepo deps`

Show package dependency graph.

Displays dependencies between packages in the monorepo.

Example:
        repotoire monorepo deps /path/to/monorepo --visualize
        repotoire monorepo deps /path/to/monorepo --output deps.json

**Arguments:**

- `REPOSITORY_PATH` - The repository path

**Options:**

| Option | Description |
|--------|-------------|
| `--visualize, -v` | Visualize dependency graph as tree |
| `--output, -o` `PATH` | Output file for dependency graph (JSON format) |

---

### `repotoire monorepo detect-packages`

Detect packages in a monorepo.

Scans for package.json, pyproject.toml, BUILD files, etc. to identify
    all packages in the monorepo.

Example:
        repotoire monorepo detect-packages /path/to/monorepo
        repotoire monorepo detect-packages /path/to/monorepo --output packages.json

**Arguments:**

- `REPOSITORY_PATH` - The repository path

**Options:**

| Option | Description |
|--------|-------------|
| `--output, -o` `PATH` | Output file for package list (JSON format) |

---

## `repotoire rule`

Manage custom code quality rules (REPO-125).

Rules are stored as graph nodes with time-based priority refresh.
    Frequently-used rules automatically bubble to the top for RAG context.

Examples:
        repotoire rule list                    # List all rules
        repotoire rule add rules.yaml          # Add rules from file
        repotoire rule test no-god-classes     # Dry-run a rule
        repotoire rule stats                   # Show rule statistics

---

### `repotoire rule add`

Add rules from a YAML file.

The YAML file should contain a list of rules with the following structure:


    rules:
      - id: no-god-classes
        name: "Classes should have fewer than 20 methods"
        description: "Large classes violate SRP"
        pattern: |
          MATCH (c:Class)-[:CONTAINS]->(m:Function)
          WITH c, count(m) as method_count
          WHERE method_count > 20
          RETURN c.qualifiedName as class_name, method_count
        severity: HIGH
        userPriority: 100
        tags: [complexity, architecture]
        autoFix: "Split into smaller classes"

**Arguments:**

- `FILE_PATH` - The file path

**Options:**

| Option | Description |
|--------|-------------|
| `--neo4j-uri` `TEXT` | Neo4j connection URI (overrides config) |
| `--neo4j-password` `TEXT` | Neo4j password (overrides config) |

---

### `repotoire rule daemon-refresh`

Force immediate priority refresh for all rules.

This command runs the daemon's refresh cycle once:
    - Decays stale rules (not used in >N days)
    - Optionally archives very old rules (>90 days)
    - Shows statistics

Examples:
        # Standard refresh (decay after 7 days)
        repotoire rule daemon-refresh

# Aggressive decay (after 3 days, reduce by 20%)
        repotoire rule daemon-refresh --decay-threshold 3 --decay-factor 0.8

# Archive very old rules
        repotoire rule daemon-refresh --auto-archive

**Options:**

| Option | Description |
|--------|-------------|
| `--decay-threshold` `INTEGER` (default: 7) | Days before decaying stale rules (default: 7) |
| `--decay-factor` `FLOAT` (default: 0.9) | Priority decay multiplier (default: 0.9) |
| `--auto-archive` | Archive rules unused for >90 days |
| `--neo4j-uri` `TEXT` | Neo4j connection URI (overrides config) |
| `--neo4j-password` `TEXT` | Neo4j password (overrides config) |

---

### `repotoire rule delete`

Delete a rule.

**Arguments:**

- `RULE_ID` - The rule id

**Options:**

| Option | Description |
|--------|-------------|
| `--yes` | Confirm the action without prompting. |
| `--neo4j-uri` `TEXT` | Neo4j connection URI (overrides config) |
| `--neo4j-password` `TEXT` | Neo4j password (overrides config) |

---

### `repotoire rule edit`

Edit an existing rule.

**Arguments:**

- `RULE_ID` - The rule id

**Options:**

| Option | Description |
|--------|-------------|
| `--name` `TEXT` | Update rule name |
| `--priority` `INTEGER` | Update user priority (0-1000) |
| `--enable, --disable` | Enable or disable rule |
| `--neo4j-uri` `TEXT` | Neo4j connection URI (overrides config) |
| `--neo4j-password` `TEXT` | Neo4j password (overrides config) |

---

### `repotoire rule list`

List all custom rules with priority scores.

**Options:**

| Option | Description |
|--------|-------------|
| `--enabled-only` | Only show enabled rules |
| `--tags` `TEXT` | Filter by tags |
| `--sort-by` `[priority|name|last-used]` (default: priority) | Sort order |
| `--limit` `INTEGER` | Maximum rules to show |
| `--neo4j-uri` `TEXT` | Neo4j connection URI (overrides config) |
| `--neo4j-password` `TEXT` | Neo4j password (overrides config) |

---

### `repotoire rule stats`

Show rule usage statistics.

**Options:**

| Option | Description |
|--------|-------------|
| `--neo4j-uri` `TEXT` | Neo4j connection URI (overrides config) |
| `--neo4j-password` `TEXT` | Neo4j password (overrides config) |

---

### `repotoire rule test`

Test a rule (dry-run) to see what it would find.

**Arguments:**

- `RULE_ID` - The rule id

**Options:**

| Option | Description |
|--------|-------------|
| `--neo4j-uri` `TEXT` | Neo4j connection URI (overrides config) |
| `--neo4j-password` `TEXT` | Neo4j password (overrides config) |

---

## `repotoire sandbox-stats`

Show sandbox execution metrics and cost statistics.

Displays comprehensive statistics about E2B sandbox operations including
    cost breakdown, success rates, and operational health metrics.

Examples:

# Show summary for last 30 days
        repotoire sandbox-stats

# Show last 7 days with breakdown by operation type
        repotoire sandbox-stats --period 7 --by-type

# Show slow operations
        repotoire sandbox-stats --slow

# Show recent failures
        repotoire sandbox-stats --failures

# Admin: Show top 10 customers by cost
        repotoire sandbox-stats --top-customers 10

**Options:**

| Option | Description |
|--------|-------------|
| `--period, -p` `INTEGER` (default: 30) | Number of days to look back (default: 30) |
| `--customer-id, -c` `TEXT` | Filter by customer ID (admin only) |
| `--by-type` | Show breakdown by operation type |
| `--slow` | Show slow operations (>10s) |
| `--failures` | Show recent failures |
| `--top-customers` `INTEGER` (default: 0) | Show top N customers by cost (admin only) |
| `--json-output` | Output as JSON |

---

## `repotoire scan-secrets`

Scan files for secrets (API keys, passwords, tokens, etc.).

REPO-149: Standalone secrets scanning with enhanced reporting.

Examples:
        # Scan current directory
        repotoire scan-secrets .

# Scan with JSON output
        repotoire scan-secrets . --format json -o secrets.json

# Scan only Python files, critical and high risk
        repotoire scan-secrets . -p "**/*.py" --min-risk high

# Scan with more workers
        repotoire scan-secrets . --workers 8

**Arguments:**

- `PATH` - The path

**Options:**

| Option | Description |
|--------|-------------|
| `--output, -o` `PATH` | Output file for results (JSON format) |
| `--format, -f` `[table|json|sarif]` (default: table) | Output format (default: table) |
| `--parallel, --no-parallel` | Use parallel scanning for multiple files (default: enabled) |
| `--workers, -w` `INTEGER` (default: 4) | Number of parallel workers (default: 4) |
| `--min-risk` `[critical|high|medium|low]` | Minimum risk level to report (default: all) |
| `--pattern, -p` `TEXT` | File patterns to scan (e.g., '**/*.py', '**/*.env') |

---

## `repotoire schema`

Manage and inspect graph schema.

Tools for exploring the Neo4j graph structure, validating integrity,
    and debugging without opening Neo4j Browser.

Examples:
        falkor schema inspect           # Show graph statistics
        falkor schema visualize         # ASCII art graph structure
        falkor schema sample Class --limit 3  # Sample Class nodes
        falkor schema validate          # Check schema integrity

---

### `repotoire schema inspect`

Show graph statistics and schema overview.

**Options:**

| Option | Description |
|--------|-------------|
| `--neo4j-uri` `TEXT` | Neo4j connection URI (overrides config) |
| `--neo4j-user` `TEXT` | Neo4j username (overrides config) |
| `--neo4j-password` `TEXT` | Neo4j password (overrides config) |
| `--format` `[table|json]` (default: table) | Output format |

---

### `repotoire schema sample`

Show sample nodes of a specific type.

NODE_TYPE: The node label to sample (e.g., Class, Function, File)

**Arguments:**

- `NODE_TYPE` - The node type

**Options:**

| Option | Description |
|--------|-------------|
| `--limit` `INTEGER` (default: 3) | Number of samples to show |
| `--neo4j-uri` `TEXT` | Neo4j connection URI (overrides config) |
| `--neo4j-user` `TEXT` | Neo4j username (overrides config) |
| `--neo4j-password` `TEXT` | Neo4j password (overrides config) |

---

### `repotoire schema validate`

Validate graph schema integrity.

**Options:**

| Option | Description |
|--------|-------------|
| `--neo4j-uri` `TEXT` | Neo4j connection URI (overrides config) |
| `--neo4j-user` `TEXT` | Neo4j username (overrides config) |
| `--neo4j-password` `TEXT` | Neo4j password (overrides config) |

---

### `repotoire schema visualize`

Visualize graph schema structure with ASCII art.

**Options:**

| Option | Description |
|--------|-------------|
| `--neo4j-uri` `TEXT` | Neo4j connection URI (overrides config) |
| `--neo4j-user` `TEXT` | Neo4j username (overrides config) |
| `--neo4j-password` `TEXT` | Neo4j password (overrides config) |

---

## `repotoire security`

Security scanning and compliance reporting.

---

### `repotoire security audit`

Run comprehensive security audit.

Performs:
    - Dependency vulnerability scan
    - SBOM generation
    - Compliance report (SOC 2)

Example:
        repotoire security audit /path/to/repo
        repotoire security audit /path/to/repo --output-dir ./security-reports

**Arguments:**

- `REPOSITORY_PATH` - The repository path

**Options:**

| Option | Description |
|--------|-------------|
| `--neo4j-uri` `TEXT` (default: bolt://localhost:7687) | Neo4j connection URI |
| `--neo4j-password` `TEXT` **(required)** | Neo4j password |
| `--output-dir, -o` `PATH` | Output directory for all reports |

**Environment Variables:**

- `REPOTOIRE_NEO4J_URI` - Neo4j connection URI
- `REPOTOIRE_NEO4J_PASSWORD` - Neo4j password

---

### `repotoire security compliance-report`

Generate compliance report for security frameworks.

Maps security findings to compliance requirements and generates
    audit-ready reports for SOC 2, ISO 27001, PCI DSS, etc.

Example:
        repotoire security compliance-report /path/to/repo --framework soc2
        repotoire security compliance-report /path/to/repo -f pci_dss --markdown report.md

**Arguments:**

- `REPOSITORY_PATH` - The repository path

**Options:**

| Option | Description |
|--------|-------------|
| `--framework, -f` `[soc2|iso27001|pci_dss|nist_csf|cis]` (default: soc2) | Compliance framework (default: soc2) |
| `--output, -o` `PATH` | Output file path (JSON format) |
| `--markdown, -md` `PATH` | Generate markdown report |
| `--neo4j-uri` `TEXT` (default: bolt://localhost:7687) | Neo4j connection URI |
| `--neo4j-password` `TEXT` **(required)** | Neo4j password |

**Environment Variables:**

- `REPOTOIRE_NEO4J_URI` - Neo4j connection URI
- `REPOTOIRE_NEO4J_PASSWORD` - Neo4j password

---

### `repotoire security generate-sbom`

Generate Software Bill of Materials (SBOM).

Creates CycloneDX format SBOM for dependency tracking,
    compliance, and supply chain security.

Example:
        repotoire security generate-sbom /path/to/repo
        repotoire security generate-sbom /path/to/repo --format xml --output sbom.xml

**Arguments:**

- `REPOSITORY_PATH` - The repository path

**Options:**

| Option | Description |
|--------|-------------|
| `--format, -f` `[json|xml]` (default: json) | SBOM format (default: json) |
| `--output, -o` `PATH` | Output file path (default: sbom.{format} in repository) |
| `--requirements, -r` `TEXT` (default: requirements.txt) | Requirements file (default: requirements.txt) |

---

### `repotoire security scan-deps`

Scan dependencies for known vulnerabilities.

Uses pip-audit to check dependencies against OSV database
    for known CVEs and security vulnerabilities.

Example:
        repotoire security scan-deps /path/to/repo
        repotoire security scan-deps /path/to/repo --requirements requirements-dev.txt

**Arguments:**

- `REPOSITORY_PATH` - The repository path

**Options:**

| Option | Description |
|--------|-------------|
| `--requirements, -r` `TEXT` (default: requirements.txt) | Requirements file to scan (default: requirements.txt) |
| `--max-findings, -m` `INTEGER` (default: 100) | Maximum findings to report (default: 100) |
| `--neo4j-uri` `TEXT` (default: bolt://localhost:7687) | Neo4j connection URI |
| `--neo4j-password` `TEXT` **(required)** | Neo4j password (or set REPOTOIRE_NEO4J_PASSWORD) |
| `--output, -o` `PATH` | Output file for findings (JSON format) |

**Environment Variables:**

- `REPOTOIRE_NEO4J_URI` - Neo4j connection URI
- `REPOTOIRE_NEO4J_PASSWORD` - Neo4j password (or set REPOTOIRE_NEO4J_PASSWORD)

---

## `repotoire show-config`

Display effective configuration from all sources.

Shows the final configuration after applying the priority chain:
    1. Command-line arguments (highest priority)
    2. Environment variables (FALKOR_*)
    3. Config file (.reporc, falkor.toml)
    4. Built-in defaults (lowest priority)

Use --format to control output format:
    - table: Pretty-printed table (default)
    - json: JSON format
    - yaml: YAML format (requires PyYAML)

**Options:**

| Option | Description |
|--------|-------------|
| `--format, -f` `[yaml|json|table]` (default: table) | Output format (default: table) |

---

## `repotoire style`

Analyze codebase style conventions.

Detects naming conventions, docstring styles, line lengths, and other
    code style patterns from your Python codebase. Results can be used to
    guide AI-powered code generation to match your existing style.

Examples:
        # Analyze style in current directory
        repotoire style .

# Analyze with more files for better accuracy
        repotoire style /path/to/repo --max-files 1000

# Show generated LLM instructions
        repotoire style /path/to/repo --instructions

# Output as JSON for automation
        repotoire style /path/to/repo --json

**Arguments:**

- `REPOSITORY` - The repository

**Options:**

| Option | Description |
|--------|-------------|
| `--max-files` `INTEGER` (default: 500) | Maximum Python files to analyze |
| `--confidence-threshold` `FLOAT` (default: 0.6) | Minimum confidence for including rules (0.0-1.0) |
| `--json` | Output as JSON |
| `--instructions` | Show generated LLM instructions |

---

## `repotoire templates`

Manage fix templates for automatic code fixes.

Templates provide fast, deterministic code fixes that don't require LLM calls.
    They are loaded from YAML files in .repotoire/fix-templates/ or ~/.config/repotoire/fix-templates/.

Examples:
        repotoire templates list           # List all loaded templates
        repotoire templates list --verbose # Show detailed template info

---

### `repotoire templates list`

List all loaded fix templates.

Shows templates loaded from default directories and any additional directories.
    Templates are sorted by priority (highest first).

**Options:**

| Option | Description |
|--------|-------------|
| `--verbose, -v` | Show detailed template information |
| `--language, -l` `TEXT` | Filter by language (e.g., python, typescript) |
| `--template-dir` `PATH` | Additional template directory to load from |

---

## `repotoire validate`

Validate configuration and connectivity without running operations.

Checks:
    - Configuration file validity (if present)
    - Neo4j connection URI format
    - Neo4j credentials
    - Neo4j connectivity (database is reachable)
    - All required settings are present

Exits with non-zero code if any validation fails.

**Options:**

| Option | Description |
|--------|-------------|
| `--neo4j-uri` `TEXT` | Neo4j connection URI (overrides config) |
| `--neo4j-user` `TEXT` | Neo4j username (overrides config) |
| `--neo4j-password` `TEXT` | Neo4j password (overrides config, prompts if not provided) |

---

## Environment Variables Reference

| Variable | Description |
|----------|-------------|
| `REPOTOIRE_NEO4J_URI` | Neo4j connection URI (e.g., bolt://localhost:7687) |
| `REPOTOIRE_NEO4J_PASSWORD` | Neo4j password |
| `REPOTOIRE_NEO4J_USER` | Neo4j username (default: neo4j) |
| `REPOTOIRE_DB_TYPE` | Database type (neo4j or falkordb) |
| `REPOTOIRE_TIMESCALE_URI` | TimescaleDB connection string |
| `REPOTOIRE_OFFLINE` | Run in offline mode (skip auth) |
| `OPENAI_API_KEY` | OpenAI API key for embeddings/RAG |
| `VOYAGE_API_KEY` | Voyage AI API key for code embeddings |
| `DEEPINFRA_API_KEY` | DeepInfra API key for embeddings |
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude |
| `E2B_API_KEY` | E2B API key for sandbox execution |

## Configuration File

Repotoire looks for configuration in these locations (in order):

1. Path specified with `--config`
2. `.reporc` in current directory
3. `falkor.toml` in current directory
4. `~/.config/repotoire/config.toml`

Example configuration:

```toml
[neo4j]
uri = "bolt://localhost:7687"
user = "neo4j"
password = "${NEO4J_PASSWORD}"  # Environment variable interpolation

[ingestion]
patterns = ["**/*.py", "**/*.js", "**/*.ts"]
batch_size = 100
max_file_size_mb = 10

[embeddings]
backend = "auto"

[logging]
level = "INFO"
format = "human"
```
