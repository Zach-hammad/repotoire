# MCP Server Overhaul Design

**Date:** 2026-02-24
**Status:** Approved
**Scope:** Full migration of Rust CLI MCP server to rmcp SDK with redesigned tools and graph-powered capabilities

## Context & Motivation

The current MCP server in `repotoire-cli/src/mcp/` is a manual JSON-RPC 2.0 implementation (1,350 lines) with:
- 10 tools (7 FREE + 3 AI)
- Stdio transport only
- Protocol version 2024-11-05 (outdated)
- Manual tool schema definitions
- No structured output support

### Research Findings (16 arXiv papers)

- **MCP spec is now at 2025-11-25** with OAuth 2.1, Streamable HTTP, structured outputs, async ops, server identity
- **66% of MCP servers have code smells** (arXiv:2506.13538) — quality is a differentiator
- **CodexGraph** (arXiv:2408.03910) validates Repotoire's exact architecture (LLM + graph DB for code repos)
- **No existing MCP server** combines graph-backed code analysis with health metrics — this is Repotoire's unique gap
- Best practices: 5-15 tools, flat arguments, outcome-oriented design, pagination, `{service}_{action}_{resource}` naming

### Key Papers

| Paper | Year | Relevance |
|-------|------|-----------|
| MCP Landscape & Security (arXiv:2503.23278) | 2025 | Security hardening for MCP server |
| MCP at First Glance (arXiv:2506.13538) | 2025 | Quality differentiation opportunity |
| CA-MCP (arXiv:2601.11595) | 2026 | Shared context store pattern |
| Securing MCP (arXiv:2511.20920) | 2025 | Per-user auth, sandboxing, DLP |
| CodexGraph (arXiv:2408.03910) | 2024 | Most similar architecture to Repotoire |
| LocAgent (arXiv:2503.09089) | 2025 | Validates graph approach (92.7% accuracy) |
| Graph-Guided Code Analysis (arXiv:2601.12890) | 2026 | Graph-guided LLM analysis pattern |
| KGoT (arXiv:2504.02670) | 2025 | 36x cost reduction with structured knowledge |

## Architecture

### Module Structure

```
repotoire-cli/src/mcp/
├── mod.rs              # Module entry, re-exports
├── server.rs           # rmcp ServerHandler impl + transport setup
├── tools/
│   ├── mod.rs          # Tool registry, #[tool_box] macro
│   ├── analysis.rs     # repotoire_analyze, repotoire_get_findings, repotoire_get_hotspots
│   ├── graph.rs        # repotoire_query_graph, repotoire_trace_dependencies, repotoire_analyze_impact
│   ├── files.rs        # repotoire_get_file, repotoire_get_architecture, repotoire_list_detectors
│   ├── ai.rs           # repotoire_search_code, repotoire_ask, repotoire_generate_fix
│   └── evolution.rs    # repotoire_query_evolution (temporal/git)
└── transport.rs        # Streamable HTTP setup
```

### Key Architectural Decisions

1. **Tokio only in MCP module** — the rest of the codebase stays sync. MCP handlers use `tokio::task::spawn_blocking` to call existing sync code (GraphStore, DetectorEngine, file I/O). No async infection.

2. **rmcp `#[tool]` macros** auto-generate JSON schemas from Rust types — eliminates the manual `ToolSchema` builder.

3. **Structured output** via `outputSchema` on tools that return typed data (findings, architecture, graph results).

4. **State sharing** via `Arc<RwLock<HandlerState>>` passed to the rmcp `ServerHandler`.

5. **Protocol version:** 2025-11-25 (latest, handled by rmcp automatically).

## rmcp Integration Pattern

### Server Handler

```rust
use rmcp::{ServerHandler, tool_box, model::*};
use std::sync::Arc;
use tokio::sync::RwLock;

#[derive(Clone)]
pub struct RepotoireServer {
    state: Arc<RwLock<HandlerState>>,
}

#[tool_box]
impl RepotoireServer {
    #[tool(description = "Run full code analysis on the repository...")]
    async fn repotoire_analyze(
        &self,
        #[tool(param, description = "Only analyze changed files")]
        incremental: Option<bool>,
    ) -> Result<CallToolResult, McpError> {
        let state = self.state.clone();
        let result = tokio::task::spawn_blocking(move || {
            let mut state = state.blocking_write();
            handle_analyze(&mut state, incremental.unwrap_or(true))
        }).await.map_err(|e| McpError::internal(e.to_string()))?;

        result.map(|v| CallToolResult::success(vec![Content::text(
            serde_json::to_string_pretty(&v).unwrap()
        )]))
    }
}

#[async_trait]
#[tool_box]
impl ServerHandler for RepotoireServer {
    fn name(&self) -> String { "repotoire".into() }
    fn version(&self) -> String { env!("CARGO_PKG_VERSION").into() }
}
```

### Transport Setup

