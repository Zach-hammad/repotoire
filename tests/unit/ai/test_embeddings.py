"""Unit tests for CodeEmbedder."""

from unittest.mock import Mock, patch, MagicMock
import pytest

from repotoire.ai.embeddings import CodeEmbedder, EmbeddingConfig, create_embedder
from repotoire.models import (
    FunctionEntity,
    ClassEntity,
    FileEntity,
    NodeType
)


@pytest.fixture
def mock_openai_client():
    """Mock OpenAI client."""
    with patch('openai.OpenAI') as mock:
        mock_client = Mock()
        mock_response = Mock()

        # Make embeddings.create return appropriate responses based on input
        def mock_create(model, input):
            if not input:
                mock_response.data = []
            else:
                embeddings = []
                for i, _ in enumerate(input):
                    mock_embedding = Mock()
                    mock_embedding.embedding = [0.1 if i == 0 else 0.2] * 1536
                    embeddings.append(mock_embedding)
                mock_response.data = embeddings
            return mock_response

        mock_client.embeddings.create.side_effect = mock_create
        mock.return_value = mock_client
        yield mock


@pytest.fixture
def embedder(mock_openai_client, monkeypatch):
    """Create embedder with mocked OpenAI (explicit backend to avoid auto-selection)."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")
    return CodeEmbedder(backend="openai")


@pytest.fixture
def sample_function():
    """Sample function entity for testing."""
    return FunctionEntity(
        name="calculate_score",
        qualified_name="mymodule.py::calculate_score:10",
        file_path="src/mymodule.py",
        line_start=10,
        line_end=25,
        docstring="Calculate the score based on value and threshold.",
        parameters=["value", "threshold"],
        parameter_types={"value": "float", "threshold": "float"},
        return_type="int",
        complexity=5,
        is_async=False,
        decorators=["lru_cache"],
        is_method=False,
        is_static=False,
        is_classmethod=False,
        is_property=False
    )


@pytest.fixture
def sample_class():
    """Sample class entity for testing."""
    return ClassEntity(
        name="AuthService",
        qualified_name="mymodule.py::AuthService:30",
        file_path="src/mymodule.py",
        line_start=30,
        line_end=100,
        docstring="Service for user authentication.",
        is_abstract=False,
        is_dataclass=False,
        is_exception=False,
        decorators=[]
    )


@pytest.fixture
def sample_file():
    """Sample file entity for testing."""
    return FileEntity(
        name="mymodule.py",
        qualified_name="src/mymodule.py",
        file_path="src/mymodule.py",
        line_start=1,
        line_end=150,
        language="python",
        loc=120,
        hash="abc123",
        exports=["AuthService", "calculate_score"]
    )


class TestCodeEmbedder:
    """Test CodeEmbedder initialization and configuration."""

    def test_initialization_with_defaults(self, mock_openai_client, monkeypatch):
        """Test embedder initializes with default config (auto resolves to available backend)."""
        # Clear API keys so auto resolves to local
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)

        mock_st_module = MagicMock()
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 1024
        mock_st_module.SentenceTransformer.return_value = mock_model

        import sys
        with patch.dict(sys.modules, {'sentence_transformers': mock_st_module}):
            embedder = CodeEmbedder()

            # Default is now "auto", which resolves to local when no API keys
            assert embedder.config.backend == "auto"
            assert embedder.resolved_backend == "local"
            assert embedder.config.batch_size == 100

    def test_initialization_with_explicit_openai(self, mock_openai_client, monkeypatch):
        """Test embedder initializes with explicit OpenAI backend."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")
        embedder = CodeEmbedder(backend="openai")

        assert embedder.config.backend == "openai"
        assert embedder.resolved_backend == "openai"
        assert embedder.config.effective_model == "text-embedding-3-small"
        assert embedder.dimensions == 1536
        assert embedder.config.batch_size == 100

    def test_initialization_with_custom_config(self, mock_openai_client, monkeypatch):
        """Test embedder with custom configuration."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")
        config = EmbeddingConfig(
            backend="openai",  # Explicit backend for predictable testing
            model="text-embedding-3-large",
            batch_size=50
        )
        embedder = CodeEmbedder(config=config)

        assert embedder.config.effective_model == "text-embedding-3-large"
        assert embedder.dimensions == 1536  # Still OpenAI default dimensions
        assert embedder.config.batch_size == 50

    def test_factory_function(self, mock_openai_client, monkeypatch):
        """Test create_embedder factory function with explicit backend."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")
        embedder = create_embedder(backend="openai", model="text-embedding-3-small")

        assert isinstance(embedder, CodeEmbedder)
        assert embedder.config.effective_model == "text-embedding-3-small"
        assert embedder.resolved_backend == "openai"


