# MCP Server Overhaul Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Migrate the Rust CLI MCP server from manual JSON-RPC to rmcp 0.16.0 SDK with 13 redesigned tools including 3 new graph-powered capabilities.

**Architecture:** Replace `src/mcp/` with rmcp-based server. `#[tool_router]` + `#[tool_handler]` macros auto-generate tool schemas. Tokio contained to MCP module only — all existing sync code called via `spawn_blocking`. State shared via `Arc<RwLock<HandlerState>>`.

**Tech Stack:** rmcp 0.16.0, tokio 1.x, schemars 1.0, axum 0.8 (HTTP transport)

**Design Doc:** `docs/plans/2026-02-24-mcp-overhaul-design.md`

---

## Task 1: Add Dependencies to Cargo.toml

**Files:**
- Modify: `repotoire-cli/Cargo.toml`

**Step 1: Add rmcp, tokio, schemars, axum dependencies**

Add to `[dependencies]` section:

```toml
# MCP Protocol
rmcp = { version = "0.16", features = ["server", "macros", "transport-io", "transport-streamable-http-server"] }
tokio = { version = "1", features = ["rt-multi-thread", "macros", "io-std", "signal"] }
schemars = "1.0"
axum = "0.8"
tokio-util = { version = "0.7", features = ["sync"] }
```

**Step 2: Verify it compiles**

Run: `cd repotoire-cli && cargo check 2>&1 | tail -20`
Expected: Compiles (possibly with unused warnings)

**Step 3: Commit**

```bash
git add repotoire-cli/Cargo.toml repotoire-cli/Cargo.lock
git commit -m "build: add rmcp 0.16, tokio, schemars, axum dependencies for MCP overhaul"
```

---

## Task 2: Create Parameter Structs Module

**Files:**
- Create: `repotoire-cli/src/mcp/params.rs`

**Step 1: Write parameter structs with schemars schemas**

Create `repotoire-cli/src/mcp/params.rs`:

