# Repotoire Workstation Design

**Date:** 2026-02-27
**Status:** Draft
**Author:** Zach + Claude

## Overview

Repotoire evolves from a code analysis CLI into an AI-native developer workstation. The existing knowledge graph, 115 detectors, parallel parsing pipeline, file watcher, and multi-provider AI client become the foundation. The new code is an agent loop, coding tools, and a multi-panel TUI — approximately 1500 lines of Rust on top of the existing codebase.

**Core thesis:** The agent doesn't read files and guess at architecture. It queries a knowledge graph that already understands call chains, dependency cycles, complexity hotspots, and security vulnerabilities. No other coding agent has this.

## Product Vision

- **Single Rust binary.** No runtime dependencies. `cargo install repotoire`, done.
- **Two modes:** `repotoire` launches the workstation (default). `repotoire --headless analyze .` preserves existing CLI behavior for CI/scripting.
- **Self-sufficient.** Open source, MIT licensed, runs with any LLM provider. No vendor lock-in.
- **Target audience (progressive):** Power users first, then solo devs, then small teams.

## What Already Exists

| Component | Location |
|-----------|----------|
| Parallel file parsing (rayon `par_iter` with cache) | `cli/analyze/parse.rs` |
| Knowledge graph (petgraph + redb) | `graph/` |
| Graph queries (callers, callees, imports, cycles, complexity, fan-in/out) | `graph/store_query.rs` |
| 115 detectors (security, architecture, quality, performance, bug risk) | `detectors/*.rs` |
| File watcher with debouncing (notify) | `cli/watch.rs` |
| Git enrichment (background thread) | `cli/analyze/detect.rs` |
| MCP server (stdio + HTTP) | `mcp/` |
| Graph queries as MCP tools | `mcp/tools/graph_queries.rs` |
| AI client (Anthropic, OpenAI, DeepInfra, OpenRouter, Ollama) | `ai/client.rs` |
| TUI findings browser + agent spawning (ratatui) | `cli/tui.rs` |
| DashMap, rayon, tokio, crossbeam, tree-sitter x10 | Cargo.toml |

## What Needs to Be Built (~1500 lines)

### 1. Streaming Anthropic Provider (~300 lines)

Upgrade from sync `ureq` to async `reqwest` + `reqwest-eventsource` for SSE streaming with tool_use flow.

- POST to `/v1/messages` with `stream: true`
- Parse SSE events: `message_start`, `content_block_delta`, `content_block_stop`, `message_stop`
- Buffer partial tool call JSON per content block index
- Push text deltas to `tokio::sync::watch` channel for live TUI rendering
- Handle: 429 rate limits (backoff + jitter), 529 overloaded, network timeouts, malformed responses
- Reuses existing `LlmBackend` enum and provider config

### 2. Agent Loop (~200 lines)

Core cycle: send messages to LLM, parse tool calls, execute, feed results back, repeat.

- Runs as `tokio::spawn` task, communicates with TUI via `watch` channel
- Tool execution via `tokio::task::JoinSet` for parallel dispatch
- CPU-bound tools (graph queries): `tokio_rayon::spawn` to rayon pool
- I/O-bound tools (bash, files): regular tokio async
- `JoinSet::abort_all()` on Ctrl+C for clean cancellation

### 3. Tool Registry + Dispatch (~100 lines)

Maps tool names to executors. Each tool: name, description, JSON schema, execute function.

### 4. Coding Tools (~400 lines)

**New tools:**

| Tool | Description |
|------|-------------|
| `bash` | `tokio::process::Command`, timeout, process group kill |
| `read` | `tokio::fs::read_to_string`, optional line range |
| `write` | `tokio::fs::write`, parent dir creation |
| `edit` | Find exact `old_string`, replace with `new_string`, fail if not unique |
| `grep` | Shell out to `rg`, parse output |

**Wrappers around existing code (zero new logic):**

| Tool | Wraps |
|------|-------|
| `query_graph` | Existing `GraphStore` query methods |
| `trace_dependencies` | Existing BFS/DFS in `mcp/tools/graph_queries.rs` |
| `analyze_impact` | Existing change impact in `mcp/tools/graph_queries.rs` |
| `run_detectors` | Existing `DetectorEngine::run()` |
| `get_findings` | Existing findings list |

