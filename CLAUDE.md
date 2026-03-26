# CLAUDE.md

This file provides essential guidance to Claude Code (claude.ai/code) and developers working with the Repotoire codebase.

## Project Overview

Repotoire is a graph-powered code health platform that analyzes codebases using knowledge graphs to detect code smells, architectural issues, and technical debt. Unlike traditional linters that examine files in isolation, Repotoire builds a **petgraph in-memory graph** combining:
- **Structural analysis** (tree-sitter AST parsing across 9 languages)
- **Relational patterns** (graph algorithms via petgraph)

This multi-layered approach enables detection of complex issues that traditional tools miss, such as circular dependencies, architectural bottlenecks, and modularity problems. 106 detectors (73 default + 33 deep-scan) are pure Rust — no external tool dependencies.

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

# Diff analysis (what changed since main)
repotoire diff main
repotoire diff main --format json
repotoire diff main --fail-on high

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
```

### CLI Commands (18 total)

| Command | Description |
|---------|-------------|
| `analyze` | Analyze codebase for issues (73 default detectors, or all 106 with `--all-detectors`) |
| `diff` | Compare findings between two analysis states |
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
| `config` | Manage configuration (init, show, set, telemetry on/off/status) |
| `feedback` | Label findings as true/false positives |
| `train` | Train the classifier on labeled data |
| `benchmark` | Compare scores against ecosystem benchmarks (56 open-source repos) |
| `debt` | View technical debt summary |

### Global Flags

- `--path` (default: `.`) — Path to repository
- `--log-level` (default: `info`) — Log level: error, warn, info, debug, trace
- `--workers` (default: `8`) — Number of parallel workers (1-64)

## Architecture

### Core Pipeline Flow

```
AnalysisEngine.analyze() → Collect → Parse → Graph (Builder→Freeze) → Git Enrich (+ CoChange) → Calibrate → Detect → Postprocess → Score → AnalysisResult
```

During **Freeze**, `GraphPrimitives::compute()` runs Phase A algorithms (dominator trees, articulation points, PageRank, betweenness, SCCs) and Phase B weighted algorithms (weighted overlay, weighted PageRank, weighted betweenness, Louvain communities) in parallel via rayon. All results are immutable and O(1)-accessible by detectors.

### System Components

**Graph Schema**: Nodes (`File`, `Function`, `Class`, `Module`, `Variable`, `Commit`), Relationships (`Calls`, `Imports`, `Contains`, `Inherits`, `Uses`, `ModifiedIn`), qualified names as unique keys

**Core Modules**:

1. **Parsers** (`repotoire-cli/src/parsers/`): 9 tree-sitter parsers — Python, TypeScript/JavaScript (with TSX), Rust, Go, Java, C#, C, C++, plus a lightweight fallback parser. Cross-language nesting depth enrichment via brace/indent counting. 2MB file size guardrail. Header file (`.h`) dispatch heuristic for C vs C++.

2. **Graph Layer** (`repotoire-cli/src/graph/`): Two-phase graph: `GraphBuilder` (mutable, `&mut self`, used during parse/build/git-enrich) → `CodeGraph` (frozen, immutable, O(1) indexed queries via pre-built `GraphIndexes`). `GraphBuilder.freeze()` produces `CodeGraph`; no locks or interior mutability. String interning via `lasso` (`ThreadedRodeo`) for ~66% memory savings. Compact node types (`CompactNode` at ~32 bytes vs ~200 bytes for `CodeNode`) defined in `interner.rs` for future large-repo support. `GraphQuery` trait (24 core methods) + `GraphQueryExt` (blanket extension trait with convenience methods). Graph primitives accessed via `graph.primitives()`. Fan-in/fan-out metrics, Tarjan SCC cycle detection. `GraphPrimitives` (computed once during freeze) provides pre-computed dominator trees, articulation points, PageRank, betweenness centrality, call-graph SCCs, weighted PageRank, weighted betweenness, and Louvain community detection — all O(1) lookups from detectors.

3. **Engine** (`repotoire-cli/src/engine/`): `AnalysisEngine` is the primary analysis orchestrator. Runs 8 stages in order: collect, parse, graph, git_enrich, calibrate, detect, postprocess, score. Returns `AnalysisResult` (findings + score + stats). Stateful: supports cold, cached, and incremental modes. Session persistence via `save()`/`load()` stores `engine_session.json` + `graph.bin` + `precomputed.json` in `~/.cache/repotoire/<repo>/session/`. Incremental detection with per-scope handling: FileLocal detectors re-run on changed files, FileScopedGraph/GraphWide cached findings carry-forward. `AnalysisConfig` controls analysis parameters; `OutputOptions` handles presentation. Stage implementations live in `engine/stages/`.

4. **Detectors** (`repotoire-cli/src/detectors/`): 106 pure Rust detectors split into two tiers: 73 default (security, bugs, performance, architecture) in `DEFAULT_DETECTOR_FACTORIES` and 33 deep-scan (code smells, style, dead code) in `DEEP_ONLY_DETECTOR_FACTORIES`. Deep-scan detectors run only with `--all-detectors`. No external tool dependencies — all analysis runs in-process. `RegisteredDetector` trait + compile-time factory registries. `create_default_detectors()` for normal mode, `create_all_detectors()` for deep mode. `run_detectors()` (in `runner.rs`) executes them in parallel via rayon. Security detectors use SSA-based intra-function taint analysis via tree-sitter ASTs. Graph-primitive detectors read pre-computed algorithms at O(1) via `GraphQuery`.

5. **Scoring** (`repotoire-cli/src/scoring/`): Three-pillar scoring — Structure (40%), Quality (30%), Architecture (30%). Flat severity-weighted penalties (Critical=5, High=2, Medium=0.5, Low=0.1) — no density normalization. Graph-derived bonuses (modularity, cohesion, clean deps, complexity distribution, test coverage). Compound smell escalation. 13 grade levels (A+ through F). Score floor at 5.0, cap at 99.9 with medium+ findings. Security multiplier (default 3x).

6. **CLI** (`repotoire-cli/src/cli/`): clap 4 with derive, 16 commands. Progress bars via indicatif. Terminal styling via console. Git presence auto-detected (no `--no-git` flag).

7. **Reporters** (`repotoire-cli/src/reporters/`): 5 output formats — text (default, themed "What stands out" + "Quick wins" + score delta), JSON, HTML (standalone with SVG architecture map, hotspot treemap, bus factor chart, narrative story, inline code snippets), SARIF 2.1.0 (GitHub Code Scanning compatible), Markdown. Two reporter APIs: `report_with_format()` (legacy, HealthReport only) and `report_with_context()` (rich, uses `ReportContext` with `GraphData`, `GitData`, `FindingSnippet`). SVG generation in `reporters/svg/` (architecture map, treemap, bar chart). Narrative generation in `reporters/narrative.rs`. `ReportContext` struct in `reporters/report_context.rs`.

8. **Config** (`repotoire-cli/src/config/`): TOML (`repotoire.toml`), JSON (`.repotoirerc.json`), YAML (`.repotoire.yaml`). Per-detector settings, scoring weights, path exclusions, project type detection (web, library, framework, CLI, etc.). User config at `~/.config/repotoire/config.toml`.

9. **Calibration** (`repotoire-cli/src/calibrate/`): Adaptive threshold system — collects metric distributions from parsed code, builds `StyleProfile` with percentile breakpoints (p50/p75/p90/p95), `ThresholdResolver` with floor/ceiling guardrails. N-gram surprisal model for anomaly detection.

10. **Models** (`repotoire-cli/src/models.rs`): `Finding` (with severity, CWE IDs, confidence, affected files), `Severity` levels (Critical, High, Medium, Low, Info).

11. **Git Co-Change** (`repotoire-cli/src/git/co_change.rs`): `CoChangeMatrix` tracks integer counts (pair_counts, file_counts, coupling_degrees) alongside decay-weighted file-pair co-change frequencies from git history. Exponential decay with configurable half-life (default 90 days). Used by hidden coupling (Bayesian lift), change coupling (propagation rates), and community detection. Used by `GraphPrimitives` to build a weighted overlay graph for Phase B algorithms (weighted PageRank, weighted betweenness, Louvain communities). Configured via `[co_change]` section in `repotoire.toml`.

12. **Predictive Coding** (`repotoire-cli/src/predictive/`): Hierarchical predictive coding engine applying Friston's free energy formalism to code analysis. Five hierarchy levels independently model "what's normal" and compute prediction errors (z-scores): L1 Token (per-language n-gram), L2 Structural (Mahalanobis distance on function feature vectors), L1.5 Dependency Chain (surprisal along call-graph paths), L3 Relational (per-edge-type node2vec embeddings + kNN cosine distance), L4 Architectural (module-level distributional outlier detection). Severity driven by concordance (how many levels agree something is surprising) with precision-weighted aggregation.

13. **Telemetry** (`repotoire-cli/src/telemetry/`): Optional, privacy-respecting telemetry via PostHog. Respects `DO_NOT_TRACK` and `REPOTOIRE_TELEMETRY` env vars. First-run opt-in prompt. Modules: `events.rs` (event tracking), `posthog.rs` (PostHog API), `config.rs` (telemetry state/opt-in), `display.rs` (terminal display), `benchmarks.rs` (ecosystem benchmark display from R2 CDN), `cache.rs` (cache stats), `repo_shape.rs` (repository shape analysis). Controlled via `repotoire config telemetry on/off/status`.

### Detector Suite (106 Pure Rust Detectors)

Detectors are split into two tiers based on real-world signal analysis (9,469 merged PRs from 98 repos):
- **Default (73)**: Security, bugs, performance, architecture — high-value detectors that always run
- **Deep-scan (33)**: Code smells, style, dead code — run with `--all-detectors`

Grouped by category:

| Category | Count | Examples |
|----------|-------|---------|
| **Security** | 23 | SQL injection, XSS, SSRF, command injection, path traversal, secrets, insecure crypto, JWT weak, CORS misconfig, NoSQL injection, log injection, XXE, prototype pollution, insecure TLS, cleartext credentials |
| **Code Quality** | 25 | Empty catch, deep nesting, magic numbers, dead store, debug code, commented code, duplicate code, unreachable code, mutable default args, broad exceptions, boolean traps, inconsistent returns |
| **Code Smells** (graph-based) | 12 | God class, feature envy, data clumps, inappropriate intimacy, lazy class, message chain, middle man, refused bequest, dead code, long parameters, circular dependencies, mutual recursion |
| **Architecture** (graph-based) | 12 | Architectural bottleneck, degree centrality, influential code, module cohesion, core utility, shotgun surgery, single point of failure, structural bridge risk, hidden coupling, community misplacement, PageRank drift, temporal bottleneck |
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

**Cross-Detector Infrastructure**: Voting engine (multi-detector consensus), health score delta calculator (fix impact estimation), risk analyzer (compound risk assessment), root cause analyzer, incremental cache, content classifier, context HMM, data flow / SSA taint analysis, function/class context inference, framework detection for FP reduction, AST fingerprinting. `is_network_bound()` trait method skips network detectors (e.g., DepAuditDetector) in incremental mode. `is_non_production_file()` downgrades findings in scripts/benchmarks/tools to LOW severity. Postprocessor `filter_inline_suppressed()` respects `repotoire:ignore` on any finding. Five detectors migrated from filesystem walks to `AnalysisContext` `FileProvider`.

**Inline Suppression**: `// repotoire:ignore` (all detectors) or `// repotoire:ignore[detector-name]` (targeted). Supports `#`, `//`, `/*`, `--` comment styles.

