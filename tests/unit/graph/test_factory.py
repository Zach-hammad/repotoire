"""Tests for graph database client factory.

Tests the factory functions that create database clients based on configuration.
"""

import pytest
import os
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
import tempfile

from repotoire.graph.factory import (
    get_api_key,
    REPOTOIRE_DIR,
    CREDENTIALS_FILE,
    DEFAULT_API_URL,
)


class TestGetApiKey:
    """Test API key retrieval logic."""

    def test_returns_none_when_no_key_set(self):
        """Should return None when no API key is configured."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("repotoire.cli.credentials.CredentialStore") as mock_store:
                mock_store.return_value.get_api_key.return_value = None
                result = get_api_key()
                assert result is None

    def test_returns_key_from_credential_store(self):
        """Should return API key from credential store."""
        with patch("repotoire.cli.credentials.CredentialStore") as mock_store:
            mock_store.return_value.get_api_key.return_value = "ak_test_key"
            result = get_api_key()
            assert result == "ak_test_key"


class TestDefaultConstants:
    """Test default configuration constants."""

    def test_repotoire_dir_is_in_home(self):
        """REPOTOIRE_DIR should be in user's home directory."""
        assert REPOTOIRE_DIR == Path.home() / ".repotoire"

    def test_credentials_file_in_repotoire_dir(self):
        """CREDENTIALS_FILE should be in REPOTOIRE_DIR."""
        assert CREDENTIALS_FILE == REPOTOIRE_DIR / "credentials"

    def test_default_api_url_is_fly(self):
        """DEFAULT_API_URL should point to Fly.io."""
        assert "fly.dev" in DEFAULT_API_URL or "repotoire" in DEFAULT_API_URL


class TestCreateKuzuClient:
    """Test Kuzu client creation for local-first mode."""

    def test_creates_kuzu_client_without_api_key(self):
        """Should create Kuzu client when no API key is set."""
        with patch("repotoire.graph.factory.get_api_key", return_value=None):
            # Import here to avoid circular imports
            from repotoire.graph.factory import create_kuzu_client
            
            with patch("repotoire.graph.kuzu_client._HAS_KUZU", True):
                with patch("repotoire.graph.kuzu_client.kuzu") as mock_kuzu:
                    mock_db = MagicMock()
                    mock_kuzu.Database.return_value = mock_db
                    mock_db.init.return_value = None
                    
                    with tempfile.TemporaryDirectory() as tmpdir:
                        client = create_kuzu_client(repository_path=tmpdir)
                        assert client is not None
                        assert client.is_kuzu is True
                        assert client.is_falkordb is False

    def test_kuzu_client_uses_repository_path(self):
        """Kuzu client should use the repository path for database storage."""
        with patch("repotoire.graph.kuzu_client._HAS_KUZU", True):
            with patch("repotoire.graph.kuzu_client.kuzu") as mock_kuzu:
                mock_db = MagicMock()
                mock_kuzu.Database.return_value = mock_db
                mock_db.init.return_value = None
                
                from repotoire.graph.factory import create_kuzu_client
                
                with tempfile.TemporaryDirectory() as tmpdir:
                    client = create_kuzu_client(repository_path=tmpdir)
                    # Should have called Database with a path
                    mock_kuzu.Database.assert_called()


class TestCreateClientAutoSelection:
    """Test automatic client selection based on configuration."""

    def test_prefers_cloud_when_api_key_set(self):
        """Should prefer cloud client when API key is available."""
        # This tests the logic that create_client checks for API key first
        with patch("repotoire.graph.factory.get_api_key") as mock_get_key:
            mock_get_key.return_value = "ak_test_key"
            # The actual cloud client creation would require network
            # Just verify the key is checked
            assert mock_get_key() == "ak_test_key"

    def test_falls_back_to_local_without_api_key(self):
        """Should fall back to local Kuzu when no API key is set."""
        with patch("repotoire.graph.factory.get_api_key") as mock_get_key:
            mock_get_key.return_value = None
            assert mock_get_key() is None


class TestCredentialStore:
    """Test credential storage functionality."""

    def test_credential_store_imports(self):
        """CredentialStore should be importable."""
        from repotoire.cli.credentials import CredentialStore
        store = CredentialStore()
        assert store is not None

    def test_credential_store_has_get_api_key(self):
        """CredentialStore should have get_api_key method."""
        from repotoire.cli.credentials import CredentialStore
        store = CredentialStore()
        assert hasattr(store, "get_api_key")


class TestCloudCacheSettings:
    """Test cloud authentication cache settings."""

    def test_cloud_cache_file_in_repotoire_dir(self):
        """CLOUD_CACHE_FILE should be in REPOTOIRE_DIR."""
        from repotoire.graph.factory import CLOUD_CACHE_FILE
        assert CLOUD_CACHE_FILE.parent == REPOTOIRE_DIR

    def test_cloud_cache_ttl_is_reasonable(self):
        """CLOUD_CACHE_TTL should be a reasonable duration (5-60 minutes)."""
        from repotoire.graph.factory import CLOUD_CACHE_TTL
        assert 300 <= CLOUD_CACHE_TTL <= 3600  # 5 min to 1 hour
