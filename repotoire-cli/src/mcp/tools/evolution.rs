//! Evolution tool handler
//!
//! Implements `query_evolution` — dispatches temporal / git history queries
//! to the existing git2 integration (`GitHistory`, `GitBlame`).
//!
//! Supports 7 query types:
//! - `FileChurn`       — churn metrics for a single file
//! - `HottestFiles`    — all files ranked by commit count
//! - `FileCommits`     — commit history for a file
//! - `FunctionHistory` — commits touching a function's line range
//! - `EntityBlame`     — blame summary for a line range
//! - `FileOwnership`   — per-author ownership percentages
//! - `RecentCommits`   — recent commits across the repo

use anyhow::Result;
use serde_json::{json, Value};

use crate::git::blame::GitBlame;
use crate::git::GitHistory;
use crate::mcp::handlers::HandlerState;
use crate::mcp::params::{EvolutionQueryType, QueryEvolutionParams};

/// Handle a `query_evolution` request.
///
/// Dispatches to the appropriate git2 API based on `params.query_type`.
/// Returns a JSON error object (not an `Err`) for missing parameters or
/// non-git repos, keeping the MCP response well-formed.
pub fn handle_query_evolution(
    state: &mut HandlerState,
    params: &QueryEvolutionParams,
) -> Result<Value> {
    let repo_path = &state.repo_path;

    // Verify the repo path is inside a git repository
    if !GitHistory::is_git_repo(repo_path) {
        return Ok(json!({
            "error": "Not a git repository. repotoire_query_evolution requires a git repo."
        }));
    }

    let limit = params.limit.unwrap_or(20) as usize;

    match params.query_type {
        EvolutionQueryType::FileChurn => handle_file_churn(repo_path, params),
        EvolutionQueryType::HottestFiles => handle_hottest_files(repo_path, limit),
        EvolutionQueryType::FileCommits => handle_file_commits(repo_path, params, limit),
        EvolutionQueryType::FunctionHistory => handle_function_history(state, params, limit),
        EvolutionQueryType::EntityBlame => handle_entity_blame(repo_path, params),
        EvolutionQueryType::FileOwnership => handle_file_ownership(repo_path, params),
        EvolutionQueryType::RecentCommits => handle_recent_commits(repo_path, limit),
    }
}

// ─── FileChurn ───────────────────────────────────────────────────────────────

fn handle_file_churn(
    repo_path: &std::path::Path,
    params: &QueryEvolutionParams,
) -> Result<Value> {
    let file = match params.file.as_deref() {
        Some(f) if !f.is_empty() => f,
        _ => {
            return Ok(json!({
                "error": "file_churn requires the `file` parameter."
            }));
        }
    };

    let history = GitHistory::new(repo_path)?;
    let churn = history.get_file_churn(file, 500)?;

    Ok(json!({
        "file": file,
        "insertions": churn.total_insertions,
        "deletions": churn.total_deletions,
        "commit_count": churn.commit_count,
        "authors": churn.authors,
        "last_modified": churn.last_modified,
    }))
}

// ─── HottestFiles ────────────────────────────────────────────────────────────

fn handle_hottest_files(repo_path: &std::path::Path, limit: usize) -> Result<Value> {
    let history = GitHistory::new(repo_path)?;
    let churn_map = history.get_all_file_churn(500)?;

    let total_count = churn_map.len();

    // Sort by commit count descending
    let mut entries: Vec<_> = churn_map.into_iter().collect();
    entries.sort_by(|a, b| b.1.commit_count.cmp(&a.1.commit_count));

    let has_more = entries.len() > limit;
    entries.truncate(limit);

    let files: Vec<Value> = entries
        .into_iter()
        .map(|(path, churn)| {
            json!({
                "path": path,
                "commits": churn.commit_count,
                "insertions": churn.total_insertions,
                "deletions": churn.total_deletions,
                "authors": churn.authors,
                "last_modified": churn.last_modified,
            })
        })
        .collect();

    Ok(json!({
        "files": files,
        "total_count": total_count,
        "has_more": has_more,
    }))
}

// ─── FileCommits ─────────────────────────────────────────────────────────────