## Design Decisions (Key Points)

### Why petgraph?
- **petgraph**: In-memory directed graph with mature algorithm library (SCC, BFS, DFS)
- Session persistence via JSON + bincode (`engine_session.json`, `graph.bin`, `precomputed.json`)
- No Docker, no Redis, no connection pooling — just a single binary

### Why GraphBuilder over GraphStore?
- `GraphBuilder` uses `&mut self` (no locks), produces `CodeGraph` via `freeze()`
- `GraphStore` was RwLock-based with interior mutability — removed in v0.5.1
- Simpler ownership model, no runtime lock contention

### Why Claude Code hook over MCP?
- Zero-config pre-commit hook via installer
- No MCP server needed — Claude Code hook runs repotoire automatically before commits

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
- Flat severity weights (Critical=5, High=2, Medium=0.5, Low=0.1) — no density normalization, so scores differentiate between projects (e.g., fd=96(A), repotoire=92(A-), flask=85(B))

## Incremental Analysis

Repotoire provides session-based incremental analysis with full graph and findings persistence.

### Session Persistence

Analysis state is persisted to `~/.cache/repotoire/<repo>/session/`:
- **`engine_session.json`** — file hashes, detector metadata, version info
- **`graph.bin`** — serialized `CodeGraph` (bincode), loaded directly on incremental runs (no rebuild)
- **`precomputed.json`** — `PrecomputedAnalysis` (lazy loading, no file I/O at load time)

