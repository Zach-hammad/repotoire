//! LLM API client supporting OpenAI and Anthropic backends
//!
//! Provides a unified interface for making API calls to different LLM providers.

use crate::ai::{AiError, AiResult};
use reqwest::header::{HeaderMap, HeaderValue, AUTHORIZATION, CONTENT_TYPE};
use serde::{Deserialize, Serialize};
use std::env;

/// Supported LLM backends
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum LlmBackend {
    #[default]
    Anthropic,
    OpenAi,
    Deepinfra,
    OpenRouter,
    Ollama,
}

impl LlmBackend {
    /// Get the environment variable name for the API key
    pub fn env_key(&self) -> &'static str {
        match self {
            LlmBackend::Anthropic => "ANTHROPIC_API_KEY",
            LlmBackend::OpenAi => "OPENAI_API_KEY",
            LlmBackend::Deepinfra => "DEEPINFRA_API_KEY",
            LlmBackend::OpenRouter => "OPENROUTER_API_KEY",
            LlmBackend::Ollama => "OLLAMA_MODEL", // Not a key, just model name
        }
    }

    /// Get the signup URL for the API key
    pub fn signup_url(&self) -> &'static str {
        match self {
            LlmBackend::Anthropic => "https://console.anthropic.com/settings/keys",
            LlmBackend::OpenAi => "https://platform.openai.com/api-keys",
            LlmBackend::Deepinfra => "https://deepinfra.com/dash/api_keys",
            LlmBackend::OpenRouter => "https://openrouter.ai/keys",
            LlmBackend::Ollama => "https://ollama.ai (no key needed, just run locally)",
        }
    }

    /// Get the default model for this backend
    pub fn default_model(&self) -> &'static str {
        match self {
            LlmBackend::Anthropic => "claude-sonnet-4-20250514",
            LlmBackend::OpenAi => "gpt-4o",
            LlmBackend::Deepinfra => "meta-llama/Llama-3.3-70B-Instruct",
            LlmBackend::OpenRouter => "anthropic/claude-sonnet-4",
            LlmBackend::Ollama => "llama3.3:70b",
        }
    }

    /// Get the API base URL
    pub fn api_url(&self) -> &'static str {
        match self {
            LlmBackend::Anthropic => "https://api.anthropic.com/v1/messages",
            LlmBackend::OpenAi => "https://api.openai.com/v1/chat/completions",
            LlmBackend::Deepinfra => "https://api.deepinfra.com/v1/openai/chat/completions",
            LlmBackend::OpenRouter => "https://openrouter.ai/api/v1/chat/completions",
            LlmBackend::Ollama => "http://localhost:11434/v1/chat/completions",
        }
    }

    /// Check if this backend uses OpenAI-compatible API format
    pub fn is_openai_compatible(&self) -> bool {
        matches!(self, LlmBackend::OpenAi | LlmBackend::Deepinfra | LlmBackend::OpenRouter | LlmBackend::Ollama)
    }
    
    /// Check if this backend requires an API key
    pub fn requires_api_key(&self) -> bool {
        !matches!(self, LlmBackend::Ollama)
    }
}

/// Message role in a conversation
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    System,
    User,
    Assistant,
}

/// A message in the conversation
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    pub role: Role,
    pub content: String,
}

impl Message {
    pub fn user(content: impl Into<String>) -> Self {
        Self {
            role: Role::User,
            content: content.into(),
        }
    }

    pub fn assistant(content: impl Into<String>) -> Self {
        Self {
            role: Role::Assistant,
            content: content.into(),
        }
    }

    pub fn system(content: impl Into<String>) -> Self {
        Self {
            role: Role::System,
            content: content.into(),
        }
    }
}

/// Configuration for the AI client
#[derive(Debug, Clone)]
pub struct AiConfig {
    pub backend: LlmBackend,
    pub model: Option<String>,
    pub max_tokens: u32,
    pub temperature: f32,
}

