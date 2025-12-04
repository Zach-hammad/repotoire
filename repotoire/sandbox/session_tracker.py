"""Redis-backed distributed session tracking using sorted sets.

This module provides distributed session tracking for sandbox quotas using Redis
sorted sets for O(1) counting and automatic expiration of stale sessions.

Key features:
- Redis sorted sets for efficient concurrent session tracking
- Automatic cleanup of expired sessions (no heartbeat > TTL)
- Pipeline batching for efficient Redis round trips
- Graceful degradation when Redis unavailable
- FastAPI dependency injection support

Example:
    ```python
    from repotoire.sandbox.session_tracker import (
        DistributedSessionTracker,
        get_session_tracker,
    )

    tracker = await get_session_tracker()

    # Start a session
    count = await tracker.start_session("org-123", "session-abc")
    print(f"Concurrent sessions: {count}")

    # Heartbeat to keep session alive
    await tracker.heartbeat("org-123", "session-abc")

    # End session
    remaining = await tracker.end_session("org-123", "session-abc")
    ```
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import redis.asyncio as aioredis

from repotoire.logging_config import get_logger
from repotoire.sandbox.exceptions import SandboxError

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = get_logger(__name__)

# Configuration
SESSION_TTL_SECONDS = 3600  # 1 hour - sessions without heartbeat expire
KEY_EXPIRY_SECONDS = 7200  # 2 hours - safety net for abandoned keys
KEY_PREFIX = "sandbox:sessions:"
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


class SessionTrackerError(SandboxError):
    """Base exception for session tracker errors."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class SessionTrackerUnavailableError(SessionTrackerError):
    """Raised when Redis is unavailable for session tracking."""

    pass


@dataclass
class SessionInfo:
    """Information about an active session.

    Attributes:
        session_id: Unique session identifier.
        started_at: Unix timestamp when session started (or last heartbeat).
    """

    session_id: str
    started_at: float


