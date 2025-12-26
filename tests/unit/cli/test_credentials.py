"""Tests for CLI credential storage (REPO-397)."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repotoire.cli.credentials import (
    CredentialStore,
    CredentialMetadata,
    StorageBackend,
    mask_api_key,
    _keyring_available,
)


class TestMaskApiKey:
    """Tests for the mask_api_key function."""

    def test_masks_standard_api_key(self):
        """Standard API key is masked correctly."""
        assert mask_api_key("ak_1234567890abcdef") == "ak_123...ef"

    def test_masks_short_key(self):
        """Short keys are masked appropriately."""
        # Keys <= 8 chars just show prefix + ellipsis
        assert mask_api_key("ak_short") == "ak_..."

    def test_masks_very_short_key(self):
        """Very short keys show just prefix."""
        assert mask_api_key("ak_") == "ak_..."

    def test_handles_empty_key(self):
        """Empty key returns ellipsis."""
        assert mask_api_key("") == "..."


class TestCredentialMetadata:
    """Tests for CredentialMetadata serialization."""

    def test_to_dict_and_from_dict(self):
        """Metadata serializes and deserializes correctly."""
        from datetime import datetime, timezone

        original = CredentialMetadata(
            storage_backend=StorageBackend.KEYRING,
            stored_at=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            key_prefix="ak_123",
        )

        data = original.to_dict()
        restored = CredentialMetadata.from_dict(data)

        assert restored.storage_backend == StorageBackend.KEYRING
        assert restored.stored_at == original.stored_at
        assert restored.key_prefix == "ak_123"


class TestCredentialStoreFileBackend:
    """Tests for file-based credential storage."""

    @pytest.fixture
    def temp_credentials_dir(self, tmp_path):
        """Create a temporary credentials directory."""
        creds_dir = tmp_path / ".repotoire"
        with patch("repotoire.cli.credentials.CREDENTIALS_DIR", creds_dir):
            with patch("repotoire.cli.credentials.CREDENTIALS_FILE", creds_dir / "credentials"):
                with patch("repotoire.cli.credentials.METADATA_FILE", creds_dir / "credentials_meta.json"):
                    yield creds_dir

    def test_save_and_get_api_key_file_backend(self, temp_credentials_dir):
        """API key is saved and retrieved correctly with file backend."""
        store = CredentialStore(prefer_keyring=False)

        backend = store.save_api_key("ak_test_key_123")
        assert backend == StorageBackend.FILE

        # Clear env var to test file fallback
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("REPOTOIRE_API_KEY", None)
            retrieved = store.get_api_key()
            assert retrieved == "ak_test_key_123"

    def test_env_var_takes_precedence(self, temp_credentials_dir):
        """Environment variable takes precedence over stored credentials."""
        store = CredentialStore(prefer_keyring=False)
        store.save_api_key("stored_key")

        with patch.dict(os.environ, {"REPOTOIRE_API_KEY": "env_key"}):
            assert store.get_api_key() == "env_key"

    def test_clear_removes_credentials(self, temp_credentials_dir):
        """Clear removes stored credentials."""
        store = CredentialStore(prefer_keyring=False)
        store.save_api_key("ak_test_key")

        assert store.clear() is True

        # Verify file is gone
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("REPOTOIRE_API_KEY", None)
            assert store.get_api_key() is None

    def test_clear_returns_false_when_no_credentials(self, temp_credentials_dir):
        """Clear returns False when no credentials exist."""
        store = CredentialStore(prefer_keyring=False)
        assert store.clear() is False

    def test_get_source_with_file(self, temp_credentials_dir):
        """Get source returns file path for file-stored credentials."""
        # Clear env var to test file fallback
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("REPOTOIRE_API_KEY", None)
            store = CredentialStore(prefer_keyring=False)
            store.save_api_key("ak_test")

            assert "credentials" in store.get_source()

    def test_get_source_with_env_var(self, temp_credentials_dir):
        """Get source returns env var description when set."""
        with patch.dict(os.environ, {"REPOTOIRE_API_KEY": "env_key"}):
            store = CredentialStore(prefer_keyring=False)
            assert "environment variable" in store.get_source()

    def test_get_source_returns_none_when_no_credentials(self, temp_credentials_dir):
        """Get source returns None when no credentials."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("REPOTOIRE_API_KEY", None)
            store = CredentialStore(prefer_keyring=False)
            assert store.get_source() is None

    def test_metadata_is_saved(self, temp_credentials_dir):
        """Metadata is saved alongside credentials."""
        store = CredentialStore(prefer_keyring=False)
        store.save_api_key("ak_test_key_12345")

        metadata = store.get_metadata()
        assert metadata is not None
        assert metadata.storage_backend == StorageBackend.FILE
        assert metadata.key_prefix == "ak_tes"  # First 6 chars

    def test_file_permissions_are_secure(self, temp_credentials_dir):
        """Credentials file has secure permissions (600)."""
        store = CredentialStore(prefer_keyring=False)
        store.save_api_key("ak_test")

        creds_file = temp_credentials_dir / "credentials"
        assert oct(creds_file.stat().st_mode)[-3:] == "600"


class TestCredentialStoreKeyringBackend:
    """Tests for keyring-based credential storage."""

    @pytest.fixture
    def mock_keyring(self):
        """Mock keyring module."""
        mock = MagicMock()
        mock.get_password.return_value = None
        mock.set_password = MagicMock()
        mock.delete_password = MagicMock()

        with patch.dict("sys.modules", {"keyring": mock}):
            with patch("repotoire.cli.credentials._keyring_available", return_value=True):
                yield mock

    @pytest.fixture
    def temp_credentials_dir(self, tmp_path):
        """Create a temporary credentials directory."""
        creds_dir = tmp_path / ".repotoire"
        with patch("repotoire.cli.credentials.CREDENTIALS_DIR", creds_dir):
            with patch("repotoire.cli.credentials.CREDENTIALS_FILE", creds_dir / "credentials"):
                with patch("repotoire.cli.credentials.METADATA_FILE", creds_dir / "credentials_meta.json"):
                    yield creds_dir

    def test_keyring_backend_preferred_when_available(self, mock_keyring, temp_credentials_dir):
        """Keyring backend is used when available."""
        with patch("repotoire.cli.credentials._keyring_available", return_value=True):
            store = CredentialStore(prefer_keyring=True)
            store._keyring_available = True  # Force it for the test

            backend = store.save_api_key("ak_test")
            assert backend == StorageBackend.KEYRING

    def test_falls_back_to_file_when_keyring_unavailable(self, temp_credentials_dir):
        """Falls back to file when keyring is not available."""
        with patch("repotoire.cli.credentials._keyring_available", return_value=False):
            store = CredentialStore(prefer_keyring=True)

            backend = store.save_api_key("ak_test")
            assert backend == StorageBackend.FILE
