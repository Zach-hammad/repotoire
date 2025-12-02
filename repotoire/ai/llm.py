"""LLM client abstraction for RAG answer generation.

Supports:
- OpenAI: GPT-4o and other OpenAI models
- Anthropic: Claude Opus 4.5 and other Claude models

Environment variables:
- OPENAI_API_KEY: Required for 'openai' backend
- ANTHROPIC_API_KEY: Required for 'anthropic' backend
"""

import os
from typing import List, Optional, Literal
from dataclasses import dataclass

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Type alias for LLM backends
LLMBackend = Literal["openai", "anthropic"]

# Backend configurations with defaults
LLM_BACKEND_CONFIGS = {
    "openai": {
        "model": "gpt-4o",
        "env_key": "OPENAI_API_KEY",
        "models": {
            "gpt-4o": {"context": 128000, "description": "Latest GPT-4o (default)"},
            "gpt-4o-mini": {"context": 128000, "description": "Fast/cheap GPT-4o"},
            "gpt-4-turbo": {"context": 128000, "description": "Previous generation"},
        },
    },
    "anthropic": {
        "model": "claude-opus-4-20250514",
        "env_key": "ANTHROPIC_API_KEY",
        "models": {
            "claude-opus-4-20250514": {"context": 200000, "description": "Claude Opus 4.5 (best reasoning)"},
            "claude-sonnet-4-20250514": {"context": 200000, "description": "Claude Sonnet 4 (balanced)"},
            "claude-3-5-haiku-20241022": {"context": 200000, "description": "Claude Haiku 3.5 (fast/cheap)"},
        },
    },
}


@dataclass
class LLMConfig:
    """Configuration for LLM generation."""

    backend: LLMBackend = "openai"
    model: Optional[str] = None  # Uses backend default if not specified
    max_tokens: int = 4096
    temperature: float = 0.0  # Deterministic for code analysis

    def get_model(self) -> str:
        """Get the effective model name (user-specified or backend default)."""
        if self.model:
            return self.model
        return LLM_BACKEND_CONFIGS[self.backend]["model"]


class LLMClient:
    """Unified LLM client supporting OpenAI and Anthropic.

    Provides a consistent interface for generating responses from either
    OpenAI (GPT-4o) or Anthropic (Claude Opus 4.5) models.

    Example:
        >>> # OpenAI backend (default)
        >>> llm = LLMClient()
        >>> response = llm.generate([{"role": "user", "content": "Hello"}])

        >>> # Anthropic backend with Claude Opus 4.5
        >>> config = LLMConfig(backend="anthropic")
        >>> llm = LLMClient(config)
        >>> response = llm.generate(
        ...     [{"role": "user", "content": "Explain this code"}],
        ...     system="You are a code expert."
        ... )
    """

    def __init__(
        self,
        config: Optional[LLMConfig] = None,
        api_key: Optional[str] = None,
    ):
        """Initialize LLM client.

        Args:
            config: LLM configuration (uses defaults if not provided)
            api_key: API key (uses env var if not provided)
        """
        self.config = config or LLMConfig()
        self._api_key = api_key
        self.backend = self.config.backend
        self.model = self.config.get_model()
        self._client = self._init_client()

        logger.info(f"Initialized LLMClient with backend={self.backend}, model={self.model}")

    def _init_client(self):
        """Initialize the appropriate client based on backend."""
        backend_config = LLM_BACKEND_CONFIGS[self.backend]
        env_key = backend_config["env_key"]
        api_key = self._api_key or os.getenv(env_key)

        if not api_key:
            raise ValueError(
                f"{env_key} environment variable required for {self.backend} backend. "
                f"Get your API key at "
                f"{'https://platform.openai.com' if self.backend == 'openai' else 'https://console.anthropic.com'}"
            )

        if self.backend == "openai":
            from openai import OpenAI
            return OpenAI(api_key=api_key)
        elif self.backend == "anthropic":
            import anthropic
            return anthropic.Anthropic(api_key=api_key)

    def generate(
        self,
        messages: List[dict],
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """Generate a response from the LLM.

        Args:
            messages: List of message dicts with 'role' and 'content' keys
            system: Optional system prompt (handled differently by backends)
            max_tokens: Maximum tokens to generate (uses config default if not provided)
            temperature: Sampling temperature (uses config default if not provided)

        Returns:
            Generated text response
        """
        max_tokens = max_tokens or self.config.max_tokens
        temperature = temperature if temperature is not None else self.config.temperature

        if self.backend == "openai":
            return self._generate_openai(messages, system, max_tokens, temperature)
        elif self.backend == "anthropic":
            return self._generate_anthropic(messages, system, max_tokens, temperature)

    def _generate_openai(
        self,
        messages: List[dict],
        system: Optional[str],
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Generate response using OpenAI API.

        Args:
            messages: List of message dicts
            system: Optional system prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature

        Returns:
            Generated text response
        """
        # Prepend system message if provided
        if system:
            messages = [{"role": "system", "content": system}] + messages

        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        return response.choices[0].message.content

    def _generate_anthropic(
        self,
        messages: List[dict],
        system: Optional[str],
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Generate response using Anthropic API.

        Anthropic handles system prompts separately from messages.

        Args:
            messages: List of message dicts
            system: Optional system prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature

        Returns:
            Generated text response
        """
        # Anthropic passes system separately from messages
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
            "temperature": temperature,
        }

        if system:
            kwargs["system"] = system

        response = self._client.messages.create(**kwargs)

        return response.content[0].text

    async def agenerate(
        self,
        messages: List[dict],
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """Async version of generate (for future use).

        Currently wraps sync call - can be extended for native async support.
        """
        # For now, wrap sync call (can be extended for native async)
        return self.generate(messages, system, max_tokens, temperature)


def create_llm_client(
    backend: LLMBackend = "openai",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
) -> LLMClient:
    """Factory function to create a configured LLMClient.

    Args:
        backend: Backend to use ("openai" or "anthropic")
        model: Model name override (uses backend default if not provided)
        api_key: Optional API key (uses env var if not provided)

    Returns:
        Configured LLMClient instance
    """
    config = LLMConfig(backend=backend, model=model)
    return LLMClient(config=config, api_key=api_key)