class DistributedSessionTracker:
    """Track concurrent sandbox sessions using Redis sorted sets.

    Uses Redis sorted sets where:
    - Key: sandbox:sessions:{org_id}
    - Member: session_id
    - Score: Unix timestamp of session start or last heartbeat

    Expired sessions (score < now - TTL) are cleaned up on every operation.

    Example:
        ```python
        tracker = DistributedSessionTracker(redis_client)

        # Start session (returns current count)
        count = await tracker.start_session("org-123", "session-abc")
        if count >= MAX_CONCURRENT:
            await tracker.end_session("org-123", "session-abc")
            raise QuotaExceededError("Max concurrent sessions reached")

        # Long-running operation with heartbeat
        while processing:
            await tracker.heartbeat("org-123", "session-abc")
            await process_chunk()

        # End session
        await tracker.end_session("org-123", "session-abc")
        ```
    """

    def __init__(
        self,
        redis: "Redis",
        ttl_seconds: int = SESSION_TTL_SECONDS,
        key_prefix: str = KEY_PREFIX,
    ) -> None:
        """Initialize the session tracker.

        Args:
            redis: Async Redis client instance.
            ttl_seconds: Session TTL in seconds (default: 3600).
            key_prefix: Redis key prefix for session sets.
        """
        self._redis = redis
        self._ttl_seconds = ttl_seconds
        self._key_prefix = key_prefix

    def _make_key(self, org_id: str) -> str:
        """Generate Redis key for an organization's sessions.

        Args:
            org_id: Organization identifier.

        Returns:
            Full Redis key with prefix.
        """
        return f"{self._key_prefix}{org_id}"

    def _get_expiry_threshold(self) -> float:
        """Get the Unix timestamp threshold for expired sessions.

        Sessions with scores below this value should be removed.

        Returns:
            Unix timestamp threshold.
        """
        return time.time() - self._ttl_seconds

    async def start_session(self, org_id: str, session_id: str) -> int:
        """Start tracking a new session.

        Adds the session to the sorted set with current timestamp as score.
        Cleans up expired sessions and returns the current count.

        Args:
            org_id: Organization identifier.
            session_id: Unique session identifier.

        Returns:
            Current number of active sessions (including the new one).

        Raises:
            SessionTrackerUnavailableError: If Redis connection fails.
        """
        key = self._make_key(org_id)
        now = time.time()
        expiry_threshold = self._get_expiry_threshold()

        try:
            # Pipeline: cleanup expired + add session + count + set key expiry
            pipe = self._redis.pipeline()
            pipe.zremrangebyscore(key, "-inf", expiry_threshold)
            pipe.zadd(key, {session_id: now})
            pipe.zcard(key)
            pipe.expire(key, KEY_EXPIRY_SECONDS)
            results = await pipe.execute()

            count = results[2]

            logger.debug(
                "Started session",
                extra={
                    "org_id": org_id,
                    "session_id": session_id,
                    "concurrent_count": count,
                },
            )

            return count

        except aioredis.RedisError as e:
            logger.error(f"Failed to start session: {e}")
            raise SessionTrackerUnavailableError(f"Redis connection failed: {e}") from e

    async def end_session(self, org_id: str, session_id: str) -> int:
        """End tracking for a session.

        Removes the session from the sorted set and returns remaining count.

        Args:
            org_id: Organization identifier.
            session_id: Session identifier to remove.

        Returns:
            Remaining number of active sessions.

        Raises:
            SessionTrackerUnavailableError: If Redis connection fails.
        """
        key = self._make_key(org_id)
        expiry_threshold = self._get_expiry_threshold()

        try:
            # Pipeline: cleanup expired + remove session + count
            pipe = self._redis.pipeline()
            pipe.zremrangebyscore(key, "-inf", expiry_threshold)
            pipe.zrem(key, session_id)
            pipe.zcard(key)
            results = await pipe.execute()

            count = results[2]

            logger.debug(
                "Ended session",
                extra={
                    "org_id": org_id,
                    "session_id": session_id,
                    "remaining_count": count,
                },
            )

            return count

        except aioredis.RedisError as e:
            logger.error(f"Failed to end session: {e}")
            raise SessionTrackerUnavailableError(f"Redis connection failed: {e}") from e

    async def get_concurrent_count(self, org_id: str) -> int:
        """Get count of currently active sessions.

        Cleans up expired sessions before counting for accuracy.

        Args:
            org_id: Organization identifier.

        Returns:
            Number of active sessions (0 if none or on error).

        Raises:
            SessionTrackerUnavailableError: If Redis connection fails.
        """
        key = self._make_key(org_id)
        expiry_threshold = self._get_expiry_threshold()

        try:
            # Pipeline: cleanup expired + count
            pipe = self._redis.pipeline()
            pipe.zremrangebyscore(key, "-inf", expiry_threshold)
            pipe.zcard(key)
            results = await pipe.execute()

            count = results[1]

            logger.debug(
                "Got concurrent count",
                extra={
                    "org_id": org_id,
                    "count": count,
                },
            )

            return count

        except aioredis.RedisError as e:
            logger.error(f"Failed to get concurrent count: {e}")
            raise SessionTrackerUnavailableError(f"Redis connection failed: {e}") from e

    async def heartbeat(self, org_id: str, session_id: str) -> bool:
        """Update session timestamp to prevent expiration.

        Updates the score (timestamp) for the session to current time.
        Should be called periodically for long-running sandboxes.

        Args:
            org_id: Organization identifier.
            session_id: Session identifier to refresh.

        Returns:
            True if session was found and updated, False if not found.

        Raises:
            SessionTrackerUnavailableError: If Redis connection fails.
        """
        key = self._make_key(org_id)
        now = time.time()

        try:
            # ZADD with XX flag only updates existing members
            # Returns 0 if updated, 0 if not found (XX means no add)
            # Use pipeline with ZSCORE first to check existence
            pipe = self._redis.pipeline()
            pipe.zscore(key, session_id)
            pipe.zadd(key, {session_id: now}, xx=True)
            results = await pipe.execute()

            existed = results[0] is not None

            if existed:
                logger.debug(
                    "Session heartbeat",
                    extra={
                        "org_id": org_id,
                        "session_id": session_id,
                    },
                )
            else:
                logger.warning(
                    "Heartbeat for unknown session",
                    extra={
                        "org_id": org_id,
                        "session_id": session_id,
                    },
                )

            return existed

        except aioredis.RedisError as e:
            logger.error(f"Failed to heartbeat session: {e}")
            raise SessionTrackerUnavailableError(f"Redis connection failed: {e}") from e

    async def get_active_sessions(self, org_id: str) -> list[SessionInfo]:
        """Get all active sessions for an organization.

        Returns sessions that haven't expired (within TTL window).

        Args:
            org_id: Organization identifier.

        Returns:
            List of SessionInfo objects for active sessions.

        Raises:
            SessionTrackerUnavailableError: If Redis connection fails.
        """
        key = self._make_key(org_id)
        expiry_threshold = self._get_expiry_threshold()
        now = time.time()

        try:
            # Pipeline: cleanup expired + get all with scores
            pipe = self._redis.pipeline()
            pipe.zremrangebyscore(key, "-inf", expiry_threshold)
            pipe.zrangebyscore(key, expiry_threshold, now, withscores=True)
            results = await pipe.execute()

            sessions_with_scores = results[1]

            sessions = [
                SessionInfo(session_id=session_id, started_at=score)
                for session_id, score in sessions_with_scores
            ]

            logger.debug(
                "Got active sessions",
                extra={
                    "org_id": org_id,
                    "session_count": len(sessions),
                },
            )

            return sessions

        except aioredis.RedisError as e:
            logger.error(f"Failed to get active sessions: {e}")
            raise SessionTrackerUnavailableError(f"Redis connection failed: {e}") from e

    async def cleanup_expired(self, org_id: str) -> int:
        """Explicitly clean up expired sessions.

        Normally cleanup happens automatically on every operation,
        but this method can be used for explicit maintenance.

        Args:
            org_id: Organization identifier.

        Returns:
            Number of expired sessions removed.

        Raises:
            SessionTrackerUnavailableError: If Redis connection fails.
        """
        key = self._make_key(org_id)
        expiry_threshold = self._get_expiry_threshold()

        try:
            removed = await self._redis.zremrangebyscore(key, "-inf", expiry_threshold)

            if removed > 0:
                logger.info(
                    "Cleaned up expired sessions",
                    extra={
                        "org_id": org_id,
                        "removed_count": removed,
                    },
                )

            return removed

        except aioredis.RedisError as e:
            logger.error(f"Failed to cleanup expired sessions: {e}")
            raise SessionTrackerUnavailableError(f"Redis connection failed: {e}") from e


