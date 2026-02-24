//! AI tool handlers
//!
//! Implements `search_code`, `ask`, and `generate_fix` MCP tools.
//!
//! - `search_code` and `ask` are **PRO-only** (require `REPOTOIRE_API_KEY`).
//!   They proxy requests to the Repotoire cloud API.
//! - `generate_fix` works with either a **BYOK** (bring-your-own-key) AI
//!   backend or the PRO cloud API. When BYOK keys are present it uses
//!   `AiClient` and `FixGenerator` locally; otherwise it falls back to the
//!   cloud endpoint.

use anyhow::{Context, Result};
use serde_json::{json, Value};

use crate::ai::{AiClient, FixGenerator, LlmBackend};
use crate::mcp::handlers::HandlerState;
use crate::mcp::params::{AskParams, GenerateFixParams, SearchCodeParams};
use crate::models::Finding;

// ─── Shared helpers ──────────────────────────────────────────────────────────

/// Map a ureq HTTP response to a `Result<Value>`.
///
/// Translates common HTTP status codes into actionable JSON error objects:
/// - **401**: Invalid API key (with signup link)
/// - **402**: PRO subscription required (with pricing link)
/// - **429**: Rate limit exceeded
/// - **4xx/5xx**: Generic API error with response body
///
/// On success (2xx), parses the response body as JSON and returns it.
pub fn handle_api_response(response: ureq::http::Response<ureq::Body>) -> Result<Value> {
    let status = response.status().as_u16();

    if status == 401 {
        return Ok(json!({
            "error": "Invalid API key. Get your key at https://app.repotoire.io/settings/api-keys"
        }));
    }
    if status == 402 {
        return Ok(json!({
            "error": "Feature requires PRO subscription. Upgrade at https://repotoire.com/pricing"
        }));
    }
    if status == 429 {
        return Ok(json!({
            "error": "Rate limit exceeded. Please try again later."
        }));
    }
    if status >= 400 {
        let error_text = response.into_body().read_to_string().unwrap_or_default();
        return Ok(json!({
            "error": format!("API error ({}): {}", status, error_text)
        }));
    }

    let body: Value = response.into_body().read_json()?;
    Ok(body)
}

// ─── search_code (PRO) ──────────────────────────────────────────────────────

/// Search the codebase semantically using AI embeddings (PRO).
///
/// Requires a valid `REPOTOIRE_API_KEY`. When the user is not PRO, returns
/// an actionable error suggesting `query_graph` as the free alternative.
///
/// Proxies the request to `{api_url}/api/v1/code/search` with `query`,
/// `top_k`, and optional `entity_types` parameters.
pub fn handle_search_code(state: &HandlerState, params: &SearchCodeParams) -> Result<Value> {
    if !state.is_pro() {
        return Ok(json!({
            "error": "This feature requires a PRO subscription.",
            "hint": "Use the free 'query_graph' tool to search by type (functions, classes, files) instead.",
            "upgrade_url": "https://repotoire.com/pricing"
        }));
    }

    let top_k = params.top_k.unwrap_or(10);

    let agent = ureq::config::Config::builder()
        .http_status_as_error(false)
        .build()
        .new_agent();

    let response = agent
        .post(&format!("{}/api/v1/code/search", state.api_url))
        .header(
            "X-API-Key",
            state.api_key.as_deref().unwrap_or("missing-key"),
        )
        .header("Content-Type", "application/json")
        .send_json(json!({
            "query": params.query,
            "top_k": top_k,
            "entity_types": params.entity_types
        }))
        .map_err(|e| anyhow::anyhow!("API request failed: {}", e))?;

    handle_api_response(response)
}

// ─── ask (PRO) ───────────────────────────────────────────────────────────────

/// Ask a natural-language question about the codebase using RAG (PRO).
///
/// Requires a valid `REPOTOIRE_API_KEY`. When the user is not PRO, returns
/// an actionable error suggesting `query_graph` as the free alternative.
///
/// Proxies the request to `{api_url}/api/v1/code/ask` with `question` and
/// `top_k` parameters.
pub fn handle_ask(state: &HandlerState, params: &AskParams) -> Result<Value> {
    if !state.is_pro() {
        return Ok(json!({
            "error": "This feature requires a PRO subscription.",
            "hint": "Use the free 'query_graph' tool to explore the codebase structure instead.",
            "upgrade_url": "https://repotoire.com/pricing"
        }));
    }

    let top_k = params.top_k.unwrap_or(10);

    let agent = ureq::config::Config::builder()
        .http_status_as_error(false)
        .build()
        .new_agent();

    let response = agent
        .post(&format!("{}/api/v1/code/ask", state.api_url))
        .header(
            "X-API-Key",
            state.api_key.as_deref().unwrap_or("missing-key"),
        )
        .header("Content-Type", "application/json")
        .send_json(json!({
            "question": params.question,
            "top_k": top_k
        }))
        .map_err(|e| anyhow::anyhow!("API request failed: {}", e))?;

    handle_api_response(response)
}