impl Default for AiConfig {
    fn default() -> Self {
        Self {
            backend: LlmBackend::default(),
            model: None,
            max_tokens: 4096,
            temperature: 0.2, // Low temperature for consistent code generation
        }
    }
}

impl AiConfig {
    /// Get the effective model name
    pub fn model(&self) -> &str {
        self.model
            .as_deref()
            .unwrap_or_else(|| self.backend.default_model())
    }
}

/// Unified LLM client supporting OpenAI and Anthropic
pub struct AiClient {
    config: AiConfig,
    api_key: String,
    http: reqwest::Client,
}

impl AiClient {
    /// Create a new client with explicit API key
    pub fn new(config: AiConfig, api_key: impl Into<String>) -> Self {
        Self {
            config,
            api_key: api_key.into(),
            http: reqwest::Client::new(),
        }
    }

    /// Create a client from environment variables
    pub fn from_env(backend: LlmBackend) -> AiResult<Self> {
        let config = AiConfig {
            backend,
            ..Default::default()
        };
        Self::from_env_with_config(config)
    }

    /// Create a client from environment with custom config
    pub fn from_env_with_config(mut config: AiConfig) -> AiResult<Self> {
        // Ollama doesn't require an API key
        if !config.backend.requires_api_key() {
            // Check if OLLAMA_MODEL is set to override default
            if let Ok(model) = env::var("OLLAMA_MODEL") {
                config.model = Some(model);
            }
            return Ok(Self::new(config, "ollama"));
        }
        
        let env_key = config.backend.env_key();
        let api_key = env::var(env_key).map_err(|_| AiError::MissingApiKey {
            env_var: env_key.to_string(),
            signup_url: config.backend.signup_url().to_string(),
        })?;

        Ok(Self::new(config, api_key))
    }
    
    /// Check if Ollama is available locally and has a model we can use
    pub fn ollama_available() -> bool {
        // Check if Ollama is running
        if std::net::TcpStream::connect("127.0.0.1:11434").is_err() {
            return false;
        }
        
        // Try to list models - this validates Ollama is responding
        match std::process::Command::new("ollama")
            .args(["list"])
            .output() 
        {
            Ok(output) => {
                let stdout = String::from_utf8_lossy(&output.stdout);
                // Check if any model is available
                stdout.lines().skip(1).next().is_some()
            }
            Err(_) => false
        }
    }
    
    /// Get a message about available Ollama models
    pub fn ollama_models() -> Option<String> {
        std::process::Command::new("ollama")
            .args(["list"])
            .output()
            .ok()
            .map(|o| String::from_utf8_lossy(&o.stdout).to_string())
    }

    /// Get the backend being used
    pub fn backend(&self) -> LlmBackend {
        self.config.backend
    }

    /// Get the model being used
    pub fn model(&self) -> &str {
        self.config.model()
    }

    /// Generate a response from the LLM
    pub async fn generate(
        &self,
        messages: Vec<Message>,
        system: Option<&str>,
    ) -> AiResult<String> {
        if self.config.backend.is_openai_compatible() {
            self.generate_openai(messages, system).await
        } else {
            self.generate_anthropic(messages, system).await
        }
    }