# Dependency injection for FastAPI
_redis_client: "Redis | None" = None
_session_tracker: DistributedSessionTracker | None = None


async def get_redis_client() -> "Redis":
    """Get or create the shared async Redis client.

    Returns:
        Async Redis client instance.

    Raises:
        SessionTrackerUnavailableError: If Redis connection fails.
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
            logger.debug("Redis client connected for session tracking")
        except aioredis.RedisError as e:
            logger.error(f"Failed to connect to Redis: {e}")
            _redis_client = None
            raise SessionTrackerUnavailableError(f"Redis connection failed: {e}") from e

    return _redis_client


async def get_session_tracker() -> DistributedSessionTracker:
    """FastAPI dependency for DistributedSessionTracker.

    Usage:
        @router.post("/sandbox")
        async def create_sandbox(
            tracker: DistributedSessionTracker = Depends(get_session_tracker)
        ):
            count = await tracker.start_session(org_id, session_id)
            if count > quota.max_concurrent:
                await tracker.end_session(org_id, session_id)
                raise HTTPException(429, "Concurrent session limit exceeded")
            ...

    Returns:
        DistributedSessionTracker instance with configured Redis client.

    Raises:
        SessionTrackerUnavailableError: If Redis is unavailable.
    """
    global _session_tracker

    if _session_tracker is None:
        redis = await get_redis_client()
        _session_tracker = DistributedSessionTracker(redis)

    return _session_tracker


async def close_session_tracker() -> None:
    """Close the shared session tracker and Redis client.

    Call during application shutdown to clean up connections.
    """
    global _redis_client, _session_tracker

    _session_tracker = None

    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None
        logger.debug("Session tracker Redis client closed")
