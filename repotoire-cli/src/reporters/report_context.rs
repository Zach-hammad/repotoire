//! Data contract for rich report generation.
//!
//! `ReportContext` bundles the health report with optional graph, git, and
//! source data. Each sub-struct is independently optional — reporters degrade
//! gracefully when data is unavailable.

use crate::calibrate::StyleProfile;
use crate::models::HealthReport;

/// Full context for report rendering. Text and HTML reporters use graph/git
/// data for themed output and visualizations. JSON/SARIF/Markdown reporters
/// only need `health`.
pub struct ReportContext {
    pub health: HealthReport,
    pub graph_data: Option<GraphData>,
    pub git_data: Option<GitData>,
    pub source_snippets: Vec<FindingSnippet>,
    pub previous_health: Option<HealthReport>,
    pub style_profile: Option<StyleProfile>,
}

/// Data derived from the frozen CodeGraph and GraphPrimitives.
/// All NodeIndex values are pre-resolved to qualified name strings.
pub struct GraphData {
    pub modules: Vec<ModuleNode>,
    pub module_edges: Vec<ModuleEdge>,
    pub communities: Vec<Community>,
    pub modularity: f64,
    pub top_pagerank: Vec<(String, f64)>,
    pub top_betweenness: Vec<(String, f64)>,
    pub articulation_points: Vec<String>,
    pub call_cycles: Vec<Vec<String>>,
}

/// Data derived from git blame and CoChangeMatrix.
/// None if the repo has no git history.
pub struct GitData {
    pub hidden_coupling: Vec<(String, String, f32)>,
    pub top_co_change: Vec<(String, String, f32)>,
    pub file_ownership: Vec<FileOwnership>,
    pub bus_factor_files: Vec<(String, usize)>,
    pub project_bus_factor: Option<usize>,
}

#[derive(Clone)]
pub struct ModuleNode {
    pub path: String,
    pub loc: usize,
    pub file_count: usize,
    pub finding_count: usize,
    pub finding_density: f64,
    pub avg_complexity: f64,
    pub community_id: Option<usize>,
    pub health_score: f64,
}

#[derive(Clone)]
pub struct ModuleEdge {
    pub from: String,
    pub to: String,
    pub weight: usize,
    pub is_cycle: bool,
}

pub struct Community {
    pub id: usize,
    pub modules: Vec<String>,
    pub label: String,
}

pub struct FileOwnership {
    pub path: String,
    pub authors: Vec<(String, f64)>,
    pub bus_factor: usize,
}

/// Source code snippet for a finding, read from disk.
pub struct FindingSnippet {
    pub finding_id: String,
    pub code: String,
    pub highlight_lines: Vec<u32>,
    pub language: String,
}