```rust
//! MCP Tool parameter types
//!
//! These structs define the inputSchema for each MCP tool via schemars derive.

use schemars::JsonSchema;
use serde::Deserialize;

// ── Analysis Tools ──────────────────────────────────────────────────────

#[derive(Debug, Deserialize, JsonSchema)]
pub struct AnalyzeParams {
    /// Only analyze changed files (faster). Defaults to true.
    pub incremental: Option<bool>,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct GetFindingsParams {
    /// Filter by severity level
    pub severity: Option<SeverityFilter>,
    /// Filter by detector name
    pub detector: Option<String>,
    /// Maximum results to return (default: 20)
    pub limit: Option<u64>,
    /// Number of results to skip for pagination (default: 0)
    pub offset: Option<u64>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum SeverityFilter {
    Critical,
    High,
    Medium,
    Low,
    Info,
}

impl std::fmt::Display for SeverityFilter {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Critical => write!(f, "critical"),
            Self::High => write!(f, "high"),
            Self::Medium => write!(f, "medium"),
            Self::Low => write!(f, "low"),
            Self::Info => write!(f, "info"),
        }
    }
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct GetHotspotsParams {
    /// Maximum number of files to return (default: 10)
    pub limit: Option<u64>,
}

// ── Graph Tools ─────────────────────────────────────────────────────────

#[derive(Debug, Deserialize, JsonSchema)]
pub struct QueryGraphParams {
    /// Query type: functions, classes, files, stats, callers, callees
    pub query_type: GraphQueryType,
    /// Function or class name (required for callers/callees queries)
    pub name: Option<String>,
    /// Maximum results to return (default: 100)
    pub limit: Option<u64>,
    /// Number of results to skip for pagination (default: 0)
    pub offset: Option<u64>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum GraphQueryType {
    Functions,
    Classes,
    Files,
    Stats,
    Callers,
    Callees,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct TraceDependenciesParams {
    /// Function or class name to trace from
    pub name: String,
    /// Traversal direction: upstream (callers), downstream (callees), or both
    pub direction: Option<TraceDirection>,
    /// Maximum traversal depth (default: 3)
    pub max_depth: Option<u32>,
    /// Edge kind to follow: calls, imports, or all
    pub kind: Option<TraceKind>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum TraceDirection {
    Upstream,
    Downstream,
    Both,
}

impl Default for TraceDirection {
    fn default() -> Self { Self::Both }
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum TraceKind {
    Calls,
    Imports,
    All,
}

impl Default for TraceKind {
    fn default() -> Self { Self::All }
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct AnalyzeImpactParams {
    /// File path of the target (relative to repo root)
    pub target: String,
    /// Scope: function or file
    pub scope: Option<ImpactScope>,
    /// Function or class name (required when scope is "function")
    pub name: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum ImpactScope {
    Function,
    File,
}

impl Default for ImpactScope {
    fn default() -> Self { Self::File }
}

// ── File Tools ──────────────────────────────────────────────────────────

#[derive(Debug, Deserialize, JsonSchema)]
pub struct GetFileParams {
    /// Path to file (relative to repo root)
    pub file_path: String,
    /// Start line (1-indexed)
    pub start_line: Option<u64>,
    /// End line (1-indexed)
    pub end_line: Option<u64>,
}

// get_architecture and list_detectors take no parameters

// ── Evolution Tools ─────────────────────────────────────────────────────

#[derive(Debug, Deserialize, JsonSchema)]
pub struct QueryEvolutionParams {
    /// Type of temporal query to run
    pub query_type: EvolutionQueryType,
    /// File path (required for file_churn, file_commits, function_history, entity_blame, file_ownership)
    pub file: Option<String>,
    /// Function or class name (for function_history)
    pub name: Option<String>,
    /// Start line (for entity_blame)
    pub line_start: Option<u32>,
    /// End line (for entity_blame)
    pub line_end: Option<u32>,
    /// Maximum results to return (default: 20)
    pub limit: Option<u64>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum EvolutionQueryType {
    /// Get churn metrics for a single file
    FileChurn,
    /// Rank all files by churn (most frequently changed)
    HottestFiles,
    /// Get commit history for a specific file
    FileCommits,
    /// Get commits that touched a function's line range
    FunctionHistory,
    /// Get ownership info for a function/class (who, when, how many authors)
    EntityBlame,
    /// Get percentage ownership breakdown per author for a file
    FileOwnership,
    /// Get recent commits across the repo
    RecentCommits,
}

// ── AI Tools ────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize, JsonSchema)]
pub struct SearchCodeParams {
    /// Natural language search query
    pub query: String,
    /// Maximum number of results (default: 10)
    pub top_k: Option<u64>,
    /// Filter by entity type (Function, Class, File)
    pub entity_types: Option<Vec<String>>,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct AskParams {
    /// Natural language question about the codebase
    pub question: String,
    /// Number of context snippets to retrieve (default: 10)
    pub top_k: Option<u64>,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct GenerateFixParams {
    /// Index of the finding to fix (1-based, from analyze results)
    pub finding_id: String,
}
```

**Step 2: Verify it compiles**

Add `pub mod params;` to `src/mcp/mod.rs` temporarily and run:
`cd repotoire-cli && cargo check 2>&1 | tail -20`
Expected: Compiles

**Step 3: Commit**

```bash
git add repotoire-cli/src/mcp/params.rs
git commit -m "feat(mcp): add parameter structs with schemars JSON Schema generation"
```

---

## Task 3: Create Tool Handler Functions (Analysis)

**Files:**
- Create: `repotoire-cli/src/mcp/tools/analysis.rs`
- Create: `repotoire-cli/src/mcp/tools/mod.rs`

**Step 1: Create tools directory and mod.rs**

Create `repotoire-cli/src/mcp/tools/mod.rs`:

```rust
//! MCP Tool handler functions
//!
//! Each module contains the handler logic for a group of tools.
//! These functions accept HandlerState and return Result<Value, anyhow::Error>.

pub mod analysis;
pub mod graph;
pub mod files;
pub mod ai;
pub mod evolution;
```

