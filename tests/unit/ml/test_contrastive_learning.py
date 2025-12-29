"""Tests for contrastive learning module.

Tests the ContrastivePairGenerator and ContrastiveTrainer classes for
fine-tuning embeddings using contrastive learning.
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from repotoire.ml.contrastive_learning import (
    ContrastiveConfig,
    ContrastivePairGenerator,
    ContrastiveTrainer,
    fine_tune_from_graph,
)

# Check if sentence-transformers is available
try:
    import sentence_transformers
    HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    HAS_SENTENCE_TRANSFORMERS = False

requires_sentence_transformers = pytest.mark.skipif(
    not HAS_SENTENCE_TRANSFORMERS,
    reason="sentence-transformers not installed"
)


class TestContrastiveConfig:
    """Tests for ContrastiveConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = ContrastiveConfig()
        assert config.base_model == "all-MiniLM-L6-v2"
        assert config.epochs == 3
        assert config.batch_size == 32
        assert config.warmup_ratio == 0.1
        assert config.learning_rate == 2e-5
        assert config.max_code_docstring_pairs == 5000
        assert config.max_same_class_pairs == 2000
        assert config.max_caller_callee_pairs == 2000

    def test_custom_values(self):
        """Test custom configuration values."""
        config = ContrastiveConfig(
            base_model="all-mpnet-base-v2",
            epochs=5,
            batch_size=64,
            warmup_ratio=0.2,
            learning_rate=1e-5,
            max_code_docstring_pairs=10000,
        )
        assert config.base_model == "all-mpnet-base-v2"
        assert config.epochs == 5
        assert config.batch_size == 64
        assert config.warmup_ratio == 0.2
        assert config.learning_rate == 1e-5
        assert config.max_code_docstring_pairs == 10000