### 5. Two-Panel TUI (~400 lines)

Evolve existing `tui.rs` into multi-panel layout:

```
+---------------+--------------------------------------+
| Findings      | Agent Conversation                   |
|               |                                      |
| * SQL inj     | > analyze the auth module            |
|   src/api     |                                      |
| * Dead code   | Found 3 functions in src/auth/:      |
|   src/old     | - acquire_lock (complexity: 12)      |
| o Complex     | - validate_token (5 callers)         |
|   src/core    | - refresh_session (unused)           |
|               |                                      |
|               | [Tool: edit src/auth/mod.rs]         |
|               | Accept? [y/n]                        |
+---------------+--------------------------------------+
| > _                                                  |
+------------------------------------------------------+
| repotoire  main  claude-opus-4-6  ctx:12k/200k       |
+------------------------------------------------------+
```

- Horizontal split: sidebar 25% | main 75%
- Findings panel: port from existing `tui.rs`
- Conversation panel: scrollable text with streaming
- Event loop: `tokio::select!` over terminal events + agent watch channel + tick timer
- Focus: Tab to switch panels

## Concurrency Architecture

```
Rayon thread pool (CPU-bound)
  - Initial graph build: par_iter over files (EXISTS)
  - Detector execution: par_iter over detectors (EXISTS)
  - Graph queries from agent: tokio_rayon::spawn (NEW bridge)

Tokio runtime (I/O-bound)
  Task: TUI event loop
    crossterm events + agent watch channel + 30fps tick
    tokio::select! over all three

  Task: Agent loop (spawned per user message)
    reqwest-eventsource for Anthropic SSE
    pushes deltas to watch channel for TUI
    tool dispatch via JoinSet

  Task: File watcher (reuses watch.rs logic)
    debounce 500ms, triggers incremental re-parse
    updates graph via ArcSwap (lock-free)

Shared state (minimal locking)
  graph:        ArcSwap<GraphSnapshot>       lock-free reads
  findings:     ArcSwap<Vec<Finding>>        lock-free reads
  conversation: RwLock<Vec<Message>>         rare writes
  file_locks:   DashMap<PathBuf, ()>         per-file sharded locks
  agent_stream: watch::Sender<StreamState>   lock-free
```

## Dependencies

| Crate | Purpose | Status |
|-------|---------|--------|
| `tokio-rayon` | Bridge tokio to rayon | New |
| `arc-swap` | Lock-free graph snapshots | New |
| `reqwest` | Async HTTP | New |
| `reqwest-eventsource` | SSE streaming | New |
| `ratatui-interact` | Panel focus management | New |
| `rayon`, `dashmap`, `crossbeam-channel` | Parallelism | Already in workspace |
| `tokio`, `ratatui`, `crossterm` | Async + TUI | Already in workspace |
| `tree-sitter` x10, `notify`, `serde` | Parsing, watching, serialization | Already in workspace |

5 new deps. 8 already present.

## File Structure

```
repotoire-cli/src/
  main.rs                         <- ADD: workstation mode routing
  ai/
    client.rs                     <- EXISTS (sync, 5 providers)
    streaming.rs                  <- NEW: async SSE streaming
  graph/                          <- EXISTS: untouched
  detectors/                      <- EXISTS: untouched
  parsers/                        <- EXISTS: untouched
  mcp/                            <- EXISTS: untouched
  cli/
    tui.rs                        <- EXISTS: port findings panel from here
    watch.rs                      <- EXISTS: reuse file watcher logic
    analyze/                      <- EXISTS: untouched
  workstation/                    <- NEW
    mod.rs                        <- app lifecycle, startup, shutdown
    app.rs                        <- ratatui event loop + layout
    agent/
      loop.rs                     <- core agent cycle
      streaming.rs                <- Anthropic SSE parsing
      tools.rs                    <- tool registry + dispatch
    tools/
      bash.rs                     <- command execution
      files.rs                    <- read, write, edit
      search.rs                   <- grep/glob via rg
      graph.rs                    <- thin wrappers around GraphStore
    panels/
      conversation.rs             <- chat display + input
      findings.rs                 <- port from existing tui.rs
```

