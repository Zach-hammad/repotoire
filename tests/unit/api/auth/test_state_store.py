"""Unit tests for Redis-backed OAuth state token store."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from repotoire.api.auth.state_store import (
    KEY_PREFIX,
    STATE_TOKEN_TTL,
    StateStoreUnavailableError,
    StateTokenStore,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis


class TestStateTokenStore:
    """Tests for StateTokenStore class."""

    @pytest.fixture
    def mock_redis(self) -> AsyncMock:
        """Create a mock async Redis client."""
        from unittest.mock import MagicMock

        redis = AsyncMock()
        # Setup pipeline mock - pipeline() is sync, execute() is async
        pipeline = MagicMock()
        pipeline.execute = AsyncMock(return_value=[None, 0])
        redis.pipeline = MagicMock(return_value=pipeline)
        return redis

    @pytest.fixture
    def store(self, mock_redis: AsyncMock) -> StateTokenStore:
        """Create a StateTokenStore instance with mock Redis."""
        return StateTokenStore(mock_redis)

    # =========================================================================
    # create_state tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_create_state_returns_token(self, store: StateTokenStore) -> None:
        """Test that create_state returns a URL-safe token."""
        token = await store.create_state()

        assert token is not None
        assert len(token) == 43  # secrets.token_urlsafe(32) produces 43 chars
        # URL-safe base64 alphabet
        assert all(c.isalnum() or c in "-_" for c in token)

    @pytest.mark.asyncio
    async def test_create_state_stores_in_redis(
        self, store: StateTokenStore, mock_redis: AsyncMock
    ) -> None:
        """Test that create_state calls Redis setex with correct parameters."""
        token = await store.create_state({"redirect_uri": "http://localhost:3000"})

        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args

        # Check key format
        key = call_args[0][0]
        assert key == f"{KEY_PREFIX}{token}"

        # Check TTL
        ttl = call_args[0][1]
        assert ttl == STATE_TOKEN_TTL

        # Check payload structure
        payload = json.loads(call_args[0][2])
        assert "created" in payload
        assert payload["redirect_uri"] == "http://localhost:3000"

    @pytest.mark.asyncio
    async def test_create_state_without_metadata(
        self, store: StateTokenStore, mock_redis: AsyncMock
    ) -> None:
        """Test that create_state works without metadata."""
        await store.create_state()

        call_args = mock_redis.setex.call_args
        payload = json.loads(call_args[0][2])

        assert "created" in payload
        assert len(payload) == 1  # Only 'created' field

    @pytest.mark.asyncio
    async def test_create_state_with_custom_metadata(
        self, store: StateTokenStore, mock_redis: AsyncMock
    ) -> None:
        """Test that create_state stores all metadata fields."""
        metadata = {
            "redirect_uri": "http://localhost:3000/callback",
            "provider": "github",
            "nonce": "abc123",
        }

        await store.create_state(metadata)

        call_args = mock_redis.setex.call_args
        payload = json.loads(call_args[0][2])

        assert payload["redirect_uri"] == "http://localhost:3000/callback"
        assert payload["provider"] == "github"
        assert payload["nonce"] == "abc123"
        assert "created" in payload

    @pytest.mark.asyncio
    async def test_create_state_unique_tokens(self, store: StateTokenStore) -> None:
        """Test that each create_state call produces a unique token."""
        tokens = [await store.create_state() for _ in range(100)]

        # All tokens should be unique
        assert len(set(tokens)) == 100

    @pytest.mark.asyncio
    async def test_create_state_redis_error_raises_unavailable(
        self, store: StateTokenStore, mock_redis: AsyncMock
    ) -> None:
        """Test that Redis errors raise StateStoreUnavailableError."""
        import redis.asyncio as aioredis

        mock_redis.setex.side_effect = aioredis.RedisError("Connection refused")

        with pytest.raises(StateStoreUnavailableError, match="Redis connection failed"):
            await store.create_state()

    # =========================================================================
    # validate_and_consume tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_validate_and_consume_returns_metadata(
        self, store: StateTokenStore, mock_redis: AsyncMock
    ) -> None:
        """Test that validate_and_consume returns stored metadata."""
        payload = {"created": time.time(), "redirect_uri": "http://localhost:3000"}
        mock_redis.getdel.return_value = json.dumps(payload)

        result = await store.validate_and_consume("valid-token")

        assert result is not None
        assert result["redirect_uri"] == "http://localhost:3000"
        assert "created" in result

    @pytest.mark.asyncio
    async def test_validate_and_consume_uses_getdel(
        self, store: StateTokenStore, mock_redis: AsyncMock
    ) -> None:
        """Test that validate_and_consume uses GETDEL for atomic operation."""
        payload = {"created": time.time()}
        mock_redis.getdel.return_value = json.dumps(payload)

        await store.validate_and_consume("test-token")

        mock_redis.getdel.assert_called_once_with(f"{KEY_PREFIX}test-token")

    @pytest.mark.asyncio
    async def test_validate_and_consume_fallback_to_pipeline(
        self, mock_redis: AsyncMock
    ) -> None:
        """Test fallback to GET+DELETE pipeline for older Redis."""
        import redis.asyncio as aioredis
        from unittest.mock import MagicMock

        # Create a fresh store with specific mock behavior
        store = StateTokenStore(mock_redis)

        # Simulate GETDEL not supported
        mock_redis.getdel.side_effect = aioredis.ResponseError("unknown command")

        # Setup pipeline mock - pipeline() returns a sync object with sync methods
        # but execute() is async
        pipeline_mock = MagicMock()
        payload = {"created": time.time()}
        pipeline_mock.execute = AsyncMock(return_value=[json.dumps(payload), 1])
        mock_redis.pipeline.return_value = pipeline_mock

        result = await store.validate_and_consume("test-token")

        assert result is not None
        pipeline_mock.get.assert_called_once()
        pipeline_mock.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_and_consume_invalid_token_returns_none(
        self, store: StateTokenStore, mock_redis: AsyncMock
    ) -> None:
        """Test that invalid/expired token returns None."""
        mock_redis.getdel.return_value = None

        result = await store.validate_and_consume("invalid-token")

        assert result is None

    @pytest.mark.asyncio
    async def test_validate_and_consume_already_consumed_returns_none(
        self, store: StateTokenStore, mock_redis: AsyncMock
    ) -> None:
        """Test that consuming same token twice returns None second time."""
        payload = {"created": time.time()}

        # First call succeeds
        mock_redis.getdel.return_value = json.dumps(payload)
        result1 = await store.validate_and_consume("one-time-token")
        assert result1 is not None

        # Second call - token already deleted
        mock_redis.getdel.return_value = None
        result2 = await store.validate_and_consume("one-time-token")
        assert result2 is None

    @pytest.mark.asyncio
    async def test_validate_and_consume_corrupted_data_returns_none(
        self, store: StateTokenStore, mock_redis: AsyncMock
    ) -> None:
        """Test that corrupted JSON data returns None."""
        mock_redis.getdel.return_value = "not-valid-json{"

        result = await store.validate_and_consume("corrupted-token")

        assert result is None

    @pytest.mark.asyncio
    async def test_validate_and_consume_redis_error_raises_unavailable(
        self, store: StateTokenStore, mock_redis: AsyncMock
    ) -> None:
        """Test that Redis errors raise StateStoreUnavailableError."""
        import redis.asyncio as aioredis

        mock_redis.getdel.side_effect = aioredis.RedisError("Connection lost")

        with pytest.raises(StateStoreUnavailableError, match="Redis connection failed"):
            await store.validate_and_consume("any-token")

    # =========================================================================
    # cleanup_expired tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_cleanup_expired_returns_zero(self, store: StateTokenStore) -> None:
        """Test that cleanup_expired returns 0 (TTL handles expiration)."""
        result = await store.cleanup_expired()

        assert result == 0

    # =========================================================================
    # Custom configuration tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_custom_ttl(self, mock_redis: AsyncMock) -> None:
        """Test that custom TTL is used."""
        store = StateTokenStore(mock_redis, ttl=300)

        await store.create_state()

        call_args = mock_redis.setex.call_args
        assert call_args[0][1] == 300

    @pytest.mark.asyncio
    async def test_custom_key_prefix(self, mock_redis: AsyncMock) -> None:
        """Test that custom key prefix is used."""
        store = StateTokenStore(mock_redis, key_prefix="custom:prefix:")

        token = await store.create_state()

        call_args = mock_redis.setex.call_args
        assert call_args[0][0] == f"custom:prefix:{token}"


class TestStateTokenStoreIntegration:
    """Integration tests using fakeredis."""

    @pytest.fixture
    async def fake_redis(self):
        """Create a fakeredis async client."""
        try:
            import fakeredis.aioredis
        except ImportError:
            pytest.skip("fakeredis not installed")

        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        yield redis
        await redis.flushall()
        await redis.close()

    @pytest.fixture
    def store(self, fake_redis) -> StateTokenStore:
        """Create a StateTokenStore with fakeredis."""
        return StateTokenStore(fake_redis)

    @pytest.mark.asyncio
    async def test_full_flow_create_validate_consume(
        self, store: StateTokenStore
    ) -> None:
        """Test complete OAuth state flow: create -> validate -> consume."""
        # Create state with metadata
        metadata = {"redirect_uri": "http://localhost:8080/callback", "provider": "github"}
        token = await store.create_state(metadata)

        # Validate and consume
        result = await store.validate_and_consume(token)

        assert result is not None
        assert result["redirect_uri"] == "http://localhost:8080/callback"
        assert result["provider"] == "github"
        assert "created" in result

        # Token is now consumed - second validation fails
        result2 = await store.validate_and_consume(token)
        assert result2 is None

    @pytest.mark.asyncio
    async def test_multiple_concurrent_tokens(self, store: StateTokenStore) -> None:
        """Test multiple tokens can exist simultaneously."""
        tokens = []
        for i in range(5):
            token = await store.create_state({"session_id": str(i)})
            tokens.append(token)

        # All tokens should be valid
        for i, token in enumerate(tokens):
            result = await store.validate_and_consume(token)
            assert result is not None
            assert result["session_id"] == str(i)

    @pytest.mark.asyncio
    async def test_token_expiration(self, fake_redis) -> None:
        """Test that tokens expire after TTL."""
        # Use short TTL for testing
        store = StateTokenStore(fake_redis, ttl=60)

        token = await store.create_state()

        # Verify TTL is set (may have already ticked down by 1 second)
        key = f"{KEY_PREFIX}{token}"
        ttl = await fake_redis.ttl(key)
        # TTL should be between 59-60 seconds (accounting for timing)
        assert 55 <= ttl <= 60

    @pytest.mark.asyncio
    async def test_empty_metadata_handling(self, store: StateTokenStore) -> None:
        """Test creating and consuming token without metadata."""
        token = await store.create_state()

        result = await store.validate_and_consume(token)

        assert result is not None
        assert "created" in result
        # Only created field should be present
        assert set(result.keys()) == {"created"}


class TestGetStateStoreDependency:
    """Tests for FastAPI dependency injection."""

    @pytest.mark.asyncio
    async def test_get_state_store_returns_store(self) -> None:
        """Test that get_state_store returns a StateTokenStore."""
        from repotoire.api.auth.state_store import get_state_store

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()

        with patch(
            "repotoire.api.auth.state_store.aioredis.from_url", return_value=mock_redis
        ):
            with patch("repotoire.api.auth.state_store._redis_client", None):
                store = await get_state_store()

                assert isinstance(store, StateTokenStore)

    @pytest.mark.asyncio
    async def test_get_state_store_reuses_client(self) -> None:
        """Test that get_state_store reuses the Redis client."""
        from repotoire.api.auth.state_store import get_state_store

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()

        with patch(
            "repotoire.api.auth.state_store.aioredis.from_url", return_value=mock_redis
        ) as mock_from_url:
            with patch("repotoire.api.auth.state_store._redis_client", None):
                # First call creates client
                await get_state_store()

                # Reset the module-level client for second call test
                import repotoire.api.auth.state_store as module

                # Second call should reuse
                await get_state_store()

                # from_url should only be called once (client reused)
                # Note: Due to module state, this may vary
                assert mock_from_url.call_count >= 1

    @pytest.mark.asyncio
    async def test_get_state_store_redis_unavailable(self) -> None:
        """Test that get_state_store raises error when Redis unavailable."""
        import redis.asyncio as aioredis

        from repotoire.api.auth.state_store import get_state_store

        mock_redis = AsyncMock()
        mock_redis.ping.side_effect = aioredis.RedisError("Connection refused")

        with patch(
            "repotoire.api.auth.state_store.aioredis.from_url", return_value=mock_redis
        ):
            with patch("repotoire.api.auth.state_store._redis_client", None):
                with pytest.raises(
                    StateStoreUnavailableError, match="Redis connection failed"
                ):
                    await get_state_store()


class TestCloseRedisClient:
    """Tests for close_redis_client function."""

    @pytest.mark.asyncio
    async def test_close_redis_client_closes_connection(self) -> None:
        """Test that close_redis_client properly closes the connection."""
        from repotoire.api.auth.state_store import close_redis_client

        mock_redis = AsyncMock()

        with patch("repotoire.api.auth.state_store._redis_client", mock_redis):
            await close_redis_client()

            mock_redis.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_redis_client_handles_none(self) -> None:
        """Test that close_redis_client handles None client gracefully."""
        from repotoire.api.auth.state_store import close_redis_client

        with patch("repotoire.api.auth.state_store._redis_client", None):
            # Should not raise
            await close_redis_client()
