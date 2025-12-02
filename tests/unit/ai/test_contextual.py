"""Unit tests for contextual retrieval module (REPO-242)."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import asyncio

from repotoire.ai.contextual import (
    ContextGenerator,
    ContextualRetrievalConfig,
    CostTracker,
    CostLimitExceeded,
    ContextGenerationResult,
    create_context_generator,
)
from repotoire.models import FunctionEntity, ClassEntity, FileEntity


class TestCostTracker:
    """Tests for CostTracker class."""

    def test_init(self):
        """Test CostTracker initialization."""
        tracker = CostTracker()
        assert tracker.input_tokens == 0
        assert tracker.output_tokens == 0
        assert tracker.model is None
        assert tracker.total_cost == 0.0

    def test_add_tokens(self):
        """Test adding token usage."""
        tracker = CostTracker()
        tracker.add(input_tokens=1000, output_tokens=100, model="claude-haiku-3-5-20241022")

        assert tracker.input_tokens == 1000
        assert tracker.output_tokens == 100
        assert tracker.model == "claude-haiku-3-5-20241022"

    def test_add_tokens_cumulative(self):
        """Test that token counts are cumulative."""
        tracker = CostTracker()
        tracker.add(input_tokens=1000, output_tokens=100, model="claude-haiku-3-5-20241022")
        tracker.add(input_tokens=2000, output_tokens=200, model="claude-haiku-3-5-20241022")

        assert tracker.input_tokens == 3000
        assert tracker.output_tokens == 300

    def test_total_cost_haiku(self):
        """Test cost calculation for Haiku model."""
        tracker = CostTracker()
        # Haiku: $0.80/1M input + $4.00/1M output
        tracker.add(
            input_tokens=1_000_000,
            output_tokens=100_000,
            model="claude-haiku-3-5-20241022"
        )

        # Expected: (1M * $0.80/1M) + (100K * $4.00/1M) = $0.80 + $0.40 = $1.20
        expected = 0.80 + 0.40
        assert abs(tracker.total_cost - expected) < 0.01

    def test_total_cost_sonnet(self):
        """Test cost calculation for Sonnet model."""
        tracker = CostTracker()
        # Sonnet: $3.00/1M input + $15.00/1M output
        tracker.add(
            input_tokens=1_000_000,
            output_tokens=100_000,
            model="claude-sonnet-4-20250514"
        )

        # Expected: (1M * $3.00/1M) + (100K * $15.00/1M) = $3.00 + $1.50 = $4.50
        expected = 3.00 + 1.50
        assert abs(tracker.total_cost - expected) < 0.01

    def test_total_cost_unknown_model(self):
        """Test cost calculation with unknown model returns 0."""
        tracker = CostTracker()
        tracker.add(input_tokens=1000, output_tokens=100, model="unknown-model")

        assert tracker.total_cost == 0.0

    def test_total_cost_no_model(self):
        """Test cost calculation with no model returns 0."""
        tracker = CostTracker()
        tracker.input_tokens = 1000
        tracker.output_tokens = 100

        assert tracker.total_cost == 0.0

    def test_summary(self):
        """Test cost summary generation."""
        tracker = CostTracker()
        tracker.add(input_tokens=1000, output_tokens=100, model="claude-haiku-3-5-20241022")

        summary = tracker.summary()

        assert summary["input_tokens"] == 1000
        assert summary["output_tokens"] == 100
        assert summary["model"] == "claude-haiku-3-5-20241022"
        assert "total_cost_usd" in summary

    def test_reset(self):
        """Test resetting the tracker."""
        tracker = CostTracker()
        tracker.add(input_tokens=1000, output_tokens=100, model="claude-haiku-3-5-20241022")
        tracker.reset()

        assert tracker.input_tokens == 0
        assert tracker.output_tokens == 0
        assert tracker.model is None


class TestContextualRetrievalConfig:
    """Tests for ContextualRetrievalConfig class."""

    def test_default_config(self):
        """Test default configuration values."""
        config = ContextualRetrievalConfig()

        assert config.enabled is False
        assert config.model == "claude-haiku-3-5-20241022"
        assert config.max_concurrent == 10
        assert config.cache_contexts is True
        assert config.track_costs is True
        assert config.max_cost_usd is None

    def test_custom_config(self):
        """Test custom configuration values."""
        config = ContextualRetrievalConfig(
            enabled=True,
            model="claude-sonnet-4-20250514",
            max_concurrent=5,
            max_cost_usd=10.00,
        )

        assert config.enabled is True
        assert config.model == "claude-sonnet-4-20250514"
        assert config.max_concurrent == 5
        assert config.max_cost_usd == 10.00


class TestContextGenerator:
    """Tests for ContextGenerator class."""

    def test_requires_api_key(self, monkeypatch):
        """Test that context generator requires ANTHROPIC_API_KEY."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        config = ContextualRetrievalConfig(enabled=True)
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            ContextGenerator(config)

    @patch("repotoire.ai.contextual.os.getenv")
    def test_init_with_api_key(self, mock_getenv):
        """Test initialization with API key set."""
        mock_getenv.return_value = "test-api-key"

        with patch("anthropic.Anthropic") as mock_anthropic:
            config = ContextualRetrievalConfig(enabled=True)
            generator = ContextGenerator(config)

            assert generator.config == config
            mock_anthropic.assert_called_once_with(api_key="test-api-key")

    def test_context_prompt_formatting(self):
        """Test that context prompt is properly formatted."""
        entity = FunctionEntity(
            qualified_name="auth.handlers.py::AuthHandler.authenticate",
            name="authenticate",
            file_path="src/auth/handlers.py",
            line_start=10,
            line_end=25,
            docstring="Validate user credentials and return JWT token.",
            parameters=["username", "password"],
        )

        # Format the prompt
        prompt = ContextGenerator.CONTEXT_PROMPT.format(
            entity_type="function",
            name=entity.name,
            file_path=entity.file_path,
            parent_class="AuthHandler",
            docstring=entity.docstring or "None",
            source_code="def authenticate(self, ...): ...",
        )

        assert "authenticate" in prompt
        assert "src/auth/handlers.py" in prompt
        assert "AuthHandler" in prompt
        assert "Validate user credentials" in prompt

    @patch("repotoire.ai.contextual.os.getenv")
    def test_contextualize_text(self, mock_getenv):
        """Test contextualizing an entity with generated context."""
        mock_getenv.return_value = "test-api-key"

        with patch("anthropic.Anthropic"):
            config = ContextualRetrievalConfig(enabled=True)
            generator = ContextGenerator(config)

            entity = FunctionEntity(
                qualified_name="utils.py::helper",
                name="helper",
                file_path="src/utils.py",
                line_start=1,
                line_end=5,
                docstring="A helper function",
                parameters=[],
            )

            context = "This function is a utility helper in the utils module."

            result = generator.contextualize_text(entity, context)

            assert result.startswith("This function is a utility helper")
            assert "helper" in result
            assert "A helper function" in result

    @patch("repotoire.ai.contextual.os.getenv")
    def test_cost_tracker_property(self, mock_getenv):
        """Test cost_tracker property."""
        mock_getenv.return_value = "test-api-key"

        with patch("anthropic.Anthropic"):
            # With cost tracking enabled (default)
            config = ContextualRetrievalConfig(enabled=True, track_costs=True)
            generator = ContextGenerator(config)
            assert generator.cost_tracker is not None

            # With cost tracking disabled
            config = ContextualRetrievalConfig(enabled=True, track_costs=False)
            generator = ContextGenerator(config)
            assert generator.cost_tracker is None

    @patch("repotoire.ai.contextual.os.getenv")
    @pytest.mark.asyncio
    async def test_generate_context_cost_limit(self, mock_getenv):
        """Test that generate_context raises CostLimitExceeded when limit reached."""
        mock_getenv.return_value = "test-api-key"

        with patch("anthropic.Anthropic"):
            config = ContextualRetrievalConfig(
                enabled=True,
                max_cost_usd=0.01,  # Very low limit
            )
            generator = ContextGenerator(config)

            # Manually set cost tracker to exceed limit
            generator._cost_tracker.add(100_000_000, 10_000_000, "claude-haiku-3-5-20241022")

            entity = FunctionEntity(
                qualified_name="test.py::test_func",
                name="test_func",
                file_path="test.py",
                line_start=1,
                line_end=5,
                parameters=[],
            )

            with pytest.raises(CostLimitExceeded):
                await generator.generate_context(entity)

    @patch("repotoire.ai.contextual.os.getenv")
    @pytest.mark.asyncio
    async def test_generate_context_success(self, mock_getenv):
        """Test successful context generation."""
        mock_getenv.return_value = "test-api-key"

        mock_response = Mock()
        mock_response.content = [Mock(text="This is a test context.")]
        mock_response.usage = Mock(input_tokens=100, output_tokens=50)

        mock_client = Mock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            config = ContextualRetrievalConfig(enabled=True)
            generator = ContextGenerator(config)

            entity = FunctionEntity(
                qualified_name="test.py::test_func",
                name="test_func",
                file_path="test.py",
                line_start=1,
                line_end=5,
                parameters=[],
            )

            context = await generator.generate_context(entity)

            assert context == "This is a test context."
            assert generator._cost_tracker.input_tokens == 100
            assert generator._cost_tracker.output_tokens == 50


