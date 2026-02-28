# Repotoire Workstation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an AI agent with graph-aware tools and a two-panel TUI to the existing repotoire CLI.

**Architecture:** New `workstation/` module inside `repotoire-cli/src/`. Agent loop streams from Anthropic, dispatches tool calls (file ops on tokio, graph queries on rayon via `tokio_rayon`), and pushes deltas to the TUI via `tokio::sync::watch`. Existing graph, detectors, parsers, and file watcher are reused as-is.

**Tech Stack:** Rust, tokio, rayon, tokio-rayon, ratatui, reqwest, reqwest-eventsource, arc-swap, serde_json

**Design doc:** `docs/plans/2026-02-27-repotoire-workstation-design.md`

---

### Task 1: Add New Dependencies to Cargo.toml

**Files:**
- Modify: `repotoire-cli/Cargo.toml`

**Step 1: Add the 5 new crates**

Add to `[dependencies]` in `repotoire-cli/Cargo.toml`:

```toml
reqwest = { version = "0.12", features = ["json", "stream"] }
reqwest-eventsource = "0.6"
tokio-rayon = "2"
arc-swap = "1"
```

Note: `ratatui-interact` deferred to Task 8 (TUI). `ratatui`, `tokio`, `rayon`, `crossterm`, `dashmap`, `serde`, `serde_json` are already present.

**Step 2: Verify it compiles**

Run: `cd repotoire-cli && cargo check`
Expected: Compiles with no errors (new deps unused but resolved).

**Step 3: Commit**

```bash
git add repotoire-cli/Cargo.toml
git commit -m "deps: add reqwest, reqwest-eventsource, tokio-rayon, arc-swap for workstation"
```

---

### Task 2: Shared State Types + Workstation Module Scaffold

**Files:**
- Create: `repotoire-cli/src/workstation/mod.rs`
- Create: `repotoire-cli/src/workstation/state.rs`
- Create: `repotoire-cli/src/workstation/agent/mod.rs`
- Create: `repotoire-cli/src/workstation/tools/mod.rs`
- Create: `repotoire-cli/src/workstation/panels/mod.rs`
- Modify: `repotoire-cli/src/main.rs` (add `pub mod workstation;`)

**Step 1: Create workstation module with shared state**

`repotoire-cli/src/workstation/mod.rs`:
```rust
pub mod agent;
pub mod panels;
pub mod state;
pub mod tools;
```

`repotoire-cli/src/workstation/state.rs`:
```rust
use arc_swap::ArcSwap;
use dashmap::DashMap;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::sync::{watch, RwLock};

use crate::graph::GraphStore;
use crate::models::Finding;

/// Agent streaming state — TUI reads this via watch channel
#[derive(Clone, Debug, Default)]
pub struct StreamState {
    pub status: AgentStatus,
    pub current_text: String,
    pub current_tool: Option<String>,
    pub tools_completed: usize,
    pub tools_total: usize,
}

#[derive(Clone, Debug, Default)]
pub enum AgentStatus {
    #[default]
    Idle,
    Streaming,
    ExecutingTool(String),
    Error(String),
}

/// Message in the conversation
#[derive(Clone, Debug)]
pub struct Message {
    pub role: MessageRole,
    pub content: String,
    pub tool_calls: Vec<ToolCall>,
    pub tool_results: Vec<ToolResult>,
}

#[derive(Clone, Debug)]
pub enum MessageRole {
    User,
    Assistant,
    ToolResult,
}

#[derive(Clone, Debug)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub input: serde_json::Value,
}

#[derive(Clone, Debug)]
pub struct ToolResult {
    pub tool_call_id: String,
    pub content: String,
    pub is_error: bool,
}

/// Shared workstation state — Arc'd once, shared everywhere
pub struct WorkstationState {
    pub graph: ArcSwap<GraphStore>,
    pub findings: ArcSwap<Vec<Finding>>,
    pub conversation: RwLock<Vec<Message>>,
    pub file_locks: DashMap<PathBuf, ()>,
    pub agent_stream_tx: watch::Sender<StreamState>,
    pub agent_stream_rx: watch::Receiver<StreamState>,
    pub repo_path: PathBuf,
    pub graph_ready: std::sync::atomic::AtomicBool,
}

impl WorkstationState {
    pub fn new(repo_path: PathBuf) -> Arc<Self> {
        let (tx, rx) = watch::channel(StreamState::default());
        // Start with empty graph — loaded lazily in background
        let empty_graph = GraphStore::new();
        Arc::new(Self {
            graph: ArcSwap::from_pointee(empty_graph),
            findings: ArcSwap::from_pointee(Vec::new()),
            conversation: RwLock::new(Vec::new()),
            file_locks: DashMap::new(),
            agent_stream_tx: tx,
            agent_stream_rx: rx,
            repo_path,
            graph_ready: std::sync::atomic::AtomicBool::new(false),
        })
    }
}
```

