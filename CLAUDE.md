# CLAUDE.md

This file provides essential guidance to Claude Code (claude.ai/code) and developers working with the Repotoire codebase.

## Project Overview

Repotoire is a graph-powered code health platform that analyzes codebases using knowledge graphs to detect code smells, architectural issues, and technical debt. Unlike traditional linters that examine files in isolation, Repotoire builds a **petgraph in-memory graph with redb persistence** combining:
- **Structural analysis** (tree-sitter AST parsing across 9 languages)
- **Relational patterns** (graph algorithms via petgraph)

This multi-layered approach enables detection of complex issues that traditional tools miss, such as circular dependencies, architectural bottlenecks, and modularity problems. All 99 detectors are pure Rust — no external tool dependencies.

## Development Rules

- **Build with**: `cargo build` (debug) or `cargo build --release` (optimized)
- **Run tests with**: `cargo test`
- **Run the CLI with**: `cargo run -- <command>` or the installed `repotoire` binary
- **Check compilation with**: `cargo check`
- **Format code with**: `cargo fmt`
- **Lint with**: `cargo clippy`

## Development Setup

### Installation

```bash
# Build from source
cd repotoire-cli
cargo build --release

# Install locally
cargo install --path repotoire-cli

# Or install via cargo-binstall (prebuilt binaries)
cargo binstall repotoire
```

### Common Commands

```bash
# Analyze codebase health
repotoire analyze /path/to/repo

# Analyze with specific output format
repotoire analyze /path/to/repo --format html --output report.html
repotoire analyze /path/to/repo --format sarif --output results.sarif.json

# Initialize config file
repotoire init

# Query the code graph
repotoire graph /path/to/repo

# Show graph statistics
repotoire stats /path/to/repo

# View findings from last analysis
repotoire findings /path/to/repo

# Generate a fix for a finding
repotoire fix /path/to/repo

# Check environment setup
repotoire doctor

# Watch for changes and re-analyze
repotoire watch /path/to/repo

# Calibrate adaptive thresholds
repotoire calibrate /path/to/repo

# Clean cached analysis data
repotoire clean /path/to/repo

# Start MCP server
repotoire serve
```

### CLI Commands (16 total)

| Command | Description |
|---------|-------------|
| `analyze` | Analyze codebase for issues |
| `findings` | View findings from last analysis |
| `fix` | Generate fix for a finding (AI-powered or rule-based) |
| `graph` | Query the code graph directly |
| `stats` | Show graph statistics |
| `status` | Show analysis status |
| `init` | Initialize repository config |
| `doctor` | Check environment setup |
| `watch` | Watch for file changes and re-analyze |
| `calibrate` | Calibrate adaptive thresholds from your codebase |
| `clean` | Remove cached analysis data |
| `version` | Show version info |
| `serve` | Start MCP server for AI assistant integration |
| `config` | Manage configuration (init, show, set) |
| `feedback` | Label findings as true/false positives |
| `train` | Train the classifier on labeled data |

### Global Flags

- `--path` (default: `.`) — Path to repository
- `--log-level` (default: `info`) — Log level: error, warn, info, debug, trace
- `--workers` (default: `8`) — Number of parallel workers (1-64)

### MCP Server (Claude Code Integration)

Repotoire provides an MCP server (built on the rmcp SDK, protocol version MCP 2025-06-18) for use with Claude Code, Cursor, and other MCP-compatible AI assistants. The server follows an **Open Core** model:

| Tier | Features | Requirements |
|------|----------|--------------|
| **Free** | Analysis, graph queries, architecture, hotspots, evolution | Local CLI only |
| **Pro** | Semantic search, RAG Q&A | `REPOTOIRE_API_KEY` |
| **AI/BYOK** | AI-powered fix generation | Any LLM API key |

**Start the MCP server:**
```bash
# Default: stdio transport
repotoire serve

# Streamable HTTP transport on a custom port
repotoire serve --http-port 8080
```

**Configure in Claude Code** (`~/.claude.json`):
```json
{
  "mcpServers": {
    "repotoire": {
      "type": "stdio",
      "command": "repotoire",
      "args": ["serve"],
      "env": {
        "REPOTOIRE_API_KEY": "${REPOTOIRE_API_KEY}"
      }
    }
  }
}
```

**Available tools (13):**

