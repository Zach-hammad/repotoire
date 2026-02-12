//! AI-powered code fix generation
//!
//! This module provides AI-powered automatic code fixes with support for
//! multiple LLM backends (OpenAI, Anthropic). Uses BYOK (bring your own key)
//! model - read API keys from environment variables.
//!
//! # Environment Variables
//!
//! - `OPENAI_API_KEY`: Required for OpenAI backend
//! - `ANTHROPIC_API_KEY`: Required for Anthropic backend
//!
//! # Example
//!
//! ```rust,ignore
//! use repotoire::ai::{AiClient, LlmBackend, FixGenerator};
//!
//! let client = AiClient::from_env(LlmBackend::Anthropic)?;
//! let generator = FixGenerator::new(client);
//! let fix = generator.generate_fix(&finding, &repo_path).await?;
//! ```

mod client;
mod fix_generator;
mod prompts;

pub use client::{AiClient, AiConfig, LlmBackend, Message, Role};
pub use fix_generator::{CodeChange, FixConfidence, FixGenerator, FixProposal, FixType};
pub use prompts::{FixPromptBuilder, PromptTemplate};

use thiserror::Error;

/// Errors that can occur in the AI module
#[derive(Error, Debug)]
pub enum AiError {
    #[error("Missing API key: {env_var} not set. Get your key at {signup_url}")]
    MissingApiKey { env_var: String, signup_url: String },

    #[error("API request failed: {0}")]
    RequestFailed(#[from] reqwest::Error),

    #[error("API error: {status} - {message}")]
    ApiError { status: u16, message: String },

    #[error("Failed to parse API response: {0}")]
    ParseError(String),

    #[error("Invalid configuration: {0}")]
    ConfigError(String),

    #[error("IO error: {0}")]
    IoError(#[from] std::io::Error),
}

pub type AiResult<T> = Result<T, AiError>;
