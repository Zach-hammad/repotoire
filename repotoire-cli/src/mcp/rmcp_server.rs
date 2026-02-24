//! rmcp-based MCP Server implementation
//!
//! This module implements the Model Context Protocol server using the `rmcp` crate's
//! `#[tool_router]` and `#[tool_handler]` macros. It wires all 13 tools (10 FREE + 3 AI)
//! to their respective handler functions in `super::tools::*`.
//!
//! The server is designed to run over stdio (JSON-RPC) or Streamable HTTP transport.
//! All tool handlers delegate blocking work to `spawn_blocking` to avoid blocking
//! the async runtime.

use rmcp::{
    handler::server::{router::tool::ToolRouter, wrapper::Parameters},
    model::*,
    tool, tool_handler, tool_router, ErrorData as McpError, ServerHandler,
};
use std::sync::Arc;
use tokio::sync::RwLock;

use super::params::*;
use super::state::HandlerState;

/// Maximum total argument size (1 MB). Requests exceeding this are rejected.
const MAX_ARGS_SIZE: usize = 1_000_000;

/// The rmcp-based MCP server for Repotoire.
///
/// Holds shared `HandlerState` behind `Arc<RwLock<_>>` so tool handlers can
/// acquire read or write access as needed. The `ToolRouter` is built at
/// construction time by the `#[tool_router]` macro.
#[derive(Clone)]
pub struct RepotoireServer {
    state: Arc<RwLock<HandlerState>>,
    #[allow(dead_code)]
    tool_router: ToolRouter<RepotoireServer>,
}

/// Validate that serialized arguments do not exceed the size limit.
fn validate_args_size<T: serde::Serialize>(params: &T) -> Result<(), McpError> {
    let size = serde_json::to_string(params)
        .map(|s| s.len())
        .unwrap_or(0);
    if size > MAX_ARGS_SIZE {
        return Err(McpError::invalid_params(
            format!(
                "Tool arguments exceed {}MB limit ({} bytes)",
                MAX_ARGS_SIZE / 1_000_000,
                size
            ),
            None,
        ));
    }
    Ok(())
}

/// Convert a `serde_json::Value` result into a pretty-printed text `CallToolResult`.
fn value_to_result(result: serde_json::Value) -> CallToolResult {
    CallToolResult::success(vec![Content::text(
        serde_json::to_string_pretty(&result).unwrap_or_else(|_| result.to_string()),
    )])
}

// ─── Tool Router ────────────────────────────────────────────────────────────

#[tool_router]
impl RepotoireServer {
    /// Create a new `RepotoireServer` with the given handler state.
    pub fn new(state: HandlerState) -> Self {
        let tool_router = Self::tool_router();
        Self {
            state: Arc::new(RwLock::new(state)),
            tool_router,
        }
    }

    // ── Analysis Tools (FREE) ───────────────────────────────────────────