class TestEntityTextConversion:
    """Test conversion of entities to text representations."""

    def test_function_entity_text_representation(self, embedder, sample_function):
        """Test function entity converts to rich text."""
        text = embedder._entity_to_text(sample_function)

        # Should include type (NodeType.FUNCTION.value is "Function")
        assert "Type: Function" in text

        # Should include name
        assert "Name: calculate_score" in text

        # Should include signature
        assert "Signature: def calculate_score(value, threshold) -> int" in text

        # Should include docstring
        assert "Documentation: Calculate the score" in text

        # Should include decorators
        assert "Decorators: lru_cache" in text

        # Should include location
        assert "Location: src/mymodule.py" in text

        # Complexity 5 doesn't show (threshold is > 5 for moderate, > 10 for complex)
        # This is expected behavior

    def test_class_entity_text_representation(self, embedder, sample_class):
        """Test class entity converts to rich text."""
        text = embedder._entity_to_text(sample_class)

        # Should include type (NodeType.CLASS.value is "Class")
        assert "Type: Class" in text

        # Should include name
        assert "Name: AuthService" in text

        # Should include docstring
        assert "Documentation: Service for user authentication" in text

        # Should include location
        assert "Location: src/mymodule.py" in text

    def test_file_entity_text_representation(self, embedder, sample_file):
        """Test file entity converts to rich text."""
        text = embedder._entity_to_text(sample_file)

        # Should include type (NodeType.FILE.value is "File")
        assert "Type: File" in text

        # Should include name
        assert "Name: mymodule.py" in text

        # Should include language
        assert "Language: python" in text

        # Should include size
        assert "Size: 120 LOC" in text

        # Should include exports
        assert "Exports: AuthService, calculate_score" in text

    def test_function_with_no_docstring(self, embedder):
        """Test function without docstring still generates text."""
        func = FunctionEntity(
            name="helper",
            qualified_name="test.py::helper:5",
            file_path="test.py",
            line_start=5,
            line_end=10,
            parameters=[],
            return_type=None
        )

        text = embedder._entity_to_text(func)

        # Should still have basic info
        assert "Name: helper" in text
        assert "Signature: def helper()" in text
        # Docstring section should not be present
        assert "Documentation:" not in text

    def test_async_function_characteristics(self, embedder):
        """Test async function characteristics are captured."""
        func = FunctionEntity(
            name="fetch_data",
            qualified_name="async_module.py::fetch_data:10",
            file_path="async_module.py",
            line_start=10,
            line_end=20,
            parameters=["url"],
            is_async=True,
            complexity=3
        )

        text = embedder._entity_to_text(func)

        assert "async" in text.lower()

    def test_static_method_characteristics(self, embedder):
        """Test static method characteristics are captured."""
        func = FunctionEntity(
            name="validate",
            qualified_name="utils.py::Utils:10.validate:15",
            file_path="utils.py",
            line_start=15,
            line_end=20,
            parameters=["value"],
            is_static=True,
            is_method=True,
            decorators=["staticmethod"]
        )

        text = embedder._entity_to_text(func)

        assert "staticmethod" in text

    def test_text_truncation_for_long_content(self, embedder):
        """Test text is truncated if too long."""
        func = FunctionEntity(
            name="huge_function",
            qualified_name="big.py::huge_function:1",
            file_path="big.py",
            line_start=1,
            line_end=1000,
            parameters=["param"] * 100,  # Many parameters
            docstring="X" * 5000  # Very long docstring
        )

        text = embedder._entity_to_text(func)

        # Should be truncated
        assert len(text) <= embedder.config.max_code_length + 3  # +3 for "..."
        assert text.endswith("...") or len(text) <= embedder.config.max_code_length


