"""Centralized HTTP client pool for connection reuse.

REPO-500: This module provides shared HTTP client instances with connection
pooling to avoid the overhead of creating new connections for every request.

Previously, most code created a new httpx.Client/AsyncClient per-request:
    async with httpx.AsyncClient() as client:  # Bad: new connection every time
        await client.get(...)

This wastes resources:
- TCP connection establishment (~50-100ms)
- TLS handshake (~100-200ms)
- No keepalive connection reuse

Now use the shared clients from this module:
    from repotoire.http_client import get_async_client, get_sync_client

    # Async context
    client = get_async_client()
    await client.get(...)

    # Sync context
    client = get_sync_client()
    client.get(...)

The clients are lazily initialized and reuse connections via HTTP/2 and
connection pooling. They are thread-safe and can be used across the application.

For cleanup on shutdown:
    from repotoire.http_client import close_clients
    await close_clients()  # or close_clients_sync()
"""

import asyncio
import os
import threading
from typing import Optional

import httpx

from repotoire.logging_config import get_logger

logger = get_logger(__name__)


# Default connection pool limits
# REPO-500: Sized for typical SaaS workload (API calls to GitHub, webhooks, etc.)
DEFAULT_MAX_CONNECTIONS = int(os.environ.get("HTTP_MAX_CONNECTIONS", "100"))
DEFAULT_MAX_KEEPALIVE = int(os.environ.get("HTTP_MAX_KEEPALIVE", "20"))

# Default timeouts (in seconds)
DEFAULT_CONNECT_TIMEOUT = float(os.environ.get("HTTP_CONNECT_TIMEOUT", "10.0"))
DEFAULT_READ_TIMEOUT = float(os.environ.get("HTTP_READ_TIMEOUT", "30.0"))
DEFAULT_WRITE_TIMEOUT = float(os.environ.get("HTTP_WRITE_TIMEOUT", "30.0"))
DEFAULT_POOL_TIMEOUT = float(os.environ.get("HTTP_POOL_TIMEOUT", "10.0"))


def _create_timeout() -> httpx.Timeout:
    """Create default timeout configuration."""
    return httpx.Timeout(
        connect=DEFAULT_CONNECT_TIMEOUT,
        read=DEFAULT_READ_TIMEOUT,
        write=DEFAULT_WRITE_TIMEOUT,
        pool=DEFAULT_POOL_TIMEOUT,
    )


def _create_limits() -> httpx.Limits:
    """Create default connection pool limits."""
    return httpx.Limits(
        max_connections=DEFAULT_MAX_CONNECTIONS,
        max_keepalive_connections=DEFAULT_MAX_KEEPALIVE,
        keepalive_expiry=30.0,  # Close idle connections after 30s
    )


# Shared client instances (lazily initialized)
_async_client: Optional[httpx.AsyncClient] = None
_sync_client: Optional[httpx.Client] = None
_async_lock = asyncio.Lock()
_sync_lock = threading.Lock()

# Separate clients for specific services (e.g., GitHub has different rate limits)
_github_async_client: Optional[httpx.AsyncClient] = None
_github_sync_client: Optional[httpx.Client] = None


async def get_async_client() -> httpx.AsyncClient:
    """Get the shared async HTTP client.

    Lazily initializes a shared AsyncClient with connection pooling.
    Thread-safe via asyncio.Lock.

    Returns:
        Shared httpx.AsyncClient instance

    Example:
        client = await get_async_client()
        response = await client.get("https://api.example.com/data")
    """
    global _async_client
    if _async_client is not None and not _async_client.is_closed:
        return _async_client

    async with _async_lock:
        # Double-check after acquiring lock
        if _async_client is not None and not _async_client.is_closed:
            return _async_client

        _async_client = httpx.AsyncClient(
            timeout=_create_timeout(),
            limits=_create_limits(),
            http2=True,  # Enable HTTP/2 for multiplexing
            follow_redirects=True,
        )
        logger.debug(
            f"Initialized shared async HTTP client "
            f"(max_connections={DEFAULT_MAX_CONNECTIONS})"
        )
        return _async_client


