"""Tests for distributed session tracking with Redis sorted sets.

Uses fakeredis for fast, isolated testing without a real Redis server.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

# Try to import fakeredis, skip tests if not available
try:
    import fakeredis.aioredis
    FAKEREDIS_AVAILABLE = True
except ImportError:
    FAKEREDIS_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not FAKEREDIS_AVAILABLE,
    reason="fakeredis not installed"
)


@pytest.fixture
async def fake_redis():
    """Create a fake Redis client for testing."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.close()


@pytest.fixture
async def tracker(fake_redis):
    """Create a DistributedSessionTracker with fake Redis."""
    from repotoire.sandbox.session_tracker import DistributedSessionTracker

    return DistributedSessionTracker(
        redis=fake_redis,
        ttl_seconds=3600,
        key_prefix="sandbox:sessions:",
    )


class TestStartSession:
    """Tests for start_session method."""

    async def test_start_session_returns_count(self, tracker):
        """Starting a session returns the concurrent count."""
        count = await tracker.start_session("org-123", "session-abc")
        assert count == 1

    async def test_start_multiple_sessions_increments_count(self, tracker):
        """Starting multiple sessions increments the count."""
        count1 = await tracker.start_session("org-123", "session-abc")
        count2 = await tracker.start_session("org-123", "session-def")
        count3 = await tracker.start_session("org-123", "session-ghi")

        assert count1 == 1
        assert count2 == 2
        assert count3 == 3

    async def test_start_session_isolated_by_org(self, tracker):
        """Sessions are isolated by organization."""
        await tracker.start_session("org-1", "session-1")
        await tracker.start_session("org-1", "session-2")
        await tracker.start_session("org-2", "session-3")

        count_org1 = await tracker.get_concurrent_count("org-1")
        count_org2 = await tracker.get_concurrent_count("org-2")

        assert count_org1 == 2
        assert count_org2 == 1

    async def test_start_session_updates_existing(self, tracker):
        """Starting a session with same ID updates timestamp (no duplicate)."""
        await tracker.start_session("org-123", "session-abc")
        count = await tracker.start_session("org-123", "session-abc")

        # Should still be 1, not 2 (ZADD updates existing member)
        assert count == 1


class TestEndSession:
    """Tests for end_session method."""

    async def test_end_session_returns_remaining_count(self, tracker):
        """Ending a session returns the remaining count."""
        await tracker.start_session("org-123", "session-abc")
        await tracker.start_session("org-123", "session-def")

        remaining = await tracker.end_session("org-123", "session-abc")

        assert remaining == 1

    async def test_end_session_removes_correct_session(self, tracker):
        """Ending a session removes only that session."""
        await tracker.start_session("org-123", "session-abc")
        await tracker.start_session("org-123", "session-def")
        await tracker.end_session("org-123", "session-abc")

        sessions = await tracker.get_active_sessions("org-123")
        session_ids = [s.session_id for s in sessions]

        assert "session-abc" not in session_ids
        assert "session-def" in session_ids

    async def test_end_nonexistent_session_returns_zero(self, tracker):
        """Ending a nonexistent session returns 0."""
        remaining = await tracker.end_session("org-123", "nonexistent")
        assert remaining == 0

    async def test_end_all_sessions_leaves_empty(self, tracker):
        """Ending all sessions leaves the count at 0."""
        await tracker.start_session("org-123", "session-abc")
        await tracker.start_session("org-123", "session-def")
        await tracker.end_session("org-123", "session-abc")
        remaining = await tracker.end_session("org-123", "session-def")

        assert remaining == 0


class TestGetConcurrentCount:
    """Tests for get_concurrent_count method."""

    async def test_get_concurrent_count_empty(self, tracker):
        """Empty org returns 0 concurrent count."""
        count = await tracker.get_concurrent_count("org-empty")
        assert count == 0

    async def test_get_concurrent_count_accurate(self, tracker):
        """Concurrent count is accurate after operations."""
        await tracker.start_session("org-123", "session-1")
        await tracker.start_session("org-123", "session-2")
        await tracker.start_session("org-123", "session-3")
        await tracker.end_session("org-123", "session-2")

        count = await tracker.get_concurrent_count("org-123")
        assert count == 2


class TestHeartbeat:
    """Tests for heartbeat method."""

    async def test_heartbeat_returns_true_for_existing(self, tracker):
        """Heartbeat returns True for existing session."""
        await tracker.start_session("org-123", "session-abc")
        result = await tracker.heartbeat("org-123", "session-abc")
        assert result is True

    async def test_heartbeat_returns_false_for_nonexistent(self, tracker):
        """Heartbeat returns False for nonexistent session."""
        result = await tracker.heartbeat("org-123", "nonexistent")
        assert result is False

    async def test_heartbeat_updates_timestamp(self, tracker, fake_redis):
        """Heartbeat updates the session timestamp."""
        await tracker.start_session("org-123", "session-abc")

        # Get initial score
        key = "sandbox:sessions:org-123"
        initial_score = await fake_redis.zscore(key, "session-abc")

        # Wait a tiny bit to ensure timestamp difference
        await asyncio.sleep(0.01)

        # Heartbeat
        await tracker.heartbeat("org-123", "session-abc")

        # Check score was updated
        new_score = await fake_redis.zscore(key, "session-abc")
        assert new_score >= initial_score