class TestEmbeddingGeneration:
    """Test embedding generation functionality."""

    def test_embed_single_entity(self, embedder, sample_function):
        """Test embedding a single entity."""
        embedding = embedder.embed_entity(sample_function)

        # Should return 1536-dimensional vector
        assert isinstance(embedding, list)
        assert len(embedding) == 1536
        assert all(isinstance(x, float) for x in embedding)

    def test_embed_query(self, embedder):
        """Test embedding a natural language query."""
        embedding = embedder.embed_query("How does authentication work?")

        # Should return 1536-dimensional vector
        assert isinstance(embedding, list)
        assert len(embedding) == 1536

    def test_embed_entities_batch(self, embedder, sample_function, sample_class):
        """Test batch embedding multiple entities."""
        entities = [sample_function, sample_class]

        embeddings = embedder.embed_entities_batch(entities)

        # Should return list of embeddings
        assert len(embeddings) == 2
        assert all(len(emb) == 1536 for emb in embeddings)

    def test_embed_empty_batch(self, embedder):
        """Test batch embedding with empty list."""
        embeddings = embedder.embed_entities_batch([])

        assert embeddings == []


class TestFunctionContext:
    """Test function-specific context extraction."""

    def test_function_context_with_return_type(self, embedder):
        """Test function context includes return type."""
        func = FunctionEntity(
            name="get_user",
            qualified_name="user.py::get_user:10",
            file_path="user.py",
            line_start=10,
            line_end=15,
            parameters=["user_id"],
            parameter_types={"user_id": "int"},
            return_type="User"
        )

        context = embedder._function_context(func)

        # Should include return type in signature
        assert any("-> User" in part for part in context)

    def test_function_context_complexity_levels(self, embedder):
        """Test complexity characterization."""
        # Low complexity
        func_low = FunctionEntity(
            name="simple",
            qualified_name="test.py::simple:1",
            file_path="test.py",
            line_start=1,
            line_end=5,
            parameters=[],
            complexity=2
        )

        # No complexity mentioned for low values
        context_low = embedder._function_context(func_low)
        assert not any("Complexity:" in part for part in context_low)

        # Moderate complexity
        func_mod = FunctionEntity(
            name="moderate",
            qualified_name="test.py::moderate:10",
            file_path="test.py",
            line_start=10,
            line_end=20,
            parameters=[],
            complexity=7
        )

        context_mod = embedder._function_context(func_mod)
        assert any("moderate" in part.lower() for part in context_mod)

        # High complexity
        func_high = FunctionEntity(
            name="complex",
            qualified_name="test.py::complex:30",
            file_path="test.py",
            line_start=30,
            line_end=50,
            parameters=[],
            complexity=15
        )

        context_high = embedder._function_context(func_high)
        assert any("complex" in part.lower() for part in context_high)


class TestClassContext:
    """Test class-specific context extraction."""

    def test_abstract_class_context(self, embedder):
        """Test abstract class is identified."""
        cls = ClassEntity(
            name="BaseParser",
            qualified_name="parser.py::BaseParser:10",
            file_path="parser.py",
            line_start=10,
            line_end=50,
            is_abstract=True
        )

        context = embedder._class_context(cls)

        assert any("abstract" in part.lower() for part in context)

    def test_dataclass_context(self, embedder):
        """Test dataclass is identified."""
        cls = ClassEntity(
            name="Config",
            qualified_name="config.py::Config:5",
            file_path="config.py",
            line_start=5,
            line_end=20,
            is_dataclass=True,
            decorators=["dataclass"]
        )

        context = embedder._class_context(cls)

        assert any("dataclass" in part.lower() for part in context)

    def test_exception_class_context(self, embedder):
        """Test exception class is identified."""
        cls = ClassEntity(
            name="CustomError",
            qualified_name="errors.py::CustomError:10",
            file_path="errors.py",
            line_start=10,
            line_end=20,
            is_exception=True
        )

        context = embedder._class_context(cls)

        assert any("exception" in part.lower() for part in context)


