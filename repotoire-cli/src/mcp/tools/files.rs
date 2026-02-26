//! File tool handlers
//!
//! Implements `get_file`, `get_architecture`, and `list_detectors` MCP tools.

use anyhow::Result;
use serde_json::{json, Value};

use crate::detectors::default_detectors_with_ngram;
use crate::mcp::state::HandlerState;
use crate::mcp::params::GetFileParams;

/// Read file content from the repository.
///
/// Resolves the requested path relative to `state.repo_path`, canonicalizes
/// both paths, and rejects the request when either path cannot be
/// canonicalized or the resolved path escapes the repository root.
///
/// Supports optional `start_line` / `end_line` (1-indexed) to return a
/// sub-range. Returns JSON with `path`, `content`, `total_lines`, and
/// `showing_lines`.
pub fn handle_get_file(state: &HandlerState, params: &GetFileParams) -> Result<Value> {
    // Prevent path traversal -- reject paths that can't be canonicalized
    let full_path = state.repo_path.join(&params.file_path);
    let repo_canonical = match state.repo_path.canonicalize() {
        Ok(p) => p,
        Err(_) => {
            return Ok(json!({
                "error": "Cannot resolve repository root path"
            }));
        }
    };
    let canonical = match full_path.canonicalize() {
        Ok(p) => p,
        Err(_) => {
            return Ok(json!({
                "error": format!("File not found or inaccessible: {}", params.file_path)
            }));
        }
    };

    if !canonical.starts_with(&repo_canonical) {
        return Ok(json!({
            "error": "Access denied: path outside repository"
        }));
    }
    if !canonical.exists() {
        return Ok(json!({
            "error": format!("File not found: {}", params.file_path)
        }));
    }

    let content = std::fs::read_to_string(&canonical)?;
    let lines: Vec<&str> = content.lines().collect();
    let total_lines = lines.len();

    let (content, showing) = if params.start_line.is_some() || params.end_line.is_some() {
        let start = params
            .start_line
            .map(|n| (n as usize).saturating_sub(1))
            .unwrap_or(0);
        let end = params
            .end_line
            .map(|n| n as usize)
            .unwrap_or(total_lines);
        let selected: Vec<&str> = lines.into_iter().skip(start).take(end - start).collect();
        let showing = format!("{}-{}", start + 1, start + selected.len());
        (selected.join("\n"), showing)
    } else {
        (content, format!("1-{}", total_lines))
    };

    Ok(json!({
        "path": params.file_path,
        "content": content,
        "total_lines": total_lines,
        "showing_lines": showing
    }))
}

/// Get codebase architecture overview.
///
/// Queries the graph store for node counts (via `stats()`), language
/// distribution from file nodes, and top classes sorted by method count
/// (limited to 20). Returns JSON with `node_counts`, `languages`, and
/// `top_classes`.
pub fn handle_get_architecture(state: &mut HandlerState) -> Result<Value> {
    let graph = state.graph()?;

    // Get node counts
    let stats = graph.stats();

    // Get language distribution
    let files = graph.get_files();
    let mut lang_counts: std::collections::HashMap<String, i64> =
        std::collections::HashMap::new();
    for file in &files {
        let lang = file
            .language
            .clone()
            .unwrap_or_else(|| "unknown".to_string());
        *lang_counts.entry(lang).or_insert(0) += 1;
    }
    let languages: Vec<Value> = lang_counts
        .into_iter()
        .map(|(lang, count)| json!({"language": lang, "file_count": count}))
        .collect();

    // Get class overview with method counts
    let classes = graph.get_classes();
    let mut top_classes: Vec<Value> = classes
        .iter()
        .map(|c| {
            json!({
                "class_name": c.name,
                "file": c.file_path,
                "method_count": c.get_i64("methodCount").unwrap_or(0)
            })
        })
        .collect();
    top_classes.sort_by(|a, b| {
        let a_count = a
            .get("method_count")
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        let b_count = b
            .get("method_count")
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        b_count.cmp(&a_count)
    });
    top_classes.truncate(20);

    Ok(json!({
        "node_counts": stats,
        "languages": languages,
        "top_classes": top_classes
    }))
}

