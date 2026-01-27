"""Idempotency key middleware for safe request retries.

This middleware implements the Idempotency-Key header pattern for POST/PUT/PATCH
requests, allowing clients to safely retry requests without creating duplicates.

Usage:
    from repotoire.api.shared.middleware.idempotency import IdempotencyMiddleware

    app.add_middleware(IdempotencyMiddleware)

    # Client usage:
    curl -X POST /api/v1/code/search \
        -H "Idempotency-Key: unique-request-id-123" \
        -d '{"query": "authentication"}'

Headers:
    Idempotency-Key: Client-provided unique key for the request (max 64 chars)
    X-Idempotency-Replayed: Set to "true" in responses when returning cached result
"""

import hashlib
import time
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Header names
IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
IDEMPOTENCY_REPLAYED_HEADER = "X-Idempotency-Replayed"

# Default configuration
DEFAULT_TTL_SECONDS = 86400  # 24 hours
DEFAULT_MAX_KEY_LENGTH = 64
IDEMPOTENT_METHODS = {"POST", "PUT", "PATCH"}


class IdempotencyStore:
    """In-memory store for idempotency keys and cached responses.

    In production, this should be backed by Redis for distributed systems.
    This implementation uses an in-memory dict with TTL-based expiration.

    Thread Safety:
        Uses a simple dict which is thread-safe for basic operations in CPython
        due to the GIL. For async-heavy workloads, consider using asyncio.Lock.
    """

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS, max_size: int = 10000):
        """Initialize the store.

        Args:
            ttl_seconds: Time-to-live for cached responses
            max_size: Maximum number of entries (LRU eviction when exceeded)
        """
        self._cache: Dict[str, Tuple[Dict[str, Any], float]] = {}
        self._ttl = ttl_seconds
        self._max_size = max_size

    def _make_key(self, idempotency_key: str, user_id: Optional[str] = None) -> str:
        """Create a cache key combining idempotency key with user context.

        Args:
            idempotency_key: Client-provided idempotency key
            user_id: Optional user/organization ID for isolation

        Returns:
            Combined cache key
        """
        if user_id:
            return hashlib.sha256(f"{user_id}:{idempotency_key}".encode()).hexdigest()
        return hashlib.sha256(idempotency_key.encode()).hexdigest()

    def get(
        self,
        idempotency_key: str,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get cached response for an idempotency key.

        Args:
            idempotency_key: Client-provided key
            user_id: Optional user context for isolation

        Returns:
            Cached response data or None if not found/expired
        """
        cache_key = self._make_key(idempotency_key, user_id)

        if cache_key not in self._cache:
            return None

        response_data, timestamp = self._cache[cache_key]

        # Check expiration
        if time.time() - timestamp > self._ttl:
            del self._cache[cache_key]
            return None

        return response_data

    def set(
        self,
        idempotency_key: str,
        response_data: Dict[str, Any],
        user_id: Optional[str] = None,
    ) -> None:
        """Store response for an idempotency key.

        Args:
            idempotency_key: Client-provided key
            response_data: Response to cache (status, headers, body)
            user_id: Optional user context for isolation
        """
        # Evict oldest entries if at capacity
        if len(self._cache) >= self._max_size:
            # Remove oldest 10% of entries
            entries_to_remove = max(1, len(self._cache) // 10)
            sorted_keys = sorted(
                self._cache.keys(),
                key=lambda k: self._cache[k][1]  # Sort by timestamp
            )
            for key in sorted_keys[:entries_to_remove]:
                del self._cache[key]

        cache_key = self._make_key(idempotency_key, user_id)
        self._cache[cache_key] = (response_data, time.time())

    def clear_expired(self) -> int:
        """Remove all expired entries.

        Returns:
            Number of entries removed
        """
        now = time.time()
        expired_keys = [
            key for key, (_, timestamp) in self._cache.items()
            if now - timestamp > self._ttl
        ]
        for key in expired_keys:
            del self._cache[key]
        return len(expired_keys)


# Global store instance (in production, use Redis)
_idempotency_store = IdempotencyStore()


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Middleware that implements idempotency key pattern.

    When a request includes an Idempotency-Key header:
    1. If we have a cached response for that key, return it with X-Idempotency-Replayed: true
    2. Otherwise, process the request and cache the response

    Configuration:
        - Only applies to POST, PUT, PATCH methods
        - Only caches successful responses (2xx)
        - Keys are scoped per user/organization when possible
    """

    def __init__(
        self,
        app,
        store: Optional[IdempotencyStore] = None,
        max_key_length: int = DEFAULT_MAX_KEY_LENGTH,
    ):
        """Initialize idempotency middleware.

        Args:
            app: The ASGI application
            store: Optional custom IdempotencyStore (uses global if not provided)
            max_key_length: Maximum length for idempotency keys
        """
        super().__init__(app)
        self.store = store or _idempotency_store
        self.max_key_length = max_key_length

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request with idempotency handling."""
        # Only handle idempotent methods
        if request.method not in IDEMPOTENT_METHODS:
            return await call_next(request)

        # Check for idempotency key header
        idempotency_key = request.headers.get(IDEMPOTENCY_KEY_HEADER)
        if not idempotency_key:
            return await call_next(request)

        # Validate key length
        if len(idempotency_key) > self.max_key_length:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_idempotency_key",
                    "detail": f"Idempotency key must be at most {self.max_key_length} characters",
                    "error_code": "IDEMPOTENCY_KEY_TOO_LONG",
                },
            )

        # Get user context for key isolation (from request state if available)
        user_id = None
        if hasattr(request.state, "user"):
            user = request.state.user
            if hasattr(user, "id"):
                user_id = str(user.id)
        if hasattr(request.state, "org_id"):
            user_id = str(request.state.org_id)

        # Check for cached response
        cached = self.store.get(idempotency_key, user_id)
        if cached is not None:
            logger.debug(
                f"Idempotency hit: returning cached response for key {idempotency_key[:8]}..."
            )
            response = JSONResponse(
                status_code=cached["status_code"],
                content=cached["body"],
                headers={IDEMPOTENCY_REPLAYED_HEADER: "true"},
            )
            return response

        # Process the request
        response = await call_next(request)

        # Only cache successful responses (2xx)
        if 200 <= response.status_code < 300:
            # Read response body to cache it
            body = b""
            async for chunk in response.body_iterator:
                body += chunk

            # Try to parse as JSON for caching
            try:
                import json
                body_json = json.loads(body.decode())

                # Cache the response
                self.store.set(
                    idempotency_key,
                    {
                        "status_code": response.status_code,
                        "body": body_json,
                    },
                    user_id,
                )
                logger.debug(
                    f"Idempotency cached: stored response for key {idempotency_key[:8]}..."
                )
            except (json.JSONDecodeError, UnicodeDecodeError):
                # Non-JSON response, don't cache
                pass

            # Return new response with the consumed body
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        return response


def get_idempotency_store() -> IdempotencyStore:
    """Get the global idempotency store.

    Returns:
        The IdempotencyStore instance
    """
    return _idempotency_store


__all__ = [
    "IdempotencyMiddleware",
    "IdempotencyStore",
    "IDEMPOTENCY_KEY_HEADER",
    "IDEMPOTENCY_REPLAYED_HEADER",
    "get_idempotency_store",
]