class TestContrastivePairGenerator:
    """Tests for ContrastivePairGenerator class."""

    def test_build_function_signature_basic(self):
        """Test building a basic function signature."""
        mock_client = MagicMock()
        generator = ContrastivePairGenerator(mock_client)

        sig = generator._build_function_signature({
            "name": "my_func",
            "qualifiedName": "module.my_func",
            "parameters": ["x", "y"],
            "return_type": "int",
            "is_async": False,
            "decorators": [],
        })

        assert "def my_func(x, y) -> int" in sig
        assert "# module.my_func" in sig

    def test_build_function_signature_async(self):
        """Test building an async function signature."""
        mock_client = MagicMock()
        generator = ContrastivePairGenerator(mock_client)

        sig = generator._build_function_signature({
            "name": "async_func",
            "qualifiedName": "module.async_func",
            "parameters": [],
            "return_type": "str",
            "is_async": True,
            "decorators": [],
        })

        assert "async def async_func() -> str" in sig

    def test_build_function_signature_with_decorators(self):
        """Test building a signature with decorators."""
        mock_client = MagicMock()
        generator = ContrastivePairGenerator(mock_client)

        sig = generator._build_function_signature({
            "name": "decorated",
            "qualifiedName": "module.decorated",
            "parameters": ["self"],
            "return_type": None,
            "is_async": False,
            "decorators": ["property", "cached"],
        })

        assert "@property" in sig
        assert "@cached" in sig
        assert "def decorated(self)" in sig

    def test_generate_code_docstring_pairs(self):
        """Test generating signature-docstring pairs from graph."""
        mock_client = MagicMock()
        mock_client.execute_query.return_value = [
            {
                "name": "foo",
                "qualifiedName": "module.foo",
                "parameters": [],
                "return_type": None,
                "is_async": False,
                "decorators": [],
                "docstring": "This is foo function",
            },
            {
                "name": "bar",
                "qualifiedName": "module.bar",
                "parameters": ["x"],
                "return_type": "int",
                "is_async": False,
                "decorators": [],
                "docstring": "Returns the input",
            },
        ]

        generator = ContrastivePairGenerator(mock_client)
        pairs = generator.generate_code_docstring_pairs(limit=100)

        assert len(pairs) == 2
        # First pair should have signature and docstring
        assert "def foo()" in pairs[0][0]
        assert pairs[0][1] == "This is foo function"
        # Second pair should include return type
        assert "def bar(x) -> int" in pairs[1][0]
        assert pairs[1][1] == "Returns the input"

        # Verify query was called with correct limit
        mock_client.execute_query.assert_called_once()
        call_args = mock_client.execute_query.call_args
        # Params are passed as second positional arg
        params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
        assert params["limit"] == 100

    def test_generate_same_class_pairs(self):
        """Test generating same-class method pairs from graph."""
        mock_client = MagicMock()
        mock_client.execute_query.return_value = [
            {
                "name1": "method1", "qname1": "MyClass.method1",
                "params1": ["self"], "ret1": None, "async1": False, "dec1": [],
                "name2": "method2", "qname2": "MyClass.method2",
                "params2": ["self"], "ret2": "int", "async2": False, "dec2": [],
            },
        ]

        generator = ContrastivePairGenerator(mock_client)
        pairs = generator.generate_same_class_pairs(limit=50)

        assert len(pairs) == 1
        assert "def method1(self)" in pairs[0][0]
        assert "def method2(self) -> int" in pairs[0][1]

    def test_generate_caller_callee_pairs(self):
        """Test generating caller-callee pairs from graph."""
        mock_client = MagicMock()
        mock_client.execute_query.return_value = [
            {
                "caller_name": "caller", "caller_qname": "module.caller",
                "caller_params": [], "caller_ret": None, "caller_async": False, "caller_dec": [],
                "callee_name": "callee", "callee_qname": "module.callee",
                "callee_params": [], "callee_ret": None, "callee_async": False, "callee_dec": [],
            },
        ]

        generator = ContrastivePairGenerator(mock_client)
        pairs = generator.generate_caller_callee_pairs(limit=50)

        assert len(pairs) == 1
        assert "def caller()" in pairs[0][0]
        assert "def callee()" in pairs[0][1]

    def test_generate_all_pairs(self):
        """Test generating all pair types."""
        mock_client = MagicMock()

        # Return different results for each query type
        def mock_execute(query, params=None):
            if "docstring" in query:
                return [{
                    "name": "foo", "qualifiedName": "module.foo",
                    "parameters": [], "return_type": None,
                    "is_async": False, "decorators": [],
                    "docstring": "Foo doc",
                }]
            elif "CONTAINS" in query:
                return [{
                    "name1": "m1", "qname1": "Class.m1",
                    "params1": [], "ret1": None, "async1": False, "dec1": [],
                    "name2": "m2", "qname2": "Class.m2",
                    "params2": [], "ret2": None, "async2": False, "dec2": [],
                }]
            elif "CALLS" in query:
                return [{
                    "caller_name": "a", "caller_qname": "module.a",
                    "caller_params": [], "caller_ret": None, "caller_async": False, "caller_dec": [],
                    "callee_name": "b", "callee_qname": "module.b",
                    "callee_params": [], "callee_ret": None, "callee_async": False, "callee_dec": [],
                }]
            return []

        mock_client.execute_query.side_effect = mock_execute

        generator = ContrastivePairGenerator(mock_client)
        config = ContrastiveConfig(
            max_code_docstring_pairs=10,
            max_same_class_pairs=10,
            max_caller_callee_pairs=10,
        )
        pairs = generator.generate_all_pairs(config)

        # Should have 3 pairs total (1 from each type)
        assert len(pairs) == 3
        assert mock_client.execute_query.call_count == 3

    def test_empty_graph_returns_empty_pairs(self):
        """Test that empty graph returns empty pairs."""
        mock_client = MagicMock()
        mock_client.execute_query.return_value = []

        generator = ContrastivePairGenerator(mock_client)
        pairs = generator.generate_code_docstring_pairs()

        assert len(pairs) == 0


