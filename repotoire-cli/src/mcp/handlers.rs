//! MCP Tool handlers
//!
//! Implementation of each MCP tool's functionality.

use anyhow::{Context, Result};
use serde_json::{json, Value};
use std::path::PathBuf;
use std::sync::Arc;

use crate::detectors::{default_detectors, DetectorEngineBuilder};
use crate::graph::GraphClient;
use crate::models::FindingsSummary;

/// State shared across tool calls
pub struct HandlerState {
    /// Path to the repository being analyzed
    pub repo_path: PathBuf,
    /// Graph client (lazily initialized)
    graph: Option<Arc<GraphClient>>,
    /// API key for PRO features
    pub api_key: Option<String>,
    /// API base URL
    pub api_url: String,
}

impl HandlerState {
    pub fn new(repo_path: PathBuf) -> Self {
        let api_key = std::env::var("REPOTOIRE_API_KEY").ok();
        let api_url = std::env::var("REPOTOIRE_API_URL")
            .unwrap_or_else(|_| "https://api.repotoire.io".to_string());

        Self {
            repo_path,
            graph: None,
            api_key,
            api_url,
        }
    }

    pub fn is_pro(&self) -> bool {
        self.api_key.is_some()
    }

    /// Get or initialize the graph client
    pub fn get_graph(&mut self) -> Result<Arc<GraphClient>> {
        if let Some(ref client) = self.graph {
            return Ok(Arc::clone(client));
        }

        let db_path = self.repo_path.join(".repotoire").join("graph");
        let client = GraphClient::new(&db_path)
            .context("Failed to initialize graph database")?;
        let client = Arc::new(client);
        self.graph = Some(Arc::clone(&client));
        Ok(client)
    }
}

// =============================================================================
// FREE Tier Tool Handlers
// =============================================================================