| Tool | Tier | Description |
|------|------|-------------|
| `repotoire_analyze` | FREE | Run code analysis, return findings by severity |
| `repotoire_get_findings` | FREE | Get findings with filtering and pagination |
| `repotoire_get_hotspots` | FREE | Get files ranked by issue density |
| `repotoire_query_graph` | FREE | Query code entities (functions, classes, files, callers, callees) |
| `repotoire_trace_dependencies` | FREE | Multi-hop graph traversal (call chains, imports, inheritance) |
| `repotoire_analyze_impact` | FREE | Change impact analysis (what breaks if I modify X?) |
| `repotoire_get_file` | FREE | Read file content with line range |
| `repotoire_get_architecture` | FREE | Codebase structure overview |
| `repotoire_list_detectors` | FREE | List available detectors |
| `repotoire_query_evolution` | FREE | Git history queries (churn, blame, commits, ownership) |
| `repotoire_search_code` | PRO | Semantic code search with embeddings |
| `repotoire_ask` | PRO | RAG-powered Q&A about the codebase |
| `repotoire_generate_fix` | AI/BYOK | AI-powered fix generation |

See [repotoire-cli/docs/MCP.md](repotoire-cli/docs/MCP.md) for complete documentation.

## Architecture

### Core Pipeline Flow

```
Codebase → Parsers (tree-sitter) → Entities + Relationships → petgraph Graph → Detectors (rayon parallel) → Scoring → Reports
```

### System Components

**Graph Schema**: Nodes (`File`, `Function`, `Class`, `Module`, `Variable`, `Commit`), Relationships (`Calls`, `Imports`, `Contains`, `Inherits`, `Uses`, `ModifiedIn`), qualified names as unique keys

**Core Modules**:

1. **Parsers** (`repotoire-cli/src/parsers/`): 9 tree-sitter parsers — Python, TypeScript/JavaScript (with TSX), Rust, Go, Java, C#, C, C++, plus a lightweight fallback parser. Cross-language nesting depth enrichment via brace/indent counting. 2MB file size guardrail. Header file (`.h`) dispatch heuristic for C vs C++.

2. **Graph Layer** (`repotoire-cli/src/graph/`): `GraphStore` — petgraph `DiGraph<CodeNode, CodeEdge>` with redb (embedded ACID database) persistence. String interning via `lasso` (`ThreadedRodeo`) for ~66% memory savings. Compact node types (`CompactNode` at ~32 bytes vs ~200 bytes for `CodeNode`) defined in `interner.rs` for future large-repo support. `GraphQuery` trait (19 methods) for backend-agnostic access. Fan-in/fan-out metrics, Tarjan SCC cycle detection.

3. **Pipeline** (`repotoire-cli/src/cli/analyze/`): Walk files → parse (tree-sitter, parallel via rayon) → batch insert into in-memory graph → run detectors. Streaming/bounded pipeline modes for large repos (20k+ files). Configurable batch sizes.

4. **Detectors** (`repotoire-cli/src/detectors/`): 99 pure Rust detectors across 14 categories. No external tool dependencies — all analysis runs in-process. Detectors run in parallel via rayon. Security detectors use SSA-based intra-function taint analysis via tree-sitter ASTs.

5. **Scoring** (`repotoire-cli/src/scoring/`): Three-pillar scoring — Structure (40%), Quality (30%), Architecture (30%). Density-based penalty normalization (penalties scaled by kLOC). Graph-derived bonuses (modularity, cohesion, clean deps, complexity distribution, test coverage). Compound smell escalation. 13 grade levels (A+ through F). Score floor at 5.0, cap at 99.9 with medium+ findings. Security multiplier (default 3x).

6. **CLI** (`repotoire-cli/src/cli/`): clap 4 with derive, 16 commands. Progress bars via indicatif. Terminal styling via console.

7. **Reporters** (`repotoire-cli/src/reporters/`): 5 output formats — text (default, colored terminal), JSON, HTML (standalone), SARIF 2.1.0 (GitHub Code Scanning compatible), Markdown.

8. **Config** (`repotoire-cli/src/config/`): TOML (`repotoire.toml`), JSON (`.repotoirerc.json`), YAML (`.repotoire.yaml`). Per-detector settings, scoring weights, path exclusions, project type detection (web, library, framework, CLI, etc.). User config at `~/.config/repotoire/config.toml`.

