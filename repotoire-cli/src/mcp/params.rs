//! MCP Tool parameter types
//!
//! These structs define the inputSchema for each MCP tool via schemars derive.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

// ── Analysis Tools ──

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct AnalyzeParams {
    /// Only analyze changed files (faster). Defaults to true.
    pub incremental: Option<bool>,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
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

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum SeverityFilter {
    Critical,
    High,
    Medium,
    Low,
    Info,
}

// Implement Display for SeverityFilter so we can compare with string values
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

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct GetHotspotsParams {
    /// Maximum number of files to return (default: 10)
    pub limit: Option<u64>,
}

// ── Graph Tools ──

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
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

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum GraphQueryType {
    Functions,
    Classes,
    Files,
    Stats,
    Callers,
    Callees,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
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

#[derive(Debug, Clone, Default, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum TraceDirection {
    Upstream,
    Downstream,
    #[default]
    Both,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum TraceKind {
    Calls,
    Imports,
    #[default]
    All,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct AnalyzeImpactParams {
    /// File path of the target (relative to repo root)
    pub target: String,
    /// Scope: function or file
    pub scope: Option<ImpactScope>,
    /// Function or class name (required when scope is "function")
    pub name: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum ImpactScope {
    Function,
    #[default]
    File,
}

// ── File Tools ──

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct GetFileParams {
    /// Path to file (relative to repo root)
    pub file_path: String,
    /// Start line (1-indexed)
    pub start_line: Option<u64>,
    /// End line (1-indexed)
    pub end_line: Option<u64>,
}

// get_architecture and list_detectors take no parameters

// ── Evolution Tools ──

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
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

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
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

// ── AI Tools ──

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct SearchCodeParams {
    /// Natural language search query
    pub query: String,
    /// Maximum number of results (default: 10)
    pub top_k: Option<u64>,
    /// Filter by entity type (Function, Class, File)
    pub entity_types: Option<Vec<String>>,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct AskParams {
    /// Natural language question about the codebase
    pub question: String,
    /// Number of context snippets to retrieve (default: 10)
    pub top_k: Option<u64>,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct GenerateFixParams {
    /// Index of the finding to fix (1-based, from analyze results)
    pub finding_id: String,
}
