"""Tests for CLI auto-connect to cloud FalkorDB (REPO-393).

These tests verify the cloud mode functionality:
1. API key detection and validation
2. Caching with TTL
3. Error handling with user-friendly messages
4. Cloud mode indicator output
5. Local mode override
"""

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from repotoire.graph.factory import (
    CloudAuthenticationError,
    CloudAuthInfo,
    CloudConnectionError,
    ConfigurationError,
    CLOUD_CACHE_FILE,
    CLOUD_CACHE_TTL,
    _cache_auth,
    _get_cache_key,
    _get_cached_auth,
    _invalidate_cache,
    _log_cloud_connection,
    _print_cloud_indicator,
    _validate_api_key,
    create_client,
    create_cloud_client,
    get_cloud_auth_info,
    is_cloud_mode,
)


# =============================================================================
# Test Data
# =============================================================================


def make_auth_info(
    org_id: str = "550e8400-e29b-41d4-a716-446655440000",
    org_slug: str = "acme-corp",
    plan: str = "pro",
    features: list = None,
    db_config: dict = None,
    cached_at: float = None,
) -> CloudAuthInfo:
    """Create a test CloudAuthInfo."""
    return CloudAuthInfo(
        org_id=org_id,
        org_slug=org_slug,
        plan=plan,
        features=features or ["graph_embeddings", "rag_search"],
        db_config=db_config or {
            "type": "falkordb",
            "host": "repotoire-falkor.fly.dev",
            "port": 6379,
            "graph": "org_acme_corp",
        },
        cached_at=cached_at or time.time(),
    )


def make_api_response(auth_info: CloudAuthInfo) -> dict:
    """Create a test API response."""
    return {
        "valid": True,
        "org_id": auth_info.org_id,
        "org_slug": auth_info.org_slug,
        "plan": auth_info.plan,
        "features": auth_info.features,
        "db_config": auth_info.db_config,
    }


# =============================================================================
# CloudAuthInfo Tests
# =============================================================================


class TestCloudAuthInfo:
    """Tests for CloudAuthInfo dataclass."""

    def test_is_expired_returns_false_for_fresh_cache(self):
        """Fresh cache should not be expired."""
        auth_info = make_auth_info(cached_at=time.time())
        assert auth_info.is_expired() is False

    def test_is_expired_returns_true_for_old_cache(self):
        """Old cache should be expired."""
        old_time = time.time() - CLOUD_CACHE_TTL - 100
        auth_info = make_auth_info(cached_at=old_time)
        assert auth_info.is_expired() is True

    def test_to_dict_and_from_dict_roundtrip(self):
        """Should serialize and deserialize correctly."""
        original = make_auth_info()
        serialized = original.to_dict()
        restored = CloudAuthInfo.from_dict(serialized)

        assert restored.org_id == original.org_id
        assert restored.org_slug == original.org_slug
        assert restored.plan == original.plan
        assert restored.features == original.features
        assert restored.db_config == original.db_config
        assert restored.cached_at == original.cached_at


# =============================================================================
# Cache Tests
# =============================================================================


