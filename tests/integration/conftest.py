"""Integration test fixtures for database tests.

Provides fixtures for testing with Neon PostgreSQL database.
"""

import os
import pytest
import pytest_asyncio
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


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