**Step 2: Create analysis.rs with handlers migrated from handlers.rs**

Create `repotoire-cli/src/mcp/tools/analysis.rs`:

```rust
//! Analysis tool handlers: repotoire_analyze, repotoire_get_findings, repotoire_get_hotspots

use anyhow::{Context, Result};
use serde_json::{json, Value};

use crate::detectors::{default_detectors_with_ngram, walk_source_files, DetectorEngineBuilder, SourceFiles};
use crate::models::FindingsSummary;

use super::super::state::HandlerState;

/// Run code analysis on the repository
pub fn handle_analyze(state: &mut HandlerState, incremental: bool) -> Result<Value> {
    let _ = incremental; // Reserved for future incremental support
    let repo_path = state.repo_path.clone();
    let graph = state.graph()?;

    let project_config = crate::config::load_project_config(&repo_path);
    let style_profile = crate::calibrate::StyleProfile::load(&repo_path);
    let ngram = state.ngram_model();
    let mut engine = DetectorEngineBuilder::new()
        .workers(4)
        .detectors(default_detectors_with_ngram(
            &repo_path,
            &project_config,
            style_profile.as_ref(),
            ngram,
        ))
        .build();

    let all_files: Vec<std::path::PathBuf> = walk_source_files(&repo_path, None).collect();
    let source_files = SourceFiles::new(all_files, repo_path.to_path_buf());
    let findings = engine.run(&graph, &source_files)?;

    let summary = FindingsSummary::from_findings(&findings);

    Ok(json!({
        "status": "completed",
        "repo_path": repo_path.display().to_string(),
        "total_findings": summary.total,
        "by_severity": {
            "critical": summary.critical,
            "high": summary.high,
            "medium": summary.medium,
            "low": summary.low,
            "info": summary.info
        },
        "message": format!("Analysis complete. Found {} issues.", summary.total)
    }))
}

/// Get findings from the last analysis with pagination
pub fn handle_get_findings(
    state: &mut HandlerState,
    severity: Option<&str>,
    detector: Option<&str>,
    limit: usize,
    offset: usize,
) -> Result<Value> {
    let findings_path = state
        .repo_path
        .join(".repotoire")
        .join("last_findings.json");

    if findings_path.exists() {
        let content = std::fs::read_to_string(&findings_path)?;
        let parsed: Value = serde_json::from_str(&content)?;
        let mut findings: Vec<Value> = parsed
            .get("findings")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();

        if let Some(sev) = severity {
            findings.retain(|f| {
                f.get("severity")
                    .and_then(|v| v.as_str())
                    .map(|s| s == sev)
                    .unwrap_or(false)
            });
        }
        if let Some(det) = detector {
            findings.retain(|f| {
                f.get("detector")
                    .and_then(|v| v.as_str())
                    .map(|d| d == det)
                    .unwrap_or(false)
            });
        }

        let total_count = findings.len();
        let paginated: Vec<Value> = findings.into_iter().skip(offset).take(limit).collect();
        let returned = paginated.len();

        return Ok(json!({
            "findings": paginated,
            "total_count": total_count,
            "returned": returned,
            "offset": offset,
            "has_more": offset + returned < total_count
        }));
    }

    // Fall back to running analysis
    let graph = state.graph()?;
    let project_config = crate::config::load_project_config(&state.repo_path);
    let style_profile = crate::calibrate::StyleProfile::load(&state.repo_path);
    let ngram = state.ngram_model();
    let mut engine = DetectorEngineBuilder::new()
        .workers(4)
        .detectors(default_detectors_with_ngram(
            &state.repo_path,
            &project_config,
            style_profile.as_ref(),
            ngram,
        ))
        .build();

    let all_files: Vec<std::path::PathBuf> = walk_source_files(&state.repo_path, None).collect();
    let source_files = SourceFiles::new(all_files, state.repo_path.to_path_buf());
    let mut findings = engine.run(&graph, &source_files)?;

    if let Some(sev) = severity {
        findings.retain(|f| f.severity.to_string() == sev);
    }
    if let Some(det) = detector {
        findings.retain(|f| f.detector == det);
    }

    let total_count = findings.len();
    let paginated: Vec<_> = findings.into_iter().skip(offset).take(limit).collect();
    let returned = paginated.len();

    Ok(json!({
        "findings": paginated,
        "total_count": total_count,
        "returned": returned,
        "offset": offset,
        "has_more": offset + returned < total_count
    }))
}

/// Get hotspot files
pub fn handle_get_hotspots(state: &mut HandlerState, limit: usize) -> Result<Value> {
    let findings_path = state
        .repo_path
        .join(".repotoire")
        .join("last_findings.json");

    if !findings_path.exists() {
        anyhow::bail!(
            "No findings available. Run `repotoire_analyze` first, then use `repotoire_get_hotspots` to retrieve results."
        );
    }

    let content = std::fs::read_to_string(&findings_path)?;
    let parsed: Value = serde_json::from_str(&content)?;
    let findings: Vec<Value> = parsed
        .get("findings")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    let mut file_counts: std::collections::HashMap<String, (usize, Vec<String>)> =
        std::collections::HashMap::new();

    for finding in &findings {
        let Some(files) = finding.get("affected_files").and_then(|v| v.as_array()) else {
            continue;
        };
        for file in files {
            let Some(path) = file.as_str() else { continue };
            let entry = file_counts
                .entry(path.to_string())
                .or_insert((0, vec![]));
            entry.0 += 1;
            if let Some(sev) = finding.get("severity").and_then(|v| v.as_str()) {
                entry.1.push(sev.to_string());
            }
        }
    }

    let mut hotspots: Vec<Value> = file_counts
        .into_iter()
        .map(|(path, (count, severities))| {
            json!({
                "file_path": path,
                "finding_count": count,
                "severities": severities
            })
        })
        .collect();

    hotspots.sort_by(|a, b| {
        b.get("finding_count")
            .and_then(|v| v.as_u64())
            .unwrap_or(0)
            .cmp(
                &a.get("finding_count")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0),
            )
    });

    hotspots.truncate(limit);

    Ok(json!({ "hotspots": hotspots }))
}
```

