//! MCP Tool definitions
//!
//! Defines the available tools and their JSON schemas for the MCP protocol.

#![allow(non_snake_case)]

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;

/// Tool definition for MCP
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Tool {
    pub name: String,
    pub description: String,
    pub input_schema: ToolSchema,
}

/// JSON Schema for tool input
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolSchema {
    #[serde(rename = "type")]
    pub schema_type: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub properties: Option<HashMap<String, Value>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub required: Option<Vec<String>>,
}

impl ToolSchema {
    pub fn object() -> Self {
        Self {
            schema_type: "object".to_string(),
            properties: Some(HashMap::new()),
            required: None,
        }
    }

    pub fn with_property(mut self, name: &str, schema: Value) -> Self {
        if let Some(ref mut props) = self.properties {
            props.insert(name.to_string(), schema);
        }
        self
    }

    pub fn with_required(mut self, fields: Vec<&str>) -> Self {
        self.required = Some(fields.into_iter().map(String::from).collect());
        self
    }
}

/// Get all FREE tier tools
pub fn FREE_TOOLS() -> Vec<Tool> {
    vec![
        Tool {
            name: "analyze".to_string(),
            description: "Run code analysis on the repository to detect issues. Returns a summary of findings by severity.".to_string(),
            input_schema: ToolSchema::object()
                .with_property("repo_path", json!({
                    "type": "string",
                    "description": "Path to repository (default: current directory)",
                    "default": "."
                }))
                .with_property("incremental", json!({
                    "type": "boolean",
                    "description": "Only analyze changed files (faster)",
                    "default": true
                })),
        },
        Tool {
            name: "query_graph".to_string(),
            description: "Query the code knowledge graph. Use type parameter to select: functions, classes, files, imports, callers, callees.".to_string(),
            input_schema: ToolSchema::object()
                .with_property("type", json!({
                    "type": "string",
                    "description": "Query type: functions, classes, files, imports, callers, callees",
                    "enum": ["functions", "classes", "files", "imports", "callers", "callees"]
                }))
                .with_property("name", json!({
                    "type": "string",
                    "description": "Function/class name for callers/callees queries"
                }))
                .with_property("params", json!({
                    "type": "object",
                    "description": "Optional query parameters"
                }))
                .with_required(vec!["type"]),
        },
        Tool {
            name: "get_findings".to_string(),
            description: "Get code quality findings from the most recent analysis. Filter by severity or detector name.".to_string(),
            input_schema: ToolSchema::object()
                .with_property("severity", json!({
                    "type": "string",
                    "enum": ["critical", "high", "medium", "low", "info"],
                    "description": "Filter by severity level"
                }))
                .with_property("detector", json!({
                    "type": "string",
                    "description": "Filter by detector name"
                }))
                .with_property("limit", json!({
                    "type": "integer",
                    "description": "Maximum results to return",
                    "default": 20
                })),
        },
        Tool {
            name: "get_file".to_string(),
            description: "Read file content from the repository. Optionally specify line range.".to_string(),
            input_schema: ToolSchema::object()
                .with_property("file_path", json!({
                    "type": "string",
                    "description": "Path to file (relative to repo root)"
                }))
                .with_property("start_line", json!({
                    "type": "integer",
                    "description": "Start line (1-indexed)"
                }))
                .with_property("end_line", json!({
                    "type": "integer",
                    "description": "End line (1-indexed)"
                }))
                .with_required(vec!["file_path"]),
        },
        Tool {
            name: "get_architecture".to_string(),
            description: "Get an overview of the codebase architecture. Returns module structure, node counts, and top-level organization.".to_string(),
            input_schema: ToolSchema::object(),
        },
        Tool {
            name: "list_detectors".to_string(),
            description: "List all available code quality detectors with their descriptions and categories.".to_string(),
            input_schema: ToolSchema::object(),
        },
        Tool {
            name: "get_hotspots".to_string(),
            description: "Get files with the most issues (hotspots). Useful for identifying problematic areas of the codebase.".to_string(),
            input_schema: ToolSchema::object()
                .with_property("limit", json!({
                    "type": "integer",
                    "description": "Maximum number of files to return",
                    "default": 10
                })),
        },
    ]
}