    #[tool(
        name = "repotoire_analyze",
        description = "Run full code analysis on the repository. Returns findings summary by severity. Use this first to generate analysis data."
    )]
    async fn repotoire_analyze(
        &self,
        Parameters(params): Parameters<AnalyzeParams>,
    ) -> Result<CallToolResult, McpError> {
        validate_args_size(&params)?;
        let state = self.state.clone();
        let result = tokio::task::spawn_blocking(move || {
            let mut state = state.blocking_write();
            super::tools::analysis::handle_analyze(&mut state, &params)
        })
        .await
        .map_err(|e| McpError::internal_error(e.to_string(), None))?
        .map_err(|e| McpError::internal_error(e.to_string(), None))?;

        Ok(value_to_result(result))
    }

    #[tool(
        name = "repotoire_get_findings",
        description = "Get code quality findings with filtering and pagination. Supports severity and detector filters. Run repotoire_analyze first."
    )]
    async fn repotoire_get_findings(
        &self,
        Parameters(params): Parameters<GetFindingsParams>,
    ) -> Result<CallToolResult, McpError> {
        validate_args_size(&params)?;
        let state = self.state.clone();
        let result = tokio::task::spawn_blocking(move || {
            let mut state = state.blocking_write();
            super::tools::analysis::handle_get_findings(&mut state, &params)
        })
        .await
        .map_err(|e| McpError::internal_error(e.to_string(), None))?
        .map_err(|e| McpError::internal_error(e.to_string(), None))?;

        Ok(value_to_result(result))
    }

    #[tool(
        name = "repotoire_get_hotspots",
        description = "Get files ranked by issue density (most problematic files first). Run repotoire_analyze first."
    )]
    async fn repotoire_get_hotspots(
        &self,
        Parameters(params): Parameters<GetHotspotsParams>,
    ) -> Result<CallToolResult, McpError> {
        validate_args_size(&params)?;
        let state = self.state.clone();
        let result = tokio::task::spawn_blocking(move || {
            let mut state = state.blocking_write();
            super::tools::analysis::handle_get_hotspots(&mut state, &params)
        })
        .await
        .map_err(|e| McpError::internal_error(e.to_string(), None))?
        .map_err(|e| McpError::internal_error(e.to_string(), None))?;

        Ok(value_to_result(result))
    }

    // ── Graph Tools (FREE) ──────────────────────────────────────────────

    #[tool(
        name = "repotoire_query_graph",
        description = "Query the code knowledge graph for functions, classes, files, stats, callers, or callees. Supports pagination."
    )]
    async fn repotoire_query_graph(
        &self,
        Parameters(params): Parameters<QueryGraphParams>,
    ) -> Result<CallToolResult, McpError> {
        validate_args_size(&params)?;
        let state = self.state.clone();
        let result = tokio::task::spawn_blocking(move || {
            let mut state = state.blocking_write();
            super::tools::graph::handle_query_graph(&mut state, &params)
        })
        .await
        .map_err(|e| McpError::internal_error(e.to_string(), None))?
        .map_err(|e| McpError::internal_error(e.to_string(), None))?;

        Ok(value_to_result(result))
    }

    #[tool(
        name = "repotoire_trace_dependencies",
        description = "Multi-hop graph traversal: trace call chains, imports, and inheritance up to N levels deep. Find upstream callers and downstream callees."
    )]
    async fn repotoire_trace_dependencies(
        &self,
        Parameters(params): Parameters<TraceDependenciesParams>,
    ) -> Result<CallToolResult, McpError> {
        validate_args_size(&params)?;
        let state = self.state.clone();
        let result = tokio::task::spawn_blocking(move || {
            let mut state = state.blocking_write();
            super::tools::graph::handle_trace_dependencies(&mut state, &params)
        })
        .await
        .map_err(|e| McpError::internal_error(e.to_string(), None))?
        .map_err(|e| McpError::internal_error(e.to_string(), None))?;

        Ok(value_to_result(result))
    }

    #[tool(
        name = "repotoire_analyze_impact",
        description = "Change impact analysis: if I modify function X or file Y, what code is affected? Shows direct and transitive dependents with risk scoring."
    )]
    async fn repotoire_analyze_impact(
        &self,
        Parameters(params): Parameters<AnalyzeImpactParams>,
    ) -> Result<CallToolResult, McpError> {
        validate_args_size(&params)?;
        let state = self.state.clone();
        let result = tokio::task::spawn_blocking(move || {
            let mut state = state.blocking_write();
            super::tools::graph::handle_analyze_impact(&mut state, &params)
        })
        .await
        .map_err(|e| McpError::internal_error(e.to_string(), None))?
        .map_err(|e| McpError::internal_error(e.to_string(), None))?;

        Ok(value_to_result(result))
    }

    // ── File Tools (FREE) ───────────────────────────────────────────────

    #[tool(
        name = "repotoire_get_file",
        description = "Read file content from the repository with optional line range. Files are sandboxed to the repository root."
    )]
    async fn repotoire_get_file(
        &self,
        Parameters(params): Parameters<GetFileParams>,
    ) -> Result<CallToolResult, McpError> {
        validate_args_size(&params)?;
        let state = self.state.clone();
        // get_file uses &HandlerState (immutable) — use blocking_read()
        let result = tokio::task::spawn_blocking(move || {
            let state = state.blocking_read();
            super::tools::files::handle_get_file(&state, &params)
        })
        .await
        .map_err(|e| McpError::internal_error(e.to_string(), None))?
        .map_err(|e| McpError::internal_error(e.to_string(), None))?;

        Ok(value_to_result(result))
    }

    #[tool(
        name = "repotoire_get_architecture",
        description = "Get codebase architecture overview: module structure, language distribution, and top classes by method count."
    )]
    async fn repotoire_get_architecture(&self) -> Result<CallToolResult, McpError> {
        let state = self.state.clone();
        let result = tokio::task::spawn_blocking(move || {
            let mut state = state.blocking_write();
            super::tools::files::handle_get_architecture(&mut state)
        })
        .await
        .map_err(|e| McpError::internal_error(e.to_string(), None))?
        .map_err(|e| McpError::internal_error(e.to_string(), None))?;

        Ok(value_to_result(result))
    }

    #[tool(
        name = "repotoire_list_detectors",
        description = "List all available code quality detectors with descriptions and categories."
    )]
    async fn repotoire_list_detectors(&self) -> Result<CallToolResult, McpError> {
        let state = self.state.clone();
        // list_detectors uses &HandlerState (immutable) — use blocking_read()
        let result = tokio::task::spawn_blocking(move || {
            let state = state.blocking_read();
            super::tools::files::handle_list_detectors(&state)
        })
        .await
        .map_err(|e| McpError::internal_error(e.to_string(), None))?
        .map_err(|e| McpError::internal_error(e.to_string(), None))?;

        Ok(value_to_result(result))
    }

    // ── Evolution Tools (FREE) ──────────────────────────────────────────

    #[tool(
        name = "repotoire_query_evolution",
        description = "Query code evolution and git history. Supports 7 query types: file_churn, hottest_files, file_commits, function_history, entity_blame, file_ownership, recent_commits."
    )]
    async fn repotoire_query_evolution(
        &self,
        Parameters(params): Parameters<QueryEvolutionParams>,
    ) -> Result<CallToolResult, McpError> {
        validate_args_size(&params)?;
        let state = self.state.clone();
        let result = tokio::task::spawn_blocking(move || {
            let mut state = state.blocking_write();
            super::tools::evolution::handle_query_evolution(&mut state, &params)
        })
        .await
        .map_err(|e| McpError::internal_error(e.to_string(), None))?
        .map_err(|e| McpError::internal_error(e.to_string(), None))?;

        Ok(value_to_result(result))
    }

    // ── AI Tools (PRO / BYOK) ───────────────────────────────────────────

    #[tool(
        name = "repotoire_search_code",
        description = "Semantic code search using AI embeddings. Find code by natural language description. Requires REPOTOIRE_API_KEY (PRO). Free alternative: repotoire_query_graph."
    )]
    async fn repotoire_search_code(
        &self,
        Parameters(params): Parameters<SearchCodeParams>,
    ) -> Result<CallToolResult, McpError> {
        validate_args_size(&params)?;
        let state = self.state.clone();
        // search_code uses &HandlerState (immutable) — use blocking_read()
        let result = tokio::task::spawn_blocking(move || {
            let state = state.blocking_read();
            super::tools::ai::handle_search_code(&state, &params)
        })
        .await
        .map_err(|e| McpError::internal_error(e.to_string(), None))?
        .map_err(|e| McpError::internal_error(e.to_string(), None))?;

        Ok(value_to_result(result))
    }

    #[tool(
        name = "repotoire_ask",
        description = "Ask questions about the codebase using RAG. Get AI-generated answers with source citations. Requires REPOTOIRE_API_KEY (PRO)."
    )]
    async fn repotoire_ask(
        &self,
        Parameters(params): Parameters<AskParams>,
    ) -> Result<CallToolResult, McpError> {
        validate_args_size(&params)?;
        let state = self.state.clone();
        // ask uses &HandlerState (immutable) — use blocking_read()
        let result = tokio::task::spawn_blocking(move || {
            let state = state.blocking_read();
            super::tools::ai::handle_ask(&state, &params)
        })
        .await
        .map_err(|e| McpError::internal_error(e.to_string(), None))?
        .map_err(|e| McpError::internal_error(e.to_string(), None))?;

        Ok(value_to_result(result))
    }

    #[tool(
        name = "repotoire_generate_fix",
        description = "Generate an AI-powered fix for a finding. Works with ANTHROPIC_API_KEY, OPENAI_API_KEY (BYOK) or REPOTOIRE_API_KEY (PRO). Run repotoire_analyze first."
    )]
    async fn repotoire_generate_fix(
        &self,
        Parameters(params): Parameters<GenerateFixParams>,
    ) -> Result<CallToolResult, McpError> {
        validate_args_size(&params)?;
        let state = self.state.clone();
        // generate_fix uses &HandlerState (immutable) — use blocking_read()
        let result = tokio::task::spawn_blocking(move || {
            let state = state.blocking_read();
            super::tools::ai::handle_generate_fix(&state, &params)
        })
        .await
        .map_err(|e| McpError::internal_error(e.to_string(), None))?
        .map_err(|e| McpError::internal_error(e.to_string(), None))?;

        Ok(value_to_result(result))
    }
}