## Risks and Concerns

### 1. Streaming Provider is the Riskiest New Code (HIGH)

The SSE streaming + partial tool call buffering is ~300 lines where every bug will live. Anthropic's format has edge cases: empty content blocks, multiple tool calls in one response, `overloaded` errors mid-stream, partial JSON that looks valid but isn't complete.

**Mitigation:** Build and test in complete isolation before TUI integration. Write tests against recorded SSE fixtures. Handle malformed tool call JSON by returning error text to the model (let it self-correct).

### 2. Startup Time (MEDIUM)

Full graph load = same time as `repotoire analyze`. On a large workspace, 10-30 seconds. Terminal tools should start instantly.

**Mitigation:** Lazy graph loading. Start TUI immediately with empty graph + loading indicator. Build graph in background task. Agent can use bash/file tools while graph loads. Graph tools return "graph still loading" if called before ready. Warm incremental cache makes subsequent launches sub-second.

### 3. The Edit Tool Will Bite You (MEDIUM)

The model will submit old_string that doesn't match (whitespace, encoding), matches multiple locations, or references content that doesn't exist.

**Mitigation:** Fail loudly. 0 matches: show the model a snippet of actual file content near where it expected the match. >1 matches: tell model to include more surrounding context. Never silently replace the wrong thing.

### 4. Git Panel is v1.1, Not v1 (LOW)

Two panels (findings + conversation) is simpler than three. Git adds layout complexity for minimal v1 value. Git info is still accessible via the bash tool.

### 5. Tree-sitter Thread Safety (LOW)

`Parser` is `Send` but `Tree` is not thread-safe for concurrent access. Already handled correctly in `cli/analyze/parse.rs` — `par_iter` creates independent parses per file. Incremental re-parse is sub-millisecond, no parallelism needed.

## Axon-Inspired Enhancements