class TestContrastiveTrainer:
    """Tests for ContrastiveTrainer class."""

    def test_initialization_with_defaults(self):
        """Test trainer initialization with default config."""
        trainer = ContrastiveTrainer()
        assert trainer.config.base_model == "all-MiniLM-L6-v2"
        assert trainer._model is None
        assert trainer._initialized is False

    def test_initialization_with_custom_config(self):
        """Test trainer initialization with custom config."""
        config = ContrastiveConfig(
            base_model="all-mpnet-base-v2",
            epochs=5,
        )
        trainer = ContrastiveTrainer(config)
        assert trainer.config.base_model == "all-mpnet-base-v2"
        assert trainer.config.epochs == 5

    def test_train_requires_pairs(self):
        """Test that training requires non-empty pairs."""
        trainer = ContrastiveTrainer()
        with pytest.raises(ValueError, match="No training pairs provided"):
            trainer.train([])

    @requires_sentence_transformers
    def test_train_with_mocked_model(self):
        """Test training with mocked sentence transformer."""
        with patch("repotoire.ml.contrastive_learning.ContrastiveTrainer._init_model"):
            trainer = ContrastiveTrainer(ContrastiveConfig(epochs=1, batch_size=2))
            trainer._initialized = True
            trainer._model = MagicMock()
            trainer._model.fit = MagicMock()

            pairs = [
                ("code1", "doc1"),
                ("code2", "doc2"),
                ("code3", "doc3"),
                ("code4", "doc4"),
            ]

            with tempfile.TemporaryDirectory() as tmpdir:
                stats = trainer.train(pairs, Path(tmpdir) / "model")

            assert stats["pairs"] == 4
            assert stats["epochs"] == 1
            assert stats["batch_size"] == 2
            trainer._model.fit.assert_called_once()

    def test_lazy_initialization(self):
        """Test that model is lazily initialized."""
        trainer = ContrastiveTrainer()
        assert trainer._model is None
        assert trainer._initialized is False

        # Model property should trigger initialization
        # But we can't test without the actual package

    @requires_sentence_transformers
    def test_encode_after_training(self):
        """Test encoding texts after training."""
        with patch("repotoire.ml.contrastive_learning.ContrastiveTrainer._init_model"):
            trainer = ContrastiveTrainer()
            trainer._initialized = True

            # Mock the model's encode method
            import numpy as np
            mock_embeddings = np.array([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
            trainer._model = MagicMock()
            trainer._model.encode.return_value = mock_embeddings

            embeddings = trainer.encode(["text1", "text2"])

            assert len(embeddings) == 2
            assert embeddings[0] == [0.1, 0.2, 0.3]
            trainer._model.encode.assert_called_once_with(["text1", "text2"], show_progress_bar=False)


class TestFineTuneFromGraph:
    """Tests for the fine_tune_from_graph convenience function."""

    def test_no_pairs_raises_error(self):
        """Test that no pairs in graph raises error."""
        mock_client = MagicMock()
        mock_client.execute_query.return_value = []

        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="No training pairs found"):
                fine_tune_from_graph(mock_client, Path(tmpdir) / "model")

    @requires_sentence_transformers
    def test_fine_tune_from_graph_success(self):
        """Test successful fine-tuning from graph."""
        mock_client = MagicMock()

        def mock_execute(query, params=None):
            if "docstring" in query:
                return [
                    {
                        "name": "foo", "qualifiedName": "module.foo",
                        "parameters": [], "return_type": None,
                        "is_async": False, "decorators": [],
                        "docstring": "Foo documentation",
                    },
                    {
                        "name": "bar", "qualifiedName": "module.bar",
                        "parameters": [], "return_type": "int",
                        "is_async": False, "decorators": [],
                        "docstring": "Bar documentation",
                    },
                ]
            return []

        mock_client.execute_query.side_effect = mock_execute

        with patch("repotoire.ml.contrastive_learning.ContrastiveTrainer._init_model"):
            with patch("repotoire.ml.contrastive_learning.ContrastiveTrainer.train") as mock_train:
                mock_train.return_value = {
                    "pairs": 2,
                    "epochs": 3,
                    "batch_size": 32,
                    "warmup_steps": 1,
                    "total_steps": 3,
                    "base_model": "all-MiniLM-L6-v2",
                    "output": "/tmp/model",
                }

                with tempfile.TemporaryDirectory() as tmpdir:
                    stats = fine_tune_from_graph(mock_client, Path(tmpdir) / "model")

                assert stats["pairs"] == 2
                mock_train.assert_called_once()


class TestContrastiveLearningIntegration:
    """Integration tests for contrastive learning pipeline."""

    @requires_sentence_transformers
    def test_end_to_end_with_small_dataset(self):
        """Test end-to-end training with a small dataset."""
        from sentence_transformers import SentenceTransformer

        # Use a very small config for fast testing
        config = ContrastiveConfig(
            base_model="all-MiniLM-L6-v2",
            epochs=1,
            batch_size=2,
        )

        trainer = ContrastiveTrainer(config)

        # Create a small set of pairs
        pairs = [
            ("def add(a, b):\n    return a + b", "Add two numbers together"),
            ("def subtract(a, b):\n    return a - b", "Subtract b from a"),
            ("class Calculator:\n    pass", "A simple calculator class"),
            ("def multiply(x, y):\n    return x * y", "Multiply two values"),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test_model"
            stats = trainer.train(pairs, output_path)

            assert stats["pairs"] == 4
            assert stats["epochs"] == 1
            assert output_path.exists()

            # Load the saved model and verify it works
            loaded_model = SentenceTransformer(str(output_path))
            embeddings = loaded_model.encode(["test code"])
            assert len(embeddings) == 1
            assert len(embeddings[0]) > 0  # Has embedding dimensions
