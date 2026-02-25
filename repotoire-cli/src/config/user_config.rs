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
        if let Some(user_config) = Self::user_config_path()
            .filter(|p| p.exists())
            .and_then(|p| std::fs::read_to_string(&p).ok())
            .and_then(|content| toml::from_str::<UserConfig>(&content).ok())
        {
            config.merge(user_config);
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_config() {
        let config = UserConfig::default();
        assert!(!config.has_ai_key());
        assert_eq!(config.ai_backend(), "claude");
        assert!(!config.use_ollama());
        assert_eq!(config.ollama_url(), "http://localhost:11434");
        assert_eq!(config.ollama_model(), "codellama");
        assert!(config.anthropic_api_key().is_none());
    }

    #[test]
    fn test_load_returns_defaults_without_file() {
        let config = UserConfig::load().unwrap();
        // Should not crash even without a config file on disk
        assert!(!config.use_ollama());
    }

    #[test]
    fn test_toml_parsing_ollama_backend() {
        let toml_str = r#"
[ai]
anthropic_api_key = "sk-test-123"
backend = "ollama"
ollama_url = "http://localhost:11434"
ollama_model = "codellama"
"#;
        let config: UserConfig = toml::from_str(toml_str).unwrap();
        assert_eq!(config.ai.anthropic_api_key.as_deref(), Some("sk-test-123"));
        assert_eq!(config.ai.backend.as_deref(), Some("ollama"));
        assert_eq!(
            config.ai.ollama_url.as_deref(),
            Some("http://localhost:11434")
        );
        assert_eq!(config.ai.ollama_model.as_deref(), Some("codellama"));
        assert!(config.has_ai_key());
        assert!(config.use_ollama());
        assert_eq!(config.ai_backend(), "ollama");
    }

    #[test]
    fn test_toml_parsing_claude_backend() {
        let toml_str = r#"
[ai]
anthropic_api_key = "sk-ant-abc"
backend = "claude"
"#;
        let config: UserConfig = toml::from_str(toml_str).unwrap();
        assert!(config.has_ai_key());
        assert!(!config.use_ollama());
        assert_eq!(config.ai_backend(), "claude");
        assert_eq!(config.anthropic_api_key(), Some("sk-ant-abc"));
    }

    #[test]
    fn test_toml_parsing_minimal() {
        let toml_str = "";
        let config: UserConfig = toml::from_str(toml_str).unwrap();
        assert!(!config.has_ai_key());
        assert_eq!(config.ai_backend(), "claude");
    }

    #[test]
    fn test_invalid_toml_does_not_crash() {
        let bad_toml = "this is [[ not valid toml {{{}}}";
        let result = toml::from_str::<UserConfig>(bad_toml);
        assert!(result.is_err());
    }

    #[test]
    fn test_merge_overrides_set_fields() {
        let mut base = UserConfig::default();
        let other = UserConfig {
            ai: AiConfig {
                anthropic_api_key: Some("sk-new".to_string()),
                openai_api_key: Some("sk-openai".to_string()),
                model: Some("claude-3".to_string()),
                backend: Some("ollama".to_string()),
                ollama_url: Some("http://remote:11434".to_string()),
                ollama_model: Some("deepseek-coder".to_string()),
            },
        };
        base.merge(other);
        assert_eq!(base.anthropic_api_key(), Some("sk-new"));
        assert_eq!(base.ai.openai_api_key.as_deref(), Some("sk-openai"));
        assert_eq!(base.ai.model.as_deref(), Some("claude-3"));
        assert_eq!(base.ai_backend(), "ollama");
        assert_eq!(base.ollama_url(), "http://remote:11434");
        assert_eq!(base.ollama_model(), "deepseek-coder");
    }

    #[test]
    fn test_merge_preserves_base_when_other_is_none() {
        let mut base = UserConfig {
            ai: AiConfig {
                anthropic_api_key: Some("sk-original".to_string()),
                openai_api_key: None,
                model: None,
                backend: Some("claude".to_string()),
                ollama_url: None,
                ollama_model: None,
            },
        };
        let other = UserConfig::default();
        base.merge(other);
        assert_eq!(base.anthropic_api_key(), Some("sk-original"));
        assert_eq!(base.ai_backend(), "claude");
    }

    #[test]
    fn test_user_config_path_returns_some() {
        // On most systems, config_dir() should return a valid path
        let path = UserConfig::user_config_path();
        if let Some(p) = path {
            assert!(p.ends_with("repotoire/config.toml"));
        }
    }
}