### Performance

| Mode | Time |
|------|------|
| Cold (first run) | ~17s |
| Cached (no changes) | ~1.3s |
| Incremental (1 file changed) | ~1.4s |

### How It Works

1. **Session Load**: `AnalysisEngine::load()` restores the full session — graph, precomputed analysis, file hashes
2. **Change Detection**: SipHash content hashing identifies changed files
3. **Per-Scope Handling**:
   - **FileLocal** detectors: re-run only on changed files
   - **FileScopedGraph / GraphWide** detectors: cached findings carry-forward from previous session
4. **Network-Bound Skipping**: Detectors with `is_network_bound() == true` (e.g., DepAuditDetector) are skipped in incremental mode
5. **Graph Reuse**: Loaded `CodeGraph` used directly on incremental-after-load (no rebuild)
6. **Deterministic**: Identical scores and findings across runs with the same inputs
7. **Binary Version Invalidation**: Session is automatically invalidated when the Repotoire binary version changes

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
2. Implement `Detector` trait + `RegisteredDetector` trait (with `create()` factory using `DetectorConfig`)
3. Register in `repotoire-cli/src/detectors/mod.rs` — add `mod`, `pub use`, and add `register::<YourDetector>()` to `DETECTOR_FACTORIES`
4. Set `detector_scope()` → `PerFile` or `GraphWide`, `is_deterministic()` → `true` for graph-based detectors
5. For graph-primitive detectors: read pre-computed data via `ctx.graph` (`&dyn GraphQuery`) — see `hidden_coupling.rs` or `mutual_recursion.rs` as templates
6. Add tests as inline `#[test]` modules

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
- petgraph in-memory graph with session persistence (JSON + bincode)
- String interning via lasso for memory efficiency
- 9 tree-sitter language parsers (Python, TypeScript/JavaScript, Rust, Go, Java, C#, C, C++, lightweight fallback)
- 106 pure Rust detectors: 73 default (security, bugs, perf, architecture) + 33 deep-scan (`--all-detectors`)
- SSA-based taint analysis for security detectors
- Three-pillar health scoring with flat severity weights (Critical=5, High=2, Medium=0.5, Low=0.1) and graph-derived bonuses
- Compound smell escalation (arXiv:2509.03896)
- Adaptive threshold calibration with n-gram surprisal
- Session-based incremental analysis (cold ~17s, cached ~1.3s, incremental 1-file ~1.4s)
- 5 report formats (text, JSON, HTML, SARIF 2.1.0, Markdown)
- **Redesigned text output**: themed "What stands out" + "Quick wins" sections, score delta on subsequent runs, first-run tips
- **Graph-powered HTML report**: SVG architecture map, hotspot treemap, bus factor visualization, narrative story generator, finding cards with inline code snippets, README badge snippet
- **ReportContext pipeline**: `ReportContext` struct with `GraphData`, `GitData`, `FindingSnippet` for rich reporting via `report_with_context()`
- Git history integration via git2 (churn, blame, commits, co-change)
- File watching with real-time re-analysis
- Inline suppression (`repotoire:ignore` / `repotoire:ignore[detector]`)
- Cross-detector analysis (voting engine, risk analyzer, root cause analyzer, health delta calculator)
- Project type detection with framework-aware detector thresholds
- `.repotoireignore` support
- Formal verification of scoring algorithms (Lean 4)
- **Graph Primitives Engine (Phase A)**: Pre-computed dominator trees, articulation points, PageRank, betweenness centrality, call-graph SCCs, BFS call depths — all O(1) from detectors. 3 detectors: SPOF, mutual recursion, bridge risk.
- **Weighted Graph Engine (Phase B)**: Git co-change temporal weights (`CoChangeMatrix`), weighted overlay graph, weighted PageRank, Dijkstra-based weighted betweenness, Louvain community detection. 4 detectors: hidden coupling, community misplacement, PageRank drift, temporal bottleneck.
- **Telemetry & benchmarks**: Optional PostHog telemetry, ecosystem benchmarks from 56 open-source repos on R2 CDN, `repotoire benchmark` command, `repotoire config telemetry on/off/status`
- **`--relaxed` deprecated**: replaced by `--severity high` (deprecation warning shown)

- **GitHub Action**: `Zach-hammad/repotoire-action@v1` (separate repo) — composite action with PR diff mode, PR commenting, SARIF upload, quality gates
- **Claude Code hook integration**: Zero-config pre-commit guardrail via installer
- **GraphQuery / GraphQueryExt split**: 24 core methods + blanket extension trait
- **Detector rearchitecture**: `is_network_bound()`, `is_non_production_file()`, `filter_inline_suppressed()` postprocessor, FileProvider migration

### Planned
- Web dashboard
- GitHub App (check run annotations)
- JetBrains plugin
- Custom rule engine
- Team analytics

## Dependencies

### Core (from Cargo.toml)
- **petgraph** (0.7): In-memory directed graph
- **clap** (4): CLI framework with derive macros
- **serde** / **serde_json** (1): Serialization
- **rayon** (1.11): Data parallelism for detectors and parsing
- **anyhow** / **thiserror**: Error handling
- **lasso** (0.7.3): Thread-safe string interning

### Parsing
- **tree-sitter** (0.25): Incremental parsing framework
- **tree-sitter-python** (0.25), **tree-sitter-javascript** (0.25), **tree-sitter-typescript** (0.23), **tree-sitter-rust** (0.24), **tree-sitter-go** (0.25), **tree-sitter-java** (0.23), **tree-sitter-c-sharp** (0.23), **tree-sitter-c** (0.24), **tree-sitter-cpp** (0.23)

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

### Parser Guardrails
- Files larger than 2MB are silently skipped during parsing (`repotoire-cli/src/parsers/mod.rs`)
- Built-in exclusions: vendor, node_modules, dist, third-party, minified files

### Credential Management
- API keys configured via environment variables or user config (`~/.config/repotoire/config.toml`)
- Never commit credentials to version control
- Restrict config file permissions: `chmod 600 ~/.config/repotoire/config.toml`

## References

- [petgraph Documentation](https://docs.rs/petgraph/)
- [Tree-sitter](https://tree-sitter.github.io/)
- [clap Framework](https://docs.rs/clap/)
- [rayon (Data Parallelism)](https://docs.rs/rayon/)

---

**For user-facing documentation**, see [README.md](README.md).
**For formal verification**, see [docs/VERIFICATION.md](docs/VERIFICATION.md).