**Step 3: Verify it compiles**

Wire up temporarily in mod.rs and run: `cd repotoire-cli && cargo check 2>&1 | tail -20`

**Step 4: Commit**

```bash
git add repotoire-cli/src/mcp/tools/
git commit -m "feat(mcp): add analysis tool handlers (analyze, get_findings, get_hotspots)"
```

---

## Task 4: Create Tool Handler Functions (Files)

**Files:**
- Create: `repotoire-cli/src/mcp/tools/files.rs`

**Step 1: Create files.rs with get_file, get_architecture, list_detectors**

Create `repotoire-cli/src/mcp/tools/files.rs`. Migrate logic from current `handlers.rs` for `handle_get_file`, `handle_get_architecture`, `handle_list_detectors`. Keep the path traversal protection. No functional changes except better error messages.

**Step 2: Verify compiles, commit**

```bash
git commit -m "feat(mcp): add file tool handlers (get_file, get_architecture, list_detectors)"
```

---

## Task 5: Create Tool Handler Functions (Graph — existing + new)

**Files:**
- Create: `repotoire-cli/src/mcp/tools/graph.rs`

**Step 1: Create graph.rs with query_graph, trace_dependencies, analyze_impact**

`handle_query_graph` — migrate from current handlers.rs, add callers/callees support and pagination.

`handle_trace_dependencies` — **NEW**. Use `GraphStore` to do BFS/DFS traversal from a named node, following edges of the requested kind up to `max_depth`. Return upstream and downstream dependency trees.

`handle_analyze_impact` — **NEW**. Given a target file or function, find all reverse dependents (nodes that call/import the target). Use `get_strongly_connected_components()` if available. Return affected file list with risk assessment.

**Step 2: Verify compiles, commit**

```bash
git commit -m "feat(mcp): add graph tool handlers (query_graph, trace_dependencies, analyze_impact)"
```

---

## Task 6: Create Tool Handler Functions (Evolution — new)

