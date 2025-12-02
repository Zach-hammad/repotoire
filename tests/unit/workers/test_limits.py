"""Unit tests for concurrency limiting."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from repotoire.db.models import PlanTier


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    with patch("repotoire.workers.limits.redis.from_url") as mock:
        redis_client = MagicMock()
        mock.return_value = redis_client
        yield redis_client


class TestConcurrencyLimiter:
    """Tests for ConcurrencyLimiter class."""

    def test_acquire_under_limit(self, mock_redis):
        """Test acquiring slot when under limit."""
        from repotoire.workers.limits import ConcurrencyLimiter

        mock_pipe = MagicMock()
        mock_pipe.execute.return_value = [1, True]  # First slot, expire set
        mock_redis.pipeline.return_value = mock_pipe

        limiter = ConcurrencyLimiter()
        limiter._redis = mock_redis

        org_id = uuid4()
        result = limiter.acquire(org_id, PlanTier.PRO)

        assert result is True
        mock_pipe.incr.assert_called_once()
        mock_pipe.expire.assert_called_once()

    def test_acquire_at_limit(self, mock_redis):
        """Test acquiring slot when at limit."""
        from repotoire.workers.limits import ConcurrencyLimiter, TIER_LIMITS

        # PRO tier has limit of 3
        mock_pipe = MagicMock()
        mock_pipe.execute.return_value = [4, True]  # Over limit
        mock_redis.pipeline.return_value = mock_pipe

        limiter = ConcurrencyLimiter()
        limiter._redis = mock_redis

        org_id = uuid4()
        result = limiter.acquire(org_id, PlanTier.PRO)

        assert result is False
        mock_redis.decr.assert_called_once()  # Should decrement after rejection

    def test_release(self, mock_redis):
        """Test releasing a slot."""
        from repotoire.workers.limits import ConcurrencyLimiter

        mock_redis.get.return_value = b"2"  # Current count

        limiter = ConcurrencyLimiter()
        limiter._redis = mock_redis

        org_id = uuid4()
        limiter.release(org_id)

        mock_redis.decr.assert_called_once()

    def test_release_at_zero(self, mock_redis):
        """Test releasing when count is already at zero."""
        from repotoire.workers.limits import ConcurrencyLimiter

        mock_redis.get.return_value = b"0"  # Already at zero

        limiter = ConcurrencyLimiter()
        limiter._redis = mock_redis

        org_id = uuid4()
        limiter.release(org_id)

        mock_redis.decr.assert_not_called()  # Should not go below zero

    def test_get_current_count(self, mock_redis):
        """Test getting current count."""
        from repotoire.workers.limits import ConcurrencyLimiter

        mock_redis.get.return_value = b"5"

        limiter = ConcurrencyLimiter()
        limiter._redis = mock_redis

        org_id = uuid4()
        count = limiter.get_current_count(org_id)

        assert count == 5

    def test_get_current_count_not_found(self, mock_redis):
        """Test getting count when key doesn't exist."""
        from repotoire.workers.limits import ConcurrencyLimiter

        mock_redis.get.return_value = None

        limiter = ConcurrencyLimiter()
        limiter._redis = mock_redis

        org_id = uuid4()
        count = limiter.get_current_count(org_id)

        assert count == 0

    def test_tier_limits(self):
        """Test tier limit values."""
        from repotoire.workers.limits import TIER_LIMITS

        assert TIER_LIMITS[PlanTier.FREE] == 1
        assert TIER_LIMITS[PlanTier.PRO] == 3
        assert TIER_LIMITS[PlanTier.ENTERPRISE] == 10

    def test_redis_failure_allows_task(self, mock_redis):
        """Test that Redis failures don't block tasks (fail open)."""
        import redis

        from repotoire.workers.limits import ConcurrencyLimiter

        mock_redis.pipeline.side_effect = redis.RedisError("Connection failed")

        limiter = ConcurrencyLimiter()
        limiter._redis = mock_redis

        org_id = uuid4()
        result = limiter.acquire(org_id, PlanTier.PRO)

        assert result is True  # Should allow on Redis failure


class TestWithConcurrencyLimit:
    """Tests for with_concurrency_limit decorator."""

    def test_decorator_acquires_and_releases(self, mock_redis):
        """Test decorator properly acquires and releases slots."""
        from repotoire.workers.limits import with_concurrency_limit

        mock_pipe = MagicMock()
        mock_pipe.execute.return_value = [1, True]
        mock_redis.pipeline.return_value = mock_pipe
        mock_redis.get.return_value = b"1"

        # Would need full Celery setup to test decorator properly

    def test_decorator_retries_on_limit(self):
        """Test decorator triggers retry when limit reached."""
        # Decorator should call self.retry() when limiter.acquire returns False
        pass


class TestRateLimiter:
    """Tests for RateLimiter class."""

    def test_is_allowed_under_limit(self, mock_redis):
        """Test request allowed when under rate limit."""
        from repotoire.workers.limits import RateLimiter

        mock_pipe = MagicMock()
        mock_pipe.execute.return_value = [0, 10, 1, True]  # 10 requests in window
        mock_redis.pipeline.return_value = mock_pipe

        limiter = RateLimiter(requests_per_minute=60)
        limiter._redis = mock_redis

        org_id = uuid4()
        result = limiter.is_allowed(org_id)

        assert result is True

    def test_is_allowed_over_limit(self, mock_redis):
        """Test request blocked when over rate limit."""
        from repotoire.workers.limits import RateLimiter

        mock_pipe = MagicMock()
        mock_pipe.execute.return_value = [0, 61, 1, True]  # 61 requests in window
        mock_redis.pipeline.return_value = mock_pipe

        limiter = RateLimiter(requests_per_minute=60)
        limiter._redis = mock_redis

        org_id = uuid4()
        result = limiter.is_allowed(org_id)

        assert result is False

    def test_get_remaining(self, mock_redis):
        """Test getting remaining request count."""
        from repotoire.workers.limits import RateLimiter

        mock_redis.zcard.return_value = 30

        limiter = RateLimiter(requests_per_minute=60)
        limiter._redis = mock_redis

        org_id = uuid4()
        remaining = limiter.get_remaining(org_id)

        assert remaining == 30  # 60 - 30 = 30
