"""Database session management for async and sync SQLAlchemy.

This module provides database session management using SQLAlchemy's
async and sync engines and session factories. It's designed for use with:
- FastAPI's dependency injection system (async)
- Celery workers (sync)

LAZY INITIALIZATION: Engines are created on first use, not at import time.
This allows CLI commands that don't need the database to run without DATABASE_URL.
"""

import os
import ssl
import threading
from contextlib import contextmanager
from typing import AsyncGenerator, Generator, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Lazy initialization - engines created on first use
_engine: Optional[object] = None
_async_session_factory: Optional[object] = None
_sync_engine: Optional[object] = None
_sync_session_factory: Optional[object] = None
_init_lock = threading.Lock()
_initialized = False


def _get_database_url() -> str:
    """Get DATABASE_URL from environment, raising if not set.
    
    Raises:
        RuntimeError: If DATABASE_URL is not set
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL environment variable is required.\n"
            "Example: postgresql://user:password@localhost:5432/repotoire\n"
            "See .env.example for configuration details."
        )
    return database_url


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


def _ensure_initialized():
    """Lazily initialize database engines on first use.
    
    Thread-safe initialization using double-checked locking.
    """
    global _engine, _async_session_factory, _sync_engine, _sync_session_factory, _initialized
    
    if _initialized:
        return
    
    with _init_lock:
        if _initialized:
            return
        
        # Get and validate DATABASE_URL
        database_url = _get_database_url()
        
        # Convert postgresql:// to postgresql+asyncpg:// if needed
        if database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        
        # Parse URL and get connect_args for SSL
        cleaned_url, connect_args = _parse_database_url(database_url)
        
        # Create async engine
        # Production-ready pool settings (conservative defaults for multi-instance deployments):
        # - pool_size=5: Base connections per instance (Neon default pool is 20 total)
        # - max_overflow=5: Limited burst capacity (total max 10 per instance)
        # - pool_timeout=30: Wait up to 30s for a connection
        # - pool_recycle=1800: Recycle connections after 30 minutes to prevent stale connections
        #
        # For single-instance deployments, increase via env vars:
        #   DATABASE_POOL_SIZE=15 DATABASE_MAX_OVERFLOW=15
        _engine = create_async_engine(
            cleaned_url,
            echo=os.getenv("DATABASE_ECHO", "false").lower() == "true",
            pool_size=int(os.getenv("DATABASE_POOL_SIZE", "5")),
            max_overflow=int(os.getenv("DATABASE_MAX_OVERFLOW", "5")),
            pool_timeout=int(os.getenv("DATABASE_POOL_TIMEOUT", "30")),
            pool_recycle=int(os.getenv("DATABASE_POOL_RECYCLE", "1800")),
            pool_pre_ping=True,  # Enable connection health checks
            connect_args=connect_args,
        )
        
        # Create async session factory
        _async_session_factory = async_sessionmaker(
            _engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        
        # Sync database URL - convert asyncpg back to psycopg2
        sync_database_url = cleaned_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        sync_url, sync_connect_args = _parse_sync_database_url(sync_database_url)
        
        # Create sync engine for Celery workers
        # Production-ready pool settings (conservative defaults for multi-instance deployments)
        _sync_engine = create_engine(
            sync_url,
            echo=os.getenv("DATABASE_ECHO", "false").lower() == "true",
            pool_size=int(os.getenv("DATABASE_POOL_SIZE", "5")),
            max_overflow=int(os.getenv("DATABASE_MAX_OVERFLOW", "5")),
            pool_timeout=int(os.getenv("DATABASE_POOL_TIMEOUT", "30")),
            pool_recycle=int(os.getenv("DATABASE_POOL_RECYCLE", "1800")),
            pool_pre_ping=True,
            connect_args=sync_connect_args,
        )
        
        # Create sync session factory for Celery workers
        _sync_session_factory = sessionmaker(
            _sync_engine,
            class_=Session,
            expire_on_commit=False,
            autoflush=False,
        )
        
        _initialized = True
        logger.debug("Database engines initialized")


# Property-like accessors for backward compatibility
def _get_engine():
    """Get async engine, initializing if needed."""
    _ensure_initialized()
    return _engine


def _get_async_session_factory():
    """Get async session factory, initializing if needed."""
    _ensure_initialized()
    return _async_session_factory


def _get_sync_engine():
    """Get sync engine, initializing if needed."""
    _ensure_initialized()
    return _sync_engine


def _get_sync_session_factory():
    """Get sync session factory, initializing if needed."""
    _ensure_initialized()
    return _sync_session_factory


# Backward-compatible module-level names (now properties via __getattr__)
# For code that accesses session.engine directly
class _LazyModule:
    """Wrapper to provide lazy attribute access."""
    
    @property
    def engine(self):
        return _get_engine()
    
    @property
    def async_session_factory(self):
        return _get_async_session_factory()
    
    @property
    def sync_engine(self):
        return _get_sync_engine()
    
    @property
    def sync_session_factory(self):
        return _get_sync_session_factory()


# For direct attribute access like `from repotoire.db.session import engine`
# We use module __getattr__ (Python 3.7+)
def __getattr__(name):
    if name == "engine":
        return _get_engine()
    elif name == "async_session_factory":
        return _get_async_session_factory()
    elif name == "sync_engine":
        return _get_sync_engine()
    elif name == "sync_session_factory":
        return _get_sync_session_factory()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    factory = _get_sync_session_factory()
    session = factory()
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
    factory = _get_async_session_factory()
    async with factory() as session:
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
        engine = _get_engine()
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
    if _engine:
        await _engine.dispose()
    if _sync_engine:
        _sync_engine.dispose()
    logger.info("Database connections closed")


def close_sync_db() -> None:
    """Close sync database connections.

    This should be called when Celery workers shut down.
    """
    if _sync_engine:
        _sync_engine.dispose()
    logger.info("Sync database connections closed")


def get_database_url_from_config() -> str:
    """Get database URL from RepotoireConfig if available.

    This provides an alternative to the DATABASE_URL environment variable,
    allowing configuration via .reporc files.

    Returns:
        Database URL string, or None if not configured

    Example:
        # In .reporc:
        postgres:
          database_url: "postgresql://user:pass@localhost:5432/repotoire"
    """
    try:
        from repotoire.config import load_config
        config = load_config()
        if config.postgres.database_url:
            return config.postgres.database_url
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"Could not load config: {e}")

    return None


def init_from_config(config=None):
    """Initialize database engines from RepotoireConfig (optional alternative).

    This function allows initializing the database engines using RepotoireConfig
    instead of direct environment variables. Useful for testing or when using
    config files.

    Note: This rebuilds the global engines. Use with caution in production.

    Args:
        config: Optional RepotoireConfig instance. If None, loads from
                environment/config files.

    Example:
        from repotoire.db.session import init_from_config
        from repotoire.config import load_config

        config = load_config()
        init_from_config(config)
    """
    global _engine, _async_session_factory, _sync_engine, _sync_session_factory, _initialized

    try:
        from repotoire.config import load_config, RepotoireConfig
    except ImportError:
        logger.warning("repotoire.config not available, using env vars")
        return

    if config is None:
        config = load_config()

    pg = config.postgres

    # Get database URL from config or fallback to env
    database_url = pg.database_url or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "Database URL not configured. Set DATABASE_URL environment variable "
            "or configure postgres.database_url in config file."
        )

    # Convert to async URL if needed
    if database_url.startswith("postgresql://"):
        async_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    else:
        async_url = database_url

    # Parse and create engines
    cleaned_url, connect_args = _parse_database_url(async_url)

    with _init_lock:
        _engine = create_async_engine(
            cleaned_url,
            echo=pg.echo,
            pool_size=pg.pool_size,
            max_overflow=pg.max_overflow,
            pool_timeout=pg.pool_timeout,
            pool_recycle=pg.pool_recycle,
            pool_pre_ping=True,
            connect_args=connect_args,
        )

        _async_session_factory = async_sessionmaker(
            _engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

        # Sync engine
        sync_url = cleaned_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        _sync_url, _sync_connect_args = _parse_sync_database_url(sync_url)

        _sync_engine = create_engine(
            _sync_url,
            echo=pg.echo,
            pool_size=pg.pool_size,
            max_overflow=pg.max_overflow,
            pool_timeout=pg.pool_timeout,
            pool_recycle=pg.pool_recycle,
            pool_pre_ping=True,
            connect_args=_sync_connect_args,
        )

        _sync_session_factory = sessionmaker(
            _sync_engine,
            class_=Session,
            expire_on_commit=False,
            autoflush=False,
        )

        _initialized = True

    logger.info("Database engines reinitialized from config")