class TestContextGenerationResult:
    """Tests for ContextGenerationResult dataclass."""

    def test_default_values(self):
        """Test default values."""
        result = ContextGenerationResult()

        assert result.contexts == {}
        assert result.entities_processed == 0
        assert result.entities_failed == 0
        assert result.cost_summary is None

    def test_with_values(self):
        """Test with explicit values."""
        contexts = {"entity1": "context1", "entity2": "context2"}
        cost_summary = {"total_cost_usd": 0.50}

        result = ContextGenerationResult(
            contexts=contexts,
            entities_processed=2,
            entities_failed=1,
            cost_summary=cost_summary,
        )

        assert result.contexts == contexts
        assert result.entities_processed == 2
        assert result.entities_failed == 1
        assert result.cost_summary == cost_summary


class TestCreateContextGenerator:
    """Tests for create_context_generator factory function."""

    def test_disabled_returns_none(self):
        """Test that disabled returns None."""
        generator = create_context_generator(enabled=False)
        assert generator is None

    @patch("repotoire.ai.contextual.os.getenv")
    def test_no_api_key_returns_none(self, mock_getenv):
        """Test that missing API key returns None with warning."""
        mock_getenv.return_value = None

        generator = create_context_generator(enabled=True)
        assert generator is None

    @patch("repotoire.ai.contextual.os.getenv")
    def test_enabled_with_api_key(self, mock_getenv):
        """Test creation with valid API key."""
        mock_getenv.return_value = "test-api-key"

        with patch("anthropic.Anthropic"):
            generator = create_context_generator(
                enabled=True,
                model="claude-sonnet-4-20250514",
                max_cost_usd=5.00,
            )

            assert generator is not None
            assert generator.config.model == "claude-sonnet-4-20250514"
            assert generator.config.max_cost_usd == 5.00


