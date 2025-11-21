"""Unit tests for GraphRAGRetriever."""

from unittest.mock import Mock, patch, mock_open
import pytest

from repotoire.ai.retrieval import GraphRAGRetriever, RetrievalResult
from repotoire.ai.embeddings import CodeEmbedder
from repotoire.graph.client import Neo4jClient


@pytest.fixture
def mock_neo4j_client():
    """Mock Neo4jClient."""
    client = Mock(spec=Neo4jClient)
    return client


@pytest.fixture
def mock_embedder():
    """Mock CodeEmbedder."""
    embedder = Mock(spec=CodeEmbedder)
    embedder.embed_query.return_value = [0.1] * 1536
    return embedder


@pytest.fixture
def retriever(mock_neo4j_client, mock_embedder):
    """Create retriever with mocked dependencies."""
    return GraphRAGRetriever(
        neo4j_client=mock_neo4j_client,
        embedder=mock_embedder,
        context_lines=5
    )


@pytest.fixture
def sample_vector_search_result():
    """Sample vector search result from Neo4j."""
    return {
        "element_id": "4:abc123:1",
        "qualified_name": "mymodule.py::calculate_score:10",
        "name": "calculate_score",
        "entity_type": "Function",
        "docstring": "Calculate the score based on value.",
        "file_path": "src/mymodule.py",
        "line_start": 10,
        "line_end": 25,
        "score": 0.95
    }


class TestGraphRAGRetriever:
    """Test GraphRAGRetriever initialization and basic functionality."""

    def test_initialization(self, mock_neo4j_client, mock_embedder):
        """Test retriever initializes correctly."""
        retriever = GraphRAGRetriever(
            neo4j_client=mock_neo4j_client,
            embedder=mock_embedder,
            context_lines=10
        )

        assert retriever.client == mock_neo4j_client
        assert retriever.embedder == mock_embedder
        assert retriever.context_lines == 10

    def test_default_context_lines(self, mock_neo4j_client, mock_embedder):
        """Test default context_lines parameter."""
        retriever = GraphRAGRetriever(
            neo4j_client=mock_neo4j_client,
            embedder=mock_embedder
        )

        assert retriever.context_lines == 5


class TestVectorSearch:
    """Test vector similarity search functionality."""

    def test_vector_search_single_entity_type(self, retriever, mock_neo4j_client):
        """Test vector search for a single entity type."""
        query_embedding = [0.1] * 1536

        # Mock Neo4j response
        mock_neo4j_client.execute_query.return_value = [
            {
                "element_id": "4:abc123:1",
                "qualified_name": "test.py::func:10",
                "name": "func",
                "entity_type": "Function",
                "docstring": "Test function",
                "file_path": "test.py",
                "line_start": 10,
                "line_end": 20,
                "score": 0.95
            }
        ]

        results = retriever._vector_search(
            query_embedding,
            top_k=5,
            entity_types=["Function"]
        )

        assert len(results) == 1
        assert results[0]["qualified_name"] == "test.py::func:10"
        assert results[0]["score"] == 0.95

        # Verify query was called with correct parameters
        call_args = mock_neo4j_client.execute_query.call_args
        # execute_query(query, params) - params is second positional arg
        params = call_args[0][1]
        assert params["index_name"] == "function_embeddings"
        assert params["top_k"] == 5
        assert params["embedding"] == query_embedding

    def test_vector_search_multiple_entity_types(self, retriever, mock_neo4j_client):
        """Test vector search across multiple entity types."""
        query_embedding = [0.1] * 1536

        # Mock Neo4j responses for different entity types
        mock_neo4j_client.execute_query.side_effect = [
            [{"score": 0.95, "qualified_name": "test.py::func:10", "name": "func", "element_id": "1"}],
            [{"score": 0.90, "qualified_name": "test.py::Class:20", "name": "Class", "element_id": "2"}],
        ]

        results = retriever._vector_search(
            query_embedding,
            top_k=10,
            entity_types=["Function", "Class"]
        )

        # Should return results sorted by score
        assert len(results) == 2
        assert results[0]["score"] == 0.95
        assert results[1]["score"] == 0.90

        # Verify both entity types were searched
        assert mock_neo4j_client.execute_query.call_count == 2

    def test_vector_search_defaults_to_all_types(self, retriever, mock_neo4j_client):
        """Test vector search defaults to Function, Class, File."""
        query_embedding = [0.1] * 1536

        # Mock empty responses
        mock_neo4j_client.execute_query.return_value = []

        retriever._vector_search(query_embedding, top_k=5)

        # Should search all 3 default types
        assert mock_neo4j_client.execute_query.call_count == 3

    def test_vector_search_limits_results(self, retriever, mock_neo4j_client):
        """Test vector search respects top_k limit."""
        query_embedding = [0.1] * 1536

        # Mock many results from each type
        mock_neo4j_client.execute_query.side_effect = [
            [{"score": 0.9 - i*0.01, "qualified_name": f"func{i}", "element_id": f"{i}"} for i in range(10)],
            [{"score": 0.8 - i*0.01, "qualified_name": f"class{i}", "element_id": f"{i+100}"} for i in range(10)],
            [{"score": 0.7 - i*0.01, "qualified_name": f"file{i}", "element_id": f"{i+200}"} for i in range(10)],
        ]

        results = retriever._vector_search(query_embedding, top_k=5)

        # Should only return top 5 results
        assert len(results) == 5
        # Should be sorted by score
        assert results[0]["score"] >= results[1]["score"]

    def test_vector_search_handles_missing_index(self, retriever, mock_neo4j_client):
        """Test vector search handles missing index gracefully."""
        query_embedding = [0.1] * 1536

        # Mock exception for missing index
        mock_neo4j_client.execute_query.side_effect = Exception("Index not found")

        results = retriever._vector_search(query_embedding, top_k=5)

        # Should return empty list instead of crashing
        assert results == []