After analyzing [Axon](https://github.com/harshkedia177/axon) — a Python graph-powered code intelligence engine (KuzuDB, tree-sitter, 3 languages, MCP-first) — several design ideas are worth stealing. Axon's strength is UX for AI consumers; Repotoire's strength is depth of analysis. These enhancements close the UX gap.

### v1 (low effort, high value)

**1. Next-Step Hints in Tool Responses**

When the agent calls `query_graph` or `analyze_impact`, the response should include contextual "you might also want to..." suggestions. Axon does this for every MCP tool response and it dramatically improves agent effectiveness — the agent doesn't have to guess what to ask next.

Implementation: Just append hint text to tool output strings in `workstation/tools/graph.rs`. Zero new logic. Examples:
- After `get_callers(fn_name)`: *"Hint: Use `get_callees` on these callers to see if they share patterns. Use `analyze_impact` to see blast radius."*
- After `get_findings`: *"Hint: Use `query_graph` with the qualified name of any finding to see its dependency context."*
- After `analyze_impact`: *"Hint: High fan-in functions are risky to change. Consider `get_callers` on depth-1 results to assess test coverage."*

**2. Depth-Tiered Impact Analysis**

Axon's blast radius groups results by depth (direct callers → indirect → transitive) instead of a flat list. Current `analyze_impact` in `mcp/tools/graph_queries.rs` already does BFS — just needs to tag each result with its depth and format output as tiered groups.

Implementation: Modify the output formatting of the `analyze_impact` wrapper in `workstation/tools/graph.rs`. The BFS already tracks depth — just expose it in the response text:
```
Depth 0 (direct): authenticate_user, validate_session (2 functions)
Depth 1 (indirect): login_handler, api_middleware (2 functions)
Depth 2 (transitive): main_router (1 function)
Total blast radius: 5 functions across 3 files
```

### v1.1 (moderate effort, strong differentiator)

**3. Confidence-Scored Call Edges**

Axon distinguishes "definitely calls this function" from "might call this via receiver/interface." Repotoire's `CALLS` edges are binary. Adding a `confidence: f32` field to `CodeEdge` (or a variant on `EdgeKind`) would make impact analysis more honest and help the agent prioritize investigation.

Implementation: Extend `EdgeKind::Calls` or add metadata to `CodeEdge`. Confidence heuristics:
- Direct function call with resolved target → 1.0
- Method call with known receiver type → 0.9
- Method call on trait object / interface → 0.6
- Dynamic dispatch / callback → 0.3

Affects: `parsers/*.rs` (emit confidence during call resolution), `graph/mod.rs` (store it), `graph/store_query.rs` (expose it), all impact/caller tools.

**4. Community Detection (Auto-Discover Module Boundaries)**

Axon uses the Leiden algorithm to automatically discover architectural clusters — "these 12 functions form a natural module." Repotoire detects circular dependencies *between* modules but doesn't discover what the modules *are*. For unfamiliar codebases, auto-clustering is extremely valuable.

Implementation options:
- `petgraph-community` crate (if it exists/matures)
- Port Leiden/Louvain to Rust (moderate — ~200 lines for basic Louvain)
- Use `graph-algorithms` crate's modularity functions
- Expose as `detect_communities` tool returning cluster labels

This would give the agent the ability to say "this codebase has 7 natural modules: auth, routing, db, ..." without any configuration.

### v2+ (significant effort, strategic)

**5. Hybrid Search (BM25 + Semantic Vectors + Fuzzy + RRF)**

Axon's killer UX feature. Instead of structural queries ("give me callers of X"), hybrid search lets you say "find code related to user authentication" and get fuzzy semantic results ranked by Reciprocal Rank Fusion across three signals.

This is the most expensive enhancement but arguably the highest-value for the "developer workstation" vision. Structural queries require you to know what you're looking for; hybrid search handles "I don't know the function name but I know what it does."

Components needed:
- BM25 index over source code (tantivy crate — Rust-native full-text search)
- Embedding model (ONNX runtime with bge-small-en or similar, ~50MB model)
- Vector storage (usearch or qdrant-client, or just brute-force cosine over <100k vectors)
- RRF fusion layer (~50 lines)
- `semantic_search` agent tool

Estimated effort: 2-3 days. But it would be the single biggest differentiator over every other terminal coding agent.

**6. Cypher/Query Language for Ad-Hoc Graph Queries**

Axon exposes `axon_cypher` — arbitrary read-only Cypher queries against KuzuDB. Repotoire's petgraph is in-memory with no query language. For power users and advanced agents, a query language would unlock exploration patterns we can't anticipate.

Options:
- Embed KuzuDB alongside petgraph (heavy, redundant)
- Implement a mini query DSL over petgraph (lighter, custom)
- Use SPARQL-like syntax with a small parser (moderate)
- Or just expose more granular `GraphQuery` trait methods as tools (cheapest)

Probably the cheapest win is just expanding the `query_graph` tool's `query_type` enum to cover more of the `GraphQuery` trait surface. Full Cypher is overkill when you already control the schema.

## v1 Scope

**In:**
- Agent loop with Anthropic streaming + tool calling
- Coding tools: bash, read, write, edit, grep
- Graph tools: query_graph, trace_dependencies, analyze_impact, run_detectors
- Two-panel TUI: findings + conversation
- File watcher triggering incremental graph updates
- Status bar: model, context usage, branch

**Out (v2+):**
- Plugin/extension system
- Subagent orchestration
- Context compaction / summarization
- Git panel
- Additional providers (Ollama, OpenAI)
- Linear / GitHub panel integrations
- Session persistence
- MCP client
- Hybrid search (BM25 + semantic vectors + fuzzy + RRF) — see Axon-Inspired Enhancements
- Cypher/query language for ad-hoc graph queries
- Confidence-scored call edges (v1.1)
- Community detection / auto-clustering (v1.1)

## Success Criteria

v1 ships when:
1. User launches `repotoire`, types a request, agent responds using graph-aware tools
2. Agent can query callers, callees, dependencies, and findings mid-conversation
3. Agent can read, edit, and write files
4. Findings panel updates live as agent modifies code and graph re-indexes
5. Conversation streams in real-time
6. Ctrl+C cleanly aborts current operation
