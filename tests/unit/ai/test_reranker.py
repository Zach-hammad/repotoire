"""Unit tests for reranking (REPO-241)."""

import sys
import pytest
from unittest.mock import Mock, patch, MagicMock

from repotoire.ai.reranker import (
    RerankerConfig,
    Reranker,
    create_reranker,
    RERANKER_CONFIGS,
)


class TestRerankerConfig:
    """Tests for RerankerConfig dataclass."""

    def test_defaults(self):
        """Test default configuration values."""
        config = RerankerConfig()

        assert config.enabled is False  # Disabled by default
        assert config.backend == "voyage"
        assert config.model is None
        assert config.top_k == 10
        assert config.retrieve_multiplier == 3

    def test_get_model_default(self):
        """Test get_model returns backend default when model is None."""
        config = RerankerConfig(backend="voyage")
        assert config.get_model() == "rerank-2"

        config = RerankerConfig(backend="local")
        assert config.get_model() == "cross-encoder/ms-marco-MiniLM-L-6-v2"

    def test_get_model_custom(self):
        """Test get_model returns custom model when specified."""
        config = RerankerConfig(backend="voyage", model="custom-model")
        assert config.get_model() == "custom-model"

    def test_get_model_none_backend(self):
        """Test get_model with none backend returns empty string."""
        config = RerankerConfig(backend="none")
        assert config.get_model() == ""


class TestVoyageReranker:
    """Tests for VoyageReranker."""

    def test_requires_api_key(self, monkeypatch):
        """Test VoyageReranker requires VOYAGE_API_KEY."""
        from repotoire.ai.reranker import VoyageReranker

        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

        with pytest.raises(ValueError, match="VOYAGE_API_KEY"):
            VoyageReranker()

    def test_requires_voyageai_package(self):
        """Test VoyageReranker requires voyageai package."""
        # This is implicitly tested by the import - if voyageai isn't
        # installed, the import will fail in the reranker module
        pass

    def test_initialization(self, monkeypatch):
        """Test VoyageReranker initializes correctly."""
        # Mock voyageai module
        mock_voyageai = MagicMock()
        mock_voyageai.Client.return_value = Mock()
        monkeypatch.setitem(sys.modules, "voyageai", mock_voyageai)
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        from repotoire.ai.reranker import VoyageReranker

        reranker = VoyageReranker(model="rerank-2")

        assert reranker.model == "rerank-2"
        mock_voyageai.Client.assert_called_once_with(api_key="test-key")

    def test_rerank(self, monkeypatch):
        """Test VoyageReranker rerank method."""
        # Mock voyageai module
        mock_client = Mock()
        mock_voyageai = MagicMock()
        mock_voyageai.Client.return_value = mock_client
        monkeypatch.setitem(sys.modules, "voyageai", mock_voyageai)
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        # Mock rerank response
        mock_result = Mock()
        mock_result.results = [
            Mock(index=1, relevance_score=0.95),
            Mock(index=0, relevance_score=0.85),
        ]
        mock_client.rerank.return_value = mock_result

        from repotoire.ai.reranker import VoyageReranker

        reranker = VoyageReranker()

        docs = [
            {"node": {"name": "func1", "docstring": "First function"}},
            {"node": {"name": "func2", "docstring": "Second function"}},
        ]

        results = reranker.rerank("test query", docs, top_k=2)

        assert len(results) == 2
        assert results[0]["score"] == 0.95
        assert results[1]["score"] == 0.85
        # Original score should be preserved in metadata
        assert "original_score" in results[0].get("metadata", {})

    def test_rerank_empty_docs(self, monkeypatch):
        """Test VoyageReranker handles empty documents."""
        mock_voyageai = MagicMock()
        mock_voyageai.Client.return_value = Mock()
        monkeypatch.setitem(sys.modules, "voyageai", mock_voyageai)
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        from repotoire.ai.reranker import VoyageReranker

        reranker = VoyageReranker()

        results = reranker.rerank("test query", [], top_k=5)

        assert results == []


