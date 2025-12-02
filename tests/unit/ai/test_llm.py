"""Unit tests for LLM client abstraction (REPO-240)."""

from unittest.mock import Mock, patch, MagicMock
import pytest
import os

from repotoire.ai.llm import LLMClient, LLMConfig, LLM_BACKEND_CONFIGS, create_llm_client


class TestLLMConfig:
    """Test LLMConfig dataclass."""

    def test_default_config(self):
        """Test default configuration uses OpenAI."""
        config = LLMConfig()

        assert config.backend == "openai"
        assert config.model is None
        assert config.max_tokens == 4096
        assert config.temperature == 0.0

    def test_get_model_openai_default(self):
        """Test OpenAI default model is gpt-4o."""
        config = LLMConfig(backend="openai")

        assert config.get_model() == "gpt-4o"

    def test_get_model_anthropic_default(self):
        """Test Anthropic default model is Claude Opus 4.5."""
        config = LLMConfig(backend="anthropic")

        assert config.get_model() == "claude-opus-4-20250514"

    def test_get_model_with_override(self):
        """Test model override takes precedence."""
        config = LLMConfig(backend="openai", model="gpt-4-turbo")

        assert config.get_model() == "gpt-4-turbo"

    def test_custom_temperature(self):
        """Test custom temperature setting."""
        config = LLMConfig(temperature=0.7)

        assert config.temperature == 0.7


class TestLLMBackendConfigs:
    """Test LLM backend configuration."""

    def test_openai_backend_config(self):
        """Test OpenAI backend config has correct values."""
        openai_config = LLM_BACKEND_CONFIGS["openai"]

        assert openai_config["model"] == "gpt-4o"
        assert openai_config["env_key"] == "OPENAI_API_KEY"
        assert "gpt-4o" in openai_config["models"]
        assert "gpt-4o-mini" in openai_config["models"]

    def test_anthropic_backend_config(self):
        """Test Anthropic backend config has correct values."""
        anthropic_config = LLM_BACKEND_CONFIGS["anthropic"]

        assert anthropic_config["model"] == "claude-opus-4-20250514"
        assert anthropic_config["env_key"] == "ANTHROPIC_API_KEY"
        assert "claude-opus-4-20250514" in anthropic_config["models"]
        assert "claude-sonnet-4-20250514" in anthropic_config["models"]
        assert "claude-3-5-haiku-20241022" in anthropic_config["models"]


class TestLLMClientOpenAI:
    """Test LLMClient with OpenAI backend."""

    def test_openai_backend_requires_api_key(self):
        """Test OpenAI backend raises error without API key."""
        env_backup = os.environ.pop("OPENAI_API_KEY", None)

        try:
            with pytest.raises(ValueError, match="OPENAI_API_KEY"):
                LLMClient(LLMConfig(backend="openai"))
        finally:
            if env_backup:
                os.environ["OPENAI_API_KEY"] = env_backup

    def test_openai_client_initialization(self):
        """Test OpenAI client initializes correctly."""
        with patch('openai.OpenAI') as mock_openai:
            mock_openai.return_value = MagicMock()

            client = LLMClient(LLMConfig(backend="openai"), api_key="test-key")

            assert client.backend == "openai"
            assert client.model == "gpt-4o"
            mock_openai.assert_called_once_with(api_key="test-key")

    def test_openai_generate_with_system_prompt(self):
        """Test OpenAI generation with system prompt."""
        with patch('openai.OpenAI') as mock_openai:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "Generated response"
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.return_value = mock_client

            llm = LLMClient(LLMConfig(backend="openai"), api_key="test-key")
            response = llm.generate(
                [{"role": "user", "content": "Hello"}],
                system="You are a helpful assistant"
            )

            assert response == "Generated response"
            mock_client.chat.completions.create.assert_called_once()

            # Check system message was prepended
            call_args = mock_client.chat.completions.create.call_args
            messages = call_args.kwargs["messages"]
            assert messages[0]["role"] == "system"
            assert messages[0]["content"] == "You are a helpful assistant"
            assert messages[1]["role"] == "user"

    def test_openai_generate_without_system_prompt(self):
        """Test OpenAI generation without system prompt."""
        with patch('openai.OpenAI') as mock_openai:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "Generated response"
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.return_value = mock_client

            llm = LLMClient(LLMConfig(backend="openai"), api_key="test-key")
            response = llm.generate([{"role": "user", "content": "Hello"}])

            assert response == "Generated response"

            # Check no system message was added
            call_args = mock_client.chat.completions.create.call_args
            messages = call_args.kwargs["messages"]
            assert len(messages) == 1
            assert messages[0]["role"] == "user"

    def test_openai_respects_max_tokens(self):
        """Test OpenAI respects max_tokens parameter."""
        with patch('openai.OpenAI') as mock_openai:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "Response"
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.return_value = mock_client

            llm = LLMClient(LLMConfig(backend="openai", max_tokens=1000), api_key="test-key")
            llm.generate([{"role": "user", "content": "Hello"}])

            call_args = mock_client.chat.completions.create.call_args
            assert call_args.kwargs["max_tokens"] == 1000


