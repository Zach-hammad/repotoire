"""Unit tests for GraphRAGRetriever."""

import time
from unittest.mock import Mock, patch, mock_open
import pytest

from repotoire.ai.retrieval import GraphRAGRetriever, RetrievalResult, RAGCache
from repotoire.ai.embeddings import CodeEmbedder
from repotoire.graph import FalkorDBClient


@pytest.fixture
def mock_graph_client():
    """Mock FalkorDBClient."""
    client = Mock(spec=FalkorDBClient)
    return client


@pytest.fixture
def mock_falkordb_client():
    """Mock FalkorDBClient."""
    # Create a mock that looks like FalkorDBClient
    client = Mock()
    # Set the class name so is_falkordb detection works
    type(client).__name__ = "FalkorDBClient"
    return client


@pytest.fixture
def mock_embedder():
    """Mock CodeEmbedder."""
    embedder = Mock(spec=CodeEmbedder)
    embedder.embed_query.return_value = [0.1] * 1536
    return embedder


@pytest.fixture
def retriever(mock_graph_client, mock_embedder):
    """Create retriever with mocked dependencies."""
    return GraphRAGRetriever(
        client=mock_graph_client,
        embedder=mock_embedder,
        context_lines=5
    )


@pytest.fixture
def retriever_with_cache(mock_graph_client, mock_embedder):
    """Create retriever with cache enabled."""
    return GraphRAGRetriever(
        client=mock_graph_client,
        embedder=mock_embedder,
        context_lines=5,
        cache_enabled=True,
        cache_ttl=3600,
        cache_max_size=100
    )


@pytest.fixture
def retriever_no_cache(mock_graph_client, mock_embedder):
    """Create retriever with cache disabled."""
    return GraphRAGRetriever(
        client=mock_graph_client,
        embedder=mock_embedder,
        context_lines=5,
        cache_enabled=False
    )