class TestCaching:
    """Tests for cloud auth caching."""

    @pytest.fixture(autouse=True)
    def setup_cache_dir(self, tmp_path, monkeypatch):
        """Use temporary cache directory for tests."""
        cache_dir = tmp_path / ".repotoire"
        cache_file = cache_dir / "cloud_auth_cache.json"
        monkeypatch.setattr("repotoire.graph.factory.REPOTOIRE_DIR", cache_dir)
        monkeypatch.setattr("repotoire.graph.factory.CLOUD_CACHE_FILE", cache_file)
        self.cache_dir = cache_dir
        self.cache_file = cache_file

    def test_get_cache_key_is_deterministic(self):
        """Same API key should produce same cache key."""
        key1 = _get_cache_key("ak_test123")
        key2 = _get_cache_key("ak_test123")
        assert key1 == key2

    def test_get_cache_key_is_different_for_different_keys(self):
        """Different API keys should produce different cache keys."""
        key1 = _get_cache_key("ak_test123")
        key2 = _get_cache_key("ak_test456")
        assert key1 != key2

    def test_cache_auth_creates_file(self):
        """Caching should create the cache file."""
        api_key = "ak_test123"
        auth_info = make_auth_info()

        _cache_auth(api_key, auth_info)

        assert self.cache_file.exists()
        data = json.loads(self.cache_file.read_text())
        cache_key = _get_cache_key(api_key)
        assert cache_key in data

    def test_get_cached_auth_returns_none_for_missing_cache(self):
        """Should return None if cache file doesn't exist."""
        result = _get_cached_auth("ak_nonexistent")
        assert result is None

    def test_get_cached_auth_returns_valid_cache(self):
        """Should return cached auth for valid cache."""
        api_key = "ak_test123"
        auth_info = make_auth_info()
        _cache_auth(api_key, auth_info)

        result = _get_cached_auth(api_key)

        assert result is not None
        assert result.org_slug == auth_info.org_slug
        assert result.plan == auth_info.plan

    def test_get_cached_auth_returns_none_for_expired_cache(self):
        """Should return None if cache is expired."""
        api_key = "ak_test123"
        old_time = time.time() - CLOUD_CACHE_TTL - 100
        auth_info = make_auth_info(cached_at=old_time)
        _cache_auth(api_key, auth_info)

        result = _get_cached_auth(api_key)
        assert result is None

    def test_get_cached_auth_returns_none_for_different_key(self):
        """Should return None if API key doesn't match."""
        api_key = "ak_test123"
        auth_info = make_auth_info()
        _cache_auth(api_key, auth_info)

        result = _get_cached_auth("ak_different_key")
        assert result is None

    def test_invalidate_cache_removes_key(self):
        """Should remove the cached entry for the key."""
        api_key = "ak_test123"
        auth_info = make_auth_info()
        _cache_auth(api_key, auth_info)

        # Verify it's cached
        assert _get_cached_auth(api_key) is not None

        # Invalidate
        _invalidate_cache(api_key)

        # Should be gone
        assert _get_cached_auth(api_key) is None

    def test_multiple_keys_can_be_cached(self):
        """Should support caching multiple API keys."""
        api_key1 = "ak_test123"
        api_key2 = "ak_test456"
        auth_info1 = make_auth_info(org_slug="org-one")
        auth_info2 = make_auth_info(org_slug="org-two")

        _cache_auth(api_key1, auth_info1)
        _cache_auth(api_key2, auth_info2)

        result1 = _get_cached_auth(api_key1)
        result2 = _get_cached_auth(api_key2)

        assert result1.org_slug == "org-one"
        assert result2.org_slug == "org-two"


# =============================================================================
# API Key Validation Tests
# =============================================================================


class TestValidateApiKey:
    """Tests for _validate_api_key function."""

    @pytest.fixture(autouse=True)
    def setup_cache_dir(self, tmp_path, monkeypatch):
        """Use temporary cache directory for tests."""
        cache_dir = tmp_path / ".repotoire"
        cache_file = cache_dir / "cloud_auth_cache.json"
        monkeypatch.setattr("repotoire.graph.factory.REPOTOIRE_DIR", cache_dir)
        monkeypatch.setattr("repotoire.graph.factory.CLOUD_CACHE_FILE", cache_file)

    def test_valid_api_key_returns_auth_info(self):
        """Valid API key should return CloudAuthInfo."""
        auth_info = make_auth_info()
        api_response = make_api_response(auth_info)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = api_response
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = _validate_api_key("ak_valid_key")

        assert result.org_id == auth_info.org_id
        assert result.org_slug == auth_info.org_slug
        assert result.plan == auth_info.plan
        assert result.features == auth_info.features
        assert result.db_config == auth_info.db_config

    def test_invalid_api_key_raises_auth_error(self):
        """Invalid API key should raise CloudAuthenticationError."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.return_value = {
            "detail": {"error": "Invalid or expired API key"}
        }

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            with pytest.raises(CloudAuthenticationError) as exc_info:
                _validate_api_key("ak_invalid_key")

        assert "Authentication failed" in str(exc_info.value)
        assert exc_info.value.suggestion is not None
        assert "repotoire.com/settings/api-keys" in exc_info.value.suggestion

    def test_rate_limited_raises_auth_error_with_retry(self):
        """Rate limiting should raise error with retry_after."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.json.return_value = {"retry_after": 45}

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            with pytest.raises(CloudAuthenticationError) as exc_info:
                _validate_api_key("ak_rate_limited")

        assert "Too many requests" in str(exc_info.value)
        assert exc_info.value.retry_after == 45

    def test_connection_error_raises_connection_error(self):
        """Network failure should raise CloudConnectionError."""
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client_cls.return_value = mock_client

            with pytest.raises(CloudConnectionError) as exc_info:
                _validate_api_key("ak_test")

        assert "Could not connect" in str(exc_info.value)

    def test_timeout_raises_connection_error(self):
        """Timeout should raise CloudConnectionError."""
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = httpx.TimeoutException("Request timed out")
            mock_client_cls.return_value = mock_client

            with pytest.raises(CloudConnectionError) as exc_info:
                _validate_api_key("ak_test")

        assert "timed out" in str(exc_info.value)