9. **Calibration** (`repotoire-cli/src/calibrate/`): Adaptive threshold system — collects metric distributions from parsed code, builds `StyleProfile` with percentile breakpoints (p50/p75/p90/p95), `ThresholdResolver` with floor/ceiling guardrails. N-gram surprisal model for anomaly detection.

10. **Models** (`repotoire-cli/src/models.rs`): `Finding` (with severity, CWE IDs, confidence, affected files), `Severity` levels (Critical, High, Medium, Low, Info).

### Detector Suite (99 Pure Rust Detectors)

All detectors are built-in Rust with zero external dependencies. Grouped by category:

| Category | Count | Examples |
|----------|-------|---------|
| **Security** | 23 | SQL injection, XSS, SSRF, command injection, path traversal, secrets, insecure crypto, JWT weak, CORS misconfig, NoSQL injection, log injection, XXE, prototype pollution, insecure TLS, cleartext credentials |
| **Code Quality** | 25 | Empty catch, deep nesting, magic numbers, dead store, debug code, commented code, duplicate code, unreachable code, mutable default args, broad exceptions, boolean traps, inconsistent returns |
| **Code Smells** (graph-based) | 11 | God class, feature envy, data clumps, inappropriate intimacy, lazy class, message chain, middle man, refused bequest, dead code, long parameters, circular dependencies |
| **Architecture** (graph-based) | 6 | Architectural bottleneck, degree centrality, influential code, module cohesion, core utility, shotgun surgery |
| **AI-Specific** | 6 | AI boilerplate, AI churn, AI complexity spike, AI duplicate block, AI missing tests, AI naming pattern |
| **ML/Data Science** | 8 | Unsafe torch.load, NaN equality, missing zero_grad, deprecated PyTorch API, chained indexing, missing random seed |
| **Rust-Specific** | 7 | Unwrap without context, unsafe without SAFETY comment, clone in hot path, missing #[must_use], unnecessary Box\<dyn\>, mutex poisoning risk, panic density |
| **Async/Promise** | 4 | Sync-in-async, missing await, unhandled promise, callback hell |
| **Framework-Specific** | 3 | React hooks violations, Django security, Express.js security |
| **Performance** | 3 | N+1 queries, regex in loop, sync-in-async |
| **Testing** | 1 | Test code in production |
| **CI/CD** | 1 | GitHub Actions injection |
| **Dependency Audit** | 1 | Multi-ecosystem vulnerability audit via OSV.dev API |
| **Predictive** | 1 | N-gram surprisal anomaly detection (conditional) |

**Cross-Detector Infrastructure**: Voting engine (multi-detector consensus), health score delta calculator (fix impact estimation), risk analyzer (compound risk assessment), root cause analyzer, incremental cache, content classifier, context HMM, data flow / SSA taint analysis, function/class context inference, framework detection for FP reduction, AST fingerprinting.

**Inline Suppression**: `// repotoire:ignore` (all detectors) or `// repotoire:ignore[detector-name]` (targeted). Supports `#`, `//`, `/*`, `--` comment styles.

## Design Decisions (Key Points)

### Why petgraph + redb?
- **petgraph**: In-memory directed graph with mature algorithm library (SCC, BFS, DFS)
- **redb**: Embedded ACID key-value store — zero network overhead, no external database to manage
- Together they provide fast in-process graph analysis with optional on-disk persistence
- No Docker, no Redis, no connection pooling — just a single binary

### Why Pure Rust Detectors?
- **Zero dependencies**: No Python, Node, or external tools to install
- **Performance**: All analysis runs in-process with rayon parallelism
- **Consistency**: Single binary deployment, same behavior everywhere
- **Depth**: SSA-based taint analysis for security detectors, graph queries for architecture detectors

### Why Qualified Names as IDs?
- Human readable (e.g., `module.Class.method`)
- Globally unique, no collisions
- Fast direct lookups via HashMap

### Why Three-Category Scoring?
- Holistic view: Structure (40%) + Quality (30%) + Architecture (30%)
- Maps to specific, actionable improvements
- Density-normalized: penalties scale by kLOC so project size doesn't bias scores

## Incremental Analysis

Repotoire provides faster re-analysis through a findings-level cache that skips re-running detectors on unchanged files.

### How It Works

