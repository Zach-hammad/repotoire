"""Database session management for async and sync SQLAlchemy.

This module provides database session management using SQLAlchemy's
async and sync engines and session factories. It's designed for use with:
- FastAPI's dependency injection system (async)
- Celery workers (sync)
"""

import os
import ssl
from contextlib import contextmanager
from typing import AsyncGenerator, Generator
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Database URL from environment
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://repotoire:repotoire-dev-password@localhost:5432/repotoire",
)

# Convert postgresql:// to postgresql+asyncpg:// if needed
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


def _parse_database_url(url: str) -> tuple[str, dict]:
    """Parse DATABASE_URL and extract asyncpg-incompatible params.

    asyncpg doesn't support sslmode in the URL, so we need to extract it
    and convert to SSL context for connect_args.

    Returns:
        Tuple of (cleaned_url, connect_args)
    """
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)

    # Extract sslmode if present
    sslmode = query_params.pop("sslmode", [None])[0]

    # Rebuild URL without sslmode
    new_query = urlencode({k: v[0] for k, v in query_params.items()}, doseq=False)
    cleaned_url = urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        parsed.fragment,
    ))

    # Build connect_args based on sslmode
    connect_args: dict = {}
    if sslmode in ("require", "verify-ca", "verify-full"):
        # Create SSL context for asyncpg
        ssl_context = ssl.create_default_context()
        if sslmode == "require":
            # Don't verify certificate, just encrypt
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        connect_args["ssl"] = ssl_context

    return cleaned_url, connect_args


# Parse URL and get connect_args for SSL
_cleaned_url, _connect_args = _parse_database_url(DATABASE_URL)

# Create async engine
engine = create_async_engine(
    _cleaned_url,
    echo=os.getenv("DATABASE_ECHO", "false").lower() == "true",
    pool_size=int(os.getenv("DATABASE_POOL_SIZE", "5")),
    max_overflow=int(os.getenv("DATABASE_MAX_OVERFLOW", "10")),
    pool_pre_ping=True,  # Enable connection health checks
    connect_args=_connect_args,
)

# Create async session factory
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

# Sync database URL - convert asyncpg back to psycopg2
SYNC_DATABASE_URL = _cleaned_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _parse_sync_database_url(url: str) -> tuple[str, dict]:
    """Parse sync DATABASE_URL and extract connect_args.

    psycopg2 supports sslmode in URL, so we don't need to extract it.
    Returns:
        Tuple of (url, connect_args)
    """
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    sslmode = query_params.get("sslmode", [None])[0]

    connect_args: dict = {}
    if sslmode in ("require", "verify-ca", "verify-full"):
        # psycopg2 handles sslmode in URL, but we can add extra args if needed
        pass

    return url, connect_args


_sync_url, _sync_connect_args = _parse_sync_database_url(SYNC_DATABASE_URL)

# Create sync engine for Celery workers
sync_engine = create_engine(
    _sync_url,
    echo=os.getenv("DATABASE_ECHO", "false").lower() == "true",
    pool_size=int(os.getenv("DATABASE_POOL_SIZE", "5")),
    max_overflow=int(os.getenv("DATABASE_MAX_OVERFLOW", "10")),
    pool_pre_ping=True,
    connect_args=_sync_connect_args,
)

# Create sync session factory for Celery workers
sync_session_factory = sessionmaker(
    sync_engine,
    class_=Session,
    expire_on_commit=False,
    autoflush=False,
)


@contextmanager
def get_sync_session() -> Generator[Session, None, None]:
    """Context manager for sync database sessions.

    Designed for use in Celery workers where async is not available.

    Usage:
        with get_sync_session() as session:
            repo = session.get(Repository, repo_id)
            session.commit()

    Yields:
        Session: A sync database session that is automatically closed
            after the context exits.
    """
    session = sync_session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that provides a database session.

    Usage:
        @router.get("/items")
        async def get_items(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(Item))
            return result.scalars().all()

    Yields:
        AsyncSession: An async database session that is automatically closed
            after the request completes.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database connection and verify connectivity.

    This should be called during application startup to ensure
    the database is reachable.
    """
    try:
        async with engine.begin() as conn:
            # Simple connectivity check
            await conn.execute(text("SELECT 1"))
        logger.info("Database connection established successfully")
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        raise


async def close_db() -> None:
    """Close database connections.

    This should be called during application shutdown.
    """
    await engine.dispose()
    sync_engine.dispose()
    logger.info("Database connections closed")


def close_sync_db() -> None:
    """Close sync database connections.

    This should be called when Celery workers shut down.
    """
    sync_engine.dispose()
    logger.info("Sync database connections closed")