class TestLLMClientAnthropic:
    """Test LLMClient with Anthropic backend."""

    def test_anthropic_backend_requires_api_key(self):
        """Test Anthropic backend raises error without API key."""
        env_backup = os.environ.pop("ANTHROPIC_API_KEY", None)

        try:
            with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
                LLMClient(LLMConfig(backend="anthropic"))
        finally:
            if env_backup:
                os.environ["ANTHROPIC_API_KEY"] = env_backup

    def test_anthropic_client_initialization(self):
        """Test Anthropic client initializes correctly."""
        import sys

        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = MagicMock()

        with patch.dict(sys.modules, {'anthropic': mock_anthropic}):
            client = LLMClient(LLMConfig(backend="anthropic"), api_key="test-key")

            assert client.backend == "anthropic"
            assert client.model == "claude-opus-4-20250514"
            mock_anthropic.Anthropic.assert_called_once_with(api_key="test-key")

    def test_anthropic_generate_with_system_prompt(self):
        """Test Anthropic generation passes system separately."""
        import sys

        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = "Claude response"
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.Anthropic.return_value = mock_client

        with patch.dict(sys.modules, {'anthropic': mock_anthropic}):
            llm = LLMClient(LLMConfig(backend="anthropic"), api_key="test-key")
            response = llm.generate(
                [{"role": "user", "content": "Hello"}],
                system="You are a code expert"
            )

            assert response == "Claude response"

            # Check system was passed separately (not in messages)
            call_args = mock_client.messages.create.call_args
            assert call_args.kwargs["system"] == "You are a code expert"
            messages = call_args.kwargs["messages"]
            assert len(messages) == 1
            assert messages[0]["role"] == "user"

    def test_anthropic_generate_without_system_prompt(self):
        """Test Anthropic generation without system prompt."""
        import sys

        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = "Claude response"
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.Anthropic.return_value = mock_client

        with patch.dict(sys.modules, {'anthropic': mock_anthropic}):
            llm = LLMClient(LLMConfig(backend="anthropic"), api_key="test-key")
            response = llm.generate([{"role": "user", "content": "Hello"}])

            assert response == "Claude response"

            # Check no system key when not provided
            call_args = mock_client.messages.create.call_args
            assert "system" not in call_args.kwargs

    def test_anthropic_respects_model_override(self):
        """Test Anthropic respects model override."""
        import sys

        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = "Response"
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.Anthropic.return_value = mock_client

        with patch.dict(sys.modules, {'anthropic': mock_anthropic}):
            config = LLMConfig(backend="anthropic", model="claude-3-5-haiku-20241022")
            llm = LLMClient(config, api_key="test-key")
            llm.generate([{"role": "user", "content": "Hello"}])

            call_args = mock_client.messages.create.call_args
            assert call_args.kwargs["model"] == "claude-3-5-haiku-20241022"


class TestCreateLLMClient:
    """Test create_llm_client factory function."""

    def test_factory_creates_openai_client(self):
        """Test factory creates OpenAI client correctly."""
        with patch('openai.OpenAI') as mock_openai:
            mock_openai.return_value = MagicMock()

            client = create_llm_client(backend="openai", api_key="test-key")

            assert isinstance(client, LLMClient)
            assert client.backend == "openai"
            assert client.model == "gpt-4o"

    def test_factory_creates_anthropic_client(self):
        """Test factory creates Anthropic client correctly."""
        import sys

        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = MagicMock()

        with patch.dict(sys.modules, {'anthropic': mock_anthropic}):
            client = create_llm_client(backend="anthropic", api_key="test-key")

            assert isinstance(client, LLMClient)
            assert client.backend == "anthropic"
            assert client.model == "claude-opus-4-20250514"

    def test_factory_with_model_override(self):
        """Test factory with custom model."""
        with patch('openai.OpenAI') as mock_openai:
            mock_openai.return_value = MagicMock()

            client = create_llm_client(
                backend="openai",
                model="gpt-4-turbo",
                api_key="test-key"
            )

            assert client.model == "gpt-4-turbo"


class TestLLMClientAsync:
    """Test async generation (placeholder for future native async support)."""

    @pytest.mark.asyncio
    async def test_agenerate_wraps_sync(self):
        """Test agenerate wraps sync generate call."""
        with patch('openai.OpenAI') as mock_openai:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "Async response"
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.return_value = mock_client

            llm = LLMClient(LLMConfig(backend="openai"), api_key="test-key")
            response = await llm.agenerate([{"role": "user", "content": "Hello"}])

            assert response == "Async response"