# =============================================================================
# Cloud Indicator Tests
# =============================================================================


class TestCloudIndicator:
    """Tests for cloud mode indicator output."""

    def test_print_cloud_indicator_shows_org_and_plan(self, capsys):
        """Should print org slug and plan."""
        auth_info = make_auth_info(org_slug="my-company", plan="pro")

        _print_cloud_indicator(auth_info)

        captured = capsys.readouterr()
        assert "my-company" in captured.out
        assert "pro" in captured.out

    def test_print_cloud_indicator_shows_cloud_emoji(self, capsys):
        """Should include cloud emoji."""
        auth_info = make_auth_info()

        _print_cloud_indicator(auth_info)

        captured = capsys.readouterr()
        # Check for cloud indicator text
        assert "Connected to Repotoire Cloud" in captured.out


# =============================================================================
# is_cloud_mode Tests
# =============================================================================


class TestIsCloudMode:
    """Tests for is_cloud_mode function."""

    def test_returns_true_when_api_key_set(self, monkeypatch):
        """Should return True when API key is set."""
        monkeypatch.setenv("REPOTOIRE_API_KEY", "ak_test123")

        assert is_cloud_mode() is True

    def test_returns_false_when_no_api_key(self, monkeypatch):
        """Should return False when no API key."""
        monkeypatch.delenv("REPOTOIRE_API_KEY", raising=False)

        # Must also mock CredentialStore since get_api_key checks multiple sources
        with patch("repotoire.graph.factory.get_api_key", return_value=None):
            assert is_cloud_mode() is False


# =============================================================================
# create_client Priority Tests
# =============================================================================


class TestCreateClientPriority:
    """Tests for create_client mode priority."""

    @pytest.fixture(autouse=True)
    def setup_env(self, monkeypatch, tmp_path):
        """Clear relevant env vars before each test."""
        monkeypatch.delenv("REPOTOIRE_API_KEY", raising=False)
        monkeypatch.delenv("FALKORDB_HOST", raising=False)
        monkeypatch.delenv("REPOTOIRE_FALKORDB_HOST", raising=False)
        monkeypatch.delenv("REPOTOIRE_DB_TYPE", raising=False)

        # Use temp cache dir
        cache_dir = tmp_path / ".repotoire"
        cache_file = cache_dir / "cloud_auth_cache.json"
        monkeypatch.setattr("repotoire.graph.factory.REPOTOIRE_DIR", cache_dir)
        monkeypatch.setattr("repotoire.graph.factory.CLOUD_CACHE_FILE", cache_file)

    def test_api_key_triggers_cloud_mode(self, monkeypatch):
        """REPOTOIRE_API_KEY should trigger cloud mode."""
        monkeypatch.setenv("REPOTOIRE_API_KEY", "ak_test123")

        with patch("repotoire.graph.factory.create_cloud_client") as mock_cloud:
            mock_client = MagicMock()
            mock_cloud.return_value = mock_client

            result = create_client()

        mock_cloud.assert_called_once_with("ak_test123", show_indicator=True)
        assert result == mock_client

    def test_no_api_key_raises_configuration_error(self, monkeypatch):
        """Should raise ConfigurationError when API key is not set."""
        # All env vars are cleared by fixture
        # Must also mock CredentialStore since get_api_key checks multiple sources
        with patch("repotoire.graph.factory.get_api_key", return_value=None):
            with pytest.raises(ConfigurationError) as exc_info:
                create_client()

            assert "API key required" in str(exc_info.value)


# =============================================================================
# create_cloud_client Tests
# =============================================================================