    /// Generate response using OpenAI API
    async fn generate_openai(
        &self,
        mut messages: Vec<Message>,
        system: Option<&str>,
    ) -> AiResult<String> {
        // Prepend system message if provided
        if let Some(sys) = system {
            messages.insert(0, Message::system(sys));
        }

        let body = OpenAiRequest {
            model: self.config.model().to_string(),
            messages,
            max_tokens: self.config.max_tokens,
            temperature: self.config.temperature,
        };

        let mut headers = HeaderMap::new();
        headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
        headers.insert(
            AUTHORIZATION,
            HeaderValue::from_str(&format!("Bearer {}", self.api_key))
                .map_err(|_| AiError::ConfigError("Invalid API key format".to_string()))?,
        );

        let response = self
            .http
            .post(self.config.backend.api_url())
            .headers(headers)
            .json(&body)
            .send()
            .await?;

        let status = response.status();
        if !status.is_success() {
            let error_text = response.text().await.unwrap_or_default();
            return Err(AiError::ApiError {
                status: status.as_u16(),
                message: error_text,
            });
        }

        let resp: OpenAiResponse = response
            .json()
            .await
            .map_err(|e| AiError::ParseError(e.to_string()))?;

        resp.choices
            .into_iter()
            .next()
            .map(|c| c.message.content)
            .ok_or_else(|| AiError::ParseError("No response choices".to_string()))
    }

    /// Generate response using Anthropic API
    async fn generate_anthropic(
        &self,
        messages: Vec<Message>,
        system: Option<&str>,
    ) -> AiResult<String> {
        // Filter out system messages (Anthropic handles system separately)
        let messages: Vec<_> = messages
            .into_iter()
            .filter(|m| m.role != Role::System)
            .collect();

        let body = AnthropicRequest {
            model: self.config.model().to_string(),
            max_tokens: self.config.max_tokens,
            messages,
            system: system.map(|s| s.to_string()),
            temperature: Some(self.config.temperature),
        };

        let mut headers = HeaderMap::new();
        headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
        headers.insert(
            "x-api-key",
            HeaderValue::from_str(&self.api_key)
                .map_err(|_| AiError::ConfigError("Invalid API key format".to_string()))?,
        );
        headers.insert(
            "anthropic-version",
            HeaderValue::from_static("2023-06-01"),
        );

        let response = self
            .http
            .post(self.config.backend.api_url())
            .headers(headers)
            .json(&body)
            .send()
            .await?;

        let status = response.status();
        if !status.is_success() {
            let error_text = response.text().await.unwrap_or_default();
            return Err(AiError::ApiError {
                status: status.as_u16(),
                message: error_text,
            });
        }

        let resp: AnthropicResponse = response
            .json()
            .await
            .map_err(|e| AiError::ParseError(e.to_string()))?;

        resp.content
            .into_iter()
            .find(|c| c.content_type == "text")
            .map(|c| c.text)
            .ok_or_else(|| AiError::ParseError("No text content in response".to_string()))
    }
}

// OpenAI API types
#[derive(Serialize)]
struct OpenAiRequest {
    model: String,
    messages: Vec<Message>,
    max_tokens: u32,
    temperature: f32,
}

#[derive(Deserialize)]
struct OpenAiResponse {
    choices: Vec<OpenAiChoice>,
}

#[derive(Deserialize)]
struct OpenAiChoice {
    message: OpenAiMessage,
}

#[derive(Deserialize)]
struct OpenAiMessage {
    content: String,
}

// Anthropic API types
#[derive(Serialize)]
struct AnthropicRequest {
    model: String,
    max_tokens: u32,
    messages: Vec<Message>,
    #[serde(skip_serializing_if = "Option::is_none")]
    system: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    temperature: Option<f32>,
}

#[derive(Deserialize)]
struct AnthropicResponse {
    content: Vec<AnthropicContent>,
}

#[derive(Deserialize)]
struct AnthropicContent {
    #[serde(rename = "type")]
    content_type: String,
    text: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_backend_defaults() {
        assert_eq!(LlmBackend::OpenAi.default_model(), "gpt-4o");
        assert_eq!(LlmBackend::Anthropic.default_model(), "claude-sonnet-4-20250514");
    }

    #[test]
    fn test_config_model() {
        let config = AiConfig::default();
        assert_eq!(config.model(), "claude-sonnet-4-20250514");

        let config = AiConfig {
            model: Some("custom-model".to_string()),
            ..Default::default()
        };
        assert_eq!(config.model(), "custom-model");
    }
}