1. **SipHash Content Hashing**: Each file's content is hashed with `DefaultHasher` (SipHash) and compared to the cached hash
2. **Findings Cache**: Detector results for unchanged files are loaded from a local JSON cache file (`~/.cache/repotoire/<repo-hash>/incremental/findings_cache.json`)
3. **Auto-Incremental Mode**: When a warm cache exists, incremental mode activates automatically
4. **Binary Version Invalidation**: Cache is automatically invalidated when the Repotoire binary version changes
5. **Cache Schema Versioning**: Internal `CACHE_VERSION` triggers rebuild on schema changes

### What Is and Isn't Cached

- **Cached**: Per-file detector findings, parse results (skip tree-sitter re-parsing), graph-level detector results, health scores
- **Not cached**: The graph itself — petgraph is always rebuilt from scratch on every run
- **Pruning**: Stale entries for deleted files are automatically removed

### Fast Path

When no files have changed, the analyze command can return fully cached scores and findings without running any detectors or rebuilding the graph.

### Key Files

- `repotoire-cli/src/detectors/incremental_cache.rs` — `IncrementalCache` with SipHash, version tracking, pruning
- `repotoire-cli/src/cli/analyze/setup.rs` — Auto-incremental mode detection
- `repotoire-cli/src/cli/analyze/mod.rs` — Fast-path cached result return

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

### Adding a New Language Parser
1. Create `repotoire-cli/src/parsers/{language}.rs`
2. Add the tree-sitter grammar crate to `Cargo.toml`
3. Implement the parser function returning `ParseResult` (entities, relationships, imports)
4. Register the file extension mapping in `repotoire-cli/src/parsers/mod.rs`
5. Add tests as inline `#[test]` modules

### Adding a New Detector
1. Create `repotoire-cli/src/detectors/{detector_name}.rs`
2. Implement the `Detector` trait: `fn name()`, `fn detect(&self, graph, files) -> Vec<Finding>`
3. Register in `repotoire-cli/src/detectors/mod.rs` — add `mod`, `pub use`, and add to `default_detectors_full()`
4. Add tests as inline `#[test]` modules

### Adding a New Report Format
1. Create `repotoire-cli/src/reporters/{format}.rs`
2. Implement the reporter function
3. Add to the `--format` CLI flag choices in `repotoire-cli/src/cli/mod.rs`

## Troubleshooting

**Common Issues**:

- **Tree-sitter grammar errors**: Ensure the correct grammar crate version in `Cargo.toml`. Tree-sitter API versions must match across all grammar crates.
- **Large repository memory**: For repos with 20k+ files, compact node types in `interner.rs` are available for memory reduction (~32 bytes vs ~200 bytes per node). Files >2MB are silently skipped by parsers.
- **Stale cache results**: Run `repotoire clean /path/to/repo` to clear cached data, or the cache auto-invalidates on binary version change.
- **Missing findings**: Check if files are excluded by `.gitignore`, `.repotoireignore`, or built-in exclusions (vendor, node_modules, dist, minified files).

## Testing

**Organization**: Inline `#[test]` modules within source files, plus `repotoire-cli/tests/` for integration tests.

**Commands**:
```bash
# Run all tests
cargo test

# Run tests with output
cargo test -- --nocapture

# Run a specific test
cargo test test_name

# Run tests in a specific module
cargo test detectors::god_class
```

## Current Status

