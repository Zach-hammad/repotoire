"""Unit tests for auto embedding backend selection (REPO-239)."""

import os
import sys
import logging
from unittest.mock import MagicMock, patch

import pytest

from repotoire.ai.embeddings import (
    EmbeddingBackend,
    EmbeddingConfig,
    CodeEmbedder,
    BACKEND_CONFIGS,
    BACKEND_PRIORITY,
    detect_available_backends,
    select_best_backend,
    get_embedding_dimensions,
)


class TestDetectAvailableBackends:
    """Test detection of available embedding backends."""

    def test_local_always_available(self, monkeypatch):
        """Test local backend is always in available list."""
        # Clear all API keys
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)

        available = detect_available_backends()

        assert "local" in available

    def test_detect_voyage_when_key_set(self, monkeypatch):
        """Test Voyage backend detected when API key is set."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        available = detect_available_backends()

        assert "voyage" in available

    def test_detect_openai_when_key_set(self, monkeypatch):
        """Test OpenAI backend detected when API key is set."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

        available = detect_available_backends()

        assert "openai" in available

    def test_detect_deepinfra_when_key_set(self, monkeypatch):
        """Test DeepInfra backend detected when API key is set."""
        monkeypatch.setenv("DEEPINFRA_API_KEY", "test-key")
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        available = detect_available_backends()

        assert "deepinfra" in available

    def test_detect_multiple_backends(self, monkeypatch):
        """Test multiple backends detected when multiple keys set."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

        available = detect_available_backends()

        assert "voyage" in available
        assert "openai" in available
        assert "local" in available

    def test_backends_in_priority_order(self, monkeypatch):
        """Test available backends are returned in priority order."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("DEEPINFRA_API_KEY", "test-key")

        available = detect_available_backends()

        # Should be in priority order: voyage, openai, deepinfra, local
        assert available.index("voyage") < available.index("openai")
        assert available.index("openai") < available.index("deepinfra")
        assert available.index("deepinfra") < available.index("local")


class TestSelectBestBackend:
    """Test automatic backend selection logic."""

    def test_auto_selects_voyage_when_available(self, monkeypatch):
        """Test Voyage selected when API key available."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

        backend, reason = select_best_backend()

        assert backend == "voyage"
        assert "voyage" in reason.lower()

    def test_auto_selects_openai_when_voyage_unavailable(self, monkeypatch):
        """Test OpenAI selected when Voyage unavailable."""
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

        backend, reason = select_best_backend()

        assert backend == "openai"
        assert "openai" in reason.lower()

    def test_auto_selects_deepinfra_when_higher_unavailable(self, monkeypatch):
        """Test DeepInfra selected when Voyage and OpenAI unavailable."""
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("DEEPINFRA_API_KEY", "test-key")

        backend, reason = select_best_backend()

        assert backend == "deepinfra"
        assert "deepinfra" in reason.lower()

    def test_auto_falls_back_to_local(self, monkeypatch):
        """Test local fallback when no API keys configured."""
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)

        backend, reason = select_best_backend()

        assert backend == "local"
        assert "free" in reason.lower()

    def test_reason_includes_description_for_api_backends(self, monkeypatch):
        """Test reason includes backend description for API backends."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        backend, reason = select_best_backend()

        assert "Using voyage" in reason
        assert "code" in reason.lower()  # "best for code" in description