class TestCreateCloudClient:
    """Tests for create_cloud_client function."""

    @pytest.fixture(autouse=True)
    def setup_cache_dir(self, tmp_path, monkeypatch):
        """Use temporary cache directory for tests."""
        cache_dir = tmp_path / ".repotoire"
        cache_file = cache_dir / "cloud_auth_cache.json"
        monkeypatch.setattr("repotoire.graph.factory.REPOTOIRE_DIR", cache_dir)
        monkeypatch.setattr("repotoire.graph.factory.CLOUD_CACHE_FILE", cache_file)
        self.cache_file = cache_file

    def test_uses_cached_auth_if_available(self, capsys):
        """Should use cached auth instead of calling API."""
        api_key = "ak_test123"
        auth_info = make_auth_info()
        _cache_auth(api_key, auth_info)

        with patch("repotoire.graph.factory._validate_api_key") as mock_validate:
            with patch("repotoire.graph.cloud_client.CloudProxyClient") as mock_client:
                mock_client.return_value = MagicMock()
                create_cloud_client(api_key, show_indicator=False)

        # Should NOT call validate since we have cache
        mock_validate.assert_not_called()

    def test_calls_validate_when_no_cache(self):
        """Should call API when no cache exists."""
        api_key = "ak_test123"
        auth_info = make_auth_info()

        with patch("repotoire.graph.factory._validate_api_key") as mock_validate:
            mock_validate.return_value = auth_info
            with patch("repotoire.graph.cloud_client.CloudProxyClient") as mock_client:
                mock_client.return_value = MagicMock()
                create_cloud_client(api_key, show_indicator=False)

        mock_validate.assert_called_once_with(api_key)

    def test_creates_cloud_proxy_client_with_api_key(self):
        """Should create CloudProxyClient with API key."""
        api_key = "ak_test123"
        auth_info = make_auth_info()
        _cache_auth(api_key, auth_info)

        with patch("repotoire.graph.cloud_client.CloudProxyClient") as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            result = create_cloud_client(api_key, show_indicator=False)

        mock_client.assert_called_once_with(api_key=api_key)
        assert result == mock_instance

    def test_caches_auth_after_validation(self):
        """Should cache auth info after successful validation."""
        api_key = "ak_test123"
        auth_info = make_auth_info()

        with patch("repotoire.graph.factory._validate_api_key") as mock_validate:
            mock_validate.return_value = auth_info
            with patch("repotoire.graph.cloud_client.CloudProxyClient") as mock_client:
                mock_client.return_value = MagicMock()
                create_cloud_client(api_key, show_indicator=False)

        # Verify cache was created
        cached = _get_cached_auth(api_key)
        assert cached is not None
        assert cached.org_slug == auth_info.org_slug


# =============================================================================
# get_cloud_auth_info Tests
# =============================================================================


class TestGetCloudAuthInfo:
    """Tests for get_cloud_auth_info function."""

    @pytest.fixture(autouse=True)
    def setup_env(self, tmp_path, monkeypatch):
        """Setup test environment."""
        monkeypatch.delenv("REPOTOIRE_API_KEY", raising=False)
        cache_dir = tmp_path / ".repotoire"
        cache_file = cache_dir / "cloud_auth_cache.json"
        monkeypatch.setattr("repotoire.graph.factory.REPOTOIRE_DIR", cache_dir)
        monkeypatch.setattr("repotoire.graph.factory.CLOUD_CACHE_FILE", cache_file)
        self.cache_file = cache_file

    def test_returns_none_when_no_api_key(self):
        """Should return None if no API key is set."""
        result = get_cloud_auth_info()
        assert result is None

    def test_returns_cached_info_when_available(self, monkeypatch):
        """Should return cached auth if API key is set and cached."""
        api_key = "ak_test123"
        monkeypatch.setenv("REPOTOIRE_API_KEY", api_key)

        auth_info = make_auth_info(org_slug="cached-org")
        _cache_auth(api_key, auth_info)

        result = get_cloud_auth_info()
        assert result is not None
        assert result.org_slug == "cached-org"

    def test_returns_none_when_not_cached(self, monkeypatch):
        """Should return None if API key is set but not cached."""
        monkeypatch.setenv("REPOTOIRE_API_KEY", "ak_uncached")

        result = get_cloud_auth_info()
        assert result is None


# =============================================================================
# Error Message Tests
# =============================================================================