### Implemented
- Pure Rust CLI (93k+ lines) with clap 4
- petgraph in-memory graph with redb persistence
- String interning via lasso for memory efficiency
- 9 tree-sitter language parsers (Python, TypeScript/JavaScript, Rust, Go, Java, C#, C, C++, lightweight fallback)
- 99 pure Rust detectors across 14 categories — zero external tool dependencies
- SSA-based taint analysis for security detectors
- Three-pillar health scoring with density normalization and graph-derived bonuses
- Compound smell escalation (arXiv:2509.03896)
- Adaptive threshold calibration with n-gram surprisal
- Findings-level incremental cache with auto-detection
- 5 report formats (text, JSON, HTML, SARIF 2.1.0, Markdown)
- MCP server (rmcp SDK, stdio + HTTP transports, 13 tools)
- Git history integration via git2 (churn, blame, commits)
- File watching with real-time re-analysis
- Inline suppression (`repotoire:ignore` / `repotoire:ignore[detector]`)
- Cross-detector analysis (voting engine, risk analyzer, root cause analyzer, health delta calculator)
- Project type detection with framework-aware detector thresholds
- `.repotoireignore` support
- Formal verification of scoring algorithms (Lean 4)

### Planned
- Web dashboard
- IDE plugins (VS Code, JetBrains)
- GitHub Actions integration
- Custom rule engine
- Team analytics

## Dependencies

### Core (from Cargo.toml)
- **petgraph** (0.7): In-memory directed graph
- **redb** (2.4): Embedded ACID key-value store for graph persistence
- **clap** (4): CLI framework with derive macros
- **serde** / **serde_json** (1): Serialization
- **rayon** (1.11): Data parallelism for detectors and parsing
- **anyhow** / **thiserror**: Error handling
- **lasso** (0.7.3): Thread-safe string interning

### Parsing
- **tree-sitter** (0.25): Incremental parsing framework
- **tree-sitter-python** (0.25), **tree-sitter-javascript** (0.25), **tree-sitter-typescript** (0.23), **tree-sitter-rust** (0.24), **tree-sitter-go** (0.25), **tree-sitter-java** (0.23), **tree-sitter-c-sharp** (0.23), **tree-sitter-c** (0.24), **tree-sitter-cpp** (0.23)

### MCP Server
- **rmcp** (0.16): MCP protocol SDK (server, macros, stdio + HTTP transport)
- **tokio** (1): Async runtime
- **axum** (0.8): HTTP framework for streamable HTTP transport
- **schemars** (1.0): JSON Schema generation for MCP tool parameters

### Git
- **git2** (0.20): libgit2 bindings for git history analysis

### Terminal UI
- **indicatif** (0.18): Progress bars
- **console** (0.15): Terminal styling
- **ratatui** (0.30): TUI framework
- **crossterm** (0.29): Terminal backend

### Misc
- **regex** (1): Regular expressions
- **ignore** (0.4): .gitignore-aware file walking
- **dashmap** (6): Concurrent hashmap
- **toml** (0.8): TOML config parsing
- **ureq** (3): Sync HTTP client (AI API calls, OSV.dev dependency audit)
- **chrono** (0.4): Date/time
- **memmap2** (0.9): Memory-mapped file access
- **crossbeam-channel** (0.5): Parallel pipeline channels
- **rustc-hash** (2): Fast hashing
- **notify** (8) / **notify-debouncer-full** (0.5): File watching
- **dirs** (6): Platform directory paths

### Development
- **tempfile** (3): Temporary files for tests

## Performance

### Memory Usage
- **In-memory graph**: petgraph holds all nodes and edges in memory. No external database needed.
- **Standard backend**: ~200 bytes per node
- **Compact nodes**: ~32 bytes per node (via string interning with lasso, defined in `interner.rs` for future large-repo support)
- **Parser guardrail**: Files >2MB are silently skipped to prevent memory/time issues

### Parallelism
- **Parsing**: File parsing runs in parallel via rayon
- **Detection**: All detectors run in parallel via rayon
- **Configurable workers**: `--workers` flag (default: 8, max: 64)

## Security Considerations

### MCP Path Traversal Protection
- The MCP `get_file` handler (`repotoire-cli/src/mcp/tools/files.rs`) validates that requested file paths stay within the repository boundary
- Prevents directory traversal attacks via `../` in MCP tool calls

### Parser Guardrails
- Files larger than 2MB are silently skipped during parsing (`repotoire-cli/src/parsers/mod.rs`)
- Built-in exclusions: vendor, node_modules, dist, third-party, minified files

### Credential Management
- API keys configured via environment variables or user config (`~/.config/repotoire/config.toml`)
- Never commit credentials to version control
- Restrict config file permissions: `chmod 600 ~/.config/repotoire/config.toml`

## References

- [petgraph Documentation](https://docs.rs/petgraph/)
- [redb Documentation](https://docs.rs/redb/)
- [Tree-sitter](https://tree-sitter.github.io/)
- [clap Framework](https://docs.rs/clap/)
- [rmcp (MCP SDK)](https://docs.rs/rmcp/)
- [rayon (Data Parallelism)](https://docs.rs/rayon/)

---

**For user-facing documentation**, see [README.md](README.md).
**For MCP server details**, see [repotoire-cli/docs/MCP.md](repotoire-cli/docs/MCP.md).
**For formal verification**, see [docs/VERIFICATION.md](docs/VERIFICATION.md).