def get_sync_client() -> httpx.Client:
    """Get the shared sync HTTP client.

    Lazily initializes a shared Client with connection pooling.
    Thread-safe via threading.Lock.

    Returns:
        Shared httpx.Client instance

    Example:
        client = get_sync_client()
        response = client.get("https://api.example.com/data")
    """
    global _sync_client
    if _sync_client is not None and not _sync_client.is_closed:
        return _sync_client

    with _sync_lock:
        # Double-check after acquiring lock
        if _sync_client is not None and not _sync_client.is_closed:
            return _sync_client

        _sync_client = httpx.Client(
            timeout=_create_timeout(),
            limits=_create_limits(),
            http2=True,
            follow_redirects=True,
        )
        logger.debug(
            f"Initialized shared sync HTTP client "
            f"(max_connections={DEFAULT_MAX_CONNECTIONS})"
        )
        return _sync_client


async def get_github_async_client() -> httpx.AsyncClient:
    """Get the shared async HTTP client for GitHub API calls.

    Uses separate connection pool for GitHub to:
    1. Respect GitHub's rate limits independently
    2. Avoid GitHub slowdowns affecting other services
    3. Configure GitHub-specific headers

    Returns:
        Shared httpx.AsyncClient configured for GitHub API
    """
    global _github_async_client
    if _github_async_client is not None and not _github_async_client.is_closed:
        return _github_async_client

    async with _async_lock:
        if _github_async_client is not None and not _github_async_client.is_closed:
            return _github_async_client

        _github_async_client = httpx.AsyncClient(
            base_url="https://api.github.com",
            timeout=_create_timeout(),
            limits=httpx.Limits(
                max_connections=50,  # GitHub has rate limits, don't need many
                max_keepalive_connections=10,
                keepalive_expiry=60.0,  # GitHub connections can stay open longer
            ),
            http2=True,
            follow_redirects=True,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        logger.debug("Initialized shared GitHub async HTTP client")
        return _github_async_client


def get_github_sync_client() -> httpx.Client:
    """Get the shared sync HTTP client for GitHub API calls.

    Returns:
        Shared httpx.Client configured for GitHub API
    """
    global _github_sync_client
    if _github_sync_client is not None and not _github_sync_client.is_closed:
        return _github_sync_client

    with _sync_lock:
        if _github_sync_client is not None and not _github_sync_client.is_closed:
            return _github_sync_client

        _github_sync_client = httpx.Client(
            base_url="https://api.github.com",
            timeout=_create_timeout(),
            limits=httpx.Limits(
                max_connections=50,
                max_keepalive_connections=10,
                keepalive_expiry=60.0,
            ),
            http2=True,
            follow_redirects=True,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        logger.debug("Initialized shared GitHub sync HTTP client")
        return _github_sync_client


async def close_clients() -> None:
    """Close all shared HTTP clients.

    Should be called during application shutdown to release connections.

    Example:
        # In FastAPI lifespan
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            yield
            await close_clients()
    """
    global _async_client, _github_async_client

    clients_closed = 0
    if _async_client is not None and not _async_client.is_closed:
        await _async_client.aclose()
        _async_client = None
        clients_closed += 1

    if _github_async_client is not None and not _github_async_client.is_closed:
        await _github_async_client.aclose()
        _github_async_client = None
        clients_closed += 1

    if clients_closed > 0:
        logger.info(f"Closed {clients_closed} shared async HTTP client(s)")


def close_clients_sync() -> None:
    """Close all shared sync HTTP clients.

    Should be called during application shutdown to release connections.
    """
    global _sync_client, _github_sync_client

    clients_closed = 0
    if _sync_client is not None and not _sync_client.is_closed:
        _sync_client.close()
        _sync_client = None
        clients_closed += 1

    if _github_sync_client is not None and not _github_sync_client.is_closed:
        _github_sync_client.close()
        _github_sync_client = None
        clients_closed += 1

    if clients_closed > 0:
        logger.info(f"Closed {clients_closed} shared sync HTTP client(s)")


def reset_clients() -> None:
    """Reset all shared clients (for testing).

    Closes and clears all client references without logging.
    """
    global _async_client, _sync_client, _github_async_client, _github_sync_client

    with _sync_lock:
        if _sync_client is not None and not _sync_client.is_closed:
            _sync_client.close()
        _sync_client = None

        if _github_sync_client is not None and not _github_sync_client.is_closed:
            _github_sync_client.close()
        _github_sync_client = None

    # Note: async clients should be closed with close_clients() in async context
    _async_client = None
    _github_async_client = None
