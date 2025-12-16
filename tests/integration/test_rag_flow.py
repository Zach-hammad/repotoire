"""Integration tests for RAG (Retrieval-Augmented Generation) flow."""

import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from repotoire.graph import Neo4jClient
from repotoire.pipeline.ingestion import IngestionPipeline
from repotoire.ai import CodeEmbedder
from repotoire.ai.retrieval import GraphRAGRetriever


# Check if OpenAI API key is available
OPENAI_API_KEY_AVAILABLE = bool(os.getenv("OPENAI_API_KEY"))
requires_openai = pytest.mark.skipif(
    not OPENAI_API_KEY_AVAILABLE,
    reason="OPENAI_API_KEY environment variable not set"
)


@pytest.fixture(scope="module")
def test_neo4j_client():
    """Create a test Neo4j client. Requires Neo4j running on default ports."""
    try:
        from repotoire.graph.schema import GraphSchema

        client = Neo4jClient(
            uri=os.getenv("REPOTOIRE_NEO4J_URI", "bolt://localhost:7687"),
            username="neo4j",
            password=os.getenv("REPOTOIRE_NEO4J_PASSWORD", "password")
        )
        # Clear any existing data
        client.clear_graph()

        # Initialize schema with vector indexes for RAG tests
        schema = GraphSchema(client)
        schema.initialize(enable_vector_search=True)

        yield client
        client.close()
    except Exception as e:
        pytest.skip(f"Neo4j test database not available: {e}")


@pytest.fixture
def sample_codebase_for_rag():
    """Create a temporary directory with sample Python files for RAG testing."""
    temp_dir = tempfile.mkdtemp()
    temp_path = Path(temp_dir)

    # Create authentication module
    (temp_path / "authentication.py").write_text("""
\"\"\"User authentication and authorization module.\"\"\"

import hashlib
import jwt


def hash_password(password: str) -> str:
    \"\"\"Hash password using SHA-256.

    Args:
        password: Plain text password

    Returns:
        Hashed password as hex string
    \"\"\"
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password: str, hashed: str) -> bool:
    \"\"\"Verify password against hash.

    Args:
        password: Plain text password to verify
        hashed: Expected password hash

    Returns:
        True if password matches hash
    \"\"\"
    return hash_password(password) == hashed


class AuthManager:
    \"\"\"Manages user authentication and JWT tokens.\"\"\"

    def __init__(self, secret_key: str):
        \"\"\"Initialize auth manager.

        Args:
            secret_key: Secret key for JWT signing
        \"\"\"
        self.secret_key = secret_key

    def create_token(self, user_id: str) -> str:
        \"\"\"Create JWT token for user.

        Args:
            user_id: User identifier

        Returns:
            Signed JWT token
        \"\"\"
        return jwt.encode({"user_id": user_id}, self.secret_key, algorithm="HS256")

    def verify_token(self, token: str) -> dict:
        \"\"\"Verify and decode JWT token.

        Args:
            token: JWT token to verify

        Returns:
            Decoded token payload
        \"\"\"
        return jwt.decode(token, self.secret_key, algorithms=["HS256"])
""")

    # Create database module
    (temp_path / "database.py").write_text("""
\"\"\"Database connection and query utilities.\"\"\"

import sqlite3
from typing import List, Dict, Any


class DatabaseConnection:
    \"\"\"Manages SQLite database connections.\"\"\"

    def __init__(self, db_path: str):
        \"\"\"Initialize database connection.

        Args:
            db_path: Path to SQLite database file
        \"\"\"
        self.db_path = db_path
        self.connection = None

    def connect(self):
        \"\"\"Establish database connection.\"\"\"
        self.connection = sqlite3.connect(self.db_path)

    def execute_query(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        \"\"\"Execute SQL query and return results.

        Args:
            query: SQL query string
            params: Query parameters

        Returns:
            List of result rows as dictionaries
        \"\"\"
        cursor = self.connection.cursor()
        cursor.execute(query, params)
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def close(self):
        \"\"\"Close database connection.\"\"\"
        if self.connection:
            self.connection.close()


def create_user_table(db: DatabaseConnection):
    \"\"\"Create users table in database.

    Args:
        db: Database connection instance
    \"\"\"
    db.execute_query('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE,
            password_hash TEXT,
            email TEXT
        )
    ''')
""")

    # Create API module
    (temp_path / "api.py").write_text("""
\"\"\"REST API endpoints for user management.\"\"\"

from fastapi import FastAPI, HTTPException, Depends
from authentication import AuthManager, verify_password
from database import DatabaseConnection


app = FastAPI()
auth_manager = AuthManager(secret_key="my-secret")


def get_db() -> DatabaseConnection:
    \"\"\"Dependency injection for database connection.\"\"\"
    db = DatabaseConnection("users.db")
    db.connect()
    try:
        yield db
    finally:
        db.close()


@app.post("/login")
def login(username: str, password: str, db: DatabaseConnection = Depends(get_db)):
    \"\"\"Authenticate user and return JWT token.

    Args:
        username: User's username
        password: User's password
        db: Database connection

    Returns:
        JWT token for authenticated user

    Raises:
        HTTPException: If authentication fails
    \"\"\"
    users = db.execute_query(
        "SELECT * FROM users WHERE username = ?",
        (username,)
    )

    if not users:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user = users[0]

    if not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = auth_manager.create_token(user["id"])
    return {"token": token}
""")

    yield temp_path

    # Cleanup
    for file in temp_path.glob("*.py"):
        file.unlink()
    temp_path.rmdir()