```rust
pub async fn run_mcp_server(repo_path: PathBuf, force_local: bool, http_port: Option<u16>) -> Result<()> {
    let state = HandlerState::new(repo_path, force_local);
    let server = RepotoireServer { state: Arc::new(RwLock::new(state)) };

    if let Some(port) = http_port {
        // Streamable HTTP transport
        let transport = StreamableHttpTransport::bind(("0.0.0.0", port)).await?;
        server.serve(transport).await?;
    } else {
        // Stdio transport (default)
        let transport = (tokio::io::stdin(), tokio::io::stdout());
        let peer = server.serve(transport).await?;
        peer.waiting().await?;
    }
    Ok(())
}
```

### CLI Entry Point

```rust
// src/cli/serve.rs — Tokio runtime contained here
pub fn run(repo_path: &Path, force_local: bool, http_port: Option<u16>) -> Result<()> {
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?
        .block_on(crate::mcp::run_mcp_server(
            repo_path.to_path_buf(), force_local, http_port
        ))
}
```

## Tool Set: 13 Tools (10 FREE + 3 AI)

### FREE Tier (10 tools)

| # | Tool | Description | Backed By |
|---|------|-------------|-----------|
| 1 | `repotoire_analyze` | Run full code analysis, return findings summary by severity | DetectorEngine |
| 2 | `repotoire_get_findings` | Get findings with filtering and pagination (limit/offset, has_more) | Cached findings + DetectorEngine fallback |
| 3 | `repotoire_get_hotspots` | Get files ranked by issue density | Cached findings |
| 4 | `repotoire_query_graph` | Query code entities: functions, classes, files, stats, callers, callees | GraphStore |
| 5 | `repotoire_get_file` | Read file content with line range | Filesystem (path-traversal protected) |
| 6 | `repotoire_get_architecture` | Get module structure, language distribution, top classes | GraphStore |
| 7 | `repotoire_list_detectors` | List all available detectors with descriptions | DetectorEngine |
| 8 | `repotoire_trace_dependencies` | **NEW.** Multi-hop graph traversal: follow call chains, imports, inheritance N hops deep | GraphStore::find_paths(), get_neighbors() |
| 9 | `repotoire_analyze_impact` | **NEW.** "If I change X, what breaks?" — reverse dependency traversal with severity scoring | GraphStore SCC + reverse traversal |
| 10 | `repotoire_query_evolution` | **NEW.** Temporal queries: file churn, function history, entity blame, file ownership | git2 via GitHistory, GitBlame |

### AI Tier (3 tools)

| # | Tool | Description | Requirement |
|---|------|-------------|-------------|
| 11 | `repotoire_search_code` | Semantic code search via embeddings | PRO (REPOTOIRE_API_KEY) |
| 12 | `repotoire_ask` | RAG Q&A about the codebase | PRO (REPOTOIRE_API_KEY) |
| 13 | `repotoire_generate_fix` | AI-powered fix generation for a finding | BYOK or PRO |

### New Tool Details

#### `repotoire_trace_dependencies`

```
Input:  { "name": "parse_config", "direction": "both", "max_depth": 3, "kind": "calls" }
Output: {
  "root": "parse_config",
  "upstream": [{"name": "main", "depth": 1, "file": "src/main.rs"}],
  "downstream": [{"name": "validate", "depth": 1}, {"name": "check_schema", "depth": 2}],
  "total_nodes": 5
}
```

#### `repotoire_analyze_impact`

```
Input:  { "target": "src/graph/store.rs", "scope": "function", "name": "add_node" }
Output: {
  "target": "GraphStore::add_node",
  "direct_dependents": 12,
  "transitive_dependents": 47,
  "affected_files": ["src/pipeline.rs", "src/detectors/engine.rs", ...],
  "risk_score": "high",
  "strongly_connected": false
}
```

#### `repotoire_query_evolution`

7 query types backed by existing git2 integration:

| Query Type | Description | Backed By |
|-----------|-------------|-----------|
| `file_churn` | Churn metrics for a file (insertions, deletions, commit count, authors) | `GitHistory::get_file_churn()` |
| `hottest_files` | Rank all files by churn | `GitHistory::get_all_file_churn()` |
| `file_commits` | Commit history for a specific file | `GitHistory::get_file_commits()` |
| `function_history` | Commits that touched a function's line range | `GitHistory::get_line_range_commits()` |
| `entity_blame` | Ownership info for a function/class | `GitBlame::get_entity_blame()` |
| `file_ownership` | Percentage ownership breakdown per author | `GitBlame::get_file_ownership()` |
| `recent_commits` | Recent commits across the repo | `GitHistory::get_recent_commits()` |

Example:
```
Input:  { "query_type": "function_history", "file": "src/graph/store.rs", "name": "add_node", "limit": 10 }
Output: {
  "function": "add_node",
  "file": "src/graph/store.rs",
  "commits": [
    {"hash": "abc1234", "author": "Zach", "timestamp": "2025-11-15T10:30:00Z", "message": "refactor graph store", "lines_added": 5, "lines_deleted": 2}
  ],
  "total_commits": 8,
  "unique_authors": 2
}
```