class TestFileContext:
    """Test file-specific context extraction."""

    def test_file_size_categorization(self, embedder):
        """Test file size is categorized correctly."""
        # Small file
        file_small = FileEntity(
            name="small.py",
            qualified_name="small.py",
            file_path="small.py",
            line_start=1,
            line_end=50,
            language="python",
            loc=50
        )

        context_small = embedder._file_context(file_small)
        assert any("small file" in part.lower() for part in context_small)

        # Medium file
        file_medium = FileEntity(
            name="medium.py",
            qualified_name="medium.py",
            file_path="medium.py",
            line_start=1,
            line_end=200,
            language="python",
            loc=200
        )

        context_medium = embedder._file_context(file_medium)
        assert any("medium file" in part.lower() for part in context_medium)

        # Large file
        file_large = FileEntity(
            name="large.py",
            qualified_name="large.py",
            file_path="large.py",
            line_start=1,
            line_end=1000,
            language="python",
            loc=1000
        )

        context_large = embedder._file_context(file_large)
        assert any("large file" in part.lower() for part in context_large)

    def test_file_exports_truncation(self, embedder):
        """Test file exports are truncated if too many."""
        exports = [f"Symbol{i}" for i in range(20)]

        file = FileEntity(
            name="big_module.py",
            qualified_name="big_module.py",
            file_path="big_module.py",
            line_start=1,
            line_end=500,
            language="python",
            loc=450,
            exports=exports
        )

        context = embedder._file_context(file)

        # Should only include first 10 exports
        exports_part = [part for part in context if "Exports:" in part][0]
        # Count commas to check number of exports listed
        assert exports_part.count(",") < len(exports) - 1


class TestLocalEmbeddings:
    """Test local embedding backend with Qwen3 and fallback."""

    def test_local_backend_config_defaults(self):
        """Test local backend config includes Qwen3 and fallback."""
        from repotoire.ai.embeddings import BACKEND_CONFIGS

        local_config = BACKEND_CONFIGS["local"]

        assert local_config["model"] == "Qwen/Qwen3-Embedding-0.6B"
        assert local_config["dimensions"] == 1024
        assert local_config["fallback_model"] == "all-MiniLM-L6-v2"
        assert local_config["fallback_dimensions"] == 384

    def test_embedding_config_local_dimensions(self):
        """Test EmbeddingConfig returns correct dimensions for local."""
        config = EmbeddingConfig(backend="local")

        assert config.dimensions == 1024
        assert config.effective_model == "Qwen/Qwen3-Embedding-0.6B"

    def test_local_embedder_initialization(self):
        """Test local embedder initializes with Qwen3 model."""
        import sys

        # Create a mock for sentence_transformers
        mock_st_module = MagicMock()
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 1024
        mock_st_module.SentenceTransformer.return_value = mock_model

        with patch.dict(sys.modules, {'sentence_transformers': mock_st_module}):
            embedder = CodeEmbedder(backend="local")

            mock_st_module.SentenceTransformer.assert_called_once_with("Qwen/Qwen3-Embedding-0.6B")
            assert embedder.dimensions == 1024

    def test_local_embedder_fallback_on_error(self):
        """Test fallback to MiniLM when Qwen3 fails to load."""
        import sys

        mock_st_module = MagicMock()
        mock_fallback_model = MagicMock()
        mock_fallback_model.get_sentence_embedding_dimension.return_value = 384

        # First call (Qwen3) fails, second call (MiniLM) succeeds
        mock_st_module.SentenceTransformer.side_effect = [
            Exception("Out of memory"),
            mock_fallback_model
        ]

        with patch.dict(sys.modules, {'sentence_transformers': mock_st_module}):
            embedder = CodeEmbedder(backend="local")

            # Should have tried both models
            assert mock_st_module.SentenceTransformer.call_count == 2
            mock_st_module.SentenceTransformer.assert_any_call("Qwen/Qwen3-Embedding-0.6B")
            mock_st_module.SentenceTransformer.assert_any_call("all-MiniLM-L6-v2")
            assert embedder.dimensions == 384

    def test_local_embedder_no_fallback_when_using_custom_model(self):
        """Test no fallback when using a custom model that fails."""
        import sys

        mock_st_module = MagicMock()
        mock_st_module.SentenceTransformer.side_effect = Exception("Model not found")

        with patch.dict(sys.modules, {'sentence_transformers': mock_st_module}):
            # When using a custom model (not the default), should not fallback
            with pytest.raises(Exception, match="Model not found"):
                CodeEmbedder(backend="local", model="custom-model")

    def test_local_embed_query(self):
        """Test embedding a query with local backend."""
        import sys
        import numpy as np

        mock_st_module = MagicMock()
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 1024
        mock_model.encode.return_value = np.array([[0.1] * 1024])
        mock_st_module.SentenceTransformer.return_value = mock_model

        with patch.dict(sys.modules, {'sentence_transformers': mock_st_module}):
            embedder = CodeEmbedder(backend="local")
            embedding = embedder.embed_query("test query")

            assert len(embedding) == 1024
            mock_model.encode.assert_called_once()

    def test_local_embed_batch(self):
        """Test batch embedding with local backend."""
        import sys
        import numpy as np

        mock_st_module = MagicMock()
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 1024
        mock_model.encode.return_value = np.array([[0.1] * 1024, [0.2] * 1024])
        mock_st_module.SentenceTransformer.return_value = mock_model

        with patch.dict(sys.modules, {'sentence_transformers': mock_st_module}):
            embedder = CodeEmbedder(backend="local")
            embeddings = embedder.embed_batch(["text1", "text2"])

            assert len(embeddings) == 2
            assert all(len(emb) == 1024 for emb in embeddings)

    def test_get_embedding_dimensions_local(self):
        """Test get_embedding_dimensions returns correct value for local."""
        from repotoire.ai.embeddings import get_embedding_dimensions

        dims = get_embedding_dimensions(backend="local")

        assert dims == 1024