@pytest.fixture
def ingested_rag_codebase(test_neo4j_client, sample_codebase_for_rag):
    """Ingest sample codebase with embeddings for RAG testing."""
    test_neo4j_client.clear_graph()

    # Ingest with embedding generation enabled
    pipeline = IngestionPipeline(
        str(sample_codebase_for_rag),
        test_neo4j_client,
        generate_embeddings=True
    )
    pipeline.ingest(patterns=["**/*.py"])

    return sample_codebase_for_rag


@requires_openai
class TestIngestionWithEmbeddings:
    """Test ingestion pipeline with embedding generation."""

    def test_embeddings_generated_during_ingestion(self, test_neo4j_client, sample_codebase_for_rag):
        """Test that embeddings are generated when flag is enabled."""
        test_neo4j_client.clear_graph()

        # Ingest with embeddings
        pipeline = IngestionPipeline(
            str(sample_codebase_for_rag),
            test_neo4j_client,
            generate_embeddings=True
        )
        pipeline.ingest(patterns=["**/*.py"])

        # Check that entities have embeddings
        query = """
        MATCH (n)
        WHERE (n:Function OR n:Class OR n:File) AND n.embedding IS NOT NULL
        RETURN count(n) as embedded_count
        """
        results = test_neo4j_client.execute_query(query)
        embedded_count = results[0]["embedded_count"]

        assert embedded_count > 0, "No embeddings were generated"

        # Verify embedding vector dimensions (should be 1536 for text-embedding-3-small)
        query = """
        MATCH (n:Function)
        WHERE n.embedding IS NOT NULL
        RETURN n.embedding as embedding
        LIMIT 1
        """
        results = test_neo4j_client.execute_query(query)
        if results:
            embedding = results[0]["embedding"]
            assert len(embedding) == 1536, f"Expected 1536 dimensions, got {len(embedding)}"

    def test_ingestion_without_embeddings(self, test_neo4j_client, sample_codebase_for_rag):
        """Test that ingestion works without embedding generation."""
        test_neo4j_client.clear_graph()

        # Ingest without embeddings
        pipeline = IngestionPipeline(
            str(sample_codebase_for_rag),
            test_neo4j_client,
            generate_embeddings=False
        )
        pipeline.ingest(patterns=["**/*.py"])

        # Verify entities exist but no embeddings
        stats = test_neo4j_client.get_stats()
        assert stats["total_functions"] > 0

        # Check no embeddings
        query = """
        MATCH (n)
        WHERE (n:Function OR n:Class OR n:File) AND n.embedding IS NOT NULL
        RETURN count(n) as embedded_count
        """
        results = test_neo4j_client.execute_query(query)
        embedded_count = results[0]["embedded_count"]

        assert embedded_count == 0, "Embeddings should not be generated when flag is False"

    def test_idempotent_embedding_generation(self, test_neo4j_client, sample_codebase_for_rag):
        """Test that running embedding generation twice doesn't duplicate."""
        test_neo4j_client.clear_graph()

        # First ingestion with embeddings
        pipeline = IngestionPipeline(
            str(sample_codebase_for_rag),
            test_neo4j_client,
            generate_embeddings=True
        )
        pipeline.ingest(patterns=["**/*.py"])

        # Count embeddings
        query = """
        MATCH (n)
        WHERE (n:Function OR n:Class OR n:File) AND n.embedding IS NOT NULL
        RETURN count(n) as embedded_count
        """
        results = test_neo4j_client.execute_query(query)
        first_count = results[0]["embedded_count"]

        # Second ingestion (should skip already embedded entities)
        pipeline2 = IngestionPipeline(
            str(sample_codebase_for_rag),
            test_neo4j_client,
            generate_embeddings=True
        )
        pipeline2.ingest(patterns=["**/*.py"])

        # Count should remain the same
        results = test_neo4j_client.execute_query(query)
        second_count = results[0]["embedded_count"]

        assert first_count == second_count, "Embedding generation should be idempotent"


