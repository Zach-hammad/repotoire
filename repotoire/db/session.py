"""Database session management for async SQLAlchemy.

This module provides async database session management using SQLAlchemy's
async engine and session factories. It's designed for use with FastAPI's
dependency injection system.
"""

import os
import ssl
from typing import AsyncGenerator
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
    logger.info("Database connections closed")