class TestRelatedEntities:
    """Test graph traversal for related entities."""

    def test_get_related_entities(self, retriever, mock_neo4j_client):
        """Test fetching related entities via graph traversal."""
        entity_id = "4:abc123:1"

        # Mock Neo4j response with relationships
        mock_neo4j_client.execute_query.return_value = [
            {"entity": "test.py::helper:30", "relationship": "CALLS"},
            {"entity": "utils.py::validate:10", "relationship": "USES"},
            {"entity": "test.py::TestClass:50", "relationship": "CONTAINS"}
        ]

        relationships = retriever._get_related_entities(entity_id)

        assert len(relationships) == 3
        assert {"entity": "test.py::helper:30", "relationship": "CALLS"} in relationships
        assert {"entity": "utils.py::validate:10", "relationship": "USES"} in relationships

        # Verify query parameters
        call_args = mock_neo4j_client.execute_query.call_args
        # execute_query(query, params) - params is second positional arg
        params = call_args[0][1]
        assert params["id"] == entity_id
        assert params["max_relationships"] == 20

    def test_get_related_entities_limit(self, retriever, mock_neo4j_client):
        """Test related entities respects limit."""
        entity_id = "4:abc123:1"

        # Mock many relationships
        mock_neo4j_client.execute_query.return_value = [
            {"entity": f"entity{i}", "relationship": "CALLS"}
            for i in range(50)
        ]

        relationships = retriever._get_related_entities(entity_id, max_relationships=10)

        # Should respect limit parameter
        call_args = mock_neo4j_client.execute_query.call_args
        # execute_query(query, params) - params is second positional arg
        params = call_args[0][1]
        assert params["max_relationships"] == 10

    def test_get_related_entities_filters_none(self, retriever, mock_neo4j_client):
        """Test related entities filters out None values."""
        entity_id = "4:abc123:1"

        # Mock response with None entities
        mock_neo4j_client.execute_query.return_value = [
            {"entity": "test.py::func:10", "relationship": "CALLS"},
            {"entity": None, "relationship": "USES"},
            {"entity": "test.py::class:20", "relationship": "CONTAINS"}
        ]

        relationships = retriever._get_related_entities(entity_id)

        # Should filter out None entities
        assert len(relationships) == 2
        assert all(r["entity"] is not None for r in relationships)

    def test_get_related_entities_handles_exception(self, retriever, mock_neo4j_client):
        """Test related entities handles query exceptions."""
        entity_id = "4:abc123:1"

        # Mock exception
        mock_neo4j_client.execute_query.side_effect = Exception("Query failed")

        relationships = retriever._get_related_entities(entity_id)

        # Should return empty list instead of crashing
        assert relationships == []