// ─── ServerHandler Implementation ───────────────────────────────────────────

#[tool_handler]
impl ServerHandler for RepotoireServer {
    fn get_info(&self) -> ServerInfo {
        ServerInfo {
            protocol_version: ProtocolVersion::V_2025_03_26,
            capabilities: ServerCapabilities::builder()
                .enable_tools()
                .build(),
            server_info: Implementation {
                name: "repotoire".to_string(),
                title: Some("Repotoire MCP Server".to_string()),
                version: env!("CARGO_PKG_VERSION").to_string(),
                description: Some(
                    "Graph-powered code health analysis with 114 detectors".to_string(),
                ),
                icons: None,
                website_url: Some("https://repotoire.com".to_string()),
            },
            instructions: Some(
                "Repotoire: graph-powered code health analysis. \
                 Use repotoire_analyze to start, then explore with \
                 graph/evolution/AI tools."
                    .into(),
            ),
        }
    }
}

// ─── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::store::{CodeEdge, CodeNode, GraphStore};
    use std::sync::Arc;
    use tempfile::tempdir;

    #[test]
    fn test_server_construction() {
        let dir = tempdir().unwrap();
        let state = HandlerState::new(dir.path().to_path_buf(), true);
        let server = RepotoireServer::new(state);

        // Verify the server can be cloned (required for rmcp)
        let _cloned = server.clone();
    }

    #[test]
    fn test_server_info() {
        let dir = tempdir().unwrap();
        let state = HandlerState::new(dir.path().to_path_buf(), true);
        let server = RepotoireServer::new(state);

        let info = server.get_info();
        assert_eq!(info.server_info.name, "repotoire");
        assert_eq!(info.protocol_version, ProtocolVersion::V_2025_03_26);
        assert!(info.capabilities.tools.is_some());
        assert!(info.instructions.is_some());
    }

    #[test]
    fn test_validate_args_size_ok() {
        let small = AnalyzeParams {
            incremental: Some(true),
        };
        assert!(validate_args_size(&small).is_ok());
    }

    #[test]
    fn test_validate_args_size_too_large() {
        // Create a params struct that will serialize to > 1MB
        let large_query = "x".repeat(MAX_ARGS_SIZE + 100);
        let params = SearchCodeParams {
            query: large_query,
            top_k: None,
            entity_types: None,
        };
        let result = validate_args_size(&params);
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn test_tool_router_has_all_tools() {
        let dir = tempdir().unwrap();
        let state = HandlerState::new(dir.path().to_path_buf(), true);
        let server = RepotoireServer::new(state);

        let tool_names: Vec<String> = server
            .tool_router
            .map
            .keys()
            .map(|k| k.to_string())
            .collect();

        // Verify all 13 tools are registered
        let expected_tools = [
            "repotoire_analyze",
            "repotoire_get_findings",
            "repotoire_get_hotspots",
            "repotoire_query_graph",
            "repotoire_trace_dependencies",
            "repotoire_analyze_impact",
            "repotoire_get_file",
            "repotoire_get_architecture",
            "repotoire_list_detectors",
            "repotoire_query_evolution",
            "repotoire_search_code",
            "repotoire_ask",
            "repotoire_generate_fix",
        ];

        assert_eq!(
            tool_names.len(),
            expected_tools.len(),
            "Expected {} tools, found {}: {:?}",
            expected_tools.len(),
            tool_names.len(),
            tool_names
        );

        for expected in &expected_tools {
            assert!(
                tool_names.contains(&expected.to_string()),
                "Missing tool: {}. Registered: {:?}",
                expected,
                tool_names
            );
        }
    }

    // ── Helper ──────────────────────────────────────────────────────────────

    /// Extract the text payload from a `CallToolResult`.
    ///
    /// Panics if the result contains no text content.
    fn extract_text(result: &CallToolResult) -> &str {
        result
            .content
            .first()
            .and_then(|c| match &c.raw {
                RawContent::Text(t) => Some(t.text.as_str()),
                _ => None,
            })
            .expect("CallToolResult should contain text content")
    }

    // ── New integration tests ───────────────────────────────────────────────

    #[test]
    fn test_all_tools_have_descriptions() {
        let dir = tempdir().unwrap();
        let state = HandlerState::new(dir.path().to_path_buf(), true);
        let server = RepotoireServer::new(state);

        let tools = server.tool_router.list_all();
        assert!(!tools.is_empty(), "Tool list should not be empty");

        for tool in &tools {
            let desc = tool
                .description
                .as_ref()
                .expect(&format!("Tool '{}' is missing a description", tool.name));
            assert!(
                !desc.is_empty(),
                "Tool '{}' has an empty description",
                tool.name
            );
        }
    }

    #[test]
    fn test_tool_names_follow_convention() {
        let dir = tempdir().unwrap();
        let state = HandlerState::new(dir.path().to_path_buf(), true);
        let server = RepotoireServer::new(state);

        let tools = server.tool_router.list_all();
        assert!(!tools.is_empty(), "Tool list should not be empty");

        for tool in &tools {
            assert!(
                tool.name.starts_with("repotoire_"),
                "Tool '{}' does not follow the repotoire_ naming convention",
                tool.name
            );
        }
    }

    #[tokio::test]
    async fn test_analyze_tool_works() {
        let dir = tempdir().unwrap();
        // Write a small Rust file so detectors have something to analyze
        std::fs::write(
            dir.path().join("main.rs"),
            "fn main() {\n    println!(\"hello\");\n}\n",
        )
        .unwrap();

        let state = HandlerState::new(dir.path().to_path_buf(), true);
        let server = RepotoireServer::new(state);

        let params = AnalyzeParams {
            incremental: Some(true),
        };
        let result = server
            .repotoire_analyze(Parameters(params))
            .await;

        assert!(result.is_ok(), "analyze should succeed: {:?}", result.err());
        let call_result = result.unwrap();
        let text = extract_text(&call_result);
        let json: serde_json::Value =
            serde_json::from_str(text).expect("Response should be valid JSON");

        assert!(json.get("status").is_some(), "Response missing 'status'");
        assert!(
            json.get("total_findings").is_some(),
            "Response missing 'total_findings'"
        );
        assert!(
            json.get("by_severity").is_some(),
            "Response missing 'by_severity'"
        );
    }

    #[tokio::test]
    async fn test_get_file_tool_works() {
        let dir = tempdir().unwrap();
        let file_content = "line one\nline two\nline three\n";
        std::fs::write(dir.path().join("sample.txt"), file_content).unwrap();

        let state = HandlerState::new(dir.path().to_path_buf(), true);
        let server = RepotoireServer::new(state);

        let params = GetFileParams {
            file_path: "sample.txt".to_string(),
            start_line: None,
            end_line: None,
        };
        let result = server
            .repotoire_get_file(Parameters(params))
            .await;

        assert!(result.is_ok(), "get_file should succeed: {:?}", result.err());
        let call_result = result.unwrap();
        let text = extract_text(&call_result);
        let json: serde_json::Value =
            serde_json::from_str(text).expect("Response should be valid JSON");

        // Should contain file content, not an error
        assert!(
            json.get("error").is_none(),
            "Unexpected error: {}",
            json
        );
        let content = json
            .get("content")
            .and_then(|v| v.as_str())
            .expect("Response should have 'content' field");
        assert!(
            content.contains("line one"),
            "File content should contain 'line one'"
        );
        assert!(
            content.contains("line two"),
            "File content should contain 'line two'"
        );
    }

    #[tokio::test]
    async fn test_query_graph_tool_works() {
        let dir = tempdir().unwrap();

        // Build an in-memory graph with test data
        let graph = GraphStore::in_memory();
        graph.add_node(
            CodeNode::function("my_func", "src/lib.rs").with_qualified_name("lib::my_func"),
        );
        graph.add_node(
            CodeNode::function("helper", "src/lib.rs").with_qualified_name("lib::helper"),
        );
        graph.add_edge_by_name("lib::my_func", "lib::helper", CodeEdge::calls());

        let mut state = HandlerState::new(dir.path().to_path_buf(), true);
        state.set_graph(Arc::new(graph));
        let server = RepotoireServer::new(state);

        let params = QueryGraphParams {
            query_type: GraphQueryType::Functions,
            name: None,
            limit: Some(100),
            offset: Some(0),
        };
        let result = server
            .repotoire_query_graph(Parameters(params))
            .await;

        assert!(
            result.is_ok(),
            "query_graph should succeed: {:?}",
            result.err()
        );
        let call_result = result.unwrap();
        let text = extract_text(&call_result);
        let json: serde_json::Value =
            serde_json::from_str(text).expect("Response should be valid JSON");

        let results = json
            .get("results")
            .and_then(|v| v.as_array())
            .expect("Response should have 'results' array");
        assert_eq!(
            results.len(),
            2,
            "Should return 2 functions, got: {:?}",
            results
        );
        assert_eq!(json["total_count"], 2);
    }

    #[tokio::test]
    async fn test_list_detectors_tool_works() {
        let dir = tempdir().unwrap();
        let state = HandlerState::new(dir.path().to_path_buf(), true);
        let server = RepotoireServer::new(state);

        let result = server.repotoire_list_detectors().await;

        assert!(
            result.is_ok(),
            "list_detectors should succeed: {:?}",
            result.err()
        );
        let call_result = result.unwrap();
        let text = extract_text(&call_result);
        let json: serde_json::Value =
            serde_json::from_str(text).expect("Response should be valid JSON");

        let detectors = json
            .get("detectors")
            .and_then(|v| v.as_array())
            .expect("Response should have 'detectors' array");
        assert!(
            !detectors.is_empty(),
            "Detector list should not be empty"
        );

        let count = json
            .get("count")
            .and_then(|v| v.as_u64())
            .expect("Response should have 'count' field");
        assert_eq!(
            count,
            detectors.len() as u64,
            "count should match detectors array length"
        );

        // Each detector should have name, description, category
        for d in detectors {
            assert!(d.get("name").is_some(), "Detector missing 'name'");
            assert!(
                d.get("description").is_some(),
                "Detector missing 'description'"
            );
            assert!(
                d.get("category").is_some(),
                "Detector missing 'category'"
            );
        }
    }
}