// ─── generate_fix (BYOK or PRO) ─────────────────────────────────────────────

/// Generate an AI-powered fix for a code finding.
///
/// Supports three modes (checked in order):
/// 1. **BYOK** — if the user has a local AI backend configured (e.g.
///    `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OLLAMA_MODEL`), uses
///    `FixGenerator` locally.
/// 2. **PRO** — if the user has a `REPOTOIRE_API_KEY`, proxies the request
///    to the cloud API.
/// 3. **Neither** — returns an actionable error explaining how to set up
///    AI keys.
pub fn handle_generate_fix(state: &HandlerState, params: &GenerateFixParams) -> Result<Value> {
    // Prefer BYOK (local AI) when available
    if let Some(backend) = &state.ai_backend {
        return handle_generate_fix_local(state, params, *backend);
    }

    // Fall back to cloud PRO API
    if !state.is_pro() {
        return Ok(json!({
            "error": "AI features require an API key.",
            "hint": "Set ANTHROPIC_API_KEY or OPENAI_API_KEY to enable AI fixes locally, or set REPOTOIRE_API_KEY for cloud access.",
            "docs": "https://github.com/Zach-hammad/repotoire#ai-powered-fixes-optional"
        }));
    }

    let agent = ureq::config::Config::builder()
        .http_status_as_error(false)
        .build()
        .new_agent();

    let response = agent
        .post(&format!("{}/api/v1/fixes/generate", state.api_url))
        .header(
            "X-API-Key",
            state.api_key.as_deref().unwrap_or("missing-key"),
        )
        .header("Content-Type", "application/json")
        .send_json(json!({ "finding_id": params.finding_id }))
        .map_err(|e| anyhow::anyhow!("API request failed: {}", e))?;

    handle_api_response(response)
}

/// Generate a fix using the local BYOK AI backend.
///
/// 1. Loads findings from `.repotoire/last_findings.json`.
/// 2. Parses `finding_id` as a 1-based index into the findings array.
/// 3. Uses `FixGenerator::generate_fix()` to produce a `FixProposal`.
/// 4. Returns the fix with description, changes, and a unified diff.
fn handle_generate_fix_local(
    state: &HandlerState,
    params: &GenerateFixParams,
    backend: LlmBackend,
) -> Result<Value> {
    // Parse as 1-based index
    let index: usize = params.finding_id.parse().unwrap_or(0);
    if index == 0 {
        return Ok(json!({
            "error": "finding_id must be a number (1-based index from findings list)"
        }));
    }

    // Load findings
    let findings_path = state.repo_path.join(".repotoire/last_findings.json");
    if !findings_path.exists() {
        return Ok(json!({
            "error": "No findings available. Run 'analyze' first.",
            "hint": "Use the 'analyze' tool to generate findings, then call 'generate_fix' again."
        }));
    }

    let findings_json = std::fs::read_to_string(&findings_path)
        .context("Failed to read findings file")?;
    let parsed: Value = serde_json::from_str(&findings_json)
        .context("Failed to parse findings JSON")?;
    let findings: Vec<Finding> =
        serde_json::from_value(parsed.get("findings").cloned().unwrap_or(json!([])))?;

    if index > findings.len() {
        return Ok(json!({
            "error": format!(
                "Invalid finding index: {}. Valid range: 1-{}",
                index,
                findings.len()
            )
        }));
    }

    let finding = &findings[index - 1];

    // Generate fix using local AI
    let client = AiClient::from_env(backend)
        .map_err(|e| anyhow::anyhow!("Failed to initialize AI client: {}", e))?;
    let generator = FixGenerator::new(client);

    let file = finding
        .affected_files
        .first()
        .map(|p| p.display().to_string())
        .unwrap_or_default();

    match generator.generate_fix(finding, &state.repo_path) {
        Ok(fix) => Ok(json!({
            "finding": {
                "title": finding.title,
                "severity": format!("{}", finding.severity),
                "file": file,
                "line": finding.line_start
            },
            "fix": {
                "description": fix.description,
                "changes": fix.changes,
                "diff": fix.diff(&state.repo_path)
            }
        })),
        Err(e) => Ok(json!({
            "error": format!("Failed to generate fix: {}", e)
        })),
    }
}

