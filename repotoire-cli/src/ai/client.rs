//! LLM API client supporting OpenAI and Anthropic backends
//!
//! Provides a unified interface for making API calls to different LLM providers.
//! Uses ureq (sync HTTP) — no async runtime needed.

use crate::ai::{AiError, AiResult};
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
    pub fn env_key(&self) -> &'static str {
        match self {
            LlmBackend::Anthropic => "ANTHROPIC_API_KEY",
            LlmBackend::OpenAi => "OPENAI_API_KEY",
            LlmBackend::Deepinfra => "DEEPINFRA_API_KEY",
            LlmBackend::OpenRouter => "OPENROUTER_API_KEY",
            LlmBackend::Ollama => "OLLAMA_MODEL",
        }
    }

    pub fn signup_url(&self) -> &'static str {
        match self {
            LlmBackend::Anthropic => "https://console.anthropic.com/settings/keys",
            LlmBackend::OpenAi => "https://platform.openai.com/api-keys",
            LlmBackend::Deepinfra => "https://deepinfra.com/dash/api_keys",
            LlmBackend::OpenRouter => "https://openrouter.ai/keys",
            LlmBackend::Ollama => "https://ollama.ai (no key needed, just run locally)",
        }
    }

    pub fn default_model(&self) -> &'static str {
        match self {
            LlmBackend::Anthropic => "claude-sonnet-4-20250514",
            LlmBackend::OpenAi => "gpt-4o",
            LlmBackend::Deepinfra => "meta-llama/Llama-3.3-70B-Instruct",
            LlmBackend::OpenRouter => "anthropic/claude-sonnet-4",
            LlmBackend::Ollama => "deepseek-coder:6.7b",
        }
    }

    pub fn api_url(&self) -> &'static str {
        match self {
            LlmBackend::Anthropic => "https://api.anthropic.com/v1/messages",
            LlmBackend::OpenAi => "https://api.openai.com/v1/chat/completions",
            LlmBackend::Deepinfra => "https://api.deepinfra.com/v1/openai/chat/completions",
            LlmBackend::OpenRouter => "https://openrouter.ai/api/v1/chat/completions",
            LlmBackend::Ollama => "http://localhost:11434/v1/chat/completions",
        }
    }

    pub fn is_openai_compatible(&self) -> bool {
        matches!(
            self,
            LlmBackend::OpenAi
                | LlmBackend::Deepinfra
                | LlmBackend::OpenRouter
                | LlmBackend::Ollama
        )
    }

    pub fn requires_api_key(&self) -> bool {
        !matches!(self, LlmBackend::Ollama)
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    System,
    User,
    Assistant,
}

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
            temperature: 0.2,
        }
    }
}

impl AiConfig {
    pub fn model(&self) -> &str {
        self.model
            .as_deref()
            .unwrap_or_else(|| self.backend.default_model())
    }
}

/// Unified LLM client — sync HTTP via ureq (no tokio needed)
pub struct AiClient {
    config: AiConfig,
    api_key: String,
    agent: ureq::Agent,
}

fn make_agent() -> ureq::Agent {
    ureq::config::Config::builder()
        .http_status_as_error(false) // We handle status codes ourselves (parity with reqwest)
        .timeout_global(Some(std::time::Duration::from_secs(120))) // LLM calls can be slow
        .build()
        .new_agent()
}

impl AiClient {
    pub fn new(config: AiConfig, api_key: impl Into<String>) -> Self {
        Self {
            config,
            api_key: api_key.into(),
            agent: make_agent(),
        }
    }

    pub fn from_env(backend: LlmBackend) -> AiResult<Self> {
        let config = AiConfig {
            backend,
            ..Default::default()
        };
        Self::from_env_with_config(config)
    }

    pub fn from_env_with_config(mut config: AiConfig) -> AiResult<Self> {
        if !config.backend.requires_api_key() {
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

    pub fn ollama_available() -> bool {
        if std::net::TcpStream::connect("127.0.0.1:11434").is_err() {
            return false;
        }
        match std::process::Command::new("ollama").args(["list"]).output() {
            Ok(output) => {
                let stdout = String::from_utf8_lossy(&output.stdout);
                stdout.lines().nth(1).is_some()
            }
            Err(_) => false,
        }
    }

    pub fn ollama_models() -> Option<String> {
        std::process::Command::new("ollama")
            .args(["list"])
            .output()
            .ok()
            .map(|o| String::from_utf8_lossy(&o.stdout).to_string())
    }

    pub fn backend(&self) -> LlmBackend {
        self.config.backend
    }

    pub fn model(&self) -> &str {
        self.config.model()
    }

    /// Generate a response (sync)
    pub fn generate(&self, messages: Vec<Message>, system: Option<&str>) -> AiResult<String> {
        if self.config.backend.is_openai_compatible() {
            self.generate_openai(messages, system)
        } else {
            self.generate_anthropic(messages, system)
        }
    }

    fn generate_openai(
        &self,
        mut messages: Vec<Message>,
        system: Option<&str>,
    ) -> AiResult<String> {
        if let Some(sys) = system {
            messages.insert(0, Message::system(sys));
        }

        let body = OpenAiRequest {
            model: self.config.model().to_string(),
            messages,
            max_tokens: self.config.max_tokens,
            temperature: self.config.temperature,
        };

        let mut req = self
            .agent
            .post(self.config.backend.api_url())
            .header("Content-Type", "application/json");

        if self.config.backend.requires_api_key() {
            req = req.header("Authorization", &format!("Bearer {}", self.api_key));
        }

        let response = req.send_json(&body).map_err(|e| AiError::ApiError {
            status: 0,
            message: e.to_string(),
        })?;

        let status = response.status().as_u16();
        if status >= 400 {
            let error_text = response.into_body().read_to_string().unwrap_or_default();
            return Err(AiError::ApiError {
                status,
                message: error_text,
            });
        }

        let resp: OpenAiResponse = response
            .into_body()
            .read_json()
            .map_err(|e| AiError::ParseError(e.to_string()))?;

        resp.choices
            .into_iter()
            .next()
            .map(|c| c.message.content)
            .ok_or_else(|| AiError::ParseError("No response choices".to_string()))
    }

    fn generate_anthropic(&self, messages: Vec<Message>, system: Option<&str>) -> AiResult<String> {
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

        let response = self
            .agent
            .post(self.config.backend.api_url())
            .header("Content-Type", "application/json")
            .header("x-api-key", &self.api_key)
            .header("anthropic-version", "2023-06-01")
            .send_json(&body)
            .map_err(|e| AiError::ApiError {
                status: 0,
                message: e.to_string(),
            })?;

        let status = response.status().as_u16();
        if status >= 400 {
            let error_text = response.into_body().read_to_string().unwrap_or_default();
            return Err(AiError::ApiError {
                status,
                message: error_text,
            });
        }

        let resp: AnthropicResponse = response
            .into_body()
            .read_json()
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
        assert_eq!(
            LlmBackend::Anthropic.default_model(),
            "claude-sonnet-4-20250514"
        );
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