fn handle_file_commits(
    repo_path: &std::path::Path,
    params: &QueryEvolutionParams,
    limit: usize,
) -> Result<Value> {
    let file = match params.file.as_deref() {
        Some(f) if !f.is_empty() => f,
        _ => {
            return Ok(json!({
                "error": "file_commits requires the `file` parameter."
            }));
        }
    };

    let history = GitHistory::new(repo_path)?;
    let commits = history.get_file_commits(file, limit)?;
    let total_commits = commits.len();

    let commits_json: Vec<Value> = commits
        .iter()
        .map(|c| {
            json!({
                "hash": c.hash,
                "author": c.author,
                "timestamp": c.timestamp,
                "message": c.message,
                "lines_added": c.insertions,
                "lines_deleted": c.deletions,
            })
        })
        .collect();

    Ok(json!({
        "file": file,
        "commits": commits_json,
        "total_commits": total_commits,
    }))
}

// ─── FunctionHistory ─────────────────────────────────────────────────────────

fn handle_function_history(
    state: &mut HandlerState,
    params: &QueryEvolutionParams,
    limit: usize,
) -> Result<Value> {
    let file = match params.file.as_deref() {
        Some(f) if !f.is_empty() => f,
        _ => {
            return Ok(json!({
                "error": "function_history requires the `file` parameter."
            }));
        }
    };

    let name = match params.name.as_deref() {
        Some(n) if !n.is_empty() => n,
        _ => {
            return Ok(json!({
                "error": "function_history requires the `name` parameter."
            }));
        }
    };

    // Look up the function in the graph to get its line range
    let graph = state.graph()?;
    let functions = graph.get_functions_in_file(file);
    let func_node = functions
        .iter()
        .find(|f| f.name == name || f.qualified_name == name);

    let (line_start, line_end) = match func_node {
        Some(node) if node.line_start > 0 && node.line_end > 0 => {
            (node.line_start, node.line_end)
        }
        Some(_) => {
            return Ok(json!({
                "error": format!(
                    "Function '{}' found in graph but has no line range information.",
                    name
                ),
                "hint": "Re-ingest the repository to populate line ranges."
            }));
        }
        None => {
            return Ok(json!({
                "error": format!(
                    "Function '{}' not found in file '{}'. Use query_graph type=functions to list available functions.",
                    name, file
                )
            }));
        }
    };

    let repo_path = &state.repo_path;
    let history = GitHistory::new(repo_path)?;
    let commits = history.get_line_range_commits(file, line_start, line_end, limit)?;
    let total_commits = commits.len();

    // Collect unique authors
    let mut authors_set = std::collections::HashSet::new();
    for c in &commits {
        authors_set.insert(c.author.clone());
    }

    let commits_json: Vec<Value> = commits
        .iter()
        .map(|c| {
            json!({
                "hash": c.hash,
                "author": c.author,
                "timestamp": c.timestamp,
                "message": c.message,
                "lines_added": c.insertions,
                "lines_deleted": c.deletions,
            })
        })
        .collect();

    Ok(json!({
        "function": name,
        "file": file,
        "commits": commits_json,
        "total_commits": total_commits,
        "unique_authors": authors_set.len(),
    }))
}

// ─── EntityBlame ─────────────────────────────────────────────────────────────

fn handle_entity_blame(
    repo_path: &std::path::Path,
    params: &QueryEvolutionParams,
) -> Result<Value> {
    let file = match params.file.as_deref() {
        Some(f) if !f.is_empty() => f,
        _ => {
            return Ok(json!({
                "error": "entity_blame requires the `file` parameter."
            }));
        }
    };

    let line_start = match params.line_start {
        Some(ls) if ls > 0 => ls,
        _ => {
            return Ok(json!({
                "error": "entity_blame requires the `line_start` parameter (> 0)."
            }));
        }
    };

    let line_end = match params.line_end {
        Some(le) if le >= line_start => le,
        _ => {
            return Ok(json!({
                "error": "entity_blame requires the `line_end` parameter (>= line_start)."
            }));
        }
    };

    let blame = GitBlame::open(repo_path)?;
    let info = blame.get_entity_blame(file, line_start, line_end)?;

    Ok(json!({
        "last_modified": info.last_modified,
        "last_author": info.last_author,
        "commit_count": info.commit_count,
        "num_authors": info.author_count,
        "most_recent_commit": info.last_commit,
    }))
}

// ─── FileOwnership ───────────────────────────────────────────────────────────