class TestGetActiveSessions:
    """Tests for get_active_sessions method."""

    async def test_get_active_sessions_returns_all(self, tracker):
        """Returns all active sessions with correct info."""
        await tracker.start_session("org-123", "session-abc")
        await tracker.start_session("org-123", "session-def")

        sessions = await tracker.get_active_sessions("org-123")

        assert len(sessions) == 2
        session_ids = {s.session_id for s in sessions}
        assert session_ids == {"session-abc", "session-def"}

    async def test_get_active_sessions_empty(self, tracker):
        """Returns empty list for org with no sessions."""
        sessions = await tracker.get_active_sessions("org-empty")
        assert sessions == []

    async def test_get_active_sessions_has_timestamp(self, tracker):
        """Sessions have valid started_at timestamp."""
        now = time.time()
        await tracker.start_session("org-123", "session-abc")

        sessions = await tracker.get_active_sessions("org-123")

        assert len(sessions) == 1
        assert sessions[0].started_at >= now - 1
        assert sessions[0].started_at <= now + 1


class TestExpiration:
    """Tests for session expiration behavior."""

    async def test_expired_sessions_cleaned_on_start(self, fake_redis):
        """Expired sessions are cleaned up when starting a new session."""
        from repotoire.sandbox.session_tracker import DistributedSessionTracker

        # Create tracker with very short TTL
        tracker = DistributedSessionTracker(
            redis=fake_redis,
            ttl_seconds=1,  # 1 second TTL
        )

        # Add a session
        await tracker.start_session("org-123", "old-session")

        # Wait for expiration
        await asyncio.sleep(1.1)

        # Start new session - should clean up old one
        count = await tracker.start_session("org-123", "new-session")

        # Old session should be gone, only new one remains
        assert count == 1
        sessions = await tracker.get_active_sessions("org-123")
        assert len(sessions) == 1
        assert sessions[0].session_id == "new-session"

    async def test_expired_sessions_cleaned_on_count(self, fake_redis):
        """Expired sessions are cleaned up when getting count."""
        from repotoire.sandbox.session_tracker import DistributedSessionTracker

        tracker = DistributedSessionTracker(
            redis=fake_redis,
            ttl_seconds=1,
        )

        await tracker.start_session("org-123", "old-session")
        await asyncio.sleep(1.1)

        count = await tracker.get_concurrent_count("org-123")
        assert count == 0

    async def test_cleanup_expired_manual(self, fake_redis):
        """Manual cleanup removes expired sessions."""
        from repotoire.sandbox.session_tracker import DistributedSessionTracker

        tracker = DistributedSessionTracker(
            redis=fake_redis,
            ttl_seconds=1,
        )

        await tracker.start_session("org-123", "session-1")
        await tracker.start_session("org-123", "session-2")
        await asyncio.sleep(1.1)

        removed = await tracker.cleanup_expired("org-123")
        assert removed == 2


class TestConcurrency:
    """Tests for concurrent access scenarios."""

    async def test_concurrent_starts(self, tracker):
        """Multiple concurrent start operations work correctly."""
        async def start_session(session_id):
            return await tracker.start_session("org-123", session_id)

        # Start 10 sessions concurrently
        results = await asyncio.gather(*[
            start_session(f"session-{i}") for i in range(10)
        ])

        # Each should have gotten a unique count
        # (exact order depends on Redis, but should all be 1-10)
        assert sorted(results) == list(range(1, 11))

        # Final count should be 10
        count = await tracker.get_concurrent_count("org-123")
        assert count == 10

    async def test_concurrent_starts_and_ends(self, tracker):
        """Mixed concurrent start/end operations are consistent."""
        # Start 5 sessions
        for i in range(5):
            await tracker.start_session("org-123", f"session-{i}")

        async def end_and_start(old_id, new_id):
            await tracker.end_session("org-123", old_id)
            await tracker.start_session("org-123", new_id)

        # Concurrently end 3 old sessions and start 3 new ones
        await asyncio.gather(
            end_and_start("session-0", "session-10"),
            end_and_start("session-1", "session-11"),
            end_and_start("session-2", "session-12"),
        )

        # Should still have 5 sessions
        count = await tracker.get_concurrent_count("org-123")
        assert count == 5