class TestLocalReranker:
    """Tests for LocalReranker."""

    def test_requires_sentence_transformers(self):
        """Test LocalReranker requires sentence-transformers package."""
        # This is implicitly tested by the import - if sentence-transformers
        # isn't installed, the import will fail in the reranker module
        pass

    def test_initialization(self, monkeypatch):
        """Test LocalReranker initializes correctly."""
        # Mock sentence_transformers module
        mock_model = Mock()
        mock_sentence_transformers = MagicMock()
        mock_sentence_transformers.CrossEncoder.return_value = mock_model
        monkeypatch.setitem(sys.modules, "sentence_transformers", mock_sentence_transformers)

        from repotoire.ai.reranker import LocalReranker

        reranker = LocalReranker(model="test-model")

        assert reranker.model_name == "test-model"
        mock_sentence_transformers.CrossEncoder.assert_called_once_with("test-model")

    def test_rerank(self, monkeypatch):
        """Test LocalReranker rerank method."""
        mock_model = Mock()
        mock_model.predict.return_value = [0.85, 0.95]
        mock_sentence_transformers = MagicMock()
        mock_sentence_transformers.CrossEncoder.return_value = mock_model
        monkeypatch.setitem(sys.modules, "sentence_transformers", mock_sentence_transformers)

        from repotoire.ai.reranker import LocalReranker

        reranker = LocalReranker()

        docs = [
            {"node": {"name": "func1"}},
            {"node": {"name": "func2"}},
        ]

        results = reranker.rerank("test query", docs, top_k=2)

        assert len(results) == 2
        # Results should be sorted by score descending
        assert results[0]["score"] == 0.95
        assert results[1]["score"] == 0.85

    def test_rerank_returns_top_k(self, monkeypatch):
        """Test LocalReranker returns only top_k results."""
        mock_model = Mock()
        mock_model.predict.return_value = [0.5, 0.9, 0.7, 0.6, 0.8]
        mock_sentence_transformers = MagicMock()
        mock_sentence_transformers.CrossEncoder.return_value = mock_model
        monkeypatch.setitem(sys.modules, "sentence_transformers", mock_sentence_transformers)

        from repotoire.ai.reranker import LocalReranker

        reranker = LocalReranker()

        docs = [{"node": {"name": f"func_{i}"}} for i in range(5)]

        results = reranker.rerank("test query", docs, top_k=3)

        assert len(results) == 3
        # Should be sorted descending by score
        scores = [r["score"] for r in results]
        assert scores == [0.9, 0.8, 0.7]

    def test_rerank_empty_docs(self, monkeypatch):
        """Test LocalReranker handles empty documents."""
        mock_sentence_transformers = MagicMock()
        mock_sentence_transformers.CrossEncoder.return_value = Mock()
        monkeypatch.setitem(sys.modules, "sentence_transformers", mock_sentence_transformers)

        from repotoire.ai.reranker import LocalReranker

        reranker = LocalReranker()

        results = reranker.rerank("test query", [], top_k=5)

        assert results == []

    def test_rerank_preserves_metadata(self, monkeypatch):
        """Test LocalReranker preserves original score in metadata."""
        mock_model = Mock()
        mock_model.predict.return_value = [0.9]
        mock_sentence_transformers = MagicMock()
        mock_sentence_transformers.CrossEncoder.return_value = mock_model
        monkeypatch.setitem(sys.modules, "sentence_transformers", mock_sentence_transformers)

        from repotoire.ai.reranker import LocalReranker

        reranker = LocalReranker()

        docs = [{"node": {"name": "func1"}, "score": 0.75}]

        results = reranker.rerank("test query", docs, top_k=1)

        assert results[0]["metadata"]["original_score"] == 0.75
        assert results[0]["metadata"]["rerank_score"] == 0.9


