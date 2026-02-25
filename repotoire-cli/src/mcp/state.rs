//! Shared state for MCP tool handlers
//!
//! `HandlerState` holds the repository path, graph client, n-gram model,
//! API key, and AI backend configuration. It is shared across all tool
//! handlers and supports lazy initialization of expensive resources.

use anyhow::{Context, Result};
use std::path::PathBuf;
use std::sync::Arc;

use crate::ai::{AiClient, LlmBackend};
use crate::graph::GraphStore;

/// State shared across tool calls
pub struct HandlerState {
    /// Path to the repository being analyzed
    pub repo_path: PathBuf,
    /// Graph client (lazily initialized)
    graph: Option<Arc<GraphStore>>,
    /// N-gram language model for predictive coding (lazily initialized)
    ngram_model: Option<crate::calibrate::NgramModel>,
    /// API key for cloud PRO features
    pub api_key: Option<String>,
    /// API base URL
    pub api_url: String,
    /// BYOK: User's own AI backend
    pub ai_backend: Option<LlmBackend>,
}

impl HandlerState {
    pub fn new(repo_path: PathBuf, force_local: bool) -> Self {
        let api_key = if force_local {
            None
        } else {
            std::env::var("REPOTOIRE_API_KEY").ok()
        };
        let api_url = std::env::var("REPOTOIRE_API_URL")
            .unwrap_or_else(|_| "https://api.repotoire.io".to_string());

        // Check for BYOK keys (in order of preference)
        let ai_backend = if std::env::var("ANTHROPIC_API_KEY").is_ok() {
            Some(LlmBackend::Anthropic)
        } else if std::env::var("OPENAI_API_KEY").is_ok() {
            Some(LlmBackend::OpenAi)
        } else if std::env::var("DEEPINFRA_API_KEY").is_ok() {
            Some(LlmBackend::Deepinfra)
        } else if std::env::var("OPENROUTER_API_KEY").is_ok() {
            Some(LlmBackend::OpenRouter)
        } else if AiClient::ollama_available() {
            Some(LlmBackend::Ollama)
        } else {
            None
        };

        Self {
            repo_path,
            graph: None,
            ngram_model: None,
            api_key,
            api_url,
            ai_backend,
        }
    }

    /// Build or return the cached n-gram language model for predictive coding
    pub fn ngram_model(&mut self) -> Option<crate::calibrate::NgramModel> {
        if self.ngram_model.is_none() {
            if let Some(model) = build_ngram_model_from_repo(&self.repo_path) {
                tracing::info!("MCP: Learned coding patterns ({} tokens, {} vocabulary)",
                    model.total_tokens(), model.vocab_size());
                self.ngram_model = Some(model);
            }
        }
        self.ngram_model.clone()
    }

    pub fn is_pro(&self) -> bool {
        self.api_key.is_some()
    }

    /// Check if user has BYOK AI keys
    pub fn has_ai(&self) -> bool {
        self.ai_backend.is_some()
    }

    /// Get mode description
    #[allow(dead_code)] // Used in tests
    pub fn mode_description(&self) -> &'static str {
        if self.is_pro() {
            "PRO (cloud)"
        } else if self.has_ai() {
            "BYOK (local AI)"
        } else {
            "FREE"
        }
    }

    /// Initialize or return the cached graph client
    pub fn graph(&mut self) -> Result<Arc<GraphStore>> {
        if let Some(ref client) = self.graph {
            return Ok(Arc::clone(client));
        }

        let db_path = self.repo_path.join(".repotoire").join("graph");
        let client = GraphStore::new(&db_path).context("Failed to initialize graph database")?;
        let client = Arc::new(client);
        self.graph = Some(Arc::clone(&client));
        Ok(client)
    }

    /// Inject a pre-built graph store (used by tests and embedding scenarios).
    #[allow(dead_code)] // Called from MCP tool handlers and tests
    pub fn set_graph(&mut self, graph: Arc<GraphStore>) {
        self.graph = Some(graph);
    }
}

/// Build an n-gram model by scanning source files in the repo
fn build_ngram_model_from_repo(repo_path: &std::path::Path) -> Option<crate::calibrate::NgramModel> {
    let mut model = crate::calibrate::NgramModel::new();
    let walker = ignore::WalkBuilder::new(repo_path)
        .hidden(false)
        .git_ignore(true)
        .build();
    for entry in walker.filter_map(|e| e.ok()) {
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
        if !matches!(ext, "rs" | "py" | "ts" | "tsx" | "js" | "jsx" | "go" | "java"
            | "c" | "cpp" | "cc" | "h" | "hpp" | "cs" | "kt")
        {
            continue;
        }
        let path_lower = path.to_string_lossy().to_lowercase();
        if path_lower.contains("/test") || path_lower.contains("/vendor")
            || path_lower.contains("/node_modules") || path_lower.contains("/generated")
        {
            continue;
        }
        let Ok(content) = std::fs::read_to_string(path) else { continue };
        let tokens = crate::calibrate::NgramModel::tokenize_file(&content);
        model.train_on_tokens(&tokens);
    }
    model.is_confident().then_some(model)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_handler_state_new() {
        let dir = tempdir().unwrap();
        let state = HandlerState::new(dir.path().to_path_buf(), false);
        assert!(!state.is_pro()); // No API key in test env
    }

    #[test]
    fn test_handler_state_force_local() {
        let dir = tempdir().unwrap();
        // Even if REPOTOIRE_API_KEY is set in env, force_local should suppress it
        let state = HandlerState::new(dir.path().to_path_buf(), true);
        assert!(state.api_key.is_none());
        assert!(!state.is_pro());
    }

    #[test]
    fn test_mode_description_free() {
        let dir = tempdir().unwrap();
        let mut state = HandlerState::new(dir.path().to_path_buf(), true);
        state.ai_backend = None;
        assert_eq!(state.mode_description(), "FREE");
    }
}