/// Run code analysis on the repository
pub fn handle_analyze(state: &mut HandlerState, args: &Value) -> Result<Value> {
    let repo_path = args
        .get("repo_path")
        .and_then(|v| v.as_str())
        .map(PathBuf::from)
        .unwrap_or_else(|| state.repo_path.clone());

    let _incremental = args
        .get("incremental")
        .and_then(|v| v.as_bool())
        .unwrap_or(true);

    // Get graph client
    let graph = state.get_graph()?;

    // Build detector engine
    let engine = DetectorEngineBuilder::new()
        .workers(4)
        .detectors(default_detectors(&repo_path))
        .build();

    // Run analysis - engine.run() returns Vec<Finding> directly
    let findings = engine.run(&graph)?;

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

/// Execute a Cypher query on the code graph
pub fn handle_query_graph(state: &mut HandlerState, args: &Value) -> Result<Value> {
    let cypher = args
        .get("cypher")
        .and_then(|v| v.as_str())
        .context("Missing required argument: cypher")?;

    let graph = state.get_graph()?;

    let results = graph.execute(cypher)?;

    Ok(json!({
        "results": results,
        "count": results.len()
    }))
}

/// Get findings from the last analysis
pub fn handle_get_findings(state: &mut HandlerState, args: &Value) -> Result<Value> {
    let severity = args.get("severity").and_then(|v| v.as_str());
    let detector = args.get("detector").and_then(|v| v.as_str());
    let limit = args
        .get("limit")
        .and_then(|v| v.as_u64())
        .unwrap_or(20) as usize;

    // Try to read from findings file first
    let findings_path = state.repo_path.join(".repotoire").join("last_findings.json");
    if findings_path.exists() {
        let content = std::fs::read_to_string(&findings_path)?;
        let parsed: Value = serde_json::from_str(&content)?;
        let mut findings: Vec<Value> = parsed
            .get("findings")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();

        // Apply filters
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

        let count = findings.len();
        findings.truncate(limit);

        return Ok(json!({
            "findings": findings,
            "count": count,
            "returned": findings.len()
        }));
    }

    // Fall back to running analysis
    let graph = state.get_graph()?;
    let engine = DetectorEngineBuilder::new()
        .workers(4)
        .detectors(default_detectors(&state.repo_path))
        .build();

    // engine.run() returns Vec<Finding> directly
    let mut findings = engine.run(&graph)?;

    // Apply filters
    if let Some(sev) = severity {
        findings.retain(|f| f.severity.to_string() == sev);
    }
    if let Some(det) = detector {
        findings.retain(|f| f.detector == det);
    }

    let count = findings.len();
    findings.truncate(limit);

    Ok(json!({
        "findings": findings,
        "count": count,
        "returned": findings.len()
    }))
}

/// Read file content
pub fn handle_get_file(state: &HandlerState, args: &Value) -> Result<Value> {
    let file_path = args
        .get("file_path")
        .and_then(|v| v.as_str())
        .context("Missing required argument: file_path")?;

    let start_line = args.get("start_line").and_then(|v| v.as_u64());
    let end_line = args.get("end_line").and_then(|v| v.as_u64());

    let full_path = state.repo_path.join(file_path);
    if !full_path.exists() {
        return Ok(json!({
            "error": format!("File not found: {}", file_path)
        }));
    }

    let content = std::fs::read_to_string(&full_path)?;
    let lines: Vec<&str> = content.lines().collect();
    let total_lines = lines.len();

    let (content, showing) = if start_line.is_some() || end_line.is_some() {
        let start = start_line.map(|n| (n as usize).saturating_sub(1)).unwrap_or(0);
        let end = end_line.map(|n| n as usize).unwrap_or(total_lines);
        let selected: Vec<&str> = lines.into_iter().skip(start).take(end - start).collect();
        let showing = format!("{}-{}", start + 1, start + selected.len());
        (selected.join("\n"), showing)
    } else {
        (content, format!("1-{}", total_lines))
    };

    Ok(json!({
        "path": file_path,
        "content": content,
        "total_lines": total_lines,
        "showing_lines": showing
    }))
}

/// Get codebase architecture overview
pub fn handle_get_architecture(state: &mut HandlerState, _args: &Value) -> Result<Value> {
    let graph = state.get_graph()?;

    // Get node counts
    let stats = graph.get_stats()?;

    // Get top-level module structure
    let modules_query = r#"
        MATCH (f:File)
        RETURN f.language AS language, count(*) AS file_count
        ORDER BY file_count DESC
    "#;
    let languages = graph.execute_safe(modules_query);

    // Get class overview
    let classes_query = r#"
        MATCH (c:Class)
        OPTIONAL MATCH (c)-[:CONTAINS]->(m:Function)
        RETURN c.name AS class_name, c.filePath AS file, count(m) AS method_count
        ORDER BY method_count DESC
        LIMIT 20
    "#;
    let top_classes = graph.execute_safe(classes_query);

    Ok(json!({
        "node_counts": stats,
        "languages": languages,
        "top_classes": top_classes
    }))
}

/// List available detectors
#[allow(unused_imports)]
pub fn handle_list_detectors(state: &HandlerState, _args: &Value) -> Result<Value> {
    use crate::detectors::Detector;

    let detectors = default_detectors(&state.repo_path);
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

/// Get hotspot files (most issues)
pub fn handle_get_hotspots(state: &mut HandlerState, args: &Value) -> Result<Value> {
    let limit = args
        .get("limit")
        .and_then(|v| v.as_u64())
        .unwrap_or(10) as usize;

    // Try findings file first
    let findings_path = state.repo_path.join(".repotoire").join("last_findings.json");
    if findings_path.exists() {
        let content = std::fs::read_to_string(&findings_path)?;
        let parsed: Value = serde_json::from_str(&content)?;
        let findings: Vec<Value> = parsed
            .get("findings")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();

        // Count findings per file
        let mut file_counts: std::collections::HashMap<String, (usize, Vec<String>)> =
            std::collections::HashMap::new();

        for finding in &findings {
            if let Some(files) = finding.get("affected_files").and_then(|v| v.as_array()) {
                for file in files {
                    if let Some(path) = file.as_str() {
                        let entry = file_counts.entry(path.to_string()).or_insert((0, vec![]));
                        entry.0 += 1;
                        if let Some(sev) = finding.get("severity").and_then(|v| v.as_str()) {
                            entry.1.push(sev.to_string());
                        }
                    }
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
                .cmp(&a.get("finding_count").and_then(|v| v.as_u64()).unwrap_or(0))
        });

        hotspots.truncate(limit);

        return Ok(json!({
            "hotspots": hotspots
        }));
    }

    // No findings file - run quick analysis
    Ok(json!({
        "error": "No findings available. Run 'analyze' first.",
        "hint": "Use the 'analyze' tool to generate findings"
    }))
}

// =============================================================================
// PRO Tier Tool Handlers
// =============================================================================

/// Search code semantically (PRO)
pub async fn handle_search_code(state: &HandlerState, args: &Value) -> Result<Value> {
    if !state.is_pro() {
        return Ok(json!({
            "error": "This feature requires a PRO subscription.",
            "upgrade_url": "https://repotoire.com/pricing"
        }));
    }

    let query = args
        .get("query")
        .and_then(|v| v.as_str())
        .context("Missing required argument: query")?;

    let top_k = args.get("top_k").and_then(|v| v.as_u64()).unwrap_or(10);
    let entity_types = args.get("entity_types");

    let client = reqwest::Client::new();
    let response = client
        .post(format!("{}/api/v1/code/search", state.api_url))
        .header("X-API-Key", state.api_key.as_ref().unwrap())
        .header("Content-Type", "application/json")
        .json(&json!({
            "query": query,
            "top_k": top_k,
            "entity_types": entity_types
        }))
        .send()
        .await?;

    handle_api_response(response).await
}

/// Ask about codebase (PRO)
pub async fn handle_ask(state: &HandlerState, args: &Value) -> Result<Value> {
    if !state.is_pro() {
        return Ok(json!({
            "error": "This feature requires a PRO subscription.",
            "upgrade_url": "https://repotoire.com/pricing"
        }));
    }

    let question = args
        .get("question")
        .and_then(|v| v.as_str())
        .context("Missing required argument: question")?;

    let top_k = args.get("top_k").and_then(|v| v.as_u64()).unwrap_or(10);

    let client = reqwest::Client::new();
    let response = client
        .post(format!("{}/api/v1/code/ask", state.api_url))
        .header("X-API-Key", state.api_key.as_ref().unwrap())
        .header("Content-Type", "application/json")
        .json(&json!({
            "question": question,
            "top_k": top_k
        }))
        .send()
        .await?;

    handle_api_response(response).await
}

/// Generate fix for a finding (PRO)
pub async fn handle_generate_fix(state: &HandlerState, args: &Value) -> Result<Value> {
    if !state.is_pro() {
        return Ok(json!({
            "error": "This feature requires a PRO subscription.",
            "upgrade_url": "https://repotoire.com/pricing"
        }));
    }

    let finding_id = args
        .get("finding_id")
        .and_then(|v| v.as_str())
        .context("Missing required argument: finding_id")?;

    let client = reqwest::Client::new();
    let response = client
        .post(format!("{}/api/v1/fixes/generate", state.api_url))
        .header("X-API-Key", state.api_key.as_ref().unwrap())
        .header("Content-Type", "application/json")
        .json(&json!({
            "finding_id": finding_id
        }))
        .send()
        .await?;

    handle_api_response(response).await
}

/// Handle API response with proper error mapping
async fn handle_api_response(response: reqwest::Response) -> Result<Value> {
    let status = response.status();

    if status == reqwest::StatusCode::UNAUTHORIZED {
        return Ok(json!({
            "error": "Invalid API key. Get your key at https://app.repotoire.io/settings/api-keys"
        }));
    }

    if status == reqwest::StatusCode::PAYMENT_REQUIRED {
        return Ok(json!({
            "error": "Feature requires PRO subscription. Upgrade at https://repotoire.com/pricing"
        }));
    }

    if status == reqwest::StatusCode::TOO_MANY_REQUESTS {
        return Ok(json!({
            "error": "Rate limit exceeded. Please try again later."
        }));
    }

    if !status.is_success() {
        let error_text = response.text().await.unwrap_or_default();
        return Ok(json!({
            "error": format!("API error ({}): {}", status, error_text)
        }));
    }

    let body: Value = response.json().await?;
    Ok(body)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_handler_state_new() {
        let dir = tempdir().unwrap();
        let state = HandlerState::new(dir.path().to_path_buf());
        assert!(!state.is_pro()); // No API key in test env
    }

    #[test]
    fn test_get_file_not_found() {
        let dir = tempdir().unwrap();
        let state = HandlerState::new(dir.path().to_path_buf());
        let result = handle_get_file(&state, &json!({"file_path": "nonexistent.txt"})).unwrap();
        assert!(result.get("error").is_some());
    }

    #[test]
    fn test_get_file_success() {
        let dir = tempdir().unwrap();
        std::fs::write(dir.path().join("test.txt"), "line1\nline2\nline3").unwrap();
        
        let state = HandlerState::new(dir.path().to_path_buf());
        let result = handle_get_file(&state, &json!({"file_path": "test.txt"})).unwrap();
        
        assert_eq!(result.get("total_lines").and_then(|v| v.as_u64()), Some(3));
    }
}