@pytest.fixture
def retriever_falkordb(mock_falkordb_client, mock_embedder):
    """Create retriever with FalkorDB backend."""
    return GraphRAGRetriever(
        client=mock_falkordb_client,
        embedder=mock_embedder,
        context_lines=5,
        cache_enabled=True
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

    def test_initialization(self, mock_graph_client, mock_embedder):
        """Test retriever initializes correctly."""
        retriever = GraphRAGRetriever(
            client=mock_graph_client,
            embedder=mock_embedder,
            context_lines=10
        )

        assert retriever.client == mock_graph_client
        assert retriever.embedder == mock_embedder
        assert retriever.context_lines == 10

    def test_default_context_lines(self, mock_graph_client, mock_embedder):
        """Test default context_lines parameter."""
        retriever = GraphRAGRetriever(
            client=mock_graph_client,
            embedder=mock_embedder
        )

        assert retriever.context_lines == 5

    def test_cache_enabled_by_default(self, mock_graph_client, mock_embedder):
        """Test cache is enabled by default."""
        retriever = GraphRAGRetriever(
            client=mock_graph_client,
            embedder=mock_embedder
        )

        assert retriever._cache_enabled is True
        assert retriever._cache is not None
        assert retriever.cache_stats["enabled"] is True

    def test_cache_disabled(self, mock_graph_client, mock_embedder):
        """Test cache can be disabled."""
        retriever = GraphRAGRetriever(
            client=mock_graph_client,
            embedder=mock_embedder,
            cache_enabled=False
        )

        assert retriever._cache_enabled is False
        assert retriever._cache is None
        assert retriever.cache_stats["enabled"] is False

    def test_cache_custom_settings(self, mock_graph_client, mock_embedder):
        """Test cache with custom TTL and max_size."""
        retriever = GraphRAGRetriever(
            client=mock_graph_client,
            embedder=mock_embedder,
            cache_ttl=7200,
            cache_max_size=500
        )

        assert retriever._cache.ttl == 7200
        assert retriever._cache.max_size == 500


class TestVectorSearch:
    """Test vector similarity search functionality."""

    def test_vector_search_single_entity_type(self, retriever, mock_graph_client):
        """Test vector search for a single entity type."""
        query_embedding = [0.1] * 1536

        # Mock Neo4j response
        mock_graph_client.execute_query.return_value = [
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
        call_args = mock_graph_client.execute_query.call_args
        # execute_query(query, params) - params is second positional arg
        params = call_args[0][1]
        assert params["index_name"] == "function_embeddings"
        assert params["top_k"] == 5
        assert params["embedding"] == query_embedding

    def test_vector_search_multiple_entity_types(self, retriever, mock_graph_client):
        """Test vector search across multiple entity types."""
        query_embedding = [0.1] * 1536

        # Mock Neo4j responses for different entity types
        mock_graph_client.execute_query.side_effect = [
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
        assert mock_graph_client.execute_query.call_count == 2

    def test_vector_search_defaults_to_all_types(self, retriever, mock_graph_client):
        """Test vector search defaults to Function, Class, File."""
        query_embedding = [0.1] * 1536

        # Mock empty responses
        mock_graph_client.execute_query.return_value = []

        retriever._vector_search(query_embedding, top_k=5)

        # Should search all 3 default types
        assert mock_graph_client.execute_query.call_count == 3

    def test_vector_search_limits_results(self, retriever, mock_graph_client):
        """Test vector search respects top_k limit."""
        query_embedding = [0.1] * 1536

        # Mock many results from each type
        mock_graph_client.execute_query.side_effect = [
            [{"score": 0.9 - i*0.01, "qualified_name": f"func{i}", "element_id": f"{i}"} for i in range(10)],
            [{"score": 0.8 - i*0.01, "qualified_name": f"class{i}", "element_id": f"{i+100}"} for i in range(10)],
            [{"score": 0.7 - i*0.01, "qualified_name": f"file{i}", "element_id": f"{i+200}"} for i in range(10)],
        ]

        results = retriever._vector_search(query_embedding, top_k=5)

        # Should only return top 5 results
        assert len(results) == 5
        # Should be sorted by score
        assert results[0]["score"] >= results[1]["score"]

    def test_vector_search_handles_missing_index(self, retriever, mock_graph_client):
        """Test vector search handles missing index gracefully."""
        query_embedding = [0.1] * 1536

        # Mock exception for missing index
        mock_graph_client.execute_query.side_effect = Exception("Index not found")

        results = retriever._vector_search(query_embedding, top_k=5)

        # Should return empty list instead of crashing
        assert results == []


class TestRelatedEntities:
    """Test graph traversal for related entities."""

    def test_get_related_entities(self, retriever, mock_graph_client):
        """Test fetching related entities via graph traversal."""
        entity_id = "4:abc123:1"

        # Mock Neo4j response with relationships
        mock_graph_client.execute_query.return_value = [
            {"entity": "test.py::helper:30", "relationship": "CALLS"},
            {"entity": "utils.py::validate:10", "relationship": "USES"},
            {"entity": "test.py::TestClass:50", "relationship": "CONTAINS"}
        ]

        relationships = retriever._get_related_entities(entity_id)

        assert len(relationships) == 3
        assert {"entity": "test.py::helper:30", "relationship": "CALLS"} in relationships
        assert {"entity": "utils.py::validate:10", "relationship": "USES"} in relationships

        # Verify query parameters
        call_args = mock_graph_client.execute_query.call_args
        # execute_query(query, params) - params is second positional arg
        params = call_args[0][1]
        assert params["id"] == entity_id
        assert params["max_relationships"] == 20

    def test_get_related_entities_limit(self, retriever, mock_graph_client):
        """Test related entities respects limit."""
        entity_id = "4:abc123:1"

        # Mock many relationships
        mock_graph_client.execute_query.return_value = [
            {"entity": f"entity{i}", "relationship": "CALLS"}
            for i in range(50)
        ]

        relationships = retriever._get_related_entities(entity_id, max_relationships=10)

        # Should respect limit parameter
        call_args = mock_graph_client.execute_query.call_args
        # execute_query(query, params) - params is second positional arg
        params = call_args[0][1]
        assert params["max_relationships"] == 10

    def test_get_related_entities_filters_none(self, retriever, mock_graph_client):
        """Test related entities filters out None values."""
        entity_id = "4:abc123:1"

        # Mock response with None entities
        mock_graph_client.execute_query.return_value = [
            {"entity": "test.py::func:10", "relationship": "CALLS"},
            {"entity": None, "relationship": "USES"},
            {"entity": "test.py::class:20", "relationship": "CONTAINS"}
        ]

        relationships = retriever._get_related_entities(entity_id)

        # Should filter out None entities
        assert len(relationships) == 2
        assert all(r["entity"] is not None for r in relationships)

    def test_get_related_entities_handles_exception(self, retriever, mock_graph_client):
        """Test related entities handles query exceptions."""
        entity_id = "4:abc123:1"

        # Mock exception
        mock_graph_client.execute_query.side_effect = Exception("Query failed")

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

    def test_retrieve_basic(self, retriever, mock_embedder, mock_graph_client):
        """Test basic hybrid retrieval."""
        query = "How does authentication work?"

        # Mock embedder
        mock_embedder.embed_query.return_value = [0.1] * 1536

        # Mock vector search results
        mock_graph_client.execute_query.side_effect = [
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

    def test_retrieve_without_relationships(self, retriever, mock_embedder, mock_graph_client):
        """Test retrieval without fetching related entities."""
        query = "test query"

        mock_embedder.embed_query.return_value = [0.1] * 1536

        # Mock vector search
        mock_graph_client.execute_query.side_effect = [
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

    def test_retrieve_filters_entity_types(self, retriever, mock_embedder, mock_graph_client):
        """Test retrieval can filter by entity types."""
        query = "test query"

        mock_embedder.embed_query.return_value = [0.1] * 1536

        # Mock vector search (only Function type)
        mock_graph_client.execute_query.return_value = []

        file_content = "test"
        with patch("builtins.open", mock_open(read_data=file_content)):
            retriever.retrieve(query, entity_types=["Function"])

        # Should only search Function type
        # _vector_search is called once internally, and it calls execute_query for each entity type
        call_args = mock_graph_client.execute_query.call_args
        assert "function_embeddings" in str(call_args)


class TestGraphTraversal:
    """Test pure graph traversal retrieval."""

    def test_retrieve_by_path_basic(self, retriever, mock_graph_client):
        """Test graph traversal from starting entity."""
        start_entity = "auth.py::AuthService:10"

        # Mock graph traversal results
        mock_graph_client.execute_query.side_effect = [
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
        call_args = mock_graph_client.execute_query.call_args_list[0]
        # execute_query(query, params) - params is second positional arg
        params = call_args[0][1]
        assert params["start_qname"] == start_entity
        assert params["limit"] == 20

    def test_retrieve_by_path_multiple_hops(self, retriever, mock_graph_client):
        """Test graph traversal respects max_hops."""
        # Mock results at different distances
        mock_graph_client.execute_query.side_effect = [
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


class TestRAGCache:
    """Test RAGCache class functionality."""

    def test_cache_initialization(self):
        """Test cache initializes with correct defaults."""
        cache = RAGCache()

        assert cache.max_size == 1000
        assert cache.ttl == 3600
        assert cache.hits == 0
        assert cache.misses == 0
        assert cache.stats["size"] == 0

    def test_cache_custom_settings(self):
        """Test cache with custom settings."""
        cache = RAGCache(max_size=500, ttl=1800)

        assert cache.max_size == 500
        assert cache.ttl == 1800

    def test_cache_set_and_get(self):
        """Test basic cache set and get operations."""
        cache = RAGCache()

        # Create mock results
        results = [Mock(qualified_name="test.py::func:10")]

        # Set value
        cache.set("test query", 10, results)
        assert cache.stats["size"] == 1

        # Get value
        cached = cache.get("test query", 10)
        assert cached == results
        assert cache.hits == 1
        assert cache.misses == 0

    def test_cache_miss(self):
        """Test cache miss behavior."""
        cache = RAGCache()

        # Query not in cache
        result = cache.get("nonexistent query", 10)
        assert result is None
        assert cache.misses == 1
        assert cache.hits == 0

    def test_cache_key_normalization(self):
        """Test cache key normalizes queries."""
        cache = RAGCache()
        results = [Mock(qualified_name="test")]

        # Set with one format
        cache.set("  Test Query  ", 10, results)

        # Get with different format (same normalized)
        cached = cache.get("test query", 10)
        assert cached == results

    def test_cache_key_includes_top_k(self):
        """Test cache key includes top_k parameter."""
        cache = RAGCache()
        results_5 = [Mock(qualified_name="result5")]
        results_10 = [Mock(qualified_name="result10")]

        # Set with different top_k values
        cache.set("test query", 5, results_5)
        cache.set("test query", 10, results_10)

        # Should return different results based on top_k
        assert cache.get("test query", 5) == results_5
        assert cache.get("test query", 10) == results_10

    def test_cache_ttl_expiration(self):
        """Test cache entries expire after TTL."""
        cache = RAGCache(ttl=1)  # 1 second TTL
        results = [Mock(qualified_name="test")]

        cache.set("test query", 10, results)
        assert cache.get("test query", 10) == results

        # Wait for TTL to expire
        time.sleep(1.5)

        # Should return None after expiration
        result = cache.get("test query", 10)
        assert result is None
        assert cache.misses == 1  # Should count as miss

    def test_cache_lru_eviction(self):
        """Test LRU eviction when cache is at capacity."""
        cache = RAGCache(max_size=3)

        # Fill cache
        for i in range(3):
            cache.set(f"query{i}", 10, [Mock(name=f"result{i}")])

        assert cache.stats["size"] == 3

        # Add one more (should evict oldest)
        cache.set("query3", 10, [Mock(name="result3")])

        assert cache.stats["size"] == 3
        assert cache.get("query0", 10) is None  # Evicted
        assert cache.get("query1", 10) is not None
        assert cache.get("query3", 10) is not None

    def test_cache_lru_access_updates_order(self):
        """Test accessing an entry moves it to end of LRU."""
        cache = RAGCache(max_size=3)

        # Fill cache
        for i in range(3):
            cache.set(f"query{i}", 10, [Mock(name=f"result{i}")])

        # Access query0 (moves it to end)
        cache.get("query0", 10)

        # Add new entry (should evict query1, not query0)
        cache.set("query3", 10, [Mock(name="result3")])

        assert cache.get("query0", 10) is not None  # Not evicted
        assert cache.get("query1", 10) is None  # Evicted
        assert cache.get("query2", 10) is not None
        assert cache.get("query3", 10) is not None

    def test_cache_clear(self):
        """Test cache clear operation."""
        cache = RAGCache()

        # Add entries
        for i in range(5):
            cache.set(f"query{i}", 10, [Mock()])

        assert cache.stats["size"] == 5
        cache.hits = 10
        cache.misses = 5

        # Clear cache
        cache.clear()

        assert cache.stats["size"] == 0
        assert cache.hits == 0
        assert cache.misses == 0

    def test_cache_invalidate_expired(self):
        """Test invalidate_expired removes only expired entries."""
        cache = RAGCache(ttl=1)

        # Add entries
        cache.set("query1", 10, [Mock()])
        time.sleep(1.5)  # Let query1 expire
        cache.set("query2", 10, [Mock()])  # Fresh entry

        # Invalidate expired
        removed = cache.invalidate_expired()

        assert removed == 1
        assert cache.get("query1", 10) is None
        assert cache.get("query2", 10) is not None

    def test_cache_stats(self):
        """Test cache statistics."""
        cache = RAGCache(max_size=100, ttl=3600)

        # Perform operations
        cache.set("query1", 10, [Mock()])
        cache.set("query2", 10, [Mock()])
        cache.get("query1", 10)  # Hit
        cache.get("query1", 10)  # Hit
        cache.get("nonexistent", 10)  # Miss

        stats = cache.stats

        assert stats["size"] == 2
        assert stats["max_size"] == 100
        assert stats["ttl"] == 3600
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["hit_rate"] == pytest.approx(2/3, rel=0.01)


class TestCacheIntegration:
    """Test cache integration with GraphRAGRetriever."""

    def test_retrieve_uses_cache(self, retriever_with_cache, mock_embedder, mock_graph_client):
        """Test retrieve uses cached results on second call."""
        query = "test query"

        mock_embedder.embed_query.return_value = [0.1] * 1536
        mock_graph_client.execute_query.side_effect = [
            [{"element_id": "1", "qualified_name": "test.py::func:1", "name": "func",
              "entity_type": "Function", "docstring": "Test", "file_path": "test.py",
              "line_start": 1, "line_end": 5, "score": 0.9}],
            [], [],  # Empty class and file results
            []  # Related entities
        ]

        with patch("builtins.open", mock_open(read_data="def func():\n    pass\n")):
            # First call - cache miss
            results1 = retriever_with_cache.retrieve(query, top_k=5)

        # Reset mock for second call
        mock_graph_client.execute_query.reset_mock()

        with patch("builtins.open", mock_open(read_data="def func():\n    pass\n")):
            # Second call - should use cache
            results2 = retriever_with_cache.retrieve(query, top_k=5)

        # Neo4j should not be called on second request (cache hit)
        mock_graph_client.execute_query.assert_not_called()

        # Results should be the same
        assert len(results1) == len(results2)

        # Verify cache stats
        assert retriever_with_cache.cache_stats["hits"] == 1
        assert retriever_with_cache.cache_stats["misses"] == 1

    def test_retrieve_bypass_cache(self, retriever_with_cache, mock_embedder, mock_graph_client):
        """Test retrieve can bypass cache with use_cache=False."""
        query = "test query"

        mock_embedder.embed_query.return_value = [0.1] * 1536
        mock_graph_client.execute_query.return_value = []

        # First call - populates cache
        retriever_with_cache.retrieve(query, top_k=5, use_cache=True)

        # Reset mock
        mock_graph_client.execute_query.reset_mock()

        # Second call with use_cache=False - should hit database
        retriever_with_cache.retrieve(query, top_k=5, use_cache=False)

        # Neo4j should be called even though result is cached
        assert mock_graph_client.execute_query.called

    def test_retrieve_no_cache(self, retriever_no_cache, mock_embedder, mock_graph_client):
        """Test retrieve works when cache is disabled."""
        query = "test query"

        mock_embedder.embed_query.return_value = [0.1] * 1536
        mock_graph_client.execute_query.return_value = []

        # First call
        retriever_no_cache.retrieve(query, top_k=5)

        # Reset mock
        mock_graph_client.execute_query.reset_mock()

        # Second call - should hit database (no cache)
        retriever_no_cache.retrieve(query, top_k=5)

        # Neo4j should be called on every request
        assert mock_graph_client.execute_query.called

    def test_invalidate_cache(self, retriever_with_cache, mock_embedder, mock_graph_client):
        """Test cache invalidation."""
        query = "test query"

        mock_embedder.embed_query.return_value = [0.1] * 1536
        mock_graph_client.execute_query.return_value = []

        # Populate cache
        retriever_with_cache.retrieve(query, top_k=5)
        assert retriever_with_cache.cache_stats["size"] == 1

        # Invalidate cache
        retriever_with_cache.invalidate_cache()

        assert retriever_with_cache.cache_stats["size"] == 0
        assert retriever_with_cache.cache_stats["hits"] == 0
        assert retriever_with_cache.cache_stats["misses"] == 0

    def test_different_top_k_not_cached(self, retriever_with_cache, mock_embedder, mock_graph_client):
        """Test same query with different top_k is not a cache hit."""
        query = "test query"

        mock_embedder.embed_query.return_value = [0.1] * 1536
        mock_graph_client.execute_query.return_value = []

        # First call with top_k=5
        retriever_with_cache.retrieve(query, top_k=5)

        # Reset mock
        mock_graph_client.execute_query.reset_mock()

        # Second call with different top_k - should miss cache
        retriever_with_cache.retrieve(query, top_k=10)

        # Neo4j should be called (cache miss due to different top_k)
        assert mock_graph_client.execute_query.called

        # Should have 2 cache entries now
        assert retriever_with_cache.cache_stats["size"] == 2


class TestFalkorDBIntegration:
    """Test cache integration with FalkorDB backend."""

    def test_falkordb_detection(self, retriever_falkordb):
        """Test FalkorDB backend is detected correctly."""
        assert retriever_falkordb.is_falkordb is True

    def test_falkordb_cache_enabled(self, retriever_falkordb):
        """Test cache is enabled with FalkorDB backend."""
        assert retriever_falkordb._cache_enabled is True
        assert retriever_falkordb._cache is not None
        assert retriever_falkordb.cache_stats["enabled"] is True

    def test_falkordb_cache_works(self, retriever_falkordb, mock_embedder, mock_falkordb_client):
        """Test cache works correctly with FalkorDB backend."""
        query = "test query"

        mock_embedder.embed_query.return_value = [0.1] * 1536
        # FalkorDB uses different vector search syntax but cache doesn't care
        mock_falkordb_client.execute_query.side_effect = [
            [{"element_id": 1, "qualified_name": "test.py::func:1", "name": "func",
              "entity_type": "Function", "docstring": "Test", "file_path": "test.py",
              "line_start": 1, "line_end": 5, "score": 0.9}],
            [], [],  # Empty class and file results
            []  # Related entities
        ]

        with patch("builtins.open", mock_open(read_data="def func():\n    pass\n")):
            # First call - cache miss
            results1 = retriever_falkordb.retrieve(query, top_k=5)

        # Reset mock
        mock_falkordb_client.execute_query.reset_mock()

        with patch("builtins.open", mock_open(read_data="def func():\n    pass\n")):
            # Second call - should use cache
            results2 = retriever_falkordb.retrieve(query, top_k=5)

        # FalkorDB should not be called on second request (cache hit)
        mock_falkordb_client.execute_query.assert_not_called()

        # Verify cache stats
        assert retriever_falkordb.cache_stats["hits"] == 1
        assert retriever_falkordb.cache_stats["misses"] == 1

    def test_falkordb_vector_search_uses_correct_syntax(self, retriever_falkordb, mock_falkordb_client):
        """Test FalkorDB uses correct vector search syntax (id() not elementId())."""
        query_embedding = [0.1] * 1536

        mock_falkordb_client.execute_query.return_value = []

        retriever_falkordb._vector_search(query_embedding, top_k=5, entity_types=["Function"])

        # Verify the query uses FalkorDB syntax (id() and vecf32())
        call_args = mock_falkordb_client.execute_query.call_args
        query = call_args[0][0]  # First positional arg is the query string

        assert "id(node)" in query  # FalkorDB uses id() not elementId()
        assert "vecf32(" in query  # FalkorDB requires vecf32() wrapper
        assert "db.idx.vector.queryNodes" in query  # FalkorDB vector search procedure
