"""Unit tests for idempotency middleware."""

import pytest
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

# Import directly from the module to avoid app.py import chain
# which triggers missing billing module
import importlib.util
from pathlib import Path

# Load the module directly
module_path = Path(__file__).parent.parent.parent.parent / "repotoire" / "api" / "shared" / "middleware" / "idempotency.py"
spec = importlib.util.spec_from_file_location("idempotency", module_path)
idempotency_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(idempotency_module)

IdempotencyStore = idempotency_module.IdempotencyStore
IdempotencyMiddleware = idempotency_module.IdempotencyMiddleware
IDEMPOTENCY_KEY_HEADER = idempotency_module.IDEMPOTENCY_KEY_HEADER
IDEMPOTENCY_REPLAYED_HEADER = idempotency_module.IDEMPOTENCY_REPLAYED_HEADER
DEFAULT_TTL_SECONDS = idempotency_module.DEFAULT_TTL_SECONDS
DEFAULT_MAX_KEY_LENGTH = idempotency_module.DEFAULT_MAX_KEY_LENGTH
IDEMPOTENT_METHODS = idempotency_module.IDEMPOTENT_METHODS
get_idempotency_store = idempotency_module.get_idempotency_store


class TestIdempotencyStoreBasics:
    """Test basic IdempotencyStore operations."""

    def test_set_and_get_returns_cached_response(self):
        """Test that set/get works for basic caching."""
        store = IdempotencyStore()
        response_data = {"status_code": 200, "body": {"result": "success"}}

        store.set("test-key", response_data)
        cached = store.get("test-key")

        assert cached == response_data

    def test_get_nonexistent_key_returns_none(self):
        """Test that missing keys return None."""
        store = IdempotencyStore()

        result = store.get("nonexistent-key")

        assert result is None

    def test_user_id_isolation(self):
        """Test that keys are isolated by user_id."""
        store = IdempotencyStore()
        response1 = {"status_code": 200, "body": {"user": "1"}}
        response2 = {"status_code": 200, "body": {"user": "2"}}

        store.set("same-key", response1, user_id="user-1")
        store.set("same-key", response2, user_id="user-2")

        assert store.get("same-key", user_id="user-1") == response1
        assert store.get("same-key", user_id="user-2") == response2

    def test_same_key_without_user_id_overwrites(self):
        """Test that same key without user_id overwrites."""
        store = IdempotencyStore()
        response1 = {"status_code": 200, "body": {"v": 1}}
        response2 = {"status_code": 200, "body": {"v": 2}}

        store.set("key", response1)
        store.set("key", response2)

        assert store.get("key") == response2


class TestIdempotencyStoreTTL:
    """Test TTL-based expiration."""

    def test_expired_entry_returns_none(self):
        """Test that expired entries are not returned."""
        store = IdempotencyStore(ttl_seconds=1)
        response_data = {"status_code": 200, "body": {}}

        store.set("key", response_data)

        # Manually expire the entry
        cache_key = store._make_key("key")
        store._cache[cache_key] = (response_data, time.time() - 2)

        assert store.get("key") is None

    def test_non_expired_entry_returns_value(self):
        """Test that non-expired entries are returned."""
        store = IdempotencyStore(ttl_seconds=60)
        response_data = {"status_code": 200, "body": {"data": "test"}}

        store.set("key", response_data)

        assert store.get("key") == response_data

    def test_clear_expired_removes_old_entries(self):
        """Test that clear_expired removes expired entries."""
        store = IdempotencyStore(ttl_seconds=1)

        # Add entries
        store.set("key1", {"status_code": 200, "body": {}})
        store.set("key2", {"status_code": 200, "body": {}})

        # Manually expire one entry
        cache_key1 = store._make_key("key1")
        store._cache[cache_key1] = ({"status_code": 200, "body": {}}, time.time() - 2)

        removed = store.clear_expired()

        assert removed == 1
        assert store.get("key1") is None
        assert store.get("key2") is not None


class TestIdempotencyStoreCapacity:
    """Test capacity limits and eviction."""

    def test_evicts_oldest_when_at_capacity(self):
        """Test that oldest entries are evicted when max_size reached."""
        store = IdempotencyStore(max_size=3)

        # Fill to capacity
        store.set("key1", {"status_code": 200, "body": {"k": 1}})
        time.sleep(0.01)  # Ensure different timestamps
        store.set("key2", {"status_code": 200, "body": {"k": 2}})
        time.sleep(0.01)
        store.set("key3", {"status_code": 200, "body": {"k": 3}})

        # Add one more - should evict oldest (key1)
        store.set("key4", {"status_code": 200, "body": {"k": 4}})

        # key1 should be evicted (oldest)
        assert store.get("key1") is None
        assert store.get("key4") is not None


class TestIdempotencyStoreKeyHashing:
    """Test cache key generation."""

    def test_make_key_without_user_id(self):
        """Test key generation without user context."""
        store = IdempotencyStore()

        key1 = store._make_key("test-key")
        key2 = store._make_key("test-key")

        assert key1 == key2
        assert len(key1) == 64  # SHA256 hex digest

    def test_make_key_with_user_id(self):
        """Test key generation with user context."""
        store = IdempotencyStore()

        key_with_user = store._make_key("test-key", user_id="user-123")
        key_without_user = store._make_key("test-key")

        assert key_with_user != key_without_user