@requires_openai
class TestGraphRAGRetriever:
    """Test GraphRAGRetriever functionality."""

    def test_vector_similarity_search(self, test_neo4j_client, ingested_rag_codebase):
        """Test vector similarity search for code entities."""
        embedder = CodeEmbedder()
        retriever = GraphRAGRetriever(
            neo4j_client=test_neo4j_client,
            embedder=embedder
        )

        # Search for authentication-related code
        results = retriever.retrieve(
            query="How do I authenticate users with passwords?",
            top_k=5,
            include_related=False
        )

        assert len(results) > 0, "Should find relevant results"

        # Check that results are related to authentication
        result_names = [r.qualified_name.lower() for r in results]
        assert any("auth" in name or "password" in name for name in result_names)

        # Verify similarity scores are in valid range
        for result in results:
            assert 0 <= result.similarity_score <= 1

    def test_hybrid_search_with_graph_traversal(self, test_neo4j_client, ingested_rag_codebase):
        """Test hybrid search that includes graph-related entities."""
        embedder = CodeEmbedder()
        retriever = GraphRAGRetriever(
            neo4j_client=test_neo4j_client,
            embedder=embedder
        )

        # Search with related entities
        results = retriever.retrieve(
            query="database connections",
            top_k=5,
            include_related=True
        )

        assert len(results) > 0, "Should find relevant results"

        # Some results should have relationships populated
        has_relationships = any(len(r.relationships) > 0 for r in results)
        assert has_relationships, "Hybrid search should include relationships"

    def test_entity_type_filtering(self, test_neo4j_client, ingested_rag_codebase):
        """Test filtering results by entity type."""
        embedder = CodeEmbedder()
        retriever = GraphRAGRetriever(
            neo4j_client=test_neo4j_client,
            embedder=embedder
        )

        # Search only for classes
        results = retriever.retrieve(
            query="authentication",
            top_k=10,
            entity_types=["Class"]
        )

        # All results should be classes
        for result in results:
            assert result.entity_type == "Class"

    def test_empty_query_returns_empty_results(self, test_neo4j_client, ingested_rag_codebase):
        """Test that empty or very short queries return no results."""
        embedder = CodeEmbedder()
        retriever = GraphRAGRetriever(
            neo4j_client=test_neo4j_client,
            embedder=embedder
        )

        # Empty query
        results = retriever.retrieve(query="", top_k=5)
        assert len(results) == 0

    def test_retrieval_includes_code_and_docstrings(self, test_neo4j_client, ingested_rag_codebase):
        """Test that retrieval results include code and docstrings."""
        embedder = CodeEmbedder()
        retriever = GraphRAGRetriever(
            neo4j_client=test_neo4j_client,
            embedder=embedder
        )

        results = retriever.retrieve(
            query="hash password function",
            top_k=5
        )

        assert len(results) > 0

        # Find the hash_password function
        hash_func = next((r for r in results if "hash_password" in r.qualified_name), None)

        if hash_func:
            assert hash_func.code is not None and len(hash_func.code) > 0
            assert hash_func.docstring is not None and len(hash_func.docstring) > 0
            assert "SHA-256" in hash_func.docstring