/// List all available detectors.
///
/// Creates a confident dummy `NgramModel` (trained for 800 iterations so
/// `SurprisalDetector` appears in the list) and builds the default
/// detector set. Returns JSON with a `detectors` array (name, description,
/// category) and a `count`.
pub fn handle_list_detectors(state: &HandlerState) -> Result<Value> {
    // Use a confident dummy model so SurprisalDetector appears in the list
    let mut dummy_model = crate::calibrate::NgramModel::new();
    for _ in 0..800 {
        dummy_model.train_on_tokens(&[
            "let".into(),
            "<ID>".into(),
            "=".into(),
            "<ID>".into(),
            ";".into(),
            "<EOL>".into(),
            "fn".into(),
            "<ID>".into(),
            "(".into(),
            ")".into(),
        ]);
    }

    let detectors = default_detectors_with_ngram(
        &state.repo_path,
        &crate::config::ProjectConfig::default(),
        None,
        Some(dummy_model),
    );

    let detector_info: Vec<Value> = detectors
        .iter()
        .map(|d| {
            json!({
                "name": d.name(),
                "description": d.description(),
                "category": d.category()
            })
        })
        .collect();

    Ok(json!({
        "detectors": detector_info,
        "count": detector_info.len()
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use anyhow::Result;
    use tempfile::tempdir;

    #[test]
    fn test_get_file_not_found() -> Result<()> {
        let dir = tempdir()?;
        let state = HandlerState::new(dir.path().to_path_buf(), false);
        let params = GetFileParams {
            file_path: "nonexistent.txt".to_string(),
            start_line: None,
            end_line: None,
        };
        let result = handle_get_file(&state, &params)?;
        assert!(result.get("error").is_some());
        Ok(())
    }

    #[test]
    fn test_get_file_success() -> Result<()> {
        let dir = tempdir()?;
        std::fs::write(dir.path().join("test.txt"), "line1\nline2\nline3")?;

        let state = HandlerState::new(dir.path().to_path_buf(), false);
        let params = GetFileParams {
            file_path: "test.txt".to_string(),
            start_line: None,
            end_line: None,
        };
        let result = handle_get_file(&state, &params)?;

        assert_eq!(result.get("total_lines").and_then(|v| v.as_u64()), Some(3));
        assert_eq!(
            result.get("showing_lines").and_then(|v| v.as_str()),
            Some("1-3")
        );
        Ok(())
    }

    #[test]
    fn test_get_file_line_range() -> Result<()> {
        let dir = tempdir()?;
        std::fs::write(dir.path().join("test.txt"), "line1\nline2\nline3\nline4\nline5")?;

        let state = HandlerState::new(dir.path().to_path_buf(), false);
        let params = GetFileParams {
            file_path: "test.txt".to_string(),
            start_line: Some(2),
            end_line: Some(4),
        };
        let result = handle_get_file(&state, &params)?;

        assert_eq!(result.get("total_lines").and_then(|v| v.as_u64()), Some(5));
        assert_eq!(
            result.get("content").and_then(|v| v.as_str()),
            Some("line2\nline3\nline4")
        );
        assert_eq!(
            result.get("showing_lines").and_then(|v| v.as_str()),
            Some("2-4")
        );
        Ok(())
    }

    #[test]
    fn test_get_file_path_traversal() -> Result<()> {
        let dir = tempdir()?;
        let state = HandlerState::new(dir.path().to_path_buf(), false);
        let params = GetFileParams {
            file_path: "../../../etc/passwd".to_string(),
            start_line: None,
            end_line: None,
        };
        let result = handle_get_file(&state, &params)?;
        let error = result.get("error").and_then(|v| v.as_str())
            .expect("expected error field in response");
        assert!(
            error.contains("Access denied") || error.contains("File not found"),
            "Expected access denied or file not found, got: {}",
            error
        );
        Ok(())
    }

    #[test]
    fn test_get_file_nonexistent() -> Result<()> {
        let dir = tempdir()?;
        let state = HandlerState::new(dir.path().to_path_buf(), false);
        let params = GetFileParams {
            file_path: "does_not_exist.rs".into(),
            start_line: None,
            end_line: None,
        };
        let result = handle_get_file(&state, &params)?;
        assert!(result.get("error").is_some());
        Ok(())
    }

    #[test]
    fn test_list_detectors() -> Result<()> {
        let dir = tempdir()?;
        let state = HandlerState::new(dir.path().to_path_buf(), false);
        let result = handle_list_detectors(&state)?;

        let detectors = result.get("detectors").and_then(|v| v.as_array())
            .expect("expected detectors array");
        assert!(!detectors.is_empty());
        assert!(result.get("count").and_then(|v| v.as_u64())
            .expect("expected count field") > 0);

        // Each detector should have name, description, category
        for d in detectors {
            assert!(d.get("name").is_some());
            assert!(d.get("description").is_some());
            assert!(d.get("category").is_some());
        }
        Ok(())
    }
}