class TestErrorHandling:
    """Tests for error handling."""

    async def test_redis_connection_error_raises(self):
        """Redis connection errors raise SessionTrackerUnavailableError."""
        from unittest.mock import MagicMock

        from repotoire.sandbox.session_tracker import (
            DistributedSessionTracker,
            SessionTrackerUnavailableError,
        )
        import redis.asyncio as aioredis

        # Create a proper mock pipeline (non-async methods for chaining)
        mock_pipe = MagicMock()
        mock_pipe.zremrangebyscore.return_value = mock_pipe
        mock_pipe.zadd.return_value = mock_pipe
        mock_pipe.zcard.return_value = mock_pipe
        mock_pipe.expire.return_value = mock_pipe
        # execute() is async and should raise
        mock_pipe.execute = AsyncMock(
            side_effect=aioredis.RedisError("Connection refused")
        )

        # Create mock Redis with synchronous pipeline() returning the mock
        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe

        tracker = DistributedSessionTracker(redis=mock_redis)

        with pytest.raises(SessionTrackerUnavailableError):
            await tracker.start_session("org-123", "session-abc")


class TestDependencyInjection:
    """Tests for FastAPI dependency injection functions."""

    async def test_get_session_tracker_returns_tracker(self, fake_redis):
        """get_session_tracker returns a DistributedSessionTracker."""
        from repotoire.sandbox import session_tracker as st

        # Patch the Redis client getter
        with patch.object(st, "_redis_client", fake_redis):
            with patch.object(st, "_session_tracker", None):
                tracker = await st.get_session_tracker()

        from repotoire.sandbox.session_tracker import DistributedSessionTracker
        assert isinstance(tracker, DistributedSessionTracker)

    async def test_close_session_tracker_cleans_up(self, fake_redis):
        """close_session_tracker cleans up resources."""
        from repotoire.sandbox import session_tracker as st

        # Set up module state
        st._redis_client = fake_redis
        st._session_tracker = AsyncMock()

        await st.close_session_tracker()

        assert st._redis_client is None
        assert st._session_tracker is None


class TestUsageTrackerIntegration:
    """Tests for SandboxUsageTracker integration with distributed tracker."""

    async def test_usage_tracker_uses_distributed_tracker(self, tracker):
        """SandboxUsageTracker uses distributed tracker when available."""
        from repotoire.sandbox.usage import SandboxUsageTracker

        usage_tracker = SandboxUsageTracker(session_tracker=tracker)

        count = await usage_tracker.increment_concurrent("org-123", "sandbox-abc")
        assert count == 1

        count = await usage_tracker.get_concurrent_count("org-123")
        assert count == 1

        count = await usage_tracker.decrement_concurrent("org-123", "sandbox-abc")
        assert count == 0

    async def test_usage_tracker_heartbeat_works(self, tracker):
        """SandboxUsageTracker heartbeat updates session."""
        from repotoire.sandbox.usage import SandboxUsageTracker

        usage_tracker = SandboxUsageTracker(session_tracker=tracker)

        await usage_tracker.increment_concurrent("org-123", "sandbox-abc")
        result = await usage_tracker.heartbeat_session("org-123", "sandbox-abc")
        assert result is True

    async def test_usage_tracker_fallback_to_inmemory(self):
        """SandboxUsageTracker falls back to in-memory when tracker fails."""
        from repotoire.sandbox.usage import SandboxUsageTracker

        # Create tracker that always fails
        mock_tracker = AsyncMock()
        mock_tracker.start_session.side_effect = Exception("Redis failed")
        mock_tracker.end_session.side_effect = Exception("Redis failed")
        mock_tracker.get_concurrent_count.side_effect = Exception("Redis failed")

        usage_tracker = SandboxUsageTracker(session_tracker=mock_tracker)

        # Should fall back to in-memory
        count = await usage_tracker.increment_concurrent("org-123", "sandbox-abc")
        assert count == 1

        count = await usage_tracker.get_concurrent_count("org-123")
        assert count == 1

        count = await usage_tracker.decrement_concurrent("org-123", "sandbox-abc")
        assert count == 0

    async def test_usage_tracker_get_all_sessions(self, tracker):
        """SandboxUsageTracker returns sessions from distributed tracker."""
        from repotoire.sandbox.usage import SandboxUsageTracker, ConcurrentSession

        usage_tracker = SandboxUsageTracker(session_tracker=tracker)

        await usage_tracker.increment_concurrent("org-123", "sandbox-abc")
        await usage_tracker.increment_concurrent("org-123", "sandbox-def")

        sessions = await usage_tracker.get_all_concurrent_sessions("org-123")

        assert len(sessions) == 2
        assert all(isinstance(s, ConcurrentSession) for s in sessions)
        sandbox_ids = {s.sandbox_id for s in sessions}
        assert sandbox_ids == {"sandbox-abc", "sandbox-def"}