class TestGetParentClass:
    """Tests for _get_parent_class helper method."""

    @patch("repotoire.ai.contextual.os.getenv")
    def test_method_with_parent_class(self, mock_getenv):
        """Test extracting parent class from method qualified name."""
        mock_getenv.return_value = "test-api-key"

        with patch("anthropic.Anthropic"):
            generator = ContextGenerator(ContextualRetrievalConfig())

            entity = FunctionEntity(
                qualified_name="module.py::MyClass.my_method",
                name="my_method",
                file_path="module.py",
                line_start=1,
                line_end=5,
                parameters=[],
            )

            parent = generator._get_parent_class(entity)
            assert parent == "MyClass"

    @patch("repotoire.ai.contextual.os.getenv")
    def test_function_without_parent_class(self, mock_getenv):
        """Test function without parent class returns N/A."""
        mock_getenv.return_value = "test-api-key"

        with patch("anthropic.Anthropic"):
            generator = ContextGenerator(ContextualRetrievalConfig())

            entity = FunctionEntity(
                qualified_name="module.py::standalone_function",
                name="standalone_function",
                file_path="module.py",
                line_start=1,
                line_end=5,
                parameters=[],
            )

            parent = generator._get_parent_class(entity)
            assert parent == "N/A"

    @patch("repotoire.ai.contextual.os.getenv")
    def test_nested_class_method(self, mock_getenv):
        """Test extracting parent class from nested class method."""
        mock_getenv.return_value = "test-api-key"

        with patch("anthropic.Anthropic"):
            generator = ContextGenerator(ContextualRetrievalConfig())

            entity = FunctionEntity(
                qualified_name="module.py::OuterClass.InnerClass.method",
                name="method",
                file_path="module.py",
                line_start=1,
                line_end=5,
                parameters=[],
            )

            parent = generator._get_parent_class(entity)
            assert parent == "InnerClass"


class TestCostLimitExceeded:
    """Tests for CostLimitExceeded exception."""

    def test_exception_message(self):
        """Test exception contains message."""
        exc = CostLimitExceeded("Cost limit $5.00 exceeded")
        assert "Cost limit $5.00 exceeded" in str(exc)

    def test_exception_is_exception(self):
        """Test CostLimitExceeded is a proper exception."""
        assert issubclass(CostLimitExceeded, Exception)

        with pytest.raises(CostLimitExceeded):
            raise CostLimitExceeded("test")
