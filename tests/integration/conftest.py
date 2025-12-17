"""Integration test fixtures for database tests.

Provides fixtures for testing with Neon PostgreSQL database and graph databases.

REPO-367: Adds autouse fixtures to ensure test isolation for graph database tests.
Each test starts with a clean graph state to prevent shared state issues.
"""

import os
import pytest
import pytest_asyncio
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


# =============================================================================
# Graph Database Fixtures (Neo4j / FalkorDB)
# =============================================================================


def _has_neo4j() -> bool:
    """Check if Neo4j is configured."""
    uri = os.getenv("REPOTOIRE_NEO4J_URI", "bolt://localhost:7687")
    password = os.getenv("REPOTOIRE_NEO4J_PASSWORD", "password")
    return bool(uri and password)


def _has_falkordb() -> bool:
    """Check if FalkorDB is configured."""
    # FalkorDB typically runs on a different port
    uri = os.getenv("REPOTOIRE_FALKORDB_URI", "")
    return bool(uri.strip())


# Skip markers for graph database tests
skip_no_neo4j = pytest.mark.skipif(
    not _has_neo4j(),
    reason="Neo4j not configured (REPOTOIRE_NEO4J_URI/PASSWORD)"
)

skip_no_falkordb = pytest.mark.skipif(
    not _has_falkordb(),
    reason="FalkorDB not configured (REPOTOIRE_FALKORDB_URI)"
)


# Custom marker for tests that need to preserve existing graph data
# Usage: @pytest.mark.preserve_graph
def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "preserve_graph: mark test to skip automatic graph clearing"
    )


@pytest.fixture(scope="module")
def neo4j_client():
    """Module-scoped Neo4j client for connection reuse.

    Creates a single connection per test module to avoid connection overhead.
    Graph clearing is handled by the isolate_graph_test autouse fixture.

    Yields:
        Neo4jClient instance
    """
    try:
        from repotoire.graph import Neo4jClient

        client = Neo4jClient(
            uri=os.getenv("REPOTOIRE_NEO4J_URI", "bolt://localhost:7687"),
            username="neo4j",
            password=os.getenv("REPOTOIRE_NEO4J_PASSWORD", "password")
        )
        yield client
        client.close()
    except Exception as e:
        pytest.skip(f"Neo4j test database not available: {e}")


@pytest.fixture(scope="module")
def test_neo4j_client(neo4j_client):
    """Alias for neo4j_client for backwards compatibility.

    Many existing tests use test_neo4j_client as the fixture name.
    """
    return neo4j_client


@pytest.fixture(scope="module")
def falkordb_client():
    """Module-scoped FalkorDB client for connection reuse.

    Creates a single connection per test module to avoid connection overhead.
    Graph clearing is handled by the isolate_graph_test autouse fixture.

    Yields:
        FalkorDBClient instance
    """
    try:
        from repotoire.graph import FalkorDBClient

        client = FalkorDBClient(
            host=os.getenv("REPOTOIRE_FALKORDB_HOST", "localhost"),
            port=int(os.getenv("REPOTOIRE_FALKORDB_PORT", "6379")),
            graph_name=os.getenv("REPOTOIRE_FALKORDB_GRAPH", "repotoire_test")
        )
        yield client
        client.close()
    except Exception as e:
        pytest.skip(f"FalkorDB test database not available: {e}")


@pytest.fixture(autouse=True)
def isolate_graph_test(request):
    """Clear graph database before each test for isolation.

    This autouse fixture runs automatically before every test function.
    It detects which graph client fixtures are being used and clears
    the appropriate database.

    Tests marked with @pytest.mark.preserve_graph will skip clearing.

    Args:
        request: pytest request fixture with test metadata
    """
    # Skip clearing if test is marked to preserve graph
    if request.node.get_closest_marker("preserve_graph"):
        yield
        return

    # Determine which graph clients this test uses
    fixture_names = getattr(request, "fixturenames", [])

    # Clear Neo4j if used
    if "neo4j_client" in fixture_names or "test_neo4j_client" in fixture_names:
        try:
            # Get the fixture value - this triggers fixture execution if not already done
            client = request.getfixturevalue("neo4j_client")
            if client:
                client.clear_graph()
        except Exception:
            pass  # Skip if fixture not available

    # Clear FalkorDB if used
    if "falkordb_client" in fixture_names:
        try:
            client = request.getfixturevalue("falkordb_client")
            if client:
                client.clear_graph()
        except Exception:
            pass  # Skip if fixture not available

    yield


@pytest.fixture
def clean_db(neo4j_client):
    """Function-scoped fixture that provides a clean graph for each test.

    This is an explicit alternative to the autouse isolate_graph_test fixture.
    Use this when you want to be explicit about needing a clean database.

    Yields:
        Neo4jClient with cleared graph
    """
    neo4j_client.clear_graph()
    yield neo4j_client


# =============================================================================
# PostgreSQL/Neon Database Fixtures
# =============================================================================


def _has_database_url() -> bool:
    """Check if DATABASE_URL is configured."""
    url = os.getenv("DATABASE_URL", "")
    return bool(url.strip()) and "localhost" not in url


# Skip marker for database tests
skip_no_database = pytest.mark.skipif(
    not _has_database_url(),
    reason="DATABASE_URL not configured for remote database"
)


@pytest_asyncio.fixture
async def db_session():
    """Create a test database session.

    Uses the DATABASE_URL environment variable to connect to Neon.
    Each test gets its own transaction that is rolled back after the test.
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL not set")

    # Convert to asyncpg if needed
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    # Parse URL and handle SSL for Neon
    from repotoire.db.session import _parse_database_url
    cleaned_url, connect_args = _parse_database_url(database_url)

    engine = create_async_engine(
        cleaned_url,
        echo=False,
        connect_args=connect_args,
    )

    async_session = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session() as session:
        # Start a transaction
        async with session.begin() as transaction:
            # Use a nested transaction (savepoint) for test isolation
            nested = await session.begin_nested()
            try:
                yield session
            finally:
                # Always rollback the nested transaction - no data persists
                await nested.rollback()

    await engine.dispose()


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession):
    """Create a test user in the database.

    Returns:
        User model instance
    """
    from repotoire.db.models import User

    user = User(
        clerk_user_id=f"test_clerk_{uuid4().hex[:12]}",
        email=f"test_{uuid4().hex[:8]}@example.com",
        name="Test User",
        avatar_url=None,
    )
    db_session.add(user)
    await db_session.flush()

    return user


@pytest_asyncio.fixture
async def test_user_with_deletion(db_session: AsyncSession):
    """Create a test user with pending deletion.

    Returns:
        User model instance with deletion_requested_at set
    """
    from datetime import datetime, timezone
    from repotoire.db.models import User

    user = User(
        clerk_user_id=f"test_clerk_{uuid4().hex[:12]}",
        email=f"test_{uuid4().hex[:8]}@example.com",
        name="Test User Pending Deletion",
        avatar_url=None,
        deletion_requested_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.flush()

    return user