class TestCodeFetching:
    """Test source code fetching with context."""

    def test_fetch_code_basic(self, retriever):
        """Test fetching code snippet from file."""
        file_content = "line 1\nline 2\nline 3\nline 4\nline 5\nline 6\nline 7\nline 8\nline 9\nline 10\n"

        with patch("builtins.open", mock_open(read_data=file_content)):
            code = retriever._fetch_code("test.py", line_start=5, line_end=7)

        # Should include context lines (5 before and after)
        # Entity lines (5-7) should be marked with ">>>"
        assert ">>> " in code
        assert "5 |" in code
        assert "6 |" in code
        assert "7 |" in code

    def test_fetch_code_with_context(self, retriever):
        """Test code fetching includes context lines."""
        lines = [f"line {i}\n" for i in range(1, 21)]
        file_content = "".join(lines)

        with patch("builtins.open", mock_open(read_data=file_content)):
            code = retriever._fetch_code("test.py", line_start=10, line_end=12)

        # Should include 5 lines before (5-9) and 5 lines after (13-17)
        assert "5 |" in code  # Context before
        assert "17 |" in code  # Context after
        # Line numbers are formatted with 4-character width, so spaces are included
        assert ">>>   10 |" in code  # Entity line marked
        assert ">>>   11 |" in code
        assert ">>>   12 |" in code

    def test_fetch_code_at_file_start(self, retriever):
        """Test code fetching at start of file."""
        file_content = "line 1\nline 2\nline 3\nline 4\nline 5\n"

        with patch("builtins.open", mock_open(read_data=file_content)):
            code = retriever._fetch_code("test.py", line_start=1, line_end=2)

        # Should not crash at file boundary
        # Line numbers are formatted with 4-character width
        assert ">>>    1 |" in code
        assert ">>>    2 |" in code

    def test_fetch_code_at_file_end(self, retriever):
        """Test code fetching at end of file."""
        file_content = "line 1\nline 2\nline 3\nline 4\nline 5\n"

        with patch("builtins.open", mock_open(read_data=file_content)):
            code = retriever._fetch_code("test.py", line_start=4, line_end=5)

        # Should not crash at file boundary
        # Line numbers are formatted with 4-character width
        assert ">>>    4 |" in code
        assert ">>>    5 |" in code

    def test_fetch_code_handles_file_not_found(self, retriever):
        """Test code fetching handles missing files."""
        with patch("builtins.open", side_effect=FileNotFoundError()):
            code = retriever._fetch_code("missing.py", line_start=1, line_end=10)

        # Should return error message instead of crashing
        assert "Could not fetch code" in code


class TestHybridRetrieval:
    """Test complete hybrid retrieval (vector + graph)."""

    def test_retrieve_basic(self, retriever, mock_embedder, mock_neo4j_client):
        """Test basic hybrid retrieval."""
        query = "How does authentication work?"

        # Mock embedder
        mock_embedder.embed_query.return_value = [0.1] * 1536

        # Mock vector search results
        mock_neo4j_client.execute_query.side_effect = [
            # Vector search results
            [{
                "element_id": "4:abc123:1",
                "qualified_name": "auth.py::authenticate:10",
                "name": "authenticate",
                "entity_type": "Function",
                "docstring": "Authenticate user credentials",
                "file_path": "src/auth.py",
                "line_start": 10,
                "line_end": 25,
                "score": 0.95
            }],
            [],  # Class vector search
            [],  # File vector search
            # Related entities query
            [{"entity": "auth.py::validate_token:30", "relationship": "CALLS"}]
        ]

        # Mock file reading
        file_content = "\n".join([f"line {i}" for i in range(1, 40)])
        with patch("builtins.open", mock_open(read_data=file_content)):
            results = retriever.retrieve(query, top_k=5)

        assert len(results) == 1
        result = results[0]

        # Verify result structure
        assert isinstance(result, RetrievalResult)
        assert result.qualified_name == "auth.py::authenticate:10"
        assert result.name == "authenticate"
        assert result.entity_type == "Function"
        assert result.similarity_score == 0.95
        assert len(result.relationships) == 1
        assert "line 10" in result.code

    def test_retrieve_without_relationships(self, retriever, mock_embedder, mock_neo4j_client):
        """Test retrieval without fetching related entities."""
        query = "test query"

        mock_embedder.embed_query.return_value = [0.1] * 1536

        # Mock vector search
        mock_neo4j_client.execute_query.side_effect = [
            [{
                "element_id": "1",
                "qualified_name": "test.py::func:1",
                "name": "func",
                "entity_type": "Function",
                "docstring": "Test",
                "file_path": "test.py",
                "line_start": 1,
                "line_end": 5,
                "score": 0.9
            }],
            [], []  # Empty class and file results
        ]

        file_content = "def func():\n    pass\n"
        with patch("builtins.open", mock_open(read_data=file_content)):
            results = retriever.retrieve(query, include_related=False)

        # Should not fetch relationships
        assert len(results) == 1
        assert results[0].relationships == []

    def test_retrieve_filters_entity_types(self, retriever, mock_embedder, mock_neo4j_client):
        """Test retrieval can filter by entity types."""
        query = "test query"

        mock_embedder.embed_query.return_value = [0.1] * 1536

        # Mock vector search (only Function type)
        mock_neo4j_client.execute_query.return_value = []

        file_content = "test"
        with patch("builtins.open", mock_open(read_data=file_content)):
            retriever.retrieve(query, entity_types=["Function"])

        # Should only search Function type
        # _vector_search is called once internally, and it calls execute_query for each entity type
        call_args = mock_neo4j_client.execute_query.call_args
        assert "function_embeddings" in str(call_args)