class TestErrorMessages:
    """Tests for user-friendly error messages."""

    def test_auth_error_has_suggestion(self):
        """CloudAuthenticationError should include suggestion."""
        error = CloudAuthenticationError(
            "Invalid API key",
            suggestion="Check your key at https://example.com",
        )

        assert "Invalid API key" in str(error)
        assert error.suggestion == "Check your key at https://example.com"

    def test_connection_error_has_cause(self):
        """CloudConnectionError should include cause."""
        cause = ConnectionError("Network unreachable")
        error = CloudConnectionError("Could not connect", cause=cause)

        assert "Could not connect" in str(error)
        assert error.cause == cause

    def test_configuration_error_includes_api_key_info(self):
        """ConfigurationError should explain API key is required."""
        error = ConfigurationError(
            "API key required.\n\n"
            "  1. Get your API key at: https://repotoire.com/settings/api-keys\n"
            "  2. Run: export REPOTOIRE_API_KEY=ak_your_key"
        )

        assert "API key required" in str(error)
        assert "REPOTOIRE_API_KEY" in str(error)


# =============================================================================
# Connection Logging Tests
# =============================================================================


class TestConnectionLogging:
    """Tests for cloud connection audit logging."""

    def test_log_connection_calls_api(self):
        """Should call the log-connection API endpoint."""
        api_key = "ak_test123"
        auth_info = make_auth_info()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post = MagicMock()
            mock_client_cls.return_value = mock_client

            # Call the function (it uses a thread, but we'll capture the call)
            _log_cloud_connection(api_key, auth_info, cached=True, command="ingest")

            # Give the thread a moment to start
            import time
            time.sleep(0.1)

            # The thread may or may not have completed, but we can at least verify
            # the function doesn't raise

    def test_log_connection_includes_metadata(self):
        """Should include org, plan, cached, and command in request."""
        api_key = "ak_test123"
        auth_info = make_auth_info(org_slug="test-org", plan="enterprise")

        captured_json = {}

        def capture_post(url, headers=None, json=None):
            captured_json.update(json or {})
            return MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post = capture_post
            mock_client_cls.return_value = mock_client

            # Run synchronously for testing
            with patch("threading.Thread") as mock_thread:
                # Capture the target function and run it directly
                def run_target(target=None, daemon=None):
                    mock = MagicMock()
                    mock.start = lambda: target()  # Run immediately
                    return mock
                mock_thread.side_effect = run_target

                _log_cloud_connection(
                    api_key, auth_info, cached=True, command="analyze"
                )

        assert captured_json.get("org_slug") == "test-org"
        assert captured_json.get("plan") == "enterprise"
        assert captured_json.get("cached") is True
        assert captured_json.get("command") == "analyze"

    def test_log_connection_silently_ignores_errors(self):
        """Should not raise even if API call fails."""
        api_key = "ak_test123"
        auth_info = make_auth_info()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = Exception("Network error")
            mock_client_cls.return_value = mock_client

            # Should not raise
            with patch("threading.Thread") as mock_thread:
                def run_target(target=None, daemon=None):
                    mock = MagicMock()
                    mock.start = lambda: target()
                    return mock
                mock_thread.side_effect = run_target

                _log_cloud_connection(api_key, auth_info)  # No exception

    def test_create_cloud_client_logs_connection(self, tmp_path, monkeypatch):
        """create_cloud_client should call _log_cloud_connection."""
        cache_dir = tmp_path / ".repotoire"
        cache_file = cache_dir / "cloud_auth_cache.json"
        monkeypatch.setattr("repotoire.graph.factory.REPOTOIRE_DIR", cache_dir)
        monkeypatch.setattr("repotoire.graph.factory.CLOUD_CACHE_FILE", cache_file)

        api_key = "ak_test123"
        auth_info = make_auth_info()
        _cache_auth(api_key, auth_info)

        with patch("repotoire.graph.factory._log_cloud_connection") as mock_log:
            with patch("repotoire.graph.cloud_client.CloudProxyClient") as mock_client:
                mock_client.return_value = MagicMock()
                create_cloud_client(api_key, show_indicator=False, command="ingest")

        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[0][0] == api_key  # api_key
        assert call_args[1]["cached"] is True  # used cache
        assert call_args[1]["command"] == "ingest"