fn handle_file_ownership(
    repo_path: &std::path::Path,
    params: &QueryEvolutionParams,
) -> Result<Value> {
    let file = match params.file.as_deref() {
        Some(f) if !f.is_empty() => f,
        _ => {
            return Ok(json!({
                "error": "file_ownership requires the `file` parameter."
            }));
        }
    };

    let blame = GitBlame::open(repo_path)?;
    let ownership_map = blame.get_file_ownership(file)?;

    // Sort by percentage descending
    let mut ownership: Vec<Value> = ownership_map
        .into_iter()
        .map(|(author, percentage)| {
            json!({
                "author": author,
                "percentage": (percentage * 100.0).round() / 100.0,
            })
        })
        .collect();
    ownership.sort_by(|a, b| {
        b.get("percentage")
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0)
            .partial_cmp(
                &a.get("percentage")
                    .and_then(|v| v.as_f64())
                    .unwrap_or(0.0),
            )
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    Ok(json!({
        "file": file,
        "ownership": ownership,
    }))
}

// ─── RecentCommits ───────────────────────────────────────────────────────────

fn handle_recent_commits(repo_path: &std::path::Path, limit: usize) -> Result<Value> {
    let history = GitHistory::new(repo_path)?;
    let commits = history.get_recent_commits(limit, None)?;
    let count = commits.len();

    let commits_json: Vec<Value> = commits
        .iter()
        .map(|c| {
            json!({
                "hash": c.hash,
                "author": c.author,
                "timestamp": c.timestamp,
                "message": c.message,
            })
        })
        .collect();

    Ok(json!({
        "commits": commits_json,
        "count": count,
    }))
}

// ─── Tests ───────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::store::{CodeNode, GraphStore};
    use std::sync::Arc;
    use tempfile::tempdir;

    /// Build a HandlerState pointing at a non-git temporary directory.
    fn state_non_git() -> (HandlerState, tempfile::TempDir) {
        let dir = tempdir().unwrap();
        let state = HandlerState::new(dir.path().to_path_buf());
        (state, dir)
    }

    /// Build a HandlerState with a pre-populated graph for function_history tests.
    fn state_with_graph_and_function() -> (HandlerState, tempfile::TempDir) {
        let dir = tempdir().unwrap();
        let mut state = HandlerState::new(dir.path().to_path_buf());

        let graph = GraphStore::in_memory();
        let mut func = CodeNode::function("my_func", "src/lib.rs");
        func.line_start = 10;
        func.line_end = 25;
        func.qualified_name = "src::lib::my_func".to_string();
        graph.add_node(func);

        state.set_graph(Arc::new(graph));
        (state, dir)
    }

    // ── Not a git repo ──────────────────────────────────────────────────────

    #[test]
    fn test_not_a_git_repo() {
        let (mut state, _dir) = state_non_git();
        let params = QueryEvolutionParams {
            query_type: EvolutionQueryType::RecentCommits,
            file: None,
            name: None,
            line_start: None,
            line_end: None,
            limit: None,
        };
        let result = handle_query_evolution(&mut state, &params).unwrap();
        let err = result.get("error").and_then(|v| v.as_str()).unwrap();
        assert!(err.contains("Not a git repository"), "got: {}", err);
    }

    // ── Missing file parameter ──────────────────────────────────────────────

    #[test]
    fn test_file_churn_missing_file() {
        // We test the inner handler directly to avoid the git-repo check
        let dir = tempdir().unwrap();
        let params = QueryEvolutionParams {
            query_type: EvolutionQueryType::FileChurn,
            file: None,
            name: None,
            line_start: None,
            line_end: None,
            limit: None,
        };
        let result = handle_file_churn(dir.path(), &params).unwrap();
        let err = result.get("error").and_then(|v| v.as_str()).unwrap();
        assert!(err.contains("file_churn requires"), "got: {}", err);
    }

    #[test]
    fn test_file_commits_missing_file() {
        let dir = tempdir().unwrap();
        let params = QueryEvolutionParams {
            query_type: EvolutionQueryType::FileCommits,
            file: None,
            name: None,
            line_start: None,
            line_end: None,
            limit: None,
        };
        let result = handle_file_commits(dir.path(), &params, 20).unwrap();
        let err = result.get("error").and_then(|v| v.as_str()).unwrap();
        assert!(err.contains("file_commits requires"), "got: {}", err);
    }

    #[test]
    fn test_file_ownership_missing_file() {
        let dir = tempdir().unwrap();
        let params = QueryEvolutionParams {
            query_type: EvolutionQueryType::FileOwnership,
            file: None,
            name: None,
            line_start: None,
            line_end: None,
            limit: None,
        };
        let result = handle_file_ownership(dir.path(), &params).unwrap();
        let err = result.get("error").and_then(|v| v.as_str()).unwrap();
        assert!(err.contains("file_ownership requires"), "got: {}", err);
    }

    // ── Missing name parameter for function_history ─────────────────────────

    #[test]
    fn test_function_history_missing_file() {
        let (mut state, _dir) = state_with_graph_and_function();
        let params = QueryEvolutionParams {
            query_type: EvolutionQueryType::FunctionHistory,
            file: None,
            name: Some("my_func".to_string()),
            line_start: None,
            line_end: None,
            limit: None,
        };
        // Bypass git check — call inner handler directly
        let result = handle_function_history(&mut state, &params, 20).unwrap();
        let err = result.get("error").and_then(|v| v.as_str()).unwrap();
        assert!(
            err.contains("function_history requires the `file`"),
            "got: {}",
            err
        );
    }

    #[test]
    fn test_function_history_missing_name() {
        let (mut state, _dir) = state_with_graph_and_function();
        let params = QueryEvolutionParams {
            query_type: EvolutionQueryType::FunctionHistory,
            file: Some("src/lib.rs".to_string()),
            name: None,
            line_start: None,
            line_end: None,
            limit: None,
        };
        let result = handle_function_history(&mut state, &params, 20).unwrap();
        let err = result.get("error").and_then(|v| v.as_str()).unwrap();
        assert!(
            err.contains("function_history requires the `name`"),
            "got: {}",
            err
        );
    }

    #[test]
    fn test_function_history_not_in_graph() {
        let (mut state, _dir) = state_with_graph_and_function();
        let params = QueryEvolutionParams {
            query_type: EvolutionQueryType::FunctionHistory,
            file: Some("src/lib.rs".to_string()),
            name: Some("nonexistent".to_string()),
            line_start: None,
            line_end: None,
            limit: None,
        };
        let result = handle_function_history(&mut state, &params, 20).unwrap();
        let err = result.get("error").and_then(|v| v.as_str()).unwrap();
        assert!(err.contains("not found"), "got: {}", err);
    }

    // ── Missing line_start/line_end for entity_blame ────────────────────────

    #[test]
    fn test_entity_blame_missing_file() {
        let dir = tempdir().unwrap();
        let params = QueryEvolutionParams {
            query_type: EvolutionQueryType::EntityBlame,
            file: None,
            name: None,
            line_start: Some(1),
            line_end: Some(10),
            limit: None,
        };
        let result = handle_entity_blame(dir.path(), &params).unwrap();
        let err = result.get("error").and_then(|v| v.as_str()).unwrap();
        assert!(err.contains("entity_blame requires the `file`"), "got: {}", err);
    }

    #[test]
    fn test_entity_blame_missing_line_start() {
        let dir = tempdir().unwrap();
        let params = QueryEvolutionParams {
            query_type: EvolutionQueryType::EntityBlame,
            file: Some("test.py".to_string()),
            name: None,
            line_start: None,
            line_end: Some(10),
            limit: None,
        };
        let result = handle_entity_blame(dir.path(), &params).unwrap();
        let err = result.get("error").and_then(|v| v.as_str()).unwrap();
        assert!(
            err.contains("entity_blame requires the `line_start`"),
            "got: {}",
            err
        );
    }

    #[test]
    fn test_entity_blame_missing_line_end() {
        let dir = tempdir().unwrap();
        let params = QueryEvolutionParams {
            query_type: EvolutionQueryType::EntityBlame,
            file: Some("test.py".to_string()),
            name: None,
            line_start: Some(1),
            line_end: None,
            limit: None,
        };
        let result = handle_entity_blame(dir.path(), &params).unwrap();
        let err = result.get("error").and_then(|v| v.as_str()).unwrap();
        assert!(
            err.contains("entity_blame requires the `line_end`"),
            "got: {}",
            err
        );
    }

    // ── Empty file param treated as missing ─────────────────────────────────

    #[test]
    fn test_file_churn_empty_file() {
        let dir = tempdir().unwrap();
        let params = QueryEvolutionParams {
            query_type: EvolutionQueryType::FileChurn,
            file: Some(String::new()),
            name: None,
            line_start: None,
            line_end: None,
            limit: None,
        };
        let result = handle_file_churn(dir.path(), &params).unwrap();
        assert!(result.get("error").is_some());
    }
}
