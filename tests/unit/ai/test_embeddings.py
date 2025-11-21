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
def mock_openai_embeddings():
    """Mock OpenAIEmbeddings from neo4j-graphrag."""
    with patch('repotoire.ai.embeddings.OpenAIEmbeddings') as mock:
        # Mock the embeddings instance
        mock_instance = Mock()
        mock_instance.embed_query.return_value = [0.1] * 1536

        # Make embed_documents return appropriate responses based on input
        def mock_embed_documents(texts):
            if not texts:
                return []
            return [[0.1 if i == 0 else 0.2] * 1536 for i in range(len(texts))]

        mock_instance.embed_documents.side_effect = mock_embed_documents
        mock.return_value = mock_instance
        yield mock


@pytest.fixture
def embedder(mock_openai_embeddings):
    """Create embedder with mocked OpenAI."""
    return CodeEmbedder()


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

    def test_initialization_with_defaults(self, mock_openai_embeddings):
        """Test embedder initializes with default config."""
        embedder = CodeEmbedder()

        assert embedder.config.model == "text-embedding-3-small"
        assert embedder.config.dimensions == 1536
        assert embedder.config.batch_size == 100

    def test_initialization_with_custom_config(self, mock_openai_embeddings):
        """Test embedder with custom configuration."""
        config = EmbeddingConfig(
            model="text-embedding-3-large",
            dimensions=3072,
            batch_size=50
        )
        embedder = CodeEmbedder(config=config)

        assert embedder.config.model == "text-embedding-3-large"
        assert embedder.config.dimensions == 3072
        assert embedder.config.batch_size == 50

    def test_factory_function(self, mock_openai_embeddings):
        """Test create_embedder factory function."""
        embedder = create_embedder(model="text-embedding-3-small")

        assert isinstance(embedder, CodeEmbedder)
        assert embedder.config.model == "text-embedding-3-small"


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