Create empty sub-module files:

`repotoire-cli/src/workstation/agent/mod.rs`:
```rust
pub mod streaming;
pub mod agent_loop;
pub mod tools;
```

`repotoire-cli/src/workstation/tools/mod.rs`:
```rust
pub mod bash;
pub mod files;
pub mod search;
pub mod graph;
```

`repotoire-cli/src/workstation/panels/mod.rs`:
```rust
pub mod conversation;
pub mod findings;
```

**Step 2: Wire into main.rs**

Add `pub mod workstation;` to `repotoire-cli/src/main.rs` module declarations.

**Step 3: Verify it compiles**

Run: `cd repotoire-cli && cargo check`
Expected: Compiles. Empty sub-modules may warn about unused imports — that's fine.

**Step 4: Commit**

```bash
git add repotoire-cli/src/workstation/ repotoire-cli/src/main.rs
git commit -m "feat: scaffold workstation module with shared state types"
```

---

### Task 3: Anthropic Streaming Provider

**Files:**
- Create: `repotoire-cli/src/workstation/agent/streaming.rs`
- Create: `repotoire-cli/tests/streaming_test.rs` (or inline tests)

This is the highest-risk code. Build and test in isolation.

**Step 1: Write a test for SSE parsing**

Create a unit test that feeds recorded SSE data and verifies correct parsing of text deltas and tool calls. Test in `repotoire-cli/src/workstation/agent/streaming.rs` as `#[cfg(test)] mod tests`.

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_text_delta() {
        let mut parser = SseResponseParser::new();
        // Simulate content_block_start for text
        parser.handle_event("content_block_start", r#"{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}"#).unwrap();
        // Simulate text delta
        parser.handle_event("content_block_delta", r#"{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}"#).unwrap();
        parser.handle_event("content_block_delta", r#"{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" world"}}"#).unwrap();
        parser.handle_event("content_block_stop", r#"{"type":"content_block_stop","index":0}"#).unwrap();

        assert_eq!(parser.text(), "Hello world");
        assert!(parser.tool_calls().is_empty());
    }

    #[test]
    fn test_parse_tool_call() {
        let mut parser = SseResponseParser::new();
        // tool_use content block
        parser.handle_event("content_block_start", r#"{"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_123","name":"read"}}"#).unwrap();
        parser.handle_event("content_block_delta", r#"{"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\"file_pa"}}"#).unwrap();
        parser.handle_event("content_block_delta", r#"{"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"th\": \"src/main.rs\"}"}}"#).unwrap();
        parser.handle_event("content_block_stop", r#"{"type":"content_block_stop","index":1}"#).unwrap();

        assert_eq!(parser.tool_calls().len(), 1);
        assert_eq!(parser.tool_calls()[0].name, "read");
        assert_eq!(parser.tool_calls()[0].input["file_path"], "src/main.rs");
    }

    #[test]
    fn test_parse_malformed_tool_json() {
        let mut parser = SseResponseParser::new();
        parser.handle_event("content_block_start", r#"{"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_456","name":"bash"}}"#).unwrap();
        parser.handle_event("content_block_delta", r#"{"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\"command\": "}}"#).unwrap();
        // Missing closing — malformed
        parser.handle_event("content_block_stop", r#"{"type":"content_block_stop","index":1}"#).unwrap();

        // Should capture as error, not panic
        assert_eq!(parser.tool_calls().len(), 1);
        assert!(parser.tool_calls()[0].input.is_null() || parser.tool_calls()[0].parse_error.is_some());
    }
}
```

**Step 2: Run tests to verify they fail**

Run: `cd repotoire-cli && cargo test --lib workstation::agent::streaming`
Expected: FAIL — `SseResponseParser` doesn't exist yet.

**Step 3: Implement SseResponseParser**

`repotoire-cli/src/workstation/agent/streaming.rs`:

```rust
//! Anthropic SSE streaming parser and client
//!
//! Handles the streaming Messages API response format:
//! - Buffers partial tool call JSON across content_block_delta events
//! - Emits text deltas immediately for TUI rendering
//! - Assembles complete ToolCall structs on content_block_stop

use anyhow::{bail, Result};
use reqwest::Client;
use reqwest_eventsource::{Event, EventSource, RequestBuilderExt};
use serde::{Deserialize, Serialize};
use tokio::sync::watch;

use crate::workstation::state::{StreamState, AgentStatus, ToolCall, ToolResult, Message, MessageRole};

/// Parses SSE events into text + tool calls
pub struct SseResponseParser {
    text_blocks: Vec<String>,
    tool_buffers: Vec<ToolBuffer>,
    current_block_index: Option<usize>,
    usage: Option<Usage>,
    stop_reason: Option<String>,
}

struct ToolBuffer {
    id: String,
    name: String,
    json_fragments: Vec<String>,
    parse_error: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Usage {
    pub input_tokens: u32,
    pub output_tokens: u32,
}

// SSE event payload types (from Anthropic API)
#[derive(Deserialize)]
struct ContentBlockStart {
    index: usize,
    content_block: ContentBlock,
}

#[derive(Deserialize)]
struct ContentBlock {
    #[serde(rename = "type")]
    block_type: String,
    #[serde(default)]
    text: String,
    #[serde(default)]
    id: String,
    #[serde(default)]
    name: String,
}

#[derive(Deserialize)]
struct ContentBlockDelta {
    index: usize,
    delta: Delta,
}

#[derive(Deserialize)]
struct Delta {
    #[serde(rename = "type")]
    delta_type: String,
    #[serde(default)]
    text: String,
    #[serde(default)]
    partial_json: String,
}

#[derive(Deserialize)]
struct MessageDelta {
    delta: MessageDeltaInner,
    usage: Option<Usage>,
}

#[derive(Deserialize)]
struct MessageDeltaInner {
    stop_reason: Option<String>,
}

impl SseResponseParser {
    pub fn new() -> Self {
        Self {
            text_blocks: Vec::new(),
            tool_buffers: Vec::new(),
            current_block_index: None,
            usage: None,
            stop_reason: None,
        }
    }

    pub fn handle_event(&mut self, event_type: &str, data: &str) -> Result<Option<String>> {
        match event_type {
            "content_block_start" => {
                let parsed: ContentBlockStart = serde_json::from_str(data)?;
                match parsed.content_block.block_type.as_str() {
                    "text" => {
                        // Ensure text_blocks has enough slots
                        while self.text_blocks.len() <= parsed.index {
                            self.text_blocks.push(String::new());
                        }
                    }
                    "tool_use" => {
                        self.tool_buffers.push(ToolBuffer {
                            id: parsed.content_block.id,
                            name: parsed.content_block.name,
                            json_fragments: Vec::new(),
                            parse_error: None,
                        });
                    }
                    _ => {}
                }
                self.current_block_index = Some(parsed.index);
                Ok(None)
            }
            "content_block_delta" => {
                let parsed: ContentBlockDelta = serde_json::from_str(data)?;
                match parsed.delta.delta_type.as_str() {
                    "text_delta" => {
                        if let Some(block) = self.text_blocks.get_mut(parsed.index) {
                            block.push_str(&parsed.delta.text);
                        }
                        // Return the delta for immediate TUI rendering
                        Ok(Some(parsed.delta.text))
                    }
                    "input_json_delta" => {
                        if let Some(buf) = self.tool_buffers.last_mut() {
                            buf.json_fragments.push(parsed.delta.partial_json);
                        }
                        Ok(None)
                    }
                    _ => Ok(None),
                }
            }
            "content_block_stop" => {
                // Finalize tool call JSON if this was a tool block
                if let Some(buf) = self.tool_buffers.last_mut() {
                    if buf.parse_error.is_none() {
                        let full_json: String = buf.json_fragments.join("");
                        match serde_json::from_str::<serde_json::Value>(&full_json) {
                            Ok(_) => {} // JSON is valid, keep fragments
                            Err(e) => {
                                buf.parse_error = Some(format!(
                                    "Malformed tool call JSON: {}. Raw: {}",
                                    e, full_json
                                ));
                            }
                        }
                    }
                }
                self.current_block_index = None;
                Ok(None)
            }
            "message_delta" => {
                let parsed: MessageDelta = serde_json::from_str(data)?;
                self.stop_reason = parsed.delta.stop_reason;
                if let Some(usage) = parsed.usage {
                    self.usage = Some(usage);
                }
                Ok(None)
            }
            "message_start" | "message_stop" | "ping" => Ok(None),
            _ => Ok(None), // Unknown events — ignore
        }
    }

    pub fn text(&self) -> String {
        self.text_blocks.join("")
    }

    pub fn tool_calls(&self) -> Vec<ToolCall> {
        self.tool_buffers
            .iter()
            .map(|buf| {
                let full_json: String = buf.json_fragments.join("");
                let input = serde_json::from_str(&full_json).unwrap_or(serde_json::Value::Null);
                ToolCall {
                    id: buf.id.clone(),
                    name: buf.name.clone(),
                    input,
                }
            })
            .collect()
    }

    pub fn stop_reason(&self) -> Option<&str> {
        self.stop_reason.as_deref()
    }

    pub fn usage(&self) -> Option<&Usage> {
        self.usage.as_ref()
    }
}

/// Anthropic streaming client
pub struct AnthropicStreaming {
    client: Client,
    api_key: String,
    model: String,
    max_tokens: u32,
}

/// Request body for Anthropic Messages API
#[derive(Serialize)]
struct ApiRequest {
    model: String,
    max_tokens: u32,
    stream: bool,
    system: String,
    messages: Vec<ApiMessage>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    tools: Vec<serde_json::Value>,
}

#[derive(Serialize)]
struct ApiMessage {
    role: String,
    content: serde_json::Value,
}

impl AnthropicStreaming {
    pub fn new(api_key: String, model: String, max_tokens: u32) -> Self {
        Self {
            client: Client::new(),
            api_key,
            model,
            max_tokens,
        }
    }

    /// Stream a message, pushing text deltas to the watch channel.
    /// Returns the complete parsed response (text + tool calls).
    pub async fn stream(
        &self,
        system_prompt: &str,
        messages: &[Message],
        tool_schemas: &[serde_json::Value],
        stream_tx: &watch::Sender<StreamState>,
    ) -> Result<(String, Vec<ToolCall>, Option<Usage>)> {
        let api_messages = self.convert_messages(messages);
        let body = ApiRequest {
            model: self.model.clone(),
            max_tokens: self.max_tokens,
            stream: true,
            system: system_prompt.to_string(),
            messages: api_messages,
            tools: tool_schemas.to_vec(),
        };

        let request = self.client
            .post("https://api.anthropic.com/v1/messages")
            .header("x-api-key", &self.api_key)
            .header("anthropic-version", "2023-06-01")
            .header("content-type", "application/json")
            .json(&body);

        let mut es = request.eventsource()?;
        let mut parser = SseResponseParser::new();

        stream_tx.send_modify(|s| s.status = AgentStatus::Streaming);

        while let Some(event) = es.next().await {
            match event {
                Ok(Event::Open) => {}
                Ok(Event::Message(msg)) => {
                    if let Some(text_delta) = parser.handle_event(&msg.event, &msg.data)? {
                        stream_tx.send_modify(|s| {
                            s.current_text.push_str(&text_delta);
                        });
                    }
                }
                Err(reqwest_eventsource::Error::StreamEnded) => break,
                Err(e) => {
                    es.close();
                    bail!("SSE stream error: {}", e);
                }
            }
        }

        stream_tx.send_modify(|s| s.status = AgentStatus::Idle);

        Ok((parser.text(), parser.tool_calls(), parser.usage().cloned()))
    }

    fn convert_messages(&self, messages: &[Message]) -> Vec<ApiMessage> {
        // Convert internal Message types to Anthropic API format
        messages.iter().map(|m| {
            match m.role {
                MessageRole::User => ApiMessage {
                    role: "user".to_string(),
                    content: serde_json::Value::String(m.content.clone()),
                },
                MessageRole::Assistant => {
                    let mut content = vec![];
                    if !m.content.is_empty() {
                        content.push(serde_json::json!({"type": "text", "text": m.content}));
                    }
                    for tc in &m.tool_calls {
                        content.push(serde_json::json!({
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.input,
                        }));
                    }
                    ApiMessage {
                        role: "assistant".to_string(),
                        content: serde_json::Value::Array(content),
                    }
                }
                MessageRole::ToolResult => {
                    let content: Vec<serde_json::Value> = m.tool_results.iter().map(|tr| {
                        serde_json::json!({
                            "type": "tool_result",
                            "tool_use_id": tr.tool_call_id,
                            "content": tr.content,
                            "is_error": tr.is_error,
                        })
                    }).collect();
                    ApiMessage {
                        role: "user".to_string(),
                        content: serde_json::Value::Array(content),
                    }
                }
            }
        }).collect()
    }
}
```

**Step 4: Run tests to verify they pass**

Run: `cd repotoire-cli && cargo test --lib workstation::agent::streaming`
Expected: All 3 tests pass.

**Step 5: Commit**

```bash
git add repotoire-cli/src/workstation/agent/streaming.rs
git commit -m "feat: Anthropic SSE streaming parser with tool call buffering"
```

---

### Task 4: Coding Tools (bash, read, write, edit, grep)

**Files:**
- Create: `repotoire-cli/src/workstation/tools/bash.rs`
- Create: `repotoire-cli/src/workstation/tools/files.rs`
- Create: `repotoire-cli/src/workstation/tools/search.rs`

**Step 1: Write tests for the edit tool (highest risk)**

In `repotoire-cli/src/workstation/tools/files.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::NamedTempFile;

    #[test]
    fn test_edit_unique_match() {
        let mut f = NamedTempFile::new().unwrap();
        fs::write(f.path(), "fn main() {\n    println!(\"hello\");\n}\n").unwrap();
        let result = edit_file(f.path(), "println!(\"hello\")", "println!(\"world\")").unwrap();
        assert!(result.contains("1 replacement"));
        let content = fs::read_to_string(f.path()).unwrap();
        assert!(content.contains("println!(\"world\")"));
    }

    #[test]
    fn test_edit_no_match() {
        let mut f = NamedTempFile::new().unwrap();
        fs::write(f.path(), "fn main() {}\n").unwrap();
        let result = edit_file(f.path(), "nonexistent text", "replacement");
        assert!(result.is_err());
        let err = result.unwrap_err().to_string();
        assert!(err.contains("0 matches"));
    }

    #[test]
    fn test_edit_multiple_matches() {
        let mut f = NamedTempFile::new().unwrap();
        fs::write(f.path(), "let x = 1;\nlet y = 1;\n").unwrap();
        let result = edit_file(f.path(), "= 1", "= 2");
        assert!(result.is_err());
        let err = result.unwrap_err().to_string();
        assert!(err.contains("2 matches"));
    }
}
```

**Step 2: Run tests to verify they fail**

Run: `cd repotoire-cli && cargo test --lib workstation::tools::files`
Expected: FAIL — functions don't exist.

**Step 3: Implement all coding tools**

Implement `bash.rs`, `files.rs` (read, write, edit), `search.rs` (grep). Each tool is a standalone async function that takes `serde_json::Value` params and returns `Result<String>`.

See design doc for specifications of each tool. Key details:
- `bash`: `tokio::process::Command`, timeout via `tokio::time::timeout`, kill via `child.kill()`
- `read`: `tokio::fs::read_to_string`, optional `offset`/`limit` line range
- `write`: `tokio::fs::write`, `tokio::fs::create_dir_all` for parents
- `edit`: Read file, count matches of `old_string`, fail if != 1, replace, write back
- `grep`: `tokio::process::Command` running `rg --json`, parse output

**Step 4: Run tests to verify they pass**

Run: `cd repotoire-cli && cargo test --lib workstation::tools`
Expected: All pass.

**Step 5: Commit**

```bash
git add repotoire-cli/src/workstation/tools/
git commit -m "feat: coding tools — bash, read, write, edit, grep"
```

---

### Task 5: Graph Tools (wrappers around existing GraphStore)

**Files:**
- Create: `repotoire-cli/src/workstation/tools/graph.rs`

**Step 1: Write test for graph tool dispatch**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_graph_tool_schemas() {
        let schemas = graph_tool_schemas();
        let names: Vec<&str> = schemas.iter()
            .map(|s| s["name"].as_str().unwrap())
            .collect();
        assert!(names.contains(&"query_graph"));
        assert!(names.contains(&"get_findings"));
    }
}
```

**Step 2: Run test to verify it fails**

Run: `cd repotoire-cli && cargo test --lib workstation::tools::graph`
Expected: FAIL.

**Step 3: Implement graph tools**

These are thin wrappers that translate JSON params into calls to `GraphStore` methods and format results as text for the LLM. Reference the existing MCP handlers in `mcp/tools/graph_queries.rs` for the exact function signatures.

Tools to wrap:
- `query_graph` — dispatches to `get_functions()`, `get_callers()`, `get_callees()`, etc. based on a `query_type` param
- `trace_dependencies` — BFS/DFS reusing logic from `mcp/tools/graph_queries.rs`
- `analyze_impact` — change impact, reusing `mcp/tools/graph_queries.rs`
- `get_findings` — reads from the `ArcSwap<Vec<Finding>>` in shared state
- `run_detectors` — runs specified detectors on specified files

**Important: Two Axon-inspired enhancements to include in this task (zero new logic, just better formatting):**

1. **Next-step hints in every tool response.** Append contextual suggestions to tool output:
   - After `get_callers(fn)`: `"\n\nHint: Use get_callees on these callers to find shared patterns. Use analyze_impact to see full blast radius."`
   - After `get_findings`: `"\n\nHint: Use query_graph with a finding's qualified name to see its dependency context."`
   - After `analyze_impact`: `"\n\nHint: High fan-in functions at depth 0 are risky to change. Check test coverage with get_callers."`

2. **Depth-tiered impact analysis output.** The existing BFS in `mcp/tools/graph_queries.rs` already tracks depth — expose it in the formatted response:
   ```
   Depth 0 (direct): authenticate_user, validate_session (2 functions)
   Depth 1 (indirect): login_handler, api_middleware (2 functions)
   Depth 2 (transitive): main_router (1 function)
   Total blast radius: 5 functions across 3 files
   ```
   This replaces a flat list with grouped tiers. Much more useful for the agent to reason about change risk.

**Step 4: Run tests**

Run: `cd repotoire-cli && cargo test --lib workstation::tools::graph`
Expected: PASS.

**Step 5: Commit**

```bash
git add repotoire-cli/src/workstation/tools/graph.rs
git commit -m "feat: graph tools wrapping existing GraphStore queries"
```

---

### Task 6: Tool Registry + Agent Loop

**Files:**
- Create: `repotoire-cli/src/workstation/agent/tools.rs`
- Create: `repotoire-cli/src/workstation/agent/agent_loop.rs`

**Step 1: Implement tool registry**

`repotoire-cli/src/workstation/agent/tools.rs`:

The registry holds tool schemas (for the LLM system prompt) and maps tool names to executor functions. Each tool declares its execution strategy (CPU vs IO).

```rust
pub enum ExecStrategy {
    Cpu,  // graph queries → tokio_rayon::spawn
    Io,   // bash, files → tokio async
}

pub struct ToolDef {
    pub name: String,
    pub description: String,
    pub schema: serde_json::Value,
    pub strategy: ExecStrategy,
}

pub struct ToolRegistry {
    tools: Vec<ToolDef>,
}

impl ToolRegistry {
    pub fn new() -> Self { /* register all tools */ }
    pub fn schemas(&self) -> Vec<serde_json::Value> { /* JSON schemas for API */ }
    pub fn get(&self, name: &str) -> Option<&ToolDef> { /* lookup */ }
}
```

**Step 2: Implement agent loop**

`repotoire-cli/src/workstation/agent/agent_loop.rs`:

Core cycle as described in design doc. Key structure:

```rust
pub async fn run_agent(
    state: Arc<WorkstationState>,
    provider: &AnthropicStreaming,
    system_prompt: &str,
    user_message: String,
    tools: &ToolRegistry,
) -> Result<()> {
    let mut messages = state.conversation.write().await;
    messages.push(Message::user(user_message));

    loop {
        let (text, tool_calls, usage) = provider.stream(
            system_prompt,
            &messages,
            &tools.schemas(),
            &state.agent_stream_tx,
        ).await?;

        messages.push(Message::assistant_with_tools(text, tool_calls.clone()));

        if tool_calls.is_empty() {
            break;
        }

        let results = execute_tools(&tool_calls, &tools, state.clone()).await?;
        messages.push(Message::tool_results(results));
    }
    Ok(())
}
```

Tool execution uses `JoinSet` with `tokio_rayon::spawn` for CPU tools and regular tokio for IO tools, as described in design doc.

**Step 3: Verify it compiles**

Run: `cd repotoire-cli && cargo check`
Expected: Compiles.

**Step 4: Commit**

```bash
git add repotoire-cli/src/workstation/agent/
git commit -m "feat: tool registry and agent loop with parallel dispatch"
```

---

### Task 7: System Prompt

**Files:**
- Create: `repotoire-cli/src/workstation/agent/prompt.rs`

**Step 1: Write the system prompt**

The system prompt tells the LLM about available tools, the knowledge graph, and how to use them. It should include:
- Tool descriptions with parameter schemas
- Guidance: "Use query_graph before reading files — it's faster and gives you architectural context"
- Guidance: "Use get_findings to check if there are known issues before investigating"
- Guidance: "The edit tool requires exact string matches — include enough context to be unique"
- Working directory and repo context

**Step 2: Commit**

```bash
git add repotoire-cli/src/workstation/agent/prompt.rs
git commit -m "feat: agent system prompt with graph-aware guidance"
```

---

### Task 8: Two-Panel TUI

**Files:**
- Create: `repotoire-cli/src/workstation/app.rs`
- Create: `repotoire-cli/src/workstation/panels/conversation.rs`
- Create: `repotoire-cli/src/workstation/panels/findings.rs`

**Step 1: Implement the TUI app**

`repotoire-cli/src/workstation/app.rs`:

The main event loop using the ratatui async-template pattern:

```rust
pub async fn run(state: Arc<WorkstationState>) -> Result<()> {
    // Terminal setup
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen, EnableMouseCapture)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    // Event channels
    let mut agent_rx = state.agent_stream_rx.clone();
    let mut tick = tokio::time::interval(Duration::from_millis(33)); // 30fps

    let mut app = AppState::new();

    loop {
        tokio::select! {
            // Terminal events
            Ok(true) = crossterm_event_available() => {
                if let Event::Key(key) = event::read()? {
                    match handle_key(&mut app, key, &state).await {
                        Action::Quit => break,
                        Action::Submit(msg) => {
                            // Spawn agent loop as background task
                            let s = state.clone();
                            tokio::spawn(async move {
                                run_agent(s, &provider, &prompt, msg, &tools).await
                            });
                        }
                        Action::Continue => {}
                    }
                }
            }
            // Agent stream updates
            Ok(()) = agent_rx.changed() => {
                // TUI will re-render on next tick with latest state
            }
            // Render tick
            _ = tick.tick() => {
                terminal.draw(|f| ui(f, &app, &state))?;
            }
        }
    }

    // Terminal teardown
    disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen, DisableMouseCapture)?;
    Ok(())
}
```

**Step 2: Implement panels**

Port the findings panel rendering from existing `cli/tui.rs` — `ListState`, `ListItem`, severity coloring.

Conversation panel: scrollable text area showing messages, tool call indicators, streaming text.

Layout: horizontal split (25% sidebar | 75% main), sidebar = findings, main = conversation + input + status bar.

**Step 3: Test manually**

Run: `cd repotoire-cli && cargo run -- workstation` (or however the CLI routing works)
Expected: TUI launches, shows two panels, accepts input.

**Step 4: Commit**

```bash
git add repotoire-cli/src/workstation/app.rs repotoire-cli/src/workstation/panels/
git commit -m "feat: two-panel TUI with findings and conversation panels"
```

---

### Task 9: Wire Workstation Mode into CLI

**Files:**
- Modify: `repotoire-cli/src/cli/mod.rs` (add Workstation command)
- Modify: `repotoire-cli/src/main.rs` (async main for workstation mode)
- Create: `repotoire-cli/src/workstation/launch.rs`

**Step 1: Add workstation command to CLI**

Add to the `Commands` enum in `cli/mod.rs`:
```rust
/// Launch interactive AI workstation
Workstation {
    /// Anthropic API key (or set ANTHROPIC_API_KEY env)
    #[arg(long, env = "ANTHROPIC_API_KEY")]
    api_key: Option<String>,

    /// Model to use
    #[arg(long, default_value = "claude-sonnet-4-20250514")]
    model: String,
},
```

**Step 2: Implement launch sequence**

`repotoire-cli/src/workstation/launch.rs`:

1. Show "Loading..." immediately
2. Spawn background task: parse files (rayon), build graph, run detectors
3. Start TUI with empty graph
4. When graph ready: swap into `ArcSwap`, update `graph_ready` flag
5. Agent can use bash/file tools while graph loads; graph tools return "loading" until ready

**Step 3: Test end-to-end**

Run: `ANTHROPIC_API_KEY=sk-... cargo run -- workstation`
Expected: TUI launches, graph loads in background, can type messages and get agent responses.

**Step 4: Commit**

```bash
git add repotoire-cli/src/cli/mod.rs repotoire-cli/src/main.rs repotoire-cli/src/workstation/launch.rs
git commit -m "feat: wire workstation mode into CLI with lazy graph loading"
```

---

### Task 10: Integration Test + Polish

**Files:**
- Create: `repotoire-cli/tests/workstation_integration.rs`

**Step 1: Write integration test**

Test the full agent loop against a mock or recorded API response. Verify:
- Text streaming works
- Tool calls are parsed and dispatched
- Graph tools return real data from a test fixture graph
- Edit tool modifies files correctly
- Agent loop terminates when model stops calling tools

**Step 2: Polish and edge cases**

- Ctrl+C handler: abort current agent task via `JoinSet::abort_all()`
- Status bar: show model name, token count from `Usage`, current branch via `git rev-parse`
- Error display: show API errors in conversation panel, not crash
- Token warning: log warning when `usage.total_tokens > 160000` (80% of 200k context)

**Step 3: Full test suite**

Run: `cd repotoire-cli && cargo test`
Expected: All existing tests + new workstation tests pass.

**Step 4: Commit**

```bash
git add -A
git commit -m "feat: integration tests and polish for workstation v1"
```

---

## Task Dependency Order

```
Task 1 (deps) → Task 2 (scaffold) → Task 3 (streaming) → Task 4 (coding tools)
                                                        → Task 5 (graph tools)
                                   Task 4 + 5 → Task 6 (registry + loop)
                                                Task 6 → Task 7 (prompt)
                                                       → Task 8 (TUI)
                                   Task 7 + 8 → Task 9 (wire into CLI)
                                                Task 9 → Task 10 (integration + polish)
```

Tasks 4 and 5 can be done in parallel. Tasks 7 and 8 can be done in parallel. Everything else is sequential.

## Estimated Timeline

| Task | Effort | Cumulative |
|------|--------|-----------|
| 1. Dependencies | 15 min | 15 min |
| 2. Scaffold + state | 1 hr | 1.25 hr |
| 3. Streaming provider | 3-4 hr | ~5 hr |
| 4. Coding tools | 2-3 hr | ~7.5 hr |
| 5. Graph tools | 1-2 hr | ~9 hr |
| 6. Registry + loop | 2 hr | ~11 hr |
| 7. System prompt | 1 hr | ~12 hr |
| 8. TUI | 3-4 hr | ~15 hr |
| 9. Wire CLI | 1 hr | ~16 hr |
| 10. Integration + polish | 2-3 hr | ~19 hr |

**Total: ~19 hours of focused work.** At 3-4 hours/evening, that's roughly 5-7 evenings. At full days, 2-3 days.
