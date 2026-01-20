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

For retry-enabled requests (rate limits, transient errors):
    from repotoire.http_client import request_with_retry

    response = await request_with_retry(
        client, "GET", "/api/data",
        max_retries=3, retry_on=[429, 500, 502, 503, 504]
    )
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
# REPO-500: Don't create asyncio.Lock at import time - it requires a running event loop
# Use a threading.Lock to protect initialization of the asyncio.Lock
_async_lock: Optional[asyncio.Lock] = None
_async_lock_init = threading.Lock()  # Sync lock to protect async lock creation
_sync_lock = threading.Lock()

# Separate clients for specific services (e.g., GitHub has different rate limits)
_github_async_client: Optional[httpx.AsyncClient] = None
_github_sync_client: Optional[httpx.Client] = None


def _get_async_lock() -> asyncio.Lock:
    """Get or create the async lock safely.

    REPO-500: Cannot create asyncio.Lock() at module import time because
    there may not be a running event loop. This uses double-checked locking
    with a sync lock to safely create the async lock on first use.
    """
    global _async_lock
    if _async_lock is not None:
        return _async_lock

    with _async_lock_init:
        if _async_lock is None:
            _async_lock = asyncio.Lock()
    return _async_lock


async def get_async_client() -> httpx.AsyncClient:
    """Get the shared async HTTP client.

    Lazily initializes a shared AsyncClient with connection pooling.
    Thread-safe via asyncio.Lock (created on first use).

    Returns:
        Shared httpx.AsyncClient instance

    Example:
        client = await get_async_client()
        response = await client.get("https://api.example.com/data")
    """
    global _async_client
    if _async_client is not None and not _async_client.is_closed:
        return _async_client

    async with _get_async_lock():
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

    async with _get_async_lock():
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


# =============================================================================
# REPO-500: Retry utilities for transient errors
# =============================================================================

# Default retry-able status codes
RETRYABLE_STATUS_CODES = frozenset([
    429,  # Rate limited
    500,  # Internal server error
    502,  # Bad gateway
    503,  # Service unavailable
    504,  # Gateway timeout
])

# Default retry configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0  # Base delay in seconds
DEFAULT_RETRY_BACKOFF = 2.0  # Exponential backoff multiplier
DEFAULT_RETRY_MAX_DELAY = 30.0  # Maximum delay between retries


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_on: frozenset[int] = RETRYABLE_STATUS_CODES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF,
    retry_max_delay: float = DEFAULT_RETRY_MAX_DELAY,
    **kwargs,
) -> httpx.Response:
    """Make an HTTP request with automatic retry for transient errors.

    REPO-500: Implements exponential backoff retry for rate limits (429)
    and server errors (5xx). Respects Retry-After headers when present.

    Args:
        client: The httpx.AsyncClient to use.
        method: HTTP method (GET, POST, etc.).
        url: Request URL.
        max_retries: Maximum number of retry attempts (default: 3).
        retry_on: Set of HTTP status codes to retry on.
        retry_delay: Initial delay between retries in seconds.
        retry_backoff: Multiplier for exponential backoff.
        retry_max_delay: Maximum delay between retries.
        **kwargs: Additional arguments passed to client.request().

    Returns:
        httpx.Response from successful request.

    Raises:
        httpx.HTTPStatusError: If all retries exhausted or non-retryable error.

    Example:
        client = await get_github_async_client()
        response = await request_with_retry(
            client, "GET", "/repos/owner/repo",
            headers={"Authorization": f"Bearer {token}"}
        )
    """
    import random

    last_response: Optional[httpx.Response] = None
    delay = retry_delay

    for attempt in range(max_retries + 1):
        try:
            response = await client.request(method, url, **kwargs)

            # Success - return immediately
            if response.status_code < 400:
                return response

            # Check if we should retry this status code
            if response.status_code not in retry_on:
                response.raise_for_status()
                return response

            last_response = response

            # Check if we have retries left
            if attempt >= max_retries:
                response.raise_for_status()
                return response

            # Calculate delay, respecting Retry-After header if present
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    wait_time = float(retry_after)
                except ValueError:
                    # Retry-After might be a date string, use default delay
                    wait_time = delay
            else:
                # Add jitter (10-25% random variation) to avoid thundering herd
                jitter = random.uniform(0.1, 0.25)
                wait_time = delay * (1 + jitter)

            wait_time = min(wait_time, retry_max_delay)

            logger.warning(
                f"Request {method} {url} returned {response.status_code}, "
                f"retrying in {wait_time:.1f}s (attempt {attempt + 1}/{max_retries + 1})"
            )

            await asyncio.sleep(wait_time)
            delay = min(delay * retry_backoff, retry_max_delay)

        except httpx.RequestError as e:
            # Network errors - retry if we have attempts left
            if attempt >= max_retries:
                raise

            logger.warning(
                f"Request {method} {url} failed with {type(e).__name__}: {e}, "
                f"retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries + 1})"
            )

            await asyncio.sleep(delay)
            delay = min(delay * retry_backoff, retry_max_delay)

    # Should not reach here, but just in case
    if last_response is not None:
        last_response.raise_for_status()
        return last_response
    raise httpx.RequestError(f"All {max_retries + 1} attempts failed for {method} {url}")


def request_with_retry_sync(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_on: frozenset[int] = RETRYABLE_STATUS_CODES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF,
    retry_max_delay: float = DEFAULT_RETRY_MAX_DELAY,
    **kwargs,
) -> httpx.Response:
    """Sync version of request_with_retry.

    See request_with_retry for full documentation.
    """
    import random
    import time

    last_response: Optional[httpx.Response] = None
    delay = retry_delay

    for attempt in range(max_retries + 1):
        try:
            response = client.request(method, url, **kwargs)

            if response.status_code < 400:
                return response

            if response.status_code not in retry_on:
                response.raise_for_status()
                return response

            last_response = response

            if attempt >= max_retries:
                response.raise_for_status()
                return response

            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    wait_time = float(retry_after)
                except ValueError:
                    wait_time = delay
            else:
                jitter = random.uniform(0.1, 0.25)
                wait_time = delay * (1 + jitter)

            wait_time = min(wait_time, retry_max_delay)

            logger.warning(
                f"Request {method} {url} returned {response.status_code}, "
                f"retrying in {wait_time:.1f}s (attempt {attempt + 1}/{max_retries + 1})"
            )

            time.sleep(wait_time)
            delay = min(delay * retry_backoff, retry_max_delay)

        except httpx.RequestError as e:
            if attempt >= max_retries:
                raise

            logger.warning(
                f"Request {method} {url} failed with {type(e).__name__}: {e}, "
                f"retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries + 1})"
            )

            time.sleep(delay)
            delay = min(delay * retry_backoff, retry_max_delay)

    if last_response is not None:
        last_response.raise_for_status()
        return last_response
    raise httpx.RequestError(f"All {max_retries + 1} attempts failed for {method} {url}")