class TestDeepInfraEmbeddings:
    """Test DeepInfra embedding backend."""

    def test_deepinfra_backend_config_defaults(self):
        """Test DeepInfra backend config has correct defaults."""
        from repotoire.ai.embeddings import BACKEND_CONFIGS

        deepinfra_config = BACKEND_CONFIGS["deepinfra"]

        assert deepinfra_config["model"] == "Qwen/Qwen3-Embedding-8B"
        assert deepinfra_config["dimensions"] == 4096
        assert deepinfra_config["base_url"] == "https://api.deepinfra.com/v1/openai"
        assert deepinfra_config["env_key"] == "DEEPINFRA_API_KEY"

    def test_embedding_config_deepinfra_dimensions(self):
        """Test EmbeddingConfig returns correct dimensions for DeepInfra."""
        config = EmbeddingConfig(backend="deepinfra")

        assert config.dimensions == 4096
        assert config.effective_model == "Qwen/Qwen3-Embedding-8B"

    def test_deepinfra_backend_requires_api_key(self):
        """Test DeepInfra backend raises error without API key."""
        import os

        # Ensure env var is not set
        env_backup = os.environ.pop("DEEPINFRA_API_KEY", None)

        try:
            with pytest.raises(ValueError, match="DEEPINFRA_API_KEY"):
                CodeEmbedder(backend="deepinfra")
        finally:
            # Restore env var if it was set
            if env_backup:
                os.environ["DEEPINFRA_API_KEY"] = env_backup

    def test_deepinfra_embedder_initialization_with_env_key(self):
        """Test DeepInfra embedder initializes with env var API key."""
        import os

        # Set mock API key
        os.environ["DEEPINFRA_API_KEY"] = "test-api-key"

        try:
            embedder = CodeEmbedder(backend="deepinfra")

            assert embedder.dimensions == 4096
            assert embedder._deepinfra_api_key == "test-api-key"
            assert embedder._deepinfra_base_url == "https://api.deepinfra.com/v1/openai"
        finally:
            del os.environ["DEEPINFRA_API_KEY"]

    def test_deepinfra_embedder_initialization_with_provided_key(self):
        """Test DeepInfra embedder initializes with provided API key."""
        embedder = CodeEmbedder(backend="deepinfra", api_key="my-custom-key")

        assert embedder.dimensions == 4096
        assert embedder._deepinfra_api_key == "my-custom-key"

    def test_deepinfra_embed_query(self):
        """Test embedding a query with DeepInfra backend."""
        from unittest.mock import patch, MagicMock

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 4096
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        with patch('openai.OpenAI', return_value=mock_client):
            embedder = CodeEmbedder(backend="deepinfra", api_key="test-key")
            embedding = embedder.embed_query("test query")

            assert len(embedding) == 4096
            mock_client.embeddings.create.assert_called_once_with(
                model="Qwen/Qwen3-Embedding-8B",
                input=["test query"],
            )

    def test_deepinfra_embed_batch(self):
        """Test batch embedding with DeepInfra backend."""
        from unittest.mock import patch, MagicMock

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_embedding1 = MagicMock()
        mock_embedding1.embedding = [0.1] * 4096
        mock_embedding2 = MagicMock()
        mock_embedding2.embedding = [0.2] * 4096
        mock_response.data = [mock_embedding1, mock_embedding2]
        mock_client.embeddings.create.return_value = mock_response

        with patch('openai.OpenAI', return_value=mock_client):
            embedder = CodeEmbedder(backend="deepinfra", api_key="test-key")
            embeddings = embedder.embed_batch(["text1", "text2"])

            assert len(embeddings) == 2
            assert all(len(emb) == 4096 for emb in embeddings)

    def test_deepinfra_custom_model(self):
        """Test DeepInfra with custom model override."""
        from unittest.mock import patch, MagicMock

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 4096
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        with patch('openai.OpenAI', return_value=mock_client):
            embedder = CodeEmbedder(
                backend="deepinfra",
                api_key="test-key",
                model="Qwen/Qwen3-Embedding-4B"
            )
            embedder.embed_query("test")

            mock_client.embeddings.create.assert_called_once_with(
                model="Qwen/Qwen3-Embedding-4B",
                input=["test"],
            )

    def test_get_embedding_dimensions_deepinfra(self):
        """Test get_embedding_dimensions returns correct value for DeepInfra."""
        from repotoire.ai.embeddings import get_embedding_dimensions

        dims = get_embedding_dimensions(backend="deepinfra")

        assert dims == 4096

    def test_create_embedder_factory_deepinfra(self):
        """Test create_embedder factory function with DeepInfra."""
        from repotoire.ai.embeddings import create_embedder

        embedder = create_embedder(backend="deepinfra", api_key="test-key")

        assert isinstance(embedder, CodeEmbedder)
        assert embedder.config.backend == "deepinfra"
        assert embedder.dimensions == 4096