class TestEmbeddingConfigResolveBackend:
    """Test EmbeddingConfig.resolve_backend method."""

    def test_auto_resolves_to_best_available(self, monkeypatch):
        """Test auto backend resolves correctly."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

        config = EmbeddingConfig(backend="auto")
        backend, reason = config.resolve_backend()

        assert backend == "openai"

    def test_explicit_backend_overrides_auto(self, monkeypatch):
        """Test explicit backend is not auto-resolved."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        config = EmbeddingConfig(backend="local")  # Explicit
        backend, reason = config.resolve_backend()

        assert backend == "local"
        assert "Explicitly configured" in reason

    def test_dimensions_uses_resolved_backend(self, monkeypatch):
        """Test dimensions property uses resolved backend."""
        monkeypatch.setenv("DEEPINFRA_API_KEY", "test-key")
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        config = EmbeddingConfig(backend="auto")

        # Should use deepinfra dimensions (4096)
        assert config.dimensions == 4096

    def test_effective_model_uses_resolved_backend(self, monkeypatch):
        """Test effective_model property uses resolved backend."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        config = EmbeddingConfig(backend="auto")

        # Should use voyage model
        assert config.effective_model == "voyage-code-3"


class TestCodeEmbedderAutoBackend:
    """Test CodeEmbedder with auto backend selection."""

    def test_embedder_resolves_auto_backend(self, monkeypatch):
        """Test embedder stores resolved backend."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

        with patch('neo4j_graphrag.embeddings.OpenAIEmbeddings'):
            embedder = CodeEmbedder(backend="auto")

        assert embedder.resolved_backend == "openai"
        assert "openai" in embedder.backend_reason.lower()

    def test_embedder_stores_backend_reason(self, monkeypatch):
        """Test embedder stores the selection reason."""
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)

        mock_st_module = MagicMock()
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 1024
        mock_st_module.SentenceTransformer.return_value = mock_model

        with patch.dict(sys.modules, {'sentence_transformers': mock_st_module}):
            embedder = CodeEmbedder(backend="auto")

        assert embedder.resolved_backend == "local"
        assert "free" in embedder.backend_reason.lower()

    def test_embedder_uses_resolved_backend_dimensions(self, monkeypatch):
        """Test embedder uses correct dimensions for resolved backend."""
        monkeypatch.setenv("DEEPINFRA_API_KEY", "test-key")
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        embedder = CodeEmbedder(backend="auto", api_key="test-key")

        assert embedder.dimensions == 4096  # DeepInfra dimensions

    def test_embedder_logs_selected_backend(self, monkeypatch, caplog):
        """Test embedder logs which backend was selected."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

        with patch('neo4j_graphrag.embeddings.OpenAIEmbeddings'):
            with caplog.at_level(logging.INFO):
                embedder = CodeEmbedder(backend="auto")

        assert "Embedding backend:" in caplog.text

    def test_default_backend_is_auto(self, monkeypatch):
        """Test default backend is auto."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

        with patch('neo4j_graphrag.embeddings.OpenAIEmbeddings'):
            # No backend parameter - should default to auto
            embedder = CodeEmbedder()

        # Should have resolved to openai (no voyage key)
        assert embedder.resolved_backend == "openai"


class TestGetEmbeddingDimensions:
    """Test get_embedding_dimensions function with auto."""

    def test_auto_returns_resolved_dimensions(self, monkeypatch):
        """Test auto returns dimensions of resolved backend."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        dims = get_embedding_dimensions(backend="auto")

        assert dims == 1024  # Voyage dimensions

    def test_auto_fallback_dimensions(self, monkeypatch):
        """Test auto falls back to local dimensions."""
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)

        dims = get_embedding_dimensions(backend="auto")

        assert dims == 1024  # Local Qwen3 dimensions

    def test_explicit_backend_dimensions(self):
        """Test explicit backend returns correct dimensions."""
        assert get_embedding_dimensions(backend="openai") == 1536
        assert get_embedding_dimensions(backend="deepinfra") == 4096
        assert get_embedding_dimensions(backend="local") == 1024
        assert get_embedding_dimensions(backend="voyage") == 1024


class TestBackendPriority:
    """Test backend priority configuration."""

    def test_priority_order(self):
        """Test backend priority is in expected order."""
        assert BACKEND_PRIORITY == ["voyage", "openai", "deepinfra", "local"]

    def test_all_backends_have_configs(self):
        """Test all backends in priority list have configurations."""
        for backend in BACKEND_PRIORITY:
            assert backend in BACKEND_CONFIGS
            assert "dimensions" in BACKEND_CONFIGS[backend]
            assert "model" in BACKEND_CONFIGS[backend]
            assert "description" in BACKEND_CONFIGS[backend]

    def test_local_has_no_env_key(self):
        """Test local backend has no API key requirement."""
        assert BACKEND_CONFIGS["local"].get("env_key") is None

    def test_api_backends_have_env_keys(self):
        """Test API backends have environment key specified."""
        for backend in ["voyage", "openai", "deepinfra"]:
            assert BACKEND_CONFIGS[backend].get("env_key") is not None


class TestEmbeddingBackendType:
    """Test EmbeddingBackend type includes auto."""

    def test_auto_is_valid_backend(self):
        """Test 'auto' is a valid backend choice."""
        # This tests at runtime that the Literal type includes 'auto'
        config = EmbeddingConfig(backend="auto")
        assert config.backend == "auto"

    def test_all_explicit_backends_valid(self):
        """Test all explicit backends are valid."""
        for backend in ["openai", "local", "deepinfra", "voyage"]:
            config = EmbeddingConfig(backend=backend)
            assert config.backend == backend
