"""Redis-backed OAuth state token store.

Provides secure, atomic state token management for OAuth flows with:
- Cryptographically secure token generation
- Automatic TTL expiration
- One-time use (atomic validate-and-consume)
- FastAPI dependency injection support
"""

from __future__ import annotations

import json
import os
import secrets
import time
from typing import TYPE_CHECKING

import redis.asyncio as aioredis

from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = get_logger(__name__)

# Configuration
STATE_TOKEN_TTL = 600  # 10 minutes
KEY_PREFIX = "oauth:state:"
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


class StateStoreError(Exception):
    """Base exception for state store errors."""

    pass


class StateStoreUnavailableError(StateStoreError):
    """Raised when Redis is unavailable."""

    pass


class StateTokenStore:
    """Redis-backed store for OAuth state tokens.

    Provides secure, one-time-use state tokens for CSRF protection in OAuth flows.
    Tokens are stored with TTL and consumed atomically on validation.

    Usage:
        store = StateTokenStore(redis_client)
        state = await store.create_state({"redirect_uri": "http://localhost:3000/callback"})
        # ... user completes OAuth flow ...
        metadata = await store.validate_and_consume(state)
        if metadata:
            # Token valid, continue with OAuth
            pass
        else:
            # Token invalid or expired
            raise HTTPException(400, "Invalid state token")

    Attributes:
        redis: Async Redis client instance.
        ttl: Token TTL in seconds (default: 600).
        key_prefix: Redis key prefix for state tokens.
    """

    def __init__(
        self,
        redis: "Redis",
        ttl: int = STATE_TOKEN_TTL,
        key_prefix: str = KEY_PREFIX,
    ) -> None:
        """Initialize the state token store.

        Args:
            redis: Async Redis client instance.
            ttl: Token TTL in seconds (default: 600).
            key_prefix: Redis key prefix for state tokens.
        """
        self._redis = redis
        self._ttl = ttl
        self._key_prefix = key_prefix

    def _make_key(self, token: str) -> str:
        """Generate Redis key for a token.

        Args:
            token: The state token.

        Returns:
            Full Redis key with prefix.
        """
        return f"{self._key_prefix}{token}"

    async def create_state(self, metadata: dict | None = None) -> str:
        """Create a new state token with optional metadata.

        Generates a cryptographically secure token and stores it in Redis
        with the provided metadata and creation timestamp.

        Args:
            metadata: Optional dictionary of metadata to store with the token.
                     Common fields: redirect_uri, provider, nonce.

        Returns:
            The generated state token (URL-safe base64, 43 chars).

        Raises:
            StateStoreUnavailableError: If Redis connection fails.

        Example:
            state = await store.create_state({
                "redirect_uri": "http://localhost:8080/callback",
                "provider": "github"
            })
        """
        token = secrets.token_urlsafe(32)
        key = self._make_key(token)

        # Build payload with creation timestamp
        payload = {
            "created": time.time(),
            **(metadata or {}),
        }

        try:
            await self._redis.setex(
                key,
                self._ttl,
                json.dumps(payload),
            )
            logger.debug(
                "State token created",
                extra={"token_prefix": token[:16], "ttl": self._ttl},
            )
            return token

        except aioredis.RedisError as e:
            logger.error(f"Failed to create state token: {e}")
            raise StateStoreUnavailableError("Redis connection failed") from e

    async def validate_and_consume(self, state: str) -> dict | None:
        """Validate and consume a state token atomically.

        Retrieves and deletes the token in a single atomic operation,
        ensuring one-time use. Returns None if token is invalid, expired,
        or already consumed.

        Args:
            state: The state token to validate.

        Returns:
            The stored metadata dict if valid, None otherwise.
            Metadata includes 'created' timestamp and any custom fields.

        Raises:
            StateStoreUnavailableError: If Redis connection fails.

        Example:
            metadata = await store.validate_and_consume(state_from_callback)
            if metadata:
                redirect_uri = metadata.get("redirect_uri")
                # Continue OAuth flow
            else:
                raise HTTPException(400, "Invalid or expired state token")
        """
        key = self._make_key(state)

        try:
            # Try GETDEL (Redis 6.2+) for atomic get-and-delete
            try:
                value = await self._redis.getdel(key)
            except aioredis.ResponseError:
                # Fallback for older Redis: pipeline GET + DELETE
                pipe = self._redis.pipeline()
                pipe.get(key)
                pipe.delete(key)
                results = await pipe.execute()
                value = results[0]

            if value is None:
                logger.warning(
                    "Invalid or expired state token attempted",
                    extra={"token_prefix": state[:16] if len(state) >= 16 else state},
                )
                return None

            payload = json.loads(value)
            logger.debug(
                "State token validated and consumed",
                extra={"token_prefix": state[:16] if len(state) >= 16 else state},
            )
            return payload

        except aioredis.RedisError as e:
            logger.error(f"Failed to validate state token: {e}")
            raise StateStoreUnavailableError("Redis connection failed") from e
        except json.JSONDecodeError as e:
            logger.error(f"Corrupted state token data: {e}")
            return None

    async def cleanup_expired(self) -> int:
        """Manual cleanup of expired tokens.

        Note: Redis TTL handles expiration automatically. This method is
        provided for explicit cleanup or monitoring purposes.

        Returns:
            Number of expired tokens removed (always 0 with TTL).
        """
        # With TTL-based expiration, Redis handles cleanup automatically.
        # This method exists for interface completeness and potential
        # future use cases (e.g., cleanup by pattern).
        logger.debug("Cleanup called - Redis TTL handles expiration automatically")
        return 0


# Dependency injection for FastAPI
_redis_client: "Redis | None" = None


async def get_redis_client() -> "Redis":
    """Get or create the shared async Redis client.

    Returns:
        Async Redis client instance.

    Raises:
        StateStoreUnavailableError: If Redis connection fails.
    """
    global _redis_client

    if _redis_client is None:
        try:
            _redis_client = aioredis.from_url(
                REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
            )
            # Test connection
            await _redis_client.ping()
        except aioredis.RedisError as e:
            logger.error(f"Failed to connect to Redis: {e}")
            _redis_client = None
            raise StateStoreUnavailableError("Redis connection failed") from e

    return _redis_client


async def get_state_store() -> StateTokenStore:
    """FastAPI dependency for StateTokenStore.

    Usage:
        @router.get("/auth/github")
        async def github_auth(
            state_store: StateTokenStore = Depends(get_state_store)
        ):
            state = await state_store.create_state({"provider": "github"})
            return RedirectResponse(f"https://github.com/...?state={state}")

    Returns:
        StateTokenStore instance with configured Redis client.

    Raises:
        StateStoreUnavailableError: If Redis is unavailable.
    """
    redis = await get_redis_client()
    return StateTokenStore(redis)


async def close_redis_client() -> None:
    """Close the shared Redis client.

    Call during application shutdown to clean up connections.
    """
    global _redis_client

    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None
        logger.debug("Redis client closed")