**Files:**
- Create: `repotoire-cli/src/mcp/tools/evolution.rs`

**Step 1: Create evolution.rs with query_evolution handler**

Create `repotoire-cli/src/mcp/tools/evolution.rs`. This is a **NEW** tool that dispatches to existing git2 APIs based on `query_type`:

- `file_churn` → `GitHistory::get_file_churn(file, 500)`
- `hottest_files` → `GitHistory::get_all_file_churn(500)`, sorted by commit count
- `file_commits` → `GitHistory::get_file_commits(file, limit)`
- `function_history` → Look up function in graph to get line range, then `GitHistory::get_line_range_commits(file, start, end, limit)`
- `entity_blame` → `GitBlame::get_entity_blame(file, line_start, line_end)`
- `file_ownership` → `GitBlame::get_file_ownership(file)`
- `recent_commits` → `GitHistory::get_recent_commits(limit, None)`

Each query type validates its required parameters and returns actionable error messages when parameters are missing (e.g., "file_churn requires the 'file' parameter").

**Step 2: Verify compiles, commit**

```bash
git commit -m "feat(mcp): add evolution tool handler (query_evolution — 7 temporal query types)"
```

---

## Task 7: Create Tool Handler Functions (AI)

**Files:**
- Create: `repotoire-cli/src/mcp/tools/ai.rs`

**Step 1: Create ai.rs with search_code, ask, generate_fix**

Migrate directly from current `handlers.rs`. The PRO API handlers (`handle_search_code`, `handle_ask`) and BYOK handler (`handle_generate_fix`, `handle_generate_fix_local`) are unchanged except:
- Better error messages
- Parameter extraction uses the new param structs

**Step 2: Verify compiles, commit**

```bash
git commit -m "feat(mcp): add AI tool handlers (search_code, ask, generate_fix)"
```

---

## Task 8: Create HandlerState Module

**Files:**
- Create: `repotoire-cli/src/mcp/state.rs`

**Step 1: Extract HandlerState from handlers.rs into state.rs**

Move `HandlerState` struct and its `impl` block to `state.rs`. This is a direct extraction — same code, new file. The state manages:
- `repo_path: PathBuf`
- `graph: Option<Arc<GraphStore>>` (lazy)
- `ngram_model: Option<NgramModel>` (lazy)
- `api_key: Option<String>`
- `api_url: String`
- `ai_backend: Option<LlmBackend>`

Add `force_local: bool` field to the constructor to replace the server-level flag.

**Step 2: Verify compiles, commit**

```bash
git commit -m "refactor(mcp): extract HandlerState into dedicated state module"
```

---

## Task 9: Create rmcp Server with Tool Router

**Files:**
- Rewrite: `repotoire-cli/src/mcp/server.rs`

**Step 1: Write the rmcp server implementation**

Create the new `server.rs` with `RepotoireServer` struct implementing `ServerHandler` via `#[tool_router]` + `#[tool_handler]`. Each `#[tool]` method:
1. Extracts params via `Parameters<T>`
2. Clones the `Arc<RwLock<HandlerState>>`
3. Calls `tokio::task::spawn_blocking` with the sync handler function
4. Maps `anyhow::Error` to `McpError::internal_error`
5. Returns `CallToolResult::success` with JSON text content

Wire all 13 tools:
- `repotoire_analyze` → `tools::analysis::handle_analyze`
- `repotoire_get_findings` → `tools::analysis::handle_get_findings`
- `repotoire_get_hotspots` → `tools::analysis::handle_get_hotspots`
- `repotoire_query_graph` → `tools::graph::handle_query_graph`
- `repotoire_trace_dependencies` → `tools::graph::handle_trace_dependencies`
- `repotoire_analyze_impact` → `tools::graph::handle_analyze_impact`
- `repotoire_get_file` → `tools::files::handle_get_file`
- `repotoire_get_architecture` → `tools::files::handle_get_architecture`
- `repotoire_list_detectors` → `tools::files::handle_list_detectors`
- `repotoire_query_evolution` → `tools::evolution::handle_query_evolution`
- `repotoire_search_code` → `tools::ai::handle_search_code`
- `repotoire_ask` → `tools::ai::handle_ask`
- `repotoire_generate_fix` → `tools::ai::handle_generate_fix`

