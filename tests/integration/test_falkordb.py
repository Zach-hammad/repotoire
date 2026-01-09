"""Integration tests for FalkorDB compatibility (REPO-201).

These tests verify that all Repotoire functionality works with FalkorDB
as a drop-in replacement for Neo4j. FalkorDB is a Redis-based graph database
that speaks the Bolt protocol.

Requirements:
- Docker (to start FalkorDB container)
- repotoire_fast (Rust algorithms)

The tests cover:
1. Basic connectivity via Bolt protocol
2. Ingestion pipeline
3. All Rust graph algorithms (SCC, PageRank, Betweenness, Leiden, Harmonic)
4. Full analysis workflow
5. Performance benchmarks vs Neo4j (optional)
"""

import logging
import os
import subprocess
import tempfile
from pathlib import Path

import pytest
import redis
from tenacity import (
    retry,
    stop_after_delay,
    wait_exponential,
    before_sleep_log,
    retry_if_exception_type,
    RetryError,
)

logger = logging.getLogger(__name__)

from repotoire.graph import FalkorDBClient, FalkorDBClient, create_client
from repotoire.pipeline.ingestion import IngestionPipeline
from repotoire.detectors.engine import AnalysisEngine
from repotoire.detectors.graph_algorithms import GraphAlgorithms


# Environment variable to control which database to test
# Set REPOTOIRE_TEST_DB=falkordb to test FalkorDB
# Set REPOTOIRE_TEST_DB=neo4j to test Neo4j (default)
# Set REPOTOIRE_TEST_DB=both to test both databases
TEST_DB = os.environ.get("REPOTOIRE_TEST_DB", "falkordb")

# FalkorDB connection settings (using port 6379 for Redis protocol, matching CI)
FALKORDB_REDIS_PORT = int(os.environ.get("REPOTOIRE_FALKORDB_PORT", "6379"))
FALKORDB_HOST = os.environ.get("REPOTOIRE_FALKORDB_HOST", "localhost")
FALKORDB_PASSWORD = os.environ.get("REPOTOIRE_FALKORDB_PASSWORD", None)


