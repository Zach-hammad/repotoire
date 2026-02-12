//! User-level configuration for repotoire
//!
//! Supports loading config from:
//! - Environment variables
//! - ~/.config/repotoire/config.toml

use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Default, Deserialize, Serialize)]
pub struct UserConfig {
    #[serde(default)]
    pub ai: AiConfig,
}

#[derive(Debug, Default, Deserialize, Serialize)]
pub struct AiConfig {
    /// Anthropic API key for Claude Agent SDK
    pub anthropic_api_key: Option<String>,

    /// OpenAI API key (for embeddings/alternative models)
    pub openai_api_key: Option<String>,

    /// Default model to use
    pub model: Option<String>,

    /// AI backend: "claude" (default), "ollama"
    pub backend: Option<String>,

    /// Ollama URL (default: http://localhost:11434)
    pub ollama_url: Option<String>,

    /// Ollama model (default: codellama)
    pub ollama_model: Option<String>,
}

impl UserConfig {
    /// Load config from all sources, with priority:
    /// 1. Environment variables (highest)
    /// 2. User config (~/.config/repotoire/config.toml)
    pub fn load() -> Result<Self> {
        let mut config = UserConfig::default();

        // Load user config
        if let Some(user_config_path) = Self::user_config_path() {
            if user_config_path.exists() {
                if let Ok(content) = std::fs::read_to_string(&user_config_path) {
                    if let Ok(user_config) = toml::from_str::<UserConfig>(&content) {
                        config.merge(user_config);
                    }
                }
            }
        }

        // Environment variables override everything
        if let Ok(key) = std::env::var("ANTHROPIC_API_KEY") {
            config.ai.anthropic_api_key = Some(key);
        }
        if let Ok(key) = std::env::var("OPENAI_API_KEY") {
            config.ai.openai_api_key = Some(key);
        }

        Ok(config)
    }

    /// Get the user config directory path
    pub fn user_config_path() -> Option<PathBuf> {
        dirs::config_dir().map(|p| p.join("repotoire").join("config.toml"))
    }

    /// Merge another config into this one (other takes priority)
    fn merge(&mut self, other: UserConfig) {
        if other.ai.anthropic_api_key.is_some() {
            self.ai.anthropic_api_key = other.ai.anthropic_api_key;
        }
        if other.ai.openai_api_key.is_some() {
            self.ai.openai_api_key = other.ai.openai_api_key;
        }
        if other.ai.model.is_some() {
            self.ai.model = other.ai.model;
        }
        if other.ai.backend.is_some() {
            self.ai.backend = other.ai.backend;
        }
        if other.ai.ollama_url.is_some() {
            self.ai.ollama_url = other.ai.ollama_url;
        }
        if other.ai.ollama_model.is_some() {
            self.ai.ollama_model = other.ai.ollama_model;
        }
    }

    /// Get the Anthropic API key, if configured
    pub fn anthropic_api_key(&self) -> Option<&str> {
        self.ai.anthropic_api_key.as_deref()
    }

    /// Check if AI features are available
    pub fn has_ai_key(&self) -> bool {
        self.ai.anthropic_api_key.is_some()
    }

    /// Get the AI backend (claude or ollama)
    pub fn ai_backend(&self) -> &str {
        self.ai.backend.as_deref().unwrap_or("claude")
    }

    /// Check if Ollama backend is configured
    pub fn use_ollama(&self) -> bool {
        self.ai.backend.as_deref() == Some("ollama")
    }

    /// Get Ollama URL
    pub fn ollama_url(&self) -> &str {
        self.ai
            .ollama_url
            .as_deref()
            .unwrap_or("http://localhost:11434")
    }

    /// Get Ollama model
    pub fn ollama_model(&self) -> &str {
        self.ai.ollama_model.as_deref().unwrap_or("codellama")
    }

    /// Initialize user config directory and create example config
    pub fn init_user_config() -> Result<PathBuf> {
        let config_path = Self::user_config_path()
            .ok_or_else(|| anyhow::anyhow!("Could not determine config directory"))?;

        if let Some(parent) = config_path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        if !config_path.exists() {
            let example = r#"# Repotoire User Configuration

[ai]
# Backend: "claude" (needs API key) or "ollama" (free, local)
# backend = "claude"

# For Claude backend - get key from: https://console.anthropic.com/
# anthropic_api_key = "sk-ant-..."

# For Ollama backend (free, runs locally)
# ollama_url = "http://localhost:11434"
# ollama_model = "codellama"  # or "deepseek-coder", "qwen2.5-coder", etc.

# Optional: for embeddings
# openai_api_key = "sk-..."
"#;
            std::fs::write(&config_path, example)?;
        }

        Ok(config_path)
    }
}