class TestIdempotencyMiddlewareConstants:
    """Test middleware constants and configuration."""

    def test_header_constants(self):
        """Test header name constants."""
        assert IDEMPOTENCY_KEY_HEADER == "Idempotency-Key"
        assert IDEMPOTENCY_REPLAYED_HEADER == "X-Idempotency-Replayed"

    def test_default_configuration(self):
        """Test default configuration values."""
        assert DEFAULT_TTL_SECONDS == 86400  # 24 hours
        assert DEFAULT_MAX_KEY_LENGTH == 64

    def test_idempotent_methods(self):
        """Test which methods are considered idempotent."""
        assert "POST" in IDEMPOTENT_METHODS
        assert "PUT" in IDEMPOTENT_METHODS
        assert "PATCH" in IDEMPOTENT_METHODS
        assert "GET" not in IDEMPOTENT_METHODS
        assert "DELETE" not in IDEMPOTENT_METHODS


class TestGetIdempotencyStore:
    """Test global store access."""

    def test_returns_store_instance(self):
        """Test that get_idempotency_store returns a store."""
        store = get_idempotency_store()

        assert isinstance(store, IdempotencyStore)

    def test_returns_same_instance(self):
        """Test that get_idempotency_store returns singleton."""
        store1 = get_idempotency_store()
        store2 = get_idempotency_store()

        assert store1 is store2


class TestIdempotencyMiddlewareInit:
    """Test middleware initialization."""

    def test_init_with_custom_store(self):
        """Test initialization with custom store."""
        custom_store = IdempotencyStore(ttl_seconds=100)
        app = MagicMock()

        middleware = IdempotencyMiddleware(app, store=custom_store)

        assert middleware.store is custom_store

    def test_init_with_custom_max_key_length(self):
        """Test initialization with custom max key length."""
        app = MagicMock()

        middleware = IdempotencyMiddleware(app, max_key_length=128)

        assert middleware.max_key_length == 128


class TestIdempotencyMiddlewareDispatch:
    """Test middleware dispatch logic.

    Note: These tests use asyncio.run() instead of pytest.mark.asyncio
    since pytest-asyncio is not installed in the test environment.
    """

    def test_get_request_passes_through(self):
        """Test that GET requests bypass idempotency logic."""
        import asyncio

        app = MagicMock()
        middleware = IdempotencyMiddleware(app)
        request = MagicMock()
        request.method = "GET"

        call_next = AsyncMock(return_value=MagicMock())

        asyncio.run(middleware.dispatch(request, call_next))

        call_next.assert_called_once_with(request)

    def test_post_without_header_passes_through(self):
        """Test that POST without Idempotency-Key passes through."""
        import asyncio

        app = MagicMock()
        middleware = IdempotencyMiddleware(app)
        request = MagicMock()
        request.method = "POST"
        request.headers = {}

        call_next = AsyncMock(return_value=MagicMock())

        asyncio.run(middleware.dispatch(request, call_next))

        call_next.assert_called_once_with(request)

    def test_key_too_long_returns_400(self):
        """Test that overly long keys return 400 error."""
        import asyncio

        app = MagicMock()
        middleware = IdempotencyMiddleware(app, max_key_length=10)
        request = MagicMock()
        request.method = "POST"
        request.headers = {IDEMPOTENCY_KEY_HEADER: "a" * 20}

        call_next = AsyncMock()

        response = asyncio.run(middleware.dispatch(request, call_next))

        assert response.status_code == 400
        call_next.assert_not_called()

    def test_cached_response_returns_with_replayed_header(self):
        """Test that cached responses include replayed header."""
        import asyncio

        store = IdempotencyStore()
        cached_data = {"status_code": 200, "body": {"cached": True}}
        store.set("test-key", cached_data)

        app = MagicMock()
        middleware = IdempotencyMiddleware(app, store=store)
        request = MagicMock()
        request.method = "POST"
        request.headers = {IDEMPOTENCY_KEY_HEADER: "test-key"}
        request.state = MagicMock(spec=[])  # No user attribute

        call_next = AsyncMock()

        response = asyncio.run(middleware.dispatch(request, call_next))

        assert response.status_code == 200
        assert response.headers.get(IDEMPOTENCY_REPLAYED_HEADER) == "true"
        call_next.assert_not_called()

    def test_user_context_from_request_state(self):
        """Test that user context is extracted from request.state."""
        import asyncio

        store = IdempotencyStore()
        cached_data = {"status_code": 200, "body": {"for_user": "123"}}
        store.set("key", cached_data, user_id="user-123")

        app = MagicMock()
        middleware = IdempotencyMiddleware(app, store=store)
        request = MagicMock()
        request.method = "POST"
        request.headers = {IDEMPOTENCY_KEY_HEADER: "key"}

        # Set up user in request state, explicitly exclude org_id
        user = MagicMock()
        user.id = "user-123"
        request.state = MagicMock(spec=["user"])  # Only user, no org_id
        request.state.user = user

        call_next = AsyncMock()

        response = asyncio.run(middleware.dispatch(request, call_next))

        assert response.status_code == 200
        call_next.assert_not_called()

    def test_org_id_context_from_request_state(self):
        """Test that org_id context is used when available."""
        import asyncio

        store = IdempotencyStore()
        cached_data = {"status_code": 200, "body": {"for_org": "org-456"}}
        store.set("key", cached_data, user_id="org-456")

        app = MagicMock()
        middleware = IdempotencyMiddleware(app, store=store)
        request = MagicMock()
        request.method = "POST"
        request.headers = {IDEMPOTENCY_KEY_HEADER: "key"}
        request.state.org_id = "org-456"

        call_next = AsyncMock()

        response = asyncio.run(middleware.dispatch(request, call_next))

        assert response.status_code == 200
        call_next.assert_not_called()