// ─── Tests ───────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    /// Helper: create a HandlerState with no API key and no AI backend.
    fn free_state() -> (HandlerState, tempfile::TempDir) {
        let dir = tempdir().unwrap();
        let mut state = HandlerState::new(dir.path().to_path_buf());
        // Ensure no PRO or BYOK keys leak from the test environment
        state.api_key = None;
        state.ai_backend = None;
        (state, dir)
    }

    // ── PRO-gate tests ──────────────────────────────────────────────────────

    #[test]
    fn test_search_code_requires_pro() {
        let (state, _dir) = free_state();
        let params = SearchCodeParams {
            query: "authentication functions".to_string(),
            top_k: None,
            entity_types: None,
        };
        let result = handle_search_code(&state, &params).unwrap();

        let error = result.get("error").and_then(|v| v.as_str()).unwrap();
        assert!(error.contains("PRO subscription"), "Expected PRO error, got: {}", error);
        assert!(result.get("hint").is_some(), "Expected actionable hint");
        assert!(result.get("upgrade_url").is_some(), "Expected upgrade URL");
    }

    #[test]
    fn test_ask_requires_pro() {
        let (state, _dir) = free_state();
        let params = AskParams {
            question: "How does authentication work?".to_string(),
            top_k: None,
        };
        let result = handle_ask(&state, &params).unwrap();

        let error = result.get("error").and_then(|v| v.as_str()).unwrap();
        assert!(error.contains("PRO subscription"), "Expected PRO error, got: {}", error);
        assert!(result.get("hint").is_some(), "Expected actionable hint");
    }

    #[test]
    fn test_generate_fix_requires_ai_or_pro() {
        let (state, _dir) = free_state();
        let params = GenerateFixParams {
            finding_id: "1".to_string(),
        };
        let result = handle_generate_fix(&state, &params).unwrap();

        let error = result.get("error").and_then(|v| v.as_str()).unwrap();
        assert!(
            error.contains("API key"),
            "Expected API key requirement error, got: {}",
            error
        );
        assert!(result.get("hint").is_some(), "Expected actionable hint");
        assert!(result.get("docs").is_some(), "Expected documentation link");
    }

    // ── generate_fix_local edge cases ───────────────────────────────────────

    #[test]
    fn test_generate_fix_local_invalid_index() {
        let (mut state, dir) = free_state();
        state.ai_backend = Some(LlmBackend::Ollama);

        // Create findings file with one finding
        let repotoire_dir = dir.path().join(".repotoire");
        std::fs::create_dir_all(&repotoire_dir).unwrap();
        std::fs::write(
            repotoire_dir.join("last_findings.json"),
            r#"{"findings":[{"id":"f1","detector":"test","severity":"high","title":"Test issue","description":"desc","affected_files":["a.rs"]}]}"#,
        ).unwrap();

        // Index 0 is invalid (1-based)
        let params = GenerateFixParams {
            finding_id: "0".to_string(),
        };
        let result = handle_generate_fix(&state, &params).unwrap();
        let error = result.get("error").and_then(|v| v.as_str()).unwrap();
        assert!(error.contains("must be a number"), "Expected index error, got: {}", error);

        // Non-numeric is invalid
        let params = GenerateFixParams {
            finding_id: "abc".to_string(),
        };
        let result = handle_generate_fix(&state, &params).unwrap();
        let error = result.get("error").and_then(|v| v.as_str()).unwrap();
        assert!(error.contains("must be a number"), "Expected index error, got: {}", error);
    }

    #[test]
    fn test_generate_fix_local_index_out_of_range() {
        let (mut state, dir) = free_state();
        state.ai_backend = Some(LlmBackend::Ollama);

        let repotoire_dir = dir.path().join(".repotoire");
        std::fs::create_dir_all(&repotoire_dir).unwrap();
        std::fs::write(
            repotoire_dir.join("last_findings.json"),
            r#"{"findings":[{"id":"f1","detector":"test","severity":"high","title":"Test","description":"desc","affected_files":[]}]}"#,
        ).unwrap();

        let params = GenerateFixParams {
            finding_id: "99".to_string(),
        };
        let result = handle_generate_fix(&state, &params).unwrap();
        let error = result.get("error").and_then(|v| v.as_str()).unwrap();
        assert!(
            error.contains("Invalid finding index: 99"),
            "Expected out-of-range error, got: {}",
            error
        );
    }

    #[test]
    fn test_generate_fix_local_no_findings_file() {
        let (mut state, _dir) = free_state();
        state.ai_backend = Some(LlmBackend::Ollama);

        let params = GenerateFixParams {
            finding_id: "1".to_string(),
        };
        let result = handle_generate_fix(&state, &params).unwrap();
        let error = result.get("error").and_then(|v| v.as_str()).unwrap();
        assert!(
            error.contains("No findings available"),
            "Expected no-findings error, got: {}",
            error
        );
    }
}