## Dependency Changes

### Additions

```toml
rmcp = { version = "0.16.0", features = ["server", "transport-stdio", "transport-streamable-http"] }
tokio = { version = "1", features = ["rt-multi-thread", "macros", "io-std"] }
```

### Removals

- Manual `JsonRpcRequest` struct
- Manual `ToolSchema` builder and `Tool` struct
- Manual JSON-RPC routing in `handle_message()`
- Manual `error_response()` helper

### Unchanged

- `git2` — powers `repotoire_query_evolution`
- `ureq` — PRO API calls (sync, via `spawn_blocking`)
- `rayon` — DetectorEngine parallelism
- `redb` + `petgraph` — GraphStore
- All detectors, parsers, calibrate modules

### Tokio Containment

Tokio only appears in two files:
1. `src/cli/serve.rs` — creates the runtime
2. `src/mcp/server.rs` — async handlers wrapping sync code via `spawn_blocking`

## Error Handling

### Error Mapping

```
anyhow::Error (internal) → McpError::internal(msg) (protocol)
```

### Actionable Error Messages

| Scenario | Message |
|----------|---------|
| No graph database | "No analysis data found. Run `repotoire_analyze` first to build the knowledge graph." |
| File not found | "File not found: X. Use `repotoire_get_architecture` to see available files." |
| No findings | "No findings available. Run `repotoire_analyze` first, then use `repotoire_get_findings` to retrieve results." |
| No git repo | "Not a git repository. `repotoire_query_evolution` requires a git repo." |
| PRO feature without key | "Semantic search requires embeddings. Set REPOTOIRE_API_KEY for cloud, or use `repotoire_query_graph` for structural search (free)." |

## Security

| Protection | Status |
|-----------|--------|
| Path traversal prevention | Kept — canonical path check against repo root |
| Input size limit (10MB) | Kept — validated before handler dispatch |
| Argument size limit (1MB) | Kept — validated before `spawn_blocking` |
| `repo_path` override ignored | Kept — server always uses configured path |
| No arbitrary Cypher queries | Kept — `query_graph` uses predefined query types |
| Streamable HTTP auth | NEW — OAuth 2.1 when using HTTP transport |

## Testing Strategy

### Unit Tests (per tool)

Each tool handler gets its own test module with tempdir + `GraphStore::in_memory()`.

### Integration Tests

| Test | Validates |
|------|-----------|
| `test_stdio_lifecycle` | Initialize -> tools/list -> tools/call -> shutdown over stdio pipe |
| `test_all_tools_listed` | All 13 tools appear in tools/list response |
| `test_tool_schemas_valid` | Every tool's inputSchema is valid JSON Schema |
| `test_structured_output` | Tools with outputSchema return conforming responses |
| `test_error_responses` | Invalid tool names, missing args, bad inputs return proper McpError |
| `test_pagination` | get_findings and query_graph respect limit/offset and return has_more |
| `test_trace_dependencies` | Multi-hop traversal returns correct upstream/downstream |
| `test_analyze_impact` | Reverse dependency traversal matches expected affected files |
| `test_query_evolution` | All 7 query types return correct temporal data from test git repo |
| `test_spawn_blocking_timeout` | Long-running analysis doesn't hang the MCP server |

### Protocol Compliance

Run mcp-validator against the built server to verify 2025-11-25 spec compliance.

## Migration Path

### File Changes

| Component | Change |
|-----------|--------|
| `src/mcp/mod.rs` | Rewrite — new module structure |
| `src/mcp/server.rs` | Rewrite — rmcp ServerHandler impl replaces manual JSON-RPC |
| `src/mcp/tools.rs` | Delete — replaced by `#[tool]` macros |
| `src/mcp/handlers.rs` | Refactor — split into `tools/*.rs`, logic preserved |
| `src/mcp/transport.rs` | New — Streamable HTTP setup |
| `src/mcp/tools/mod.rs` | New — tool registry |
| `src/mcp/tools/analysis.rs` | New — analysis tool handlers |
| `src/mcp/tools/graph.rs` | New — graph tool handlers |
| `src/mcp/tools/files.rs` | New — file tool handlers |
| `src/mcp/tools/ai.rs` | New — AI tool handlers |
| `src/mcp/tools/evolution.rs` | New — temporal/git tool handlers |
| `src/cli/serve.rs` | Small change — add Tokio runtime, --http-port flag |
| `src/cli/mod.rs` | Small change — add --http-port arg to serve command |
| `Cargo.toml` | Add rmcp, tokio |

### Summary

- Files deleted: 1 (`tools.rs`)
- Files rewritten: 2 (`mod.rs`, `server.rs`)
- Files refactored: 1 (`handlers.rs` -> split into 5 tool modules)
- Files added: 2 (`transport.rs`, `tools/mod.rs`)
- Files lightly modified: 3 (`serve.rs`, `cli/mod.rs`, `Cargo.toml`)
- Estimated: ~1,350 lines replaced with ~1,600 lines (net +250 for new tools and better errors)