@requires_openai
class TestAPIEndpoints:
    """Test FastAPI RAG endpoints."""

    @pytest.fixture
    def api_client(self, test_neo4j_client, ingested_rag_codebase):
        """Create FastAPI test client with mocked dependencies."""
        from repotoire.api.app import app
        from repotoire.api.routes import code

        # Mock the get_neo4j_client dependency
        def override_get_neo4j_client():
            return test_neo4j_client

        app.dependency_overrides[code.get_neo4j_client] = override_get_neo4j_client

        client = TestClient(app)
        yield client

        # Cleanup
        app.dependency_overrides.clear()

    def test_search_endpoint(self, api_client):
        """Test POST /api/v1/code/search endpoint."""
        response = api_client.post(
            "/api/v1/code/search",
            json={
                "query": "authentication password hashing",
                "top_k": 5,
                "include_related": True
            }
        )

        assert response.status_code == 200

        data = response.json()
        assert "results" in data
        assert "total" in data
        assert "execution_time_ms" in data
        assert data["search_strategy"] in ["hybrid", "vector"]

        # Should find relevant results
        assert len(data["results"]) > 0

        # Check result structure
        result = data["results"][0]
        assert "entity_type" in result
        assert "qualified_name" in result
        assert "similarity_score" in result
        assert "file_path" in result

    def test_search_endpoint_validation(self, api_client):
        """Test search endpoint input validation."""
        # Query too short
        response = api_client.post(
            "/api/v1/code/search",
            json={"query": "ab"}
        )
        assert response.status_code == 422  # Validation error

        # Invalid top_k
        response = api_client.post(
            "/api/v1/code/search",
            json={"query": "test query", "top_k": 100}
        )
        assert response.status_code == 422

    @patch('repotoire.api.routes.code.OpenAI')
    def test_ask_endpoint(self, mock_openai, api_client):
        """Test POST /api/v1/code/ask endpoint."""
        # Mock OpenAI responses
        mock_client = Mock()
        mock_openai.return_value = mock_client

        mock_answer_response = Mock()
        mock_answer_response.choices = [Mock(message=Mock(content="The authentication system uses JWT tokens and password hashing."))]

        mock_followup_response = Mock()
        mock_followup_response.choices = [Mock(message=Mock(content="- How are JWT tokens verified?\n- What hashing algorithm is used?"))]

        mock_client.chat.completions.create.side_effect = [
            mock_answer_response,
            mock_followup_response
        ]

        response = api_client.post(
            "/api/v1/code/ask",
            json={
                "question": "How does the authentication system work?",
                "top_k": 5
            }
        )

        assert response.status_code == 200

        data = response.json()
        assert "answer" in data
        assert "sources" in data
        assert "confidence" in data
        assert "follow_up_questions" in data
        assert "execution_time_ms" in data

        # Should have generated answer
        assert len(data["answer"]) > 0

        # Should have sources
        assert len(data["sources"]) > 0

        # Should have follow-up questions
        assert len(data["follow_up_questions"]) > 0

    def test_embeddings_status_endpoint(self, api_client):
        """Test GET /api/v1/code/embeddings/status endpoint."""
        response = api_client.get("/api/v1/code/embeddings/status")

        assert response.status_code == 200

        data = response.json()
        assert "total_entities" in data
        assert "embedded_entities" in data
        assert "embedding_coverage" in data
        assert "functions_embedded" in data
        assert "classes_embedded" in data
        assert "files_embedded" in data
        assert "model_used" in data

        # Should have some embedded entities
        assert data["embedded_entities"] > 0
        assert data["embedding_coverage"] > 0
        assert data["model_used"] == "text-embedding-3-small"

    def test_api_health_check(self, api_client):
        """Test health check endpoint."""
        response = api_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