class TestVoyageEmbeddings:
    """Test Voyage AI embedding backend (REPO-236)."""

    def test_voyage_backend_config_defaults(self):
        """Test Voyage backend config has correct defaults."""
        from repotoire.ai.embeddings import BACKEND_CONFIGS

        voyage_config = BACKEND_CONFIGS["voyage"]

        assert voyage_config["model"] == "voyage-code-3"
        assert voyage_config["dimensions"] == 1024
        assert voyage_config["env_key"] == "VOYAGE_API_KEY"
        assert "models" in voyage_config
        assert "voyage-code-3" in voyage_config["models"]
        assert "voyage-3.5" in voyage_config["models"]
        assert "voyage-3.5-lite" in voyage_config["models"]

    def test_embedding_config_voyage_dimensions(self):
        """Test EmbeddingConfig returns correct dimensions for Voyage."""
        config = EmbeddingConfig(backend="voyage")

        assert config.dimensions == 1024
        assert config.effective_model == "voyage-code-3"

    def test_voyage_backend_requires_api_key(self):
        """Test Voyage backend raises error without API key."""
        import os

        # Ensure env var is not set
        env_backup = os.environ.pop("VOYAGE_API_KEY", None)

        try:
            with pytest.raises(ValueError, match="VOYAGE_API_KEY"):
                CodeEmbedder(backend="voyage")
        finally:
            # Restore env var if it was set
            if env_backup:
                os.environ["VOYAGE_API_KEY"] = env_backup

    def test_voyage_embedder_initialization_with_env_key(self):
        """Test Voyage embedder initializes with env var API key."""
        import os

        # Set mock API key
        os.environ["VOYAGE_API_KEY"] = "test-voyage-key"

        try:
            embedder = CodeEmbedder(backend="voyage")

            assert embedder.dimensions == 1024
            assert embedder._voyage_api_key == "test-voyage-key"
        finally:
            del os.environ["VOYAGE_API_KEY"]

    def test_voyage_embedder_initialization_with_provided_key(self):
        """Test Voyage embedder initializes with provided API key."""
        embedder = CodeEmbedder(backend="voyage", api_key="my-voyage-key")

        assert embedder.dimensions == 1024
        assert embedder._voyage_api_key == "my-voyage-key"

    def test_voyage_uses_document_type_for_batch(self):
        """Test Voyage uses input_type='document' for batch embedding."""
        import sys

        mock_voyageai = MagicMock()
        mock_client = MagicMock()
        mock_result = MagicMock()
        mock_result.embeddings = [[0.1] * 1024, [0.2] * 1024]
        mock_client.embed.return_value = mock_result
        mock_voyageai.Client.return_value = mock_client

        with patch.dict(sys.modules, {'voyageai': mock_voyageai}):
            embedder = CodeEmbedder(backend="voyage", api_key="test-key")
            embeddings = embedder.embed_batch(["code1", "code2"])

            assert len(embeddings) == 2
            mock_client.embed.assert_called_once_with(
                texts=["code1", "code2"],
                model="voyage-code-3",
                input_type="document",
            )

    def test_voyage_uses_query_type_for_search(self):
        """Test Voyage uses input_type='query' for query embedding."""
        import sys

        mock_voyageai = MagicMock()
        mock_client = MagicMock()
        mock_result = MagicMock()
        mock_result.embeddings = [[0.1] * 1024]
        mock_client.embed.return_value = mock_result
        mock_voyageai.Client.return_value = mock_client

        with patch.dict(sys.modules, {'voyageai': mock_voyageai}):
            embedder = CodeEmbedder(backend="voyage", api_key="test-key")
            embedding = embedder.embed_query("find auth functions")

            assert len(embedding) == 1024
            mock_client.embed.assert_called_once_with(
                texts=["find auth functions"],
                model="voyage-code-3",
                input_type="query",
            )

    def test_voyage_custom_model(self):
        """Test Voyage with custom model override."""
        import sys

        mock_voyageai = MagicMock()
        mock_client = MagicMock()
        mock_result = MagicMock()
        mock_result.embeddings = [[0.1] * 512]
        mock_client.embed.return_value = mock_result
        mock_voyageai.Client.return_value = mock_client

        with patch.dict(sys.modules, {'voyageai': mock_voyageai}):
            embedder = CodeEmbedder(
                backend="voyage",
                api_key="test-key",
                model="voyage-3.5-lite"
            )
            # voyage-3.5-lite has 512 dimensions
            assert embedder.dimensions == 512

            embedder.embed_query("test")

            mock_client.embed.assert_called_once_with(
                texts=["test"],
                model="voyage-3.5-lite",
                input_type="query",
            )

    def test_voyage_raises_import_error_when_not_installed(self, monkeypatch):
        """Test Voyage raises ImportError when voyageai package not installed."""
        import sys
        import builtins

        # Clear environment to avoid auto-selection issues
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

        # Remove voyageai from sys.modules if present
        voyageai_module = sys.modules.pop('voyageai', None)

        # Also block import attempts
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == 'voyageai' or (isinstance(name, str) and name.startswith('voyageai')):
                raise ImportError("No module named 'voyageai'")
            return original_import(name, *args, **kwargs)

        try:
            monkeypatch.setattr(builtins, '__import__', mock_import)
            embedder = CodeEmbedder(backend="voyage", api_key="test-key")

            # This should raise ImportError when trying to embed
            with pytest.raises(ImportError, match="voyageai package required"):
                embedder.embed_query("test")
        finally:
            if voyageai_module:
                sys.modules['voyageai'] = voyageai_module

    def test_get_embedding_dimensions_voyage(self):
        """Test get_embedding_dimensions returns correct value for Voyage."""
        from repotoire.ai.embeddings import get_embedding_dimensions

        dims = get_embedding_dimensions(backend="voyage")

        assert dims == 1024

    def test_create_embedder_factory_voyage(self):
        """Test create_embedder factory function with Voyage."""
        from repotoire.ai.embeddings import create_embedder

        embedder = create_embedder(backend="voyage", api_key="test-key")

        assert isinstance(embedder, CodeEmbedder)
        assert embedder.config.backend == "voyage"
        assert embedder.dimensions == 1024