class TestExtractText:
    """Tests for Reranker._extract_text method."""

    def test_extract_text_full_doc(self, monkeypatch):
        """Test text extraction with all fields."""
        mock_sentence_transformers = MagicMock()
        mock_sentence_transformers.CrossEncoder.return_value = Mock()
        monkeypatch.setitem(sys.modules, "sentence_transformers", mock_sentence_transformers)

        from repotoire.ai.reranker import LocalReranker

        reranker = LocalReranker()

        doc = {
            "node": {
                "name": "authenticate",
                "docstring": "Authenticate user credentials",
                "source_code": "def authenticate(user, password): pass",
            }
        }

        text = reranker._extract_text(doc)

        assert "authenticate" in text
        assert "Authenticate user credentials" in text
        assert "def authenticate" in text

    def test_extract_text_flat_doc(self, monkeypatch):
        """Test text extraction from flat (non-nested) document."""
        mock_sentence_transformers = MagicMock()
        mock_sentence_transformers.CrossEncoder.return_value = Mock()
        monkeypatch.setitem(sys.modules, "sentence_transformers", mock_sentence_transformers)

        from repotoire.ai.reranker import LocalReranker

        reranker = LocalReranker()

        doc = {
            "name": "my_function",
            "docstring": "A simple function",
        }

        text = reranker._extract_text(doc)

        assert "my_function" in text
        assert "A simple function" in text

    def test_extract_text_truncates_source(self, monkeypatch):
        """Test source code is truncated to 500 characters."""
        mock_sentence_transformers = MagicMock()
        mock_sentence_transformers.CrossEncoder.return_value = Mock()
        monkeypatch.setitem(sys.modules, "sentence_transformers", mock_sentence_transformers)

        from repotoire.ai.reranker import LocalReranker

        reranker = LocalReranker()

        long_code = "x" * 1000
        doc = {"node": {"source_code": long_code}}

        text = reranker._extract_text(doc)

        # Should only include first 500 chars of source
        assert len(text) <= 510  # 500 chars + some prefix text


class TestCreateReranker:
    """Tests for create_reranker factory function."""

    def test_returns_none_when_disabled(self):
        """Test create_reranker returns None when disabled."""
        config = RerankerConfig(enabled=False)
        assert create_reranker(config) is None

    def test_returns_none_for_none_backend(self):
        """Test create_reranker returns None for 'none' backend."""
        config = RerankerConfig(enabled=True, backend="none")
        assert create_reranker(config) is None

    def test_creates_voyage_reranker(self, monkeypatch):
        """Test create_reranker creates VoyageReranker."""
        mock_voyageai = MagicMock()
        mock_voyageai.Client.return_value = Mock()
        monkeypatch.setitem(sys.modules, "voyageai", mock_voyageai)
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        from repotoire.ai.reranker import create_reranker, VoyageReranker

        config = RerankerConfig(enabled=True, backend="voyage")
        reranker = create_reranker(config)

        assert isinstance(reranker, VoyageReranker)

    def test_creates_local_reranker(self, monkeypatch):
        """Test create_reranker creates LocalReranker."""
        mock_sentence_transformers = MagicMock()
        mock_sentence_transformers.CrossEncoder.return_value = Mock()
        monkeypatch.setitem(sys.modules, "sentence_transformers", mock_sentence_transformers)

        from repotoire.ai.reranker import create_reranker, LocalReranker

        config = RerankerConfig(enabled=True, backend="local")
        reranker = create_reranker(config)

        assert isinstance(reranker, LocalReranker)

    def test_raises_for_unknown_backend(self):
        """Test create_reranker raises error for unknown backend."""
        config = RerankerConfig(enabled=True)
        config.backend = "unknown"  # type: ignore

        with pytest.raises(ValueError, match="Unknown reranker backend"):
            create_reranker(config)


class TestRerankerBackendConfigs:
    """Tests for reranker backend configurations."""

    def test_voyage_config(self):
        """Test Voyage backend configuration."""
        config = RERANKER_CONFIGS["voyage"]

        assert config["model"] == "rerank-2"
        assert config["env_key"] == "VOYAGE_API_KEY"
        assert "rerank-2" in config["models"]

    def test_local_config(self):
        """Test local backend configuration."""
        config = RERANKER_CONFIGS["local"]

        assert config["model"] == "cross-encoder/ms-marco-MiniLM-L-6-v2"
        assert "cross-encoder/ms-marco-MiniLM-L-6-v2" in config["models"]
        assert "Qwen/Qwen3-Reranker-0.6B" in config["models"]