@requires_openai
class TestRAGPerformance:
    """Performance benchmarks for RAG operations."""

    def test_embedding_generation_performance(self, test_neo4j_client, sample_codebase_for_rag):
        """Benchmark embedding generation speed."""
        import time

        test_neo4j_client.clear_graph()

        # Ingest without embeddings first
        pipeline = IngestionPipeline(
            str(sample_codebase_for_rag),
            test_neo4j_client,
            generate_embeddings=False
        )
        pipeline.ingest(patterns=["**/*.py"])

        # Measure embedding generation time
        pipeline_with_embeddings = IngestionPipeline(
            str(sample_codebase_for_rag),
            test_neo4j_client,
            generate_embeddings=True
        )

        start = time.time()
        entities_embedded = pipeline_with_embeddings._generate_embeddings_for_all_entities()
        duration = time.time() - start

        assert entities_embedded > 0

        # Should complete in reasonable time (< 30 seconds for small codebase)
        assert duration < 30, f"Embedding generation took {duration:.2f}s, expected < 30s"

        # Log performance for monitoring
        print(f"\nEmbedding performance: {entities_embedded} entities in {duration:.2f}s ({entities_embedded/duration:.1f} entities/sec)")

    def test_retrieval_performance(self, test_neo4j_client, ingested_rag_codebase):
        """Benchmark retrieval speed."""
        import time

        embedder = CodeEmbedder()
        retriever = GraphRAGRetriever(
            neo4j_client=test_neo4j_client,
            embedder=embedder
        )

        queries = [
            "authentication system",
            "database connection",
            "JWT token generation",
            "password hashing"
        ]

        durations = []
        for query in queries:
            start = time.time()
            results = retriever.retrieve(query, top_k=10)
            duration = time.time() - start
            durations.append(duration)

            assert len(results) > 0, f"No results for query: {query}"

        avg_duration = sum(durations) / len(durations)

        # Retrieval should be fast (< 2 seconds per query on average)
        assert avg_duration < 2, f"Average retrieval took {avg_duration:.2f}s, expected < 2s"

        print(f"\nRetrieval performance: {len(queries)} queries, avg {avg_duration:.3f}s per query")

    def test_vector_index_performance(self, test_neo4j_client, ingested_rag_codebase):
        """Test that vector indexes are being used for efficient search."""
        # Check if vector index exists
        query = """
        SHOW INDEXES
        YIELD name, type, labelsOrTypes, properties
        WHERE type = 'VECTOR'
        RETURN name, labelsOrTypes, properties
        """

        try:
            results = test_neo4j_client.execute_query(query)

            # Should have vector indexes for Function, Class, and/or File
            index_labels = [r["labelsOrTypes"] for r in results]

            # At least one vector index should exist
            assert len(results) > 0, "No vector indexes found - search will be slow"

            print(f"\nVector indexes found: {len(results)}")
            for result in results:
                print(f"  - {result['name']}: {result['labelsOrTypes']}")

        except Exception as e:
            # SHOW INDEXES might not be supported in older Neo4j versions
            pytest.skip(f"Cannot verify vector indexes: {e}")