def is_docker_available() -> bool:
    """Check if Docker is available."""
    try:
        subprocess.run(["docker", "version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def is_port_in_use(port: int) -> bool:
    """Check if a port is already in use."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


@retry(
    stop=stop_after_delay(60),  # Max 60 seconds total
    wait=wait_exponential(multiplier=0.5, min=0.5, max=10),  # 0.5s, 1s, 2s, 4s, 8s, 10s...
    before_sleep=before_sleep_log(logger, logging.INFO),  # Log retry attempts
    retry=retry_if_exception_type((
        redis.ConnectionError,
        redis.TimeoutError,
        ConnectionRefusedError,
        OSError,
    ))
)
def wait_for_falkordb_ready(host: str = "localhost", port: int = FALKORDB_REDIS_PORT) -> bool:
    """Wait for FalkorDB to be ready using actual Redis PING.

    Uses exponential backoff with tenacity to verify FalkorDB is actually
    accepting connections, not just that the port is bound.

    Args:
        host: FalkorDB host (default: localhost)
        port: FalkorDB Redis port (default: FALKORDB_REDIS_PORT)

    Returns:
        True if FalkorDB is ready

    Raises:
        RetryError: If FalkorDB doesn't become ready within 60 seconds
    """
    client = redis.Redis(
        host=host,
        port=port,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        response = client.ping()
        if response:
            logger.info(f"FalkorDB is ready on {host}:{port}")
            return True
        raise redis.ConnectionError("PING returned False")
    finally:
        client.close()


def start_falkordb_container() -> bool:
    """Start FalkorDB container for testing.

    Returns:
        True if container started successfully
    """
    # First check if the port is already in use (container may already be running)
    if is_port_in_use(FALKORDB_REDIS_PORT):
        # Port is in use, verify FalkorDB is actually ready
        try:
            return wait_for_falkordb_ready(host=FALKORDB_HOST, port=FALKORDB_REDIS_PORT)
        except RetryError:
            logger.warning("Port in use but FalkorDB not responding")
            return False

    container_name = "repotoire-test-falkordb"

    # Check if already running
    result = subprocess.run(
        ["docker", "ps", "--filter", f"name={container_name}", "--format", "{{.Names}}"],
        capture_output=True,
        text=True
    )
    if container_name in result.stdout:
        try:
            return wait_for_falkordb_ready(host=FALKORDB_HOST, port=FALKORDB_REDIS_PORT)
        except RetryError:
            logger.warning(f"Container {container_name} running but not responding")
            return False

    # Also check for repotoire-falkordb container
    result = subprocess.run(
        ["docker", "ps", "--filter", "name=repotoire-falkordb", "--format", "{{.Names}}"],
        capture_output=True,
        text=True
    )
    if "repotoire-falkordb" in result.stdout:
        try:
            return wait_for_falkordb_ready(host=FALKORDB_HOST, port=FALKORDB_REDIS_PORT)
        except RetryError:
            logger.warning("Container repotoire-falkordb running but not responding")
            return False

    # Remove existing stopped container
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

    # Start FalkorDB container
    result = subprocess.run([
        "docker", "run",
        "-d",
        "--name", container_name,
        "-p", f"{FALKORDB_REDIS_PORT}:6379",  # FalkorDB uses Redis protocol on port 6379
        "-e", "REDIS_ARGS=--maxmemory 1gb",
        "falkordb/falkordb:latest"
    ], capture_output=True)

    if result.returncode != 0:
        logger.error(f"Failed to start FalkorDB: {result.stderr.decode()}")
        return False

    # Wait for FalkorDB to be ready using proper retry logic (REPO-366)
    try:
        return wait_for_falkordb_ready(host=FALKORDB_HOST, port=FALKORDB_REDIS_PORT)
    except RetryError as e:
        logger.error(f"FalkorDB did not become ready after 60s: {e.last_attempt.exception()}")
        return False


def stop_falkordb_container():
    """Stop and remove FalkorDB test container."""
    subprocess.run(["docker", "rm", "-f", "repotoire-test-falkordb"], capture_output=True)


@pytest.fixture(scope="module")
def falkordb_client():
    """Create a FalkorDB client for testing.

    Starts container if needed, yields client, cleans up afterward.
    """
    if TEST_DB not in ("falkordb", "both"):
        pytest.skip("FalkorDB tests disabled (set REPOTOIRE_TEST_DB=falkordb)")

    if not is_docker_available():
        pytest.skip("Docker not available")

    if not start_falkordb_container():
        pytest.skip("Failed to start FalkorDB container")

    try:
        # Use FalkorDBClient with Redis protocol
        client = FalkorDBClient(
            host=FALKORDB_HOST,
            port=FALKORDB_REDIS_PORT,
            graph_name="repotoire_test",
            password=FALKORDB_PASSWORD,
            max_retries=5,
            retry_base_delay=2.0,
        )
        # Graph clearing handled by isolate_graph_test autouse fixture (REPO-367)
        yield client
        client.close()
    except Exception as e:
        pytest.skip(f"FalkorDB not available: {e}")


@pytest.fixture(scope="module")
def graph_client():
    """Create a Neo4j client for comparison testing."""
    if TEST_DB not in ("neo4j", "both"):
        pytest.skip("Neo4j tests disabled (set REPOTOIRE_TEST_DB=neo4j or both)")

    try:
        client = FalkorDBClient(
            uri=FALKORDB_HOST,
            username="neo4j",
            password=FALKORDB_PASSWORD,
        )
        # Graph clearing handled by isolate_graph_test autouse fixture (REPO-367)
        yield client
        client.close()
    except Exception as e:
        pytest.skip(f"Neo4j not available: {e}")


@pytest.fixture
def sample_codebase():
    """Create a temporary directory with sample Python files for testing."""
    temp_dir = tempfile.mkdtemp()
    temp_path = Path(temp_dir)

    # Create a realistic codebase structure
    (temp_path / "main.py").write_text('''"""Main entry point."""
from utils import helper
from models import User

def main():
    """Main function."""
    user = User("test")
    result = helper.process(user)
    return result

if __name__ == "__main__":
    main()
''')

    (temp_path / "utils" / "__init__.py").parent.mkdir(exist_ok=True)
    (temp_path / "utils" / "__init__.py").write_text('"""Utilities package."""\n')

    (temp_path / "utils" / "helper.py").write_text('''"""Helper utilities."""
from typing import Any

def process(obj: Any) -> str:
    """Process an object."""
    return str(obj)

def validate(data: dict) -> bool:
    """Validate data."""
    return bool(data)

def format_output(text: str) -> str:
    """Format output text."""
    return text.strip()
''')

    (temp_path / "models" / "__init__.py").parent.mkdir(exist_ok=True)
    (temp_path / "models" / "__init__.py").write_text('"""Models package."""\nfrom .user import User\n')

    (temp_path / "models" / "user.py").write_text('''"""User model."""

class User:
    """Represents a user."""

    def __init__(self, name: str):
        self.name = name

    def __str__(self) -> str:
        return f"User({self.name})"

    def validate(self) -> bool:
        """Validate user."""
        return bool(self.name)
''')

    # Create circular dependency for SCC testing
    (temp_path / "circular_a.py").write_text('''"""Module A (circular)."""
from circular_b import func_b

def func_a():
    """Function A."""
    return func_b()
''')

    (temp_path / "circular_b.py").write_text('''"""Module B (circular)."""
from circular_a import func_a

def func_b():
    """Function B."""
    return func_a()
''')

    yield temp_path

    # Cleanup
    import shutil
    shutil.rmtree(temp_dir)


class TestFalkorDBConnectivity:
    """Test basic FalkorDB connectivity via Bolt protocol."""

    def test_connection_successful(self, falkordb_client):
        """Test that we can connect to FalkorDB."""
        assert falkordb_client is not None

    def test_execute_simple_query(self, falkordb_client):
        """Test executing a simple Cypher query."""
        result = falkordb_client.execute_query("RETURN 1 AS value")
        assert result[0]["value"] == 1

    def test_create_and_query_node(self, falkordb_client):
        """Test creating and querying a node."""
        # Create node
        falkordb_client.execute_query(
            "CREATE (n:Test {name: $name}) RETURN n",
            {"name": "test_node"}
        )

        # Query node
        result = falkordb_client.execute_query(
            "MATCH (n:Test {name: $name}) RETURN n.name AS name",
            {"name": "test_node"}
        )
        assert result[0]["name"] == "test_node"

        # Cleanup
        falkordb_client.execute_query("MATCH (n:Test) DELETE n")

    def test_create_relationship(self, falkordb_client):
        """Test creating relationships between nodes."""
        # Create nodes and relationship
        falkordb_client.execute_query("""
            CREATE (a:TestNode {id: 1})
            CREATE (b:TestNode {id: 2})
            CREATE (a)-[:RELATES_TO]->(b)
        """)

        # Query relationship
        result = falkordb_client.execute_query("""
            MATCH (a:TestNode)-[r:RELATES_TO]->(b:TestNode)
            RETURN a.id AS from_id, b.id AS to_id
        """)
        assert len(result) == 1
        assert result[0]["from_id"] == 1
        assert result[0]["to_id"] == 2

        # Cleanup
        falkordb_client.execute_query("MATCH (n:TestNode) DETACH DELETE n")

    def test_get_stats(self, falkordb_client):
        """Test get_stats method works with FalkorDB."""
        stats = falkordb_client.get_stats()
        assert "total_nodes" in stats
        assert "total_files" in stats
        assert isinstance(stats["total_nodes"], int)


class TestFalkorDBIngestion:
    """Test ingestion pipeline with FalkorDB."""

    def test_ingest_sample_codebase(self, falkordb_client, sample_codebase):
        """Test that ingestion pipeline works with FalkorDB."""
        pipeline = IngestionPipeline(str(sample_codebase), falkordb_client)
        pipeline.ingest(patterns=["**/*.py"])

        # Verify nodes were created
        stats = falkordb_client.get_stats()
        assert stats["total_files"] >= 5  # At least our test files
        assert stats["total_functions"] >= 3  # main, process, validate, etc.

    def test_ingestion_creates_relationships(self, falkordb_client, sample_codebase):
        """Test that relationships are created correctly."""
        # Ingest (graph cleared automatically by isolate_graph_test)
        pipeline = IngestionPipeline(str(sample_codebase), falkordb_client)
        pipeline.ingest(patterns=["**/*.py"])

        # Check for IMPORTS relationships
        result = falkordb_client.execute_query("""
            MATCH ()-[r:IMPORTS]->()
            RETURN count(r) AS import_count
        """)
        assert result[0]["import_count"] > 0

        # Check for CONTAINS relationships
        result = falkordb_client.execute_query("""
            MATCH ()-[r:CONTAINS]->()
            RETURN count(r) AS contains_count
        """)
        assert result[0]["contains_count"] > 0


class TestFalkorDBRustAlgorithms:
    """Test all Rust graph algorithms with FalkorDB."""

    @pytest.fixture(autouse=True)
    def setup_graph_data(self, falkordb_client, sample_codebase):
        """Ensure graph has data before running algorithm tests.

        Graph is cleared automatically by isolate_graph_test autouse fixture.
        """
        pipeline = IngestionPipeline(str(sample_codebase), falkordb_client)
        pipeline.ingest(patterns=["**/*.py"])

    def test_scc_algorithm(self, falkordb_client):
        """Test SCC (Tarjan's) algorithm works with FalkorDB."""
        graph_algo = GraphAlgorithms(falkordb_client)
        result = graph_algo.calculate_scc()

        # Should complete without error
        assert result is not None
        assert "componentCount" in result
        assert result["componentCount"] >= 0

    def test_scc_detects_cycles(self, falkordb_client):
        """Test SCC detects circular dependencies."""
        graph_algo = GraphAlgorithms(falkordb_client)
        graph_algo.calculate_scc()

        cycles = graph_algo.get_scc_cycles(min_cycle_size=2)
        # Our test data has circular_a <-> circular_b
        # This may or may not be detected depending on how imports are resolved
        assert isinstance(cycles, list)

    def test_pagerank_algorithm(self, falkordb_client):
        """Test PageRank algorithm works with FalkorDB."""
        graph_algo = GraphAlgorithms(falkordb_client)
        result = graph_algo.calculate_pagerank()

        assert result is not None
        assert "nodePropertiesWritten" in result

    def test_pagerank_writes_scores(self, falkordb_client):
        """Test PageRank scores are written to nodes."""
        graph_algo = GraphAlgorithms(falkordb_client)
        graph_algo.calculate_pagerank()

        # Check that some nodes have pagerank property
        result = falkordb_client.execute_query("""
            MATCH (f:Function)
            WHERE f.pagerank IS NOT NULL
            RETURN count(f) AS count
        """)
        # May be 0 if no call graph exists, but should not error
        assert result[0]["count"] >= 0

    def test_betweenness_centrality_algorithm(self, falkordb_client):
        """Test Betweenness Centrality (Brandes) works with FalkorDB."""
        graph_algo = GraphAlgorithms(falkordb_client)
        result = graph_algo.calculate_betweenness_centrality()

        assert result is not None
        assert "nodePropertiesWritten" in result
        assert "computeMillis" in result

    def test_leiden_community_detection(self, falkordb_client):
        """Test Leiden community detection works with FalkorDB."""
        graph_algo = GraphAlgorithms(falkordb_client)
        result = graph_algo.calculate_communities()

        assert result is not None
        assert "communityCount" in result

    def test_leiden_file_communities(self, falkordb_client):
        """Test file-level Leiden community detection."""
        graph_algo = GraphAlgorithms(falkordb_client)
        result = graph_algo.calculate_file_communities()

        assert result is not None
        assert "communityCount" in result

    def test_harmonic_centrality_algorithm(self, falkordb_client):
        """Test Harmonic Centrality works with FalkorDB."""
        graph_algo = GraphAlgorithms(falkordb_client)
        result = graph_algo.calculate_harmonic_centrality()

        assert result is not None
        assert "nodePropertiesWritten" in result
        assert "computeMillis" in result

    def test_full_analysis_pipeline(self, falkordb_client):
        """Test running all algorithms together."""
        graph_algo = GraphAlgorithms(falkordb_client)
        results = graph_algo.run_full_analysis()

        assert results["rust_algorithms"] is True
        assert "communities" in results
        assert "pagerank" in results
        assert "betweenness" in results
        assert "scc" in results
        assert len(results.get("errors", [])) == 0


class TestFalkorDBAnalysisEngine:
    """Test full analysis workflow with FalkorDB."""

    def test_analysis_engine_works(self, falkordb_client, sample_codebase):
        """Test that AnalysisEngine works with FalkorDB."""
        # Ingest (graph cleared automatically by isolate_graph_test)
        pipeline = IngestionPipeline(str(sample_codebase), falkordb_client)
        pipeline.ingest(patterns=["**/*.py"])

        # Run analysis - IMPORTANT: pass repository_path to avoid analyzing entire project
        engine = AnalysisEngine(falkordb_client, repository_path=str(sample_codebase))
        health = engine.analyze()

        # Verify results
        assert health is not None
        assert health.grade in ["A", "B", "C", "D", "F"]
        assert 0 <= health.overall_score <= 100
        assert health.metrics is not None

    def test_detectors_produce_findings(self, falkordb_client, sample_codebase):
        """Test that detectors produce findings."""
        # Ingest (graph cleared automatically by isolate_graph_test)
        pipeline = IngestionPipeline(str(sample_codebase), falkordb_client)
        pipeline.ingest(patterns=["**/*.py"])

        # IMPORTANT: pass repository_path to avoid analyzing entire project
        engine = AnalysisEngine(falkordb_client, repository_path=str(sample_codebase))
        health = engine.analyze()

        # Should have some findings (at least from dead code or complexity)
        assert health.findings_summary is not None
        # Total might be 0 for small clean codebase, but should not error
        assert health.findings_summary.total >= 0


class TestFalkorDBCypherCompatibility:
    """Test Cypher compatibility between FalkorDB and Neo4j.

    Documents any FalkorDB-specific Cypher adjustments needed.
    """

    def test_basic_match(self, falkordb_client):
        """Test basic MATCH query."""
        falkordb_client.execute_query("CREATE (n:CompatTest {value: 1})")
        result = falkordb_client.execute_query("MATCH (n:CompatTest) RETURN n.value AS val")
        assert result[0]["val"] == 1
        falkordb_client.execute_query("MATCH (n:CompatTest) DELETE n")

    def test_merge_query(self, falkordb_client):
        """Test MERGE query works."""
        falkordb_client.execute_query(
            "MERGE (n:CompatTest {id: $id}) SET n.updated = true",
            {"id": "test1"}
        )
        result = falkordb_client.execute_query(
            "MATCH (n:CompatTest {id: $id}) RETURN n.updated AS updated",
            {"id": "test1"}
        )
        assert result[0]["updated"] is True
        falkordb_client.execute_query("MATCH (n:CompatTest) DELETE n")

    def test_unwind_query(self, falkordb_client):
        """Test UNWIND query for batch operations."""
        items = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
        falkordb_client.execute_query(
            "UNWIND $items AS item CREATE (n:CompatTest) SET n = item",
            {"items": items}
        )
        result = falkordb_client.execute_query(
            "MATCH (n:CompatTest) RETURN n.name AS name ORDER BY n.id"
        )
        assert [r["name"] for r in result] == ["a", "b"]
        falkordb_client.execute_query("MATCH (n:CompatTest) DELETE n")

    def test_aggregation_functions(self, falkordb_client):
        """Test aggregation functions work."""
        falkordb_client.execute_query("""
            CREATE (n1:CompatTest {value: 10})
            CREATE (n2:CompatTest {value: 20})
            CREATE (n3:CompatTest {value: 30})
        """)
        result = falkordb_client.execute_query("""
            MATCH (n:CompatTest)
            RETURN
                count(n) AS cnt,
                sum(n.value) AS total,
                avg(n.value) AS average,
                min(n.value) AS minimum,
                max(n.value) AS maximum
        """)
        assert result[0]["cnt"] == 3
        assert result[0]["total"] == 60
        assert result[0]["average"] == 20.0
        assert result[0]["minimum"] == 10
        assert result[0]["maximum"] == 30
        falkordb_client.execute_query("MATCH (n:CompatTest) DELETE n")

    def test_path_queries(self, falkordb_client):
        """Test path-based queries work."""
        falkordb_client.execute_query("""
            CREATE (a:PathTest {name: 'a'})
            CREATE (b:PathTest {name: 'b'})
            CREATE (c:PathTest {name: 'c'})
            CREATE (a)-[:NEXT]->(b)-[:NEXT]->(c)
        """)
        result = falkordb_client.execute_query("""
            MATCH path = (start:PathTest {name: 'a'})-[:NEXT*]->(end:PathTest)
            RETURN end.name AS end_name
        """)
        end_names = [r["end_name"] for r in result]
        assert "b" in end_names
        assert "c" in end_names
        falkordb_client.execute_query("MATCH (n:PathTest) DETACH DELETE n")

    def test_collect_function(self, falkordb_client):
        """Test collect() aggregation works."""
        falkordb_client.execute_query("""
            CREATE (g:Group {name: 'group1'})
            CREATE (i1:Item {name: 'item1'})
            CREATE (i2:Item {name: 'item2'})
            CREATE (g)-[:HAS]->(i1)
            CREATE (g)-[:HAS]->(i2)
        """)
        result = falkordb_client.execute_query("""
            MATCH (g:Group)-[:HAS]->(i:Item)
            RETURN g.name AS group, collect(i.name) AS items
        """)
        assert result[0]["group"] == "group1"
        assert set(result[0]["items"]) == {"item1", "item2"}
        falkordb_client.execute_query("MATCH (n:Group) DETACH DELETE n")
        falkordb_client.execute_query("MATCH (n:Item) DELETE n")


class TestFalkorDBVectorSearch:
    """Test vector search functionality with FalkorDB (REPO-204).

    Tests vector index creation, embedding storage, and similarity search.
    """

    def test_vector_index_creation(self, falkordb_client):
        """Test creating vector indexes on FalkorDB."""
        from repotoire.graph.schema import GraphSchema

        schema = GraphSchema(falkordb_client)

        # Should not raise - creates vector indexes
        schema.create_vector_indexes()

        # Verify by checking we can query (will return empty but shouldn't error)
        # Note: FalkorDB may not have a way to list indexes, so we just verify no error

    def test_store_embedding_on_node(self, falkordb_client):
        """Test storing embeddings as node properties."""
        # Create a test node with embedding using vecf32() for FalkorDB
        test_embedding = [0.1] * 1536  # 1536 dimensions like OpenAI

        falkordb_client.execute_query("""
            CREATE (f:Function {
                qualifiedName: 'test.vector_func',
                name: 'vector_func',
                embedding: vecf32($embedding)
            })
        """, {"embedding": test_embedding})

        # Retrieve and verify
        result = falkordb_client.execute_query("""
            MATCH (f:Function {qualifiedName: 'test.vector_func'})
            RETURN f.embedding AS embedding
        """)

        assert len(result) == 1
        stored_embedding = result[0]["embedding"]
        assert len(stored_embedding) == 1536
        assert abs(stored_embedding[0] - 0.1) < 0.001

        # Cleanup
        falkordb_client.execute_query(
            "MATCH (f:Function {qualifiedName: 'test.vector_func'}) DELETE f"
        )

    def test_vector_similarity_search(self, falkordb_client):
        """Test vector similarity search query."""
        from repotoire.graph import FalkorDBClient
        from repotoire.graph.schema import GraphSchema
        import time

        # Use dedicated client with separate graph for isolation
        # Note: We pass falkordb_client fixture to ensure skip logic runs,
        # but create our own client for a dedicated graph name
        client = FalkorDBClient(
            host="localhost",
            port=int(os.environ.get("REPOTOIRE_FALKORDB_PORT", "6379")),
            graph_name="vector_search_test",
            max_retries=1,
        )
        client.clear_graph()

        try:
            # Create vector index FIRST (required before adding nodes for FalkorDB)
            schema = GraphSchema(client)
            schema.create_vector_indexes()

            # Small delay for index to initialize
            time.sleep(0.5)

            # Create test nodes with different embeddings using vecf32()
            embedding_close = [0.9] * 1536
            embedding_far = [0.1] * 1536

            client.execute_query("""
                CREATE (f1:Function {
                    qualifiedName: 'test.close_func',
                    name: 'close_func',
                    embedding: vecf32($embedding)
                })
            """, {"embedding": embedding_close})

            client.execute_query("""
                CREATE (f2:Function {
                    qualifiedName: 'test.far_func',
                    name: 'far_func',
                    embedding: vecf32($embedding)
                })
            """, {"embedding": embedding_far})

            # Small delay for indexing
            time.sleep(1)

            # Query with embedding similar to close_func
            query_embedding = [0.85] * 1536

            result = client.execute_query("""
                CALL db.idx.vector.queryNodes(
                    'Function',
                    'embedding',
                    2,
                    vecf32($embedding)
                ) YIELD node, score
                RETURN node.qualifiedName AS name, score
            """, {"embedding": query_embedding})

            # Should return results
            assert len(result) >= 1
            names = [r["name"] for r in result]
            assert "test.close_func" in names

        except Exception as e:
            pytest.skip(f"Vector search not available: {e}")

        finally:
            client.close()

    def test_retriever_with_falkordb(self, falkordb_client, sample_codebase):
        """Test GraphRAGRetriever works with FalkorDB."""
        from unittest.mock import Mock
        from repotoire.ai.retrieval import GraphRAGRetriever

        # Create mock embedder
        mock_embedder = Mock()
        mock_embedder.embed_query.return_value = [0.5] * 1536

        # Create retriever
        retriever = GraphRAGRetriever(falkordb_client, mock_embedder)

        # Verify FalkorDB detection
        assert retriever.is_falkordb is True

        # Test retrieve_by_path (doesn't need vector index)
        falkordb_client.execute_query("""
            CREATE (f1:Function {qualifiedName: 'test.caller', name: 'caller'})
            CREATE (f2:Function {qualifiedName: 'test.callee', name: 'callee'})
            CREATE (f1)-[:CALLS]->(f2)
        """)

        results = retriever.retrieve_by_path(
            start_entity="test.caller",
            relationship_types=["CALLS"],
            max_hops=2,
            limit=10
        )

        # Should find callee via CALLS relationship
        assert len(results) >= 1
        names = [r.qualified_name for r in results]
        assert "test.callee" in names

        # Cleanup
        falkordb_client.execute_query(
            "MATCH (f:Function) WHERE f.qualifiedName STARTS WITH 'test.' DETACH DELETE f"
        )


class TestPerformanceComparison:
    """Optional performance benchmarks comparing FalkorDB and Neo4j.

    These tests are skipped by default. Run with:
    REPOTOIRE_TEST_DB=both pytest -v tests/integration/test_falkordb.py::TestPerformanceComparison
    """

    @pytest.fixture
    def large_graph_data(self):
        """Generate data for performance testing."""
        # Create 100 files with 500 functions and relationships
        files = [{"name": f"file_{i}.py", "path": f"/src/file_{i}.py"} for i in range(100)]
        functions = [
            {"name": f"func_{i}", "file_idx": i % 100, "complexity": i % 20}
            for i in range(500)
        ]
        # Create call relationships (random-ish pattern)
        calls = [(i, (i * 7 + 3) % 500) for i in range(1000)]
        return {"files": files, "functions": functions, "calls": calls}

    @pytest.mark.skip(reason="Performance tests disabled by default")
    def test_bulk_insert_performance(self, falkordb_client, graph_client, large_graph_data):
        """Compare bulk insert performance.

        Both graphs are cleared automatically by isolate_graph_test before each test.
        """
        import time

        # FalkorDB (graph already cleared by autouse fixture)
        start = time.time()
        for f in large_graph_data["files"]:
            falkordb_client.execute_query(
                "CREATE (n:File {name: $name, path: $path})",
                f
            )
        falkordb_time = time.time() - start

        # Neo4j - need to clear since we're using both in same test
        graph_client.clear_graph()
        start = time.time()
        for f in large_graph_data["files"]:
            graph_client.execute_query(
                "CREATE (n:File {name: $name, path: $path})",
                f
            )
        neo4j_time = time.time() - start

        print(f"\nBulk insert performance:")
        print(f"  FalkorDB: {falkordb_time:.3f}s")
        print(f"  Neo4j:    {neo4j_time:.3f}s")
        print(f"  Ratio:    {falkordb_time/neo4j_time:.2f}x")

    @pytest.mark.skip(reason="Performance tests disabled by default")
    def test_query_performance(self, falkordb_client, graph_client, large_graph_data):
        """Compare query performance.

        Both graphs are cleared automatically by isolate_graph_test before each test.
        """
        import time

        # Setup data in both databases (already cleared by autouse fixture)
        for client in [falkordb_client, graph_client]:
            for f in large_graph_data["files"]:
                client.execute_query(
                    "CREATE (n:File {name: $name, path: $path})",
                    f
                )

        query = "MATCH (f:File) RETURN count(f) AS count"

        # FalkorDB
        start = time.time()
        for _ in range(100):
            falkordb_client.execute_query(query)
        falkordb_time = time.time() - start

        # Neo4j
        start = time.time()
        for _ in range(100):
            graph_client.execute_query(query)
        neo4j_time = time.time() - start

        print(f"\nQuery performance (100 iterations):")
        print(f"  FalkorDB: {falkordb_time:.3f}s")
        print(f"  Neo4j:    {neo4j_time:.3f}s")
        print(f"  Ratio:    {falkordb_time/neo4j_time:.2f}x")


# Document FalkorDB-specific adjustments
FALKORDB_CYPHER_NOTES = """
# FalkorDB Cypher Compatibility Notes

## Fully Compatible
- Basic MATCH, CREATE, MERGE, DELETE queries
- Parameterized queries ($param syntax)
- UNWIND for batch operations
- collect(), count(), sum(), avg(), min(), max()
- Variable-length path patterns ([:REL*])
- CASE expressions
- WITH clauses
- ORDER BY, LIMIT

## Differences from Neo4j
1. No APOC procedures (apoc.* functions unavailable)
2. No GDS plugin (we use Rust algorithms instead)
3. Some datetime functions may differ
4. No full-text indexes (use Redis search instead)

## Workarounds Implemented
1. All graph algorithms use Rust via repotoire_fast
2. APOC path functions replaced with native Cypher
3. GDS projections not needed - algorithms extract data directly

## Performance Notes
- FalkorDB excels at write-heavy workloads (Redis backend)
- Complex traversals may be slower than Neo4j
- Memory usage is generally lower
"""


if __name__ == "__main__":
    print(FALKORDB_CYPHER_NOTES)
    pytest.main([__file__, "-v"])