class TestGraphTraversal:
    """Test pure graph traversal retrieval."""

    def test_retrieve_by_path_basic(self, retriever, mock_neo4j_client):
        """Test graph traversal from starting entity."""
        start_entity = "auth.py::AuthService:10"

        # Mock graph traversal results
        mock_neo4j_client.execute_query.side_effect = [
            # Traversal results
            [{
                "element_id": "4:abc123:2",
                "qualified_name": "auth.py::validate:30",
                "name": "validate",
                "entity_type": "Function",
                "docstring": "Validate credentials",
                "file_path": "src/auth.py",
                "line_start": 30,
                "line_end": 40,
                "distance": 1
            }],
            # Related entities for result
            [{"entity": "utils.py::hash:10", "relationship": "CALLS"}]
        ]

        file_content = "\n".join([f"line {i}" for i in range(1, 50)])
        with patch("builtins.open", mock_open(read_data=file_content)):
            results = retriever.retrieve_by_path(
                start_entity=start_entity,
                relationship_types=["CALLS", "USES"],
                max_hops=3,
                limit=20
            )

        assert len(results) == 1
        result = results[0]

        # Verify result structure
        assert result.qualified_name == "auth.py::validate:30"
        assert result.name == "validate"
        # Score should be 1.0 / (distance + 1) = 1.0 / 2 = 0.5
        assert result.similarity_score == 0.5
        assert len(result.relationships) == 1

        # Verify query parameters
        call_args = mock_neo4j_client.execute_query.call_args_list[0]
        # execute_query(query, params) - params is second positional arg
        params = call_args[0][1]
        assert params["start_qname"] == start_entity
        assert params["limit"] == 20

    def test_retrieve_by_path_multiple_hops(self, retriever, mock_neo4j_client):
        """Test graph traversal respects max_hops."""
        # Mock results at different distances
        mock_neo4j_client.execute_query.side_effect = [
            [
                {"element_id": "1", "qualified_name": "func1", "name": "func1",
                 "entity_type": "Function", "docstring": "", "file_path": "test.py",
                 "line_start": 1, "line_end": 5, "distance": 1},
                {"element_id": "2", "qualified_name": "func2", "name": "func2",
                 "entity_type": "Function", "docstring": "", "file_path": "test.py",
                 "line_start": 10, "line_end": 15, "distance": 2},
                {"element_id": "3", "qualified_name": "func3", "name": "func3",
                 "entity_type": "Function", "docstring": "", "file_path": "test.py",
                 "line_start": 20, "line_end": 25, "distance": 3},
            ],
            [], [], []  # Related entities for each result
        ]

        file_content = "\n".join([f"line {i}" for i in range(1, 30)])
        with patch("builtins.open", mock_open(read_data=file_content)):
            results = retriever.retrieve_by_path("start", ["CALLS"], max_hops=3)

        # Should return results ordered by distance (closer = higher score)
        assert len(results) == 3
        assert results[0].similarity_score > results[1].similarity_score
        assert results[1].similarity_score > results[2].similarity_score
