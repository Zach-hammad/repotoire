//! Analysis tool handlers
//!
//! Implements `analyze`, `get_findings`, and `get_hotspots` MCP tools.

use anyhow::Result;
use serde_json::{json, Value};

use crate::detectors::{default_detectors_with_ngram, walk_source_files, DetectorEngineBuilder, SourceFiles};
use crate::mcp::state::HandlerState;
use crate::mcp::params::{AnalyzeParams, DiffParams, GetFindingsParams, GetHotspotsParams};
use crate::models::FindingsSummary;

/// Run code analysis on the repository.
///
/// Builds a `DetectorEngine` with the project config and style profile,
/// scans all source files, and returns a JSON summary with `status`,
/// `total_findings`, and `by_severity` breakdown.
pub fn handle_analyze(state: &mut HandlerState, params: &AnalyzeParams) -> Result<Value> {
    let _incremental = params.incremental.unwrap_or(true);

    let repo_path = state.repo_path.clone();

    // Get graph client
    let graph = state.graph()?;

    // Build detector engine (with predictive coding)
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

    // Run analysis
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

/// Get findings from the last analysis with pagination.
///
/// Reads cached findings from `.repotoire/last_findings.json` when available,
/// falling back to running a fresh analysis. Supports filtering by severity
/// and detector name. Returns paginated results with `offset`, `has_more`,
/// and `total_count` fields.
pub fn handle_get_findings(state: &mut HandlerState, params: &GetFindingsParams) -> Result<Value> {
    let severity = params.severity.as_ref().map(|s| s.to_string());
    let detector = params.detector.as_deref();
    let limit = params.limit.unwrap_or(20) as usize;
    let offset = params.offset.unwrap_or(0) as usize;

    // Try to read from findings file first
    let findings_path = state
        .repo_path
        .join(".repotoire")
        .join("last_findings.json");

    if findings_path.exists() {
        let content = std::fs::read_to_string(&findings_path)?;
        let parsed: Value = serde_json::from_str(&content)?;
        let findings_val = parsed.get("findings").ok_or_else(|| {
            anyhow::anyhow!("Cached findings file is malformed (missing 'findings' key). Re-run: repotoire analyze")
        })?;
        let mut findings: Vec<Value> = findings_val
            .as_array()
            .cloned()
            .unwrap_or_default();

        // Apply filters
        if let Some(ref sev) = severity {
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
        let page: Vec<Value> = findings.into_iter().skip(offset).take(limit).collect();
        let has_more = offset + page.len() < total_count;

        return Ok(json!({
            "findings": page,
            "total_count": total_count,
            "offset": offset,
            "returned": page.len(),
            "has_more": has_more
        }));
    }

    // Fall back to running analysis (with predictive coding)
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
    let mut findings = engine.run(&graph, &source_files)?;

    // Apply filters
    if let Some(ref sev) = severity {
        findings.retain(|f| f.severity.to_string() == *sev);
    }
    if let Some(det) = detector {
        findings.retain(|f| f.detector == det);
    }

    let total_count = findings.len();
    let page: Vec<&_> = findings.iter().skip(offset).take(limit).collect();
    let has_more = offset + page.len() < total_count;

    Ok(json!({
        "findings": page,
        "total_count": total_count,
        "offset": offset,
        "returned": page.len(),
        "has_more": has_more
    }))
}

/// Get hotspot files ranked by finding count.
///
/// Reads findings from `.repotoire/last_findings.json` and aggregates
/// counts per file. Returns an actionable error if no findings file
/// exists, directing the user to run `analyze` first.
pub fn handle_get_hotspots(state: &mut HandlerState, params: &GetHotspotsParams) -> Result<Value> {
    let limit = params.limit.unwrap_or(10) as usize;

    // Try findings file first
    let findings_path = state
        .repo_path
        .join(".repotoire")
        .join("last_findings.json");

    if !findings_path.exists() {
        return Ok(json!({
            "error": "No findings available. Run 'analyze' first.",
            "hint": "Use the 'analyze' tool to generate findings, then call 'get_hotspots' again."
        }));
    }

    let content = std::fs::read_to_string(&findings_path)?;
    let parsed: Value = serde_json::from_str(&content)?;
    let findings_val = parsed.get("findings").ok_or_else(|| {
        anyhow::anyhow!("Cached findings file is malformed (missing 'findings' key). Re-run: repotoire analyze")
    })?;
    let findings: Vec<Value> = findings_val
        .as_array()
        .cloned()
        .unwrap_or_default();

    // Count findings per file
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

    Ok(json!({
        "hotspots": hotspots,
        "total_files": hotspots.len()
    }))
}

/// Compare findings between two analysis states.
///
/// Loads baseline from cache, runs analysis on HEAD, diffs the two sets.
/// Returns new findings, fixed findings, and score delta.
pub fn handle_diff(state: &mut HandlerState, params: &DiffParams) -> Result<Value> {
    use crate::cli::diff::{diff_findings, format_json};

    let repo_path = state.repo_path.clone();
    let repotoire_dir = repo_path.join(".repotoire");

    // Load baseline
    let baseline = crate::cli::analyze::output::load_cached_findings(&repotoire_dir)
        .ok_or_else(|| anyhow::anyhow!("No baseline found. Run repotoire_analyze first."))?;

    let score_before = {
        let path = repotoire_dir.join("last_health.json");
        std::fs::read_to_string(&path)
            .ok()
            .and_then(|data| serde_json::from_str::<serde_json::Value>(&data).ok())
            .and_then(|j| j.get("health_score").and_then(|v| v.as_f64()))
    };

    // Run fresh analysis (reuse existing handle_analyze)
    let analyze_params = crate::mcp::params::AnalyzeParams {
        incremental: Some(true),
    };
    let _ = handle_analyze(state, &analyze_params)?;

    // Load head findings
    let head = crate::cli::analyze::output::load_cached_findings(&repotoire_dir)
        .unwrap_or_default();

    let score_after = {
        let path = repotoire_dir.join("last_health.json");
        std::fs::read_to_string(&path)
            .ok()
            .and_then(|data| serde_json::from_str::<serde_json::Value>(&data).ok())
            .and_then(|j| j.get("health_score").and_then(|v| v.as_f64()))
    };

    // Count changed files
    let base_ref = params.base_ref.as_deref().unwrap_or("HEAD~1");
    let files_changed = std::process::Command::new("git")
        .args(["diff", "--name-only", base_ref, "HEAD"])
        .current_dir(&repo_path)
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| {
            String::from_utf8_lossy(&o.stdout)
                .lines()
                .filter(|l| !l.is_empty())
                .count()
        })
        .unwrap_or(0);

    let base_label = params.base_ref.as_deref().unwrap_or("cached");
    let result = diff_findings(
        &baseline,
        &head,
        base_label,
        "HEAD",
        files_changed,
        score_before,
        score_after,
    );

    // Return structured JSON
    let json_str = format_json(&result);
    let value: serde_json::Value = serde_json::from_str(&json_str)?;

    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;
    use anyhow::Result;
    use std::path::PathBuf;
    use tempfile::tempdir;

    #[test]
    fn test_handle_get_hotspots_no_findings_file() -> Result<()> {
        let dir = tempdir()?;
        let mut state = HandlerState::new(dir.path().to_path_buf(), false);
        let params = GetHotspotsParams { limit: None };
        let result = handle_get_hotspots(&mut state, &params)?;
        assert!(result.get("error").is_some());
        assert!(result.get("hint").is_some());
        Ok(())
    }

    #[test]
    fn test_handle_get_findings_no_findings_file_no_graph() -> Result<()> {
        let dir = tempdir()?;
        let mut state = HandlerState::new(dir.path().to_path_buf(), false);
        let params = GetFindingsParams {
            severity: None,
            detector: None,
            limit: Some(5),
            offset: Some(0),
        };
        // Should fall back to running analysis, which will attempt to
        // initialize the graph store. This may succeed (empty analysis)
        // or fail. Either way it should not panic.
        let _result = handle_get_findings(&mut state, &params);
        Ok(())
    }

    #[test]
    fn test_handle_get_findings_cached() -> Result<()> {
        let dir = tempdir()?;
        let repotoire_dir = dir.path().join(".repotoire");
        std::fs::create_dir_all(&repotoire_dir)?;
        std::fs::write(
            repotoire_dir.join("last_findings.json"),
            r#"{"findings":[
                {"severity":"high","detector":"test","affected_files":["a.rs"]},
                {"severity":"low","detector":"test","affected_files":["b.rs"]},
                {"severity":"high","detector":"other","affected_files":["c.rs"]}
            ]}"#,
        )?;

        let mut state = HandlerState::new(dir.path().to_path_buf(), false);

        // No filter
        let params = GetFindingsParams {
            severity: None,
            detector: None,
            limit: Some(10),
            offset: Some(0),
        };
        let result = handle_get_findings(&mut state, &params)?;
        assert_eq!(result["total_count"], 3);
        assert_eq!(result["returned"], 3);
        assert_eq!(result["has_more"], false);

        // Filter by severity
        let params = GetFindingsParams {
            severity: Some(crate::mcp::params::SeverityFilter::High),
            detector: None,
            limit: Some(10),
            offset: Some(0),
        };
        let result = handle_get_findings(&mut state, &params)?;
        assert_eq!(result["total_count"], 2);

        // Pagination
        let params = GetFindingsParams {
            severity: None,
            detector: None,
            limit: Some(2),
            offset: Some(0),
        };
        let result = handle_get_findings(&mut state, &params)?;
        assert_eq!(result["returned"], 2);
        assert_eq!(result["has_more"], true);

        // Offset past results
        let params = GetFindingsParams {
            severity: None,
            detector: None,
            limit: Some(10),
            offset: Some(3),
        };
        let result = handle_get_findings(&mut state, &params)?;
        assert_eq!(result["returned"], 0);
        assert_eq!(result["has_more"], false);
        Ok(())
    }

    #[test]
    fn test_handle_get_hotspots_cached() -> Result<()> {
        let dir = tempdir()?;
        let repotoire_dir = dir.path().join(".repotoire");
        std::fs::create_dir_all(&repotoire_dir)?;
        std::fs::write(
            repotoire_dir.join("last_findings.json"),
            r#"{"findings":[
                {"severity":"high","affected_files":["a.rs"]},
                {"severity":"medium","affected_files":["a.rs"]},
                {"severity":"low","affected_files":["b.rs"]}
            ]}"#,
        )?;

        let mut state = HandlerState::new(dir.path().to_path_buf(), false);
        let params = GetHotspotsParams { limit: Some(1) };
        let result = handle_get_hotspots(&mut state, &params)?;

        let hotspots = result["hotspots"].as_array()
            .expect("expected hotspots array");
        assert_eq!(hotspots.len(), 1);
        // a.rs has 2 findings, should be first
        assert_eq!(hotspots[0]["file_path"], "a.rs");
        assert_eq!(hotspots[0]["finding_count"], 2);
        Ok(())
    }

    #[test]
    fn test_handle_get_findings_malformed_cache() -> Result<()> {
        let dir = tempdir()?;
        let repotoire_dir = dir.path().join(".repotoire");
        std::fs::create_dir_all(&repotoire_dir)?;
        // Write JSON without the required "findings" key
        std::fs::write(
            repotoire_dir.join("last_findings.json"),
            r#"{"version": 1, "detectors": []}"#,
        )?;

        let mut state = HandlerState::new(dir.path().to_path_buf(), false);
        let params = GetFindingsParams {
            severity: None,
            detector: None,
            limit: Some(10),
            offset: Some(0),
        };
        let result = handle_get_findings(&mut state, &params);
        assert!(result.is_err());
        let err_msg = result.expect_err("expected malformed cache error").to_string();
        assert!(err_msg.contains("malformed"));
        assert!(err_msg.contains("findings"));
        Ok(())
    }

    #[test]
    fn test_get_findings_no_cache() {
        let mut state = HandlerState::new(PathBuf::from("/tmp/nonexistent_repo_xyz"), false);
        let params = GetFindingsParams {
            severity: None,
            detector: None,
            limit: Some(5),
            offset: None,
        };
        // No cache file and no graph — should not panic.
        // Falls back to running analysis which tries to init graph.
        let _result = handle_get_findings(&mut state, &params);
    }

    #[test]
    fn test_handle_get_hotspots_malformed_cache() -> Result<()> {
        let dir = tempdir()?;
        let repotoire_dir = dir.path().join(".repotoire");
        std::fs::create_dir_all(&repotoire_dir)?;
        // Write JSON without the required "findings" key
        std::fs::write(
            repotoire_dir.join("last_findings.json"),
            r#"{"version": 1, "detectors": []}"#,
        )?;

        let mut state = HandlerState::new(dir.path().to_path_buf(), false);
        let params = GetHotspotsParams { limit: None };
        let result = handle_get_hotspots(&mut state, &params);
        assert!(result.is_err());
        let err_msg = result.expect_err("expected malformed cache error").to_string();
        assert!(err_msg.contains("malformed"));
        assert!(err_msg.contains("findings"));
        Ok(())
    }
}