Security validations (input size, argument size) applied before `spawn_blocking`.

**Step 2: Verify compiles**

Run: `cd repotoire-cli && cargo check 2>&1 | tail -20`

**Step 3: Commit**

```bash
git commit -m "feat(mcp): implement rmcp ServerHandler with 13 tools via tool_router"
```

---

## Task 10: Create Streamable HTTP Transport Module

**Files:**
- Create: `repotoire-cli/src/mcp/transport.rs`

**Step 1: Write HTTP transport setup**

Create `transport.rs` with a function that sets up Streamable HTTP via axum:

```rust
use rmcp::transport::streamable_http_server::{
    StreamableHttpServerConfig, StreamableHttpService,
    session::local::LocalSessionManager,
};

pub async fn serve_http(
    server_factory: impl Fn() -> anyhow::Result<RepotoireServer> + Send + Sync + 'static,
    port: u16,
) -> anyhow::Result<()> {
    let ct = tokio_util::sync::CancellationToken::new();
    let service = StreamableHttpService::new(
        move || server_factory().map_err(|e| e.into()),
        LocalSessionManager::default().into(),
        StreamableHttpServerConfig {
            cancellation_token: ct.child_token(),
            ..Default::default()
        },
    );
    let router = axum::Router::new().nest_service("/mcp", service);
    let listener = tokio::net::TcpListener::bind(("0.0.0.0", port)).await?;
    axum::serve(listener, router)
        .with_graceful_shutdown(async move {
            tokio::signal::ctrl_c().await.ok();
            ct.cancel();
        })
        .await?;
    Ok(())
}
```

**Step 2: Verify compiles, commit**

```bash
git commit -m "feat(mcp): add Streamable HTTP transport module"
```

---

## Task 11: Rewrite Module Entry Point

**Files:**
- Rewrite: `repotoire-cli/src/mcp/mod.rs`

**Step 1: Rewrite mod.rs to wire everything together**

```rust
//! MCP (Model Context Protocol) Server
//!
//! Implements MCP 2025-06-18 via rmcp SDK with stdio and Streamable HTTP transports.

mod params;
mod server;
mod state;
mod tools;
mod transport;

pub use server::RepotoireServer;
pub use state::HandlerState;

use std::path::PathBuf;
use anyhow::Result;
use rmcp::{ServiceExt, transport::stdio};

/// Run MCP server on stdio (default) or HTTP
pub async fn run_mcp_server(
    repo_path: PathBuf,
    force_local: bool,
    http_port: Option<u16>,
) -> Result<()> {
    eprintln!("🎼 Repotoire MCP server starting...");
    eprintln!("   Repository: {}", repo_path.display());

    if let Some(port) = http_port {
        eprintln!("   Transport: Streamable HTTP on port {}", port);
        let rp = repo_path.clone();
        let fl = force_local;
        transport::serve_http(
            move || Ok(RepotoireServer::new(HandlerState::new(rp.clone(), fl))),
            port,
        )
        .await?;
    } else {
        eprintln!("   Transport: stdio (JSON-RPC 2.0)");
        eprintln!("   Ready. Waiting for MCP messages on stdin...");
        let state = HandlerState::new(repo_path, force_local);
        let service = RepotoireServer::new(state)
            .serve(stdio())
            .await
            .inspect_err(|e| tracing::error!("MCP serve error: {:?}", e))?;
        service.waiting().await?;
    }
    Ok(())
}
```

**Step 2: Delete old files**

Remove `repotoire-cli/src/mcp/handlers.rs` and `repotoire-cli/src/mcp/tools.rs` (the old manual schema file — NOT the new `tools/` directory).

**Step 3: Verify compiles, commit**

```bash
git commit -m "feat(mcp): rewrite module entry point with rmcp stdio + HTTP transports"
```

---

## Task 12: Update CLI Entry Point