/// Get all PRO tier tools (require API key)
pub fn PRO_TOOLS() -> Vec<Tool> {
    vec![
        Tool {
            name: "search_code".to_string(),
            description: "Search codebase semantically using AI embeddings. Find code by natural language description. (PRO)".to_string(),
            input_schema: ToolSchema::object()
                .with_property("query", json!({
                    "type": "string",
                    "description": "Natural language search query"
                }))
                .with_property("top_k", json!({
                    "type": "integer",
                    "description": "Maximum number of results",
                    "default": 10
                }))
                .with_property("entity_types", json!({
                    "type": "array",
                    "items": { "type": "string" },
                    "description": "Filter by type (Function, Class, File)"
                }))
                .with_required(vec!["query"]),
        },
        Tool {
            name: "ask".to_string(),
            description: "Ask questions about the codebase using RAG (Retrieval Augmented Generation). Get AI-generated answers with source citations. (PRO)".to_string(),
            input_schema: ToolSchema::object()
                .with_property("question", json!({
                    "type": "string",
                    "description": "Natural language question about the codebase"
                }))
                .with_property("top_k", json!({
                    "type": "integer",
                    "description": "Number of context snippets to retrieve",
                    "default": 10
                }))
                .with_required(vec!["question"]),
        },
        Tool {
            name: "generate_fix".to_string(),
            description: "Generate an AI-powered fix for a code finding. Returns proposed code changes with explanation. Requires ANTHROPIC_API_KEY or OPENAI_API_KEY.".to_string(),
            input_schema: ToolSchema::object()
                .with_property("finding_id", json!({
                    "type": "string",
                    "description": "Index of the finding to fix (1-based, from analyze results)"
                }))
                .with_required(vec!["finding_id"]),
        },
    ]
}

/// AI tools that require BYOK (user's own API key)
pub fn AI_TOOLS() -> Vec<Tool> {
    vec![
        Tool {
            name: "generate_fix".to_string(),
            description: "Generate an AI-powered fix for a code finding. Requires ANTHROPIC_API_KEY or OPENAI_API_KEY.".to_string(),
            input_schema: ToolSchema::object()
                .with_property("finding_id", json!({
                    "type": "string",
                    "description": "Index of the finding to fix (1-based, from analyze results)"
                }))
                .with_required(vec!["finding_id"]),
        },
    ]
}

/// All available tools based on mode
#[allow(dead_code)] // Public API helper
pub fn available_tools(is_pro: bool) -> Vec<Tool> {
    available_tools_full(is_pro, false)
}

/// All available tools based on mode and AI availability
pub fn available_tools_full(is_pro: bool, has_ai: bool) -> Vec<Tool> {
    let mut tools = FREE_TOOLS();

    // AI tools available with BYOK or PRO
    if has_ai || is_pro {
        tools.extend(AI_TOOLS());
    }

    // Additional PRO-only cloud tools
    if is_pro {
        // search_code and ask are cloud-only (need embeddings)
        tools.extend(PRO_TOOLS().into_iter().filter(|t| t.name != "generate_fix"));
    }

    tools
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_free_tools() {
        let tools = FREE_TOOLS();
        assert!(!tools.is_empty());
        assert!(tools.iter().any(|t| t.name == "analyze"));
        assert!(tools.iter().any(|t| t.name == "query_graph"));
    }

    #[test]
    fn test_pro_tools() {
        let tools = PRO_TOOLS();
        assert!(!tools.is_empty());
        assert!(tools.iter().any(|t| t.name == "search_code"));
        assert!(tools.iter().any(|t| t.name == "ask"));
    }

    #[test]
    fn test_tool_schema_builder() {
        let schema = ToolSchema::object()
            .with_property("test", json!({"type": "string"}))
            .with_required(vec!["test"]);

        assert_eq!(schema.schema_type, "object");
        assert!(schema.properties.is_some());
        assert!(schema.required.is_some());
        assert_eq!(schema.required.unwrap(), vec!["test"]);
    }
}