**Files:**
- Modify: `repotoire-cli/src/cli/serve.rs`
- Modify: `repotoire-cli/src/cli/mod.rs`

**Step 1: Update serve.rs to create Tokio runtime**

```rust
use std::path::Path;
use anyhow::Result;

pub fn run(repo_path: &Path, force_local: bool, http_port: Option<u16>) -> Result<()> {
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?
        .block_on(crate::mcp::run_mcp_server(
            repo_path.to_path_buf(),
            force_local,
            http_port,
        ))
}
```

**Step 2: Add --http-port flag to CLI**

In `src/cli/mod.rs`, find the `serve` subcommand and add:

```rust
/// Optional HTTP port for Streamable HTTP transport (default: stdio)
#[arg(long)]
http_port: Option<u16>,
```

Pass it through to `serve::run()`.

**Step 3: Verify compiles, commit**

```bash
git commit -m "feat(mcp): update CLI serve command with Tokio runtime and --http-port flag"
```

---

## Task 13: Write Unit Tests

**Files:**
- Create: `repotoire-cli/tests/mcp/mod.rs` (or add tests inline in each module)

**Step 1: Write tests for parameter deserialization**

Test that each param struct correctly deserializes from JSON (validates the schemas agents will send).

**Step 2: Write tests for each handler function**

For each handler, create a tempdir with test data, initialize `GraphStore::in_memory()`, and verify the response JSON shape:

- `test_analyze_returns_summary` — analyze empty repo returns zero findings
- `test_get_findings_pagination` — verify limit/offset/has_more
- `test_get_file_path_traversal` — verify `../` is rejected
- `test_get_file_success` — read a test file
- `test_query_graph_functions` — query functions from test graph
- `test_trace_dependencies_basic` — trace from a node with known edges
- `test_analyze_impact_basic` — find dependents of a known node
- `test_query_evolution_no_git` — graceful error when not a git repo
- `test_list_detectors` — returns non-empty list

**Step 3: Write integration test for MCP lifecycle**

Test the full stdio lifecycle by spawning the server as a child process:

```rust
#[tokio::test]
async fn test_stdio_lifecycle() {
    // Spawn: repotoire serve --local
    // Send: initialize request
    // Assert: get ServerInfo with protocol version
    // Send: tools/list
    // Assert: 13 tools (or 10 if no AI keys)
    // Send: tools/call get_architecture
    // Assert: valid response
    // Send: shutdown
}
```

**Step 4: Run all tests**

Run: `cd repotoire-cli && cargo test --lib -- mcp 2>&1 | tail -40`
Expected: All tests pass

**Step 5: Commit**

```bash
git commit -m "test(mcp): add unit and integration tests for rmcp MCP server"
```

---

## Task 14: Update CLAUDE.md and Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `repotoire-cli/docs/MCP.md`

**Step 1: Update CLAUDE.md MCP section**

Replace the outdated MCP section (references `health_check`, `analyze_codebase`, Python `repotoire-mcp`) with accurate information about the 13 tools, the `repotoire serve` command, and Claude Code configuration using the Rust binary.

**Step 2: Update MCP.md documentation**

Update `repotoire-cli/docs/MCP.md` to reflect the new tools, parameter schemas, and HTTP transport option.

**Step 3: Commit**

```bash
git commit -m "docs: update CLAUDE.md and MCP.md for rmcp-based MCP server with 13 tools"
```

---

## Task 15: Final Verification

**Step 1: Full cargo check + clippy**

Run: `cd repotoire-cli && cargo check && cargo clippy -- -D warnings 2>&1 | tail -40`
Expected: No errors, no warnings

**Step 2: Full test suite**

Run: `cd repotoire-cli && cargo test 2>&1 | tail -40`
Expected: All tests pass

**Step 3: Manual smoke test (stdio)**

```bash
cd repotoire-cli && echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' | cargo run -- serve --local 2>/dev/null | head -1
```

Expected: JSON response with `protocolVersion` and `serverInfo`

**Step 4: Commit any fixes, tag**

```bash
git commit -m "chore: final MCP overhaul cleanup"
```
